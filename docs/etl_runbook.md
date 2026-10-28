# Sudi 19 → 20 data migration — ETL workbook

This is a *workbook*, not just documentation: **you run every command yourself**, we
review the output together after each chapter, and only then move to the next one. By
the end you will have executed a complete Odoo major-version data migration and be able
to explain every step of it.

The tooling is the house ETL harness proven on the marut v18→20 project
(`/home/om1203/opt/odoo20/custom/marut_20/tools/etl`), adapted here for sudi 19→20.
The custom-addon code migration is already done and tested (see
`docs/port_log_19_to_20.md`); this project moves the **data**.

## The big picture

```
  prod (VPS, Odoo 19)                      this machine
  ──────────────────                       ────────────────────────────────────────────
  pg_dump + filestore  ──────────────►  sudi_src19  (SOURCE: restored raw dump,
                                            │         neutralized, read-only for us)
                                            │ postgres_fdw ("src19." schema visible
                                            ▼            inside the target DB)
                                        sudi20_etl_dry1 (TARGET: fresh Odoo 20 DB with
                                            │            all 142 modules pre-installed)
                                            │  run.py: ORM lane + SQL lane
                                            ▼
                                        verify.py + website compare  ──►  dump + filestore
                                                                          (portable artifact
                                                                           for the new VPS)
```

Why a **fresh target** instead of upgrading the old DB in place? The target is born with
correct Odoo 20 structures (security, views, chart of accounts, module data) and we move
only *our* data into it. Every transformation is an explicit script we wrote and can
re-run — that is the "ETL" (Extract from src19 / Transform in the mapping rules /
Load into the target).

### Vocabulary you will use daily

| Term | Meaning |
|---|---|
| **source / `sudi_src19`** | the restored raw prod dump. We only ever READ it. |
| **target / `sudi20_etl_*`** | a fresh v20 DB the ETL loads into. Disposable — wrecking one costs `mkdb.sh` + a re-run. |
| **FDW / `src19.` schema** | PostgreSQL's *foreign data wrapper*: inside the target DB, `SELECT * FROM src19.res_partner` reads the source live. No CSV files anywhere. |
| **ORM lane** | records created through the Odoo 20 ORM (`OrmStep`), so v20 business logic validates them. Used for live business data. |
| **SQL lane** | rows bulk-copied with SQL (`SqlStep`/`TableCopy`), keeping `old_id + 10,000,000` as the new id. Used for history that must arrive byte-identical: chatter, attachments, WhatsApp conversations. |
| **id map / `etl_idmap`** | table inside the target recording `model, old_id → new_id` for every migrated record. It makes every step **resumable** (already-mapped rows are skipped) and lets later steps re-point references. |
| **refusal** | a row the engine could not load, recorded in `etl_refusal` with the reason instead of crashing the run. Every refusal must end up explained (fixed, or documented as intentional). |
| **dry-run guard** | after every phase the engine forcibly disables crons, outgoing mail and the WhatsApp sidecar credentials on the target. A test DB must never be able to message a customer. |

### House rules

1. **Port 8069 is the live v19 server. Nothing in this project touches it.** Our ports:
   8119 (v19 baseline), 8121–8123 (ETL/v20). Check with `ss -ltn | grep -E '81(1|2)[0-9]'`.
2. **Never hand-fix a target database.** Found a problem? The fix goes into a mapping
   rule/script, then re-run. The final cutover replays everything unattended — a manual
   fix would be silently missing that day.
3. Everything is **re-runnable**: `restore_src.sh` rebuilds the source, `mkdb.sh`
   rebuilds a target, `run.py` resumes where it stopped.

### Campaign map

| Chapter | You will | Gate to pass |
|---|---|---|
| **0. Base camp** (this one) | restore + neutralize the source, stage the filestore, capture the v19 truth (checksums, pages, screenshots), smoke-test the engine | all captures on disk; FDW smoke green |
| 1. Mapping workbench | build the v20 reference DB, generate the schema diff, classify EVERY difference into rules | 100 % of the diff classified; no open DECIDEs |
| 2. Mapping modules | turn the rules into `mapping/*.py` phases | `run.py --list` shows the plan; base phase loads |
| 3. First full run | load everything into `sudi20_etl_dry1`, work the refusal list to zero | `verify.py` all green |
| 4. Website precision | carry the 60 custom views + pages, compare v19/v20 renders side by side | every page verdict accepted |
| 5. Functional QA | click through the migrated system (WhatsApp chats, reports, OTP login…) | your sign-off |
| 6. Package + drill | export dump+filestore, restore it cold, re-verify | the *artifact* passes, not just the DB |
| 7. Cutover rehearsal | fresh prod pull → one command → finished artifact, timed | unattended green run = downtime budget |

