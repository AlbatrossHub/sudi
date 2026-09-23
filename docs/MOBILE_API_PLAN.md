# Sudi — Mobile API and offline-first field apps: backend implementation plan

Scope of this document: **the backend only.** It defines the addons, the auth
model, the sync protocol, the write contract and the phasing. The client plan
(Flutter/Dart) follows once this is agreed; §12 lists what the client team is
handed at the end of each stage.

Reference implementation throughout: `/opt/odoo19/custom/investo_be` — same
house style (OCA `fastapi`, hand-rolled JWT in `ir.config_parameter`, thin
routers over domain addons, Pydantic v2 schemas, ORM tests per addon).

---

## 1. What exists today

Three Odoo *web* nav apps in the `diamond` addon, all on `stock.picking`:

| App | Menu | Gated by | Records shown | Operator actions |
|---|---|---|---|---|
| Pickup | `menu_sudi_operator_pickup_root` | `group_sudi_pickup_app` | incoming, `state = sudi_pickup_pending` | confirm pickup, cancel pickup (both via wizard) |
| Deliveries | `menu_sudi_operator_deliveries_root` | `group_sudi_pickup_delivery_operator` | outgoing with an origin receipt; awaiting / out / today's delivered | take for delivery, release, mark delivered |
| Job Work | `menu_sudi_operator_job_work_root` | `group_sudi_pickup_delivery_operator` | incoming, `state = assigned` | timer start/stop, transfer department, finish job work |

Customer side: a server-rendered PWA at `/jangad` (`diamond/controllers/main.py`)
with a manifest, a network-first service worker and an offline page. It uploads
**one** image per receipt and creates the receipt through
`stock.picking.sudi_create_public_jangad_receipt`. Login is phone + OTP over
WhatsApp (`web_auth_otp_login` + `open_whatsapp_connector`), followed by GST
onboarding at `/web/gst_onboarding`.

State machine that the API must respect (all on `stock.picking`):

```
Receipt (incoming):  sudi_pickup_pending --confirm--> draft --confirm/assign--> assigned --validate--> done
                                          --cancel--> cancel + active=False
Delivery (outgoing): sudi_delivery_stage  awaiting --take--> out --deliver--> delivered
                                                   <--release--
```

`sudi_delivery_stage` and `state` are **computed and stored**; the API never
writes them directly, it calls the existing action methods.

---

## 2. Findings that must be fixed before anything is exposed over HTTP

> **Status: done.** All eight are fixed in `diamond` 19.0.1.4.0, verified with
> `-u diamond --test-enable --test-tags=/diamond` against a clone of the `sudi`
> database: 63 tests, 0 failures, 0 errors. Two consequences and one unrelated
> pre-existing defect surfaced while doing it — see §2.9.

These are pre-existing and are only listed because an API turns each of them
from a UI quirk into a reachable defect. They form **Stage 0**.

1. **Job Work and Deliveries share one group.** Both menus are gated on
   `diamond.group_sudi_pickup_delivery_operator`. The requirement is that job-work
   people are *different* people from pickup/delivery. Needs a new
   `group_sudi_job_work_user`, the Job Work menu re-gated onto it, and the
   record rules split accordingly.
2. **Record rules are granted to `base.group_user`, not to the operator group.**
   `rule_sudi_operator_*` in `security/diamond_security.xml` all carry
   `groups="base.group_user"`, so *every* internal user can read pending pickups,
   active deliveries and assigned job work. Over the web this is obscured by menu
   visibility; over an API there are no menus.
3. **`_sudi_check_pickup_delivery_operator_access` only checks `base.group_user`.**
   So any internal user can confirm any pickup, take any delivery and mark
   anything delivered. Must check the role group.
4. **Event timestamps are server-generated.** `action_sudi_confirm_pickup`,
   `action_sudi_take_for_delivery` and `action_sudi_mark_delivered` all write
   `fields.Datetime.now()`. An action captured offline at 10:05 and flushed at
   14:40 would be recorded at 14:40. Every one of these needs an optional
   caller-supplied `occurred_at`, a server clamp (not in the future, not older
   than a configurable window), and the *received* time kept separately.
5. **One jangad image per receipt.** `sudi_jangad_image` is a single
   `fields.Image`. Multi-page jangads need `ir.attachment` records instead
   (see §8.3). Open question Q6.
6. **Two operator actions return `ir.actions.act_window`.**
   `action_sudi_transfer_department` and `action_sudi_open_confirm_pickup_wizard`
   return window actions; an API cannot consume those. The plain methods
   (`action_sudi_confirm_pickup`, `action_sudi_cancel_pickup`) already exist;
   department transfer needs an equivalent `_sudi_transfer_department(job_type)`.
7. **`rule_sudi_operator_today_done_deliveries` is a moving time window.**
   `date_done >= today` means a record silently leaves the operator's scope at
   midnight with no write to detect. The delta protocol must not derive scope
   from that rule — see §7.4.
8. **OTP verification state lives in the HTTP session.**
   `web_auth_otp_login` stores `otp_code`, `otp_phone`, `otp_time`,
   `otp_user_id` in `request.session`, and the plaintext code at that. A
   stateless token API cannot use it, and it should not: port
   `investo_be/auth_custom/models/auth_otp.py` (salted PBKDF2 hash, TTL,
   attempt and per-hour rate limits, `consumed_at`) as a model. See §5.2.

