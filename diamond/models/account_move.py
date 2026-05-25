from odoo import _, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    sudi_is_diamond_job_work_invoice = fields.Boolean(
        string="Diamond Job Work Invoice",
        copy=False,
    )
    sudi_receipt_id = fields.Many2one(
        "stock.picking",
        string="Diamond Receipt",
        copy=False,
        index=True,
    )
    sudi_delivery_ids = fields.Many2many(
        "stock.picking",
        "sudi_account_move_stock_picking_rel",
        "move_id",
        "picking_id",
        string="Diamond Deliveries",
        copy=False,
    )
    sudi_delivery_count = fields.Integer(compute="_compute_sudi_delivery_count")

    def _compute_sudi_delivery_count(self):
        for move in self:
            move.sudi_delivery_count = len(move.sudi_delivery_ids)

    def action_sudi_view_receipt(self):
        self.ensure_one()
        return self._sudi_action_view_pickings(self.sudi_receipt_id, _("Diamond Receipt"))

    def action_sudi_view_deliveries(self):
        self.ensure_one()
        return self._sudi_action_view_pickings(self.sudi_delivery_ids, _("Diamond Deliveries"))

    def _sudi_action_view_pickings(self, pickings, name):
        action = self.env["ir.actions.actions"]._for_xml_id("stock.action_picking_tree_all")
        action["name"] = name
        action["domain"] = [("id", "in", pickings.ids)]
        if len(pickings) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = pickings.id
        return action


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    sudi_stock_move_id = fields.Many2one(
        "stock.move",
        string="Diamond Delivery Move",
        copy=False,
        index=True,
        ondelete="set null",
    )
