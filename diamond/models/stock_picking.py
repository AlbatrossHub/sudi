import base64
import logging
import re
from datetime import datetime, timedelta, timezone

from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_is_zero

_logger = logging.getLogger(__name__)

# Field events are captured on a phone that may be offline and flushed hours
# later, so the caller supplies when the event happened rather than letting the
# server stamp its own clock. These bound how far the supplied time may sit from
# the server's: see docs/MOBILE_API_PLAN.md section 7.3.
SUDI_MAX_CLOCK_SKEW_SECONDS = 60
SUDI_MAX_BACKDATE_HOURS = 72

# Which parts of the proof of delivery are mandatory is configuration, not code:
# the app captures all three and the business tightens the requirement without
# needing an app release.
SUDI_POD_PARAMS = {
    "receiver_name": "sudi_diamond.pod_require_receiver_name",
    "signature": "sudi_diamond.pod_require_signature",
    "photo": "sudi_diamond.pod_require_photo",
}


class StockPicking(models.Model):
    _name = "stock.picking"
    _inherit = ["stock.picking", "timer.parent.mixin", "sudi.diamond.whatsapp.mixin"]

    active = fields.Boolean(
        string="Active",
        default=True,
    )
    state = fields.Selection(selection_add=[
        ("sudi_pickup_pending", "Pick up pending"),
        ("draft",),
        ("assigned", "Job Work in Progress"),
    ])
    sudi_is_diamond_job_work = fields.Boolean(
        string="Diamond Job Work",
        copy=True,
        tracking=True,
        default=True,
    )
    sudi_origin_receipt_id = fields.Many2one(
        "stock.picking",
        string="Origin Receipt",
        copy=True,
        index=True,
    )
    sudi_delivery_ids = fields.One2many(
        "stock.picking",
        "sudi_origin_receipt_id",
        string="Diamond Deliveries",
    )
    sudi_origin_receipt_name = fields.Char(
        string="Origin Receipt",
        compute="_compute_sudi_origin_receipt_name",
        compute_sudo=True,
    )
    sudi_delivery_count = fields.Integer(compute="_compute_sudi_counts")
    # Stored inverse of account.move.sudi_delivery_ids: the invoices that
    # settle this delivery. sudi_invoice_ids adds the receipt-side view.
    sudi_billed_invoice_ids = fields.Many2many(
        "account.move",
        "sudi_account_move_stock_picking_rel",
        "picking_id",
        "move_id",
        string="Settling Invoices",
        copy=False,
        readonly=True,
    )
    sudi_reference_statement_ids = fields.Many2many(
        "sudi.diamond.reference.statement",
        "sudi_reference_statement_stock_picking_rel",
        "picking_id",
        "statement_id",
        string="Reference Statements",
        copy=False,
        readonly=True,
    )
    sudi_invoice_ids = fields.Many2many(
        "account.move",
        compute="_compute_sudi_invoice_ids",
        string="Diamond Invoices",
    )
    sudi_invoice_count = fields.Integer(compute="_compute_sudi_invoice_ids")
    sudi_billing_log_count = fields.Integer(compute="_compute_sudi_billing_log_count")
    sudi_billing_status = fields.Selection(
        [
            ("none", "Not Billable"),
            ("to_bill", "To Bill"),
            ("partial", "Partially Billed"),
            ("no_charge", "No Charge"),
            ("billed", "Billed"),
            ("closed", "Closed"),
        ],
        string="Billing Status",
        compute="_compute_sudi_billing_status",
        store=True,
        index=True,
        copy=False,
    )
    sudi_settlement_mode = fields.Selection(
        [("invoice", "Invoice"), ("reference", "Reference")],
        string="Settle As",
        default="invoice",
        required=True,
        copy=False,
        help="Chosen by the billing reviewer per delivery: an official invoice, or an "
             "off-book reference statement.",
    )
    sudi_total_pcs = fields.Float(
        string="Total Pcs",
        digits="Product Unit",
        compute="_compute_sudi_billing_totals",
        store=True,
    )
    sudi_total_carats = fields.Float(
        string="Total Carats",
        digits="Product Unit",
        compute="_compute_sudi_billing_totals",
        store=True,
    )
    sudi_billable_amount = fields.Monetary(
        string="Amount To Bill",
        currency_field="sudi_currency_id",
        compute="_compute_sudi_billing_totals",
        store=True,
    )
    sudi_settled_amount = fields.Monetary(
        string="Settled Amount",
        currency_field="sudi_currency_id",
        compute="_compute_sudi_billing_totals",
        store=True,
    )
    sudi_currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    sudi_job_type_summary = fields.Char(
        string="Job Work",
        compute="_compute_sudi_billing_totals",
        store=True,
    )
    sudi_pickup_user_id = fields.Many2one(
        "res.users",
        string="Pickup Person",
        tracking=True,
    )
    sudi_pickup_datetime = fields.Datetime(string="Pickup Date/Time", tracking=True)
    # Delivery orders share `state` with receipts, whose "assigned" reads "Job Work
    # in Progress". Deliveries get their own stage so the label fits the flow:
    # confirmation awaited -> out for delivery (taken by an operator) -> delivered.
    sudi_delivery_stage = fields.Selection(
        [
            ("awaiting", "Delivery Confirmation Awaited"),
            ("out", "Out for Delivery"),
            ("delivered", "Delivered"),
            ("cancelled", "Cancelled"),
        ],
        string="Delivery Stage",
        compute="_compute_sudi_delivery_stage",
        store=True,
        index=True,
        copy=False,
    )
    sudi_out_for_delivery_datetime = fields.Datetime(string="Out for Delivery Since", copy=False, tracking=True)
    sudi_pod_receiver_name = fields.Char(
        string="Received By",
        copy=False,
        tracking=True,
        help="Who took delivery of the parcel.",
    )
    sudi_pod_signature = fields.Image(
        string="Receiver Signature",
        max_width=1024,
        max_height=256,
        copy=False,
    )
    sudi_pod_attachment_ids = fields.Many2many(
        "ir.attachment",
        "sudi_picking_pod_attachment_rel",
        "picking_id",
        "attachment_id",
        string="Delivery Photos",
        copy=False,
    )
    sudi_customer_contact = fields.Char(string="Customer Contact", tracking=True)
    sudi_pickup_address_id = fields.Many2one(
        "res.partner",
        string="Pickup Address",
        copy=False,
        tracking=True,
    )
    sudi_pickup_address = fields.Text(
        string="Pickup Address",
        copy=False,
        tracking=True,
    )
    sudi_internal_notes = fields.Text(string="Job Work Notes", tracking=True)
    sudi_jangad_image = fields.Image(string="Jangad")
    sudi_jangad_attachment_ids = fields.Many2many(
        "ir.attachment",
        "sudi_picking_jangad_attachment_rel",
        "picking_id",
        "attachment_id",
        string="Jangad Pages",
        copy=False,
        help="Every page of the jangad. The first page is mirrored into "
             "sudi_jangad_image, which the receipt report and the WhatsApp "
             "templates read.",
    )
    sudi_jangad_page_count = fields.Integer(compute="_compute_sudi_jangad_page_count")
    sudi_partner_address = fields.Text(
        string="Customer Address",
        compute="_compute_sudi_partner_address",
    )
    sudi_billing_line_ids = fields.One2many(
        "sudi.diamond.billing.line",
        "picking_id",
        string="Billing Details",
        copy=False,
    )
    sudi_timesheet_ids = fields.One2many(
        "account.analytic.line",
        "sudi_picking_id",
        string="Timesheets",
    )
    sudi_total_hours_spent = fields.Float(
        string="Time Spent",
        compute="_compute_sudi_total_hours_spent",
        compute_sudo=True,
    )
    sudi_display_timesheet_timer = fields.Boolean(
        string="Display Timesheet Timer",
        compute="_compute_sudi_display_timesheet_timer",
    )
    sudi_timesheet_unit_amount = fields.Float(compute="_compute_sudi_timesheet_unit_amount")
    sudi_timesheet_job_type_id = fields.Many2one(
        "sudi.diamond.job.type",
        string="Timer Job Type",
        copy=False,
    )
    sudi_current_department_id = fields.Many2one(
        "sudi.diamond.job.type",
        string="Current Department",
        copy=False,
        tracking=True,
    )
    sudi_involved_department_ids = fields.Many2many(
        "sudi.diamond.job.type",
        "sudi_picking_job_type_involved_rel",
        "picking_id",
        "job_type_id",
        string="Involved Departments",
        tracking=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("sudi_is_diamond_job_work"):
                vals.pop("sudi_billing_line_ids", None)
        pickings = super().create(vals_list)
        pickings._sudi_notify_pickup_scheduled()
        pickings._sudi_sync_billing_details_on_save()
        return pickings

    def write(self, vals):
        if (
            ("move_ids" in vals or "move_line_ids" in vals or "sudi_billing_line_ids" in vals)
            and not self.env.context.get("sudi_skip_billing_sync")
            and not self.env.context.get("sudi_allow_pickup_edit")
        ):
            for picking in self:
                if picking.sudi_is_diamond_job_work and picking.picking_type_code == "incoming" and picking.state == "sudi_pickup_pending":
                    raise UserError(_("This pickup is still pending you can not input the data please confirm the pick up"))
        res = super().write(vals)
        if not self.env.context.get("sudi_skip_pickup_scheduled_notify") and (
            "sudi_jangad_image" in vals
            or "sudi_jangad_attachment_ids" in vals
            or "sudi_pickup_user_id" in vals
        ):
            self._sudi_notify_pickup_scheduled()
        if not self.env.context.get("sudi_skip_billing_sync"):
            trigger_fields = {
                "move_ids",
                "partner_id",
                "company_id",
                "sudi_is_diamond_job_work",
            }
            if trigger_fields.intersection(vals):
                self._sudi_sync_billing_details_on_save()
        return res

    def _sudi_sync_billing_details_on_save(self):
        receipts = self.filtered(
            lambda picking: picking.sudi_is_diamond_job_work
            and picking.picking_type_code == "incoming"
            and picking.state not in ("cancel", "done")
        )
        if receipts:
            receipts.with_context(sudi_skip_billing_sync=True)._sudi_sync_billing_details()

    @api.model
    def _sudi_get_pickup_notify_users(self):
        return self.env["res.users"]._sudi_get_notification_users("sudi_notify_pickup_scheduled")

    @api.model
    def _sudi_get_pickup_confirmed_notify_users(self):
        return self.env["res.users"]._sudi_get_notification_users("sudi_notify_pickup_confirmed")

    @api.model
    def _sudi_get_due_data_entry_notify_users(self):
        return self.env["res.users"]._sudi_get_notification_users("sudi_notify_due_data_entry")

    @api.model
    def _cron_sudi_notify_due_data_entry(self):
        """Cron job to send daily WhatsApp notifications/reminders for pending Jangad receipts due for data entry."""
        notify_users = self._sudi_get_due_data_entry_notify_users()
        if not notify_users:
            return

        pending_receipts = self.search([
            ("sudi_is_diamond_job_work", "=", True),
            ("picking_type_code", "=", "incoming"),
            ("state", "not in", ("done", "cancel")),
        ])
        if not pending_receipts:
            return

        body_lines = [
            "===============================",
            "             *SDPPL*",
            "    _Daily Data Entry Reminder_",
            "===============================\n\n",
            "📋  *DAILY JANGAD DATA ENTRY REMINDER*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
            "The following Jangad receipts are pending data entry:\n",
        ]
        for receipt in pending_receipts:
            customer_name = receipt.partner_id.name if receipt.partner_id else _("Customer")
            body_lines.append(f"    ▪ *#{receipt.name}*  -  {customer_name}")

        body_lines.append(_("\nPlease update data entry for these orders at the earliest.\n\n\nThank you,\n\n*Team SDPPL*"))
        body_text = "\n".join(body_lines)
        for user in notify_users:
            user_phone = user.partner_id.phone
            if user_phone:
                pending_receipts[:1]._sudi_send_whatsapp_message(
                    recipient_phone=user_phone,
                    body_text=body_text,
                    partner=user.partner_id,
                )

    @api.model
    def _sudi_build_partner_mention_body(self, text, partner):
        mention_html = Markup(
            "<a href=\"#\" class=\"o_mail_redirect\" data-oe-model=\"res.partner\" "
            "data-oe-id=\"%s\" target=\"_blank\" contenteditable=\"false\">@%s</a>"
        ) % (partner.id, escape(partner.display_name))
        return Markup("%s %s") % (escape(text), mention_html)

    def _sudi_post_user_notification(self, notify_users, subject, body_text, toast_type="info"):
        notify_users = notify_users.exists()
        if not notify_users:
            return
        for notify_user in notify_users:
            partner = notify_user.partner_id
            body = self._sudi_build_partner_mention_body(body_text, partner)
            for record in self:
                record.sudo().message_post(
                    body=body,
                    subject=subject,
                    message_type="comment",
                    partner_ids=partner.ids,
                    subtype_xmlid="mail.mt_comment",
                )
                notify_user._bus_send(
                    "simple_notification",
                    {
                        "title": subject,
                        "message": body_text,
                        "type": toast_type,
                        "sticky": True,
                    },
                )

    @api.model
    def _sudi_parse_event_datetime(self, occurred_at):
        """Resolve a caller-supplied event time to naive UTC.

        Returns ``(value, error_code)``; exactly one of the two is set. The
        codes are the ones the mobile API answers with -- ``VALIDATION``,
        ``CLOCK_SKEW``, ``STALE_INTENT`` -- so a router can classify the failure
        without catching and re-reading an exception message.

        A falsy ``occurred_at`` means now, which is what every web-client caller
        passes.
        """
        now = fields.Datetime.now()
        if not occurred_at:
            return now, None

        value = occurred_at
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                return None, "VALIDATION"
        if not isinstance(value, datetime):
            return None, "VALIDATION"
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)

        if value > now + timedelta(seconds=SUDI_MAX_CLOCK_SKEW_SECONDS):
            return None, "CLOCK_SKEW"
        if value < now - timedelta(hours=SUDI_MAX_BACKDATE_HOURS):
            return None, "STALE_INTENT"
        # A device a few seconds fast must not be allowed to record the future.
        return min(value, now), None

    @api.model
    def _sudi_event_datetime(self, occurred_at=None):
        """``_sudi_parse_event_datetime`` for callers that want an exception."""
        value, error = self._sudi_parse_event_datetime(occurred_at)
        if error == "CLOCK_SKEW":
            raise UserError(_(
                "This device's clock is more than %s seconds ahead of the server, "
                "so the event time was refused.", SUDI_MAX_CLOCK_SKEW_SECONDS,
            ))
        if error == "STALE_INTENT":
            raise UserError(_(
                "This event is more than %s hours old and can no longer be "
                "recorded automatically.", SUDI_MAX_BACKDATE_HOURS,
            ))
        if error:
            raise UserError(_("%r is not a valid event date and time.", occurred_at))
        return value

    def _sudi_post_event_provenance(self, event, occurred_at_value):
        """Record that an event happened earlier, and on which device.

        Skipped for an ordinary online action with no device behind it: a note
        on every delivery saying it happened just now would drown the chatter.
        """
        device_uid = self.env.context.get("sudi_device_uid")
        received_at = fields.Datetime.now()
        delay = abs((received_at - occurred_at_value).total_seconds())
        if not device_uid and delay < 120:
            return
        device_note = _(" from device %s", device_uid) if device_uid else ""
        for picking in self:
            # sudo: confirming a pickup moves the receipt out of the operator's
            # own scope (it becomes job work), so by the time this note is
            # written they can no longer read the record they just acted on.
            # The action's access check has already run; this is the audit
            # trail of it, written the same way _sudi_post_user_notification
            # writes its own.
            picking.sudo().message_post(
                body=_(
                    "%(event)s recorded by %(user)s. Happened at %(occurred)s UTC, "
                    "received at %(received)s UTC%(device)s.",
                    event=event,
                    user=self.env.user.name,
                    occurred=occurred_at_value,
                    received=received_at,
                    device=device_note,
                ),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )

    def _sudi_get_form_view_url(self):
        self.ensure_one()
        base_url = self.get_base_url().rstrip("/")
        return f"{base_url}/web#id={self.id}&model={self._name}&view_type=form"

    @api.depends("sudi_jangad_attachment_ids", "sudi_jangad_image")
    def _compute_sudi_jangad_page_count(self):
        for picking in self:
            picking.sudi_jangad_page_count = (
                (1 if picking.sudi_jangad_image else 0)
                + len(picking.sudi_jangad_attachment_ids)
            )

    def _sudi_add_jangad_pages(self, datas_list):
        """Attach jangad pages, keeping page 1 mirrored into ``sudi_jangad_image``.

        A jangad can run to several handwritten sheets. The report and the
        WhatsApp templates read ``sudi_jangad_image``, so the first page stays
        there and the remainder live as attachments; nothing that reads the old
        field has to change.
        """
        self.ensure_one()
        pages = [datas for datas in (datas_list or []) if datas]
        if not pages:
            return self.env["ir.attachment"]
        vals = {}
        if not self.sudi_jangad_image:
            vals["sudi_jangad_image"] = pages.pop(0)
            if not pages:
                self.write(vals)
                return self.env["ir.attachment"]
        offset = len(self.sudi_jangad_attachment_ids) + 1
        attachments = self.env["ir.attachment"].sudo().create([
            {
                "name": f"jangad_{self.name or self.id}_{offset + index + 1}.jpg",
                "type": "binary",
                "datas": datas,
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "image/jpeg",
            }
            for index, datas in enumerate(pages)
        ])
        vals["sudi_jangad_attachment_ids"] = [
            Command.link(record.id) for record in attachments
        ]
        self.write(vals)
        return attachments

    def _sudi_get_jangad_image_attachment(self):
        """Every jangad page as attachments, for the WhatsApp notifications.

        Page 1 lives in ``sudi_jangad_image`` and pages 2+ are already
        attachments, so the first is materialised here and the rest appended.
        """
        self.ensure_one()
        if not self.sudi_jangad_image:
            return self.sudi_jangad_attachment_ids
        first_page = self.env["ir.attachment"].sudo().create({
            "name": f"jangad_{self.name}.jpg",
            "type": "binary",
            "datas": self.sudi_jangad_image,
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "image/jpeg",
        })
        return first_page | self.sudi_jangad_attachment_ids

    def _sudi_get_delivery_pdf_attachment(self):
        """Render and return the PDF attachment of Diamond Job Work Receipt / Delivery report."""
        self.ensure_one()
        try:
            report_xml_id = "diamond.action_report_diamond_job_work"
            pdf_content = False
            report = self.env.ref(report_xml_id, raise_if_not_found=False)
            if report:
                try:
                    res = report.sudo()._render_qweb_pdf([self.id])
                    pdf_content = res[0] if isinstance(res, (tuple, list)) else res
                except Exception:
                    res = self.env["ir.actions.report"].sudo()._render_qweb_pdf(report_xml_id, [self.id])
                    pdf_content = res[0] if isinstance(res, (tuple, list)) else res

            if not pdf_content:
                return self.env["ir.attachment"]

            clean_name = self.name.replace("/", "_")
            attachment_name = f"Delivery_{clean_name}.pdf"
            attachment = self.env["ir.attachment"].sudo().create({
                "name": attachment_name,
                "type": "binary",
                "datas": base64.b64encode(pdf_content),
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "application/pdf",
            })
            return attachment
        except Exception:
            _logger.exception("Failed to render delivery PDF report for %s", self.display_name)
            return self.env["ir.attachment"]


    def _sudi_get_whatsapp_template_context(self, event):

        """Centralized controlled variable resolver for Sudi WhatsApp templates."""
        self.ensure_one()
        customer_partner = self.partner_id
        customer_name = customer_partner.name if customer_partner else _("Customer")
        pickup_person_name = (
            self.sudi_pickup_user_id.name
            if self.sudi_pickup_user_id
            else (self.env.user.name or _("N/A"))
        )
        address = (
            self.sudi_pickup_address
            or self.sudi_partner_address
            or (customer_partner.contact_address if customer_partner else "")
            or ""
        )
        phone_no = (
            self.sudi_customer_contact
            or (customer_partner.phone if customer_partner else "")
            or ""
        )
        url = self._sudi_get_form_view_url()

        return {
            "customer_name": customer_name,
            "pickup_person": pickup_person_name,
            "delivery_person": pickup_person_name,
            "address": address,
            "phone": phone_no,
            "url": url,
            "pickup_reference": self.name,
            "delivery_reference": self.name,
        }

    def _sudi_notify_pickup_scheduled(self):
        """Trigger 1: Notify pickup person(s) and customer on Jangad upload / schedule creation."""
        notify_users = self._sudi_get_pickup_notify_users()
        receipts = self.filtered(
            lambda picking: picking.sudi_is_diamond_job_work
            and picking.picking_type_code == "incoming"
            and (picking.state == "sudi_pickup_pending" or picking.sudi_jangad_image)
        )
        # sudo: these are system notifications, not reads on the actor's
        # behalf. By the time one runs, the record has usually moved on to a
        # state the actor can no longer read -- a confirmed pickup belongs to
        # job work, and a delivery backdated with occurred_at drops out of the
        # operator's "done today" window -- and a WhatsApp to the customer must
        # not fail because of that.
        for receipt in receipts.sudo():
            if notify_users:
                receipt._sudi_post_user_notification(
                    notify_users,
                    _("Pickup scheduled: %s") % receipt.name,
                    _("A pick has been scheduled for receipt %s.", receipt.name),
                    toast_type="info",
                )

            customer_partner = receipt.partner_id
            attachment = receipt._sudi_get_jangad_image_attachment()

            # Message 1A: Send To Pickup Person
            wa_recipients = receipt.sudi_pickup_user_id or notify_users
            body_pickup = receipt._sudi_render_event_whatsapp_message("pickup_scheduled")
            if body_pickup:
                for user in wa_recipients:
                    pickup_phone = user.partner_id.phone
                    if pickup_phone:
                        receipt._sudi_send_whatsapp_message(
                            recipient_phone=pickup_phone,
                            body_text=body_pickup,
                            attachment=attachment,
                            partner=user.partner_id,
                        )

            # Message 1B: Send To Customer
            customer_phone = receipt.sudi_customer_contact or (customer_partner.phone if customer_partner else False)
            if customer_phone:
                body_cust = receipt._sudi_render_event_whatsapp_message("pickup_request_confirmation")
                if body_cust:
                    receipt._sudi_send_whatsapp_message(
                        recipient_phone=customer_phone,
                        body_text=body_cust,
                        partner=customer_partner,
                    )

    def _sudi_notify_pickup_confirmed(self):
        """Trigger 2: Notify Customer & Admin on Pickup Done."""
        notify_users = self._sudi_get_pickup_confirmed_notify_users()
        # sudo: these are system notifications, not reads on the actor's
        # behalf. By the time one runs, the record has usually moved on to a
        # state the actor can no longer read -- a confirmed pickup belongs to
        # job work, and a delivery backdated with occurred_at drops out of the
        # operator's "done today" window -- and a WhatsApp to the customer must
        # not fail because of that.
        for receipt in self.sudo():
            if notify_users:
                receipt._sudi_post_user_notification(
                    notify_users,
                    _("Pickup confirmed: %s") % receipt.name,
                    _(
                        "Pickup for receipt %s was successful. "
                        "Please validate the Jangad and fill in the job table for this order.",
                        receipt.name,
                    ),
                    toast_type="success",
                )

            customer_partner = receipt.partner_id
            attachment = receipt._sudi_get_jangad_image_attachment()

            # Message 2A: Send To Customer
            customer_phone = receipt.sudi_customer_contact or (customer_partner.phone if customer_partner else False)
            if customer_phone:
                cust_body = receipt._sudi_render_event_whatsapp_message("pickup_confirmed")
                if cust_body:
                    receipt._sudi_send_whatsapp_message(
                        recipient_phone=customer_phone,
                        body_text=cust_body,
                        attachment=attachment,
                        partner=customer_partner,
                    )

            # Message 2B: Send To Admin
            admin_body = receipt._sudi_render_event_whatsapp_message("pickup_admin_intimation")
            if admin_body:
                for notify_user in notify_users:
                    admin_partner = notify_user.partner_id
                    admin_phone = admin_partner.phone
                    if admin_phone:
                        receipt._sudi_send_whatsapp_message(
                            recipient_phone=admin_phone,
                            body_text=admin_body,
                            attachment=attachment,
                            partner=admin_partner,
                        )

    def _sudi_notify_pickup_cancelled(self):
        """Send WhatsApp cancellation intimation to customer with Jangad attachment."""
        # sudo: these are system notifications, not reads on the actor's
        # behalf. By the time one runs, the record has usually moved on to a
        # state the actor can no longer read -- a confirmed pickup belongs to
        # job work, and a delivery backdated with occurred_at drops out of the
        # operator's "done today" window -- and a WhatsApp to the customer must
        # not fail because of that.
        for receipt in self.sudo():
            customer_partner = receipt.partner_id
            customer_phone = receipt.sudi_customer_contact or (customer_partner.phone if customer_partner else False)

            if customer_phone:
                attachment = receipt._sudi_get_jangad_image_attachment()
                wa_body = receipt._sudi_render_event_whatsapp_message("pickup_cancelled")
                if wa_body:
                    receipt._sudi_send_whatsapp_message(
                        recipient_phone=customer_phone,
                        body_text=wa_body,
                        attachment=attachment,
                        partner=customer_partner,
                    )

    def _sudi_notify_delivery_assigned(self):
        """Notify customer and delivery person when delivery picking state turns 'assigned'."""
        deliveries = self.filtered(
            lambda picking: picking.sudi_is_diamond_job_work
            and picking.picking_type_code == "outgoing"
            and picking.state == "assigned"
        )
        # sudo: these are system notifications, not reads on the actor's
        # behalf. By the time one runs, the record has usually moved on to a
        # state the actor can no longer read -- a confirmed pickup belongs to
        # job work, and a delivery backdated with occurred_at drops out of the
        # operator's "done today" window -- and a WhatsApp to the customer must
        # not fail because of that.
        for delivery in deliveries.sudo():
            customer_partner = delivery.partner_id
            customer_phone = delivery.sudi_customer_contact or (customer_partner.phone if customer_partner else False)

            # 1. Send To Customer
            # if customer_phone:
            #     cust_body = delivery._sudi_render_event_whatsapp_message("delivery_assigned")
            #     if cust_body:
            #         delivery._sudi_send_whatsapp_message(
            #             recipient_phone=customer_phone,
            #             body_text=cust_body,
            #             partner=customer_partner,
            #         )

            # 2. Send To Delivery Person / Flagged users
            notify_users = self._sudi_get_pickup_notify_users()
            delivery_recipients = delivery.sudi_pickup_user_id or notify_users
            deliv_body = delivery._sudi_render_event_whatsapp_message("delivery_dispatch")
            if deliv_body:
                for user in delivery_recipients:
                    user_phone = user.partner_id.phone
                    if user_phone:
                        delivery._sudi_send_whatsapp_message(
                            recipient_phone=user_phone,
                            body_text=deliv_body,
                            partner=user.partner_id,
                        )

    def _sudi_notify_delivery_completed(self):
        """Notify customer and admin when delivery is completed."""
        deliveries = self.filtered(
            lambda picking: picking.sudi_is_diamond_job_work
            and picking.picking_type_code == "outgoing"
            and picking.state == "done"
        )
        # sudo: these are system notifications, not reads on the actor's
        # behalf. By the time one runs, the record has usually moved on to a
        # state the actor can no longer read -- a confirmed pickup belongs to
        # job work, and a delivery backdated with occurred_at drops out of the
        # operator's "done today" window -- and a WhatsApp to the customer must
        # not fail because of that.
        for delivery in deliveries.sudo():
            customer_partner = delivery.partner_id
            customer_phone = delivery.sudi_customer_contact or (customer_partner.phone if customer_partner else False)

            # Generate PDF attachment for delivery report
            pdf_attachment = delivery._sudi_get_delivery_pdf_attachment()

            # 1. Send To Customer
            if customer_phone:
                cust_body = delivery._sudi_render_event_whatsapp_message("delivery_completed")
                if cust_body:
                    delivery._sudi_send_whatsapp_message(
                        recipient_phone=customer_phone,
                        body_text=cust_body,
                        attachment=pdf_attachment,
                        partner=customer_partner,
                    )

            # 2. Send To Back Office Admin
            notify_users = self._sudi_get_pickup_confirmed_notify_users()
            admin_body = delivery._sudi_render_event_whatsapp_message("delivery_admin_intimation")
            if admin_body:
                for user in notify_users:
                    user_phone = user.partner_id.phone
                    if user_phone:
                        delivery._sudi_send_whatsapp_message(
                            recipient_phone=user_phone,
                            body_text=admin_body,
                            attachment=pdf_attachment,
                            partner=user.partner_id,
                        )




    @api.depends("sudi_delivery_ids")
    def _compute_sudi_counts(self):
        for picking in self:
            picking.sudi_delivery_count = len(picking.sudi_delivery_ids)

    @api.depends("sudi_origin_receipt_id.name")
    def _compute_sudi_origin_receipt_name(self):
        for picking in self:
            picking.sudi_origin_receipt_name = picking.sudi_origin_receipt_id.name or ""

    def _compute_sudi_billing_log_count(self):
        Log = self.env["sudi.diamond.billing.log"]
        for picking in self:
            field = "receipt_id" if picking.picking_type_code == "incoming" else "delivery_id"
            picking.sudi_billing_log_count = Log.search_count([(field, "=", picking.id)]) if picking.id else 0

    @api.depends("sudi_billed_invoice_ids", "sudi_delivery_ids.sudi_billed_invoice_ids")
    def _compute_sudi_invoice_ids(self):
        for picking in self:
            if picking.picking_type_code == "incoming":
                invoices = picking.sudi_delivery_ids.sudi_billed_invoice_ids
            else:
                invoices = picking.sudi_billed_invoice_ids
            picking.sudi_invoice_ids = invoices
            picking.sudi_invoice_count = len(invoices)

    @api.depends(
        "state",
        "picking_type_code",
        "sudi_is_diamond_job_work",
        "sudi_origin_receipt_id",
        "move_ids.sudi_billing_state",
        "sudi_billed_invoice_ids.state",
        "sudi_reference_statement_ids.state",
    )
    def _compute_sudi_billing_status(self):
        for picking in self:
            if not picking._sudi_is_job_work_delivery() or picking.state != "done":
                picking.sudi_billing_status = "none"
                continue
            states = set(picking.move_ids.filtered(lambda move: move.state == "done").mapped("sudi_billing_state")) - {"none"}
            settled = states & {"billed", "closed"}
            if not states:
                picking.sudi_billing_status = "none"
            elif "to_bill" in states:
                picking.sudi_billing_status = "partial" if settled else "to_bill"
            elif settled:
                picking.sudi_billing_status = "billed" if "billed" in settled else "closed"
            elif picking.sudi_billed_invoice_ids.filtered(lambda move: move.state != "cancel"):
                # Only no-charge returns, attached to an invoice's annexure.
                picking.sudi_billing_status = "billed"
            elif picking.sudi_reference_statement_ids.filtered(lambda statement: statement.state == "settled"):
                picking.sudi_billing_status = "closed"
            else:
                picking.sudi_billing_status = "no_charge"

    @api.depends(
        "move_ids.state",
        "move_ids.sudi_pcs_qty",
        "move_ids.sudi_carats",
        "move_ids.sudi_job_type_id",
        "move_ids.sudi_billing_state",
        "move_ids.sudi_billable_amount",
    )
    def _compute_sudi_billing_totals(self):
        for picking in self:
            moves = picking.move_ids.filtered(lambda move: move.state != "cancel")
            picking.sudi_total_pcs = sum(moves.mapped("sudi_pcs_qty"))
            picking.sudi_total_carats = sum(moves.mapped("sudi_carats"))
            picking.sudi_billable_amount = sum(
                moves.filtered(lambda move: move.sudi_billing_state == "to_bill").mapped("sudi_billable_amount")
            )
            picking.sudi_settled_amount = sum(
                moves.filtered(lambda move: move.sudi_billing_state in ("billed", "closed")).mapped("sudi_billable_amount")
            )
            names = []
            for move in moves.sorted(key=lambda move: (move.sudi_sr or 0, move.id)):
                name = move.sudi_job_type_id.name
                if name and name not in names:
                    names.append(name)
            picking.sudi_job_type_summary = ", ".join(names)

    @api.depends(
        "state",
        "picking_type_code",
        "sudi_is_diamond_job_work",
        "sudi_origin_receipt_id",
        "sudi_pickup_user_id",
        "sudi_out_for_delivery_datetime",
    )
    def _compute_sudi_delivery_stage(self):
        for picking in self:
            if not picking._sudi_is_job_work_delivery():
                picking.sudi_delivery_stage = False
            elif picking.state == "done":
                picking.sudi_delivery_stage = "delivered"
            elif picking.state == "cancel":
                picking.sudi_delivery_stage = "cancelled"
            elif picking.sudi_pickup_user_id and picking.sudi_out_for_delivery_datetime:
                picking.sudi_delivery_stage = "out"
            else:
                picking.sudi_delivery_stage = "awaiting"

    def action_sudi_take_for_delivery(self, occurred_at=None):
        """An operator picks the deliveries they are going out with.

        Multi-record: called from the operator's list selection. The dispatch
        WhatsApp goes out here, to the person who actually took the parcels.
        ``occurred_at`` carries the capture time when the tap happened offline.
        """
        self._sudi_check_pickup_delivery_operator_access()
        deliveries = self.filtered(lambda picking: picking.sudi_delivery_stage == "awaiting")
        if not deliveries:
            raise UserError(_("Select deliveries that are still awaiting confirmation."))
        taken_at = self._sudi_event_datetime(occurred_at)
        deliveries.with_context(sudi_skip_pickup_scheduled_notify=True).write({
            "sudi_pickup_user_id": self.env.user.id,
            "sudi_out_for_delivery_datetime": taken_at,
        })
        for delivery in deliveries:
            delivery.message_post(
                body=_("Taken for delivery by %s.", self.env.user.name),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        deliveries._sudi_post_event_provenance(_("Taken for delivery"), taken_at)
        deliveries._sudi_notify_delivery_assigned()
        return True

    def action_sudi_release_delivery(self):
        """Hand a taken delivery back to the awaiting pool."""
        self._sudi_check_pickup_delivery_operator_access()
        deliveries = self.filtered(lambda picking: picking.sudi_delivery_stage == "out")
        deliveries.with_context(sudi_skip_pickup_scheduled_notify=True).write({
            "sudi_pickup_user_id": False,
            "sudi_out_for_delivery_datetime": False,
        })
        for delivery in deliveries:
            delivery.message_post(
                body=_("Released back to awaiting deliveries by %s.", self.env.user.name),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        return True

    def _sudi_is_job_work_delivery(self):
        self.ensure_one()
        return bool(
            self.sudi_is_diamond_job_work
            and self.picking_type_code == "outgoing"
            and self.sudi_origin_receipt_id
        )

    def _sudi_post_billing_chatter(self, body):
        """Post ``body`` on each delivery and on its origin receipt."""
        for delivery in self:
            targets = delivery | delivery.sudi_origin_receipt_id
            for picking in targets:
                picking.message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_note")

    @api.depends("partner_id")
    def _compute_sudi_partner_address(self):
        for picking in self:
            picking.sudi_partner_address = picking.partner_id.contact_address or ""

    @api.depends(
        "move_type",
        "move_ids.state",
        "move_ids.picking_id",
        "sudi_is_diamond_job_work",
        "sudi_pickup_datetime",
        "picking_type_id.code",
    )
    def _compute_state(self):
        super()._compute_state()
        for picking in self:
            if (
                picking.sudi_is_diamond_job_work
                and picking.picking_type_code == "incoming"
                and picking.state == "draft"
                and not picking.sudi_pickup_datetime
            ):
                picking.state = "sudi_pickup_pending"

    @api.depends("sudi_timesheet_ids.unit_amount")
    def _compute_sudi_total_hours_spent(self):
        if not any(self._ids):
            for picking in self:
                picking.sudi_total_hours_spent = sum(picking.sudi_timesheet_ids.mapped("unit_amount"))
            return

        timesheet_read_group = self.env["account.analytic.line"]._read_group(
            [("sudi_picking_id", "in", self.ids)],
            ["sudi_picking_id"],
            ["unit_amount:sum"],
        )
        hours_by_picking = {picking.id: unit_amount_sum for picking, unit_amount_sum in timesheet_read_group}
        for picking in self:
            picking.sudi_total_hours_spent = hours_by_picking.get(picking.id, 0.0)

    def _compute_sudi_display_timesheet_timer(self):
        uom_hour = self.env.ref("uom.product_uom_hour", raise_if_not_found=False)
        is_hour_encoding = self.env.company.timesheet_encode_uom_id == uom_hour
        for picking in self:
            picking.sudi_display_timesheet_timer = (
                is_hour_encoding
                and picking.sudi_is_diamond_job_work
                and picking.picking_type_code == "incoming"
                and picking.state == "assigned"
            )

    @api.depends("user_timer_id")
    def _compute_sudi_timesheet_unit_amount(self):
        timesheet_ids = self.mapped("user_timer_id.res_id")
        unit_amount_by_timesheet_id = {}
        if timesheet_ids:
            timesheet_read = self.env["account.analytic.line"].search_read(
                [("id", "in", timesheet_ids)],
                ["unit_amount"],
            )
            unit_amount_by_timesheet_id = {
                timesheet["id"]: timesheet["unit_amount"]
                for timesheet in timesheet_read
            }

        for picking in self:
            timesheet_id = picking.user_timer_id.res_id if picking.user_timer_id else False
            picking.sudi_timesheet_unit_amount = unit_amount_by_timesheet_id.get(timesheet_id, 0.0)

    def action_sudi_open_confirm_pickup_wizard(self):
        self.ensure_one()
        self._sudi_check_pickup_delivery_operator_access()
        return {
            "name": _("Confirm Pickup"),
            "type": "ir.actions.act_window",
            "res_model": "sudi.pickup.confirmation.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_picking_id": self.id,
                "default_action_type": "confirm",
            },
        }

    def action_sudi_open_cancel_pickup_wizard(self):
        self.ensure_one()
        self._sudi_check_pickup_delivery_operator_access()
        return {
            "name": _("Cancel Pickup"),
            "type": "ir.actions.act_window",
            "res_model": "sudi.pickup.confirmation.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_picking_id": self.id,
                "default_action_type": "cancel",
            },
        }

    def action_sudi_confirm_pickup(self, occurred_at=None):
        self._sudi_check_pickup_delivery_operator_access()
        invalid_pickings = self.filtered(
            lambda picking: not picking.sudi_is_diamond_job_work
            or picking.picking_type_code != "incoming"
            or picking.state != "sudi_pickup_pending"
        )
        if invalid_pickings:
            raise UserError(_("Pickup can only be confirmed on diamond job-work receipts waiting for pickup."))

        picked_at = self._sudi_event_datetime(occurred_at)
        self.with_context(sudi_skip_pickup_scheduled_notify=True).write({
            "sudi_pickup_user_id": self.env.user.id,
            "sudi_pickup_datetime": picked_at,
        })
        self._sudi_post_event_provenance(_("Pickup"), picked_at)
        self._sudi_notify_pickup_confirmed()
        return True

    def action_sudi_cancel_pickup(self, occurred_at=None, reason=None):
        self._sudi_check_pickup_delivery_operator_access()
        invalid_pickings = self.filtered(
            lambda picking: not picking.sudi_is_diamond_job_work
            or picking.picking_type_code != "incoming"
            or picking.state != "sudi_pickup_pending"
        )
        if invalid_pickings:
            raise UserError(_("Pickup can only be cancelled on diamond job-work receipts waiting for pickup."))

        cancelled_at = self._sudi_event_datetime(occurred_at)
        if reason:
            for picking in self:
                picking.message_post(
                    body=_("Pickup cancelled: %s", reason),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
        self._sudi_post_event_provenance(_("Pickup cancellation"), cancelled_at)
        self._sudi_notify_pickup_cancelled()
        self.write({
            "active": False,
            "state": "cancel",
        })
        return True

    def _sudi_check_pickup_delivery_operator_access(self):
        """Gate the pickup and delivery field actions.

        This used to accept any ``base.group_user``, which the web client hid
        behind menu visibility. Once these methods are callable over the mobile
        API there are no menus, so the role group is checked properly. Stock
        users keep access because the office still drives the same actions from
        the web client.
        """
        if self.env.su:
            return
        if not (
            self.env.user.has_group("diamond.group_sudi_pickup_delivery_operator")
            or self.env.user.has_group("stock.group_stock_user")
        ):
            raise AccessError(_("You are not allowed to operate diamond pickup and delivery records."))

    def _sudi_check_job_work_access(self):
        """Gate the job-work actions, which are a different role from the field."""
        if self.env.su:
            return
        if not (
            self.env.user.has_group("diamond.group_sudi_job_work_user")
            or self.env.user.has_group("stock.group_stock_user")
        ):
            raise AccessError(_("You are not allowed to operate diamond job-work records."))

    def _sudi_transfer_department(self, job_type):
        """Move a job-work receipt to another department.

        The wizard and the mobile API both call this; the wizard used to hold
        the write itself, which an API cannot reach because
        ``action_sudi_transfer_department`` returns a window action.
        """
        self.ensure_one()
        self._sudi_check_job_work_access()
        if (
            not self.sudi_is_diamond_job_work
            or self.picking_type_code != "incoming"
            or self.state != "assigned"
        ):
            raise UserError(_("Department transfer is only available on diamond job-work receipts in progress."))
        if not job_type:
            raise UserError(_("Select the department to transfer to."))
        self.write({"sudi_current_department_id": job_type.id})
        return True

    def action_sudi_transfer_department(self):
        self.ensure_one()
        self._sudi_check_job_work_access()
        if (
            not self.sudi_is_diamond_job_work
            or self.picking_type_code != "incoming"
            or self.state != "assigned"
        ):
            raise UserError(_("Department transfer is only available on diamond job-work receipts in progress."))
        return {
            "name": _("Transfer Department"),
            "type": "ir.actions.act_window",
            "res_model": "sudi.diamond.department.transfer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_picking_id": self.id,
            },
        }

    def action_timer_start(self):
        self.ensure_one()
        if self.sudi_display_timesheet_timer:
            return super().action_timer_start()
        return False

    def action_timer_stop(self):
        self.ensure_one()
        if self.sudi_display_timesheet_timer and self.user_timer_id:
            timesheet = self._get_record_with_timer_running()
            if timesheet:
                return {
                    "name": _("Confirm Time Spent"),
                    "type": "ir.actions.act_window",
                    "res_model": "hr.timesheet.stop.timer.confirmation.wizard",
                    "context": {
                        "default_timesheet_id": timesheet.id,
                        "dialog_size": "medium",
                    },
                    "views": [[
                        self.env.ref("timesheet_grid.hr_timesheet_stop_timer_confirmation_wizard_view_form").id,
                        "form",
                    ]],
                    "target": "new",
                }
            return super().action_timer_stop()
        return False

    def _sudi_stop_timer(self):
        """Stop the receipt timer and bank the measured time.

        The web flow returns ``hr.timesheet.stop.timer.confirmation.wizard`` so
        the user can adjust the figure before it is saved. A phone has no
        wizard, and an operator has nothing to adjust against on the road, so
        the elapsed time is banked as measured. Returns the minutes.
        """
        self.ensure_one()
        self._sudi_check_job_work_access()
        timer = self.user_timer_id
        if not timer:
            return 0.0
        timesheet = self._get_record_with_timer_running()
        minutes = timer.action_timer_stop() or 0.0
        timer.unlink()
        if timesheet:
            timesheet.sudo().write({
                "unit_amount": (timesheet.unit_amount or 0.0) + minutes / 60.0,
            })
        return minutes

    def _sudi_get_default_timesheet_job_type(self):
        self.ensure_one()
        if self.sudi_timesheet_job_type_id:
            return self.sudi_timesheet_job_type_id

        job_types = self.move_ids.filtered(
            lambda move: move.state != "cancel" and move.sudi_job_type_id
        ).mapped("sudi_job_type_id")
        return job_types if len(job_types) == 1 else self.env["sudi.diamond.job.type"]

    def _create_record_to_start_timer(self):
        self.ensure_one()
        job_type = self._sudi_get_default_timesheet_job_type()
        if not job_type:
            raise UserError(_("Please select a Timer Job Type before starting the receipt timer."))

        project = self.env["account.analytic.line"]._sudi_get_timesheet_project()
        return self.env["account.analytic.line"].create({
            "sudi_picking_id": self.id,
            "sudi_job_type_id": job_type.id,
            "project_id": project.id,
            "date": fields.Date.context_today(self),
            "name": "/",
            "user_id": self.env.uid,
        })

    def _action_interrupt_user_timers(self):
        self.action_timer_stop()

    @api.model
    def _sudi_normalize_phone(self, phone):
        digits = re.sub(r"\D+", "", phone or "")
        return digits[-10:] if len(digits) > 10 else digits

    @api.model
    def _sudi_find_partner_by_phone(self, phone):
        phone_key = self._sudi_normalize_phone(phone)
        if not phone_key:
            return self.env["res.partner"]

        Partner = self.env["res.partner"].sudo()
        phone_fields = [field for field in ("phone", "mobile") if field in Partner._fields]
        if not phone_fields:
            return self.env["res.partner"]

        domain = [(phone_fields[0], "!=", False)]
        for field in phone_fields[1:]:
            domain = ["|", (field, "!=", False)] + domain

        partners = Partner.search(domain)
        for partner in partners:
            partner_numbers = {
                self._sudi_normalize_phone(partner[field])
                for field in phone_fields
                if partner[field]
            }
            if phone_key in partner_numbers:
                return partner.commercial_partner_id
        return self.env["res.partner"]

    @api.model
    def _sudi_format_pickup_address(self, partner):
        address = partner.contact_address or ""
        return "\n".join(line.strip() for line in address.splitlines() if line.strip())

    @api.model
    def sudi_get_public_pickup_address_suggestions(self, phone=None, partner=None):
        commercial_partner = (partner or self._sudi_find_partner_by_phone(phone)).sudo().commercial_partner_id
        if not commercial_partner:
            return []

        candidates = commercial_partner | commercial_partner.child_ids.filtered("active")
        suggestions = []
        seen_partner_ids = set()
        for candidate in candidates:
            if candidate.id in seen_partner_ids:
                continue
            address = self._sudi_format_pickup_address(candidate)
            if not address:
                continue
            seen_partner_ids.add(candidate.id)
            suggestions.append({
                "id": candidate.id,
                "name": candidate.display_name,
                "address": address,
                "is_default": candidate == commercial_partner,
            })
        return suggestions

    @api.model
    def _sudi_get_or_create_public_jangad_partner(self, phone):
        partner = self._sudi_find_partner_by_phone(phone)
        if partner:
            return partner
        return self.env["res.partner"].sudo().create({
            "name": _("Jangad Customer %s") % (phone or "").strip(),
            "phone": (phone or "").strip(),
        })

    @api.model
    def _sudi_create_manual_pickup_address(self, commercial_partner, phone, manual_pickup_address):
        lines = [
            line.strip()
            for line in (manual_pickup_address or "").splitlines()
            if line.strip()
        ]
        if not lines:
            return self.env["res.partner"]

        return self.env["res.partner"].sudo().create({
            "parent_id": commercial_partner.id,
            "type": "delivery",
            "name": _("Pickup Address"),
            "phone": (phone or "").strip(),
            "street": lines[0],
            "street2": "\n".join(lines[1:]),
        })

    @api.model
    def _sudi_resolve_public_pickup_address(self, phone, pickup_address_id=False, manual_pickup_address=False):
        Partner = self.env["res.partner"].sudo()
        manual_pickup_address = (manual_pickup_address or "").strip()
        commercial_partner = self._sudi_find_partner_by_phone(phone)
        pickup_address = self.env["res.partner"]

        if pickup_address_id:
            try:
                pickup_address_id = int(pickup_address_id)
            except (TypeError, ValueError):
                raise UserError(_("Please select a valid pickup address."))
            pickup_address = Partner.browse(pickup_address_id).exists()
            if not pickup_address:
                raise UserError(_("Please select a valid pickup address."))
            commercial_partner = commercial_partner or pickup_address.commercial_partner_id
            valid_address_ids = {
                suggestion["id"]
                for suggestion in self.sudi_get_public_pickup_address_suggestions(phone, commercial_partner)
            }
            if pickup_address.id not in valid_address_ids:
                raise UserError(_("The selected pickup address does not match the entered phone number."))
        elif manual_pickup_address:
            commercial_partner = self._sudi_get_or_create_public_jangad_partner(phone)
            pickup_address = self._sudi_create_manual_pickup_address(
                commercial_partner,
                phone,
                manual_pickup_address,
            )
        else:
            suggestions = self.sudi_get_public_pickup_address_suggestions(phone, commercial_partner)
            if not suggestions:
                raise UserError(_("Please enter a pickup address."))
            pickup_address = Partner.browse(suggestions[0]["id"])

        pickup_address_text = manual_pickup_address or self._sudi_format_pickup_address(pickup_address)
        return commercial_partner, pickup_address, pickup_address_text

    @api.model
    def _sudi_get_public_receipt_defaults(self):
        company = self.env.company
        warehouse = self.env["stock.warehouse"].sudo().search([("company_id", "=", company.id)], limit=1)
        if not warehouse:
            warehouse = self.env["stock.warehouse"].sudo().search([], limit=1)

        picking_type = warehouse.in_type_id if warehouse else self.env["stock.picking.type"]
        if not picking_type:
            picking_type = self.env["stock.picking.type"].sudo().search([
                ("code", "=", "incoming"),
                ("company_id", "in", [False, company.id]),
            ], limit=1)
        if not picking_type:
            raise UserError(_("No incoming receipt operation type is configured."))

        source_location = (
            picking_type.default_location_src_id
            or self.env.ref("stock.stock_location_suppliers", raise_if_not_found=False)
        )
        destination_location = picking_type.default_location_dest_id or warehouse.lot_stock_id
        if not source_location or not destination_location:
            raise UserError(_("Please configure source and destination locations on the receipt operation type."))
        return picking_type, source_location, destination_location

    @api.model
    def sudi_create_public_jangad_receipt(
        self,
        phone,
        jangad_image,
        pickup_address_id=False,
        manual_pickup_address=False,
        extra_pages=None,
    ):
        """Create a receipt from a customer upload.

        ``jangad_image`` is page 1 and keeps its original meaning; ``extra_pages``
        are the remaining sheets of a multi-page jangad, attached after creation
        so the notification that ``create`` fires still carries page 1.
        """
        picking_type, source_location, destination_location = self._sudi_get_public_receipt_defaults()
        partner, pickup_address, pickup_address_text = self._sudi_resolve_public_pickup_address(
            phone,
            pickup_address_id=pickup_address_id,
            manual_pickup_address=manual_pickup_address,
        )
        vals = {
            "picking_type_id": picking_type.id,
            "location_id": source_location.id,
            "location_dest_id": destination_location.id,
            "company_id": picking_type.company_id.id or self.env.company.id,
            "sudi_is_diamond_job_work": True,
            "sudi_customer_contact": phone,
            "sudi_pickup_address": pickup_address_text,
            "sudi_jangad_image": jangad_image,
        }
        if partner:
            vals["partner_id"] = partner.id
        if pickup_address:
            vals["sudi_pickup_address_id"] = pickup_address.id
        receipt = self.sudo().create(vals)
        if extra_pages:
            receipt.with_context(
                sudi_skip_pickup_scheduled_notify=True
            )._sudi_add_jangad_pages(extra_pages)
        return receipt

    @api.onchange(
        "partner_id",
        "company_id",
        "move_ids",
        "move_ids.sudi_job_type_id",
        "move_ids.sudi_pcs_qty",
        "move_ids.sudi_carats",
        "move_ids.quantity",
        "move_ids.product_uom_qty",
    )
    def _onchange_sudi_billing_details_source(self):
        return

    def action_sudi_recompute_billing_details(self):
        self._sudi_sync_billing_details()
        return True

    def _sudi_prepare_billing_detail_values(self):
        self.ensure_one()
        values = []
        partner = self.partner_id.commercial_partner_id
        for index, move in enumerate(
            self.move_ids.filtered(lambda stock_move: stock_move.state != "cancel" and stock_move.sudi_job_type_id),
            start=1,
        ):
            quantity = move._sudi_get_invoice_quantity()
            rounding = move.product_uom.rounding if move.product_uom else 0.01
            if float_is_zero(quantity, precision_rounding=rounding):
                continue
            job_type = move.sudi_job_type_id
            price_unit, price_source = job_type._sudi_get_price_for_partner_with_source(partner, self.company_id)
            values.append({
                "sequence": move.sudi_sr or index,
                "receipt_move_id": move.id,
                "job_type_id": job_type.id,
                "name": move._sudi_get_invoice_line_name(),
                "quantity": quantity,
                "price_unit": price_unit,
                "price_source": price_source,
                "active": True,
            })
        return values

    def _sudi_sync_billing_details(self):
        BillingLine = self.env["sudi.diamond.billing.line"].sudo()
        for receipt in self.filtered(
            lambda picking: picking.sudi_is_diamond_job_work and picking.picking_type_code == "incoming"
        ):
            prepared_values = receipt._sudi_prepare_billing_detail_values()
            prepared_receipt_move_ids = {vals["receipt_move_id"] for vals in prepared_values}
            existing_lines = receipt.sudi_billing_line_ids.sudo().filtered(lambda line: line.active and not line._sudi_is_locked())
            existing_by_receipt_move = {
                line.receipt_move_id.id: line
                for line in existing_lines
                if line.receipt_move_id
            }

            for vals in prepared_values:
                line = existing_by_receipt_move.get(vals["receipt_move_id"])
                if not line:
                    BillingLine.create({"picking_id": receipt.id, **vals})
                    continue

                write_vals = {
                    "sequence": vals["sequence"],
                    "job_type_id": vals["job_type_id"],
                    "name": vals["name"],
                    "active": True,
                }
                if not line.manual_quantity:
                    write_vals["quantity"] = vals["quantity"]
                if line.manual_price:
                    write_vals["price_source"] = "manual"
                else:
                    write_vals["price_unit"] = vals["price_unit"]
                    write_vals["price_source"] = vals["price_source"]
                line.write(write_vals)

            stale_lines = existing_lines.filtered(
                lambda line: (
                    not line.receipt_move_id
                    or line.receipt_move_id.id not in prepared_receipt_move_ids
                )
                and not line.manual_quantity
                and not line.manual_price
            )
            stale_lines.write({"active": False})

    def _autoconfirm_picking(self):
        regular_pickings = self.filtered(
            lambda picking: not (
                picking.sudi_is_diamond_job_work
                and picking.picking_type_code == "incoming"
                and (not picking.sudi_pickup_datetime or picking.state in ("sudi_pickup_pending", "draft"))
            )
        )
        return super(StockPicking, regular_pickings)._autoconfirm_picking()

    def _pre_action_done_hook(self):
        res = super()._pre_action_done_hook()
        if res is not True:
            return res
        self._sudi_validate_job_work_pickings()
        return True

    def action_assign(self):
        # The dispatch WhatsApp used to go out here, before anyone had taken the
        # delivery; it now fires from action_sudi_take_for_delivery.
        return super().action_assign()

    def _action_done(self):
        res = super()._action_done()
        diamond_receipts = self.filtered(
            lambda picking: picking.sudi_is_diamond_job_work
            and picking.picking_type_id.code == "incoming"
            and picking.state == "done"
        )
        diamond_receipts._sudi_sync_billing_details()
        diamond_receipts.filtered(lambda picking: not picking.sudi_delivery_ids)._sudi_create_delivery_from_receipt()

        completed_deliveries = self.filtered(
            lambda picking: picking.sudi_is_diamond_job_work
            and picking.picking_type_code == "outgoing"
            and picking.state == "done"
        )
        if completed_deliveries:
            completed_deliveries._sudi_notify_delivery_completed()

        self._sudi_clear_sms_failure_notifications()
        return res

    def _send_confirmation_email(self):
        # Permanently bypass stock_sms carrier SMS dispatch on delivery order completion to prevent SMS failure popups
        return super(StockPicking, self.with_context(skip_sms=True))._send_confirmation_email()

    def _message_sms_with_template(self, template=None, template_xmlid=None, template_fallback="", partner_ids=None, **kwargs):
        if self.env.context.get("skip_sms"):
            return self.env["mail.message"]
        return super()._message_sms_with_template(
            template=template,
            template_xmlid=template_xmlid,
            template_fallback=template_fallback,
            partner_ids=partner_ids,
            **kwargs,
        )

    def _sudi_clear_sms_failure_notifications(self):
        """Clean up failed SMS notifications for stock pickings so 'SMS Failure:' alerts never persist."""
        if not self:
            return
        sms_notifications = self.env["mail.notification"].sudo().search([
            ("notification_type", "=", "sms"),
            ("notification_status", "in", ["exception", "bounce"]),
            ("mail_message_id.model", "=", "stock.picking"),
            ("mail_message_id.res_id", "in", self.ids),
        ])
        if sms_notifications:
            sms_notifications.write({"notification_status": "canceled", "is_read": True})

        failed_sms = self.env["sms.sms"].sudo().search([
            ("mail_message_id.model", "=", "stock.picking"),
            ("mail_message_id.res_id", "in", self.ids),
            ("state", "=", "error"),
        ])
        if failed_sms:
            failed_sms.write({"state": "canceled"})

    def _sudi_validate_job_work_pickings(self):
        for picking in self.filtered("sudi_is_diamond_job_work"):
            if not picking.partner_id:
                raise ValidationError(_("A diamond job-work transfer must have a customer."))
            for move in picking.move_ids.filtered(lambda m: m.state != "cancel"):
                if not move.product_id:
                    raise ValidationError(_("Every diamond job-work line must have a product."))
                if not move.sudi_job_type_id:
                    raise ValidationError(_("Every diamond job-work line must have a job type."))
                if move.sudi_pcs_qty < 0 or move.sudi_carats < 0:
                    raise ValidationError(_("Pieces/Qty and Carats must be zero or positive."))

    def action_sudi_create_delivery_order(self):
        self.ensure_one()
        self._sudi_check_pickup_delivery_operator_access()
        if not self.sudi_is_diamond_job_work or self.picking_type_code != "incoming":
            raise UserError(_("Delivery Order creation is only supported on diamond job-work receipts."))
        if self.state != "done":
            raise UserError(_("Job work is currently in progress. Complete the job work before creating a delivery order."))
        if self.sudi_delivery_ids:
            return self.action_sudi_view_deliveries()
        self._sudi_create_delivery_from_receipt()
        return self.action_sudi_view_deliveries()

    def _sudi_create_delivery_from_receipt(self):
        for receipt in self:
            if (
                receipt.sudi_is_diamond_job_work
                and receipt.picking_type_code == "incoming"
                and receipt.state != "done"
            ):
                raise UserError(
                    _("Job work is currently in progress. Complete the job work before creating a delivery order.")
                )
            delivery_type = receipt._sudi_get_delivery_picking_type()
            source_location = delivery_type.default_location_src_id or receipt.location_dest_id
            destination_location = (
                delivery_type.default_location_dest_id
                or self.env.ref("stock.stock_location_customers", raise_if_not_found=False)
            )
            if not source_location or not destination_location:
                raise UserError(_("Please configure source and destination locations on the delivery operation type."))

            move_commands = []
            for move in receipt.move_ids.filtered(lambda m: m.state == "done"):
                quantity = move.quantity or move.product_uom_qty
                if float_is_zero(quantity, precision_rounding=move.product_uom.rounding):
                    continue
                move_commands.append(Command.create({
                    "description_picking": move.description_picking or move.product_id.display_name,
                    "product_id": move.product_id.id,
                    "product_uom_qty": quantity,
                    "product_uom": move.product_uom.id,
                    "location_id": source_location.id,
                    "location_dest_id": destination_location.id,
                    "partner_id": receipt.partner_id.id,
                    "company_id": receipt.company_id.id,
                    "move_orig_ids": [Command.link(move.id)],
                    "sudi_sr": move.sudi_sr,
                    "sudi_size": move.sudi_size,
                    "sudi_pcs_qty": move.sudi_pcs_qty or quantity,
                    "sudi_carats": move.sudi_carats,
                    "sudi_job_type_id": move.sudi_job_type_id.id,
                    "sudi_remarks": move.sudi_remarks,
                    "sudi_origin_receipt_move_id": move.id,
                }))

            if not move_commands:
                continue

            delivery = self.create({
                "partner_id": receipt.partner_id.id,
                "picking_type_id": delivery_type.id,
                "location_id": source_location.id,
                "location_dest_id": destination_location.id,
                "origin": receipt.name,
                "company_id": receipt.company_id.id,
                "scheduled_date": fields.Datetime.now(),
                "sudi_is_diamond_job_work": True,
                "sudi_origin_receipt_id": receipt.id,
                "sudi_customer_contact": receipt.sudi_customer_contact,
                "sudi_internal_notes": receipt.sudi_internal_notes,
                "move_ids": move_commands,
            })
            delivery.action_confirm()
            delivery.action_assign()

    def _sudi_pod_is_required(self, requirement):
        """Whether this part of the proof of delivery is mandatory."""
        value = self.env["ir.config_parameter"].sudo().get_param(
            SUDI_POD_PARAMS[requirement], "0"
        )
        return str(value).strip().lower() in ("1", "true", "yes")

    def _sudi_apply_proof_of_delivery(self, receiver_name=None, signature=None, photo_datas=None):
        """Validate and record the proof of delivery.

        Validation runs before the write so a refused delivery leaves no partial
        record behind, and it reads the *incoming* values as well as what is
        already stored -- an operator who captured the signature earlier in the
        form should not have to draw it again.
        """
        Attachment = self.env["ir.attachment"].sudo()
        for delivery in self:
            name = (receiver_name or delivery.sudi_pod_receiver_name or "").strip()
            has_signature = bool(signature or delivery.sudi_pod_signature)
            has_photo = bool(photo_datas or delivery.sudi_pod_attachment_ids)

            if delivery._sudi_pod_is_required("receiver_name") and not name:
                raise UserError(_(
                    "Enter who received %s before marking it delivered.", delivery.name
                ))
            if delivery._sudi_pod_is_required("signature") and not has_signature:
                raise UserError(_(
                    "Capture the receiver's signature for %s before marking it delivered.",
                    delivery.name,
                ))
            if delivery._sudi_pod_is_required("photo") and not has_photo:
                raise UserError(_(
                    "Attach a delivery photo for %s before marking it delivered.",
                    delivery.name,
                ))

            vals = {}
            if name and name != delivery.sudi_pod_receiver_name:
                vals["sudi_pod_receiver_name"] = name
            if signature:
                vals["sudi_pod_signature"] = signature
            if photo_datas:
                offset = len(delivery.sudi_pod_attachment_ids)
                attachments = Attachment.create([
                    {
                        "name": f"delivery_{delivery.name or delivery.id}_{offset + index + 1}.jpg",
                        "type": "binary",
                        "datas": datas,
                        "res_model": delivery._name,
                        "res_id": delivery.id,
                        "mimetype": "image/jpeg",
                    }
                    for index, datas in enumerate(photo_datas)
                    if datas
                ])
                if attachments:
                    vals["sudi_pod_attachment_ids"] = [
                        Command.link(record.id) for record in attachments
                    ]
            if vals:
                delivery.write(vals)
        return True

    def action_sudi_mark_delivered(
        self,
        occurred_at=None,
        receiver_name=None,
        signature=None,
        photo_datas=None,
    ):
        self._sudi_check_pickup_delivery_operator_access()
        invalid_pickings = self.filtered(
            lambda picking: not picking.sudi_is_diamond_job_work
            or picking.picking_type_code != "outgoing"
            or not picking.sudi_origin_receipt_id
            or picking.state in ("done", "cancel")
        )
        if invalid_pickings:
            raise UserError(_("Only active diamond job-work deliveries can be marked delivered."))

        delivered_at = self._sudi_event_datetime(occurred_at)
        self._sudi_apply_proof_of_delivery(
            receiver_name=receiver_name,
            signature=signature,
            photo_datas=photo_datas,
        )
        for delivery in self:
            delivery.with_context(sudi_skip_pickup_scheduled_notify=True).write({
                "sudi_pickup_user_id": delivery.sudi_pickup_user_id.id or self.env.user.id,
                "sudi_pickup_datetime": delivered_at,
            })
        for move in self.move_ids.filtered(lambda stock_move: stock_move.state not in ("done", "cancel")):
            if float_is_zero(move.quantity, precision_rounding=move.product_uom.rounding):
                move.quantity = move.product_uom_qty
            move.picked = True
        result = self.button_validate()
        # button_validate stamps date_done with the server clock; for an event
        # captured offline the delivery happened when the operator said it did.
        delivered = self.filtered(lambda picking: picking.state == "done")
        if delivered:
            delivered.write({"date_done": delivered_at})
            delivered._sudi_post_event_provenance(_("Delivery"), delivered_at)
        return result

    def _sudi_get_delivery_picking_type(self):
        self.ensure_one()
        warehouse = self.picking_type_id.warehouse_id or self.env["stock.warehouse"].search(
            [("company_id", "=", self.company_id.id)],
            limit=1,
        )
        delivery_type = warehouse.out_type_id if warehouse else self.env["stock.picking.type"]
        if not delivery_type:
            delivery_type = self.env["stock.picking.type"].search(
                [("code", "=", "outgoing"), ("company_id", "in", [False, self.company_id.id])],
                limit=1,
            )
        if not delivery_type:
            raise UserError(_("No outgoing delivery operation type is configured for this company."))
        return delivery_type

    def action_sudi_view_deliveries(self):
        self.ensure_one()
        return self._sudi_action_view_pickings(self.sudi_delivery_ids, _("Diamond Deliveries"))

    def action_sudi_view_origin_receipt(self):
        self.ensure_one()
        return self._sudi_action_view_pickings(self.sudi_origin_receipt_id, _("Origin Receipt"))

    def action_sudi_view_billing_log(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("diamond.action_sudi_billing_log")
        field = "receipt_id" if self.picking_type_code == "incoming" else "delivery_id"
        action["domain"] = [(field, "=", self.id)]
        action["context"] = {}
        return action

    def action_sudi_view_invoices(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("account.action_move_out_invoice")
        invoices = self.sudi_invoice_ids
        action["domain"] = [("id", "in", invoices.ids)]
        if len(invoices) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = invoices.id
        return action

    def action_sudi_return_all_without_work(self):
        """Flag every unsettled line of these deliveries as returned without job work."""
        for delivery in self:
            if not delivery._sudi_is_job_work_delivery():
                raise UserError(_("Only diamond job-work deliveries can be returned without job work."))
            moves = delivery.move_ids.filtered(lambda move: move.state != "cancel" and not move._sudi_is_settled())
            if not moves:
                raise UserError(_("Every line of %s is already settled.", delivery.name))
            moves.write({"sudi_returned_without_work": True})
        return True

    def action_sudi_create_invoice(self):
        """Settle every delivered, unbilled line of this receipt (or this delivery) by invoice."""
        self.ensure_one()
        deliveries = self.sudi_delivery_ids if self.picking_type_code == "incoming" else self
        invoices, _statements = deliveries._sudi_settle_deliveries(mode="invoice")
        if not invoices:
            raise UserError(_("There are no delivered uninvoiced diamond job-work lines."))
        action = self.env["ir.actions.actions"]._for_xml_id("account.action_move_out_invoice")
        action["domain"] = [("id", "in", invoices.ids)]
        if len(invoices) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = invoices.id
        return action

    # ------------------------------------------------------------------
    # Settlement engine
    # ------------------------------------------------------------------
    def _sudi_settle_deliveries(self, mode=None, date=None, period=None):
        """Settle the unbilled lines of these deliveries.

        One draft invoice (``mode='invoice'``) or one reference statement
        (``mode='reference'``) is created per customer. ``mode=None`` uses each
        delivery's own ``sudi_settlement_mode``, so a mixed selection produces
        both kinds of document. Returns ``(invoices, statements)``.
        """
        date = date or fields.Date.context_today(self)
        deliveries = self.filtered(lambda picking: picking._sudi_is_job_work_delivery() and picking.state == "done")
        if not deliveries:
            return self.env["account.move"], self.env["sudi.diamond.reference.statement"]
        if len(deliveries.company_id) > 1:
            raise UserError(_("Deliveries from different companies cannot be settled together."))

        # Pick up any price-list change since the receipt was done; manual
        # overrides survive this (see _sudi_sync_billing_details).
        deliveries.sudi_origin_receipt_id.with_context(sudi_skip_billing_sync=True)._sudi_sync_billing_details()

        invoices = self.env["account.move"]
        statements = self.env["sudi.diamond.reference.statement"]
        batches = {}
        for delivery in deliveries:
            delivery_mode = mode or delivery.sudi_settlement_mode or "invoice"
            key = (delivery.partner_id.commercial_partner_id.id, delivery_mode)
            batches.setdefault(key, self.env["stock.picking"])
            batches[key] |= delivery

        for (_partner_id, delivery_mode), batch in batches.items():
            moves = batch.move_ids.filtered(
                lambda move: move.state == "done"
                and move.sudi_job_type_id
                and move.sudi_billing_state in ("to_bill", "no_charge")
            )
            if not moves.filtered(lambda move: move.sudi_billing_state == "to_bill"):
                continue
            if delivery_mode == "reference":
                statements |= batch._sudi_create_reference_statement(moves, date, period)
            else:
                invoices |= batch._sudi_create_invoice(moves, date, period)
        return invoices, statements

    def _sudi_create_invoice(self, moves, date, period=None):
        partner = self[:1].partner_id.commercial_partner_id
        shipping_partner = self[:1].partner_id
        self[:1]._sudi_validate_invoice_partner(partner, shipping_partner)

        receipts = self.sudi_origin_receipt_id
        invoice = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "partner_shipping_id": shipping_partner.id,
            "invoice_date": date,
            "invoice_origin": ", ".join(receipts.sorted("name").mapped("name")),
            "ref": ", ".join(self.sorted("name").mapped("name")),
            "company_id": self[:1].company_id.id,
            "sudi_is_diamond_job_work_invoice": True,
            "sudi_delivery_ids": [Command.set(self.ids)],
            "sudi_billing_period_from": period[0] if period else False,
            "sudi_billing_period_to": period[1] if period else False,
        })

        # One invoice line per (job type, rate): five inscription receipts at
        # the same rate collapse into one line, a receipt at another rate gets
        # its own. The annexure carries the delivery-wise detail.
        chargeable = moves.filtered(lambda move: move.sudi_billing_state == "to_bill")
        groups = {}
        order = []
        for move in chargeable.sorted(key=lambda move: (move.sudi_job_type_id.sequence, move.sudi_job_type_id.id, move.sudi_price_unit)):
            rounding = move.product_uom.rounding if move.product_uom else 0.01
            if float_is_zero(move.sudi_billable_qty, precision_rounding=rounding):
                continue
            key = (move.sudi_job_type_id.id, move.sudi_price_unit)
            if key not in groups:
                groups[key] = self.env["stock.move"]
                order.append(key)
            groups[key] |= move

        line_commands = []
        taxes_by_key = {}
        for key in order:
            group_moves = groups[key]
            job_type = group_moves[:1].sudi_job_type_id
            product = job_type.service_product_id
            if not product:
                raise UserError(_("Please configure a service product on job type %s.", job_type.display_name))
            line_vals = {
                "product_id": product.id,
                "name": job_type._sudi_get_invoice_line_name(),
                "quantity": sum(group_moves.mapped("sudi_billable_qty")),
                "product_uom_id": product.uom_id.id,
                "price_unit": key[1],
                "sudi_job_type_id": job_type.id,
                "sudi_billing_line_ids": [Command.set(group_moves.sudi_billing_line_id.ids)],
                "sudi_stock_move_ids": [Command.set(group_moves.ids)],
            }
            if job_type.tax_ids:
                taxes_by_key[key] = invoice.fiscal_position_id.map_tax(
                    job_type.tax_ids._filter_taxes_by_company(invoice.company_id)
                )
            line_commands.append(Command.create(line_vals))

        if not line_commands:
            invoice.unlink()
            raise UserError(_("The delivered diamond job-work lines have zero invoice quantity."))

        invoice.write({"invoice_line_ids": line_commands})
        invoice.action_update_fpos_values()
        for line in invoice.invoice_line_ids.filtered("sudi_job_type_id"):
            key = (line.sudi_job_type_id.id, line.price_unit)
            if key in taxes_by_key:
                line.tax_ids = taxes_by_key[key]

        settled_moves = invoice.line_ids.sudi_stock_move_ids
        self.env["sudi.diamond.billing.log"]._sudi_log_moves("billed", settled_moves, invoice=invoice)
        no_charge = moves.filtered(lambda move: move.sudi_billing_state == "no_charge")
        if no_charge:
            self.env["sudi.diamond.billing.log"]._sudi_log_moves(
                "billed", no_charge, invoice=invoice, note=_("Returned without job work — listed on annexure, no charge")
            )
        self._sudi_post_billing_chatter(
            _("Draft invoice %s created for this job work.", invoice._get_html_link())
        )
        return invoice

    def _sudi_create_reference_statement(self, moves, date, period=None):
        partner = self[:1].partner_id.commercial_partner_id
        statement = self.env["sudi.diamond.reference.statement"].sudo().create({
            "date": date,
            "partner_id": partner.id,
            "company_id": self[:1].company_id.id,
            "user_id": self.env.user.id,
            "delivery_ids": [Command.set(self.ids)],
            "line_ids": [
                Command.create({
                    "date": fields.Date.to_date(move.picking_id.date_done) if move.picking_id.date_done else date,
                    "delivery_id": move.picking_id.id,
                    "receipt_id": move.picking_id.sudi_origin_receipt_id.id,
                    "stock_move_id": move.id,
                    "billing_line_id": move.sudi_billing_line_id.id,
                    "job_type_id": move.sudi_job_type_id.id,
                    "size": move.sudi_size,
                    "pcs": move.sudi_pcs_qty,
                    "carats": move.sudi_carats,
                    "quantity": move.sudi_billable_qty,
                    "price_unit": move.sudi_price_unit,
                    "amount": move.sudi_billable_amount,
                    "returned_without_work": move.sudi_returned_without_work,
                })
                for move in moves.sorted(key=lambda move: (move.picking_id.date_done or fields.Datetime.now(), move.picking_id.id, move.sudi_sr or 0, move.id))
            ],
        })
        for line in statement.line_ids:
            line.stock_move_id.sudo().sudi_reference_line_id = line
        self.env["sudi.diamond.billing.log"]._sudi_log_moves("reference", statement.line_ids.stock_move_id, statement=statement)
        # Ordinary users must not learn how this was settled: the chatter stays neutral.
        self._sudi_post_billing_chatter(_("Closed for billing."))
        return statement

    def _sudi_validate_invoice_partner(self, partner, shipping_partner):
        self.ensure_one()
        company = self.company_id
        if company.country_code == "IN" and not company.state_id:
            raise UserError(_("Please configure a State on the company before creating an Indian GST invoice."))
        partner_to_check = shipping_partner or partner
        if (
            company.country_code == "IN"
            and (not partner_to_check.country_id or partner_to_check.country_id.code == "IN")
            and not partner_to_check.state_id
        ):
            raise UserError(_("Please configure a State on customer %s before creating an Indian GST invoice.") % partner_to_check.display_name)

    def _sudi_action_view_pickings(self, pickings, name):
        action = self.env["ir.actions.actions"]._for_xml_id("stock.action_picking_tree_all")
        action["name"] = name
        action["domain"] = [("id", "in", pickings.ids)]
        if len(pickings) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = pickings.id
        return action
