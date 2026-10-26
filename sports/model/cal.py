"""cal-mi-1: market-informed calibration of the displayed Scout chance at book lines - REGISTER.md amendment A8.

    P(Over | no push) = sigmoid(a + w_s logit P_scout + w_m logit P_market)      variant M (market chance at the quote)
    P(Over | no push) = sigmoid(b + v_s logit P_scout)                           variant S (no market chance)
    P'(Over) = (1 - pp) q, P'(Under) = (1 - pp) (1 - q), P'(Push) = pp           (sums to one)

Fit: penalised ML (prior centred on the identity = live chance, precision LAMBDA), league pooled then per stat;
walk-forward by game-day on settled props only (props settled BEFORE the day's first forecast). Promotion per league x
stat (A8 6) decides the per-market `on` flag written into the weights file.

    python -m sports.model.cal fit  [--learning L --inputs I --out PATH]   walk-forward eval + final fit -> weights JSON
    python -m sports.model.cal eval [--learning L --inputs I]              walk-forward report only (prints JSON)
    python -m sports.model.cal status                                      the weights in use (file / default / off)

Serving (pure python, no numpy): adjust(league, stat, po, pu, pp, mkc) -> calibrated chances or None (off / market
OFF / invalid weights). Weights: <SPORTS_DATA_DIR>/model/cal_mi.json (owner's Shell run), else the committed default
sports/model/artifacts/cal_mi_default.json. Global off switch SPORTS_CAL_MI=0.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "cal-mi-1"
REGISTER = "sports/research/REGISTER.md A8"
LABEL = "below the 20-game minimum (owner override, REGISTER A8)"
LAMBDA = 20.0
MIN_FIT, MIN_FIT_GAMES = 50, 2
MIN_EVAL, MIN_EVAL_DAYS = 30, 2
LOCAL_MIN_GAMES = 5
DAY_SHIFT_H = 10
P_MAX = 5.0
CITL_TOL, SHARE_TOL = 0.03, 0.15
ID_M, ID_S = (0.0, 1.0, 0.0), (0.0, 1.0)
ART_DIR = Path(__file__).resolve().parent / "artifacts"
DEFAULT_PATH = ART_DIR / "cal_mi_default.json"
FILE_NAME = "cal_mi.json"
_OFF = {"0", "false", "no", "off"}
EPS = 1e-6


def enabled():
    return os.environ.get("SPORTS_CAL_MI", "1").strip().lower() not in _OFF


def weights_path():
    from .. import config
    return config.DATA_DIR / "model" / FILE_NAME


def _logit(p):
    p = min(max(float(p), EPS), 1 - EPS)
    return math.log(p / (1 - p))


def _sig(x):
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def cond_over(po, pu):
    """P(Over | no push) from unconditional chances, or None."""
    if po is None or pu is None:
        return None
    s = float(po) + float(pu)
    return None if s <= 0 else min(max(float(po) / s, EPS), 1 - EPS)


# ============================================================================================ weights file + validation
def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _theta_ok(t, n):
    return (isinstance(t, (list, tuple)) and len(t) == n and
            all(isinstance(x, (int, float)) and math.isfinite(x) and abs(x) <= P_MAX for x in t))


def validate(obj):
    """-> obj (with its sha checked) or raise ValueError. Schema: {"version", "params": {"<lg>:<stat>": {"on": bool,
    "M": [a, w_s, w_m], "S": [b, v_s], ...}}, "sha": sha of params}."""
    if not isinstance(obj, dict) or obj.get("version") != VERSION:
        raise ValueError("not a cal-mi-1 weights file")
    params = obj.get("params")
    if not isinstance(params, dict):
        raise ValueError("params missing")
    for k, p in params.items():
        if not isinstance(k, str) or k.count(":") != 1 or not isinstance(p, dict):
            raise ValueError(f"bad market key {k!r}")
        if not isinstance(p.get("on"), bool):
            raise ValueError(f"{k}: on must be true / false")
        if not _theta_ok(p.get("M"), 3) or not _theta_ok(p.get("S"), 2):
            raise ValueError(f"{k}: parameters must be finite numbers with |x| <= {P_MAX}")
    if obj.get("sha") != _sha(params):
        raise ValueError("sha mismatch")
    return obj


_cache = {"t": 0.0, "key": None, "obj": None, "src": None}
CHECK_S = 30.0


def load(force=False):
    """The weights in use -> (obj, source) - source "file" / "default" / None (off: switch, nothing valid). Cached;
    the file's mtime is re-checked every CHECK_S seconds (a daily refit is picked up without a deploy)."""
    if not enabled():
        return None, None
    now = time.time()
    if not force and now - _cache["t"] < CHECK_S and _cache["key"] is not None:
        return _cache["obj"], _cache["src"]
    cands = []
    try:
        cands.append((weights_path(), "file"))
    except Exception:                                    # noqa: BLE001
        pass
    cands.append((DEFAULT_PATH, "default"))
    keys = []
    for p, src in cands:
        try:
            keys.append((str(p), p.stat().st_mtime_ns))
        except OSError:
            keys.append((str(p), None))
    key = tuple(keys)
    if not force and key == _cache["key"]:
        _cache["t"] = now
        return _cache["obj"], _cache["src"]
    obj, used = None, None
    for (p, src), (_, mt) in zip(cands, keys):
        if mt is None:
            continue
        try:
            obj, used = validate(json.loads(p.read_text(encoding="utf-8"))), src
            break
        except (OSError, ValueError):
            continue
    _cache.update(t=now, key=key, obj=obj, src=used)
    return obj, used


