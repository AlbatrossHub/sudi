"""Unauthenticated endpoints, for load balancers, client smoke tests and the clock."""

from datetime import datetime, timezone
from typing import Annotated

from odoo import release

from fastapi import APIRouter, Depends

from ..dependencies import audience
from ..schemas import HealthInfo, ServerTime

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthInfo, summary="Liveness probe")
def health(api: Annotated[str, Depends(audience)]) -> HealthInfo:
    return HealthInfo(status="ok", api=f"sudi-{api}", odoo_version=release.version)


@router.get(
    "/time",
    response_model=ServerTime,
    summary="The server's clock, which the app pins its own to",
)
def server_time() -> ServerTime:
    """No token, on purpose: the clock is needed before the first sign-in.

    The app counts this forward by the phone's uptime and stamps every offline
    event from that, so a phone whose date is wrong -- common in the field --
    cannot misdate a pickup, and the server's 72-hour window cannot turn a
    correct capture into a STALE_INTENT.
    """
    return ServerTime(server_time=datetime.now(timezone.utc))
