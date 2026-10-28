"""In-app notices: the bell in the staff app.

One row per person per event, so "read" is a property of the row and the list
a phone pulls is simply its owner's rows. Push (FCM) plugs into the same rows
later: a sender reads what was raised here, so the two can never disagree about
what somebody was told.

Raising a notice is a side effect of a business action, and must never be the
reason that action fails. :meth:`_sudi_raise` therefore works in a savepoint,
under ``sudo()``, and logs rather than raises. It runs in the same transaction
as the action, so a notice about something that was rolled back is rolled back
with it.
"""

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

PARAM_RETENTION_DAYS = "sudi_sync.notice_retention_days"
DEFAULT_RETENTION_DAYS = 30

# Who hears about what, and which worklist the app opens when they tap it.
NOTICE_KINDS = {
    "pickup_new": ("diamond.group_sudi_pickup_delivery_operator", "pickup"),
    "office_validate": ("diamond.group_sudi_office_validator", "office"),
    "jobwork_ready": ("diamond.group_sudi_job_work_user", "jobwork"),
    "department_transfer": ("diamond.group_sudi_job_work_user", "jobwork"),
    "delivery_ready": ("diamond.group_sudi_pickup_delivery_operator", "delivery"),
}


class SudiAppNotice(models.Model):
    _name = "sudi.app.notice"
    _description = "Sudi App Notice"
    _order = "id desc"

    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade", readonly=True
    )
    kind = fields.Selection(
        [
            ("pickup_new", "New pickup"),
            ("office_validate", "Jangad to validate"),
            ("jobwork_ready", "Job work ready"),
            ("department_transfer", "Department transfer"),
            ("delivery_ready", "Delivery ready"),
        ],
        required=True,
        readonly=True,
    )
    title = fields.Char(required=True, readonly=True)
    body = fields.Text(readonly=True)
    picking_id = fields.Many2one(
        "stock.picking", index=True, ondelete="cascade", readonly=True
    )
    scope = fields.Char(
        readonly=True,
        help="The worklist the app opens when the notice is tapped.",
    )
    read_at = fields.Datetime(readonly=True, index=True)

    # ------------------------------------------------------------------
    # raising
    # ------------------------------------------------------------------
    @api.model
    def _sudi_recipients(self, kind):
        """The active internal users who hold the role ``kind`` is meant for."""
        group = self.env.ref(NOTICE_KINDS[kind][0], raise_if_not_found=False)
        if not group:
            return self.env["res.users"]
        return self.env["res.users"].sudo().search([
            ("all_group_ids", "in", group.ids),
            ("share", "=", False),
        ])

    @api.model
    def _sudi_raise(self, kind, pickings, **extra):
        """Tell everyone in ``kind``'s role about each of ``pickings``.

        Never the actor: whoever caused the event already knows, and a bell
        that rings for your own taps is a bell nobody looks at. ``env.uid`` is
        the actor even under ``sudo()``, which is why it is read before the
        switch.

        The pickings are read under ``sudo()``: by the time a notice is raised
        the record has often moved somewhere the actor can no longer read -- a
        collected pickup belongs to the office -- and the same cache-dependent
        ``AccessError`` that once broke ``_sudi_notify_pickup_confirmed`` must
        not break this.

        Returns the notices created, or an empty recordset if anything went
        wrong -- in which case the failure is logged and the action carries on.
        """
        if not pickings:
            return self.browse()
        actor_id = self.env.uid
        # Pending business writes are flushed *outside* the savepoint: a
        # failure in them belongs to the action, and swallowing it here would
        # quietly lose the action's own data.
        self.env.flush_all()
        try:
            with self.env.cr.savepoint(flush=False):
                recipients = self._sudi_recipients(kind).filtered(
                    lambda user: user.id != actor_id
                )
                if not recipients:
                    return self.browse()
                scope = NOTICE_KINDS[kind][1]
                vals_list = []
                for picking in pickings.sudo():
                    title, body = self._sudi_text(kind, picking, **extra)
                    vals_list.extend(
                        {
                            "user_id": user.id,
                            "kind": kind,
                            "title": title,
                            "body": body or False,
                            "picking_id": picking.id,
                            "scope": scope,
                        }
                        for user in recipients
                        if not picking.company_id or picking.company_id in user.company_ids
                    )
                if not vals_list:
                    return self.browse()
                notices = self.sudo().create(vals_list)
                notices.flush_recordset()
                return notices
        except Exception:
            _logger.exception(
                "Could not raise %s notices for %s; the action itself went ahead",
                kind, pickings.ids,
            )
            self.invalidate_model(flush=False)
            return self.browse()

    @api.model
    def _sudi_customer_line(self, picking):
        """``Kiran Gems · 12, Mahidharpura`` -- who and where, for a body."""
        parts = [
            picking.partner_id.display_name or picking.sudi_customer_contact or "",
            (picking.sudi_pickup_address or picking.sudi_partner_address or "")
            .replace("\n", ", "),
        ]
        return " · ".join(part for part in parts if part)

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    def _sudi_mark_read(self):
        """Mark these read, leaving any already read alone. Returns how many."""
        unread = self.filtered(lambda notice: not notice.read_at)
        if unread:
            unread.sudo().write({"read_at": fields.Datetime.now()})
        return len(unread)

    @api.model
    def _sudi_unread_count(self, user):
        return self.sudo().search_count([
            ("user_id", "=", user.id), ("read_at", "=", False),
        ])

    # ------------------------------------------------------------------
    # retention
    # ------------------------------------------------------------------
    @api.model
    def _cron_prune(self):
        """Drop notices past the retention window. A bell is not an archive."""
        days = self.env["ir.config_parameter"].sudo().get_int(
            PARAM_RETENTION_DAYS, DEFAULT_RETENTION_DAYS
        )
        if days <= 0:
            days = DEFAULT_RETENTION_DAYS
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        stale = self.sudo().search([("create_date", "<", cutoff)])
        count = len(stale)
        if stale:
            stale.unlink()
            _logger.info("sudi.app.notice: pruned %s notices older than %s days", count, days)
        return count

    @api.model
    def _sudi_text(self, kind, picking, **extra):
        """The title and body for one notice, as the person will read them."""
        name = picking.name or ""
        if kind == "pickup_new":
            return _("New pickup: %s", name), self._sudi_customer_line(picking)
        if kind == "office_validate":
            collector = picking.sudi_pickup_user_id.name or extra.get("actor") or ""
            return (
                _("Jangad to validate: %s", name),
                _("Collected by %(who)s. Validate the jangad and fill in the item table.",
                  who=collector),
            )
        if kind == "jobwork_ready":
            return (
                _("Job work ready: %s", name),
                picking.partner_id.display_name or "",
            )
        if kind == "department_transfer":
            return (
                _("Moved to %(department)s: %(name)s",
                  department=extra.get("department") or "", name=name),
                _("Transferred by %s.", extra.get("actor") or ""),
            )
        if kind == "delivery_ready":
            return _("Ready for delivery: %s", name), self._sudi_customer_line(picking)
        raise ValueError(f"Unknown notice kind {kind!r}")