### 2.9 What the Stage 0 build turned up

- **An operator who confirms a pickup immediately lost sight of it.** I first
  recorded this as "correct as designed". It was not. Confirming moves the
  receipt from the pickup scope into the job-work scope, and those are now
  different roles, so the operator could no longer read the record a second
  after acting on it: no way to review the round, to check the jangad they had
  just collected against, or to notice a parcel they had missed. The old record
  rules hid the hole by granting every internal user everything.

  **Fixed in the flow rather than papered over.**
  `rule_sudi_operator_own_pickups_today`, with its move and move-line siblings,
  keeps a receipt with the operator who collected it for the rest of the day —
  which is exactly what `rule_sudi_operator_today_done_deliveries` already did
  for the delivery side, so this restores a symmetry rather than inventing a
  concept. It is **visibility only**: the timer, department transfer and
  finishing stay gated on the Job Work User role.

  The `pickup` sync scope and the Pickup app list gained the same tail, so the
  web and the app agree. `PickupDoc` carries `stage` (`awaiting` / `collected`)
  so one list holds both, and the web action defaults to its "To Collect"
  filter, leaving the day's own round one tap away.
- **Proof of delivery ships switched off.** Requiring a receiver name changes what
  an existing "Mark Delivered" click does, for the office web form as much as for
  the app, so all three switches default to `0` and the operations team turns them
  on when they are ready. Enforcement and its tests are in place either way.
- **Unrelated, pre-existing, still open:** the
  `base.group_system` → `group_sudi_reference_ledger` implication in
  `diamond_security.xml` sits inside a `noupdate="1"` block, so no *upgraded*
  database ever receives it — only fresh installs. That is exactly why
  `test_reopen_reference_statement_is_admin_only_and_releases` fails on any real
  database, and it means a system administrator cannot reopen a reference
  statement in production. It belongs to the in-flight billing work, so it is
  flagged here rather than fixed.

---

## 3. Addon layout

Two new addons in the existing `/opt/odoo19/custom/sudi/` addons directory,
plus changes to `diamond`:

```
sudi/
├── diamond/                 (changed)  Stage 0 fixes: groups, occurred_at, attachments
├── sudi_sync/               (new)      offline machinery, no FastAPI dependency
│   ├── models/sudi_sync_change.py      the change log and its cursor
│   ├── models/sudi_api_device.py       device registry, per-device token epoch, push token
│   ├── models/sudi_idempotency.py      (partner|user, endpoint, key) -> stored response
│   ├── models/sudi_upload.py           staged binaries awaiting an intent
│   ├── models/stock_picking.py         _sudi_sync_payload(), the denormalised read shape
│   └── tests/                          ORM tests: cursor monotonicity, replay, scope exit
└── sudi_api/                (new)      the HTTP surface
    ├── jwt_tokens.py                   copy of investo_api/jwt_tokens.py + device claim
    ├── dependencies.py                 bearer -> user env, role gates, idempotency
    ├── schemas.py                      Pydantic v2
    ├── errors.py                       the typed error taxonomy of §7.6
    ├── models/fastapi_endpoint.py      two apps: sudi_field, sudi_customer
    ├── routers/ auth, device, sync, pickup, delivery, jobwork, upload,
    │            customer_auth, customer_jangad, customer_receipt
    └── tests/
```

**Why `sudi_sync` is separate from `sudi_api`:** the change log, the idempotency
table and the payload builders are ORM code with real invariants (cursor
monotonicity, replay-once, scope-exit tombstones). Keeping them out of the HTTP
addon means they are unit-testable with `TransactionCase` and no HTTP client,
and a second consumer (a webhook, a back-office batch) can reuse them. This
mirrors how `investo_wallet` holds `investo.idempotency.key` while
`investo_api` merely reads it.

**Why two `fastapi.endpoint` records rather than one:**

```python
app = fields.Selection(selection_add=[
    ("sudi_field", "Sudi Field Ops API"),      # root_path /api/field/v1
    ("sudi_customer", "Sudi Customer API"),    # root_path /api/customer/v1
])
```

Same codebase, same JWT helpers, but two router lists, two OpenAPI documents and
two independently disableable surfaces. The staff app's operations never appear
in the customer app's contract, and a bug or a rate-limit problem on one does
not take the other down. Cost is one extra data record.

---

## 4. Actors and roles

| Actor | Odoo user | Login | Group | App |
|---|---|---|---|---|
| Pickup + delivery operator | internal | login + password (no signup) | `group_sudi_pickup_delivery_operator` | field |
| Job work person | internal | login + password (no signup) | `group_sudi_job_work_user` *(new)* | field |
| Billing reviewer / office | internal | Odoo web | `group_sudi_billing_reviewer` | none — stays on the web client |
| Customer | portal | phone + OTP, self-signup | `base.group_portal` | customer |

Pickup and delivery are the same person, so one group covers both — that matches
the requirement and today's menus. An operator may additionally hold the job-work
group; the JWT carries whatever they hold.

Office staff deliberately get **no** app. The billing review screen
(`sudi_diamond_billing`) is a desktop-density review tool; re-implementing it on
a phone is out of scope and would double the billing surface area.

---

## 5. Authentication

### 5.1 Token design

