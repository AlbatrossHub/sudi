from odoo import Command
from odoo.exceptions import AccessError, UserError

from odoo.addons.diamond.tests.test_diamond_job_work import SudiJobWorkCase


class TestSudiBillingReview(SudiJobWorkCase):
    """The RPC service behind the Billing Review client action."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Review = cls.env["sudi.diamond.billing.review"]
        cls.reviewer = cls.env["res.users"].create({
            "name": "Billing Reviewer",
            "login": "sudi_billing_reviewer",
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_billing_reviewer").id,
                cls.env.ref("stock.group_stock_user").id,
            ])],
        })
        cls.operator = cls.env["res.users"].create({
            "name": "Plain Stock User",
            "login": "sudi_plain_stock_user",
            "group_ids": [Command.set([cls.env.ref("stock.group_stock_user").id])],
        })
        cls.partner_monthly = cls.env["res.partner"].create({
            "name": "Monthly Diamond Customer",
            "country_id": cls.env.ref("base.in").id,
            "state_id": cls.env.ref("base.state_in_gj").id,
            "l10n_in_gst_treatment": "unregistered",
            "sudi_invoice_policy": "monthly",
        })

    def _delivered(self, partner=None, qty=10.0, pcs=10.0):
        receipt = self._create_receipt(qty=qty, pcs=pcs, partner=partner)
        delivery = receipt.sudi_delivery_ids
        for move in delivery.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        delivery.button_validate()
        return receipt, delivery

    def _review(self, user=None):
        return self.Review.with_user(user or self.reviewer)

    def _find_row(self, data, delivery):
        for group in data["groups"]:
            for row in group["rows"]:
                if row["id"] == delivery.id:
                    return group, row
        return None, None

    def test_access_is_enforced_on_the_rpc(self):
        with self.assertRaises(AccessError):
            self._review(self.operator).get_review_data()
        with self.assertRaises(AccessError):
            self._review(self.operator).settle([])

    def test_review_lists_due_deliveries_grouped_by_customer(self):
        receipt, delivery = self._delivered()
        _receipt_2, delivery_2 = self._delivered(partner=self.partner_monthly, qty=4.0, pcs=4.0)

        data = self._review().get_review_data()

        self.assertEqual(data["kpis"]["due_count"], 2)
        self.assertEqual(data["kpis"]["pcs"], 14.0)
        self.assertEqual(data["kpis"]["amount"], 1400.0)
        self.assertEqual({customer["name"] for customer in data["customers"]}, {self.partner.display_name, self.partner_monthly.display_name})
        group, row = self._find_row(data, delivery)
        self.assertEqual(group["title"], self.partner.display_name)
        self.assertFalse(group["monthly"])
        self.assertEqual(row["receipt"], receipt.name)
        self.assertEqual(row["status"], "to_bill")
        self.assertTrue(row["open"])
        self.assertEqual(row["mode"], "invoice")
        self.assertEqual(row["amount"], 1000.0)
        self.assertEqual(row["jobs"], [{"name": self.job_type.name, "no_charge": False}])
        group_2, _row_2 = self._find_row(data, delivery_2)
        self.assertTrue(group_2["monthly"])

        by_month = self._review().get_review_data(group_by="month")
        self.assertEqual(len(by_month["groups"]), 1)
        flat = self._review().get_review_data(group_by="none")
        self.assertEqual(flat["groups"][0]["title"], "All deliveries")
        self.assertEqual(flat["groups"][0]["count"], 2)

    def test_filters_by_customer_status_and_search(self):
        receipt, delivery = self._delivered()
        _receipt_2, delivery_2 = self._delivered(partner=self.partner_monthly)
        delivery_2._sudi_settle_deliveries()

        due = self._review().get_review_data(status="due")
        self.assertEqual([row["id"] for group in due["groups"] for row in group["rows"]], [delivery.id])
        closed = self._review().get_review_data(status="closed")
        self.assertEqual([row["id"] for group in closed["groups"] for row in group["rows"]], [delivery_2.id])
        _group, closed_row = self._find_row(closed, delivery_2)
        self.assertEqual(closed_row["status"], "billed")
        self.assertEqual(closed_row["docs"][0]["model"], "account.move")
        everything = self._review().get_review_data(status="all")
        self.assertEqual(sum(group["count"] for group in everything["groups"]), 2)
        by_partner = self._review().get_review_data(status="all", partner_id=self.partner_monthly.id)
        self.assertEqual(sum(group["count"] for group in by_partner["groups"]), 1)
        by_search = self._review().get_review_data(status="all", search=receipt.name)
        self.assertEqual([row["id"] for group in by_search["groups"] for row in group["rows"]], [delivery.id])

    def test_awaiting_lists_receipts_not_yet_delivered(self):
        # A pending pickup: its lines can only be entered through the pickup flow.
        receipt = self.env["stock.picking"].with_context(sudi_allow_pickup_edit=True).create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": True,
            "move_ids": [self._prepare_receipt_move_command(qty=3.0, pcs=3.0, carats=1.0)],
        })
        data = self._review().get_review_data()
        awaiting = [row for row in data["awaiting"] if row["id"] == receipt.id]
        self.assertEqual(len(awaiting), 1)
        self.assertEqual(awaiting[0]["stage"], "Pick up pending")
        self.assertEqual(awaiting[0]["pcs"], 3.0)

    def test_lines_expose_receipt_rates_and_edits_persist(self):
        receipt, delivery = self._delivered()
        detail = self._review().get_delivery_lines(delivery.id)
        self.assertEqual(detail["receipt"], receipt.name)
        line = detail["lines"][0]
        self.assertEqual(line["rate"], 100.0)
        self.assertEqual(line["source_key"], "cust")
        self.assertFalse(line["locked"])

        self._review().set_line_price(line["move_id"], 55.0)
        detail = self._review().get_delivery_lines(delivery.id)
        self.assertEqual(detail["lines"][0]["rate"], 55.0)
        self.assertEqual(detail["lines"][0]["source_key"], "man")
        self.assertEqual(receipt.sudi_billing_line_ids.price_unit, 55.0)
        self.assertTrue(receipt.sudi_billing_line_ids.manual_price)

        self._review().reset_prices([delivery.id])
        detail = self._review().get_delivery_lines(delivery.id)
        self.assertEqual(detail["lines"][0]["rate"], 100.0)
        self.assertEqual(detail["lines"][0]["source_key"], "cust")

    def test_no_charge_toggle_from_the_screen(self):
        _receipt, delivery = self._delivered()
        move = delivery.move_ids
        self._review().set_returned_without_work([move.id], True)
        self.assertEqual(delivery.sudi_billing_status, "no_charge")
        line = self._review().get_delivery_lines(delivery.id)["lines"][0]
        self.assertTrue(line["no_charge"])
        self.assertEqual(line["source_key"], "none")
        self._review().set_returned_without_work([move.id], False)
        self.assertEqual(delivery.sudi_billing_status, "to_bill")

    def test_settle_mixed_selection_with_preview_and_action(self):
        _receipt_a, delivery_a = self._delivered(qty=5.0, pcs=5.0)
        _receipt_b, delivery_b = self._delivered(qty=7.0, pcs=7.0)
        _receipt_c, delivery_c = self._delivered(partner=self.partner_monthly, qty=3.0, pcs=3.0)
        self._review().set_settlement_mode([delivery_b.id], "reference")
        self.assertEqual(delivery_b.sudi_settlement_mode, "reference")

        preview = self._review().get_settlement_preview([delivery_a.id, delivery_b.id, delivery_c.id])
        own = next(entry for entry in preview if entry["customer"] == self.partner.display_name)
        self.assertEqual((own["invoice_count"], own["invoice_amount"]), (1, 500.0))
        self.assertEqual((own["reference_count"], own["reference_amount"]), (1, 700.0))

        result = self._review().settle([delivery_a.id, delivery_b.id, delivery_c.id], date="2026-09-20")

        self.assertEqual(len(result["invoices"]), 2)
        self.assertEqual(result["statement_count"], 1)
        # A reviewer without ledger access never receives reference numbers.
        self.assertEqual(result["statements"], [])
        self.assertEqual(result["action"]["res_model"], "account.move")
        self.assertEqual(delivery_a.sudi_billing_status, "billed")
        self.assertEqual(delivery_b.sudi_billing_status, "closed")
        self.assertEqual(delivery_c.sudi_billing_status, "billed")
        self.assertEqual(str(delivery_a.sudi_billed_invoice_ids.invoice_date), "2026-09-20")

        data = self._review().get_review_data(status="closed")
        _group, row_b = self._find_row(data, delivery_b)
        self.assertEqual(row_b["status_label"], "Closed")
        self.assertEqual(row_b["docs"], [])
        history = self._review().get_history()
        reference_entries = [entry for entry in history if entry["event"] == "reference"]
        self.assertTrue(reference_entries)
        self.assertEqual(reference_entries[0]["doc"], "Closed")
        self.assertFalse(reference_entries[0]["doc_model"])

        # Ledger admins do see the statement.
        admin_data = self._review(self.env.ref("base.user_admin")).get_review_data(status="closed")
        _group, admin_row_b = self._find_row(admin_data, delivery_b)
        self.assertEqual(admin_row_b["docs"][0]["model"], "sudi.diamond.reference.statement")
        self.assertTrue(admin_row_b["docs"][0]["name"].startswith("REF/"))

        with self.assertRaises(UserError):
            self._review().settle([delivery_a.id])

    def test_monthly_due_uses_partner_policy_and_month_start(self):
        _receipt, delivery = self._delivered(partner=self.partner_monthly)
        self.assertEqual(self._review().get_due_delivery_ids(), [])
        delivery.date_done = "2026-01-15 10:00:00"
        self.assertEqual(self._review().get_due_delivery_ids(), [delivery.id])
        kpis = self._review().get_review_data()["kpis"]
        self.assertEqual(kpis["monthly_customers"], 1)
        self.assertEqual(kpis["monthly_deliveries"], 1)
        self.assertIn(self.partner_monthly.display_name, kpis["monthly_names"])

    def test_open_action_only_for_known_models(self):
        _receipt, delivery = self._delivered()
        action = self._review().get_open_action("stock.picking", delivery.id)
        self.assertEqual(action["res_id"], delivery.id)
        with self.assertRaises(AccessError):
            self._review().get_open_action("res.users", self.reviewer.id)
