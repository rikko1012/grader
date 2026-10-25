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
  recent            rx-1 (owner 2026-10-25 "easier to understand"): the newest RECENT_N settled Scout leans, one row
                    per prop (game, player, stat, line; newest Scout forecast), newest settlement first - player name,
                    stat, line, lean side, result at that line, final value, rg-1 research grade, chance of the side.
  model gate        per league: the stats whose sm-1 model passed its registered gate (board_view.gate_state) and,
                    when none did, the member text "<LG> model not validated" (board_view.NOT_VALIDATED_TEXT).
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
    # THE forecast <-> settlement link (snapshots.settled_forecast_links: offer identity, plus any ids a settlement
    # names) - production settlements carry no affected_forecast_ids, so keying on those alone found none
    links, fcs = snapshots.settled_forecast_links(con, setts)
    by_offer = defaultdict(list)
    for s in setts:
        by_offer[(s.platform, s.offer_id)].append(s)
    by_fc = defaultdict(list)
    for k, ss in by_offer.items():
        for fid in links.get(k, ()):
            for s in ss:
                if s not in by_fc[fid]:
                    by_fc[fid].append(s)
    rows = []
    for fid, ss in by_fc.items():
        fc = fcs.get(fid) or store.get(con, fid)
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
             "forecast_at": fc.forecast_at, "actual": actual,
             "settled_at": max((x.settled_at or "") for x in ss) or None,
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


def _contained(league, stat):
    """vg-1 containment (board_view.VG_CONTAIN_LEAN on + the market's model failed its gate): no research letter."""
    from . import board_view
    return bool(league) and board_view.contain_reason(league, stat) is not None


def grades(rows, league=None):
    """rg-1 research letters of settled Scout leans: sports-gp-1 band of the UNROUNDED unconditional lean-side chance
    (board_view.research_grade); n, W-L-P, hit rate + game-clustered 95% interval. Research - not validated. The
    board's market (N4) / sanity holds are live-board rules and are not re-applied to stored forecasts here."""
    from . import board_view
    leans = [r for r in rows if r["status"] == "settled" and r["lean"] and not _contained(league, r["stat"])]
    out = []
    if league and _league_contained(league):
        from . import board_view
        return {"label": "Research grade - not validated", "rule": "rg-1", "rows": [],
                "withheld": board_view.NOT_VALIDATED_TEXT.format(LG=league.upper())}
    for g in ("A", "B", "C", "Lean"):
        xs = [r for r in leans if board_view.research_grade(r["p_over"] if r["lean"] == "over" else r["p_under"]) == g]
        out.append({"grade": g, "n": len(xs), "wins": sum(1 for r in xs if r["lean_result"] == "win"),
                    "losses": sum(1 for r in xs if r["lean_result"] == "loss"),
                    "pushes": sum(1 for r in xs if r["lean_result"] == "push"),
                    "hit_rate": _r(_hit(xs)), "hit_rate_ci95": cluster_ci(xs, _hit)})
    return {"label": "Research grade - not validated", "rule": "rg-1", "rows": out}


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
    bgl = agg.get("by_grade_league") or {}
    return {"version": agg.get("version"), "rule": agg.get("rule"),
            "leagues": {lg: dict({k: g.get(k) for k in keep},
                                 by_grade=None if _league_contained(lg) else bgl.get(lg))
                        for lg, g in ((agg.get("by") or {}).get("league") or {}).items()},
            "note": "member picks only (test picks excluded); each pick at its own line; hit rate = wins / (wins + "
                    "losses); never a return"}


RECENT_N = 50


def _league_contained(league):
    st = _league_stats(league)
    return bool(st) and all(_contained(league, x) for x in st)


def _league_stats(league):
    from . import capabilities
    return list(capabilities.SUPPORTED.get(league, ()))


