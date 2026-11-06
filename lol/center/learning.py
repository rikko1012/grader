"""
Continuous learning for LoL without rewriting rules after every loss:
- every settled pre-match forecast updates a dashboard of verified results at the lines that were really offered
  (projection error, Brier / log-loss of P(over) at the offered line, calibration, side hit rate) by stat, scope,
  platform, role and grade, plus the evidence count against the release requirement (300 settled player-markets across
  60+ series);
- old forecasts are never changed; new forecasts use the newly available history under the same versioned fit;
- candidate refits run on a schedule (weekly) on all history up to that day and are RECORDED with a forward comparison
  on the most recent four weeks; nothing is promoted automatically (promotion follows lol/EVALUATION.md). A growing
  settlement count is not, by itself, evidence that accuracy improved.
"""
import json
import math
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from . import store

REQUIRED = {"player_markets": 300, "series": 60}
CONTAIN_ROWS_MAX = 2000             # newest settled offered-side legs kept for the server's letter containment check
REFIT_EVERY_S = 7 * 86400


def dashboard(con):
    rows = con.execute(
        "SELECT f.*, s.status AS st, s.actual, s.over_hit, s.push, s.version FROM lol_forecasts f "
        "JOIN (SELECT platform, market_key, line, MAX(id) AS id FROM lol_forecasts WHERE forecast_at < series_start "
        "GROUP BY platform, market_key, line) z ON z.id = f.id "
        "JOIN (SELECT unit_key, MAX(version) AS v FROM lol_settlements GROUP BY unit_key) lv ON "
        "lv.unit_key = f.platform || '|' || f.market_key || '|' || f.line "
        "JOIN lol_settlements s ON s.unit_key = lv.unit_key AND s.version = lv.v").fetchall()
    status = defaultdict(int)
    offered_status = defaultdict(int)
    groups = defaultdict(list)
    series = set()
    player_props = set()
    units = []
    calendar_rows=[]
    contain_rows = []
    from .forecast import shown, side_offered
    for r in rows:
        r = dict(r)
        sh = shown(r)
        status[r["st"]] += 1
        if sh["side_offered"] == 1:
            offered_status[r["st"]] += 1
            if r['st'] in ('graded','push'):
                calendar_rows.append({'at':r.get('series_start'),'status':'push' if r['st']=='push' else
                    'win' if bool(r['over_hit']) == (sh['side']=='over') else 'loss'})
                # the server's letter containment check (letter_containment.py lc-1, gradecore ct-3): one leg per saved
                # pre-match forecast on a line that offered Scout's side, at the chance members were shown for that side
                contain_rows.append({"p": round(float(sh["p_side"]), 6), "status": calendar_rows[-1]["status"],
                                     "match_key": r.get("sid"), "stat": r.get("stat"), "side": sh["side"],
                                     "platform": r.get("platform"), "at": r.get("series_start"), "id": r.get("id")})
        if r["st"] != "graded":
            continue
        series.add(r["sid"])
        player_props.add((r["sid"], r["pid"], r["stat"], r["scope"]))
        hit = float(r["over_hit"])

        def unit(po, pu, side, offered):
            p = po / max(po + pu, 1e-9)                                # P(over | no push) at the offered line
            return {"err": r["lambda"] - r["actual"], "brier": (p - hit) ** 2,
                    "ll": -(hit * math.log(max(p, 1e-6)) + (1 - hit) * math.log(max(1 - p, 1e-6))), "p": p, "hit": hit,
                    "side_hit": (hit == 1.0) if side == "over" else (hit == 0.0), "p_side": p if side == "over" else 1 - p,
                    "printed": po if side == "over" else pu, "sid": r["sid"], "offered": offered}
        u = unit(sh["p_over"], sh["p_under"], sh["side"], sh["side_offered"] == 1)
        units.append(u)
        for key in ("all", f"stat {r['stat']}", f"scope {r['scope']}", f"platform {r['platform']}", f"role {r['role']}",
                    f"grade {sh['grade_label']}", f"model {r['model_version']}",
                    f"fit {r['model_version']} {r['fit_sha']}",
                    "side offered" if u["offered"] else "side not offered",
                    f"chance shown {sh['cal'] or 'raw'}"):
            groups[key].append(u)
            if u["offered"]:
                groups["offered cohort " + key].append(u)
        # the raw model chance on the same props (before lc-1), kept apart so both can be read side by side
        rw = sh["raw"]
        roff = side_offered(r["platform"], json.loads(r["sides"] or "{}"), rw["side"]) is True
        groups["raw model chance (before lc-1)"].append(unit(rw["p_over"], rw["p_under"], rw["side"], roff))
        # Compare the displayed (unconditional) chance with decisions separately from no-push calibration.
        printed = sh["p_side"]
        if u["offered"] and printed >= 0.60:
            groups["offered picks printed 60%+"].append(u)
            groups[f"model {r['model_version']} offered picks printed 60%+"].append(u)
    out = {}
    for k, us in groups.items():
        n = len(us)
        bins = defaultdict(lambda: [0, 0.0, 0.0])
        for u in us:
            b = min(int(u["p"] * 10), 9)
            bins[b][0] += 1
            bins[b][1] += u["p"]
            bins[b][2] += u["hit"]
        out[k] = {"n": n, "series": len({u["sid"] for u in us}), "mae": round(sum(abs(u["err"]) for u in us) / n, 3), "bias": round(sum(u["err"] for u in us) / n, 3),
                  "brier": round(sum(u["brier"] for u in us) / n, 4), "logloss": round(sum(u["ll"] for u in us) / n, 4),
                  "side_hit_rate": round(sum(u["side_hit"] for u in us) / n, 3), "side_wins": sum(1 for u in us if u["side_hit"]),
                  "mean_p_side": round(sum(u["p_side"] for u in us) / n, 3),
                  "mean_displayed_p_side": round(sum(u["printed"] for u in us) / n, 3),
                  "calibration": [{"bin": f"{b / 10:.1f}", "n": v[0], "predicted": round(v[1] / v[0], 3), "actual": round(v[2] / v[0], 3)}
                                  for b, v in sorted(bins.items())]}
    graded = len(groups.get("all", []))
    counts = {"forecast_rows_saved": con.execute("SELECT COUNT(*) FROM lol_forecasts").fetchone()[0],
              "forecast_rows_pre_match": con.execute("SELECT COUNT(*) FROM lol_forecasts WHERE forecast_at < series_start").fetchone()[0],
              "distinct_offered_props": con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT platform, market_key, line FROM lol_forecasts "
                                                    "WHERE forecast_at < series_start)").fetchone()[0],
              "distinct_player_props": con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT pid, stat, scope, sid FROM lol_forecasts "
                                                   "WHERE forecast_at < series_start)").fetchone()[0],
              "distinct_matches": con.execute("SELECT COUNT(DISTINCT sid) FROM lol_forecasts WHERE forecast_at < series_start").fetchone()[0],
              "graded_offered_props": graded, "graded_matches": len(series), "graded_distinct_player_props": len(player_props)}
    from gradecore.calendar import daily_record
    return {"at": store.now_iso(), 'calendar':daily_record(calendar_rows,store.now_iso(),'Series start date · US Central','America/Chicago'), "settled_by_status": dict(status), "counts": counts, "metrics": out,
            "offered_side": {"version": 1, "settled_by_status": dict(offered_status),
                             "metrics": {k.removeprefix("offered cohort "): v for k, v in out.items() if k.startswith("offered cohort ")}},
            "series_grouped_95ci": {"all": _grouped_ci(units), "offered_side_only": _grouped_ci([u for u in units if u["offered"]])},
            "containment_rows": [{k: v for k, v in x.items() if k != "id"} for x in
                                 sorted(contain_rows, key=lambda x: (str(x["at"] or ""), x["id"] or 0))[-CONTAIN_ROWS_MAX:]],
            "evidence": {"graded_player_markets": len(player_props), "series": len(series), "required": REQUIRED,
                         "unit": "distinct series/player/stat/scope, independent of book or alternate line",
                         "met": len(player_props) >= REQUIRED["player_markets"] and len(series) >= REQUIRED["series"]},
            "candidates": store.get_meta(con, "candidates") or [],
            "lc1": _lc1_view(),
            "note": "forward record of saved pre-match forecasts at offered lines; all mixes versions and alternate lines; "
                    "mean_p_side and probability scores condition on no push; inspect model/fit and offered-side groups; "
                    "no promotion happens automatically"}


