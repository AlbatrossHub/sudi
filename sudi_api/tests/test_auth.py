from odoo.tests.common import tagged

from ..jwt_tokens import (
    AUDIENCE_CUSTOMER,
    TOKEN_TYPE_ACCESS,
    access_token_ttl,
    encode_token,
)
from .common import CUSTOMER_ROOT, FIELD_ROOT, SudiApiCase


@tagged("post_install", "-at_install")
class TestSudiApiHealth(SudiApiCase):

    def test_the_field_endpoint_answers_without_a_token(self):
        response = self._get("/health")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["api"], "sudi-field")

    def test_the_customer_endpoint_is_mounted_separately(self):
        response = self._get("/health", root=CUSTOMER_ROOT)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["api"], "sudi-customer")

    def test_each_endpoint_publishes_its_own_contract(self):
        field = self._get("/openapi.json")
        customer = self._get("/openapi.json", root=CUSTOMER_ROOT)
        self.assertEqual(field.status_code, 200)
        self.assertEqual(customer.status_code, 200)
        field_paths = set(field.json()["paths"])
        customer_paths = set(customer.json()["paths"])
        self.assertIn("/auth/login", field_paths)
        self.assertIn("/sync/pull", field_paths)
        # A password login must never appear on the customer contract: a
        # customer account is created with a discarded random password so that
        # OTP is its only way in.
        self.assertNotIn("/auth/login", customer_paths)
        self.assertNotIn("/sync/pull", customer_paths)
        # ...and the customer's own flows must not appear on the staff one.
        self.assertIn("/auth/otp/request", customer_paths)
        self.assertIn("/gst", customer_paths)
        self.assertNotIn("/auth/otp/request", field_paths)
        self.assertNotIn("/gst", field_paths)
        # Devices are deliberately on both: a customer handset also carries a
        # push token and can be signed out.
        self.assertIn("/devices", field_paths & customer_paths)


@tagged("post_install", "-at_install")
class TestSudiApiLogin(SudiApiCase):

    def test_a_valid_login_returns_a_pair_and_registers_the_device(self):
        body = self._login(device_uid="phone-11111111", platform="android",
                           app_version="1.0.0", push_token="fcm-1")
        self.assertTrue(body["access_token"])
        self.assertTrue(body["refresh_token"])
        self.assertEqual(body["device_uid"], "phone-11111111")
        self.assertEqual(body["roles"], ["pickup_delivery"])
        self.assertEqual(body["expires_in"], int(access_token_ttl(self.env).total_seconds()))

        device = self.env["sudi.api.device"].sudo().search([
            ("user_id", "=", self.operator.id),
            ("device_uid", "=", "phone-11111111"),
        ])
        self.assertEqual(len(device), 1)
        self.assertEqual(device.platform, "android")
        self.assertEqual(device.push_token, "fcm-1")

    def test_a_wrong_password_is_vague_and_flat(self):
        response = self._post("/auth/login", {
            "login": self.operator.login,
            "password": "not-the-password",
            "device": {"device_uid": "phone-22222222"},
        })
        body = self.assertEnvelope(response, 401, "AUTH")
        self.assertEqual(body["message"], "Invalid credentials")
        self.assertFalse(body["retryable"])

    def test_an_unknown_login_is_indistinguishable_from_a_wrong_password(self):
        response = self._post("/auth/login", {
            "login": "nobody-at-all",
            "password": "whatever",
            "device": {"device_uid": "phone-33333333"},
        })
        body = self.assertEnvelope(response, 401, "AUTH")
        self.assertEqual(body["message"], "Invalid credentials")

    def test_an_internal_account_with_no_field_role_is_refused(self):
        response = self._post("/auth/login", {
            "login": self.office.login,
            "password": self.password,
            "device": {"device_uid": "phone-44444444"},
        })
        self.assertEnvelope(response, 403, "AUTH")

    def test_a_job_work_account_may_log_in(self):
        body = self._login(user=self.job_worker, device_uid="phone-55555555")
        self.assertEqual(body["roles"], ["job_work"])

    def test_a_malformed_body_is_a_validation_envelope(self):
        response = self._post("/auth/login", {"login": "x"})
        body = self.assertEnvelope(response, 422, "VALIDATION")
        self.assertIn("errors", body["detail"])

    def test_a_device_uid_is_required(self):
        response = self._post("/auth/login", {
            "login": self.operator.login,
            "password": self.password,
            "device": {"device_uid": "short"},
        })
        self.assertEnvelope(response, 422, "VALIDATION")


