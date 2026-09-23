from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    sudi_api_token_epoch = fields.Integer(
        default=1,
        copy=False,
        groups="base.group_system",
        help="Raised to invalidate every API token this user holds, on every "
             "device at once. The per-device epoch on sudi.api.device is the "
             "normal tool; this one is the break-glass.",
    )
    sudi_api_device_ids = fields.One2many(
        "sudi.api.device", "user_id", string="Mobile Devices"
    )

    def _sudi_invalidate_api_tokens(self):
        """Sign this user out of every device."""
        for user in self.sudo():
            user.sudi_api_token_epoch = (user.sudi_api_token_epoch or 1) + 1
        self.sudo().sudi_api_device_ids.action_revoke()
        return True

    def write(self, vals):
        result = super().write(vals)
        # A password change or a deactivation must not leave live tokens
        # behind: that is the whole point of having an epoch.
        if "password" in vals or vals.get("active") is False:
            self._sudi_invalidate_api_tokens()
        return result
