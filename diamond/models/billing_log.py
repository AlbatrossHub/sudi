from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SudiDiamondBillingLog(models.Model):
    """Immutable trail of every settlement and release of a delivered job-work line.

    One row per delivery move per event, so the ledger can answer "which
    invoice / reference statement covered this delivery, when, by whom and
    for how much" without walking the invoice or the chatter.
    """

    _name = "sudi.diamond.billing.log"
    _description = "Diamond Job Work Billing Log"
    _order = "date desc, id desc"

    event = fields.Selection(
        [
            ("billed", "Billed"),
            ("reference", "Closed (reference)"),
            ("released", "Released"),
            ("reopened", "Reopened"),
        ],
        required=True,
        readonly=True,
        index=True,
    )
    date = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True, index=True)
    user_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True, index=True)
    delivery_id = fields.Many2one("stock.picking", string="Delivery", readonly=True, index=True, ondelete="set null")
    receipt_id = fields.Many2one("stock.picking", string="Receipt", readonly=True, index=True, ondelete="set null")
    stock_move_id = fields.Many2one("stock.move", string="Delivery Line", readonly=True, index=True, ondelete="set null")
    job_type_id = fields.Many2one("sudi.diamond.job.type", string="Job Type", readonly=True)
    invoice_id = fields.Many2one("account.move", string="Invoice", readonly=True, index=True, ondelete="set null")
    invoice_line_id = fields.Many2one("account.move.line", string="Invoice Line", readonly=True, ondelete="set null")
    reference_statement_id = fields.Many2one(
        "sudi.diamond.reference.statement",
        string="Reference Statement",
        readonly=True,
        index=True,
        ondelete="set null",
    )
    document_name = fields.Char(
        string="Document",
        compute="_compute_document_name",
        help="Invoice or reference statement number at the time of reading.",
    )
    pcs = fields.Float(string="Pcs", digits="Product Unit", readonly=True)
    carats = fields.Float(string="Carats", digits="Product Unit", readonly=True)
    quantity = fields.Float(string="Billed Qty", digits="Product Unit", readonly=True)
    price_unit = fields.Monetary(string="Rate", currency_field="currency_id", readonly=True)
    amount = fields.Monetary(currency_field="currency_id", readonly=True)
    returned_without_work = fields.Boolean(readonly=True)
    note = fields.Char(readonly=True)

    @api.depends("invoice_id.name", "reference_statement_id.name")
    def _compute_document_name(self):
        for log in self:
            log.document_name = log.invoice_id.name or log.reference_statement_id.name or ""

    def write(self, vals):
        raise UserError(_("Billing log entries cannot be modified."))

    def unlink(self):
        if not self.env.su:
            raise UserError(_("Billing log entries cannot be deleted."))
        return super().unlink()

    @api.model
    def _sudi_log_moves(self, event, moves, invoice=False, statement=False, note=False):
        """Write one entry per delivery move for ``event``.

        Quantities and amounts are frozen from the move's stored billing values
        so a later price change never rewrites history.
        """
        vals_list = []
        for move in moves:
            delivery = move.picking_id
            vals_list.append({
                "event": event,
                "company_id": delivery.company_id.id,
                "partner_id": delivery.partner_id.commercial_partner_id.id,
                "delivery_id": delivery.id,
                "receipt_id": delivery.sudi_origin_receipt_id.id,
                "stock_move_id": move.id,
                "job_type_id": move.sudi_job_type_id.id,
                "invoice_id": invoice.id if invoice else False,
                "invoice_line_id": move.sudi_invoice_line_id.id if invoice else False,
                "reference_statement_id": statement.id if statement else False,
                "pcs": move.sudi_pcs_qty,
                "carats": move.sudi_carats,
                "quantity": move.sudi_billable_qty,
                "price_unit": move.sudi_price_unit,
                "amount": move.sudi_billable_amount,
                "returned_without_work": move.sudi_returned_without_work,
                "note": note or False,
            })
        return self.sudo().create(vals_list)
