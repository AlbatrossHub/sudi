import json

from odoo import Command, fields
from odoo.tests.common import tagged

from .common import CUSTOMER_ROOT, SudiApiCase

# Valid Gujarat GSTIN shape: 24 | ABCDE | 1234 | F | 1 | Z | 5
GSTIN = "24ABCDE1234F1Z5"
# Fifteen characters, but the 14th must be in [Zz1-9A-Ja-j]; Q is not.
BAD_GSTIN = "24ABCDE1234F1Q5"


@tagged("post_install", "-at_install")
class SudiCustomerCase(SudiApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.phone = "9812345670"

    def setUp(self):
        super().setUp()
        self.codes = self._capture_codes()

    def _capture_codes(self):
        """Keep the code instead of sending it.

        The WhatsApp connector is not connected in a test database, and a real
        send would fail the request with OTP_UNDELIVERABLE, which is correct
        behaviour and useless here.
        """
        Otp = type(self.env["sudi.auth.otp"])
        codes = {}
        original = Otp._sudi_deliver

        def fake(records, code):
            for otp in records:
                codes[otp.identifier] = code
            records.sudo().write({"delivery_state": "sent"})
            return True

        Otp._sudi_deliver = fake
        self.addCleanup(setattr, Otp, "_sudi_deliver", original)
        return codes

    def _cpost(self, path, payload, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.env.flush_all()
        return self.url_open(
            f"{CUSTOMER_ROOT}{path}", data=json.dumps(payload), headers=headers
        )

    def _device(self, uid="cust-aaaa0001"):
        return {"device_uid": uid, "platform": "android"}

    def _request_code(self, phone=None):
        phone = phone or self.phone
        response = self._cpost("/auth/otp/request", {"phone": phone})
        self.assertEqual(response.status_code, 200, response.text)
        return self.codes[
            self.env["stock.picking"].sudo()._sudi_normalize_phone(phone)
        ]

    def _verify(self, code, phone=None, uid="cust-aaaa0001"):
        return self._cpost("/auth/otp/verify", {
            "phone": phone or self.phone, "code": code, "device": self._device(uid),
        })


class TestSudiCustomerOtp(SudiCustomerCase):

    def test_a_code_is_sent_and_the_client_is_told_how_long_it_lasts(self):
        response = self._cpost("/auth/otp/request", {"phone": self.phone})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["sent"])
        self.assertEqual(body["channel"], "whatsapp")
        self.assertEqual(body["expires_in"], 300)
        self.assertEqual(body["resend_after"], 60)

    def test_an_unknown_number_gets_the_same_answer(self):
        # Which numbers have accounts is not something this endpoint tells you.
        known = self._cpost("/auth/otp/request", {"phone": self.phone}).json()
        unknown = self._cpost("/auth/otp/request", {"phone": "9800000001"}).json()
        self.assertEqual(known, unknown)

    def test_asking_twice_in_a_row_is_rate_limited_and_retryable(self):
        self._cpost("/auth/otp/request", {"phone": self.phone})

        response = self._cpost("/auth/otp/request", {"phone": self.phone})

        body = self.assertEnvelope(response, 429, "OTP_RATE_LIMITED")
        self.assertTrue(body["retryable"])

    def test_a_short_number_is_refused(self):
        response = self._cpost("/auth/otp/request", {"phone": "1234"})
        self.assertEnvelope(response, 422, "VALIDATION")

    def test_the_code_is_never_stored_in_the_clear(self):
        code = self._request_code()
        otp = self.env["sudi.auth.otp"].sudo().search(
            [("identifier", "=", self.phone)], limit=1
        )
        self.assertTrue(otp.code_hash)
        self.assertNotIn(code, otp.code_hash)
        self.assertNotEqual(otp.code_hash, code)

    def test_a_wrong_code_is_refused(self):
        self._request_code()
        response = self._verify("000000")
        self.assertEnvelope(response, 422, "OTP_INVALID")

    def test_five_wrong_codes_lock_the_code(self):
        self._request_code()
        for _attempt in range(4):
            self.assertEnvelope(self._verify("000000"), 422, "OTP_INVALID")

        body = self.assertEnvelope(self._verify("000000"), 429, "OTP_RATE_LIMITED")

        self.assertTrue(body["retryable"])
        self.assertTrue(
            self.env["sudi.auth.otp"].sudo().search(
                [("identifier", "=", self.phone)], limit=1
            ).locked
        )

    def test_an_expired_code_is_refused(self):
        code = self._request_code()
        self.env["sudi.auth.otp"].sudo().search(
            [("identifier", "=", self.phone)], limit=1
        ).write({
            "expires_at": fields.Datetime.subtract(fields.Datetime.now(), minutes=1)
        })
        self.assertEnvelope(self._verify(code), 422, "OTP_EXPIRED")

    def test_a_code_cannot_be_used_twice(self):
        code = self._request_code()
        first = self._verify(code)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEnvelope(self._verify(code), 422, "OTP_NOT_FOUND")

    def test_verifying_without_a_pending_code_is_refused(self):
        self.assertEnvelope(self._verify("123456"), 422, "OTP_NOT_FOUND")


class TestSudiCustomerRegistration(SudiCustomerCase):

    def test_a_new_number_is_asked_to_register(self):
        code = self._request_code()

        body = self._verify(code).json()

        self.assertTrue(body["registration_required"])
        self.assertTrue(body["registration_token"])
        self.assertIsNone(body["tokens"])

    def test_registering_creates_a_portal_account_and_returns_tokens(self):
        code = self._request_code()
        token = self._verify(code).json()["registration_token"]

        response = self._cpost("/auth/register", {
            "registration_token": token,
            "name": "Kiran Gems",
            "device": self._device(),
        })

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["access_token"])
        self.assertEqual(body["roles"], ["customer"])
        user = self.env["res.users"].sudo().search([("login", "=", self.phone)])
        self.assertEqual(len(user), 1)
        self.assertTrue(user.share)
        self.assertEqual(user.partner_id.name, "Kiran Gems")

    def test_a_second_verify_now_signs_the_customer_straight_in(self):
        code = self._request_code()
        token = self._verify(code).json()["registration_token"]
        self._cpost("/auth/register", {
            "registration_token": token, "name": "Kiran Gems",
            "device": self._device(),
        })

        # A minute later, a fresh code: no registration step this time.
        self.env["sudi.auth.otp"].sudo().search([]).unlink()
        code = self._request_code()
        body = self._verify(code, uid="cust-aaaa0002").json()

        self.assertFalse(body["registration_required"])
        self.assertTrue(body["tokens"]["access_token"])

    def test_registration_links_to_the_record_the_office_already_has(self):
        # A jangad upload from an unknown number leaves a guest partner; the
        # customer must land on it rather than beside it.
        guest = self.env["res.partner"].sudo().create({
            "name": f"Guest ({self.phone})", "phone": self.phone,
        })
        code = self._request_code()
        token = self._verify(code).json()["registration_token"]

        self._cpost("/auth/register", {
            "registration_token": token, "name": "Kiran Gems",
            "device": self._device(),
        })

        self.assertEqual(guest.name, "Kiran Gems")
        self.assertEqual(
            self.env["res.users"].sudo().search([("login", "=", self.phone)]).partner_id,
            guest,
        )

    def test_a_forged_registration_token_is_refused(self):
        response = self._cpost("/auth/register", {
            "registration_token": "not-a-token", "name": "Nobody",
            "device": self._device(),
        })
        self.assertEnvelope(response, 422, "VALIDATION")
        self.assertFalse(
            self.env["res.users"].sudo().search([("login", "=", self.phone)])
        )

    def test_an_access_token_cannot_be_used_to_register(self):
        # The registration token has its own typ precisely so a bearer token
        # cannot stand in for "this number answered a code".
        code = self._request_code()
        token = self._verify(code).json()["registration_token"]
        self._cpost("/auth/register", {
            "registration_token": token, "name": "Kiran Gems",
            "device": self._device(),
        })
        self.env["sudi.auth.otp"].sudo().search([]).unlink()
        code = self._request_code()
        access = self._verify(code, uid="cust-aaaa0003").json()["tokens"]["access_token"]

        response = self._cpost("/auth/register", {
            "registration_token": access, "name": "Someone Else",
            "device": self._device("cust-aaaa0004"),
        })

        self.assertEnvelope(response, 422, "VALIDATION")

    def test_a_staff_number_is_refused_rather_than_duplicated(self):
        self.operator.partner_id.sudo().write({"phone": "9899999999"})
        code = self._request_code("9899999999")

        response = self._verify(code, phone="9899999999")

        self.assertEnvelope(response, 403, "AUTH")


