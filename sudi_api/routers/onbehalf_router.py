"""Posting a jangad for a customer, from the staff app.

The member of staff builds **the same request a customer would**: who it is
for, the services, the pages, the pickup address. It lands in the pickup pool
like any customer jangad and is collected with the ordinary "Confirm pickup",
which the app offers straight after posting.

Gated twice: on an app role, because the tick on the user form is an extra
ability and never a way into the app, and on the tick itself
(``res.users.sudi_can_post_jangad_on_behalf``).
"""

import re
from typing import Annotated

from odoo.exceptions import UserError
from odoo.tools.translate import _

from fastapi import APIRouter, Depends, Header, Response, status

from .. import intents
from ..dependencies import Caller, on_behalf_caller
from ..errors import SudiApiError, validation
from ..etags import conditional
from ..schemas import (
    CustomerEntry,
    OnBehalfCustomer,
    OnBehalfJangadInput,
    OnBehalfJangadResult,
)
from .customer_auth_router import _users_for

router = APIRouter(tags=["on-behalf"])

# A phone app holds the whole directory, so it has to stay a size a phone can
# hold. Past this it is truncated, alphabetically.
DIRECTORY_LIMIT = 5000


def _valid_phone(env, raw):
    """``raw`` normalised to ten digits, or ``""`` if it cannot be one.

    Stricter than ``_sudi_normalize_phone`` alone, which keeps the last ten
    digits of anything longer: that is right for ``+91 98250 11111`` and wrong
    for the WhatsApp group ids the connector files as partners
    (``+120363176023391403``), which would otherwise appear in the directory
    as customers with an invented number. Ten digits, optionally after a
    leading 0 or the 91 country code.
    """
    digits = re.sub(r"\D+", "", raw or "")
    if not (
        len(digits) == 10
        or (len(digits) == 11 and digits.startswith("0"))
        or (len(digits) == 12 and digits.startswith("91"))
    ):
        return ""
    return env["stock.picking"].sudo()._sudi_normalize_phone(digits)


def _phone_fields(env):
    return [name for name in ("phone", "mobile") if name in env["res.partner"]._fields]


def _partner_phone(env, partner):
    """The customer's usable number: their own, else their first contact's."""
    fields_ = _phone_fields(env)
    for candidate in partner | partner.child_ids:
        for name in fields_:
            phone = _valid_phone(env, candidate[name])
            if phone:
                return phone
    return ""


def _is_staff_number(env, partner):
    """The customer-registration rule, verbatim.

    A number with accounts behind it, none of them a customer's, belongs to
    staff. Refused rather than posted for: a jangad filed against a member of
    staff's own record is one person with two roles, which is exactly what
    registration refuses for the same reason.
    """
    users = _users_for(env, partner)
    return bool(users) and not users.filtered(lambda user: user.share)


def _never_customers(env):
    """Partner ids that are never offered: staff (current or former) and the
    companies themselves."""
    Users = env["res.users"].sudo().with_context(active_test=False)
    staff = set(Users.search([("share", "=", False)]).partner_id.ids)
    companies = set(
        env["res.company"].sudo().with_context(active_test=False).search([]).partner_id.ids
    )
    return staff | companies


def _account_partner_ids(env):
    """``(staff, customers)``: partner ids behind active internal and portal
    accounts, for applying the registration rule to a whole directory at once
    instead of one search per customer."""
    users = env["res.users"].sudo().search([])
    staff = set(users.filtered(lambda user: not user.share).partner_id.ids)
    customers = set(users.filtered(lambda user: user.share).partner_id.ids)
    return staff, customers


