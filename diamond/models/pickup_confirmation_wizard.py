from odoo import _, fields, models


class SudiPickupConfirmationWizard(models.TransientModel):
    _name = "sudi.pickup.confirmation.wizard"
    _description = "Pickup Confirmation & Cancellation Wizard"

    picking_id = fields.Many2one(
        "stock.picking",
        string="Receipt",
        required=True,
        readonly=True,
    )
    action_type = fields.Selection(
        [("confirm", "Confirm Pickup"), ("cancel", "Cancel Pickup")],
        string="Action Type",
        required=True,
        readonly=True,
    )
    message = fields.Html(
        compute="_compute_message",
        string="Message",
    )

    def _compute_message(self):
        for wizard in self:
            picking_name = wizard.picking_id.name or _("Receipt")
            if wizard.action_type == "confirm":
                wizard.message = _(
                    "<p>Are you sure you want to confirm pickup for <b>%s</b>?</p>"
                ) % picking_name
            elif wizard.action_type == "cancel":
                wizard.message = _(
                    "<p>Are you sure you want to cancel pickup for <b>%s</b>?</p>"
                    "<p>This will archive the record and send a cancellation intimation message to the customer.</p>"
                ) % picking_name
            else:
                wizard.message = ""

    def action_process(self):
        self.ensure_one()
        picking = self.picking_id
        if self.action_type == "confirm":
            picking.action_sudi_confirm_pickup()
        elif self.action_type == "cancel":
            picking.action_sudi_cancel_pickup()
        return {"type": "ir.actions.act_window_close"}