Copy `investo_api/jwt_tokens.py` verbatim — HS256, secret in
`ir.config_parameter` generated by a `post_init_hook`, `iss`/`sub`/`typ`/`exp`/
`jti` claims, epoch-based revocation — and add two things:

```python
payload = {
    ...,                        # as investo_api
    "aud": "field" | "customer",   # which endpoint may accept this token
    "dev": device_id,              # the registered device
    "dev_epoch": device.token_epoch,
    "roles": ["pickup_delivery", "job_work"],   # UI hint only, never a gate
}
```

- **`aud`** is checked by the dependency, so a customer token presented to a
  field route is a 401, not a 403 from a group check deeper in.
- **`dev` + per-device epoch** is the change from investo. Investo's `logout`
  bumps `res.users.api_token_epoch`, which signs out every device of that user.
  For field staff that is the wrong granularity: "operator lost their phone"
  must revoke one device and leave the rest working, and the device is also the
  natural owner of the sync cursor. Keep the user-level epoch as the
  break-glass ("revoke everything for this user") and add the device epoch for
  the normal case.
- **`roles`** exists so the client can draw its navigation before the first
  network call. **Every route still enforces the group server-side.** This must
  be stated in the client brief or someone will trust it.

TTLs, changed from investo's defaults because the whole point is working away
from a signal:

| Token | investo | here | why |
|---|---|---|---|
| access | 60 min | **24 h** | an operator out of coverage all morning must still flush at 14:00 without a refresh round trip |
| refresh | 30 d | **90 d** | field phones are not re-enrolled often; revocation is by device, not by expiry |

Offline app unlock is a **client** concern and must not be confused with the
token: the app holds a local PIN/biometric gate over an encrypted local
database, and the token is only needed when the radio is back. The server never
sees the PIN.

### 5.2 Staff login

`POST /api/field/v1/auth/login` → `{login, password, device: {...}}` →
token pair + the device record. Reuses `res.users.authenticate` exactly as
`investo_api/routers/auth_router.py:login` does, then:

- rejects the public user,
- rejects any user holding neither field group (the mirror of investo's
  `portal_only` parameter — here it is `field_groups_only`, shipped **on**),
- registers or re-binds the device in the same transaction.

No signup, no password reset in the app: accounts are created in Odoo by an
administrator, which is the stated requirement.

### 5.3 Customer login

`POST /api/customer/v1/auth/otp/request {phone}` → `POST .../otp/verify
{phone, code, device}` → token pair, *or* `{registration_required: true}` when
the phone has no non-guest partner behind it, followed by
`POST .../auth/register {name, vat?}`. This is the same three-step shape the web
flow already has in `diamond/controllers/auth.py`, lifted out of the session.

Requires the `sudi.auth.otp` model of finding §2.8. Port investo's, keeping its
properties: the code is never stored (salted PBKDF2), 5-minute TTL, max 5
attempts, max 5 requests/hour per identifier, `consumed_at` so a code cannot be
replayed. Delivery stays on today's channel — WhatsApp via `open_whatsapp_connector`
(**D4**) — but goes behind a `_deliver(code)` indirection, as investo's does, so a
provider can be swapped in later without touching the router or the schema. The
`delivery_state` / `delivery_error` fields come across in the port and are worth
keeping: `open_whatsapp_connector` is an unofficial connector, OTP is the single
point of failure for every customer login, and a support person needs to be able
to see *that* a code failed to send rather than guess. Stage 8 adds an alert when
the WhatsApp account leaves `connected`.

### 5.4 What the builds settled

Each of these was found by running real HTTP against the mounted app, and none
of them would have shown up in a test that called the router functions directly.

**Stage 2.** *The error envelope comes from a middleware, not an exception
handler.* The OCA addon calls `_clear_fastapi_exception_handlers` over the whole
mounted app tree, on purpose, to hand error rendering back to Odoo — so any
handler registered here is thrown away. `SudiErrorMiddleware` produces the flat
`{code, message, retryable, resync, detail}` body instead, and also catches
`RequestValidationError` so a malformed body answers in the same shape.

*The endpoint record is read with `sudo()`, by id.* The OCA `fastapi_endpoint`
dependency reads it as the **request** user, and the request user is whoever an
Odoo session cookie says it is. A member of staff with a live web session, or a
customer signed in to the `/jangad` PWA on the same phone, would get a 403 about
`fastapi.endpoint` — on a route that never needed access to that record.

**Stage 3.** *System notifications run under `sudo()`.* Confirming a pickup moves
the receipt into the job-work scope, and `_sudi_notify_pickup_confirmed` then
read `sudi_jangad_image` as the operator, who could no longer see it: an
`AccessError` on a successful confirmation. The same latent fault sat in
`_sudi_notify_delivery_completed`, reachable as soon as a delivery is backdated
with `occurred_at` past the "done today" window. All five `_sudi_notify_*`
methods now iterate `self.sudo()`. It went unnoticed because it is
**cache-dependent**: with the record already in the ORM cache the notification
reads from memory and never checks access, so it raises in production far more
often than in a test. The regression test calls `invalidate_recordset()` first
to make it deterministic. The underlying *flow* problem is fixed separately
(§2.9); the `sudo()` stays as defence in depth, since these are system
notifications rather than reads on the actor's behalf in any case.

