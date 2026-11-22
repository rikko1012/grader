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
  ONE PICK PER PROP rp-2 (audit 2026-11-05 A7): Sports forecasts every listed line of a prop (pick'em main lines,
                    every book's alternates, Kalshi rungs), so counting forecasts counted the same game x player x stat
                    up to hundreds of times (31,882 "picks" in ~11 days). Every count, hit rate, band, grade, calendar
                    and recent row now uses ONE representative forecast per (game, player, stat):
                      1. per line, the newest forecast of that line (the last one before the game);
                      2. a line settled on a pick'em app (PrizePicks / Underdog, any line type: standard, goblin,
                         demon, alternate) before a sportsbook line, before a Kalshi rung only;
                      3. then the line CLOSEST TO THE FORECAST'S OWN PROJECTION (|line - projection mean|, what the
                         model saw at forecast time); ties: newest forecast, then forecast id. This is result-blind
                         but it is NOT necessarily the board's headline line: it is the line where that model leaned
                         least, so stated hit rates lean toward closer-to-even lines (said in COUNTING);
                      4. across Scout models the board's model order (snapshots.models_pref) decides; a Scout model
                         before a market-only model.
                    Per model tables use step 1-3 inside each model. The head-to-head pairs EACH Scout model with
                    the market on THAT model's own representative line only (one pair per model per prop).
                    raw_counts keeps the forecast-level totals for the owner.
                    Excluded entirely: quarter / half forecasts (S1/S2) and forecasts saved for a stored one-sided
                    Underdog line (cap-2, owner 2026-11-06: Over only / Under only lines are not graded).
