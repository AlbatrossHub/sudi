"""Phase 'chatter' (SQL lane): attachments + filestore, messages, recipients, followers,
tracking values (module mail_tracking, added to the target set), notifications, mails, sms,
reactions. Adapted from the marut ETL — 19→20 has ZERO column drift on mail_message and
ir_attachment, so most copies are verbatim. Runs after every business phase."""
import logging
import os
import shutil

from ..core import SqlStep, TableCopy, FuncStep, IDM
from .. import config

_logger = logging.getLogger("etl.chatter")
S = config.SRC_SCHEMA
COMPANY = "(SELECT id FROM res_company ORDER BY id LIMIT 1)"


def _maps(engine, ctx):
    """etl_idmap rows for reference models matched by xmlid / name: subtypes, activity types, fields, models."""
    cr = engine.cr
    n = {}
    for model, table, namecol, extra in (
        ("mail.message.subtype", "mail_message_subtype", "name", "AND coalesce(a.res_model,'') = coalesce(b.res_model,'')"),
        ("mail.activity.type", "mail_activity_type", "name", ""),
    ):
        cr.execute(f"""INSERT INTO etl_idmap (model, old_id, new_id)
                       SELECT %s, d18.res_id, d20.res_id FROM {S}.ir_model_data d18
                       JOIN ir_model_data d20 ON d20.module = d18.module AND d20.name = d18.name AND d20.model = %s
                       WHERE d18.model = %s AND d18.module NOT IN ('__export__') ON CONFLICT DO NOTHING""",
                   (model, model, model))
        x = cr.rowcount
        cr.execute(f"""INSERT INTO etl_idmap (model, old_id, new_id)
                       SELECT %s, a.id, min(b.id) FROM {S}.{table} a JOIN {table} b
                         ON b.{namecol}->>'en_US' = a.{namecol}->>'en_US' {extra}
                       WHERE a.id NOT IN (SELECT old_id FROM etl_idmap WHERE model = %s) GROUP BY a.id ON CONFLICT DO NOTHING""",
                   (model, model))
        n[model] = (x, cr.rowcount)
    cr.execute(f"""INSERT INTO etl_idmap (model, old_id, new_id)
                   SELECT 'ir.model.fields', a.id, b.id FROM {S}.ir_model_fields a
                   JOIN ir_model_fields b ON b.model = a.model AND b.name = a.name ON CONFLICT DO NOTHING""")
    n["ir.model.fields"] = cr.rowcount
    cr.execute(f"""INSERT INTO etl_idmap (model, old_id, new_id)
                   SELECT 'ir.model', a.id, b.id FROM {S}.ir_model a JOIN ir_model b ON b.model = a.model ON CONFLICT DO NOTHING""")
    n["ir.model"] = cr.rowcount
    for m in ("mail.message.subtype", "mail.activity.type", "ir.model.fields", "ir.model"):
        engine.idmap._cache.pop(m, None)
    return n


MAPS = FuncStep("chatter.maps", _maps)

# ir.ui.view attachments (website images!) resolve through the idmap filled by the website phase.
ATTACHMENTS = TableCopy(
    "ir.attachment", "ir_attachment",
    joins=["LEFT JOIN etl_idmap m ON m.model = s.res_model AND m.old_id = s.res_id"],
    where="(s.res_model IS NULL OR s.res_id IS NULL OR s.res_id = 0 OR m.new_id IS NOT NULL)"
          " AND coalesce(s.res_model, '') NOT IN ('ir.ui.menu', 'ir.module.module', 'mail.compose.message')"
          " AND coalesce(s.name, '') NOT LIKE '/web/assets/%'",
    overrides={"id": "s.id + {OFF}", "res_id": "CASE WHEN s.res_id IS NULL OR s.res_id = 0 THEN s.res_id ELSE m.new_id END",
               "company_id": f"CASE WHEN s.company_id IS NOT NULL THEN {COMPANY} END",
               "original_id": "NULL", "website_id": "CASE WHEN s.website_id IS NOT NULL THEN 1 END",
               "theme_template_id": "NULL"},
    register=("ir.attachment", "ir_attachment"), chunk=50000, on_conflict="DO NOTHING",
)

