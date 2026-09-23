from odoo.tests.common import tagged

from .common import SudiApiCase


@tagged("post_install", "-at_install")
class TestSudiApiDevices(SudiApiCase):

    def test_a_caller_sees_their_own_handsets_and_which_one_is_current(self):
        first = self._login(device_uid="dev-11111111", platform="android")
        self._login(device_uid="dev-22222222", platform="ios")

        response = self._get("/devices", token=first["access_token"])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            {device["device_uid"] for device in body},
            {"dev-11111111", "dev-22222222"},
        )
        current = [device for device in body if device["is_current"]]
        self.assertEqual([device["device_uid"] for device in current], ["dev-11111111"])

    def test_a_caller_does_not_see_anyone_elses_handsets(self):
        self._login(user=self.job_worker, device_uid="dev-33333333")
        mine = self._login(device_uid="dev-44444444")

        body = self._get("/devices", token=mine["access_token"]).json()

        self.assertNotIn("dev-33333333", {device["device_uid"] for device in body})

    def test_a_client_can_update_its_push_token(self):
        token = self._login(device_uid="dev-55555555")["access_token"]
        response = self._post(
            "/devices/me",
            {"push_token": "fcm-new", "app_version": "1.2.3"},
            token=token,
            method="PATCH",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["app_version"], "1.2.3")
        device = self.env["sudi.api.device"].sudo().search([
            ("device_uid", "=", "dev-55555555")
        ])
        self.assertEqual(device.push_token, "fcm-new")

    def test_an_empty_push_token_clears_it(self):
        # What an app sends when the user turns notifications off.
        token = self._login(device_uid="dev-66666666", push_token="fcm-old")["access_token"]
        self._post("/devices/me", {"push_token": ""}, token=token, method="PATCH")
        device = self.env["sudi.api.device"].sudo().search([
            ("device_uid", "=", "dev-66666666")
        ])
        self.assertFalse(device.push_token)

    def test_a_caller_can_revoke_another_of_their_own_handsets(self):
        keep = self._login(device_uid="dev-77777777")
        self._login(device_uid="dev-88888888")

        response = self._post(
            "/devices/dev-88888888/revoke", None, token=keep["access_token"]
        )
        self.assertEqual(response.status_code, 204, response.text)

        self.assertTrue(
            self.env["sudi.api.device"].sudo().search([
                ("device_uid", "=", "dev-88888888")
            ]).revoked
        )
        # The handset that issued the revocation still works.
        self.assertEqual(
            self._get("/auth/me", token=keep["access_token"]).status_code, 200
        )

    def test_revoking_someone_elses_handset_looks_exactly_like_a_typo(self):
        # Otherwise the route becomes a way to enumerate other people's phones.
        self._login(user=self.job_worker, device_uid="dev-99999999")
        mine = self._login(device_uid="dev-aaaa0000")

        theirs = self._post(
            "/devices/dev-99999999/revoke", None, token=mine["access_token"]
        )
        typo = self._post(
            "/devices/dev-nosuchdevice/revoke", None, token=mine["access_token"]
        )

        self.assertEnvelope(theirs, 404, "NOT_IN_SCOPE")
        self.assertEnvelope(typo, 404, "NOT_IN_SCOPE")
        self.assertEqual(theirs.json()["message"], typo.json()["message"])
        self.assertFalse(
            self.env["sudi.api.device"].sudo().search([
                ("device_uid", "=", "dev-99999999")
            ]).revoked
        )

    def test_the_device_list_needs_a_token(self):
        self.assertEnvelope(self._get("/devices"), 401, "AUTH")
