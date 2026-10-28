#!/usr/bin/env python3
"""P3 verification: prove the loaded target against the v19 baseline with numbers.

    /opt/odoo20/odoo20-venv/bin/python -m tools.etl.verify --db sudi20_etl_dry1

Sections: refusal accounting, row parity per business table, accounting to the paisa,
stock on hand, chatter counts per model, filestore presence, users/website. Exit 1 on FAIL.
"""
import argparse
import json
import os
import sys

import psycopg2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from tools.etl import config  # noqa: E402

S = config.SRC_SCHEMA
OFF = config.OFFSET
BASELINE = config.BASELINE_FILE

failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    a = ap.parse_args()
    base = json.load(open(BASELINE))
    conn = psycopg2.connect(dbname=a.db)
    cr = conn.cursor()

    def q1(sql, args=()):
        cr.execute(sql, args)
        return cr.fetchone()[0]

    print("== refusals ==")
    cr.execute("SELECT step, left(reason, 60), count(DISTINCT old_id) FROM etl_refusal GROUP BY 1, 2 ORDER BY 3 DESC")
    rows = cr.fetchall()
    explained = {("website.page", "view not copied"): 4}   # module-owned generic pages exist on 20
    for step, reason, n in rows:
        check(f"refusal {step}: {reason}", explained.get((step, reason)) == n, f"n={n} distinct")
    if not rows:
        print("(none)")

    print("\n== row parity (migrated rows vs source rows) ==")
    T = base["table_counts"]
    for table in ("stock_picking", "stock_move", "stock_move_line", "stock_quant", "discuss_channel",
                  "discuss_channel_member", "sudi_diamond_billing_line", "sudi_diamond_partner_service_price",
                  "owa_call_log"):
        got = q1(f"SELECT count(*) FROM {table} WHERE id > {OFF}")
        check(table, got == T.get(table, 0), f"{got}/{T.get(table, 0)}")
    # followers/tracking of records that are intentionally not migrated (system/module records)
    # id-independent: every source follower whose record+partner migrated must exist on 20
    # (some are recreated natively by the ORM lane's auto-subscribe, hence not id+OFF)
    unsatisfied = q1(f"""SELECT count(*) FROM {S}.mail_followers f
                         JOIN etl_idmap m ON m.model = f.res_model AND m.old_id = f.res_id
                         JOIN etl_idmap p ON p.model = 'res.partner' AND p.old_id = f.partner_id
                         WHERE NOT EXISTS (SELECT 1 FROM mail_followers t
                                           WHERE t.res_model = f.res_model AND t.res_id = m.new_id
                                             AND t.partner_id = p.new_id)""")
    check("mail_followers coverage", unsatisfied == 0, f"{unsatisfied} source followers unmatched")
    got = q1(f"SELECT count(*) FROM mail_tracking_value WHERE id > {OFF}")
    droppable = q1(f"""SELECT count(*) FROM {S}.mail_tracking_value v
                       JOIN {S}.mail_message s ON s.id = v.mail_message_id
                       WHERE s.model IS NOT NULL AND NOT EXISTS
                             (SELECT 1 FROM etl_idmap m WHERE m.model = s.model AND m.old_id = s.res_id)""")
    check("mail_tracking_value (minus non-migrated messages)", got == T.get("mail_tracking_value", 0) - droppable,
          f"{got} = {T.get('mail_tracking_value', 0)} - {droppable}")
    for model, table in [("res.partner", "res_partner"), ("account.move", "account_move"),
                         ("account.move.line", "account_move_line"), ("product.product", "product_product"),
                         ("hr.employee", "hr_employee"), ("project.task", "project_task"),
                         ("account.analytic.line", "account_analytic_line")]:
        got = q1("SELECT count(*) FROM etl_idmap WHERE model = %s", (model,))
        check(f"idmap {model}", got >= T.get(table, 0), f"{got}/{T.get(table, 0)}")

    print("\n== accounting (to the paisa) ==")
    d, c = base["account"]["posted_debit_credit"]
    cr.execute("SELECT round(sum(debit)::numeric,2)::text, round(sum(credit)::numeric,2)::text "
               "FROM account_move_line WHERE parent_state='posted'")
    got = cr.fetchone()
    check("posted Σdebit", got[0] == d, f"{got[0]} vs {d}")
    check("posted Σcredit", got[1] == c, f"{got[1]} vs {c}")
    per19 = {k: list(v) for k, v in base["account"]["per_account"].items()}
    cr.execute("""SELECT a.code_store->>'1', round(sum(l.debit)::numeric,2)::text,
                         round(sum(l.credit)::numeric,2)::text
                  FROM account_move_line l JOIN account_account a ON a.id = l.account_id
                  WHERE l.parent_state='posted' GROUP BY 1""")
    per20 = {r[0]: [r[1], r[2]] for r in cr.fetchall()}
    diff = {k: (v, per20.get(k)) for k, v in per19.items() if per20.get(k) != v}
    check("per-account balances", not diff, json.dumps(diff))
    check("posted moves", q1("SELECT count(*) FROM account_move WHERE state='posted'") == base["account"]["posted_moves"])
    check("balanced moves", q1("SELECT count(*) FROM (SELECT move_id FROM account_move_line "
                               "GROUP BY move_id HAVING abs(sum(balance)) > 0.005) x") == 0)

    print("\n== stock on hand ==")
    cr.execute("""SELECT l.complete_name, p.default_code, t.name->>'en_US', round(q.quantity::numeric, 4)::text
                  FROM stock_quant q JOIN stock_location l ON l.id=q.location_id
                  JOIN product_product p ON p.id=q.product_id JOIN product_template t ON t.id=p.product_tmpl_id
                  WHERE q.quantity != 0 ORDER BY 1,2,3""")
    got = [list(r) for r in cr.fetchall()]
    want = [list(r) for r in base["stock"]["quants"]]
    check("quants per (location, product)", got == want, f"{len(got)}/{len(want)} lines")
    cr.execute(f"SELECT state, count(*) FROM stock_picking WHERE id > {OFF} GROUP BY 1")
    got = dict(cr.fetchall())
    check("pickings by state", got == base["stock"]["pickings_by_state"], json.dumps(got))
    got = q1(f"SELECT round(sum(quantity)::numeric,4)::text FROM stock_move WHERE state='done' AND id > {OFF}")
    check("done move qty", got == base["stock"]["moves_done_qty"], f"{got}")

    print("\n== chatter ==")
    # migrated messages = the id-mapped ones (records created after the ETL -- the mobile API
    # rollout, testing -- also get ids above OFF once the sequences continue after prod's)
    cr.execute("""SELECT coalesce(m.model,'<none>'), count(*) FROM mail_message m
                  JOIN etl_idmap i ON i.model = 'mail.message' AND i.new_id = m.id GROUP BY 1""")
    got = dict(cr.fetchall())
    want = base["chatter"]["messages_per_model"]
    # sudi.auth.otp / sudi.api.device: mobile-API runtime rows (OTP codes, handsets) are not migrated;
    # devices re-register on first login against the new server.
    dropped_ok = {"sudi.auth.otp", "sudi.api.device", "ir.actions.server", "ir.cron", "hr.version", "crm.team",
                  "account.return.type", "crm.team.member", "<none>", "stock.picking"}
    bad = {m: (want.get(m), got.get(m)) for m in set(want) | set(got)
           if want.get(m) != got.get(m) and m not in dropped_ok}
    check("messages per model", not bad, json.dumps(bad))
    stale = q1(f"""SELECT count(*) FROM {S}.mail_message s WHERE s.model = 'stock.picking'
                   AND NOT EXISTS (SELECT 1 FROM {S}.stock_picking p WHERE p.id = s.res_id)""")
    check("stock.picking messages (minus stale refs)",
          got.get("stock.picking", 0) == want.get("stock.picking", 0) - stale,
          f"{got.get('stock.picking', 0)} = {want.get('stock.picking', 0)} - {stale} stale")
    cr.execute(f"SELECT store_fname FROM ir_attachment WHERE store_fname IS NOT NULL AND id > {OFF}")
    fs_root = os.path.join(config.ETL_DIR, "data20", "filestore", a.db)
    missing = [f for (f,) in cr.fetchall() if not os.path.exists(os.path.join(fs_root, f))]
    # a file the SOURCE filestore never had cannot be migrated (the local dev DB has rows whose
    # files were never on this machine); only files present at the source must arrive.
    lost = [f for f in missing if os.path.exists(os.path.join(config.FILESTORE_SRC, f))]
    # every source attachment whose owner record migrated (or that has no owner) must exist on 20;
    # module images (payment methods, menus...) belong to module records 20 ships itself
    cr.execute(f"""SELECT coalesce(s.res_model, '(none)'), count(*) FROM {S}.ir_attachment s
                   WHERE (s.res_model IS NULL OR s.res_id IS NULL OR s.res_id = 0
                          OR EXISTS (SELECT 1 FROM etl_idmap o WHERE o.model = s.res_model AND o.old_id = s.res_id))
                     AND coalesce(s.res_model, '') NOT IN ('ir.ui.menu', 'ir.module.module', 'mail.compose.message')
                     AND coalesce(s.name, '') NOT LIKE '/web/assets/%'
                     AND NOT EXISTS (SELECT 1 FROM etl_idmap m WHERE m.model = 'ir.attachment' AND m.old_id = s.id)
                   GROUP BY 1""")
    left = dict(cr.fetchall())
    check("attachments of migrated records", not left, json.dumps(left))
    check("filestore files present", not lost,
          f"{len(lost)} lost under {fs_root}" + (f"; {len(missing) - len(lost)} rows point at files"
                                                 f" absent on the source too" if len(missing) > len(lost) else ""))

    print("\n== people / website ==")
    check("active users", q1("SELECT count(*) FROM res_users WHERE active") >= 17)
    got = q1("SELECT count(*) FROM etl_idmap WHERE model = 'ir.ui.view'")
    check("custom views", got == len(base["website"]["custom_views"]),
          f"{got}/{len(base['website']['custom_views'])} (repair COWs excluded)")
    got = q1("SELECT count(*) FROM website_page")
    check("website pages", got == len(base["website"]["pages"]), f"{got}/{len(base['website']['pages'])}")

    print("\n== exact table parity (no install-artifact leftovers) ==")
    # idmap counts alone cannot see DUPLICATES (auto-created grids, onboarding data,
    # a twin company partner) -- these caught real gaps in the side-by-side QA.
    for table in ("sudi_diamond_partner_service_price", "project_task", "discuss_channel",
                  "sudi_diamond_job_type", "product_template"):
        want = q1(f"SELECT count(*) FROM {S}.{table}")
        got = q1(f"SELECT count(*) FROM {table}")
        check(f"total rows {table}", got == want, f"{got}/{want}")
    got = q1("""SELECT count(*) FROM res_partner p JOIN res_company c ON true
                WHERE p.name = (SELECT name FROM res_partner WHERE id = c.partner_id)""")
    check("company partner not duplicated", got == 1, f"{got} partners carry the company name")

    print("\n== full-table sweep findings (2026-10-26 prod pull) ==")
    # tables the September harness never met with data, or met as install-seeded twins;
    # config tables must match in TOTAL (no seeded duplicates), history tables in migrated rows
    for table in ("owa_chatbot", "owa_chatbot_step", "owa_chatbot_menu_option", "owa_auto_reply",
                  "owa_notification_rule", "owa_quick_reply", "iap_account", "pwa_config",
                  "discuss_channel_res_groups_rel"):
        want = q1(f"SELECT count(*) FROM {S}.{table}")
        got = q1(f"SELECT count(*) FROM {table}")
        check(f"total rows {table}", got == want, f"{got}/{want}")
    for table in ("owa_message", "owa_inbound_dedupe", "owa_group_session", "website_visitor", "website_track",
                  "sms_tracker", "res_users_log"):
        want = q1(f"SELECT count(*) FROM {S}.{table}")
        # source ids that arrived (+OFF): rows written after the ETL (logins, new chats) do not count
        got = q1(f"SELECT count(*) FROM {table} t WHERE t.id > {OFF} AND EXISTS "
                 f"(SELECT 1 FROM {S}.{table} s WHERE s.id = t.id - {OFF})")
        check(f"migrated rows {table}", got == want, f"{got}/{want}")
    # orphans on prod itself (a timer on a deleted timesheet line, e-mails of messages on deleted
    # records) cannot follow: only rows whose parent migrated are expected
    want = q1(f"""SELECT count(*) FROM {S}.timer_timer t
                  WHERE EXISTS (SELECT 1 FROM etl_idmap m WHERE m.model = t.res_model AND m.old_id = t.res_id)""")
    got = q1(f"SELECT count(*) FROM timer_timer WHERE id > {OFF}")
    check("running timers on migrated lines", got == want, f"{got}/{want}")
    want = q1(f"""SELECT count(*) FROM {S}.mail_mail_res_partner_rel r
                  WHERE EXISTS (SELECT 1 FROM etl_idmap m WHERE m.model = 'mail.mail' AND m.old_id = r.mail_mail_id)""")
    got = q1(f"SELECT count(*) FROM mail_mail_res_partner_rel WHERE mail_mail_id > {OFF}")
    check("mail_mail recipients (of migrated mails)", got == want, f"{got}/{want}")
    want = q1(f"""SELECT count(*) FROM {S}.mail_activity a
                  WHERE EXISTS (SELECT 1 FROM etl_idmap m WHERE m.model = a.res_model AND m.old_id = a.res_id)""")
    got = q1(f"SELECT count(*) FROM mail_activity WHERE id > {OFF}")
    check("activities on migrated records", got == want, f"{got}/{want}")
    want = q1(f"SELECT count(*) FROM {S}.res_users WHERE share AND id > 2 AND id NOT IN "
              f"(SELECT res_id FROM {S}.ir_model_data WHERE model = 'res.users' AND module = 'base')")
    got = q1("""SELECT count(*) FROM res_users u JOIN etl_idmap m ON m.model = 'res.users' AND m.new_id = u.id
                WHERE u.share""")
    check("portal (customer) logins", got == want, f"{got}/{want}")
    got = q1(f"""SELECT count(*) FROM {S}.res_users s JOIN etl_idmap m ON m.model = 'res.users' AND m.old_id = s.id
                 JOIN res_users u ON u.id = m.new_id JOIN etl_idmap p ON p.model = 'res.partner' AND p.old_id = s.partner_id
                 WHERE u.partner_id <> p.new_id""")
    check("every user sits on its migrated contact", got == 0, f"{got} users on another partner")
    cr.execute(f"""SELECT count(*) FROM {S}.pwa_config s JOIN {S}.ir_model_data sd ON sd.model = 'pwa.config' AND sd.res_id = s.id
                   JOIN ir_model_data td ON td.model = 'pwa.config' AND td.module = sd.module AND td.name = sd.name
                   JOIN pwa_config t ON t.id = td.res_id
                   WHERE (t.app_name, t.app_short_name, t.theme_color, t.background_color, t.app_description)
                         IS DISTINCT FROM (s.app_name, s.app_short_name, s.theme_color, s.background_color, s.app_description)""")
    check("office app branding (pwa.config)", cr.fetchone()[0] == 0)
    want = q1(f"SELECT count(*) FROM {S}.ir_attachment WHERE res_model = 'pwa.config' AND store_fname IS NOT NULL")
    got = q1(f"SELECT count(*) FROM ir_attachment WHERE res_model = 'pwa.config' AND store_fname IS NOT NULL")
    check("office app icons", got == want, f"{got}/{want}")
    got = q1(f"""SELECT count(*) FROM mail_message m WHERE m.id < {OFF} AND m.model IS NOT NULL
                 AND EXISTS (SELECT 1 FROM etl_idmap i WHERE i.model = m.model AND i.new_id = m.res_id)""")
    check("no migration-day chatter on migrated records", got == 0, f"{got} messages")
    cr.execute(f"""SELECT (SELECT periodicity FROM {S}.digest_digest ORDER BY id LIMIT 1),
                          (SELECT periodicity FROM digest_digest ORDER BY id LIMIT 1),
                          (SELECT count(*) FROM {S}.digest_digest_res_users_rel),
                          (SELECT count(*) FROM digest_digest_res_users_rel)""")
    p19, p20, n19, n20 = cr.fetchone()
    check("digest periodicity + subscribers", (p19, n19) == (p20, n20), f"{p20}/{n20} vs {p19}/{n19}")
    cr.execute(f"""SELECT (SELECT d.json_value FROM {S}.ir_default d JOIN {S}.ir_model_fields f ON f.id = d.field_id
                           WHERE f.model = 'res.partner' AND f.name = 'lang' AND d.user_id IS NULL AND d.company_id IS NULL),
                          (SELECT d.json_value FROM ir_default d JOIN ir_model_fields f ON f.id = d.field_id
                           WHERE f.model = 'res.partner' AND f.name = 'lang' AND d.user_id IS NULL AND d.company_id IS NULL)""")
    l19, l20 = cr.fetchone()
    check("default contact language", l19 == l20, f"{l20} vs {l19}")
    # diamond 1.7.0 stored computes a 1.2.3 source has no column for (diamond.recompute fills them)
    for table, col, where in (("stock_move", "sudi_billing_state", "true"),
                              ("stock_picking", "sudi_billing_status", "true"),
                              ("stock_picking", "sudi_delivery_stage", "sudi_origin_receipt_id IS NOT NULL")):
        got = q1(f"SELECT count(*) FROM {table} WHERE id > {OFF} AND {where} AND {col} IS NULL")
        check(f"{table}.{col} computed", got == 0, f"{got} empty")
    # the field app's delivery list: every job-work delivery still open on prod
    want = q1(f"""SELECT count(*) FROM {S}.stock_picking p JOIN {S}.stock_picking_type t ON t.id = p.picking_type_id
                  WHERE t.code = 'outgoing' AND p.sudi_origin_receipt_id IS NOT NULL AND p.state NOT IN ('done', 'cancel')""")
    got = q1(f"SELECT count(*) FROM stock_picking WHERE id > {OFF} AND sudi_delivery_stage IN ('awaiting', 'out')")
    check("open deliveries listed for the field app", got == want, f"{got}/{want}")
    cr.execute("""SELECT c.table_name FROM information_schema.columns c
                  JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                   AND t.table_type = 'BASE TABLE'
                  WHERE c.table_schema = 'public' AND c.column_name = 'id'""")
    behind = []
    for (table,) in cr.fetchall():
        seq = q1("SELECT pg_get_serial_sequence(%s, 'id')", (f'public."{table}"',))
        if not seq:
            continue
        top = q1(f'SELECT max(id) FROM "{table}"')
        if top is not None and top >= OFF and q1("SELECT last_value FROM " + seq) < top:
            behind.append(table)
    check("id sequences continue after migrated rows", not behind, ", ".join(behind))

    print(f"\n{'ALL GREEN' if not failures else str(len(failures)) + ' FAILURES: ' + ', '.join(failures)}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
