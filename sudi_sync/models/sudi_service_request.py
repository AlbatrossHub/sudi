from odoo import models


class SudiDiamondServiceRequest(models.Model):
    _name = "sudi.diamond.service.request"
    _inherit = ["sudi.diamond.service.request", "sudi.sync.source"]

    def _sudi_sync_pickings(self):
        # The requested services travel inside the pickup, office and job-work
        # documents, so a change to one is a change to its receipt.
        return self.picking_id
