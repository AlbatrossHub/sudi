# Sudi — Flutter ↔ Backend integration brief

A self-contained handoff for the client team. Drop this file into the app repo
and point a session at **this brief + the app code**; once the backend reaches
stage 4 an `openapi.json` lands beside it and becomes the source of truth for
shapes.

> **Read this first — status.** Unlike the Investo brief, this one is written
> **before** the backend exists. It is the *frozen contract* the backend is being
> built to, not a description of a running server. That is deliberate: the
> offline machinery is most of the client work and none of it needs a live API to
> build. Everything marked **[FROZEN]** will not change under you. Everything
> marked **[PENDING Qn]** is an open product question — build around it, do not
> build on it. §11 lists the pending set and what each one moves.
>
> Backend plan, with the reasoning behind every decision here:
> [`MOBILE_API_PLAN.md`](MOBILE_API_PLAN.md).

---

## 1. What is being built

Two apps from **one Flutter codebase, two build flavors** (decision D1):

| Flavor | Audience | Base URL | Auth | Offline |
|---|---|---|---|---|
| `field` | pickup/delivery operators, job-work staff — internal Odoo users | `/api/field/v1` | login + password, **no signup** | the whole worklist + event capture |
| `customer` | diamond customers | `/api/customer/v1` | phone + OTP, self-signup | jangad capture + submission |

They share `lib/core/` — HTTP client, token store, local database, the outbox,
the sync engine, image compression — and differ in `lib/features/`, theme,
app id, icon and store listing. Nothing staff-only ships in the customer binary.

What these apps replace: three Odoo *web* nav apps (Pickup, Deliveries, Job
Work) that field staff currently use through a mobile browser, and the
server-rendered PWA at `/jangad` that customers use to upload. Office and
billing staff stay on the Odoo web client — there is no app for them, and no
endpoint either.

**The reason this project exists is connectivity.** Field staff lose signal
mid-round; customers photograph a jangad in a basement. An app that needs the
network to record an event is no better than the web views. So: §4 (the local
database), §5 (the outbox) and §6 (conflicts) are not optimisations layered on
at the end. They are the architecture. Build them first, on a fake backend,
before any screen.

---

## 2. Pointing at the backend

The API is mounted by the `sudi_api` Odoo addon as two endpoints on the same
server. The database is appended as `?db=` on every call — the server hosts
several.

| Target | Base URL |
|---|---|
| Chrome / desktop | `http://localhost:8069/api/field/v1` |
| Android emulator | `http://10.0.2.2:8069/api/field/v1` |
| Device on USB | `adb reverse tcp:8069 tcp:8069`, then `localhost` |
| Device on LAN | the dev machine's address |

`GET /health` (no auth) is the liveness probe on both endpoints — hit it first.
Swagger UI at `<base>/docs`, schema at `<base>/openapi.json`, once stage 2 is up.

Until then, build against a stand-in server in `test/support/`, the same way the
Investo client did (`fake_backend.dart`). It needs to be a bit more than a
response map here: give it an in-memory change log so `/sync/pull` really does
return deltas and `gone` lists, and a switch to make any intent answer 409. You
cannot test the outbox against a server that always says yes.

---

## 3. Auth, devices and what a token means

### 3.1 The flows

**Field [FROZEN]**

```
POST /auth/login   {login, password, device: {...}}  → {access_token, refresh_token, expires_in, device_uid, roles}
POST /auth/refresh {refresh_token}                   → a new pair
POST /auth/logout                                    → 204, revokes THIS device
GET  /me                                             → profile + roles
```

There is no signup, no password reset, no "forgot password" in the field app.
Accounts are created by an administrator in Odoo. A wrong password is a 401 with
`code: "AUTH"` and a deliberately vague message — do not build a
"user not found" branch, the server will not tell you.

**Customer [FROZEN except where noted]**

