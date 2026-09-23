from odoo import models


class AccountAnalyticLine(models.Model):
    _name = "account.analytic.line"
    _inherit = ["account.analytic.line", "sudi.sync.source"]

    def _sudi_sync_pickings(self):
        # Timesheets move total_hours and the running timer on a job-work
        # document, so a phone watching that receipt needs to hear about them.
        return self.sudi_picking_id
