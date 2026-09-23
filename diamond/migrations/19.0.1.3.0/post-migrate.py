def migrate(cr, version):
    """Carry billing-line → invoice-line links into the new many2many.

    Billing lines used to hold a single invoice_line_id; a receipt line can now
    be covered by several invoice lines (backorders), so the link moved to
    sudi_billing_line_account_move_line_rel. The old column is left in place.
    """
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sudi_diamond_billing_line' AND column_name = 'invoice_line_id'
        """
    )
    if not cr.fetchone():
        return
    cr.execute(
        """
        INSERT INTO sudi_billing_line_account_move_line_rel (billing_line_id, invoice_line_id)
        SELECT bl.id, bl.invoice_line_id
        FROM sudi_diamond_billing_line bl
        JOIN account_move_line aml ON aml.id = bl.invoice_line_id
        ON CONFLICT DO NOTHING
        """
    )
    # Invoice lines created by the old engine carry the job type only through
    # the billing line; copy it so the consolidated-line logic can read it.
    cr.execute(
        """
        UPDATE account_move_line aml
        SET sudi_job_type_id = bl.job_type_id
        FROM sudi_diamond_billing_line bl
        WHERE bl.invoice_line_id = aml.id AND aml.sudi_job_type_id IS NULL
        """
    )
