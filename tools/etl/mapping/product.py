"""Phase 'product': units (20 = flat tree), categories, the 19 templates and their variants."""
import logging

from ..core import OrmStep, FuncStep
from .. import config

_logger = logging.getLogger("etl.product")
S = config.SRC_SCHEMA


def _uom_transform(vals, row, ctx):
    # 20: flat tree with relative_factor; 19 units that exist on 20 are reused by name,
    # new ones (Ton) become reference units.
    for k in ("category_id", "uom_type", "rounding", "factor", "factor_inv"):
        vals.pop(k, None)
    vals.setdefault("relative_factor", 1.0)
    return vals


UOM = OrmStep("uom.uom", key=["name"], transform=_uom_transform, update_existing=False,
              skip={"category_id", "uom_type", "rounding", "factor", "factor_inv"})

PCATEG = OrmStep("product.category", key=["name"], order="parent_path, id", batch=1,
                 skip={"parent_path", "complete_name", "product_count"}, update_existing=False)


def _tmpl_transform(vals, row, ctx):
    if vals.get("tracking") not in ("lot", "serial"):
        vals["tracking"] = False   # 19 'none' key removed on 20
    vals.pop("uom_po_id", None)
    return vals


# key=["name"]: the diamond module ships the 16 service products as module data, so a
# fresh 20 target already HAS them -- prod's templates must be matched onto those
# records (values written over), not copied as duplicates. Found by the flow QA: job
# types reference the module records, so duplicated templates left invoices untaxed.
TEMPLATE = OrmStep(
    "product.template", transform=_tmpl_transform, batch=200, key=["name"],
    skip={"uom_po_id", "product_variant_ids", "product_variant_id", "product_variant_count",
          "sale_line_warn", "sale_line_warn_msg", "route_ids", "sale_delay",
          "optional_product_ids", "attribute_line_ids", "image_variant_1920", "can_image_1024_be_zoomed",
          "property_account_creditor_price_difference", "property_price_difference_account_id"},
    force_fields={"list_price", "standard_price", "is_storable"},
)


def _variants(engine, ctx):
    """19 product.product -> the variant 20 created with the template (1 variant per template)."""
    cr = engine.cr; env = engine.env
    cr.execute(f"SELECT p.id, p.product_tmpl_id, p.default_code, p.barcode, p.active FROM {S}.product_product p ORDER BY p.id")
    rows = cr.fetchall()
    tmap = engine.idmap.load("product.template")
    pairs, missing = [], 0
    for pid, tid, code, barcode, active in rows:
        new_t = tmap.get(tid)
        if not new_t:
            missing += 1; engine.idmap.refuse("product.product", "product.product", pid, "template not mapped"); continue
        tmpl = env["product.template"].with_context(active_test=False).browse(new_t)
        variant = tmpl.product_variant_ids[:1]
        if not variant:
            missing += 1; engine.idmap.refuse("product.product", "product.product", pid, "template has no variant"); continue
        w = {}
        if code and variant.default_code != code:
            w["default_code"] = code
        if barcode and variant.barcode != barcode:
            w["barcode"] = barcode
        if variant.active != active:
            w["active"] = active
        if w:
            variant.with_context(**config.SILENT_CTX).write(w)
        pairs.append((pid, variant.id))
    engine.idmap.add_many("product.product", pairs)
    return dict(variants=len(pairs), refused=missing)


VARIANTS = FuncStep("product.product", _variants)

STEPS = [UOM, PCATEG, TEMPLATE, VARIANTS]
