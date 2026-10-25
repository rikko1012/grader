"""Public Results (rp-1) - SPORTS ONLY (esports has no results page by owner rule). Settled FORWARD forecasts and tracked
member picks, aggregated per league / stat / model. Accuracy only: no returns, no ROI, no P&L, no stakes.
Built in the WORKER (python -m sports.sheets build), served by sports/api_sheets.py:results_public.

File: <snapshots>/results_public.json

DEFINITIONS
  settled forecast  a stored forward forecast with a current settlement whose outcome at the FORECAST'S OWN line is
                    known (sports/settlement.at_line, LINE_CORRECTNESS C10); void / unresolved are counted apart.
  settled prop      one (game, player, stat) with at least one settled forecast - the validation gate counts these:
                    GATE = 300 settled props per league is the MINIMUM before the registered review (gate F,
                    REGISTER.md 7); the count alone never validates: "1,566 settled (minimum 300) · under review",
                    plus the one-line pass rule (GATE_RULE). "validated" stays False until a registered review passes.
  projection        error = settled value - projection mean; MAE and bias (mean error).
  Scout lean        the board's lean rule (SportsRows.leanOf, sports/UI.md): Scout records only (market-only models
                    have none); Over when P(Over) > 50% and the mean is above the line, Under mirrored. Hit rate = wins / (wins + losses); pushes listed apart.
  intervals         95% percentile bootstrap over GAMES (all rows of a game resampled together), 1,000 draws, seed 7;
                    none with fewer than 2 games.
  calibration       P(over | no push) in 10-point bands: mean stated chance vs observed Over share (pushes left out).
  probability band  the lean side's chance given no push: 50-55, 55-60, 60-65, 65+ (labelled "probability band" -
                    grade letters are withheld until the owner turns grades on).
  tracked picks     member picks (sports/picks.py aggregate, test picks excluded): wins / losses / pushes / voids,
                    hit rate with the same game-clustered interval. Never a return.
"""
from __future__ import annotations

import json
import random
import time
from collections import defaultdict
from pathlib import Path

from . import snapshots, store
from .contracts import now_utc

VERSION = "rp-1"
GATE = 300
GATE_RULE = ("Pass rule (registered gate F): at least 300 settled props from 100+ games at real offered lines; "
             "Scout's chance on its side within 3 points of how often that side hit (game-clustered interval "
             "including 0); and a lower log-loss than the market-only baseline on the same props. Reviewed at 300, "
             "600 and 1,000 settled props; nothing passes automatically.")


def gate_text(n, minimum=GATE):
    """'1,566 settled (minimum 300) · under review' - the same wording as SportsStats.validationState."""
    return f"{n:,} settled (minimum {minimum:,})" + (" · under review" if n >= minimum else "")
REFRESH_S = 1800
BANDS = ((0.50, 0.55, "50-55%"), (0.55, 0.60, "55-60%"), (0.60, 0.65, "60-65%"), (0.65, 1.01, "65%+"))


def path(base=None) -> Path:
    return snapshots.snap_root(base) / "results_public.json"


def cluster_ci(items, stat, reps=1000, seed=7):
    groups = defaultdict(list)
    for it in items:
        groups[it["event_id"] or it["forecast_id"]].append(it)
    keys = sorted(groups)
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        sample = [x for _k in range(len(keys)) for x in groups[keys[rng.randrange(len(keys))]]]
        v = stat(sample)
        if v is not None:
            vals.append(v)
    if len(vals) < reps // 2:
        return None
    vals.sort()
    return [round(vals[int(0.025 * (len(vals) - 1))], 4), round(vals[int(0.975 * (len(vals) - 1))], 4)]


def lean_side(r):
    """The board's lean (sports/UI.md "Prop board table", SportsRows.leanOf in web/sports/table.js - the same rule,
    applied server-side because results are scored in the worker): market-only records have no lean; Over when
    P(Over) > 50% and the projected mean (when present) is above the line; Under mirrored; else none."""
    from .forecast_detail import kind
    po, pu, mu, ln = r["p_over"], r["p_under"], r["mean"], r["line"]
    if po is None or pu is None or kind(r.get("model")) == "market":
        return None
    if po > 0.5 and (mu is None or mu > ln):
        return "over"
    if pu > 0.5 and (mu is None or mu < ln):
        return "under"
    return None