# PDF preview thumbnails hang off another attachment (res_model 'ir.attachment'): the step above
# resolves parents through the id map it is still filling, so they follow once it is complete
# (found by the per-category count of attachments left behind: 89 thumbnails)
ATTACHMENT_THUMBS = TableCopy(
    "ir.attachment.thumbnails", "ir_attachment",
    joins=["JOIN etl_idmap m ON m.model = 'ir.attachment' AND m.old_id = s.res_id"],
    where="s.res_model = 'ir.attachment'",
    overrides=dict(ATTACHMENTS.overrides, res_id="m.new_id"),
    register=("ir.attachment", "ir_attachment"), chunk=50000, on_conflict="DO NOTHING",
)


def _filestore(engine, ctx):
    """Hard-link every migrated attachment file from the staged 19 filestore (same disk)."""
    env, cr = engine.env, engine.cr
    dst_root = env["ir.attachment"]._filestore()
    cr.execute(f"SELECT DISTINCT store_fname FROM ir_attachment WHERE id > {config.OFFSET} AND store_fname IS NOT NULL")
    names = [r[0] for r in cr.fetchall()]
    linked = existed = missing = 0
    for fname in names:
        src = os.path.join(config.FILESTORE_SRC, fname); dst = os.path.join(dst_root, fname)
        if os.path.exists(dst):
            existed += 1; continue
        if not os.path.exists(src):
            missing += 1; continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        linked += 1
    if missing:
        _logger.warning("filestore: %d attachment files missing in %s", missing, config.FILESTORE_SRC)
    return dict(files=len(names), linked=linked, existed=existed, missing=missing, filestore=dst_root)


FILESTORE = FuncStep("chatter.filestore", _filestore)

MESSAGES = TableCopy(
    "mail.message", "mail_message",
    joins=["LEFT JOIN etl_idmap m ON m.model = s.model AND m.old_id = s.res_id",
           IDM("pa", "res.partner", "s.author_id"), IDM("st", "mail.message.subtype", "s.subtype_id"),
           IDM("at", "mail.activity.type", "s.mail_activity_type_id")],
    where="s.model IS NULL OR m.new_id IS NOT NULL",
    overrides={"id": "s.id + {OFF}", "parent_id": "NULL", "res_id": "m.new_id", "model": "s.model",
               "author_id": "pa.new_id", "author_guest_id": "NULL", "subtype_id": "st.new_id",
               "mail_activity_type_id": "at.new_id", "mail_server_id": "NULL",
               "record_company_id": f"CASE WHEN s.record_company_id IS NOT NULL THEN {COMPANY} END",
               "record_alias_domain_id": "NULL", "body": "etl_shift_urls(s.body, {OFF})"},
    register=("mail.message", "mail_message"), chunk=200000, on_conflict="DO NOTHING",
)


def _message_parents(engine, ctx):
    cr = engine.cr
    cr.execute(f"""UPDATE mail_message t SET parent_id = s.parent_id + {config.OFFSET}
                   FROM {S}.mail_message s
                   WHERE t.id = s.id + {config.OFFSET} AND s.parent_id IS NOT NULL AND t.parent_id IS NULL
                     AND EXISTS (SELECT 1 FROM mail_message x WHERE x.id = s.parent_id + {config.OFFSET})""")
    n = cr.rowcount
    cr.execute(f"""SELECT s.model, count(*) FROM {S}.mail_message s
                   WHERE NOT EXISTS (SELECT 1 FROM mail_message t WHERE t.id = s.id + {config.OFFSET})
                   GROUP BY 1 ORDER BY 2 DESC LIMIT 30""")
    dropped = {m or "(none)": c for m, c in cr.fetchall()}
    return dict(parents_linked=n, messages_dropped_by_model=dropped)


MESSAGE_PARENTS = FuncStep("mail.message.parents", _message_parents)

