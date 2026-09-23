"""The change log a device reads deltas from, and the cursor over it.

Why a log rather than ``write_date > since``:

* ``write_date`` is not indexed on ``stock_picking``, and indexing Odoo's
  busiest table to serve a phone is the wrong trade;
* an unlink or an archival leaves nothing behind to query;
* a *move* changing has to invalidate its *picking's* payload, which the log
  records directly instead of making every client join.

Rows are written once per affected picking per transaction, at precommit, not
once per ``write``. See ``sudi.sync.source``.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

PARAM_LAG_SECONDS = "sudi_sync.visibility_lag_seconds"
PARAM_RETENTION_DAYS = "sudi_sync.retention_days"
PARAM_PRUNED_THROUGH = "sudi_sync.pruned_through"

DEFAULT_LAG_SECONDS = 2
DEFAULT_RETENTION_DAYS = 30


class SudiSyncChange(models.Model):
    _name = "sudi.sync.change"
    _description = "Sudi Offline Sync Change Log"
    _order = "id"
    # High-volume, append-only, and never edited: create_uid/write_uid/
    # write_date would be three columns of pure overhead. logged_at replaces
    # create_date because the cursor needs it.
    _log_access = False

    model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    op = fields.Selection(
        [("create", "Created"), ("write", "Changed"), ("unlink", "Deleted")],
        required=True,
    )
    logged_at = fields.Datetime(
        required=True,
        index=True,
        default=fields.Datetime.now,
        help="When the row was written, which is at precommit and therefore "
             "within a second of the commit that caused it.",
    )
    company_id = fields.Many2one("res.company", index=True)

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------
    @api.model
    def _sudi_int_param(self, key, default):
        try:
            value = int(self.env["ir.config_parameter"].sudo().get_param(key, default))
        except (TypeError, ValueError):
            return default
        return value if value >= 0 else default

    @api.model
    def _sudi_lag_seconds(self):
        return self._sudi_int_param(PARAM_LAG_SECONDS, DEFAULT_LAG_SECONDS)

    @api.model
    def _sudi_retention_days(self):
        return self._sudi_int_param(PARAM_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)

    @api.model
    def _sudi_pruned_through(self):
        """The highest id the retention cron has already deleted."""
        return self._sudi_int_param(PARAM_PRUNED_THROUGH, 0)

    # ------------------------------------------------------------------
    # the cursor
    # ------------------------------------------------------------------
    @api.model
    def _sudi_latest_visible_id(self):
        """The newest row a device may be told about.

        A serial is allocated *before* commit, so transaction A can take id 100
        and transaction B take 101 and commit first. A client that read up to
        101 and stored that would never see 100. Serving only rows that have
        been settled for ``visibility_lag_seconds`` removes the whole class of
        lost updates, and since the row is written at precommit the lag is
        measured from very close to the commit itself.

        The price is that a change is invisible for a couple of seconds. In a
        workflow measured in minutes on the road, that is nothing.

        ``clock_timestamp()`` and not ``now()``: the latter is the *reading*
        transaction's start time, so a long-running reader would measure the lag
        from the wrong moment and hide rows that had settled long ago.
        """
        self.flush_model()
        self.env.cr.execute(
            """
            SELECT COALESCE(MAX(id), 0) FROM sudi_sync_change
            WHERE logged_at <= (clock_timestamp() AT TIME ZONE 'UTC') - make_interval(secs => %s)
            """,
            (self._sudi_lag_seconds(),),
        )
        return self.env.cr.fetchone()[0]

    @api.model
    def _sudi_is_cursor_stale(self, cursor):
        """Whether ``cursor`` is older than what the log still holds.

        Compared against what the retention cron actually deleted rather than
        against ``MIN(id)``: an empty log means "nothing has changed lately",
        which must not force every device into a full resync.
        """
        if not cursor:
            return True
        return int(cursor) < self._sudi_pruned_through()

    @api.model
    def _sudi_changed_res_ids(self, model, after_cursor, up_to_cursor, limit=None):
        """Pickings touched in ``(after_cursor, up_to_cursor]``, oldest change first.

        Returns ``[(res_id, rev), ...]`` where ``rev`` is the id of that
        record's most recent change. Ordering by ``rev`` is what lets a client
        resume mid-window: the caller advances its cursor to the ``rev`` of the
        last row it accepted.
        """
        self.flush_model()
        if up_to_cursor <= (after_cursor or 0):
            return []
        query = """
            SELECT res_id, MAX(id) AS rev
            FROM sudi_sync_change
            WHERE model = %s AND id > %s AND id <= %s
            GROUP BY res_id
            ORDER BY rev
        """
        params = [model, after_cursor or 0, up_to_cursor]
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        self.env.cr.execute(query, params)
        return [(row[0], row[1]) for row in self.env.cr.fetchall()]

    @api.model
    def _sudi_revisions_for(self, model, res_ids):
        """``{res_id: rev}`` for records fetched outside a cursor window.

        A record that has not changed inside the retained window has no row and
        gets 0, which is still monotonic for the client.
        """
        self.flush_model()
        if not res_ids:
            return {}
        self.env.cr.execute(
            """
            SELECT res_id, MAX(id) FROM sudi_sync_change
            WHERE model = %s AND res_id IN %s
            GROUP BY res_id
            """,
            (model, tuple(res_ids)),
        )
        return dict(self.env.cr.fetchall())

    # ------------------------------------------------------------------
    # retention
    # ------------------------------------------------------------------
    @api.model
    def _cron_prune(self):
        """Drop rows past the retention window, recording how far we pruned.

        Any device whose cursor predates ``pruned_through`` is answered with a
        full resync, so the log can be trimmed without tracking devices.
        """
        self.flush_model()
        days = self._sudi_retention_days()
        if not days:
            return 0
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        self.env.cr.execute(
            "SELECT COALESCE(MAX(id), 0) FROM sudi_sync_change WHERE logged_at < %s",
            (cutoff,),
        )
        highest = self.env.cr.fetchone()[0]
        if not highest:
            return 0
        self.env.cr.execute(
            "DELETE FROM sudi_sync_change WHERE id <= %s", (highest,)
        )
        deleted = self.env.cr.rowcount
        self.env["ir.config_parameter"].sudo().set_param(
            PARAM_PRUNED_THROUGH, str(highest)
        )
        _logger.info(
            "sudi.sync.change: pruned %s rows up to id %s (older than %s days)",
            deleted, highest, days,
        )
        return deleted
