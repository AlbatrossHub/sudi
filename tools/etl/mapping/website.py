"""Phase 'website' — the precision lane for the user's custom views (rules doc: ALL 60
custom views are website qweb). COW/custom views are copied with their arch byte-identical
(+OFFSET ids), inherit_id re-anchored by VIEW KEY on the fresh v20 website module, pages and
menus rebuilt, socials moved to the company (v20 dropped website.social_*)."""
import json
import logging
import os

from psycopg2.extras import Json

from ..core import FuncStep, SqlStep, TableCopy, IDM
from .. import config

_logger = logging.getLogger("etl.website")
S = config.SRC_SCHEMA


def _views(engine, ctx):
    """Copy every custom/COW view: website_id set, arch edited (arch_updated), or no xmlid."""
    cr = engine.cr
    cr.execute(f"""SELECT v.id, v.name, v.key, v.type, v.mode, v.arch_db, v.arch_fs, v.arch_updated, v.active,
                          v.inherit_id, v.website_id, v.priority, v.create_date, v.write_date,
                          (SELECT key FROM {S}.ir_ui_view p WHERE p.id = v.inherit_id) AS parent_key,
                          v.visibility
                   FROM {S}.ir_ui_view v
                   WHERE v.website_id IS NOT NULL OR v.arch_updated
                      OR NOT EXISTS (SELECT 1 FROM {S}.ir_model_data d WHERE d.model = 'ir.ui.view' AND d.res_id = v.id)
                   ORDER BY v.id""")
    rows = cr.fetchall()
    src_ids = {r[0] for r in rows}
    copied = skipped = 0
    for (vid, name, key, vtype, mode, arch, arch_fs, arch_upd, active, inherit_id, website_id,
         priority, cdate, wdate, parent_key, visibility) in rows:
        # prod's jsonb archs are keyed 'en_IN'; mirror to 'en_US' so the target's default lang renders them
        if isinstance(arch, dict) and "en_US" not in arch and arch:
            arch = dict(arch); arch["en_US"] = next(iter(arch.values()))
        new_inherit = None
        if inherit_id:
            if inherit_id in src_ids:
                new_inherit = inherit_id + config.OFFSET   # parent is another copied custom view
            elif parent_key:
                cr.execute("SELECT id FROM ir_ui_view WHERE key = %s AND website_id IS NULL ORDER BY id LIMIT 1",
                           (parent_key,))
                r = cr.fetchone()
                if r:
                    new_inherit = r[0]
            if new_inherit is None:
                engine.idmap.refuse("ir.ui.view", "ir.ui.view", vid,
                                    f"parent view not found on 20 (key={parent_key!r}) — view parked inactive")
                active = False
        # a COW copy shadows the generic view of the same key for website 1 — v20 resolves it identically
        cr.execute("""INSERT INTO ir_ui_view (id, name, key, type, mode, arch_db, arch_updated, active, inherit_id,
                                              website_id, priority, visibility, create_uid, write_uid,
                                              create_date, write_date)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 1, %s, %s)
                      ON CONFLICT (id) DO NOTHING""",
                   (vid + config.OFFSET, name, key, vtype, mode, Json(arch) if isinstance(arch, dict) else arch,
                    arch_upd, active, new_inherit, 1 if website_id else None, priority or 16,
                    visibility or "public", cdate, wdate))
        copied += cr.rowcount
        if not cr.rowcount:
            skipped += 1
    cr.execute(f"""INSERT INTO etl_idmap (model, old_id, new_id)
                   SELECT 'ir.ui.view', s.id, s.id + {config.OFFSET} FROM {S}.ir_ui_view s
                   WHERE EXISTS (SELECT 1 FROM ir_ui_view t WHERE t.id = s.id + {config.OFFSET})
                   ON CONFLICT DO NOTHING""")
    cr.execute("SELECT setval(pg_get_serial_sequence('ir_ui_view', 'id'), GREATEST((SELECT max(id) FROM ir_ui_view), 1))")
    engine.idmap._cache.pop("ir.ui.view", None)
    return dict(views=copied, already=skipped)


VIEWS = FuncStep("website.views", _views)


