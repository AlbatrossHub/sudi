from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


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
    receipt_move_id = fields.Many2one(
        "stock.move",
        string="Receipt Line",
        ondelete="cascade",
        index=True,
    )
    sudi_sr = fields.Integer(
        string="Sr",
        related="receipt_move_id.sudi_sr",
        readonly=True,
    )
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
    invoice_line_ids = fields.Many2many(
        "account.move.line",
        "sudi_billing_line_account_move_line_rel",
        "billing_line_id",
        "invoice_line_id",
        string="Invoice Lines",
        copy=False,
        readonly=True,
    )
    reference_line_ids = fields.One2many(
        "sudi.diamond.reference.line",
        "billing_line_id",
        string="Reference Statement Lines",
        readonly=True,
    )
    delivery_move_ids = fields.One2many(
        "stock.move",
        "sudi_billing_line_id",
        string="Delivery Lines",
        readonly=True,
    )
    is_settled = fields.Boolean(
        string="Settled",
        compute="_compute_is_settled",
        help="Every delivered quantity of this receipt line has been invoiced or closed by a reference statement.",
    )
    settlement_names = fields.Char(
        string="Settled On",
        compute="_compute_is_settled",
    )

    _quantity_non_negative = models.Constraint(
        "CHECK(quantity >= 0)",
        "The billing quantity must be zero or positive.",
    )
    _price_unit_non_negative = models.Constraint(
        "CHECK(price_unit >= 0)",
        "The billing unit price must be zero or positive.",
    )

    @api.depends(
        "invoice_line_ids.move_id.state",
        "invoice_line_ids.move_id.name",
        "reference_line_ids.statement_id.state",
        "reference_line_ids.statement_id.name",
        "delivery_move_ids.sudi_billing_state",
    )
    def _compute_is_settled(self):
        for line in self:
            invoices = line.invoice_line_ids.move_id.filtered(lambda move: move.state != "cancel")
            statements = line.reference_line_ids.statement_id.filtered(lambda statement: statement.state == "settled")
            names = [move.name for move in invoices if move.name and move.name != "/"]
            names += statements.mapped("name")
            line.settlement_names = ", ".join(names)
            has_settlement = bool(invoices or statements)
            pending = line.delivery_move_ids.filtered(lambda move: move.sudi_billing_state == "to_bill")
            line.is_settled = has_settlement and not pending

    def _sudi_is_locked(self):
        """A billing line is frozen once nothing delivered against it is left to bill."""
        self.ensure_one()
        return self.is_settled

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

    @api.onchange("job_type_id", "picking_id", "receipt_move_id")
    def _onchange_job_type_id(self):
        for line in self:
            if line.receipt_move_id:
                line.name = line.receipt_move_id._sudi_get_invoice_line_name()
            elif line.job_type_id:
                line.name = line.job_type_id.invoice_description or line.job_type_id.display_name
            if not line.job_type_id:
                continue
            if not line.manual_price:
                price, source = line.job_type_id._sudi_get_price_for_partner_with_source(
                    line.picking_id.partner_id.commercial_partner_id,
                    line.picking_id.company_id,
                )
                line.price_unit = price
                line.price_source = source
                line.manual_price = False

    @api.constrains("picking_id", "receipt_move_id", "active")
    def _check_unique_active_receipt_move(self):
        for line in self.filtered(lambda record: record.active and record.receipt_move_id):
            duplicate = self.search(
                [
                    ("id", "!=", line.id),
                    ("picking_id", "=", line.picking_id.id),
                    ("receipt_move_id", "=", line.receipt_move_id.id),
                    ("active", "=", True),
                ],
                limit=1,
            )
            if duplicate:
                raise ValidationError(_("Only one active billing line is allowed per receipt line."))

    @api.constrains("receipt_move_id", "active", "picking_id")
    def _check_receipt_move_required(self):
        for line in self.filtered(
            lambda record: record.active
            and not record.invoice_line_ids
            and record.picking_id.state == "done"
        ):
            if not line.receipt_move_id:
                raise ValidationError(_("Active billing lines must be linked to a receipt line."))

    def _check_pickup_pending_lock(self):
        if self.env.context.get("sudi_skip_billing_sync") or self.env.context.get("sudi_allow_pickup_billing_edit"):
            return
        for line in self:
            picking = line.picking_id
            if picking and picking.sudi_is_diamond_job_work and picking.picking_type_code == "incoming" and picking.state == "sudi_pickup_pending":
                raise UserError(_("This pickup is still pending you can not input the data please confirm the pick up"))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("sudi_skip_billing_sync") and not self.env.context.get("sudi_allow_pickup_billing_edit"):
            picking_ids = [vals.get("picking_id") for vals in vals_list if vals.get("picking_id")]
            if picking_ids:
                pickings = self.env["stock.picking"].browse(picking_ids)
                for picking in pickings:
                    if picking.sudi_is_diamond_job_work and picking.picking_type_code == "incoming" and picking.state == "sudi_pickup_pending":
                        raise UserError(_("This pickup is still pending you can not input the data please confirm the pick up"))
        return super().create(vals_list)

    def write(self, vals):
        self._check_pickup_pending_lock()
        protected_fields = {"job_type_id", "receipt_move_id", "quantity", "price_unit", "name", "active"}
        if protected_fields.intersection(vals) and any(line._sudi_is_locked() for line in self):
            raise ValidationError(_("You cannot modify a billing line whose deliveries have all been settled."))
        return super().write(vals)

    def unlink(self):
        self._check_pickup_pending_lock()
        if any(self.mapped("invoice_line_ids")) or any(self.mapped("reference_line_ids")):
            raise ValidationError(_("You cannot delete a billing line that has already been settled."))
        return super().unlink()

    def sudi_set_manual_price(self, price_unit):
        """Reviewer override of the rate; kept on the receipt so a re-sync respects it."""
        for line in self:
            if line._sudi_is_locked():
                raise ValidationError(_("This rate is frozen: everything delivered against it is already settled."))
            line.write({"price_unit": price_unit, "manual_price": True, "price_source": "manual"})
        return True

    def sudi_reset_manual_price(self):
        """Drop the manual override and fall back to the customer / job type price."""
        for line in self:
            if line._sudi_is_locked():
                continue
            price, source = line.job_type_id._sudi_get_price_for_partner_with_source(
                line.picking_id.partner_id.commercial_partner_id,
                line.picking_id.company_id,
            )
            line.write({"price_unit": price, "price_source": source, "manual_price": False})
        return True

