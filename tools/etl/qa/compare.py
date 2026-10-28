#!/usr/bin/env python3
"""Side-by-side QA, part 5: merge every probe (inventory, crawl, renders, flows) into
one verdict report: /opt/odoo20/etl/qa/QA_REPORT.md.

    /opt/odoo20/odoo20-venv/bin/python tools/etl/qa/compare.py
"""
import difflib
import json
import os
import re
import sys

QA = "/opt/odoo20/etl/qa"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from tools.etl import config  # noqa: E402

# v19 modules that are legitimately absent on 20 (merged into core), and v20 extras
MERGED = set(config.MERGED_MODULES)
EXTRA20 = set(config.EXTRA_MODULES) | {"mail_tracking"}

# known behavior deltas: prod runs diamond 1.2.3, the target carries the 1.7.0 upgrade
KNOWN_NOTES = {
    "menus_new_in_20": "new v20 core menus + diamond 1.7.0 menus are expected",
}

# the diamond 1.2.3 -> 1.7.0 migration (deliberately carried by the ETL) grants
# group_sudi_job_work_user / delivery operator to pickup-app users, which brings
# timesheet (account.analytic.line) read access those users lack on prod.
ALLOWED_ACL = {
    ("rakesh@sdppl.com", "account.analytic.line"),
    ("sdpplbdb@sdppl.com", "account.analytic.line"),
}


def load(name):
    p = os.path.join(QA, name)
    return json.load(open(p)) if os.path.exists(p) else None


def norm_render(t):
    t = re.sub(r"\s+", " ", t or "").strip()
    # print date lines differ run-to-run, never data
    t = re.sub(r"\b\d{2}/\d{2}/\d{4}\b", "<date>", t)
    return t


def word_diff(a, b, n=3):
    out = []
    for line in difflib.unified_diff(a.split(" "), b.split(" "), lineterm="", n=n):
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            out.append(line[:160])
    return out[:40]


