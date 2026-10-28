"""Phase 'base': users (incl. password hashes + group membership), PAN entities, partners.
Rules doc §E. Adapted from the marut ETL; sudi is single-company and v19 has no
partner.title / mobile / company_name columns, so the transforms are leaner."""
import logging

from ..core import OrmStep, FuncStep, SqlStep, TableCopy, IDM
from .. import config

_logger = logging.getLogger("etl.base")

S = config.SRC_SCHEMA


# ----------------------------------------------------------------------------- users
def _user_transform(vals, row, ctx):
    """name/email live on the 19 partner; passwords are copied by SQL afterwards."""
    p = ctx["src"].row("res_partner", row["partner_id"], ("name", "email", "phone", "tz", "lang", "function"))
    if p:
        vals.setdefault("name", p["name"])
        for k in ("email", "tz", "function", "phone"):
            if p.get(k):
                vals.setdefault(k, config.TZ_ALIASES.get(p[k], p[k]) if k == "tz" else p[k])
        vals["lang"] = "en_US"
    vals.pop("password", None)
    vals.pop("company_ids", None)
    vals["company_id"] = ctx["env"].company.id
    vals["company_ids"] = [(6, 0, [ctx["env"].company.id])]
    return vals


def _after_users(ctx):
    eng = ctx["engine"]; cr = eng.cr
    # the 19 partner of every migrated user maps onto the 20 user's partner (authors, followers, ...)
    cr.execute(f"""INSERT INTO etl_idmap (model, old_id, new_id)
                   SELECT 'res.partner', s.partner_id, u.partner_id
                   FROM etl_idmap m JOIN {S}.res_users s ON s.id = m.old_id JOIN res_users u ON u.id = m.new_id
                   WHERE m.model = 'res.users' ON CONFLICT DO NOTHING""")
    # OdooBot / admin (ids 1, 2 on both sides)
    cr.execute(f"""INSERT INTO etl_idmap (model, old_id, new_id)
                   SELECT 'res.users', s.id, s.id FROM {S}.res_users s WHERE s.id IN (1, 2) ON CONFLICT DO NOTHING""")
    cr.execute(f"""INSERT INTO etl_idmap (model, old_id, new_id)
                   SELECT 'res.partner', s.partner_id, u.partner_id FROM {S}.res_users s JOIN res_users u ON u.id = s.id
                   WHERE s.id IN (1, 2) ON CONFLICT DO NOTHING""")
    # password hashes (same passlib scheme on both versions); admin keeps the fresh-DB password
    cr.execute(f"""UPDATE res_users u SET password = s.password
                   FROM etl_idmap m JOIN {S}.res_users s ON s.id = m.old_id
                   WHERE m.model = 'res.users' AND u.id = m.new_id AND s.password IS NOT NULL AND u.id > 2""")
    _logger.info("users: %d password hashes copied", cr.rowcount)
    # TOTP secrets survive the hop (same scheme)
    cr.execute(f"""UPDATE res_users u SET totp_secret = s.totp_secret
                   FROM etl_idmap m JOIN {S}.res_users s ON s.id = m.old_id
                   WHERE m.model = 'res.users' AND u.id = m.new_id AND s.totp_secret IS NOT NULL AND u.id > 2""")
    eng.idmap._cache.pop("res.partner", None); eng.idmap._cache.pop("res.users", None)


SPECIAL_USERS = f"(SELECT res_id FROM {S}.ir_model_data WHERE model = 'res.users' AND module = 'base')"
USERS = OrmStep(
    "res.users", where=f"id > 2 AND share = false AND id NOT IN {SPECIAL_USERS}", key=["login"],
    skip={"password", "partner_id", "totp_secret", "api_key_ids", "action_id", "company_ids", "log_ids", "device_ids",
          "res_users_settings_id", "groups_id", "group_ids", "odoobot_state", "odoobot_failed", "sidebar_type"},
    rename={"groups_id": "group_ids"},
    transform=_user_transform, after=_after_users, context={"no_reset_password": True},
)
# group membership: 19 res_groups_users_rel resolved by group xmlid (natural resolver);
# groups that no longer exist on 20 (stock.group_reception_report) drop silently.
GROUPS = OrmStep(
    "res.users", name="res.users.groups", where=f"id > 2 AND share = false AND id NOT IN {SPECIAL_USERS}",
    write_mapped=True, restore_dates=False,
    skip={"login", "password", "partner_id", "signature", "active", "notification_type", "company_id", "company_ids",
          "odoobot_state", "odoobot_failed", "tz", "share", "action_id", "karma", "totp_secret", "sidebar_type",
          "res_users_settings_id", "email", "login_date", "calendar_default_privacy", "sale_team_id", "employee_ids",
          "device_ids", "api_key_ids", "log_ids"},
    rename={"groups_id": "group_ids"},
    transform=lambda vals, row, ctx: {"group_ids": vals["group_ids"]} if vals.get("group_ids") else None,
)