"""
from __future__ import annotations

import itertools
import json
import random
import time
from collections import defaultdict
from pathlib import Path

from . import grade_rules, snapshots, store
from .contracts import now_utc

VERSION = "rp-2"
PICKEM = ("prizepicks", "underdog")
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
    """95% percentile bootstrap over games (all items of a game drawn together), `reps` draws, seed 7.
    A stat that declares how it reduces (ci_spec, below) is evaluated from per-game value lists / counts instead of a
    fresh list of every drawn row per draw (sheets job 2026-11-08: 1.3-5.5 s per call at 14k-40k rows, called a few
    hundred times per league). Same games in the same sorted order, the same rng draws, the same values in the same
    order handed to sum() (itertools.chain over the drawn games' lists) and the same integer counts -> bit-identical
    intervals (sports/tests/test_sports_jobs_1108.py compares it with the original on random inputs)."""
    groups = defaultdict(list)
    for it in items:
        groups[it["event_id"] or it["forecast_id"]].append(it)
    keys = sorted(groups)
    if len(keys) < 2:
        return None
    spec = getattr(stat, "ci_spec", None)
    if spec is not None and spec[0] == "mean":          # mean of value(x) over items whose value is not None
        value = spec[1]
        return ci_mean([[v for v in map(value, groups[k]) if v is not None] for k in keys], reps, seed)
    if spec is not None and spec[0] == "ratio":         # sum(num(x)) / sum(den(x)), integer counts
        num, den = spec[1], spec[2]
        return ci_ratio([(sum(num(x) for x in groups[k]), sum(den(x) for x in groups[k])) for k in keys], reps, seed)
    rng = random.Random(seed)
    n = len(keys)
    vals = []
    for _ in range(reps):
        sample = [x for _k in range(n) for x in groups[keys[rng.randrange(n)]]]
        v = stat(sample)
        if v is not None:
            vals.append(v)
    return _ci_end(vals, reps)


def ci_mean(per, reps=1000, seed=7):
    """cluster_ci of a mean from per-game value lists (games in sorted key order, None values already left out)."""
    n = len(per)
    if n < 2:
        return None
    rng = random.Random(seed)
    vals = []
    lens = [len(v) for v in per]
    chain = itertools.chain.from_iterable
    for _ in range(reps):
        pick = [rng.randrange(n) for _k in range(n)]
        cnt = sum(lens[i] for i in pick)
        if cnt:
            vals.append(sum(chain([per[i] for i in pick])) / cnt)
    return _ci_end(vals, reps)


def ci_ratio(per, reps=1000, seed=7):
    """cluster_ci of sum(num) / sum(den) from per-game (num, den) integer totals (games in sorted key order)."""
    n = len(per)
    if n < 2:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        a = b = 0
        for _k in range(n):
            pa, pb = per[rng.randrange(n)]
            a += pa
            b += pb
        if b:
            vals.append(a / b)
    return _ci_end(vals, reps)


def _ci_end(vals, reps):
    if len(vals) < reps // 2:
        return None
    vals.sort()
    return [round(vals[int(0.025 * (len(vals) - 1))], 4), round(vals[int(0.975 * (len(vals) - 1))], 4)]


class _MeanCI:
    """One cluster_ci mean statistic built one item at a time (2026-11-08 review: the season's rows are never held):
    per game (event_id, else forecast_id - every item registers its game, as cluster_ci groups every item) the
    non-None values in arrival order, and every value in arrival order for the plain mean."""
    __slots__ = ("groups", "vals")

    def __init__(self):
        self.groups, self.vals = {}, []

    def add(self, gkey, v):
        g = self.groups.get(gkey)
        if g is None:
            g = self.groups[gkey] = []
        if v is not None:
            g.append(v)
            self.vals.append(v)

    def mean(self, fn=None):
        if not self.vals:
            return None
        return (sum(self.vals) if fn is None else sum(map(fn, self.vals))) / len(self.vals)

    def ci(self, fn=None):
        if len(self.groups) < 2:
            return None
        return ci_mean([self.groups[k] if fn is None else list(map(fn, self.groups[k])) for k in sorted(self.groups)])


class _RatioCI:
    """One cluster_ci ratio statistic built one item at a time: integer (num, den) per game."""
    __slots__ = ("groups",)

    def __init__(self):
        self.groups = {}

    def add(self, gkey, num, den):
        g = self.groups.get(gkey)
        if g is None:
            g = self.groups[gkey] = [0, 0]
        g[0] += num
        g[1] += den

    def ci(self):
        if len(self.groups) < 2:
            return None
        return ci_ratio([(self.groups[k][0], self.groups[k][1]) for k in sorted(self.groups)])


def _mean_spec(fn, value):
    """Declare fn(xs) == mean of value(x) over xs, None values left out (None when none) - cluster_ci's fast path."""
    fn.ci_spec = ("mean", value)
    return fn


def _ratio_spec(fn, num, den):
    """Declare fn(xs) == sum(num(x)) / sum(den(x)) (None when the denominator is 0), integer num / den."""
    fn.ci_spec = ("ratio", num, den)
    return fn


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


_ratio_spec(_hit, lambda x: 1 if x["lean_result"] == "win" else 0,
            lambda x: 1 if x["lean_result"] in ("win", "loss") else 0)
_mean_spec(_mae, lambda x: abs(x["error"]) if x["error"] is not None else None)
_mean_spec(_bias, lambda x: x["error"])


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def settlement_chunks(con, league):
    """The CURRENT settlements of this league's events that hold a forecast, a few events at a time (the same stream
    as snapshots.write_results; was: every settlement of the league ever stored, decoded at once - a season of
    ~6,000 sides a day outgrows the sheets job's memory cap)."""
    fc_events = {r[0] for r in con.execute("SELECT DISTINCT event_id FROM records WHERE kind='forecast' AND league = ?",
                                           (league,))}
    st_events = sorted(fc_events & {r[0] for r in con.execute(
        "SELECT DISTINCT event_id FROM records WHERE kind='settlement' AND event_id LIKE ?", (f"{league}:%",))})
    n = snapshots.RESULTS_EVENTS_PER_PASS
    for i in range(0, len(st_events), n):
        yield snapshots._event_settlements(con, st_events[i:i + n])


def rows_for(con, league):
    """One row per settled-or-void forward forecast of this league (streamed by event, settlement_chunks)."""
    rows = []
    for setts in settlement_chunks(con, league):
        rows += _rows_from(con, league, setts)
    return rows


def _rows_from(con, league, setts):
    from .settlement import at_line
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
    try:                                                # A9a 6: the chance members saw (cal-mi ON), raw kept
        from .model import shown as _shown
        seen = _shown.get_many(by_fc)
    except Exception:                                   # noqa: BLE001 - results never break on the side store
        seen = {}
    os_cache = {}
    for fid, ss in by_fc.items():
        fc = fcs.get(fid) or store.get(con, fid)
        if fc is None or getattr(fc, "league", league) != league:
            continue
        if (getattr(fc, "scope", "full_game") or "full_game") != "full_game":
            continue                                    # S1/S2: a quarter / half line is never in the record
        plats = sorted({s.platform for s in ss if getattr(s, "platform", None)})
        # cap-2 (owner 2026-11-06): a one-sided line is not graded, so a forecast saved for one is not in the record.
        # Read from the STORED offer variant, never today's offered sides (a suspended side never drops a line)
        if any(p in PICKEM for p in plats) and snapshots.one_sided_forecast(con, fc, os_cache):
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
             "forecast_at": fc.forecast_at, "actual": actual, "platforms": plats,
             "settled_at": max((x.settled_at or "") for x in ss) or None,
             "status": "void" if void else ("settled" if outcome else "unresolved"), "outcome": outcome,
             "error": (actual - fc.projection_mean) if (outcome and actual is not None and fc.projection_mean is not None)
             else None}
        sh = seen.get(fid)
        if sh is not None:                              # score the displayed chance; the record's raw chance kept
            r["raw"] = [fc.p_over, fc.p_under, fc.p_push]
            r.update(p_over=sh["p_over"], p_under=sh["p_under"], p_push=sh["p_push"], chance="shown")
        side = lean_side(r) if outcome else None
        r["lean"] = side
        r["lean_result"] = None if not side else ("push" if outcome == "push" else ("win" if outcome == side else "loss"))
        if side:
            ps, po_ = (r["p_over"], r["p_under"]) if side == "over" else (r["p_under"], r["p_over"])
            r["lean_p"] = ps / (ps + po_) if ps + po_ > 0 else None
        rows.append(r)
    return rows


def _source_rank(r):
    ps = r.get("platforms") or ()
    if any(p in PICKEM for p in ps):
        return 0
    if any(p != "kalshi" for p in ps):
        return 1
    return 2 if ps else 1


def _closeness(r):
    mu = r.get("mean")
    return abs(float(r["line"]) - float(mu)) if mu is not None else float("inf")


def representative(rows):
    """rp-2 steps 1-3: one forecast per (game, player, stat, model) - see the module doc. SPORTS_LATEST_LINE (default
    off; grade_rules.latest_line_on): when on, only lines that are their book's latest (current_lines) can be chosen."""
    newest, latest, opens = {}, {}, {}
    for r in rows:
        _rep_newest_add(newest, r)
        _latest_add(latest, r)
        _open_add(opens, r)
    cur = _current_set(latest)
    newest = {k: r for k, r in newest.items()
              if (k[:4], k[4]) in cur or not grade_rules.latest_line_on(str(k[0]).split(":", 1)[0])}
    out = _rep_select(newest)
    for r in out:
        r["mv_open"] = _open_of(opens, r)
    return out


def _open_keys(r):
    """mvlean-1: (game, player, stat, platform) keys of an sm-1 pick'em row, else []."""
    if r.get("model") != "sm-1":
        return []
    return [(r["event_id"], r["player_id"], r["stat"], p) for p in (r.get("platforms") or ()) if p in PICKEM]


def _open_add(opens, r):
    """mvlean-1: the first sm-1 forecast's line per (game, player, stat, platform) (grade_rules.OpenLines' key)."""
    fa = r.get("forecast_at") or ""
    for k in _open_keys(r):
        if k not in opens or (fa, r["forecast_id"]) < opens[k][1:]:
            opens[k] = (float(r["line"]), fa, r["forecast_id"])


def _open_of(opens, r):
    """The open line for a representative row (its single pick'em platform), or None."""
    ks = [k for k in _open_keys(r) if k in opens]
    return opens[ks[0]][0] if len(ks) == 1 else None


def _plat_keys(r):
    """(game, player, stat, model, platform) for each platform a row settled on (one key with platform None if none)."""
    k4 = (r["event_id"], r["player_id"], r["stat"], r.get("model"))
    return [k4 + (p,) for p in (r.get("platforms") or [None])]


def _latest_add(latest, r):
    """SPORTS_LATEST_LINE: {(game, player, stat, model, platform): {line: newest forecast_at}} - a book's latest line is
    the line of its newest forecast (several lines at that same newest time are all current)."""
    ln, fa = round(float(r["line"]), 3), r.get("forecast_at") or ""
    for kp in _plat_keys(r):
        d = latest.setdefault(kp, {})
        if fa > d.get(ln, ""):
            d[ln] = fa


def _current_set(latest):
    """-> {((game, player, stat, model), line)} for every line that is some platform's latest line."""
    out = set()
    for kp, d in latest.items():
        top = max(d.values())
        for ln, fa in d.items():
            if fa == top:
                out.add((kp[:4], ln))
    return out


def _rep_key(r):
    return (r["event_id"], r["player_id"], r["stat"], r.get("model"), round(float(r["line"]), 3))


def _rep_newest_add(newest, r):
    """rp-2 step 1, one row at a time: the newest forecast per (game, player, stat, model, line)."""
    k = _rep_key(r)
    if k not in newest or ((r.get("forecast_at") or ""), r["forecast_id"]) > \
            ((newest[k].get("forecast_at") or ""), newest[k]["forecast_id"]):
        newest[k] = r


def _rep_select(newest):
    """rp-2 steps 2-3 over step 1's {key: row}."""
    return _rep_order([(r["forecast_id"], r.get("forecast_at") or "", (_source_rank(r), _closeness(r)), k[:4], r)
                       for k, r in newest.items()])


def _rep_order(cands):
    """cands: [(forecast_id, forecast_at or "", (source rank, closeness), (game, player, stat, model), row)] in step 1's
    order -> the chosen rows. Newest first, then a stable sort on (source rank, closeness): ties keep the newer
    forecast, then the forecast id."""
    cands = sorted(cands, key=lambda c: c[0])
    cands.sort(key=lambda c: c[1], reverse=True)
    cands.sort(key=lambda c: c[2])
    best = {}
    for c in cands:
        best.setdefault(c[3], c[4])
    return list(best.values())


def one_per_prop(rows, pref=None):
    """rp-2 step 4: one forecast per (game, player, stat) across models (input: representative() rows)."""
    rank = _model_rank(pref)
    best = {}
    for r in rows:
        k = (r["event_id"], r["player_id"], r["stat"])
        if k not in best or rank(r) < rank(best[k]):
            best[k] = r
    return list(best.values())


def _model_rank(pref=None):
    """one_per_prop's order of models: a Scout model before a market one, then the snapshots.models_pref order, then
    the model name."""
    from .forecast_detail import kind
    if pref is None:
        try:
            pref = tuple(snapshots.models_pref())
        except Exception:                               # noqa: BLE001
            pref = ()
    order = {m: i for i, m in enumerate(pref)}

    def rank(r):
        m = r.get("model") or ""
        return (1 if kind(m) == "market" else 0, order.get(m, len(order)), m)
    return rank


def raw_counts(rows):
    """Forecast-level totals (before rp-2), kept for the owner."""
    rr = [r for r in rows if r["status"] == "settled"]
    return {"forecasts": len(rows), "settled": len(rr), "leans": sum(1 for r in rr if r["lean"]),
            "games": len({r["event_id"] for r in rr}),
            "per_game": round(len(rr) / max(1, len({r["event_id"] for r in rr})), 1)}


def summarize(rows):
    acc = Summary()
    for r in rows:
        acc.add(r)
    return acc.result()


class Summary:
    """summarize() fed one row at a time (2026-11-08 review): counts, the error values (per game and in arrival order)
    and the lean win / loss counts per game - never the rows. unique_props=True where the caller's rows are already one
    per (game, player, stat) - every streamed scope of build(): one_per_prop rows, one model's representative rows, one
    stat's rows - so the props count is the settled count without a season-long set of keys."""

    def __init__(self, unique_props=False):
        self.unique = unique_props
        self.n = self.void = self.unresolved = self.settled = 0
        self.props, self.games = set(), set()
        self.err = _MeanCI()
        self.leans = self.wins = self.losses = self.pushes = 0
        self.hit = _RatioCI()

    def add(self, r):
        self.n += 1
        st = r["status"]
        if st == "void":
            self.void += 1
        elif st == "unresolved":
            self.unresolved += 1
        if st != "settled":
            return
        self.settled += 1
        if not self.unique:
            self.props.add((r["event_id"], r["player_id"], r["stat"]))
        self.games.add(r["event_id"])
        gk = r["event_id"] or r["forecast_id"]
        self.err.add(gk, r["error"])
        if r["lean"]:
            res = r["lean_result"]
            self.leans += 1
            self.wins += res == "win"
            self.losses += res == "loss"
            self.pushes += res == "push"
            self.hit.add(gk, 1 if res == "win" else 0, 1 if res in ("win", "loss") else 0)

    def result(self):
        w, l_ = self.wins, self.losses
        return {"forecasts": self.n, "settled": self.settled, "void": self.void, "unresolved": self.unresolved,
                "props": self.settled if self.unique else len(self.props), "games": len(self.games),
                "projection": {"n": len(self.err.vals), "mae": _r(self.err.mean(abs)), "mae_ci95": self.err.ci(abs),
                               "bias": _r(self.err.mean()), "bias_ci95": self.err.ci()},
                "leans": {"n": self.leans, "wins": w, "losses": l_, "pushes": self.pushes,
                          "hit_rate": _r(w / (w + l_) if w + l_ else None), "hit_rate_ci95": self.hit.ci()}}


def calibration(rows):
    acc = Calibration()
    for r in rows:
        acc.add(r)
    return acc.result()


class Calibration:
    """calibration() fed one row at a time: per 10-point band the stated chances in arrival order and the Over counts
    per game."""

    def __init__(self):
        self.bands = {}

    def add(self, r):
        if not (r["status"] == "settled" and r["outcome"] in ("over", "under") and r["p_over"] is not None
                and (r["p_over"] + r["p_under"]) > 0):
            return
        for i in range(10):
            lo, hi = i / 10, (i + 1) / 10
            if lo <= r["p_over"] / (r["p_over"] + r["p_under"]) < hi or (i == 9 and
                                                                        r["p_over"] / (r["p_over"] + r["p_under"]) == 1):
                b = self.bands.get(i)
                if b is None:
                    b = self.bands[i] = [[], 0, _RatioCI()]
                over = 1 if r["outcome"] == "over" else 0
                b[0].append(r["p_over"] / (r["p_over"] + r["p_under"]))
                b[1] += over
                b[2].add(r["event_id"] or r["forecast_id"], over, 1)

    def result(self):
        out = []
        for i in range(10):
            b = self.bands.get(i)
            if b is None:
                continue
            qs, over, ci = b
            out.append({"band": f"{i * 10}-{(i + 1) * 10}%", "n": len(qs), "stated": _r(sum(qs) / len(qs)),
                        "observed": _r(over / len(qs)), "observed_ci95": ci.ci()})
        return out


def bands(rows):
    acc = Bands()
    for r in rows:
        acc.add(r)
    return acc.result()


class Bands:
    """bands() fed one row at a time."""

    def __init__(self):
        self.b = [[0, 0, 0, 0, [], _RatioCI()] for _ in BANDS]        # n, wins, losses, pushes, stated (W/L), hit

    def add(self, r):
        if not (r["status"] == "settled" and r["lean"] and r.get("lean_p") is not None):
            return
        res = r["lean_result"]
        for (lo, hi, _label), b in zip(BANDS, self.b):
            if lo <= r["lean_p"] < hi:
                b[0] += 1
                b[1] += res == "win"
                b[2] += res == "loss"
                b[3] += res == "push"
                if res in ("win", "loss"):
                    b[4].append(r["lean_p"])
                b[5].add(r["event_id"] or r["forecast_id"], 1 if res == "win" else 0, 1 if res in ("win", "loss") else 0)

    def result(self):
        out = []
        for (_lo, _hi, label), (n, w, l_, p, wl, hit) in zip(BANDS, self.b):
            out.append({"band": label, "label": "probability band", "n": n, "wins": w, "losses": l_, "pushes": p,
                        "stated": _r(sum(wl) / len(wl)) if wl else None,
                        "hit_rate": _r(w / (w + l_) if w + l_ else None), "hit_rate_ci95": hit.ci()})
        return out


def _contained(league, stat):
    """vg-1 containment (board_view.VG_CONTAIN_LEAN on + the market's model failed its gate): no research letter."""
    from . import board_view
    return bool(league) and board_view.contain_reason(league, stat) is not None


def grades(rows, league=None):
    """rg-1 research letters of settled Scout leans: sports-gp-1 band of the UNROUNDED unconditional lean-side chance
    (board_view.research_grade); n, W-L-P, hit rate + game-clustered 95% interval. Research - not validated. The
    board's market (N4) / sanity holds are live-board rules and are not re-applied to stored forecasts here.
    The chance is the one members saw: the shown (cal-mi-1) chance stored beside the forecast when its market was ON
    (sports/model/shown.py, A9a 6), else the record's raw sm-1 chance (= what the board showed while OFF)."""
    acc = Grades(league)
    for r in rows:
        acc.add(r)
    return acc.result()


class Grades:
    """grades() fed one row at a time: W-L-P and per-game win / loss counts per research letter."""
    LETTERS = ("A", "B", "C", "Lean")

    def __init__(self, league=None):
        self.league = league
        self.g = {g: [0, 0, 0, 0, _RatioCI()] for g in self.LETTERS}           # n, wins, losses, pushes, hit

    def add(self, r):
        from . import board_view
        if not (r["status"] == "settled" and r["lean"] and not _contained(self.league, r.get("stat"))):
            return
        g = board_view.research_grade(r["p_over"] if r["lean"] == "over" else r["p_under"])
        demote = (g in ("A", "B", "C") and self.league == "nba" and grade_rules.mvlean_on(self.league)
                  and (r.get("forecast_at") or "") >= grade_rules.mvlean_from()
                  and grade_rules.mvlean_demotes(r["lean"], r["line"], r.get("mv_open")))
        if demote:
            g = "Lean"                                  # mvlean-1: the board showed this side as Lean (grade_rules)
        b = self.g.get(g)
        if b is None:
            return
        res = r["lean_result"]
        b[0] += 1
        b[1] += res == "win"
        b[2] += res == "loss"
        b[3] += res == "push"
        b[4].add(r["event_id"] or r["forecast_id"], 1 if res == "win" else 0, 1 if res in ("win", "loss") else 0)

    def result(self):
        league = self.league
        if league and _league_contained(league):
            from . import board_view
            return {"label": "Research grade - not validated", "rule": "rg-1", "rows": [],
                    "withheld": board_view.NOT_VALIDATED_TEXT.format(LG=league.upper())}
        out = []
        for g in self.LETTERS:
            n, w, l_, p, hit = self.g[g]
            out.append({"grade": g, "n": n, "wins": w, "losses": l_, "pushes": p,
                        "hit_rate": _r(w / (w + l_) if w + l_ else None), "hit_rate_ci95": hit.ci()})
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
                    "grade": None if _contained(league, r.get("stat")) else board_view.research_grade(p_side),
                    "settled_at": r.get("settled_at"), "forecast_at": r.get("forecast_at")})
    return out


