"""pc-1: the PRINTED Scout chance for NBA / NFL props is the rate such chances have actually hit (owner decision
2026-11-05: "we're grading every prop on the board these props will never hit 60% printed chance" -> CALIBRATE; this
supersedes the older "never lower percentages" rule for NBA / NFL; still forbidden: an untested cut, raising stakes to
compensate, forcing Over / Under balance).

    z = logit P_raw(Over | no push)                                     (the saved sm-1 chance, never changed)
    spec "platt":  q = sigmoid(a + b z)
    spec "split":  q = sigmoid(a + b_over max(z, 0) + b_under min(z, 0))  (Over-leaning and Under-leaning rows get their
                                                                          own slope; continuous at 50%)
    per stat family: the same form, penalised toward the league fit (ridge, LAM_STAT) - a stat with few rows stays at
    the league fit.
    printed: P(Over) = (1 - pp) q, P(Under) = (1 - pp)(1 - q), P(Push) = pp       (sums to one; push mass unchanged)
    intercept: "zero" by default (a = 0: the chance only shrinks toward 50%, the printed side is always the model's
    side); a fitted shift ("free", or "games" = penalised by game count) is served only when it beats intercept 0 on
    held-out log loss with the game-clustered 95% interval of the difference entirely below 0 (ICPT_RULE).

Fit (chronological, no leakage): rows = settled sm-1 forecasts (one per offer: the last forecast >= 30 min before the
start, sports/model/gradecore_export.py), game-day = start - 10 h. The spec (league form x per-stat penalty) is chosen
on an INNER split of the fit window only (its last INNER_DAYS game-days); the chosen spec is fitted on the whole fit
window and scored ONCE on the held-out window (the most recent game-days). Pass rule per league (REGISTER-style, fixed
in this file): calibrated log loss below raw with the game-clustered 95% interval of the difference entirely below 0,
|stated - hit| <= SIDE_TOL on the chosen side overall, and for Over-leaning and Under-leaning rows SEPARATELY (opposite
errors cannot cancel) the hit interval reaches the stated chance or |stated - hit| <= SIDE_TOL. The served parameters
are the same spec refitted on every settled row (fit + held-out); the held-out verdict travels with them.

A league whose held-out test FAILED keeps its letters held (containment.contained -> True for that league) but its
printed chance is still the calibrated one - except a league the owner released (ct-1b, sports/containment.py
OWNER_RELEASE: NBA + NFL since 2026-11-08), where letters_held() is reported only. A league that passed keeps the
existing containment rule. Letters always come from the printed (calibrated) chance with the sports-gp-1 thresholds.

Serving (pure python): adjust(league, stat, po, pu, pp) -> {"po", "pu", "pp", "raw": [po, pu, pp], "v": "pc-1:<sha>",
"mode": "P", "src"} or None (off / league not fitted). Parameters: <SPORTS_DATA_DIR>/model/chance_cal.json (a Shell
refit), else the committed sports/model/artifacts/chance_cal_pc1.json. Default ON; SPORTS_CHANCE_CAL=0 turns it off
(the board then shows cal-mi-1 where a market is on, else the raw chance - the previous behaviour).

    python -m sports.model.chance_cal fit --nba EXPORT.json --nfl EXPORT.json [--test-from 2026-11-01] [--out PATH]
                                          [--forward-nba RESULTS.json]   held-out report + the served artifact
    python -m sports.model.chance_cal status
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "pc-1"
LEAGUES = ("nba", "nfl")
ART_DIR = Path(__file__).resolve().parent / "artifacts"
DEFAULT_PATH = ART_DIR / "chance_cal_pc1.json"
FILE_NAME = "chance_cal.json"
_OFF = {"0", "false", "no", "off"}
EPS = 1e-6
P_MAX = 6.0
DAY_SHIFT_H = 10
LAM_LEAGUE = 1.0                    # weak ridge toward 0 (keeps a near-flat fit finite)
STAT_LAMS = (None, 400.0, 100.0, 25.0)   # per-stat penalty candidates (None = league fit only)
SPECS = ("platt", "split")
MIN_STAT = 150                      # a stat family needs this many fit rows for its own parameters
INNER_DAYS = 2                      # the inner (spec-choice) validation window: the fit window's last game-days
SIDE_TOL = 0.03
BINS = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))


def enabled():
    # OFF by default again (owner 2026-11-05 after go-live: "yeah you've ruined the grader"); SPORTS_CHANCE_CAL=1 turns it on
    return os.environ.get("SPORTS_CHANCE_CAL", "0").strip().lower() not in _OFF


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
    if po is None or pu is None:
        return None
    s = float(po) + float(pu)
    return None if s <= 0 else min(max(float(po) / s, EPS), 1 - EPS)


def features(spec, z):
    if spec == "platt":
        return [1.0, z]
    return [1.0, max(z, 0.0), min(z, 0.0)]


def q_of(spec, theta, ps):
    z = _logit(ps)
    return _sig(sum(t * x for t, x in zip(theta, features(spec, z))))


# ============================================================================================ artifact + validation
def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _theta_ok(t, spec):
    n = 2 if spec == "platt" else 3
    return (isinstance(t, (list, tuple)) and len(t) == n and
            all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and abs(x) <= P_MAX
                for x in t))


def validate(obj):
    """-> obj or raise ValueError. {"version": "pc-1", "leagues": {lg: {"spec", "theta", "stats": {stat: theta},
    "letters": "held" | "contain-rule", ...}}, "sha": sha of leagues' serving fields}."""
    if not isinstance(obj, dict) or obj.get("version") != VERSION:
        raise ValueError("not a pc-1 file")
    lgs = obj.get("leagues")
    if not isinstance(lgs, dict):
        raise ValueError("leagues missing")
    for lg, p in lgs.items():
        if lg not in LEAGUES or not isinstance(p, dict) or p.get("spec") not in SPECS:
            raise ValueError(f"bad league entry {lg!r}")
        if not _theta_ok(p.get("theta"), p["spec"]):
            raise ValueError(f"{lg}: theta must be finite numbers with |x| <= {P_MAX}")
        if not isinstance(p.get("stats") or {}, dict) or not all(
                isinstance(k, str) and _theta_ok(v, p["spec"]) for k, v in (p.get("stats") or {}).items()):
            raise ValueError(f"{lg}: bad per-stat parameters")
        if p.get("letters") not in ("held", "contain-rule"):
            raise ValueError(f"{lg}: letters must be held / contain-rule")
        if p.get("intercept", "zero") not in ICPT_MODES:
            raise ValueError(f"{lg}: unknown intercept mode")
        if p.get("intercept") == "zero" and any(t[0] != 0 for t in [p["theta"]] + list((p.get("stats") or {}).values())):
            raise ValueError(f"{lg}: intercept 0 parameters carry a shift")
    if obj.get("sha") != _sha(_serving(lgs)):
        raise ValueError("sha mismatch")
    return obj


def _serving(lgs):
    return {lg: {"spec": p["spec"], "theta": p["theta"], "stats": p.get("stats") or {}, "letters": p["letters"]}
            for lg, p in sorted(lgs.items())}


_cache = {"t": 0.0, "key": None, "obj": None, "src": None}
CHECK_S = 30.0


def load(force=False):
    """-> (obj, "file" | "default") or (None, None) when off / nothing valid. Cached; mtimes re-checked every CHECK_S."""
    if not enabled():
        return None, None
    now = time.time()
    if not force and _cache["key"] is not None and now - _cache["t"] < CHECK_S:
        return _cache["obj"], _cache["src"]
    cands = []
    try:
        cands.append((weights_path(), "file"))
    except Exception:                                    # noqa: BLE001
        pass
    cands.append((DEFAULT_PATH, "default"))
    keys = []
    for p, _src in cands:
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


def params_for(league, stat, weights=None):
    """-> (spec, theta, sha) for one market, or None (off / league not fitted)."""
    if weights is None:
        weights, _ = load()
    p = ((weights or {}).get("leagues") or {}).get(league)
    if not p:
        return None
    return p["spec"], (p.get("stats") or {}).get(stat) or p["theta"], weights["sha"]


def adjust(league, stat, po, pu, pp, weights=None):
    """THE serving function -> the printed chance dict (see the module doc) or None."""
    if weights is None:
        weights, src = load()
    else:
        src = "given"
    pr = params_for(league, stat, weights)
    if pr is None:
        return None
    ps = cond_over(po, pu)
    if ps is None:
        return None
    spec, theta, sha = pr
    q = q_of(spec, theta, ps)
    ppv = min(max(float(pp or 0.0), 0.0), 1.0)
    dec = 1.0 - ppv
    return {"po": round(dec * q, 6), "pu": round(dec * (1 - q), 6), "pp": round(ppv, 6),
            "raw": [round(float(po), 6), round(float(pu), 6), round(ppv, 6)],
            "v": f"{VERSION}:{sha}", "mode": "P", "src": src}


def letters_held(league):
    """True when this league's held-out test FAILED in the parameters in use (its letters stay held)."""
    obj, _ = load()
    p = ((obj or {}).get("leagues") or {}).get(league)
    return bool(p) and p.get("letters") == "held"


# ================================================================================================ fitting (numpy)
def game_day(t):
    x = datetime.strptime(str(t)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return (x - timedelta(hours=DAY_SHIFT_H)).date().isoformat()


def rows_from_export(obj):
    """gradecore-export-1 rows (sports/model/gradecore_export.py: the last saved sm-1 forecast >= 30 min before start
    per event / player / stat / period / line / book) -> decided rows {league, stat, ps, hit, day, event, ...}.
    p is the chosen side's chance given no push; push / void rows are dropped (never a win)."""
    out = []
    for r in obj.get("rows") or ():
        if r.get("status") not in ("win", "loss") or r.get("side") not in ("over", "under"):
            continue
        p = float(r["p"])
        ps = p if r["side"] == "over" else 1 - p
        over_hit = (r["status"] == "win") == (r["side"] == "over")
        out.append({"league": r["sport"], "stat": r["market"], "ps": min(max(ps, EPS), 1 - EPS),
                    "hit": 1.0 if over_hit else 0.0, "day": game_day(r.get("start_at") or r["forecast_at"]),
                    "event": r["match_key"], "forecast_at": r["forecast_at"], "settled_at": r.get("settled_at"),
                    "book": r.get("book"), "forecast_id": r.get("forecast_id")})
    return sorted(out, key=lambda r: (r["day"], r["forecast_at"]))


def rows_from_results(obj, league):
    """A results ledger snapshot (GET /api/sports/results, ui-snap-1) -> decided sm-1 pick'em rows, one per
    (event, player, stat, line, platform) = the latest forecast. Used only as a later forward check."""
    last = {}
    for r in obj.get("rows") or ():
        if r.get("model_version") != "sm-1" or r.get("status") != "settled" or r.get("outcome") not in ("over", "under"):
            continue
        if r.get("platform") not in ("prizepicks", "underdog") or r.get("p_over") is None or r.get("p_under") is None:
            continue
        k = (r["event_id"], r.get("player_id"), r["stat"], r["line"], r["platform"])
        if k not in last or r["forecast_at"] > last[k]["forecast_at"]:
            last[k] = r
    out = []
    for r in last.values():
        ps = cond_over(r["p_over"], r["p_under"])
        if ps is None:
            continue
        out.append({"league": league, "stat": r["stat"], "ps": ps, "hit": 1.0 if r["outcome"] == "over" else 0.0,
                    "day": game_day(r["forecast_at"]), "event": r["event_id"], "forecast_at": r["forecast_at"],
                    "settled_at": r.get("settled_at"), "book": r["platform"], "forecast_id": r.get("forecast_id")})
    return sorted(out, key=lambda r: (r["day"], r["forecast_at"]))


def _fit(X, y, lam, prior):
    """Penalised logistic regression (Newton). lam: one number or one penalty per parameter."""
    import numpy as np
    X, y = np.asarray(X, float), np.asarray(y, float)
    t0 = np.asarray(prior, float)
    L = np.broadcast_to(np.asarray(lam, float), t0.shape).copy()
    th = t0.copy()
    for _ in range(100):
        p = 1 / (1 + np.exp(-(X @ th)))
        g = X.T @ (p - y) + L * (th - t0)
        H = (X * (p * (1 - p))[:, None]).T @ X + np.diag(L)
        step = np.linalg.solve(H, g)
        th = th - step
        if np.max(np.abs(step)) < 1e-10:
            break
    th = np.clip(th, -P_MAX, P_MAX)
    return [float(x) for x in th]


# Intercept (logit shift) modes. "zero" (DEFAULT, coordinator review 2026-11-05): no shift - the calibrated chance
# only shrinks toward 50%, so the printed side is always the model's side (a flip against the projected mean cannot
# come from calibration). "free": unpenalised shift. "games": the shift penalised as if the data were worth one row
# per GAME (outcomes inside a game move together): prior sd ICPT_SD on the logit in game units, i.e. penalty
# (rows / games) / ICPT_SD^2. A shift is served only when it beats "zero" on held-out log loss with the
# game-clustered 95% interval of the difference entirely below 0 (ICPT_RULE).
ICPT_MODES = ("zero", "games", "free")
ICPT_SD = 0.25
ZERO_LAM = 1e12
ICPT_RULE = ("intercept served only if its held-out log loss beats intercept-0 with the game-clustered 95% interval "
             "of the difference entirely below 0; else intercept 0")


def _icpt_lam(icpt, rows, base):
    if icpt == "zero":
        return ZERO_LAM
    if icpt == "games":
        games = max(1, len({r["event"] for r in rows}))
        return max(base, (len(rows) / games) / ICPT_SD ** 2)
    return base


def fit(rows, spec, stat_lam, icpt="zero"):
    """-> {"spec", "theta", "stats": {stat: theta}, "n", "n_stat": {stat: n}, "intercept"} on these rows."""
    X = [features(spec, _logit(r["ps"])) for r in rows]
    y = [r["hit"] for r in rows]
    k = len(X[0])
    theta = _fit(X, y, [_icpt_lam(icpt, rows, LAM_LEAGUE)] + [LAM_LEAGUE] * (k - 1), [0.0] * k)
    if icpt == "zero":
        theta[0] = 0.0
    stats, n_stat = {}, {}
    if stat_lam is not None:
        by = {}
        for r, x in zip(rows, X):
            by.setdefault(r["stat"], []).append((r, x))
        for st, xs in sorted(by.items()):
            n_stat[st] = len(xs)
            if len(xs) >= MIN_STAT:
                srows = [a for a, _ in xs]
                t = _fit([b for _, b in xs], [a["hit"] for a in srows],
                         [_icpt_lam(icpt, srows, stat_lam)] + [stat_lam] * (k - 1), theta)
                if icpt == "zero":
                    t[0] = 0.0
                stats[st] = t
    return {"spec": spec, "theta": [round(x, 6) for x in theta],
            "stats": {k: [round(x, 6) for x in v] for k, v in stats.items()}, "n": len(rows), "n_stat": n_stat,
            "stat_lam": stat_lam, "intercept": icpt}


def predict(model, rows):
    return [q_of(model["spec"], model["stats"].get(r["stat"]) or model["theta"], r["ps"]) for r in rows]


def _ll(p, y):
    import numpy as np
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    y = np.asarray(y, float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def choose_spec(fit_rows, inner_days=INNER_DAYS, icpt="zero"):
    """Spec choice on the fit window ONLY: fit on its earlier game-days, score on its last inner_days. -> (best key,
    {key: inner log loss})."""
    import numpy as np
    days = sorted({r["day"] for r in fit_rows})
    cut = days[-inner_days] if len(days) > inner_days else days[-1]
    a = [r for r in fit_rows if r["day"] < cut]
    b = [r for r in fit_rows if r["day"] >= cut]
    scores = {}
    for spec in SPECS:
        for lam in STAT_LAMS:
            m = fit(a, spec, lam, icpt)
            scores[f"{spec}|{lam}"] = float(np.mean(_ll(predict(m, b), [r["hit"] for r in b])))
    best = min(scores, key=lambda k: (round(scores[k], 6), _complexity(k)))
    return best, scores, {"inner_fit_days": sorted({r["day"] for r in a}), "inner_eval_days": sorted({r["day"] for r in b}),
                          "inner_fit_n": len(a), "inner_eval_n": len(b)}


def _complexity(key):
    spec, lam = key.split("|")
    return (0 if spec == "platt" else 1) + (0 if lam == "None" else 2 + 1 / float(lam))


def _spec_of(key):
    spec, lam = key.split("|")
    return spec, (None if lam == "None" else float(lam))


# ================================================================================================ reporting
def _boot(x, groups, b=2000, seed=20261105):
    """Game-clustered bootstrap of a mean -> (mean, lo, hi)."""
    import numpy as np
    x = np.asarray(x, float)
    if len(x) == 0:
        return None, None, None
    _, inv = np.unique(np.asarray(groups, dtype=object).astype(str), return_inverse=True)
    G = inv.max() + 1
    s = np.bincount(inv, weights=x, minlength=G)
    n = np.bincount(inv, minlength=G).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, G, size=(b, G))
    bs = s[idx].sum(axis=1) / n[idx].sum(axis=1)
    lo, hi = np.quantile(bs, [0.025, 0.975])
    return float(x.mean()), float(lo), float(hi)


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def reliability(p_over, rows):
    """Stated vs hit per 5-point bin of the CHOSEN side's chance (side = the more likely one), Over and Under
    separately and together, game-clustered 95% intervals on the hit rate."""
    import numpy as np
    p = np.asarray(p_over, float)
    y = np.array([r["hit"] for r in rows])
    ev = np.array([r["event"] for r in rows], dtype=object)
    side = np.where(p >= 0.5, "over", "under")
    pch = np.maximum(p, 1 - p)
    win = np.where(p >= 0.5, y, 1 - y)
    out = {}
    for nm, m in (("all", np.ones(len(p), bool)), ("over", side == "over"), ("under", side == "under")):
        bins = []
        for lo in BINS:
            hi = lo + 0.05
            mm = m & (pch >= lo - 1e-12) & ((pch < hi - 1e-12) if lo < 0.949 else (pch <= 1.0))
            if not mm.sum():
                continue
            h, a, b = _boot(win[mm], ev[mm])
            bins.append({"bin": f"{int(round(lo * 100))}-{int(round(hi * 100))}", "n": int(mm.sum()),
                         "stated": _r(pch[mm].mean()), "hit": _r(h), "ci": [_r(a), _r(b)]})
        h, a, b = _boot(win[m], ev[m]) if m.sum() else (None, None, None)
        out[nm] = {"n": int(m.sum()), "stated": _r(pch[m].mean()) if m.sum() else None, "hit": _r(h),
                   "ci": [_r(a), _r(b)], "ge60": int((pch[m] >= 0.6 - 1e-12).sum()), "bins": bins}
    return out


def scores(p_over, rows):
    import numpy as np
    p = np.asarray(p_over, float)
    y = np.array([r["hit"] for r in rows])
    return {"n": len(rows), "games": len({r["event"] for r in rows}), "log_loss": _r(np.mean(_ll(p, y)), 5),
            "brier": _r(np.mean((p - y) ** 2), 5), "over_rate": _r(y.mean()), "share_over_side": _r(np.mean(p >= 0.5))}


def letters(p_over):
    from . import grade_policy
    c = {}
    for p in p_over:
        b = grade_policy.band(max(p, 1 - p))
        c[b] = c.get(b, 0) + 1
    return {k: c.get(k, 0) for k in ("A", "B", "C", "Lean")}


def verdict(raw, cal, rows):
    """The pc-1 pass rule (module doc) on the held-out window -> {"pass", "checks", "why"}."""
    import numpy as np
    y = np.array([r["hit"] for r in rows])
    ev = [r["event"] for r in rows]
    d, lo, hi = _boot(_ll(cal, y) - _ll(raw, y), ev)
    rel = reliability(cal, rows)
    checks = {"log_loss_better": hi is not None and hi < 0,
              "log_loss_diff": [_r(d, 5), _r(lo, 5), _r(hi, 5)]}
    a = rel["all"]
    checks["in_the_large"] = a["stated"] is not None and abs(a["stated"] - a["hit"]) <= SIDE_TOL
    for s in ("over", "under"):
        x = rel[s]
        if not x["n"]:
            checks[f"{s}_side"] = True
            continue
        gap = abs(x["stated"] - x["hit"])
        checks[f"{s}_side"] = gap <= SIDE_TOL or (x["ci"][0] is not None and x["ci"][0] <= x["stated"] <= x["ci"][1])
    ok = all(v for k, v in checks.items() if k != "log_loss_diff")
    why = [k for k, v in checks.items() if k != "log_loss_diff" and not v]
    return {"pass": bool(ok), "checks": checks, "why": "all checks pass" if ok else "failed: " + ", ".join(why)}


def evaluate_league(rows, test_from, icpt="zero"):
    """Chronological evaluation of one league for one intercept mode -> (report, chosen key). Fit window = game-days
    before test_from."""
    fit_rows = [r for r in rows if r["day"] < test_from]
    test_rows = [r for r in rows if r["day"] >= test_from]
    if not fit_rows or not test_rows:
        raise ValueError("need settled rows on both sides of test_from")
    key, inner, inner_meta = choose_spec(fit_rows, icpt=icpt)
    spec, lam = _spec_of(key)
    m = fit(fit_rows, spec, lam, icpt)
    raw = [r["ps"] for r in test_rows]
    cal = predict(m, test_rows)
    rep = {"fit_window": [fit_rows[0]["day"], fit_rows[-1]["day"]], "test_window": [test_rows[0]["day"], test_rows[-1]["day"]],
           "fit_days": sorted({r["day"] for r in fit_rows}), "test_days": sorted({r["day"] for r in test_rows}),
           "n_fit": len(fit_rows), "n_test": len(test_rows),
           "spec_choice": {"chosen": key, "inner_log_loss": {k: _r(v, 5) for k, v in inner.items()}, **inner_meta},
           "intercept": icpt,
           "fitted_on_fit_window": {k: m[k] for k in ("spec", "theta", "stats", "n", "n_stat", "stat_lam", "intercept")},
           "raw": {"scores": scores(raw, test_rows), "reliability": reliability(raw, test_rows), "letters": letters(raw)},
           "calibrated": {"scores": scores(cal, test_rows), "reliability": reliability(cal, test_rows),
                          "letters": letters(cal)},
           "by_stat": {}}
    for st in sorted({r["stat"] for r in test_rows}):
        ix = [i for i, r in enumerate(test_rows) if r["stat"] == st]
        rr = [test_rows[i] for i in ix]
        rep["by_stat"][st] = {"raw": scores([raw[i] for i in ix], rr), "calibrated": scores([cal[i] for i in ix], rr),
                              "raw_rel": {k: {x: v[x] for x in ("n", "stated", "hit", "ci")} for k, v in
                                          reliability([raw[i] for i in ix], rr).items()},
                              "cal_rel": {k: {x: v[x] for x in ("n", "stated", "hit", "ci")} for k, v in
                                          reliability([cal[i] for i in ix], rr).items()}}
    rep["verdict"] = verdict(raw, cal, test_rows)
    rep["side_flips"] = sum(1 for a, b in zip(raw, cal) if (a >= 0.5) != (b >= 0.5))
    rep["_cal"] = cal
    return rep, key


def variant_summary(rep, test_rows, base_cal=None):
    """Compact per-intercept-mode summary for the comparison table (+ log-loss difference vs intercept 0)."""
    import numpy as np
    c = rep["calibrated"]
    out = {"intercept": rep["intercept"], "spec": rep["spec_choice"]["chosen"],
           "theta_fit_window": rep["fitted_on_fit_window"]["theta"],
           "log_loss": c["scores"]["log_loss"], "brier": c["scores"]["brier"],
           "log_loss_minus_raw": rep["verdict"]["checks"]["log_loss_diff"],
           "share_over_side": c["scores"]["share_over_side"], "side_flips_vs_raw": rep["side_flips"],
           "ge60": c["reliability"]["all"]["ge60"], "letters": c["letters"], "verdict": rep["verdict"]["pass"],
           "why": rep["verdict"]["why"]}
    for s in ("all", "over", "under"):
        r = c["reliability"][s]
        out[s] = {"n": r["n"], "stated": r["stated"], "hit": r["hit"], "ci": r["ci"]}
    if base_cal is not None:
        y = np.array([r["hit"] for r in test_rows])
        d, lo, hi = _boot(_ll(rep["_cal"], y) - _ll(base_cal, y), [r["event"] for r in test_rows])
        out["log_loss_minus_intercept0"] = [_r(d, 5), _r(lo, 5), _r(hi, 5)]
    return out


def build(exports, test_from, forward=None):
    """exports {league: export obj} -> (artifact obj, report). forward {league: rows} = a later window scored with the
    SERVED parameters (fit on every export row)."""
    leagues, report = {}, {"version": VERSION, "test_from": test_from, "leagues": {}}
    for lg in LEAGUES:
        if lg not in exports:
            continue
        rows = rows_from_export(exports[lg])
        test_rows = [r for r in rows if r["day"] >= test_from]
        runs = {m: evaluate_league(rows, test_from, m) for m in ICPT_MODES}
        base = runs["zero"][0]["_cal"]
        variants = {m: variant_summary(runs[m][0], test_rows, None if m == "zero" else base) for m in ICPT_MODES}
        chosen = "zero"                                 # ICPT_RULE: a shift must beat intercept 0 clearly
        for m in ("games", "free"):
            hi = variants[m]["log_loss_minus_intercept0"][2]
            if hi is not None and hi < 0 and (chosen == "zero" or variants[m]["log_loss"] < variants[chosen]["log_loss"]):
                chosen = m
        rep, key = runs[chosen]
        for r_ in runs.values():
            r_[0].pop("_cal", None)
        rep["intercept_variants"] = variants
        rep["intercept_rule"] = ICPT_RULE
        rep["intercept_chosen"] = chosen
        spec, lam = _spec_of(key)
        served = fit(rows, spec, lam, chosen)
        rep["served"] = {k: served[k] for k in ("spec", "theta", "stats", "n", "n_stat", "stat_lam", "intercept")}
        rep["served"]["data_window"] = [rows[0]["day"], rows[-1]["day"]]
        if forward and forward.get(lg):
            fr = [r for r in forward[lg] if r["day"] > rows[-1]["day"]]
            if fr:
                fraw = [r["ps"] for r in fr]
                fcal = predict(served, fr)
                rep["forward"] = {"window": [fr[0]["day"], fr[-1]["day"]], "source": "results ledger snapshot",
                                  "raw": {"scores": scores(fraw, fr), "reliability": reliability(fraw, fr)},
                                  "calibrated": {"scores": scores(fcal, fr), "reliability": reliability(fcal, fr)},
                                  "verdict": verdict(fraw, fcal, fr)}
        report["leagues"][lg] = rep
        leagues[lg] = {"spec": served["spec"], "theta": served["theta"], "stats": served["stats"],
                       "intercept": chosen,
                       "letters": "contain-rule" if rep["verdict"]["pass"] else "held",
                       "stat_lam": lam, "n": served["n"], "data_window": rep["served"]["data_window"],
                       "held_out": {"fit_window": rep["fit_window"], "test_window": rep["test_window"],
                                    "n_fit": rep["n_fit"], "n_test": rep["n_test"], "pass": rep["verdict"]["pass"],
                                    "why": rep["verdict"]["why"],
                                    "log_loss": [rep["raw"]["scores"]["log_loss"], rep["calibrated"]["scores"]["log_loss"]],
                                    "brier": [rep["raw"]["scores"]["brier"], rep["calibrated"]["scores"]["brier"]]}}
    obj = {"version": VERSION, "rule": "owner decision 2026-11-05: printed chance = calibrated chance (chronological "
                                       "fit, held-out test); a failed league keeps letters held",
           "fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "test_from": test_from,
           "source": "gradecore-export-1 (last saved sm-1 forecast >= 30 min before start, settled)", "leagues": leagues}
    obj["sha"] = _sha(_serving(leagues))
    return validate(obj), report


def main(argv=None):
    import argparse
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    ap = argparse.ArgumentParser(prog="python -m sports.model.chance_cal")
    ap.add_argument("cmd", choices=["fit", "status"])
    ap.add_argument("--nba")
    ap.add_argument("--nfl")
    ap.add_argument("--forward-nba")
    ap.add_argument("--forward-nfl")
    ap.add_argument("--test-from", default="2026-11-01")
    ap.add_argument("--out")
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        obj, src = load(force=True)
        print(json.dumps({"version": VERSION, "enabled": enabled(), "source": src, "sha": (obj or {}).get("sha"),
                          "fitted_at": (obj or {}).get("fitted_at"),
                          "leagues": {lg: {k: p.get(k) for k in ("spec", "letters", "n", "data_window", "held_out")}
                                      for lg, p in ((obj or {}).get("leagues") or {}).items()}}, indent=1))
        return
    exports = {lg: json.loads(Path(p).read_text(encoding="utf-8")) for lg, p in (("nba", a.nba), ("nfl", a.nfl)) if p}
    forward = {}
    for lg, p in (("nba", a.forward_nba), ("nfl", a.forward_nfl)):
        if p:
            forward[lg] = rows_from_results(json.loads(Path(p).read_text(encoding="utf-8")), lg)
    obj, rep = build(exports, a.test_from, forward)
    out = Path(a.out) if a.out else weights_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    os.replace(tmp, out)
    if a.report:
        Path(a.report).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(out), "sha": obj["sha"],
                      "leagues": {lg: {"letters": p["letters"], "spec": p["spec"], "stat_lam": p["stat_lam"],
                                       "held_out": p["held_out"]} for lg, p in obj["leagues"].items()}}, indent=1))


if __name__ == "__main__":
    main()
