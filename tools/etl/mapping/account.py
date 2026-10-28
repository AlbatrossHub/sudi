"""Phase 'account': the 62 journal entries + 371 items through the ORM with invoice
synchronisation disabled (lines land exactly as on 19), then state/reconciliation restored
by SQL and the residual computes re-run. No payments/banks on prod."""
import logging

from ..core import OrmStep, FuncStep, TableCopy, SqlStep, IDM
from .. import config

_logger = logging.getLogger("etl.account")
S = config.SRC_SCHEMA
COMPANY = "(SELECT id FROM res_company ORDER BY id LIMIT 1)"
ACCT_CTX = {"skip_invoice_sync": True, "skip_invoice_line_sync": True, "skip_account_move_synchronization": True,
            "check_move_validity": False, "dynamic_unlink": True}


def _register_refs(engine, ctx):
    cr = engine.cr
    cr.execute(f"""INSERT INTO etl_idmap (model, old_id, new_id)
                   SELECT 'res.currency', a.id, b.id FROM {S}.res_currency a JOIN res_currency b ON b.name = a.name
                   ON CONFLICT DO NOTHING""")
    cr.execute(f"INSERT INTO etl_idmap (model, old_id, new_id) SELECT 'res.company', id, {COMPANY} FROM {S}.res_company ON CONFLICT DO NOTHING")
    cr.execute(f"""INSERT INTO etl_idmap (model, old_id, new_id)
                   SELECT 'account.account.tag', d18.res_id, d20.res_id FROM {S}.ir_model_data d18
                   JOIN ir_model_data d20 ON d20.module = d18.module AND d20.name = d18.name AND d20.model = 'account.account.tag'
                   WHERE d18.model = 'account.account.tag' ON CONFLICT DO NOTHING""")
    for m in ("res.currency", "res.company", "account.account.tag"):
        engine.idmap._cache.pop(m, None)
    return {}


REFS = FuncStep("account.refs", _register_refs)


def _move_transform(vals, row, ctx):
    vals["state"] = "draft"
    checked = row.get("checked")
    vals["review_state"] = "reviewed" if checked else ("todo" if row.get("state") == "posted" and checked is False else "no_review")
    for k in ("origin_payment_id", "statement_line_id", "reversed_entry_id"):
        vals.pop(k, None)
    return vals


MOVES = OrmStep(
    "account.move", transform=_move_transform, batch=200, context=ACCT_CTX,
    skip={"line_ids", "invoice_line_ids", "is_storno", "checked", "expense_sheet_id", "stock_move_id",
          "tax_closing_report_id", "asset_id", "asset_move_type", "asset_depreciation_beginning_date",
          "asset_number_days", "asset_value_change", "depreciation_value", "asset_ids", "asset_id_display_name",
          "generating_loan_line_id", "is_loan_payment_move", "loan_id",
          "l10n_in_gst_return_period_id", "l10n_in_withholding_ref_move_id", "l10n_in_is_withholding",
          "l10n_in_withholding_ref_payment_id", "l10n_in_edi_status", "l10n_in_edi_error", "l10n_in_edi_content",
          "message_main_attachment_id", "origin_payment_id", "statement_line_id", "reversed_entry_id",
          "reversal_move_ids", "payment_ids", "transaction_ids", "state", "payment_state",
          "amount_untaxed", "amount_tax", "amount_total", "amount_residual", "amount_untaxed_signed",
          "amount_tax_signed", "amount_total_signed", "amount_residual_signed", "amount_total_in_currency_signed",
          "quick_edit_total_amount", "tax_totals", "needed_terms_dirty", "sequence_prefix", "sequence_number",
          "made_sequence_gap", "access_token", "inalterable_hash", "secure_sequence_number",
          "extract_partner_name", "extract_state", "extract_status", "extract_error_message", "extract_word_ids",
          "extract_attachment_id", "extract_can_show_send_button", "extract_document_uuid", "extract_state_processed",
          "edi_document_ids", "edi_state", "invoice_pdf_report_id", "invoice_pdf_report_file", "attachment_ids",
          "debit_origin_id", "duplicated_ref_ids", "send_and_print_values", "incoterm_location"},
    force_fields={"name", "date", "invoice_date", "invoice_date_due", "ref", "narration", "journal_id", "partner_id",
                  "currency_id", "fiscal_position_id", "invoice_payment_term_id", "invoice_user_id", "team_id",
                  "partner_shipping_id", "partner_bank_id", "invoice_origin", "payment_reference", "auto_post",
                  "posted_before", "always_tax_exigible", "invoice_incoterm_id", "l10n_in_gst_treatment",
                  "l10n_in_state_id", "sudi_receipt_id", "sudi_is_diamond_job_work_invoice",
                  "sudi_billing_period_from", "sudi_billing_period_to"},
)


