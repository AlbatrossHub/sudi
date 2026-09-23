"""Password login, for field staff only.

Refresh, logout and who-am-I are in ``session_router``: both apps need them and
they behave identically, whereas a password login must never appear on the
customer contract -- customer accounts are created with a discarded random
password precisely so that OTP is their only way in.

There is no signup and no password reset here on purpose: staff accounts are
created by an administrator in Odoo (decision in plan section 5.2). A wrong
password answers with a deliberately vague message -- the API never reveals
whether a login exists.
"""

from typing import Annotated

from odoo.api import Environment
from odoo.exceptions import AccessDenied

from odoo.addons.fastapi.dependencies import odoo_env

from fastapi import APIRouter, Depends, status

from ..dependencies import assert_audience_allowed, audience
from ..errors import unauthorized
from ..jwt_tokens import issue_token_pair
from ..schemas import LoginInput, TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Exchange staff credentials and a device for a token pair",
)
def login(
    data: LoginInput,
    env: Annotated[Environment, Depends(odoo_env)],
    aud: Annotated[str, Depends(audience)],
) -> TokenPair:
    try:
        auth_info = (
            env["res.users"]
            .sudo()
            .authenticate(
                credential={
                    "type": "password",
                    "login": data.login,
                    "password": data.password,
                },
                user_agent_env={"interactive": False},
            )
        )
    except AccessDenied as err:
        raise unauthorized("Invalid credentials") from err

    uid = auth_info.get("uid")
    if not uid:
        raise unauthorized("Invalid credentials")

    user = env["res.users"].sudo().browse(uid)
    assert_audience_allowed(env, user, aud)
    device = env["sudi.api.device"]._sudi_register(
        user,
        data.device.device_uid,
        audience=aud,
        platform=data.device.platform,
        app_version=data.device.app_version,
        push_token=data.device.push_token,
    )
    return TokenPair(**issue_token_pair(env, user, device, aud))