```
POST /auth/otp/request {phone}                → 200 (always — see below)
POST /auth/otp/verify  {phone, code, device}  → tokens, OR {registration_required: true}
POST /auth/register    {name, vat?}           → tokens          (only after a verify that asked for it)
POST /gst              {vat}                  → GST accepted and linked
```

OTP rules, mirroring the existing web flow and the Investo model it is ported
from: 6 digits, **5-minute TTL**, max **5 wrong attempts** before the code is
dead, max **5 requests per hour** per phone. The response to `/otp/request` is
identical for a known and an unknown phone — never leak which numbers exist.
Drive the resend countdown from the returned `expires_at`, not a local timer.
A **502** means the code was not sent (the WhatsApp channel is down) — say so
plainly and offer retry; anything else and the customer waits for a code that
will never arrive.

> OTP is delivered over **WhatsApp**, not SMS (decision D4). Say "we sent a code
> to your WhatsApp" in the UI, or every support call will be "I didn't get the
> SMS". There is no SMS fallback in v1.

### 3.2 Devices are first-class

Every login and every OTP verify sends a `device` object, and the server keeps a
record of it:

```json
{"device_uid": "<stable uuid, generated once, stored in secure storage>",
 "platform": "android" | "ios",
 "app_version": "1.0.3+14",
 "push_token": "<FCM/APNs token, or null>"}
```

`device_uid` must be **generated once on first launch and never regenerated** —
not from `androidId`, not from a vendor id that changes on reinstall. Keep it in
`flutter_secure_storage`. It is the identity behind per-device revocation and
the server's record of your sync cursor.

Tokens carry it, which has one consequence you must handle: a `401` with
`code: "DEVICE_REVOKED"` means an administrator revoked this phone. That is not a
refresh-and-retry — it is **wipe the local database, drop the tokens, return to
login**. Treat it as the remote-wipe signal it is. Anything on that device is
diamond logistics data.

### 3.3 Token lifetimes, and why they are long

Access **24 h**, refresh **90 d** [FROZEN]. Longer than is fashionable, on
purpose: an operator out of coverage all morning has to be able to flush at
14:00 without a round trip they cannot make. Refresh on `401 code:"AUTH"` and
retry once; refresh proactively when the app foregrounds and the access token
has under an hour left.

**Never gate the UI on the token.** The app must open, show the cached worklist
and capture new events with an expired token and no radio. Local unlock is a
PIN/biometric over the encrypted database and is entirely a client concern — the
server never sees it. A token is needed only to talk.

### 3.4 `roles` is a hint, never a gate

The token and `/me` carry `roles: ["pickup_delivery", "job_work"]`. Use it to
decide which tabs to draw before the first network call. **The server enforces
group membership on every single route regardless.** If you find yourself relying
on `roles` for anything other than navigation, you have a bug waiting: a 403 can
still come back, and the UI must survive it.

---

## 4. The local database

Mirror the sync payloads, nothing more. Do not model `stock.picking`.

```
pickups(id PK, rev, payload JSON, ...projected columns for list queries)
deliveries(id PK, rev, payload JSON, stage, taken_by_me, ...)
jobwork(id PK, rev, payload JSON, ...)
receipts(id PK, rev, payload JSON, ...)          -- customer flavor
outbox(local_id PK, kind, record_id, idem_key, occurred_at, body JSON,
       upload_refs JSON, state, attempts, next_attempt_at, last_error JSON)
uploads(local_id PK, file_path, sha256, bytes, upload_id NULL, state)
sync(scope PK, cursor, last_pulled_at, server_offset_ms)
```

Two rules that will save a rewrite:

1. **Keep the whole server payload as JSON, and project only what your list
   queries actually filter or sort on** into real columns. The payloads will grow
   during the build; a schema migration per added field is a tax you do not need.