def reset_cache():
    _cache.update(t=0.0, key=None, obj=None, src=None)


def market_chance(mkc):
    """mv-1 market chance at the quote (board_view.market_at result) -> P(Over | no push) or None."""
    if not mkc or not (mkc.get("n") or 0) > 0:
        return None
    return cond_over(mkc.get("po"), mkc.get("pu"))


def q_of(p, ps, pm):
    """Calibrated P(Over | no push) from one market's params, the Scout and market conditional chances."""
    if pm is not None:
        a, ws, wm = p["M"]
        return _sig(a + ws * _logit(ps) + wm * _logit(pm)), "M"
    b, vs = p["S"]
    return _sig(b + vs * _logit(ps)), "S"


def adjust(league, stat, po, pu, pp, mkc=None, weights=None):
    """THE serving function. -> {"po", "pu", "pp", "raw": [po, pu, pp], "v": "cal-mi-1:<sha>", "mode": "M" | "S",
    "src": "file" | "default"} or None (off / market OFF / no Scout chance)."""
    if weights is None:
        weights, src = load()
    else:
        src = "given"
    if not weights:
        return None
    p = (weights.get("params") or {}).get(f"{league}:{stat}")
    if not p or not p.get("on"):
        return None
    ps = cond_over(po, pu)
    if ps is None:
        return None
    ppv = float(pp or 0.0)
    q, mode = q_of(p, ps, market_chance(mkc))
    dec = 1.0 - ppv
    return {"po": round(dec * q, 6), "pu": round(dec * (1 - q), 6), "pp": round(ppv, 6),
            "raw": [round(float(po), 6), round(float(pu), 6), round(ppv, 6)],
            "v": f"{VERSION}:{weights['sha']}", "mode": mode, "src": src}


# ================================================================================================ fitting (numpy)
def _fit(X, y, theta0, lam=LAMBDA, iters=50):
    """Penalised logistic regression by Newton: min sum NLL + lam/2 ||theta - theta0||^2. -> theta (list) or None."""
    import numpy as np
    X, y = np.asarray(X, float), np.asarray(y, float)
    t0 = np.asarray(theta0, float)
    th = t0.copy()
    for _ in range(iters):
        z = X @ th
        p = 1 / (1 + np.exp(-z))
        g = X.T @ (p - y) + lam * (th - t0)
        H = (X * (p * (1 - p))[:, None]).T @ X + lam * np.eye(len(th))
        step = np.linalg.solve(H, g)
        th = th - step
        if np.max(np.abs(step)) < 1e-10:
            break
    if not np.all(np.isfinite(th)) or np.max(np.abs(th)) > P_MAX:
        return None
    return [float(x) for x in th]


def _design(rows, with_market):
    import numpy as np
    s = np.array([_logit(r["ps"]) for r in rows])
    if with_market:
        m = np.array([_logit(r["pm"]) for r in rows])
        return np.column_stack([np.ones(len(rows)), s, m])
    return np.column_stack([np.ones(len(rows)), s])


def _enough(rows):
    return len(rows) >= MIN_FIT and len({r["event"] for r in rows}) >= MIN_FIT_GAMES


