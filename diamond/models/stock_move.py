from odoo import api, fields, models
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
    )

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
