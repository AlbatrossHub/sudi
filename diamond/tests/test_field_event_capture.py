import base64
import io
from datetime import timedelta

from PIL import Image

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError

from .test_diamond_job_work import SudiJobWorkCase


def _png(size=(1, 1)):
    """A real PNG. fields.Image and ir.attachment both run it through PIL,
    so a hand-written base64 literal is not good enough."""
    buffer = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue())


PIXEL = _png()


class TestSudiEventDatetime(SudiJobWorkCase):
    """The clamp on a caller-supplied event time (offline capture)."""

    def setUp(self):
        super().setUp()
        self.Picking = self.env["stock.picking"]

    def test_missing_occurred_at_means_now(self):
        value, error = self.Picking._sudi_parse_event_datetime(None)
        self.assertIsNone(error)
        self.assertLess(abs((value - fields.Datetime.now()).total_seconds()), 5)

    def test_offset_aware_string_is_converted_to_utc(self):
        # 14:30 in IST is 09:00 UTC. The client sends an explicit offset exactly
        # so this cannot be guessed wrong.
        now = fields.Datetime.now()
        ist = (now - timedelta(hours=2)).replace(microsecond=0)
        value, error = self.Picking._sudi_parse_event_datetime(
            (ist + timedelta(hours=5, minutes=30)).isoformat() + "+05:30"
        )
        self.assertIsNone(error)
        self.assertEqual(value, ist)

    def test_zulu_suffix_is_accepted(self):
        stamp = (fields.Datetime.now() - timedelta(minutes=10)).replace(microsecond=0)
        value, error = self.Picking._sudi_parse_event_datetime(stamp.isoformat() + "Z")
        self.assertIsNone(error)
        self.assertEqual(value, stamp)

    def test_small_clock_lead_is_clamped_not_refused(self):
        # A phone a few seconds fast is normal; it must not record the future.
        ahead = fields.Datetime.now() + timedelta(seconds=30)
        value, error = self.Picking._sudi_parse_event_datetime(ahead)
        self.assertIsNone(error)
        self.assertLessEqual(value, fields.Datetime.now())

    def test_far_future_is_clock_skew(self):
        ahead = fields.Datetime.now() + timedelta(hours=3)
        value, error = self.Picking._sudi_parse_event_datetime(ahead)
        self.assertIsNone(value)
        self.assertEqual(error, "CLOCK_SKEW")

    def test_older_than_the_backdate_window_is_stale(self):
        old = fields.Datetime.now() - timedelta(hours=100)
        value, error = self.Picking._sudi_parse_event_datetime(old)
        self.assertIsNone(value)
        self.assertEqual(error, "STALE_INTENT")

    def test_unparseable_value_is_validation(self):
        value, error = self.Picking._sudi_parse_event_datetime("yesterday-ish")
        self.assertIsNone(value)
        self.assertEqual(error, "VALIDATION")

    def test_raising_wrapper_reports_the_reason(self):
        with self.assertRaises(UserError):
            self.Picking._sudi_event_datetime(fields.Datetime.now() + timedelta(days=1))


