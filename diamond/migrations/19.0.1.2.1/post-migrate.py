def migrate(cr, version):
    """Grant operator group to users opted into pickup-scheduled notifications."""
    from odoo import Command, SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})
    operator_group = env.ref("diamond.group_sudi_pickup_delivery_operator", raise_if_not_found=False)
    if not operator_group:
        return

    users = env["res.users"].sudo().search([
        ("sudi_notify_pickup_scheduled", "=", True),
        ("active", "=", True),
        ("share", "=", False),
    ])
    for user in users:
        if operator_group not in user.groups_id:
            user.write({"group_ids": [Command.link(operator_group.id)]})