def _line_transform(vals, row, ctx):
    if not vals.get("move_id"):
        return None
    if "balance" not in vals:
        vals["balance"] = (row.get("debit") or 0.0) - (row.get("credit") or 0.0)
    vals.pop("full_reconcile_id", None)
    return vals


LINES = OrmStep(
    "account.move.line", transform=_line_transform, batch=500, context=ACCT_CTX,
    skip={"tax_tag_invert", "full_reconcile_id", "matched_debit_ids", "matched_credit_ids", "reconciled",
          "amount_residual", "amount_residual_currency", "price_subtotal", "price_total", "parent_state",
          "move_name", "date", "ref", "journal_id", "company_currency_id", "account_root_id", "account_type",
          "account_internal_group", "is_storno", "invoice_date", "payment_date", "statement_line_id", "statement_id",
          "cumulated_balance", "discount_date", "discount_amount_currency", "discount_balance", "is_downpayment",
          "is_imported", "tax_key", "compute_all_tax", "compute_all_tax_dirty", "epd_key", "epd_needed", "epd_dirty",
          "term_key", "deductible_amount", "expense_id", "asset_ids", "non_deductible_tax_value",
          "sudi_stock_move_id", "sudi_billing_line_id", "l10n_in_withhold_tax_amount"},
    force_fields={"name", "debit", "credit", "balance", "amount_currency", "currency_id", "date_maturity", "quantity",
                  "price_unit", "discount", "product_id", "product_uom_id", "account_id", "partner_id", "tax_line_id",
                  "tax_repartition_line_id", "tax_base_amount", "display_type", "sequence", "tax_group_id",
                  "group_tax_id", "analytic_distribution"},
)


def _move_state(engine, ctx):
    cr = engine.cr
    cr.execute(f"""UPDATE account_move t SET state = s.state, posted_before = coalesce(s.posted_before, s.state = 'posted')
                   FROM etl_idmap m JOIN {S}.account_move s ON s.id = m.old_id
                   WHERE m.model = 'account.move' AND t.id = m.new_id AND t.state IS DISTINCT FROM s.state""")
    n = cr.rowcount
    cr.execute("""UPDATE account_move_line l SET parent_state = m.state FROM account_move m
                  WHERE l.move_id = m.id AND l.parent_state IS DISTINCT FROM m.state""")
    cr.execute(f"""UPDATE account_move t SET reversed_entry_id = r.new_id
                   FROM etl_idmap m JOIN {S}.account_move s ON s.id = m.old_id
                   JOIN etl_idmap r ON r.model = 'account.move' AND r.old_id = s.reversed_entry_id
                   WHERE m.model = 'account.move' AND t.id = m.new_id AND s.reversed_entry_id IS NOT NULL""")
    cr.execute("""SELECT count(*) FROM (SELECT move_id FROM account_move_line GROUP BY move_id
                                        HAVING abs(sum(balance)) > 0.005) x""")
    unbalanced = cr.fetchone()[0]
    engine._restore_dates("account_move", "account.move")
    engine._restore_dates("account_move_line", "account.move.line")
    return dict(states_restored=n, unbalanced_moves=unbalanced)


MOVE_STATE = FuncStep("account.move.state", _move_state)

FULLS = TableCopy("account.full.reconcile", "account_full_reconcile", overrides={"id": "s.id + {OFF}"},
                  exclude={"exchange_move_id"}, register=("account.full.reconcile", "account_full_reconcile"))
