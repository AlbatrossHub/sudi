# ETL mapping rules — sudi_src19 (Odoo 19) → Odoo 20 (P1 deliverable, 2026-09-28)

Companion to the generated `docs/etl_mapping.md` (raw diff of `sudi_src19` vs `sudi20_ref`).
Every diff finding gets a rule here; the engine implements this file. Vocabulary (marut
convention): **KEEP / RENAME / TRANSFORM / DERIVE / DROP / NEW / DECIDE**.

Lanes: **ORM** = created through the Odoo 20 ORM (master data, few rows, business checks
wanted). **SQL** = `INSERT..SELECT` through the FDW with id offset 10,000,000 (history that
must arrive byte-identical: chatter, attachments, WhatsApp chats, job-work operations).

## 0. Ground rules

- Module-owned data (`res_country`, `payment_provider`, `account_report*`,
  `mail_activity_type` chains, `resource_calendar_attendance`, report actions, module
  ACL/security, l10n_in sections/templates) is **never copied row-wise**: the fresh v20
  modules ship their own. Only rows *referenced* by business data are id-mapped by
  natural key (name/code/xmlid) — the F-section catalogue of `etl_mapping.md`.
- The source is prod truth at diamond **1.2.3**; the target code is **1.7.0**-based.
  The ETL therefore also applies diamond's own pending 1.2.3→1.7.0 migrations
  (`m2m billing links`, notify-param→user-flag, group carryovers) — taken from
  `/opt/odoo19/custom/sudi/diamond/migrations/19.0.1.3.0 + 1.4.0` (1.1.0–1.2.3 already
  ran on prod).
- **Module set**: 147 installed − 5 merged (base_vat, account_add_gln → account;
  hr_org_chart/homeworking/hourly_cost → hr) + 1 added (`mail_tracking`, because v20
  split tracked values out of `mail` and prod has 6,984 of them) = **143**.

## A. Tables absent on 20

| 19 table (rows) | Rule |
|---|---|
| `mail_tracking_value` (6,984) | **KEEP via `mail_tracking` module** (added to target set) + **TRANSFORM**: v20 chatter renders tracked values from the message body, so bodies of the 4,217 empty tracking messages are rendered in SQL (marut recipe) and `message_type` set to `tracking`. |
| `ir_model_access` (990) / `ir_rule` (295) / `rule_group_rel` | **DROP** — zero DB-created rows; v20 modules ship `ir.access`. |
| `payment_method_payment_provider_rel` (306) | **DROP** — module data; all providers disabled on prod. |
| `l10n_in_section_alert` (87) | **DROP** — v20 l10n_in ships its own withholding structure; only 3 taxes are referenced by business data (none TDS). |
| `hr_timesheet_stop_timer_confirmation_wizard` (13) | **DROP** — transient wizard rows. |
| `hr_contract_type` (12) | **DROP** — model removed; `hr_version.contract_type_id` empty on prod. |
| `mail_message_res_partner_starred_rel` (1) | **DROP** — starring removed on 20 (1 row, noted). |
| `res_bank` (1) | **DROP** — no `res_partner_bank` rows reference anything (0 bank accounts). |
| `whatsapp_template` (1) | **DROP** — orphan table of an uninstalled trial module (no ir_model row). |

## B. Column rules (data-bearing drops from `etl_mapping.md` §B)

- `stock_move.product_uom` → **RENAME** `uom_id`; `stock_move_line.product_uom_id` →
  **RENAME** `uom_id`; `stock_move.scrap_id` → **TRANSFORM** `is_scrap = scrap_id IS NOT
  NULL` (all NULL on prod); `quantity_product_uom` **NEW** = `quantity` (single-uom data).
- `account_move.checked` (all False) → **TRANSFORM** `review_state='no_review'` (v20
  default; required).
- `account_move_line.sudi_stock_move_id` / `sudi_billing_line_id` +
  `sudi_diamond_billing_line.invoice_line_id` → **DERIVE** into
  `sudi_billing_line_account_move_line_rel(billing_line_id, invoice_line_id)` — diamond's
  own 19.0.1.3.0 migration, applied by the ETL; old m2o columns then DROP.