2. **The outbox is the only writer of user intent.** A screen never mutates
   `deliveries` directly. It appends to `outbox`, and the UI reads
   `cached record + pending intents for that record` as one derived view. This is
   the difference between a queue you can reason about and a cache you cannot.

**Encrypt it.** `sqflite_sqlcipher` or Drift over an encrypted VFS, key in
`flutter_secure_storage`. Also encrypt the staged image files, or keep them in
the app's private directory and delete them on ack. A lost operator phone should
not be a lost customer list.

### 4.1 Optimistic display

Show the record as the operator expects it, with the pending intent applied and
a visible "queued" marker — a small cloud/clock affordance, not a modal. When
the intent lands, the marker clears; the next `/sync/pull` brings the server's
version and replaces the local row. When the intent **conflicts**, §6 says what
to show.

Never show a plain success tick for a queued intent. The operator will assume
the office can see it.

---

## 5. Sync — the read side, and the outbox

### 5.1 `/sync/pull` [FROZEN]

```
GET /sync/pull?cursor=<opaque>&scopes=pickup,delivery,jobwork&limit=200
```

```json
{
  "cursor": 90251,
  "full_resync": false,
  "has_more": false,
  "next_after_scope": null,
  "next_after_id": null,
  "server_time": "2026-09-23T09:14:02",
  "scopes": {
    "pickup":   {"upserts": [/* PickupDoc */], "gone": [1234, 1235]},
    "delivery": {"upserts": [/* DeliveryDoc */], "gone": []},
    "jobwork":  {"upserts": [/* JobWorkDoc */], "gone": []}
  }
}
```

The worklists are nested under `scopes` so no scope name can collide with a
protocol field. Every requested scope is always present, even when empty, so the
client never has to reason about which page it is on.

- **`upserts` are whole documents**, not field diffs. Upsert by `id`; `rev`
  increases monotonically per record and is there for debugging and for dropping
  an out-of-order write.
- **`gone` ids must be deleted locally.** A record that left your scope — a
  delivery someone else took, a receipt archived, yesterday's completed work —
  arrives here. Skip this and the phone accumulates work that no longer exists.
- **`full_resync: true`** — wipe the scope's table and pull again from no cursor.
  Happens when the cursor is older than the server's 30-day change-log retention,
  or the user's roles changed. One code path; do not attempt a clever repair.
- **`has_more: true`** — call again immediately; one response is not the world.
  **The two cases page differently.** On an *incremental* pull the `cursor` has
  already been advanced to the last change handed over, so call again with it.
  On a *full resync*, call again with the **same** `cursor` you were given plus
  `after_scope: next_after_scope` and `after_id: next_after_id`; the server
  hands that cursor straight back, so a change committed while you page is not
  skipped — it carries a higher id and arrives on the next incremental pull.
  Store the cursor only once `has_more` is false.
- Send `If-None-Match` with the previous `ETag`; **304 means nothing changed**.
  Dio's default `validateStatus` throws on 304 — widen it, as the Investo client
  had to.

Store `cursor` **and the server's `ETag`** per scope set. The cursor is opaque:
treat it as a string, never sort or compare it.

### 5.2 `server_time` and the clock offset

`server_time` is there for one job: `server_offset_ms = server_time - device_now`,
stored in `sync`. Every captured `occurred_at` is stamped with the **corrected**
clock. A field phone with a wrong date is common, and §7.3 explains what the
server does to an out-of-range timestamp — this is how you avoid it.

If the offset exceeds ~10 minutes, also tell the operator their phone's clock is
wrong. Correcting silently is right; hiding it is not.

### 5.3 When to sync

| Trigger | Action |
|---|---|
| app foreground | pull, then flush |
| connectivity regained (`connectivity_plus`) | flush, then pull |
| push **data** message (`{"action": "pull"}`) | pull |
| pull-to-refresh | pull |
| while foregrounded | pull every 60 s, backed off to 5 min on 304s |
| app background | stop everything |