def _pages(engine, ctx):
    """website_page rows (url became translatable jsonb on 20) + menu tree."""
    cr, env = engine.cr, engine.env
    cr.execute(f"""SELECT p.id, p.url, p.view_id, p.website_indexed, p.is_published,
                          p.website_id, p.header_overlay, p.header_color, p.header_text_color, p.header_visible,
                          p.footer_visible, p.create_date, p.write_date,
                          (SELECT v.name FROM {S}.ir_ui_view v WHERE v.id = p.view_id) AS page_name
                   FROM {S}.website_page p ORDER BY p.id""")
    n = 0
    cr2 = engine.cr
    for (pid, url, view_id, indexed, published, website_id, overlay, color, text_color,
         header_visible, footer_visible, cdate, wdate, page_name) in cr.fetchall():
        cr2.execute("SELECT new_id FROM etl_idmap WHERE model = 'ir.ui.view' AND old_id = %s", (view_id,))
        r = cr2.fetchone()
        if not r:
            engine.idmap.refuse("website.page", "website.page", pid, "view not copied")
            continue
        cr2.execute("""INSERT INTO website_page (id, name, url, view_id, website_indexed, is_published, website_id,
                                                 header_overlay, header_color, header_text_color, header_visible,
                                                 footer_visible, create_uid, write_uid, create_date, write_date)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 1, %s, %s)
                       ON CONFLICT (id) DO NOTHING""",
                    (pid + config.OFFSET, json.dumps({"en_US": page_name}), json.dumps({"en_US": url}), r[0], indexed, published,
                     1 if website_id else None, overlay, color, text_color, header_visible, footer_visible,
                     cdate, wdate))
        n += cr2.rowcount
    cr.execute("SELECT setval(pg_get_serial_sequence('website_page', 'id'), GREATEST((SELECT max(id) FROM website_page), 1))")
    # menus: the RENDERED tree is website 1's own ("Top Menu for Website 1" + children on 19).
    # Generic menus (website NULL) are module data and stay untouched. Idempotent: every
    # website-1 menu (fresh default or an earlier copy) is wiped before the rebuild.
    website = env["website"].browse(1)
    Menu = env["website.menu"].sudo()
    removed = Menu.search([("website_id", "=", website.id)])
    n_removed = len(removed)
    removed.unlink()
    cr.execute(f"""SELECT m.id, m.name->>'en_US', m.url, m.page_id, m.parent_id, m.sequence, m.new_window
                   FROM {S}.website_menu m WHERE m.website_id IS NOT NULL
                   ORDER BY m.parent_id NULLS FIRST, m.sequence, m.id""")
    rows = cr.fetchall()
    mapping = {}
    created = 0
    for mid, name, url, page_id, parent_id, sequence, new_window in rows:
        vals = {"name": name, "sequence": sequence or 10, "new_window": bool(new_window),
                "website_id": website.id}
        if parent_id and parent_id in mapping:
            vals["parent_id"] = mapping[parent_id]
        if page_id:
            cr.execute("SELECT 1 FROM website_page WHERE id = %s", (page_id + config.OFFSET,))
            if cr.fetchone():
                vals["page_id"] = page_id + config.OFFSET
        if url and not vals.get("page_id"):
            vals["manual_url"] = url
        rec = Menu.create(vals)
        mapping[mid] = rec.id
        created += 1
    engine.idmap.add_many("website.menu", list(mapping.items()))
    return dict(pages=n, menus=created, website1_menus_removed=n_removed)


PAGES = FuncStep("website.pages", _pages)