RECIPIENTS = SqlStep("mail.message.recipients", f"""
    INSERT INTO mail_message_res_partner_rel (mail_message_id, res_partner_id)
    SELECT r.mail_message_id + {{OFF}}, p.new_id FROM {{S}}.mail_message_res_partner_rel r
    JOIN mail_message t ON t.id = r.mail_message_id + {{OFF}}
    JOIN etl_idmap p ON p.model = 'res.partner' AND p.old_id = r.res_partner_id
    ON CONFLICT DO NOTHING""")

FOLLOWERS = TableCopy(
    "mail.followers", "mail_followers",
    joins=["LEFT JOIN etl_idmap m ON m.model = s.res_model AND m.old_id = s.res_id",
           IDM("p", "res.partner", "s.partner_id")],
    where="m.new_id IS NOT NULL AND p.new_id IS NOT NULL",
    overrides={"id": "s.id + {OFF}", "res_id": "m.new_id", "res_model": "s.res_model", "partner_id": "p.new_id"},
    on_conflict="DO NOTHING", register=("mail.followers", "mail_followers"), chunk=200000,
)
FOLLOWER_SUBTYPES = SqlStep("mail.followers.subtypes", f"""
    INSERT INTO mail_followers_mail_message_subtype_rel (mail_followers_id, mail_message_subtype_id)
    SELECT r.mail_followers_id + {{OFF}}, st.new_id FROM {{S}}.mail_followers_mail_message_subtype_rel r
    JOIN mail_followers f ON f.id = r.mail_followers_id + {{OFF}}
    JOIN etl_idmap st ON st.model = 'mail.message.subtype' AND st.old_id = r.mail_message_subtype_id
    ON CONFLICT DO NOTHING""")

TRACKING = TableCopy(
    "mail.tracking.value", "mail_tracking_value",
    joins=["JOIN mail_message t ON t.id = s.mail_message_id + {OFF}", IDM("f", "ir.model.fields", "s.field_id"),
           "LEFT JOIN {S}.ir_model_fields fld ON fld.id = s.field_id"],
    overrides={"id": "s.id + {OFF}", "mail_message_id": "t.id", "field_id": "f.new_id"},
    extra={"field_info": "CASE WHEN f.new_id IS NULL AND fld.id IS NOT NULL THEN jsonb_build_object('desc', fld.field_description->>'en_US', "
                         "'name', fld.name, 'sequence', 100, 'type', fld.ttype) END"},
    exclude={"currency_id"}, chunk=200000, on_conflict="DO NOTHING",
)

MAILS = TableCopy(
    "mail.mail", "mail_mail",
    joins=["JOIN mail_message t ON t.id = s.mail_message_id + {OFF}"],
    overrides={"id": "s.id + {OFF}", "mail_message_id": "t.id", "fetchmail_server_id": "NULL", "mailing_id": "NULL"},
    register=("mail.mail", "mail_mail"), chunk=50000, on_conflict="DO NOTHING",
)

SMS = TableCopy(
    "sms.sms", "sms_sms",
    joins=["JOIN mail_message t ON t.id = s.mail_message_id + {OFF}", IDM("pa", "res.partner", "s.partner_id")],
    overrides={"id": "s.id + {OFF}", "mail_message_id": "t.id", "partner_id": "pa.new_id", "sms_tracker_id": "NULL"},
    register=("sms.sms", "sms_sms"), chunk=50000, on_conflict="DO NOTHING",
)

NOTIFICATIONS = TableCopy(
    "mail.notification", "mail_notification",
    joins=["JOIN mail_message t ON t.id = s.mail_message_id + {OFF}", IDM("pa", "res.partner", "s.author_id"),
           IDM("rp", "res.partner", "s.res_partner_id"), "LEFT JOIN mail_mail mm ON mm.id = s.mail_mail_id + {OFF}",
           "LEFT JOIN sms_sms ss ON ss.id = s.sms_id_int + {OFF}"],
    overrides={"id": "s.id + {OFF}", "mail_message_id": "t.id", "author_id": "pa.new_id", "res_partner_id": "rp.new_id",
               "mail_mail_id": "mm.id", "sms_id_int": "ss.id", "letter_id": "NULL"},
    chunk=200000, on_conflict="DO NOTHING",
)

