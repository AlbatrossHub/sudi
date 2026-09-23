import base64
import io
import json
import uuid
from datetime import timedelta

from PIL import Image

from odoo import Command, fields
from odoo.tests.common import tagged

from .common import SudiApiCase


def _png(size=(4, 4)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


@tagged("post_install", "-at_install")
class SudiIntentCase(SudiApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator.sudo().write({"group_ids": [
            Command.link(cls.env.ref("diamond.group_sudi_job_work_user").id)
        ]})
        cls.env["ir.config_parameter"].sudo().set_param(
            "sudi_sync.visibility_lag_seconds", "0"
        )
        cls.partner = cls.env["res.partner"].create({"name": "Kiran Gems"})
        cls.product = cls.env.ref("diamond.product_customer_diamond_parcel")
        cls.job_type = cls.env.ref("diamond.job_type_laser_inscription")
        cls.job_type_2 = cls.env["sudi.diamond.job.type"].create({
            "name": "API Polishing",
            "service_product_id": cls.job_type.service_product_id.id,
        })
        # hr_timesheet refuses a line without an active employee, and real
        # field staff are employees.
        for user in (cls.operator, cls.job_worker):
            cls.env["hr.employee"].sudo().create({
                "name": user.name,
                "user_id": user.id,
                "company_id": cls.env.company.id,
            })
        warehouse = cls.env["stock.warehouse"].sudo().search([], limit=1)
        cls.type_in = warehouse.in_type_id
        cls.type_out = warehouse.out_type_id
        cls.type_out.sudo().create_backorder = "always"

    def setUp(self):
        super().setUp()
        # WhatsApp dispatch is not what these tests are about, and the
        # connector is not connected in a test database.
        Picking = type(self.env["stock.picking"])
        for name in (
            "_sudi_notify_pickup_scheduled", "_sudi_notify_pickup_confirmed",
            "_sudi_notify_pickup_cancelled", "_sudi_notify_delivery_assigned",
            "_sudi_notify_delivery_completed",
        ):
            original = getattr(Picking, name)
            setattr(Picking, name, lambda records: None)
            self.addCleanup(setattr, Picking, name, original)

    # ------------------------------------------------------------------ data
    def _pending_receipt(self):
        receipt = self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.type_in.id,
            "location_id": self.type_in.default_location_src_id.id,
            "location_dest_id": self.type_in.default_location_dest_id.id,
            "sudi_is_diamond_job_work": True,
            "sudi_customer_contact": "9876543210",
            "sudi_pickup_address": "12, Mahidharpura, Surat",
        })
        self._settle()
        return receipt

    def _move(self):
        return Command.create({
            "product_id": self.product.id,
            "product_uom_qty": 10.0,
            "product_uom": self.product.uom_id.id,
            "location_id": self.type_in.default_location_src_id.id,
            "location_dest_id": self.type_in.default_location_dest_id.id,
            "sudi_sr": 1,
            "sudi_size": "1.00 MM",
            "sudi_pcs_qty": 10.0,
            "sudi_carats": 2.5,
            "sudi_job_type_id": self.job_type.id,
        })

    def _assigned_receipt(self):
        receipt = self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.type_in.id,
            "location_id": self.type_in.default_location_src_id.id,
            "location_dest_id": self.type_in.default_location_dest_id.id,
            "sudi_is_diamond_job_work": True,
            "sudi_pickup_datetime": fields.Datetime.now(),
            "move_ids": [self._move()],
        })
        receipt.action_confirm()
        self._settle()
        return receipt

    def _delivery(self):
        receipt = self._assigned_receipt()
        for move in receipt.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        receipt.button_validate()
        self._settle()
        return receipt.sudi_delivery_ids

    def _settle(self):
        self.env.flush_all()
        self.env.cr.precommit.run()

    # ------------------------------------------------------------------ http
    def _intent(self, path, payload, token, key=None, method="POST"):
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        if key:
            headers["Idempotency-Key"] = key
        self.env.flush_all()
        return self.url_open(
            f"/api/field/v1{path}",
            data=json.dumps(payload),
            headers=headers,
            method=method,
        )

    def _upload(self, token, raw=None, filename="page.png", content_type="image/png"):
        self.env.flush_all()
        return self.url_open(
            "/api/field/v1/uploads",
            files={"file": (filename, raw or _png(), content_type)},
            headers={"Authorization": f"Bearer {token}"},
        )

    def _token(self, user=None, uid=None):
        return self._login(
            user=user, device_uid=uid or f"intent-{uuid.uuid4().hex[:10]}"
        )["access_token"]


