"""Customer authentication: a phone number, a WhatsApp code, and a name.

No password anywhere. A customer's portal user is created with a discarded
random one precisely so that OTP is the only way in.

The web flow this replaces kept the plaintext code and the "this number is
verified" fact in ``request.session``. Neither can serve a stateless API, so
the code lives hashed in ``sudi.auth.otp`` and the verified fact comes back as
a short-lived registration token.
"""

import uuid
from typing import Annotated

from odoo.api import Environment

from odoo.addons.diamond.models.sudi_auth_otp import (
    PURPOSE_LOGIN,
    SudiOtpError,
    SudiOtpRateLimited,
    SudiOtpUndeliverable,
)
from odoo.addons.fastapi.dependencies import odoo_env

from fastapi import APIRouter, Depends, status

from ..dependencies import assert_audience_allowed, audience
from ..errors import HTTP_422, SudiApiError, forbidden, validation
from ..jwt_tokens import (
    TokenError,
    decode_registration_token,
    encode_registration_token,
    issue_token_pair,
)
from ..schemas import (
    OtpRequestInput,
    OtpRequestResult,
    OtpVerifyInput,
    OtpVerifyResult,
    RegisterInput,
    TokenPair,
)

router = APIRouter(prefix="/auth", tags=["customer-auth"])


def _otp_failure(error):
    """Map an OTP refusal onto the client contract.

    Rate limiting is a 429 and *retryable*: waiting is exactly what the client
    should do. An undeliverable code is a 502, because telling a customer to
    wait for a message that was never sent is the one answer that wastes their
    afternoon.
    """
    code = getattr(error, "sudi_code", "OTP_INVALID")
    if isinstance(error, SudiOtpRateLimited):
        return SudiApiError(
            status.HTTP_429_TOO_MANY_REQUESTS, code, str(error), retryable=True
        )
    if isinstance(error, SudiOtpUndeliverable):
        return SudiApiError(status.HTTP_502_BAD_GATEWAY, code, str(error), retryable=True)
    return SudiApiError(HTTP_422, code, str(error))


def _users_for(env: Environment, partner):
    """Every active account behind this partner, staff included.

    Filtering to portal users here would hide a staff account rather than
    refuse it, and registration would then create a second user on the same
    partner: one person, two accounts, two sets of rights.
    """
    if not partner:
        return env["res.users"]
    partners = partner | partner.child_ids
    return env["res.users"].sudo().search(
        [("partner_id", "in", partners.ids), ("active", "=", True)]
    )


def _staff_account_refusal():
    return forbidden(
        "This phone number belongs to a staff account. Staff sign in to the "
        "field app with their own login."
    )


@router.post(
    "/otp/request",
    response_model=OtpRequestResult,
    summary="Send a verification code over WhatsApp",
)
def request_otp(
    data: OtpRequestInput, env: Annotated[Environment, Depends(odoo_env)]
) -> OtpRequestResult:
    Otp = env["sudi.auth.otp"].sudo()
    partner = env["stock.picking"].sudo()._sudi_find_partner_by_phone(data.phone)
    try:
        Otp._sudi_issue(data.phone, PURPOSE_LOGIN, partner=partner or None)
    except SudiOtpError as error:
        raise _otp_failure(error) from error
    return OtpRequestResult(
        sent=True,
        expires_in=Otp._sudi_param("sudi_auth.otp_ttl_minutes") * 60,
        resend_after=Otp._sudi_param("sudi_auth.otp_resend_cooldown_seconds"),
        channel="whatsapp",
    )


@router.post(
    "/otp/verify",
    response_model=OtpVerifyResult,
    summary="Verify a code — tokens, or a registration token",
)
def verify_otp(
    data: OtpVerifyInput,
    env: Annotated[Environment, Depends(odoo_env)],
    aud: Annotated[str, Depends(audience)],
) -> OtpVerifyResult:
    Otp = env["sudi.auth.otp"].sudo()
    try:
        Otp._sudi_verify(data.phone, data.code, PURPOSE_LOGIN)
    except SudiOtpError as error:
        raise _otp_failure(error) from error

    phone = env["stock.picking"].sudo()._sudi_normalize_phone(data.phone)
    partner = env["stock.picking"].sudo()._sudi_find_partner_by_phone(phone)
    users = _users_for(env, partner)
    portal = users.filtered(lambda record: record.share)
    if not portal:
        if users:
            raise _staff_account_refusal()
        # No account yet. The partner id travels in the token so registration
        # links to the record the office may already have created from a
        # jangad upload, instead of making a second one.
        return OtpVerifyResult(
            registration_required=True,
            registration_token=encode_registration_token(env, phone, partner or None),
            tokens=None,
        )

    user = portal[0]
    assert_audience_allowed(env, user, aud)
    device = env["sudi.api.device"]._sudi_register(
        user,
        data.device.device_uid,
        audience=aud,
        platform=data.device.platform,
        app_version=data.device.app_version,
        push_token=data.device.push_token,
    )
    return OtpVerifyResult(
        registration_required=False,
        registration_token=None,
        tokens=TokenPair(**issue_token_pair(env, user, device, aud)),
    )


@router.post(
    "/register",
    response_model=TokenPair,
    summary="Finish signing up a verified phone number",
)
def register(
    data: RegisterInput,
    env: Annotated[Environment, Depends(odoo_env)],
    aud: Annotated[str, Depends(audience)],
) -> TokenPair:
    try:
        claim = decode_registration_token(env, data.registration_token)
    except TokenError as error:
        raise validation(str(error)) from error

    phone = claim["phone"]
    Partner = env["res.partner"].sudo()
    partner = Partner.browse(claim["pid"]).exists() if claim.get("pid") else Partner
    if not partner:
        partner = env["stock.picking"].sudo()._sudi_find_partner_by_phone(phone)

    vals = {"name": data.name.strip(), "phone": phone}
    if partner:
        # A guest record the office created from a jangad upload gets a real
        # name rather than a duplicate alongside it.
        partner.write(vals)
    else:
        partner = Partner.create({
            **vals, "is_company": False, "company_type": "person",
        })

    if data.vat:
        from odoo.exceptions import ValidationError

        try:
            partner._sudi_apply_gstin(data.vat)
        except ValidationError as error:
            raise validation(error.args[0]) from error
    else:
        # Decision D3: a customer may finish without a GSTIN and be asked
        # again later, so this is "missing" and not "skipped".
        partner.write({"x_skip_gst": False})

    users = _users_for(env, partner)
    if users and not users.filtered(lambda record: record.share):
        raise _staff_account_refusal()
    user = users.filtered(lambda record: record.share)[:1]
    if not user:
        user = env["res.users"].sudo().create({
            "name": partner.name,
            "login": phone,
            "partner_id": partner.id,
            "group_ids": [(6, 0, [env.ref("base.group_portal").id])],
            # Discarded on purpose: OTP is the only way into a customer
            # account, so there is no password to steal or to reset.
            "password": uuid.uuid4().hex,
            "active": True,
        })
    device = env["sudi.api.device"]._sudi_register(
        user,
        data.device.device_uid,
        audience=aud,
        platform=data.device.platform,
        app_version=data.device.app_version,
        push_token=data.device.push_token,
    )
    return TokenPair(**issue_token_pair(env, user, device, aud))
