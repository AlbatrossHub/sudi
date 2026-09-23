"""Customer onboarding beyond the login itself.

GST is auto-accepted from the GSTIN and the customer may skip it (decision
D3), so these two routes are the whole of it: submit a number, or say not now.
"""

from typing import Annotated

from odoo.exceptions import ValidationError

from fastapi import APIRouter, Depends

from ..dependencies import Caller, customer_caller
from ..errors import validation
from ..schemas import GstInput, GstResult

router = APIRouter(tags=["customer"])


def _gst_result(partner) -> GstResult:
    commercial = partner.commercial_partner_id
    return GstResult(
        gst_state=partner._sudi_gst_state(),
        vat=partner.vat or commercial.vat or None,
        company=commercial.name if commercial != partner else None,
    )


@router.post("/gst", response_model=GstResult, summary="Submit a GST number")
def submit_gst(
    data: GstInput, caller_obj: Annotated[Caller, Depends(customer_caller)]
) -> GstResult:
    partner = caller_obj.user.partner_id.sudo()
    try:
        partner._sudi_apply_gstin(data.vat)
    except ValidationError as error:
        raise validation(error.args[0]) from error
    return _gst_result(partner)


@router.post(
    "/gst/skip",
    response_model=GstResult,
    summary="Carry on without a GST number for now",
)
def skip_gst(
    caller_obj: Annotated[Caller, Depends(customer_caller)]
) -> GstResult:
    """Not final: a skipped customer may be asked again on a later launch."""
    partner = caller_obj.user.partner_id.sudo()
    partner._sudi_skip_gst()
    return _gst_result(partner)
