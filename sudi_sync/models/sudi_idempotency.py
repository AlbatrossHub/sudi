"""Idempotency keys, so a retried request cannot act twice.

A phone on a flaky connection retries. Without this, one tap in a lift confirms
a pickup, the response is lost, the client resends, and the second call either
acts again or comes back as a confusing conflict. The key makes the retry a
no-op that returns the first call's answer.

Uniqueness is enforced by the database, not by a check-then-insert in Python:
two requests racing on the same key would both pass a Python check. The unique
index means exactly one insert wins, and the loser is told the work is already
under way.
"""

import json
import logging

import psycopg2

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# What `_claim` tells its caller to do.
CLAIM_NEW = "new"            # you own the work; do it, then call _store
CLAIM_REPLAY = "replay"      # return the stored response verbatim
CLAIM_IN_PROGRESS = "in_progress"  # the first attempt has not answered yet


class SudiIdempotencyKey(models.Model):
    _name = "sudi.idempotency.key"
    _description = "Sudi API Idempotency Key"
    _order = "id desc"

    key = fields.Char(required=True, index=True, readonly=True)
    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade", readonly=True
    )
    endpoint = fields.Char(
        required=True,
        readonly=True,
        help="Scopes the key to one operation, so a client that reuses a key "
             "across endpoints is not deduplicated by accident.",
    )
    response_json = fields.Text(
        readonly=True,
        help="The first request's response, replayed verbatim to any retry.",
    )
    result_model = fields.Char(readonly=True)
    result_id = fields.Integer(readonly=True)

    # (user, endpoint, key) rather than the key alone: two users may pick the
    # same key, and one user may legitimately reuse a key across endpoints.
    _key_unique = models.Constraint(
        "UNIQUE (user_id, endpoint, key)",
        "This idempotency key has already been used for this operation.",
    )

    @api.model
    def _sudi_find(self, user, endpoint, key):
        return self.sudo().search([
            ("user_id", "=", user.id),
            ("endpoint", "=", endpoint),
            ("key", "=", key),
        ], limit=1)

    @api.model
    def _sudi_claim(self, user, endpoint, key):
        """Reserve ``key``, or report what the caller should do instead.

        Returns ``(state, payload)``:

        * ``("new", claim)`` -- nobody has this key; do the work and call
          ``claim._sudi_store(...)``.
        * ``("replay", response)`` -- already done; return ``response`` and
          touch nothing.
        * ``("in_progress", None)`` -- another request holds the key and has
          not answered yet. The honest answer is "retry", not an empty
          response: the winner's transaction has not committed, so there is
          genuinely nothing to replay.

        A falsy ``key`` returns ``("new", None)``, which lets a caller treat
        the header as optional without branching.
        """
        if not key:
            return CLAIM_NEW, None

        existing = self._sudi_find(user, endpoint, key)
        if existing:
            return self._sudi_state_of(existing)

        try:
            # A savepoint, because a unique violation would otherwise poison
            # the whole transaction rather than just this insert.
            with self.env.cr.savepoint():
                claim = self.sudo().create({
                    "user_id": user.id,
                    "endpoint": endpoint,
                    "key": key,
                })
        except psycopg2.errors.UniqueViolation:
            _logger.info(
                "Idempotency key %s raced on %s; deferring to the winner", key, endpoint
            )
            return self._sudi_state_of(self._sudi_find(user, endpoint, key))
        return CLAIM_NEW, claim

    @api.model
    def _sudi_state_of(self, record):
        if not record:
            # Lost the race and then could not read the winner: the only safe
            # answer is "try again".
            return CLAIM_IN_PROGRESS, None
        if record.response_json is False or record.response_json is None:
            return CLAIM_IN_PROGRESS, None
        return CLAIM_REPLAY, json.loads(record.response_json)

    def _sudi_store(self, response, record=None):
        """Remember this request's answer so a retry can be handed the same one."""
        self.ensure_one()
        vals = {"response_json": json.dumps(response, default=str)}
        if record is not None and record:
            vals["result_model"] = record._name
            vals["result_id"] = record.id
        self.sudo().write(vals)
        return response
