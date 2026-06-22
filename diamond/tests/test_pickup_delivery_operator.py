from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user
from odoo.addons.stock.tests.common import TestStockCommon


class TestSudiPickupDeliveryOperator(TestStockCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env.ref("diamond.product_customer_diamond_parcel")
        cls.job_type = cls.env.ref("diamond.job_type_laser_inscription")
        cls.partner = cls.env["res.partner"].create({
            "name": "Operator Customer",
            "street": "101 Pickup Street",
            "city": "Surat",
            "country_id": cls.env.ref("base.in").id,
            "state_id": cls.env.ref("base.state_in_gj").id,
            "l10n_in_gst_treatment": "unregistered",
        })
        cls.internal_user = new_test_user(
            cls.env,
            login="sudi_pickup_delivery_internal_user",
            groups="base.group_user",
        )
        cls.notify_user = new_test_user(
            cls.env,
            login="sudi_pickup_notify_user",
            groups="base.group_user",
        )
        cls.confirmed_notify_user = new_test_user(
            cls.env,
            login="sudi_pickup_confirmed_notify_user",
            groups="base.group_user",
        )

    def _set_pickup_notify_user(self, user):
        self.env["ir.config_parameter"].sudo().set_param(
            "diamond.sudi_pickup_notify_user_id",
            str(user.id),
        )

    def _set_pickup_confirmed_notify_user(self, user):
        self.env["ir.config_parameter"].sudo().set_param(
            "diamond.sudi_pickup_confirmed_notify_user_id",
            str(user.id),
        )

    def _receipt_move_command(self, qty=10.0):
        return Command.create({
            "name": self.product.display_name,
            "product_id": self.product.id,
            "product_uom_qty": qty,
            "product_uom": self.product.uom_id.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_pcs_qty": qty,
            "sudi_carats": qty / 2.0,
            "sudi_job_type_id": self.job_type.id,
        })

    def _create_pending_receipt(self):
        receipt = self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": True,
            "sudi_customer_contact": "9999999999",
            "move_ids": [self._receipt_move_command()],
        })
        self.assertEqual(receipt.state, "sudi_pickup_pending")
        return receipt

    def _create_active_delivery(self):
        receipt = self._create_pending_receipt()
        receipt.action_sudi_confirm_pickup()
        receipt.action_confirm()
        for move in receipt.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        receipt.button_validate()
        self.assertEqual(receipt.state, "done")
        self.assertEqual(len(receipt.sudi_delivery_ids), 1)
        return receipt.sudi_delivery_ids

    def _create_assigned_receipt(self):
        receipt = self._create_pending_receipt()
        receipt.action_sudi_confirm_pickup()
        receipt.action_confirm()
        self.assertEqual(receipt.state, "assigned")
        return receipt

    def test_internal_user_does_not_need_inventory_user_group(self):
        self.assertTrue(self.internal_user.has_group("base.group_user"))
        self.assertFalse(self.internal_user.has_group("stock.group_stock_user"))
        self.assertFalse(self.internal_user.has_group("diamond.group_sudi_pickup_delivery_operator"))

    def test_internal_user_can_confirm_only_pending_pickup_receipts(self):
        receipt = self._create_pending_receipt()

        receipt.with_user(self.internal_user).read(["name", "partner_id", "sudi_partner_address"])
        receipt.with_user(self.internal_user).action_sudi_confirm_pickup()

        self.assertEqual(receipt.sudi_pickup_user_id, self.internal_user)
        self.assertTrue(receipt.sudi_pickup_datetime)
        with self.assertRaises(AccessError):
            receipt.with_user(self.internal_user).read(["name"])

    def test_internal_user_cannot_read_unrelated_pickings(self):
        unrelated = self.env["stock.picking"].create({
            "picking_type_id": self.picking_type_int.id,
            "location_id": self.stock_location.id,
            "location_dest_id": self.customer_location.id,
            "sudi_is_diamond_job_work": False,
        })

        with self.assertRaises(AccessError):
            unrelated.with_user(self.internal_user).read(["name"])

    def test_internal_user_can_mark_active_delivery_done_and_read_it_today(self):
        delivery = self._create_active_delivery()

        delivery.with_user(self.internal_user).read(["name", "partner_id", "sudi_origin_receipt_name"])
        delivery.with_user(self.internal_user).action_sudi_mark_delivered()

        self.assertEqual(delivery.state, "done")
        self.assertEqual(delivery.sudi_pickup_user_id, self.internal_user)
        delivery.with_user(self.internal_user).read(["name", "date_done"])

    def test_internal_user_cannot_read_older_done_deliveries(self):
        delivery = self._create_active_delivery()
        delivery.action_sudi_mark_delivered()
        delivery.date_done = fields.Datetime.now() - timedelta(days=2)

        with self.assertRaises(AccessError):
            delivery.with_user(self.internal_user).read(["name", "date_done"])

    def test_operator_menus_are_bound_to_internal_users(self):
        internal_group = self.env.ref("base.group_user")
        pickup_menu = self.env.ref("diamond.menu_sudi_operator_pickup_root")
        delivery_menu = self.env.ref("diamond.menu_sudi_operator_deliveries_root")
        job_work_menu = self.env.ref("diamond.menu_sudi_operator_job_work_root")

        self.assertIn(internal_group, pickup_menu.groups_id)
        self.assertIn(internal_group, delivery_menu.groups_id)
        self.assertIn(internal_group, job_work_menu.groups_id)
        self.assertEqual(pickup_menu.action, self.env.ref("diamond.action_sudi_operator_pickups"))
        self.assertEqual(delivery_menu.action, self.env.ref("diamond.action_sudi_operator_deliveries"))
        self.assertEqual(job_work_menu.action, self.env.ref("diamond.action_sudi_operator_job_work"))

    def test_internal_user_can_read_assigned_receipt(self):
        receipt = self._create_assigned_receipt()

        receipt.with_user(self.internal_user).read(["name", "partner_id", "state"])
        self.assertEqual(receipt.state, "assigned")

    def test_internal_user_cannot_read_non_assigned_receipt(self):
        receipt = self._create_pending_receipt()
        receipt.action_sudi_confirm_pickup()
        self.assertEqual(receipt.state, "draft")

        with self.assertRaises(AccessError):
            receipt.with_user(self.internal_user).read(["name"])

    def test_internal_user_cannot_read_unrelated_assigned_receipt(self):
        unrelated = self.env["stock.picking"].create({
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": False,
            "move_ids": [Command.create({
                "name": self.product.display_name,
                "product_id": self.product.id,
                "product_uom_qty": 1.0,
                "product_uom": self.product.uom_id.id,
                "location_id": self.supplier_location.id,
                "location_dest_id": self.stock_location.id,
            })],
        })
        unrelated.action_confirm()
        self.assertEqual(unrelated.state, "assigned")

        with self.assertRaises(AccessError):
            unrelated.with_user(self.internal_user).read(["name"])

    def test_pending_receipt_creation_notifies_configured_user(self):
        self._set_pickup_notify_user(self.notify_user)
        with patch.object(type(self.notify_user), "_bus_send") as mock_bus_send:
            receipt = self._create_pending_receipt()

        messages = receipt.message_ids.filtered(
            lambda message: self.notify_user.partner_id in message.notified_partner_ids
        )
        self.assertEqual(len(messages), 1)
        self.assertIn(receipt.name, messages.subject)
        mock_bus_send.assert_called_once()
        _channel, payload = mock_bus_send.call_args[0]
        self.assertEqual(_channel, "simple_notification")
        self.assertEqual(payload["type"], "info")
        self.assertTrue(payload["sticky"])

    def test_non_diamond_picking_creation_does_not_notify(self):
        self._set_pickup_notify_user(self.notify_user)
        unrelated = self.env["stock.picking"].create({
            "picking_type_id": self.picking_type_int.id,
            "location_id": self.stock_location.id,
            "location_dest_id": self.customer_location.id,
            "sudi_is_diamond_job_work": False,
        })

        messages = unrelated.message_ids.filtered(
            lambda message: self.notify_user.partner_id in message.notified_partner_ids
        )
        self.assertFalse(messages)

    def test_confirm_pickup_notifies_configured_user(self):
        self._set_pickup_confirmed_notify_user(self.confirmed_notify_user)
        receipt = self._create_pending_receipt()
        with patch.object(type(self.confirmed_notify_user), "_bus_send") as mock_bus_send:
            receipt.action_sudi_confirm_pickup()

        messages = receipt.message_ids.filtered(
            lambda message: self.confirmed_notify_user.partner_id in message.notified_partner_ids
        )
        self.assertEqual(len(messages), 1)
        self.assertIn(receipt.name, messages.subject)
        self.assertIn("validate the Jangad", messages.body)
        mock_bus_send.assert_called_once()
        _channel, payload = mock_bus_send.call_args[0]
        self.assertEqual(_channel, "simple_notification")
        self.assertEqual(payload["type"], "success")
        self.assertTrue(payload["sticky"])
