from odoo import fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    # Stored so the dashboard can group on them: _read_group cannot group by a
    # related field unless it is materialised on this table.
    sudi_receipt_date = fields.Datetime(
        string="Job Work Date",
        related="picking_id.sudi_receipt_date",
        store=True,
        index=True,
    )
    sudi_customer_id = fields.Many2one(
        "res.partner",
        string="Job Work Customer",
        related="picking_id.partner_id.commercial_partner_id",
        store=True,
        index=True,
    )