@tagged("post_install", "-at_install")
class TestSudiApiMe(SudiApiCase):

    def test_me_returns_the_caller_and_their_roles(self):
        token = self._login()["access_token"]
        response = self._get("/auth/me", token=token)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["id"], self.operator.id)
        self.assertEqual(body["login"], self.operator.login)
        self.assertEqual(body["roles"], ["pickup_delivery"])
        self.assertFalse(body["is_customer"])

    def test_no_token_is_an_auth_envelope_with_the_challenge_header(self):
        response = self._get("/auth/me")
        self.assertEnvelope(response, 401, "AUTH")
        self.assertIn("WWW-Authenticate", response.headers)

    def test_a_garbage_token_is_refused(self):
        response = self._get("/auth/me", token="not-a-jwt")
        self.assertEnvelope(response, 401, "AUTH")

    def test_a_customer_token_cannot_be_used_on_the_field_api(self):
        # The audience claim fails at the signature check rather than three
        # layers deeper on a group test.
        device = self.env["sudi.api.device"]._sudi_register(
            self.operator, "phone-66666666", audience=AUDIENCE_CUSTOMER
        )
        token = encode_token(
            self.env, self.operator, device, AUDIENCE_CUSTOMER,
            TOKEN_TYPE_ACCESS, access_token_ttl(self.env),
        )
        response = self._get("/auth/me", token=token)
        self.assertEnvelope(response, 401, "AUTH")

    def test_a_role_removed_now_applies_now(self):
        # Roles are re-read on every request, not trusted from the token: a
        # role taken away this morning must not keep working until tonight.
        # Two roles to start with, because dropping a user's *only* group also
        # drops base.group_user and turns them into a share user, which is a
        # different thing from losing an app role.
        self.operator.sudo().write({"group_ids": [
            (4, self.env.ref("diamond.group_sudi_job_work_user").id)
        ]})
        token = self._login()["access_token"]
        self.assertEqual(
            self._get("/auth/me", token=token).json()["roles"],
            ["pickup_delivery", "job_work"],
        )

        self.operator.sudo().write({"group_ids": [
            (3, self.env.ref("diamond.group_sudi_pickup_delivery_operator").id)
        ]})

        response = self._get("/auth/me", token=token)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["roles"], ["job_work"])


@tagged("post_install", "-at_install")
class TestSudiApiRefreshAndLogout(SudiApiCase):

    def test_a_refresh_token_buys_a_new_pair(self):
        body = self._login()
        response = self._post("/auth/refresh", {"refresh_token": body["refresh_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["access_token"])

    def test_an_access_token_cannot_be_replayed_as_a_refresh_token(self):
        body = self._login()
        response = self._post("/auth/refresh", {"refresh_token": body["access_token"]})
        self.assertEnvelope(response, 401, "AUTH")

    def test_logout_signs_out_only_this_device(self):
        first = self._login(device_uid="phone-77777777")
        second = self._login(device_uid="phone-88888888")

        response = self._post("/auth/logout", None, token=first["access_token"])
        self.assertEqual(response.status_code, 204, response.text)

        # The handset that logged out is done...
        self.assertEnvelope(
            self._get("/auth/me", token=first["access_token"]), 401, "DEVICE_REVOKED"
        )
        # ...and the operator's other phone is untouched.
        self.assertEqual(
            self._get("/auth/me", token=second["access_token"]).status_code, 200
        )

    def test_a_revoked_device_gets_the_remote_wipe_signal(self):
        body = self._login(device_uid="phone-99999999")
        device = self.env["sudi.api.device"].sudo().search([
            ("device_uid", "=", "phone-99999999")
        ])
        device.action_revoke()
        self.assertEnvelope(
            self._get("/auth/me", token=body["access_token"]), 401, "DEVICE_REVOKED"
        )

    def test_a_revoked_device_cannot_refresh_either(self):
        # The refresh token is what actually sits on a lost handset, so this is
        # the request revocation exists to stop.
        body = self._login(device_uid="phone-aaaa0000")
        self.env["sudi.api.device"].sudo().search([
            ("device_uid", "=", "phone-aaaa0000")
        ]).action_revoke()
        response = self._post("/auth/refresh", {"refresh_token": body["refresh_token"]})
        self.assertEnvelope(response, 401, "DEVICE_REVOKED")

    def test_invalidating_the_user_kills_every_token(self):
        first = self._login(device_uid="phone-bbbb0000")
        second = self._login(device_uid="phone-cccc0000")
        self.operator._sudi_invalidate_api_tokens()
        for body in (first, second):
            self.assertEnvelope(
                self._get("/auth/me", token=body["access_token"]), 401, "AUTH"
            )
