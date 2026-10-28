"""lc-1: calibration of the LoL chance shown to members against the OFFERED line - lol/EVALUATION.md amendment A14.

    P(Over | no push) = q = sigmoid(theta . x),  x = [1, logit p, z, v, goblin, demon, scope_12, scope_123]
    P'(Over) = (1 - pp) q, P'(Under) = (1 - pp) (1 - q), P'(Push) = pp                     (sums to one)

p = the raw lol-1f / lol-1 chance P(Over | no push); z = (line - projection) / sd (NB sd, clipped to +-4); v = +1 Over-only
line, -1 Under-only line, 0 otherwise; goblin / demon = PrizePicks alternate-line indicators; scope indicators (Map 1 = 0).
No price: PrizePicks lists none, Underdog's payout_multiplier is a payout modifier and its decimal prices are undocumented
(lol/center/prices.py) - none of them is an input.

Fit: penalised ML (prior centred on the identity = the live chance, precision LAMBDA), pooled "lol:all" then per market
(stat x platform); walk-forward by UTC day on settled props only (settled BEFORE the day's first forecast). Promotion per
market (A14 6) decides the `on` flag written into the weights file.

    python -m lol.cal eval   [--db PATH]               walk-forward report only (prints JSON)
    python -m lol.cal fit    [--db PATH --out PATH]    walk-forward + final fit -> weights JSON (the only writer)
    python -m lol.cal status                           the weights in use (file / default / off)
    python -m lol.cal autofit [--db PATH --out PATH]   A16 1: the daily run (fit + hysteresis + decision log); the LoL worker
                                                       starts it once a UTC day as a child process (LOL_CAL_AUTOFIT=0: never)

--db defaults to the production lol.db (lol/center/store.db_path()), opened read-only.

Serving (pure python): adjust(rec) -> calibrated chances or None (off / market OFF / invalid weights). Applied once when the
worker saves a forecast record; every surface reads it through lol.center.forecast.shown(). Weights:
<LOL_DATA_DIR>/model/lol_cal.json, else the committed default lol/cal_default.json; global off switch LOL_CAL=0.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = "lc-1"
REGISTER = "lol/EVALUATION.md A14"
LABEL = "below the A6 minimum (owner override, lol/EVALUATION.md A14)"
LAMBDA = 20.0
MIN_FIT, MIN_FIT_SERIES = 50, 2
MIN_EVAL, MIN_EVAL_DAYS = 30, 2
P_MAX = 5.0
Z_MAX = 4.0
CITL_TOL, SHARE_TOL = 0.03, 0.15
N_BOOT, SEED = 2000, 20261026
FEATS = ("intercept", "logit_p", "z_line_gap", "v_side_offered", "goblin", "demon", "scope_12", "scope_123")
NP = len(FEATS)
IDENTITY = (0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
POOLED = "lol:all"
PKG = Path(__file__).resolve().parent
DEFAULT_PATH = PKG / "cal_default.json"
FILE_NAME = "lol_cal.json"
_OFF = {"0", "false", "no", "off"}
EPS = 1e-6
BANDS = ((0.80, "A+"), (0.74, "A"), (0.68, "A-"), (0.63, "B+"), (0.57, "B"), (0.55, "B-"))   # = center/forecast.BANDS


def enabled():
    return os.environ.get("LOL_CAL", "1").strip().lower() not in _OFF


def weights_path():
    from . import config
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


def band(p):
    for cut, g in BANDS:
        if p >= cut:
            return g
    return "Lean"


# ======================================================================================================== features
def _sides(rec):
    s = rec.get("sides")
    if isinstance(s, str):
        try:
            s = json.loads(s or "{}")
        except ValueError:
            s = {}
    return s or {}


def offered(platform, sides, side):
    """True only when the feed lists `side` as offered (same rule as center/forecast.side_offered; None -> False)."""
    key = "higher" if side == "over" else "lower"
    s = (sides or {}).get(key)
    if platform == "underdog":
        return bool(s) and (s.get("status") in (None, "active"))
    return bool(s) and s.get("offered") is True


def features(rec, p=None):
    """The A14 1 design vector of one forecast record (dict with platform, sides, odds_type, scope, line, lambda, nb_size,
    p_over / p_under) -> list of NP floats, or None when the raw chance or the line is missing."""
    if p is None:
        p = cond_over(rec.get("p_over"), rec.get("p_under"))
    try:
        line, mu, size = float(rec["line"]), float(rec["lambda"]), float(rec.get("nb_size") or 1e6)
    except (KeyError, TypeError, ValueError):
        return None
    if p is None or not all(math.isfinite(x) for x in (line, mu, size)):
        return None
    sd = math.sqrt(max(mu + mu * mu / max(size, 1e-6), 1e-6))
    z = max(-Z_MAX, min(Z_MAX, (line - mu) / sd))
    sides, plat = _sides(rec), rec.get("platform")
    o, u = offered(plat, sides, "over"), offered(plat, sides, "under")
    v = 1.0 if (o and not u) else (-1.0 if (u and not o) else 0.0)
    ot, sc = rec.get("odds_type"), str(rec.get("scope") or "")
    return [1.0, _logit(p), z, v, float(plat == "prizepicks" and ot == "goblin"), float(plat == "prizepicks" and ot == "demon"),
            float(sc == "12"), float(sc == "123")]


def q_of(theta, x):
    return _sig(sum(t * xi for t, xi in zip(theta, x)))


# ======================================================================================================== weights file
def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _theta_ok(t):
    return (isinstance(t, (list, tuple)) and len(t) == NP and
            all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and abs(x) <= P_MAX for x in t))


def validate(obj):
    """-> obj or raise ValueError. Schema: {"version": "lc-1", "params": {"<stat>:<platform>": {"on": bool, "theta": [8]}},
    "sha": sha of params}."""
    if not isinstance(obj, dict) or obj.get("version") != VERSION:
        raise ValueError("not an lc-1 weights file")
    params = obj.get("params")
    if not isinstance(params, dict):
        raise ValueError("params missing")
    for k, p in params.items():
        if not isinstance(k, str) or k.count(":") != 1 or not isinstance(p, dict):
            raise ValueError(f"bad market key {k!r}")
        if not isinstance(p.get("on"), bool):
            raise ValueError(f"{k}: on must be true / false")
        if not _theta_ok(p.get("theta")):
            raise ValueError(f"{k}: theta must be {NP} finite numbers with |x| <= {P_MAX}")
    if obj.get("sha") != _sha(params):
        raise ValueError("sha mismatch")
    return obj


_cache = {"t": 0.0, "key": None, "obj": None, "src": None}
CHECK_S = 30.0


def load(force=False):
    """The weights in use -> (obj, source) - "file" / "default" / None (off or nothing valid). Cached; the files' mtimes
    are re-checked every CHECK_S seconds (an owner refit is picked up without a deploy)."""
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
    for p, _ in cands:
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


def adjust(rec, weights=None):
    """THE serving function. rec: a forecast record (raw p_over / p_under / p_push, stat, platform, line, lambda, nb_size,
    sides, odds_type, scope). -> {"po", "pu", "pp", "v": "lc-1:<sha>", "src"} or None (off / market OFF / no raw chance)."""
    if weights is None:
        weights, src = load()
    else:
        src = "given"
    if not weights:
        return None
    p = (weights.get("params") or {}).get(f"{rec.get('stat')}:{rec.get('platform')}")
    if not p or not p.get("on"):
        return None
    x = features(rec)
    if x is None:
        return None
    pp = float(rec.get("p_push") or 0.0)
    q = q_of(p["theta"], x)
    dec = 1.0 - pp
    return {"po": round(dec * q, 4), "pu": round(dec * (1 - q), 4), "pp": round(pp, 4),
            "v": f"{VERSION}:{weights['sha']}", "src": src}


# ======================================================================================================== fitting (numpy)
def _fit(X, y, theta0, lam=LAMBDA, iters=60):
    """Penalised logistic regression by Newton: min NLL + lam/2 ||theta - theta0||^2 -> list or None."""
    import numpy as np
    X, y, t0 = np.asarray(X, float), np.asarray(y, float), np.asarray(theta0, float)
    th = t0.copy()
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ th)))
        g = X.T @ (p - y) + lam * (th - t0)
        H = (X * (p * (1 - p))[:, None]).T @ X + lam * np.eye(len(th))
        step = np.linalg.solve(H, g)
        th = th - step
        if np.max(np.abs(step)) < 1e-10:
            break
    if not np.all(np.isfinite(th)) or np.max(np.abs(th)) > P_MAX:
        return None
    return [float(x) for x in th]


def _enough(rows):
    return len(rows) >= MIN_FIT and len({r["sid"] for r in rows}) >= MIN_FIT_SERIES


def _fit_level(rows, prior):
    th = _fit([r["x"] for r in rows], [r["hit"] for r in rows], prior) if _enough(rows) else None
    return (th or list(prior)), (len(rows) if th else 0)


def market(r):
    return f"{r['stat']}:{r['platform']}"


def fit_params(rows):
    """{"<stat>:<platform>": {"theta", "n", "level"}, "lol:all": {...}} (A14 2)."""
    pooled, n_pool = _fit_level(rows, IDENTITY)
    out = {POOLED: {"theta": pooled, "n": n_pool, "level": "pooled" if n_pool else "identity"}}
    by = {}
    for r in rows:
        by.setdefault(market(r), []).append(r)
    for k, mrows in by.items():
        th, n = _fit_level(mrows, pooled)
        out[k] = {"theta": th, "n": n, "level": "market" if n else ("pooled" if n_pool else "identity")}
    return out


def day_of(forecast_at):
    return str(forecast_at)[:10]


def walk(rows):
    """Walk-forward by UTC day (A14 4) -> [(row, q or None, cutoff)]: q from parameters fitted ONLY on props settled before
    the day's first forecast_at."""
    days = {}
    for r in rows:
        days.setdefault(day_of(r["forecast_at"]), []).append(r)
    out = []
    for d in sorted(days):
        drows = days[d]
        cutoff = min(r["forecast_at"] for r in drows)
        train = [r for r in rows if (r.get("settled_at") or "9") < cutoff]
        params = fit_params(train) if train else {}
        for r in drows:
            p = params.get(market(r))
            if p is None or p["level"] == "identity":
                out.append((r, None, cutoff))
                continue
            out.append((r, q_of(p["theta"], r["x"]), cutoff))
    return out


