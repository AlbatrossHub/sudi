"""Phase 'post': config parameters, export xmlids, ir.filters, sequence counters, the two
DB-created "Send WhatsApp" server actions, company details."""
import logging

from psycopg2.extras import Json

from ..core import OrmStep, FuncStep, SqlStep
from .. import config

_logger = logging.getLogger("etl.post")
S = config.SRC_SCHEMA

EXPORT_XMLIDS = SqlStep("ir.model.data.__export__", f"""
    INSERT INTO ir_model_data (module, name, model, res_id, noupdate, create_uid, write_uid, create_date, write_date)
    SELECT d.module, d.name, d.model, m.new_id, true, 1, 1, d.create_date, d.write_date
    FROM {{S}}.ir_model_data d JOIN etl_idmap m ON m.model = d.model AND m.old_id = d.res_id
    WHERE d.module = '__export__' ON CONFLICT (module, name) DO NOTHING""")

PARAM_DENY = ("database.", "web.base.url", "mail.catchall", "mail.bounce", "mail.default.from", "base.login_cooldown",
              "auth_", "iap.", "web.push", "mail.web_push", "mail.session", "mail.restrict", "odoo_", "res.partner.",
              "report.", "web_tour", "mail.mail.queue", "base_setup.",
              "open_whatsapp_connector.default_sidecar_url",   # neutralized value must not overwrite the guard's
              "diamond.sudi_pickup_notify_user_id", "diamond.sudi_pickup_confirmed_notify_user_id")  # 1.2->1.7 migration


def _params(engine, ctx):
    """custom keys (owa, diamond, pwa branding, otp, hide_powered_by ...) that the fresh 20 lacks."""
    cr = engine.cr
    cr.execute(f"SELECT key, value FROM {S}.ir_config_parameter")
    src = dict(cr.fetchall())
    cr.execute("SELECT key FROM ir_config_parameter")
    have = {r[0] for r in cr.fetchall()}
    P = engine.env["ir.config_parameter"].sudo()
    copied = []
    for k, v in src.items():
        if k in have or any(k.startswith(p) for p in PARAM_DENY):
            continue
        P.set_str(k, v); copied.append(k)
    return dict(copied=sorted(copied))


PARAMS = FuncStep("ir.config_parameter", _params)


def _filter_transform(vals, row, ctx):
    if row.get("user_id"):
        u = ctx["idmap"].get("res.users", row["user_id"])
        vals["user_ids"] = [(6, 0, [u])] if u else [(6, 0, [])]
    vals.pop("user_id", None)
    if not vals.get("embedded_action_id"):
        vals.pop("embedded_parent_res_id", None)
    return vals


FILTERS = OrmStep("ir.filters", transform=_filter_transform, skip={"user_id"})


def _src_next(scr, impl, pg_seq, number_next):
    if impl == "standard":
        scr.execute(f"SELECT last_value, is_called FROM {pg_seq}")
        last, called = scr.fetchone()
        return last + 1 if called else last
    return number_next or 1


def _sequences(engine, ctx):
    """ir.sequence counters continue from the 19 counter, matched by code (incl. date ranges)."""
    import odoo.sql_db
    env = engine.env
    Seq = env["ir.sequence"].sudo().with_context(active_test=False)
    bumped = []
    with odoo.sql_db.db_connect(config.SRC_DB).cursor() as scr:
        scr.execute("SELECT id, code, implementation, number_next, use_date_range, prefix, suffix, padding, "
                    "number_increment FROM ir_sequence WHERE code IS NOT NULL AND active ORDER BY id")
        for sid, code, impl, nn, udr, prefix, suffix, padding, incr in scr.fetchall():
            seq = Seq.search([("code", "=", code)], limit=1)
            if not seq:
                continue
            seq.write({"prefix": prefix, "suffix": suffix, "padding": padding, "number_increment": incr,
                       "use_date_range": udr})
            if udr:
                scr.execute("SELECT id, date_from, date_to, number_next FROM ir_sequence_date_range WHERE sequence_id = %s", (sid,))
                for rid, df, dt, rnn in scr.fetchall():
                    nxt = _src_next(scr, impl, f"ir_sequence_{sid:03d}_{rid:03d}", rnn)
                    rng = seq.date_range_ids.filtered(lambda r: r.date_from == df and r.date_to == dt)
                    if not rng:
                        rng = env["ir.sequence.date_range"].sudo().create(
                            {"sequence_id": seq.id, "date_from": df, "date_to": dt})
                    if (rng.number_next_actual or 0) < nxt:
                        rng.write({"number_next_actual": nxt}); bumped.append(f"{code} {df:%Y}")
            else:
                nxt = _src_next(scr, impl, f"ir_sequence_{sid:03d}", nn)
                if (seq.number_next_actual or 0) < nxt:
                    seq.write({"number_next_actual": nxt}); bumped.append(code)
    return dict(bumped=bumped)