class TestSudiFieldEventCapture(SudiJobWorkCase):
    """Pickup and delivery events carry the time they were captured."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = cls.env["res.users"].create({
            "name": "Ramesh Onfield",
            "login": "sudi_capture_operator",
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_pickup_delivery_operator").id,
            ])],
        })

    def _pending_receipt(self):
        return self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": True,
            "sudi_customer_contact": "9876543210",
        })

    def _patch_dispatch(self):
        Picking = type(self.env["stock.picking"])
        original = Picking._sudi_notify_delivery_assigned
        Picking._sudi_notify_delivery_assigned = lambda records: None
        self.addCleanup(setattr, Picking, "_sudi_notify_delivery_assigned", original)

    def test_confirm_pickup_records_the_capture_time(self):
        receipt = self._pending_receipt()
        self.assertEqual(receipt.state, "sudi_pickup_pending")
        captured = (fields.Datetime.now() - timedelta(hours=4)).replace(microsecond=0)

        receipt.with_user(self.operator).action_sudi_confirm_pickup(occurred_at=captured)

        self.assertEqual(receipt.sudi_pickup_datetime, captured)
        self.assertEqual(receipt.sudi_pickup_user_id, self.operator)

    def test_confirm_pickup_notes_the_delay_and_the_device(self):
        receipt = self._pending_receipt()
        captured = fields.Datetime.now() - timedelta(hours=4)

        receipt.with_user(self.operator).with_context(
            sudi_device_uid="phone-7"
        ).action_sudi_confirm_pickup(occurred_at=captured)

        bodies = receipt.message_ids.mapped("body")
        self.assertTrue(any("phone-7" in body for body in bodies), bodies)

    def test_an_online_confirm_adds_no_provenance_note(self):
        receipt = self._pending_receipt()
        before = len(receipt.message_ids)
        receipt.with_user(self.operator).action_sudi_confirm_pickup()
        added = receipt.message_ids[:len(receipt.message_ids) - before].mapped("body")
        self.assertFalse(
            any("received at" in body for body in added),
            "an event recorded as it happens needs no provenance note",
        )

    def test_a_stale_pickup_is_refused(self):
        receipt = self._pending_receipt()
        with self.assertRaises(UserError):
            receipt.with_user(self.operator).action_sudi_confirm_pickup(
                occurred_at=fields.Datetime.now() - timedelta(hours=100)
            )

    def test_take_for_delivery_records_the_capture_time(self):
        self._patch_dispatch()
        delivery = self._create_receipt().sudi_delivery_ids
        captured = (fields.Datetime.now() - timedelta(hours=2)).replace(microsecond=0)

        delivery.with_user(self.operator).action_sudi_take_for_delivery(occurred_at=captured)

        self.assertEqual(delivery.sudi_out_for_delivery_datetime, captured)
        self.assertEqual(delivery.sudi_delivery_stage, "out")

    def test_mark_delivered_backdates_date_done(self):
        self._patch_dispatch()
        delivery = self._create_receipt().sudi_delivery_ids
        delivery.with_user(self.operator).action_sudi_take_for_delivery()
        captured = (fields.Datetime.now() - timedelta(hours=3)).replace(microsecond=0)

        delivery.with_user(self.operator).action_sudi_mark_delivered(occurred_at=captured)

        self.assertEqual(delivery.state, "done")
        self.assertEqual(delivery.sudi_delivery_stage, "delivered")
        self.assertEqual(delivery.date_done, captured)
        self.assertEqual(delivery.sudi_pickup_datetime, captured)


class TestSudiProofOfDelivery(SudiJobWorkCase):
    """Proof of delivery: always captured, mandatory only when configured."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = cls.env["res.users"].create({
            "name": "Suresh Onfield",
            "login": "sudi_pod_operator",
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_pickup_delivery_operator").id,
            ])],
        })

    def setUp(self):
        super().setUp()
        Picking = type(self.env["stock.picking"])
        original = Picking._sudi_notify_delivery_assigned
        Picking._sudi_notify_delivery_assigned = lambda records: None
        self.addCleanup(setattr, Picking, "_sudi_notify_delivery_assigned", original)

    def setUpRequirements(self, **requirements):
        """Pin every proof-of-delivery switch for this test.

        They are ir.config_parameter values, which persist in a database
        between runs, so a test that read the shipped default would pass or
        fail depending on what the last run left behind.
        """
        params = self.env["ir.config_parameter"].sudo()
        for name in ("receiver_name", "signature", "photo"):
            params.set_param(
                f"sudi_diamond.pod_require_{name}",
                "1" if requirements.get(name) else "0",
            )

    def _require(self, requirement):
        self.setUpRequirements(**{requirement: True})

    def _out_for_delivery(self):
        delivery = self._create_receipt().sudi_delivery_ids
        delivery.with_user(self.operator).action_sudi_take_for_delivery()
        return delivery

    def test_nothing_is_enforced_when_every_switch_is_off(self):
        self.setUpRequirements()
        delivery = self._out_for_delivery()
        delivery.with_user(self.operator).action_sudi_mark_delivered()
        self.assertEqual(delivery.state, "done")

    def test_receiver_name_is_recorded_when_supplied(self):
        self.setUpRequirements()
        delivery = self._out_for_delivery()
        delivery.with_user(self.operator).action_sudi_mark_delivered(
            receiver_name="  Mehul Shah  "
        )
        self.assertEqual(delivery.sudi_pod_receiver_name, "Mehul Shah")

    def test_a_required_receiver_name_blocks_delivery(self):
        self._require("receiver_name")
        delivery = self._out_for_delivery()
        with self.assertRaises(UserError):
            delivery.with_user(self.operator).action_sudi_mark_delivered()
        self.assertNotEqual(delivery.state, "done")

    def test_a_required_receiver_name_is_satisfied_by_the_stored_value(self):
        # Typed into the form earlier, then delivered: not asked for twice.
        self._require("receiver_name")
        delivery = self._out_for_delivery()
        delivery.sudi_pod_receiver_name = "Mehul Shah"
        delivery.with_user(self.operator).action_sudi_mark_delivered()
        self.assertEqual(delivery.state, "done")

    def test_a_required_signature_blocks_then_passes(self):
        self._require("signature")
        delivery = self._out_for_delivery()
        with self.assertRaises(UserError):
            delivery.with_user(self.operator).action_sudi_mark_delivered()
        delivery.with_user(self.operator).action_sudi_mark_delivered(signature=PIXEL)
        self.assertEqual(delivery.state, "done")
        self.assertTrue(delivery.sudi_pod_signature)

    def test_a_required_photo_blocks_then_passes(self):
        self._require("photo")
        delivery = self._out_for_delivery()
        with self.assertRaises(UserError):
            delivery.with_user(self.operator).action_sudi_mark_delivered()
        delivery.with_user(self.operator).action_sudi_mark_delivered(photo_datas=[PIXEL, PIXEL])
        self.assertEqual(delivery.state, "done")
        self.assertEqual(len(delivery.sudi_pod_attachment_ids), 2)


