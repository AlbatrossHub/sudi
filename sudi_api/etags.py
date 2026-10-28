"""Weak validators for the cached reads.

The catalogue and the customer directory change rarely and are fetched on
every launch, so an unchanged one should cost a 304 and no body. The validator
is a digest of the body itself: no change log to consult, and nothing to get
out of step with what was actually sent.
"""

import hashlib
import json

from fastapi import Response, status


def weak_etag(payload) -> str:
    material = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return 'W/"%s"' % hashlib.sha256(material.encode()).hexdigest()[:32]


def conditional(response: Response, payload, if_none_match: str | None):
    """``payload`` with its ETag, or a bodiless 304 when the client has it."""
    etag = weak_etag(payload)
    if if_none_match and etag in [value.strip() for value in if_none_match.split(",")]:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return payload