def _settings(engine, ctx):
    """website name/logo/favicon/company socials; the fresh website keeps localhost as base
    domain (cutover checklist re-points it on the VPS)."""
    from odoo.tools.binary import BinaryBytes
    cr, env = engine.cr, engine.env
    # the website record itself maps 1 -> 1 (single website): lets the chatter lane copy its
    # attachments (logo, favicon, theme images)
    cr.execute("""INSERT INTO etl_idmap (model, old_id, new_id) VALUES ('website', 1, 1)
                  ON CONFLICT DO NOTHING""")
    engine.idmap._cache.pop("website", None)
    cr.execute(f"""SELECT name, social_twitter, social_facebook, social_github, social_linkedin,
                          social_youtube, social_instagram, social_tiktok, default_lang_id
                   FROM {S}.website ORDER BY id LIMIT 1""")
    row = cr.fetchone()
    if not row:
        return {}
    name = row[0]
    website = env["website"].browse(1)
    website.write({"name": name if isinstance(name, str) else (name or {}).get("en_US", "SDPPL")})
    company = env.company
    social = {}
    for field, val in zip(("social_twitter", "social_facebook", "social_github", "social_linkedin",
                           "social_youtube", "social_instagram", "social_tiktok"), row[1:8]):
        if val and field in company._fields and not company[field]:
            social[field] = val
    if social:
        company.write(social)
    binaries = []
    for field in ("logo", "favicon"):
        cr.execute(f"""SELECT store_fname FROM {S}.ir_attachment
                       WHERE res_model = 'website' AND res_id = 1 AND res_field = %s
                       ORDER BY id DESC LIMIT 1""", (field,))
        r = cr.fetchone()
        if r and r[0]:
            path = os.path.join(config.FILESTORE_SRC, r[0])
            if os.path.exists(path):
                with open(path, "rb") as f:
                    website.write({field: BinaryBytes(f.read())})
                binaries.append(field)
    # company logo drives reports and the backend: same treatment. On 19 company.logo
    # is RELATED to partner_id.image_1920, so the attachment lives on res.partner.
    cr.execute(f"""SELECT a.store_fname FROM {S}.ir_attachment a
                   WHERE (a.res_model = 'res.partner' AND a.res_field = 'image_1920'
                          AND a.res_id = (SELECT partner_id FROM {S}.res_company ORDER BY id LIMIT 1))
                      OR (a.res_model = 'res.company' AND a.res_id = 1 AND a.res_field = 'logo')
                   ORDER BY a.id DESC LIMIT 1""")
    r = cr.fetchone()
    if r and r[0] and os.path.exists(os.path.join(config.FILESTORE_SRC, r[0])):
        with open(os.path.join(config.FILESTORE_SRC, r[0]), "rb") as f:
            company.write({"logo": BinaryBytes(f.read())})
        binaries.append("company.logo")
    return dict(website=website.name, socials=list(social), binaries=binaries)


SETTINGS = FuncStep("website.settings", _settings)

# Per-view repairs: prod COW customizations re-fitted onto v20 markup (decision log in
# docs/etl_mapping_rules.md §F; prod's CONTENT is kept, only anchors/classes move).
VIEW_REPAIRS = {
    # v20 renamed the copyright column to div[@name='footer_copyright_col'] with col-md classes
    "website.footer_copyright_company_name": """<data inherit_id="website.layout" priority="15">
    <xpath expr="//footer//span[hasclass('o_footer_copyright_name')]" position="replace">
        <span class="o_footer_copyright_name me-2 small">Copyright © SDPPL 2026</span>
    </xpath>
    <xpath expr="//footer//div[@name='footer_copyright_col']" position="attributes">
        <attribute name="class" remove="text-sm-start" add="d-flex flex-column-reverse gap-2 text-md-start" separator=" "/>
    </xpath>
</data>""",
}


# v19 API spellings inside copied archs -> their v20 home (plain text substitution, content kept)
ARCH_SUBSTITUTIONS = [
    ("request.env['website.visitor']._get_visitor_from_request()",
     "request.env['ir.http']._get_visitor_from_request()"),
]


def _repairs(engine, ctx):
    cr = engine.cr
    fixed = []
    for old, new in ARCH_SUBSTITUTIONS:
        cr.execute("""UPDATE ir_ui_view
                         SET arch_db = replace(arch_db::text, %s, %s)::jsonb
                       WHERE id > %s AND arch_db::text LIKE %s""",
                   (old.replace("'", "\u0027") if False else old, new, config.OFFSET, f"%{old}%"))
        if cr.rowcount:
            fixed.append(f"substitution x{cr.rowcount}: {old[:60]}")
    for key, arch in VIEW_REPAIRS.items():
        cr.execute("""UPDATE ir_ui_view
                         SET arch_db = jsonb_build_object('en_US', %s::text, 'en_IN', %s::text)
                       WHERE id > %s AND key = %s""",
                   (arch, arch, config.OFFSET, key))
        if cr.rowcount:
            fixed.append(key)
    env = engine.env
    box = env["ir.ui.view"].sudo().search([("key", "=", "website.header_search_box"),
                                           ("website_id", "=", False)], limit=1)
    if box:
        cow = env["ir.ui.view"].sudo().search([("key", "=", "website.header_search_box"),
                                               ("website_id", "=", 1)], limit=1)
        if not cow:
            box.with_context(website_id=1).write({"active": False})
            fixed.append("header_search_box disabled (website 1)")
    return dict(repaired=fixed)