**Do not promise background sync on iOS.** iOS grants background execution at its
own discretion; a queued intent may sit until the operator next opens the app.
Android can use `workmanager` for a periodic flush and it mostly works. Design
the UX for "flushes when the app is open", and treat background success as a
bonus — the alternative is an operator who believes a delivery was reported and
went home.

### 5.4 Flushing the outbox [FROZEN semantics]

- **Serial per record, parallel across records.** Two intents on delivery 1234
  go in capture order; delivery 1235 is not blocked behind them. A conflict stops
  *that record's* chain only.
- **One `Idempotency-Key` per intent, generated at capture time** (a UUID v4
  stored in the outbox row) and reused on **every** retry, forever. This is what
  makes "the response was lost on a train" safe: the server replays the first
  answer instead of acting twice. Generating a fresh key on retry defeats the
  entire mechanism.
- Backoff on retryable failures: 2s, 4s, 8s … capped at 5 min, with jitter.
- Never drop an intent silently. A non-retryable failure becomes a visible item
  the operator can read and dismiss.

---

## 6. Conflicts — the part that decides whether this works

Two operators with two phones and one delivery is the normal case, not the edge
case. Every 4xx from an intent route comes back in one envelope [FROZEN]:

```json
{
  "code": "ALREADY_TAKEN",
  "message": "Taken for delivery by Rakesh at 09:12.",
  "retryable": false,
  "resync": [1234],
  "detail": {"picking_id": 1234, "user": "Rakesh", "at": "2026-09-23T09:12:00"}
}
```

`code` is the branch, `message` is safe to show verbatim, `retryable` is the only
thing that decides retry-vs-drop, `resync` lists ids to re-pull immediately.

| `code` | HTTP | retryable | what the client does |
|---|---|---|---|
| `ALREADY_DONE` | 200 | — | **success.** Idempotent replay; clear the outbox row |
| `VALIDATION` | 422 | no | drop, show `message` |
| `NOT_IN_SCOPE` | 404 | no | drop, re-pull the record |
| `ALREADY_TAKEN` / `ALREADY_CONFIRMED` | 409 | no | drop, re-pull, tell the operator **who won** |
| `STALE_INTENT` | 409 | no | drop, ask the operator to redo it |
| `CLOCK_SKEW` | 422 | no | drop, tell them the phone's clock is wrong |
| `LOCKED` / `SERIALIZATION` | 409 | **yes** | retry with backoff |
| `AUTH` | 401 | yes, once | refresh, retry once, then re-login |
| `DEVICE_REVOKED` | 401 | no | **wipe local data**, return to login |
| `SERVER` | 5xx | yes | retry with backoff |
| network timeout | — | yes | retry with backoff |

**A 409 is not an error dialog.** It is information: someone else got there
first. Show it on the record — "Rakesh took this at 09:12" — and move on. An
operator who sees a red failure modal for a normal race will stop trusting the
app by the end of the week.

`ALREADY_DONE` returning **200** is intentional: a retry that finds its own
earlier effect is a success, and the client must not special-case it into a
failure.

---

## 7. Intents — the write contract

Every one is `POST`, carries `Idempotency-Key` as a **header**, and carries
`occurred_at` + `device_uid` in the body.

### 7.1 Field intents [FROZEN]

| Intent | Route | Body (beyond the envelope) |
|---|---|---|
| confirm pickup | `POST /pickups/{id}/confirm` | `upload_ids?`, `note?` |
| cancel pickup | `POST /pickups/{id}/cancel` | `reason` |
| take deliveries | `POST /deliveries/take` | `ids: [int]` |
| release delivery | `POST /deliveries/{id}/release` | — |
| mark delivered | `POST /deliveries/{id}/deliver` | `receiver_name?`, `upload_ids?` **[PENDING Q7]** |
| timer | `POST /jobwork/{id}/timer` | `action: "start"\|"stop"`, `job_type_id?` |
| transfer department | `POST /jobwork/{id}/department` | `department_id` |
| finish job work | `POST /jobwork/{id}/finish` | — — **ONLINE ONLY** |

