"""Side-by-side QA, part 2: render the business-critical QWeb reports on the SAME
records (matched by name) on both versions and dump the visible text for diffing.

    QA_OUT=/opt/odoo20/etl/qa/rep19.json  odoo-bin shell ... < renders.py

Reports covered: the diamond job-work receipt/delivery report (the document that goes
to customers over WhatsApp) and the standard invoice print, plus one real PDF render
to prove the wkhtmltopdf pipeline.
"""
import json
import os
import re

OUT = os.environ.get("QA_OUT", "/opt/odoo20/etl/qa/rep.json")
env = env  # noqa: F821

from lxml import html as lhtml  # noqa: E402


def html_text(html):
    doc = lhtml.fromstring(html if isinstance(html, str) else html.decode())
    for el in doc.xpath("//script | //style"):
        el.getparent().remove(el)
    txt = " ".join(doc.itertext())
    return re.sub(r"\s+", " ", txt).strip()


def render(report_ref, docids):
    Report = env["ir.actions.report"].with_context(lang="en_US")
    html = Report._render_qweb_html(report_ref, docids)[0]
    return html_text(html)


res = {"renders": {}, "pdf": {}}

# same records on both sides: newest done receipts / deliveries / posted invoices BY NAME
env.cr.execute("""SELECT name FROM stock_picking WHERE state='done' ORDER BY id DESC LIMIT 6""")
picking_names = [r[0] for r in env.cr.fetchall()]
env.cr.execute("""SELECT name FROM account_move WHERE state='posted' AND move_type='out_invoice'
                  ORDER BY id DESC LIMIT 3""")
invoice_names = [r[0] for r in env.cr.fetchall()]
res["picked"] = {"pickings": picking_names, "invoices": invoice_names}

for name in picking_names:
    pick = env["stock.picking"].search([("name", "=", name)], limit=1)
    try:
        res["renders"][f"jobwork/{name}"] = render("diamond.action_report_diamond_job_work", pick.ids)
    except Exception as e:  # noqa: BLE001
        res["renders"][f"jobwork/{name}"] = f"ERROR {type(e).__name__}: {e}"
    env.cr.rollback()

for name in invoice_names:
    inv = env["account.move"].search([("name", "=", name)], limit=1)
    try:
        res["renders"][f"invoice/{name}"] = render("account.account_invoices", inv.ids)
    except Exception as e:  # noqa: BLE001
        res["renders"][f"invoice/{name}"] = f"ERROR {type(e).__name__}: {e}"
    env.cr.rollback()

# PDF pipeline proof (one document is enough; parity of content is the html diff above)
if picking_names:
    pick = env["stock.picking"].search([("name", "=", picking_names[0])], limit=1)
    try:
        pdf = env["ir.actions.report"]._render_qweb_pdf("diamond.action_report_diamond_job_work", pick.ids)[0]
        res["pdf"][picking_names[0]] = {"ok": True, "bytes": len(pdf), "magic": pdf[:4].decode("latin1")}
    except Exception as e:  # noqa: BLE001
        res["pdf"][picking_names[0]] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    env.cr.rollback()

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    json.dump(res, f, indent=1, sort_keys=True)
errs = sum(1 for v in res["renders"].values() if isinstance(v, str) and v.startswith("ERROR"))
print(f"QA-RENDERS-DONE {OUT} renders={len(res['renders'])} errors={errs}")
