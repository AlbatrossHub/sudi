from datetime import timedelta

import pytz

from odoo import Command, api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    sudi_notify_pickup_scheduled = fields.Boolean(
        string="Notify on Jangad Upload",
        help="When enabled, this user is notified (Discuss + WhatsApp) when a "
             "Jangad attachment is uploaded and a diamond inventory receipt is created. "
             "It also makes the Pickup app visible for this user.",
    )
    sudi_notify_pickup_confirmed = fields.Boolean(
        string="Notify on Pickup Confirmed",
        help="When enabled, this user is notified (Discuss + WhatsApp) when a "
             "diamond pickup is confirmed by the operator.",
    )
    sudi_notify_due_data_entry = fields.Boolean(
        string="Notify on Due Data Entry",
    )

    def _on_webclient_bootstrap(self):
        super()._on_webclient_bootstrap()
        if "odoobot_state" in self._fields and self.odoobot_state != "disabled":
            self.sudo().odoobot_state = "disabled"
        odoobot_partner = self.env.ref("base.partner_root", raise_if_not_found=False)
        if odoobot_partner and self.partner_id:
            odoobot_channels = self.env["discuss.channel"].sudo().search([
                ("channel_partner_ids", "in", [odoobot_partner.id]),
                ("channel_partner_ids", "in", [self.partner_id.id]),
                ("active", "=", True),
            ])
            if odoobot_channels:
                odoobot_channels.write({"active": False})

    def _init_odoobot(self):
        if "odoobot_state" in self._fields:
            self.sudo().odoobot_state = "disabled"
        return False

    @api.model
    def _sudi_get_notification_users(self, field_name):
        """Return active internal users flagged for a diamond notification type."""
        return self.sudo().search([
            (field_name, "=", True),
            ("active", "=", True),
            ("share", "=", False),
        ])

    def _sudi_get_pickup_app_group(self):
        return self.env.ref("diamond.group_sudi_pickup_app", raise_if_not_found=False)

    def _sudi_sync_pickup_app_access_from_notification_flag(self):
        """Keep Pickup app group in sync with Notify on Jangad Upload."""
        pickup_group = self._sudi_get_pickup_app_group()
        if not pickup_group:
            return

        for user in self.sudo():
            has_group = pickup_group in user.group_ids
            should_have = bool(user.sudi_notify_pickup_scheduled)
            if should_have and not has_group:
                user.with_context(sudi_skip_pickup_group_sync=True).write({
                    "group_ids": [Command.link(pickup_group.id)],
                })
            elif not should_have and has_group:
                # Do not remove if user is an Onfield operator (group implies Pickup App).
                if user.has_group("diamond.group_sudi_pickup_delivery_operator"):
                    continue
                user.with_context(sudi_skip_pickup_group_sync=True).write({
                    "group_ids": [Command.unlink(pickup_group.id)],
                })

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users._sudi_sync_pickup_app_access_from_notification_flag()
        return users

    def write(self, vals):
        res = super().write(vals)
        if (
            "sudi_notify_pickup_scheduled" in vals
            and not self.env.context.get("sudi_skip_pickup_group_sync")
        ):
            self._sudi_sync_pickup_app_access_from_notification_flag()
        return res

    def _sudi_operator_today_bounds_utc(self):
        self.ensure_one()
        start_today = fields.Datetime.context_timestamp(
            self,
            fields.Datetime.now(),
        ).replace(hour=0, minute=0, second=0, microsecond=0)
        start_today = start_today.astimezone(pytz.UTC).replace(tzinfo=None)
        start_tomorrow = start_today + timedelta(days=1)
        return (
            fields.Datetime.to_string(start_today),
            fields.Datetime.to_string(start_tomorrow),
        )

    def _sudi_operator_today_start_utc(self):
        return self._sudi_operator_today_bounds_utc()[0]

    def _sudi_operator_tomorrow_start_utc(self):
        return self._sudi_operator_today_bounds_utc()[1]
