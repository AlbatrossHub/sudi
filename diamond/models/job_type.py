from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SudiDiamondJobType(models.Model):
    _name = "sudi.diamond.job.type"
    _description = "Diamond Job Work Service"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
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
    service_product_id = fields.Many2one(
        "product.product",
        string="Service Product",
        domain=[("type", "=", "service")],
        check_company=True,
        required=True,
    )
    invoice_basis = fields.Selection(
        [
            ("pieces", "Pieces / Qty"),
            ("carats", "Carats"),
            ("manual", "Manual"),
        ],
        default="pieces",
        required=True,
        help="Controls which delivered diamond quantity is used on invoice lines.",
    )
    invoice_description = fields.Text(
        string="Invoice Description",
        translate=True,
        help="Exact description to use on invoice lines for this job type.",
    )
    base_price = fields.Monetary(
        currency_field="currency_id",
        default=0.0,
        help="Fallback service price when the customer has no special price.",
    )
    tax_ids = fields.Many2many(
        "account.tax",
        "sudi_diamond_job_type_account_tax_rel",
        "job_type_id",
        "tax_id",
        string="Default Taxes",
        domain=[("type_tax_use", "=", "sale")],
        check_company=True,
        help="Optional override. Leave empty to use the service product taxes.",
    )

    _sql_constraints = [
        (
            "name_company_uniq",
            "unique(name, company_id)",
            "A diamond job type with this name already exists for this company.",
        ),
        (
            "base_price_non_negative",
            "CHECK(base_price >= 0)",
            "The base price must be zero or positive.",
        ),
    ]

    @api.constrains("service_product_id")
    def _check_service_product_id(self):
        for job_type in self:
            if job_type.service_product_id.type != "service":
                raise ValidationError("The service product must be a service.")

    def _get_price_for_partner(self, partner, company=None):
        """Return the active customer-specific price, falling back to base price."""
        self.ensure_one()
        company = company or self.company_id or self.env.company
        commercial_partner = partner.commercial_partner_id if partner else self.env["res.partner"]
        special_price = self.env["sudi.diamond.partner.service.price"].search(
            [
                ("partner_id", "=", commercial_partner.id),
                ("job_type_id", "=", self.id),
                ("company_id", "in", [False, company.id]),
                ("active", "=", True),
            ],
            order="company_id desc, id desc",
            limit=1,
        )
        return special_price.price if special_price else self.base_price
