"""Phase 'account_refs': chart-level reference data. The v20 l10n_in template ships its own
chart (121 accounts, 279 taxes); 19 accounts are reused by code, referenced taxes are mapped
by (name, type, amount) and cloned when absent. Unreferenced legacy taxes are NOT migrated
(rules doc §F.4)."""
import logging

from ..core import OrmStep, FuncStep, Resolver, SqlStep
from .. import config

_logger = logging.getLogger("etl.account_refs")
S = config.SRC_SCHEMA
tr = Resolver._tr


def _account_transform(vals, row, ctx):
    code = row.get("code_store") or {}
    vals["code"] = code.get(str(config.COMPANY)) if isinstance(code, dict) else code
    vals.pop("code_store", None)
    return vals


ACCOUNTS = OrmStep("account.account", key=["code"], transform=_account_transform, update_existing=False,
                   skip={"code_store", "deprecated", "create_asset", "multiple_assets_per_line", "asset_model_ids",
                         "l10n_in_tds_tcs_section_id", "root_id", "group_id", "company_id", "company_ids", "used",
                         "current_balance", "related_taxes_amount"})


def _account_flags(engine, ctx):
    """accounts reused from the 20 template keep 19's reconcile flag."""
    cr = engine.cr
    cr.execute(f"""UPDATE account_account b SET reconcile = a.reconcile
                   FROM etl_idmap m JOIN {S}.account_account a ON a.id = m.old_id
                   WHERE m.model = 'account.account' AND b.id = m.new_id AND b.reconcile IS DISTINCT FROM a.reconcile""")
    return dict(reconcile_flags_aligned=cr.rowcount)


ACCOUNT_FLAGS = FuncStep("account.account.flags", _account_flags)

TAX_GROUPS = OrmStep("account.tax.group", key=["name"], update_existing=False,
                     skip={"tax_payable_account_id", "tax_receivable_account_id", "advance_tax_payment_account_id",
                           "company_id"})

TAX_TYPE_MAP = {"tds_purchase": "tds", "tds_sale": "tds"}


def _taxes(engine, ctx):
    """Map only the taxes REFERENCED by business data (move lines, products, job types) onto the
    20 template by (name, type, amount); clone the referenced ones that have no counterpart."""
    cr, env = engine.cr, engine.env
    cr.execute(f"""SELECT DISTINCT tax_id FROM (
                     SELECT account_tax_id AS tax_id FROM {S}.account_move_line_account_tax_rel
                     UNION SELECT tax_id FROM {S}.product_taxes_rel
                     UNION SELECT tax_id FROM {S}.product_supplier_taxes_rel
                     UNION SELECT tax_line_id FROM {S}.account_move_line WHERE tax_line_id IS NOT NULL
                     UNION SELECT tax_id FROM {S}.sudi_diamond_job_type_account_tax_rel
                   ) t WHERE tax_id IS NOT NULL""")
    used = [r[0] for r in cr.fetchall()]
    # pull group children of used group-taxes too
    cr.execute(f"SELECT parent_tax, child_tax FROM {S}.account_tax_filiation_rel WHERE parent_tax = ANY(%s)", (used,))
    kids_by_parent = {}
    for p, c in cr.fetchall():
        kids_by_parent.setdefault(p, []).append(c)
        if c not in used:
            used.append(c)
    cr.execute(f"""SELECT t.id, t.name, t.type_tax_use, t.amount, t.active, t.amount_type, t.include_base_amount,
                          t.is_base_affected, t.tax_group_id, t.description, t.tax_scope, t.sequence,
                          t.l10n_in_reverse_charge, t.l10n_in_tax_type, t.price_include_override
                   FROM {S}.account_tax t WHERE t.id = ANY(%s) ORDER BY (t.amount_type = 'group'), t.id""", (used,))
    rows = cr.fetchall()
    pairs, cloned, refused = [], [], 0
    for (tid, name, ttu, amount, active, atype, inc_base, base_aff, grp, desc, scope, seq,
         rc, in_type, price_inc) in rows:
        new = engine.resolver.resolve("account.tax", tid)
        if new:
            pairs.append((tid, new)); continue
        vals = {"name": tr(name), "type_tax_use": ttu, "amount_type": atype, "amount": amount, "active": active,
                "include_base_amount": inc_base, "is_base_affected": base_aff, "sequence": seq or 1,
                "tax_group_id": engine.resolver.resolve("account.tax.group", grp),
                "country_id": env.company.country_id.id, "company_id": env.company.id}
        if desc:
            vals["description"] = tr(desc)
        if scope:
            vals["tax_scope"] = scope
        if price_inc:
            vals["price_include_override"] = price_inc
        F = env["account.tax"]._fields
        if rc is not None and "l10n_in_reverse_charge" in F:
            vals["l10n_in_reverse_charge"] = rc
        if in_type and "l10n_in_tax_type" in F:
            vals["l10n_in_tax_type"] = TAX_TYPE_MAP.get(in_type, in_type)
        if atype == "group":
            kids = [engine.idmap.get("account.tax", c) for c in kids_by_parent.get(tid, [])]
            if not all(kids):
                refused += 1
                engine.idmap.refuse("account.tax", "account.tax", tid, f"group tax {tr(name)!r}: child tax unmapped")
                continue
            vals["children_tax_ids"] = [(6, 0, kids)]
        try:
            with cr.savepoint():
                rec = env["account.tax"].with_context(**config.SILENT_CTX).create(vals)
            pairs.append((tid, rec.id)); cloned.append(tr(name))
        except Exception as e:  # noqa: BLE001
            refused += 1
            engine.idmap.refuse("account.tax", "account.tax", tid,
                                f"clone failed for {tr(name)!r} {ttu} {amount}: {str(e).splitlines()[0][:200]}", vals)
    engine.idmap.add_many("account.tax", pairs)
    engine.resolver._cache = {k: v for k, v in engine.resolver._cache.items() if k[0] != "account.tax"}
    return dict(referenced=len(rows), mapped=len(pairs) - len(cloned), cloned=cloned, refused=refused)


