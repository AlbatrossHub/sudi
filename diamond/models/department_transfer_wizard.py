from odoo import _, fields, models
from odoo.exceptions import UserError


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
        picking = self.picking_id
        if (
            not picking.sudi_is_diamond_job_work
            or picking.picking_type_code != "incoming"
            or picking.state != "assigned"
        ):
            raise UserError(_("Department transfer is only available on diamond job-work receipts in progress."))
        picking.write({
            "sudi_current_department_id": self.department_id.id,
        })
        return {"type": "ir.actions.act_window_close"}
