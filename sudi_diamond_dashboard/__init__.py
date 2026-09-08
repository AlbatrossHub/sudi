from . import models


def post_init_hook(env):
    """Fill the dashboard aggregation columns for job work that already exists.

    Odoo computes stored fields when it creates their columns, but doing it in
    SQL here is far cheaper on an existing database and is safe to repeat: only
    rows still holding NULL are touched.
    """
    env.cr.execute(
        """
        UPDATE stock_picking
           SET sudi_receipt_date = COALESCE(sudi_pickup_datetime, scheduled_date)
         WHERE sudi_receipt_date IS NULL
        """
    )
    env.cr.execute(
        """
        UPDATE stock_move m
           SET sudi_receipt_date = p.sudi_receipt_date
          FROM stock_picking p
         WHERE m.picking_id = p.id
           AND m.sudi_receipt_date IS NULL
           AND p.sudi_receipt_date IS NOT NULL
        """
    )
    env.cr.execute(
        """
        UPDATE stock_move m
           SET sudi_customer_id = rp.commercial_partner_id
          FROM stock_picking p
          JOIN res_partner rp ON rp.id = p.partner_id
         WHERE m.picking_id = p.id
           AND m.sudi_customer_id IS NULL
        """
    )
