"""Turning a bearer token into an authenticated Odoo environment.

The chain, and why each link exists:

``Authorization: Bearer <jwt>``
  -> ``access_payload``   verified, and checked against *this* endpoint's audience
  -> ``caller``           the user, the device, and a non-sudo env bound to them
  -> role gates           the group a route actually requires

The env is deliberately **not** sudo, so Odoo's record rules and ACLs apply to
everything a router does. It also carries ``sudi_device_uid`` in its context,
which is what makes an offline event's provenance note in ``diamond`` name the
handset it came from.
"""

import dataclasses
from typing import Annotated

from odoo.api import Environment
from odoo.exceptions import AccessError

from odoo.addons.base.models.res_users import ResUsers
from odoo.addons.fastapi.dependencies import fastapi_endpoint_id, odoo_env

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import device_revoked, forbidden, unauthorized
from .jwt_tokens import (
    AUDIENCE_CUSTOMER,
    AUDIENCE_FIELD,
    TOKEN_TYPE_ACCESS,
    TokenError,
    decode_token,
)

bearer_scheme = HTTPBearer(
    scheme_name="SudiJWT",
    description="Access token returned by the login endpoint.",
    # Handled here so every auth failure comes back in the same envelope.
    auto_error=False,
)

APP_AUDIENCE = {"sudi_field": AUDIENCE_FIELD, "sudi_customer": AUDIENCE_CUSTOMER}

ROLE_PICKUP_DELIVERY = "pickup_delivery"
ROLE_JOB_WORK = "job_work"
ROLE_CUSTOMER = "customer"


@dataclasses.dataclass
class Caller:
    """Everything a router needs about who is asking."""

    user: ResUsers
    device: object
    env: Environment
    roles: list

    def has_role(self, role):
        return role in self.roles


def audience(
    endpoint_id: Annotated[int, Depends(fastapi_endpoint_id)],
    env: Annotated[Environment, Depends(odoo_env)],
) -> str:
    """Which app served this request, from the endpoint record itself.

    One dependency chain therefore serves both endpoints, and a customer token
    presented to the field API fails at the signature check rather than three
    layers deeper on a group test.

    Read by id and with ``sudo()``, deliberately not through the OCA
    ``fastapi_endpoint`` dependency: that one reads the record as the *request*
    user, and the request user is whoever an Odoo session cookie says it is. A
    member of staff with a live web session -- or a customer signed in to the
    /jangad PWA on the same device -- would then get a 403 about
    ``fastapi.endpoint``, on a route that never needed access to that record.
    """
    app = env["fastapi.endpoint"].sudo().browse(endpoint_id).app
    return APP_AUDIENCE.get(app, AUDIENCE_FIELD)


def access_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    env: Annotated[Environment, Depends(odoo_env)],
    aud: Annotated[str, Depends(audience)],
) -> dict:
    if credentials is None:
        raise unauthorized("Missing bearer token")
    try:
        return decode_token(env, credentials.credentials, aud, TOKEN_TYPE_ACCESS)
    except TokenError as err:
        raise unauthorized(str(err)) from err


def caller(
    payload: Annotated[dict, Depends(access_payload)],
    env: Annotated[Environment, Depends(odoo_env)],
) -> Caller:
    try:
        uid = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as err:
        raise unauthorized("Invalid token subject") from err

    user = env["res.users"].sudo().browse(uid).exists()
    if not user or not user.active:
        raise unauthorized("User is no longer active")
    if payload.get("epoch") != user.sudi_api_token_epoch:
        raise unauthorized("Token has been revoked")

    # Per-device revocation, which is the whole reason devices are records:
    # "this handset is lost" must not sign out the user's other phones.
    try:
        device = env["sudi.api.device"]._sudi_resolve(
            user, payload.get("dev"), payload.get("dev_epoch")
        )
    except AccessError as err:
        raise device_revoked(str(err)) from err

    bound_env = user.env(
        user=user.id,
        su=False,
        context=dict(
            user.env.context,
            sudi_device_uid=device.device_uid,
            sudi_api_audience=payload.get("aud"),
        ),
    )
    return Caller(
        user=user,
        device=device,
        env=bound_env,
        # Read from the user now, not from the token: a role removed this
        # morning must not keep working until the token expires tonight.
        roles=user._sudi_api_roles(),
    )


PARAM_FIELD_GROUPS_ONLY = "sudi_api.field_groups_only"


def assert_audience_allowed(env: Environment, user, aud: str) -> None:
    """Gate which Odoo users may hold a token for which app.

    Two different questions, which is why one flag cannot answer both: the
    field API must keep out accounts with no field role, and the customer API
    must keep out internal accounts altogether. Without the second, a member of
    staff could authenticate against the customer surface and read whatever a
    customer can.
    """
    if user.id == env.ref("base.public_user").id:
        raise unauthorized("Invalid credentials")
    if aud == AUDIENCE_CUSTOMER:
        if not user.share:
            raise forbidden("This account may not use the customer app.")
        return
    gate = env["ir.config_parameter"].sudo().get_param(PARAM_FIELD_GROUPS_ONLY, "1")
    if str(gate).strip().lower() in ("1", "true", "yes"):
        if not env["res.users"]._sudi_api_has_field_role(user):
            raise forbidden("This account has no field role.")


def _require(caller_obj: Caller, role: str, what: str) -> Caller:
    if not caller_obj.has_role(role):
        raise forbidden(f"Your account is not allowed to {what}.")
    return caller_obj


def field_caller(caller_obj: Annotated[Caller, Depends(caller)]) -> Caller:
    """Any member of staff who has an app role at all."""
    if not (
        caller_obj.has_role(ROLE_PICKUP_DELIVERY) or caller_obj.has_role(ROLE_JOB_WORK)
    ):
        raise forbidden("Your account has no field role.")
    return caller_obj


def pickup_delivery_caller(caller_obj: Annotated[Caller, Depends(caller)]) -> Caller:
    return _require(caller_obj, ROLE_PICKUP_DELIVERY, "collect and deliver parcels")


def job_work_caller(caller_obj: Annotated[Caller, Depends(caller)]) -> Caller:
    return _require(caller_obj, ROLE_JOB_WORK, "work on job-work receipts")


def customer_caller(caller_obj: Annotated[Caller, Depends(caller)]) -> Caller:
    return _require(caller_obj, ROLE_CUSTOMER, "use the customer app")