# ----------------------------------------------------------------------------- reference data
INDUSTRY = OrmStep("res.partner.industry", key=["name"], update_existing=False)
PCATEG = OrmStep("res.partner.category", key=["name"], order="parent_path, id", batch=1,
                 skip={"parent_path", "partner_ids"})
PAN = OrmStep("l10n_in.pan.entity", key=["name"], update_existing=False,
              skip={"tds_certificate_filename", "partner_ids", "related_partner_count"})

# ----------------------------------------------------------------------------- partners
PARTNER_SKIP = {"vies_valid", "global_location_number", "peppol_eas", "peppol_endpoint", "company_registry",
                "company_name", "online_partner_information", "commercial_partner_id", "commercial_company_name",
                "complete_name", "user_ids", "employee_ids", "signup_token", "signup_type", "signup_expiration",
                "partner_share", "same_vat_partner_id", "same_company_registry_partner_id", "message_bounce",
                "email_normalized", "phone_sanitized", "im_status", "contact_address", "contact_address_complete",
                "channel_ids", "starred_message_ids", "bank_ids", "child_ids", "ref_company_ids", "self",
                "meeting_ids", "calendar_last_notif_ack", "task_ids", "invoice_ids", "ocn_token",
                "l10n_in_gst_treatment", "l10n_in_gstin_verified_status", "l10n_in_gstin_verified_date",
                "is_created_by_ocr", "invoice_template_pdf_report_id"}  # gst treatment re-set in a second pass


def _partner_retry(vals, err):
    """20 refuses malformed VAT numbers: keep the string in the notes and retry without it."""
    if ("VAT" in err or "GSTIN" in err) and vals.get("vat"):
        vals["comment"] = (vals.get("comment") or "") + f"<p>GSTIN/VAT (as entered on v19, invalid format): {vals['vat']}</p>"
        vals["vat"] = False
        return vals
    if "res_partner_check_name" in err:
        vals["name"] = vals.get("name") or vals.get("email") or "Unnamed contact"
        vals["type"] = "contact"
        return vals
    return None


def _partner_transform(vals, row, ctx):
    if not vals.get("name") and not vals.get("is_company") and vals.get("type", "contact") == "contact":
        vals["name"] = row.get("email") or row.get("phone") or f"Unnamed contact #{row['id']}"
    return vals


def _company_partner_premap(engine, ctx):
    """mkdb already created the company partner; prod's company partner must land ON it
    (found by the side-by-side QA: it was copied as a duplicate contact instead)."""
    cr, env = engine.cr, engine.env
    cr.execute(f"SELECT partner_id FROM {S}.res_company ORDER BY id LIMIT 1")
    old = cr.fetchone()[0]
    new = env.company.partner_id.id
    if not engine.idmap.get("res.partner", old):
        engine.idmap.add_many("res.partner", [(old, new)])
    return dict(mapped={old: new})


COMPANY_PARTNER = FuncStep("res.partner.company_premap", _company_partner_premap)

PARTNERS_1 = OrmStep(
    "res.partner", name="res.partner.roots",
    where=f"parent_id IS NULL AND id NOT IN (SELECT partner_id FROM {S}.res_users WHERE partner_id IS NOT NULL)",
    skip=PARTNER_SKIP, transform=_partner_transform, batch=400, retry=_partner_retry,
    write_mapped=True,   # the pre-mapped company partner gets prod's values written
)
PARTNERS_2 = OrmStep(
    "res.partner", name="res.partner.children",
    where=f"parent_id IS NOT NULL AND id NOT IN (SELECT partner_id FROM {S}.res_users WHERE partner_id IS NOT NULL)",
    skip=PARTNER_SKIP, transform=_partner_transform, batch=400, retry=_partner_retry,
)
PARTNERS_USERS = OrmStep(
    "res.partner", name="res.partner.users",
    where=f"id IN (SELECT partner_id FROM {S}.res_users WHERE partner_id IS NOT NULL AND id > 2)",
    skip=PARTNER_SKIP | {"active", "type", "is_company", "user_id"}, transform=_partner_transform,
    write_mapped=True, restore_dates=False,
)
USER_PARTNER_NAMES = SqlStep("res.partner.users_names", f"""
    UPDATE res_partner p SET name = s.name, email = coalesce(nullif(s.email, ''), p.email)
    FROM etl_idmap m JOIN {{S}}.res_partner s ON s.id = m.old_id
    WHERE m.model = 'res.partner' AND p.id = m.new_id AND nullif(s.name, '') IS NOT NULL
      AND p.id IN (SELECT partner_id FROM res_users)
      AND (p.name IS DISTINCT FROM s.name OR p.email IS DISTINCT FROM coalesce(nullif(s.email, ''), p.email))""")


