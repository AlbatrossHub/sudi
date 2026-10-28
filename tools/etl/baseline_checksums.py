#!/usr/bin/env python3
"""Capture the v19 SOURCE truth as JSON before anything is migrated. verify.py (P3)
compares the loaded v20 target against this file — if a number is not captured here,
its loss cannot be detected later.

    /opt/odoo20/odoo20-venv/bin/python tools/etl/baseline_checksums.py
Writes /opt/odoo20/etl/baseline/checksums_src.json
"""
import datetime
import hashlib
import json
import os
import sys

import psycopg2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from tools.etl import config  # noqa: E402

OUT = config.BASELINE_FILE


def rows(cr, sql, args=()):
    cr.execute(sql, args)
    return cr.fetchall()


def main():
    conn = psycopg2.connect(dbname=config.SRC_DB)
    cr = conn.cursor()
    data = {"db": config.SRC_DB, "captured": datetime.datetime.now().isoformat(timespec="seconds")}

    # 1. every user table with rows (nothing escapes by not being on a curated list)
    counts = {}
    for (t,) in rows(cr, """SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"""):
        cr.execute(f'SELECT count(*) FROM "{t}"')
        n = cr.fetchone()[0]
        if n:
            counts[t] = n
    data["table_counts"] = counts

    # 2. accounting: posted journal items to the cent, per account code and per tax
    data["account"] = {
        "posted_debit_credit": rows(cr, """SELECT round(sum(debit)::numeric,2)::text, round(sum(credit)::numeric,2)::text
                                           FROM account_move_line WHERE parent_state='posted'""")[0],
        "per_account": {code: (d, c) for code, d, c in rows(cr, """
            SELECT a.code_store->>%s, round(sum(l.debit)::numeric,2)::text, round(sum(l.credit)::numeric,2)::text
            FROM account_move_line l JOIN account_account a ON a.id=l.account_id
            WHERE l.parent_state='posted' GROUP BY 1 ORDER BY 1""", (str(config.COMPANY),))},
        "posted_moves": rows(cr, "SELECT count(*) FROM account_move WHERE state='posted'")[0][0],
        "amount_total_posted": rows(cr, """SELECT round(sum(amount_total)::numeric,2)::text
                                           FROM account_move WHERE state='posted'""")[0][0],
    }

    # 3. stock: on-hand per (location, product) — the physical truth of the job-work flow
    quants = rows(cr, """SELECT l.complete_name, p.default_code, t.name->>'en_US', round(q.quantity::numeric, 4)::text
                         FROM stock_quant q JOIN stock_location l ON l.id=q.location_id
                         JOIN product_product p ON p.id=q.product_id
                         JOIN product_template t ON t.id=p.product_tmpl_id
                         WHERE q.quantity != 0 ORDER BY 1,2,3""")
    data["stock"] = {
        "quants": quants,
        "quants_md5": hashlib.md5(json.dumps(quants).encode()).hexdigest(),
        "pickings_by_state": dict(rows(cr, "SELECT state, count(*) FROM stock_picking GROUP BY 1 ORDER BY 1")),
        "moves_done_qty": rows(cr, """SELECT round(sum(quantity)::numeric,4)::text FROM stock_move
                                      WHERE state='done'""")[0][0],
    }

    # 4. chatter: messages per model; attachments per model with byte sums
    data["chatter"] = {
        "messages_per_model": dict(rows(cr, """SELECT coalesce(model,'<none>'), count(*) FROM mail_message
                                               GROUP BY 1 ORDER BY 1""")),
        "attachments_per_model": {m: [n, int(b or 0)] for m, n, b in rows(cr, """
            SELECT coalesce(res_model,'<none>'), count(*), sum(file_size) FROM ir_attachment GROUP BY 1 ORDER BY 1""")},
        "followers": rows(cr, "SELECT count(*) FROM mail_followers")[0][0],
        "tracking_values": rows(cr, "SELECT count(*) FROM mail_tracking_value")[0][0],
    }

    # 5. discuss / whatsapp
    data["discuss"] = {
        "channels_by_type": dict(rows(cr, "SELECT channel_type, count(*) FROM discuss_channel GROUP BY 1 ORDER BY 1")),
        "members": rows(cr, "SELECT count(*) FROM discuss_channel_member")[0][0],
    }

    # 6. people
    data["people"] = {
        "users_active": sorted(u for (u,) in rows(cr, "SELECT login FROM res_users WHERE active")),
        "partners": rows(cr, "SELECT count(*) FROM res_partner")[0][0],
        "companies_flag": rows(cr, "SELECT count(*) FROM res_partner WHERE is_company")[0][0],
    }

    # 7. website: the inventory the precision pass (P4) must reproduce
    data["website"] = {
        "pages": rows(cr, """SELECT p.url, v.key, v.name FROM website_page p
                             JOIN ir_ui_view v ON v.id=p.view_id ORDER BY p.url"""),
        "menus": rows(cr, "SELECT name->>'en_US', url FROM website_menu ORDER BY parent_id NULLS FIRST, sequence, id"),
        "custom_views": rows(cr, """SELECT v.id, v.key, v.mode, v.active FROM ir_ui_view v
                                    WHERE v.arch_updated OR v.website_id IS NOT NULL
                                       OR NOT EXISTS (SELECT 1 FROM ir_model_data d
                                                      WHERE d.model='ir.ui.view' AND d.res_id=v.id)
                                    ORDER BY v.id"""),
    }

    os.makedirs(config.BASELINE_DIR, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(data, f, indent=1, default=str)
    print(f"wrote {OUT}")
    print(f"  tables with rows: {len(counts)}")
    print(f"  posted debit/credit: {data['account']['posted_debit_credit']}")
    print(f"  quant lines: {len(quants)} (md5 {data['stock']['quants_md5'][:12]}…)")
    print(f"  messages: {sum(data['chatter']['messages_per_model'].values())}, "
          f"attachments: {sum(v[0] for v in data['chatter']['attachments_per_model'].values())}")
    print(f"  website pages: {len(data['website']['pages'])}, custom views: {len(data['website']['custom_views'])}")


if __name__ == "__main__":
    main()