def _boot(vals, clusters, n_boot=N_BOOT, seed=SEED):
    """Mean with a cluster (series) bootstrap 95% interval -> (mean, lo, hi); lo / hi None under 2 clusters."""
    import numpy as np
    vals = np.asarray(vals, float)
    if not len(vals):
        return None, None, None
    m = float(vals.mean())
    cl = np.asarray(clusters, dtype=object)
    keys = sorted(set(cl.tolist()))
    if len(keys) < 2:
        return m, None, None
    idx = {k: np.where(cl == k)[0] for k in keys}
    sums = np.array([vals[idx[k]].sum() for k in keys])
    cnts = np.array([len(idx[k]) for k in keys], float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    est = sums[draws].sum(axis=1) / cnts[draws].sum(axis=1)
    return m, float(np.quantile(est, 0.025)), float(np.quantile(est, 0.975))


def summary(p, hit, sids):
    """Scores of one arm's P(Over | no push) on rows: log loss, Brier, in-the-large, share Over vs over rate, chosen-side
    stated vs hit, grade bands (forecast.BANDS)."""
    import numpy as np
    p, hit = np.clip(np.asarray(p, float), EPS, 1 - EPS), np.asarray(hit, float)
    ll = -(hit * np.log(p) + (1 - hit) * np.log(1 - p))
    pch = np.maximum(p, 1 - p)
    win = np.where(p >= 0.5, hit, 1 - hit)
    bands = {}
    for _, b in BANDS + ((0, "Lean"),):
        m = np.array([band(x) == b for x in pch])
        if m.sum():
            bands[b] = {"n": int(m.sum()), "stated": round(float(pch[m].mean()), 4), "hit": round(float(win[m].mean()), 4)}
    hm, hlo, hhi = _boot(win, sids)
    return {"n": int(len(p)), "logloss": round(float(ll.mean()), 5), "brier": round(float(np.mean((p - hit) ** 2)), 5),
            "in_the_large": round(float(p.mean() - hit.mean()), 4), "share_over_favoured": round(float(np.mean(p > 0.5)), 4),
            "over_rate": round(float(hit.mean()), 4), "stated_chosen": round(float(pch.mean()), 4),
            "hit_chosen": round(float(win.mean()), 4), "hit_chosen_ci_series": [hlo and round(hlo, 4), hhi and round(hhi, 4)],
            "bands": bands}, ll


def _compare(rows_q):
    """rows_q: [(row, q)] -> live / lc-1 summaries + the paired log-loss difference with its series interval."""
    import numpy as np
    hit = np.array([r["hit"] for r, _ in rows_q], float)
    sids = [r["sid"] for r, _ in rows_q]
    live, ll_l = summary([r["p"] for r, _ in rows_q], hit, sids)
    cal, ll_c = summary([q for _, q in rows_q], hit, sids)
    m, lo, hi = _boot(ll_c - ll_l, sids)
    return {"live": live, "lc_1": cal, "logloss_cal_minus_live": {"mean": m and round(m, 5), "ci_series": [lo and round(lo, 5),
                                                                                                          hi and round(hi, 5)]}}, (m, lo, hi)


def _verdict(rep, m, lo, hi, m_off, enough):
    c = rep["all"]["lc_1"]
    balanced = abs(c["share_over_favoured"] - c["over_rate"]) <= SHARE_TOL
    sig = hi is not None and hi < 0
    weak = m is not None and m <= 0 and abs(c["in_the_large"]) <= CITL_TOL
    off_ok = m_off is not None and m_off <= 0
    if not enough:
        return "not evaluable", False, f"needs >= {MIN_EVAL} scored props from >= {MIN_EVAL_DAYS} days"
    if (sig or weak) and balanced and off_ok:
        return ("PASS (i)" if sig else "PASS (ii)"), True, (
            ("log-loss interval entirely below 0" if sig else "log loss not worse, in-the-large within 0.03") +
            "; share favouring Over within 0.15; offered-side log loss not worse")
    why = []
    if not (sig or weak):
        if m is None or m > 0:
            why.append(f"log loss worse ({m:+.4f})" if m is not None else "no log loss")
        if abs(c["in_the_large"]) > CITL_TOL:
            why.append(f"log-loss interval {lo:+.4f}..{hi:+.4f} not below 0 and in-the-large {c['in_the_large']:+.3f}"
                       if lo is not None else f"in-the-large {c['in_the_large']:+.3f}")
    if not balanced:
        why.append(f"share Over {c['share_over_favoured']:.2f} vs over rate {c['over_rate']:.2f}")
    if not off_ok:
        why.append(f"offered-side log loss worse ({m_off:+.4f})" if m_off is not None else "no offered-side props")
    return "FAIL", False, "; ".join(why)


def report(scored, min_eval=MIN_EVAL, min_days=MIN_EVAL_DAYS):
    """Per market + pooled: (a) all props, (b) offered-side props, the A14 6 verdict. scored: walk() output."""
    by = {}
    for r, q, _ in scored:
        by.setdefault(market(r), []).append((r, q))
        by.setdefault(POOLED, []).append((r, q))
        by.setdefault(f"stat:{r['stat']}", []).append((r, q))             # report-only groups (never a switch)
        by.setdefault(f"platform:{r['platform']}", []).append((r, q))
    out = {}
    for k, xs in sorted(by.items()):
        ok = [(r, q) for r, q in xs if q is not None]
        rep = {"props": len(xs), "scored": len(ok), "series": len({r["sid"] for r, _ in ok}),
               "days": len({day_of(r["forecast_at"]) for r, _ in ok})}
        if not ok:
            rep.update(verdict="not evaluable", on=False, why="no walk-forward fit for any day")
            out[k] = rep
            continue
        rep["all"], (m, lo, hi) = _compare(ok)
        off = [(r, q) for r, q in ok if r["off_over" if q >= 0.5 else "off_under"]]
        live_off = [(r, q) for r, q in ok if r["off_over" if r["p"] >= 0.5 else "off_under"]]
        m_off = None
        if off:
            rep["offered_side"], (m_off, _, _) = _compare(off)
        if live_off:
            import numpy as np
            rep["offered_side_live_rows"] = summary([r["p"] for r, _ in live_off], np.array([r["hit"] for r, _ in live_off]),
                                                    [r["sid"] for r, _ in live_off])[0]
        enough = len(ok) >= min_eval and rep["days"] >= min_days
        v, on, why = _verdict(rep, m, lo, hi, m_off, enough)
        switch = k != POOLED and not k.startswith(("stat:", "platform:"))
        rep.update(verdict=v if switch else f"{v} (report only)", on=on and switch, why=why)
        out[k] = rep
    return out


def weights_obj(rows, rep, source, as_of=None):
    """Final parameters on ALL rows + the per-market on flag from the report -> a validated weights object."""
    fitted = fit_params(rows)
    params = {}
    for k, p in sorted(fitted.items()):
        if k == POOLED:
            continue
        params[k] = {"on": bool((rep.get(k) or {}).get("on")), "theta": [round(x, 6) for x in p["theta"]], "n": p["n"],
                     "level": p["level"], "verdict": (rep.get(k) or {}).get("verdict", "not evaluable")}
    obj = {"version": VERSION, "register": REGISTER, "label": LABEL, "source": source, "features": list(FEATS),
           "fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data_as_of": as_of, "rows": len(rows),
           "lambda": LAMBDA, "pooled": {"theta": [round(x, 6) for x in fitted[POOLED]["theta"]], "level": fitted[POOLED]["level"],
                                        "verdict": (rep.get(POOLED) or {}).get("verdict")}, "params": params}
    obj["sha"] = _sha(params)
    return validate(obj)


# ======================================================================================================== production rows
ROWS_SQL = (
    "SELECT f.*, s.over_hit AS s_over_hit, s.push AS s_push, s.settled_at AS s_settled_at, s.status AS s_status "
    "FROM lol_forecasts f "
    "JOIN (SELECT platform, market_key, line, MAX(id) AS id FROM lol_forecasts WHERE forecast_at < series_start "
    "GROUP BY platform, market_key, line) z ON z.id = f.id "
    "JOIN (SELECT unit_key, MAX(version) AS v FROM lol_settlements GROUP BY unit_key) lv ON "
    "lv.unit_key = f.platform || '|' || f.market_key || '|' || f.line "
    "JOIN lol_settlements s ON s.unit_key = lv.unit_key AND s.version = lv.v")


def rows_from_db(con):
    """A14 3: settled, graded, non-push offered props = the latest pre-match forecast per (platform, market_key, line) with
    the latest settlement version of that unit. Each row: raw chance p, features x, hit, sid, offered flags, times."""
    con.row_factory = __import__("sqlite3").Row
    out = []
    for r in con.execute(ROWS_SQL):
        r = dict(r)
        if r["s_status"] != "graded" or r["s_push"] or r["s_over_hit"] not in (0, 1):
            continue
        p = cond_over(r["p_over"], r["p_under"])
        x = features(r, p)
        if x is None:
            continue
        sides = _sides(r)
        out.append({"id": r["id"], "stat": r["stat"], "platform": r["platform"], "scope": r["scope"], "odds_type": r["odds_type"],
                    "sid": r["sid"], "line": r["line"], "p": p, "x": x, "hit": float(r["s_over_hit"]),
                    "off_over": offered(r["platform"], sides, "over"), "off_under": offered(r["platform"], sides, "under"),
                    "forecast_at": r["forecast_at"], "settled_at": r["s_settled_at"], "model_version": r["model_version"]})
    return sorted(out, key=lambda r: (r["forecast_at"], r["id"]))


def run_eval(rows):
    return report(walk(rows))


# ======================================================================================================== daily decision (A16 1)
LOG_NAME, STATE_NAME = "lol_cal_log.jsonl", "lol_cal_state.json"
FAILS_OFF = 2                                             # an ON market switches OFF after this many consecutive daily fails
AUTOFIT_HOUR = 6                                          # UTC hour from which the day's run may start


def autofit_enabled():
    return os.environ.get("LOL_CAL_AUTOFIT", "1").strip().lower() not in _OFF


def log_path(base=None):
    return Path(base) / LOG_NAME if base else weights_path().with_name(LOG_NAME)


def state_path(base=None):
    return Path(base) / STATE_NAME if base else weights_path().with_name(STATE_NAME)


def append_log(path, recs):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, sort_keys=True, default=float) + "\n")