def _lc1_view():
    """lc-1 status in use + the last 10 daily decisions + the last per-market stated / hit table (lol/cal.py admin_view)."""
    try:
        from .. import cal
        return cal.admin_view()
    except Exception as e:                                   # noqa: BLE001 - the dashboard never fails on this block
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


def _grouped_ci(units, n_boot=2000, seed=7):
    """Brier, log-loss, side hit rate and projection error with 95% intervals from a bootstrap that resamples whole
    series (same-match props are correlated). None until there are at least 2 series."""
    import random
    by = defaultdict(list)
    for u in units:
        by[u["sid"]].append(u)
    sids = sorted(by)
    if len(sids) < 2:
        return {"series": len(sids), "props": len(units), "note": "fewer than 2 settled series - no interval"}
    rnd = random.Random(seed)
    keys = ("brier", "ll", "side_hit", "err")
    mean = lambda us, k: sum(float(u[k]) for u in us) / len(us)
    draws = {k: [] for k in keys}
    for _ in range(n_boot):
        us = [u for s in (rnd.choice(sids) for _ in sids) for u in by[s]]
        for k in keys:
            draws[k].append(mean(us, k))
    out = {"series": len(sids), "props": len(units)}
    for k in keys:
        d = sorted(draws[k])
        out[k] = {"mean": round(mean(units, k), 4), "lo95": round(d[int(0.025 * n_boot)], 4), "hi95": round(d[int(0.975 * n_boot) - 1], 4)}
    return out


