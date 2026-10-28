"""Natural-key lookups: how an unmapped 18 many2one is found on 20 (plan §1.4, rules §7).
Each function receives the 18 row (dict of all columns) and the resolver; returns a 20 record or None.
Registered through @natural(relation); used automatically by the ORM lane for that relation."""
from .core import natural, Resolver

tr = Resolver._tr


@natural("res.country")
def _country(row, r):
    return r.search1("res.country", [("code", "=", row["code"])])


@natural("res.country.state")
def _state(row, r):
    country = r.resolve("res.country", row["country_id"])
    rec = r.search1("res.country.state", [("country_id", "=", country), ("code", "=", row["code"])])
    if rec is None and country:  # user-made states of 18 (IN-201301 ...) are created as-is
        rec = r.env["res.country.state"].sudo().create({"country_id": country, "code": row["code"], "name": tr(row["name"]) or row["code"]})
    return rec


@natural("res.currency")
def _currency(row, r):
    rec = r.search1("res.currency", [("name", "=", row["name"])])
    if rec is not None and not rec.active:
        rec.write({"active": True})
    return rec


@natural("res.lang")
def _lang(row, r):
    return r.search1("res.lang", [("code", "=", row["code"])])


@natural("uom.uom")
def _uom(row, r):
    rec = r.search1("uom.uom", [("name", "=", tr(row["name"]))])
    if rec is not None and not rec.active:
        rec.write({"active": True})
    if rec is None:
        rec = r.env["uom.uom"].sudo().create({"name": tr(row["name"]), "relative_factor": 1.0})
    return rec


@natural("res.partner.industry")
def _industry(row, r):
    return r.search1("res.partner.industry", [("name", "=", tr(row["name"]))])


@natural("res.partner.category")
def _pcateg(row, r):
    return r.search1("res.partner.category", [("name", "=", tr(row["name"]))])


@natural("account.account")
def _account(row, r):
    code = row.get("code")
    if isinstance(code, dict):
        code = code.get("1") or next(iter(code.values()), None)
    if not code and row.get("code_store"):
        cs = row["code_store"]; code = cs.get("1") if isinstance(cs, dict) else cs
    return r.search1("account.account", [("code", "=", code)]) if code else None


TAX_ALIAS = {  # 18 name -> 20 template name (export variants renamed on 20)
    " (SZ/EX)": " (EXP)",
    " (SZ/EX-LUT)": " (EXP-LUT)",
}


@natural("account.tax")
def _tax(row, r):
    name = tr(row["name"]) or ""
    cands = [name] + [name.replace(a, b) for a, b in TAX_ALIAS.items() if a in name]
    for n in cands:
        rec = r.search1("account.tax", [("name", "=", n), ("type_tax_use", "=", row["type_tax_use"]),
                                        ("amount", "=", row["amount"])])
        if rec:
            return rec
    for n in cands:  # same name, different amount representation
        rec = r.search1("account.tax", [("name", "=", n), ("type_tax_use", "=", row["type_tax_use"])])
        if rec:
            return rec
    return None


@natural("account.tax.group")
def _tax_group(row, r):
    return r.search1("account.tax.group", [("name", "=", tr(row["name"]))])


@natural("account.journal")
def _journal(row, r):
    return r.search1("account.journal", [("code", "=", row["code"])])


@natural("account.payment.term")
def _pterm(row, r):
    return r.search1("account.payment.term", [("name", "=", tr(row["name"]))])


@natural("account.fiscal.position")
def _fpos(row, r):
    return r.search1("account.fiscal.position", [("name", "=", tr(row["name"]))])


@natural("account.incoterms")
def _incoterm(row, r):
    return r.search1("account.incoterms", [("code", "=", row["code"])])


@natural("account.analytic.plan")
def _aplan(row, r):
    return r.search1("account.analytic.plan", [("name", "=", tr(row["name"]))])


@natural("product.category")
def _pcat(row, r):
    return r.search1("product.category", [("complete_name", "=", row["complete_name"])]) or \
        r.search1("product.category", [("name", "=", tr(row["name"]))])


@natural("stock.warehouse")
def _wh(row, r):
    return r.search1("stock.warehouse", [("code", "=", row["code"])]) or r.env["stock.warehouse"].sudo().search([], limit=1)


@natural("stock.location")
def _loc(row, r):
    name = row["complete_name"]
    rec = r.search1("stock.location", [("complete_name", "=", name)])
    if rec is None and name and "/" in name:
        # 18 warehouse code U1 vs the 20 warehouse code: match on the part after the warehouse
        tail = "/".join(name.split("/")[1:])
        rec = r.search1("stock.location", [("complete_name", "=like", "%/" + tail), ("usage", "=", row["usage"])])
    if rec is None:
        rec = r.search1("stock.location", [("name", "=", tr(row["name"])), ("usage", "=", row["usage"])])
    return rec