**Stage 4.** *ORM refusals are mapped in the middleware, not per route.* A
`UserError` escaping a route came back as a bare `{"detail": "..."}` with a 400,
outside the envelope entirely.

*A record that has left the caller's scope is fetched once more.* A bare 404 is
technically true and useless: the app is holding a queued tap and has to tell
the operator **why** it did not land. `intents.record()` re-reads it restricted
to diamond job work of the right direction, and the route's own state checks
turn it into `ALREADY_CONFIRMED`, `ALREADY_TAKEN` or `ALREADY_DONE`.

*`button_validate` runs under `sudo()` after the role check.* It moves a receipt
through states no record rule can describe — a move line written while the
picking is between `assigned` and `done` matches neither — so the alternative
was granting a field role blanket write on `stock.move.line`, which is the
over-granting §2 just removed. `env.user` is unchanged, so the moves and the
chatter still name the operator.

*Job Work User now implies Timesheets/User.* The role was created in stage 0
without the capability it exists for: booking time failed on
`account.analytic.line` create. The implied group is the narrow "own timesheets
only" one, and it belongs to the role rather than to an administrator's memory.

*The timer stops without a wizard.* The web flow returns
`hr.timesheet.stop.timer.confirmation.wizard` so a user can adjust the figure
before saving; a phone has no wizard and an operator has nothing to adjust
against on the road, so `_sudi_stop_timer` banks the measured time.

**Stage 5.** *The registration token replaces the session.* Verification hands
back a short-lived token (15 minutes, its own `typ`, no `sub`) saying only "this
number answered a code"; `/auth/register` will not proceed without one. The
alternative — trusting a phone number in the register call — would let anyone
create an account for any number.

*A staff phone number is refused, not offered registration.* Filtering the
account lookup to portal users would have hidden a staff account rather than
refusing it, and registration would then have created a second user on the same
partner: one person, two accounts, two sets of rights.

*Registration lands on the record the office already has.* The partner id
travels inside the registration token, so a customer whose jangad upload left a
`Guest (…)` partner gets that record renamed rather than a duplicate beside it.

*GSTIN is validated with Odoo's own `check_vat_in`*, which knows the normal,
composite, casual, UN, NRI, TDS and TCS shapes. A hand-rolled regex would get
one of them wrong, and the controller validated nothing at all.

*A skip is not final.* `gst_state` is `present` / `skipped` / `missing` rather
than a boolean, so a customer who skipped can be asked again later.

**Stage 6.** *A validator's `ValueError` was answering 500, not 422.* Pydantic
puts the original exception **object** in the error's `ctx`, and `JSONResponse`
cannot serialise it — so the first `model_validator` written turned a plainly
bad request into an internal error. Every custom validator hits this; plain
field constraints do not, which is why it stayed hidden through stages 2 to 5.
`_jsonable_errors` in `errors.py` now flattens them.

*Staged uploads are read under `sudo()`.* `_sudi_resolve` filters by `user_id`,
and that filter **is** the ownership check — but it then browsed non-sudo, and a
portal customer has no rights on `ir.attachment` at all. The field intents never
showed it, because an operator is an internal user.

### 5.5 An existing weakness stage 5 did not fix

`web_auth_otp_login`, which the `/jangad` PWA still logs in through, generates
its codes with `random.randint(100000, 999999)` and keeps the **plaintext** code
in `request.session`. `random` is a clock-seeded Mersenne Twister, not a CSPRNG:
given one or two observed codes the sequence is recoverable. The new
`sudi.auth.otp` model uses `secrets` and stores only a salted PBKDF2 hash, but
**only the API path goes through it** — the web login still does not. Pointing
the web controller at `sudi.auth.otp` fixes both in one change and is the
obvious follow-on; it is flagged rather than done because it touches the live
customer PWA.

### 5.6 Device registry

`sudi.api.device`: `device_uid` (client-generated, stable), `user_id`,
`platform`, `app_version`, `push_token`, `token_epoch`, `last_seen_at`,
`sync_cursor`, `revoked`. Uniqueness on `(user_id, device_uid)`.

