"""UT-1: the CURRENT board (forecasts logged after the last P1 forecast, matches not started at the pull) - Under share and the
same step decomposition as build_units.py. No outcomes (forward). Board = a forecast whose saved quote names a PrizePicks /
Underdog standard line; the latest such forecast per pick.
usage: python board_now.py <board_now_fc.json> <pulled_at ISO>"""
import collections
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_units import decompose, PERF_VERSIONS, ts  # noqa: E402


def main(path, now):
    now = ts(now)
    rows = json.load(open(path))["rows"]
    latest = {}
    for r in rows:
        try:
            q = json.loads(r["quote"]) if r.get("quote") else None
        except ValueError:
            q = None
        tq = ((q or {}).get("anchors") or {}).get("target") or {}
        i = r.get("inputs") or {}
        if tq.get("book") not in ("prizepicks", "underdog") or (i.get("line_type") or "standard") != "standard" \
                or (tq.get("market_type") or (q or {}).get("feed_odds_type") or "standard") != "standard":
            continue
        if not r.get("match_start") or ts(r["match_start"]) <= now or r.get("p_more") is None:
            continue
        k = r["pick_id"]
        if k not in latest or (r["logged_at"], r["id"]) > (latest[k]["logged_at"], latest[k]["id"]):
            latest[k] = r
    out = []
    for r in latest.values():
        i = r["inputs"]
        pf = i.get("perf") if isinstance(i.get("perf"), dict) else {}
        src = pf.get("version") if (pf.get("lambda") is not None and pf.get("sd") and pf.get("version") in PERF_VERSIONS) else "legacy"
        p = float(r["p_more"])
        rec = {"prop": i.get("prop"), "scope": i.get("scope") or "12", "line": float(r["line"]), "lam": float(r["lambda"]), "p": p,
               "side": "Over" if p > .5 else "Under" if p < .5 else "none", "conf": max(p, 1 - p), "source": src, "version": r["model_version"],
               "start": r["match_start"]}
        if src != "legacy":
            rec.update(decompose(pf, src, rec["prop"], rec["scope"], r["lambda"]))
        out.append(rec)
    m = lambda xs: f"{statistics.mean(xs):+.2f}" if xs else "-"
    print(f"Board at {now.isoformat()}: {len(out)} standard board lines (latest forecast per pick, match not started); versions "
          f"{dict(collections.Counter(u['version'] for u in out))}\n")
    print("| stat | source | lines | Under share all | graded (55%+) | Under share graded | raw x R - line | prior | env | opp | cm-2 | proj - line |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for prop in ("kills", "hs"):
        for src in sorted({u["source"] for u in out}):
            g = [u for u in out if u["prop"] == prop and u["source"] == src and u["side"] != "none"]
            if not g:
                continue
            gr = [u for u in g if u["conf"] >= .55]
            dd = [u for u in g if u.get("L_raw") is not None]
            print(f"| {prop} | {src} | {len(g)} | {100 * sum(u['side'] == 'Under' for u in g) / len(g):.0f}% | {len(gr)} | "
                  f"{100 * sum(u['side'] == 'Under' for u in gr) / len(gr) if gr else 0:.0f}% | {m([u['L_raw'] - u['line'] for u in dd])} | "
                  f"{m([u['d_prior'] for u in dd])} | {m([u['d_env'] for u in dd])} | {m([u['d_opp'] for u in dd])} | {m([u['d_cm2'] for u in dd])} | "
                  f"{m([u['lam'] - u['line'] for u in g])} |")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else datetime.now(timezone.utc).isoformat())