---

## Chapter 0 — Base camp: source, baseline, engine smoke

**Goal.** A neutralized copy of production (`sudi_src19`) that the whole project reads
from; the "v19 truth" captured to files (numbers + rendered pages) so chapter 3/4 can
prove nothing was lost; proof that the engine talks to both databases.

**Concepts.** Two ideas carry this chapter:

*Neutralization.* A copy of production is dangerous: its cron jobs would run, its mail
server would send, its WhatsApp account would reconnect to the live Node sidecar and
start acking real customer chats. `restore_src.sh` therefore immediately runs
`neutralize_src.sql` — crons off, mail off, sidecar credentials replaced by
`dry-run-disabled`. The engine re-applies the same discipline to every *target* after
every phase (the dry-run guard in `core.py`). Read `tools/etl/neutralize_src.sql` now —
it is short, and you should be able to say why each UPDATE is there.

*Baseline capture.* After the migration we must answer "is everything still there?"
with numbers, not feelings. Anything not measured **before** cannot be checked
**after** — so we capture table counts (every table, not a curated list), accounting
sums to the cent, stock on hand per location, chatter counts per model, and a rendered
copy + screenshot of every website page, all from the untouched source.

**Commands.** (terminal A, as `om1203`)

```bash
cd /opt/odoo20/custom/sudi_20

# 0.1  restore the raw prod dump as sudi_src19, neutralize, stage the filestore
tools/etl/restore_src.sh

# 0.2  derive the frozen v20 module list from the source (147 installed − 5 merged = 142)
/opt/odoo20/odoo20-venv/bin/python tools/etl/gen_target_modules.py

# 0.3  capture the v19 truth into /opt/odoo20/etl/baseline/checksums_src.json
ETL_SRC=sudi_src19 /opt/odoo20/odoo20-venv/bin/python tools/etl/baseline_checksums.py
```

Now the website baseline. Start the read-only v19 server in **terminal B** and leave it
running (it is locked to `sudi_src19` and cannot see any other database):

```bash
/opt/odoo19/odoo19-venv/bin/python /opt/odoo19/odoo19/odoo-bin \
    -c /opt/odoo20/custom/sudi_20/tools/etl/conf/odoo19-baseline.conf
```

Back in terminal A:

```bash
# 0.4  fetch every website page (HTML + screenshot) from the baseline server
/opt/odoo20/odoo20-venv/bin/python tools/etl/fetch_pages.py --shots

# have a look at what was captured — these screenshots are the "before" pictures
ls /opt/odoo20/etl/baseline/pages19/
```

Browse http://localhost:8119 yourself too — this is your reference system for the whole
project (login works with your prod credentials; it cannot send anything). Stop it with
Ctrl+C when done, or leave it running.

Finally the engine smoke test — a 1-minute base-only target, connect the engine, look
through the fence at the source, throw the target away:

```bash
# 0.5  engine smoke
tools/etl/mkdb.sh sudi20_etl_smoke --base-only
/opt/odoo20/odoo20-venv/bin/python -m tools.etl.run --db sudi20_etl_smoke --setup-only
psql -d sudi20_etl_smoke -c '\dt etl_*' \
     -c 'SELECT count(*) AS partners_seen_through_fdw FROM src19.res_partner'
dropdb sudi20_etl_smoke
```

**Expected output.**
- 0.1 ends with a summary block: `installed modules: 147`, `users: 34`,
  `mail_message: 17533`, `active crons: 0`, `active mail srv: 0`, `owa enabled: 0`,
  and ~4,600 filestore files.
- 0.2 prints the 5 merged modules (`base_vat → account`, …) and
  `wrote …/target_modules_prod.txt: 142 target modules`.
