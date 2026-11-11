"""gu-1111: markdown tables for the report from the result JSONs.

  python -m research.sports_lab.grade_up_1111.tables <results_dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def ci(x):
    return "n/a" if x is None else f"[{x[0]:+.2f}, {x[1]:+.2f}]"


def d(x):
    return "n/a" if x is None else f"{x:+.2f}"


def v(x):
    return "n/a" if x is None else f"{x:.1f}"


def factor_table(rep, key="pooled"):
    out = ["| arm | sides flipped | all props: base -> new hit | diff [95% CI] | Over / Under hit (all), base -> new | graded n base -> new | graded hit base -> new | diff [95% CI] | graded Over / Under hit, new | graded Over share base -> new | verdict | games needed |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for a, r in rep["arms"].items():
        p = r[key] if key == "pooled" else r
        A, G = p["all"], p["graded"]
        verdict = "PASS" if p["pass"] else "FAIL " + ", ".join(p["failed"])
        out.append(f"| {a} | {p['flips']} | {v(A['base']['hit'])} -> {v(A['arm']['hit'])} | {d(A['diff'])} {ci(A['ci'])} | "
                   f"{v(A['base']['over_hit'])} / {v(A['base']['under_hit'])} -> {v(A['arm']['over_hit'])} / {v(A['arm']['under_hit'])} | "
                   f"{G['base']['n']} -> {G['arm']['n']} | {v(G['base']['hit'])} -> {v(G['arm']['hit'])} | {d(G['diff'])} {ci(G['ci'])} | "
                   f"{v(G['arm']['over_hit'])} / {v(G['arm']['under_hit'])} | {v(G['base']['over_share'])}% -> {v(G['arm']['over_share'])}% | "
                   f"{verdict} | {G['games_needed'] if G['games_needed'] else '-'} |")
    return "\n".join(out)


def market_table(rep):
    out = ["| arm | market | rows (graded base -> new) | all diff [CI] | graded diff [CI] | failed |", "|---|---|---|---|---|---|"]
    for a, r in rep["arms"].items():
        for mk, p in r["by_market"].items():
            A, G = p["all"], p["graded"]
            out.append(f"| {a} | {mk} | {A['base']['n']} ({G['base']['n']} -> {G['arm']['n']}) | {d(A['diff'])} {ci(A['ci'])} | "
                       f"{d(G['diff'])} {ci(G['ci'])} | {'PASS' if p['pass'] else ', '.join(p['failed'])} |")
    return "\n".join(out)


def letter_table(g):
    out = ["| arm | scope | letters changed | graded n base -> new | graded hit base -> new | diff [95% CI] | graded Over / Under hit, new | graded Over share | top (A+B) n | top hit base -> new | diff [95% CI] | verdict |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for a, sc in g["arms"].items():
        for s, r in sc.items():
            if not isinstance(r, dict) or "graded" not in r:
                out.append(f"| {a} | {s} | {r} | | | | | | | | | not measurable |")
                continue
            G, T = r["graded"], r["top"]
            out.append(f"| {a} | {s} | {r['changed']} | {G['base']['n']} -> {G['arm']['n']} | {v(G['base']['hit'])} -> {v(G['arm']['hit'])} | "
                       f"{d(G['diff'])} {ci(G['ci'])} | {v(G['arm']['over_hit'])} / {v(G['arm']['under_hit'])} | "
                       f"{v(G['base']['over_share'])}% -> {v(G['arm']['over_share'])}% | {T['base']['n']} -> {T['arm']['n']} | "
                       f"{v(T['base']['hit'])} -> {v(T['arm']['hit'])} | {d(T['diff'])} {ci(T['ci'])} | "
                       f"{'PASS' if r['pass'] else 'FAIL ' + ', '.join(r['failed'])} |")
    return "\n".join(out)


def desc_tables(g):
    de = g["descriptive"]
    out = ["| letter | n | hit | Over n / hit | Under n / hit |", "|---|---|---|---|---|"]
    for k, x in de["by_letter"].items():
        out.append(f"| {k} | {x['n']} | {v(x['hit'])} | {x['over_n']} / {v(x['over_hit'])} | {x['under_n']} / {v(x['under_hit'])} |")
    out += ["", "| raw chance bucket | n | stated raw | printed | hit | Over n / hit | Under n / hit |", "|---|---|---|---|---|---|---|"]
    for k, x in de["by_raw_bucket"].items():
        out.append(f"| {k} | {x['n']} | {x['stated_raw']} | {x['printed']} | {v(x['hit'])} | {x['over_n']} / {v(x['over_hit'])} | {x['under_n']} / {v(x['under_hit'])} |")
    out += ["", "| stat | n | hit | Over / Under hit | graded n | graded hit | graded Over / Under hit |", "|---|---|---|---|---|---|---|"]
    for k, x in de["by_stat"].items():
        a, gr = x["all"], x["graded"]
        out.append(f"| {k} | {a['n']} | {v(a['hit'])} | {v(a['over_hit'])} / {v(a['under_hit'])} | {gr['n']} | {v(gr['hit'])} | {v(gr['over_hit'])} / {v(gr['under_hit'])} |")
    return "\n".join(out)


if __name__ == "__main__":
    R = Path(sys.argv[1])
    for lg in ("nba", "nfl"):
        f = json.loads((R / f"{lg}_factors.json").read_text(encoding="utf-8"))
        g = json.loads((R / f"grade_layer_{lg}.json").read_text(encoding="utf-8"))
        print(f"\n### {lg.upper()} projection arms, pooled\n\n" + factor_table(f))
        print(f"\n### {lg.upper()} projection arms, by market\n\n" + market_table(f))
        print(f"\n### {lg.upper()} letter arms\n\n" + letter_table(g))
        print(f"\n### {lg.upper()} descriptive\n\n" + desc_tables(g))
