def migrate(cr, version):
    """Post-migration script to:
    1. Archive all existing OdooBot discuss channels and set user odoobot_state to 'disabled'.
    2. Clean up existing failed SMS notifications for stock pickings so 'SMS Failure:' alerts are removed.
    """
    from odoo import SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})

    # 1. Disable OdooBot state on all res.users if field exists
    if "odoobot_state" in env["res.users"]._fields:
        users = env["res.users"].sudo().search([("odoobot_state", "!=", "disabled")])
        if users:
            users.write({"odoobot_state": "disabled"})

    # 2. Archive all discuss channels with OdooBot partner
    odoobot_partner = env.ref("base.partner_root", raise_if_not_found=False)
    if odoobot_partner:
        odoobot_channels = env["discuss.channel"].sudo().search([
            ("channel_partner_ids", "in", [odoobot_partner.id]),
            ("active", "=", True),
        ])
        if odoobot_channels:
            odoobot_channels.write({"active": False})

    # 3. Clean up existing failed SMS notifications for stock pickings
    sms_notifications = env["mail.notification"].sudo().search([
        ("notification_type", "=", "sms"),
        ("notification_status", "in", ["exception", "bounce"]),
        ("mail_message_id.model", "=", "stock.picking"),
    ])
    if sms_notifications:
        sms_notifications.write({"notification_status": "canceled", "is_read": True})

    failed_sms = env["sms.sms"].sudo().search([
        ("model", "=", "stock.picking"),
        ("state", "=", "error"),
    ])
    if failed_sms:
        failed_sms.write({"state": "canceled"})