# recipients of the queued/sent e-mails (the m2m beside mail_mail; found by the full-table sweep)
MAIL_RECIPIENTS = SqlStep("mail.mail.recipients", f"""
    INSERT INTO mail_mail_res_partner_rel (mail_mail_id, res_partner_id)
    SELECT r.mail_mail_id + {{OFF}}, p.new_id FROM {{S}}.mail_mail_res_partner_rel r
    JOIN mail_mail t ON t.id = r.mail_mail_id + {{OFF}}
    JOIN etl_idmap p ON p.model = 'res.partner' AND p.old_id = r.res_partner_id
    ON CONFLICT DO NOTHING""")

# delivery-report handle of each SMS (sms_uuid -> notification), so a late report still lands
SMS_TRACKERS = TableCopy(
    "sms.tracker", "sms_tracker",
    joins=["LEFT JOIN mail_notification n ON n.id = s.mail_notification_id + {OFF}"],
    overrides={"id": "s.id + {OFF}", "mail_notification_id": "n.id"}, on_conflict="DO NOTHING",
)

# scheduled activities on migrated records (5 of prod's 6 sit on pickings deleted since)
ACTIVITIES = TableCopy(
    "mail.activity", "mail_activity",
    joins=["JOIN etl_idmap m ON m.model = s.res_model AND m.old_id = s.res_id", IDM("im", "ir.model", "s.res_model_id"),
           IDM("at", "mail.activity.type", "s.activity_type_id"), IDM("au", "res.users", "s.user_id")],
    overrides={"id": "s.id + {OFF}", "res_id": "m.new_id", "res_model_id": "im.new_id", "activity_type_id": "at.new_id",
               "user_id": "au.new_id"},
    register=("mail.activity", "mail_activity"), on_conflict="DO NOTHING",
)

MSG_ATTACHMENTS = SqlStep("mail.message.attachments", f"""
    INSERT INTO message_attachment_rel (message_id, attachment_id)
    SELECT r.message_id + {{OFF}}, r.attachment_id + {{OFF}} FROM {{S}}.message_attachment_rel r
    JOIN mail_message t ON t.id = r.message_id + {{OFF}}
    JOIN ir_attachment a ON a.id = r.attachment_id + {{OFF}}
    ON CONFLICT DO NOTHING""")

REACTIONS = TableCopy(
    "mail.message.reaction", "mail_message_reaction",
    joins=["JOIN mail_message t ON t.id = s.message_id + {OFF}", IDM("p", "res.partner", "s.partner_id")],
    where="p.new_id IS NOT NULL",
    overrides={"id": "s.id + {OFF}", "message_id": "t.id", "partner_id": "p.new_id", "guest_id": "NULL"},
    on_conflict="DO NOTHING",
)

