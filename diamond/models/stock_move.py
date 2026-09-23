from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero, float_round


class StockMove(models.Model):
    _inherit = "stock.move"

    sudi_sr = fields.Integer(string="Sr")
    sudi_size = fields.Char(string="Size")
    sudi_pcs_qty = fields.Float(string="Pcs / Qty", digits="Product Unit")
    sudi_carats = fields.Float(string="Carats", digits="Product Unit")
    sudi_job_type_id = fields.Many2one(
        "sudi.diamond.job.type",
        string="Job Type",
        domain=[("active", "=", True)],
        check_company=True,
    )
    sudi_remarks = fields.Char(string="Remarks")
    sudi_origin_receipt_move_id = fields.Many2one(
        "stock.move",
        string="Receipt Move",
        copy=False,
        index=True,
    )
    sudi_invoice_line_id = fields.Many2one(
        "account.move.line",
        string="Diamond Invoice Line",
        copy=False,
        readonly=True,
        index=True,
        ondelete="set null",
    )
    sudi_reference_line_id = fields.Many2one(
        "sudi.diamond.reference.line",
        string="Reference Statement Line",
        copy=False,
        readonly=True,
        index=True,
        ondelete="set null",
    )
    sudi_returned_without_work = fields.Boolean(
        string="Returned Without Job Work",
        copy=False,
        help="The goods went back to the customer as received: nothing is charged, "
             "but the line is still listed on the invoice annexure.",
    )
    sudi_billing_line_ids = fields.One2many(
        "sudi.diamond.billing.line",
        "receipt_move_id",
        string="Billing Lines",
    )
    # Billing values are resolved from the origin receipt's billing line and
    # stored on the delivery move, so the review screen can filter, group and
    # total without touching the receipt.
    sudi_currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    sudi_billing_line_id = fields.Many2one(
        "sudi.diamond.billing.line",
        string="Billing Line",
        compute="_compute_sudi_billing_values",
        store=True,
        index=True,
    )
    sudi_price_unit = fields.Monetary(
        string="Rate",
        currency_field="sudi_currency_id",
        compute="_compute_sudi_billing_values",
        store=True,
    )
    sudi_billable_qty = fields.Float(
        string="Billable Qty",
        digits="Product Unit",
        compute="_compute_sudi_billing_values",
        store=True,
    )
    sudi_billable_amount = fields.Monetary(
        string="Billable Amount",
        currency_field="sudi_currency_id",
        compute="_compute_sudi_billing_values",
        store=True,
    )
    sudi_billing_state = fields.Selection(
        [
            ("none", "Not Billable"),
            ("to_bill", "To Bill"),
            ("no_charge", "No Charge"),
            ("billed", "Billed"),
            ("closed", "Closed"),
        ],
        string="Billing State",
        compute="_compute_sudi_billing_state",
        store=True,
        index=True,
    )

    @api.depends(
        "state",
        "quantity",
        "product_uom_qty",
        "sudi_pcs_qty",
        "sudi_carats",
        "sudi_job_type_id.invoice_basis",
        "sudi_returned_without_work",
        "sudi_origin_receipt_move_id.sudi_billing_line_ids.active",
        "sudi_origin_receipt_move_id.sudi_billing_line_ids.price_unit",
        "picking_id.picking_type_code",
        "picking_id.sudi_is_diamond_job_work",
    )
    def _compute_sudi_billing_values(self):
        for move in self:
            billing_line = self.env["sudi.diamond.billing.line"]
            if move._sudi_is_delivery_move():
                billing_line = move.sudi_origin_receipt_move_id.sudi_billing_line_ids.filtered("active")[:1]
            move.sudi_billing_line_id = billing_line
            if not billing_line or move.sudi_returned_without_work:
                move.sudi_price_unit = 0.0
                move.sudi_billable_qty = 0.0 if move.sudi_returned_without_work else (
                    move._sudi_get_invoice_quantity() if move._sudi_is_delivery_move() else 0.0
                )
                move.sudi_billable_amount = 0.0
                continue
            quantity = move._sudi_get_invoice_quantity()
            move.sudi_price_unit = billing_line.price_unit
            move.sudi_billable_qty = quantity
            move.sudi_billable_amount = quantity * billing_line.price_unit

    @api.depends(
        "state",
        "sudi_job_type_id",
        "sudi_returned_without_work",
        "sudi_invoice_line_id.move_id.state",
        "sudi_reference_line_id.statement_id.state",
        "picking_id.state",
        "picking_id.picking_type_code",
        "picking_id.sudi_is_diamond_job_work",
        "picking_id.sudi_origin_receipt_id",
    )
    def _compute_sudi_billing_state(self):
        for move in self:
            if not move._sudi_is_delivery_move() or move.state != "done" or not move.sudi_job_type_id:
                move.sudi_billing_state = "none"
            elif move.sudi_invoice_line_id and move.sudi_invoice_line_id.move_id.state != "cancel":
                move.sudi_billing_state = "billed"
            elif move.sudi_reference_line_id and move.sudi_reference_line_id.statement_id.state == "settled":
                move.sudi_billing_state = "closed"
            elif move.sudi_returned_without_work:
                move.sudi_billing_state = "no_charge"
            else:
                move.sudi_billing_state = "to_bill"

    def _sudi_is_delivery_move(self):
        self.ensure_one()
        picking = self.picking_id
        return bool(
            picking
            and picking.sudi_is_diamond_job_work
            and picking.picking_type_code == "outgoing"
            and picking.sudi_origin_receipt_id
        )

    def _sudi_is_settled(self):
        self.ensure_one()
        if self.sudi_billing_state in ("billed", "closed"):
            return True
        # A no-charge return has no invoice line of its own, but it is listed
        # on the annexure of whatever settled its delivery.
        if self.sudi_billing_state == "no_charge":
            return self.picking_id.sudi_billing_status in ("billed", "closed")
        return False

    def action_sudi_toggle_returned_without_work(self):
        settled = self.filtered(lambda move: move._sudi_is_settled())
        if settled:
            raise UserError(_("Settled delivery lines cannot be flagged as returned without job work."))
        for move in self:
            move.sudi_returned_without_work = not move.sudi_returned_without_work
        return True

    @api.model_create_multi
    def create(self, vals_list):
        self._sudi_prepare_create_vals(vals_list)
        return super().create(vals_list)

    @api.model
    def _sudi_prepare_create_vals(self, vals_list):
        picking_ids = {vals.get("picking_id") for vals in vals_list if vals.get("picking_id")}
        pickings = {picking.id: picking for picking in self.env["stock.picking"].browse(picking_ids)}
        next_sr_by_picking = {}

        for vals in vals_list:
            picking_id = vals.get("picking_id")
            picking = pickings.get(picking_id)
            if (
                picking
                and picking.sudi_is_diamond_job_work
                and picking.picking_type_code == "incoming"
                and picking.state == "sudi_pickup_pending"
                and not self.env.context.get("sudi_allow_pickup_edit")
            ):
                raise UserError(_("This pickup is still pending you can not input the data please confirm the pick up"))

            if picking and picking.sudi_is_diamond_job_work and not vals.get("sudi_sr"):
                if picking_id not in next_sr_by_picking:
                    existing_sr = picking.move_ids.mapped("sudi_sr")
                    next_sr_by_picking[picking_id] = (max(existing_sr) if existing_sr else 0) + 1
                vals["sudi_sr"] = next_sr_by_picking[picking_id]
                next_sr_by_picking[picking_id] += 1

            if "sudi_pcs_qty" in vals:
                vals["product_uom_qty"] = vals["sudi_pcs_qty"]
                if vals.get("state") not in ("done", "cancel"):
                    vals["quantity"] = vals["sudi_pcs_qty"]

    def write(self, vals):
        if not self.env.context.get("sudi_allow_pickup_edit"):
            for move in self:
                picking = move.picking_id
                if (
                    picking
                    and picking.sudi_is_diamond_job_work
                    and picking.picking_type_code == "incoming"
                    and picking.state == "sudi_pickup_pending"
                ):
                    raise UserError(_("This pickup is still pending you can not input the data please confirm the pick up"))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get("sudi_allow_pickup_edit"):
            for move in self:
                picking = move.picking_id
                if (
                    picking
                    and picking.sudi_is_diamond_job_work
                    and picking.picking_type_code == "incoming"
                    and picking.state == "sudi_pickup_pending"
                ):
                    raise UserError(_("This pickup is still pending you can not input the data please confirm the pick up"))
        return super().unlink()

    @api.onchange("sudi_pcs_qty")
    def _onchange_sudi_pcs_qty(self):
        for move in self:
            if not move.picking_id.sudi_is_diamond_job_work:
                continue
            if move.product_uom_qty != move.sudi_pcs_qty:
                move.product_uom_qty = move.sudi_pcs_qty
            if move.state not in ("done", "cancel") and move.quantity != move.sudi_pcs_qty:
                move.quantity = move.sudi_pcs_qty

    @api.onchange("product_uom_qty")
    def _onchange_sudi_product_uom_qty(self):
        for move in self:
            if (
                move.picking_id.sudi_is_diamond_job_work
                and not move.sudi_pcs_qty
                and move.sudi_pcs_qty != move.product_uom_qty
            ):
                move.sudi_pcs_qty = move.product_uom_qty

    def _prepare_move_split_vals(self, qty):
        vals = super()._prepare_move_split_vals(qty)
        for move in self:
            if not move.picking_id.sudi_is_diamond_job_work:
                continue
            original_qty = move.product_uom_qty or move.product_qty
            if float_is_zero(original_qty, precision_rounding=move.product_uom.rounding):
                continue
            ratio = qty / original_qty
            vals.update(move._sudi_prepare_split_quantity_vals(ratio))
        return vals

    @api.model
    def _prepare_merge_moves_distinct_fields(self):
        fields = super()._prepare_merge_moves_distinct_fields()
        if self.picking_id.filtered("sudi_is_diamond_job_work"):
            fields.extend([
                "sudi_sr",
                "sudi_job_type_id",
                "sudi_size",
                "sudi_pcs_qty",
                "sudi_carats",
                "sudi_remarks",
            ])
        return fields

    def _action_confirm(self, merge=True, merge_into=False, create_proc=True):
        if self.picking_id.filtered("sudi_is_diamond_job_work"):
            merge = False
        return super()._action_confirm(
            merge=merge,
            merge_into=merge_into,
            create_proc=create_proc,
        )

    def _split(self, qty, restrict_partner_id=False):
        original_values = {}
        for move in self.filtered(lambda m: m.picking_id.sudi_is_diamond_job_work):
            if float_is_zero(move.product_qty, precision_rounding=move.product_id.uom_id.rounding):
                continue
            ratio = qty / move.product_qty
            original_values[move.id] = move._sudi_prepare_split_quantity_vals(ratio)

        new_move_vals = super()._split(qty, restrict_partner_id=restrict_partner_id)

        for move in self.filtered(lambda m: m.id in original_values):
            split_vals = original_values[move.id]
            move.write({
                "sudi_pcs_qty": max(move.sudi_pcs_qty - split_vals.get("sudi_pcs_qty", 0.0), 0.0),
                "sudi_carats": max(move.sudi_carats - split_vals.get("sudi_carats", 0.0), 0.0),
            })
        return new_move_vals

    def _sudi_prepare_split_quantity_vals(self, ratio):
        self.ensure_one()
        ratio = max(min(ratio, 1.0), 0.0)
        return {
            "sudi_sr": self.sudi_sr,
            "sudi_size": self.sudi_size,
            "sudi_pcs_qty": float_round(self.sudi_pcs_qty * ratio, precision_digits=2),
            "sudi_carats": float_round(self.sudi_carats * ratio, precision_digits=3),
            "sudi_job_type_id": self.sudi_job_type_id.id,
            "sudi_remarks": self.sudi_remarks,
            "sudi_origin_receipt_move_id": self.sudi_origin_receipt_move_id.id,
        }

    def _sudi_get_invoice_quantity(self):
        self.ensure_one()
        basis = self.sudi_job_type_id.invoice_basis
        if basis == "carats":
            return self.sudi_carats
        if basis == "manual":
            return self.quantity or self.product_uom_qty
        return self.sudi_pcs_qty or self.quantity or self.product_uom_qty

    def _sudi_get_invoice_line_name(self):
        self.ensure_one()
        if self.sudi_job_type_id.invoice_description:
            return self.sudi_job_type_id.invoice_description
        parts = [self.sudi_job_type_id.display_name]
        if self.description_picking:
            parts.append(self.description_picking)
        if self.sudi_size:
            parts.append("Size: %s" % self.sudi_size)
        if self.sudi_carats:
            parts.append("Carats: %s" % self.sudi_carats)
        if self.sudi_remarks:
            parts.append("Remarks: %s" % self.sudi_remarks)
        return "\n".join(parts)