class TestSudiJangadPages(SudiJobWorkCase):
    """A jangad can run to several sheets; page 1 stays in the Image field."""

    def test_first_page_lands_in_the_image_field(self):
        receipt = self._create_receipt()
        receipt.sudi_jangad_image = False
        receipt.sudi_jangad_attachment_ids = [Command.clear()]

        attachments = receipt._sudi_add_jangad_pages([PIXEL])

        self.assertFalse(attachments)
        self.assertTrue(receipt.sudi_jangad_image)
        self.assertEqual(receipt.sudi_jangad_page_count, 1)

    def test_later_pages_become_attachments(self):
        receipt = self._create_receipt()
        receipt.sudi_jangad_image = False
        receipt.sudi_jangad_attachment_ids = [Command.clear()]

        attachments = receipt._sudi_add_jangad_pages([PIXEL, PIXEL, PIXEL])

        self.assertEqual(len(attachments), 2)
        self.assertTrue(receipt.sudi_jangad_image)
        self.assertEqual(receipt.sudi_jangad_page_count, 3)
        # Every page reaches the WhatsApp notification, not just the extras.
        self.assertEqual(len(receipt._sudi_get_jangad_image_attachment()), 3)

    def test_pages_are_appended_not_replaced(self):
        receipt = self._create_receipt()
        receipt.sudi_jangad_image = False
        receipt.sudi_jangad_attachment_ids = [Command.clear()]
        receipt._sudi_add_jangad_pages([PIXEL, PIXEL])
        receipt._sudi_add_jangad_pages([PIXEL])
        self.assertEqual(receipt.sudi_jangad_page_count, 3)


class TestSudiRoleSplit(SudiJobWorkCase):
    """Job work is a different role from pickup and delivery."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.field_only = cls.env["res.users"].create({
            "name": "Field Only",
            "login": "sudi_field_only",
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_pickup_delivery_operator").id,
            ])],
        })
        cls.job_work_only = cls.env["res.users"].create({
            "name": "Job Work Only",
            "login": "sudi_job_work_only",
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_job_work_user").id,
            ])],
        })

    def test_job_work_user_can_transfer_department(self):
        receipt = self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": True,
            "sudi_pickup_datetime": fields.Datetime.now(),
            "move_ids": [self._prepare_receipt_move_command()],
        })
        receipt.action_confirm()
        self.assertEqual(receipt.state, "assigned")

        receipt.with_user(self.job_work_only)._sudi_transfer_department(self.job_type)

        self.assertEqual(receipt.sudi_current_department_id, self.job_type)

    def test_a_field_operator_alone_cannot_transfer_department(self):
        receipt = self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": True,
            "sudi_pickup_datetime": fields.Datetime.now(),
            "move_ids": [self._prepare_receipt_move_command()],
        })
        receipt.action_confirm()
        with self.assertRaises(AccessError):
            receipt.with_user(self.field_only)._sudi_transfer_department(self.job_type)

    def test_a_job_work_user_alone_cannot_take_a_delivery(self):
        Picking = type(self.env["stock.picking"])
        original = Picking._sudi_notify_delivery_assigned
        Picking._sudi_notify_delivery_assigned = lambda records: None
        self.addCleanup(setattr, Picking, "_sudi_notify_delivery_assigned", original)

        delivery = self._create_receipt().sudi_delivery_ids
        with self.assertRaises(AccessError):
            delivery.with_user(self.job_work_only).action_sudi_take_for_delivery()
