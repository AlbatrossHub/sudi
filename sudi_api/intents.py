"""The machinery every write route shares.

An intent is one tap on a phone that may have been offline when it happened.
Four things therefore have to be true of every one of them, and they are true
here rather than in each route:

1. **It is idempotent.** The client generates an ``Idempotency-Key`` when it
   captures the tap and reuses it on every retry forever, so a response lost on
   a train cannot turn into a second action.
2. **It is serialised per record.** The state check and the write happen under
   a row lock, so two operators tapping "take" at the same moment cannot both
   win.
3. **It knows when it happened.** ``occurred_at`` comes from the device, and
   the server clamps it rather than trusting it.
4. **It fails in one shape.** Conflicts are detected *before* the domain method
   runs, so the answer can say who got there first instead of relaying a
   ``UserError`` written for a web form.
"""

import dataclasses
import logging

import psycopg2

from .errors import (
    CODE_ALREADY_DONE,
    CODE_LOCKED,
    SudiApiError,
    conflict,
    not_in_scope,
    validation,
)

_logger = logging.getLogger(__name__)

# What an intent answers when it had nothing left to do.
RESULT_OK = "OK"
RESULT_ALREADY_DONE = CODE_ALREADY_DONE


@dataclasses.dataclass
class Claim:
    """The outcome of reserving an idempotency key."""

    record: object | None
    replay: dict | None

    @property
    def is_replay(self):
        return self.replay is not None

    def store(self, response, record=None):
        """Remember this answer so a retry is handed the same one."""
        if self.record is not None:
            self.record._sudi_store(response, record=record)
        return response


def claim(caller, endpoint, key) -> Claim:
    """Reserve ``key`` for ``endpoint``, or hand back the first answer.

    A missing key is allowed and simply means "no deduplication", so a route
    never has to branch on whether the header was sent.
    """
    Keys = caller.env["sudi.idempotency.key"]
    state, payload = Keys._sudi_claim(caller.user, endpoint, key)
    if state == "replay":
        return Claim(record=None, replay=payload)
    if state == "in_progress":
        # The winner has not committed, so there is genuinely nothing to
        # replay. Retryable: the client tries the same key again shortly.
        raise SudiApiError(
            409, CODE_LOCKED,
            "This request is already being processed. Try again shortly.",
            retryable=True,
        )
    return Claim(record=payload, replay=None)


def event_time(caller, occurred_at):
    """The device's capture time, clamped, or the right typed failure."""
    value, error = caller.env["stock.picking"]._sudi_parse_event_datetime(occurred_at)
    if error == "CLOCK_SKEW":
        raise SudiApiError(
            422, error,
            "This device's clock is too far ahead of the server. "
            "Correct the time and capture again.",
            detail={"occurred_at": str(occurred_at)},
        )
    if error == "STALE_INTENT":
        raise SudiApiError(
            409, error,
            "This was captured too long ago to record automatically. "
            "Redo it, or ask the office.",
            detail={"occurred_at": str(occurred_at)},
        )
    if error:
        raise validation(
            f"{occurred_at!r} is not a valid capture time.",
            detail={"occurred_at": str(occurred_at)},
        )
    return value


def lock(caller, picking_ids):
    """Serialise this record against any other intent touching it.

    ``NOWAIT`` rather than waiting: a phone that has to wait for a lock is
    better told to retry than left holding a request open, and ``LOCKED`` is
    the one conflict code the client is allowed to retry blindly.
    """
    ids = sorted({int(picking_id) for picking_id in picking_ids})
    if not ids:
        return
    caller.env.flush_all()
    try:
        with caller.env.cr.savepoint():
            caller.env.cr.execute(
                "SELECT id FROM stock_picking WHERE id IN %s ORDER BY id FOR UPDATE NOWAIT",
                (tuple(ids),),
            )
    except psycopg2.errors.LockNotAvailable as error:
        raise SudiApiError(
            409, CODE_LOCKED,
            "Somebody else is updating this right now. Try again shortly.",
            retryable=True,
            resync=ids,
        ) from error
    # The lock is on the committed row; drop any cached values so the state
    # check below reads what is really there.
    caller.env["stock.picking"].browse(ids).invalidate_recordset()


