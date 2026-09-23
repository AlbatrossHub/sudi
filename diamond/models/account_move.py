import base64
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = ["account.move", "sudi.diamond.whatsapp.mixin"]
    _name = "account.move"

    sudi_is_diamond_job_work_invoice = fields.Boolean(
        string="Diamond Job Work Invoice",
        copy=False,
    )
    sudi_delivery_ids = fields.Many2many(
        "stock.picking",
        "sudi_account_move_stock_picking_rel",
        "move_id",
        "picking_id",
        string="Diamond Deliveries",
        copy=False,
    )
    sudi_receipt_ids = fields.Many2many(
        "stock.picking",
        "sudi_account_move_receipt_rel",
        "move_id",
        "picking_id",
        string="Diamond Receipts",
        compute="_compute_sudi_receipt_ids",
        store=True,
    )
    # Kept for older records and reports; a consolidated invoice spans many
    # receipts, so new code reads sudi_receipt_ids.
    sudi_receipt_id = fields.Many2one(
        "stock.picking",
        string="Diamond Receipt",
        compute="_compute_sudi_receipt_ids",
        store=True,
        copy=False,
        index=True,
    )
    sudi_delivery_count = fields.Integer(compute="_compute_sudi_counts")
    sudi_receipt_count = fields.Integer(compute="_compute_sudi_counts")
    sudi_billing_period_from = fields.Date(string="Job Work From", copy=False)
    sudi_billing_period_to = fields.Date(string="Job Work To", copy=False)
    sudi_annexure_line_ids = fields.One2many(
        "stock.move",
        compute="_compute_sudi_annexure_line_ids",
        string="Annexure Lines",
    )

    @api.depends("sudi_delivery_ids.sudi_origin_receipt_id")
    def _compute_sudi_receipt_ids(self):
        for move in self:
            receipts = move.sudi_delivery_ids.sudi_origin_receipt_id
            move.sudi_receipt_ids = receipts
            move.sudi_receipt_id = receipts[:1]

    def _compute_sudi_counts(self):
        for move in self:
            move.sudi_delivery_count = len(move.sudi_delivery_ids)
            move.sudi_receipt_count = len(move.sudi_receipt_ids)

    def _compute_sudi_annexure_line_ids(self):
        """Delivery lines this invoice settles, plus no-charge returns from the same deliveries."""
        for move in self:
            invoice_lines = move.line_ids
            settled = move.sudi_delivery_ids.move_ids.filtered(
                lambda stock_move: stock_move.state == "done"
                and stock_move.sudi_job_type_id
                and (
                    stock_move.sudi_invoice_line_id in invoice_lines
                    or stock_move.sudi_returned_without_work
                )
            )
            move.sudi_annexure_line_ids = settled.sorted(
                key=lambda stock_move: (stock_move.picking_id.date_done or fields.Datetime.now(), stock_move.picking_id.id, stock_move.sudi_sr, stock_move.id)
            )

    def _sudi_get_annexure_groups(self):
        """Annexure rows grouped per delivery, in delivery-date order, for the report."""
        self.ensure_one()
        groups = []
        for delivery in self.sudi_delivery_ids.sorted(key=lambda picking: (picking.date_done or fields.Datetime.now(), picking.id)):
            lines = self.sudi_annexure_line_ids.filtered(lambda stock_move: stock_move.picking_id == delivery)
            if not lines:
                continue
            groups.append({
                "delivery": delivery,
                "receipt": delivery.sudi_origin_receipt_id,
                "date": fields.Date.to_date(delivery.date_done) if delivery.date_done else False,
                "lines": lines,
                "pcs": sum(lines.mapped("sudi_pcs_qty")),
                "carats": sum(lines.mapped("sudi_carats")),
                "amount": sum(lines.mapped("sudi_billable_amount")),
            })
        return groups

    def _sudi_get_annexure_job_type_totals(self):
        """Per job type totals across the annexure; charged lines only."""
        self.ensure_one()
        totals = {}
        order = []
        for move in self.sudi_annexure_line_ids.filtered(lambda stock_move: not stock_move.sudi_returned_without_work):
            job_type = move.sudi_job_type_id
            if job_type.id not in totals:
                totals[job_type.id] = {"name": job_type.name, "pcs": 0.0, "carats": 0.0, "amount": 0.0}
                order.append(job_type.id)
            totals[job_type.id]["pcs"] += move.sudi_pcs_qty
            totals[job_type.id]["carats"] += move.sudi_carats
            totals[job_type.id]["amount"] += move.sudi_billable_amount
        return [totals[key] for key in order]

    def action_sudi_view_receipt(self):
        self.ensure_one()
        return self._sudi_action_view_pickings(self.sudi_receipt_ids, _("Diamond Receipts"))

    def action_sudi_view_deliveries(self):
        self.ensure_one()
        return self._sudi_action_view_pickings(self.sudi_delivery_ids, _("Diamond Deliveries"))

    def _sudi_action_view_pickings(self, pickings, name):
        action = self.env["ir.actions.actions"]._for_xml_id("stock.action_picking_tree_all")
        action["name"] = name
        action["domain"] = [("id", "in", pickings.ids)]
        if len(pickings) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = pickings.id
        return action

    # ------------------------------------------------------------------
    # Settlement trail: the delivery's billing state is derived from this
    # invoice's state, so only the log and the chatter need writing here.
    # ------------------------------------------------------------------
    def _sudi_settled_moves(self):
        return self.line_ids.sudi_stock_move_ids

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for invoice in posted.filtered(lambda move: move.sudi_is_diamond_job_work_invoice and move.move_type == "out_invoice"):
            invoice.sudi_delivery_ids._sudi_post_billing_chatter(
                _("Billed on %(invoice)s — %(amount)s.", invoice=invoice._get_html_link(), amount=invoice.currency_id.format(invoice.amount_total)),
            )
            if not self.env.context.get("sudi_skip_invoice_whatsapp"):
                invoice._sudi_notify_invoice_posted()
        return posted

    # ------------------------------------------------------------------
    # WhatsApp: the posted invoice PDF (with its annexure) goes to the customer
    # ------------------------------------------------------------------
    def _sudi_get_whatsapp_template_context(self, event):
        self.ensure_one()
        partner = self.partner_id
        return {
            "customer_name": partner.name or _("Customer"),
            "invoice_number": self.name or "",
            "amount": self.currency_id.format(self.amount_total),
            "due_date": fields.Date.to_string(self.invoice_date_due) if self.invoice_date_due else "",
            "invoice_date": fields.Date.to_string(self.invoice_date) if self.invoice_date else "",
            "period": (
                "%s – %s" % (self.sudi_billing_period_from, self.sudi_billing_period_to)
                if self.sudi_billing_period_from and self.sudi_billing_period_to else ""
            ),
            "deliveries": ", ".join(self.sudi_delivery_ids.sorted("name").mapped("name")),
            "receipts": ", ".join(self.sudi_receipt_ids.sorted("name").mapped("name")),
            "phone": partner.phone or "",
            "url": self.get_base_url() + "/odoo/action-account.action_move_out_invoice/%s" % self.id,
        }

    def _sudi_get_invoice_pdf_attachment(self):
        self.ensure_one()
        try:
            pdf_content, _type = self.env["ir.actions.report"].sudo()._render_qweb_pdf("account.account_invoices", [self.id])
        except Exception:
            _logger.exception("Failed to render invoice PDF for %s", self.display_name)
            return self.env["ir.attachment"]
        return self.env["ir.attachment"].sudo().create({
            "name": "Invoice_%s.pdf" % (self.name or "draft").replace("/", "_"),
            "type": "binary",
            "datas": base64.b64encode(pdf_content),
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "application/pdf",
        })

    def _sudi_notify_invoice_posted(self):
        """Send the customer the invoice PDF on WhatsApp; failures only log."""
        for invoice in self:
            partner = invoice.partner_shipping_id or invoice.partner_id
            phone = partner.phone or invoice.partner_id.phone
            if not phone:
                _logger.info("No phone on %s: invoice %s not sent on WhatsApp", partner.display_name, invoice.display_name)
                continue
            body = invoice._sudi_render_event_whatsapp_message("invoice_posted")
            if not body:
                continue
            attachment = invoice._sudi_get_invoice_pdf_attachment()
            invoice._sudi_send_whatsapp_message(
                recipient_phone=phone,
                body_text=body,
                attachment=attachment or None,
                partner=partner,
            )

    def button_cancel(self):
        was_active = self.filtered(lambda move: move.sudi_is_diamond_job_work_invoice and move.state != "cancel")
        res = super().button_cancel()
        for invoice in was_active:
            moves = invoice._sudi_settled_moves()
            self.env["sudi.diamond.billing.log"]._sudi_log_moves("released", moves, invoice=invoice, note=_("Invoice cancelled"))
            invoice.sudi_delivery_ids._sudi_post_billing_chatter(
                _("Released for billing — invoice %s cancelled.", invoice.display_name),
            )
        return res

    def button_draft(self):
        was_cancelled = self.filtered(lambda move: move.sudi_is_diamond_job_work_invoice and move.state == "cancel")
        res = super().button_draft()
        for invoice in was_cancelled:
            moves = invoice._sudi_settled_moves()
            self.env["sudi.diamond.billing.log"]._sudi_log_moves("billed", moves, invoice=invoice, note=_("Invoice reset to draft"))
        return res

    def unlink(self):
        job_work_invoices = self.filtered(lambda move: move.sudi_is_diamond_job_work_invoice and move.state != "cancel")
        for invoice in job_work_invoices:
            moves = invoice._sudi_settled_moves()
            self.env["sudi.diamond.billing.log"]._sudi_log_moves("released", moves, note=_("Invoice %s deleted", invoice.display_name))
            invoice.sudi_delivery_ids._sudi_post_billing_chatter(
                _("Released for billing — draft invoice %s deleted.", invoice.display_name),
            )
        return super().unlink()


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    sudi_stock_move_ids = fields.One2many(
        "stock.move",
        "sudi_invoice_line_id",
        string="Diamond Delivery Lines",
    )
    sudi_billing_line_ids = fields.Many2many(
        "sudi.diamond.billing.line",
        "sudi_billing_line_account_move_line_rel",
        "invoice_line_id",
        "billing_line_id",
        string="Diamond Billing Lines",
        copy=False,
    )
    sudi_job_type_id = fields.Many2one(
        "sudi.diamond.job.type",
        string="Diamond Job Type",
        copy=False,
        index=True,
    )
    sudi_delivery_names = fields.Char(
        string="Diamond Deliveries",
        compute="_compute_sudi_delivery_names",
    )

    @api.depends("sudi_stock_move_ids.picking_id.name")
    def _compute_sudi_delivery_names(self):
        for line in self:
            names = sorted(set(line.sudi_stock_move_ids.picking_id.mapped("name")))
            line.sudi_delivery_names = ", ".join(names)
