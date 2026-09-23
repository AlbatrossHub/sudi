from odoo import fields, models

from starlette.middleware import Middleware

from fastapi import APIRouter

from ..errors import SudiErrorMiddleware
from ..routers import (
    auth_router,
    customer_auth_router,
    customer_jangad_router,
    customer_router,
    delivery_router,
    device_router,
    jobwork_router,
    pickup_router,
    session_router,
    sync_router,
    system_router,
    upload_router,
)


class FastapiEndpoint(models.Model):
    _inherit = "fastapi.endpoint"

    app: str = fields.Selection(
        selection_add=[
            ("sudi_field", "Sudi Field Ops API"),
            ("sudi_customer", "Sudi Customer API"),
        ],
        ondelete={"sudi_field": "cascade", "sudi_customer": "cascade"},
    )

    def _get_fastapi_routers(self) -> list[APIRouter]:
        if self.app == "sudi_field":
            return [
                system_router,
                auth_router,
                session_router,
                device_router,
                sync_router,
                pickup_router,
                delivery_router,
                jobwork_router,
                upload_router,
            ]
        if self.app == "sudi_customer":
            return [
                system_router,
                customer_auth_router,
                session_router,
                device_router,
                upload_router,
                customer_router,
                customer_jangad_router,
            ]
        return super()._get_fastapi_routers()

    def _get_fastapi_app_middlewares(self) -> list[Middleware]:
        middlewares = super()._get_fastapi_app_middlewares()
        if self.app in ("sudi_field", "sudi_customer"):
            # A middleware and not an exception handler: the addon clears every
            # handler on the mounted tree (_clear_fastapi_exception_handlers),
            # so a handler would be thrown away. See errors.py.
            middlewares = middlewares + [Middleware(SudiErrorMiddleware)]
        return middlewares
