"""Stateless JWT issuing and verification for the Sudi mobile apps.

HS256 over a secret in ``ir.config_parameter``, generated once at install.
Revocation needs no token store: every token carries both the user's epoch and
the *device's* epoch, and raising either invalidates the tokens that quote it.

The lifetimes are long on purpose. An operator out of coverage all morning has
to be able to flush their queue at 14:00 without a round trip they cannot make,
so an access token lasts a day and a refresh token a quarter. Revocation is by
device (``sudi.api.device``), not by expiry.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt

ALGORITHM = "HS256"

PARAM_SECRET = "sudi_api.jwt_secret"
PARAM_ISSUER = "sudi_api.jwt_issuer"
PARAM_ACCESS_TTL = "sudi_api.access_token_ttl_minutes"
PARAM_REFRESH_TTL = "sudi_api.refresh_token_ttl_days"

DEFAULT_ISSUER = "sudi"
DEFAULT_ACCESS_TTL_MINUTES = 24 * 60
DEFAULT_REFRESH_TTL_DAYS = 90

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"
TOKEN_TYPE_REGISTER = "register"

# Long enough to type a name, short enough that a leaked one is worthless.
REGISTER_TTL_MINUTES = 15

AUDIENCE_FIELD = "field"
AUDIENCE_CUSTOMER = "customer"


class TokenError(Exception):
    """Raised when a token cannot be trusted. Callers map this to a 401."""


def _params(env):
    return env["ir.config_parameter"].sudo()


def get_secret(env) -> str:
    secret = _params(env).get_param(PARAM_SECRET)
    if not secret:
        # Never fall back to a default: an unsigned-in-practice API is worse
        # than a broken one.
        raise TokenError(
            f"Missing system parameter {PARAM_SECRET!r}. "
            "Reinstall sudi_api or set it manually."
        )
    return secret


def get_issuer(env) -> str:
    return _params(env).get_param(PARAM_ISSUER, DEFAULT_ISSUER)


def _int_param(env, key, default) -> int:
    try:
        value = int(_params(env).get_param(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def access_token_ttl(env) -> timedelta:
    return timedelta(minutes=_int_param(env, PARAM_ACCESS_TTL, DEFAULT_ACCESS_TTL_MINUTES))


def refresh_token_ttl(env) -> timedelta:
    return timedelta(days=_int_param(env, PARAM_REFRESH_TTL, DEFAULT_REFRESH_TTL_DAYS))


def encode_token(env, user, device, audience, token_type, ttl) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": get_issuer(env),
        "sub": str(user.id),
        "aud": audience,
        "typ": token_type,
        "epoch": user.sudo().sudi_api_token_epoch,
        "dev": device.device_uid,
        "dev_epoch": device.token_epoch,
        # A hint so the client can draw its navigation before the first call.
        # Never a gate: every route checks the group server-side.
        "roles": user._sudi_api_roles(),
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, get_secret(env), algorithm=ALGORITHM)


def decode_token(env, token, audience, expected_type) -> dict:
    """Return the verified payload of ``token`` or raise :class:`TokenError`."""
    try:
        payload = jwt.decode(
            token,
            get_secret(env),
            algorithms=[ALGORITHM],
            issuer=get_issuer(env),
            audience=audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as err:
        raise TokenError("Token has expired") from err
    except jwt.InvalidAudienceError as err:
        # A customer token presented to the field API, or the reverse.
        raise TokenError("This token is not valid for this API") from err
    except jwt.InvalidTokenError as err:
        raise TokenError("Invalid token") from err

    if payload.get("typ") != expected_type:
        # Stops a refresh token being replayed as an access token, and back.
        raise TokenError(f"Expected a {expected_type} token")
    return payload


def issue_token_pair(env, user, device, audience) -> dict:
    """Mint an access/refresh pair, shared by the password and OTP paths."""
    ttl = access_token_ttl(env)
    return {
        "access_token": encode_token(env, user, device, audience, TOKEN_TYPE_ACCESS, ttl),
        "refresh_token": encode_token(
            env, user, device, audience, TOKEN_TYPE_REFRESH, refresh_token_ttl(env)
        ),
        "expires_in": int(ttl.total_seconds()),
        "device_uid": device.device_uid,
        "roles": user._sudi_api_roles(),
    }


def encode_registration_token(env, phone, partner=None) -> str:
    """Proof that a phone number was verified, with no account behind it yet.

    The web flow kept this in ``request.session``. A stateless API cannot, and
    the alternative -- trusting a phone number in the register call -- would let
    anyone create an account for any number. So verification hands back a token
    that says only "this number answered a code", and registration will not
    proceed without it.

    Deliberately not an access token: it has no ``sub`` because there is no
    user, and its own ``typ`` means it cannot be presented as a bearer token.
    """
    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": get_issuer(env),
        "aud": AUDIENCE_CUSTOMER,
        "typ": TOKEN_TYPE_REGISTER,
        "phone": phone,
        "pid": partner.id if partner else None,
        "iat": now,
        "exp": now + timedelta(minutes=REGISTER_TTL_MINUTES),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, get_secret(env), algorithm=ALGORITHM)


def decode_registration_token(env, token) -> dict:
    """``{"phone": ..., "pid": ...}`` or :class:`TokenError`."""
    try:
        payload = jwt.decode(
            token,
            get_secret(env),
            algorithms=[ALGORITHM],
            issuer=get_issuer(env),
            audience=AUDIENCE_CUSTOMER,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as err:
        raise TokenError("This registration has expired. Start again.") from err
    except jwt.InvalidTokenError as err:
        raise TokenError("Invalid registration token") from err
    if payload.get("typ") != TOKEN_TYPE_REGISTER:
        raise TokenError("Expected a registration token")
    if not payload.get("phone"):
        raise TokenError("Invalid registration token")
    return payload
