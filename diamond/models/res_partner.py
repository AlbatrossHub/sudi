import logging

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

GST_STATE_PRESENT = "present"
GST_STATE_SKIPPED = "skipped"
GST_STATE_MISSING = "missing"


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_skip_gst = fields.Boolean(string="Skip GST Prompt", default=False)
    sudi_invoice_policy = fields.Selection(
        [("on_demand", "On Demand"), ("monthly", "Monthly")],
        string="Job Work Invoicing",
        default="on_demand",
        help="Monthly: the billing review lists this customer as due once the month is "
             "over, so the reviewer can bill everything delivered in one go.",
    )
    sudi_diamond_service_price_ids = fields.One2many(
        "sudi.diamond.partner.service.price",
        "partner_id",
        string="Diamond Service Prices",
    )

    # ------------------------------------------------------------------
    # GST onboarding
    #
    # Lifted out of diamond/controllers/auth.py so the web onboarding form and
    # the customer API share one implementation. Three things the controller
    # version did not do, and which matter far more once this is reachable as
    # an API: validate the format, notice when a GSTIN is already on more than
    # one company record, and refuse to attach a customer to a company built
    # from input nobody checked.
    # ------------------------------------------------------------------
    @api.model
    def _sudi_normalize_gstin(self, vat):
        return "".join((vat or "").split()).upper()

    @api.model
    def _sudi_validate_gstin(self, vat):
        """The cleaned GSTIN, or a ValidationError naming the problem."""
        cleaned = self._sudi_normalize_gstin(vat)
        if not cleaned:
            raise ValidationError(_("Enter a GST number."))
        # Odoo's own checker, which knows the normal, composite, casual, UN,
        # NRI, TDS and TCS shapes. Writing a regex here would get one wrong.
        checker = getattr(self.env["res.partner"], "check_vat_in", None)
        if checker and not checker(cleaned):
            raise ValidationError(
                _("%s is not a valid GST number.", cleaned)
            )
        return cleaned

    @api.model
    def _sudi_gst_company_vals(self, cleaned):
        """Values for a company partner built from a GSTIN.

        The first two digits are the state TIN, which is the one thing a GSTIN
        always tells us. Everything else is enrichment, and enrichment is
        allowed to fail: a customer must be able to finish onboarding when an
        external lookup is down.
        """
        Partner = self.env["res.partner"].with_user(SUPERUSER_ID)
        country = self.env["res.country"].sudo().search([("code", "=", "IN")], limit=1)
        state = self.env["res.country.state"]
        if len(cleaned) >= 2 and cleaned[:2].isdigit():
            state = self.env["res.country.state"].sudo().search(
                [("l10n_in_tin", "=", cleaned[:2])], limit=1
            )

        vals = {
            "name": _("Company (%s)", cleaned),
            "is_company": True,
            "company_type": "company",
            "vat": cleaned,
            "country_id": country.id or False,
            "state_id": state.id or False,
            "x_skip_gst": False,
        }
        if "l10n_in_gst_treatment" in Partner._fields:
            vals["l10n_in_gst_treatment"] = "regular"

        try:
            enriched = None
            if hasattr(Partner, "_l10n_in_get_partner_vals_by_vat"):
                enriched = Partner._l10n_in_get_partner_vals_by_vat(cleaned)
            elif hasattr(Partner, "enrich_by_gst"):
                enriched = Partner.enrich_by_gst(cleaned)
                if enriched and enriched.get("error"):
                    enriched = None
            for field in (
                "name", "street", "street2", "city", "zip",
                "state_id", "country_id", "l10n_in_gst_treatment",
            ):
                value = (enriched or {}).get(field)
                if isinstance(value, dict):
                    value = value.get("id")
                if value:
                    vals[field] = value
        except Exception:
            # Deliberately broad and deliberately loud: the lookup is an
            # external dependency, and onboarding must not fail with it.
            _logger.warning(
                "GST enrichment failed for %s; keeping the GSTIN only",
                cleaned, exc_info=True,
            )
        return vals

    @api.model
    def _sudi_resolve_gst_company(self, vat):
        """The company partner behind ``vat``, created if it does not exist."""
        cleaned = self._sudi_validate_gstin(vat)
        Partner = self.env["res.partner"].sudo()
        existing = Partner.search(
            [("vat", "=ilike", cleaned), ("is_company", "=", True)], order="id"
        )
        if len(existing) > 1:
            # Not fatal, but somebody should look: two companies sharing a
            # GSTIN means invoices can be raised against either.
            _logger.warning(
                "GSTIN %s is on %s company records %s; using the oldest",
                cleaned, len(existing), existing.ids,
            )
        if existing:
            return existing[0]
        return Partner.create(self._sudi_gst_company_vals(cleaned))

    def _sudi_apply_gstin(self, vat):
        """Attach this customer to the company behind ``vat``."""
        self.ensure_one()
        company = self._sudi_resolve_gst_company(vat)
        if company == self:
            # The customer *is* the company record. Nothing to parent to.
            self.sudo().write({"vat": company.vat, "x_skip_gst": False})
            return company
        self.sudo().write({
            "vat": company.vat,
            "parent_id": company.id,
            "is_company": False,
            "company_type": "person",
            "x_skip_gst": False,
        })
        return company

    def _sudi_skip_gst(self):
        """Let a customer past onboarding without a GSTIN (decision D3)."""
        self.sudo().write({"x_skip_gst": True})
        return True

    def _sudi_gst_state(self):
        """``present`` / ``skipped`` / ``missing``.

        Three states and not a boolean, because the skip is not final: a
        customer who skipped can be asked again on a later launch, and one who
        never answered should be asked now.
        """
        self.ensure_one()
        if self.vat or self.commercial_partner_id.vat:
            return GST_STATE_PRESENT
        return GST_STATE_SKIPPED if self.x_skip_gst else GST_STATE_MISSING

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        partners._sudi_ensure_diamond_service_price_lines()
        return partners

    def _sudi_ensure_diamond_service_price_lines(self):
        owners = self.env["res.partner"]
        for partner in self:
            owners |= partner.commercial_partner_id or partner

        job_types = self.env["sudi.diamond.job.type"].sudo().search([("active", "=", True)])
        Price = self.env["sudi.diamond.partner.service.price"].sudo()
        for owner in owners.sudo():
            for job_type in job_types:
                company = job_type.company_id or self.env.company
                existing = Price.search_count([
                    ("partner_id", "=", owner.id),
                    ("job_type_id", "=", job_type.id),
                    ("company_id", "=", company.id),
                    ("active", "=", True),
                ])
                if existing:
                    continue
                Price.create({
                    "partner_id": owner.id,
                    "job_type_id": job_type.id,
                    "company_id": company.id,
                    "price": job_type.base_price,
                })

    def action_sudi_sync_diamond_service_prices(self):
        self._sudi_ensure_diamond_service_price_lines()
        return True