TAXES = FuncStep("account.tax", _taxes)

JOURNALS = OrmStep("account.journal", key=["code"], update_existing=False,
                   skip={"autocheck_on_post", "alias_id", "alias_name", "alias_domain_id", "bank_account_id",
                         "default_account_id", "suspense_account_id", "profit_account_id", "loss_account_id",
                         "company_id", "sequence_override_regex", "json_activity_data", "current_statement_balance",
                         "has_statement_lines", "accounting_date"})
PAYMENT_TERMS = OrmStep("account.payment.term", key=["name"], update_existing=False,
                        skip={"line_ids", "company_id", "example_amount", "example_date", "example_invalid",
                              "example_preview", "example_preview_discount"})
FPOS = OrmStep("account.fiscal.position", key=["name"], update_existing=False,
               skip={"account_ids", "tax_ids", "company_id", "states_count", "l10n_in_tds_fiscal_position",
                     "l10n_in_tcs_fiscal_position"})
APLAN = OrmStep("account.analytic.plan", key=["name"], update_existing=False,
                skip={"parent_path", "complete_name", "root_id", "account_ids", "all_account_count", "account_count",
                      "children_ids", "children_count"})
AACCOUNT = OrmStep("account.analytic.account", key=["name"], update_existing=False,
                   skip={"root_plan_id", "line_ids", "invoice_count", "vendor_bill_count", "debit", "credit", "balance",
                         "company_id"})

# default sale/purchase taxes on products: the product phase ran before the tax map
# existed, so these m2m rows are synced here (found by the side-by-side flow QA: the
# job-work invoice came out untaxed because the service products had lost "5% GST S").
PRODUCT_TAXES = SqlStep("product.taxes_id", """
    INSERT INTO product_taxes_rel (prod_id, tax_id)
    SELECT mp.new_id, mt.new_id
    FROM {S}.product_taxes_rel r
    JOIN etl_idmap mp ON mp.model = 'product.template' AND mp.old_id = r.prod_id
    JOIN etl_idmap mt ON mt.model = 'account.tax' AND mt.old_id = r.tax_id
    ON CONFLICT DO NOTHING""")

PRODUCT_SUPPLIER_TAXES = SqlStep("product.supplier_taxes_id", """
    INSERT INTO product_supplier_taxes_rel (prod_id, tax_id)
    SELECT mp.new_id, mt.new_id
    FROM {S}.product_supplier_taxes_rel r
    JOIN etl_idmap mp ON mp.model = 'product.template' AND mp.old_id = r.prod_id
    JOIN etl_idmap mt ON mt.model = 'account.tax' AND mt.old_id = r.tax_id
    ON CONFLICT DO NOTHING""")

STEPS = [ACCOUNTS, ACCOUNT_FLAGS, TAX_GROUPS, TAXES, JOURNALS, PAYMENT_TERMS, FPOS, APLAN, AACCOUNT,
         PRODUCT_TAXES, PRODUCT_SUPPLIER_TAXES]
