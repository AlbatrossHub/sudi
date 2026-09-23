"""One error shape for every 4xx, and the middleware that guarantees it.

An offline client has to decide, with no human present, whether to retry an
intent or drop it. So every failure answers in the same envelope, and
``retryable`` is the only field that decision reads:

    {"code": "ALREADY_TAKEN",
     "message": "Taken for delivery by Rakesh at 09:12.",
     "retryable": false,
     "resync": [1234],
     "detail": {...}}

Why a middleware and not an exception handler: the OCA ``fastapi`` addon calls
``_clear_fastapi_exception_handlers`` on the whole mounted app tree, deliberately,
to hand error rendering back to Odoo. Any handler registered here would be
removed. Middlewares are not touched, so that is where this lives.
"""

import logging

from odoo.exceptions import AccessError, UserError, ValidationError

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from fastapi import status
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

_logger = logging.getLogger(__name__)

# The taxonomy. See docs/FLUTTER_INTEGRATION_BRIEF.md section 7.
CODE_VALIDATION = "VALIDATION"
CODE_NOT_IN_SCOPE = "NOT_IN_SCOPE"
CODE_ALREADY_DONE = "ALREADY_DONE"
CODE_ALREADY_TAKEN = "ALREADY_TAKEN"
CODE_ALREADY_CONFIRMED = "ALREADY_CONFIRMED"
CODE_STALE_INTENT = "STALE_INTENT"
CODE_CLOCK_SKEW = "CLOCK_SKEW"
CODE_LOCKED = "LOCKED"
CODE_AUTH = "AUTH"
CODE_DEVICE_REVOKED = "DEVICE_REVOKED"
CODE_SERVER = "SERVER"

# Spelled as an integer because Starlette renamed its constant
# (HTTP_422_UNPROCESSABLE_ENTITY -> ..._CONTENT) and both spellings warn on one
# version or the other. The number has not changed since 1999.
HTTP_422 = 422

# Whether the client should try the very same request again. The client must
# read this rather than infer from the status: a 409 can be either.
RETRYABLE = {CODE_LOCKED, CODE_SERVER}


class SudiApiError(Exception):
    """A failure the client is expected to handle, in the standard envelope."""

    def __init__(self, status_code, code, message, resync=None, detail=None,
                 headers=None, retryable=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.resync = list(resync or [])
        self.detail = detail or {}
        self.headers = headers or {}
        self.retryable = (code in RETRYABLE) if retryable is None else retryable
        # Odoo logs any exception without this at ERROR with a full traceback.
        # An expected 4xx should cost one line, not a hundred.
        self.loglevel = logging.INFO

    @property
    def envelope(self):
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "resync": self.resync,
            "detail": self.detail,
        }


def unauthorized(message="Missing or invalid credentials", code=CODE_AUTH, detail=None):
    return SudiApiError(
        status.HTTP_401_UNAUTHORIZED,
        code,
        message,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def device_revoked(message="This device is no longer allowed to sign in."):
    """The remote-wipe signal: the client clears its local database on this."""
    return unauthorized(message, code=CODE_DEVICE_REVOKED)


def forbidden(message, detail=None):
    return SudiApiError(status.HTTP_403_FORBIDDEN, CODE_AUTH, message, detail=detail)


def validation(message, detail=None):
    return SudiApiError(
        HTTP_422, CODE_VALIDATION, message, detail=detail
    )


def not_in_scope(message="This record is not in your work list.", resync=None):
    return SudiApiError(
        status.HTTP_404_NOT_FOUND, CODE_NOT_IN_SCOPE, message, resync=resync
    )


def conflict(code, message, resync=None, detail=None):
    return SudiApiError(status.HTTP_409_CONFLICT, code, message, resync=resync, detail=detail)


def _jsonable_errors(error):
    """Pydantic's errors, stripped of anything JSON cannot carry.

    A ``ValueError`` raised inside a validator arrives with the exception
    *object* in ``ctx``, and ``JSONResponse`` then fails to serialise it -- so
    the client got a bare 500 for what is simply a bad request. Every custom
    validator hits this; the plain field constraints do not, which is why it
    stayed hidden until the first ``model_validator`` was written.
    """
    safe = []
    for item in error.errors():
        entry = {
            key: value for key, value in item.items()
            if key not in ("ctx", "loc", "url")
        }
        entry["loc"] = [str(part) for part in item.get("loc", ())]
        if item.get("ctx"):
            entry["ctx"] = {
                key: str(value) for key, value in item["ctx"].items()
            }
        safe.append(entry)
    return safe


class SudiErrorMiddleware(BaseHTTPMiddleware):
    """Renders every failure as the envelope above."""

    async def dispatch(self, request, call_next):
        try:
            return await call_next(request)
        except SudiApiError as error:
            return JSONResponse(
                status_code=error.status_code,
                content=error.envelope,
                headers=error.headers or None,
            )
        except RequestValidationError as error:
            # A malformed body is the client's bug, so it must never be retried
            # blindly; the errors are passed through for the developer.
            return JSONResponse(
                status_code=HTTP_422,
                content=SudiApiError(
                    HTTP_422,
                    CODE_VALIDATION,
                    "The request body is not valid.",
                    detail={"errors": _jsonable_errors(error)},
                ).envelope,
            )
        except (UserError, ValidationError) as error:
            # An ORM refusal the route did not anticipate. Never retryable:
            # the same request would be refused again for the same reason.
            return JSONResponse(
                status_code=HTTP_422,
                content=SudiApiError(
                    HTTP_422, CODE_VALIDATION, str(error),
                    detail={"source": "orm"},
                ).envelope,
            )
        except AccessError as error:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content=SudiApiError(
                    status.HTTP_403_FORBIDDEN, CODE_AUTH, str(error),
                    detail={"source": "orm"},
                ).envelope,
            )
        except StarletteHTTPException as error:
            # Anything raised by FastAPI itself, e.g. an unknown route.
            code = CODE_AUTH if error.status_code in (401, 403) else CODE_VALIDATION
            if error.status_code == status.HTTP_404_NOT_FOUND:
                code = CODE_NOT_IN_SCOPE
            return JSONResponse(
                status_code=error.status_code,
                content=SudiApiError(
                    error.status_code, code, str(error.detail)
                ).envelope,
                headers=getattr(error, "headers", None),
            )