def recent(rows, names=None, n=RECENT_N, league=None):
    """rx-1: newest n settled Scout leans, one per prop (newest Scout forecast of that prop), newest settled first."""
    from . import board_view
    best = {}
    for r in rows:
        if r["status"] != "settled" or not r["lean"]:
            continue
        k = (r["event_id"], r["player_id"], r["stat"], round(float(r["line"]), 3))
        if k not in best or (r.get("forecast_at") or "") > (best[k].get("forecast_at") or ""):
            best[k] = r
    out = []
    for r in sorted(best.values(), key=lambda x: (x.get("settled_at") or "", x["forecast_id"]), reverse=True)[:n]:
        p_side = r["p_over"] if r["lean"] == "over" else r["p_under"]
        out.append({"forecast_id": r["forecast_id"], "model": r["model"], "event_id": r["event_id"],
                    "player_id": r["player_id"], "player": (names or {}).get(r["player_id"]), "stat": r["stat"],
                    "line": r["line"], "side": r["lean"], "result": r["lean_result"], "outcome": r["outcome"],
                    "actual": r.get("actual"), "p_side": _r(p_side),
                    "grade": None if _contained(league, r["stat"]) else board_view.research_grade(p_side),
                    "settled_at": r.get("settled_at"), "forecast_at": r.get("forecast_at")})
    return out