@router.get(
    "/customers",
    response_model=list[CustomerEntry],
    summary="The customers a jangad can be posted for",
    responses={status.HTTP_304_NOT_MODIFIED: {"description": "Nothing has changed"}},
)
def list_customers(
    caller_obj: Annotated[Caller, Depends(on_behalf_caller)],
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
):
    """Every customer with a usable phone number, with their pickup addresses.

    One record per customer (the commercial partner), never the company itself,
    never a member of staff, never an archived record: the same people the
    posting route will accept, so the phone never offers a choice the server
    then refuses.
    """
    env = caller_obj.env
    Partner = env["res.partner"].sudo()
    Picking = env["stock.picking"].sudo()

    excluded = _never_customers(env)
    staff_ids, customer_ids = _account_partner_ids(env)

    fields_ = _phone_fields(env)
    domain = [(fields_[0], "!=", False)]
    for name in fields_[1:]:
        domain = ["|", (name, "!=", False)] + domain
    phones = {}
    for partner in Partner.search(domain + [("active", "=", True)]):
        commercial = partner.commercial_partner_id
        if not commercial.active or commercial.id in excluded:
            continue
        phone = next(
            (p for p in (_valid_phone(env, partner[name]) for name in fields_) if p), ""
        )
        if not phone:
            continue
        if partner == commercial:
            # The customer's own number wins over a contact person's.
            phones[commercial.id] = phone
        else:
            phones.setdefault(commercial.id, phone)

    payload = []
    for commercial in Partner.browse(sorted(phones)).sorted(
        key=lambda record: ((record.name or "").lower(), record.id)
    ):
        # _is_staff_number, for the whole directory in one pass.
        family = {commercial.id, *commercial.child_ids.ids}
        if family & staff_ids and not family & customer_ids:
            continue
        payload.append({
            "id": commercial.id,
            "name": commercial.name or "",
            "phone": phones[commercial.id],
            "gst_state": commercial._sudi_gst_state(),
            "addresses": [
                {
                    "id": suggestion["id"],
                    "label": suggestion["name"],
                    "address": suggestion["address"],
                    "is_default": suggestion["is_default"],
                }
                for suggestion in Picking.sudi_get_public_pickup_address_suggestions(
                    partner=commercial
                )
            ],
        })
        if len(payload) >= DIRECTORY_LIMIT:
            break
    return conditional(response, payload, if_none_match)


def _resolve_customer(caller_obj: Caller, customer: OnBehalfCustomer):
    """``(partner or empty, phone)`` for the jangad, or a typed refusal."""
    env = caller_obj.env
    Partner = env["res.partner"].sudo()
    if customer.partner_id:
        partner = Partner.browse(customer.partner_id).exists()
        if not partner or not partner.active:
            raise validation(
                "That customer is no longer on file. Refresh the customer list.",
                detail={"partner_id": customer.partner_id},
            )
        partner = partner.commercial_partner_id
        phone = _partner_phone(env, partner)
        if not phone:
            raise validation(
                "This customer has no usable phone number. Type the number "
                "instead, or ask the office to add one.",
                detail={"partner_id": partner.id},
            )
    else:
        phone = _valid_phone(env, customer.phone)
        if not phone:
            raise validation(
                "Enter a valid ten-digit phone number.",
                detail={"phone": customer.phone},
            )
        partner = env["stock.picking"].sudo()._sudi_find_partner_by_phone(phone)
        if not partner and (customer.name or "").strip():
            # A new customer, named by the person standing in front of them,
            # rather than the "Jangad Customer <number>" a bare upload gets.
            partner = Partner.create({"name": customer.name.strip(), "phone": phone})

    if partner and (partner.id in _never_customers(env) or _is_staff_number(env, partner)):
        raise validation(
            "This phone number belongs to a staff account, so a jangad cannot "
            "be posted for it.",
            detail={"reason": "staff_phone", "phone": phone},
        )
    return partner, phone


@router.post(
    "/jangads",
    response_model=OnBehalfJangadResult,
    summary="Post a jangad for a customer",
)
def post_jangad(
    data: OnBehalfJangadInput,
    caller_obj: Annotated[Caller, Depends(on_behalf_caller)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> OnBehalfJangadResult:
    """An offline intent, like the customer's own ``POST /jangad``.

    Captured at the counter or on the phone, possibly with no signal, so it
    carries the whole machinery: a capture-time ``Idempotency-Key``, a clamped
    ``occurred_at``, and pages staged beforehand with ``POST /uploads``.
    """
    reservation = intents.claim(caller_obj, "field.jangads", idempotency_key)
    if reservation.is_replay:
        return reservation.replay

    occurred_at = intents.event_time(caller_obj, data.occurred_at)
    partner, phone = _resolve_customer(caller_obj, data.customer)

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
            services=[service.model_dump() for service in data.services],
            source="on_behalf",
            posted_by=caller_obj.user,
            partner=partner or None,
        )
    except UserError as error:
        raise validation(str(error)) from error

    uploads._sudi_bind(receipt)
    # Provenance: who posted it, when it was captured, from which handset.
    # sudo keeps env.user, so the note names the operator, not a superuser.
    receipt.with_context(
        sudi_device_uid=caller_obj.device.device_uid
    )._sudi_post_event_provenance(_("Jangad posted for the customer"), occurred_at)
    if data.note:
        receipt.message_post(
            body=_("Note from %(who)s: %(note)s", who=caller_obj.user.name, note=data.note),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

    try:
        pickup = intents.document(caller_obj, "pickup", receipt.id)
    except SudiApiError:
        pickup = None
    return reservation.store(
        OnBehalfJangadResult(
            picking_id=receipt.id, reference=receipt.name, pickup=pickup,
        ).model_dump(),
        record=receipt,
    )