class TestSudiCustomerGst(SudiCustomerCase):

    def _customer_token(self):
        code = self._request_code()
        token = self._verify(code).json()["registration_token"]
        return self._cpost("/auth/register", {
            "registration_token": token, "name": "Kiran Gems",
            "device": self._device(),
        }).json()["access_token"]

    def test_a_new_customer_has_no_gst_yet(self):
        token = self._customer_token()
        self.env.flush_all()
        body = self.url_open(
            f"{CUSTOMER_ROOT}/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        self.assertEqual(body["gst_state"], "missing")
        self.assertTrue(body["is_customer"])

    def test_submitting_a_gstin_links_the_company(self):
        token = self._customer_token()

        response = self._cpost("/gst", {"vat": GSTIN}, token=token)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["gst_state"], "present")
        self.assertEqual(body["vat"], GSTIN)
        partner = self.env["res.users"].sudo().search(
            [("login", "=", self.phone)]
        ).partner_id
        self.assertTrue(partner.parent_id.is_company)
        self.assertEqual(partner.parent_id.vat, GSTIN)

    def test_two_customers_land_on_the_same_company(self):
        first = self._customer_token()
        self._cpost("/gst", {"vat": GSTIN}, token=first)
        companies = self.env["res.partner"].sudo().search_count([
            ("vat", "=ilike", GSTIN), ("is_company", "=", True)
        ])
        self.assertEqual(companies, 1)

        # The same GSTIN from a second customer must not make a second company.
        self.env["sudi.auth.otp"].sudo().search([]).unlink()
        self.phone = "9812345671"
        second = self._customer_token()
        self._cpost("/gst", {"vat": GSTIN}, token=second)

        self.assertEqual(
            self.env["res.partner"].sudo().search_count([
                ("vat", "=ilike", GSTIN), ("is_company", "=", True)
            ]),
            1,
        )

    def test_an_invalid_gstin_is_refused(self):
        token = self._customer_token()

        response = self._cpost("/gst", {"vat": BAD_GSTIN}, token=token)

        body = self.assertEnvelope(response, 422, "VALIDATION")
        self.assertIn(BAD_GSTIN, body["message"])
        partner = self.env["res.users"].sudo().search(
            [("login", "=", self.phone)]
        ).partner_id
        self.assertFalse(partner.vat)

    def test_skipping_is_allowed_and_is_not_final(self):
        token = self._customer_token()

        response = self._cpost("/gst/skip", {}, token=token)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["gst_state"], "skipped")
        # Still submittable later: a skip is a "not now", not a "never".
        self.assertEqual(
            self._cpost("/gst", {"vat": GSTIN}, token=token).json()["gst_state"],
            "present",
        )

    def test_registering_with_a_gstin_links_it_straight_away(self):
        code = self._request_code()
        token = self._verify(code).json()["registration_token"]

        self._cpost("/auth/register", {
            "registration_token": token, "name": "Kiran Gems",
            "vat": GSTIN, "device": self._device(),
        })

        partner = self.env["res.users"].sudo().search(
            [("login", "=", self.phone)]
        ).partner_id
        self.assertEqual(partner.parent_id.vat, GSTIN)

    def test_registering_with_an_invalid_gstin_creates_nothing(self):
        code = self._request_code()
        token = self._verify(code).json()["registration_token"]

        response = self._cpost("/auth/register", {
            "registration_token": token, "name": "Kiran Gems",
            "vat": BAD_GSTIN, "device": self._device(),
        })

        self.assertEnvelope(response, 422, "VALIDATION")
        self.assertFalse(
            self.env["res.users"].sudo().search([("login", "=", self.phone)])
        )

    def test_a_staff_token_cannot_reach_the_customer_routes(self):
        staff = self._login(device_uid="cust-staff0001")["access_token"]
        response = self._cpost("/gst", {"vat": GSTIN}, token=staff)
        self.assertEnvelope(response, 401, "AUTH")
