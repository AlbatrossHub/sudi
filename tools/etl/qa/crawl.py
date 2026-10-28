#!/usr/bin/env python3
"""Side-by-side QA, part 4: browser crawl. Logs in as admin and OPENS EVERY
menu-reachable action (window actions AND client actions like the WhatsApp dashboard),
capturing JS page errors, console errors and Odoo error dialogs per screen.

    python crawl.py --base http://localhost:8129 --inv /opt/odoo20/etl/qa/inv19.json \
                    --out /opt/odoo20/etl/qa/crawl19.json

The action list comes from the inventory JSON (menus visible to admin), so both sides
crawl the SAME functional surface keyed by menu path — the compare step then diffs
error status menu by menu.
"""
import argparse
import json
import time

from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--inv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--settle", type=float, default=2.5, help="seconds to let a screen render")
    args = ap.parse_args()

    inv = json.load(open(args.inv))
    # one crawl target per menu path that carries an openable action
    targets = {path: m for path, m in sorted(inv["menus"].items())
               if m.get("action_type") in ("ir.actions.act_window", "ir.actions.client")}

    results, errors_now = {}, []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors_now.append(f"pageerror: {str(e)[:400]}"))
        page.on("console", lambda m: errors_now.append(f"console: {m.text[:400]}")
                if m.type == "error" and "favicon" not in m.text else None)

        page.goto(args.base + "/web/login", timeout=60000)
        # web_auth_otp_login tabs the form; make sure the password tab is active
        if page.locator("#tab_email").count():
            page.click("#tab_email")
        page.fill(".oe_login_form input[name=login]", "admin")
        page.fill(".oe_login_form input[name=password]", "admin")
        page.click(".oe_login_form button[type=submit]")
        page.wait_for_url("**/odoo**", timeout=60000)
        time.sleep(4)
        errors_now.clear()

        for i, (path, m) in enumerate(targets.items()):
            errors_now.clear()
            rec = {"action": m.get("action_name"), "type": m["action_type"]}
            try:
                page.goto(f"{args.base}/odoo/action-{m['action_id']}", timeout=45000)
                time.sleep(args.settle)
                rec["error_dialog"] = page.locator(".o_error_dialog, .modal .o_technical_modal").count() > 0
                rec["rendered_view"] = page.locator(
                    ".o_action_manager .o_view_controller, .o_action_manager .o-mail-Discuss,"
                    " .o_action_manager .owa_dashboard, .o_action_manager .o_client_action,"
                    " .o_action_manager > *").count() > 0
            except Exception as e:  # noqa: BLE001
                rec["nav_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            rec["js_errors"] = list(dict.fromkeys(errors_now))
            rec["status"] = ("ERROR" if rec.get("js_errors") or rec.get("error_dialog")
                             or rec.get("nav_error") else "OK")
            results[path] = rec
            print(f"[{i+1:>3}/{len(targets)}] {'OK ' if rec['status']=='OK' else 'ERR'} {path}")

        browser.close()

    bad = {k: v for k, v in results.items() if v["status"] != "OK"}
    json.dump({"base": args.base, "screens": results, "bad": sorted(bad)},
              open(args.out, "w"), indent=1, sort_keys=True)
    print(f"\nQA-CRAWL-DONE {args.out}: {len(results)} screens, {len(bad)} with errors")
    for k, v in bad.items():
        print(f"  ERR {k}: {v.get('nav_error') or v.get('js_errors') or 'error dialog'}")


if __name__ == "__main__":
    main()