def _hit(xs):
    w = sum(1 for x in xs if x["lean_result"] == "win")
    l_ = sum(1 for x in xs if x["lean_result"] == "loss")
    return w / (w + l_) if w + l_ else None


def _mae(xs):
    e = [abs(x["error"]) for x in xs if x["error"] is not None]
    return sum(e) / len(e) if e else None


def _bias(xs):
    e = [x["error"] for x in xs if x["error"] is not None]
    return sum(e) / len(e) if e else None


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def rows_for(con, league):
    """One row per settled-or-void forward forecast of this league."""
    from .settlement import at_line
    setts = snapshots._current_settlements(con, league)
    by_fc = defaultdict(list)
    for s in setts:
        for fid in s.affected_forecast_ids:
            by_fc[fid].append(s)
    rows = []
    for fid, ss in by_fc.items():
        fc = store.get(con, fid)
        if fc is None or getattr(fc, "league", league) != league:
            continue
        outcome = None
        for s in ss:
            st, oc = at_line(s, fc.line, "over")
            if oc:
                outcome = oc
                break
        void = all(s.status == "void" for s in ss)
        actual = next((s.source_value for s in ss if s.source_value is not None), None)
        r = {"forecast_id": fid, "model": fc.model_version, "event_id": fc.event_id, "player_id": fc.player_id,
             "stat": fc.stat, "line": float(fc.line), "mean": fc.projection_mean, "p_over": fc.p_over,
             "p_under": fc.p_under, "p_push": fc.p_push, "research_only": fc.research_only,
             "status": "void" if void else ("settled" if outcome else "unresolved"), "outcome": outcome,
             "error": (actual - fc.projection_mean) if (outcome and actual is not None and fc.projection_mean is not None)
             else None}
        side = lean_side(r) if outcome else None
        r["lean"] = side
        r["lean_result"] = None if not side else ("push" if outcome == "push" else ("win" if outcome == side else "loss"))
        if side:
            ps, po_ = (r["p_over"], r["p_under"]) if side == "over" else (r["p_under"], r["p_over"])
            r["lean_p"] = ps / (ps + po_) if ps + po_ > 0 else None
        rows.append(r)
    return rows


def summarize(rows):
    rr = [r for r in rows if r["status"] == "settled"]
    leans = [r for r in rr if r["lean"]]
    return {"forecasts": len(rows), "settled": len(rr), "void": sum(1 for r in rows if r["status"] == "void"),
            "unresolved": sum(1 for r in rows if r["status"] == "unresolved"),
            "props": len({(r["event_id"], r["player_id"], r["stat"]) for r in rr}),
            "games": len({r["event_id"] for r in rr}),
            "projection": {"n": sum(1 for r in rr if r["error"] is not None), "mae": _r(_mae(rr)),
                           "mae_ci95": cluster_ci(rr, _mae), "bias": _r(_bias(rr)), "bias_ci95": cluster_ci(rr, _bias)},
            "leans": {"n": len(leans), "wins": sum(1 for r in leans if r["lean_result"] == "win"),
                      "losses": sum(1 for r in leans if r["lean_result"] == "loss"),
                      "pushes": sum(1 for r in leans if r["lean_result"] == "push"),
                      "hit_rate": _r(_hit(leans)), "hit_rate_ci95": cluster_ci(leans, _hit)}}


def calibration(rows):
    out = []
    rr = [r for r in rows if r["status"] == "settled" and r["outcome"] in ("over", "under")
          and r["p_over"] is not None and (r["p_over"] + r["p_under"]) > 0]
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        xs = [r for r in rr if lo <= r["p_over"] / (r["p_over"] + r["p_under"]) < hi or (i == 9 and
                                                                                       r["p_over"] / (r["p_over"] + r["p_under"]) == 1)]
        if not xs:
            continue

        def obs(x):
            return sum(1 for r in x if r["outcome"] == "over") / len(x) if x else None
        out.append({"band": f"{i * 10}-{(i + 1) * 10}%", "n": len(xs),
                    "stated": _r(sum(r["p_over"] / (r["p_over"] + r["p_under"]) for r in xs) / len(xs)),
                    "observed": _r(obs(xs)), "observed_ci95": cluster_ci(xs, obs)})
    return out