The envelope on every one:

```json
{"occurred_at": "2026-09-23T09:12:04+05:30", "device_uid": "...", "clock_offset_ms": -4200}
```

`take deliveries` is the one multi-record intent, because operators select a
handful of parcels at once. It answers per-id, so a partial result is normal:

```json
{"taken": [1234, 1236], "failed": [{"id": 1235, "code": "ALREADY_TAKEN", "detail": {...}}]}
```

### 7.2 `finish job work` needs the network [FROZEN — D2]

It runs Odoo's stock validation chain (reservations, backorders), whose outcome
cannot be predicted on the device. Queueing it would mean showing a success that
may not happen. **Disable the button when offline and say why** — "needs a
connection" — rather than accepting the tap into the outbox. Same for anything
billing-related.

Everything else in the table above works with the radio off.

### 7.3 Timestamps — read this twice

- **Responses** carry **naive UTC**, Odoo-style: `"2026-09-23T09:12:00"`, no `Z`,
  no offset. Parse as UTC. A phone reading it as local time will be hours out.
- **`occurred_at` you send** is the exception: ISO-8601 **with** an explicit
  offset or `Z`, stamped from the server-corrected clock (§5.2). The asymmetry is
  deliberate — for a captured event, an ambiguous timestamp is a silent data bug.
- The server **clamps**: more than 60 s in the future, or more than **72 h** old,
  and the intent is rejected `CLOCK_SKEW` / `STALE_INTENT`. An outbox row older
  than 72 h will never succeed; surface it to the operator before it expires,
  and consider warning at 48 h.
- Calendar dates (no time) stay date-only.

---

## 8. Images — jangad pages, delivery proof

### 8.1 Two-phase upload [FROZEN]

```
POST /uploads          multipart, one file  → {"upload_id": "...", "sha256": "..."}
POST /pickups/{id}/confirm  {..., "upload_ids": ["..."]}
```

Binary first, intent second. The intent is a few hundred bytes and lands on the
worst signal; the image retries on its own. Never inline a photo into an intent
body.

The returned `sha256` is your proof the bytes arrived intact — verify it against
the local file's digest, and only then delete the local copy. An unconsumed
upload is garbage-collected server-side after 7 days, so an upload whose intent
never flushed within a week must be re-uploaded: keep the local file until the
**intent** is acked, not just the upload.

### 8.2 Compression policy [FROZEN]

`flutter_image_compress`, and these numbers are not arbitrary:

- long edge **1600–2000 px**, JPEG quality **~80**, EXIF stripped except
  orientation;
- expect **250–400 KB** per page;
- server accepts `image/jpeg`, `image/png`, `image/webp`, max **8 MB**.

**Do not go below 1600 px.** A jangad is a handwritten slip and the digits on it
are the entire point of the photograph; losing a `3` that reads as an `8` costs
more than every byte this saves. Keep the original until the server acks.

Auto-crop, deskew or brighten only with the operator able to see and reject the
result — an unreadable "improved" scan is worse than a plain one.

### 8.3 Multi-page jangads **[PENDING Q6]**

The backend holds **one** image per receipt today. Multi-page means a model
change (§8.3 of the plan). Build the capture UI as a **list of pages** with an
`upload_ids` array from day one even if the answer is one — a list that happens
to hold one item costs nothing; retrofitting single-image screens into a gallery
costs a sprint.

---

## 9. Screens and payloads

Field-side documents, shapes as they will appear in `/sync/pull` [FROZEN unless
marked]. Names are the JSON keys; the Odoo field behind each is in the plan.