REPAIRS = FuncStep("website.view_repairs", _repairs)

# prod wins over the fresh install's default pages: the configurator homepage COW is replaced by
# prod's, and pages prod never had (v20's /privacy) are removed so their URLs 404 exactly as on 19.
CLEANUP = SqlStep("website.cleanup", f"""
    DELETE FROM website_page p USING ir_ui_view v
     WHERE p.view_id = v.id AND p.id < {{OFF}} AND p.website_id IS NOT NULL
       AND EXISTS (SELECT 1 FROM website_page q
                   WHERE q.id > {{OFF}} AND q.website_id = p.website_id AND q.url = p.url);
    DELETE FROM ir_ui_view v
     WHERE v.id < {{OFF}} AND v.website_id IS NOT NULL AND v.type = 'qweb'
       AND NOT EXISTS (SELECT 1 FROM website_page p WHERE p.view_id = v.id)
       AND EXISTS (SELECT 1 FROM ir_ui_view w WHERE w.id > {{OFF}} AND w.key = v.key
                   AND w.website_id = v.website_id);
    DELETE FROM website_page p
     WHERE p.id < {{OFF}} AND p.url->>'en_US' NOT IN (SELECT s.url FROM {{S}}.website_page s)""")

def _module_images(engine, ctx):
    """Module-xmlid attachments (snippet stock images like website.s_cover_default_image) whose
    CONTENT differs between prod-19 and fresh-20: prod's file wins, so every page pixel that the
    user knows stays as it is. The file is hard-linked into the target filestore."""
    import shutil
    cr, env = engine.cr, engine.env
    cr.execute(f"""SELECT d20.res_id, a19.store_fname, a19.file_size, a19.mimetype, a19.checksum
                   FROM {S}.ir_model_data d19
                   JOIN {S}.ir_attachment a19 ON a19.id = d19.res_id
                   JOIN ir_model_data d20 ON d20.module = d19.module AND d20.name = d19.name
                                          AND d20.model = 'ir.attachment'
                   JOIN ir_attachment a20 ON a20.id = d20.res_id
                   WHERE d19.model = 'ir.attachment' AND d19.module NOT IN ('__export__')
                     AND a19.store_fname IS NOT NULL
                     AND (coalesce(a19.checksum, '') <> coalesce(a20.checksum, '')
                          OR a19.file_size IS DISTINCT FROM a20.file_size)
                     AND d20.module || '.' || d20.name <> 'web.asset_styles_company_report'""")
    rows = cr.fetchall()
    dst_root = env["ir.attachment"]._filestore()
    n = missing = 0
    for res_id, fname, size, mimetype, checksum in rows:
        src = os.path.join(config.FILESTORE_SRC, fname)
        if not os.path.exists(src):
            missing += 1
            continue
        dst = os.path.join(dst_root, fname)
        if not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        cr.execute("""UPDATE ir_attachment SET store_fname = %s, file_size = %s, mimetype = %s,
                                               checksum = %s, db_datas = NULL
                      WHERE id = %s""", (fname, size, mimetype, checksum, res_id))
        n += 1
    # URL-type module attachments point at STATIC files shipped with the code: when the v19
    # file differs from the v20 one (stock snippet art changed between versions), the target
    # attachment is converted to a binary copy of the v19 bytes so the pages keep prod's look.
    from odoo.tools.binary import BinaryBytes
    V19_STATIC = ("/opt/odoo19/odoo19/addons", "/opt/odoo19/ent", "/opt/odoo20/etl/prod_addons/sudi",
                  "/opt/odoo19/odoo19/odoo/addons")
    V20_STATIC = ("/opt/odoo20/odoo20/addons", "/opt/odoo20/ent", "/opt/odoo20/custom/sudi_20",
                  "/opt/odoo20/odoo20/odoo/addons")

    def _read_static(roots, url):
        parts = url.lstrip("/").split("/", 1)
        if len(parts) != 2:
            return None
        for root in roots:
            p = os.path.join(root, parts[0], parts[1])
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read()
        return None

    # v19's website configurator saved industry images as BINARY attachments NAMED after the
    # snippet xmlid (e.g. 'website.s_cover_default_image'); v19 served them in place of the
    # stock art, v20 does not know that mechanism -> fold the bytes into the xmlid attachment.
    cr.execute(f"""SELECT d20.res_id, a19.url, ov.store_fname
                   FROM {S}.ir_model_data d19
                   JOIN {S}.ir_attachment a19 ON a19.id = d19.res_id AND a19.type = 'url'
                   JOIN ir_model_data d20 ON d20.module = d19.module AND d20.name = d19.name
                                          AND d20.model = 'ir.attachment'
                   JOIN ir_attachment a20 ON a20.id = d20.res_id
                   LEFT JOIN LATERAL (
                        SELECT o.store_fname FROM {S}.ir_attachment o
                        WHERE o.name = d19.module || '.' || d19.name AND o.type = 'binary'
                          AND o.store_fname IS NOT NULL AND o.res_model IS NULL
                        ORDER BY o.id DESC LIMIT 1) ov ON true
                   WHERE d19.model = 'ir.attachment' AND d19.module NOT IN ('__export__')
                     AND a19.url IS NOT NULL
                     AND a19.url LIKE '%/img/%'""")
    converted = same = 0
    Att = env["ir.attachment"].sudo()
    for res_id, url, override_fname in cr.fetchall():
        b19 = None
        if override_fname:
            p = os.path.join(config.FILESTORE_SRC, override_fname)
            if os.path.exists(p):
                with open(p, "rb") as f:
                    b19 = f.read()
        if b19 is None:
            b19 = _read_static(V19_STATIC, url)
            if b19 is None or b19 == _read_static(V20_STATIC, url):
                same += 1
                continue
        Att.browse(res_id).write({"type": "binary", "raw": BinaryBytes(b19)})
        converted += 1
    env.flush_all()
    return dict(synced=n, missing_files=missing, static_converted=converted, static_same=same)


