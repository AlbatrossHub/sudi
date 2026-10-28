"""Diamond job-work data. REF_STEPS (job types) run before the stock phase (pickings/moves
FK them at +OFFSET). STEPS run after the account phase and also apply diamond's own pending
1.2.3 -> 1.7.0 migrations (prod never ran them): billing m2m links, notify-param -> user
flags, job-work group carryover (see /opt/odoo19/custom/sudi/diamond/migrations)."""
import logging
import os

from ..core import FuncStep, TableCopy, SqlStep, IDM  # noqa: F401
from .. import config

_logger = logging.getLogger("etl.diamond")
S = config.SRC_SCHEMA
COMPANY = "(SELECT id FROM res_company ORDER BY id LIMIT 1)"

def _job_types(engine, ctx):
    """diamond ships the 15 job types as module data (identical on both sides): map by name,
    copy any prod-only ones."""
    cr = engine.cr
    cr.execute(f"""INSERT INTO etl_idmap (model, old_id, new_id)
                   SELECT 'sudi.diamond.job.type', a.id, min(b.id)
                   FROM {S}.sudi_diamond_job_type a JOIN sudi_diamond_job_type b
                     ON b.name->>'en_US' = a.name->>'en_US'
                   GROUP BY a.id ON CONFLICT DO NOTHING""")
    mapped = cr.rowcount
    cr.execute(f"""SELECT count(*) FROM {S}.sudi_diamond_job_type a
                   WHERE NOT EXISTS (SELECT 1 FROM etl_idmap m
                                     WHERE m.model = 'sudi.diamond.job.type' AND m.old_id = a.id)""")
    unmatched = cr.fetchone()[0]
    engine.idmap._cache.pop("sudi.diamond.job.type", None)
    return dict(mapped=mapped, unmatched=unmatched)


JOB_TYPES = FuncStep("sudi.diamond.job.type", _job_types)
JOB_TYPE_TAXES = SqlStep("sudi.job.type.taxes", f"""
    INSERT INTO sudi_diamond_job_type_account_tax_rel (job_type_id, tax_id)
    SELECT j.new_id, t.new_id FROM {{S}}.sudi_diamond_job_type_account_tax_rel r
    JOIN etl_idmap j ON j.model = 'sudi.diamond.job.type' AND j.old_id = r.job_type_id
    JOIN etl_idmap t ON t.model = 'account.tax' AND t.old_id = r.tax_id
    ON CONFLICT DO NOTHING""")

REF_STEPS = [JOB_TYPES]

SERVICE_PRICES = TableCopy(
    "sudi.diamond.partner.service.price", "sudi_diamond_partner_service_price",
    joins=[IDM("pa", "res.partner", "s.partner_id"), IDM("jt", "sudi.diamond.job.type", "s.job_type_id")],
    where="pa.new_id IS NOT NULL",
    overrides={"id": "s.id + {OFF}", "partner_id": "pa.new_id", "job_type_id": "jt.new_id",
               "company_id": COMPANY},
    register=("sudi.diamond.partner.service.price", "sudi_diamond_partner_service_price"), chunk=50000,
)

BILLING_LINES = TableCopy(
    "sudi.diamond.billing.line", "sudi_diamond_billing_line",
    joins=[IDM("jt", "sudi.diamond.job.type", "s.job_type_id")],
    # invoice_line_id (the pre-1.3.0 m2o) is not a column on 20: BILLING_M2M below carries the link
    overrides={"id": "s.id + {OFF}", "picking_id": "s.picking_id + {OFF}", "receipt_move_id": "s.receipt_move_id + {OFF}",
               "job_type_id": "jt.new_id", "company_id": COMPANY},
    register=("sudi.diamond.billing.line", "sudi_diamond_billing_line"), chunk=50000,
)