def read_log(path, last=10):
    """The last `last` lines of the decision log (bounded read of the file's tail)."""
    path = Path(path)
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-last:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=float), encoding="utf-8")
    os.replace(tmp, path)


def settle_signature(con):
    """(count, latest settled_at) of lol_settlements - a day whose signature equals the last run's has nothing new."""
    n, last = con.execute("SELECT COUNT(*), MAX(settled_at) FROM lol_settlements").fetchone()
    return [int(n or 0), last]


def table(rep):
    """The per-market stated / hit table (live vs lc-1) for the admin page."""
    out = {}
    for k, m in rep.items():
        a = m.get("all") or {}
        lv, c = a.get("live") or {}, a.get("lc_1") or {}
        out[k] = {"scored": m.get("scored"), "series": m.get("series"), "days": m.get("days"), "verdict": m.get("verdict"),
                  "why": m.get("why"), "live_stated": lv.get("stated_chosen"), "live_hit": lv.get("hit_chosen"),
                  "lc1_stated": c.get("stated_chosen"), "lc1_hit": c.get("hit_chosen"),
                  "logloss_diff": (a.get("logloss_cal_minus_live") or {}).get("mean"),
                  "logloss_ci": (a.get("logloss_cal_minus_live") or {}).get("ci_series"),
                  "share_over": c.get("share_over_favoured"), "over_rate": c.get("over_rate")}
    return out


