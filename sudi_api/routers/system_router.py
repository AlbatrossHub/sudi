"""Unauthenticated endpoints, for load balancers and client smoke tests."""

from typing import Annotated

from odoo import release

from fastapi import APIRouter, Depends

from ..dependencies import audience
from ..schemas import HealthInfo

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthInfo, summary="Liveness probe")
def health(api: Annotated[str, Depends(audience)]) -> HealthInfo:
    return HealthInfo(status="ok", api=f"sudi-{api}", odoo_version=release.version)
