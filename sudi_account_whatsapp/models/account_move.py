# -*- coding: utf-8 -*-
from odoo import models, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_send_by_whatsapp(self):
        """Trigger WhatsApp composer wizard for account.move with pre-filled dynamic message body."""
        self.ensure_one()
        partner = self.partner_id
        phone = getattr(partner, 'phone', False) or getattr(partner, 'mobile', False)
        if not partner or not phone:
            raise UserError(
                _("Customer '%s' does not have a valid phone number. "
                  "Please add a phone number to the customer profile before sending via WhatsApp.")
                % (partner.name if partner else _("Unknown"))
            )

        amount = f"{self.amount_total:,.2f}"
        currency = self.currency_id.symbol or self.currency_id.name or ''
        due_date = self.invoice_date_due.strftime('%Y-%m-%d') if self.invoice_date_due else 'N/A'

        body_text = _(
            "Hello %(partner_name)s,\n\n"
            "Please find your invoice details below:\n"
            "• Invoice Number: %(invoice_name)s\n"
            "• Total Amount: %(currency)s %(amount)s\n"
            "• Due Date: %(due_date)s\n"
            "• Contact Phone: %(phone)s\n\n"
            "The invoice PDF is attached to this message.\n\n"
            "If you have any questions, please let us know.\n\n"
            "Thank you!"
        ) % {
            'partner_name': partner.name or '',
            'invoice_name': self.name or '',
            'currency': currency,
            'amount': amount,
            'due_date': due_date,
            'phone': phone,
        }

        action = self.env['owa.composer']._action_open_composer(
            self,
            report_xmlid='account.account_invoices'
        )
        if isinstance(action, dict) and 'context' in action:
            action['context']['default_body'] = body_text
        return action
