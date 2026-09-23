from odoo.exceptions import AccessError

from .common import SudiSyncCase


class TestSudiApiDevice(SudiSyncCase):

    def _sudi_register(self, user=None, device_uid="phone-1", **kwargs):
        return self.env["sudi.api.device"]._sudi_register(
            user or self.operator, device_uid, **kwargs
        )

    def test_registering_twice_updates_one_row(self):
        first = self._sudi_register(platform="android", app_version="1.0.0")
        second = self._sudi_register(platform="android", app_version="1.0.1")
        self.assertEqual(first, second)
        self.assertEqual(second.app_version, "1.0.1")
        self.assertEqual(
            self.env["sudi.api.device"].sudo().search_count([
                ("user_id", "=", self.operator.id)
            ]),
            1,
        )

    def test_two_users_may_share_a_device_uid(self):
        # Uniqueness is (user, device_uid): a shared handset is a real thing.
        mine = self._sudi_register()
        theirs = self._sudi_register(user=self.operator_2)
        self.assertNotEqual(mine, theirs)

    def test_revoking_raises_the_epoch_and_drops_the_push_token(self):
        device = self._sudi_register(push_token="fcm-abc")
        epoch = device.token_epoch

        device.action_revoke()

        self.assertTrue(device.revoked)
        self.assertEqual(device.token_epoch, epoch + 1)
        self.assertFalse(device.push_token)

    def test_a_revoked_device_cannot_resolve_a_token(self):
        device = self._sudi_register()
        epoch = device.token_epoch
        device.action_revoke()
        with self.assertRaises(AccessError):
            self.env["sudi.api.device"]._sudi_resolve(self.operator, device.device_uid, epoch)

    def test_an_old_epoch_cannot_resolve(self):
        device = self._sudi_register()
        stale_epoch = device.token_epoch
        device.sudo().write({"token_epoch": stale_epoch + 1})
        with self.assertRaises(AccessError):
            self.env["sudi.api.device"]._sudi_resolve(
                self.operator, device.device_uid, stale_epoch
            )

    def test_an_unknown_device_cannot_resolve(self):
        with self.assertRaises(AccessError):
            self.env["sudi.api.device"]._sudi_resolve(self.operator, "never-seen", 1)

    def test_re_registering_a_revoked_device_raises_its_epoch(self):
        # Revocation kills the refresh token sitting on the handset. Someone
        # who can supply the password again has the credentials, so the device
        # comes back -- but the old token stays dead.
        device = self._sudi_register()
        device.action_revoke()
        revoked_epoch = device.token_epoch

        again = self._sudi_register()

        self.assertEqual(again, device)
        self.assertFalse(again.revoked)
        self.assertEqual(again.token_epoch, revoked_epoch + 1)
        with self.assertRaises(AccessError):
            self.env["sudi.api.device"]._sudi_resolve(
                self.operator, device.device_uid, revoked_epoch
            )

    def test_touch_records_where_the_device_got_to(self):
        device = self._sudi_register()
        device._sudi_touch(cursor=4321)
        self.assertEqual(device.sync_cursor, 4321)
        self.assertTrue(device.last_seen_at)

    def test_invalidating_a_user_revokes_every_device(self):
        first = self._sudi_register(device_uid="phone-1")
        second = self._sudi_register(device_uid="phone-2")
        epoch = self.operator.sudo().sudi_api_token_epoch

        self.operator._sudi_invalidate_api_tokens()

        self.assertEqual(self.operator.sudo().sudi_api_token_epoch, epoch + 1)
        self.assertTrue(first.revoked)
        self.assertTrue(second.revoked)

    def test_a_password_change_invalidates_tokens(self):
        device = self._sudi_register()
        self.operator.sudo().write({"password": "a-new-secret-1234"})
        self.assertTrue(device.revoked)

    def test_deactivating_a_user_invalidates_tokens(self):
        device = self._sudi_register()
        self.operator.sudo().write({"active": False})
        self.assertTrue(device.revoked)
