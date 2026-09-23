"""Delivery intents: take, release, deliver.

``take`` is the one multi-record intent, because an operator picks up a handful
of parcels at once. It is also the highest-conflict intent in the system: two
operators can both take the same parcel while neither can see the other, so it
answers per id and a partial result is normal rather than exceptional.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from .. import intents
from ..dependencies import Caller, pickup_delivery_caller
from ..schemas import (
    DeliverInput,
    DeliveryIntentResult,
    IntentFailure,
    ReleaseDeliveryInput,
    TakeDeliveriesInput,
    TakeDeliveriesResult,
)

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


@router.post(
    "/take",
    response_model=TakeDeliveriesResult,
    summary="Take a selection of parcels out for delivery",
)
def take_deliveries(
    data: TakeDeliveriesInput,
    caller_obj: Annotated[Caller, Depends(pickup_delivery_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TakeDeliveriesResult:
    reservation = intents.claim(caller_obj, "deliveries.take", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    occurred_at = intents.event_time(caller_obj, data.occurred_at)
    wanted = sorted(set(data.ids))
    intents.lock(caller_obj, wanted)

    Picking = caller_obj.env["stock.picking"]
    takeable = Picking.browse()
    failed: list[IntentFailure] = []
    for picking_id in wanted:
        try:
            delivery = intents.in_scope(caller_obj, picking_id, ["delivery"])
        except Exception as error:
            failed.append(IntentFailure(
                id=picking_id,
                code=getattr(error, "code", "NOT_IN_SCOPE"),
                message=getattr(error, "message", str(error)),
                detail=getattr(error, "detail", {}) or {},
            ))
            continue
        if delivery.sudi_delivery_stage == "awaiting":
            takeable |= delivery
            continue
        if delivery.sudi_pickup_user_id == caller_obj.user:
            # Already in this operator's own bag: their earlier tap landed.
            takeable |= delivery
            continue
        held = intents.conflict_taken(delivery)
        failed.append(IntentFailure(
            id=picking_id, code=held.code, message=held.message, detail=held.detail
        ))

    to_take = takeable.filtered(
        lambda record: record.sudi_delivery_stage == "awaiting"
    )
    if to_take:
        to_take.with_user(caller_obj.user).with_context(
            sudi_device_uid=caller_obj.device.device_uid
        ).action_sudi_take_for_delivery(occurred_at=occurred_at)

    return reservation.store(TakeDeliveriesResult(
        taken=[
            intents.document(caller_obj, "delivery", delivery.id)
            for delivery in takeable
        ],
        failed=failed,
    ).model_dump())


@router.post(
    "/{picking_id}/release",
    response_model=DeliveryIntentResult,
    summary="Hand a parcel back to the awaiting pool",
)
def release_delivery(
    picking_id: int,
    data: ReleaseDeliveryInput,
    caller_obj: Annotated[Caller, Depends(pickup_delivery_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DeliveryIntentResult:
    reservation = intents.claim(caller_obj, "deliveries.release", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    delivery = intents.record(caller_obj, picking_id, ["delivery"], codes=("outgoing",))
    intents.lock(caller_obj, [picking_id])

    if delivery.sudi_delivery_stage == "awaiting":
        return reservation.store(DeliveryIntentResult(
            code=intents.RESULT_ALREADY_DONE,
            record=intents.document(caller_obj, "delivery", picking_id),
        ).model_dump())
    if delivery.sudi_delivery_stage != "out":
        raise intents.conflict_taken(
            delivery, "This parcel is no longer out for delivery."
        )
    if delivery.sudi_pickup_user_id != caller_obj.user:
        # Releasing somebody else's parcel would put a bag they are carrying
        # back into the pool.
        raise intents.conflict_taken(
            delivery, f"This parcel is out with {delivery.sudi_pickup_user_id.name}."
        )

    intents.event_time(caller_obj, data.occurred_at)
    delivery.with_user(caller_obj.user).action_sudi_release_delivery()

    return reservation.store(DeliveryIntentResult(
        code=intents.RESULT_OK,
        record=intents.document(caller_obj, "delivery", picking_id),
    ).model_dump())


@router.post(
    "/{picking_id}/deliver",
    response_model=DeliveryIntentResult,
    summary="Mark a parcel delivered, with its proof of delivery",
)
def deliver(
    picking_id: int,
    data: DeliverInput,
    caller_obj: Annotated[Caller, Depends(pickup_delivery_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DeliveryIntentResult:
    reservation = intents.claim(caller_obj, "deliveries.deliver", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    delivery = intents.record(caller_obj, picking_id, ["delivery"], codes=("outgoing",))
    intents.lock(caller_obj, [picking_id])

    if delivery.state == "done":
        # Delivered is delivered. Whoever got there first, the outcome the
        # operator wanted has happened, so this is a success and not a
        # conflict; `detail` says who, for the UI to explain.
        record = intents.document(caller_obj, "delivery", picking_id)
        return reservation.store(DeliveryIntentResult(
            code=intents.RESULT_ALREADY_DONE, record=record
        ).model_dump())
    if delivery.state == "cancel":
        raise intents.conflict_taken(delivery, "This delivery was cancelled.")

    occurred_at = intents.event_time(caller_obj, data.occurred_at)
    photos = intents.claim_uploads(caller_obj, data.upload_ids, delivery)
    signature = None
    if data.signature_upload_id:
        signature = intents.claim_uploads(
            caller_obj, [data.signature_upload_id], delivery
        )[0]

    delivery.with_user(caller_obj.user).with_context(
        sudi_device_uid=caller_obj.device.device_uid
    ).action_sudi_mark_delivered(
        occurred_at=occurred_at,
        receiver_name=data.receiver_name,
        signature=signature,
        photo_datas=photos,
    )

    return reservation.store(DeliveryIntentResult(
        code=intents.RESULT_OK,
        record=intents.document(caller_obj, "delivery", picking_id),
    ).model_dump())
