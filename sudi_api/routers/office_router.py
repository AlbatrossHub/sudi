"""The office's validation of a collected jangad, from the staff app.

View the pages and the requested services (the ``office`` sync scope), build
the item table, mark it READY. The same three things a stock user does on the
receipt form, on the same methods, so the web and the app cannot disagree
about what "ready" means.

None of these is an offline intent: the table is edited against what is really
on the receipt, and READY starts job work for other people. The phone keeps a
draft of the table and sends it when there is a connection.
"""

from typing import Annotated

from odoo import fields
from odoo.tools.translate import _

from fastapi import APIRouter, Depends, Header

from .. import intents
from ..dependencies import Caller, office_caller
from ..errors import conflict, not_in_scope, validation
from ..schemas import OfficeDoc, OfficeLinesInput, OfficeReadyResult

router = APIRouter(prefix="/receipts", tags=["office"])


def _locked_receipt(caller_obj: Caller, picking_id: int):
    """The receipt, in the office scope or not, under a row lock.

    Fetched even out of scope so a refusal can say *why*: not collected yet is
    a VALIDATION the office can act on (wait for the pickup), already ready is
    a conflict to resync, anything else is not this caller's business.
    """
    receipt = intents.record(caller_obj, picking_id, ["office"])
    intents.lock(caller_obj, [picking_id])
    return receipt


def _assert_collected(receipt):
    """Refuse a receipt that is not collected-and-awaiting-validation."""
    picking_id = receipt.id
    if receipt.state == "draft" and receipt.sudi_pickup_datetime:
        return receipt
    detail = {"picking_id": picking_id, "state": receipt.state}
    if receipt.state in ("sudi_pickup_pending", "draft"):
        raise validation(
            "This receipt has not been collected yet, so its items cannot be "
            "entered.",
            detail={**detail, "reason": "not_collected"},
        )
    if receipt.state in ("assigned", "done"):
        raise conflict(
            "ALREADY_CONFIRMED",
            "This receipt has already been marked ready for job work.",
            resync=[picking_id],
            detail=detail,
        )
    raise not_in_scope("This receipt is not in your work list.", resync=[picking_id])


def _line_problems(receipt, data: OfficeLinesInput, env):
    """Every reason the table cannot be saved, one human message each."""
    existing = receipt.move_ids.filtered(lambda move: move.state != "cancel")
    requests = receipt.sudi_service_request_ids
    JobType = env["sudi.diamond.job.type"].sudo().with_context(active_test=False)
    job_types = {
        job_type.id: job_type for job_type in JobType.browse(
            {line.job_type_id for line in data.lines if line.job_type_id}
        ).exists()
    }
    problems = []
    seen_moves, seen_requests = set(), set()
    for row, line in enumerate(data.lines, start=1):
        def problem(field, message, row=row):
            problems.append({"row": row, "field": field, "message": message})
        if line.move_id:
            if line.move_id not in existing.ids:
                problem("move_id", f"Row {row} refers to an item that is not on this receipt.")
            elif line.move_id in seen_moves:
                problem("move_id", f"Row {row} repeats an item already in the table.")
            seen_moves.add(line.move_id)
        if line.job_type_id:
            job_type = job_types.get(line.job_type_id)
            if not job_type or not job_type.active:
                problem("job_type_id", f"Row {row}: that job type is not offered any more.")
        if line.request_id:
            if line.request_id not in requests.ids:
                problem("request_id", f"Row {row} refers to a requested service of another receipt.")
            elif line.request_id in seen_requests:
                problem("request_id", f"Row {row} enters a requested service twice.")
            seen_requests.add(line.request_id)
    return problems


def _refuse(problems):
    first = problems[0]["message"]
    message = first if len(problems) == 1 else f"{first} (and {len(problems) - 1} more)"
    raise validation(message, detail={"problems": problems})


