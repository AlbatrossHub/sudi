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

**A refusal rolls back.** Turning an exception into a response means no
exception reaches the HTTP layer, and the HTTP layer commits whatever has no
exception attached. So the middleware rolls the transaction back itself before
it answers; without that, an intent refused halfway committed its first half --
an idempotency key reserved with no answer behind it (every retry then saw
``LOCKED`` for ever), uploads marked used by an intent that never happened, a
customer renamed by a registration that was refused. The one exception is
opted into per error (``rollback=False``): the OTP refusals, whose bookkeeping
-- the attempt counter, the failed-send record -- *is* the refusal.
"""

import logging

from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.fastapi.context import odoo_env_ctx

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
                 headers=None, retryable=None, rollback=True):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.resync = list(resync or [])
        self.detail = detail or {}
        self.headers = headers or {}
        self.retryable = (code in RETRYABLE) if retryable is None else retryable
        # Whether the work done before the refusal is undone. Almost always:
        # see the module docstring for the one reason not to.
        self.rollback = rollback
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


def _rollback(error):
    """Undo the request's work before a refusal is answered.

    The env is the request's own, from the context variable the OCA dispatcher
    sets around the app. Rolling back here is safe because the route has
    finished: the exception is on its way out. In a test the cursor is a
    ``TestCursor``, whose rollback returns to the savepoint it took at the
    start of the request, so a test's own fixtures survive.
    """
    if not getattr(error, "rollback", True):
        return
    env = odoo_env_ctx.get(None)
    if env is None:
        return
    try:
        env.cr.rollback()
    except Exception:
        # Answering the client matters more than this; the HTTP layer rolls
        # back anyway if the transaction is broken.
        _logger.exception("Could not roll back a refused request")


class SudiErrorMiddleware(BaseHTTPMiddleware):
    """Renders every failure as the envelope above, after rolling it back."""

    async def dispatch(self, request, call_next):
        # Every branch rolls back first. An unexpected exception is not caught
        # at all: it reaches the HTTP layer, which rolls back and answers 500.
        try:
            return await call_next(request)
        except SudiApiError as error:
            _rollback(error)
            return JSONResponse(
                status_code=error.status_code,
                content=error.envelope,
                headers=error.headers or None,
            )
        except RequestValidationError as error:
            _rollback(error)
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
            _rollback(error)
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
            _rollback(error)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content=SudiApiError(
                    status.HTTP_403_FORBIDDEN, CODE_AUTH, str(error),
                    detail={"source": "orm"},
                ).envelope,
            )
        except StarletteHTTPException as error:
            _rollback(error)
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