- `account_move_line.deductible_amount` (100.0 everywhere) → **DROP** (fully deductible
  is the v20 default).
- `discuss_channel_member.fetched_message_id`, `custom_channel_name` → **DROP**
  (ephemeral read-state; empty).
- `res_users_settings.*` dropped columns (voice_active_duration, sidebar category
  flags, push_to_talk_key) → **DROP** — v20 redesigned these prefs; the settings rows
  themselves are copied for the surviving columns.
- `website.social_*` → **TRANSFORM**: copied onto `res_company.social_*` (v20 moved
  them); `website_menu.url` → **RENAME** `manual_url`; `website_page.url` char →
  **TRANSFORM** jsonb `{"en_US": url}`.
- `res_company` dropped columns (layout_background, income/expense_account_id,
  inventory_valuation, cost_method, l10n_in_withholding_account_id,
  l10n_in_is_gst_registered) → **DROP**, logged in the cutover checklist as
  "re-check Settings→Accounting after go-live" (v20 moved these knobs; 62 moves make
  re-configuration trivial and safe).
- `product_template.sale_delay` (all 0), `tracking='none'` → **DROP** / **TRANSFORM**
  tracking `none`→NULL.
- `account_account.create_asset`/`multiple_assets_per_line` (all no/False),
  `l10n_in_tds_tcs_section_id`, `account_tax.l10n_in_section_id` → **DROP** (v20
  withholding module owns this; nothing TDS-active in business data).
- `mail_activity.previous_activity_type_id` (1 row) → **DROP**.
- Everything in `etl_mapping.md` §B.2 (empty on 19) → **DROP** by definition.

## C. Selection keys

- `account.tax.l10n_in_tax_type`: `tds_purchase`/`tds_sale` → `tds` (only if such a tax
  is ever cloned; none is referenced by business data).
- `product.template.tracking`: `none` → NULL.
- `resource.calendar.attendance.day_period` `lunch` → module data, not copied.

## D. New NOT NULL columns supplied by the mapping

`stock_move.uom_id`/`stock_move_line.uom_id` (rename), `stock_picking.
sudi_settlement_mode` = `'invoice'` (diamond 1.7.0 default), `account_move.review_state`
= `'no_review'`, others belong to module data or ORM-created rows (defaults apply).

## E. Reference data mapped by natural key (from §F catalogue)

users by login (31 created, phone-number logins kept); partners/products/channels by
idmap; accounts by `code_store->>'1'` (unmatched codes cloned with their 19 type); taxes
by (name, type_tax_use, amount) — unmatched *referenced* taxes cloned, unreferenced ones
NOT migrated; journals by code (8/8); payment terms, fiscal positions, industries by
name; warehouse: the fresh v20 warehouse takes prod's code `HO`/name so location
complete_names match; locations by (name, usage, parent); picking types by (name,
warehouse); uom by name (`Ton` created as reference unit); subtypes/activity
types/groups by xmlid (membership of `stock.group_reception_report` dropped — group gone);
`en_IN` language activated; analytic plan `Project` + 2 analytic accounts created.

## F. DECIDEs taken autonomously (flagged for the user's review during testing)

1. **mail_tracking added** to the module set (chatter fidelity) — remove later if unwanted.
2. Starred flag of 1 message dropped.
3. `res_company` accounting/valuation knobs re-entered at go-live instead of copied
   (cutover checklist item).
4. Unreferenced legacy taxes (mostly the 424-row template import of 19) are **not**
   migrated — v20's own 279-tax template replaces them; only referenced taxes map/clone.
5. The 2 DB-created "Send WhatsApp" server actions are copied verbatim — their one-line
   code calls `owa.composer._action_open_composer`, unchanged in the ported connector.

## G. Prod pull 2026-10-26 — full-table sweep (every table both sides, row counts)

