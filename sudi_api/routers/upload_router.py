"""Staging a binary before the intent that references it.

Two phases, because that is what makes a photograph survive a bad connection:
the intent is a few hundred bytes and lands on the worst signal, while the
image retries on its own.
"""

import base64
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from ..dependencies import Caller, caller
from ..errors import validation
from ..schemas import UploadResult

router = APIRouter(prefix="/uploads", tags=["uploads"])

# Generous enough for a 2000px jangad page at quality 80, mean enough to stop
# an un-resized 12MP original.
MAX_REQUEST_BYTES = 8 * 1024 * 1024


@router.post(
    "",
    response_model=UploadResult,
    summary="Stage one image, to be quoted by an intent",
)
async def stage_upload(
    caller_obj: Annotated[Caller, Depends(caller)],
    file: Annotated[UploadFile, File(description="A single JPEG, PNG or WebP.")],
) -> UploadResult:
    raw = await file.read()
    if not raw:
        raise validation("The uploaded file is empty.")
    if len(raw) > MAX_REQUEST_BYTES:
        raise validation(
            f"The file is {len(raw)} bytes; the limit is {MAX_REQUEST_BYTES}. "
            "Compress before uploading.",
            detail={"bytes": len(raw), "limit": MAX_REQUEST_BYTES},
        )

    from odoo.exceptions import UserError

    try:
        upload = caller_obj.env["sudi.upload"]._sudi_stage(
            caller_obj.user,
            base64.b64encode(raw),
            filename=file.filename,
            mimetype=file.content_type,
        )
    except UserError as error:
        raise validation(str(error)) from error

    return UploadResult(
        reference=upload.reference,
        sha256=upload.sha256,
        bytes=upload.file_size,
        mimetype=upload.mimetype,
        expires_at=upload.expires_at.isoformat(),
    )