def _fit_level(rows, prior_m, prior_s):
    """-> (M, S, nM, nS): M on rows with a market chance, S on every row; each falls back to its prior."""
    rm = [r for r in rows if r.get("pm") is not None]
    M = _fit(_design(rm, True), [r["hit"] for r in rm], prior_m) if _enough(rm) else None
    S = _fit(_design(rows, False), [r["hit"] for r in rows], prior_s) if _enough(rows) else None
    return (M or list(prior_m)), (S or list(prior_s)), (len(rm) if M else 0), (len(rows) if S else 0)


def fit_params(rows):
    """{"<lg>:<stat>": {"M", "S", "n_M", "n_S", "level_M", "level_S"}} for every league x stat in rows (A8 2)."""
    out = {}
    by_lg = {}
    for r in rows:
        by_lg.setdefault(r["league"], []).append(r)
    for lg, lrows in by_lg.items():
        LM, LS, lnM, lnS = _fit_level(lrows, ID_M, ID_S)
        by_st = {}
        for r in lrows:
            by_st.setdefault(r["stat"], []).append(r)
        for st, srows in by_st.items():
            M, S, nM, nS = _fit_level(srows, LM, LS)
            out[f"{lg}:{st}"] = {"M": M, "S": S, "n_M": nM, "n_S": nS,
                                 "level_M": "stat" if nM else ("league" if lnM else "identity"),
                                 "level_S": "stat" if nS else ("league" if lnS else "identity")}
    return out


