from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SudiDiamondBillingLine(models.Model):
    _name = "sudi.diamond.billing.line"
    _description = "Diamond Receipt Billing Line"
    _order = "picking_id, sequence, id"

    picking_id = fields.Many2one(
        "stock.picking",
        string="Receipt",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="picking_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        readonly=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    job_type_id = fields.Many2one(
        "sudi.diamond.job.type",
        string="Job Type",
        required=True,
        domain=[("active", "=", True)],
        ondelete="restrict",
        index=True,
    )
    service_product_id = fields.Many2one(
        "product.product",
        related="job_type_id.service_product_id",
        readonly=True,
    )
    name = fields.Text(string="Description", required=True)
    quantity = fields.Float(string="Quantity", digits="Product Unit", required=True, default=0.0)
    price_unit = fields.Monetary(string="Unit Price", currency_field="currency_id", required=True, default=0.0)
    price_subtotal = fields.Monetary(
        string="Subtotal",
        currency_field="currency_id",
        compute="_compute_price_subtotal",
        store=True,
    )
    price_source = fields.Selection(
        [
            ("partner", "Partner Price"),
            ("job_type", "Job Type Price"),
            ("manual", "Manual"),
        ],
        default="job_type",
        required=True,
        readonly=True,
    )
    manual_price = fields.Boolean(string="Manual Price")
    manual_quantity = fields.Boolean(string="Manual Quantity")
    invoice_line_id = fields.Many2one(
        "account.move.line",
        string="Invoice Line",
        copy=False,
        readonly=True,
        ondelete="set null",
        index=True,
    )

    _sql_constraints = [
        (
            "quantity_non_negative",
            "CHECK(quantity >= 0)",
            "The billing quantity must be zero or positive.",
        ),
        (
            "price_unit_non_negative",
            "CHECK(price_unit >= 0)",
            "The billing unit price must be zero or positive.",
        ),
    ]

    @api.depends("quantity", "price_unit")
    def _compute_price_subtotal(self):
        for line in self:
            line.price_subtotal = line.quantity * line.price_unit

    @api.onchange("quantity")
    def _onchange_quantity_manual(self):
        for line in self:
            if line._origin and line.quantity != line._origin.quantity:
                line.manual_quantity = True

    @api.onchange("price_unit")
    def _onchange_price_unit_manual(self):
        for line in self:
            if line._origin and line.price_unit != line._origin.price_unit:
                line.manual_price = True
                line.price_source = "manual"

    @api.onchange("job_type_id", "picking_id")
    def _onchange_job_type_id(self):
        for line in self:
            if not line.job_type_id:
                continue
            line.name = line.job_type_id.invoice_description or line.job_type_id.display_name
            if not line.manual_price:
                price, source = line.job_type_id._sudi_get_price_for_partner_with_source(
                    line.picking_id.partner_id.commercial_partner_id,
                    line.picking_id.company_id,
                )
                line.price_unit = price
                line.price_source = source
                line.manual_price = False

    @api.constrains("picking_id", "job_type_id", "active")
    def _check_unique_active_job_type(self):
        for line in self.filtered("active"):
            duplicate = self.search(
                [
                    ("id", "!=", line.id),
                    ("picking_id", "=", line.picking_id.id),
                    ("job_type_id", "=", line.job_type_id.id),
                    ("active", "=", True),
                ],
                limit=1,
            )
            if duplicate:
                raise ValidationError(_("Only one active billing line is allowed per receipt and job type."))

    def write(self, vals):
        protected_fields = {"job_type_id", "quantity", "price_unit", "name", "active"}
        if protected_fields.intersection(vals) and any(self.mapped("invoice_line_id")):
            raise ValidationError(_("You cannot modify a billing line that has already been invoiced."))
        return super().write(vals)

    def unlink(self):
        if any(self.mapped("invoice_line_id")):
            raise ValidationError(_("You cannot delete a billing line that has already been invoiced."))
        return super().unlink()