def maybe_refit(con, history_db=None, now=None):
    """Weekly: refit B0 / lol-1 on all history up to today (TRAIN = everything older than four weeks, scored on the last
    four weeks) and record the candidate. Never promoted automatically. Returns the record or None."""
    now = now or time.time()
    last = store.get_meta(con, "last_refit_at")
    if last and last > store.now_iso(now - REFIT_EVERY_S):
        return None
    from .. import backtest, history
    db = history_db or history.DB(dsn="")
    try:
        today = datetime.fromtimestamp(now, timezone.utc).date()
        data = backtest.Data(history.all_player_games(db, since=(today - timedelta(days=245)).isoformat()))
        # the forward weeks never overlap the sealed TEST period (2026-08-01..10-16, lol/EVALUATION.md A4)
        fwd_from = max(today - timedelta(days=28), date(2026, 10, 17))
        if fwd_from >= today:
            return None
        periods = {"train_from": today - timedelta(days=245 - 45), "test_from": fwd_from, "test_to": today + timedelta(days=1)}
        fit, rep = backtest.compare(data, periods, log=lambda m: None)
    finally:
        if history_db is None:
            db.close()
    path = store.db_path().parent / "candidates"
    path.mkdir(parents=True, exist_ok=True)
    name = f"fit_{today.isoformat()}.json"
    (path / name).write_text(json.dumps({"fit": fit, "report": rep}, default=str))
    rec = {"at": store.now_iso(now), "file": name, "selection": fit.get("selection"), "periods": {k: str(v) for k, v in periods.items()},
           "forward_last_4_weeks": {p: {sc: {k: rep["stats"][p]["scopes"][sc].get(k) for k in ("n", "logscore_gain_lol1_minus_b0")}
                                        for sc in ("12", "123") if sc in rep["stats"][p]["scopes"]} for p in rep["stats"]},
           "promoted": False}
    cands = (store.get_meta(con, "candidates") or [])[-25:] + [rec]
    store.set_meta(con, "candidates", cands)
    store.set_meta(con, "last_refit_at", store.now_iso(now))
    con.commit()
    return rec