# 20 renders tracked values from the message body (message_type 'tracking'); 19 stored them
# only in the side table with an empty body -> rendered here in SQL, in 19's display rules.
TRACKING_BODIES = SqlStep("chatter.tracking_bodies", f"""
    CREATE OR REPLACE FUNCTION etl_html(t text) RETURNS text LANGUAGE sql IMMUTABLE AS
      $$ SELECT replace(replace(replace(coalesce(t, ''), '&', '&amp;'), '<', '&lt;'), '>', '&gt;') $$;
    WITH tv AS (
      SELECT v.mail_message_id,
             coalesce(f.tracking, (v.field_info->>'sequence')::int, 100) AS seq, v.id,
             coalesce(f.field_description->>'en_US', v.field_info->>'desc', f.name, v.field_info->>'name', '?') AS label,
             coalesce(f.ttype, v.field_info->>'type', 'char') AS ttype,
             v.old_value_char, v.new_value_char, v.old_value_text, v.new_value_text, v.old_value_integer,
             v.new_value_integer, v.old_value_float, v.new_value_float, v.old_value_datetime, v.new_value_datetime
      FROM mail_tracking_value v LEFT JOIN ir_model_fields f ON f.id = v.field_id
      WHERE v.mail_message_id - {{OFF}} BETWEEN {{LO}} AND {{HI}}
    ), disp AS (
      SELECT mail_message_id, seq, id, label,
             CASE ttype WHEN 'boolean' THEN CASE WHEN old_value_integer = 1 THEN 'Yes' WHEN old_value_integer = 0 THEN 'No' END
                        WHEN 'integer' THEN old_value_integer::text
                        WHEN 'float' THEN to_char(old_value_float, 'FM999,999,999,999,990.00')
                        WHEN 'monetary' THEN to_char(old_value_float, 'FM999,999,999,999,990.00')
                        WHEN 'date' THEN to_char(old_value_datetime, 'MM/DD/YYYY')
                        WHEN 'datetime' THEN to_char(old_value_datetime, 'MM/DD/YYYY HH24:MI:SS')
                        WHEN 'text' THEN old_value_text ELSE old_value_char END AS old_disp,
             CASE ttype WHEN 'boolean' THEN CASE WHEN new_value_integer = 1 THEN 'Yes' WHEN new_value_integer = 0 THEN 'No' END
                        WHEN 'integer' THEN new_value_integer::text
                        WHEN 'float' THEN to_char(new_value_float, 'FM999,999,999,999,990.00')
                        WHEN 'monetary' THEN to_char(new_value_float, 'FM999,999,999,999,990.00')
                        WHEN 'date' THEN to_char(new_value_datetime, 'MM/DD/YYYY')
                        WHEN 'datetime' THEN to_char(new_value_datetime, 'MM/DD/YYYY HH24:MI:SS')
                        WHEN 'text' THEN new_value_text ELSE new_value_char END AS new_disp
      FROM tv
    ), html AS (
      SELECT mail_message_id,
             '<div>' || string_agg(
               etl_html(coalesce(nullif(old_disp, ''), 'None')) || ' → <b>' || etl_html(coalesce(nullif(new_disp, ''), 'None'))
               || '</b> <i>(' || etl_html(label) || ')</i>',
               '<br>' ORDER BY seq, id) || '</div>' AS body
      FROM disp GROUP BY mail_message_id
    )
    UPDATE mail_message m SET
      body = CASE WHEN m.body IS NULL OR m.body IN ('', '<p></p>', '<p><br></p>', '<p><br/></p>') THEN h.body ELSE m.body || h.body END,
      message_type = 'tracking'
    FROM html h WHERE m.id = h.mail_message_id AND m.message_type <> 'tracking'""",
    chunk=("mail_message", 200000))

# the ORM creates an empty placeholder binary per image field on create; the copied 19 file
# then coexists with it and the older (native) row wins the (res_model, res_id, res_field) lookup
BINARY_DEDUP = SqlStep("chatter.binary_dedup", f"""
    DELETE FROM ir_attachment a WHERE a.res_field IS NOT NULL AND a.id < {{OFF}}
      AND EXISTS (SELECT 1 FROM ir_attachment b WHERE b.res_model = a.res_model AND b.res_id = a.res_id
                  AND b.res_field = a.res_field AND b.id >= {{OFF}})""")

SEQ_TABLES = ["ir_attachment", "mail_message", "mail_followers", "mail_tracking_value", "mail_notification",
              "mail_mail", "mail_message_reaction", "sms_sms", "sms_tracker", "mail_activity"]


def _sequences(engine, ctx):
    cr = engine.cr
    for t in SEQ_TABLES:
        cr.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), GREATEST((SELECT coalesce(max(id), 1) FROM {t}), 1))")
    return dict(tables=len(SEQ_TABLES))


SEQUENCES = FuncStep("chatter.sequences", _sequences)

STEPS = [MAPS, ATTACHMENTS, ATTACHMENT_THUMBS, FILESTORE, MESSAGES, MESSAGE_PARENTS, RECIPIENTS, FOLLOWERS, FOLLOWER_SUBTYPES,
         TRACKING, MAILS, MAIL_RECIPIENTS, SMS, NOTIFICATIONS, SMS_TRACKERS, MSG_ATTACHMENTS, REACTIONS, ACTIVITIES, TRACKING_BODIES, BINARY_DEDUP, SEQUENCES]
