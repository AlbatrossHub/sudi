"""Phase 'settings' (before chatter, so their chatter and attachments follow): module
configuration prod edited in the database -- the installable office app's branding
(pwa.config: name, colours, icons) and the IAP accounts (SMS credits, partner autocomplete).
Both were found by the full-table sweep of the 2026-10-26 prod pull: 20 showed the module
defaults instead."""
import logging

from ..core import FuncStep, TableCopy, SqlStep
from .. import config

_logger = logging.getLogger("etl.settings")
S = config.SRC_SCHEMA
COMPANY = "(SELECT id FROM res_company ORDER BY id LIMIT 1)"
NEVER_COPIED = {"id", "create_uid", "write_uid", "company_id"}


def copy_onto_xmlid(engine, model, table):
    """Prod's row of a module-seeded record lands ON the row the module seeded on 20 (same
    xmlid): every shared column is written, and the id map points chatter/attachments at it."""
    cr = engine.cr
    cr.execute("select column_name from information_schema.columns where table_schema='public' and table_name=%s",
               (table,))
    cols20 = {r[0] for r in cr.fetchall()}
    cols = [c for c in engine.src.columns(table) if c in cols20 and c not in NEVER_COPIED]
    cr.execute(f"""SELECT s.res_id, t.res_id FROM {S}.ir_model_data s
                   JOIN ir_model_data t ON t.model = s.model AND t.module = s.module AND t.name = s.name
                   WHERE s.model = %s""", (model,))
    pairs = cr.fetchall()
    sets = ", ".join(f'"{c}" = s."{c}"' for c in cols)
    for old, new in pairs:
        cr.execute(f"UPDATE {table} t SET {sets} FROM {S}.{table} s WHERE s.id = %s AND t.id = %s", (old, new))
    engine.idmap.add_many(model, pairs)
    return dict(mapped=len(pairs), columns=len(cols))


PWA = FuncStep("pwa.config", lambda engine, ctx: copy_onto_xmlid(engine, "pwa.config", "pwa_config"))

# tokens are blanked by the dry-run guard outside a production run (a test DB must not spend credits)
IAP_ACCOUNTS = TableCopy(
    "iap.account", "iap_account",
    joins=["JOIN {S}.iap_service ss ON ss.id = s.service_id", "JOIN iap_service ts ON ts.technical_name = ss.technical_name"],
    overrides={"id": "s.id + {OFF}", "service_id": "ts.id"},
    register=("iap.account", "iap_account"), on_conflict="DO NOTHING",
)
IAP_RELS = SqlStep("iap.account.rels", f"""
    INSERT INTO iap_account_res_company_rel (iap_account_id, res_company_id)
    SELECT a.id, {COMPANY} FROM {{S}}.iap_account_res_company_rel r
    JOIN iap_account a ON a.id = r.iap_account_id + {{OFF}}
    ON CONFLICT DO NOTHING;
    INSERT INTO iap_account_res_users_rel (iap_account_id, res_users_id)
    SELECT a.id, u.new_id FROM {{S}}.iap_account_res_users_rel r
    JOIN iap_account a ON a.id = r.iap_account_id + {{OFF}}
    JOIN etl_idmap u ON u.model = 'res.users' AND u.old_id = r.res_users_id
    ON CONFLICT DO NOTHING""")

STEPS = [PWA, IAP_ACCOUNTS, IAP_RELS]
