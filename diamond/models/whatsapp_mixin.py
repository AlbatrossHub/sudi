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

    def _sudi_send_whatsapp_message(self, recipient_phone, body_text, attachment=None, partner=None):
        """Send a WhatsApp message via open_whatsapp_connector engine."""
        if not recipient_phone:
            _logger.warning("No recipient phone number provided for WhatsApp notification on %s", self.display_name)
            return False
        
        wa_account = (
            self.env['owa.account'].sudo().search([('session_state', '=', 'connected')], limit=1)
            or self.env['owa.account'].sudo().search([], limit=1)
        )
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
        try:
            owa_msg._send_message()
        except Exception:
            _logger.exception("Failed to send WhatsApp message for %s to %s", self.display_name, recipient_phone)
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
