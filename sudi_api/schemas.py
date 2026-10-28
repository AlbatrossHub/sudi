"""Pydantic v2 request and response models for the Sudi mobile API."""

from datetime import date as Date, datetime

from pydantic import BaseModel, Field, model_validator


def _opt(value):
    """Odoo returns ``False`` for an unset Char; JSON should carry ``null``."""
    return value or None


class HealthInfo(BaseModel):
    status: str
    api: str
    odoo_version: str


class ServerTime(BaseModel):
    server_time: datetime = Field(
        ...,
        description="The server's clock, aware UTC. Pin the app's trusted "
                    "clock to it (counted forward by the phone's uptime) and "
                    "stamp every occurred_at from that, so a phone with a "
                    "wrong date cannot misdate an offline event.",
    )


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
    move_id: int | None = Field(
        default=None,
        description="The line's own id. Key an editable table on it, and send "
                    "it back in PUT /receipts/{id}/lines to keep the line.",
    )
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


class ServiceLine(BaseModel):
    """A service the customer asked for when the jangad was posted.

    A request, not an item: nothing has been counted yet. The office enters
    item lines from these, after which ``move_id`` names the line.
    """

    id: int
    job_type: Ref | None = None
    quantity: float
    unit: str = Field(
        ...,
        description="`pcs`, `ct`, or empty for a service billed by hand.",
        examples=["pcs", "ct", ""],
    )
    remarks: str | None = None
    move_id: int | None = Field(
        default=None,
        description="The item line entered from this request; null while it "
                    "is still to enter.",
    )


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
    services: list[ServiceLine] = Field(
        default_factory=list,
        description="What the customer asked for. Empty for a jangad posted "
                    "without choosing services.",
    )


class OfficeDoc(PickupDoc):
    """A collected jangad awaiting validation: scope `office`.

    The pickup document plus the item table being built and where the jangad
    came from. Leaves the scope once it is marked READY.
    """

    items: list[ItemLine] = Field(default_factory=list)
    jangad_source: str | None = Field(
        default=None,
        description="`customer_app`, `customer_web`, `on_behalf` or `office`; "
                    "null for a receipt from before sources were recorded.",
    )
    posted_by: Ref | None = Field(
        default=None,
        description="The member of staff who posted it for the customer.",
    )


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
    services: list[ServiceLine] = Field(default_factory=list)
    my_hours: float = Field(
        default=0.0,
        description="Hours the caller has booked on this receipt; total_hours "
                    "is everybody's. Excludes a timer still running.",
    )


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


class OfficeDelta(BaseModel):
    upserts: list[OfficeDoc] = Field(default_factory=list)
    gone: list[int] = Field(default_factory=list)


class SyncScopes(BaseModel):
    """Only the scopes the caller's roles entitle them to appear."""

    pickup: PickupDelta | None = None
    delivery: DeliveryDelta | None = None
    jobwork: JobWorkDelta | None = None
    office: OfficeDelta | None = None


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


class FieldEventEnvelope(IntentEnvelope):
    """An intent that also records **where** it happened.

    Only on the two customer-facing field events. Deliberately not on the
    envelope every intent shares: accepting a coordinate on a route that
    ignores it is worse than not offering the field, and a customer's location
    is a separate consent question nobody has asked for.
    """

    latitude: float | None = Field(
        default=None, ge=-90, le=90,
        description="Optional, and never required: this app is used in "
                    "basements and back rooms where there is no fix, so a "
                    "missing one must never block the event.",
    )
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy_m: float | None = Field(
        default=None, ge=0, description="Whatever the device reported."
    )

    @model_validator(mode="after")
    def _location_is_a_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Give both latitude and longitude, or neither.")
        return self

    def location(self) -> dict | None:
        if self.latitude is None:
            return None
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "accuracy_m": self.accuracy_m,
        }


class ConfirmPickupInput(FieldEventEnvelope):
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