SEQUENCES = FuncStep("ir.sequence.counters", _sequences)

# picking-type sequences: prod continues its own numbering (WH/IN etc. matched via picking types)
PICKING_SEQ = FuncStep("stock.picking.sequences", lambda engine, ctx: _picking_seq(engine))


def _picking_seq(engine):
    cr, env = engine.cr, engine.env
    cr.execute(f"""SELECT pt.id, sq.prefix, sq.padding, sq.implementation, sq.number_next, sq.id
                   FROM {S}.stock_picking_type pt JOIN {S}.ir_sequence sq ON sq.id = pt.sequence_id""")
    import odoo.sql_db
    bumped = []
    with odoo.sql_db.db_connect(config.SRC_DB).cursor() as scr:
        for old_pt, prefix, padding, impl, nn, sid in cr.fetchall():
            new_pt = engine.idmap.get("stock.picking.type", old_pt)
            if not new_pt:
                continue
            seq = env["stock.picking.type"].browse(new_pt).sequence_id
            if not seq:
                continue
            nxt = _src_next(scr, impl, f"ir_sequence_{sid:03d}", nn)
            vals = {}
            if prefix and seq.prefix != prefix:
                vals["prefix"] = prefix
            if padding and seq.padding != padding:
                vals["padding"] = padding
            if (seq.number_next_actual or 0) < nxt:
                vals["number_next_actual"] = nxt
            if vals:
                seq.write(vals); bumped.append(prefix or str(new_pt))
    return dict(bumped=bumped)


def _server_actions(engine, ctx):
    """The 2 DB-created 'Send WhatsApp' code actions — their one-liner API survived the port."""
    cr, env = engine.cr, engine.env
    cr.execute(f"""SELECT a.id, a.name, a.code, m.model, a.binding_type, a.binding_view_types
                   FROM {S}.ir_act_server a JOIN {S}.ir_model m ON m.id = a.model_id
                   WHERE a.state = 'code'
                     AND NOT EXISTS (SELECT 1 FROM {S}.ir_model_data d
                                     WHERE d.model = 'ir.actions.server' AND d.res_id = a.id)""")
    created = []
    for aid, name, code, model, btype, bviews in cr.fetchall():
        model_rec = env["ir.model"]._get(model)
        if not model_rec:
            engine.idmap.refuse("ir.actions.server", "ir.actions.server", aid, f"model {model} missing on 20")
            continue
        rec = env["ir.actions.server"].create({
            "name": name if isinstance(name, str) else (name or {}).get("en_US", "Server action"),
            "state": "code", "code": code, "model_id": model_rec.id,
            "binding_model_id": model_rec.id if btype else False,
            "binding_type": btype or "action", "binding_view_types": bviews,
        })
        engine.idmap.add_many("ir.actions.server", [(aid, rec.id)])
        created.append(name if isinstance(name, str) else (name or {}).get("en_US"))
    return dict(created=created)


SERVER_ACTIONS = FuncStep("ir.actions.server.custom", _server_actions)


def _languages(engine, ctx):
    """prod's active language is en_IN: activate it on the target and restore user/partner langs
    (jsonb translations copied by the SQL lane carry en_IN keys)."""
    cr, env = engine.cr, engine.env
    lang = env["res.lang"].sudo().with_context(active_test=False).search([("code", "=", "en_IN")], limit=1)
    activated = False
    if lang and not lang.active:
        env["res.lang"].sudo()._activate_lang("en_IN")
        env["res.users"].sudo().browse(1)  # noop, keeps registry warm
        activated = True
    cr.execute(f"""UPDATE res_partner p SET lang = s.lang
                   FROM etl_idmap m JOIN {S}.res_partner s ON s.id = m.old_id
                   WHERE m.model = 'res.partner' AND p.id = m.new_id AND s.lang = 'en_IN'
                     AND p.lang IS DISTINCT FROM s.lang""")
    partners = cr.rowcount
    cr.execute(f"""UPDATE res_users u SET lang = s.lang  -- res_users.lang is related+stored? if not, noop below
                   FROM etl_idmap m JOIN {S}.res_users s ON s.id = m.old_id
                   WHERE m.model = 'res.users' AND u.id = m.new_id AND s.lang = 'en_IN'""") if _users_have_lang(cr) else None
    return dict(activated=activated, partner_langs=partners)


