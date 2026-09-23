from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class SudiDiamondReferenceStatement(models.Model):
    """Off-book settlement of delivered job work.

    Holds exactly the lines an invoice would have carried, but creates no
    journal entry and no customer document. Deliveries it covers are shown
    to ordinary users only as "Closed"; the statement itself is visible to
    the Reference Ledger group.
    """

    _name = "sudi.diamond.reference.statement"
    _description = "Diamond Job Work Reference Statement"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(required=True, readonly=True, copy=False, default="/")
    date = fields.Date(required=True, readonly=True, default=fields.Date.context_today)
    state = fields.Selection(
        [("settled", "Settled"), ("reopened", "Reopened")],
        default="settled",
        required=True,
        readonly=True,
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Customer", required=True, readonly=True, index=True)
    user_id = fields.Many2one("res.users", string="Settled By", readonly=True, default=lambda self: self.env.user)
    delivery_ids = fields.Many2many(
        "stock.picking",
        "sudi_reference_statement_stock_picking_rel",
        "statement_id",
        "picking_id",
        string="Deliveries",
        readonly=True,
    )
    receipt_ids = fields.Many2many(
        "stock.picking",
        compute="_compute_receipt_ids",
        string="Receipts",
    )
    line_ids = fields.One2many("sudi.diamond.reference.line", "statement_id", string="Lines", readonly=True)
    pcs_total = fields.Float(string="Pcs", digits="Product Unit", compute="_compute_totals", store=True)
    carats_total = fields.Float(string="Carats", digits="Product Unit", compute="_compute_totals", store=True)
    amount_total = fields.Monetary(currency_field="currency_id", compute="_compute_totals", store=True)
    note = fields.Text()
    reopen_reason = fields.Text(readonly=True)

    @api.depends("delivery_ids.sudi_origin_receipt_id")
    def _compute_receipt_ids(self):
        for statement in self:
            statement.receipt_ids = statement.delivery_ids.sudi_origin_receipt_id

    @api.depends("line_ids.pcs", "line_ids.carats", "line_ids.amount")
    def _compute_totals(self):
        for statement in self:
            statement.pcs_total = sum(statement.line_ids.mapped("pcs"))
            statement.carats_total = sum(statement.line_ids.mapped("carats"))
            statement.amount_total = sum(statement.line_ids.mapped("amount"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].with_company(vals.get("company_id")).next_by_code(
                    "sudi.diamond.reference.statement"
                ) or "/"
        return super().create(vals_list)

    def unlink(self):
        if any(statement.state == "settled" for statement in self):
            raise UserError(_("Reopen a reference statement before deleting it."))
        return super().unlink()

    def action_reopen(self, reason=False):
        """Release the covered deliveries back to "To bill". Admin only."""
        if not self.env.user.has_group("diamond.group_sudi_reference_ledger"):
            raise AccessError(_("Only Reference Ledger administrators can reopen a reference statement."))
        for statement in self:
            if statement.state != "settled":
                continue
            moves = statement.line_ids.stock_move_id
            statement.write({"state": "reopened", "reopen_reason": reason or False})
            # State is derived from the statement, so the moves flip back to
            # "to bill" on their own; only the trail and the chatter are ours.
            self.env["sudi.diamond.billing.log"]._sudi_log_moves(
                "reopened", moves, statement=statement, note=reason or False
            )
            statement.delivery_ids._sudi_post_billing_chatter(
                _("Released for billing — reference statement %s reopened.", statement.name)
            )
        return True


class SudiDiamondReferenceLine(models.Model):
    _name = "sudi.diamond.reference.line"
    _description = "Diamond Job Work Reference Statement Line"
    _order = "date, delivery_id, stock_move_id"

    statement_id = fields.Many2one(
        "sudi.diamond.reference.statement",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(related="statement_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="statement_id.currency_id", readonly=True)
    partner_id = fields.Many2one(related="statement_id.partner_id", store=True, readonly=True)
    state = fields.Selection(related="statement_id.state", store=True, readonly=True)
    date = fields.Date(string="Delivered", readonly=True)
    delivery_id = fields.Many2one("stock.picking", string="Delivery", readonly=True, index=True)
    receipt_id = fields.Many2one("stock.picking", string="Receipt", readonly=True, index=True)
    stock_move_id = fields.Many2one("stock.move", string="Delivery Line", readonly=True, index=True)
    billing_line_id = fields.Many2one("sudi.diamond.billing.line", string="Billing Line", readonly=True)
    job_type_id = fields.Many2one("sudi.diamond.job.type", string="Job Type", readonly=True, index=True)
    size = fields.Char(readonly=True)
    pcs = fields.Float(string="Pcs", digits="Product Unit", readonly=True)
    carats = fields.Float(string="Carats", digits="Product Unit", readonly=True)
    quantity = fields.Float(string="Charged Qty", digits="Product Unit", readonly=True)
    price_unit = fields.Monetary(string="Rate", currency_field="currency_id", readonly=True)
    amount = fields.Monetary(string="Charge", currency_field="currency_id", readonly=True)
    returned_without_work = fields.Boolean(readonly=True)