PARTIALS = TableCopy(
    "account.partial.reconcile", "account_partial_reconcile",
    joins=[IDM("dl", "account.move.line", "s.debit_move_id"), IDM("cl", "account.move.line", "s.credit_move_id"),
           IDM("em", "account.move", "s.exchange_move_id"), IDM("dc", "res.currency", "s.debit_currency_id"),
           IDM("cc", "res.currency", "s.credit_currency_id"),
           "LEFT JOIN account_full_reconcile fr ON fr.id = s.full_reconcile_id + {OFF}"],
    where="dl.new_id IS NOT NULL AND cl.new_id IS NOT NULL",
    overrides={"id": "s.id + {OFF}", "debit_move_id": "dl.new_id", "credit_move_id": "cl.new_id",
               "exchange_move_id": "em.new_id", "debit_currency_id": "dc.new_id", "credit_currency_id": "cc.new_id",
               "full_reconcile_id": "fr.id", "company_id": COMPANY},
    register=("account.partial.reconcile", "account_partial_reconcile"),
)
FULL_LINK = SqlStep("account.full.reconcile.lines", f"""
    UPDATE account_move_line t SET full_reconcile_id = s.full_reconcile_id + {{OFF}}
    FROM etl_idmap m JOIN {{S}}.account_move_line s ON s.id = m.old_id
    JOIN account_full_reconcile f ON f.id = s.full_reconcile_id + {{OFF}}
    WHERE m.model = 'account.move.line' AND t.id = m.new_id AND s.full_reconcile_id IS NOT NULL""")

LINE_TAXES = SqlStep("account.move.line.taxes", f"""
    INSERT INTO account_move_line_account_tax_rel (account_move_line_id, account_tax_id)
    SELECT m.new_id, t.new_id FROM {{S}}.account_move_line_account_tax_rel r
    JOIN etl_idmap m ON m.model = 'account.move.line' AND m.old_id = r.account_move_line_id
    JOIN etl_idmap t ON t.model = 'account.tax' AND t.old_id = r.account_tax_id
    ON CONFLICT DO NOTHING;
    INSERT INTO account_account_tag_account_move_line_rel (account_move_line_id, account_account_tag_id)
    SELECT m.new_id, g.new_id FROM {{S}}.account_account_tag_account_move_line_rel r
    JOIN etl_idmap m ON m.model = 'account.move.line' AND m.old_id = r.account_move_line_id
    JOIN etl_idmap g ON g.model = 'account.account.tag' AND g.old_id = r.account_account_tag_id
    ON CONFLICT DO NOTHING""")


def _recompute(engine, ctx):
    env, cr = engine.env, engine.cr
    env.invalidate_all()
    cr.execute("SELECT new_id FROM etl_idmap WHERE model = 'account.move.line'")
    lines = env["account.move.line"].browse([r[0] for r in cr.fetchall()])
    L = env["account.move.line"]
    for f in ("amount_residual", "amount_residual_currency", "reconciled"):
        env.add_to_compute(L._fields[f], lines)
    L.flush_model(["amount_residual", "amount_residual_currency", "reconciled"])
    cr.execute("SELECT new_id FROM etl_idmap WHERE model = 'account.move'")
    moves = env["account.move"].browse([r[0] for r in cr.fetchall()])
    M = env["account.move"]
    moves.invalidate_recordset()
    for f in ("tax_totals", "amount_residual", "amount_residual_signed", "payment_state", "amount_total",
              "amount_untaxed", "amount_tax", "amount_total_signed", "amount_untaxed_signed", "amount_tax_signed",
              "amount_total_in_currency_signed"):
        if f in M._fields and M._fields[f].store and M._fields[f].compute:
            env.add_to_compute(M._fields[f], moves)
    M.flush_model()
    cr.execute(f"""SELECT j.code, count(*), round(sum(s.amount_total)::numeric,2), round(sum(t.amount_total)::numeric,2)
                   FROM etl_idmap m JOIN {S}.account_move s ON s.id = m.old_id JOIN account_move t ON t.id = m.new_id
                   JOIN account_journal j ON j.id = t.journal_id WHERE m.model = 'account.move' AND s.state = 'posted'
                   GROUP BY j.code ORDER BY j.code""")
    per_journal = {r[0]: dict(moves=r[1], total19=str(r[2]), total20=str(r[3])) for r in cr.fetchall()}
    for t in ("account_partial_reconcile", "account_full_reconcile"):
        cr.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), GREATEST((SELECT coalesce(max(id), 1) FROM {t}), 1))")
    return dict(lines=len(lines), moves=len(moves), per_journal=per_journal)


RECOMPUTE = FuncStep("account.recompute", _recompute)

STEPS = [REFS, MOVES, LINES, MOVE_STATE, FULLS, PARTIALS, FULL_LINK, LINE_TAXES, RECOMPUTE]
