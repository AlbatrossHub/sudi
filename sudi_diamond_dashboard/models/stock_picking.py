from odoo import api, fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    sudi_receipt_date = fields.Datetime(
        string="Job Work Date",
        compute="_compute_sudi_receipt_date",
        store=True,
        index=True,
        compute_sudo=True,
        help="Date the job work is counted against on the analytical dashboard: "
             "the confirmed pickup date when there is one, otherwise the scheduled date.",
    )

    @api.depends("sudi_pickup_datetime", "scheduled_date")
    def _compute_sudi_receipt_date(self):
        for picking in self:
            picking.sudi_receipt_date = picking.sudi_pickup_datetime or picking.scheduled_date
