"""Phase 'stock_refs' (ORM: warehouse renamed to prod's HO, locations, picking types) and
phase 'stock' (SQL lane: the job-work pickings, moves, move lines, quants). No MRP, no
valuation layers on prod (service-style flow, all costs 0)."""
import logging

from ..core import OrmStep, FuncStep, TableCopy, SqlStep, IDM
from .. import config

_logger = logging.getLogger("etl.stock")
S = config.SRC_SCHEMA
COMPANY = "(SELECT id FROM res_company ORDER BY id LIMIT 1)"


def _warehouse(engine, ctx):
    """The fresh 20 warehouse takes the 19 code/name so location complete_names match (HO/Stock ...)."""
    cr, env = engine.cr, engine.env
    cr.execute(f"SELECT id, name, code, reception_steps, delivery_steps FROM {S}.stock_warehouse ORDER BY id")
    rows = cr.fetchall()
    wh20 = env["stock.warehouse"].search([], order="id", limit=1)
    pairs = []
    for i, (wid, name, code, rsteps, dsteps) in enumerate(rows):
        if i == 0 and wh20:
            vals = {}
            for f, v in (("code", code), ("name", name), ("reception_steps", rsteps), ("delivery_steps", dsteps)):
                if getattr(wh20, f) != v:
                    vals[f] = v
            if vals:
                wh20.write(vals)
            pairs.append((wid, wh20.id))
        else:
            rec = env["stock.warehouse"].create({"name": name, "code": code,
                                                 "reception_steps": rsteps, "delivery_steps": dsteps})
            pairs.append((wid, rec.id))
    engine.idmap.add_many("stock.warehouse", pairs)
    return dict(warehouses=len(pairs))


WAREHOUSE = FuncStep("stock.warehouse", _warehouse)

LOCATION = OrmStep("stock.location", key=["name", "usage", "location_id"], key_allow_null=True,
                   order="parent_path, id", update_existing=False, batch=1,
                   skip={"posx", "posy", "posz", "scrap_location", "parent_path", "complete_name", "warehouse_id",
                         "child_ids", "child_internal_location_ids", "quant_ids", "cyclic_inventory_frequency",
                         "last_inventory_date", "next_inventory_date", "storage_category_id",
                         "valuation_in_account_id", "valuation_out_account_id"})
PICKING_TYPE = OrmStep("stock.picking.type", key=["name", "warehouse_id"], update_existing=False,
                       skip={"sequence_code", "show_operations", "sequence_id", "return_picking_type_id",
                             "default_location_src_id", "default_location_dest_id", "count_picking_draft",
                             "count_picking_ready", "count_picking_waiting", "count_picking_late",
                             "count_picking_backorders", "count_picking", "rating_count", "rating_avg", "auto_batch",
                             "kanban_dashboard_graph", "favorite_user_ids", "auto_show_reception_report"})

REF_STEPS = [WAREHOUSE, LOCATION, PICKING_TYPE]

# ------------------------------------------------------------------ SQL lane
_loc = IDM("lo", "stock.location", "s.location_id")
_locd = IDM("ld", "stock.location", "s.location_dest_id")
_pt = IDM("pt", "stock.picking.type", "s.picking_type_id")
_partner = IDM("pa", "res.partner", "s.partner_id")
_user = IDM("us", "res.users", "s.user_id")
_owner = IDM("ow", "res.partner", "s.owner_id")
_prod = IDM("pr", "product.product", "s.product_id")

