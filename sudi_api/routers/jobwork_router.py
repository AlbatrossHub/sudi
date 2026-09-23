"""Job-work intents: the timer, the department, and finishing.

Finishing is the one intent that cannot be queued offline (decision D2): it
runs Odoo's stock validation chain, whose outcome cannot be predicted on the
device, so queueing it would mean showing an operator a success that may not
happen.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from .. import intents
from ..dependencies import Caller, job_work_caller
from ..errors import validation
from ..schemas import (
    DepartmentInput,
    FinishJobWorkInput,
    JobWorkIntentResult,
    TimerInput,
)

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