def _billing_m2m(engine, ctx):
    """Billing line <-> invoice line links. A source still on diamond < 1.3.0 (raw prod) holds them
    as the m2o sudi_diamond_billing_line.invoice_line_id (migration 19.0.1.3.0 pending); one already
    on 1.7.0 (the local dev copy) holds them in the m2m table itself."""
    cr = engine.cr
    if engine.src.table_exists("sudi_billing_line_account_move_line_rel"):
        src = f"""SELECT r.billing_line_id AS bl, r.invoice_line_id AS il
                  FROM {S}.sudi_billing_line_account_move_line_rel r"""
    else:
        src = f"""SELECT s.id AS bl, s.invoice_line_id AS il
                  FROM {S}.sudi_diamond_billing_line s WHERE s.invoice_line_id IS NOT NULL"""
    cr.execute(f"""INSERT INTO sudi_billing_line_account_move_line_rel (billing_line_id, invoice_line_id)
                   SELECT x.bl + {config.OFFSET}, il.new_id FROM ({src}) x
                   JOIN etl_idmap il ON il.model = 'account.move.line' AND il.old_id = x.il
                   JOIN sudi_diamond_billing_line bl ON bl.id = x.bl + {config.OFFSET}
                   ON CONFLICT DO NOTHING""")
    return dict(links=cr.rowcount)


BILLING_M2M = FuncStep("sudi.billing.m2m", _billing_m2m)

PICKING_JOB_TYPES = SqlStep("sudi.picking.job_types", f"""
    INSERT INTO sudi_picking_job_type_involved_rel (picking_id, job_type_id)
    SELECT r.picking_id + {{OFF}}, j.new_id FROM {{S}}.sudi_picking_job_type_involved_rel r
    JOIN stock_picking p ON p.id = r.picking_id + {{OFF}}
    JOIN etl_idmap j ON j.model = 'sudi.diamond.job.type' AND j.old_id = r.job_type_id
    ON CONFLICT DO NOTHING""")

# account.move.sudi_receipt_id (m2o, both sides) also feeds 1.7.0's m2m helper table
MOVE_RECEIPT_M2M = SqlStep("sudi.move.receipt_m2m", f"""
    INSERT INTO sudi_account_move_receipt_rel (move_id, picking_id)
    SELECT m.new_id, s.sudi_receipt_id + {{OFF}} FROM {{S}}.account_move s
    JOIN etl_idmap m ON m.model = 'account.move' AND m.old_id = s.id
    JOIN stock_picking p ON p.id = s.sudi_receipt_id + {{OFF}}
    WHERE s.sudi_receipt_id IS NOT NULL
    ON CONFLICT DO NOTHING""")

# stock_move.sudi_invoice_line_id was NULLed in the stock phase (account ran later): link now
MOVE_INVOICE_LINES = SqlStep("sudi.move.invoice_lines", f"""
    UPDATE stock_move t SET sudi_invoice_line_id = il.new_id
    FROM {{S}}.stock_move s JOIN etl_idmap il ON il.model = 'account.move.line' AND il.old_id = s.sudi_invoice_line_id
    WHERE t.id = s.id + {{OFF}} AND s.sudi_invoice_line_id IS NOT NULL""")

def _move_billing_links(engine, ctx):
    """stock_move.sudi_billing_line_id was NULLed in the stock phase (billing lines load here).
    Only a source already on diamond >= 1.4 has the column. Reference statements are not carried:
    the tables are empty on every source so far — refuse loudly if that ever changes."""
    cr, info = engine.cr, {}
    if "sudi_billing_line_id" in engine.src.columns("stock_move"):
        cr.execute(f"""UPDATE stock_move t SET sudi_billing_line_id = s.sudi_billing_line_id + {config.OFFSET}
                       FROM {S}.stock_move s
                       JOIN sudi_diamond_billing_line bl ON bl.id = s.sudi_billing_line_id + {config.OFFSET}
                       WHERE t.id = s.id + {config.OFFSET} AND s.sudi_billing_line_id IS NOT NULL""")
        info["billing_links"] = cr.rowcount
    for table in ("sudi_diamond_reference_statement", "sudi_diamond_reference_line", "sudi_diamond_billing_log"):
        if engine.src.table_exists(table):
            cr.execute(f"SELECT count(*) FROM {S}.{table}")
            n = cr.fetchone()[0]
            if n:
                raise RuntimeError(f"{table} has {n} rows on the source: add a copy step before migrating it")
    return info


