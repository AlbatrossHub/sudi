from datetime import datetime, time

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class SudiDiamondBillingReview(models.AbstractModel):
    """RPC service behind the Billing Review client action.

    Everything the screen shows or does goes through here, so the group check
    is applied to the data and the actions, not only to the menu entry.
    """

    _name = "sudi.diamond.billing.review"
    _description = "Diamond Job Work Billing Review"

    REVIEW_GROUP = "diamond.group_sudi_billing_reviewer"
    LEDGER_GROUP = "diamond.group_sudi_reference_ledger"

    OPEN_STATUSES = ("to_bill", "partial")
    CLOSED_STATUSES = ("billed", "closed", "no_charge")

    STATUS_LABELS = {
        "to_bill": "To bill",
        "partial": "Partially billed",
        "billed": "Billed",
        "closed": "Closed",
        "no_charge": "No charge",
        "none": "Not billable",
    }
    RECEIPT_STAGES = {
        "sudi_pickup_pending": ("Pick up pending", "pend"),
        "draft": ("Job work in progress", "prog"),
        "waiting": ("Job work in progress", "prog"),
        "confirmed": ("Job work in progress", "prog"),
        "assigned": ("Job work in progress", "prog"),
    }

    # ------------------------------------------------------------------
    # Access & helpers
    # ------------------------------------------------------------------
    @api.model
    def _check_access(self):
        if not self.env.user.has_group(self.REVIEW_GROUP):
            raise AccessError(_("Only Billing Reviewers can use the Billing Review."))

    @api.model
    def _has_ledger_access(self):
        return self.env.user.has_group(self.LEDGER_GROUP)

    @api.model
    def _tz(self):
        return pytz.timezone(self.env.user.tz or "Asia/Kolkata")

    @api.model
    def _local_today(self):
        return datetime.now(self._tz()).date()

    @api.model
    def _to_utc(self, day, end=False):
        """Local midnight (or end of day) of ``day`` as a naive UTC datetime."""
        naive = datetime.combine(day, time.max if end else time.min)
        return self._tz().localize(naive).astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _local_date(self, value):
        if not value:
            return False
        return fields.Datetime.context_timestamp(self.with_context(tz=self._tz().zone), value).date()

    @api.model
    def _base_domain(self):
        return [
            ("sudi_is_diamond_job_work", "=", True),
            ("picking_type_code", "=", "outgoing"),
            ("sudi_origin_receipt_id", "!=", False),
            ("state", "=", "done"),
            ("sudi_billing_status", "!=", "none"),
            ("company_id", "in", self.env.companies.ids),
        ]

    @api.model
    def _due_domain(self):
        return self._base_domain() + [("sudi_billing_status", "in", self.OPEN_STATUSES)]

    @api.model
    def _month_start(self):
        today = self._local_today()
        return today.replace(day=1)

    @api.model
    def _monthly_due(self):
        """Deliveries of monthly customers that were delivered before this month."""
        return self.env["stock.picking"].search(
            self._due_domain() + [
                ("partner_id.commercial_partner_id.sudi_invoice_policy", "=", "monthly"),
                ("date_done", "<", self._to_utc(self._month_start())),
            ]
        )

    def _doc_entries(self, delivery):
        """Documents that settled ``delivery``; reference statements only for ledger users."""
        docs = [
            {"model": "account.move", "id": move.id, "name": move.name if move.name != "/" else _("Draft invoice"), "state": move.state}
            for move in delivery.sudi_billed_invoice_ids.filtered(lambda move: move.state != "cancel")
        ]
        if self._has_ledger_access():
            docs += [
                {"model": "sudi.diamond.reference.statement", "id": statement.id, "name": statement.name, "state": statement.state}
                for statement in delivery.sudi_reference_statement_ids.filtered(lambda statement: statement.state == "settled")
            ]
        return docs

    def _row(self, delivery):
        is_open = delivery.sudi_billing_status in self.OPEN_STATUSES
        moves = delivery.move_ids.filtered(lambda move: move.state == "done" and move.sudi_job_type_id)
        jobs = []
        for move in moves.sorted(key=lambda move: (move.sudi_sr or 0, move.id)):
            entry = {"name": move.sudi_job_type_id.name, "no_charge": move.sudi_returned_without_work}
            if entry not in jobs:
                jobs.append(entry)
        if is_open:
            billable = moves.filtered(lambda move: move.sudi_billing_state == "to_bill")
            pcs = sum(billable.mapped("sudi_pcs_qty"))
            carats = sum(billable.mapped("sudi_carats"))
            amount = delivery.sudi_billable_amount
        else:
            pcs = delivery.sudi_total_pcs
            carats = delivery.sudi_total_carats
            amount = delivery.sudi_settled_amount
        partner = delivery.partner_id.commercial_partner_id
        return {
            "id": delivery.id,
            "name": delivery.name,
            "receipt": delivery.sudi_origin_receipt_id.name,
            "receipt_id": delivery.sudi_origin_receipt_id.id,
            "customer": partner.display_name,
            "customer_id": partner.id,
            "date": fields.Date.to_string(self._local_date(delivery.date_done)),
            "month": fields.Date.to_string(self._local_date(delivery.date_done))[:7] if delivery.date_done else "",
            "pcs": pcs,
            "carats": carats,
            "amount": amount,
            "status": delivery.sudi_billing_status,
            "status_label": self.STATUS_LABELS.get(delivery.sudi_billing_status, delivery.sudi_billing_status),
            "open": is_open,
            "mode": delivery.sudi_settlement_mode or "invoice",
            "jobs": jobs,
            "docs": self._doc_entries(delivery),
        }

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    @api.model
    def get_review_data(self, partner_id=None, date_from=None, date_to=None, status="due", search=None, group_by="customer"):
        self._check_access()
        Picking = self.env["stock.picking"]

        domain = self._base_domain()
        if status == "due":
            domain.append(("sudi_billing_status", "in", self.OPEN_STATUSES))
        elif status == "closed":
            domain.append(("sudi_billing_status", "in", self.CLOSED_STATUSES))
        if partner_id:
            domain.append(("partner_id.commercial_partner_id", "=", int(partner_id)))
        if date_from:
            domain.append(("date_done", ">=", self._to_utc(fields.Date.from_string(date_from))))
        if date_to:
            domain.append(("date_done", "<=", self._to_utc(fields.Date.from_string(date_to), end=True)))
        if search:
            domain += ["|", ("name", "ilike", search), ("sudi_origin_receipt_id.name", "ilike", search)]

        deliveries = Picking.search(domain, order="date_done asc, id asc")
        rows = [self._row(delivery) for delivery in deliveries]

        groups = {}
        order = []
        for row in rows:
            if group_by == "month":
                key, title = row["month"], self._month_title(row["month"])
            elif group_by == "none":
                key, title = "all", _("All deliveries")
            else:
                key, title = row["customer_id"], row["customer"]
            if key not in groups:
                groups[key] = {"key": key, "title": title, "customer_id": row["customer_id"] if group_by == "customer" else False, "rows": []}
                order.append(key)
            groups[key]["rows"].append(row)
        group_list = []
        for key in order:
            group = groups[key]
            due_rows = [row for row in group["rows"] if row["open"]]
            partner = self.env["res.partner"].browse(group["customer_id"]) if group["customer_id"] else None
            group.update({
                "count": len(group["rows"]),
                "due_count": len(due_rows),
                "pcs": sum(row["pcs"] for row in due_rows),
                "carats": sum(row["carats"] for row in due_rows),
                "amount": sum(row["amount"] for row in due_rows),
                "monthly": bool(partner and partner.sudi_invoice_policy == "monthly"),
            })
            group_list.append(group)

        return {
            "today": fields.Date.to_string(self._local_today()),
            "currency": self.env.company.currency_id.name,
            "ledger_access": self._has_ledger_access(),
            "kpis": self._kpis(),
            "customers": self._customers(),
            "groups": group_list,
            "awaiting": self._awaiting(),
        }

    @api.model
    def _month_title(self, month_key):
        if not month_key:
            return _("Undated")
        year, month = month_key.split("-")
        return datetime(int(year), int(month), 1).strftime("%B %Y")

    @api.model
    def _kpis(self):
        due = self.env["stock.picking"].search(self._due_domain())
        moves = due.move_ids.filtered(lambda move: move.sudi_billing_state == "to_bill")
        monthly = self._monthly_due()
        monthly_partners = monthly.partner_id.commercial_partner_id
        return {
            "due_count": len(due),
            "partial_count": len(due.filtered(lambda picking: picking.sudi_billing_status == "partial")),
            "customers": len(due.partner_id.commercial_partner_id),
            "pcs": sum(moves.mapped("sudi_pcs_qty")),
            "carats": sum(moves.mapped("sudi_carats")),
            "amount": sum(due.mapped("sudi_billable_amount")),
            "monthly_customers": len(monthly_partners),
            "monthly_names": ", ".join(monthly_partners.sorted("display_name").mapped("display_name")),
            "monthly_deliveries": len(monthly),
            "month_start": fields.Date.to_string(self._month_start()),
        }

    @api.model
    def _customers(self):
        groups = self.env["stock.picking"]._read_group(
            self._base_domain(),
            groupby=["partner_id"],
            aggregates=["__count"],
        )
        partners = self.env["res.partner"]
        for partner, _count in groups:
            partners |= partner.commercial_partner_id
        return [
            {"id": partner.id, "name": partner.display_name, "monthly": partner.sudi_invoice_policy == "monthly"}
            for partner in partners.sorted("display_name")
        ]

    @api.model
    def _awaiting(self):
        receipts = self.env["stock.picking"].search(
            [
                ("sudi_is_diamond_job_work", "=", True),
                ("picking_type_code", "=", "incoming"),
                ("state", "not in", ("done", "cancel")),
                ("company_id", "in", self.env.companies.ids),
            ],
            order="scheduled_date asc, id asc",
        )
        rows = []
        for receipt in receipts:
            moves = receipt.move_ids.filtered(lambda move: move.state != "cancel")
            stage, stage_key = self.RECEIPT_STAGES.get(receipt.state, (receipt.state, "prog"))
            billing_lines = receipt.sudi_billing_line_ids.filtered("active")
            jobs = []
            for name in moves.sudi_job_type_id.sorted("sequence").mapped("name"):
                if name not in jobs:
                    jobs.append(name)
            rows.append({
                "id": receipt.id,
                "name": receipt.name,
                "customer": receipt.partner_id.commercial_partner_id.display_name,
                "date": fields.Date.to_string(self._local_date(receipt.sudi_pickup_datetime or receipt.scheduled_date)),
                "stage": stage,
                "stage_key": stage_key,
                "jobs": jobs,
                "pcs": sum(moves.mapped("sudi_pcs_qty")),
                "carats": sum(moves.mapped("sudi_carats")),
                "amount": sum(billing_lines.mapped("price_subtotal")),
            })
        return rows

    @api.model
    def get_delivery_lines(self, picking_id):
        self._check_access()
        delivery = self.env["stock.picking"].browse(int(picking_id))
        delivery.check_access("read")
        source_labels = {"partner": ("Customer price", "cust"), "job_type": ("Job type price", "job"), "manual": ("Manual", "man")}
        lines = []
        for move in delivery.move_ids.filtered(lambda move: move.state == "done" and move.sudi_job_type_id).sorted(
            key=lambda move: (move.sudi_sr or 0, move.id)
        ):
            billing_line = move.sudi_billing_line_id
            source, source_key = source_labels.get(billing_line.price_source, ("Job type price", "job"))
            if move.sudi_returned_without_work:
                source, source_key = "No charge", "none"
            doc = ""
            if move.sudi_billing_state == "billed":
                doc = move.sudi_invoice_line_id.move_id.name or _("Draft invoice")
            elif move.sudi_billing_state == "closed":
                doc = move.sudi_reference_line_id.statement_id.name if self._has_ledger_access() else _("Closed")
            lines.append({
                "move_id": move.id,
                "billing_line_id": billing_line.id,
                "sr": move.sudi_sr,
                "size": move.sudi_size or "",
                "job": move.sudi_job_type_id.name,
                "basis": move.sudi_job_type_id.invoice_basis,
                "pcs": move.sudi_pcs_qty,
                "carats": move.sudi_carats,
                "qty": move.sudi_billable_qty,
                "rate": move.sudi_price_unit,
                "amount": move.sudi_billable_amount,
                "state": move.sudi_billing_state,
                "no_charge": move.sudi_returned_without_work,
                "locked": move._sudi_is_settled() or not billing_line or billing_line._sudi_is_locked(),
                "source": source,
                "source_key": source_key,
                "doc": doc,
            })
        return {"receipt": delivery.sudi_origin_receipt_id.name, "lines": lines}

    @api.model
    def get_history(self, partner_id=None, limit=200):
        self._check_access()
        domain = [("company_id", "in", self.env.companies.ids)]
        if partner_id:
            domain.append(("partner_id", "=", int(partner_id)))
        ledger = self._has_ledger_access()
        rows = []
        for log in self.env["sudi.diamond.billing.log"].search(domain, limit=limit):
            if log.event == "reference" and not ledger:
                doc = _("Closed")
            elif log.event == "reopened" and not ledger:
                doc = _("Reopened")
            elif log.invoice_id:
                doc = log.invoice_id.name if log.invoice_id.name and log.invoice_id.name != "/" else _("Draft invoice")
            else:
                doc = log.document_name or (log.note or "")
            rows.append({
                "id": log.id,
                "date": fields.Datetime.to_string(fields.Datetime.context_timestamp(self.with_context(tz=self._tz().zone), log.date))[:16],
                "event": log.event,
                "doc": doc,
                "doc_model": "account.move" if log.invoice_id else ("sudi.diamond.reference.statement" if log.reference_statement_id and ledger else False),
                "doc_id": log.invoice_id.id or (log.reference_statement_id.id if ledger else False),
                "customer": log.partner_id.display_name,
                "delivery": log.delivery_id.name,
                "delivery_id": log.delivery_id.id,
                "receipt": log.receipt_id.name,
                "job": log.job_type_id.name,
                "pcs": log.pcs,
                "carats": log.carats,
                "amount": log.amount,
                "note": log.note or "",
                "user": log.user_id.name,
                "admin_only": log.event in ("reference", "reopened"),
            })
        return rows

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    @api.model
    def set_line_price(self, move_id, price_unit):
        self._check_access()
        move = self.env["stock.move"].browse(int(move_id))
        if not move.sudi_billing_line_id:
            raise UserError(_("This delivery line has no billing line on its receipt; recompute the receipt's billing details first."))
        move.sudi_billing_line_id.sudi_set_manual_price(float(price_unit))
        return True

    @api.model
    def reset_prices(self, picking_ids):
        """Drop manual overrides and re-sync from the customer price list."""
        self._check_access()
        deliveries = self.env["stock.picking"].browse(picking_ids)
        receipts = deliveries.sudi_origin_receipt_id
        receipts.sudi_billing_line_ids.filtered(lambda line: line.active and line.manual_price).sudi_reset_manual_price()
        receipts.with_context(sudi_skip_billing_sync=True)._sudi_sync_billing_details()
        return True

    @api.model
    def set_settlement_mode(self, picking_ids, mode):
        self._check_access()
        if mode not in ("invoice", "reference"):
            raise UserError(_("Unknown settlement mode."))
        deliveries = self.env["stock.picking"].browse(picking_ids)
        deliveries.filtered(lambda picking: picking.sudi_billing_status in self.OPEN_STATUSES).write({"sudi_settlement_mode": mode})
        return True

    @api.model
    def set_returned_without_work(self, move_ids, value):
        self._check_access()
        moves = self.env["stock.move"].browse(move_ids)
        if any(move._sudi_is_settled() for move in moves):
            raise UserError(_("Settled delivery lines cannot be changed."))
        moves.write({"sudi_returned_without_work": bool(value)})
        return True

    @api.model
    def get_due_delivery_ids(self):
        self._check_access()
        return self._monthly_due().ids

    @api.model
    def get_settlement_preview(self, picking_ids):
        """Per customer: how many deliveries and how much go to an invoice / a reference statement."""
        self._check_access()
        deliveries = self.env["stock.picking"].browse(picking_ids).filtered(
            lambda picking: picking.sudi_billing_status in self.OPEN_STATUSES
        )
        preview = {}
        for delivery in deliveries:
            partner = delivery.partner_id.commercial_partner_id
            entry = preview.setdefault(partner.id, {
                "customer": partner.display_name,
                "invoice_count": 0, "invoice_amount": 0.0,
                "reference_count": 0, "reference_amount": 0.0,
            })
            if (delivery.sudi_settlement_mode or "invoice") == "invoice":
                entry["invoice_count"] += 1
                entry["invoice_amount"] += delivery.sudi_billable_amount
            else:
                entry["reference_count"] += 1
                entry["reference_amount"] += delivery.sudi_billable_amount
        return sorted(preview.values(), key=lambda entry: entry["customer"])

    @api.model
    def settle(self, picking_ids, date=None):
        self._check_access()
        deliveries = self.env["stock.picking"].browse(picking_ids)
        if not deliveries:
            raise UserError(_("Select at least one delivery."))
        invoices, statements = deliveries._sudi_settle_deliveries(date=date or None)
        if not invoices and not statements:
            raise UserError(_("Nothing to settle: the selected deliveries have no unbilled lines."))
        result = {
            "invoices": [{"id": invoice.id, "name": invoice.name if invoice.name != "/" else _("Draft invoice")} for invoice in invoices],
            "statements": [{"id": statement.id, "name": statement.name} for statement in statements] if self._has_ledger_access() else [],
            "statement_count": len(statements),
        }
        if invoices:
            action = self.env["ir.actions.act_window"]._for_xml_id("diamond.action_sudi_diamond_invoices")
            action["domain"] = [("id", "in", invoices.ids)]
            if len(invoices) == 1:
                action["views"] = [(False, "form")]
                action["res_id"] = invoices.id
            result["action"] = action
        return result

    @api.model
    def get_open_action(self, model, res_id):
        self._check_access()
        if model == "account.move":
            action = self.env["ir.actions.act_window"]._for_xml_id("diamond.action_sudi_diamond_invoices")
        elif model == "stock.picking":
            action = self.env["ir.actions.act_window"]._for_xml_id("diamond.action_sudi_diamond_deliveries")
        elif model == "sudi.diamond.reference.statement" and self._has_ledger_access():
            action = self.env["ir.actions.act_window"]._for_xml_id("sudi_diamond_billing.action_sudi_reference_statement")
        else:
            raise AccessError(_("You cannot open this record from the Billing Review."))
        action["views"] = [(False, "form")]
        action["res_id"] = int(res_id)
        return action
