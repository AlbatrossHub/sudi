from odoo import api, models

ROLE_GROUPS = [
    ("pickup_delivery", "diamond.group_sudi_pickup_delivery_operator"),
    ("job_work", "diamond.group_sudi_job_work_user"),
    ("office", "diamond.group_sudi_office_validator"),
]

# The roles that open the staff app. ``jangad_on_behalf`` is deliberately not
# one of them: it is an extra ability for someone who already works in the app.
FIELD_ROLES = {"pickup_delivery", "job_work", "office"}


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
        if not self.share and self.sudo().sudi_can_post_jangad_on_behalf:
            roles.append("jangad_on_behalf")
        if self.share:
            roles.append("customer")
        return roles

    @api.model
    def _sudi_api_has_field_role(self, user):
        return bool(set(user._sudi_api_roles()) & FIELD_ROLES)