class TestSudiConfirmPickup(SudiIntentCase):

    def test_confirming_records_the_operator_and_returns_the_document(self):
        receipt = self._pending_receipt()
        token = self._token()

        response = self._intent(f"/pickups/{receipt.id}/confirm", {}, token)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["code"], "OK")
        self.assertEqual(body["record"]["stage"], "collected")
        self.assertEqual(body["record"]["collected_by"]["id"], self.operator.id)
        self.assertEqual(receipt.sudi_pickup_user_id, self.operator)

    def test_a_retry_with_the_same_key_replays_the_first_answer(self):
        receipt = self._pending_receipt()
        token = self._token()
        key = "capture-key-1"

        first = self._intent(f"/pickups/{receipt.id}/confirm", {}, token, key=key)
        second = self._intent(f"/pickups/{receipt.id}/confirm", {}, token, key=key)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())

    def test_a_retry_with_a_fresh_key_is_already_done_not_a_duplicate(self):
        # What happens when a client wrongly regenerates its key: the action
        # must still not happen twice.
        receipt = self._pending_receipt()
        token = self._token()
        self._intent(f"/pickups/{receipt.id}/confirm", {}, token, key="k-a")

        second = self._intent(f"/pickups/{receipt.id}/confirm", {}, token, key="k-b")

        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["code"], "ALREADY_DONE")

    def test_someone_else_getting_there_first_is_a_conflict_that_names_them(self):
        receipt = self._pending_receipt()
        receipt.with_user(self.operator_2).action_sudi_confirm_pickup()
        self._settle()
        token = self._token()

        response = self._intent(f"/pickups/{receipt.id}/confirm", {}, token)

        body = self.assertEnvelope(response, 409, "ALREADY_CONFIRMED")
        self.assertFalse(body["retryable"])
        self.assertEqual(body["detail"]["user"], self.operator_2.name)
        self.assertEqual(body["resync"], [receipt.id])

    def test_a_backdated_capture_is_recorded_at_the_time_it_happened(self):
        receipt = self._pending_receipt()
        token = self._token()
        captured = (fields.Datetime.now() - timedelta(hours=3)).replace(microsecond=0)

        response = self._intent(
            f"/pickups/{receipt.id}/confirm",
            {"occurred_at": captured.isoformat() + "Z"},
            token,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(receipt.sudi_pickup_datetime, captured)

    def test_a_device_clock_far_ahead_is_refused(self):
        receipt = self._pending_receipt()
        token = self._token()
        ahead = (fields.Datetime.now() + timedelta(hours=3)).isoformat() + "Z"

        response = self._intent(
            f"/pickups/{receipt.id}/confirm", {"occurred_at": ahead}, token
        )

        self.assertEnvelope(response, 422, "CLOCK_SKEW")
        self.assertEqual(receipt.state, "sudi_pickup_pending")

    def test_a_capture_older_than_the_window_is_stale(self):
        receipt = self._pending_receipt()
        token = self._token()
        old = (fields.Datetime.now() - timedelta(hours=100)).isoformat() + "Z"

        response = self._intent(
            f"/pickups/{receipt.id}/confirm", {"occurred_at": old}, token
        )

        self.assertEnvelope(response, 409, "STALE_INTENT")

    def test_the_provenance_note_names_the_device(self):
        receipt = self._pending_receipt()
        token = self._token(uid="intent-device-42")
        captured = (fields.Datetime.now() - timedelta(hours=2)).isoformat() + "Z"

        self._intent(
            f"/pickups/{receipt.id}/confirm", {"occurred_at": captured}, token
        )

        bodies = receipt.sudo().message_ids.mapped("body")
        self.assertTrue(
            any("intent-device-42" in body for body in bodies), bodies
        )

    def test_a_receipt_outside_the_work_list_is_not_found(self):
        other = self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.type_in.id,
            "location_id": self.type_in.default_location_src_id.id,
            "location_dest_id": self.type_in.default_location_dest_id.id,
            "sudi_is_diamond_job_work": False,
        })
        self._settle()
        response = self._intent(f"/pickups/{other.id}/confirm", {}, self._token())
        self.assertEnvelope(response, 404, "NOT_IN_SCOPE")

    def test_a_job_work_only_account_cannot_confirm_a_pickup(self):
        receipt = self._pending_receipt()
        token = self._token(user=self.job_worker)
        response = self._intent(f"/pickups/{receipt.id}/confirm", {}, token)
        self.assertEnvelope(response, 403, "AUTH")

    def test_cancelling_gives_a_reason_and_archives_the_receipt(self):
        receipt = self._pending_receipt()
        token = self._token()

        response = self._intent(
            f"/pickups/{receipt.id}/cancel", {"reason": "Shop was shut"}, token
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(receipt.sudo().state, "cancel")
        self.assertTrue(
            any("Shop was shut" in body for body in receipt.sudo().message_ids.mapped("body"))
        )

    def test_a_cancel_reason_is_required(self):
        receipt = self._pending_receipt()
        response = self._intent(f"/pickups/{receipt.id}/cancel", {}, self._token())
        self.assertEnvelope(response, 422, "VALIDATION")


class TestSudiUploadsAndJangad(SudiIntentCase):

    def test_staging_returns_a_reference_and_a_digest(self):
        import hashlib

        raw = _png()
        response = self._upload(self._token(), raw=raw)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(body["bytes"], len(raw))
        self.assertTrue(body["reference"])

    def test_confirming_attaches_the_staged_pages_in_order(self):
        receipt = self._pending_receipt()
        token = self._token()
        first = self._upload(token).json()["reference"]
        second = self._upload(token).json()["reference"]

        response = self._intent(
            f"/pickups/{receipt.id}/confirm",
            {"upload_ids": [first, second]},
            token,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["record"]["jangad_pages"], 2)
        self.assertTrue(receipt.sudo().sudi_jangad_image)
        self.assertEqual(len(receipt.sudo().sudi_jangad_attachment_ids), 1)

    def test_a_reference_cannot_be_used_twice(self):
        first_receipt = self._pending_receipt()
        second_receipt = self._pending_receipt()
        token = self._token()
        reference = self._upload(token).json()["reference"]

        self._intent(
            f"/pickups/{first_receipt.id}/confirm", {"upload_ids": [reference]}, token
        )
        response = self._intent(
            f"/pickups/{second_receipt.id}/confirm", {"upload_ids": [reference]}, token
        )

        self.assertEnvelope(response, 422, "VALIDATION")

    def test_one_operator_cannot_use_anothers_upload(self):
        receipt = self._pending_receipt()
        theirs = self._upload(self._token(user=self.operator_2)).json()["reference"]

        response = self._intent(
            f"/pickups/{receipt.id}/confirm", {"upload_ids": [theirs]}, self._token()
        )

        self.assertEnvelope(response, 422, "VALIDATION")
        self.assertEqual(receipt.state, "sudi_pickup_pending")

    def test_an_unknown_reference_is_refused(self):
        receipt = self._pending_receipt()
        response = self._intent(
            f"/pickups/{receipt.id}/confirm",
            {"upload_ids": ["nope"]},
            self._token(),
        )
        self.assertEnvelope(response, 422, "VALIDATION")


class TestSudiDeliveryIntents(SudiIntentCase):

    def test_taking_a_parcel_puts_it_out_with_the_operator(self):
        delivery = self._delivery()
        token = self._token()

        response = self._intent("/deliveries/take", {"ids": [delivery.id]}, token)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([doc["id"] for doc in body["taken"]], [delivery.id])
        self.assertEqual(body["taken"][0]["stage"], "out")
        self.assertEqual(body["failed"], [])

    def test_a_partial_result_is_normal(self):
        mine = self._delivery()
        theirs = self._delivery()
        theirs.with_user(self.operator_2).action_sudi_take_for_delivery()
        self._settle()
        token = self._token()

        body = self._intent(
            "/deliveries/take", {"ids": [mine.id, theirs.id]}, token
        ).json()

        self.assertEqual([doc["id"] for doc in body["taken"]], [mine.id])
        self.assertEqual(len(body["failed"]), 1)
        self.assertEqual(body["failed"][0]["id"], theirs.id)
        self.assertEqual(body["failed"][0]["code"], "ALREADY_TAKEN")
        self.assertEqual(body["failed"][0]["detail"]["user"], self.operator_2.name)

    def test_taking_what_is_already_in_my_bag_is_not_a_failure(self):
        delivery = self._delivery()
        token = self._token()
        self._intent("/deliveries/take", {"ids": [delivery.id]}, token)

        body = self._intent("/deliveries/take", {"ids": [delivery.id]}, token).json()

        self.assertEqual([doc["id"] for doc in body["taken"]], [delivery.id])
        self.assertEqual(body["failed"], [])

    def test_releasing_someone_elses_parcel_is_refused(self):
        delivery = self._delivery()
        delivery.with_user(self.operator_2).action_sudi_take_for_delivery()
        self._settle()

        response = self._intent(
            f"/deliveries/{delivery.id}/release", {}, self._token()
        )

        body = self.assertEnvelope(response, 409, "ALREADY_TAKEN")
        self.assertIn(self.operator_2.name, body["message"])
        self.assertEqual(delivery.sudi_pickup_user_id, self.operator_2)

    def test_releasing_my_own_parcel_returns_it_to_the_pool(self):
        delivery = self._delivery()
        token = self._token()
        self._intent("/deliveries/take", {"ids": [delivery.id]}, token)

        response = self._intent(f"/deliveries/{delivery.id}/release", {}, token)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["record"]["stage"], "awaiting")

    def test_delivering_records_the_proof_and_the_time(self):
        delivery = self._delivery()
        token = self._token()
        self._intent("/deliveries/take", {"ids": [delivery.id]}, token)
        photo = self._upload(token).json()["reference"]
        captured = (fields.Datetime.now() - timedelta(hours=1)).replace(microsecond=0)

        response = self._intent(
            f"/deliveries/{delivery.id}/deliver",
            {
                "occurred_at": captured.isoformat() + "Z",
                "receiver_name": "Mehul Shah",
                "upload_ids": [photo],
            },
            token,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["record"]["received_by"], "Mehul Shah")
        self.assertEqual(delivery.state, "done")
        self.assertEqual(delivery.date_done, captured)
        self.assertEqual(len(delivery.sudi_pod_attachment_ids), 1)

    def test_delivering_something_already_delivered_is_a_success(self):
        # Delivered is delivered: whoever got there first, the outcome the
        # operator wanted has happened.
        delivery = self._delivery()
        delivery.with_user(self.operator_2).action_sudi_mark_delivered()
        self._settle()
        token = self._token()

        response = self._intent(f"/deliveries/{delivery.id}/deliver", {}, token)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["code"], "ALREADY_DONE")