def _users_have_lang(cr):
    cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name='res_users' AND column_name='lang'")
    return bool(cr.fetchone())


LANGUAGES = FuncStep("res.lang.en_IN", _languages)


def _tr(v):
    if isinstance(v, dict):
        return v.get("en_IN") or v.get("en_US") or next(iter(v.values()), None)
    return v


def _company(engine, ctx):
    """Remaining company details from prod (mkdb set the basics); dropped v19 knobs are on the
    cutover checklist."""
    cr, env = engine.cr, engine.env
    cr.execute(f"""SELECT p.email, p.website, c.report_header, c.report_footer, c.company_details,
                          c.primary_color, c.secondary_color, c.font, c.external_report_layout_id
                   FROM {S}.res_company c JOIN {S}.res_partner p ON p.id = c.partner_id LIMIT 1""")
    row = cr.fetchone()
    if not row:
        return {}
    email, website, header, footer, details, c1, c2, font, layout = row
    company = env.company
    vals = {}
    if email:
        vals["email"] = email
    if website:
        vals["website"] = website
    for f, v in (("report_header", _tr(header)), ("report_footer", _tr(footer)),
                 ("company_details", _tr(details)), ("primary_color", c1), ("secondary_color", c2), ("font", font)):
        if v and f in company._fields:
            vals[f] = v
    if layout:
        cr.execute(f"SELECT module||'.'||name FROM {S}.ir_model_data WHERE model='ir.ui.view' AND res_id=%s", (layout,))
        r = cr.fetchone()
        if r:
            view = env.ref(r[0], raise_if_not_found=False)
            if view:
                vals["external_report_layout_id"] = view.id
    if vals:
        company.write(vals)

    # One-time UX/config flags a fresh company lacks. Found by the side-by-side QA:
    # prod dismissed the stock-SMS wizard years ago; without the flag every outgoing
    # validation (incl. the app's mark-delivered) pops confirm.stock.sms and does NOT
    # validate. The rest keep prod's exact backend behavior and Indian compliance link.
    copied = []
    cr.execute(f"""SELECT has_received_warning_stock_sms, quick_edit_mode, email_secondary_color,
                          account_opening_date, employee_properties_definition, l10n_in_pan_entity_id
                   FROM {S}.res_company ORDER BY id LIMIT 1""")
    sms_warned, quick_edit, mail_color2, opening_date, emp_props, pan_old = cr.fetchone()
    cr.execute("""UPDATE res_company SET has_received_warning_stock_sms = %s, quick_edit_mode = %s,
                         email_secondary_color = %s, account_opening_date = %s,
                         employee_properties_definition = %s
                  WHERE id = %s""",
               (sms_warned, quick_edit, mail_color2, opening_date,
                Json(emp_props) if emp_props is not None else None, company.id))
    copied += ["has_received_warning_stock_sms", "quick_edit_mode", "email_secondary_color",
               "account_opening_date", "employee_properties_definition"]
    if pan_old:
        pan_new = engine.idmap.get("l10n_in.pan.entity", pan_old)
        if pan_new:
            cr.execute("UPDATE res_company SET l10n_in_pan_entity_id = %s WHERE id = %s",
                       (pan_new, company.id))
            copied.append("l10n_in_pan_entity_id")
    env.invalidate_all()
    return dict(written=sorted(vals), flags=copied)


COMPANY_DETAILS = FuncStep("res.company.details", _company)

