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
    groups = defaultdict(list)
    series = set()
    for r in rows:
        status[r["st"]] += 1
        if r["st"] != "graded":
            continue
        series.add(r["sid"])
        p = r["p_over"] / max(r["p_over"] + r["p_under"], 1e-9)       # P(over | no push) at the offered line
        hit = float(r["over_hit"])
        side_hit = (hit == 1.0) if r["model_side"] == "over" else (hit == 0.0)
        u = {"err": r["lambda"] - r["actual"], "brier": (p - hit) ** 2,
             "ll": -(hit * math.log(max(p, 1e-6)) + (1 - hit) * math.log(max(1 - p, 1e-6))), "p": p, "hit": hit,
             "side_hit": side_hit, "p_side": max(p, 1 - p)}
        for key in ("all", f"stat {r['stat']}", f"scope {r['scope']}", f"platform {r['platform']}", f"role {r['role']}",
                    f"grade {r['grade_label']}"):
            groups[key].append(u)
    out = {}
    for k, us in groups.items():
        n = len(us)
        bins = defaultdict(lambda: [0, 0.0, 0.0])
        for u in us:
            b = min(int(u["p"] * 10), 9)
            bins[b][0] += 1
            bins[b][1] += u["p"]
            bins[b][2] += u["hit"]
        out[k] = {"n": n, "mae": round(sum(abs(u["err"]) for u in us) / n, 3), "bias": round(sum(u["err"] for u in us) / n, 3),
                  "brier": round(sum(u["brier"] for u in us) / n, 4), "logloss": round(sum(u["ll"] for u in us) / n, 4),
                  "side_hit_rate": round(sum(u["side_hit"] for u in us) / n, 3),
                  "mean_p_side": round(sum(u["p_side"] for u in us) / n, 3),
                  "calibration": [{"bin": f"{b / 10:.1f}", "n": v[0], "predicted": round(v[1] / v[0], 3), "actual": round(v[2] / v[0], 3)}
                                  for b, v in sorted(bins.items())]}
    graded = len(groups.get("all", []))
    return {"at": store.now_iso(), "settled_by_status": dict(status), "metrics": out,
            "evidence": {"graded_player_markets": graded, "series": len(series), "required": REQUIRED,
                         "met": graded >= REQUIRED["player_markets"] and len(series) >= REQUIRED["series"]},
            "candidates": store.get_meta(con, "candidates") or [],
            "note": "forward record of saved pre-match forecasts at offered lines; no promotion happens automatically"}


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
        periods = {"train_from": today - timedelta(days=245 - 45), "test_from": today - timedelta(days=28),
                   "test_to": today + timedelta(days=1)}
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
