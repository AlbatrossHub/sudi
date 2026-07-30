def migrate(cr, version):
    """Grant Pickup App group to users with Notify on Jangad Upload enabled."""
    from odoo import Command, SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})
    pickup_group = env.ref("diamond.group_sudi_pickup_app", raise_if_not_found=False)
    if not pickup_group:
        return

    users = env["res.users"].sudo().search([
        ("sudi_notify_pickup_scheduled", "=", True),
        ("active", "=", True),
        ("share", "=", False),
    ])
    for user in users:
        if pickup_group not in user.group_ids:
            user.write({"group_ids": [Command.link(pickup_group.id)]})