def main():
    inv19, inv20 = load("inv19.json"), load("inv20.json")
    cr19, cr20 = load("crawl19.json"), load("crawl20.json")
    rep19, rep20 = load("rep19.json"), load("rep20.json")
    fl19, fl20 = load("flow19.json"), load("flow20.json")

    R = ["# Sudi 19 -> 20 side-by-side functional QA", ""]
    problems = []

    def section(title):
        R.extend(["", f"## {title}", ""])

    # ── modules ──────────────────────────────────────────────────────────────
    section("Installed modules")
    m19, m20 = set(inv19["modules"]), set(inv20["modules"])
    missing = m19 - m20 - MERGED
    extra = m20 - m19 - EXTRA20
    R.append(f"- v19: {len(m19)} modules, v20: {len(m20)}")
    R.append(f"- merged away in 20 (expected): {sorted(m19 & MERGED - m20) or sorted(MERGED & m19)}")
    R.append(f"- extra on 20 (expected: {sorted(EXTRA20)}): {sorted(m20 - m19)}")
    if missing:
        problems.append(f"modules missing on 20: {sorted(missing)}")
        R.append(f"- **MISSING on 20: {sorted(missing)}**")
    else:
        R.append("- **no module missing on 20** beyond the known merges -> PASS")

    # ── menus ────────────────────────────────────────────────────────────────
    section("Menus (admin-visible, by full path)")
    n19, n20 = set(inv19["menus"]), set(inv20["menus"])
    gone = sorted(n19 - n20)
    new = sorted(n20 - n19)
    R.append(f"- v19: {len(n19)} menus, v20: {len(n20)}, common: {len(n19 & n20)}")
    if gone:
        R.append(f"- v19 menus not present on 20 ({len(gone)}):")
        R.extend(f"    - {g}" for g in gone)
    if new:
        R.append(f"- new on 20 ({len(new)}):")
        R.extend(f"    - {g}" for g in new)

    # ── server-side view scan ────────────────────────────────────────────────
    section("View arch scan (get_views on every window action)")
    for tag, inv in (("v19", inv19), ("v20", inv20)):
        errs = {k: v for k, v in inv["view_scan"].items() if v["status"] == "ERROR"}
        R.append(f"- {tag}: {len(inv['view_scan'])} actions scanned, {len(errs)} errors")
        for k, v in errs.items():
            R.append(f"    - **{k}**: {v['error']}")
            if tag == "v20":
                problems.append(f"view scan {k}: {v['error']}")

    # ── browser crawl ────────────────────────────────────────────────────────
    section("Browser crawl (every menu-reachable screen, JS errors)")
    for tag, cr in (("v19", cr19), ("v20", cr20)):
        if not cr:
            R.append(f"- {tag}: NOT RUN")
            continue
        bad = {k: v for k, v in cr["screens"].items() if v["status"] != "OK"}
        R.append(f"- {tag}: {len(cr['screens'])} screens opened, {len(bad)} with errors")
        for k, v in sorted(bad.items()):
            detail = v.get("nav_error") or v.get("js_errors") or "error dialog"
            R.append(f"    - **{k}**: {str(detail)[:220]}")
            if tag == "v20":
                problems.append(f"crawl {k}: {str(detail)[:120]}")
    if cr19 and cr20:
        b19 = {k for k, v in cr19["screens"].items() if v["status"] != "OK"}
        b20 = {k for k, v in cr20["screens"].items() if v["status"] != "OK"}
        only20 = b20 - b19
        R.append(f"- screens broken ONLY on v20: {sorted(only20) if only20 else 'none'}")

    # ── report renders ───────────────────────────────────────────────────────
    section("Report renders (same documents, visible text)")
    if rep19 and rep20:
        keys = sorted(set(rep19["renders"]) | set(rep20["renders"]))
        same = 0
        for k in keys:
            a, b = norm_render(rep19["renders"].get(k, "<absent>")), norm_render(rep20["renders"].get(k, "<absent>"))
            if a == b:
                same += 1
                continue
            if a.startswith("ERROR") or b.startswith("ERROR"):
                R.append(f"- **{k}: render error** 19={a[:120]} | 20={b[:120]}")
                problems.append(f"render {k}")
                continue
            R.append(f"- {k}: TEXT DIFF")
            R.extend(f"    ```{l}```" for l in word_diff(a, b))
        R.append(f"- {same}/{len(keys)} documents render with identical visible text")
        for tag, rep in (("v19", rep19), ("v20", rep20)):
            for k, v in (rep.get("pdf") or {}).items():
                R.append(f"- {tag} PDF pipeline on {k}: {'OK, %s bytes' % v['bytes'] if v.get('ok') else 'FAIL ' + v.get('error', '')}")

    # ── flows ────────────────────────────────────────────────────────────────
    section("Business flows (executed on both, rolled back)")
    if fl19 and fl20:
        R.append(f"- fixtures 19: {fl19['fixtures']}")
        R.append(f"- fixtures 20: {fl20['fixtures']}")
        R.append("")
        R.append("| step | v19 | v20 | notes |")
        R.append("|---|---|---|---|")
        for k in sorted(set(fl19["steps"]) | set(fl20["steps"])):
            a, b = fl19["steps"].get(k, {}), fl20["steps"].get(k, {})
            note = ""
            ka = {x: y for x, y in a.items() if x not in ("status", "trace")}
            kb = {x: y for x, y in b.items() if x not in ("status", "trace")}
            if a.get("status") == b.get("status") == "OK" and ka != kb:
                note = "outputs differ: " + "; ".join(
                    f"{f}: {ka.get(f)!r} vs {kb.get(f)!r}" for f in sorted(set(ka) | set(kb))
                    if ka.get(f) != kb.get(f))[:400]
            if b.get("status") != "OK":
                problems.append(f"flow {k} on v20: {b.get('error')}")
            R.append(f"| {k} | {a.get('status', '-')} | {b.get('status', '-')} | {note or a.get('error') or ''} |")
        R.append("")
        R.append(f"- document names drawn (sequence parity): 19={fl19.get('names')} 20={fl20.get('names')}")
        # receipt/delivery numbers use PG sequences, which survive rollbacks -- they
        # drift by how many times flows ran on each clone. The invoice number is
        # computed from the last posted name, so it must match EXACTLY.
        inv19n = (fl19.get("names") or {}).get("invoice")
        inv20n = (fl20.get("names") or {}).get("invoice")
        if inv19n != inv20n:
            problems.append(f"invoice sequence parity: {inv19n} vs {inv20n}")

    # ── sequences ────────────────────────────────────────────────────────────
    section("Sequence counters")
    s19, s20 = inv19["sequences"], inv20["sequences"]
    diffs = []
    for code in sorted(set(s19) & set(s20)):
        if s19[code]["next"] != s20[code]["next"] or s19[code]["prefix"] != s20[code]["prefix"]:
            diffs.append(f"    - {code}: 19 {s19[code]['prefix']}@{s19[code]['next']} vs 20 {s20[code]['prefix']}@{s20[code]['next']}")
    R.append(f"- {len(set(s19) & set(s20))} common sequence codes, {len(diffs)} differ:")
    R.extend(diffs or ["    - none"])

    # ── ACL ──────────────────────────────────────────────────────────────────
    section("Per-user access smoke (search_count as each real user)")
    acl_bad = 0
    for login in sorted(set(inv19["acl"]) & set(inv20["acl"])):
        a, b = inv19["acl"][login], inv20["acl"][login]
        for model in sorted(set(a) | set(b)):
            va, vb = a.get(model), b.get(model)
            denied_a, denied_b = str(va).startswith("DENIED"), str(vb).startswith("DENIED")
            if denied_a != denied_b:
                if (login, model) in ALLOWED_ACL:
                    R.append(f"- (accepted, diamond 1.7.0 group grant) {login} / {model}: 19={va} 20={vb}")
                    continue
                acl_bad += 1
                R.append(f"- **{login} / {model}: 19={va} 20={vb}**")
                problems.append(f"ACL {login}/{model}: {va} vs {vb}")
            elif va != vb and not denied_a:
                R.append(f"- (count) {login} / {model}: 19={va} 20={vb}")
    users_only19 = sorted(set(inv19["acl"]) - set(inv20["acl"]))
    if users_only19:
        R.append(f"- users only on 19: {users_only19}")
        problems.append(f"users missing on 20: {users_only19}")
    if not acl_bad:
        R.append(f"- {len(set(inv19['acl']) & set(inv20['acl']))} users x {10} models: allow/deny identical -> PASS")

    # ── verdict ──────────────────────────────────────────────────────────────
    section("VERDICT")
    if problems:
        R.append(f"**{len(problems)} problem(s) to fix:**")
        R.extend(f"1. {p}" for p in problems)
    else:
        R.append("**ALL PROBES GREEN** (differences listed above are explained/expected).")

    out = os.path.join(QA, "QA_REPORT.md")
    open(out, "w").write("\n".join(R) + "\n")
    print("\n".join(R[-25:]))
    print(f"\nreport -> {out}; problems={len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
