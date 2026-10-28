#!/usr/bin/env python3
"""Fetch every website page (HTML + optional screenshot) from a running server, as the
public user. Used twice: P0 captures the v19 baseline, P4 captures the migrated v20 site
— then the two directories are diffed page by page.

    # v19 baseline (server from conf/odoo19-baseline.conf must be running):
    /opt/odoo20/odoo20-venv/bin/python tools/etl/fetch_pages.py --shots

    # v20 side (P4):
    ... fetch_pages.py --db sudi20_etl_dry1 --base http://localhost:8123 --out /opt/odoo20/etl/baseline/pages20 --shots

Screenshots use headless google-chrome (no extra dependencies). The page list comes from
the DATABASE (website_page + '/' root), never from a hand-typed list.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

import psycopg2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from tools.etl import config  # noqa: E402

CHROME = "/usr/bin/google-chrome"
VIEWPORT = "1366,3000"  # tall viewport: whole page on one shot for these small pages


def slug(url):
    return (url.strip("/") or "home").replace("/", "_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=config.SRC_DB, help="database to read the page list from")
    ap.add_argument("--base", default=f"http://localhost:{config.PORT_BASELINE19}")
    ap.add_argument("--out", default=os.path.join(config.BASELINE_DIR, "pages19"))
    ap.add_argument("--shots", action="store_true", help="also take google-chrome screenshots")
    args = ap.parse_args()

    # expected public status: 200 for published pages, 404 for unpublished ones (a page that
    # is hidden on 19 must STAY hidden on 20 — that asymmetry is part of the parity contract).
    with psycopg2.connect(dbname=args.db) as conn, conn.cursor() as cr:
        cr.execute("""SELECT data_type FROM information_schema.columns
                      WHERE table_name='website_page' AND column_name='url'""")
        url_expr = "p.url->>'en_US'" if cr.fetchone()[0] == "jsonb" else "p.url"
        cr.execute(f"""SELECT {url_expr}, bool_or(p.is_published) FROM website_page p
                      JOIN ir_ui_view v ON v.id = p.view_id
                      WHERE v.active GROUP BY 1 ORDER BY 1""")
        pages = dict(cr.fetchall())
    pages.setdefault("/", True)
    urls = sorted(pages)

    os.makedirs(args.out, exist_ok=True)
    index, failed = {}, []
    for url in urls:
        name = slug(url)
        full = args.base.rstrip("/") + url
        try:
            with urllib.request.urlopen(full, timeout=60) as resp:
                body = resp.read()
                status = resp.status
        except urllib.error.HTTPError as e:
            body, status = e.read(), e.code
        except Exception as e:  # noqa: BLE001
            body, status = str(e).encode(), 0
        html_path = os.path.join(args.out, name + ".html")
        with open(html_path, "wb") as f:
            f.write(body)
        expected = 200 if pages[url] else 404
        index[url] = {"status": status, "expected": expected, "published": pages[url],
                      "html": name + ".html", "bytes": len(body)}
        mark = "ok" if status == expected else "FAIL"
        if status != expected:
            failed.append(url)
        if args.shots and status == 200:
            png = os.path.join(args.out, name + ".png")
            r = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--hide-scrollbars",
                                f"--window-size={VIEWPORT}", f"--screenshot={png}", full],
                               capture_output=True, timeout=120)
            index[url]["shot"] = name + ".png" if r.returncode == 0 and os.path.exists(png) else None
            mark += "+shot" if index[url]["shot"] else "+SHOT-FAIL"
        print(f"  [{mark:>9}] {status} {url} ({len(body)} bytes)")

    with open(os.path.join(args.out, "index.json"), "w") as f:
        json.dump({"base": args.base, "db": args.db, "pages": index}, f, indent=1)
    print(f"\n{len(urls)} pages -> {args.out}  ({len(failed)} failures)")
    if failed:
        print("FAILED:", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