class TestSudiJobWorkIntents(SudiIntentCase):

    def test_transferring_a_department_moves_the_receipt(self):
        receipt = self._assigned_receipt()
        token = self._token()

        response = self._intent(
            f"/jobwork/{receipt.id}/department",
            {"department_id": self.job_type_2.id},
            token,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["record"]["current_department"]["id"], self.job_type_2.id
        )

    def test_transferring_to_the_same_department_is_already_done(self):
        receipt = self._assigned_receipt()
        token = self._token()
        self._intent(
            f"/jobwork/{receipt.id}/department",
            {"department_id": self.job_type_2.id}, token,
        )

        again = self._intent(
            f"/jobwork/{receipt.id}/department",
            {"department_id": self.job_type_2.id}, token,
        )

        self.assertEqual(again.json()["code"], "ALREADY_DONE")

    def test_an_unknown_department_is_refused(self):
        receipt = self._assigned_receipt()
        response = self._intent(
            f"/jobwork/{receipt.id}/department", {"department_id": 0}, self._token()
        )
        self.assertEnvelope(response, 422, "VALIDATION")

    def test_a_pickup_only_account_cannot_transfer_a_department(self):
        receipt = self._assigned_receipt()
        token = self._token(user=self.operator_2)
        response = self._intent(
            f"/jobwork/{receipt.id}/department",
            {"department_id": self.job_type_2.id}, token,
        )
        self.assertEnvelope(response, 403, "AUTH")

    def test_the_timer_starts_and_stops_and_banks_the_time(self):
        receipt = self._assigned_receipt()
        token = self._token()

        started = self._intent(f"/jobwork/{receipt.id}/timer", {"action": "start"}, token)
        self.assertEqual(started.status_code, 200, started.text)
        self.assertTrue(started.json()["record"]["timer"]["running"])

        stopped = self._intent(f"/jobwork/{receipt.id}/timer", {"action": "stop"}, token)
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertFalse(stopped.json()["record"]["timer"]["running"])

    def test_starting_a_running_timer_is_already_done(self):
        receipt = self._assigned_receipt()
        token = self._token()
        self._intent(f"/jobwork/{receipt.id}/timer", {"action": "start"}, token)

        again = self._intent(f"/jobwork/{receipt.id}/timer", {"action": "start"}, token)

        self.assertEqual(again.json()["code"], "ALREADY_DONE")

    def test_stopping_a_timer_that_is_not_running_is_already_done(self):
        receipt = self._assigned_receipt()
        response = self._intent(
            f"/jobwork/{receipt.id}/timer", {"action": "stop"}, self._token()
        )
        self.assertEqual(response.json()["code"], "ALREADY_DONE")

    def test_an_invalid_timer_action_is_a_validation_error(self):
        receipt = self._assigned_receipt()
        response = self._intent(
            f"/jobwork/{receipt.id}/timer", {"action": "pause"}, self._token()
        )
        self.assertEnvelope(response, 422, "VALIDATION")

    def test_finishing_completes_the_receipt(self):
        receipt = self._assigned_receipt()
        for move in receipt.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        self._settle()
        token = self._token()

        response = self._intent(f"/jobwork/{receipt.id}/finish", {}, token)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(receipt.state, "done")
