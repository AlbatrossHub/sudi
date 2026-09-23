from odoo import Command
from odoo.exceptions import UserError, ValidationError

from .test_diamond_job_work import SudiJobWorkCase


class TestSudiBillingSettlement(SudiJobWorkCase):
    """Delivery-based settlement: consolidated invoices, backorders, reference statements, releases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.job_type_2 = cls.env.ref("diamond.job_type_natts_process")
        cls.job_type_2.base_price = 40.0
        cls.partner_2 = cls.env["res.partner"].create({
            "name": "Second Diamond Customer",
            "country_id": cls.env.ref("base.in").id,
            "state_id": cls.env.ref("base.state_in_gj").id,
            "l10n_in_gst_treatment": "unregistered",
        })

    def _deliver(self, receipt, qty=None):
        delivery = receipt.sudi_delivery_ids.filtered(lambda picking: picking.state not in ("done", "cancel"))[:1]
        for move in delivery.move_ids:
            move.quantity = qty if qty is not None else move.product_uom_qty
            move.picked = True
        delivery.button_validate()
        return delivery

    def _delivered_receipt(self, partner=None, qty=10.0, pcs=10.0, carats=2.5, **kwargs):
        receipt = self._create_receipt(qty=qty, pcs=pcs, carats=carats, partner=partner, **kwargs)
        delivery = self._deliver(receipt)
        return receipt, delivery

    # -- status & values --------------------------------------------------
    def test_done_delivery_is_to_bill_with_receipt_rates(self):
        self._get_partner_price_line().price = 80.0
        receipt, delivery = self._delivered_receipt()
        move = delivery.move_ids

        self.assertEqual(delivery.sudi_billing_status, "to_bill")
        self.assertEqual(move.sudi_billing_state, "to_bill")
        self.assertEqual(move.sudi_billing_line_id, receipt.sudi_billing_line_ids)
        self.assertEqual(move.sudi_price_unit, 80.0)
        self.assertEqual(move.sudi_billable_qty, 10.0)
        self.assertEqual(move.sudi_billable_amount, 800.0)
        self.assertEqual(delivery.sudi_billable_amount, 800.0)
        self.assertEqual(delivery.sudi_total_pcs, 10.0)
        self.assertEqual(delivery.sudi_job_type_summary, self.job_type.name)
        self.assertEqual(receipt.sudi_billing_status, "none")

    def test_manual_rate_from_review_is_kept_and_reset(self):
        receipt, delivery = self._delivered_receipt()
        billing_line = receipt.sudi_billing_line_ids

        billing_line.sudi_set_manual_price(55.0)
        self.assertEqual(delivery.move_ids.sudi_price_unit, 55.0)
        receipt.action_sudi_recompute_billing_details()
        self.assertEqual(billing_line.price_unit, 55.0)

        billing_line.sudi_reset_manual_price()
        self.assertEqual(billing_line.price_unit, 100.0)
        self.assertFalse(billing_line.manual_price)
        self.assertEqual(delivery.move_ids.sudi_price_unit, 100.0)

    # -- consolidated invoice ----------------------------------------------
    def test_multiple_receipts_settle_into_one_invoice_per_customer(self):
        receipt_a, delivery_a = self._delivered_receipt(qty=5.0, pcs=5.0)
        receipt_b, delivery_b = self._delivered_receipt(qty=7.0, pcs=7.0)
        receipt_c, delivery_c = self._delivered_receipt(partner=self.partner_2, qty=3.0, pcs=3.0)

        invoices, statements = (delivery_a | delivery_b | delivery_c)._sudi_settle_deliveries(date="2026-09-20")

        self.assertEqual(len(invoices), 2)
        self.assertFalse(statements)
        own = invoices.filtered(lambda move: move.partner_id == self.partner)
        self.assertEqual(own.sudi_delivery_ids, delivery_a | delivery_b)
        self.assertEqual(own.sudi_receipt_ids, receipt_a | receipt_b)
        self.assertEqual(len(own.invoice_line_ids), 1)
        self.assertEqual(own.invoice_line_ids.quantity, 12.0)
        self.assertEqual(str(own.invoice_date), "2026-09-20")
        self.assertEqual(own.state, "draft")
        self.assertEqual((delivery_a | delivery_b).mapped("sudi_billing_status"), ["billed", "billed"])
        self.assertEqual(delivery_c.sudi_billing_status, "billed")
        self.assertEqual(delivery_c.sudi_billed_invoice_ids, invoices - own)
        self.assertEqual(receipt_a.sudi_invoice_ids, own)

    def test_receipt_wise_rates_give_separate_invoice_lines(self):
        receipt_a, delivery_a = self._delivered_receipt(qty=5.0, pcs=5.0)
        receipt_b, delivery_b = self._delivered_receipt(qty=7.0, pcs=7.0)
        receipt_b.sudi_billing_line_ids.sudi_set_manual_price(150.0)

        invoices, _statements = (delivery_a | delivery_b)._sudi_settle_deliveries()

        lines = invoices.invoice_line_ids.sorted("price_unit")
        self.assertEqual(lines.mapped("price_unit"), [100.0, 150.0])
        self.assertEqual(lines.mapped("quantity"), [5.0, 7.0])
        self.assertEqual(lines[0].sudi_stock_move_ids, delivery_a.move_ids)
        self.assertEqual(lines[1].sudi_stock_move_ids, delivery_b.move_ids)

    def test_backorder_second_delivery_can_be_invoiced_later(self):
        receipt = self._create_receipt(qty=10.0, pcs=10.0)
        first = self._deliver(receipt, qty=4.0)
        first.action_sudi_create_invoice()
        self.assertEqual(first.sudi_billing_status, "billed")

        backorder = receipt.sudi_delivery_ids - first
        self.assertEqual(len(backorder), 1)
        self.assertEqual(backorder.sudi_billing_status, "none")
        self._deliver(receipt)
        self.assertEqual(backorder.sudi_billing_status, "to_bill")
        self.assertEqual(backorder.move_ids.sudi_billable_qty, 6.0)

        invoices, _statements = backorder._sudi_settle_deliveries()
        self.assertEqual(invoices.invoice_line_ids.quantity, 6.0)
        self.assertEqual(backorder.sudi_billing_status, "billed")
        self.assertEqual(len(receipt.sudi_billing_line_ids.invoice_line_ids), 2)
        self.assertTrue(receipt.sudi_billing_line_ids.is_settled)

    def test_returned_without_work_is_not_charged_but_listed(self):
        receipt = self._create_receipt(
            move_commands=[
                self._prepare_receipt_move_command(qty=5.0, pcs=5.0, carats=1.0, sr=1),
                self._prepare_receipt_move_command(qty=7.0, pcs=7.0, carats=2.0, sr=2, job_type=self.job_type_2),
            ],
        )
        delivery = self._deliver(receipt)
        returned = delivery.move_ids.filtered(lambda move: move.sudi_sr == 2)
        returned.action_sudi_toggle_returned_without_work()

        self.assertEqual(returned.sudi_billing_state, "no_charge")
        self.assertEqual(returned.sudi_billable_amount, 0.0)
        self.assertEqual(delivery.sudi_billing_status, "to_bill")
        self.assertEqual(delivery.sudi_billable_amount, 500.0)

        invoices, _statements = delivery._sudi_settle_deliveries()
        self.assertEqual(len(invoices.invoice_line_ids), 1)
        self.assertEqual(invoices.amount_untaxed, 500.0)
        self.assertEqual(len(invoices.sudi_annexure_line_ids), 2)
        self.assertEqual(delivery.sudi_billing_status, "billed")
        with self.assertRaises(UserError):
            returned.action_sudi_toggle_returned_without_work()

    def test_all_returned_delivery_cannot_settle_alone(self):
        receipt, delivery = self._delivered_receipt()
        delivery.action_sudi_return_all_without_work()
        self.assertEqual(delivery.sudi_billing_status, "no_charge")
        invoices, statements = delivery._sudi_settle_deliveries()
        self.assertFalse(invoices)
        self.assertFalse(statements)

    # -- release / cancel ---------------------------------------------------
    def test_cancelled_invoice_releases_delivery_and_logs(self):
        receipt, delivery = self._delivered_receipt()
        invoices, _statements = delivery._sudi_settle_deliveries()
        Log = self.env["sudi.diamond.billing.log"]
        self.assertEqual(Log.search([("delivery_id", "=", delivery.id)]).mapped("event"), ["billed"])

        invoices.button_cancel()
        self.assertEqual(delivery.sudi_billing_status, "to_bill")
        self.assertEqual(delivery.move_ids.sudi_billing_state, "to_bill")
        self.assertFalse(receipt.sudi_billing_line_ids.is_settled)
        events = Log.search([("delivery_id", "=", delivery.id)], order="id").mapped("event")
        self.assertEqual(events, ["billed", "released"])

        # Re-settle after the cancel: a new invoice, no duplicate charge.
        again, _statements = delivery._sudi_settle_deliveries()
        self.assertNotEqual(again, invoices)
        self.assertEqual(delivery.sudi_billing_status, "billed")
        self.assertEqual(delivery.sudi_billed_invoice_ids, invoices | again)
        self.assertEqual(delivery.sudi_invoice_count, 2)

    def test_deleted_draft_invoice_releases_delivery(self):
        receipt, delivery = self._delivered_receipt()
        invoices, _statements = delivery._sudi_settle_deliveries()
        invoices.unlink()
        self.assertEqual(delivery.sudi_billing_status, "to_bill")
        self.assertFalse(delivery.move_ids.sudi_invoice_line_id)

    def test_settled_billing_line_is_locked(self):
        receipt, delivery = self._delivered_receipt()
        delivery._sudi_settle_deliveries()
        with self.assertRaises(ValidationError):
            receipt.sudi_billing_line_ids.write({"price_unit": 1.0})
        with self.assertRaises(ValidationError):
            receipt.sudi_billing_line_ids.sudi_set_manual_price(1.0)

    # -- reference statements ----------------------------------------------
    def test_reference_mode_creates_statement_not_invoice(self):
        receipt, delivery = self._delivered_receipt()
        delivery.sudi_settlement_mode = "reference"

        invoices, statements = delivery._sudi_settle_deliveries()

        self.assertFalse(invoices)
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements.name.startswith("REF/"))
        self.assertEqual(statements.partner_id, self.partner)
        self.assertEqual(statements.delivery_ids, delivery)
        self.assertEqual(statements.receipt_ids, receipt)
        self.assertEqual(statements.amount_total, 1000.0)
        self.assertEqual(statements.pcs_total, 10.0)
        self.assertEqual(statements.line_ids.job_type_id, self.job_type)
        self.assertEqual(delivery.sudi_billing_status, "closed")
        self.assertEqual(delivery.move_ids.sudi_billing_state, "closed")
        self.assertEqual(delivery.move_ids.sudi_reference_line_id, statements.line_ids)
        self.assertFalse(delivery.sudi_billed_invoice_ids)
        self.assertTrue(receipt.sudi_billing_line_ids.is_settled)
        self.assertEqual(
            self.env["sudi.diamond.billing.log"].search([("delivery_id", "=", delivery.id)]).mapped("event"),
            ["reference"],
        )
        self.assertIn("Closed for billing", delivery.message_ids[0].body)
        self.assertNotIn("REF/", delivery.message_ids[0].body)

    def test_mixed_selection_splits_per_customer_and_mode(self):
        _receipt_a, delivery_a = self._delivered_receipt(qty=5.0, pcs=5.0)
        _receipt_b, delivery_b = self._delivered_receipt(qty=7.0, pcs=7.0)
        _receipt_c, delivery_c = self._delivered_receipt(partner=self.partner_2, qty=3.0, pcs=3.0)
        delivery_b.sudi_settlement_mode = "reference"

        invoices, statements = (delivery_a | delivery_b | delivery_c)._sudi_settle_deliveries()

        self.assertEqual(len(invoices), 2)
        self.assertEqual(len(statements), 1)
        self.assertEqual(statements.delivery_ids, delivery_b)
        self.assertEqual(invoices.filtered(lambda move: move.partner_id == self.partner).sudi_delivery_ids, delivery_a)
        self.assertEqual(invoices.filtered(lambda move: move.partner_id == self.partner_2).sudi_delivery_ids, delivery_c)

    def test_reopen_reference_statement_is_admin_only_and_releases(self):
        receipt, delivery = self._delivered_receipt()
        delivery.sudi_settlement_mode = "reference"
        _invoices, statements = delivery._sudi_settle_deliveries()

        reviewer = self.env["res.users"].create({
            "name": "Reviewer",
            "login": "sudi_reviewer",
            "group_ids": [Command.set([self.env.ref("diamond.group_sudi_billing_reviewer").id])],
        })
        with self.assertRaises(Exception):
            statements.with_user(reviewer).action_reopen()

        statements.action_reopen(reason="Customer paid after all")
        self.assertEqual(statements.state, "reopened")
        self.assertEqual(delivery.sudi_billing_status, "to_bill")
        self.assertFalse(receipt.sudi_billing_line_ids.is_settled)
        events = self.env["sudi.diamond.billing.log"].search([("delivery_id", "=", delivery.id)], order="id").mapped("event")
        self.assertEqual(events, ["reference", "reopened"])

        invoices, _statements = delivery._sudi_settle_deliveries(mode="invoice")
        self.assertEqual(delivery.sudi_billing_status, "billed")
        self.assertEqual(delivery.sudi_reference_statement_ids, statements)

    def test_billing_log_is_immutable(self):
        _receipt, delivery = self._delivered_receipt()
        delivery._sudi_settle_deliveries()
        log = self.env["sudi.diamond.billing.log"].search([("delivery_id", "=", delivery.id)])
        self.assertEqual(log.amount, 1000.0)
        with self.assertRaises(UserError):
            log.write({"amount": 1.0})
        with self.assertRaises(UserError):
            log.with_user(self.env.ref("base.user_admin")).unlink()


class TestSudiInvoiceAnnexure(SudiJobWorkCase):
    """Phase 2: annexure data, the invoice report, and what posting triggers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.job_type_2 = cls.env.ref("diamond.job_type_natts_process")
        cls.job_type_2.base_price = 40.0

    def _delivered(self, move_commands=None, qty=10.0, pcs=10.0):
        receipt = self._create_receipt(qty=qty, pcs=pcs, move_commands=move_commands)
        delivery = receipt.sudi_delivery_ids
        for move in delivery.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        delivery.button_validate()
        return receipt, delivery

    def test_annexure_groups_per_delivery_with_no_charge_and_job_totals(self):
        receipt_a, delivery_a = self._delivered(qty=5.0, pcs=5.0)
        receipt_b, delivery_b = self._delivered(
            move_commands=[
                self._prepare_receipt_move_command(qty=7.0, pcs=7.0, carats=2.0, sr=1),
                self._prepare_receipt_move_command(qty=3.0, pcs=3.0, carats=1.0, sr=2, job_type=self.job_type_2),
            ],
        )
        delivery_b.move_ids.filtered(lambda move: move.sudi_sr == 2).action_sudi_toggle_returned_without_work()

        invoices, _statements = (delivery_a | delivery_b)._sudi_settle_deliveries(date="2026-09-20")
        groups = invoices._sudi_get_annexure_groups()

        self.assertEqual([group["delivery"] for group in groups], [delivery_a, delivery_b])
        self.assertEqual([group["receipt"] for group in groups], [receipt_a, receipt_b])
        self.assertEqual(len(groups[1]["lines"]), 2)
        self.assertEqual(groups[1]["pcs"], 10.0)
        self.assertEqual(groups[1]["amount"], 700.0)
        totals = invoices._sudi_get_annexure_job_type_totals()
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]["name"], self.job_type.name)
        self.assertEqual(totals[0]["pcs"], 12.0)
        self.assertEqual(totals[0]["amount"], invoices.amount_untaxed)

    def test_invoice_pdf_renders_with_annexure(self):
        _receipt, delivery = self._delivered()
        invoices, _statements = delivery._sudi_settle_deliveries()
        html = self.env["ir.actions.report"]._render_qweb_html("account.account_invoices", invoices.ids)[0]
        self.assertIn(b"Annexure", html)
        self.assertIn(delivery.name.encode(), html)
        self.assertIn(b"Job Work Statement", html)

    def test_posting_posts_chatter_and_attempts_whatsapp(self):
        receipt, delivery = self._delivered()
        invoices, _statements = delivery._sudi_settle_deliveries()
        sent = []
        AccountMove = type(self.env["account.move"])
        original = AccountMove._sudi_send_whatsapp_message

        def fake_send(inv, recipient_phone, body_text, attachment=None, partner=None):
            sent.append((recipient_phone, body_text, attachment))
            return True

        AccountMove._sudi_send_whatsapp_message = fake_send
        try:
            self.partner.phone = "+91 98765 43210"
            invoices.action_post()
        finally:
            AccountMove._sudi_send_whatsapp_message = original

        self.assertEqual(invoices.state, "posted")
        self.assertTrue(any("Billed on" in message.body for message in delivery.message_ids))
        self.assertTrue(any("Billed on" in message.body for message in receipt.message_ids))
        self.assertEqual(len(sent), 1)
        phone, body, attachment = sent[0]
        self.assertEqual(phone, "+91 98765 43210")
        self.assertIn(invoices.name, body)
        self.assertIn(delivery.name, body)
        self.assertTrue(attachment and attachment.mimetype == "application/pdf")

    def test_posting_without_phone_skips_whatsapp_quietly(self):
        _receipt, delivery = self._delivered()
        invoices, _statements = delivery._sudi_settle_deliveries()
        self.partner.phone = False
        invoices.action_post()
        self.assertEqual(invoices.state, "posted")
        self.assertEqual(delivery.sudi_billing_status, "billed")
