from datetime import date, timedelta
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.addons.stock.tests.common import TestStockCommon


class TestSudiDiamondDashboard(TestStockCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dashboard = cls.env["sudi.diamond.dashboard"]
        cls.product = cls.env.ref("diamond.product_customer_diamond_parcel")
        cls.job_laser = cls.env.ref("diamond.job_type_laser_inscription")
        cls.job_natts = cls.env.ref("diamond.job_type_natts_process")

        cls.customer_a = cls.env["res.partner"].create({
            "name": "Shreeji Gems LLP", "phone": "+91 98250 11111",
        })
        cls.customer_b = cls.env["res.partner"].create({
            "name": "Nakshatra Diamonds", "phone": "+91 98250 22222",
        })

        cls.manager = cls.env["res.users"].create({
            "name": "Inventory Admin",
            "login": "sudi_dashboard_manager",
            "group_ids": [Command.set([cls.env.ref("stock.group_stock_manager").id])],
        })
        cls.operator = cls.env["res.users"].create({
            "name": "Stock User",
            "login": "sudi_dashboard_user",
            "group_ids": [Command.set([cls.env.ref("stock.group_stock_user").id])],
        })

    def _create_receipt(self, partner, job_type, pcs, carats):
        # Receipts start in 'sudi_pickup_pending', where the module blocks line
        # entry; this is its own escape hatch for seeding data.
        env = self.env(context=dict(self.env.context, sudi_allow_pickup_edit=True))
        # These tests exercise aggregation, not WhatsApp: silence the pickup
        # notification so they do not depend on template or phone configuration.
        with patch.object(
            type(env["stock.picking"]), "_sudi_notify_pickup_scheduled", lambda self: None
        ):
            receipt = self._create_receipt_record(env, partner, job_type, pcs, carats)
        return receipt

    def _create_receipt_record(self, env, partner, job_type, pcs, carats):
        receipt = env["stock.picking"].create({
            "partner_id": partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": True,
            "move_ids": [Command.create({
                "product_id": self.product.id,
                "product_uom_qty": pcs,
                "product_uom": self.product.uom_id.id,
                "location_id": self.supplier_location.id,
                "location_dest_id": self.stock_location.id,
                "sudi_pcs_qty": pcs,
                "sudi_carats": carats,
                "sudi_job_type_id": job_type.id,
            })],
        })
        receipt.action_confirm()
        return receipt

    def _data(self, **kwargs):
        return self.dashboard.with_user(self.manager).get_dashboard_data(**kwargs)

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------
    def test_dashboard_refuses_non_inventory_admin(self):
        """The menu is hidden for operators; the RPC must refuse them too."""
        with self.assertRaises(AccessError):
            self.dashboard.with_user(self.operator).get_dashboard_data()

    def test_dashboard_allows_inventory_admin(self):
        data = self._data()
        self.assertEqual(data["period"], "month")
        self.assertEqual(data["measure"], "pcs")

    def test_drilldown_refuses_non_inventory_admin(self):
        with self.assertRaises(AccessError):
            self.dashboard.with_user(self.operator).get_drilldown_action("customer")

    # ------------------------------------------------------------------
    # Stored aggregation fields
    # ------------------------------------------------------------------
    def test_receipt_date_falls_back_to_scheduled_date(self):
        receipt = self._create_receipt(self.customer_a, self.job_laser, 10, 2.5)
        self.assertEqual(receipt.sudi_receipt_date, receipt.scheduled_date)
        self.assertEqual(receipt.move_ids.sudi_receipt_date, receipt.sudi_receipt_date)
        self.assertEqual(receipt.move_ids.sudi_customer_id, self.customer_a)

    def test_receipt_date_prefers_confirmed_pickup(self):
        receipt = self._create_receipt(self.customer_a, self.job_laser, 10, 2.5)
        pickup = receipt.scheduled_date - timedelta(days=3)
        receipt.sudi_pickup_datetime = pickup
        self.assertEqual(receipt.sudi_receipt_date, pickup)
        self.assertEqual(receipt.move_ids.sudi_receipt_date, pickup)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------
    def test_totals_and_ranking_by_pieces(self):
        self._create_receipt(self.customer_a, self.job_laser, 100, 25)
        self._create_receipt(self.customer_b, self.job_natts, 60, 12)

        data = self._data(period="month", measure="pcs")

        self.assertEqual(data["kpis"]["jobs"], 2)
        self.assertEqual(data["kpis"]["qty"], 160)
        self.assertEqual(data["kpis"]["customers"], 2)
        self.assertEqual(data["kpis"]["avg"], 80)

        job_names = [row["name"] for row in data["job_types"]]
        self.assertEqual(job_names[0], self.job_laser.display_name)
        self.assertEqual(data["job_types"][0]["value"], 100)

        customer_names = [row["name"] for row in data["customers"]]
        self.assertEqual(customer_names[0], self.customer_a.display_name)
        self.assertEqual(data["customers"][0]["receipts"], 1)

    def test_measure_toggle_switches_to_carats(self):
        self._create_receipt(self.customer_a, self.job_laser, 100, 25)
        self._create_receipt(self.customer_b, self.job_natts, 60, 12)

        data = self._data(period="month", measure="carats")

        self.assertEqual(data["measure"], "carats")
        self.assertEqual(data["unit"], "ct")
        self.assertEqual(data["kpis"]["qty"], 37)
        self.assertEqual(data["job_types"][0]["value"], 25)

    def test_stages_split_receipts_and_count_open(self):
        self._create_receipt(self.customer_a, self.job_laser, 100, 25)

        data = self._data(period="month")
        by_key = {stage["key"]: stage for stage in data["stages"]}

        self.assertEqual(sum(stage["count"] for stage in data["stages"]), 1)
        self.assertEqual(by_key["progress"]["count"], 1)
        self.assertEqual(data["open_jobs"], 1)

    def test_trend_has_one_label_per_bucket_with_gaps_filled(self):
        self._create_receipt(self.customer_a, self.job_laser, 100, 25)

        data = self._data(period="month")
        trend = data["trend"]

        self.assertEqual(trend["granularity"], "month")
        self.assertEqual(len(trend["labels"]), 12)
        self.assertEqual(len(trend["values"]), 12)
        self.assertEqual(len(trend["receipts"]), 12)
        # The live period is the final bucket.
        self.assertEqual(trend["values"][-1], 100)

    # ------------------------------------------------------------------
    # Custom range
    # ------------------------------------------------------------------
    def test_custom_range_picks_daily_buckets_for_a_short_span(self):
        today = date.today()
        data = self._data(
            period="custom",
            date_from=str(today - timedelta(days=6)),
            date_to=str(today),
        )
        self.assertEqual(data["period"], "custom")
        self.assertEqual(data["trend"]["granularity"], "day")
        self.assertEqual(len(data["trend"]["labels"]), 7)

    def test_custom_range_coarsens_buckets_for_a_long_span(self):
        today = date.today()
        data = self._data(
            period="custom",
            date_from=str(today - timedelta(days=400)),
            date_to=str(today),
        )
        self.assertEqual(data["trend"]["granularity"], "month")

    def test_custom_range_swaps_reversed_dates(self):
        today = date.today()
        data = self._data(
            period="custom",
            date_from=str(today),
            date_to=str(today - timedelta(days=5)),
        )
        self.assertEqual(data["date_from"], str(today - timedelta(days=5)))
        self.assertEqual(data["date_to"], str(today))

    # ------------------------------------------------------------------
    # Drilldown
    # ------------------------------------------------------------------
    def test_drilldown_filters_receipts_by_customer(self):
        self._create_receipt(self.customer_a, self.job_laser, 100, 25)
        self._create_receipt(self.customer_b, self.job_natts, 60, 12)

        action = self.dashboard.with_user(self.manager).get_drilldown_action(
            "customer", self.customer_a.id, period="month"
        )
        self.assertEqual(action["res_model"], "stock.picking")

        pickings = self.env["stock.picking"].with_user(self.manager).search(action["domain"])
        self.assertEqual(pickings.mapped("partner_id"), self.customer_a)

    def test_drilldown_filters_receipts_by_job_type(self):
        self._create_receipt(self.customer_a, self.job_laser, 100, 25)
        self._create_receipt(self.customer_b, self.job_natts, 60, 12)

        action = self.dashboard.with_user(self.manager).get_drilldown_action(
            "job_type", self.job_natts.id, period="month"
        )
        pickings = self.env["stock.picking"].with_user(self.manager).search(action["domain"])
        self.assertEqual(pickings.move_ids.sudi_job_type_id, self.job_natts)