- 0.3 prints `posted debit/credit: ('6142.50', '6142.50')`, 60 custom views, 10 pages.
- 0.4: every page `ok` — including `404` for `/install-sdppl-to-iphone`, which is
  *unpublished* on prod; staying hidden is part of the parity contract.
- 0.5: `etl_idmap`/`etl_refusal` tables exist and the FDW count says `494`.

**Checkpoint questions** (answer from the files/output, not from memory):
1. Which three channels out of the source could have reached real customers, and which
   lines of `neutralize_src.sql` close each one?
2. `SELECT count(*) FROM src19.res_partner` ran inside `sudi20_etl_smoke` — which
   PostgreSQL feature made that possible, and where was it set up? (hint: `core.py`,
   `setup_fdw`)
3. Why does `gen_target_modules.py` read the module list from the source DB instead of
   shipping a hand-written list?
4. The id offset is 10,000,000. What breaks if two different steps both load
   `mail_message` rows without a shared id map?

**Failure drill** (do it — breaking things safely is the fastest way to learn):
- Run `tools/etl/restore_src.sh` a second time. Watch it drop and rebuild the source
  identically — this is exactly what the cutover replay does with a fresh dump.
- Run step 0.5's `--setup-only` twice. The second run prints nothing about creating the
  schema: `setup_fdw` saw `src19` already exists and returned. Idempotency everywhere.

**Gate.** All five outputs match, checkpoint answers reviewed together, then we open
chapter 1 (the mapping workbench — the most educational part of the whole project).

### Interlude — running v19 and v20 side by side

Any time during the project you can keep both systems open in two browser tabs:

| | command (own terminal each) | URL | login |
|---|---|---|---|
| **v19, real prod data** | `/opt/odoo19/odoo19-venv/bin/python /opt/odoo19/odoo19/odoo-bin -c /opt/odoo20/custom/sudi_20/tools/etl/conf/odoo19-baseline.conf` | http://localhost:8119 | your prod credentials |
| **v20** | `/opt/odoo20/odoo20-venv/bin/python /opt/odoo20/odoo20/odoo-bin -c /opt/odoo20/custom/sudi_20/tools/etl/conf/odoo20-qa.conf` | http://localhost:8123 | `admin` / `admin` |

