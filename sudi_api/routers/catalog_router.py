"""The services the workshop offers, which are also its departments.

One list serves the staff app twice -- the services an operator records when
posting a jangad for a customer, and the departments a job worker transfers a
parcel to -- so it is fetched once, cached, and revalidated with an ETag.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status

from ..dependencies import Caller, field_caller
from ..etags import conditional
from ..schemas import JobTypeInfo

router = APIRouter(prefix="/catalog", tags=["catalog"])


def active_job_types(env):
    """The active services of the caller's company, in catalogue order."""
    # sudo keeps env's context, so the names come back in the caller's
    # language (Accept-Language, or the account's own).
    return env["sudi.diamond.job.type"].sudo().search(
        [("active", "=", True), ("company_id", "in", [False, env.company.id])],
        order="sequence, name, id",
    )


@router.get(
    "/job-types",
    response_model=list[JobTypeInfo],
    summary="Active services and departments",
    responses={status.HTTP_304_NOT_MODIFIED: {"description": "Nothing has changed"}},
)
def job_types(
    caller_obj: Annotated[Caller, Depends(field_caller)],
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
):
    payload = [
        {
            "id": job_type.id,
            "name": job_type.name,
            "unit": job_type._sudi_unit(),
            "sequence": job_type.sequence,
        }
        for job_type in active_job_types(caller_obj.env)
    ]
    return conditional(response, payload, if_none_match)
