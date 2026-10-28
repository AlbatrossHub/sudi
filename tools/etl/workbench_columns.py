#!/usr/bin/env python3
"""ETL mapping workbench (P1): diff every populated table of the Odoo 19 staging copy (sudi_src19)
against the Odoo 20 reference database and propose a rule for every difference.

Run as om1203 (peer auth on both databases):
    /opt/odoo20/odoo20-venv/bin/python tools/etl/workbench_columns.py
Environment: ETL_SRC (default sudi_src19), ETL_DST (default sudi20_ref), ETL_OUT (default docs/).

Writes docs/etl_mapping.md (human review) and tools/etl/workbench/columns.json (consumed by P2).
"""
import datetime
import json
import os
import re
import sys

import psycopg2

SRC = os.environ.get("ETL_SRC", "sudi_src19")
DST = os.environ.get("ETL_DST", "sudi20_ref")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.environ.get("ETL_OUT", os.path.join(REPO, "docs"))
JSON_OUT = os.path.join(REPO, "tools", "etl", "workbench", "columns.json")

# Runtime/ephemeral tables never migrated (sudi decision, chapter 1): reported, never mapped.
DROP_TABLES = {
    "website_track", "website_visitor", "res_device_log", "res_device", "mail_push", "mail_push_device",
    "bus_bus", "ir_logging", "ir_cron_progress", "ir_attachment_embedding", "mail_notification_web_push",
    "auth_totp_device", "ir_sessions", "base_import_import", "base_import_mapping",
    "spreadsheet_revision",  # 0 rows on prod
    "mail_message_res_partner_starred_rel",  # starring removed on 20 (report the count; archive if ever non-trivial)
}
# Column-level rules known before looking (from the sudi_20 code port). Key: (table or '*', column).
KNOWN = {
    ("stock_move", "product_uom"): "RENAME -> uom_id",
    ("stock_move", "scrap_id"): "TRANSFORM -> is_scrap boolean",
    ("res_partner", "peppol_endpoint"): "RENAME -> routing_endpoint (routing_scheme from peppol_eas)",
    ("res_partner", "vies_valid"): "DROP (base_vat merged into account; recheck on 20)",
    ("res_partner", "global_location_number"): "DROP (account_add_gln merged; GLN unused on prod)",
    ("mail_tracking_value", "currency_id"): "DROP (removed on 20)",
    ("*", "company_type"): "DROP (is_company computed from VAT on 20)",
}
LEGACY_RE = re.compile(r"^(x_live13|x_livev13|x_v13|v13_)")
STUDIO_RE = re.compile(r"^x_studio_")
SAMPLE_MAX_ROWS = 1_500_000


def q(cur, sql, args=None):
    cur.execute(sql, args or ())
    return cur.fetchall()


def columns(cur, table):
    return {
        r[0]: {"type": r[1], "nullable": r[2] == "YES", "default": r[3]}
        for r in q(cur, """select column_name, data_type, is_nullable, column_default
                           from information_schema.columns where table_schema='public' and table_name=%s""", (table,))
    }


