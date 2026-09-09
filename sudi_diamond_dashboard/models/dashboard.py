from datetime import datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import date_utils


class SudiDiamondDashboard(models.AbstractModel):
    """Read-only aggregation service behind the neumorphic analytical dashboard.

    Everything the client action draws comes from a single :meth:`get_dashboard_data`
    call so the page paints in one round trip.
    """

    _name = "sudi.diamond.dashboard"
    _description = "Diamond Job Work Analytical Dashboard"

    DASHBOARD_GROUP = "stock.group_stock_manager"

    MEASURE_FIELDS = {"pcs": "sudi_pcs_qty", "carats": "sudi_carats"}
    MEASURE_LABELS = {"pcs": "Pieces", "carats": "Carats"}
    MEASURE_UNITS = {"pcs": "pcs", "carats": "ct"}

    # period -> (trend granularity, how many buckets of history to draw)
    TREND_SPEC = {
        "day": ("day", 14),
        "week": ("week", 12),
        "month": ("month", 12),
        "year": ("year", 5),
    }

    GRAN_DELTA = {
        "day": relativedelta(days=1),
        "week": relativedelta(weeks=1),
        "month": relativedelta(months=1),
        "year": relativedelta(years=1),
    }

    # Ordered stages of a receipt, mapped onto the module's custom picking states.
    STAGE_SPEC = [
        ("pending", "Pick up pending", ["sudi_pickup_pending"], "#fdba74"),
        ("progress", "Job work in progress", ["draft", "waiting", "confirmed", "assigned"], "#f2601a"),
        ("done", "Delivered", ["done"], "#c2410c"),
    ]

    MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    MONTH_FULL = ["January", "February", "March", "April", "May", "June",
                  "July", "August", "September", "October", "November", "December"]

    MAX_CUSTOM_BUCKETS = 400
    TOP_LIMIT = 6

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------
    @api.model
    def _sudi_check_dashboard_access(self):
        """Guard the RPC itself.

        The menu and action carry ``groups=``, but that only hides the entry
        point; without this check any Onfield operator could still read
        company-wide customer and quantity figures over JSON-RPC.
        """
        if not self.env.user.has_group(self.DASHBOARD_GROUP):
            raise AccessError(_(
                "Only Inventory Administrators can open the Diamond Job Work dashboard."
            ))

    # ------------------------------------------------------------------
    # Timezone helpers -- all period boundaries are the user's local midnights
    # ------------------------------------------------------------------
    def _sudi_tz(self):
        return pytz.timezone(self.env.user.tz or "UTC")

    def _sudi_local_today(self):
        return fields.Datetime.context_timestamp(self, fields.Datetime.now()).date()

    def _sudi_to_utc(self, naive_local):
        return self._sudi_tz().localize(naive_local).astimezone(pytz.UTC).replace(tzinfo=None)

    # ------------------------------------------------------------------
    # Range resolution
    # ------------------------------------------------------------------
    def _sudi_custom_granularity(self, start_date, end_date):
        span = (end_date - start_date).days + 1
        if span <= 31:
            return "day"
        if span <= 182:
            return "week"
        if span <= 1095:
            return "month"
        return "year"

    def _sudi_resolve_range(self, period, date_from=None, date_to=None):
        """Return ``(start_date, end_date, granularity, buckets, period)``.

        ``end_date`` is inclusive; callers turn it into an exclusive datetime.
        """
        today = self._sudi_local_today()

        if period == "custom":
            start_date = fields.Date.to_date(date_from) or today
            end_date = fields.Date.to_date(date_to) or today
            if end_date < start_date:
                start_date, end_date = end_date, start_date
            gran = self._sudi_custom_granularity(start_date, end_date)
            return start_date, end_date, gran, 0, "custom"

        if period not in self.TREND_SPEC:
            period = "month"

        if period == "day":
            start_date = today
            end_date = today
        elif period == "week":
            start_date = date_utils.start_of(today, "week")
            end_date = start_date + timedelta(days=6)
        elif period == "year":
            start_date = date_utils.start_of(today, "year")
            end_date = start_date + relativedelta(years=1) - timedelta(days=1)
        else:
            start_date = date_utils.start_of(today, "month")
            end_date = start_date + relativedelta(months=1) - timedelta(days=1)

        gran, buckets = self.TREND_SPEC[period]
        return start_date, end_date, gran, buckets, period

    def _sudi_bucket_start(self, value, gran):
        if gran in ("week", "month", "year"):
            return date_utils.start_of(value, gran)
        return value

    def _sudi_trend_buckets(self, period, start_date, end_date, gran, buckets):
        """The bucket start dates the trend chart draws, gaps included."""
        delta = self.GRAN_DELTA[gran]
        out = []
        if period == "custom":
            cur = self._sudi_bucket_start(start_date, gran)
            while cur <= end_date and len(out) < self.MAX_CUSTOM_BUCKETS:
                out.append(cur)
                cur += delta
            return out or [self._sudi_bucket_start(start_date, gran)]

        last = self._sudi_bucket_start(start_date, gran)
        cur = last - delta * (buckets - 1)
        while cur <= last:
            out.append(cur)
            cur += delta
        return out

    def _sudi_previous_range(self, period, start_date, end_date):
        """The immediately preceding window of equal length, for the deltas."""
        if period == "day":
            return start_date - timedelta(days=1), start_date - timedelta(days=1)
        if period == "week":
            return start_date - timedelta(days=7), start_date - timedelta(days=1)
        if period == "month":
            prev_start = start_date - relativedelta(months=1)
            return prev_start, start_date - timedelta(days=1)
        if period == "year":
            prev_start = start_date - relativedelta(years=1)
            return prev_start, start_date - timedelta(days=1)
        span = (end_date - start_date).days + 1
        return start_date - timedelta(days=span), start_date - timedelta(days=1)

    # ------------------------------------------------------------------
    # Domains
    # ------------------------------------------------------------------
    def _sudi_date_bounds(self, start_date, end_date_exclusive):
        start = self._sudi_to_utc(datetime.combine(start_date, time.min))
        end = self._sudi_to_utc(datetime.combine(end_date_exclusive, time.min))
        return start, end

    def _sudi_move_domain(self, start_date, end_date):
        start, end = self._sudi_date_bounds(start_date, end_date + timedelta(days=1))
        return [
            ("picking_id.sudi_is_diamond_job_work", "=", True),
            ("picking_id.picking_type_code", "=", "incoming"),
            ("picking_id.state", "!=", "cancel"),
            ("company_id", "in", self.env.companies.ids),
            ("sudi_receipt_date", ">=", start),
            ("sudi_receipt_date", "<", end),
        ]

    def _sudi_picking_domain(self, start_date, end_date):
        start, end = self._sudi_date_bounds(start_date, end_date + timedelta(days=1))
        return [
            ("sudi_is_diamond_job_work", "=", True),
            ("picking_type_code", "=", "incoming"),
            ("state", "!=", "cancel"),
            ("company_id", "in", self.env.companies.ids),
            ("sudi_receipt_date", ">=", start),
            ("sudi_receipt_date", "<", end),
        ]

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------
    def _sudi_totals(self, domain):
        rows = self.env["stock.move"]._read_group(
            domain,
            [],
            [
                "sudi_pcs_qty:sum",
                "sudi_carats:sum",
                "picking_id:count_distinct",
                "sudi_customer_id:count_distinct",
            ],
        )
        if not rows:
            return {"pcs": 0.0, "carats": 0.0, "jobs": 0, "customers": 0}
        pcs, carats, jobs, customers = rows[0]
        return {
            "pcs": pcs or 0.0,
            "carats": carats or 0.0,
            "jobs": jobs or 0,
            "customers": customers or 0,
        }

    def _sudi_bucket_label(self, value, gran, is_first):
        if gran == "year":
            return str(value.year)
        if gran == "month":
            label = self.MONTH_ABBR[value.month - 1]
            if is_first or value.month == 1:
                label += " '%s" % str(value.year)[2:]
            return label
        return "%d %s" % (value.day, self.MONTH_ABBR[value.month - 1])

    def _sudi_trend(self, period, start_date, end_date, gran, buckets, qty_field):
        bucket_dates = self._sudi_trend_buckets(period, start_date, end_date, gran, buckets)
        window_end = bucket_dates[-1] + self.GRAN_DELTA[gran] - timedelta(days=1)
        domain = self._sudi_move_domain(bucket_dates[0], max(window_end, end_date))

        rows = self.env["stock.move"]._read_group(
            domain,
            ["sudi_receipt_date:%s" % gran],
            ["%s:sum" % qty_field, "picking_id:count_distinct"],
        )

        by_bucket = {}
        for bucket, qty, receipts in rows:
            if not bucket:
                continue
            key = bucket.date() if isinstance(bucket, datetime) else bucket
            by_bucket[key] = (qty or 0.0, receipts or 0)

        labels, values, receipts = [], [], []
        for index, bucket in enumerate(bucket_dates):
            qty, count = by_bucket.get(bucket, (0.0, 0))
            labels.append(self._sudi_bucket_label(bucket, gran, index == 0))
            values.append(round(qty, 3))
            receipts.append(count)

        return {
            "labels": labels,
            "values": values,
            "receipts": receipts,
            "granularity": gran,
        }

    def _sudi_rank(self, domain, groupby, qty_field, total, with_receipts=False):
        aggregates = ["%s:sum" % qty_field]
        if with_receipts:
            aggregates.append("picking_id:count_distinct")

        rows = self.env["stock.move"]._read_group(domain, [groupby], aggregates)

        entries = []
        for row in rows:
            record = row[0]
            qty = row[1] or 0.0
            if not qty:
                continue
            entries.append({
                "id": record.id if record else False,
                "name": record.display_name if record else _("Unassigned"),
                "value": round(qty, 3),
                "share": (qty / total) if total else 0.0,
                "receipts": (row[2] or 0) if with_receipts else 0,
            })

        entries.sort(key=lambda entry: entry["value"], reverse=True)
        return entries[: self.TOP_LIMIT]

    def _sudi_stages(self, picking_domain):
        rows = self.env["stock.picking"]._read_group(picking_domain, ["state"], ["__count"])
        counts = {state: count for state, count in rows}

        stages, total = [], 0
        for key, label, states, color in self.STAGE_SPEC:
            count = sum(counts.get(state, 0) for state in states)
            total += count
            stages.append({"key": key, "name": label, "count": count, "color": color, "share": 0.0})

        for stage in stages:
            stage["share"] = (stage["count"] / total) if total else 0.0

        open_jobs = sum(stage["count"] for stage in stages if stage["key"] != "done")
        return stages, open_jobs

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------
    def _sudi_format_date(self, value, with_year=True):
        text = "%d %s" % (value.day, self.MONTH_ABBR[value.month - 1])
        return "%s %d" % (text, value.year) if with_year else text

    def _sudi_scope_label(self, period, start_date, end_date):
        today = self._sudi_local_today()
        if period == "day":
            label = self._sudi_format_date(start_date)
            return _("Today · %s", label) if start_date == today else label
        if period == "month":
            return "%s %d" % (self.MONTH_FULL[start_date.month - 1], start_date.year)
        if period == "year":
            return str(start_date.year)

        same_year = start_date.year == end_date.year
        span = "%s – %s" % (
            self._sudi_format_date(start_date, with_year=not same_year),
            self._sudi_format_date(end_date),
        )
        if period == "week":
            return _("This week · %s", span) if start_date <= today <= end_date else span
        return span

    def _sudi_delta(self, current, previous):
        if not previous:
            return None
        return round((current - previous) / previous * 100.0, 1)

    # ------------------------------------------------------------------
    # Public RPC
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, period="month", measure="pcs", date_from=None, date_to=None):
        self._sudi_check_dashboard_access()

        if measure not in self.MEASURE_FIELDS:
            measure = "pcs"
        qty_field = self.MEASURE_FIELDS[measure]

        start_date, end_date, gran, buckets, period = self._sudi_resolve_range(
            period, date_from, date_to
        )

        move_domain = self._sudi_move_domain(start_date, end_date)
        picking_domain = self._sudi_picking_domain(start_date, end_date)

        totals = self._sudi_totals(move_domain)
        qty = totals["carats"] if measure == "carats" else totals["pcs"]

        prev_start, prev_end = self._sudi_previous_range(period, start_date, end_date)
        prev_totals = self._sudi_totals(self._sudi_move_domain(prev_start, prev_end))
        prev_qty = prev_totals["carats"] if measure == "carats" else prev_totals["pcs"]

        stages, open_jobs = self._sudi_stages(picking_domain)

        return {
            "period": period,
            "measure": measure,
            "measure_label": self.MEASURE_LABELS[measure],
            "unit": self.MEASURE_UNITS[measure],
            "scope_label": self._sudi_scope_label(period, start_date, end_date),
            "date_from": fields.Date.to_string(start_date),
            "date_to": fields.Date.to_string(end_date),
            "kpis": {
                "jobs": totals["jobs"],
                "qty": round(qty, 3),
                "avg": round(qty / totals["jobs"], 3) if totals["jobs"] else 0.0,
                "customers": totals["customers"],
            },
            "deltas": {
                "jobs": self._sudi_delta(totals["jobs"], prev_totals["jobs"]),
                "qty": self._sudi_delta(qty, prev_qty),
            },
            "trend": self._sudi_trend(period, start_date, end_date, gran, buckets, qty_field),
            "stages": stages,
            "open_jobs": open_jobs,
            "job_types": self._sudi_rank(move_domain, "sudi_job_type_id", qty_field, qty),
            "customers": self._sudi_rank(
                move_domain, "sudi_customer_id", qty_field, qty, with_receipts=True
            ),
        }

    @api.model
    def get_drilldown_action(self, kind, record_id=None, period="month", date_from=None, date_to=None):
        """Open the receipts behind a bar the user clicked."""
        self._sudi_check_dashboard_access()

        start_date, end_date, _gran, _buckets, period = self._sudi_resolve_range(
            period, date_from, date_to
        )
        domain = self._sudi_picking_domain(start_date, end_date)
        name = _("Job Work Receipts")

        if kind == "job_type":
            domain.append(("move_ids.sudi_job_type_id", "=", record_id or False))
            job_type = self.env["sudi.diamond.job.type"].browse(record_id) if record_id else None
            name = job_type.display_name if job_type else _("Unassigned job type")
        elif kind == "customer":
            domain.append(("partner_id.commercial_partner_id", "=", record_id or False))
            partner = self.env["res.partner"].browse(record_id) if record_id else None
            name = partner.display_name if partner else _("Unassigned customer")
        elif kind == "stage":
            states = next(
                (spec[2] for spec in self.STAGE_SPEC if spec[0] == record_id), None
            )
            if states:
                domain.append(("state", "in", states))
                name = next(spec[1] for spec in self.STAGE_SPEC if spec[0] == record_id)

        # Reuse the module's own receipts action rather than returning a bare dict:
        # the web client's _preprocessAction() calls action.views.map() with no
        # guard, so an action without a resolved "views" list raises
        # "can't access property map, action.views is undefined". Going through
        # _for_xml_id() also lands the drilldown on the Diamond list, with its
        # Sr / Size / Carats / Job Type columns, instead of the plain picking list.
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "diamond.action_sudi_diamond_receipts"
        )
        action.update({
            "name": name,
            "domain": domain,
            "context": {"create": False, "restricted_picking_type_code": "incoming"},
            "target": "current",
        })
        return action