def in_scope(caller, picking_id, scopes):
    """The record, if it is in one of ``scopes`` for this caller, else 404.

    Scoped by the same explicit domains the pull uses, so a phone can only act
    on work it was actually shown. A record outside every scope is a 404 and
    never a 403: whether it exists is not this caller's business.
    """
    from .routers.sync_router import SCOPE_ROLES

    Picking = caller.env["stock.picking"]
    for scope in scopes:
        role = SCOPE_ROLES[scope]
        if not caller.has_role(role):
            continue
        if picking_id in Picking._sudi_sync_scope_ids(
            scope, extra_domain=[("id", "=", picking_id)]
        ):
            return Picking.sudo().browse(picking_id)
    raise not_in_scope(
        "This record is not in your work list.", resync=[picking_id]
    )


def record(caller, picking_id, scopes, codes=("incoming",)):
    """The record, in scope or not, so the route can explain itself.

    A receipt somebody else collected, or a parcel somebody else delivered, has
    left this caller's scope — so a bare 404 would be technically true and
    useless: the app has a queued tap and has to tell the operator *why* it did
    not land. The record is therefore fetched once more, restricted to diamond
    job work of the right direction, and the route's own state checks turn it
    into ``ALREADY_CONFIRMED``, ``ALREADY_TAKEN`` or ``ALREADY_DONE``.

    Anything that is not diamond job work stays a 404: whether it exists is
    none of this caller's business.
    """
    try:
        return in_scope(caller, picking_id, scopes)
    except SudiApiError:
        picking = (
            caller.env["stock.picking"]
            .sudo()
            .with_context(active_test=False)
            .browse(picking_id)
            .exists()
        )
        if (
            picking
            and picking.sudi_is_diamond_job_work
            and picking.picking_type_code in codes
        ):
            return picking
        raise


def conflict_taken(picking, message=None):
    """409 naming who holds the record, so the app can explain itself."""
    holder = picking.sudi_pickup_user_id
    when = picking.sudi_out_for_delivery_datetime or picking.sudi_pickup_datetime
    return conflict(
        "ALREADY_TAKEN",
        message or (
            f"Taken by {holder.name}." if holder else "Already taken."
        ),
        resync=[picking.id],
        detail={
            "picking_id": picking.id,
            "user": holder.name or None,
            "user_id": holder.id or None,
            "at": when.isoformat() if when else None,
        },
    )


def conflict_confirmed(picking):
    holder = picking.sudi_pickup_user_id
    return conflict(
        "ALREADY_CONFIRMED",
        f"Already collected by {holder.name}." if holder else "Already collected.",
        resync=[picking.id],
        detail={
            "picking_id": picking.id,
            "user": holder.name or None,
            "user_id": holder.id or None,
            "at": picking.sudi_pickup_datetime.isoformat()
            if picking.sudi_pickup_datetime else None,
        },
    )


def settle(caller):
    """Run what the commit is about to run, so the response can be truthful.

    ``sudi_sync`` writes its change-log row from a ``cr.precommit`` callback, so
    without this the document handed back would still carry the *previous*
    ``rev`` — the client would then hold a revision lower than the one the next
    pull brings for the same change. This is exactly what ``Cursor.commit``
    does a moment later, so nothing is forced early that was not already due.
    """
    caller.env.flush_all()
    caller.env.cr.precommit.run()


def document(caller, scope, picking_id):
    """The scope document for one record, as the pull would render it."""
    settle(caller)
    Picking = caller.env["stock.picking"]
    revisions = caller.env["sudi.sync.change"].sudo()._sudi_revisions_for(
        "stock.picking", [picking_id]
    )
    payloads = Picking._sudi_sync_payloads(scope, [picking_id], revisions)
    if not payloads:
        # It left the scope by acting on it, which is normal: confirming a
        # pickup by somebody else's account, for instance. The client resyncs.
        raise not_in_scope(
            "The record left your work list as a result of this change.",
            resync=[picking_id],
        )
    return payloads[0]


def claim_uploads(caller, references, record):
    """Bind staged files to a record, in the order the client listed them."""
    if not references:
        return []
    uploads = caller.env["sudi.upload"]._sudi_claim(caller.user, references, record)
    return uploads._sudi_datas()