def _purge_install_artifacts(engine, ctx):
    """Fresh-install onboarding records prod does not have (Welcome/Training tasks,
    default General/Administrators channels): delete everything the idmap does not
    own so the target lists exactly what prod lists."""
    cr, env = engine.cr, engine.env
    out = {}
    # the connector seeds the demo chatbots / auto-replies prod already has (xmlid'd module data):
    # prod's copies (+OFF) take over the module xmlids first, then the seeded twins go
    # (found by the full-table sweep: every chatbot, step, option and auto-reply listed twice)
    owa = ("owa.chatbot", "owa.chatbot.step", "owa.chatbot.menu.option", "owa.auto.reply", "owa.notification.rule")
    for model in owa:
        table = model.replace(".", "_")
        cr.execute(f"""UPDATE ir_model_data t SET res_id = s.res_id + {config.OFFSET}
                       FROM {S}.ir_model_data s
                       WHERE s.model = %s AND t.model = s.model AND t.module = s.module AND t.name = s.name
                         AND t.res_id <> s.res_id + {config.OFFSET}
                         AND EXISTS (SELECT 1 FROM {table} c WHERE c.id = s.res_id + {config.OFFSET})""", (model,))
        out[f"{model} xmlids"] = cr.rowcount
    # steps and menu options go with their chatbot (ondelete cascade)
    for model in ("project.task", "discuss.channel", "owa.chatbot", "owa.auto.reply", "owa.notification.rule"):
        table = model.replace(".", "_")
        cr.execute(f"""SELECT t.id FROM {table} t WHERE NOT EXISTS
                       (SELECT 1 FROM etl_idmap m WHERE m.model = %s AND m.new_id = t.id)""",
                   (model,))
        ids = [r[0] for r in cr.fetchall()]
        if ids:
            env[model].sudo().with_context(active_test=False).browse(ids).unlink()
        out[model] = len(ids)
    return out


PURGE_INSTALL = FuncStep("parity.purge_install_artifacts", _purge_install_artifacts)

# Chatter is prod's: the ORM lane and the module installs wrote notes on MIGRATED records
# (a migration-day "0.00 -> 1,540.00 (Untaxed Amount)" on every invoice, a second "created"
# on users/employees/products) next to the history the SQL lane copied byte-identical (+OFF).
# Records that exist only on 20 keep theirs. Tracking values/notifications cascade.
ETL_CHATTER = SqlStep("parity.etl_chatter", """
    DELETE FROM mail_message m WHERE m.id < {OFF} AND m.model IS NOT NULL
      AND EXISTS (SELECT 1 FROM etl_idmap i WHERE i.model = m.model AND i.new_id = m.res_id)""")

# prod creates contacts in en_IN (activated by res.lang.en_IN above); 20's install default is en_US
PARTNER_LANG = SqlStep("ir.default.partner_lang", """
    UPDATE ir_default t SET json_value = s.json_value
    FROM {S}.ir_default s
    JOIN {S}.ir_model_fields sf ON sf.id = s.field_id
    JOIN ir_model_fields tf ON tf.model = sf.model AND tf.name = sf.name
    WHERE t.field_id = tf.id AND sf.model = 'res.partner' AND sf.name = 'lang'
      AND s.user_id IS NULL AND s.company_id IS NULL AND s.condition IS NULL
      AND t.user_id IS NULL AND t.company_id IS NULL AND t.condition IS NULL
      AND t.json_value IS DISTINCT FROM s.json_value""")

# the periodic KPI digest: 20 subscribes every new user and runs daily; prod mails it monthly to
# one user. Its settings and subscribers follow prod (matched by xmlid).
DIGEST = SqlStep("digest.subscriptions", """
    UPDATE digest_digest t SET periodicity = s.periodicity, state = s.state, next_run_date = s.next_run_date
    FROM {S}.digest_digest s
    JOIN {S}.ir_model_data sd ON sd.model = 'digest.digest' AND sd.res_id = s.id
    JOIN ir_model_data td ON td.model = 'digest.digest' AND td.module = sd.module AND td.name = sd.name
    WHERE t.id = td.res_id;
    DELETE FROM digest_digest_res_users_rel r
    USING {S}.ir_model_data sd JOIN ir_model_data td ON td.model = sd.model AND td.module = sd.module AND td.name = sd.name
    WHERE sd.model = 'digest.digest' AND r.digest_digest_id = td.res_id;
    INSERT INTO digest_digest_res_users_rel (digest_digest_id, res_users_id)
    SELECT td.res_id, u.new_id FROM {S}.digest_digest_res_users_rel r
    JOIN {S}.ir_model_data sd ON sd.model = 'digest.digest' AND sd.res_id = r.digest_digest_id
    JOIN ir_model_data td ON td.model = 'digest.digest' AND td.module = sd.module AND td.name = sd.name
    JOIN etl_idmap u ON u.model = 'res.users' AND u.old_id = r.res_users_id
    ON CONFLICT DO NOTHING""")


