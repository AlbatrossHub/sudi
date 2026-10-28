# Sudi mobile v2 — four personas on Odoo 20: plan and API contract

Status: **backend built and green** (2026-10-22) — diamond 20.0.1.8.0, sudi_sync and
sudi_api 20.0.1.1.0; 381 tests (272 existing + 109 new) pass on a fresh DB; `-u` clean on
`sudi20_local`. The regenerated `openapi.field.json` (30 operations) and
`openapi.customer.json` (17) are now the contract of record over §5 below; additive fields
(`code`, `has_more`, `date_from`/`date_to`, address `is_default`) and the stricter directory
phone rule are listed there. Two pre-existing defects fixed on the way: the error middleware
never rolled back (refused intents committed half-done work: stuck idempotency keys, consumed
uploads), and a plain stock user could not save item lines at all (billing upkeep now sudo).

Builds on `MOBILE_API_PLAN.md`
(stages 0–6, all shipped) and `FLUTTER_INTEGRATION_BRIEF.md`; everything not
restated here is unchanged, including the error envelope, idempotency, `occurred_at`
clamping, two-phase uploads and the delta pull.

Backend target: `sudi_20` (Odoo 20). Test DB: `sudi20_local` = the local v19 dev DB
migrated with the ETL (`tools/etl`, source `sudi_local_src19`), served by
`/opt/odoo20/sudi20_local.conf` on :8075.

---

## 1. Requirement → interpretation

The requirement text is the authority. Where it leaves room, the most literal reading
was taken; each is listed so it can be corrected cheaply.

| # | Requirement | Interpretation built |
|---|---|---|
| P1a | Pickup/delivery person: existing pickup and delivery operations | The shipped field-app flows (collect, cancel, take, hand back, deliver with proof). Unchanged. |
| P1b | Work offline the way Pahal does | Pahal's offline behaviour is adopted on top of sudi's sync engine (§6): trusted clock, resume where you left off, drafts that survive a kill, a saved copy of every screen, optimistic state, "recorded offline, synced at …". Everything syncs by itself when the network comes back. |
| P1c | Post a jangad on behalf of a customer; ability given by a Boolean on the user form | New tick **"Can post jangad for customers"** on the user form. The operator builds the **same request a customer would** (customer → services → jangad photo → address) and it lands in the pickup pool like any customer jangad; works offline. Collecting it is the normal "Confirm pickup", which the app offers right after posting. |
| P2 | Job workers: see approved, filled and ready job work | The job-work list = receipts the office has validated and filled and marked **READY** (`state = assigned`). Unchanged scope, richer document. |
| P2 | Log productivity | Time: the existing timer **plus manual time entries** and a **My work** summary (today / week, hours per receipt and per service). |
| P2 | Internal job work transfer | Department (= service/job type) transfer, now also recording every department the parcel passed through, and notifying job workers. |
| P2 | Notifications | In-app notifications (bell + list + phone notification while the app is open): new job work ready, transferred to a department, new pickup, delivery ready, jangad to validate. Push (FCM) plugs into the same notices later (needs the Firebase project). |
| P2 | Proper UX for the one2many table | A real item table on the phone: sticky Sr column, horizontal scroll for the rest, totals row, group by service, tap a row for the full line (§5.3). Same component everywhere lines appear. |
| P3 | KYC-approved customer can submit the jangad image (as of now) | Today's gate stays: WhatsApp-code sign-in + GST onboarding (GSTIN or skip). The app now actually runs the GST step (endpoints existed, no screen called them). |
| P3 | Dashboard: pick available services, one by one, quantity, remarks | **Home dashboard** with the active services. Tap a service → quantity + remarks sheet → added to the request list; repeat. |
| P3 | Phase 2: capture the jangad image | Step 2 of the same request: photograph the jangad pages, choose the pickup address, submit. One submission = services + pages. |
| P4 | Office admin: validate jangad, add entries from the table | New **Office** tab in the staff app: collected jangads awaiting validation → view pages + requested services → **add entries from the requested-services table** (one tap) or by hand → edit the item table → **Mark READY for job work**. Needs a signal (stock confirmation), draft kept on the phone. |

Out of scope (not asked): prices in the customer app, a customer receipt-status
history (descoped earlier by the product owner), item editing by job workers (Q5b).

---

## 2. Roles

