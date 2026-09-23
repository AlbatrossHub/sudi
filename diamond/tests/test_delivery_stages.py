from odoo import Command
from odoo.exceptions import UserError

from .test_diamond_job_work import SudiJobWorkCase


class TestSudiDeliveryStages(SudiJobWorkCase):
    """Delivery Confirmation Awaited -> Out for Delivery -> Delivered."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = cls.env["res.users"].create({
            "name": "Ramesh Onfield",
            "login": "sudi_onfield_ramesh",
            "group_ids": [Command.set([cls.env.ref("diamond.group_sudi_pickup_delivery_operator").id])],
        })
        cls.operator_2 = cls.env["res.users"].create({
            "name": "Suresh Onfield",
            "login": "sudi_onfield_suresh",
            "group_ids": [Command.set([cls.env.ref("diamond.group_sudi_pickup_delivery_operator").id])],
        })
        # Proof of delivery is configurable and its parameters persist in a
        # database between runs; these tests are about the stages, so pin them
        # off rather than inherit whatever the last run left behind.
        params = cls.env["ir.config_parameter"].sudo()
        for requirement in ("receiver_name", "signature", "photo"):
            params.set_param(f"sudi_diamond.pod_require_{requirement}", "0")

    def _patch_dispatch(self):
        """Capture the dispatch notification instead of sending WhatsApp."""
        Picking = type(self.env["stock.picking"])
        calls = []
        original = Picking._sudi_notify_delivery_assigned

        def fake(pickings):
            calls.append(pickings.ids)

        Picking._sudi_notify_delivery_assigned = fake
        self.addCleanup(setattr, Picking, "_sudi_notify_delivery_assigned", original)
        return calls

    def test_new_delivery_awaits_confirmation_and_no_dispatch_yet(self):
        calls = self._patch_dispatch()
        receipt = self._create_receipt()
        delivery = receipt.sudi_delivery_ids
        self.assertEqual(delivery.state, "assigned")
        self.assertEqual(delivery.sudi_delivery_stage, "awaiting")
        self.assertFalse(receipt.sudi_delivery_stage)
        self.assertEqual(calls, [])

    def test_take_for_delivery_sets_person_stage_and_dispatch(self):
        calls = self._patch_dispatch()
        receipt_a = self._create_receipt()
        receipt_b = self._create_receipt(qty=4.0, pcs=4.0)
        deliveries = receipt_a.sudi_delivery_ids | receipt_b.sudi_delivery_ids

        deliveries.with_user(self.operator).action_sudi_take_for_delivery()

        self.assertEqual(set(deliveries.mapped("sudi_delivery_stage")), {"out"})
        self.assertEqual(deliveries.sudi_pickup_user_id, self.operator)
        self.assertTrue(all(deliveries.mapped("sudi_out_for_delivery_datetime")))
        self.assertEqual(calls, [deliveries.ids])
        self.assertTrue(any("Taken for delivery" in message.body for message in deliveries[0].message_ids))

        # Already taken: nothing left to take.
        with self.assertRaises(UserError):
            deliveries.with_user(self.operator_2).action_sudi_take_for_delivery()

    def test_release_returns_delivery_to_awaiting(self):
        self._patch_dispatch()
        delivery = self._create_receipt().sudi_delivery_ids
        delivery.with_user(self.operator).action_sudi_take_for_delivery()
        delivery.with_user(self.operator).action_sudi_release_delivery()
        self.assertEqual(delivery.sudi_delivery_stage, "awaiting")
        self.assertFalse(delivery.sudi_pickup_user_id)
        delivery.with_user(self.operator_2).action_sudi_take_for_delivery()
        self.assertEqual(delivery.sudi_pickup_user_id, self.operator_2)

    def test_mark_delivered_keeps_the_person_who_took_it(self):
        self._patch_dispatch()
        delivery = self._create_receipt().sudi_delivery_ids
        delivery.with_user(self.operator).action_sudi_take_for_delivery()

        delivery.with_user(self.operator_2).action_sudi_mark_delivered()

        self.assertEqual(delivery.state, "done")
        self.assertEqual(delivery.sudi_delivery_stage, "delivered")
        self.assertEqual(delivery.sudi_pickup_user_id, self.operator)
        self.assertEqual(delivery.sudi_billing_status, "to_bill")

    def test_mark_delivered_without_taking_records_the_deliverer(self):
        self._patch_dispatch()
        delivery = self._create_receipt().sudi_delivery_ids
        delivery.with_user(self.operator).action_sudi_mark_delivered()
        self.assertEqual(delivery.sudi_delivery_stage, "delivered")
        self.assertEqual(delivery.sudi_pickup_user_id, self.operator)

    def test_cancelled_delivery_stage(self):
        self._patch_dispatch()
        delivery = self._create_receipt().sudi_delivery_ids
        delivery.action_cancel()
        self.assertEqual(delivery.sudi_delivery_stage, "cancelled")
