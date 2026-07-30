def migrate(cr, version):
    """Carry over hard-coded notify user IDs from ir.config_parameter onto res.users flags."""
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env["ir.config_parameter"].sudo()
    Users = env["res.users"].sudo()

    mapping = {
        "diamond.sudi_pickup_notify_user_id": "sudi_notify_pickup_scheduled",
        "diamond.sudi_pickup_confirmed_notify_user_id": "sudi_notify_pickup_confirmed",
    }
    for param_key, field_name in mapping.items():
        raw = ICP.get_param(param_key)
        if not raw:
            continue
        try:
            user_id = int(raw)
        except (TypeError, ValueError):
            continue
        user = Users.browse(user_id).exists()
        if user:
            user.write({field_name: True})
        ICP.search([("key", "=", param_key)]).unlink()
