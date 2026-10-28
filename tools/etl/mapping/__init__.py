"""Ordered phases of the sudi 19→20 ETL. Each phase is a list of steps
(OrmStep / SqlStep / FuncStep / TableCopy — see core.py)."""
from . import base, product, account_refs, stock, hr, account, discuss, diamond, website, settings, chatter, post  # noqa: F401

PHASES = {
    "base": base.STEPS,
    "product": product.STEPS,
    "account_refs": account_refs.STEPS,
    "stock_refs": stock.REF_STEPS,
    "diamond_refs": diamond.REF_STEPS,      # job types: pickings/moves FK them at +OFFSET
    "stock": stock.STEPS,
    "account": account.STEPS,
    "hr": hr.STEPS,
    "owa": discuss.OWA_STEPS,
    "discuss": discuss.STEPS,
    "diamond": diamond.STEPS,
    "website": website.STEPS,
    "settings": settings.STEPS,             # pwa branding / IAP: their chatter + attachments follow
    "chatter": chatter.STEPS,
    "post_discuss": discuss.POST_CHATTER_STEPS,   # channel<->message links need chatter done
    "post_diamond": diamond.POST_CHATTER_STEPS,   # picking<->attachment m2m tables need chatter done
    "post": post.STEPS,
}
ORDER = ["base", "product", "account_refs", "stock_refs", "diamond_refs", "stock", "account", "hr",
         "owa", "discuss", "diamond", "website", "settings", "chatter", "post_discuss", "post_diamond",
         "post"]


def steps_for(phases=None):
    out = []
    for p in (phases or ORDER):
        out += PHASES[p]
    return out
