from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    sudi_diamond_service_price_ids = fields.One2many(
        "sudi.diamond.partner.service.price",
        "partner_id",
        string="Diamond Service Prices",
    )

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

    _sql_constraints = [
        (
            "price_non_negative",
            "CHECK(price >= 0)",
            "The special service price must be zero or positive.",
        ),
    ]

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