The v19 server runs a **snapshot of the code prod actually runs**
(`/opt/odoo20/etl/prod_addons/sudi`, rsync'd from the VPS) — NOT the local dev
checkout `/opt/odoo19/custom/sudi`, which is several PRs ahead of prod (diamond
1.7.0 vs deployed 1.2.3) and crashes against the prod database because it expects
columns that were never installed there. Lesson: a database only works with the
code generation that built its schema.

The v19 side needs `sudi_src19` (command 0.1). The v20 side serves `sudi20_test`
(fresh install, **no prod data yet**) until chapter 3 produces `sudi20_etl_dry1` —
then start it with `-d sudi20_etl_dry1` appended and browse your real data on 20.
Both servers are cron-less and mail-less; neither can touch port 8069 or send
anything. Stop either with Ctrl+C.

---

*Chapters 1–7 are written as we reach them, each after the previous gate passes.*

---

## Chapters 1–4 — executed 2026-09-28 (mode change: Claude drove, you test)

You asked for the chapters to be built and run end-to-end; here is the record. Every
command below is re-runnable by you; every fix lives in `tools/etl/mapping/*.py`, so a
full replay (`mkdb.sh` + `run.py`) reproduces everything from scratch.

**Chapter 1 (mapping workbench).** `sudi20_ref` built (143 modules, l10n_in chart, 0
errors, ~2 min). `workbench_columns.py` generated `docs/etl_mapping.md`: 11 tables and
109 columns gone on 20 — a quarter of the marut 18→20 diff. Every finding is classified
in `docs/etl_mapping_rules.md`; module set = 147 − 5 merged + `mail_tracking` = 143.

**Chapter 2 (mapping modules).** 15 phases / 75 steps in `tools/etl/mapping/`
(`run.py --db … --list` prints the plan). ORM lane: users (+password/TOTP hashes,
group memberships), partners, products, chart mapping (10/10 referenced taxes matched
the v20 template — zero clones needed), the 62 journal entries. SQL lane (+10M ids):
pickings/moves/quants, discuss channels+members (with `member_indices` computed
in-copy), owa account/chatbots/call logs, diamond billing lines + service prices,
chatter (messages, attachments+filestore hard-links, followers, tracking values with
v19-style rendered bodies), website views/pages/menus. Diamond's pending 1.2.3→1.7.0
migrations (billing m2m, notify flags, group carryovers) are applied by the pipeline.

**Chapter 3 (first full run + verify).** `sudi20_etl_dry1` loaded; iteration fixed 12
issues (each now a permanent mapping rule). `python -m tools.etl.verify --db
sudi20_etl_dry1` → **ALL GREEN**: 1159/1159 pickings, 3640/3640 moves, 331/331
channels, 1883/1883 billing lines, 494/494 partners, posted ₹6,142.50/₹6,142.50 to the
paisa per account, quants identical, 17,176 messages (the 360 gap = messages of
system/module records + 129 v19 messages pointing at already-deleted pickings — each
accounted for by the verifier), 0 filestore files missing.

**Chapter 4 (website precision).** All 60 custom views carried; `compare_pages.py`
proves **identical visible text** on every page except two ACCEPTED diffs: v20 renders
the title as "Services | SDPPL" (was reversed), and v20's thank-you template drops the
"My Company / 3575 Fake Buena Vista Avenue" DEMO block that v19 leaked on your live
site. Pixel side-by-sides: `/opt/odoo20/etl/baseline/side_*.png`. Notable recovered
fidelity: the jewellery hero photo (v19's website configurator stored it as an
attachment *named* `website.s_cover_default_image` — a mechanism v20 dropped; the
pipeline folds those 92 images into the real attachments), website logo/favicon,
menu tree, header without v20's new search box, `/install-sdppl-to-iphone` still 404
for the public exactly as on prod.

## Chapter 5 — YOUR testing round

Browse the migrated database (server already running, or restart with):

```bash
/opt/odoo20/odoo20-venv/bin/python /opt/odoo20/odoo20/odoo-bin \
    -c /opt/odoo20/custom/sudi_20/tools/etl/conf/odoo20-qa.conf \
    -d sudi20_etl_dry3 --db-filter='^sudi20_etl_dry3$' \
    --http-port=8126 --gevent-port=8127
```

→ **http://localhost:8126** — log in with `admin`/`admin` OR **your own prod
credentials** (all user passwords and TOTP secrets migrated). ⚠ Always pass
`--db-filter` when serving this DB: the QA config's default filter matches two
databases and Odoo would show the selector instead (lesson learned the hard way).

Suggested pass, side by side with v19 on :8119:
1. Discuss → WhatsApp tab: open your real chats, scroll history, attachments, search.
2. A done job-work receipt: chatter, moves, job types, POD attachments; print the
   receipt + an invoice with its annexure.
3. Job Work Analytics + Dashboards with real numbers; Billing state on receipts.
4. Contacts/partners (GST treatment, PAN), Employees, Timesheets.
5. Website: every page, then log in and try the website editor on a page.
6. Settings→Users: your groups; try logging in as a second user.

Anything wrong → tell Claude; the fix goes into the mapping and the DB is replayed —
never hand-patched.


## Chapter 5b — automated side-by-side functional QA (executed 2026-09-28)

Before your manual round finished, an automated dry run compared v19 and v20 head to
head. Harness: `tools/etl/qa/` — inventory.py (menus + get_views on every window
action + per-user ACL smoke), crawl.py (playwright opens EVERY menu-reachable screen
as admin, captures JS errors), renders.py (same documents rendered on both sides,
visible text diffed, one real PDF), flows.py (the full golden business flow executed
identically on both sides in one rolled-back transaction), compare.py (merges all of
it into /opt/odoo20/etl/qa/QA_REPORT.md).

Everything ran on throwaway clones (sudi19_fx @8129 / sudi20_fx @8131) so the QA
servers stayed clean. The golden flow: public jangad receipt (the app's entry point)
→ pickup confirm → data entry + billing auto-pricing → department-transfer wizard →
validate → delivery order → proof-of-delivery mark-delivered → create invoice → post
→ register payment → timesheet timer → WhatsApp template render → chatter.

Both sides produced the SAME document numbers (HO/IN/00660, HO/OUT/00570,
INV/26-27/0002) and the SAME totals (₹4,000 + ₹200 GST 5% = ₹4,200).

The dry run caught 3 REAL migration gaps — each was fixed IN THE MAPPING and the
whole database was rebuilt from scratch (`sudi20_etl_dry2`: mkdb → full run →
verify ALL GREEN, unattended), proving the replay machinery for the cutover:

1. **Fresh-company UX flags**: prod had dismissed the stock-SMS wizard
   (`has_received_warning_stock_sms`); without it every outgoing validation popped a
   wizard and the app's mark-delivered silently did nothing. Also carried:
   quick_edit_mode, email_secondary_color, account_opening_date,
   employee_properties_definition, l10n_in_pan_entity_id (post.py COMPANY_DETAILS).
2. **Company logo**: on 19 `company.logo` is related to `partner.image_1920`; the
   settings step looked at res.company attachments and found nothing, so reports
   carried the default logo (website.py SETTINGS).
3. **Duplicated service products**: the diamond module ships its 16 service products
   as module data, so the fresh target already had them; the template step copied
   prod's instead of matching → job types pointed at tax-less duplicates → job-work
   invoices came out UNTAXED (₹4,000 instead of ₹4,200). Fixed with a natural-key
   match (`key=["name"]`) in product.py TEMPLATE.

Also fixed in sudi_20 code: the three **Timesheets → Reporting** screens crashed on
`Unknown field sudi_picking_id` — **on v19 production too** (pre-existing bug: the
shared search view adds diamond fields the reporting SQL-view model lacks). diamond
now mirrors both fields onto `timesheets.analysis.report`, so v20 fixes what prod
never could.

Known accepted deltas (diamond 1.2.3 on prod vs 1.7.0 migrated): proof-of-delivery
(receiver name + signature) is required on deliveries; invoice line labels are
shorter; the pickup WhatsApp message formats the address without repeating the
partner name; pickup/delivery operators (rakesh, sdpplbdb) see only their own
active/today's pickings — 1.7.0's operator record rules, granted by its migration.

**Current QA server: http://localhost:8126 serves `sudi20_etl_dry3`** (dry1/dry2 kept
as fallbacks). v19 comparison server unchanged on :8119.

A second QA round on dry2 caught four more parity gaps — all fixed in the mapping and
proven by ANOTHER full unattended replay (dry3, verify ALL GREEN incl. new exact
table-parity checks):

4. **Company partner duplicated**: prod's company partner was copied as a new contact
   instead of landing on the partner mkdb created for the company (base.py
   COMPANY_PARTNER pre-map + write_mapped on the roots step).
5. **Service-price auto-grid**: the res.partner create() hook builds a default price
   grid for every partner the ORM lane creates — 5,910 phantom rows under prod's
   5,865 (diamond.py PRICE_PURGE deletes everything the idmap does not own).
6. **Install artifacts**: fresh-install Welcome/Training tasks + default
   General/Administrators channels deleted (post.py PURGE_INSTALL); archived
   partners re-archived (active=False is dropped by the create lane — FIXUPS).
7. **Channel group restrictions**: group_public_id cannot copy verbatim; restored by
   group xmlid for all 279 restricted channels incl. every WhatsApp channel
   (discuss.py CHANNEL_GROUPS).
8. **DB-level menu customizations**: prod's admin had added base.group_user to the
   Pickup / Deliveries / Job Work app menus (that is why "apps were missing" on the
   first v20 look!) — 20 such menu-group additions carried by xmlid (post.py
   MENU_GROUPS). App visibility is now identical PER USER: admin and sdpplbdb see all
   three, rakesh sees Deliveries+Pickup, vishal sees Job Work — same as prod.

verify.py now has an "exact table parity" section (total row counts vs source via the
src19 schema + single-company-partner check) so idmap-invisible duplicates can never
slip through again. Final tally: **v20 crawls 299/299 screens with ZERO JS errors**
(v19 itself has 3 — the Timesheets Reporting bug, fixed only on v20), flows 14/14 both
sides with identical document numbers and totals, QA_REPORT.md problems=0.

## Second source — the local dev DB (executed 2026-10-22)

The same harness migrates **any** v19 sudi database, not only the prod dump. The local
dev DB `sudi` (diamond 1.7.0 already applied, plus `sudi_api`/`sudi_sync`/
`sudi_diamond_billing`/OCA `fastapi` installed) was migrated for v2 app testing. Every
per-source artifact gets its own name when `ETL_SRC` is not `sudi_src19`
(`target_modules_prod_<src>.txt`, `checksums_src_<src>.json`), so the prod cutover files
are never overwritten.

```bash
cd /opt/odoo20/custom/sudi_20
B=~/local-backups/sudi-local-2026-10-22            # pg_dump -Fc of `sudi` + a copy of its filestore
export ETL_SRC=sudi_local_src19
tools/etl/restore_src.sh $B/sudi.dump $B/filestore
/opt/odoo20/odoo20-venv/bin/python tools/etl/gen_target_modules.py       # 152 - 5 merged = 148
/opt/odoo20/odoo20-venv/bin/python tools/etl/baseline_checksums.py
tools/etl/mkdb.sh sudi20_local
/opt/odoo20/odoo20-venv/bin/python -m tools.etl.run --db sudi20_local
/opt/odoo20/odoo20-venv/bin/python -m tools.etl.verify --db sudi20_local   # ALL GREEN
# serve: /opt/odoo20/odoo20-venv/bin/python /opt/odoo20/odoo20/odoo-bin -c /opt/odoo20/sudi20_local.conf  (:8075)
```

What a 1.7.0 source needed that raw prod did not (all now permanent, source-aware rules):

1. `bootstrap.open_env` drops the repo root from `sys.path`: the repo is also an addons
   dir, and its vendored `fastapi` addon shadowed the pip `fastapi` package as soon as the
   target had the addon installed (circular ImportError before any step ran).
2. `stock_move.sudi_billing_line_id` / `sudi_reference_line_id` are NULLed in the stock
   phase and re-linked after the billing lines load (`sudi.move.billing_links`); the
   billing m2m is copied from the source's own m2m table when it has one; the multi-page
   jangad and proof-of-delivery attachment m2m tables are carried (`post_diamond` phase).
3. `config.TZ_ALIASES`: the local DB stores `Asia/Calcutta` on 154 partners, which v20's
   tz selection refuses — mapped to `Asia/Kolkata` in the ORM lane, the user transform and
   a final SQL sweep (`parity.tz_aliases`).
4. `verify.py`: OTP/device rows of the mobile API are runtime data and are not migrated;
   a filestore check only fails for files that exist at the source (the local DB has 786
   attachment rows whose files were never on this machine).

## Latest production pull → `sudi20_prod` (executed 2026-10-26, local only)

A fresh pull of production (database + filestore) was migrated with this harness and is
the database the mobile apps are tested against. Nothing was deployed: no VPS, no
Nginx/SSL; production was only read.

```bash
B=~/prod-backups/sudi-2026-10-26        # sudi.dump (pg_dump -Fc), filestore/ (2,514 files), code/ snapshot
cd /opt/odoo20/custom/sudi_20
tools/etl/restore_src.sh $B/sudi.dump $B/filestore            # -> sudi_src19 (+ staged filestore)
/opt/odoo20/odoo20-venv/bin/python tools/etl/gen_target_modules.py   # 147 - 5 merged + mail_tracking = 143
/opt/odoo20/odoo20-venv/bin/python tools/etl/baseline_checksums.py
tools/etl/mkdb.sh sudi20_prod
/opt/odoo20/odoo20-venv/bin/python -m tools.etl.run --db sudi20_prod
/opt/odoo20/odoo20-venv/bin/python -m tools.etl.verify --db sudi20_prod        # ALL GREEN
pg_dump -Fc sudi20_prod > $B/sudi20_prod_post_etl.dump                          # snapshot before the rollout
# rollout: the mobile API is not installed on prod v19, it is installed on top of the migrated data
/opt/odoo20/odoo20-venv/bin/python /opt/odoo20/odoo20/odoo-bin -c /opt/odoo20/sudi20_prod.conf \
    -i sudi_api,sudi_diamond_billing --stop-after-init
psql -d sudi20_prod -c "UPDATE ir_cron SET active = false WHERE active"       # the install adds active crons
/opt/odoo20/odoo20-venv/bin/python -m tools.etl.verify --db sudi20_prod        # ALL GREEN again (data untouched)
# serve (:8077, LAN + adb reverse for the apps):
/opt/odoo20/odoo20-venv/bin/python /opt/odoo20/odoo20/odoo-bin -c /opt/odoo20/sudi20_prod.conf
```

Prod carried one uncommitted hotfix (`open_whatsapp_connector/models/owa_inbound_dedupe.py`,
SerializationFailure in the dedupe claim); it is ported to sudi_20 for parity.

**What the replay found** — a full-table sweep (every table both sides, row counts) plus the
phone found gaps the September source never exposed. All are mapping rules now (rules doc §G),
each proven by an unattended mkdb + run + verify:

1. 14 customer phone-number logins (portal) were not migrated; now created on their
   migrated contact (no twin), hashes copied — the customer app signs them in by OTP.
2. WhatsApp: 1,511 per-message records, 21 notification rules (prod's on/off choices),
   2,805 inbound dedupe keys, the group session; demo chatbots/auto-replies were listed
   twice (install seed + copy) — the copies took over the module xmlids, the twins are gone.
3. Office-app branding (`pwa.config`: SDPPL name, colours, 3 icons) showed module defaults.
4. IAP accounts (SMS credits, partner autocomplete) copied; the dry-run guard rewrites
   their tokens outside `ETL_PRODUCTION=1`.
5. Activities, e-mail recipients, SMS trackers, login history, running timers, channel
   auto-join groups, website visitors/page views, 89 PDF preview thumbnails.
6. Migration-day chatter on migrated records (142: "0.00 → 1,540.00 (Untaxed Amount)" on
   every invoice, second "created" notes) is deleted — chatter of migrated records is prod's.
7. Digest (monthly, 1 subscriber), default contact language en_IN, sequences continue after
   the +OFF ids (new WhatsApp messages sort after prod's history).
8. **diamond 1.7.0 stored computes were empty on every migrated move/picking** (prod runs
   1.2.3, which has no such columns): billing line/price/qty/amount/state, billing status,
   delivery stage. The field app's delivery list showed 0 of 10 open deliveries on the phone.
   `diamond.recompute` now computes every stored compute a sudi module defines whose column
   the source lacks, as the module upgrade would.

`verify.py` gained a section for all of the above (config tables must match in total, history
tables by source id, derived fields filled, open deliveries = prod's, no migration-day
chatter, sequences ahead of the copied ids, every attachment of a migrated record present).

**Result**: verify ALL GREEN after the ETL and again after the rollout; accounting identical to
the paisa (posted Σ ₹6,142.50, per-account balances); 1,657 pickings / 5,214 moves / 5,233 move
lines / 2,716 billing lines / 6,450 service prices / 363 channels; 24,281 of 24,618 messages
(the 337 others sit on 129 pickings deleted on prod and on system records 20 rebuilds);
2,039 files linked, 0 missing. Side-by-side QA (`tools/etl/qa`, clones sudi19_fx/sudi20_fx):
v20 crawls 311/311 screens with 0 JS errors (v19: 3, the pre-existing Timesheets Reporting
bug), 527 actions compile, golden flow 14/14 with identical numbers and totals, per-user access
identical for 17 users, report verdict ALL PROBES GREEN.

**Phone test** (M31, `adb reverse tcp:8077 tcp:8077`, APKs built with
`SUDI_BASE_URL=http://127.0.0.1:8077 SUDI_DB=sudi20_prod`): customer OTP sign-in as a migrated
login → jangad with typed quantity + photo + saved address (HO/IN/00930) → pickup confirmed with
a page → office entered the table and marked READY (auto-priced from the migrated price grid) →
job worker timer + department move + finish → delivery HO/OUT/00810 taken out and delivered with
name + signature → billing status "to bill"; productivity and notifications shown. Test logins:
test.all / test.pickup / test.jobwork / test.office, password `Sudi@2026`; customer OTP by the dev
switch `sudi_auth.otp_dev_no_send=1` (code in the server log: "OTP for <phone> is <code>").
