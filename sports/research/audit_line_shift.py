"""Audit of the data branch's line-shift transformation (fair.at_line + dispersion.json), 2026-10-23.

Question: when the sharp price is at line A and the pick'em line is B, sharp-1 solves the mean of a fixed-variance
distribution (negative binomial for counts, normal for points / yards; variance function fitted on whole-season
player variance) so P(X > A) matches the price, then reads P(X > B). Is that calibrated?

Part A - the market's own ladder (no outcomes needed, out of sample by construction): Kalshi lists several rungs for
one player and stat at the same moment (10+, 15+, 20+ ...). Anchor on one rung, predict a neighbouring rung's price
with the sharp-1 transformation, compare with that rung's actual midpoint. Eligible rungs: both YES bid and ask, spread
<= 0.05 (sharp-1's own cut), midpoint in [0.05, 0.95]. Reported by stat and line gap, signed (pred - market).
Also: per ladder, the variance that best reproduces the ladder (descriptive only - never adopted here).

Part B - held-out outcomes: ESPN game logs for the season BEFORE the one dispersion.json was fitted on (NBA 2024-25,
NFL 2024), players drawn from today's Kalshi boards. Each game after >= 10 earlier games that season: centre = mean of
the previous 10 games (known before the game), predictive distribution = sharp-1 shape at that centre. Reported: PIT
coverage of the central 50% / 80% intervals and calibration of P(X > centre + k) for several k. A trailing mean is a
noisier centre than a market price, so a calibrated sharp-1 shape here would still be TOO WIDE around a market price;
an over-wide result here is conclusive in the same direction.

    SPORTS_DATA_DIR=<store with a Kalshi ingest> python -m sports.research.audit_line_shift [--players 60] [--out f]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone

from .. import config, espn, fair, markets, net, store

STATS = {"nba": ("points", "rebounds", "assists", "threes"), "nfl": ("pass_yds", "rush_yds", "rec_yds", "rec", "rush_att")}
HELD_OUT_SEASON = {"nba": 2025, "nfl": 2024}          # ESPN season labels: NBA 2024-25, NFL 2024


def _mid(o):
    s = o.side("over")
    if not s or s.bid is None or s.ask is None or s.ask - s.bid > 0.05 + 1e-9:
        return None
    m = (s.bid + s.ask) / 2
    return m if 0.05 <= m <= 0.95 else None


def ladders(con, league):
    out = defaultdict(list)
    for _, o in store.offers(con, league=league, platform="kalshi", limit=500000):
        if o.stat in STATS[league] and o.availability == "open" and o.event_id and o.player_id:
            m = _mid(o)
            if m is not None:
                out[(o.event_id, o.player_id, o.stat)].append((o.line, m, o.liquidity_dict().get("volume") or 0.0))
    return {k: sorted(v) for k, v in out.items() if len(v) >= 2}


def _shape(fam, market):
    spec = markets.market(fam, market)
    a, b = fair.shape_params(fam, market, spec["shape"])
    return spec["shape"], a, b


def part_a(con, league):
    fam = config.league(league)["family"]
    rows = defaultdict(list)
    implied = defaultdict(list)
    between = defaultdict(list)
    for (ev, pl, mk), rungs in ladders(con, league).items():
        shape, a, b = _shape(fam, mk)
        for i, (la, pa, _) in enumerate(rungs):
            for j, (lb, pb, _) in enumerate(rungs):
                if i == j:
                    continue
                pred = fair.at_line(fam, mk, shape, la, pa, lb)["over"]
                rows[(mk, round(lb - la, 1))].append(pred - pb)
        if len(rungs) >= 3:
            implied[mk].append(_implied_scale(shape, a, b, rungs))
            for j in range(1, len(rungs) - 1):          # sharp-1's "between rungs": both neighbours, 1/gap weights
                (l0, p0, _), (lj, pj, _), (l1, p1, _) = rungs[j - 1], rungs[j], rungs[j + 1]
                f0 = fair.at_line(fam, mk, shape, l0, p0, lj)["over"]
                f1 = fair.at_line(fam, mk, shape, l1, p1, lj)["over"]
                w0, w1 = 1 / abs(lj - l0), 1 / abs(l1 - lj)
                between[mk].append((f0 * w0 + f1 * w1) / (w0 + w1) - pj)
    by = {}
    for (mk, gap), errs in sorted(rows.items()):
        by.setdefault(mk, {})[f"{gap:+g}"] = {"n": len(errs), "mean_pred_minus_market": round(st.mean(errs), 4),
                                               "mae": round(st.mean(abs(e) for e in errs), 4)}
    imp = {mk: {"ladders": len(v), "median_var_ratio": round(st.median(v), 3),
                "q25": round(sorted(v)[len(v) // 4], 3), "q75": round(sorted(v)[(3 * len(v)) // 4], 3)}
           for mk, v in implied.items() if v}
    btw = {mk: {"n": len(v), "mean_pred_minus_market": round(st.mean(v), 4), "mae": round(st.mean(abs(x) for x in v), 4)}
           for mk, v in between.items() if v}
    return {"by_stat_gap": by, "between_rungs": btw, "implied_variance_ratio": imp}


def _implied_scale(shape, a, b, rungs):
    """Ratio s such that scaling sharp-1's variance by s best reproduces the ladder's midpoints (grid search, squared
    error). s < 1 = the market's distribution is NARROWER than sharp-1's."""
    best = (1e9, 1.0)
    for s in [x / 20 for x in range(4, 41)]:
        mean = _solve_mean_scaled(shape, a, b, s, rungs[len(rungs) // 2][0], rungs[len(rungs) // 2][1])
        err = sum((_p_over_scaled(shape, a, b, s, mean, ln) - p) ** 2 for ln, p, _ in rungs)
        if err < best[0]:
            best = (err, s)
    return best[1]


def _p_over_scaled(shape, a, b, s, mean, line):
    if shape == "normal":
        sd = math.sqrt(max(1e-9, s * (a * mean + b * mean * mean)))
        return 1 - 0.5 * (1 + math.erf((math.floor(line) + 0.5 - mean) / sd / math.sqrt(2)))
    var = s * (mean + b * mean * mean)
    if var <= mean * 1.0001:                           # under-dispersed relative to Poisson: binomial-like via normal
        sd = math.sqrt(max(1e-9, var))
        return 1 - 0.5 * (1 + math.erf((math.floor(line) + 0.5 - mean) / sd / math.sqrt(2)))
    bb = (var - mean) / (mean * mean)
    return fair.dist_probs("count", 1.0, bb, mean, line)[0]


def _solve_mean_scaled(shape, a, b, s, line, p):
    lo, hi = 1e-3, max(5.0, line * 4 + 10)
    for _ in range(100):
        mid = (lo + hi) / 2
        if _p_over_scaled(shape, a, b, s, mid, line) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------------------------------- part B
def _gamelog_season(lg, athlete_id, season):
    url = f"{espn.WEB}/{espn._path(lg)}/athletes/{athlete_id}/gamelog"
    from .. import cache
    body = cache.fetch("espn:gamelog", f"{lg['key']}:{athlete_id}:{season}", 30 * 86400,
                       lambda: net.get_json(url, params={"season": season}))
    return sorted(espn.parse_gamelog(body, lg["family"]), key=lambda g: g["date"] or "")


def part_b(con, league, n_players, seed=7):
    lg = config.league(league)
    fam = lg["family"]
    by_stat = defaultdict(set)
    for _, o in store.offers(con, league=league, platform="kalshi", limit=500000):
        if o.stat in STATS[league] and o.player_id:
            by_stat[o.stat].add(o.player_id.split(":")[-1])
    rng = random.Random(seed)
    players = sorted(set().union(*by_stat.values()))
    rng.shuffle(players)
    players = players[:n_players]
    pit = defaultdict(list)
    calib = defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0]))     # stat -> k -> [n, sum pred, hits]
    used = 0
    pit_rng = random.Random(seed + 1)
    for aid in players:
        try:
            games = _gamelog_season(lg, aid, HELD_OUT_SEASON[league])
        except net.HttpError:
            continue
        games = [g for g in games if fam != "basketball" or (g["parts"].get("min") or 0) > 0]
        if len(games) < 12:
            continue
        used += 1
        for mk in STATS[league]:
            if aid not in by_stat[mk]:
                continue
            vals = [markets.value(fam, mk, g["parts"]) for g in games]
            if any(v is None for v in vals):
                continue
            shape, a, b = _shape(fam, mk)
            for t in range(10, len(vals)):
                mu = st.mean(vals[t - 10:t])
                if mu <= 0.5:
                    continue
                x = vals[t]
                # randomised PIT for a whole-number outcome: F(x-1) + V * (F(x) - F(x-1)), V ~ U(0,1) (seeded)
                f_x = fair.dist_probs(shape, a, b, mu, math.floor(x) + 0.5)[1]
                f_xm1 = 0.0 if (shape == "count" and x < 1) else fair.dist_probs(shape, a, b, mu, math.floor(x) - 0.5)[1]
                pit[mk].append(f_xm1 + pit_rng.random() * (f_x - f_xm1))
                step = {"points": 2.5, "pass_yds": 15.0, "rush_yds": 10.0, "rec_yds": 10.0}.get(mk, 1.0)
                base = math.floor(mu) + 0.5
                for k in (-2, -1, 0, 1, 2):
                    line = base + k * step if step != 1.0 else base + k
                    po = fair.dist_probs(shape, a, b, mu, line)[0]
                    c = calib[mk][k]
                    c[0] += 1
                    c[1] += po
                    c[2] += int(x > line)
    out = {"players_with_12plus_games": used, "season": HELD_OUT_SEASON[league], "by_stat": {}}
    for mk, ps in pit.items():
        n = len(ps)
        out["by_stat"][mk] = {
            "games": n,
            "central_50_coverage": round(sum(1 for q in ps if 0.25 <= q <= 0.75) / n, 3),
            "central_80_coverage": round(sum(1 for q in ps if 0.10 <= q <= 0.90) / n, 3),
            "calibration_p_over_centre_plus_k": {str(k): {"n": c[0], "mean_pred": round(c[1] / c[0], 3),
                                                          "observed": round(c[2] / c[0], 3)}
                                                 for k, c in sorted(calib[mk].items())}}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", type=int, default=60)
    ap.add_argument("--out")
    ap.add_argument("--skip-b", action="store_true")
    a = ap.parse_args(argv)
    res = {"run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "dispersion": fair._dispersion().get(
        "_fitted_at"), "store": str(store.db_path())}
    with store.opened() as con:
        for lg in ("nba", "nfl"):
            res[lg] = {"part_a_kalshi_ladder": part_a(con, lg)}
            if not a.skip_b:
                res[lg]["part_b_held_out_outcomes"] = part_b(con, lg, a.players)
    txt = json.dumps(res, indent=1)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
    sys.stdout.write(txt + "\n")


if __name__ == "__main__":
    main()
