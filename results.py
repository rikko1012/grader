#!/usr/bin/env python3
"""
results.py - the members' Results page (2026-10-09). Read-only summaries of the pick log.

Kept apart, never mixed into one number:
- cohort: "board" = real Underdog offers the board graded (test=2) vs "member" = lines members typed in (research
  lines, test=0); owner tests (test=1) are never shown;
- model version (each pick = its FIRST published forecast, immutable);
- settled / push / void / pending, and where the result came from (HLTV vs BO3.gg);
- predicted (average printed chance) vs actual hit rate, by grade;
- returns: payout-based entry returns are NOT available (Underdog entry payouts aren't recorded) - said so; a
  hypothetical unit result is shown separately and labelled (each pick as a single at Underdog's displayed -112).
"""
import json
import threading
import time
from collections import Counter, defaultdict

GRADES = ("A+", "A", "A-", "B+", "B", "B-", "LEAN")
HYPO_WIN = 0.9            # decimal 1.90 (Underdog's displayed -112 per pick) -> +0.90 units per unit staked
_CACHE = {"at": 0.0, "rows": None}
_LOCK = threading.Lock()
TTL = 300


def _rows():
    with _LOCK:
        if _CACHE["rows"] is not None and time.time() - _CACHE["at"] < TTL:
            return _CACHE["rows"]
    import picklog
    rows = picklog.export()
    with _LOCK:
        _CACHE.update(at=time.time(), rows=rows)
    return rows


def _src(r):
    try:
        return (json.loads(r.get("result") or "{}") or {}).get("source")
    except (TypeError, ValueError):
        return None


def summary(cohort="board", version=None, frm=None, to=None, prop=None, scope=None, grades=None, rows=None):
    rows = _rows() if rows is None else rows
    test = 2 if cohort == "board" else 0
    pool = [r for r in rows if (r.get("test") or 0) == test]
    versions = Counter(r.get("model_version") or "untagged" for r in pool)
    sel = []
    for r in pool:
        d = (r.get("match_start") or "")[:10]
        if version and (r.get("model_version") or "untagged") != version:
            continue
        if frm and d < frm or to and d > to:
            continue
        if prop and r.get("prop") != prop:
            continue
        if scope and (r.get("scope") or "12") != scope:
            continue
        g = r.get("grade") if r.get("grade") in GRADES else "LEAN"
        if grades and g not in grades:
            continue
        sel.append(r)
    st = Counter(r.get("status") for r in sel)
    dec = [r for r in sel if r.get("status") == "settled" and r.get("hit") is not None]
    by = defaultdict(lambda: {"n": 0, "hits": 0, "pred": 0.0})
    for r in dec:
        g = r.get("grade") if r.get("grade") in GRADES else "LEAN"
        b = by[g]; b["n"] += 1; b["hits"] += int(r["hit"]); b["pred"] += float(r.get("p_pick") or 0.5)
    bets = [r for r in dec if (r.get("units") or 0) > 0]
    hypo = sum((r["units"] * HYPO_WIN) if r["hit"] else -r["units"] for r in bets)
    staked = sum(r["units"] for r in bets)
    return {
        "cohort": cohort, "filters": {"version": version, "from": frm, "to": to, "prop": prop, "scope": scope, "grades": grades},
        "versions": versions.most_common(), "picks": len(sel),
        "settled": st.get("settled", 0), "push": st.get("push", 0), "void": st.get("void", 0), "pending": st.get("open", 0),
        "matches": len({r.get("match_slug") or f"{r.get('player')}|{r.get('match_start')}" for r in sel}),
        "matches_decided": len({r.get("match_slug") or f"{r.get('player')}|{r.get('match_start')}" for r in dec}),
        "decided": len(dec), "hits": sum(int(r["hit"]) for r in dec),
        "predicted": round(sum(float(r.get("p_pick") or 0.5) for r in dec) / len(dec), 4) if dec else None,
        "actual": round(sum(int(r["hit"]) for r in dec) / len(dec), 4) if dec else None,
        "by_grade": [{"grade": g, "n": by[g]["n"], "hits": by[g]["hits"], "predicted": round(by[g]["pred"] / by[g]["n"], 4),
                      "actual": round(by[g]["hits"] / by[g]["n"], 4)} for g in GRADES if by[g]["n"]],
        "sources": dict(Counter(_src(r) or "unknown" for r in dec)),
        "returns": {"payout_based": None,
                    "payout_note": "Not available: actual Underdog entry payouts aren't recorded.",
                    "hypothetical_units": round(hypo, 2), "hypothetical_staked": round(staked, 2), "hypothetical_bets": len(bets),
                    "hypothetical_note": "Hypothetical: each B- or better pick as a single at Underdog's displayed -112 (1.90), "
                                         "staked at its printed units. Not an entry result."},
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
