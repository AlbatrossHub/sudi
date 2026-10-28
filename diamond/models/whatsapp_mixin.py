import logging

from odoo import _, models

_logger = logging.getLogger(__name__)


class SudiDiamondWhatsappMixin(models.AbstractModel):
    """Send event-driven WhatsApp messages through open_whatsapp_connector.

    Bodies come from the user-editable sudi.whatsapp.template records; the
    inheriting model supplies the placeholders via _sudi_get_whatsapp_template_context.
    """

    _name = "sudi.diamond.whatsapp.mixin"
    _description = "Diamond WhatsApp Notifications"

    # event -> keys behind the numbered {{1}}, {{2}} placeholders of its template
    _SUDI_WHATSAPP_POSITIONAL = {
        "pickup_scheduled": ["customer_name", "address", "phone", "url"],
        "pickup_request_confirmation": ["customer_name", "pickup_reference"],
        "pickup_confirmed": ["customer_name", "pickup_reference"],
        "pickup_admin_intimation": ["customer_name", "pickup_reference", "pickup_person", "url"],
        "pickup_cancelled": ["customer_name", "pickup_reference"],
        "delivery_assigned": ["customer_name", "delivery_reference"],
        "delivery_dispatch": ["customer_name", "address", "phone", "delivery_reference", "url"],
        "delivery_completed": ["customer_name", "delivery_reference"],
        "delivery_admin_intimation": ["customer_name", "delivery_reference", "delivery_person", "url"],
        "invoice_posted": ["customer_name", "invoice_number", "amount", "due_date", "url"],
    }

    def _sudi_get_whatsapp_template_context(self, event):
        self.ensure_one()
        return {}

    def _sudi_whatsapp_account(self):
        """The business number every Sudi message goes out from: the WhatsApp account
        marked "Default Account", else the first account set up. Never "whichever number
        happens to be connected" - a test or personal number linked for a while must not
        start messaging customers and staff from that number."""
        Account = self.env["owa.account"].sudo()
        default_id = self.env["ir.config_parameter"].sudo().get_int(
            "open_whatsapp_connector.default_account_id"
        )
        account = Account.browse(default_id).exists().filtered("active") if default_id else Account
        return account or Account.search([], order="id", limit=1)

    def _sudi_send_whatsapp_message(self, recipient_phone, body_text, attachment=None, partner=None,
                                    send_now=False):
        """Queue a WhatsApp message from the business number; returns the owa.message.

        Notifications are queued and the send queue is woken at once, so a jangad, pickup
        or delivery action never waits 2-5 s for WhatsApp (and the app never hangs on it).
        ``send_now`` sends inside the request instead: for the login code, which the
        customer is waiting for and whose failure must be reported straight away.
        """
        if not recipient_phone:
            _logger.warning("No recipient phone number provided for WhatsApp notification on %s", self.display_name)
            return False

        wa_account = self._sudi_whatsapp_account()
        if not wa_account:
            _logger.warning("No WhatsApp account found in open_whatsapp_connector for %s", self.display_name)
            return False

        mail_vals = {
            'model': self._name,
            'res_id': self.id,
            'body': body_text,
            'message_type': 'whatsapp_message',
        }
        if attachment:
            mail_vals['attachment_ids'] = [(6, 0, attachment.ids)]

        mail_message = self.env['mail.message'].sudo().create(mail_vals)

        msg_vals = {
            'mobile_number': recipient_phone,
            'message_type': 'outbound',
            'state': 'outgoing',
            'wa_account_id': wa_account.id,
            'mail_message_id': mail_message.id,
        }
        if partner:
            msg_vals['whatsapp_partner_id'] = partner.id

        owa_msg = self.env['owa.message'].sudo().create(msg_vals)
        if send_now:
            try:
                owa_msg._send_message()
            except Exception:
                _logger.exception("Failed to send WhatsApp message for %s to %s", self.display_name, recipient_phone)
        else:
            cron = self.env.ref("open_whatsapp_connector.ir_cron_send_owa_queue", raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
        return owa_msg

    def _sudi_render_event_whatsapp_message(self, event):
        """Render body text from configured sudi.whatsapp.template mapping for event."""
        self.ensure_one()
        mapping = self.env["sudi.whatsapp.template"].sudo().search(
            [
                ("event", "=", event),
                ("active", "=", True),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.company_id.id or self.env.company.id),
            ],
            order="company_id desc, sequence asc, id asc",
            limit=1,
        )
        if not mapping or not mapping.body:
            _logger.warning(
                "No active sudi.whatsapp.template found for event '%s' on %s",
                event,
                self.display_name,
            )
            return False

        body = mapping.body
        ctx = self._sudi_get_whatsapp_template_context(event)

        # 1. Unescape XML-escaped % strings if present (%%(key)s -> %(key)s)
        if "%%(" in body:
            body = body.replace("%%(", "%(")

        # 2. Map positional index variables {{1}}, {{2}}, etc. to event context keys
        keys = self._SUDI_WHATSAPP_POSITIONAL.get(event, [])
        for idx, key in enumerate(keys, 1):
            val = str(ctx.get(key, "") or "")
            body = body.replace(f"{{{{{idx}}}}}", val)
            body = body.replace(f"{{{idx}}}", val)

        # 3. Map named placeholder syntaxes: %(key)s, {{key}}, {key}
        for key, val in ctx.items():
            val_str = str(val or "")
            body = body.replace(f"{{{{{key}}}}}", val_str)
            body = body.replace(f"{{{key}}}", val_str)
            body = body.replace(f"%({key})s", val_str)
            body = body.replace(f"%({key})d", val_str)

        # 4. Safe Python dict interpolation fallback
        try:
            if "%(" in body:
                body = body % ctx
        except Exception:
            pass

        return body
