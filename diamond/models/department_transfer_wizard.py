from odoo import fields, models


class SudiDiamondDepartmentTransferWizard(models.TransientModel):
    _name = "sudi.diamond.department.transfer.wizard"
    _description = "Diamond Job Work Department Transfer"

    picking_id = fields.Many2one(
        "stock.picking",
        string="Receipt",
        required=True,
        readonly=True,
    )
    department_id = fields.Many2one(
        "sudi.diamond.job.type",
        string="Transfer Department To",
        required=True,
        domain="[('active', '=', True)]",
    )

    def action_confirm(self):
        self.ensure_one()
        # The validation and the write live on the picking, so the mobile API
        # and this wizard cannot drift apart.
        self.picking_id._sudi_transfer_department(self.department_id)
        return {"type": "ir.actions.act_window_close"}