@router.put(
    "/{picking_id}/lines",
    response_model=OfficeDoc,
    summary="Replace the item table of a collected receipt",
)
def replace_lines(
    picking_id: int,
    data: OfficeLinesInput,
    caller_obj: Annotated[Caller, Depends(office_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> OfficeDoc:
    """The whole table, as the office leaves it.

    Rows with a ``move_id`` keep and update that line, rows without one are
    new, and lines left out are removed. Sr follows the order sent, so the
    phone's order is the receipt's. A replay therefore leaves the same table,
    with or without a key.
    """
    reservation = intents.claim(caller_obj, "office.lines", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    receipt = _assert_collected(_locked_receipt(caller_obj, picking_id))
    problems = _line_problems(receipt, data, caller_obj.env)
    if problems:
        _refuse(problems)

    # As the office user, so tracking and the chatter name them: a stock user
    # is exactly who edits this table on the form.
    as_user = receipt.with_user(caller_obj.user).with_context(
        sudi_device_uid=caller_obj.device.device_uid
    )
    Move = as_user.env["stock.move"]
    existing = as_user.move_ids.filtered(lambda move: move.state != "cancel")
    kept = {line.move_id for line in data.lines if line.move_id}
    removed = existing.filtered(lambda move: move.id not in kept)
    if removed:
        # sudo: a stock user may create and edit lines but not delete them,
        # and the role check above is the authorisation. A removed line's
        # requested service becomes open again (ondelete set null).
        removed.sudo().unlink()

    product = caller_obj.env.ref("diamond.product_customer_diamond_parcel")
    Requests = as_user.env["sudi.diamond.service.request"]
    for sr, line in enumerate(data.lines, start=1):
        vals = {
            "sudi_sr": sr,
            "sudi_size": (line.size or "").strip() or False,
            "sudi_pcs_qty": line.pcs,
            "sudi_carats": line.carats,
            "sudi_job_type_id": line.job_type_id or False,
            "sudi_remarks": (line.remarks or "").strip() or False,
        }
        if line.move_id:
            move = Move.browse(line.move_id)
            move.write(vals)
        else:
            move = Move.create({
                **vals,
                "picking_id": receipt.id,
                "product_id": product.id,
                "uom_id": product.uom_id.id,
                "location_id": receipt.location_id.id,
                "location_dest_id": receipt.location_dest_id.id,
                "company_id": receipt.company_id.id,
                "partner_id": receipt.partner_id.id,
            })
        if line.request_id:
            Requests.browse(line.request_id).move_id = move
    # The billing details follow the table, as they do when the form saves.
    as_user._sudi_sync_billing_details_on_save()

    return reservation.store(
        intents.document(caller_obj, "office", picking_id), record=receipt
    )


@router.post(
    "/{picking_id}/lines/from-requests",
    response_model=OfficeDoc,
    summary="Add entries from the requested-services table",
)
def lines_from_requests(
    picking_id: int,
    caller_obj: Annotated[Caller, Depends(office_caller)],
) -> OfficeDoc:
    """One line per requested service not entered yet. Naturally idempotent:
    a request that already has a line is skipped, so a second tap adds nothing."""
    receipt = _assert_collected(_locked_receipt(caller_obj, picking_id))
    receipt.with_user(caller_obj.user)._sudi_add_lines_from_requests()
    return intents.document(caller_obj, "office", picking_id)


@router.post(
    "/{picking_id}/ready",
    response_model=OfficeReadyResult,
    summary="Mark READY for job work — needs a connection",
)
def mark_ready(
    picking_id: int,
    caller_obj: Annotated[Caller, Depends(office_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> OfficeReadyResult:
    """The form's "Mark READY for Job work", after the checks it implies.

    Every problem is reported at once, one human message each, in
    ``detail.problems``. On success the receipt is job work: it leaves the
    office scope and the job workers' bell rings.
    """
    reservation = intents.claim(caller_obj, "office.ready", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    receipt = _locked_receipt(caller_obj, picking_id)
    if receipt.state in ("assigned", "done"):
        # Ready is ready, whoever got there first.
        return reservation.store(OfficeReadyResult(
            code=intents.RESULT_ALREADY_DONE, state=receipt.state, reference=receipt.name,
        ).model_dump(), record=receipt)
    _assert_collected(receipt)

    problems = receipt._sudi_job_work_ready_problems()
    if problems:
        _refuse(problems)

    as_user = receipt.with_user(caller_obj.user).with_context(
        sudi_device_uid=caller_obj.device.device_uid
    )
    result = as_user.action_confirm()
    if isinstance(result, dict) and result.get("type") == "ir.actions.act_window":
        raise validation(
            "This receipt needs attention in the back office before it can be "
            "marked ready.",
            detail={"picking_id": picking_id, "wizard": result.get("res_model")},
        )
    as_user._sudi_post_event_provenance(
        _("Marked ready for job work"), fields.Datetime.now()
    )
    receipt.invalidate_recordset(["state"])
    return reservation.store(OfficeReadyResult(
        code=intents.RESULT_OK, state=receipt.state, reference=receipt.name,
    ).model_dump(), record=receipt)