MODULE_IMAGES = FuncStep("website.module_images", _module_images)

ARCH_SHIFT = SqlStep("website.arch_shift", f"""
    UPDATE ir_ui_view
       SET arch_db = etl_shift_urls(arch_db::text, {{OFF}})::jsonb
     WHERE id > {{OFF}}
       AND (arch_db::text LIKE '%/web/image/%' OR arch_db::text LIKE '%/web/content/%')""")

# visitor analytics (Website -> Reporting): visitors and the pages they opened. Language and
# country by code; a page that was not copied (prod's untouched module pages) is the module page
# with the same URL on 20. Found by the full-table sweep of the prod pull.
VISITORS = TableCopy(
    "website.visitor", "website_visitor",
    joins=[IDM("pa", "res.partner", "s.partner_id"),
           "LEFT JOIN {S}.res_lang sl ON sl.id = s.lang_id", "LEFT JOIN res_lang tl ON tl.code = sl.code",
           "LEFT JOIN {S}.res_country sc ON sc.id = s.country_id", "LEFT JOIN res_country tc ON tc.code = sc.code"],
    overrides={"id": "s.id + {OFF}", "partner_id": "pa.new_id", "lang_id": "tl.id", "country_id": "tc.id",
               "website_id": "CASE WHEN s.website_id IS NOT NULL THEN 1 END"},
    register=("website.visitor", "website_visitor"), on_conflict="DO NOTHING",
)
TRACKS = TableCopy(
    "website.track", "website_track",
    joins=["JOIN website_visitor v ON v.id = s.visitor_id + {OFF}",
           "LEFT JOIN website_page wp ON wp.id = s.page_id + {OFF}",
           "LEFT JOIN {S}.website_page sp ON sp.id = s.page_id",
           "LEFT JOIN LATERAL (SELECT min(x.id) AS id FROM website_page x "
           "WHERE x.url->>'en_US' = sp.url AND x.id < {OFF}) mp ON true"],
    overrides={"id": "s.id + {OFF}", "visitor_id": "v.id", "page_id": "coalesce(wp.id, mp.id)"},
    chunk=50000, on_conflict="DO NOTHING",
)


STEPS = [VIEWS, PAGES, SETTINGS, CLEANUP, REPAIRS, MODULE_IMAGES, ARCH_SHIFT, VISITORS, TRACKS]