```jsonc
// PickupDoc — scope "pickup": incoming receipts awaiting pickup
{"id": 1234, "rev": 90210, "name": "WH/IN/00042",
 "customer": {"id": 77, "name": "Kiran Gems"} /* null if phone was unknown */,
 "contact_phone": "9876543210",
 "pickup_address": "12, Mahidharpura, Surat",
 "scheduled_date": "2026-09-23T04:30:00",
 "jangad_pages": 2,     // page count; the API layer builds the URLs as
                        // /api/field/v1/pickups/1234/jangad/<n>, bearer required
 "created_at": "2026-09-23T04:12:00"}

// DeliveryDoc — scope "delivery"
{"id": 5678, "rev": 90244, "name": "WH/OUT/00031",
 "customer": {...}, "contact_phone": "...", "address": "...",
 "stage": "awaiting" | "out" | "delivered",
 "taken_by": {"id": 9, "name": "Rakesh"} /* null when awaiting */,
 "out_since": "2026-09-23T05:40:00",
 "origin_receipt": {"id": 1234, "name": "WH/IN/00042"},
 "delivered_at": null,
 "received_by": null,          // proof of delivery, once captured
 "items": [ /* ItemLine */ ]}

// JobWorkDoc — scope "jobwork": receipts in progress
{"id": 1234, "rev": 90251, "name": "WH/IN/00042", "customer": {...},
 "scheduled_date": "...", "state": "assigned",
 "current_department": {"id": 3, "name": "Polishing"},
 "involved_departments": [{"id": 2, "name": "Cutting"}, ...],
 "total_hours": 4.25,
 "timer": {"running": true, "started_at": "..."},
 "jangad_pages": 1,
 "items": [ /* ItemLine */ ]}

// ItemLine
{"sr": 1, "product": "Round Brilliant", "size": "0.30-0.35",
 "pcs": 24.0, "carats": 7.85,
 "job_type": {"id": 3, "name": "Polishing"}, "remarks": "chip on 4"}
```

Customer-side:

```jsonc
// ReceiptDoc — GET /receipts
{"id": 1234, "rev": 90251, "name": "WH/IN/00042",
 "stage": "picked_up" | "in_job_work" | "out_for_delivery" | "delivered" | "invoiced",
 "submitted_at": "...", "picked_up_at": "...", "delivered_at": null,
 "items": [ /* ItemLine, once office data entry has happened */ ],
 "jangad_pages": ["/api/customer/v1/receipts/1234/jangad/0"]}

// AddressSuggestion — GET /addresses
{"id": 441, "label": "12, Mahidharpura, Surat", "is_default": true}
```

Note `items` is **empty until the office does data entry** — a customer who just
uploaded sees a receipt with a jangad photo and no lines, and that is correct.
Say "being processed", not "0 items".

`GET /receipts` is the reason a customer keeps the app: today, after uploading,
they see nothing at all. The stage timeline is the feature.

**Money and precision.** Pieces and carats are floats at the product's UoM
precision — display what the server sends, never round a carat figure yourself.
Invoice amounts are INR at 2 dp and come from the server formatted; the app does
no arithmetic on them.

---

## 10. Build order

Front-end stages that interlock with the backend's (§11 of the plan). Stages
A–C need no backend at all.

**A. The offline core, against the fake backend.** Encrypted local DB, outbox
with per-record serial flush and backoff, upload staging, sync engine with
cursor/`gone`/`full_resync`, the error taxonomy of §6 as a typed Dart sealed
class, the clock-offset logic. Tests: a queued intent survives a restart; a 409
stops one record's chain and not another's; `full_resync` wipes cleanly; the same
idempotency key is reused across retries. **This is the highest-risk code in the
project — do it first, and test it hardest.**

**B. Flavors and shell.** Two entry points, two app ids, flavor-conditional
routing and theme, shared `core`. Login/OTP screens against the fake backend.
Device uid in secure storage, local PIN/biometric gate.