def bands(rows):
    out = []
    leans = [r for r in rows if r["status"] == "settled" and r["lean"] and r.get("lean_p") is not None]
    for lo, hi, label in BANDS:
        xs = [r for r in leans if lo <= r["lean_p"] < hi]
        wl = [r for r in xs if r["lean_result"] in ("win", "loss")]
        out.append({"band": label, "label": "probability band", "n": len(xs),
                    "wins": sum(1 for r in xs if r["lean_result"] == "win"),
                    "losses": sum(1 for r in xs if r["lean_result"] == "loss"),
                    "pushes": sum(1 for r in xs if r["lean_result"] == "push"),
                    "stated": _r(sum(r["lean_p"] for r in wl) / len(wl)) if wl else None,
                    "hit_rate": _r(_hit(xs)), "hit_rate_ci95": cluster_ci(xs, _hit)})
    return out


def tracked_picks():
    """Member pick aggregate per league (sports/picks.py; test picks excluded there). Public-safe fields only."""
    from . import picks
    try:
        con = picks.connect_ro()
    except FileNotFoundError:
        return {"note": "no picks recorded yet", "leagues": {}}
    try:
        agg = picks.aggregate(con)
    except Exception as e:                              # noqa: BLE001 - an empty / older picks.db
        return {"note": f"picks unavailable ({type(e).__name__})", "leagues": {}}
    finally:
        con.close()
    keep = ("picks", "games", "wins", "losses", "pushes", "voids", "unresolved", "pending", "hit_rate",
            "hit_rate_ci95_games")
    return {"version": agg.get("version"), "rule": agg.get("rule"),
            "leagues": {lg: {k: g.get(k) for k in keep} for lg, g in ((agg.get("by") or {}).get("league") or {}).items()},
            "note": "member picks only (test picks excluded); each pick at its own line; hit rate = wins / (wins + "
                    "losses); never a return"}


def build(con, now=None, base=None):
    t0 = time.time()
    now = now or now_utc()
    leagues = {}
    for lg in snapshots.LEAGUES:
        rows = rows_for(con, lg)
        s = summarize(rows)
        models = {}
        for mv in sorted({r["model"] for r in rows}):
            mr = [r for r in rows if r["model"] == mv]
            models[mv] = dict(summarize(mr), research_only=all(r["research_only"] for r in mr))
        stats = {st: summarize([r for r in rows if r["stat"] == st]) for st in sorted({r["stat"] for r in rows})}
        leagues[lg] = {**s, "gate": {"settled_props": s["props"], "required": GATE, "validated": False,
                                     "under_review": s["props"] >= GATE, "text": gate_text(s["props"]),
                                     "rule": GATE_RULE},
                       "models": models, "stats": stats, "calibration": calibration(rows), "bands": bands(rows)}
    doc = {"version": VERSION, "built_at": now_utc(), "as_of": now, "leagues": leagues, "picks": tracked_picks(),
           "grades": "withheld (owner has not turned grade letters on) - bands are probability bands",
           "returns": "none: this page measures accuracy only (no ROI, no profit, no stakes)",
           "method": {k: v.strip() for k, v in (
               ("settled", "forward forecasts only, scored at their own line from the box score; void / unresolved "
                           "listed apart"),
               ("gate", f"at least {GATE} settled props (game x player x stat) per league before the registered "
                        f"review; {GATE_RULE}"),
               ("projection", "error = settled value - projection mean; MAE and bias"),
               ("lean", "Scout models only: Over when its chance of Over is above 50% and its projection is above the "
                        "line (Under mirrored); hit rate = wins / (wins + losses), pushes apart"),
               ("ci", "95% bootstrap over games (1,000 draws, seed 7)"),
               ("calibration", "chance of Over given no push in 10-point bands vs the observed Over share"))}}
    size = snapshots._write(path(base), doc)
    return {"ok": True, "bytes": size, "build_s": round(time.time() - t0, 2),
            **{lg: leagues[lg]["gate"]["text"] for lg in leagues}}


def build_if_stale(con, force=False, base=None):
    try:
        if not force and time.time() - path(base).stat().st_mtime < REFRESH_S:
            return {"ok": True, "skipped": "fresh"}
    except OSError:
        pass
    return build(con, base=base)