def game_day(forecast_at):
    t = datetime.strptime(forecast_at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return (t - timedelta(hours=DAY_SHIFT_H)).date().isoformat()


def walk(rows):
    """Walk-forward by game-day (A8 4). -> list of (row, q or None, mode, cutoff): q from parameters fitted ONLY on
    props settled before the day's first forecast_at."""
    days = {}
    for r in rows:
        days.setdefault(game_day(r["forecast_at"]), []).append(r)
    out = []
    for d in sorted(days):
        drows = days[d]
        cutoff = min(r["forecast_at"] for r in drows)
        train = [r for r in rows if (r.get("settled_at") or "9") < cutoff]
        params = fit_params(train) if train else {}
        for r in drows:
            p = params.get(f"{r['league']}:{r['stat']}")
            if p is None or (p["level_M"] == "identity" and p["level_S"] == "identity"):
                out.append((r, None, None, cutoff))
                continue
            q, mode = q_of(p, r["ps"], r.get("pm"))
            out.append((r, q, mode, cutoff))
    return out


def leave_one_game_out(rows):
    """A8 8 (local default only): each game scored with parameters fitted on the OTHER games."""
    out = []
    for ev in sorted({r["event"] for r in rows}):
        train = [r for r in rows if r["event"] != ev]
        params = fit_params(train)
        for r in rows:
            if r["event"] != ev:
                continue
            p = params.get(f"{r['league']}:{r['stat']}")
            if p is None or (p["level_M"] == "identity" and p["level_S"] == "identity"):
                out.append((r, None, None, None))
                continue
            q, mode = q_of(p, r["ps"], r.get("pm"))
            out.append((r, q, mode, None))
    return out


def summary(p, hit, games):
    import numpy as np
    from . import evaluate, grade_policy
    p, hit = np.clip(np.asarray(p, float), EPS, 1 - EPS), np.asarray(hit, float)
    ll = -(hit * np.log(p) + (1 - hit) * np.log(1 - p))
    s, o = float(np.mean(p > 0.5)), float(hit.mean())
    pch = np.maximum(p, 1 - p)
    win = np.where(p >= 0.5, hit, 1 - hit)
    bands = {}
    for b in ("A", "B", "C", "Lean"):
        m = np.array([grade_policy.band(x) == b for x in pch])
        if m.sum():
            mm, lo, hi = evaluate.boot_mean(win[m] - pch[m], np.asarray(games)[m])
            bands[b] = {"n": int(m.sum()), "stated": round(float(pch[m].mean()), 4),
                        "hit": round(float(win[m].mean()), 4), "ci_game": [lo, hi]}
    return {"n": int(len(p)), "logloss": float(ll.mean()), "brier": float(np.mean((p - hit) ** 2)),
            "in_the_large": float(p.mean() - o), "share_over_favoured": s, "over_rate": o,
            "mean_chosen_p": float(pch.mean()), "bands": bands}, ll


def report(scored, min_eval=MIN_EVAL, min_days=MIN_EVAL_DAYS, min_games=None):
    """Per league x stat before / after + the A8 6 verdict. scored: walk() / leave_one_game_out() output."""
    import numpy as np
    from . import evaluate
    by = {}
    for r, q, mode, _ in scored:
        by.setdefault(f"{r['league']}:{r['stat']}", []).append((r, q, mode))
    out = {}
    for k, xs in sorted(by.items()):
        ok = [(r, q, mode) for r, q, mode in xs if q is not None]
        rep = {"props": len(xs), "scored": len(ok), "games": len({r["event"] for r, _, _ in ok}),
               "days": len({game_day(r["forecast_at"]) for r, _, _ in ok}),
               "with_market": sum(1 for _, _, m in ok if m == "M")}
        if not ok:
            rep.update(verdict="not evaluable", on=False, why="no walk-forward fit for any day")
            out[k] = rep
            continue
        hit = np.array([r["hit"] for r, _, _ in ok], float)
        games = np.array([r["event"] for r, _, _ in ok], dtype=object)
        live = np.array([r["ps"] for r, _, _ in ok])
        cal = np.array([q for _, q, _ in ok])
        rep["live"], ll_l = summary(live, hit, games)
        rep["cal_mi"], ll_c = summary(cal, hit, games)
        m, lo, hi = evaluate.boot_mean(ll_c - ll_l, games)
        rep["logloss_cal_minus_live"] = {"mean": m, "ci_game": [lo, hi]}
        c = rep["cal_mi"]
        enough = len(ok) >= min_eval and (rep["days"] >= min_days if min_games is None else rep["games"] >= min_games)
        balanced = abs(c["share_over_favoured"] - c["over_rate"]) <= SHARE_TOL  # A8a: required on both paths
        sig = hi is not None and hi < 0 and balanced
        weak = m is not None and m <= 0 and abs(c["in_the_large"]) <= CITL_TOL and balanced
        if not enough:
            rep.update(verdict="not evaluable", on=False,
                       why=f"needs >= {min_eval} scored props from >= " +
                           (f"{min_days} game-days" if min_games is None else f"{min_games} games"))
        elif sig and min_games is None:
            rep.update(verdict="PASS (i)", on=True,
                       why="log-loss interval entirely below 0, share favouring Over within 0.15")
        elif weak:
            rep.update(verdict="PASS (ii)", on=True,
                       why="not worse on log loss, in-the-large within 0.03, share favouring Over within 0.15")
        else:
            why = []
            if m is None or m > 0:
                why.append(f"log loss worse ({m:+.4f})" if m is not None else "no log loss")
            if abs(c["in_the_large"]) > CITL_TOL:
                why.append(f"in-the-large {c['in_the_large']:+.3f}")
            if abs(c["share_over_favoured"] - c["over_rate"]) > SHARE_TOL:
                why.append(f"share Over {c['share_over_favoured']:.2f} vs over rate {c['over_rate']:.2f}")
            rep.update(verdict="FAIL", on=False, why="; ".join(why))
        out[k] = rep
    return out


def weights_obj(rows, rep, source, as_of=None):
    """Final parameters on ALL rows + the per-market on flag from the report -> a validated weights object."""
    fitted = fit_params(rows)
    params = {}
    for k, p in sorted(fitted.items()):
        params[k] = {"on": bool((rep.get(k) or {}).get("on")), "M": [round(x, 6) for x in p["M"]],
                     "S": [round(x, 6) for x in p["S"]], "n_M": p["n_M"], "n_S": p["n_S"],
                     "level_M": p["level_M"], "level_S": p["level_S"],
                     "verdict": (rep.get(k) or {}).get("verdict", "not evaluable")}
    obj = {"version": VERSION, "register": REGISTER, "label": LABEL, "source": source,
           "fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data_as_of": as_of,
           "rows": len(rows), "lambda": LAMBDA, "params": params}
    obj["sha"] = _sha(params)
    return validate(obj)


# ============================================================================================ production rows (Shell)
def _offer_id(rid, platform):
    import re
    x = str(rid or "")
    x = x[len("offer:"):] if x.startswith("offer:") else x
    x = x[len(platform) + 1:] if x.startswith(platform + ":") else x
    return re.sub(r":r\d+:[0-9a-f]+$", "", x)


def rows_from_learning(lcon, icon=None):
    """A8 3: settled sm-1 target-board props + the market chance at the same offer and forecast_at (A8 1)."""
    q = ("SELECT forecast_id, model_version, league, stat, event_id, platform, line, p_over, p_under, p_push, status, "
         "value, forecast_at, settled_at FROM scorecard WHERE superseded = 0")
    sm, probs = [], {}
    for r in lcon.execute(q):
        fid, mv, lg, st, ev, plat, line, po, pu, pp, status, value, fat, sat = r
        if mv != "sm-1":
            if po is not None and pu is not None:
                probs[fid] = (mv, po, pu)
            continue
        if plat not in ("prizepicks", "underdog") or value is None or status not in ("win", "loss") \
                or float(value) == float(line) or po is None or pu is None:
            continue
        sm.append({"fid": fid, "league": lg, "stat": st, "event": ev, "platform": plat, "line": float(line),
                   "po": po, "pu": pu, "pp": pp or 0.0, "hit": 1.0 if float(value) > float(line) else 0.0,
                   "forecast_at": fat, "settled_at": sat})
    last = {}
    for r in sm:
        rid = None
        pm = None
        if icon is not None:
            x = icon.execute("SELECT offer_record_id FROM inputs WHERE forecast_id = ?", (r["fid"],)).fetchone()
            rid = x[0] if x else None
            if rid:
                ls = []
                mc = None
                for mfid, mmv in icon.execute("SELECT forecast_id, model_version FROM inputs WHERE offer_record_id = ? "
                                              "AND forecast_at = ?", (rid, r["forecast_at"])):
                    pr = probs.get(mfid)
                    if pr is None:
                        continue
                    c = cond_over(pr[1], pr[2])
                    if c is None:
                        continue
                    if mmv.startswith("mkt-"):
                        ls.append(_logit(c))
                    elif mmv == "mc-1":
                        mc = c
                pm = _sig(sum(ls) / len(ls)) if ls else mc
        r["pm"] = pm
        r["ps"] = cond_over(r["po"], r["pu"])
        key = (r["event"], r["platform"], _offer_id(rid, r["platform"]) if rid else r["fid"], r["stat"], r["line"])
        if key not in last or r["forecast_at"] > last[key]["forecast_at"]:
            last[key] = r
    return sorted(last.values(), key=lambda r: r["forecast_at"])


def run_eval(rows):
    return report(walk(rows))


def main(argv=None):
    import argparse
    import sqlite3
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    ap = argparse.ArgumentParser(prog="python -m sports.model.cal")
    ap.add_argument("cmd", choices=["fit", "eval", "status"])
    ap.add_argument("--learning")
    ap.add_argument("--inputs")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        obj, src = load(force=True)
        print(json.dumps({"version": VERSION, "enabled": enabled(), "source": src,
                          "path": str(weights_path()), "sha": (obj or {}).get("sha"),
                          "fitted_at": (obj or {}).get("fitted_at"), "label": (obj or {}).get("label"),
                          "on": sorted(k for k, p in ((obj or {}).get("params") or {}).items() if p.get("on"))},
                         indent=1))
        return
    from .. import config
    lp = Path(a.learning) if a.learning else config.DATA_DIR / "model" / "learning.db"
    ip = Path(a.inputs) if a.inputs else config.DATA_DIR / "model" / "forecasts.db"
    lcon = sqlite3.connect(f"file:{lp.as_posix()}?mode=ro", uri=True, timeout=30)
    icon = sqlite3.connect(f"file:{ip.as_posix()}?mode=ro", uri=True, timeout=30) if ip.exists() else None
    rows = rows_from_learning(lcon, icon)
    rep = run_eval(rows)
    res = {"version": VERSION, "label": LABEL, "register": REGISTER, "rows": len(rows),
           "with_market": sum(1 for r in rows if r.get("pm") is not None),
           "days": len({game_day(r["forecast_at"]) for r in rows}),
           "rule": "A8 6 + A8a: >= 30 scored props from >= 2 game-days; |share Over - over rate| <= 0.15 always; "
                   "PASS (i) log-loss CI < 0, or (ii) log loss <= 0 and |in-the-large| <= 0.03",
           "markets": rep}
    if a.cmd == "fit":
        obj = weights_obj(rows, rep, "production learning.db walk-forward",
                          max((r["settled_at"] or "" for r in rows), default=None))
        out = Path(a.out) if a.out else weights_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
        os.replace(tmp, out)
        res["written"] = {"path": str(out), "sha": obj["sha"],
                          "on": sorted(k for k, p in obj["params"].items() if p["on"]),
                          "params": {k: {x: p[x] for x in ("on", "M", "S", "level_M", "level_S")}
                                     for k, p in obj["params"].items()}}
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