def model_gate(league):
    """Which of the league's stats passed sm-1's registered gate; the plain member text when none did."""
    from . import board_view, capabilities
    stats = list(capabilities.SUPPORTED.get(league, ()))
    ok = [st for st in stats if board_view.gate_reason(league, st) is None]
    return {"rule": board_view.GATE_VERSION, "validated_stats": ok,
            "contained": _league_contained(league),
            "not_validated": None if ok or not stats else board_view.NOT_VALIDATED_TEXT.format(LG=league.upper())}


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
        rec = recent(rows, league=lg)
        try:
            names = snapshots.player_names(con, {r["player_id"] for r in rec if r.get("player_id")}, now)
        except Exception:                               # noqa: BLE001 - names are a nicety; ids stay
            names = {}
        for r in rec:
            r["player"] = names.get(r["player_id"])
        leagues[lg] = {**s, "gate": {"settled_props": s["props"], "required": GATE, "validated": False,
                                     "under_review": s["props"] >= GATE, "text": gate_text(s["props"]),
                                     "rule": GATE_RULE},
                       "models": models, "stats": stats, "calibration": calibration(rows), "bands": bands(rows), "grades": grades(rows, lg),
                       "recent": rec, "model_gate": model_gate(lg),
                       "head_to_head": {"version": HH_VERSION, "method": HH_METHOD, "rows": head_to_head(rows)}}
    doc = {"version": VERSION, "built_at": now_utc(), "as_of": now, "leagues": leagues, "picks": tracked_picks(),
           "grades": "research grades rg-1 (owner 2026-10-25): sports-gp-1 bands of the unrounded lean-side chance - "
                     "A >= 60%, B >= 55.59%, C >= 53.45%, else Lean; research, not validated; stakes 0",
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


# ------------------------------------------------------------------------------------------- hh-1 head to head (N5)
# Scout vs market on the SAME props (owner re-audit 2026-10-24 N5): the per-model tables above score each model on
# the props IT forecast, so their Brier scores are not comparable. hh-1 pairs them:
#   prop key   (game, player, stat, line) - the same line, so the same settlement outcome
#   pair       the newest forecast of a Scout model (sm-1 / mi-1) and the newest of a market model (mc-1, sharp-*,
#              mkt-<source>) on the same key, both settled Over or Under (pushes / voids counted, not scored)
#   scores     binary, on the chance of Over given no push q = P(Over) / (P(Over) + P(Under)):
#              Brier (q - y)^2 (0.25 = always 50%), log loss -ln(chance given to what happened), hit rate of the
#              side the model favoured (q = 0.5 exactly: no side, left out of that model's hit rate)
#   difference Scout minus market per pair (Brier / log loss: negative = Scout better); 95% bootstrap interval over
#              GAMES (all pairs of a game resampled together, 1,000 draws, seed 7)
HH_VERSION = "hh-1"
SCOUT = ("sm-1", "mi-1")


def _is_market(mv):
    return mv == "mc-1" or mv.startswith("sharp") or mv.startswith("mkt-")


def binary_scores(p_over, p_under, outcome):
    """-> (q, brier, log loss, favoured side or None) for a settled Over / Under outcome, or None."""
    if outcome not in ("over", "under") or p_over is None or p_under is None or p_over + p_under <= 0:
        return None
    import math
    q = p_over / (p_over + p_under)
    y = 1.0 if outcome == "over" else 0.0
    pq = q if y else 1 - q
    fav = "over" if q > 0.5 else "under" if q < 0.5 else None
    return q, (q - y) ** 2, -math.log(max(1e-12, pq)), fav


def head_to_head(rows):
    """rows: dicts with model, event_id, player_id, stat, line, p_over, p_under, outcome, status, forecast_at.
    -> [{"scout", "market", "stat", "pairs", "pushes", "games", "scout_brier", ..., "diff": {...}}] (stat "all" first)."""
    newest = {}
    for r in rows:
        if r.get("status") not in ("settled", "void") or not r.get("model"):
            continue
        k = (r["event_id"], r["player_id"], r["stat"], round(float(r["line"]), 3), r["model"])
        if k not in newest or (r.get("forecast_at") or "") > (newest[k].get("forecast_at") or ""):
            newest[k] = r
    by_key = defaultdict(dict)
    for (ev, pid, st, ln, mv), r in newest.items():
        by_key[(ev, pid, st, ln)][mv] = r
    pairs = defaultdict(list)
    pushes = defaultdict(int)
    for (ev, pid, st, ln), ms in by_key.items():
        for s in SCOUT:
            if s not in ms:
                continue
            for mk in ms:
                if not _is_market(mk):
                    continue
                a, b = ms[s], ms[mk]
                if a["status"] != "settled" or b["status"] != "settled":
                    continue
                if a["outcome"] == "push":
                    for g in (st, "all"):
                        pushes[(s, mk, g)] += 1
                    continue
                sa = binary_scores(a["p_over"], a["p_under"], a["outcome"])
                sb = binary_scores(b["p_over"], b["p_under"], b["outcome"])
                if sa is None or sb is None:
                    continue
                item = {"event_id": ev, "forecast_id": a.get("forecast_id"), "outcome": a["outcome"],
                        "sb": sa[1], "mb": sb[1], "sl": sa[2], "ml": sb[2],
                        "sh": None if sa[3] is None else (1.0 if sa[3] == a["outcome"] else 0.0),
                        "mh": None if sb[3] is None else (1.0 if sb[3] == a["outcome"] else 0.0)}
                for g in (st, "all"):
                    pairs[(s, mk, g)].append(item)

    def mean(key):
        def f(xs):
            v = [x[key] for x in xs if x[key] is not None]
            return sum(v) / len(v) if v else None
        return f

    def diff(a, b):
        def f(xs):
            v = [x[a] - x[b] for x in xs if x[a] is not None and x[b] is not None]
            return sum(v) / len(v) if v else None
        return f
    out = []
    for (s, mk, g), xs in sorted(pairs.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] != "all", kv[0][2])):
        out.append({"scout": s, "market": mk, "stat": g, "pairs": len(xs), "pushes": pushes.get((s, mk, g), 0),
                    "games": len({x["event_id"] for x in xs}),
                    "scout_brier": _r(mean("sb")(xs)), "market_brier": _r(mean("mb")(xs)),
                    "scout_log_loss": _r(mean("sl")(xs)), "market_log_loss": _r(mean("ml")(xs)),
                    "scout_hit_rate": _r(mean("sh")(xs)), "market_hit_rate": _r(mean("mh")(xs)),
                    "diff": {"brier": _r(diff("sb", "mb")(xs)), "brier_ci95": cluster_ci(xs, diff("sb", "mb")),
                             "log_loss": _r(diff("sl", "ml")(xs)), "log_loss_ci95": cluster_ci(xs, diff("sl", "ml")),
                             "hit_rate": _r(diff("sh", "mh")(xs)), "hit_rate_ci95": cluster_ci(xs, diff("sh", "mh"))}})
    return out


HH_METHOD = ("Scout (sm-1 / mi-1) and each market estimate on the SAME props: same game, player, stat and line, so the "
             "same settlement; newest forecast of each; pushes and voids counted, not scored. Binary scores on the "
             "chance of Over given no push: Brier (0.25 = always 50%), log loss, hit rate of the favoured side. "
             "Difference = Scout minus market (Brier / log loss below 0 = Scout better), 95% bootstrap interval over "
             "games (1,000 draws, seed 7).")