def decide(rep, fresh, prev, fails):
    """A16 1.3 hysteresis. rep: report(); fresh: weights_obj() of today (on = today's pass); prev: the weights file in use before
    (or None); fails: {market: consecutive daily fails}. -> (params, fails, decisions)."""
    prev_p = (prev or {}).get("params") or {}
    params, new_fails, dec = {}, {}, []
    for k in sorted(set(fresh["params"]) | set(prev_p)):
        f, p = fresh["params"].get(k), prev_p.get(k)
        was_on = bool(p and p.get("on"))
        passed = bool(f and f.get("on"))
        nf = 0 if passed else int(fails.get(k, 0)) + 1
        if passed:
            params[k], action = dict(f, on=True), ("ON (pass)" if not was_on else "stays ON (pass, refitted)")
        elif was_on and nf < FAILS_OFF:
            params[k], action = dict(p, on=True, verdict=(f or {}).get("verdict", "not evaluable")), f"stays ON ({nf} of {FAILS_OFF} fails, last passing parameters)"
        else:
            base = f or p
            params[k], action = dict(base, on=False), ("OFF" + (f" ({nf} consecutive fails)" if was_on else ""))
        new_fails[k] = nf
        m = rep.get(k) or {}
        dec.append({"market": k, "n": m.get("scored", 0), "series": m.get("series", 0), "days": m.get("days", 0),
                    "verdict": m.get("verdict", "not evaluable"), "why": m.get("why", "no scored props"),
                    "on_before": was_on, "on_after": params[k]["on"], "fails": nf, "action": action})
    return params, new_fails, dec


