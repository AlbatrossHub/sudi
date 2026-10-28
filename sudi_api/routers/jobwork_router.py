"""Job-work intents: the timer, manual time, the department, and finishing.

Finishing is the one intent that cannot be queued offline (decision D2): it
runs the stock validation chain, whose outcome cannot be predicted on the
device, so queueing it would mean showing an operator a success that may not
happen.
"""

from datetime import timedelta
from typing import Annotated

from odoo import fields
from odoo.exceptions import UserError

from fastapi import APIRouter, Depends, Header

from .. import intents
from ..dependencies import Caller, job_work_caller
from ..errors import validation
from ..schemas import (
    DepartmentInput,
    FinishJobWorkInput,
    JobWorkIntentResult,
    TimeLogInput,
    TimeLogResult,
    TimerInput,
)

# How far back a manual entry may be dated from the moment it was captured.
# Time is logged for the week just worked, not for last quarter.
MAX_TIMESHEET_BACKDATE_DAYS = 31

router = APIRouter(prefix="/jobwork", tags=["jobwork"])


@router.post(
    "/{picking_id}/timer",
    response_model=JobWorkIntentResult,
    summary="Start or stop the receipt timer",
)
def timer(
    picking_id: int,
    data: TimerInput,
    caller_obj: Annotated[Caller, Depends(job_work_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobWorkIntentResult:
    reservation = intents.claim(
        caller_obj, f"jobwork.timer.{data.action}", idempotency_key
    )
    if reservation.is_replay:
        return reservation.replay

    receipt = intents.in_scope(caller_obj, picking_id, ["jobwork"])
    intents.lock(caller_obj, [picking_id])
    intents.event_time(caller_obj, data.occurred_at)

    as_user = receipt.with_user(caller_obj.user).with_context(
        sudi_device_uid=caller_obj.device.device_uid
    )
    running = bool(as_user.user_timer_id)
    code = intents.RESULT_OK

    if data.action == "start":
        if running:
            code = intents.RESULT_ALREADY_DONE
        else:
            if data.job_type_id:
                receipt.sudo().write({"sudi_timesheet_job_type_id": data.job_type_id})
            if not as_user._sudi_get_default_timesheet_job_type():
                # The lines do not agree on one job type and the client did not
                # pick one, so there is nothing to book the time against.
                raise validation(
                    "Choose which job type this time is for before starting.",
                    detail={"picking_id": picking_id, "needs": "job_type_id"},
                )
            as_user.action_timer_start()
    else:
        if not running:
            code = intents.RESULT_ALREADY_DONE
        else:
            as_user._sudi_stop_timer()

    return reservation.store(JobWorkIntentResult(
        code=code, record=intents.document(caller_obj, "jobwork", picking_id)
    ).model_dump())


def time_entry(line):
    """One timesheet line as the app shows it, for the log and My work."""
    return {
        "id": line.id,
        "date": line.date.isoformat() if line.date else None,
        "picking": {"id": line.sudi_picking_id.id, "name": line.sudi_picking_id.name},
        "job_type": (
            {"id": line.sudi_job_type_id.id, "name": line.sudi_job_type_id.display_name}
            if line.sudi_job_type_id else None
        ),
        "hours": line.unit_amount,
        "description": line.name if line.name and line.name != "/" else None,
        "running": bool(line.is_timer_running),
    }


@router.post(
    "/{picking_id}/timesheets",
    response_model=TimeLogResult,
    summary="Log time spent on a receipt by hand",
)
def log_time(
    picking_id: int,
    data: TimeLogInput,
    caller_obj: Annotated[Caller, Depends(job_work_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TimeLogResult:
    """The hour that was worked and never timed. Works offline.

    Unlike the timer this one is *not* naturally idempotent -- two identical
    entries are two hours -- so the capture-time ``Idempotency-Key`` is what
    stops a retried flush from booking the time twice. Always send it.
    """
    reservation = intents.claim(caller_obj, "jobwork.timesheets", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    receipt = intents.in_scope(caller_obj, picking_id, ["jobwork"])
    intents.lock(caller_obj, [picking_id])
    occurred_at = intents.event_time(caller_obj, data.occurred_at)

    # The day the work happened, in the worker's own timezone: an entry
    # captured at 00:30 IST belongs to that day, not to the UTC one before.
    captured_on = fields.Datetime.context_timestamp(caller_obj.user, occurred_at).date()
    day = data.date or captured_on
    if day > captured_on or day < captured_on - timedelta(days=MAX_TIMESHEET_BACKDATE_DAYS):
        raise validation(
            f"Time can be logged for {captured_on.isoformat()} or up to "
            f"{MAX_TIMESHEET_BACKDATE_DAYS} days before it, not {day.isoformat()}.",
            detail={"date": day.isoformat(), "captured_on": captured_on.isoformat()},
        )

    job_type = caller_obj.env["sudi.diamond.job.type"].sudo().browse(
        data.job_type_id
    ).exists()
    if not job_type or not job_type.active:
        raise validation(
            "That job type does not exist.", detail={"job_type_id": data.job_type_id}
        )

    try:
        line = receipt.with_user(caller_obj.user).with_context(
            sudi_device_uid=caller_obj.device.device_uid
        )._sudi_log_time(job_type, data.hours, description=data.description, date=day)
    except UserError as error:
        raise validation(str(error), detail={"picking_id": picking_id}) from error

    return reservation.store(TimeLogResult(
        code=intents.RESULT_OK,
        entry=time_entry(line.sudo()),
        jobwork=intents.document(caller_obj, "jobwork", picking_id),
    ).model_dump(), record=line)


@router.post(
    "/{picking_id}/department",
    response_model=JobWorkIntentResult,
    summary="Move a receipt to another department",
)
def transfer_department(
    picking_id: int,
    data: DepartmentInput,
    caller_obj: Annotated[Caller, Depends(job_work_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobWorkIntentResult:
    reservation = intents.claim(caller_obj, "jobwork.department", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    receipt = intents.in_scope(caller_obj, picking_id, ["jobwork"])
    intents.lock(caller_obj, [picking_id])
    intents.event_time(caller_obj, data.occurred_at)

    job_type = caller_obj.env["sudi.diamond.job.type"].sudo().browse(
        data.department_id
    ).exists()
    if not job_type or not job_type.active:
        raise validation(
            "That department does not exist.",
            detail={"department_id": data.department_id},
        )
    if receipt.sudi_current_department_id == job_type:
        return reservation.store(JobWorkIntentResult(
            code=intents.RESULT_ALREADY_DONE,
            record=intents.document(caller_obj, "jobwork", picking_id),
        ).model_dump())

    receipt.with_user(caller_obj.user)._sudi_transfer_department(job_type)

    return reservation.store(JobWorkIntentResult(
        code=intents.RESULT_OK,
        record=intents.document(caller_obj, "jobwork", picking_id),
    ).model_dump())


@router.post(
    "/{picking_id}/finish",
    response_model=JobWorkIntentResult,
    summary="Finish the job work on a receipt — needs a connection",
)
def finish(
    picking_id: int,
    data: FinishJobWorkInput,
    caller_obj: Annotated[Caller, Depends(job_work_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobWorkIntentResult:
    """Never queue this on the device.

    ``button_validate`` runs reservations, lots and backorders; if it needs a
    human it answers with a window action, which this turns into a validation
    failure rather than pretending the work is done.
    """
    reservation = intents.claim(caller_obj, "jobwork.finish", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    receipt = intents.in_scope(caller_obj, picking_id, ["jobwork"])
    intents.lock(caller_obj, [picking_id])
    intents.event_time(caller_obj, data.occurred_at)

    record = intents.document(caller_obj, "jobwork", picking_id)
    # sudo: the role check above is the authorisation. button_validate then
    # moves the receipt through states no record rule can describe -- a move
    # line written while the picking is between "assigned" and "done" matches
    # neither -- and the alternative is granting a field role blanket write on
    # stock.move.line, which is the over-granting this project just removed.
    # env.user is unchanged, so the moves and the chatter still name the
    # operator who finished the work.
    result = receipt.sudo().with_context(
        sudi_device_uid=caller_obj.device.device_uid
    ).button_validate()
    if isinstance(result, dict) and result.get("type") == "ir.actions.act_window":
        raise validation(
            "This receipt needs attention in the back office before it can be "
            "finished.",
            detail={"picking_id": picking_id, "wizard": result.get("res_model")},
        )

    return reservation.store(
        JobWorkIntentResult(code=intents.RESULT_OK, record=record).model_dump()
    )
