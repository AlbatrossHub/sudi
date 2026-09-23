"""Uploading a jangad. The whole of the customer's write surface.

Receipts, delivery orders and invoices are deliberately **not** here: the
customer app's job right now is to get a photograph of a handwritten slip into
the office, and nothing else.

This is the customer's offline intent, so it carries the same machinery as the
field ones: an ``Idempotency-Key`` captured with the tap, an ``occurred_at``
from the device, and pages staged beforehand through ``POST /uploads``.
"""

from typing import Annotated

from odoo.exceptions import UserError

from fastapi import APIRouter, Depends, Header

from .. import intents
from ..dependencies import Caller, customer_caller
from ..errors import validation
from ..schemas import (
    AddressSuggestion,
    JangadSubmitInput,
    JangadSubmitResult,
)

router = APIRouter(tags=["jangad"])


def _caller_phone(caller_obj: Caller) -> str:
    """The caller's **own** number, from their account.

    Never from the request body. The public PWA endpoint takes a phone from a
    form, which is why it can only create receipts against whatever number was
    typed; an authenticated customer has exactly one number, and letting the
    body override it would let anyone file a jangad in somebody else's name.
    """
    partner = caller_obj.user.partner_id.sudo()
    raw = partner.phone or partner.commercial_partner_id.phone or ""
    phone = caller_obj.env["stock.picking"].sudo()._sudi_normalize_phone(raw)
    if len(phone) != 10:
        raise validation(
            "Your account has no valid phone number, so a pickup cannot be "
            "arranged. Please contact the office.",
        )
    return phone


@router.get(
    "/addresses",
    response_model=list[AddressSuggestion],
    summary="The caller's known pickup addresses",
)
def list_addresses(
    caller_obj: Annotated[Caller, Depends(customer_caller)]
) -> list[AddressSuggestion]:
    partner = caller_obj.user.partner_id.sudo()
    suggestions = (
        caller_obj.env["stock.picking"]
        .sudo()
        .sudi_get_public_pickup_address_suggestions(partner=partner)
    )
    return [AddressSuggestion(**suggestion) for suggestion in suggestions]


@router.post(
    "/jangad",
    response_model=JangadSubmitResult,
    summary="Submit a jangad's pages and arrange a pickup",
)
def submit_jangad(
    data: JangadSubmitInput,
    caller_obj: Annotated[Caller, Depends(customer_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JangadSubmitResult:
    reservation = intents.claim(caller_obj, "customer.jangad", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    phone = _caller_phone(caller_obj)
    occurred_at = intents.event_time(caller_obj, data.occurred_at)

    # Resolved before the receipt exists, because the receipt is built *from*
    # these files; they are bound to it immediately afterwards.
    Uploads = caller_obj.env["sudi.upload"]
    try:
        uploads = Uploads._sudi_resolve(caller_obj.user, data.upload_ids)
    except UserError as error:
        raise validation(str(error)) from error
    pages = uploads._sudi_datas()

    Picking = caller_obj.env["stock.picking"].sudo()
    try:
        receipt = Picking.sudi_create_public_jangad_receipt(
            phone,
            pages[0],
            pickup_address_id=data.pickup_address_id or False,
            manual_pickup_address=(data.manual_pickup_address or "").strip(),
            extra_pages=pages[1:],
        )
    except UserError as error:
        raise validation(str(error)) from error

    uploads._sudi_bind(receipt)
    # The customer's capture time, kept where the office can see it: there is
    # no field for "submitted at" and inventing one for a note would be worse.
    receipt.with_context(
        sudi_device_uid=caller_obj.device.device_uid
    )._sudi_post_event_provenance("Jangad upload", occurred_at)

    return reservation.store(
        JangadSubmitResult(
            reference=receipt.name,
            pages=receipt.sudi_jangad_page_count,
            pickup_address=receipt.sudi_pickup_address or None,
            submitted_at=occurred_at.isoformat(),
        ).model_dump(),
        record=receipt,
    )
