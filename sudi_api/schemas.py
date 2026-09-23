"""Pydantic v2 request and response models for the Sudi mobile API."""

from pydantic import BaseModel, Field, model_validator


def _opt(value):
    """Odoo returns ``False`` for an unset Char; JSON should carry ``null``."""
    return value or None


class HealthInfo(BaseModel):
    status: str
    api: str
    odoo_version: str


class DeviceInput(BaseModel):
    """The handset behind a login. Sent on every authentication."""

    device_uid: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Generated once by the client on first launch and never "
                    "again. Not a vendor id: those change on reinstall.",
    )
    platform: str | None = Field(default=None, pattern="^(android|ios|web)$")
    app_version: str | None = None
    push_token: str | None = None


class LoginInput(BaseModel):
    login: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    device: DeviceInput


class RefreshInput(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access token lifetime, in seconds")
    device_uid: str
    roles: list[str] = Field(
        default_factory=list,
        description="A hint so the client can draw its navigation before the "
                    "first call. Never a gate: every route checks the group "
                    "server-side.",
    )


class MeInfo(BaseModel):
    id: int
    name: str
    login: str
    email: str | None = None
    phone: str | None = None
    roles: list[str]
    device_uid: str
    company: str | None = None
    is_customer: bool
    gst_state: str | None = Field(
        default=None,
        description="Customers only: `present`, `skipped` or `missing`. A "
                    "skipped customer may be asked again on a later launch, so "
                    "do not treat a missing GSTIN as a blocker (decision D3).",
    )

    @classmethod
    def from_caller(cls, caller) -> "MeInfo":
        user = caller.user
        return cls(
            id=user.id,
            name=user.name or "",
            login=user.login or "",
            email=_opt(user.email),
            phone=_opt(user.partner_id.phone),
            roles=caller.roles,
            device_uid=caller.device.device_uid,
            company=_opt(user.company_id.name),
            is_customer=user.share,
            gst_state=(
                user.partner_id.sudo()._sudi_gst_state() if user.share else None
            ),
        )


class DeviceInfo(BaseModel):
    device_uid: str
    platform: str | None = None
    app_version: str | None = None
    last_seen_at: str | None = None
    sync_cursor: int
    revoked: bool
    is_current: bool

    @classmethod
    def from_device(cls, device, current_uid) -> "DeviceInfo":
        return cls(
            device_uid=device.device_uid,
            platform=_opt(device.platform),
            app_version=_opt(device.app_version),
            last_seen_at=device.last_seen_at.isoformat() if device.last_seen_at else None,
            sync_cursor=device.sync_cursor,
            revoked=device.revoked,
            is_current=device.device_uid == current_uid,
        )


class DeviceUpdateInput(BaseModel):
    """What a client may change about itself after login."""

    app_version: str | None = None
    push_token: str | None = None


# ---------------------------------------------------------------------------
# Sync — the read side. These models are the client's local schema, so they are
# spelled out rather than left as free-form dicts: the OpenAPI document is what
# the Dart models are generated from.
# ---------------------------------------------------------------------------

class Ref(BaseModel):
    """A record reference, flattened for a client that holds no ORM."""

    id: int
    name: str | None = None


class ItemLine(BaseModel):
    sr: int
    product: str | None = None
    size: str | None = None
    pcs: float
    carats: float
    job_type: Ref | None = None
    remarks: str | None = None


class TimerInfo(BaseModel):
    running: bool
    started_at: str | None = None


class PickupDoc(BaseModel):
    id: int
    rev: int = Field(
        ...,
        description="Rises monotonically per record. For debugging, and for "
                    "dropping a write that arrives out of order.",
    )
    name: str
    stage: str = Field(
        ...,
        description="`awaiting` for a parcel still to collect, `collected` for "
                    "one this operator picked up today. Split the list on it.",
        examples=["awaiting", "collected"],
    )
    customer: Ref | None = Field(
        default=None,
        description="Null when a jangad was uploaded from a phone number no "
                    "customer record matches yet.",
    )
    contact_phone: str | None = None
    pickup_address: str | None = None
    scheduled_date: str | None = None
    created_at: str | None = None
    collected_at: str | None = None
    collected_by: Ref | None = None
    jangad_pages: int


class DeliveryDoc(BaseModel):
    id: int
    rev: int
    name: str
    customer: Ref | None = None
    contact_phone: str | None = None
    address: str | None = None
    stage: str | None = None
    taken_by: Ref | None = None
    out_since: str | None = None
    origin_receipt: Ref | None = None
    delivered_at: str | None = None
    received_by: str | None = None
    items: list[ItemLine] = Field(default_factory=list)


class JobWorkDoc(BaseModel):
    id: int
    rev: int
    name: str
    customer: Ref | None = None
    scheduled_date: str | None = None
    state: str
    current_department: Ref | None = None
    involved_departments: list[Ref] = Field(default_factory=list)
    total_hours: float
    timer: TimerInfo
    jangad_pages: int
    items: list[ItemLine] = Field(default_factory=list)


class PickupDelta(BaseModel):
    upserts: list[PickupDoc] = Field(default_factory=list)
    gone: list[int] = Field(
        default_factory=list,
        description="Ids to delete locally. Over-reports on purpose: deleting "
                    "a row you do not hold is a no-op, while missing a real "
                    "scope exit strands work on the phone forever.",
    )


class DeliveryDelta(BaseModel):
    upserts: list[DeliveryDoc] = Field(default_factory=list)
    gone: list[int] = Field(default_factory=list)


class JobWorkDelta(BaseModel):
    upserts: list[JobWorkDoc] = Field(default_factory=list)
    gone: list[int] = Field(default_factory=list)


class SyncScopes(BaseModel):
    """Only the scopes the caller's roles entitle them to appear."""

    pickup: PickupDelta | None = None
    delivery: DeliveryDelta | None = None
    jobwork: JobWorkDelta | None = None


class SyncPullResult(BaseModel):
    cursor: int = Field(
        ...,
        description="Store verbatim and send back next time. On a full resync "
                    "keep the value from the first page until has_more is false.",
    )
    full_resync: bool = Field(
        ...,
        description="Wipe the local tables and take what follows as the whole "
                    "world. Answered when the cursor is absent, or older than "
                    "the server's change-log retention.",
    )
    has_more: bool
    next_after_scope: str | None = None
    next_after_id: int | None = None
    server_time: str = Field(
        ...,
        description="Naive UTC. Use it to keep a clock offset and stamp "
                    "occurred_at from the corrected clock.",
    )
    scopes: SyncScopes


# ---------------------------------------------------------------------------
# Intents — the write side. Every one carries the same envelope, because every
# one may have been captured while the phone was offline.
# ---------------------------------------------------------------------------

class IntentEnvelope(BaseModel):
    occurred_at: str | None = Field(
        default=None,
        description="When the tap happened, ISO-8601 **with** an explicit "
                    "offset or Z, stamped from the server-corrected clock. "
                    "Omit it and the server uses its own time. More than 60s "
                    "ahead is CLOCK_SKEW; more than 72h old is STALE_INTENT.",
        examples=["2026-09-23T09:12:04+05:30"],
    )
    clock_offset_ms: int | None = Field(
        default=None,
        description="The correction the client applied, recorded for support.",
    )


class ConfirmPickupInput(IntentEnvelope):
    upload_ids: list[str] = Field(
        default_factory=list,
        description="Jangad pages staged with POST /uploads, in page order.",
    )
    note: str | None = None


class CancelPickupInput(IntentEnvelope):
    reason: str = Field(..., min_length=1, description="Shown to the office.")


class TakeDeliveriesInput(IntentEnvelope):
    ids: list[int] = Field(
        ..., min_length=1,
        description="Operators pick up a handful of parcels at once, so this "
                    "is the one multi-record intent. It answers per id.",
    )


class ReleaseDeliveryInput(IntentEnvelope):
    pass


class DeliverInput(IntentEnvelope):
    receiver_name: str | None = Field(
        default=None, description="Who took the parcel."
    )
    signature_upload_id: str | None = Field(
        default=None, description="A drawn signature, staged as an upload."
    )
    upload_ids: list[str] = Field(
        default_factory=list, description="Delivery photos, staged as uploads."
    )


class TimerInput(IntentEnvelope):
    action: str = Field(..., pattern="^(start|stop)$")
    job_type_id: int | None = Field(
        default=None,
        description="Required the first time a receipt's timer is started if "
                    "its lines do not agree on one job type.",
    )


class DepartmentInput(IntentEnvelope):
    department_id: int


class FinishJobWorkInput(IntentEnvelope):
    pass


class PickupIntentResult(BaseModel):
    code: str = Field(
        ...,
        description="`OK`, or `ALREADY_DONE` when the intent found its own "
                    "earlier effect. Both are successes.",
        examples=["OK", "ALREADY_DONE"],
    )
    record: PickupDoc


class DeliveryIntentResult(BaseModel):
    code: str
    record: DeliveryDoc


class JobWorkIntentResult(BaseModel):
    code: str
    record: JobWorkDoc


class IntentFailure(BaseModel):
    id: int
    code: str
    message: str
    detail: dict = Field(default_factory=dict)


class TakeDeliveriesResult(BaseModel):
    taken: list[DeliveryDoc] = Field(default_factory=list)
    failed: list[IntentFailure] = Field(
        default_factory=list,
        description="A partial result is normal: somebody else may have taken "
                    "one of the parcels while this phone was offline.",
    )


class UploadResult(BaseModel):
    reference: str = Field(..., description="Quote this in the intent's upload_ids.")
    sha256: str = Field(
        ...,
        description="Of the bytes as sent. Verify it, then delete the local "
                    "copy only once the *intent* is acknowledged.",
    )
    bytes: int
    mimetype: str
    expires_at: str = Field(
        ..., description="An upload nothing claims is collected after this."
    )


# ---------------------------------------------------------------------------
# Customer authentication and onboarding
# ---------------------------------------------------------------------------

class OtpRequestInput(BaseModel):
    phone: str = Field(..., min_length=6, max_length=20)


class OtpRequestResult(BaseModel):
    sent: bool = Field(
        ...,
        description="Always true on a 200. The answer is identical for a known "
                    "and an unknown number: which numbers have accounts is not "
                    "something this endpoint will tell you.",
    )
    expires_in: int = Field(..., description="Seconds the code stays valid.")
    resend_after: int = Field(
        ..., description="Seconds before another code may be requested. Drive "
                         "the resend countdown from this."
    )
    channel: str = Field(
        ...,
        description="Always `whatsapp`. Say so in the UI, or every support "
                    "call will be about a missing SMS.",
        examples=["whatsapp"],
    )


class OtpVerifyInput(BaseModel):
    phone: str = Field(..., min_length=6, max_length=20)
    code: str = Field(..., min_length=4, max_length=10)
    device: DeviceInput


class OtpVerifyResult(BaseModel):
    registration_required: bool = Field(
        ...,
        description="True when the number has no account yet. Tokens are then "
                    "null and `registration_token` must be sent to /auth/register.",
    )
    registration_token: str | None = None
    tokens: TokenPair | None = None


class RegisterInput(BaseModel):
    registration_token: str = Field(
        ..., description="From /auth/otp/verify. Proof the number answered a code."
    )
    name: str = Field(..., min_length=1, max_length=120)
    vat: str | None = Field(
        default=None,
        description="A GSTIN. Optional: a customer may complete registration "
                    "without one and be asked again later.",
    )
    device: DeviceInput


class GstInput(BaseModel):
    vat: str = Field(..., min_length=15, max_length=20)


class GstResult(BaseModel):
    gst_state: str = Field(
        ...,
        description="`present`, `skipped` or `missing`. Three states and not a "
                    "boolean, because a skip is not final.",
        examples=["present", "skipped", "missing"],
    )
    vat: str | None = None
    company: str | None = Field(
        default=None, description="The company the GSTIN resolved to."
    )


# ---------------------------------------------------------------------------
# Jangad submission — the customer's one write
# ---------------------------------------------------------------------------

class AddressSuggestion(BaseModel):
    id: int
    name: str
    address: str
    is_default: bool


class JangadSubmitInput(IntentEnvelope):
    upload_ids: list[str] = Field(
        ..., min_length=1, max_length=20,
        description="The jangad's pages, staged with POST /uploads, in page order.",
    )
    pickup_address_id: int | None = Field(
        default=None, description="One of the ids from GET /addresses."
    )
    manual_pickup_address: str | None = Field(
        default=None,
        description="A new address, typed. Give this or pickup_address_id, "
                    "not both and not neither.",
    )

    @model_validator(mode="after")
    def _exactly_one_address(self):
        typed = bool((self.manual_pickup_address or "").strip())
        if bool(self.pickup_address_id) == typed:
            raise ValueError(
                "Give either pickup_address_id or manual_pickup_address."
            )
        return self


class JangadSubmitResult(BaseModel):
    reference: str = Field(..., description="The receipt number the office sees.")
    pages: int
    pickup_address: str | None = None
    submitted_at: str = Field(
        ...,
        description="When the customer captured it, not when it reached the "
                    "server — the two differ whenever it was submitted offline.",
    )
