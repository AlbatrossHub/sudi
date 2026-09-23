from odoo import api, models

ROLE_GROUPS = [
    ("pickup_delivery", "diamond.group_sudi_pickup_delivery_operator"),
    ("job_work", "diamond.group_sudi_job_work_user"),
]


class ResUsers(models.Model):
    _inherit = "res.users"

    def _sudi_api_roles(self):
        """The app roles this user holds, right now.

        Put in the token so a client can draw its navigation before the first
        call, and re-read on every authenticated request so a role taken away
        this morning does not keep working until tonight. It is never the gate:
        the route still checks the group.
        """
        self.ensure_one()
        roles = [
            role for role, xmlid in ROLE_GROUPS
            if self.has_group(xmlid)
        ]
        if self.share:
            roles.append("customer")
        return roles

    @api.model
    def _sudi_api_has_field_role(self, user):
        return bool(set(user._sudi_api_roles()) & {"pickup_delivery", "job_work"})
