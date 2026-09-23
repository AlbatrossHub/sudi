"""The delta pull: everything a device needs to catch up, and nothing else.

The algorithm lives in ``sudi_sync`` so it can be tested without HTTP. What
belongs here is the HTTP contract around it: which scopes this caller is
entitled to, the ETag that makes an unchanged poll free, and recording where
the device got to.
"""

import hashlib
from typing import Annotated

from odoo import fields

from fastapi import APIRouter, Depends, Header, Query, Response, status

from ..dependencies import (
    Caller,
    ROLE_JOB_WORK,
    ROLE_PICKUP_DELIVERY,
    field_caller,
)
from ..errors import forbidden, validation
from ..schemas import SyncPullResult

router = APIRouter(prefix="/sync", tags=["sync"])

# Which role a scope belongs to. Pickup and delivery are the same person;
# job work is deliberately somebody else.
SCOPE_ROLES = {
    "pickup": ROLE_PICKUP_DELIVERY,
    "delivery": ROLE_PICKUP_DELIVERY,
    "jobwork": ROLE_JOB_WORK,
}


def _entitled_scopes(caller_obj: Caller) -> list[str]:
    return [
        scope for scope, role in SCOPE_ROLES.items() if caller_obj.has_role(role)
    ]


def _resolve_scopes(caller_obj: Caller, requested: str | None) -> list[str]:
    """The scopes to serve.

    Omitting ``scopes`` means "everything my roles entitle me to", which is
    what a client should normally send. Naming a scope the caller has no role
    for is answered with a 403 rather than silently dropped: the client already
    knows its roles from the token, so asking for one it does not hold is a bug
    worth surfacing rather than hiding behind an empty response.
    """
    entitled = _entitled_scopes(caller_obj)
    if not requested:
        return entitled
    asked = [scope.strip() for scope in requested.split(",") if scope.strip()]
    unknown = [scope for scope in asked if scope not in SCOPE_ROLES]
    if unknown:
        raise validation(
            f"Unknown sync scope(s): {', '.join(sorted(unknown))}.",
            detail={"known": sorted(SCOPE_ROLES)},
        )
    refused = [scope for scope in asked if scope not in entitled]
    if refused:
        raise forbidden(
            f"Your account has no role for: {', '.join(sorted(refused))}.",
            detail={"entitled": entitled},
        )
    # Keep the canonical order so paging is deterministic.
    return [scope for scope in SCOPE_ROLES if scope in asked]


def _etag(caller_obj: Caller, scopes: list[str], cursor: int, latest: int) -> str:
    """A weak validator for an unchanged incremental pull.

    The user's local date is part of it because the delivery scope carries a
    tail of "delivered by me today": that window moves at midnight with no
    write to detect, so without the date a client could sit on a 304 and keep
    yesterday's round on screen.
    """
    today = fields.Datetime.context_timestamp(
        caller_obj.user, fields.Datetime.now()
    ).date()
    material = "|".join([
        str(caller_obj.user.id),
        caller_obj.device.device_uid,
        ",".join(scopes),
        str(cursor),
        str(latest),
        today.isoformat(),
    ])
    return 'W/"%s"' % hashlib.sha256(material.encode()).hexdigest()[:32]


@router.get(
    "/pull",
    response_model=SyncPullResult,
    summary="Everything that changed since the caller's cursor",
    responses={status.HTTP_304_NOT_MODIFIED: {"description": "Nothing has changed"}},
)
def pull(
    caller_obj: Annotated[Caller, Depends(field_caller)],
    response: Response,
    scopes: Annotated[str | None, Query(
        description="Comma separated. Omit for every scope your roles allow.",
        examples=["pickup,delivery"],
    )] = None,
    cursor: Annotated[int | None, Query(
        ge=0, description="The cursor from the last accepted response."
    )] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    after_scope: Annotated[str | None, Query(
        description="Full-resync continuation: the scope to resume."
    )] = None,
    after_id: Annotated[int | None, Query(
        ge=0, description="Full-resync continuation: the id to resume after."
    )] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
):
    wanted = _resolve_scopes(caller_obj, scopes)
    if after_scope and after_scope not in wanted:
        raise validation(
            f"Cannot resume {after_scope!r}: it is not one of the scopes being pulled.",
            detail={"scopes": wanted},
        )

    Picking = caller_obj.env["stock.picking"]
    Change = caller_obj.env["sudi.sync.change"].sudo()
    latest = Change._sudi_latest_visible_id()

    # Only an ordinary incremental pull is cacheable. A full resync must never
    # come back as a 304, and a continuation page is not a stable resource.
    cacheable = bool(cursor) and not after_scope and not Change._sudi_is_cursor_stale(cursor)
    etag = _etag(caller_obj, wanted, cursor or 0, latest) if cacheable else None
    if etag:
        response.headers["ETag"] = etag
        if if_none_match and etag in [
            value.strip() for value in if_none_match.split(",")
        ]:
            caller_obj.device._sudi_touch(cursor=cursor)
            return Response(
                status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag}
            )

    result = Picking._sudi_sync_pull(
        scopes=wanted,
        cursor=cursor,
        limit=limit,
        after_scope=after_scope,
        after_id=after_id,
    )
    # Advisory, for support: "this phone last synced at 11:20 and reached
    # cursor 90251". The client remains the authority on its own cursor.
    caller_obj.device._sudi_touch(cursor=result["cursor"])
    return result