def model_gate(league):
    """Which of the league's stats passed sm-1's registered gate; the contained-league wording ("<LG> model not
    validated", the owner's text) only while the league's letters are contained (A9a 4) - never beside released letters."""
    from . import board_view, capabilities
    stats = list(capabilities.SUPPORTED.get(league, ()))
    ok = [st for st in stats if board_view.gate_reason(league, st) is None]
    contained = _league_contained(league)
    return {"rule": board_view.GATE_VERSION, "validated_stats": ok, "contained": contained,
            "not_validated": board_view.NOT_VALIDATED_TEXT.format(LG=league.upper()) if contained else None}


def build(con, now=None, base=None):
    t0 = time.time()
    now = now or now_utc()
    leagues = {}
    for lg in snapshots.LEAGUES:                        # one league at a time: its rows are freed before the next
        leagues[lg] = _league(con, lg, now)
    doc ={"version": VERSION, "built_at": now_utc(), "as_of": now, "leagues": leagues, "picks": tracked_picks(),
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


def _league(con, lg, now):
    """One league's results block (was the body of build's loop, which kept the previous league's rows alive)."""
    return league_block(con, lg, now, (r for setts in settlement_chunks(con, lg) for r in _rows_from(con, lg, setts)))


# Streamed build (2026-11-08 review: the in-memory reduction still kept one row per (game, player, stat, model, line) of
# the season, ~0.6 MB per game -> killed over the cap partway through a season). Every row goes to a throwaway SQLite
# file as it streams (sports/spill.py); the rp-2 choices, head_to_head's newest-per-key and every ordering run there
# with the same rules and tie-breaks as the list code, and the summaries are fed one decoded row at a time (Summary,
# Calibration, Bands, Grades, HeadToHead). Python keeps per-game numbers and the value lists of the means, never the
# row dicts. The file is identical to the list build's (sports/tests/test_sports_jobs_1108.py, against ecfa81a).
_SPILL_SCHEMA = (
    # rp-2 step 1: newest forecast per (game, player, stat, model, line); k_seq = the key's first arrival (dict order)
    "CREATE TABLE rep (k TEXT PRIMARY KEY, k_seq INTEGER, fa TEXT, fid TEXT, r0 INTEGER, r1 REAL, k4 TEXT, blob BLOB, "
    "ln REAL)",
    # SPORTS_LATEST_LINE: newest forecast time per (game, player, stat, model, platform) and line (= _latest_add)
    "CREATE TABLE lat (kp TEXT, k4 TEXT, ln REAL, fa TEXT, PRIMARY KEY (kp, ln))",
    # mvlean-1: the first sm-1 forecast per (game, player, stat, platform) and its line (= _open_add)
    "CREATE TABLE opn (k TEXT PRIMARY KEY, fa TEXT, fid TEXT, ln REAL)",
    # head_to_head's newest per (game, player, stat, line, model) of settled / void rows (first row kept on a tie)
    "CREATE TABLE hh (k TEXT PRIMARY KEY, k_seq INTEGER, fa TEXT, prefix TEXT, k4m TEXT, ln REAL, mkt INTEGER, "
    "blob BLOB)",
    "CREATE TABLE opp (pos INTEGER PRIMARY KEY, k3 TEXT, a INTEGER, b INTEGER, m TEXT, blob BLOB)",   # step 4 input
    "CREATE TABLE own (k4 TEXT PRIMARY KEY, line REAL)",          # head_to_head_rows: each Scout model's own line
    "CREATE TABLE lines (k TEXT PRIMARY KEY)",                    # ... and the props x lines they make
    "CREATE TABLE rec (pos INTEGER PRIMARY KEY, k TEXT, fa TEXT, sa TEXT, fid TEXT, blob BLOB)",     # recent() input
    "CREATE TABLE cal (pos INTEGER PRIMARY KEY, at TEXT, status TEXT)",                              # calendar input
    # an ordered pass = the row ids in order (INSERT ... SELECT ... ORDER BY numbers them in that order), then a rowid
    # walk joined to the blobs: the sorts carry only the keys, never the row blobs
    "CREATE TABLE ord (pos INTEGER PRIMARY KEY, id INTEGER)",
)
_OPN_UPSERT = ("INSERT INTO opn VALUES (?,?,?,?) ON CONFLICT(k) DO UPDATE SET fa=excluded.fa, fid=excluded.fid, "
               "ln=excluded.ln WHERE excluded.fa < opn.fa OR (excluded.fa = opn.fa AND excluded.fid < opn.fid)")
_LAT_UPSERT = ("INSERT INTO lat VALUES (?,?,?,?) ON CONFLICT(kp, ln) DO UPDATE SET fa=excluded.fa "
               "WHERE excluded.fa > lat.fa")
_REP_UPSERT = ("INSERT INTO rep VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(k) DO UPDATE SET fa=excluded.fa, fid=excluded.fid, "
               "r0=excluded.r0, r1=excluded.r1, blob=excluded.blob "
               "WHERE excluded.fa > rep.fa OR (excluded.fa = rep.fa AND excluded.fid > rep.fid)")   # = _rep_newest_add
_HH_UPSERT = ("INSERT INTO hh VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(k) DO UPDATE SET fa=excluded.fa, blob=excluded.blob "
              "WHERE excluded.fa > hh.fa")                                                          # = _hh_newest_add
_SPILL_BATCH = 2000


def league_block(con, lg, now, rows):
    """The league block of results_public.json from a stream of _rows_from rows (in the order the list build read
    them). Same output as the list build: representative / one_per_prop / summarize / calibration / bands / grades /
    recent / head_to_head over the season's rows."""
    from gradecore.calendar import daily_record

    from . import spill
    with spill.Spill("results-public") as sp:
        for ddl in _SPILL_SCHEMA:
            sp.execute(ddl)
        raw = _spill_rows(sp, rows)
        # rp-2 steps 2-3 (_rep_order): newest first, then a stable sort on (source rank, closeness), ties on the forecast
        # id, then the key's arrival; the first row per (game, player, stat, model) is its representative
        # SPORTS_LATEST_LINE: only a line that is some book's latest (_current_set) can be the representative
        cur = ("WHERE (k4, ln) IN (SELECT k4, ln FROM (SELECT k4, ln, fa, MAX(fa) OVER (PARTITION BY kp) AS top "
               "FROM lat) WHERE fa = top)") if grade_rules.latest_line_on(lg) else ""
        reps = _ordered(sp, "rep", "SELECT id FROM (SELECT rowid AS id, r0, r1, fa, fid, k_seq, ROW_NUMBER() OVER "
                                   "(PARTITION BY k4 ORDER BY r0, r1, fa DESC, fid, k_seq) AS rn FROM rep " + cur +
                                   ") WHERE rn = 1 ORDER BY r0, r1, fa DESC, fid, k_seq")
        rank = _model_rank()
        models, research = {}, {}
        batch_opp, batch_own, batch_lines = [], [], []
        for pos, (blob,) in enumerate(reps):
            r = spill.unpack(blob)
            mv = r["model"]
            acc = models.get(mv)
            if acc is None:
                acc = models[mv] = Summary(unique_props=True)       # reps are one per (game, player, stat, model)
            acc.add(r)
            research[mv] = research.get(mv, True) and bool(r["research_only"])
            a, b, m = rank(r)
            batch_opp.append((pos, repr((r["event_id"], r["player_id"], r["stat"])), a, b, m, blob))
            if r.get("model") in SCOUT:
                ln = round(float(r["line"]), 3)
                batch_own.append((repr((r["event_id"], r["player_id"], r["stat"], r.get("model"))), ln))
                batch_lines.append((repr((r["event_id"], r["player_id"], r["stat"], ln)),))
            if len(batch_opp) >= _SPILL_BATCH:
                _flush_reps(sp, batch_opp, batch_own, batch_lines)
        _flush_reps(sp, batch_opp, batch_own, batch_lines)
        models = {mv: dict(models[mv].result(), research_only=research[mv]) for mv in sorted(models)}
        # rp-2 step 4 (one_per_prop): the best-ranked model per (game, player, stat), the first on a tie, in the order
        # the props first appear among the representatives
        props = _ordered(sp, "opp", "SELECT id FROM (SELECT pos AS id, MIN(pos) OVER (PARTITION BY k3) AS first, "
                                    "ROW_NUMBER() OVER (PARTITION BY k3 ORDER BY a, b, m, pos) AS rn FROM opp) WHERE rn = 1 "
                                    "ORDER BY first")
        total, stats = Summary(unique_props=True), {}
        cal, bnd, grd = Calibration(), Bands(), Grades(lg)
        batch_rec, batch_cal = [], []
        for pos, (blob,) in enumerate(props):
            r = spill.unpack(blob)
            total.add(r)
            acc = stats.get(r["stat"])
            if acc is None:
                acc = stats[r["stat"]] = Summary(unique_props=True)
            acc.add(r)
            cal.add(r)
            bnd.add(r)
            if lg == "nba" and r.get("model") == "sm-1":    # mvlean-1: the open line of the row's single pick'em book
                oks = _open_keys(r)
                got = sp.execute("SELECT ln FROM opn WHERE k = ?", (repr(oks[0]),)).fetchone() if len(oks) == 1 else None
                r["mv_open"] = got[0] if got else None
            grd.add(r)
            batch_cal.append((pos, r.get("settled_at"), r.get("lean_result")))
            if r["status"] == "settled" and r["lean"]:
                batch_rec.append((pos, repr((r["event_id"], r["player_id"], r["stat"], round(float(r["line"]), 3))),
                                  r.get("forecast_at") or "", r.get("settled_at") or "", r["forecast_id"], blob))
            if len(batch_cal) >= _SPILL_BATCH:
                _flush_rows(sp, batch_rec, batch_cal)
        _flush_rows(sp, batch_rec, batch_cal)
        s = total.result()
        stats = {st: stats[st].result() for st in sorted(stats)}
        # recent(): newest Scout forecast per (game, player, stat, line) among the settled leans (first on a tie), newest
        # settled first; recent() on those RECENT_N rows gives the list it gives on every row
        rec_rows = [spill.unpack(b) for (b,) in _ordered(
            sp, "rec", "SELECT id FROM (SELECT pos AS id, sa, fid, ROW_NUMBER() OVER (PARTITION BY k ORDER BY fa DESC, pos) "
                       f"AS rn FROM rec) WHERE rn = 1 ORDER BY sa DESC, fid DESC, id LIMIT {int(RECENT_N)}")]
        rec = recent(rec_rows, league=lg)
        del rec_rows
        calendar = daily_record(({"at": at, "status": st} for at, st in sp.execute("SELECT at, status FROM cal "
                                                                                   "ORDER BY pos")),
                                now, "Settlement date · US Central", "America/Chicago")
        hh = HeadToHead()
        _feed_head_to_head(sp, hh)
        hh_rows = hh.result()
    try:
        names = snapshots.player_names(con, {r["player_id"] for r in rec if r.get("player_id")}, now)
    except Exception:                                   # noqa: BLE001 - names are a nicety; ids stay
        names = {}
    for r in rec:
        r["player"] = names.get(r["player_id"])
    return {**s, "gate": {"settled_props": s["props"], "required": GATE, "validated": False,
                          "under_review": s["props"] >= GATE, "text": gate_text(s["props"]),
                          "rule": GATE_RULE},
            "models": models, "stats": stats, "calibration": cal.result(), "bands": bnd.result(),
            "grades": grd.result(),
            "recent": rec, "model_gate": model_gate(lg),
            "calendar": calendar,
            "head_to_head": {"version": HH_VERSION, "method": HH_METHOD, "rows": hh_rows},
            "raw_counts": raw, "counting": COUNTING}


def _spill_rows(sp, rows):
    """Every streamed row into the rep / hh tables (newest per key, the list build's tie rules); -> raw_counts."""
    from . import spill
    n = settled = leans = 0
    games = set()
    rep_b, hh_b, lat_b = [], [], []
    for r in rows:
        n += 1
        if r["status"] == "settled":
            settled += 1
            leans += 1 if r["lean"] else 0
            games.add(r["event_id"])
        blob = spill.pack(r)
        k = _rep_key(r)
        rep_b.append((repr(k), n, r.get("forecast_at") or "", r["forecast_id"], _source_rank(r), _closeness(r),
                      repr(k[:4]), blob, k[4]))
        for kp in _plat_keys(r):
            lat_b.append((repr(kp), repr(k[:4]), k[4], r.get("forecast_at") or ""))
        for ok in _open_keys(r):
            lat_b.append(("opn", repr(ok), r.get("forecast_at") or "", r["forecast_id"], float(r["line"])))
        hk = _hh_key(r)
        if hk is not None:
            ev, pid, st, ln, mv = hk
            hh_b.append((repr(hk), n, r.get("forecast_at") or "", repr((ev, pid, st, ln)),
                         repr((ev, pid, st, r.get("model") or r.get("model_version") or "")), ln,
                         1 if _is_market(mv) else 0, blob))
        if len(rep_b) >= _SPILL_BATCH:
            _flush_spill(sp, rep_b, hh_b, lat_b)
    _flush_spill(sp, rep_b, hh_b, lat_b)
    return {"forecasts": n, "settled": settled, "leans": leans, "games": len(games),
            "per_game": round(settled / max(1, len(games)), 1)}


def _flush_spill(sp, rep_b, hh_b, lat_b=None):
    # executemany applies the rows in order, so a later row of a key meets the earlier one as the list build does
    if lat_b:
        sp.executemany(_LAT_UPSERT, [x for x in lat_b if x[0] != "opn"])
        sp.executemany(_OPN_UPSERT, [x[1:] for x in lat_b if x[0] == "opn"])
        lat_b.clear()
    if rep_b:
        sp.executemany(_REP_UPSERT, rep_b)
        rep_b.clear()
    if hh_b:
        sp.executemany(_HH_UPSERT, hh_b)
        hh_b.clear()


def _flush_reps(sp, opp, own, lines):
    if opp:
        sp.executemany("INSERT INTO opp VALUES (?,?,?,?,?,?)", opp)
    if own:
        sp.executemany("INSERT OR REPLACE INTO own VALUES (?,?)", own)   # a dict: the last one per key stays
    if lines:
        sp.executemany("INSERT OR IGNORE INTO lines VALUES (?)", lines)
    opp.clear()
    own.clear()
    lines.clear()


def _flush_rows(sp, rec, cal):
    if rec:
        sp.executemany("INSERT INTO rec VALUES (?,?,?,?,?,?)", rec)
    if cal:
        sp.executemany("INSERT INTO cal VALUES (?,?,?)", cal)
    rec.clear()
    cal.clear()


def _feed_head_to_head(sp, acc):
    """head_to_head(head_to_head_rows(all rows, reps)) from the spill: the newest row per head_to_head key whose line is
    a Scout model's own representative line (a market at that line, or that Scout model itself), grouped per prop key
    in the order the keys first arrived - head_to_head's own grouping order."""
    from . import spill
    q = ("SELECT id FROM (SELECT h.rowid AS id, h.k_seq, MIN(h.k_seq) OVER (PARTITION BY h.prefix) AS first FROM hh h "
         "WHERE h.prefix IN (SELECT k FROM lines) AND (h.mkt = 1 OR EXISTS (SELECT 1 FROM own o WHERE o.k4 = h.k4m "
         "AND o.line = h.ln))) ORDER BY first, k_seq")
    cur_key, ms, ev, st = None, {}, None, None
    for prefix, blob in _ordered(sp, "hh", q, "t.prefix, t.blob"):
        if prefix != cur_key:
            if ms:
                acc.add(ev, st, ms)
            cur_key, ms = prefix, {}
        r = spill.unpack(blob)
        ev, st = r["event_id"], r["stat"]
        ms[r.get("model") or r.get("model_version")] = r
    if ms:
        acc.add(ev, st, ms)


def _ordered(sp, table, ids_sql, cols="t.blob"):
    """Rows of `table` in the order of ids_sql (a SELECT of rowids): the ids go to the ord table in that order, then a
    rowid walk of ord (CROSS JOIN: ord stays the outer loop, so no second sort) fetches each row."""
    sp.execute("DELETE FROM ord")
    sp.execute(f"INSERT INTO ord (id) {ids_sql}")
    return sp.execute(f"SELECT {cols} FROM ord o CROSS JOIN {table} t ON t.rowid = o.id ORDER BY o.pos")


COUNTING = ("one pick per game x player x stat (rp-2): a pick'em app line (any type) before a sportsbook line, then "
            "the line closest to Scout's projection at forecast time - so the counted line is often a near-even line, "
            "not always the board's headline line; raw_counts = every forecast of every listed line, kept for the owner")


def head_to_head_rows(all_rows, reps):
    """A7 (follow-up 2026-11-06): each Scout model's forecasts ONLY at that model's own representative line, plus the
    market forecasts at those lines. Before, the union of every Scout model's lines paired each model once per line in
    the union (sm-1 @15.5 and mi-1 @16.5 -> 2 pairs each); now head_to_head yields one pair per model per prop."""
    own = {(r["event_id"], r["player_id"], r["stat"], r.get("model")): round(float(r["line"]), 3)
           for r in reps if r.get("model") in SCOUT}
    lines = {(k[0], k[1], k[2], ln) for k, ln in own.items()}
    out = []
    for r in all_rows:
        mv = r.get("model") or r.get("model_version") or ""
        ln = round(float(r["line"]), 3)
        if (r["event_id"], r["player_id"], r["stat"], ln) not in lines:
            continue
        if _is_market(mv) or own.get((r["event_id"], r["player_id"], r["stat"], mv)) == ln:
            out.append(r)
    return out


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


def _hh_key(r):
    """head_to_head's key (game, player, stat, line, model) - None for a row it leaves out (not settled / void, or no
    model)."""
    model = r.get("model") or r.get("model_version")
    if r.get("status") not in ("settled", "void") or not model:
        return None
    return (r["event_id"], r["player_id"], r["stat"], round(float(r["line"]), 3), model)


def _hh_newest_add(newest, r):
    """head_to_head's newest forecast per (game, player, stat, line, model): settled / void rows of a model only; on a
    forecast_at tie the first row stays."""
    k = _hh_key(r)
    if k is None:
        return
    if k not in newest or (r.get("forecast_at") or "") > (newest[k].get("forecast_at") or ""):
        newest[k] = r


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
        _hh_newest_add(newest, r)
    by_key = defaultdict(dict)
    for (ev, pid, st, ln, mv), r in newest.items():
        by_key[(ev, pid, st, ln)][mv] = r
    acc = HeadToHead()
    for (ev, pid, st, ln), ms in by_key.items():
        acc.add(ev, st, ms)
    return acc.result()


class HeadToHead:
    """head_to_head's pairs and scores fed one prop key at a time (2026-11-08 review: neither the rows nor the pair
    dicts of the season are held). add(game, stat, {model: newest row}) in head_to_head's key order; per (Scout,
    market, stat) the scores in pair order, the per-pair differences and their per-game lists for the interval."""

    def __init__(self):
        self.pairs = {}
        self.pushes = defaultdict(int)

    def add(self, ev, st, ms):
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
                        self.pushes[(s, mk, g)] += 1
                    continue
                sa = binary_scores(a["p_over"], a["p_under"], a["outcome"])
                sb = binary_scores(b["p_over"], b["p_under"], b["outcome"])
                if sa is None or sb is None:
                    continue
                sh = None if sa[3] is None else (1.0 if sa[3] == a["outcome"] else 0.0)
                mh = None if sb[3] is None else (1.0 if sb[3] == a["outcome"] else 0.0)
                vals = (sa[1], sb[1], sa[2], sb[2], sh, mh)
                diffs = (sa[1] - sb[1], sa[2] - sb[2], None if sh is None or mh is None else sh - mh)
                gk = ev or a.get("forecast_id")
                for g in (st, "all"):
                    acc = self.pairs.get((s, mk, g))
                    if acc is None:
                        acc = self.pairs[(s, mk, g)] = [0, set(), [[] for _ in vals], [_MeanCI() for _ in diffs]]
                    acc[0] += 1
                    acc[1].add(ev)
                    for lst, v in zip(acc[2], vals):
                        if v is not None:
                            lst.append(v)
                    for m, d in zip(acc[3], diffs):
                        m.add(gk, d)

    def result(self):
        def mean(v):
            return sum(v) / len(v) if v else None
        out = []
        for (s, mk, g), (n, games, (sb, mb, sl, ml, sh, mh), (db, dl, dh)) in sorted(
                self.pairs.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] != "all", kv[0][2])):
            out.append({"scout": s, "market": mk, "stat": g, "pairs": n, "pushes": self.pushes.get((s, mk, g), 0),
                        "games": len(games),
                        "scout_brier": _r(mean(sb)), "market_brier": _r(mean(mb)),
                        "scout_log_loss": _r(mean(sl)), "market_log_loss": _r(mean(ml)),
                        "scout_hit_rate": _r(mean(sh)), "market_hit_rate": _r(mean(mh)),
                        "diff": {"brier": _r(db.mean()), "brier_ci95": db.ci(),
                                 "log_loss": _r(dl.mean()), "log_loss_ci95": dl.ci(),
                                 "hit_rate": _r(dh.mean()), "hit_rate_ci95": dh.ci()}})
        return out


HH_METHOD = ("Scout (sm-1 / mi-1) and each market estimate on the SAME props: same game, player, stat and line, so the "
             "same settlement; newest forecast of each; pushes and voids counted, not scored. Binary scores on the "
             "chance of Over given no push: Brier (0.25 = always 50%), log loss, hit rate of the favoured side. "
             "Difference = Scout minus market (Brier / log loss below 0 = Scout better), 95% bootstrap interval over "
             "games (1,000 draws, seed 7).")
