"""The caller's own handsets.

Scoped to the authenticated user in code, not by a record rule: ``sudi.api.device``
is readable only by administrators, so these routes read it with ``sudo()`` and
filter by ``user_id``. Any new route touching the model must do the same or that
scoping is lost.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from ..dependencies import Caller, caller
from ..errors import not_in_scope
from ..schemas import DeviceInfo, DeviceUpdateInput

router = APIRouter(prefix="/devices", tags=["devices"])


def _own_devices(caller_obj: Caller):
    return (
        caller_obj.env["sudi.api.device"]
        .sudo()
        .search([("user_id", "=", caller_obj.user.id)])
    )


@router.get(
    "",
    response_model=list[DeviceInfo],
    summary="Every handset registered to the caller",
)
def list_devices(caller_obj: Annotated[Caller, Depends(caller)]) -> list[DeviceInfo]:
    return [
        DeviceInfo.from_device(device, caller_obj.device.device_uid)
        for device in _own_devices(caller_obj)
    ]


@router.patch(
    "/me",
    response_model=DeviceInfo,
    summary="Update this handset's push token or app version",
)
def update_current_device(
    data: DeviceUpdateInput, caller_obj: Annotated[Caller, Depends(caller)]
) -> DeviceInfo:
    vals = {}
    if data.app_version is not None:
        vals["app_version"] = data.app_version
    if data.push_token is not None:
        # An empty string clears it, which is what an app does when the user
        # turns notifications off.
        vals["push_token"] = data.push_token or False
    if vals:
        caller_obj.device.sudo().write(vals)
    caller_obj.device._sudi_touch()
    return DeviceInfo.from_device(caller_obj.device, caller_obj.device.device_uid)


@router.post(
    "/{device_uid}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign one of the caller's own handsets out",
)
def revoke_device(
    device_uid: str, caller_obj: Annotated[Caller, Depends(caller)]
) -> None:
    device = _own_devices(caller_obj).filtered(
        lambda record: record.device_uid == device_uid
    )
    if not device:
        # Deliberately the same answer as a device belonging to someone else:
        # this must not become a way to enumerate other people's handsets.
        raise not_in_scope("No such device is registered to you.")
    device.action_revoke()