def _id_sequences(engine, ctx):
    """Every table holding SQL-lane rows (+OFF ids): its sequence continues after them, so a
    record created after the switch-over sorts after prod's history (owa.message, chatter... are
    ordered by id)."""
    cr = engine.cr
    cr.execute("""SELECT c.table_name FROM information_schema.columns c JOIN information_schema.tables t
                    ON t.table_schema = c.table_schema AND t.table_name = c.table_name AND t.table_type = 'BASE TABLE'
                  WHERE c.table_schema = 'public' AND c.column_name = 'id'""")
    bumped = []
    for (table,) in cr.fetchall():
        cr.execute("SELECT pg_get_serial_sequence(%s, 'id')", (f'public."{table}"',))
        seq = cr.fetchone()[0]
        if not seq:
            continue
        cr.execute(f'SELECT max(id) FROM "{table}"')
        top = cr.fetchone()[0]
        if top is None or top < config.OFFSET:
            continue
        cr.execute("SELECT last_value FROM " + seq)
        if cr.fetchone()[0] < top:
            cr.execute("SELECT setval(%s, %s)", (seq, top))
            bumped.append(table)
    return dict(bumped=sorted(bumped))


ID_SEQUENCES = FuncStep("parity.id_sequences", _id_sequences)

# menu visibility customized IN THE DATABASE: prod added base.group_user to the three
# diamond app menus so every employee sees Pickup / Deliveries / Job Work. Menus are
# module data, so only the group DELTA must be carried (matched by menu + group xmlid;
# groups are only ADDED, the module's own gates are never removed).
MENU_GROUPS = SqlStep("ir.ui.menu.group_deltas", """
    INSERT INTO ir_ui_menu_group_rel (menu_id, gid)
    SELECT tm.res_id, tg.res_id
    FROM {S}.ir_ui_menu_group_rel r
    JOIN {S}.ir_model_data smd ON smd.model = 'ir.ui.menu' AND smd.res_id = r.menu_id
    JOIN ir_model_data tm ON tm.model = 'ir.ui.menu' AND tm.module = smd.module AND tm.name = smd.name
    JOIN {S}.ir_model_data sgd ON sgd.model = 'res.groups' AND sgd.res_id = r.gid
    JOIN ir_model_data tg ON tg.model = 'res.groups' AND tg.module = sgd.module AND tg.name = sgd.name
    WHERE NOT EXISTS (SELECT 1 FROM ir_ui_menu_group_rel e
                      WHERE e.menu_id = tm.res_id AND e.gid = tg.res_id)""")

def _tz_aliases(engine, ctx):
    """SQL-lane rows carry tz text verbatim (website_visitor.timezone, resource_resource.tz...):
    rewrite legacy aliases to the canonical names v20's selections list."""
    cr, fixed = engine.cr, {}
    cr.execute("""SELECT table_name, column_name FROM information_schema.columns
                  WHERE table_schema = 'public' AND column_name IN ('tz', 'timezone')
                    AND data_type IN ('character varying', 'text')""")
    for table, col in cr.fetchall():
        cr.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s"
                   " AND table_type='BASE TABLE'", (table,))
        if not cr.fetchone():
            continue
        for old, new in config.TZ_ALIASES.items():
            cr.execute(f'UPDATE "{table}" SET "{col}" = %s WHERE "{col}" = %s', (new, old))
            if cr.rowcount:
                fixed[f"{table}.{col}"] = fixed.get(f"{table}.{col}", 0) + cr.rowcount
    return fixed


TZ_ALIASES = FuncStep("parity.tz_aliases", _tz_aliases)

STEPS = [EXPORT_XMLIDS, PARAMS, FILTERS, SEQUENCES, PICKING_SEQ, SERVER_ACTIONS, LANGUAGES, COMPANY_DETAILS,
         PURGE_INSTALL, ETL_CHATTER, MENU_GROUPS, TZ_ALIASES, PARTNER_LANG, DIGEST, ID_SEQUENCES]
