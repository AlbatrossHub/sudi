"""The mixin that puts rows into the change log.

Marking is deliberately cheap and deliberately over-eager.

*Cheap*: a mark only appends to ``cr.precommit.data``, so a transaction that
writes one picking fifty times still writes one log row. Doing the insert at
precommit rather than at write time is also what makes the visibility lag in
``sudi.sync.change._latest_visible_id`` meaningful -- the row's ``logged_at``
lands within a moment of the commit that caused it, not at the start of a long
transaction.

*Over-eager*: every write is logged, even one that cannot change a payload. A
field allowlist would be smaller but would silently drift out of step with the
payload builders, and the failure mode of drift is a phone showing work that no
longer exists. An extra row costs a few bytes; a missed change costs a wasted
trip to a customer.
"""

from odoo import api, models

PRECOMMIT_KEY = "sudi_sync.changes"


class SudiSyncSource(models.AbstractModel):
    _name = "sudi.sync.source"
    _description = "Sudi Offline Sync Source"

    def _sudi_sync_pickings(self):
        """The pickings whose payload this record's change invalidates."""
        raise NotImplementedError

    def _sudi_sync_mark(self, op):
        if self.env.context.get("sudi_sync_skip"):
            return
        pickings = self._sudi_sync_pickings().filtered("sudi_is_diamond_job_work")
        if not pickings:
            return
        pending = self.env.cr.precommit.data.setdefault(PRECOMMIT_KEY, {})
        for picking in pickings:
            previous = pending.get(picking.id)
            # A record created and then written inside one transaction is a
            # create as far as any device is concerned; an unlink outranks both.
            if previous and (previous[0] == "unlink" or op == "write"):
                continue
            pending[picking.id] = (op, picking.company_id.id or None)
        self.env.cr.precommit.add(self._sudi_sync_flush)

    def _sudi_sync_flush(self):
        """Write one log row per picking touched in this transaction.

        Registered possibly many times per transaction; ``pop`` makes every
        call after the first a no-op, which is the same shape mail's own
        ``_track_finalize`` uses.
        """
        pending = self.env.cr.precommit.data.pop(PRECOMMIT_KEY, None)
        if not pending:
            return
        self.env["sudi.sync.change"].sudo().create([
            {
                "model": "stock.picking",
                "res_id": res_id,
                "op": op,
                "company_id": company_id,
            }
            for res_id, (op, company_id) in sorted(pending.items())
        ])

    # ------------------------------------------------------------------
    # the three hooks
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sudi_sync_mark("create")
        return records

    def write(self, vals):
        result = super().write(vals)
        self._sudi_sync_mark("write")
        return result

    def unlink(self):
        # Resolved before the delete: afterwards there is nothing to resolve.
        self._sudi_sync_mark("unlink")
        return super().unlink()
