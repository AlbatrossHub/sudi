#!/usr/bin/env python3
"""P4 website precision check: compare the captured v19 pages (pages19/) against the migrated
v20 pages (pages20/) by VISIBLE TEXT (scripts/styles stripped, whitespace normalised, session
tokens ignored). Exit 1 if any page's visible text differs.

    /opt/odoo20/odoo20-venv/bin/python tools/etl/compare_pages.py
"""
import difflib
import json
import os
import re
import sys

from lxml import html as lhtml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from tools.etl import config  # noqa: E402

DIR19 = os.path.join(config.BASELINE_DIR, "pages19")
DIR20 = os.path.join(config.BASELINE_DIR, "pages20")
NOISE = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}[\d:.]*"), "<ts>"),
    (re.compile(r"csrf_token[^,;\"']*"), "csrf"),
    # v20 header search widget ships hidden helper text inside the page markup
    (re.compile(r"Loading\.\.\. 0 result\(s\) ?"), ""),
]


def text_of(path):
    with open(path, "rb") as f:
        doc = lhtml.fromstring(f.read() or b"<html/>")
    for el in doc.xpath("//script | //style | //noscript"):
        el.getparent().remove(el)
    txt = " ".join(doc.itertext())
    txt = re.sub(r"\s+", " ", txt).strip()
    for rx, sub in NOISE:
        txt = rx.sub(sub, txt)
    return txt


def main():
    idx19 = json.load(open(os.path.join(DIR19, "index.json")))["pages"]
    idx20 = json.load(open(os.path.join(DIR20, "index.json")))["pages"]
    failures = 0
    for url in sorted(set(idx19) | set(idx20)):
        a, b = idx19.get(url), idx20.get(url)
        if not a or not b:
            print(f"[MISSING ] {url}: only on {'19' if a else '20'}")
            failures += 1
            continue
        if a["status"] != b["status"]:
            print(f"[STATUS  ] {url}: 19={a['status']} 20={b['status']}")
            failures += 1
            continue
        if a["status"] != 200:
            print(f"[PASS    ] {url} (both {a['status']})")
            continue
        t19 = text_of(os.path.join(DIR19, a["html"]))
        t20 = text_of(os.path.join(DIR20, b["html"]))
        if t19 == t20:
            print(f"[PASS    ] {url} ({len(t19)} chars of visible text, identical)")
        else:
            print(f"[TEXTDIFF] {url}:")
            for line in difflib.unified_diff(t19.split(" "), t20.split(" "), lineterm="", n=2):
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                    print("   ", line[:160])
            failures += 1
    print(f"\n{'ALL PAGES MATCH' if not failures else f'{failures} page(s) differ'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
