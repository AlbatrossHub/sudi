"""Side-by-side QA, part 1: full backend inventory + server-side view-render scan.

Runs UNCHANGED inside an Odoo 19 or Odoo 20 shell (piped on stdin), writes one JSON:

    QA_OUT=/opt/odoo20/etl/qa/inv19.json \
    /opt/odoo19/odoo19-venv/bin/python /opt/odoo19/odoo19/odoo-bin shell \
        -c .../odoo19-baseline.conf -d sudi19_fx --no-http < inventory.py

Collected per side (as admin):
  - installed modules
  - every menu VISIBLE to admin (complete path) with its action + res_model
  - get_views() on every window action  -> catches every broken view arch server-side
    (this is the "open every screen" test without a browser: if get_views passes,
    the view compiles; browser crawl then covers the JS layer)
  - every report action
  - ir.sequence next numbers (code -> number_next_actual)
  - ACL smoke: per active internal user, search_count on key business models
"""
import json
import os
import traceback

OUT = os.environ.get("QA_OUT", "/opt/odoo20/etl/qa/inv.json")
env = env  # noqa: F821  (provided by odoo shell)
release = __import__("odoo.release", fromlist=["release"])

res = {"version": release.version, "db": env.cr.dbname}

# --- modules ------------------------------------------------------------------
mods = env["ir.module.module"].sudo().search([("state", "=", "installed")])
res["modules"] = sorted(mods.mapped("name"))

# --- menus visible to admin (the REAL admin, uid 2: the shell superuser's group
# set differs between databases and would fake menu diffs) -------------------------
_admin = env["res.users"].sudo().search([("login", "=", "admin")], limit=1)
Menu = env["ir.ui.menu"].with_user(_admin).with_context(lang="en_US")
visible = Menu.browse(list(Menu._visible_menu_ids(debug=True))).sudo()
menus = {}
for m in visible:
    entry = {"id": m.id}
    if m.action:
        entry.update(action_type=m.action._name, action_id=m.action.id,
                     action_name=(m.action.name or ""))
        if m.action._name == "ir.actions.act_window":
            entry["res_model"] = m.action.res_model
        elif m.action._name == "ir.actions.client":
            entry["tag"] = m.action.tag
    menus[m.complete_name] = entry
res["menus"] = menus

# --- get_views scan over every window action ------------------------------------
scan = {}
for act in env["ir.actions.act_window"].sudo().search([]):
    model = act.res_model
    key = f"{act.id}:{model}:{act.name}"
    if model not in env:
        scan[key] = {"status": "NO-MODEL"}
        continue
    views = [(v.view_id.id, v.view_mode) for v in act.view_ids]
    if not views:
        views = [(False, vm) for vm in (act.view_mode or "list,form").split(",")]
    if "form" not in [v[1] for v in views]:
        views.append((False, "form"))
    try:
        env[model].with_context(lang="en_US").get_views(views)
        scan[key] = {"status": "OK", "views": [v[1] for v in views]}
    except Exception as e:  # noqa: BLE001
        scan[key] = {"status": "ERROR", "error": f"{type(e).__name__}: {e}",
                     "trace": traceback.format_exc()[-1500:]}
    env.cr.rollback()
res["view_scan"] = scan

# --- report actions ---------------------------------------------------------------
res["reports"] = {
    f"{r.model}/{r.report_name}": {"name": r.name, "type": r.report_type}
    for r in env["ir.actions.report"].sudo().search([])
}

# --- sequences ---------------------------------------------------------------------
seqs = {}
for s in env["ir.sequence"].sudo().with_context(active_test=False).search([]):
    key = s.code or f"noname/{s.prefix or s.name}"
    seqs[key] = {"prefix": s.prefix or "", "next": s.number_next_actual, "active": s.active}
res["sequences"] = seqs

# --- ACL smoke: what can each real user see -----------------------------------------
ACL_MODELS = ["stock.picking", "stock.move", "account.move", "res.partner",
              "sudi.diamond.billing.line", "sudi.diamond.job.type",
              "sudi.diamond.partner.service.price", "project.task",
              "account.analytic.line", "discuss.channel"]
acl = {}
users = env["res.users"].sudo().search([("active", "=", True), ("share", "=", False)])
for u in users:
    row = {}
    for model in ACL_MODELS:
        if model not in env:
            row[model] = "NO-MODEL"
            continue
        try:
            row[model] = env[model].with_user(u).search_count([])
        except Exception as e:  # noqa: BLE001
            row[model] = f"DENIED:{type(e).__name__}"
        env.cr.rollback()
    acl[u.login] = row
res["acl"] = acl

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    json.dump(res, f, indent=1, sort_keys=True)
print(f"QA-INVENTORY-DONE {OUT} menus={len(menus)} actions={len(scan)} "
      f"errors={sum(1 for v in scan.values() if v['status'] == 'ERROR')}")
