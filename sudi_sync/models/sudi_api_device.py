"""The devices that hold a copy of the work.

A device is first-class rather than implicit because three things need it:

* **revocation at the right granularity.** Bumping a *user's* token epoch signs
  out every phone they own, which is the wrong answer to "Ramesh lost his
  handset". The device carries its own epoch so one handset can be killed.
* **the push destination** for the data message that says "pull now".
* **support.** "This phone last synced at 11:20 and has not been seen since" is
  a question someone will ask on a bad day.
"""

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.translate import _


class SudiApiDevice(models.Model):
    _name = "sudi.api.device"
    _description = "Sudi Mobile Device"
    _order = "last_seen_at desc, id desc"
    _rec_name = "device_uid"

    device_uid = fields.Char(
        required=True,
        index=True,
        readonly=True,
        help="Generated once by the client on first launch and never again. "
             "Not a vendor id: those change on reinstall.",
    )
    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade", readonly=True
    )
    audience = fields.Selection(
        [("field", "Field app"), ("customer", "Customer app")],
        required=True,
        default="field",
        readonly=True,
    )
    platform = fields.Selection(
        [("android", "Android"), ("ios", "iOS"), ("web", "Web")]
    )
    app_version = fields.Char()
    push_token = fields.Char(help="FCM or APNs registration token.")
    token_epoch = fields.Integer(
        default=1,
        readonly=True,
        help="Every token issued to this device carries this number. Raising "
             "it invalidates them all without touching the user's other devices.",
    )
    last_seen_at = fields.Datetime(readonly=True)
    sync_cursor = fields.Integer(
        default=0,
        readonly=True,
        help="Where this device's last pull reached. Recorded for support; the "
             "client is the authority on its own cursor.",
    )
    revoked = fields.Boolean(
        default=False,
        readonly=True,
        help="Kept rather than deleted, so the audit trail survives the phone.",
    )

    _device_unique = models.Constraint(
        "UNIQUE (user_id, device_uid)",
        "This device is already registered for this user.",
    )

    @api.depends("device_uid", "user_id", "platform")
    def _compute_display_name(self):
        for device in self:
            label = device.platform or _("device")
            short = (device.device_uid or "")[:8]
            device.display_name = f"{device.user_id.name} - {label} ({short})"

    @api.model
    def _sudi_register(self, user, device_uid, audience="field", platform=None,
                  app_version=None, push_token=None):
        """Find or create the device behind a successful authentication.

        A previously revoked device is brought back and its epoch raised. That
        is deliberate: revocation exists to kill the *tokens* held by a handset
        someone no longer controls, and the refresh token is what sits on that
        handset -- not the password. Anyone who can authenticate afresh has the
        credentials, and raising the epoch makes sure the stolen token stays
        dead even so.
        """
        if not device_uid:
            raise ValueError("A device_uid is required to register a device")
        device = self.sudo().search([
            ("user_id", "=", user.id),
            ("device_uid", "=", device_uid),
        ], limit=1)
        vals = {
            "platform": platform or False,
            "app_version": app_version or False,
            "last_seen_at": fields.Datetime.now(),
        }
        if push_token:
            vals["push_token"] = push_token
        if device:
            if device.revoked:
                vals["revoked"] = False
                vals["token_epoch"] = device.token_epoch + 1
            device.sudo().write(vals)
            return device
        vals.update({
            "user_id": user.id,
            "device_uid": device_uid,
            "audience": audience,
        })
        return self.sudo().create(vals)

    def _sudi_touch(self, cursor=None):
        """Record that the device called in, and how far it got."""
        vals = {"last_seen_at": fields.Datetime.now()}
        if cursor is not None:
            vals["sync_cursor"] = int(cursor)
        self.sudo().write(vals)

    def action_revoke(self):
        """Kill every token on this handset. The user's others keep working."""
        for device in self:
            device.sudo().write({
                "revoked": True,
                "token_epoch": device.token_epoch + 1,
                "push_token": False,
            })
        return True

    def action_unrevoke(self):
        self.sudo().write({"revoked": False})
        return True

    @api.model
    def _sudi_resolve(self, user, device_uid, token_epoch):
        """The device a bearer token claims to come from, or raise.

        Called on every authenticated request, so it is the one place that can
        answer ``DEVICE_REVOKED``.
        """
        device = self.sudo().search([
            ("user_id", "=", user.id),
            ("device_uid", "=", device_uid),
        ], limit=1)
        if not device or device.revoked:
            raise AccessError(_("This device is no longer allowed to sign in."))
        if device.token_epoch != token_epoch:
            raise AccessError(_("This device's access has been reset."))
        return device