PICKINGS = TableCopy(
    "stock.picking", "stock_picking",
    joins=[_loc, _locd, _pt, _partner, _user, _owner,
           IDM("pj", "project.project", "s.project_id"),
           IDM("pu", "res.users", "s.sudi_pickup_user_id"),
           IDM("pd", "res.partner", "s.sudi_pickup_address_id"),
           IDM("cd", "sudi.diamond.job.type", "s.sudi_current_department_id"),
           IDM("tj", "sudi.diamond.job.type", "s.sudi_timesheet_job_type_id")],
    where="lo.new_id IS NOT NULL AND ld.new_id IS NOT NULL AND pt.new_id IS NOT NULL",
    overrides={"id": "s.id + {OFF}", "location_id": "lo.new_id", "location_dest_id": "ld.new_id",
               "picking_type_id": "pt.new_id", "partner_id": "pa.new_id", "user_id": "us.new_id",
               "owner_id": "ow.new_id", "project_id": "pj.new_id", "company_id": COMPANY,
               "backorder_id": "NULL", "return_id": "NULL", "picking_properties": "NULL",
               "sudi_pickup_user_id": "pu.new_id", "sudi_pickup_address_id": "pd.new_id",
               "sudi_current_department_id": "cd.new_id",
               "sudi_origin_receipt_id": "NULL",
               "sudi_timesheet_job_type_id": "tj.new_id"},
    extra={"sudi_settlement_mode": "'invoice'"},   # NEW NOT NULL on 20 (diamond 1.7.0 default)
    register=("stock.picking", "stock_picking"), chunk=20000,
)
PICKING_LINKS = SqlStep("stock.picking.links", f"""
    UPDATE stock_picking t SET backorder_id = CASE WHEN b.id IS NOT NULL THEN s.backorder_id + {{OFF}} END,
                               return_id = CASE WHEN r.id IS NOT NULL THEN s.return_id + {{OFF}} END,
                               sudi_origin_receipt_id = CASE WHEN o.id IS NOT NULL THEN s.sudi_origin_receipt_id + {{OFF}} END
    FROM {{S}}.stock_picking s LEFT JOIN stock_picking b ON b.id = s.backorder_id + {{OFF}}
                               LEFT JOIN stock_picking r ON r.id = s.return_id + {{OFF}}
                               LEFT JOIN stock_picking o ON o.id = s.sudi_origin_receipt_id + {{OFF}}
    WHERE t.id = s.id + {{OFF}}
      AND (s.backorder_id IS NOT NULL OR s.return_id IS NOT NULL OR s.sudi_origin_receipt_id IS NOT NULL)""")

MOVES = TableCopy(
    "stock.move", "stock_move",
    joins=[_prod, IDM("uom", "uom.uom", "s.product_uom"), IDM("puom", "uom.uom", "s.packaging_uom_id"),
           _loc, _locd, _partner, IDM("pk", "stock.picking", "s.picking_id"), _pt,
           IDM("rp", "res.partner", "s.restrict_partner_id"), IDM("wh", "stock.warehouse", "s.warehouse_id"),
           IDM("sc", "res.partner", "s.sudi_customer_id"),
           IDM("jt", "sudi.diamond.job.type", "s.sudi_job_type_id")],
    where="pr.new_id IS NOT NULL AND uom.new_id IS NOT NULL AND lo.new_id IS NOT NULL AND ld.new_id IS NOT NULL"
          " AND (s.picking_id IS NULL OR pk.new_id IS NOT NULL)",
    overrides={"id": "s.id + {OFF}", "product_id": "pr.new_id", "location_id": "lo.new_id",
               "location_dest_id": "ld.new_id", "partner_id": "pa.new_id", "picking_id": "pk.new_id",
               "picking_type_id": "pt.new_id", "restrict_partner_id": "rp.new_id", "warehouse_id": "wh.new_id",
               "packaging_uom_id": "puom.new_id", "company_id": COMPANY,
               "rule_id": "NULL", "orderpoint_id": "NULL", "origin_returned_move_id": "NULL", "account_move_id": "NULL",
               "sudi_customer_id": "sc.new_id", "sudi_job_type_id": "jt.new_id",
               "sudi_invoice_line_id": "NULL",   # linked after the account phase
               # diamond >= 1.4 billing links (absent on raw prod): billing lines load in the diamond phase
               "sudi_billing_line_id": "NULL", "sudi_reference_line_id": "NULL",
               "sudi_origin_receipt_move_id": "NULL"},
    extra={"uom_id": "uom.new_id", "quantity_product_uom": "s.quantity",
           "is_scrap": "(s.scrap_id IS NOT NULL)", "value": "0", "is_in": "false", "is_out": "false"},
    register=("stock.move", "stock_move"), chunk=20000,
)
MOVE_LINKS = SqlStep("stock.move.links", f"""
    UPDATE stock_move t SET origin_returned_move_id = s.origin_returned_move_id + {{OFF}}
    FROM {{S}}.stock_move s JOIN stock_move o ON o.id = s.origin_returned_move_id + {{OFF}}
    WHERE t.id = s.id + {{OFF}} AND s.origin_returned_move_id IS NOT NULL;
    UPDATE stock_move t SET sudi_origin_receipt_move_id = s.sudi_origin_receipt_move_id + {{OFF}}
    FROM {{S}}.stock_move s JOIN stock_move o ON o.id = s.sudi_origin_receipt_move_id + {{OFF}}
    WHERE t.id = s.id + {{OFF}} AND s.sudi_origin_receipt_move_id IS NOT NULL;
    INSERT INTO stock_move_move_rel (move_orig_id, move_dest_id)
    SELECT r.move_orig_id + {{OFF}}, r.move_dest_id + {{OFF}} FROM {{S}}.stock_move_move_rel r
    JOIN stock_move a ON a.id = r.move_orig_id + {{OFF}} JOIN stock_move b ON b.id = r.move_dest_id + {{OFF}}
    ON CONFLICT DO NOTHING;
    UPDATE stock_move t SET is_in = (t.state = 'done' AND ld.usage IN ('internal','transit') AND lo.usage NOT IN ('internal','transit')),
                            is_out = (t.state = 'done' AND lo.usage IN ('internal','transit') AND ld.usage NOT IN ('internal','transit'))
    FROM stock_location lo, stock_location ld WHERE lo.id = t.location_id AND ld.id = t.location_dest_id AND t.id > {{OFF}}""")