def main():
    src = psycopg2.connect(dbname=SRC)
    dst = psycopg2.connect(dbname=DST)
    s, d = src.cursor(), dst.cursor()

    tables18 = {r[0]: int(r[1]) for r in q(s, "select relname, n_live_tup from pg_stat_user_tables where n_live_tup > 0")}
    all20 = {r[0] for r in q(d, "select table_name from information_schema.tables where table_schema='public'")}
    all18 = {r[0] for r in q(s, "select table_name from information_schema.tables where table_schema='public'")}

    # model <-> table and ownership on 18
    model_module18 = {}
    for model, module in q(s, """select m.model, min(d.module) from ir_model m
                                  join ir_model_data d on d.model='ir.model' and d.res_id=m.id group by m.model"""):
        model_module18[model] = module
    table_model18 = {m.replace(".", "_"): m for m in model_module18}
    # m2m relation tables on 18
    rel18 = {}
    for rt, model, name in q(s, "select relation_table, model, name from ir_model_fields where relation_table is not null"):
        rel18.setdefault(rt, []).append(f"{model}.{name}")
    rel20 = {r[0] for r in q(d, "select distinct relation_table from ir_model_fields where relation_table is not null")}
    # field ownership on 18 (module that created the field)
    field_module18 = {}
    for module, model, name in q(s, """select d.module, f.model, f.name from ir_model_fields f
                                        join ir_model_data d on d.model='ir.model.fields' and d.res_id=f.id"""):
        field_module18.setdefault((model, name), module)
    # field types both sides
    def fields(cur):
        out = {}
        for model, name, ttype, relation, store in q(cur, "select model, name, ttype, relation, store from ir_model_fields"):
            out[(model, name)] = {"ttype": ttype, "relation": relation, "store": store}
        return out
    f18, f20 = fields(s), fields(d)
    # selection keys both sides
    def selections(cur):
        out = {}
        for model, name, value in q(cur, """select f.model, f.name, s.value from ir_model_fields_selection s
                                             join ir_model_fields f on f.id=s.field_id"""):
            out.setdefault((model, name), set()).add(value)
        return out
    sel18, sel20 = selections(s), selections(d)

    report = {"generated": datetime.datetime.utcnow().isoformat() + "Z", "src": SRC, "dst": DST,
              "tables_missing": [], "columns_dropped": [], "type_changes": [], "selection_keys_gone": [],
              "new_not_null": [], "tables_ok": 0}

    for table, rows in sorted(tables18.items(), key=lambda kv: -kv[1]):
        model = table_model18.get(table)
        if table not in all20:
            entry = {"table": table, "rows": rows, "model": model,
                     "module": model_module18.get(model) if model else None,
                     "m2m_of": rel18.get(table), "on_20_as_rel": table in rel20}
            if table in DROP_TABLES:
                entry["rule"] = "DROPPED BY DECISION"
            elif entry["module"] in ("data_import_script", "studio_customization"):
                entry["rule"] = "DROP (module not migrated)"
            elif rel18.get(table):
                entry["rule"] = "REVIEW m2m: find the 20 relation table of " + ", ".join(rel18[table])
            else:
                entry["rule"] = "REVIEW"
            report["tables_missing"].append(entry)
            continue
        c18, c20 = columns(s, table), columns(d, table)
        dropped = [c for c in c18 if c not in c20]
        report["tables_ok"] += 1
        if dropped:
            counts = q(s, "select " + ", ".join(f'count("{c}")' for c in dropped) + f' from "{table}"')[0]
            for col, nn in zip(dropped, counts):
                fkey = (model, col) if model else None
                entry = {"table": table, "rows": rows, "column": col, "type": c18[col]["type"], "non_null": int(nn),
                         "module": field_module18.get(fkey) if fkey else None,
                         "ttype": f18.get(fkey, {}).get("ttype") if fkey else None,
                         "relation": f18.get(fkey, {}).get("relation") if fkey else None, "samples": None}
                if table in DROP_TABLES:
                    entry["rule"] = "DROPPED BY DECISION"
                elif LEGACY_RE.match(col):
                    entry["rule"] = "LEGACY_ID → etl_idmap.legacy13_id"
                elif STUDIO_RE.match(col):
                    entry["rule"] = "STUDIO → marut_custom (ex_close_date kept, others dropped)"
                elif (table, col) in KNOWN:
                    entry["rule"] = KNOWN[(table, col)]
                elif ("*", col) in KNOWN:
                    entry["rule"] = KNOWN[("*", col)]
                elif nn == 0:
                    entry["rule"] = "DROP (no data)"
                else:
                    entry["rule"] = "REVIEW"
                    if rows <= SAMPLE_MAX_ROWS and c18[col]["type"] not in ("bytea",):
                        try:
                            entry["samples"] = [[str(v)[:60], int(n)] for v, n in q(
                                s, f'select "{col}", count(*) from "{table}" where "{col}" is not null group by 1 order by 2 desc limit 3')]
                        except Exception:
                            src.rollback()
                report["columns_dropped"].append(entry)
        for col in c18:
            if col in c20 and c18[col]["type"] != c20[col]["type"]:
                report["type_changes"].append({"table": table, "column": col, "type18": c18[col]["type"], "type20": c20[col]["type"]})
        for col, meta in c20.items():
            if col not in c18 and not meta["nullable"] and meta["default"] is None and col != "id":
                report["new_not_null"].append({"table": table, "column": col, "type": meta["type"]})
        # selection keys
        if model:
            for (m, name), keys in sel18.items():
                if m != model or (m, name) not in sel20 or name not in c18:
                    continue
                if c18[name]["type"] not in ("character varying", "text"):
                    continue  # company-dependent selection stored as jsonb: checked by hand
                gone = keys - sel20[(m, name)]
                if gone:
                    usage = dict(q(s, f'select "{name}", count(*) from "{table}" where "{name}" = any(%s) group by 1', (list(gone),)))
                    for k in sorted(gone):
                        if usage.get(k):
                            report["selection_keys_gone"].append({"model": model, "field": name, "key": k, "rows": int(usage[k]),
                                                                  "keys20": sorted(sel20[(m, name)])})

    # ---- natural-key catalogue --------------------------------------------------------------
    def col_exists(cur, table, col):
        return bool(q(cur, "select 1 from information_schema.columns where table_name=%s and column_name=%s", (table, col)))
    acc_code18 = 'code' if col_exists(s, 'account_account', 'code') else "code_store->>'1'"
    acc_code20 = 'code' if col_exists(d, 'account_account', 'code') else "code_store->>'1'"
    CAT = [
        ("res.company", "select name from res_company", None),
        ("res.currency (active)", "select name from res_currency where active", None),
        ("res.country", "select code from res_country", None),
        ("res.country.state", "select c.code||'-'||s.code from res_country_state s join res_country c on c.id=s.country_id", None),
        ("res.lang (active)", "select code from res_lang where active", None),
        ("uom.uom", "select name->>'en_US' from uom_uom", None),
        ("account.account (code)", f"select {acc_code18} from account_account", f"select {acc_code20} from account_account"),
        ("account.tax (name|type|amount)", "select (name->>'en_US')||'|'||type_tax_use||'|'||amount from account_tax", None),
        ("account.journal (code)", "select code from account_journal", None),
        ("account.payment.term", "select name->>'en_US' from account_payment_term", None),
        ("account.fiscal.position", "select name->>'en_US' from account_fiscal_position", None),
        ("account.analytic.plan", "select name->>'en_US' from account_analytic_plan", None),
        ("account.analytic.account", "select name from account_analytic_account", None),
        ("account.incoterms", "select code from account_incoterms", None),
        ("product.category (complete_name)", "select complete_name from product_category", None),
        ("product.pricelist", "select name->>'en_US' from product_pricelist", None),
        ("stock.warehouse (code)", "select code from stock_warehouse", None),
        ("stock.location (complete_name)", "select complete_name from stock_location", None),
        ("stock.picking.type (wh|seq)", "select coalesce(w.code,'-')||'|'||t.sequence_code from stock_picking_type t left join stock_warehouse w on w.id=t.warehouse_id", None),
        ("crm.team", "select name->>'en_US' from crm_team", None),
        ("crm.stage", "select name->>'en_US' from crm_stage", None),
        ("crm.tag", "select name->>'en_US' from crm_tag", None),
        ("crm.lost.reason", "select name->>'en_US' from crm_lost_reason", None),
        ("utm.source", "select name from utm_source", None),
        ("utm.medium", "select name from utm_medium", None),
        ("utm.campaign", "select name from utm_campaign", None),
        ("res.partner.category", "select name->>'en_US' from res_partner_category", None),
        ("res.partner.industry", "select name->>'en_US' from res_partner_industry", None),
        ("mail.message.subtype (xmlid)", "select module||'.'||name from ir_model_data where model='mail.message.subtype' and module<>'__export__'", None),
        ("mail.activity.type (xmlid)", "select module||'.'||name from ir_model_data where model='mail.activity.type' and module<>'__export__'", None),
        ("mail.activity.type (name)", "select name->>'en_US' from mail_activity_type", None),
        ("res.groups (xmlid)", "select module||'.'||name from ir_model_data where model='res.groups' and module<>'__export__'", None),
        ("ir.model (model)", "select model from ir_model", None),
        ("ir.model.fields (model.name, stored)", "select model||'.'||name from ir_model_fields where store", None),
        ("ir.sequence (code)", "select code from ir_sequence where code is not null", None),
        ("ir.cron (xmlid)", "select module||'.'||name from ir_model_data where model='ir.cron' and module<>'__export__'", None),
        ("mail.template (xmlid)", "select module||'.'||name from ir_model_data where model='mail.template' and module<>'__export__'", None),
        ("ir.actions.report (xmlid)", "select module||'.'||name from ir_model_data where model='ir.actions.report' and module<>'__export__'", None),
        ("res.users (login)", "select login from res_users where active", None),
        ("hr.department", "select name->>'en_US' from hr_department", None),
        ("hr.job", "select name->>'en_US' from hr_job", None),
        ("helpdesk.team", "select name->>'en_US' from helpdesk_team", None),
        ("helpdesk.stage", "select name->>'en_US' from helpdesk_stage", None),
        ("project.project", "select name from project_project", None),
        ("project.task.type", "select name->>'en_US' from project_task_type", None),
        ("res.bank", "select name from res_bank", None),
        ("documents.folder/document type", "select name from documents_document where type='folder'", None),
    ]
    catalogue = []
    for label, sql18, sql20 in CAT:
        try:
            k18 = {r[0] for r in q(s, sql18) if r[0] is not None}
        except Exception as e:
            src.rollback(); k18 = None
        try:
            k20 = {r[0] for r in q(d, sql20 or sql18) if r[0] is not None}
        except Exception as e:
            dst.rollback(); k20 = None
        if k18 is None:
            catalogue.append({"model": label, "n18": None, "n20": len(k20) if k20 is not None else None, "matched": None, "unmatched_sample": None})
            continue
        matched = len(k18 & k20) if k20 is not None else None
        sample = sorted(k18 - k20)[:8] if k20 is not None else None
        catalogue.append({"model": label, "n18": len(k18), "n20": len(k20) if k20 is not None else None,
                          "matched": matched, "unmatched_sample": sample})
    report["natural_keys"] = catalogue
    report["tables18_with_rows"] = len(tables18)

    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    write_md(report)
    print(f"tables with rows on 18: {len(tables18)}; present on 20: {report['tables_ok']}; missing: {len(report['tables_missing'])}")
    print(f"dropped columns: {len(report['columns_dropped'])} "
          f"(REVIEW: {sum(1 for c in report['columns_dropped'] if c['rule']=='REVIEW')}); "
          f"type changes: {len(report['type_changes'])}; selection keys gone (used): {len(report['selection_keys_gone'])}; "
          f"new NOT NULL w/o default: {len(report['new_not_null'])}")
    print("wrote", JSON_OUT, "and", os.path.join(OUT_DIR, "etl_mapping.md"))


