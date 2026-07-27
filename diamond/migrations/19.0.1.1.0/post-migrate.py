def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    receipts = env["stock.picking"].search(
        [
            ("sudi_is_diamond_job_work", "=", True),
            ("picking_type_code", "=", "incoming"),
            ("state", "=", "done"),
        ]
    )
    receipts._sudi_sync_billing_details()