MOVE_BILLING_LINKS = FuncStep("sudi.move.billing_links", _move_billing_links)

# diamond ships default templates (unique on event+company): prod's edited body/name/active win.
SUDI_WA_TEMPLATES = SqlStep("sudi.whatsapp.template", f"""
    UPDATE sudi_whatsapp_template t
       SET name = s.name, body = s.body, active = s.active, sequence = s.sequence,
           create_date = s.create_date, write_date = s.write_date
      FROM {{S}}.sudi_whatsapp_template s WHERE t.event = s.event;
    INSERT INTO sudi_whatsapp_template (id, sequence, company_id, create_uid, write_uid, name, event, body,
                                        active, create_date, write_date)
    SELECT s.id + {{OFF}}, s.sequence, {COMPANY}, 1, 1, s.name, s.event, s.body, s.active,
           s.create_date, s.write_date
      FROM {{S}}.sudi_whatsapp_template s
     WHERE NOT EXISTS (SELECT 1 FROM sudi_whatsapp_template t WHERE t.event = s.event)
    ON CONFLICT DO NOTHING;
    INSERT INTO etl_idmap (model, old_id, new_id)
    SELECT 'sudi.whatsapp.template', s.id, t.id FROM {{S}}.sudi_whatsapp_template s
    JOIN sudi_whatsapp_template t ON t.event = s.event ON CONFLICT DO NOTHING""")


def _pending_migrations(engine, ctx):
    """diamond 19.0.1.2.0-1.2.2 + 1.4.0 equivalents (params -> user flags, group carryover)."""
    env = engine.env
    ICP = env["ir.config_parameter"].sudo()
    Users = env["res.users"].sudo()
    cr = engine.cr
    flags = 0
    for param_key, field_name in (("diamond.sudi_pickup_notify_user_id", "sudi_notify_pickup_scheduled"),
                                  ("diamond.sudi_pickup_confirmed_notify_user_id", "sudi_notify_pickup_confirmed")):
        cr.execute(f"SELECT value FROM {S}.ir_config_parameter WHERE key = %s", (param_key,))
        r = cr.fetchone()
        if not r:
            continue
        try:
            old_uid = int(r[0])
        except (TypeError, ValueError):
            continue
        new_uid = engine.idmap.get("res.users", old_uid)
        user = Users.browse(new_uid).exists() if new_uid else None
        if user and field_name in user._fields:
            user.write({field_name: True}); flags += 1
    granted = {}
    for group_xml, flag_domain in (("diamond.group_sudi_pickup_delivery_operator", [("sudi_notify_pickup_scheduled", "=", True)]),
                                   ("diamond.group_sudi_pickup_app", [("sudi_notify_pickup_scheduled", "=", True)])):
        group = env.ref(group_xml, raise_if_not_found=False)
        if not group:
            continue
        users = Users.search(flag_domain + [("active", "=", True), ("share", "=", False)])
        add = users - group.all_user_ids
        if add:
            group.write({"user_ids": [(4, u.id) for u in add]})
        granted[group_xml] = len(add)
    # 1.4.0: every pickup/delivery operator keeps the Job Work app
    operator = env.ref("diamond.group_sudi_pickup_delivery_operator", raise_if_not_found=False)
    job_work = env.ref("diamond.group_sudi_job_work_user", raise_if_not_found=False)
    carried = 0
    if operator and job_work:
        users = operator.all_user_ids - job_work.all_user_ids
        if users:
            job_work.write({"user_ids": [(4, u.id) for u in users]})
            carried = len(users)
    return dict(notify_flags=flags, groups_granted=granted, job_work_carryover=carried)