def write_md(r):
    os.makedirs(OUT_DIR, exist_ok=True)
    L = []
    w = L.append
    w(f"# ETL mapping workbench: {r['src']} (Odoo 18) → {r['dst']} (Odoo 20)\n")
    w(f"Generated {r['generated']} by `tools/etl/workbench_columns.py`. Every row below needs a rule before the first dry run; "
      "`REVIEW` rows are the open items. Raw data: `tools/etl/workbench/columns.json`.\n")
    w(f"Tables with data on 18: **{r['tables18_with_rows']}**, present on 20: **{r['tables_ok']}**, absent on 20: **{len(r['tables_missing'])}**. "
      f"Dropped columns: **{len(r['columns_dropped'])}** ({sum(1 for c in r['columns_dropped'] if c['rule']=='REVIEW')} to review, "
      f"{sum(1 for c in r['columns_dropped'] if c['non_null']==0)} empty). Type changes: {len(r['type_changes'])}. "
      f"Selection keys in use that vanished: {len(r['selection_keys_gone'])}. New NOT NULL columns without default: {len(r['new_not_null'])}.\n")

    w("\n## A. Tables with data on 18 that do not exist on 20\n")
    w("| table | rows | model / m2m of | 18 module | rule |\n|---|---:|---|---|---|")
    for t in r["tables_missing"]:
        what = t["model"] or (("m2m: " + ", ".join(t["m2m_of"])) if t["m2m_of"] else "?")
        w(f"| `{t['table']}` | {t['rows']:,} | {what} | {t['module'] or ''} | {t['rule']} |")

    w("\n## B. Columns present on 18 and gone on 20 (tables that exist on both)\n")
    w("Rows with data and no known rule are marked REVIEW; `samples` are the three most frequent values.\n")
    w("| table | rows | column | type | non-null | 18 module | rule | samples |\n|---|---:|---|---|---:|---|---|---|")
    for c in sorted(r["columns_dropped"], key=lambda c: (c["rule"] != "REVIEW", -c["non_null"], c["table"])):
        if c["rule"].startswith("LEGACY_ID") or c["rule"] == "DROP (no data)":
            continue
        smp = "; ".join(f"{v} ×{n}" for v, n in (c["samples"] or [])) if c["samples"] else ""
        rel = f" → {c['relation']}" if c.get("relation") else ""
        w(f"| `{c['table']}` | {c['rows']:,} | `{c['column']}` | {c['ttype'] or c['type']}{rel} | {c['non_null']:,} | {c['module'] or ''} | {c['rule']} | {smp} |")
    legacy = [c for c in r["columns_dropped"] if c["rule"].startswith("LEGACY_ID")]
    empty = [c for c in r["columns_dropped"] if c["rule"] == "DROP (no data)"]
    w(f"\n### B.1 Legacy v13 id columns ({len(legacy)}) → `etl_idmap.legacy13_id`\n")
    w(", ".join(f"`{c['table']}.{c['column']}`" for c in legacy))
    w(f"\n\n### B.2 Dropped columns with no data on 18 ({len(empty)}) → dropped\n")
    w(", ".join(f"`{c['table']}.{c['column']}`" for c in empty))

    w("\n\n## C. Selection keys in use on 18 that no longer exist on 20\n")
    w("| model | field | key on 18 | rows | keys on 20 |\n|---|---|---|---:|---|")
    for k in r["selection_keys_gone"]:
        w(f"| {k['model']} | `{k['field']}` | `{k['key']}` | {k['rows']:,} | {', '.join(k['keys20'])} |")

    w("\n## D. Column type changes\n")
    w("| table | column | 18 | 20 |\n|---|---|---|---|")
    for t in r["type_changes"]:
        w(f"| `{t['table']}` | `{t['column']}` | {t['type18']} | {t['type20']} |")

    w("\n## E. New NOT NULL columns on 20 without a database default (the mapping must supply a value)\n")
    w("| table | column | type |\n|---|---|---|")
    for n in r["new_not_null"]:
        w(f"| `{n['table']}` | `{n['column']}` | {n['type']} |")

    w("\n## F. Natural-key catalogue (reference data matched by key, not by id)\n")
    w("`n18` distinct keys on 18, `n20` on the fresh 20 gate DB, `matched` = 18 keys already present on 20; the rest are created by the ETL.\n")
    w("| model (key) | n18 | n20 | matched | unmatched examples |\n|---|---:|---:|---:|---|")
    for c in r["natural_keys"]:
        ex = ", ".join(str(x) for x in (c["unmatched_sample"] or []))
        w(f"| {c['model']} | {c['n18'] if c['n18'] is not None else 'n/a'} | {c['n20'] if c['n20'] is not None else 'n/a'} | {c['matched'] if c['matched'] is not None else 'n/a'} | {ex} |")
    with open(os.path.join(OUT_DIR, "etl_mapping.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
