# Sudi custom addons — Odoo 19 → 20 port log (2026-09-28)

Workspace: `/opt/odoo20/custom/sudi_20` (v19 pristine copy kept at
`../sudi_20_v19_baseline` for diffing; no git commits yet by request).
Stack under test: odoo20 community + enterprise (final 20.0 build), venv python 3.12.

## Status — FINAL (2026-09-28)

- All 13 modules (11 sudi + vendored OCA `fastapi`, `endpoint_route_handler`)
  install together on a fresh v20 DB with zero errors/tracebacks.
- Test suites, all on the v20 stack:
  - open_whatsapp_connector: 166 tests — 0 port regressions (1 error is a
    pre-existing vendor bug failing identically on v19: owa.account create
    without notify_user_ids as superuser; 3 more only fail without crm).
  - diamond + sudi_sync + sudi_api + sudi_diamond_billing +
    sudi_diamond_dashboard: 295 tests — ALL PASS.
  - fastapi + endpoint_route_handler (vendored OCA): 46 tests — ALL PASS.
- Server smoke on :8199: login page + OTP form, /web/manifest.webmanifest,
  /pwa/icon/192, /odoo/offline, service worker, /api/field/v1/docs,
  /api/customer/v1/docs, /web/auth/otp/send all 200; authenticated /odoo
  loads and the backend asset bundle builds cleanly WITH the connector JS
  (import-resolution + OWL template inheritance validated server-side).
- Inspectable install kept in local DB `sudi20_test` (start odoo20 with
  addons path odoo20/addons,ent,custom/sudi_20).

## Mechanical passes (whole repo)

- `odoo-bin upgrade_code` scripts: ir.access conversion, ormcache→
  transaction.invalidate_ormcache, route type json→jsonrpc, sql-constraint,
  base64-in-xml, owl3-migration. CAUTION: the CLI also rewrites the CORE
  checkout it runs from (1,010 files were touched and reverted via git).
- `get_param`/`set_param` → typed `get_str/get_int/get_bool/set_*`
  (~140 sites). String-encoded booleans that deliberately parse leniently
  ('true'/'1'/'yes') stay strings via get_str.
- `odoo.http` package split: Stream/content_disposition→odoo.http.stream,
  Dispatcher→odoo.http.dispatcher, session fns→odoo.http.session,
  Request→odoo.http.requestlib, db_filter/db_list→odoo.http.router.
- `ir.attachment.datas` is REMOVED in 20 (writes silently ignored!): all 17
  sites now write `raw` (bytes) / read `.raw` (BinaryValue, `.content`,
  `.to_base64()`).
- Binary fields hold BinaryValue: `.content` to read bytes, assign
  `BinaryBytes(bytes)` or a base64 *str* (raw bytes raise TypeError).
- `read_group` (old signature) → `_read_group` tuples (owa_dashboard ×6).
- `res.partner.company_type` no longer exists; `is_company` is computed from
  VAT — the keys were dropped from partner create/write vals.