PENDING_MIGRATIONS = FuncStep("diamond.pending_migrations", _pending_migrations)


# the sudi addons in this repo (diamond, ...): their stored computes are what a source on an older
# diamond lacks
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SUDI_MODULES = {d for d in os.listdir(REPO) if os.path.isfile(os.path.join(REPO, d, "__manifest__.py"))}


def _recompute(engine, ctx):
    """Stored computes on the SQL-lane rows: every one a sudi module defines whose column the
    source lacks. On a diamond 1.2.3 source that is 1.7.0's billing values/state on moves and
    billing status / delivery stage on pickings -- the module upgrade computes them for existing
    rows, a SQL copy does not (found on the phone: the delivery list was empty), plus pcs/carats."""
    env, cr = engine.env, engine.cr
    env.invalidate_all()
    out = {}
    for model in ("stock.move.line", "stock.move", "stock.picking"):   # pickings read their moves
        M = env[model]
        src = engine.src.columns(M._table)
        names = sorted(name for name, f in M._fields.items()
                       if f.store and f.compute and f.column_type and f._module in SUDI_MODULES
                       and (name not in src or name in ("sudi_total_pcs", "sudi_total_carats")))
        if not names:
            continue
        cr.execute(f"SELECT id + {config.OFFSET} FROM {S}.{M._table}")
        records = M.browse([r[0] for r in cr.fetchall()]).exists()
        for name in names:
            env.add_to_compute(M._fields[name], records)
        M.flush_model()
        out[model] = dict(records=len(records), fields=names)
    for t in ("sudi_diamond_job_type", "sudi_diamond_partner_service_price", "sudi_diamond_billing_line",
              "sudi_whatsapp_template"):
        cr.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), GREATEST((SELECT coalesce(max(id), 1) FROM {t}), 1))")
    return out


RECOMPUTE = FuncStep("diamond.recompute", _recompute)

# the res.partner create() hook auto-builds a default price grid for every partner the
# ORM lane creates; prod's real grid then lands on top -> duplicates per (partner, job
# type). Prod's rows (in the idmap) are the truth; the hook refills for future partners.
PRICE_PURGE = SqlStep("sudi.service_price.purge_autogrid", """
    DELETE FROM sudi_diamond_partner_service_price p
    WHERE NOT EXISTS (SELECT 1 FROM etl_idmap m
                      WHERE m.model = 'sudi.diamond.partner.service.price' AND m.new_id = p.id)""")

def _attachment_rels(engine, ctx):
    """Multi-page jangad (pages 2+) and proof-of-delivery photos: picking <-> ir.attachment m2m
    tables added by diamond 1.4.0. Both ends are SQL-lane rows (+OFFSET); absent on an older source."""
    cr, done = engine.cr, {}
    for table in ("sudi_picking_jangad_attachment_rel", "sudi_picking_pod_attachment_rel"):
        if not engine.src.table_exists(table):
            continue
        cr.execute(f"""INSERT INTO {table} (picking_id, attachment_id)
                       SELECT r.picking_id + {config.OFFSET}, r.attachment_id + {config.OFFSET}
                       FROM {S}.{table} r
                       JOIN stock_picking p ON p.id = r.picking_id + {config.OFFSET}
                       JOIN ir_attachment a ON a.id = r.attachment_id + {config.OFFSET}
                       ON CONFLICT DO NOTHING""")
        done[table] = cr.rowcount
    return done


ATTACHMENT_RELS = FuncStep("diamond.attachment_rels", _attachment_rels)
POST_CHATTER_STEPS = [ATTACHMENT_RELS]

STEPS = [JOB_TYPE_TAXES, SERVICE_PRICES, PRICE_PURGE, BILLING_LINES, BILLING_M2M, MOVE_BILLING_LINKS, PICKING_JOB_TYPES, MOVE_RECEIPT_M2M,
         MOVE_INVOICE_LINES, SUDI_WA_TEMPLATES, PENDING_MIGRATIONS, RECOMPUTE]
