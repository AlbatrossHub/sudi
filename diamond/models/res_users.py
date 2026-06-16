from datetime import timedelta

import pytz

from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

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