@natural("stock.picking.type")
def _ptype(row, r):
    wh = r.resolve("stock.warehouse", row["warehouse_id"]) if row.get("warehouse_id") else None
    dom = [("sequence_code", "=", row["sequence_code"])] if row.get("sequence_code") else [("name", "=", tr(row["name"]))]
    if wh:
        dom.append(("warehouse_id", "=", wh))
    return r.search1("stock.picking.type", dom) or r.search1("stock.picking.type", [("code", "=", row["code"]), ("warehouse_id", "=", wh)])


@natural("mail.message.subtype")
def _subtype(row, r):
    return r.search1("mail.message.subtype", [("name", "=", tr(row["name"])), ("res_model", "=", row.get("res_model"))])


@natural("mail.activity.type")
def _atype(row, r):
    return r.search1("mail.activity.type", [("name", "=", tr(row["name"]))])


@natural("ir.model")
def _model(row, r):
    return r.search1("ir.model", [("model", "=", row["model"])])


@natural("ir.model.fields")
def _field(row, r):
    return r.search1("ir.model.fields", [("model", "=", row["model"]), ("name", "=", row["name"])])


@natural("res.users")
def _user(row, r):
    return r.search1("res.users", [("login", "=", row["login"])])


@natural("res.groups")
def _group(row, r):
    return None  # groups only by xmlid: 18 custom groups do not exist


@natural("ir.ui.menu")
def _menu(row, r):
    return None  # menus only by xmlid


@natural("crm.team")
def _team(row, r):
    return r.search1("crm.team", [("name", "=", tr(row["name"]))])


@natural("crm.stage")
def _cstage(row, r):
    return r.search1("crm.stage", [("name", "=", tr(row["name"]))])


@natural("crm.tag")
def _ctag(row, r):
    return r.search1("crm.tag", [("name", "=", (tr(row["name"]) or "").strip())])


@natural("hr.department")
def _dept(row, r):
    return r.search1("hr.department", [("name", "=", tr(row["name"]))])


@natural("project.task.type")
def _tstage(row, r):
    return r.search1("project.task.type", [("name", "=", tr(row["name"]))])


@natural("helpdesk.stage")
def _hstage(row, r):
    return r.search1("helpdesk.stage", [("name", "=", tr(row["name"]))])


@natural("helpdesk.team")
def _hteam(row, r):
    return r.search1("helpdesk.team", [("name", "=", tr(row["name"]))])


@natural("ir.sequence")
def _seq(row, r):
    return r.search1("ir.sequence", [("code", "=", row["code"])]) if row.get("code") else None


@natural("ir.actions.report")
def _report(row, r):
    return r.search1("ir.actions.report", [("report_name", "=", row["report_name"])])


@natural("mail.template")
def _tmpl(row, r):
    return r.search1("mail.template", [("name", "=", tr(row["name"]))])


@natural("utm.source")
def _usrc(row, r):
    return r.search1("utm.source", [("name", "=", row["name"])])


@natural("utm.medium")
def _umed(row, r):
    return r.search1("utm.medium", [("name", "=", row["name"])])


@natural("utm.campaign")
def _ucamp(row, r):
    return r.search1("utm.campaign", [("name", "=", row["name"])])


@natural("res.partner.title")
def _title(row, r):
    return None  # model gone on 20; handled by the partner transform (prefix)


@natural("account.tax.repartition.line")
def _rep_line(row, r):
    tax = r.resolve("account.tax", row["tax_id"])
    if not tax:
        return None
    dom = [("tax_id", "=", tax), ("repartition_type", "=", row["repartition_type"]), ("document_type", "=", row["document_type"]),
           ("factor_percent", "=", row["factor_percent"])]
    recs = r.env["account.tax.repartition.line"].sudo().search(dom)
    if len(recs) > 1 and row.get("account_id"):
        acc = r.resolve("account.account", row["account_id"])
        recs = recs.filtered(lambda x: x.account_id.id == acc) or recs
    return recs[:1] or None


@natural("account.account.tag")
def _acc_tag(row, r):
    return r.search1("account.account.tag", [("name", "=", tr(row["name"])), ("applicability", "=", row.get("applicability") or "taxes")])


@natural("account.payment.method")
def _pay_method(row, r):
    return r.search1("account.payment.method", [("code", "=", row["code"]), ("payment_type", "=", row["payment_type"])])


@natural("account.payment.method.line")
def _pay_method_line(row, r):
    journal = r.resolve("account.journal", row["journal_id"])
    method = r.resolve("account.payment.method", row["payment_method_id"])
    if not (journal and method):
        return None
    return r.search1("account.payment.method.line", [("journal_id", "=", journal), ("payment_method_id", "=", method)])


@natural("res.company")
def _company(row, r):
    return r.env.company
