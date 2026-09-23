import base64
import io
import json

from PIL import Image

from odoo.tests.common import tagged

from .common import CUSTOMER_ROOT
from .test_customer import SudiCustomerCase


def _png(size=(4, 4)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


@tagged("post_install", "-at_install")
class SudiJangadCase(SudiCustomerCase):

    def setUp(self):
        super().setUp()
        # The jangad upload notifies the pickup staff over WhatsApp, which is
        # not what these tests are about.
        Picking = type(self.env["stock.picking"])
        original = Picking._sudi_notify_pickup_scheduled
        Picking._sudi_notify_pickup_scheduled = lambda records: None
        self.addCleanup(
            setattr, Picking, "_sudi_notify_pickup_scheduled", original
        )

    def _customer(self, phone=None, name="Kiran Gems", uid="jang-aaaa0001"):
        """A registered customer, and their access token."""
        if phone:
            self.phone = phone
        code = self._request_code()
        token = self._verify(code, uid=uid).json()["registration_token"]
        return self._cpost("/auth/register", {
            "registration_token": token, "name": name,
            "device": self._device(uid),
        }).json()["access_token"]

    def _stage(self, token, raw=None):
        self.env.flush_all()
        response = self.url_open(
            f"{CUSTOMER_ROOT}/uploads",
            files={"file": ("page.png", raw or _png(), "image/png")},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["reference"]

    def _submit(self, token, payload, key=None):
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        if key:
            headers["Idempotency-Key"] = key
        self.env.flush_all()
        return self.url_open(
            f"{CUSTOMER_ROOT}/jangad", data=json.dumps(payload), headers=headers
        )

    def _get(self, path, token):
        self.env.flush_all()
        return self.url_open(
            f"{CUSTOMER_ROOT}{path}", headers={"Authorization": f"Bearer {token}"}
        )

    def _receipt(self, reference):
        return self.env["stock.picking"].sudo().search([("name", "=", reference)])


class TestSudiCustomerAddresses(SudiJangadCase):

    def test_a_customer_with_no_address_yet_gets_an_empty_list(self):
        token = self._customer()
        response = self._get("/addresses", token)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])

    def test_an_address_on_the_account_is_offered(self):
        token = self._customer()
        partner = self.env["res.users"].sudo().search(
            [("login", "=", self.phone)]
        ).partner_id
        partner.sudo().write({"street": "12, Mahidharpura", "city": "Surat"})

        body = self._get("/addresses", token).json()

        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], partner.id)
        self.assertIn("Mahidharpura", body[0]["address"])
        self.assertTrue(body[0]["is_default"])

    def test_addresses_need_a_customer_token(self):
        self.assertEnvelope(self._get("/addresses", "nope"), 401, "AUTH")