def peak_rss_mb():
    """This process's peak resident memory (MB): ru_maxrss on POSIX, PeakWorkingSetSize on Windows."""
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(r / (1024 * 1024 if __import__("sys").platform == "darwin" else 1024), 1)
    except Exception:                                    # noqa: BLE001
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        f = k32.K32GetProcessMemoryInfo
        f.argtypes, f.restype = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD], wintypes.BOOL
        if f(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return round(c.PeakWorkingSetSize / 1e6 / 1.048576, 1)
    except Exception:                                    # noqa: BLE001
        pass
    return None


def skip_if_unchanged(sig, today, base=None):
    """True (and one "skip" log line, state touched) when `sig` equals the last completed run's settlement signature."""
    sp = state_path(base)
    state = _read_json(sp) or {}
    if state.get("signature") != sig:
        return False
    append_log(log_path(base), [{"date": today, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "market": "*",
                                 "action": "skip", "why": "no new settlement since the last run", "signature": sig}])
    state.update(last_skip=today, last_attempt=today)
    _write_json(sp, state)
    return True


def autofit(dbp, out=None, base=None, today=None, force=False):
    """A16 1: the daily A14 fit with hysteresis. Reads lol.db read-only; writes the weights file, the state file and appends the
    decision log. Skips (one log line) when nothing settled since the last completed run. -> summary dict."""
    import sqlite3
    out = Path(out) if out else (Path(base) / FILE_NAME if base else weights_path())
    lp, sp = log_path(base or out.parent), state_path(base or out.parent)
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = _read_json(sp) or {}
    con = sqlite3.connect(f"file:{Path(dbp).as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        sig = settle_signature(con)
        if not force and skip_if_unchanged(sig, today, base or out.parent):
            return {"skipped": True, "date": today, "signature": sig, "peak_rss_mb": peak_rss_mb()}
        rows = rows_from_db(con)
    finally:
        con.close()
    rep = run_eval(rows)
    fresh = weights_obj(rows, rep, f"daily autofit {today} (A16 1)", max((r["settled_at"] or "" for r in rows), default=None))
    prev = None
    try:
        prev = validate(json.loads(out.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        prev = None
    if prev is None:
        try:
            prev = validate(json.loads(DEFAULT_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            prev = None
    params, fails, dec = decide(rep, fresh, prev, state.get("fails") or {})
    obj = dict(fresh, params=params)
    obj["sha"] = _sha(params)
    validate(obj)
    _write_json(out, obj)
    reset_cache()
    for d in dec:
        d.update(date=today, at=at, sha=obj["sha"])
    append_log(lp, dec)
    state.update(signature=sig, fails=fails, last_run=today, last_attempt=today, sha=obj["sha"], rows=len(rows),
                 series=len({r["sid"] for r in rows}), table=table(rep), at=at)
    _write_json(sp, state)
    return {"skipped": False, "date": today, "rows": len(rows), "sha": obj["sha"], "on": sorted(k for k, p in params.items() if p["on"]),
            "decisions": dec, "peak_rss_mb": peak_rss_mb()}


def admin_view(base=None):
    """The lc-1 block of /admin/lol/learning.json (pure file reads; no numpy)."""
    obj, src = load(force=True)
    st = _read_json(state_path(base)) or {}
    return {"version": VERSION, "register": REGISTER + " + A16 1", "enabled": enabled(), "autofit": autofit_enabled(),
            "source": src, "sha": (obj or {}).get("sha"), "fitted_at": (obj or {}).get("fitted_at"),
            "on": sorted(k for k, p in ((obj or {}).get("params") or {}).items() if p.get("on")),
            "markets": {k: {"on": p.get("on"), "verdict": p.get("verdict")} for k, p in ((obj or {}).get("params") or {}).items()},
            "fails": st.get("fails") or {}, "last_run": st.get("last_run"), "last_attempt": st.get("last_attempt"),
            "rule": f"A14 6 daily; ON on a pass, OFF after {FAILS_OFF} consecutive daily fails; LOL_CAL_AUTOFIT=0 stops it",
            "table": st.get("table") or {}, "decisions": read_log(log_path(base), 10), "log": str(log_path(base))}


def main(argv=None):
    import argparse
    import sqlite3
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    ap = argparse.ArgumentParser(prog="python -m lol.cal")
    ap.add_argument("cmd", choices=["fit", "eval", "status", "autofit"])
    ap.add_argument("--db", help="lol.db (default: the production data-center database, read-only)")
    ap.add_argument("--out", help="weights file to write (fit / autofit; default <LOL_DATA_DIR>/model/lol_cal.json)")
    ap.add_argument("--source", help="label of the data in the weights file (fit only)")
    ap.add_argument("--force", action="store_true", help="autofit: run even when nothing new settled")
    a = ap.parse_args(argv)
    if a.cmd == "autofit":
        if a.db:
            dbp = Path(a.db)
        else:
            from .center import store
            dbp = store.db_path()
        out = Path(a.out) if a.out else weights_path()
        print(json.dumps(autofit(dbp, out=out, base=out.parent, force=a.force), default=float))
        return
    if a.cmd == "status":
        obj, src = load(force=True)
        print(json.dumps({"version": VERSION, "enabled": enabled(), "source": src, "path": str(weights_path()),
                          "default": str(DEFAULT_PATH), "sha": (obj or {}).get("sha"), "fitted_at": (obj or {}).get("fitted_at"),
                          "label": (obj or {}).get("label"),
                          "on": sorted(k for k, p in ((obj or {}).get("params") or {}).items() if p.get("on")),
                          "markets": {k: p.get("verdict") for k, p in ((obj or {}).get("params") or {}).items()}}, indent=1))
        return
    if a.db:
        dbp = Path(a.db)
    else:
        from .center import store
        dbp = store.db_path()
    con = sqlite3.connect(f"file:{dbp.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        rows = rows_from_db(con)
    finally:
        con.close()
    rep = run_eval(rows)
    res = {"version": VERSION, "label": LABEL, "register": REGISTER, "db": str(dbp), "rows": len(rows),
           "series": len({r["sid"] for r in rows}), "days": len({day_of(r["forecast_at"]) for r in rows}),
           "rule": "A14 6: >= 30 scored props from >= 2 days; (a) all props: log-loss series CI < 0, or log loss <= 0 and "
                   "|in-the-large| <= 0.03; AND |share Over - over rate| <= 0.15 always; AND (b) offered-side log loss <= 0",
           "markets": rep}
    if a.cmd == "fit":
        obj = weights_obj(rows, rep, a.source or f"walk-forward on {dbp}", max((r["settled_at"] or "" for r in rows), default=None))
        out = Path(a.out) if a.out else weights_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
        os.replace(tmp, out)
        res["written"] = {"path": str(out), "sha": obj["sha"], "on": sorted(k for k, p in obj["params"].items() if p["on"]),
                          "params": {k: {x: p[x] for x in ("on", "theta", "level", "verdict")} for k, p in obj["params"].items()}}
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
