"""Worklist scopes, their payloads, and the delta pull.

Three role-scoped worklists, each a flat denormalised document. The phone is
not a replica of the ORM, so nothing here exposes ``stock.picking`` generically:
a scope is an explicit domain this test suite can pin, and a payload is a fixed
shape the client's local schema mirrors.

See docs/MOBILE_API_PLAN.md sections 6.1 and 6.4, and
docs/FLUTTER_INTEGRATION_BRIEF.md section 9 for the documents themselves.
"""

from odoo import api, fields, models
from odoo.fields import Domain

SUDI_SYNC_SCOPES = ("pickup", "delivery", "jobwork")

DEFAULT_PULL_LIMIT = 200
MAX_PULL_LIMIT = 500


def _iso(value):
    """Naive UTC, ISO-8601 with a ``T``, or None.

    Odoo's own ``to_string`` uses a space separator; the client contract is
    ISO, so the separator is not left to chance.
    """
    return value.isoformat() if value else None


def _ref(value):
    """A many2one as ``search_read`` returns it -> ``{id, name}`` or None."""
    if not value:
        return None
    return {"id": value[0], "name": value[1]}


class StockPicking(models.Model):
    _name = "stock.picking"
    _inherit = ["stock.picking", "sudi.sync.source"]

    def _sudi_sync_pickings(self):
        return self

    # ------------------------------------------------------------------
    # scopes
    # ------------------------------------------------------------------
    @api.model
    def _sudi_sync_jobwork_extra_domain(self):
        """Hook for narrowing job work to a department or a person.

        Open question Q5 in the plan: today the Job Work app shows every
        receipt in progress, so the default keeps that and the decision can be
        taken later without touching the pull.
        """
        return Domain.TRUE

    @api.model
    def _sudi_sync_scope_domain(self, scope):
        """The explicit domain for a scope.

        Explicit, and not derived from the ``rule_sudi_operator_*`` record
        rules, for the reason in plan section 6.4: one of those rules is a
        moving time window, and a protocol whose notion of "in scope" changes
        at midnight with no write to detect is a protocol that silently strands
        records on a phone. The rules stay in place as defence in depth for
        ordinary reads.
        """
        domain = Domain([("sudi_is_diamond_job_work", "=", True)])
        if scope == "pickup":
            start, end = self.env.user._sudi_operator_today_bounds_utc()
            awaiting = Domain([("state", "=", "sudi_pickup_pending")])
            # The tail of what this operator collected today. Without it the
            # receipt vanishes from their phone the instant they confirm, so
            # they cannot review the round or notice a parcel they missed.
            # Symmetric with the delivery scope below.
            mine_today = Domain([
                ("sudi_pickup_user_id", "=", self.env.user.id),
                ("sudi_pickup_datetime", ">=", start),
                ("sudi_pickup_datetime", "<", end),
            ])
            return domain & Domain([
                ("picking_type_id.code", "=", "incoming"),
            ]) & (awaiting | mine_today)
        if scope == "delivery":
            start, end = self.env.user._sudi_operator_today_bounds_utc()
            active = Domain([("sudi_delivery_stage", "in", ("awaiting", "out"))])
            # The tail of work the operator finished today, so their own list
            # still shows what they delivered this morning.
            mine_today = Domain([
                ("sudi_delivery_stage", "=", "delivered"),
                ("sudi_pickup_user_id", "=", self.env.user.id),
                ("date_done", ">=", start),
                ("date_done", "<", end),
            ])
            return domain & Domain([
                ("picking_type_id.code", "=", "outgoing"),
                ("sudi_origin_receipt_id", "!=", False),
            ]) & (active | mine_today)
        if scope == "jobwork":
            return domain & Domain([
                ("picking_type_id.code", "=", "incoming"),
                ("state", "=", "assigned"),
            ]) & self._sudi_sync_jobwork_extra_domain()
        raise ValueError(f"Unknown sync scope {scope!r}")

    @api.model
    def _sudi_sync_scope_ids(self, scope, extra_domain=None, limit=None):
        """Ids in a scope, under sudo and the explicit domain, oldest first."""
        domain = self._sudi_sync_scope_domain(scope)
        if extra_domain is not None:
            domain = domain & Domain(extra_domain)
        return self.sudo().search(domain, order="id", limit=limit).ids

    # ------------------------------------------------------------------
    # payloads
    # ------------------------------------------------------------------
    @api.model
    def _sudi_sync_item_lines(self, picking_ids):
        """``{picking_id: [ItemLine, ...]}`` in one query, not one per picking."""
        if not picking_ids:
            return {}
        moves = self.env["stock.move"].sudo().search_read(
            [("picking_id", "in", list(picking_ids)), ("state", "!=", "cancel")],
            [
                "picking_id", "sudi_sr", "product_id", "sudi_size",
                "sudi_pcs_qty", "sudi_carats", "sudi_job_type_id", "sudi_remarks",
            ],
            order="sudi_sr, id",
        )
        grouped = {}
        for move in moves:
            grouped.setdefault(move["picking_id"][0], []).append({
                "sr": move["sudi_sr"],
                "product": move["product_id"][1] if move["product_id"] else None,
                "size": move["sudi_size"] or None,
                "pcs": move["sudi_pcs_qty"],
                "carats": move["sudi_carats"],
                "job_type": _ref(move["sudi_job_type_id"]),
                "remarks": move["sudi_remarks"] or None,
            })
        return grouped

    _SUDI_SYNC_FIELDS = {
        "pickup": [
            "name", "partner_id", "sudi_customer_contact", "sudi_pickup_address",
            "scheduled_date", "create_date", "sudi_jangad_page_count",
            "state", "sudi_pickup_datetime", "sudi_pickup_user_id",
        ],
        "delivery": [
            "name", "partner_id", "sudi_customer_contact", "sudi_partner_address",
            "sudi_delivery_stage", "sudi_pickup_user_id",
            "sudi_out_for_delivery_datetime", "sudi_origin_receipt_id",
            "date_done", "sudi_pod_receiver_name",
        ],
        "jobwork": [
            "name", "partner_id", "scheduled_date", "state",
            "sudi_current_department_id", "sudi_involved_department_ids",
            "sudi_total_hours_spent", "timer_start", "sudi_jangad_page_count",
        ],
    }

    @api.model
    def _sudi_sync_payloads(self, scope, picking_ids, revisions):
        """The documents for ``picking_ids``, in ``picking_ids`` order.

        One ``search_read`` for the pickings and one for their items. No browse
        loop, and no per-record compute that is not already batched by the ORM.
        """
        if not picking_ids:
            return []
        rows = self.sudo().with_context(active_test=False).search_read(
            # active_test off: a cancelled pickup is archived, and an intent
            # that cancels one still has to hand back the document it acted on.
            [("id", "in", list(picking_ids))],
            self._SUDI_SYNC_FIELDS[scope],
        )
        by_id = {row["id"]: row for row in rows}
        related = {
            "items": (
                self._sudi_sync_item_lines(picking_ids)
                if scope in ("delivery", "jobwork")
                else {}
            ),
            "departments": self._sudi_sync_department_names(rows),
        }
        builder = getattr(self, f"_sudi_sync_{scope}_doc")
        payloads = []
        for picking_id in picking_ids:
            row = by_id.get(picking_id)
            if not row:
                # Vanished between the scope search and here. The client hears
                # about it on the next pull as a `gone`.
                continue
            payloads.append(builder(row, revisions.get(picking_id, 0), related))
        return payloads

    @api.model
    def _sudi_sync_department_names(self, rows):
        """``{job_type_id: name}`` for every department mentioned in ``rows``.

        A many2many comes back from ``search_read`` as bare ids, and resolving
        each row's names on its own would be a query per receipt.
        """
        wanted = set()
        for row in rows:
            wanted.update(row.get("sudi_involved_department_ids") or [])
        if not wanted:
            return {}
        return {
            record["id"]: record["display_name"]
            for record in self.env["sudi.diamond.job.type"]
            .sudo()
            .search_read([("id", "in", sorted(wanted))], ["display_name"])
        }

    @api.model
    def _sudi_sync_pickup_doc(self, row, rev, related):
        return {
            "id": row["id"],
            "rev": rev,
            "name": row["name"],
            # Two lists in one scope, so the client can separate what is still
            # to collect from what it already has.
            "stage": (
                "awaiting" if row["state"] == "sudi_pickup_pending" else "collected"
            ),
            "customer": _ref(row["partner_id"]),
            "contact_phone": row["sudi_customer_contact"] or None,
            "pickup_address": row["sudi_pickup_address"] or None,
            "scheduled_date": _iso(row["scheduled_date"]),
            "created_at": _iso(row["create_date"]),
            "collected_at": _iso(row["sudi_pickup_datetime"]),
            "collected_by": _ref(row["sudi_pickup_user_id"]),
            "jangad_pages": row["sudi_jangad_page_count"],
        }

    @api.model
    def _sudi_sync_delivery_doc(self, row, rev, related):
        return {
            "id": row["id"],
            "rev": rev,
            "name": row["name"],
            "customer": _ref(row["partner_id"]),
            "contact_phone": row["sudi_customer_contact"] or None,
            "address": row["sudi_partner_address"] or None,
            "stage": row["sudi_delivery_stage"] or None,
            "taken_by": _ref(row["sudi_pickup_user_id"]),
            "out_since": _iso(row["sudi_out_for_delivery_datetime"]),
            "origin_receipt": _ref(row["sudi_origin_receipt_id"]),
            "delivered_at": _iso(row["date_done"]),
            "received_by": row["sudi_pod_receiver_name"] or None,
            "items": related["items"].get(row["id"], []),
        }

    @api.model
    def _sudi_sync_jobwork_doc(self, row, rev, related):
        return {
            "id": row["id"],
            "rev": rev,
            "name": row["name"],
            "customer": _ref(row["partner_id"]),
            "scheduled_date": _iso(row["scheduled_date"]),
            "state": row["state"],
            "current_department": _ref(row["sudi_current_department_id"]),
            "involved_departments": [
                {"id": job_type_id, "name": related["departments"].get(job_type_id)}
                for job_type_id in row["sudi_involved_department_ids"] or []
            ],
            "total_hours": row["sudi_total_hours_spent"],
            "timer": {
                "running": bool(row["timer_start"]),
                "started_at": _iso(row["timer_start"]),
            },
            "jangad_pages": row["sudi_jangad_page_count"],
            "items": related["items"].get(row["id"], []),
        }

    # ------------------------------------------------------------------
    # the pull
    # ------------------------------------------------------------------
    @api.model
    def _sudi_sync_pull(
        self, scopes=None, cursor=None, limit=DEFAULT_PULL_LIMIT,
        after_scope=None, after_id=None,
    ):
        """Everything a device needs to catch up.

        Must be called on an environment bound to the requesting *user*: the
        identity decides the delivery scope's "delivered by me today" tail. The
        scope searches themselves run ``sudo()`` against the explicit domains
        of ``_sudi_sync_scope_domain``.

        ``cursor`` absent or older than the log's retention answers
        ``full_resync``, and the client wipes its tables and takes what follows
        as the whole world. A full resync pages with ``after_scope`` /
        ``after_id``; an incremental pull pages by advancing ``cursor``.
        """
        scopes = [scope for scope in (scopes or SUDI_SYNC_SCOPES) if scope in SUDI_SYNC_SCOPES]
        limit = max(1, min(int(limit or DEFAULT_PULL_LIMIT), MAX_PULL_LIMIT))
        Change = self.env["sudi.sync.change"].sudo()

        continuation = bool(after_scope)
        full = continuation or Change._sudi_is_cursor_stale(cursor)
        # On a continuation the client hands back the cursor page 1 gave it, so
        # changes committed while it pages are not skipped: they carry ids
        # above that cursor and arrive on the next incremental pull.
        next_cursor = int(cursor or 0) if continuation else Change._sudi_latest_visible_id()

        result = {
            "cursor": next_cursor,
            "full_resync": full,
            "has_more": False,
            "next_after_scope": None,
            "next_after_id": None,
            "server_time": _iso(fields.Datetime.now()),
            "scopes": {},
        }
        if full:
            self._sudi_sync_pull_full(result, scopes, limit, after_scope, after_id)
        else:
            self._sudi_sync_pull_delta(result, scopes, cursor, next_cursor, limit)
        return result

    @api.model
    def _sudi_sync_pull_full(self, result, scopes, limit, after_scope, after_id):
        """Everything in scope, paged by picking id within a shared budget.

        Scopes are filled in order until the budget runs out; the response then
        names the scope and the id to resume from, and the client calls again
        with them plus the cursor it was given. Scopes already delivered come
        back empty rather than missing, so the client never has to know which
        page it is on.
        """
        budget = limit
        started = after_scope is None
        for index, scope in enumerate(scopes):
            if not started:
                result["scopes"][scope] = {"upserts": [], "gone": []}
                if scope != after_scope:
                    continue
                started = True
            if budget <= 0:
                # The previous scope used the budget exactly: resume this one
                # from its start.
                self._sudi_sync_pull_suspend(result, scopes, index, scope, 0)
                return
            extra = (
                Domain([("id", ">", after_id)])
                if scope == after_scope and after_id
                else None
            )
            ids = self._sudi_sync_scope_ids(scope, extra_domain=extra, limit=budget + 1)
            truncated = len(ids) > budget
            if truncated:
                ids = ids[:budget]
            revisions = self.env["sudi.sync.change"].sudo()._sudi_revisions_for(
                "stock.picking", ids
            )
            result["scopes"][scope] = {
                "upserts": self._sudi_sync_payloads(scope, ids, revisions),
                "gone": [],
            }
            budget -= len(ids)
            if truncated:
                self._sudi_sync_pull_suspend(result, scopes, index + 1, scope, ids[-1])
                return
        for scope in scopes:
            result["scopes"].setdefault(scope, {"upserts": [], "gone": []})

    @api.model
    def _sudi_sync_pull_suspend(self, result, scopes, from_index, scope, after_id):
        """Mark the response as partial and say where to resume."""
        result["has_more"] = True
        result["next_after_scope"] = scope
        result["next_after_id"] = after_id
        for remaining in scopes[from_index:]:
            result["scopes"].setdefault(remaining, {"upserts": [], "gone": []})

    @api.model
    def _sudi_sync_pull_delta(self, result, scopes, cursor, next_cursor, limit):
        """Only what changed, classified into upserts and scope exits."""
        Change = self.env["sudi.sync.change"].sudo()
        changed = Change._sudi_changed_res_ids("stock.picking", cursor, next_cursor, limit=limit + 1)
        if len(changed) > limit:
            changed = changed[:limit]
            result["has_more"] = True
            # Resume from the last change actually handed over.
            result["cursor"] = changed[-1][1]
        revisions = dict(changed)
        changed_ids = set(revisions)

        for scope in scopes:
            if not changed_ids:
                result["scopes"][scope] = {"upserts": [], "gone": []}
                continue
            extra = Domain([("id", "in", sorted(changed_ids))])
            in_scope = self._sudi_sync_scope_ids(scope, extra_domain=extra)
            result["scopes"][scope] = {
                "upserts": self._sudi_sync_payloads(scope, in_scope, revisions),
                # Everything that changed and is not in this scope. That
                # over-reports -- a receipt awaiting pickup was never in the
                # delivery scope either -- but deleting a row the client does
                # not hold is a no-op, while failing to report a real scope exit
                # leaves work stranded on a phone forever.
                "gone": sorted(changed_ids - set(in_scope)),
            }