class TestSudiJangadSubmission(SudiJangadCase):

    def test_submitting_a_single_page_creates_a_receipt(self):
        token = self._customer()
        reference = self._stage(token)

        response = self._submit(token, {
            "upload_ids": [reference],
            "manual_pickup_address": "12, Mahidharpura\nSurat 395003",
        })

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["reference"])
        self.assertEqual(body["pages"], 1)
        self.assertIn("Mahidharpura", body["pickup_address"])

        receipt = self._receipt(body["reference"])
        self.assertTrue(receipt.sudi_is_diamond_job_work)
        self.assertEqual(receipt.state, "sudi_pickup_pending")
        self.assertEqual(receipt.sudi_customer_contact, self.phone)
        self.assertTrue(receipt.sudi_jangad_image)

    def test_several_pages_land_in_page_order(self):
        token = self._customer()
        first = self._stage(token, _png((4, 4)))
        second = self._stage(token, _png((8, 8)))
        third = self._stage(token, _png((12, 12)))

        body = self._submit(token, {
            "upload_ids": [first, second, third],
            "manual_pickup_address": "12, Mahidharpura",
        }).json()

        self.assertEqual(body["pages"], 3)
        receipt = self._receipt(body["reference"])
        # Page 1 stays in the image field every existing reader uses.
        self.assertTrue(receipt.sudi_jangad_image)
        self.assertEqual(len(receipt.sudi_jangad_attachment_ids), 2)

    def test_the_receipt_is_filed_against_the_callers_own_number(self):
        # Never a number from the body: the whole point of authenticating.
        token = self._customer(phone="9812345690")
        reference = self._stage(token)

        body = self._submit(token, {
            "upload_ids": [reference],
            "manual_pickup_address": "12, Mahidharpura",
            "phone": "9800000009",
        }).json()

        self.assertEqual(self._receipt(body["reference"]).sudi_customer_contact, "9812345690")

    def test_a_known_address_can_be_chosen_by_id(self):
        token = self._customer()
        partner = self.env["res.users"].sudo().search(
            [("login", "=", self.phone)]
        ).partner_id
        partner.sudo().write({"street": "12, Mahidharpura", "city": "Surat"})
        address_id = self._get("/addresses", token).json()[0]["id"]
        reference = self._stage(token)

        body = self._submit(token, {
            "upload_ids": [reference], "pickup_address_id": address_id,
        }).json()

        receipt = self._receipt(body["reference"])
        self.assertEqual(receipt.sudi_pickup_address_id.id, address_id)

    def test_somebody_elses_address_cannot_be_used(self):
        other = self.env["res.partner"].sudo().create({
            "name": "Someone Else", "phone": "9800000123",
            "street": "Not your street",
        })
        token = self._customer()
        reference = self._stage(token)

        response = self._submit(token, {
            "upload_ids": [reference], "pickup_address_id": other.id,
        })

        self.assertEnvelope(response, 422, "VALIDATION")

    def test_an_address_is_required(self):
        token = self._customer()
        reference = self._stage(token)
        response = self._submit(token, {"upload_ids": [reference]})
        self.assertEnvelope(response, 422, "VALIDATION")

    def test_giving_both_kinds_of_address_is_refused(self):
        token = self._customer()
        reference = self._stage(token)
        response = self._submit(token, {
            "upload_ids": [reference],
            "pickup_address_id": 1,
            "manual_pickup_address": "12, Mahidharpura",
        })
        self.assertEnvelope(response, 422, "VALIDATION")

    def test_at_least_one_page_is_required(self):
        token = self._customer()
        response = self._submit(token, {
            "upload_ids": [], "manual_pickup_address": "12, Mahidharpura",
        })
        self.assertEnvelope(response, 422, "VALIDATION")

    def test_a_retry_with_the_same_key_files_one_receipt(self):
        token = self._customer()
        reference = self._stage(token)
        payload = {
            "upload_ids": [reference],
            "manual_pickup_address": "12, Mahidharpura",
        }

        first = self._submit(token, payload, key="jangad-capture-1")
        second = self._submit(token, payload, key="jangad-capture-1")

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(len(self._receipt(first.json()["reference"])), 1)

    def test_a_backdated_capture_is_recorded_in_the_chatter(self):
        from datetime import timedelta

        from odoo import fields

        token = self._customer(uid="jang-device-77")
        reference = self._stage(token)
        captured = (fields.Datetime.now() - timedelta(hours=5)).replace(microsecond=0)

        body = self._submit(token, {
            "upload_ids": [reference],
            "manual_pickup_address": "12, Mahidharpura",
            "occurred_at": captured.isoformat() + "Z",
        }).json()

        self.assertEqual(body["submitted_at"], captured.isoformat())
        bodies = self._receipt(body["reference"]).message_ids.mapped("body")
        self.assertTrue(any("jang-device-77" in text for text in bodies), bodies)

    def test_a_stale_capture_is_refused(self):
        from datetime import timedelta

        from odoo import fields

        token = self._customer()
        reference = self._stage(token)
        old = (fields.Datetime.now() - timedelta(hours=100)).isoformat() + "Z"

        response = self._submit(token, {
            "upload_ids": [reference],
            "manual_pickup_address": "12, Mahidharpura",
            "occurred_at": old,
        })

        self.assertEnvelope(response, 409, "STALE_INTENT")

    def test_one_customer_cannot_submit_anothers_pages(self):
        theirs = self._customer(phone="9812345691", uid="jang-bbbb0001")
        reference = self._stage(theirs)
        mine = self._customer(phone="9812345692", uid="jang-cccc0001")

        response = self._submit(mine, {
            "upload_ids": [reference],
            "manual_pickup_address": "12, Mahidharpura",
        })

        self.assertEnvelope(response, 422, "VALIDATION")

    def test_pages_cannot_be_submitted_twice(self):
        token = self._customer()
        reference = self._stage(token)
        payload = {
            "upload_ids": [reference],
            "manual_pickup_address": "12, Mahidharpura",
        }
        self._submit(token, payload)

        response = self._submit(token, payload)

        self.assertEnvelope(response, 422, "VALIDATION")

    def test_staff_cannot_submit_a_jangad_as_a_customer(self):
        staff = self._login(device_uid="jang-staff0001")["access_token"]
        response = self._submit(staff, {
            "upload_ids": ["x"], "manual_pickup_address": "12, Mahidharpura",
        })
        self.assertEnvelope(response, 401, "AUTH")

    def test_the_field_api_does_not_expose_the_customer_upload(self):
        response = self.url_open("/api/field/v1/jangad", data="{}", method="POST")
        self.assertEqual(response.status_code, 404, response.text)