class DeliverInput(FieldEventEnvelope):
    receiver_name: str | None = Field(
        default=None,
        description="Who took the parcel. **Required** by default: the server "
                    "refuses the delivery without it (422 VALIDATION).",
    )
    signature_upload_id: str | None = Field(
        default=None,
        description="A drawn signature, staged as an upload. **Required** by "
                    "default. Offline this means the signature image has to "
                    "flush before the intent can.",
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


class ServiceInfo(BaseModel):
    """One service on the customer's dashboard."""

    id: int
    name: str
    unit: str = Field(..., description="`pcs`, `ct`, or empty.", examples=["pcs"])
    description: str | None = None


class ServiceRequestInput(BaseModel):
    """One service picked on the dashboard, with how much and a remark."""

    job_type_id: int = Field(..., gt=0, description="From GET /services.")
    quantity: float = Field(
        default=0.0, ge=0,
        description="In the service's unit. Zero is allowed: the customer may "
                    "not know the count, and the office counts anyway.",
    )
    remarks: str | None = Field(default=None, max_length=256)


def _check_one_address(model):
    typed = bool((model.manual_pickup_address or "").strip())
    if bool(model.pickup_address_id) == typed:
        raise ValueError(
            "Give either pickup_address_id or manual_pickup_address."
        )
    return model


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
    services: list[ServiceRequestInput] = Field(
        default_factory=list, max_length=50,
        description="The services picked on the dashboard, in the order "
                    "picked. Optional: a jangad alone is still accepted.",
    )

    @model_validator(mode="after")
    def _exactly_one_address(self):
        return _check_one_address(self)


class JangadSubmitResult(BaseModel):
    reference: str = Field(..., description="The receipt number the office sees.")
    pages: int
    pickup_address: str | None = None
    submitted_at: str = Field(
        ...,
        description="When the customer captured it, not when it reached the "
                    "server — the two differ whenever it was submitted offline.",
    )


# ---------------------------------------------------------------------------
# Staff app, v2: catalogue, posting on a customer's behalf, manual time, the
# office, and the bell. See docs/MOBILE_V2_PERSONAS_PLAN.md section 5.
# ---------------------------------------------------------------------------

class JobTypeInfo(BaseModel):
    """A service, which is also a department of the workshop."""

    id: int
    name: str
    unit: str = Field(..., description="`pcs`, `ct`, or empty.", examples=["pcs"])
    sequence: int


class CustomerAddress(BaseModel):
    id: int = Field(..., description="Send as pickup_address_id.")
    label: str
    address: str
    is_default: bool


class CustomerEntry(BaseModel):
    """One customer in the posting-on-behalf directory."""

    id: int = Field(..., description="Send as customer.partner_id.")
    name: str
    phone: str = Field(..., description="Ten digits, as the pickup flow uses it.")
    gst_state: str = Field(..., examples=["present", "skipped", "missing"])
    addresses: list[CustomerAddress] = Field(default_factory=list)


class OnBehalfCustomer(BaseModel):
    """Who the jangad is for: a directory entry, or a number typed in."""

    partner_id: int | None = Field(default=None, description="From GET /customers.")
    phone: str | None = Field(
        default=None, min_length=6, max_length=20,
        description="A customer not in the directory yet. Ten digits once "
                    "spaces and the country code are dropped.",
    )
    name: str | None = Field(
        default=None, max_length=120,
        description="With a phone the server does not know: the customer "
                    "record is created under this name.",
    )

    @model_validator(mode="after")
    def _exactly_one_customer(self):
        if bool(self.partner_id) == bool((self.phone or "").strip()):
            raise ValueError("Give either partner_id or phone.")
        return self


class OnBehalfJangadInput(IntentEnvelope):
    customer: OnBehalfCustomer
    pickup_address_id: int | None = Field(
        default=None, description="One of the customer's addresses from GET /customers."
    )
    manual_pickup_address: str | None = Field(
        default=None,
        description="A new address, typed. Give this or pickup_address_id, "
                    "not both and not neither.",
    )
    services: list[ServiceRequestInput] = Field(default_factory=list, max_length=50)
    upload_ids: list[str] = Field(
        ..., min_length=1, max_length=20,
        description="The jangad's pages, staged with POST /uploads, in page order.",
    )
    note: str | None = Field(
        default=None, max_length=1000, description="Shown to the office."
    )

    @model_validator(mode="after")
    def _exactly_one_address(self):
        return _check_one_address(self)


class OnBehalfJangadResult(BaseModel):
    picking_id: int = Field(
        ...,
        description="Use it straight away for the pickup: POST "
                    "/pickups/{picking_id}/confirm, in the same outbox chain.",
    )
    reference: str
    pickup: PickupDoc | None = Field(
        default=None,
        description="The receipt as the pickup list shows it. Null only for "
                    "an account without the pickup role.",
    )


class TimeLogInput(IntentEnvelope):
    job_type_id: int = Field(..., gt=0, description="What the time was spent on.")
    hours: float = Field(..., gt=0, le=24, examples=[1.5])
    description: str | None = Field(default=None, max_length=500)
    date: Date | None = Field(
        default=None,
        description="The day worked. Defaults to the day of occurred_at; may "
                    "not be later than it, nor more than 31 days before it.",
    )


class TimeEntry(BaseModel):
    id: int
    date: str
    picking: Ref
    job_type: Ref | None = None
    hours: float
    description: str | None = None
    running: bool = Field(
        ..., description="A timer is running on it; hours exclude the running part."
    )


class TimeLogResult(BaseModel):
    code: str = Field(..., examples=["OK"])
    entry: TimeEntry
    jobwork: JobWorkDoc


class DayHours(BaseModel):
    date: str
    hours: float


class JobTypeHours(BaseModel):
    job_type: Ref | None = None
    hours: float


class ProductivityResult(BaseModel):
    date_from: str
    date_to: str
    total_hours: float
    by_day: list[DayHours] = Field(
        default_factory=list, description="Every day of the range, zeros included."
    )
    by_job_type: list[JobTypeHours] = Field(
        default_factory=list, description="Most hours first."
    )
    entries: list[TimeEntry] = Field(default_factory=list, description="Newest first.")


class NoticeInfo(BaseModel):
    id: int
    kind: str = Field(
        ...,
        examples=["pickup_new", "office_validate", "jobwork_ready",
                  "department_transfer", "delivery_ready"],
    )
    title: str
    body: str | None = None
    picking_id: int | None = None
    scope: str | None = Field(
        default=None, description="The worklist to open when it is tapped."
    )
    created_at: str
    read: bool


class NoticeList(BaseModel):
    notices: list[NoticeInfo] = Field(default_factory=list, description="Newest first.")
    last_id: int = Field(..., description="Send back as `after` on the next poll.")
    unread: int = Field(..., description="All of the caller's unread notices.")
    has_more: bool = Field(
        default=False,
        description="More notices above last_id than fitted: poll again now.",
    )


class NoticeReadInput(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=500)
    up_to: int | None = Field(
        default=None, ge=0, description="Everything up to and including this id."
    )

    @model_validator(mode="after")
    def _ids_or_up_to(self):
        if bool(self.ids) == (self.up_to is not None):
            raise ValueError("Give either ids or up_to.")
        return self


class NoticeReadResult(BaseModel):
    marked: int
    unread: int


class OfficeLineInput(BaseModel):
    """One row of the item table, as the office leaves it."""

    move_id: int | None = Field(
        default=None,
        description="An existing line to keep and update. Omit for a new one.",
    )
    size: str | None = Field(default=None, max_length=64)
    pcs: float = Field(default=0.0, ge=0)
    carats: float = Field(default=0.0, ge=0)
    job_type_id: int | None = Field(
        default=None, description="May stay empty in a draft; READY needs it."
    )
    remarks: str | None = Field(default=None, max_length=256)
    request_id: int | None = Field(
        default=None,
        description="The requested service this line was entered from.",
    )


class OfficeLinesInput(BaseModel):
    lines: list[OfficeLineInput] = Field(
        default_factory=list, max_length=500,
        description="The whole table, in order: rows left out are removed, "
                    "and Sr follows the order given.",
    )


class OfficeReadyResult(BaseModel):
    code: str = Field(..., examples=["OK", "ALREADY_DONE"])
    state: str = Field(..., examples=["assigned"])
    reference: str
