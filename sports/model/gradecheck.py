"""gc-1: the multilevel grade check (REGISTER.md amendment A9) - READ-ONLY, prints one JSON document.

    python -m sports.model.tune gradecheck [--league nba|nfl] [--days N]
                                           [--learning L.db --inputs F.db --store canonical.db --history H.db --games G.db]

Data: learning.db scorecard rows of sm-1 (superseded = 0, PrizePicks / Underdog), one row per (event, platform, offer,
stat, line) = the last pre-start forecast (A8 3); the saved inputs (forecasts.db: feature row, PMF, artifact sha); the
canonical store (offer variant / promotional, event home / away / start) and the game store (moneyline / Kalshi
home-win) when reachable; the history store for the sg-1 L10 rule when reachable. Every file is opened read-only
(sqlite `mode=ro`); nothing is written anywhere (A9 6).

Side = the board lean (board_view.lean_of with the PMF mean), letter = sports-gp-1 band of the unrounded lean-side
chance (board_view.research_grade), hit = the lean's result at the forecast's own line. "as shown" removes what the
live board would have held: N4 (market prices the lean side at least as high), sg-1 ratio holds, sg-1 l10_contra when
the history store is reachable, and (cap-2, owner 2026-11-06) a stored one-sided Underdog line when the canonical store
is reachable. The chance is the raw sm-1 chance, or the A8 4 walk-forward cal-mi chance for a market
whose cal-mi flag is ON in the weights in use (A9 2). Verdict per league = A9 5, read by the owner; nothing here
changes a letter, a flag or a stake.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .. import board_view
from . import cal, evaluate, grade_policy

VERSION = "gc-1"
REGISTER = "sports/research/REGISTER.md A9"
MIN_SLICE = 30                      # A9 4: below this a slice prints counts only
AB_MIN, AB_MIN_DAYS, AB_MIN_GAMES = 150, 3, 10
REL_TOL, CONTAIN_TOL, SHARE_TOL = 0.05, 0.10, 0.15
STAT_FAIL_N, STAT_CONTAIN_N = 30, 60
TARGETS = ("prizepicks", "underdog")
LEAGUES = ("nba", "nfl")
DIST_BINS = ((0.0, 0.5, "<0.5"), (0.5, 1.5, "0.5-1.5"), (1.5, 3.0, "1.5-3"), (3.0, 1e9, ">=3"))
AGE_BINS = ((0, 1, "<1h"), (1, 3, "1-3h"), (3, 12, "3-12h"), (12, 1e9, ">=12h"))
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
B2B_REST = 1.1
HOURS_SHIFT = 10


# ================================================================================================ loading (read-only)
def _ro(path):
    p = Path(path)
    if not p.exists():
        return None
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _ts(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def game_day(start_utc, forecast_at):
    """A9 5: UTC date of (event start - 10 h), else of (forecast_at - 10 h)."""
    t = start_utc or forecast_at
    return (_ts(t) - timedelta(hours=HOURS_SHIFT)).date().isoformat()


def _outcome(value, line):
    if value is None:
        return None
    v, ln = float(value), float(line)
    return "over" if v > ln else ("under" if v < ln else "push")


def load_rows(lcon, icon=None, scon=None, league=None, days=None, now=None):
    """-> (rows, meta). One row per (event, platform, offer, stat, line): the last pre-start sm-1 forecast with its
    saved inputs, market chance at the same offer and forecast_at (A8 1), store and game context when reachable."""
    q = ("SELECT forecast_id, model_version, league, stat, event_id, platform, line, side, p_side, p_over, p_under, "
         "p_push, status, outcome, value, forecast_at, settled_at FROM scorecard WHERE superseded = 0")
    cutoff = None
    if days:
        cutoff = (_ts(now) if now else datetime.now(timezone.utc)) - timedelta(days=float(days))
        cutoff = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    sm, probs, n_read = [], {}, 0
    for r in lcon.execute(q):
        n_read += 1
        if r["model_version"] != "sm-1":
            if r["p_over"] is not None and r["p_under"] is not None:
                probs[r["forecast_id"]] = (r["model_version"], r["p_over"], r["p_under"])
            continue
        if league and r["league"] != league:
            continue
        if r["platform"] not in TARGETS or r["p_over"] is None or r["p_under"] is None:
            continue
        if cutoff and (r["forecast_at"] or "") < cutoff:
            continue
        sm.append({"fid": r["forecast_id"], "league": r["league"], "stat": r["stat"], "event": r["event_id"],
                   "platform": r["platform"], "line": float(r["line"]), "po": float(r["p_over"]),
                   "pu": float(r["p_under"]), "pp": float(r["p_push"] or 0.0), "status": r["status"],
                   "value": r["value"], "chosen": r["side"], "forecast_at": r["forecast_at"],
                   "settled_at": r["settled_at"]})
    meta = {"scorecard_rows_read": n_read, "sm1_rows": len(sm), "inputs": icon is not None, "store": scon is not None}
    last = {}
    for r in sm:
        rid = None
        if icon is not None:
            x = icon.execute("SELECT offer_record_id, artifact_sha, row_json, pmf_lo, pmf_json FROM inputs WHERE "
                             "forecast_id = ?", (r["fid"],)).fetchone()
            if x:
                rid = x["offer_record_id"]
                r["sha"] = x["artifact_sha"]
                row = {}
                try:
                    row = json.loads(x["row_json"] or "{}") or {}
                except ValueError:
                    row = {}
                r["home"] = row.get("home")
                r["rest_days"] = row.get("rest_days")
                r["start_utc"] = row.get("start_utc")
                r["prior_n"] = row.get("prior_n")
                r["player_id"] = row.get("player_id")
                if x["pmf_json"] and x["pmf_lo"] is not None:
                    try:
                        pmf = np.asarray(json.loads(x["pmf_json"]), float)
                        ks = np.arange(int(x["pmf_lo"]), int(x["pmf_lo"]) + len(pmf), dtype=float)
                        r["mean"] = float(pmf @ ks)
                        c = np.cumsum(pmf)
                        r["iv"] = [float(ks[min(int((c < 0.1).sum()), len(ks) - 1)]),
                                   float(ks[min(int((c < 0.9).sum()), len(ks) - 1)])]
                    except (ValueError, TypeError):
                        pass
                ls, mc = [], None
                for m in icon.execute("SELECT forecast_id, model_version FROM inputs WHERE offer_record_id = ? AND "
                                      "forecast_at = ?", (rid, r["forecast_at"])):
                    pr = probs.get(m["forecast_id"])
                    if pr is None:
                        continue
                    c = cal.cond_over(pr[1], pr[2])
                    if c is None:
                        continue
                    if m["model_version"].startswith("mkt-"):
                        ls.append(cal._logit(c))
                    elif m["model_version"] == "mc-1":
                        mc = c
                if ls:
                    pm = cal._sig(sum(ls) / len(ls))
                    r["pm"], r["n_src"] = pm, len(ls)
                elif mc is not None:
                    r["pm"], r["n_src"] = mc, 1
        r["rid"] = rid
        r["ps"] = cal.cond_over(r["po"], r["pu"])
        key = (r["event"], r["platform"], cal._offer_id(rid, r["platform"]) if rid else r["fid"], r["stat"], r["line"])
        if key not in last or r["forecast_at"] > last[key]["forecast_at"]:
            last[key] = r
    rows = sorted(last.values(), key=lambda r: r["forecast_at"])
    if scon is not None:
        _attach_store(scon, rows, meta)
    return rows, meta


def _attach_store(scon, rows, meta):
    """Offer variant / promotional and event home / away / start from the canonical store (read-only)."""
    from .. import store
    ev_cache, n_off, n_ev = {}, 0, 0
    for r in rows:
        if r.get("rid"):
            try:
                o = store.get(scon, r["rid"])
            except Exception:                           # noqa: BLE001 - an archived / unreadable revision
                o = None
            if o is not None and getattr(o, "KIND", None) == "offer":
                r["variant"] = o.variant
                r["promo"] = bool(o.promotional)
                n_off += 1
        ev = r.get("event")
        if ev and ev not in ev_cache:
            try:
                rs = store._latest_rows(scon, "event", "event_id = ?", [ev], None, 1)
                ev_cache[ev] = store._rec(rs[0]) if rs else None
            except Exception:                           # noqa: BLE001
                ev_cache[ev] = None
        e = ev_cache.get(ev)
        if e is not None:
            n_ev += 1
            r["start_utc"] = r.get("start_utc") or e.start_utc
            r["home_tid"], r["away_tid"] = e.home_team_id, e.away_team_id
    meta["store_offers"] = n_off
    meta["store_events"] = len([v for v in ev_cache.values() if v is not None])


def attach_favourite(rows, games_path, league):
    """favourite / underdog of the player's team from the game store: ESPN pickcenter moneyline (lower = favourite),
    else the Kalshi implied home-win chance; None when unknown. -> events with a line."""
    try:
        from .. import game_lines
        from ..games import store as game_store
        db = game_store.connect(path=Path(games_path))
    except Exception:                                   # noqa: BLE001 - no game store
        return 0
    seen, n = {}, 0
    try:
        for r in rows:
            ev, home = r.get("event"), r.get("home")
            if not ev or home is None:
                continue
            if ev not in seen:
                try:
                    ids = lambda t: (t.split(":")[-1], t) if t else (None,)      # noqa: E731
                    v = game_lines._event_lines(db, league, ev.split(":")[-1], ids(r.get("home_tid")),
                                                ids(r.get("away_tid")))
                except Exception:                       # noqa: BLE001
                    v = None
                hf = None
                if v:
                    hm, am = v.get("home_ml"), v.get("away_ml")
                    if hm is not None and am is not None and hm != am:
                        hf = hm < am
                    elif v.get("kalshi") and v["kalshi"].get("home_win") is not None and v["kalshi"]["home_win"] != 0.5:
                        hf = v["kalshi"]["home_win"] > 0.5
                seen[ev] = hf
                if hf is not None:
                    n += 1
            hf = seen[ev]
            if hf is not None:
                r["fav"] = bool(hf) if float(home) == 1 else (not hf)
    finally:
        try:
            db.close()
        except Exception:                               # noqa: BLE001
            pass
    return n


def attach_l10(rows, hcon, league):
    """sg-1 L10 strict counts at the line from the history store (played games before the start). -> rows with one."""
    from . import featurize
    cols = featurize.NBA_MARKETS if league == "nba" else featurize.NFL_MARKETS
    tbl = "nba_pg" if league == "nba" else "nfl_pg"
    played = "AND p.played = 1 " if league == "nba" else ""
    n = 0
    cache = {}
    for r in rows:
        pid, st, start = r.get("player_id"), r.get("stat"), r.get("start_utc") or r.get("forecast_at")
        col = cols.get(st)
        if not pid or not col or not start:
            continue
        k = (pid, st, start)
        if k not in cache:
            try:
                vals = [float(x[0]) for x in hcon.execute(
                    f"SELECT p.{col} FROM {tbl} p JOIN games g ON g.league = ? AND g.game_id = p.game_id "
                    f"WHERE p.player_id = ? {played}AND p.{col} IS NOT NULL AND g.start_utc < ? "
                    "ORDER BY g.start_utc DESC LIMIT 10", (league, pid, start))]
            except sqlite3.Error:
                vals = []
            cache[k] = vals
        vals = cache[k]
        if vals:
            r["l10"] = board_view.hit_counts(vals, r["line"], 10)
            n += 1
    return n


# ================================================================================================ scoring (A9 2-3)
def score_rows(rows, cal_q=None, label="cal-mi", labels=None):
    """Lean, letter, hit and the as-shown holds for every row. cal_q: {fid: walk-forward q} for cal-mi ON markets, or
    the pc-1 walk-forward chance (label "pc-1", REGISTER A10) when pc-1 prints the chance. labels: {fid: label} for
    rows whose chance comes from another rule (ac-2 for NBA assists)."""
    for r in rows:
        po, pu, pp = r["po"], r["pu"], r["pp"]
        r["chance"] = "raw"
        if cal_q and r["fid"] in cal_q and cal_q[r["fid"]] is not None:
            q = cal_q[r["fid"]]
            po, pu = (1 - pp) * q, (1 - pp) * (1 - q)
            r["chance"] = (labels or {}).get(r["fid"], label)
        r["po_s"], r["pu_s"] = po, pu
        mean = r.get("mean")
        lean = board_view.lean_of(po, pu, mean, r["line"])
        r["lean"] = lean
        r["outcome"] = _outcome(r["value"], r["line"]) if r["status"] in ("win", "loss", "push") else None
        if r["status"] == "void":
            r["outcome"] = "void"
        if not lean:
            continue
        p_un = po if lean == "over" else pu
        r["p_lean"] = p_un
        dec = 1 - pp
        r["p"] = min(max(p_un / dec if dec > 0 else p_un, 1e-6), 1 - 1e-6)
        r["letter"] = board_view.research_grade(p_un, r.get("league"), r.get("stat"))
        r["hit"] = None if r["outcome"] in (None, "push", "void") else (1.0 if r["outcome"] == lean else 0.0)
        mkc = None
        if r.get("pm") is not None:
            mkc = {"po": r["pm"], "pu": 1 - r["pm"], "n": r.get("n_src") or 1}
            r["mk_side"] = mkc["po"] if lean == "over" else mkc["pu"]
            r["n4"] = (p_un - r["mk_side"]) <= 0
        r["sg_ratio"] = board_view.sanity_check(mean, r["line"], None, None,           # sg-2 floor / certain too
                                                chances=(po, pu, r["po"], r["pu"]))
        r["sg_l10"] = (board_view.sanity_check(None, r["line"], lean, r["l10"]) if r.get("l10") else None)
        # cap-2 (owner 2026-11-06): a stored one-sided Underdog line is not graded on the live board - never "as shown"
        r["one_sided"] = r.get("variant") == "one_sided"
        held = bool(r.get("n4")) or bool(r["sg_ratio"]) or bool(r["sg_l10"]) or r["one_sided"]
        r["shown"] = not held
        try:
            r["cf2"] = board_view.signal(po, pu, mean, r["line"], mkc, r.get("l10"), r.get("iv")).get("c")
        except Exception:                               # noqa: BLE001
            r["cf2"] = None
    return rows


def cal_walk_q(rows, league):
    """{fid: walk-forward cal-mi q} for the markets whose cal-mi flag is ON in the weights in use (A9 2)."""
    obj, src = cal.load(force=True)
    on = sorted(k for k, p in ((obj or {}).get("params") or {}).items() if p.get("on") and k.startswith(league + ":"))
    info = {"enabled": cal.enabled(), "source": src, "sha": (obj or {}).get("sha"), "on": on}
    if not on:
        return {}, info
    crow = [{"fid": r["fid"], "league": r["league"], "stat": r["stat"], "event": r["event"], "ps": r["ps"],
             "pm": r.get("pm"), "hit": 1.0 if float(r["value"]) > r["line"] else 0.0, "forecast_at": r["forecast_at"],
             "settled_at": r["settled_at"]}
            for r in rows if r["status"] in ("win", "loss") and r["ps"] is not None and r["value"] is not None
            and float(r["value"]) != r["line"]]
    out = {}
    for r, q, mode, _ in cal.walk(crow):
        if f"{r['league']}:{r['stat']}" in on and q is not None:
            out[r["fid"]] = q
    info["rows_with_walk_forward_chance"] = len(out)
    return out, info


def ac2_walk_q(rows):
    """{fid: walk-forward ac-2 q} for NBA assists (sports/model/ac2.py, NBA_ASSISTS_CAL2) - empty when the flag is off."""
    from . import ac2
    info = {"version": ac2.VERSION, "enabled": ac2.enabled()}
    if not ac2.enabled():
        return {}, info
    out = ac2.walk_q(rows, cal.game_day)
    info["rows_with_walk_forward_chance"] = len(out)
    return out, info


PC_WALK_MIN = 300


def pc_walk_q(rows, league):
    """REGISTER A10: {fid: walk-forward pc-1 q} - the printed-chance calibration (sports/model/chance_cal.py, the spec
    of the parameters in use) refitted for each game-day ONLY on props settled before that day's first forecast
    (>= PC_WALK_MIN decided rows, else that day keeps the raw chance). Empty when pc-1 is off / not fitted for the
    league; then the cal-mi rule applies as before."""
    from . import chance_cal
    obj, src = chance_cal.load(force=True)
    p = ((obj or {}).get("leagues") or {}).get(league)
    info = {"version": chance_cal.VERSION, "enabled": chance_cal.enabled(), "source": src,
            "sha": (obj or {}).get("sha"), "letters": (p or {}).get("letters")}
    if not p:
        return {}, info
    dec = [{"fid": r["fid"], "stat": r["stat"], "ps": r["ps"], "settled_at": r["settled_at"],
            "hit": 1.0 if float(r["value"]) > r["line"] else 0.0}
           for r in rows if r["status"] in ("win", "loss") and r["ps"] is not None and r["value"] is not None
           and float(r["value"]) != r["line"]]
    days = {}
    for r in rows:
        if r.get("ps") is not None:
            days.setdefault(cal.game_day(r["forecast_at"]), []).append(r)
    out, fitted_days = {}, 0
    for d in sorted(days):
        cutoff = min(r["forecast_at"] for r in days[d])
        train = [x for x in dec if (x["settled_at"] or "9") < cutoff]
        if len(train) < PC_WALK_MIN:
            continue
        m = chance_cal.fit(train, p["spec"], p.get("stat_lam"))
        fitted_days += 1
        for r, q in zip(days[d], chance_cal.predict(m, days[d])):
            out[r["fid"]] = q
    info.update(rows_with_walk_forward_chance=len(out), game_days_fitted=fitted_days, min_train=PC_WALK_MIN)
    return out, info


# ================================================================================================ aggregation
def _r(x, n=4):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), n)


def stat_of(rs, full=None):
    """One slice -> {n, w, l, p, games, hit, ci, stated, diff, share_over, over_rate, bal} (A9 4). rs: lean rows."""
    wl = [r for r in rs if r.get("hit") is not None]
    out = {"n": len(wl), "w": int(sum(r["hit"] for r in wl)), "l": int(sum(1 - r["hit"] for r in wl)),
           "p": sum(1 for r in rs if r.get("outcome") == "push")}
    if not wl:
        return out
    games = np.array([r["event"] for r in wl], dtype=object)
    hit = np.array([r["hit"] for r in wl], float)
    p = np.array([r["p"] for r in wl], float)
    out["games"] = int(len(set(games)))
    m, lo, hi = evaluate.boot_mean(hit, games)
    if out["games"] < 2:
        lo = hi = None
    out["hit"] = _r(m)
    out["ci"] = [_r(lo), _r(hi)]
    out["stated"] = _r(p.mean())
    if full is None:
        full = len(wl) >= MIN_SLICE
    if full:
        out["diff"] = _r(float(hit.mean() - p.mean()))
        so = float(np.mean([r["lean"] == "over" for r in wl]))
        orate = float(np.mean([r["outcome"] == "over" for r in wl]))
        out["share_over"], out["over_rate"], out["bal"] = _r(so), _r(orate), _r(abs(so - orate), 3)
    return out


def by(rs, key, order=None, full=None):
    groups = defaultdict(list)
    for r in rs:
        groups[key(r)].append(r)
    keys = [k for k in order if k in groups] if order else sorted(groups, key=lambda k: (k is None, str(k)))
    return {("unknown" if k is None else str(k)): stat_of(groups[k], full) for k in keys}


def _age_bin(r):
    if not r.get("start_utc") or not r.get("forecast_at"):
        return None
    h = (_ts(r["start_utc"]) - _ts(r["forecast_at"])).total_seconds() / 3600
    return next((lab for lo, hi, lab in AGE_BINS if lo <= h < hi), None)


def _dist_bin(r):
    if r.get("mean") is None:
        return None
    d = abs(r["line"] - r["mean"])
    return next((lab for lo, hi, lab in DIST_BINS if lo <= d < hi), None)


def _weekday(r):
    t = r.get("start_utc")
    return WEEKDAYS[(_ts(t) - timedelta(hours=HOURS_SHIFT)).weekday()] if t else None


def _variant(r):
    """standard = PrizePicks standard + Underdog balanced (its main line, sports/adapters); goblin / demon as named;
    any other variant (alternate, ladder_rung, ...) under its own name."""
    v = r.get("variant")
    if v is None:
        return None
    return "standard" if v in ("standard", "balanced", "main") else v


def letter_table(rs):
    out = by(rs, lambda r: r["letter"], order=("A", "B", "C", "Lean"))
    hits = [out[k]["hit"] for k in ("A", "B", "C", "Lean") if k in out and out[k]["n"] >= MIN_SLICE]
    out["order_holds"] = (all(a >= b for a, b in zip(hits, hits[1:])) if len(hits) >= 2 else None)
    out["AB"] = stat_of([r for r in rs if r.get("letter") in ("A", "B")], full=True)
    return out


def verdict(ab, all_stat, per_stat_ab, game_days):
    """A9 5 per league. ab = stat_of(as-shown A+B), all_stat = stat_of(as-shown all), per_stat_ab = {stat: stat_of}."""
    n, games = ab.get("n", 0), ab.get("games", 0)
    v = {"rule": "A9 5", "set": "as shown, letters A + B pooled", "n": n, "games": games, "game_days": game_days,
         "hit": ab.get("hit"), "stated": ab.get("stated"), "ci": ab.get("ci"), "bal_AB": ab.get("bal"),
         "bal_all": all_stat.get("bal")}
    fail30 = sorted(k for k, s in per_stat_ab.items()
                    if s.get("n", 0) >= STAT_FAIL_N and s.get("ci") and s["ci"][1] is not None and s["ci"][1] < 0.5)
    fail60 = sorted(k for k, s in per_stat_ab.items()
                    if s.get("n", 0) >= STAT_CONTAIN_N and s.get("ci") and s["ci"][1] is not None and s["ci"][1] < 0.5)
    v["stat_ci_upper_below_50_n30"], v["stat_ci_upper_below_50_n60"] = fail30, fail60
    enough = n >= AB_MIN and game_days >= AB_MIN_DAYS
    lo, hi = (ab.get("ci") or [None, None])
    if fail60:
        v["verdict"] = "contain:" + ",".join(fail60)
        v["why"] = f"stat(s) with >= {STAT_CONTAIN_N} A+B props and a hit interval entirely below 50%"
        return v
    if not enough:
        v["verdict"] = "insufficient"
        v["why"] = f"needs >= {AB_MIN} A+B props from >= {AB_MIN_DAYS} game-days (have {n} from {game_days})"
        return v
    hit, stated = ab["hit"], ab["stated"]
    if (hi is not None and hi < 0.5) or hit < stated - CONTAIN_TOL:
        v["verdict"] = "contain"
        v["why"] = ("hit interval entirely below 50%" if (hi is not None and hi < 0.5) else
                    f"hit {hit:.3f} more than {CONTAIN_TOL:.2f} below stated {stated:.3f}")
        return v
    conds = {"n_ge_150_days_ge_3_games_ge_10": enough and games >= AB_MIN_GAMES,
             "hit_ge_stated_minus_5pts": hit >= stated - REL_TOL,
             "ci_lower_ge_50": lo is not None and lo >= 0.5,
             "balance_AB_and_all_le_15pts": (ab.get("bal") is not None and ab["bal"] <= SHARE_TOL and
                                            all_stat.get("bal") is not None and all_stat["bal"] <= SHARE_TOL),
             "no_stat_n30_with_ci_upper_below_50": not fail30}
    v["release_conditions"] = conds
    v["verdict"] = "release" if all(conds.values()) else "keep"
    v["why"] = ("all release conditions hold" if v["verdict"] == "release" else
                "failed: " + ", ".join(k for k, ok in conds.items() if not ok))
    return v


def league_report(rows, league, cal_info):
    leans = [r for r in rows if r.get("lean")]
    shown = [r for r in leans if r.get("shown")]
    scored = [r for r in shown if r.get("hit") is not None]
    counts = {"props": len(rows), "win_loss": sum(1 for r in rows if r["status"] in ("win", "loss")),
              "push": sum(1 for r in rows if r["status"] == "push"), "void": sum(1 for r in rows if r["status"] == "void"),
              "unresolved": sum(1 for r in rows if r["status"] not in ("win", "loss", "push", "void")),
              "no_lean": sum(1 for r in rows if not r.get("lean")),
              "lean_disagrees_with_max_p_side": sum(1 for r in leans if r.get("chosen") and r["chosen"] != r["lean"]),
              "n4_blocked": sum(1 for r in leans if r.get("n4")),
              "sg1_ratio": sum(1 for r in leans if r.get("sg_ratio")),
              "sg1_l10_contra": sum(1 for r in leans if r.get("sg_l10")),
              "l10_unknown": sum(1 for r in leans if not r.get("l10")),
              "as_shown": len(shown), "as_shown_scored": len(scored),
              "chance_cal_mi": sum(1 for r in rows if r.get("chance") == "cal-mi"),
              "chance_pc1": sum(1 for r in rows if r.get("chance") == "pc-1"),
              "chance_ac2": sum(1 for r in rows if r.get("chance") == "ac-2"),
              "with_market_chance": sum(1 for r in rows if r.get("pm") is not None),
              "with_inputs": sum(1 for r in rows if r.get("sha")), "with_variant": sum(1 for r in rows if r.get("variant")),
              "with_favourite": sum(1 for r in rows if r.get("fav") is not None)}
    days = sorted({game_day(r.get("start_utc"), r["forecast_at"]) for r in scored})
    rep = {"counts": counts, "games": len({r["event"] for r in scored}), "game_days": len(days),
           "first_day": days[0] if days else None, "last_day": days[-1] if days else None}
    # max-p side (the scorecard's chosen side) for comparison only
    maxp = []
    for r in rows:
        if r["status"] in ("win", "loss") and r.get("chosen") in ("over", "under"):
            o = _outcome(r["value"], r["line"])
            if o in ("over", "under"):
                pch = r["po"] if r["chosen"] == "over" else r["pu"]
                maxp.append({"event": r["event"], "hit": 1.0 if o == r["chosen"] else 0.0, "lean": r["chosen"],
                             "outcome": o, "p": pch / max(1 - r["pp"], 1e-9)})
    rep["overall"] = {"all_leans": stat_of(leans, full=True), "as_shown": stat_of(shown, full=True),
                      "scorecard_max_p_side": stat_of(maxp, full=True)}
    s_all = rep["overall"]["as_shown"]
    rep["calibration"] = {"hit_minus_stated": s_all.get("diff"), "in_the_large_stated_minus_hit": _r(-(s_all.get("diff") or 0))
                          if s_all.get("diff") is not None else None,
                          "note": "calibration = does the stated chance match the hit rate (per letter below)"}
    rep["selection"] = {"hit": s_all.get("hit"), "ci": s_all.get("ci"), "over_rate": s_all.get("over_rate"),
                        "share_over": s_all.get("share_over"), "bal": s_all.get("bal"),
                        "note": "selection = hit rate vs 50% and vs the over rate, judged apart from calibration"}
    rep["stat"] = by(shown, lambda r: r["stat"])
    rep["letter"] = letter_table(shown)
    rep["letter_all_leans"] = letter_table(leans)
    rep["letter_by_stat"] = {st: by([r for r in shown if r["stat"] == st], lambda r: r["letter"],
                                    order=("A", "B", "C", "Lean"))
                             for st in sorted({r["stat"] for r in shown})}
    rep["side"] = by(shown, lambda r: r["lean"], order=("over", "under"))
    rep["platform"] = by(shown, lambda r: r["platform"], order=TARGETS)
    rep["variant"] = by(shown, _variant, order=("standard", "goblin", "demon") +
                        tuple(sorted({_variant(r) for r in shown} - {"standard", "goblin", "demon", None})) + (None,))
    rep["home_away"] = by(shown, lambda r: ("home" if float(r["home"]) == 1 else "away") if r.get("home") is not None
                          else None, order=("home", "away", None))
    rep["favourite"] = by(shown, lambda r: ("favourite" if r["fav"] else "underdog") if r.get("fav") is not None
                          else None, order=("favourite", "underdog", None))
    rep["line_minus_mean"] = by(shown, _dist_bin, order=[b[2] for b in DIST_BINS] + [None])
    rep["market_chance"] = by(shown, lambda r: "yes" if r.get("pm") is not None else "no", order=("yes", "no"))
    rep["weekday"] = by(shown, _weekday, order=WEEKDAYS + (None,))
    if league == "nba":
        rep["back_to_back"] = by(shown, lambda r: ("b2b" if float(r["rest_days"]) <= B2B_REST else "rested")
                                 if r.get("rest_days") is not None else None, order=("b2b", "rested", None))
    rep["forecast_age"] = by(shown, _age_bin, order=[b[2] for b in AGE_BINS] + [None])
    rep["model"] = by(shown, lambda r: f"sm-1:{r['sha']}" if r.get("sha") else "sm-1:?")
    rep["guards"] = {"vg1_gate": {st: board_view.gate_state(st) for st in sorted({r["stat"] for r in rows})},
                     "vg1_contain_lean": board_view.VG_CONTAIN_LEAN,
                     "containment": _containment(league),
                     "n4_blocked": stat_of([r for r in leans if r.get("n4")]),
                     "sg1_ratio_held": stat_of([r for r in leans if r.get("sg_ratio")]),
                     "sg1_l10_held": stat_of([r for r in leans if r.get("sg_l10")]),
                     "note": "hit rate of what each guard removed (how those leans would have done)"}
    rep["confidence_cf2"] = by(shown, lambda r: r.get("cf2"), order=(1, 2, 3, 4, 5, None))
    rep["cal_mi"] = cal_info
    per_stat_ab = {st: stat_of([r for r in shown if r["stat"] == st and r.get("letter") in ("A", "B")], full=True)
                   for st in sorted({r["stat"] for r in shown})}
    rep["verdict"] = verdict(rep["letter"]["AB"], s_all, per_stat_ab, len(days))
    return rep


def _containment(league):
    """The live letter containment of this league (sports/containment.py, A9a 4) - reported, never applied here."""
    try:
        from .. import containment
        return containment.league_state(league)
    except Exception as e:                              # noqa: BLE001
        return {"error": type(e).__name__}


def cal_mi_compact(lcon, icon, league):
    """cal-mi-1 walk-forward with A8a (cal.run_eval, unchanged) - compact per market."""
    try:
        rows = [r for r in cal.rows_from_learning(lcon, icon) if r["league"] == league]
        rep = cal.run_eval(rows)
    except Exception as e:                              # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    out = {}
    for k, v in sorted(rep.items()):
        c = {"props": v["props"], "scored": v["scored"], "games": v["games"], "days": v["days"],
             "with_market": v["with_market"], "verdict": v.get("verdict"), "on": v.get("on"), "why": v.get("why")}
        if v.get("live"):
            d = v["logloss_cal_minus_live"]
            c["logloss_cal_minus_live"] = [_r(d["mean"]), _r(d["ci_game"][0]), _r(d["ci_game"][1])]
            for nm in ("live", "cal_mi"):
                s = v[nm]
                c[nm] = {"logloss": _r(s["logloss"]), "itl": _r(s["in_the_large"]), "share_over": _r(s["share_over_favoured"]),
                         "over_rate": _r(s["over_rate"]),
                         "bands": {b: [x["n"], x["stated"], x["hit"]] for b, x in s["bands"].items()}}
        out[k] = c
    return out


# ================================================================================================ entry point
def run(learning, inputs=None, store_path=None, history=None, games=None, league=None, days=None, now=None):
    lcon = _ro(learning)
    if lcon is None:
        return {"version": VERSION, "register": REGISTER, "error": f"learning.db not found: {learning}"}
    icon = _ro(inputs) if inputs else None
    scon = _ro(store_path) if store_path else None
    hcon = _ro(history) if history else None
    gpath = Path(games) if games and Path(games).exists() else None
    out = {"version": VERSION, "register": REGISTER, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "args": {"league": league, "days": days}, "read_only": True,
           "sources": {"learning": str(learning), "inputs": str(inputs) if icon else None,
                       "store": str(store_path) if scon else None, "history": str(history) if hcon else None,
                       "games": str(gpath) if gpath else None},
           "policy": {"grade": grade_policy.VERSION, "bands": {"A": 0.60, "B": grade_policy.BE_HIGH, "C": grade_policy.BE_LOW},
                      "side": "board lean (lean_of: chance > 50% and the mean on that side), never the max-p side",
                      "stated": "mean lean-side chance given no push; hit = wins / (wins + losses)",
                      "ci": "game-clustered bootstrap 95% (B = 2,000, seed 20261023); null below 2 games",
                      "min_slice": MIN_SLICE, "vg1_contain_lean": board_view.VG_CONTAIN_LEAN,
                      "containment": "sports/containment.py (A9a 4); per league in leagues.<lg>.guards"},
           "leagues": {}}
    try:
        for lg in (league,) if league else LEAGUES:
            rows, meta = load_rows(lcon, icon, scon, lg, days, now)
            if not rows:
                out["leagues"][lg] = {"counts": {"props": 0}, "meta": meta, "verdict": {"verdict": "insufficient",
                                                                                     "why": "no sm-1 rows"}}
                continue
            if gpath:
                meta["favourite_events"] = attach_favourite(rows, gpath, lg)
            if hcon is not None:
                meta["l10_rows"] = attach_l10(rows, hcon, lg)
            pc_q, pc_info = pc_walk_q(rows, lg)
            if pc_info.get("letters") is not None:      # A10: pc-1 prints the chance -> score the pc-1 chance
                cal_q, cal_info = pc_q, {"pc1": pc_info}
                score_rows(rows, cal_q, label="pc-1")
            else:
                cal_q, cal_info = cal_walk_q(rows, lg)
                labels = None
                if lg == "nba" and "nba:assists" not in (cal_info.get("on") or []):
                    a_q, cal_info["ac2"] = ac2_walk_q(rows)   # ac-2: the chance the board shows for assists
                    if a_q:
                        cal_q = dict(cal_q, **a_q)
                        labels = {f: "ac-2" for f in a_q}
                score_rows(rows, cal_q, labels=labels)
            cal_info["walk_forward_a8a"] = cal_mi_compact(lcon, icon, lg)
            rep = league_report(rows, lg, cal_info)
            rep["meta"] = meta
            out["leagues"][lg] = rep
    finally:
        for c in (lcon, icon, scon, hcon):
            if c is not None:
                c.close()
    return out


def _leaf(v):
    return v is None or isinstance(v, (bool, int, float, str)) or (
        isinstance(v, list) and len(v) <= 8 and all(_leaf(x) and not isinstance(x, list) for x in v))


def dumps(obj, indent=0):
    """Compact JSON: a dict whose values are all scalars / short lists prints on one line (A9: under ~400 lines)."""
    pad = " " * indent
    if isinstance(obj, dict):
        if all(_leaf(v) for v in obj.values()):
            return json.dumps(obj, default=float)
        parts = [f'{pad} {json.dumps(str(k))}: {dumps(v, indent + 1)}' for k, v in obj.items()]
        return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
    if isinstance(obj, list):
        if _leaf(obj):
            return json.dumps(obj, default=float)
        return "[\n" + ",\n".join(f"{pad} {dumps(v, indent + 1)}" for v in obj) + "\n" + pad + "]"
    return json.dumps(obj, default=float)


def main(a):
    from .. import config
    base = config.DATA_DIR
    learning = a.learning or (base / "model" / "learning.db")
    inputs = a.inputs or (base / "model" / "forecasts.db")
    store_path = a.store or (base / "canonical.db")
    history = getattr(a, "history_db", None) or (base / "model" / "history.db")
    games = a.games or (base / "games.db")
    res = run(learning, inputs, store_path, history, games, a.league, a.days)
    print(dumps(res))
