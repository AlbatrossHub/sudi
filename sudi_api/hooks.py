import logging
import secrets

from .jwt_tokens import PARAM_SECRET

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    params = env["ir.config_parameter"].sudo()
    if not params.get_param(PARAM_SECRET):
        # Per-database secret. Rotating it invalidates every live token, on
        # every device, at once.
        params.set_param(PARAM_SECRET, secrets.token_urlsafe(64))
        _logger.info("Generated a new JWT signing secret (%s)", PARAM_SECRET)

    for xmlid in ("fastapi_endpoint_sudi_field", "fastapi_endpoint_sudi_customer"):
        endpoint = env.ref(f"sudi_api.{xmlid}", raise_if_not_found=False)
        if endpoint:
            # Routes are only mounted once the record is synced to the endpoint
            # registry; create() alone does not trigger it.
            endpoint.action_sync_registry()