class SudiDiamondPartnerServicePrice(models.Model):
    _name = "sudi.diamond.partner.service.price"
    _description = "Customer Diamond Service Price"
    _order = "partner_id, job_type_id"
    _rec_name = "job_type_id"

    partner_id = fields.Many2one(
        "res.partner",
        required=True,
        ondelete="cascade",
        index=True,
    )
    job_type_id = fields.Many2one(
        "sudi.diamond.job.type",
        string="Job Type",
        required=True,
        domain=[("active", "=", True)],
        ondelete="cascade",
        index=True,
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        readonly=True,
    )
    price = fields.Monetary(currency_field="currency_id", required=True, default=0.0)

    _price_non_negative = models.Constraint(
        "CHECK(price >= 0)",
        "The special service price must be zero or positive.",
    )

    @api.constrains("partner_id", "job_type_id", "company_id", "active")
    def _check_unique_active_price(self):
        for price in self.filtered("active"):
            duplicate = self.search(
                [
                    ("id", "!=", price.id),
                    ("partner_id", "=", price.partner_id.id),
                    ("job_type_id", "=", price.job_type_id.id),
                    ("company_id", "=", price.company_id.id),
                    ("active", "=", True),
                ],
                limit=1,
            )
            if duplicate:
                raise ValidationError(
                    "Only one active special price is allowed per customer, company, and job type."
                )