It earns its place three times over: per-device revocation, the push
destination, and the server-side record of where each device's sync cursor
actually reached (useful for support: "this phone last synced at 11:20 and has
4 queued intents").

---

## 6. The read side — delta sync

### 6.1 The shape of the problem

Do not replicate `stock.picking` generically. The phone needs three small,
role-scoped worklists, each a flat denormalised document, and it needs to learn
about changes without re-downloading the lists.

```
GET /api/field/v1/sync/pull?cursor=<opaque>&scopes=pickup,delivery,jobwork
→ {
    "cursor": "<next>",
    "full_resync": false,
    "pickup":   {"upserts": [ {...}, ... ], "gone": [1234, 1235]},
    "delivery": {"upserts": [...],          "gone": []},
    "jobwork":  {"upserts": [...],          "gone": []},
    "server_time": "2026-09-23T09:14:02Z"
  }
```

- **`upserts`** — whole documents, not field diffs. A picking payload is ~1 KB
  including its moves; diffing per field would save bytes and cost far more in
  client complexity and bug surface.
- **`gone`** — ids the device must delete because they left the role scope
  (delivered yesterday, archived, reassigned to another operator). Without this
  the phone accumulates stale work forever.
- **`full_resync: true`** — the server's answer when the cursor is older than
  the change-log retention (§6.3) or the device's role set changed. The client
  wipes its cache and pulls from cursor 0. One code path, no clever repair.

### 6.2 The change log

`sudi.sync.change`: `seq` (bigserial), `model`, `res_id`, `op`, `company_id`.
Written from `create`/`write`/`unlink` overrides on `stock.picking`,
`stock.move` and `account.analytic.line` — a single insert per write on those
models, no triggers, no logical decoding.

Why a log at all, rather than `write_date > since`:

- `write_date` is not indexed on `stock_picking`, and adding a plain index on it
  is a write-amplification cost on Odoo's busiest table;
- unlinks and archivals leave nothing to query;
- a *move* changing must invalidate its *picking* payload, which the log can
  record directly (`model='stock.picking', res_id=<the picking>`) instead of
  making the client join.

**The commit-order trap, and the fix.** A bigserial is allocated before commit,
so transaction A can take `seq=100`, transaction B take `seq=101` and commit
first. A client that reads up to 101 and stores that as its cursor never sees
100. The mitigation is a **visibility lag**: the endpoint only serves rows whose
`logged_at < clock_timestamp() - 2 s` (configurable) and returns the max `seq`
among those. A 2-second staleness is invisible in this workflow and removes the
whole class of lost updates. Two details from the build matter: the row is
written at **precommit**, so `logged_at` lands within a moment of the commit
that caused it rather than at the start of a long transaction; and the
comparison uses `clock_timestamp()` and not `now()`, because `now()` is the
*reading* transaction's start time and would measure the lag from the wrong
moment — a bug the test suite caught. (The alternatives — a serialised cursor table, or
`pg_snapshot_xmin`-based bookkeeping — are more correct and much more code; if
we ever need sub-second sync we revisit.)

### 6.3 Retention and cost

The log is append-only and grows with write volume. A daily cron deletes rows
older than `sudi_sync.retention_days` (default 30). Any device whose cursor
predates the oldest row gets `full_resync: true`. 30 days is far longer than a
field phone realistically stays dark, and the table stays small.

### 6.4 Scoping — explicitly, not by record rule

Per finding §2.7, the delta query uses an **explicit domain per scope**, under
`sudo()`, built by `sudi_sync` — the same pattern and the same reasoning as
`investo_api`'s `authenticated_kyc_request`, which comments that ownership is
enforced in code because the record rules cannot express it. Record rules stay
in place as defence in depth for single-record reads, but the protocol's notion
of "in scope" is code the test suite can pin:

| Scope | Domain |
|---|---|
| `pickup` | job work, incoming, `state = sudi_pickup_pending`, **plus** whatever `sudi_pickup_user_id = me` collected today (§2.9) |
| `delivery` | job work, outgoing, has origin receipt, stage in (`awaiting`, `out`), **plus** `delivered` where `sudi_pickup_user_id = me` and `date_done >= today` |
| `jobwork` | job work, incoming, `state = assigned`, department/user filter per Q5 |

The "today's delivered" tail is the moving window of §2.7. Handled by having the
client apply the same date rule locally at midnight — the server also emits a
`gone` for those ids on the next pull after the day rolls, so the two agree.

### 6.5 Performance

- One `search_read` per scope with an explicit field list, then one `read_group`
  for the move aggregates. No `browse` loops, no per-row computes — the existing
  `_compute_sudi_total_hours_spent` already does this correctly and is the model
  to follow.
- Partial index, added from `sudi_sync`'s `init()`:
  `CREATE INDEX ... ON stock_picking (write_date) WHERE sudi_is_diamond_job_work`.
  A plain index on `sudi_is_diamond_job_work` would be useless — it defaults to
  `True` on every picking in the database.
- `ETag` + `If-None-Match` on the pull, so an unchanged poll is a 304 with no
  body. `investo_api`'s market routes already do this.
- Keyset pagination with `limit` (default 200, max 500) and a
  `has_more` flag; a first sync on a fresh phone must not be one 5 MB response.

---

## 7. The write side — intents

### 7.1 The model

The phone does not write records; it **queues typed intents** in a local outbox
and replays them. Each intent is a single POST, carries a client-generated
`Idempotency-Key` created *at capture time* (so every retry of that tap reuses
it), and carries the `occurred_at` of the capture.

| Intent | Route (`/api/field/v1`) | Underlying method | Offline |
|---|---|---|---|
| confirm pickup | `POST /pickups/{id}/confirm` | `action_sudi_confirm_pickup` | yes |
| cancel pickup | `POST /pickups/{id}/cancel` | `action_sudi_cancel_pickup` | yes |
| take deliveries | `POST /deliveries/take` `{ids}` | `action_sudi_take_for_delivery` | yes |
| release delivery | `POST /deliveries/{id}/release` | `action_sudi_release_delivery` | yes |
| mark delivered | `POST /deliveries/{id}/deliver` | `action_sudi_mark_delivered` | yes |
| timer start/stop | `POST /jobwork/{id}/timer` | analytic line create | yes |
| transfer department | `POST /jobwork/{id}/department` | new `_sudi_transfer_department` | yes |
| finish job work | `POST /jobwork/{id}/finish` | `button_validate` | **no** — see §7.5 |

### 7.2 Idempotency

`sudi.idempotency.key`, a copy of `investo.idempotency.key` with `user_id`
alongside `partner_id`: unique on `(owner, endpoint, key)`, storing the first
response verbatim. The uniqueness is enforced by the database index, not a
check-then-insert — investo's docstring on that file explains exactly why, and
the same race exists here whenever a phone retries on a flapping connection.

### 7.3 Client-supplied time

Each intent body carries `occurred_at` (ISO-8601, device clock, with the device
timezone). The server:

- rejects a timestamp in the future by more than `clock_skew_seconds` (60),
- rejects one older than `max_backdate_hours` (default 72) — beyond that the
  intent is stale and needs a human,
- writes `occurred_at` into the domain field (`sudi_pickup_datetime`,
  `date_done`, the timesheet's `date`),
- records `received_at`, the device and the app version in the chatter, so the
  audit trail shows both times.

Field 4 of §2 is the ORM change that makes this possible; without it every
offline event is mis-timed and the delivery/pickup reports are wrong.

### 7.4 Ordering

The outbox flushes **serially per record**, in capture order, and independently
across records. One delivery's conflict must not block another delivery's
flush. This is a client rule, but the server enables it by making every intent
independently idempotent and by never requiring intents to arrive as a batch.

### 7.5 Which intents are *not* offline

`finish job work` calls `button_validate`, which runs the full stock validation
chain (reservations, lot/serial, backorder wizards). Its outcome cannot be
predicted on the device, so queueing it offline would mean showing the operator
a success that may not happen. It requires connectivity, and the app says so.
Same reasoning for anything touching billing.

This is decision **D2**: field events are offline, stock validation and billing
are online.

### 7.6 The error taxonomy

Every 4xx from an intent route answers in one shape. This is the single most
important part of the contract for an offline client, because the client has to
decide, without a human, whether to retry or to drop.

```json
{
  "code": "ALREADY_TAKEN",
  "message": "Taken for delivery by Rakesh at 09:12.",
  "retryable": false,
  "resync": [1234],
  "detail": {"picking_id": 1234, "user": "Rakesh", "at": "2026-09-23T09:12:00Z"}
}
```

| code | HTTP | retryable | client behaviour |
|---|---|---|---|
| `VALIDATION` | 422 | no | drop, show the message |
| `NOT_IN_SCOPE` | 404 | no | drop, resync the record |
| `ALREADY_DONE` | 200 | — | treat as success (idempotent replay) |
| `ALREADY_TAKEN` / `ALREADY_CONFIRMED` | 409 | no | drop, resync, tell the operator who won |
| `STALE_INTENT` | 409 | no | drop, ask the operator to redo it |
| `CLOCK_SKEW` | 422 | no | drop, tell the operator the device clock is wrong |
| `LOCKED` / `SERIALIZATION` | 409 | **yes** | retry with backoff |
| `AUTH` | 401 | yes after refresh | refresh, then retry once |
| `DEVICE_REVOKED` | 401 | no | wipe the local database, return to login |
| `SERVER` | 5xx | yes | retry with backoff |

`DEVICE_REVOKED` is the remote-wipe signal: it is returned whenever the token's
`dev` claim names a revoked device, and it is the reason revocation is
per-device (§5.1). Administrators need a "this phone is lost" button that makes
the data on it unreachable; the client contract is that this code wipes local
storage.

The existing action methods raise `UserError`/`AccessError`. The routers map
those to `VALIDATION`/`AUTH`, but the *conflict* cases must be detected before
calling the method (state check under a `FOR UPDATE` row lock) so the response
can carry the `detail` that lets the app explain what happened. Raw
`UserError` text — "Select deliveries that are still awaiting confirmation" —
is not an answer an offline client can act on.

All 4xx go through investo's `client_error()` helper, which sets `loglevel` so
Odoo does not print a hundred-line traceback for every wrong OTP or lost race.

---

## 8. Binaries — jangad images, delivery proof

### 8.1 Two-phase upload

```
POST /uploads            multipart, one file → {"upload_id": "...", "sha256": "..."}
POST /pickups/{id}/confirm   {..., "upload_ids": ["..."]}
```

Separating the binary from the intent is what makes a photo survive a bad
connection: the intent is a few hundred bytes and lands the moment there is any
signal, while the image retries on its own. It also leaves room for chunked or
resumable upload later without changing the intent routes.

`sudi.upload`: the staged `ir.attachment`, its owner, its `sha256`, `consumed_by`
and an expiry. A cron deletes unconsumed uploads after 7 days. The `sha256` is
returned so the client can prove the upload landed intact before deleting its
local copy.

### 8.2 Compression policy

Server side: accept `image/jpeg`, `image/png`, `image/webp` (as the PWA already
does), cap at 8 MB per file, re-encode nothing by default.

Client side, and this belongs in the plan because the server depends on it: long
edge **1600–2000 px**, JPEG quality ~80, EXIF stripped except orientation. A
jangad is a handwritten slip and the digits on it are the point — going below
1600 px starts losing them, which is worse than the bandwidth it saves. Expect
~250–400 KB per page. The device keeps the original until the server acks the
`sha256`, then deletes it.

### 8.3 Multi-page jangads

Per finding §2.5, `sudi_jangad_image` holds one image. If a jangad can run to
several pages — Q6 — the model needs `sudi_jangad_attachment_ids`
(`ir.attachment`), the existing `jangad_image_viewer` widget extended to a
gallery, and `sudi_jangad_image` kept as the first page for backwards
compatibility with the reports (`report/diamond_receipt_report.xml`) and the
WhatsApp templates, which attach it today.

---

## 9. Customer API

`/api/customer/v1`, portal users only, `aud = customer`.

| Method | Route | Purpose |
|---|---|---|
| POST | `/auth/otp/request` | phone → OTP (WhatsApp today) |
| POST | `/auth/otp/verify` | code → tokens, or `registration_required` |
| POST | `/auth/register` | registration token + name + optional GST → portal user + partner |
| POST | `/auth/refresh`, `/auth/logout` | as staff |
| GET | `/me` | profile, GST state, whether onboarding is complete |
| POST | `/gst` | submit GSTIN → validate, enrich, link the company partner |
| POST | `/gst/skip` | carry on without one (D3); not final |
| GET | `/addresses` | pickup-address suggestions for this customer |
| POST | `/addresses` | add a manual pickup address |
| POST | `/jangad` | create a receipt from staged uploads — **the offline one** |
| ~~GET~~ | ~~`/receipts`~~ | **descoped** — not built |
| ~~GET~~ | ~~`/receipts/{id}`~~ | **descoped** — not built |
| ~~GET~~ | ~~`/invoices`~~ | **descoped** — not built |

The `/jangad` intent reuses the whole §7 machinery — idempotency key,
`occurred_at`, two-phase upload — so an image captured in a basement submits
itself when the customer walks outside. Server side it calls the existing
`sudi_create_public_jangad_receipt`, which already resolves the partner by
phone and validates that the chosen address belongs to it.

`GET /receipts` **is not built.** It was argued for here as the reason a
customer keeps the app: after uploading they see nothing at all, and deriving a
customer-facing stage from `state` + `sudi_delivery_stage` +
`sudi_billing_status` ("picked up → in job work → out for delivery → delivered
→ invoiced") would be cheap. The product owner descoped it, so submission is
the whole of the customer app for now and the `reference` returned by
`POST /jangad` is the only acknowledgement the customer gets. The derivation
stays cheap whenever it is wanted.

**GST onboarding keeps today's behaviour (D3):** `POST /gst` enriches from the
GSTIN via `l10n_in`, creates or links the company partner and accepts it with no
human review, and the **skip** (`x_skip_gst`) stays — so a customer can reach
`/jangad` without a GSTIN, exactly as the web flow allows. No `kyc.request`
state machine, no review queue, no extra stage.

Two consequences to build for deliberately:

- `GET /me` returns `gst_state` as one of `present` / `skipped` / `missing`
  rather than a bare boolean, so the app can keep nudging a `skipped` customer
  on a later launch instead of treating the skip as final.
- `_get_or_create_gst_company_partner` (in `diamond/controllers/auth.py`) runs
  under `SUPERUSER_ID`, creates a partner from customer-supplied input, and
  swallows enrichment failures with a bare `except Exception`. Moving it out of
  the controller into a model method that the router and the web flow share is
  part of stage 5 — it needs GSTIN format validation and a duplicate check it
  does not have today, because over an API it is an unauthenticated-ish
  partner-creation primitive.

---

## 10. Push notifications

The existing `_sudi_notify_pickup_scheduled` / `_sudi_notify_delivery_assigned`
family posts to Discuss and WhatsApp. Add a third sink: FCM (Android) and APNs
(iOS) to the `push_token` on `sudi.api.device`.

Two kinds of message, and the distinction matters for offline:

- a **data** message that carries no content and only tells the app "pull now" —
  this is what makes a queued sync happen promptly rather than at next
  foreground;
- a **notification** message for the things a human must see: a new pickup in
  the area, a delivery assigned to them.

Sent from a queued job, never inline in the request that caused it — a dead push
gateway must not fail a delivery confirmation.

The Odoo bus is not usable here; a backgrounded phone holds no long poll.

---

## 11. Phasing

Each stage ends green (ORM + router tests) and is independently deployable.

| Stage | Contents | Depends on |
|---|---|---|
| **0** | **Done** (19.0.1.4.0) — job-work group split + carry-over migration, record rules and ACLs re-gated, role checks tightened, `occurred_at` on the event actions, `_sudi_transfer_department`, multi-page jangad, proof of delivery | — |
| **1** | **Done** (`sudi_sync` 19.0.1.0.0) — change log + cursor + visibility lag + retention cron, device registry with per-device epochs, idempotency keys, upload staging, scope domains, payload builders and the whole pull. 66 ORM tests, no HTTP. | 0 |
| **2** | **Done** (`sudi_api` 19.0.1.0.0) — two endpoint records, JWT with audience/device/role claims, the error envelope, `/health`, staff `/auth/login|refresh|logout`, `/auth/me`, `/devices`. 28 HTTP tests. | 1 |
| **3** | **Done** — `GET /sync/pull` with role-entitled scopes, weak ETag, both paging modes, `gone`, `full_resync`, device cursor recording, and `GET /pickups/{id}/jangad/{page}`. 22 more HTTP tests. | 2 |
| **4** | **Done** — the eight intents with row-locked conflict detection, idempotency, `occurred_at` clamping, `POST /uploads`, and the taxonomy in anger. 34 more HTTP tests. | 3 |
| **5** | **Done** — `sudi.auth.otp` (hashed, rate limited), OTP request/verify/register with a registration token, `/gst` and `/gst/skip`, and the GST resolution moved onto `res.partner` and shared with the web form. 34 more HTTP tests. | 2 |
| **6** | **Done, narrowed** — `POST /jangad` and `GET /addresses` only. `/receipts` and `/invoices` were **descoped** by the product owner: the customer app's job right now is to get a photograph of a handwritten slip into the office, and nothing else. 22 more HTTP tests. | 5, 4 |
| **7** | Push: device tokens wired into the existing notification methods, queued sender. | 4 |
| **8** | Hardening: per-route rate limits, audit trail on every intent, load test of `/sync/pull` at expected device count, `openapi.json` frozen and handed over. | all |

Stages 3–4 (field) and 5–6 (customer) are independent after stage 2 and can run
in parallel if there are two people.

---

## 12. What the client team receives

The brief is written and frozen **ahead of** the backend, so the client team can
build the offline core in parallel:
[`FLUTTER_INTEGRATION_BRIEF.md`](FLUTTER_INTEGRATION_BRIEF.md). Following the
`investo_be` precedent (`docs/FLUTTER_INTEGRATION_BRIEF.md`, which worked), a
frozen `openapi.json` per endpoint joins it at the end of stage 4 and again at
stage 6. **Both are exported**: `docs/openapi.field.json` (19 operations, 36
schemas) and `docs/openapi.customer.json` (15 operations, 21 schemas),
generated from the running endpoints rather than written by hand. Between them they cover what the spec cannot express —

- the outbox contract: capture-time idempotency keys, serial-per-record flush,
  the retryable/drop table of §7.6;
- the local schema mirroring the sync payloads, and the `gone`/`full_resync`
  rules;
- the image policy of §8.2;
- that `roles` in the JWT is a UI hint and the server is the authority;
- which intents are online-only and why (§7.5).

---

## 13. Assumptions this plan makes

Stated so they can be corrected cheaply rather than discovered late.

- A1. Field staff are internal Odoo users created by an administrator; there is
  no self-signup and no in-app password reset.
- A2. *(Now decision D2.)* Offline covers field *events* and customer *jangad
  submission*. Stock validation, billing and invoicing stay online (§7.5).
- A3. Receipt line data entry (`sudi_sr`, `sudi_size`, `sudi_pcs_qty`,
  `sudi_carats`, job type) stays in the Odoo web client — the operator apps are
  read-only on items today. See Q5.
- A4. One company. Multi-company would add `company_id` to the cursor and the
  scopes.
- A5. Office/billing users keep using the Odoo web client; no app for them.
- A6. English only in the first release; the API returns codes, and the client
  owns the strings, so localisation later costs nothing server-side.

---

## 14. Decisions taken

- **D1. App packaging — one codebase, two build flavors.** Staff and customer
  ship as separate app ids and store listings from one Flutter project, sharing
  the sync engine, outbox, HTTP client and local database. This is why §3 mounts
  two `fastapi.endpoint` records: each flavor gets its own root path and its own
  OpenAPI document, and neither contract mentions the other's operations.
- **D2. Offline scope — field events and customer jangad only.** As §7.5:
  the eight intents of §7.1 minus `finish job work`, which needs connectivity
  because `button_validate` runs the stock reservation/backorder chain. Billing
  is online. Item data entry stays in the web client (A3, and Q5 below).
- **D3. GST — auto-accept on enrichment, skip retained.** No review queue, no
  `kyc.request` port. See §9 for the two things this still requires.
- **D4. OTP — stays on `open_whatsapp_connector`.** Behind a `_deliver()`
  indirection with delivery-state tracking, so the provider is replaceable and
  a failed send is visible. See §5.3.

## 15. Open questions

None of these block stages 0–3. Each is flagged at the stage that needs it.

- **Q5. Job-work scoping and data entry.** Should a job-work person see *all*
  assigned receipts, only their department (`sudi_current_department_id`), or
  only ones assigned to them (`user_id`)? And should they be able to enter or
  correct item lines (pcs/carats/size) from the phone, or is that office-only?
- ~~**Q6. Multi-page jangads.**~~ **Answered: many.** Built in stage 0
  (`sudi_jangad_attachment_ids`, page 1 still in `sudi_jangad_image`).
- ~~**Q7. Proof of delivery.**~~ **Answered: yes, all three.** Built in stage 0:
  receiver name, drawn signature and photos are always captured, and which of
  them is *mandatory* is an `ir.config_parameter` so operations can tighten it
  without an app release. All three ship off.
- **Q8. Location capture.** Record device lat/lon on pickup/delivery
  confirmation? Useful for disputes, but it is employee tracking and needs a
  decision (and an OS permission) rather than a default.
- **Q9. Device policy.** One active device per operator (new login revokes the
  old), or several? Diamonds on a lost phone argue for one, plus remote wipe of
  the local cache on revoke.
- **Q10. Does the `/jangad` PWA stay?** Running it alongside the customer app is
  fine and is a good fallback for iOS users who will not install; it just means
  two customer surfaces to keep in step.
- **Q11. Expected scale.** How many field devices, and how many customers? It
  sets the `/sync/pull` poll interval, the log retention and whether stage 8
  needs a real load test.