The September rules were written against a source where these tables were empty or small;
the fresh prod pull (24,618 messages, 5,225 attachments) was swept table by table and every
gap got a rule. All are permanent mapping steps, proven by an unattended mkdb + run + verify.

| Finding (source rows) | Rule |
|---|---|
| Customer phone-number logins (14 portal users) not migrated — the users step took internal users only | **KEEP** `res.users.portal`: created ON the partner `res.partner.users` already migrated (no twin contact), portal group by xmlid, password hashes copied. |
| `owa_message` (1,511: delivery ticks, Meta message ids, reactions) | **KEEP** SQL lane after chatter, `mail_message_id` +OFF, quick replies/reports by xmlid. |
| `owa_notification_rule` (21, all switched off by a user on prod) | **KEEP** SQL lane; model/field by name, quick reply/report by xmlid. The connector's install hook seeds none on a fresh 20 (it installs before account/stock/project). |
| Demo chatbots / steps / options / auto-replies listed **twice** (install seed + copy) | **TRANSFORM**: prod's copies take over the module xmlids, the seeded twins are deleted (`parity.purge_install_artifacts`). |
| `owa_inbound_dedupe` (2,805), `owa_group_session` (1) | **KEEP** — without the dedupe keys a webhook replayed after the switch-over would land a customer message twice. |
| `pwa_config` showed the module default name and colours | **TRANSFORM**: prod's row written onto the seeded row (same xmlid); its 3 icons follow as attachments (`settings` phase, before chatter). |
| `iap_account` (2: SMS credits, partner autocomplete) | **KEEP** + dry-run guard rewrites the token outside `ETL_PRODUCTION=1`. |
| `mail_activity` (6; 5 on pickings deleted on prod) | **KEEP** activities of migrated records (1). |
| `mail_mail_res_partner_rel` (517), `sms_tracker` (184), `res_users_log` (16), `timer_timer` (3), `discuss_channel_res_groups_rel` (2) | **KEEP** (rows whose parent migrated: 510 / 184 / 16 / 2 / 2). |
| `website_visitor` (1,427), `website_track` (2,664) | **KEEP**; a track on a module page that was not copied points at 20's page with the same URL. |
| 89 PDF preview thumbnails (attachments of attachments) | **KEEP** `ir.attachment.thumbnails` after the attachment step (their parent ids exist only then). |
| Migration-day chatter on migrated records (142: "0.00 → 1,540.00 (Untaxed Amount)" on every invoice, second "created" notes) | **DROP** `parity.etl_chatter`: chatter of migrated records is prod's alone. |
| Digest: 20 subscribes every user daily; prod mails monthly to 1 user | **TRANSFORM** `digest.subscriptions` by xmlid. |
| Default contact language en_US (prod: en_IN) | **TRANSFORM** `ir.default.partner_lang`. |
| diamond 1.7.0 stored computes empty on every migrated move/picking (billing line/price/qty/amount/state, billing status, delivery stage — prod runs 1.2.3, which has no such columns; the field app's delivery list showed 0 of 10 open deliveries on the phone) | **DERIVE** `diamond.recompute`: every stored compute a sudi module defines whose column the source lacks is computed for the SQL-lane rows (moves before pickings), as the module upgrade would. |
| Sequences behind the +OFF ids (new `owa.message` rows would sort below prod's) | **TRANSFORM** `parity.id_sequences`: every table with migrated rows continues after them. |
| `res_device_log`, `ir_cron_progress`, `bus_bus`, `mail_push_device`, `discuss_call_history`, `discuss_voice_metadata`, `res_users_settings_volumes`, `digest_tip_res_users_rel`, `base_import_mapping`, `onboarding_progress_step` | **DROP** — runtime/session state (browser push subscriptions are tied to the old server's keys and re-register). |
| `product_value` (19 automatic "None → 0.00" cost entries), personal to-do stages, payment-method/menu/module images, taxes/report actions/calendars/barcode rules | **DROP** — module data 20 ships itself (rule §0). |
