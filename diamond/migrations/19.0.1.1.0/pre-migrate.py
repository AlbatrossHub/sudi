def migrate(cr, version):
    cr.execute(
        """
        UPDATE sudi_diamond_billing_line
           SET active = FALSE
         WHERE active = TRUE
           AND invoice_line_id IS NULL
        """
    )
