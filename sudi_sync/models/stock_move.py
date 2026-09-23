from odoo import models


class StockMove(models.Model):
    _name = "stock.move"
    _inherit = ["stock.move", "sudi.sync.source"]

    def _sudi_sync_pickings(self):
        # An item line changing is a change to the picking's payload: pcs,
        # carats, size and job type all travel inside the picking document.
        return self.picking_id
