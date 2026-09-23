"""Reads on a single receipt. The jangad pages live here.

The intents that act on a receipt (confirm, cancel) land beside these in
stage 4; the scope check they will share is already here.
"""

import base64
from typing import Annotated

from odoo.tools.translate import _

from fastapi import APIRouter, Depends, Header, Response

from .. import intents
from ..dependencies import Caller, field_caller, pickup_delivery_caller
from ..errors import not_in_scope
from ..schemas import CancelPickupInput, ConfirmPickupInput, PickupIntentResult
from .sync_router import SCOPE_ROLES

router = APIRouter(prefix="/pickups", tags=["pickups"])

# A jangad page is an incoming receipt's image, so only the two incoming
# scopes can reach one.
JANGAD_SCOPES = ("pickup", "jobwork")


def _receipt_in_scope(caller_obj: Caller, picking_id: int, scopes=JANGAD_SCOPES):
    """A receipt the caller may see, or raise.

    Scoped by the same explicit domains the pull uses, not by a record rule:
    the record rules cannot express the moving "today" window, and a read that
    disagreed with the worklist would let a phone fetch an image for work it
    was never shown.
    """
    Picking = caller_obj.env["stock.picking"]
    for scope in scopes:
        if not caller_obj.has_role(SCOPE_ROLES[scope]):
            continue
        if picking_id in Picking._sudi_sync_scope_ids(
            scope, extra_domain=[("id", "=", picking_id)]
        ):
            return Picking.sudo().browse(picking_id)
    raise not_in_scope("This receipt is not in your work list.", resync=[picking_id])


@router.get(
    "/{picking_id}/jangad/{page}",
    summary="One page of a receipt's jangad",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "The page's bytes"},
        404: {"description": "No such page, or the receipt is not in your work list"},
    },
)
def jangad_page(
    picking_id: int,
    page: int,
    caller_obj: Annotated[Caller, Depends(field_caller)],
) -> Response:
    """Page 0 is the image on the receipt; 1 and up are the extra sheets.

    Page 0 stays where every existing reader of ``sudi_jangad_image`` expects
    it -- the receipt report and the WhatsApp templates -- so the API reads the
    same field rather than a parallel copy.
    """
    receipt = _receipt_in_scope(caller_obj, picking_id)
    if page < 0:
        raise not_in_scope("A page number cannot be negative.")

    if page == 0:
        datas = receipt.sudi_jangad_image
        mimetype = "image/jpeg"
    else:
        attachments = receipt.sudi_jangad_attachment_ids
        if page > len(attachments):
            raise not_in_scope(
                f"This receipt has {len(attachments) + (1 if receipt.sudi_jangad_image else 0)} page(s)."
            )
        attachment = attachments[page - 1]
        datas = attachment.datas
        mimetype = attachment.mimetype or "image/jpeg"
    if not datas:
        raise not_in_scope("This receipt has no jangad image.")

    raw = base64.b64decode(datas)
    return Response(
        content=raw,
        media_type=mimetype,
        headers={
            # Private: it is a customer's handwritten slip, not a public asset.
            "Cache-Control": "private, max-age=86400",
            "Content-Length": str(len(raw)),
        },
    )


@router.post(
    "/{picking_id}/confirm",
    response_model=PickupIntentResult,
    summary="Confirm a pickup, with the jangad pages collected against it",
)
def confirm_pickup(
    picking_id: int,
    data: ConfirmPickupInput,
    caller_obj: Annotated[Caller, Depends(pickup_delivery_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PickupIntentResult:
    reservation = intents.claim(caller_obj, "pickups.confirm", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    receipt = intents.record(caller_obj, picking_id, ["pickup"])
    intents.lock(caller_obj, [picking_id])

    if receipt.state != "sudi_pickup_pending":
        # Its own earlier effect is a success; somebody else's is a conflict
        # the operator needs told about, because they are standing in the shop.
        if receipt.sudi_pickup_user_id == caller_obj.user:
            return reservation.store(PickupIntentResult(
                code=intents.RESULT_ALREADY_DONE,
                record=intents.document(caller_obj, "pickup", picking_id),
            ).model_dump())
        raise intents.conflict_confirmed(receipt)

    occurred_at = intents.event_time(caller_obj, data.occurred_at)
    pages = intents.claim_uploads(caller_obj, data.upload_ids, receipt)
    if pages:
        receipt.with_context(sudi_skip_pickup_scheduled_notify=True)._sudi_add_jangad_pages(pages)
    if data.note:
        receipt.sudo().message_post(
            body=_("Pickup note: %s", data.note),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

    receipt.with_user(caller_obj.user).with_context(
        sudi_device_uid=caller_obj.device.device_uid
    ).action_sudi_confirm_pickup(occurred_at=occurred_at, location=data.location())

    return reservation.store(PickupIntentResult(
        code=intents.RESULT_OK,
        record=intents.document(caller_obj, "pickup", picking_id),
    ).model_dump())


@router.post(
    "/{picking_id}/cancel",
    response_model=PickupIntentResult,
    summary="Cancel a pickup that cannot be collected",
)
def cancel_pickup(
    picking_id: int,
    data: CancelPickupInput,
    caller_obj: Annotated[Caller, Depends(pickup_delivery_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PickupIntentResult:
    reservation = intents.claim(caller_obj, "pickups.cancel", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    receipt = intents.record(caller_obj, picking_id, ["pickup"])
    intents.lock(caller_obj, [picking_id])

    if receipt.state == "cancel":
        return reservation.store(PickupIntentResult(
            code=intents.RESULT_ALREADY_DONE,
            record=intents.document(caller_obj, "pickup", picking_id),
        ).model_dump())
    if receipt.state != "sudi_pickup_pending":
        raise intents.conflict_confirmed(receipt)

    occurred_at = intents.event_time(caller_obj, data.occurred_at)
    # Cancelling archives the receipt, so the document has to be rendered
    # before the action runs: afterwards it is out of every scope, which is
    # correct but leaves nothing to hand back.
    record = intents.document(caller_obj, "pickup", picking_id)
    receipt.with_user(caller_obj.user).with_context(
        sudi_device_uid=caller_obj.device.device_uid
    ).action_sudi_cancel_pickup(occurred_at=occurred_at, reason=data.reason)

    return reservation.store(
        PickupIntentResult(code=intents.RESULT_OK, record=record).model_dump()
    )
