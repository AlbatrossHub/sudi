"""Refresh, logout and who-am-I. Mounted on both apps.

Identical behaviour for staff and customers, so it lives in one place; the
difference between the two is the audience gate, which is applied here rather
than being duplicated per app.
"""

from typing import Annotated

from odoo.api import Environment
from odoo.exceptions import AccessError

from odoo.addons.fastapi.dependencies import odoo_env

from fastapi import APIRouter, Depends, status

from ..dependencies import Caller, assert_audience_allowed, audience, caller
from ..errors import device_revoked, unauthorized
from ..jwt_tokens import (
    TOKEN_TYPE_REFRESH,
    TokenError,
    decode_token,
    issue_token_pair,
)
from ..schemas import MeInfo, RefreshInput, TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Trade a refresh token for a new pair",
)
def refresh(
    data: RefreshInput,
    env: Annotated[Environment, Depends(odoo_env)],
    aud: Annotated[str, Depends(audience)],
) -> TokenPair:
    try:
        payload = decode_token(env, data.refresh_token, aud, TOKEN_TYPE_REFRESH)
    except TokenError as err:
        raise unauthorized(str(err)) from err

    user = env["res.users"].sudo().browse(int(payload["sub"])).exists()
    if not user or not user.active:
        raise unauthorized("User is no longer active")
    if payload.get("epoch") != user.sudi_api_token_epoch:
        raise unauthorized("Token has been revoked")
    assert_audience_allowed(env, user, aud)

    try:
        device = env["sudi.api.device"]._sudi_resolve(
            user, payload.get("dev"), payload.get("dev_epoch")
        )
    except AccessError as err:
        # A refresh token is what actually sits on a lost handset, so this is
        # the request revocation exists to stop.
        raise device_revoked(str(err)) from err

    device._sudi_touch()
    return TokenPair(**issue_token_pair(env, user, device, aud))


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign this device out, leaving the user's others alone",
)
def logout(caller_obj: Annotated[Caller, Depends(caller)]) -> None:
    # Tokens are stateless, so signing out means raising this device's epoch.
    # Deliberately one device and not all of them.
    caller_obj.device.action_revoke()


@router.get("/me", response_model=MeInfo, summary="The caller's own profile")
def me(caller_obj: Annotated[Caller, Depends(caller)]) -> MeInfo:
    caller_obj.device._sudi_touch()
    return MeInfo.from_caller(caller_obj)