- `stock.move.product_uom` → `uom_id` (vals keys AND attribute reads).
- `uom.uom.rounding` is gone → `uom.is_zero()/compare()/round()` ("Product
  Unit" decimal precision).
- Manifest versions 20.0.x; `migrations/19.*` deleted; FontAwesome →
  Material `data-icon` across all backend templates/JS (fa renders blank in 20).

## Security (ir.model.access + ir.rule → ir.access)

- Converter output hand-reviewed. Fixed by hand:
  - Diamond: the converter dropped 10 ACL rows, all verified as correct
    fusions (the operator/internal/job-work stock grants live on in the
    domained rule rows with identical operation letters) or as redundant via
    group implications (job-type operator row, service-price stock-manager
    row). Nothing restored.
  - Connector: 3 admin CSV rows (campaign/contact list/member) dropped as
    "redundant" were restored — needed for own_team mode.
  - The 4 conditional (get_param-driven) rules lost their domains → rebuilt
    verbatim as ir.access records with `get_str` (the Python-expression
    domain pattern still works; eval context has `user`).
  - Toggle-able marketing rules+ACL pairs became single domained ir.access
    XML grants; set_values() now also flips the shared CSV rows
    (grants OR together, so shared rows must go inactive in own_team mode).
- ir.access semantics: group row = grant (union), no-group row = restriction
  (intersection, applies to admins too — admin exemptions live INSIDE the
  conditional domains, as before).

## Discuss layer (connector)

- Python: `_to_store`/`_thread_to_store`/`_to_store_defaults`/
  `add_records_fields` → `_store_message_fields`/`_store_thread_fields`/
  `_store_channel_fields` FieldList API; `message_post` keyword-only;
  `_notify_thread(message, **kwargs)` (msg_vals gone); `_broadcast(users)`;
  `add_members` → `_add_members(partners=recordset)`; `_action_unfollow`/
  `execute_command_leave` → `action_unfollow()` guard; Store auto-sends
  (no `.bus_send()`); member persona `._bus_channels()` loop.
- JS: `@mail/core/common/record` → `@mail/model/export`; chatter path →
  `web_portal_project`; DiscussAppCategory is GONE — the WhatsApp sidebar
  is now a messaging-menu TAB (MENU_TABS.WHATSAPP) with per-kind filters
  (Chats/Groups/Communities/Channels) + server-side tab/filter domains in
  `controllers/messaging_menu.py` (mirrors the official whatsapp module).
- OWL 3: every `static props` → `props = useProps(...)` (compat layer throws
  otherwise); hoot tests moved to `web.assets_unit_tests`.

## Diamond specifics

- `report_file` removed from ir.actions.report records.
- Invoice annexure t-esc → t-out (server QWeb silently drops t-esc in 20).
- Analytic search view: no `<group name="groupby">` — anchors moved to
  `field[@name='task_id']` / `filter[@name='group_date']`.
- Picking form: statusbar widget is `stock_picking_locked_statusbar` (anchor
  by field name); moves list wraps product_id in `<column>` (descendant xpath).
- timesheet_grid dropped the stop-timer confirmation wizard AND no longer
  mixes timer.mixin into account.analytic.line: diamond now restores
  `timer.mixin` on the line itself and banks measured time directly on stop
  (same as the phone flow). Widgets `timesheets_one2many`/`timesheet_uom_timer`
  are gone → plain one2many + `timesheet_uom`.
- Load-order bug (latent since 19): menu_views must load before
  billing_log_views on a fresh install.
- OTP login: both web_auth_otp_login and diamond's register flow now
  authenticate through a `whatsapp_otp` credential type
  (res.users._check_credentials override + odoo.http.session.authenticate),
  replacing the hand-rolled session writes. Login throttling, last-login
  logging and MFA now apply.

## v20 test harness notes

- `BaseCommon` runs each test as a limited `_test_user` built from
  `_test_user_groups`; `SudiJobWorkCase` declares the realistic sudi
  back-office group set (incl. reference-ledger, timesheet approver,
  partner manager) and the dashboard tests inherit it.
- v20 `TestStockCommon` also creates a journal-less "Stock Tests Company";
  `SudiJobWorkCase._sudi_use_main_company()` re-roots the suite in the main
  company (l10n_in CoA, diamond master data) — the company the v19 suite
  effectively ran in — and repoints the warehouse/picking-type fixtures.
- In-test `res.users` fixtures now use sudo() (the limited test user cannot
  create users).
- OCA pair: `_auth_method_user_endpoint` now takes the v20 `routing` arg;
  the routing ormcache uses `invalidate_routing_cache()` (transaction
  invalidation + in-place clear of the shared registry LRUs, restoring the
  v19 immediate cross-env contract for NEW transactions); the db-param
  tests were rewritten for the router-level patch; the cross-env test
  simulates a signaling refresh for an already-open transaction.
- JS: v20 channels are `DiscussChannel` records (no longer `Thread`) — all
  channel-level fields/getters moved to a single
  `discuss_channel_model_patch.js`; ImStatus is fed via the
  `mail.im_status_data` registry; the member-list WhatsApp section is
  re-anchored on the v20 category template.

## Known leftovers / TODOs

- Security hardening (out of scope by decision): OTP login open redirect,
  non-crypto random OTP, no rate limiting on /web/auth/otp/*;
  scs_hide_menu_user_wise cross-user menu-cache leak (exists in stock 19 & 20).
- Vendor test bug: owa.account default notify_user_ids empty for superuser.
- pwa_preview.js `PWAPreview` component references a template that never
  existed (dead code, pre-existing).
- res.users.settings `is_discuss_sidebar_category_whatsapp*` fields are kept
  but currently unused (v20 sidebar sections are tab filters; state no longer
  persists server-side).
- v19 per-account sidebar sections and online-first sort had no v20
  equivalent hook; dropped for now (channels sort by recent activity).
- Browser QA pass still pending (OWL runtime, tabs, composer actions,
  reports rendering with real data).

## Browser-QA fixes (2026-09-28, found via Playwright console capture)

The backend web client rendered a black screen after login. Two silent-undefined
import classes survived the server-side bundle build (the bundler does not error on
a named import that the target module does not export — it becomes `undefined` at
runtime):

1. `import { ActionManagerPlugin } from "@web/webclient/actions/action_plugin"` —
   the v20 export is **`ActionPlugin`**. `usePlugin(undefined)` threw in
   `OwaCallRingingNotifier.setup`, killing MainComponentsContainer → black screen.
   Renamed in 10 files (20 occurrences).
2. `import { useRef } from "@web/owl2/utils"` — OWL3 removed `useRef` and the compat
   utils do not shim it. The owl3-migration script had rewritten the templates'
   `t-ref="name"` to `t-custom-ref="name"` as a fix-me marker (missed in the port).
   Ported 5 components (chatter_patch, flow_designer, owa_dashboard, trend_chart,
   jangad_image_viewer) to the v20 pattern: `this.x = signal.ref()` +
   `t-ref="this.x"` + element read via `this.x()`. mail Thread's `scrollRef` prop
   is `t.signal(t.instanceOf(HTMLElement))` — `signal.ref()` satisfies it directly.
3. `t-model` — removed in OWL3; the owl3-migration script rewrites it to
   `t-custom-model` as a fix-me marker (v20 core registers only the `click` and
   `portal` custom directives, so the template dies lazily at first render with
   *Custom directive "model" is not defined*). Found on the two date inputs of
   OwaDashboard; ported to `t-att-value` + `t-on-change` writing the state field
   (`onDateFilterChange`) — same behavior, Refresh still drives the reload.
   Swept all 13 modules for remaining `t-custom-*` markers: zero left.
4. Chatter patch xpath anchor: v20 spells the Thread anchor attribute
   `thread="this.state.thread"` (OWL3 added the `this.` prefix); the v19-spelled
   `//Thread[@thread='state.thread']` no longer matches. Template-inheritance
   errors are lazy — they fire on the first render of that screen, not at bundle
   build — so these surfaced one screen at a time during QA. All connector
   template anchors audited against the v20 sources: clean.

Verified: Playwright (channel=chrome) full login flow → GST onboarding gate →
backend home renders the complete app grid with zero console errors. A static
named-export checker (scratchpad check_imports.py) sweeps for further cases; all
remaining hits are false positives (owl UMD bundle, relative re-exports).

5. Timesheets → Reporting (By Employee / By Project / By Task) crashed with
   *Unknown field sudi_picking_id* — **on v19 production as well** (verified by the
   side-by-side crawl: the same 3 screens error on prod code + prod data). Cause:
   diamond's shared timesheet search view adds sudi_picking_id / sudi_job_type_id,
   and the EE Reporting actions reuse that view on the timesheets.analysis.report
   SQL-view model, which lacked the columns. Fix (diamond/models/
   account_analytic_line.py): inherit timesheets.analysis.report, declare both m2o
   fields and extend _select() with `A.sudi_picking_id / A.sudi_job_type_id` —
   the hr_timesheet base model builds the view from _select, so the shared search
   view now works everywhere. v20 thereby fixes a screen prod never had working.