`roles` in the JWT stays a UI hint; every route enforces server-side, recomputed per
request (`res.users._sudi_api_roles`).

| Role | Granted by | New? |
|---|---|---|
| `pickup_delivery` | `diamond.group_sudi_pickup_delivery_operator` | — |
| `job_work` | `diamond.group_sudi_job_work_user` | — |
| `office` | **`diamond.group_sudi_office_validator`** ("Office: Validate Jangads (app)"), implies `stock.group_stock_user` | new |
| `jangad_on_behalf` | **`res.users.sudi_can_post_jangad_on_behalf`** (Boolean, user form) on an internal user | new |
| `customer` | portal user | — |

`field_caller` and `field_groups_only` accept any of `pickup_delivery`, `job_work`,
`office`. `jangad_on_behalf` alone does not open the app (it is an extra ability).

---

## 3. Data model changes (`diamond`)

1. `res.users.sudi_can_post_jangad_on_behalf` — Boolean, "Can post jangad for
   customers", on the user form (internal users only, next to the Diamond
   notification ticks).
2. **`sudi.diamond.service.request`** — "Requested Service":
   `picking_id` (m2o stock.picking, required, cascade, index), `sequence`,
   `job_type_id` (m2o sudi.diamond.job.type, required, restrict, active only),
   `quantity` (Float ≥ 0, digits "Product Unit"), `unit` (computed from the job
   type's `invoice_basis`: `pcs` / `ct` / `""`), `remarks` (Char), `move_id`
   (m2o stock.move, set when an entry was made from it), `company_id` (related).
   `stock.picking.sudi_service_request_ids` (O2m). Office form: tab
   **"Requested Services"** and button **"Add entries from requested services"**.
3. `stock.picking.sudi_jangad_source` — Selection `customer_app` / `customer_web` /
   `on_behalf` / `office`; `stock.picking.sudi_posted_by_id` — m2o res.users
   (the staff member who posted on behalf). Shown on the receipt form.
4. `sudi_create_public_jangad_receipt(..., services=None, source=None, posted_by=None)`
   — creates the request lines in the same transaction; existing callers unchanged.
5. `_sudi_add_lines_from_requests()` — one `stock.move` per request line without a
   `move_id`: product `diamond.product_customer_diamond_parcel`, job type, `sudi_pcs_qty`
   = quantity when the basis is pieces (or manual), `sudi_carats` = quantity when the
   basis is carats, `sudi_remarks` = remarks. Allowed in `draft` (collected) only.
6. `_sudi_transfer_department(job_type)` also adds the department to
   `sudi_involved_department_ids` and raises a notice (§4).
7. `_sudi_log_time(job_type, hours, description, date)` — manual timesheet line on a
   job-work receipt for `env.user` (same constraints as the timer: job type
   required, employee resolved from the user).
8. `stock.move.write()` keeps `product_uom_qty` in step with `sudi_pcs_qty` (today
   only create/onchange do — an API write would otherwise drift).

## 4. Notices (`sudi_sync`)

**`sudi.app.notice`**: `user_id`, `kind`, `title`, `body`, `picking_id`, `scope`,
`read_at`, `create_date`. Raised (sudo, after commit-safe hooks) on:

| kind | when | to |
|---|---|---|
| `pickup_new` | a jangad receipt is created (any source) | pickup/delivery operators |
| `office_validate` | a pickup is confirmed (collected) | office validators |
| `jobwork_ready` | a receipt reaches `assigned` | job workers |
| `department_transfer` | department transfer | job workers |
| `delivery_ready` | a job-work delivery order is created | pickup/delivery operators |

The actor never gets a notice about their own action. Daily cron purges notices
older than 30 days.

## 5. API contract

### 5.1 Shared (both apps, no auth)

| Method | Path | Answer |
|---|---|---|
| GET | `/time` | `{"server_time": "<ISO UTC>"}` — the trusted-clock anchor (§6.1) |

### 5.2 Field API `/api/field/v1`

| Method | Path | Role | Offline | Purpose |
|---|---|---|---|---|
| GET | `/catalog/job-types` | any field | cached | `[{id, name, unit, sequence}]` active services/departments; ETag |
| GET | `/customers` | `jangad_on_behalf` | cached | directory for posting on behalf: `[{id, name, phone, gst_state, addresses: [{id, label, address}]}]`; ETag; ≤ 5000 |
| POST | `/jangads` | `jangad_on_behalf` | **yes** | post a jangad on behalf (body below) → `{picking_id, reference, pickup: <pickup doc>}` |
| POST | `/jobwork/{id}/timesheets` | `job_work` | **yes** | manual time log `{job_type_id, hours, description?, date?, occurred_at}` → `{entry, jobwork: <doc>}` |
| GET | `/me/productivity?date_from&date_to` | `job_work` | cached | `{total_hours, by_day: [{date, hours}], by_job_type: [{job_type, hours}], entries: [{id, date, picking: {id, name}, job_type, hours, description, running}]}` (default: this week) |
| GET | `/notices?after=<id>&limit=` | any field | cached | `{notices: [{id, kind, title, body, picking_id, scope, created_at, read}], last_id, unread}` |
| POST | `/notices/read` | any field | yes | `{ids: [...]}` or `{up_to: <id>}` |
| PUT | `/receipts/{id}/lines` | `office` | no (draft kept) | replace the item table `{lines: [{move_id?, size, pcs, carats, job_type_id, remarks, request_id?}]}` → office doc |
| POST | `/receipts/{id}/lines/from-requests` | `office` | no | add entries from the requested-services table → office doc |
| POST | `/receipts/{id}/ready` | `office` | no | Mark READY for job work (`action_confirm`) after checks → `{state, reference}`; leaves the office scope |

`POST /jangads` body:

```json
{
  "occurred_at": "2026-10-22T10:05:00+05:30",
  "customer": {"partner_id": 412}            // or {"phone": "9820012345", "name": "Shah Gems"}
  , "pickup_address_id": 418,                 // or "manual_pickup_address": "..."
  "services": [{"job_type_id": 3, "quantity": 120, "remarks": "big stones first"}],
  "upload_ids": ["…"],                         // ≥ 1 staged page
  "note": "customer will hand over at the gate"
}
```

The receipt is created by `sudi_create_public_jangad_receipt` with
`source="on_behalf"`, `posted_by=<operator>`; provenance in the chatter names both
the operator and the capture time. A phone that matches a staff account is
refused, exactly as in customer registration.

`/sync/pull` gains:

- scope **`office`** (role `office`): diamond incoming, `state = draft`,
  `sudi_pickup_datetime` set — collected, awaiting validation. Document = pickup
  document + `items` (with `move_id`) + `services` + `jangad_source` + `posted_by`.
- `services` (requested) on the pickup and job-work documents; job-work document
  adds `involved_departments` and `my_hours`.

### 5.3 Customer API `/api/customer/v1`

| Method | Path | Purpose |
|---|---|---|
| GET | `/services` | `[{id, name, unit, description}]` active services (the dashboard) |
| POST | `/jangad` | unchanged shape **plus** optional `services: [{job_type_id, quantity, remarks}]`; `source = customer_app` |
| POST | `/gst`, `/gst/skip` | existing; now wired in the app as the onboarding step |

## 6. Client (sudi_fe) — Pahal-parity offline

1. **Trusted clock**: `GET /time` pinned to monotonic uptime (Pahal `TrustedClock`);
   `occurred_at` from it; source flag in the envelope.
2. **Resume**: last route + tab + open form restored on cold start (Pahal `ResumeStore`).
3. **Drafts survive a kill**: confirm-pickup, deliver, cancel, jangad (customer and on
   behalf), office line edits, manual time log.
4. **Saved copy of every GET** (`/auth/me`, addresses, job types, customers,
   productivity, notices, jangad page images) — the screen opens from it, then refreshes.
5. **Optimistic projection**: a queued confirm moves the card to "Collected", a queued
   timer shows running, a queued on-behalf jangad appears in Pickups marked "on this phone".
6. **One chain per record** (fixes `chainKey` per verb), on-behalf jangad + its confirm
   in one chain (the confirm uses the id the post returns).
7. Reachability = the server answered, not the radio; flush when it comes back.
8. A row left `sending` by a killed app is retried at boot.

## 7. Phasing

| Step | Content |
|---|---|
| 4 | Design preview (all new screens, both flavors) |
| 5 | Backend §3–§5 + HTTP/ORM tests on Odoo 20, run against a clone of `sudi20_local` |
| 6 | sudi_fe: §6 + the four personas wired to §5; Flutter tests; APKs |