MOVE_LINES = TableCopy(
    "stock.move.line", "stock_move_line",
    joins=["JOIN stock_move mv ON mv.id = s.move_id + {OFF}", _prod, IDM("uom", "uom.uom", "s.product_uom_id"),
           _loc, _locd, _owner, IDM("pk", "stock.picking", "s.picking_id")],
    where="pr.new_id IS NOT NULL AND lo.new_id IS NOT NULL AND ld.new_id IS NOT NULL",
    overrides={"id": "s.id + {OFF}", "move_id": "mv.id", "product_id": "pr.new_id", "location_id": "lo.new_id",
               "location_dest_id": "ld.new_id", "owner_id": "ow.new_id", "picking_id": "pk.new_id",
               "company_id": COMPANY, "lot_id": "NULL", "package_id": "NULL", "result_package_id": "NULL",
               "package_level_id": "NULL"},
    extra={"uom_id": "uom.new_id", "quantity_product_uom": "s.quantity"},
    register=("stock.move.line", "stock_move_line"), chunk=50000,
)

QUANTS = TableCopy(
    "stock.quant", "stock_quant",
    joins=[_prod, _loc, _owner, _user],
    where="pr.new_id IS NOT NULL AND lo.new_id IS NOT NULL",
    overrides={"id": "s.id + {OFF}", "product_id": "pr.new_id", "location_id": "lo.new_id", "owner_id": "ow.new_id",
               "user_id": "us.new_id", "company_id": COMPANY, "lot_id": "NULL", "package_id": "NULL",
               "in_date": "coalesce(s.in_date, s.create_date, now())"},
    register=("stock.quant", "stock_quant"), chunk=50000,
)


def _stock_check(engine, ctx):
    cr = engine.cr
    for t in ("stock_picking", "stock_move", "stock_move_line", "stock_quant"):
        cr.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), GREATEST((SELECT coalesce(max(id), 1) FROM {t}), 1))")
    cr.execute(f"""SELECT (SELECT count(*) FROM {S}.stock_picking), (SELECT count(*) FROM stock_picking WHERE id > {config.OFFSET}),
                          (SELECT count(*) FROM {S}.stock_move), (SELECT count(*) FROM stock_move WHERE id > {config.OFFSET}),
                          (SELECT count(*) FROM {S}.stock_move_line), (SELECT count(*) FROM stock_move_line WHERE id > {config.OFFSET}),
                          (SELECT count(*) FROM {S}.stock_quant), (SELECT count(*) FROM stock_quant WHERE id > {config.OFFSET})""")
    r = cr.fetchone()
    return dict(pickings=f"{r[1]}/{r[0]}", moves=f"{r[3]}/{r[2]}", move_lines=f"{r[5]}/{r[4]}", quants=f"{r[7]}/{r[6]}")


STOCK_CHECK = FuncStep("stock.check", _stock_check)

STEPS = [PICKINGS, PICKING_LINKS, MOVES, MOVE_LINKS, MOVE_LINES, QUANTS, STOCK_CHECK]
