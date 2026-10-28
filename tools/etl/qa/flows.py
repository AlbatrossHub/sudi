"""Side-by-side QA, part 3: execute the REAL business flows identically on the v19 and
v20 clones and record every observable output for diffing. Runs inside odoo shell:

    QA_OUT=/opt/odoo20/etl/qa/flow19.json  odoo-bin shell -d sudi19_fx --no-http < flows.py

The golden path exercised end to end (all custom code):
  public jangad receipt (the customer app entry) -> move + billing-line auto-pricing
  (from the migrated partner service prices) -> pickup confirm -> validate -> delivery
  order -> mark delivered -> action_sudi_create_invoice -> post (sequence parity!) ->
  register payment -> timesheet timer -> WhatsApp template render -> chatter post.

Everything runs in ONE transaction that is ROLLED BACK at the end: the clones stay
clean and the run is repeatable. Sequence numbers drawn inside the transaction are
still comparable across sides because both start from the same migrated counters.
"""
import base64
import json
import os
import traceback

OUT = os.environ.get("QA_OUT", "/opt/odoo20/etl/qa/flow.json")
env = env  # noqa: F821
release = __import__("odoo.release", fromlist=["release"])
MAJOR = int(str(release.version).split("saas~")[-1].split(".")[0])

admin = env["res.users"].sudo().search([("login", "=", "admin")], limit=1)
env = env(user=admin.id, context={"lang": "en_US", "tz": "Asia/Kolkata"})

# 1x1 white PNG
PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
))

results = {"version": release.version, "steps": {}}
state = {}


STEPS = []


def step(name):
    def deco(fn):
        STEPS.append((name, fn))
        return fn
    return deco


def run_steps():
    for name, fn in sorted(STEPS):  # chronological: names are numbered
        try:
            out = fn() or {}
            results["steps"][name] = {"status": "OK", **out}
            print(f"  [OK  ] {name}: {out}")
        except Exception as e:  # noqa: BLE001
            results["steps"][name] = {"status": "ERROR", "error": f"{type(e).__name__}: {e}",
                                      "trace": traceback.format_exc()[-1200:]}
            print(f"  [FAIL] {name}: {type(e).__name__}: {e}")


# ── fixtures: same records chosen by the same deterministic queries on both sides ──
env.cr.execute("""
    SELECT p.id, p.partner_id FROM stock_picking p
    JOIN stock_picking_type t ON t.id = p.picking_type_id
    WHERE p.state = 'done' AND t.code = 'incoming' AND p.partner_id IS NOT NULL
      AND p.sudi_is_diamond_job_work
    ORDER BY p.name DESC LIMIT 1""")
ref_pick_id, partner_id = env.cr.fetchone()
ref_pick = env["stock.picking"].browse(ref_pick_id)
partner = env["res.partner"].browse(partner_id)
env.cr.execute("SELECT product_id FROM stock_move WHERE picking_id=%s ORDER BY id LIMIT 1", (ref_pick_id,))
product = env["product.product"].browse(env.cr.fetchone()[0])
env.cr.execute("""
    SELECT jt.id FROM sudi_diamond_partner_service_price sp
    JOIN sudi_diamond_job_type jt ON jt.id = sp.job_type_id
    WHERE sp.partner_id = %s AND sp.price > 0 AND jt.active AND sp.active
    ORDER BY sp.price DESC, jt.id LIMIT 1""", (partner.commercial_partner_id.id,))
row = env.cr.fetchone()
job_type = env["sudi.diamond.job.type"].browse(row[0]) if row else env["sudi.diamond.job.type"].search([], limit=1)
results["fixtures"] = {"ref_receipt": ref_pick.name, "partner": partner.display_name,
                       "product": product.display_name, "job_type": job_type.name}
print(f"fixtures: {results['fixtures']}")


@step("01_partner_chatter")
def _partner():
    p = env["res.partner"].create({"name": "QA Parity Partner", "phone": "+91 9898000001",
                                   "email": "qa.parity@example.com", "city": "Surat"})
    msg = p.message_post(body="QA parity chatter note", message_type="comment")
    act = env["mail.activity"].create({
        "res_model_id": env["ir.model"]._get_id("res.partner"), "res_id": p.id,
        "activity_type_id": env.ref("mail.mail_activity_data_todo").id,
        "summary": "QA parity activity"})
    return {"partner": p.name, "is_company": p.is_company, "message": bool(msg.id),
            "activity": act.summary, "followers": len(p.message_follower_ids)}


@step("02_public_receipt")
def _receipt():
    phone = partner.phone or "+91 9898000001"  # .mobile was removed in Odoo 19
    # the real callers (website JSON controller / Flutter app) send base64 as STR
    pick = env["stock.picking"].sudi_create_public_jangad_receipt(phone, PNG.decode())
    state["receipt"] = pick
    return {"name": pick.name, "state": pick.state, "partner": pick.partner_id.display_name,
            "job_work": pick.sudi_is_diamond_job_work,
            "has_jangad_image": bool(pick.sudi_jangad_image)}


