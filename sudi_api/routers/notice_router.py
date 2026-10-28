"""The bell: the caller's own in-app notices.

Scoped to the caller in code, as the device routes are: ``sudi.app.notice`` is
read with ``sudo()`` and filtered by ``user_id``. Any new route touching the
model must do the same or that scoping is lost.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..dependencies import Caller, field_caller
from ..schemas import NoticeList, NoticeReadInput, NoticeReadResult

router = APIRouter(prefix="/notices", tags=["notices"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _own(caller_obj: Caller):
    return caller_obj.env["sudi.app.notice"].sudo()


@router.get("", response_model=NoticeList, summary="The caller's notices")
def list_notices(
    caller_obj: Annotated[Caller, Depends(field_caller)],
    after: Annotated[int, Query(
        ge=0, description="The last_id from the previous answer. 0 on first launch."
    )] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> NoticeList:
    """Newest first, always; which ones depends on ``after``.

    On first launch (``after=0``) the newest ``limit``: the bell shows what is
    recent, not the first notice of the month. After that, the *oldest*
    ``limit`` above ``after``, so a phone back from a long dark spell catches
    up page by page (``has_more``) without a gap in the middle.
    """
    Notice = _own(caller_obj)
    domain = [("user_id", "=", caller_obj.user.id), ("id", ">", after)]
    order = "id desc" if not after else "id asc"
    found = Notice.search(domain, order=order, limit=limit + 1)
    has_more = bool(after) and len(found) > limit
    found = found[:limit]
    notices = found.sorted("id", reverse=True)
    return NoticeList(
        notices=[
            {
                "id": notice.id,
                "kind": notice.kind,
                "title": notice.title,
                "body": notice.body or None,
                "picking_id": notice.picking_id.id or None,
                "scope": notice.scope or None,
                "created_at": notice.create_date.isoformat(),
                "read": bool(notice.read_at),
            }
            for notice in notices
        ],
        last_id=max(notices.ids or [after]),
        unread=Notice._sudi_unread_count(caller_obj.user),
        has_more=has_more,
    )


@router.post("/read", response_model=NoticeReadResult, summary="Mark notices read")
def mark_read(
    data: NoticeReadInput,
    caller_obj: Annotated[Caller, Depends(field_caller)],
) -> NoticeReadResult:
    """Naturally idempotent, so it can sit in the outbox without a key:
    marking something read twice leaves it read."""
    Notice = _own(caller_obj)
    domain = [("user_id", "=", caller_obj.user.id), ("read_at", "=", False)]
    if data.ids:
        domain.append(("id", "in", data.ids))
    else:
        domain.append(("id", "<=", data.up_to))
    marked = Notice.search(domain)._sudi_mark_read()
    return NoticeReadResult(marked=marked, unread=Notice._sudi_unread_count(caller_obj.user))