**C. Field screens on cached data.** Pickup list/detail with confirm+cancel,
delivery list with take/release/deliver, job-work list with timer and department
transfer, all reading the local DB and writing the outbox. Queued markers.
Offline-disabled state for `finish job work`.

**D. Wire to the real server** — needs backend stages 2–4. Regenerate models
from `openapi.json`, swap the fake for Dio, and run the conflict cases against
two real phones on one delivery. Budget real time for this: the fake backend
will have been kinder than the real one.

**E. Customer flavor** — needs backend stages 5–6. OTP + register + GST, jangad
capture as a page list, submission through the same outbox, receipt timeline.

**F. Push** — needs backend stage 7. FCM/APNs token to `/devices`, data messages
trigger a pull, notification messages for new pickups and assigned deliveries.

Suggested packages, consistent with `investo_fe` so the two clients stay legible
to one team: `dio`, `flutter_riverpod`, `go_router`, `flutter_secure_storage`,
`flutter_image_compress`, `camera`/`image_picker`, `path_provider`, plus
`sqflite_sqlcipher` (or Drift), `connectivity_plus`, `uuid`,
`firebase_messaging`, and `workmanager` for Android-only background flush.

---

## 11. Pending questions — do not build on these

| # | Question | What it moves |
|---|---|---|
| Q5 | Do job-work staff see all assigned receipts, only their department, or only their own? Can they edit item lines (pcs/carats/size) on the phone? | the `jobwork` scope, and whether an item-edit intent exists at all |
| Q6 | Multi-page jangads? | §8.3 — build the page-list UI regardless |
| Q7 | Does `mark delivered` need a receiver name, a signature, a photo? | the deliver intent body and the local schema — **ask early**, it is cheap now |
| Q8 | Capture lat/lon on pickup/delivery? | an OS permission and a privacy decision, plus two body fields |
| Q9 | One active device per operator, or several? | whether a new login wipes the previous phone |
| Q10 | Does the `/jangad` PWA stay alongside the customer app? | whether customer features must be kept in step in two places |
| Q11 | How many devices and customers? | poll interval and page sizes |

## 12. Out of scope — do not build client flows that assume these

- **No office or billing app.** Billing review, invoicing and reference
  statements stay in the Odoo web client, and no endpoint exposes them.
- **No item data entry in v1** (subject to Q5). The operator apps are read-only
  on item lines, as the current web views are.
- **No SMS OTP.** WhatsApp only (D4).
- **No GST review queue.** GST is auto-accepted from the GSTIN and the customer
  can **skip** it (D3) — so the app must let a customer without a GSTIN reach the
  upload screen, and may re-prompt a skipped one on a later launch. Read
  `gst_state` (`present` / `skipped` / `missing`) from `/me`; do not treat a
  missing GSTIN as a blocker.
- **No in-app password reset or staff signup.**
- **No real-time push of record changes.** Sync is pull; push only ever says
  "pull now".

---

## 13. One-paragraph summary for the FE session

One Flutter codebase, two flavors — a field app for pickup/delivery/job-work
staff and a customer app for jangad upload — both offline-first against an Odoo
19 + FastAPI backend at `/api/field/v1` and `/api/customer/v1`. The architecture
*is* the offline layer: an encrypted local database mirroring server payloads, a
`/sync/pull` cursor protocol with `upserts`/`gone`/`full_resync`, and an outbox
of typed intents each carrying a capture-time `Idempotency-Key` and an
`occurred_at` stamped from a server-corrected clock, flushed serially per record.
Conflicts are normal, not errors: a 409 says who got there first, and the typed
`code`/`retryable` envelope decides retry-versus-drop without a human. Tokens are
long-lived (24 h / 90 d) and the UI never waits on them; a `DEVICE_REVOKED` 401
is a remote-wipe signal. Images go up in two phases at 1600–2000 px so a
handwritten jangad stays legible. Build the offline core against a fake backend
with a real change log and a forced-409 switch before you build a single screen.