@step("04_add_move_billing")
def _move():
    pick = state["receipt"]
    mv = env["stock.move"].create({
        "picking_id": pick.id, "product_id": product.id, "product_uom_qty": 2,
        "location_id": pick.location_id.id, "location_dest_id": pick.location_dest_id.id,
        "company_id": pick.company_id.id,
        "sudi_job_type_id": job_type.id, "sudi_pcs_qty": 2, "sudi_carats": 1.5,
    })
    state["move"] = mv
    pick.action_sudi_recompute_billing_details()
    bl = pick.sudi_billing_line_ids.filtered("active")
    state["billing"] = bl
    return {"moves": len(pick.move_ids), "billing_lines": len(bl),
            "billing": [{"job_type": l.job_type_id.name, "qty": l.quantity,
                         "price_unit": l.price_unit, "subtotal": l.price_subtotal} for l in bl]}


@step("03_confirm_pickup")
def _pickup():
    pick = state["receipt"]
    pick.action_sudi_confirm_pickup()
    return {k: getattr(pick, k) for k in ("sudi_pickup_status",) if k in pick._fields}


@step("06_validate_receipt")
def _validate():
    pick = state["receipt"]
    try:
        pick.action_confirm()  # incoming moves self-assign on confirm
    except Exception:
        pass
    for mv in pick.move_ids:
        mv.quantity = mv.product_uom_qty
        mv.picked = True
    res = pick.button_validate()
    return {"state": pick.state, "validate_returned": res if isinstance(res, bool) else type(res).__name__,
            "move_states": pick.move_ids.mapped("state")}


@step("07_delivery_order")
def _delivery():
    pick = state["receipt"]
    pick.action_sudi_create_delivery_order()
    dlv = pick.sudi_delivery_ids
    state["delivery"] = dlv
    return {"deliveries": dlv.mapped("name"), "states": dlv.mapped("state"),
            "moves": len(dlv.move_ids)}


@step("08_mark_delivered")
def _delivered():
    dlv = state["delivery"]
    for mv in dlv.move_ids:
        mv.quantity = mv.product_uom_qty
        mv.picked = True
    if "sudi_pod_receiver_name" in dlv._fields:  # diamond 1.7.0 proof-of-delivery
        dlv.action_sudi_mark_delivered(receiver_name="QA Receiver", signature=PNG.decode())
    else:
        dlv.action_sudi_mark_delivered()
    return {"states": dlv.mapped("state"),
            "pod": dlv[:1].sudi_pod_receiver_name if "sudi_pod_receiver_name" in dlv._fields else "n/a"}


@step("09_create_invoice")
def _invoice():
    pick = state["receipt"]
    pick.action_sudi_create_invoice()
    inv = env["account.move"].search([("sudi_receipt_id", "=", pick.id)], limit=1)
    state["invoice"] = inv
    return {"lines": [{"name": l.name, "qty": l.quantity, "price_unit": l.price_unit,
                       "taxes": l.tax_ids.mapped("name"), "subtotal": l.price_subtotal}
                      for l in inv.invoice_line_ids],
            "amount_untaxed": inv.amount_untaxed, "amount_tax": inv.amount_tax,
            "amount_total": inv.amount_total, "jobwork_flag": inv.sudi_is_diamond_job_work_invoice}


@step("10_post_invoice")
def _post():
    inv = state["invoice"]
    inv.action_post()
    return {"name": inv.name, "state": inv.state, "payment_state": inv.payment_state,
            "tax_totals_sane": bool(inv.amount_total >= inv.amount_untaxed)}


@step("11_register_payment")
def _payment():
    inv = state["invoice"]
    wiz = env["account.payment.register"].with_context(
        active_model="account.move", active_ids=inv.ids).create({})
    wiz.action_create_payments()
    return {"payment_state": inv.payment_state, "amount": wiz.amount}


@step("12_timesheet_timer")
def _timer():
    pick = state["receipt"]
    pick.action_timer_start()
    running = pick.is_timer_running if "is_timer_running" in pick._fields else None
    pick.action_timer_stop()
    return {"started_then_stopped": True, "was_running": running,
            "timesheets": len(pick.sudi_timesheet_ids) if "sudi_timesheet_ids" in pick._fields else "n/a"}


@step("13_whatsapp_template_render")
def _wa_render():
    pick = state["receipt"]
    body = pick._sudi_render_event_whatsapp_message("pickup_scheduled")
    if isinstance(body, tuple):
        body = body[0]
    return {"rendered": (body or "")[:400]}


@step("14_receipt_chatter")
def _chatter():
    pick = state["receipt"]
    n0 = len(pick.message_ids)
    pick.message_post(body="QA parity receipt note", message_type="comment")
    return {"messages_before": n0, "messages_after": len(pick.message_ids),
            "followers": pick.message_follower_ids.partner_id.mapped("name")}


@step("05_department_transfer")
def _dept():
    pick = state["receipt"]
    pick.action_confirm()  # transfer requires state == assigned
    res = pick.action_sudi_transfer_department()
    return {"returned": res.get("res_model") if isinstance(res, dict) else str(type(res).__name__)}


run_steps()

results["names"] = {
    "receipt": state.get("receipt") and state["receipt"].name,
    "delivery": state.get("delivery") and state["delivery"].mapped("name"),
    "invoice": state.get("invoice") and state["invoice"].name,
}

env.cr.rollback()  # leave the clone exactly as it was

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    json.dump(results, f, indent=1, sort_keys=True, default=str)
fails = [k for k, v in results["steps"].items() if v["status"] != "OK"]
print(f"QA-FLOWS-DONE {OUT} steps={len(results['steps'])} failed={fails}")
