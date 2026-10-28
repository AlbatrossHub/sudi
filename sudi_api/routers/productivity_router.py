"""My work: what a job worker has booked, by day, by service and by entry.

Read from the timesheet lines themselves, the same ones the office's
timesheet reports read, so the phone and the office cannot disagree about
anybody's week.
"""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from odoo import fields

from ..dependencies import Caller, job_work_caller
from ..errors import validation
from ..schemas import ProductivityResult
from .jobwork_router import time_entry

router = APIRouter(prefix="/me", tags=["jobwork"])

# A quarter: enough for "how did last month go", small enough to stay one
# response on a phone.
MAX_RANGE_DAYS = 92
MAX_ENTRIES = 1000


@router.get(
    "/productivity",
    response_model=ProductivityResult,
    summary="The caller's booked time: totals, per day, per service, entries",
)
def productivity(
    caller_obj: Annotated[Caller, Depends(job_work_caller)],
    date_from: Annotated[date | None, Query(
        description="First day, inclusive. Default: Monday of this week."
    )] = None,
    date_to: Annotated[date | None, Query(
        description="Last day, inclusive. Default: Sunday of this week."
    )] = None,
) -> ProductivityResult:
    today = fields.Date.context_today(caller_obj.user)
    monday = today - timedelta(days=today.weekday())
    date_from = date_from or (monday if not date_to else date_to - timedelta(days=6))
    date_to = date_to or (date_from + timedelta(days=6))
    if date_to < date_from:
        raise validation(
            "date_to is before date_from.",
            detail={"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        )
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise validation(
            f"Ask for at most {MAX_RANGE_DAYS} days at a time.",
            detail={"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        )

    # sudo with an explicit user filter: the caller's own lines, and nothing
    # else, whatever the timesheet rules of the day happen to allow.
    lines = caller_obj.env["account.analytic.line"].sudo().search(
        [
            ("user_id", "=", caller_obj.user.id),
            ("sudi_picking_id", "!=", False),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
        ],
        order="date desc, id desc",
    )
    by_day = {
        (date_from + timedelta(days=offset)).isoformat(): 0.0
        for offset in range((date_to - date_from).days + 1)
    }
    by_job_type = {}
    for line in lines:
        by_day[line.date.isoformat()] = by_day.get(line.date.isoformat(), 0.0) + line.unit_amount
        key = line.sudi_job_type_id.id or 0
        bucket = by_job_type.setdefault(key, {
            "job_type": (
                {"id": line.sudi_job_type_id.id, "name": line.sudi_job_type_id.display_name}
                if line.sudi_job_type_id else None
            ),
            "hours": 0.0,
        })
        bucket["hours"] += line.unit_amount

    # is_timer_running is per *current* user, and sudo keeps the caller's uid.
    entries = lines[:MAX_ENTRIES]
    return ProductivityResult(
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        total_hours=sum(lines.mapped("unit_amount")),
        by_day=[{"date": day, "hours": hours} for day, hours in sorted(by_day.items())],
        by_job_type=sorted(
            by_job_type.values(),
            key=lambda bucket: (-bucket["hours"], (bucket["job_type"] or {}).get("id", 0)),
        ),
        entries=[time_entry(line) for line in entries],
    )