def _partner_fixups(engine, ctx):
    """Second pass once every partner exists: gst treatment as on 19, children created before their
    parent re-linked, and is_company restored (20 computes it from VAT — prod's flags win)."""
    cr = engine.cr
    cr.execute(f"""UPDATE res_partner p SET l10n_in_gst_treatment = s.l10n_in_gst_treatment
                   FROM etl_idmap m JOIN {S}.res_partner s ON s.id = m.old_id
                   WHERE m.model = 'res.partner' AND p.id = m.new_id AND s.l10n_in_gst_treatment IS NOT NULL
                     AND p.l10n_in_gst_treatment IS DISTINCT FROM s.l10n_in_gst_treatment""")
    gst = cr.rowcount
    cr.execute(f"""UPDATE res_partner c SET parent_id = pm.new_id
                   FROM etl_idmap m JOIN {S}.res_partner s ON s.id = m.old_id
                   JOIN etl_idmap pm ON pm.model = 'res.partner' AND pm.old_id = s.parent_id
                   WHERE m.model = 'res.partner' AND c.id = m.new_id AND s.parent_id IS NOT NULL
                     AND c.parent_id IS NULL AND pm.new_id <> c.id""")
    relinked = cr.rowcount
    cr.execute(f"""UPDATE res_partner p SET is_company = s.is_company
                   FROM etl_idmap m JOIN {S}.res_partner s ON s.id = m.old_id
                   WHERE m.model = 'res.partner' AND p.id = m.new_id
                     AND p.is_company IS DISTINCT FROM s.is_company""")
    is_company = cr.rowcount
    # active=False is dropped by the create lane (same class as is_company): prod's
    # archived partners must stay archived (found by the QA per-user partner counts).
    cr.execute(f"""UPDATE res_partner p SET active = s.active
                   FROM etl_idmap m JOIN {S}.res_partner s ON s.id = m.old_id
                   WHERE m.model = 'res.partner' AND p.id = m.new_id
                     AND p.active IS DISTINCT FROM s.active""")
    actives = cr.rowcount
    cr.execute("""UPDATE res_partner c SET commercial_partner_id = p.commercial_partner_id FROM res_partner p
                  WHERE p.id = c.parent_id AND c.is_company = false
                    AND c.commercial_partner_id IS DISTINCT FROM p.commercial_partner_id""")
    engine._restore_dates("res_partner", "res.partner")
    return dict(gst_treatment_fixed=gst, children_relinked=relinked, is_company_restored=is_company,
                active_restored=actives)


FIXUPS = FuncStep("res.partner.fixups", _partner_fixups)


# customers' phone-number logins (portal: the customer app + /my). Their partner already exists
# (res.partner.users created it with its address/GST), so the user is created ON it, never on a
# twin contact. Found by the full-table sweep on the prod source: 14 live logins were left behind.
def _portal_user_transform(vals, row, ctx):
    if not vals.get("partner_id"):
        return None
    vals.pop("password", None)
    vals["company_id"] = ctx["env"].company.id
    vals["company_ids"] = [(6, 0, [ctx["env"].company.id])]
    return vals


PORTAL_USERS = OrmStep(
    "res.users", name="res.users.portal", where=f"id > 2 AND share = true AND id NOT IN {SPECIAL_USERS}",
    key=["login"],
    skip={"password", "totp_secret", "api_key_ids", "action_id", "company_ids", "log_ids", "device_ids",
          "res_users_settings_id", "odoobot_state", "odoobot_failed", "sidebar_type"},
    rename={"groups_id": "group_ids"},
    transform=_portal_user_transform, after=_after_users, context={"no_reset_password": True},
)

# login history: "Latest authentication" on a user is computed from these rows
USERS_LOG = TableCopy(
    "res.users.log", "res_users_log",
    joins=[IDM("lu", "res.users", "s.create_uid")],
    where="lu.new_id IS NOT NULL",
    overrides={"id": "s.id + {OFF}", "create_uid": "lu.new_id", "write_uid": "lu.new_id"},
    on_conflict="DO NOTHING",
)

USERS_SETTINGS = OrmStep(
    "res.users.settings", key=["user_id"],
    skip={"voice_active_duration", "is_discuss_sidebar_category_channel_open", "is_discuss_sidebar_category_chat_open",
          "use_push_to_talk", "push_to_talk_key", "volume_settings_ids", "channel_notifications"},
    transform=lambda v, r, c: v if v.get("user_id") else None,
)

STEPS = [USERS, GROUPS, INDUSTRY, PCATEG, PAN, COMPANY_PARTNER, PARTNERS_1, PARTNERS_2, PARTNERS_USERS, USER_PARTNER_NAMES, FIXUPS,
         PORTAL_USERS, USERS_LOG, USERS_SETTINGS]
