"""cal-cs2lol-1 shared code: unit loading, the registered candidates C0-C6, scores and match-clustered bootstrap.
REGISTER.md is the contract; nothing here may differ from it. Research only: no live module imports this file."""
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import norm

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from gradecore import calibrate as gcal  # noqa: E402

SEED = 20261105
B = 2000
MIN_FIT = 200
CLIP_Z = (0.001, 0.999)
SEG = {"cs2": {"dev": ("2026-10-18", "2026-10-27"), "conf": ("2026-10-28", "2026-11-04")},
       "lol": {"dev": ("2026-10-23", "2026-10-27"), "conf": ("2026-10-28", "2026-11-04")}}
PROPS = {"cs2": ("kills", "hs"), "lol": ("kills", "assists")}
CANDS = ("C1", "C2", "C3", "C4", "C5", "C6")
NAMES = {"C0": "live", "C1": "isotonic", "C2": "Platt", "C3": "spread inflation", "C4": "mean-bias", "C5": "gradecore/calibrate.py",
         "C6": "per-side spread"}


def ts(s):
    s = s.replace("Z", "+00:00")
    if "." in s:
        s = s.split(".")[0] + "+00:00"
    return datetime.fromisoformat(s)


# ------------------------------------------------------------------------------------------------------------ units
def load_cs2(data):
    d = json.loads((Path(data) / "cs2_p1.json").read_text(encoding="utf-8"))
    fc = {}
    fp = Path(data) / "cs2_fc.json"
    if fp.exists():
        fc = {int(k): v for k, v in json.loads(fp.read_text(encoding="utf-8"))["rows"].items()}
    out = []
    for r in d["rows"]:
        f = fc.get(r["forecast_id"]) or {}
        raw = None
        try:
            raw = (json.loads(f["probs_raw"]) or {}).get("p_more_no_push") if f.get("probs_raw") else None
        except (ValueError, TypeError):
            raw = None
        out.append({"sport": "cs2", "id": r["forecast_id"], "prop": r["prop"], "scope": r["scope"] or "12", "p": float(r["p"]),
                    "y": int(r["over"]), "cluster": r["match"], "start": ts(r["start"]), "day": r["start"][:10],
                    "line": r["line"], "lam": r["lam"], "actual": r["actual"], "version": r["version"], "book": r["book"],
                    "units": r["units"], "raw": raw, "source": f.get("shadow_source"), "odds_source": f.get("odds_source"),
                    "push_p": 0.0, "platform": r["book"], "sides": None, "logged": r["logged"]})
    return out


def load_lol(data):
    from lol.center.forecast import shown
    d = json.loads((Path(data) / "lol_units.json").read_text(encoding="utf-8"))["rows"]
    out = []
    for r in d:
        if r["st"] != "graded":
            continue
        po, pu, pp = float(r["p_over"] or 0), float(r["p_under"] or 0), float(r["p_push"] or 0)
        sh = shown(r)
        out.append({"sport": "lol", "id": r["id"], "prop": r["stat"], "scope": r["scope"], "p": po / max(po + pu, 1e-9),
                    "y": int(r["over_hit"]), "cluster": r["sid"], "start": ts(r["series_start"]), "day": r["series_start"][:10],
                    "line": r["line"], "lam": r["lambda"], "actual": r["actual"], "version": r["model_version"],
                    "book": r["platform"], "platform": r["platform"], "odds_type": r["odds_type"], "league": r["league"],
                    "role": r["role"], "best_of": r["best_of"], "push_p": pp, "sides": json.loads(r["sides"] or "{}"),
                    "nb_size": r["nb_size"], "live_side": sh["side"], "live_offered": sh["side_offered"], "live_grade": sh["grade_label"],
                    "live_printed": sh["p_side"], "units": r["units"], "hist_games": r["hist_games"]})
    return out


# ------------------------------------------------------------------------------------------------------- candidates
def _z(p):
    return norm.ppf(np.clip(np.asarray(p, float), *CLIP_Z))


def _ll(p, y):
    p = np.clip(np.asarray(p, float), *CLIP_Z)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def _fit_scalar(f, lo, hi):
    return float(minimize_scalar(f, bounds=(lo, hi), method="bounded", options={"xatol": 1e-5}).x)


def fit(cand, p, y):
    """-> a plain dict fit for one (sport, prop) group from training P(Over) p and outcomes y."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    if cand != "C5" and len(y) < MIN_FIT:
        return {"kind": "identity", "n": int(len(y))}
    z = _z(p)
    if cand == "C1":
        kx, ky = gcal.fit_isotonic(p, y)
        return {"kind": "isotonic", "n": int(len(y)), "x": kx, "y": [min(max(v, 0.01), 0.99) for v in ky]}
    if cand == "C2":
        ab = gcal.fit_platt(p, y)
        return {"kind": "identity", "n": int(len(y))} if ab is None else {"kind": "platt", "n": int(len(y)), "a": ab[0], "b": ab[1]}
    if cand == "C3":
        return {"kind": "spread", "n": int(len(y)), "k": _fit_scalar(lambda k: _ll(norm.cdf(z / k), y).mean(), 0.4, 3.0)}
    if cand == "C4":
        return {"kind": "bias", "n": int(len(y)), "d": _fit_scalar(lambda d: _ll(norm.cdf(z - d), y).mean(), -1.5, 1.5)}
    if cand == "C5":
        over = p >= 0.5
        rows = [{"market": "m", "side": "Over" if o else "Under", "p": float(pi if o else 1 - pi),
                 "status": "win" if (yi == 1) == o else "loss"} for pi, yi, o in zip(p, y, over)]
        return {"kind": "gradecore", "n": int(len(y)), "doc": gcal.build(rows, "x")}
    if cand == "C6":
        out = {"kind": "per_side_spread", "n": int(len(y))}
        for side, m in (("Over", p >= 0.5), ("Under", p < 0.5)):
            zs = np.abs(z[m])
            ys = np.where(p[m] >= 0.5, y[m], 1 - y[m])
            out["k_" + side] = (1.0 if m.sum() < MIN_FIT // 2 else
                                _fit_scalar(lambda k: _ll(norm.cdf(zs / k), ys).mean(), 0.4, 3.0))
            out["n_" + side] = int(m.sum())
        return out
    raise ValueError(cand)


def apply(art, p):
    """P(Over) after the fit (vectorised)."""
    p = np.asarray(p, float)
    k = art["kind"]
    if k == "identity":
        return p.copy()
    if k == "isotonic":
        return np.clip(np.interp(p, art["x"], art["y"]), 0.01, 0.99)
    if k == "platt":
        return np.array([gcal.apply({"kind": "platt", "a": art["a"], "b": art["b"]}, v) for v in p])
    z = _z(p)
    if k == "spread":
        return norm.cdf(z / art["k"])
    if k == "bias":
        return norm.cdf(z - art["d"])
    if k == "gradecore":
        out = []
        for v in p:
            o = v >= 0.5
            ps = gcal.apply(gcal.lookup(art["doc"], "m", "Over" if o else "Under"), v if o else 1 - v)
            out.append(ps if o else 1 - ps)
        return np.array(out)
    if k == "per_side_spread":
        o = p >= 0.5
        ks = np.where(o, art["k_Over"], art["k_Under"])
        ps = norm.cdf(np.abs(z) / ks)
        return np.where(o, ps, 1 - ps)
    raise ValueError(k)


# ------------------------------------------------------------------------------------------------- rolling origin
def days(a, b):
    d0, d1 = datetime.fromisoformat(a), datetime.fromisoformat(b)
    while d0 <= d1:
        yield d0.strftime("%Y-%m-%d")
        d0 += timedelta(days=1)


def rolling(units, sport, seg, cands=CANDS):
    """-> test units of the segment, each with u['pc'][cand] = the candidate's P(Over) from a fit on earlier units only,
    and the fits used per day."""
    a, b = SEG[sport][seg]
    tested, fits = [], {}
    for day in days(a, b):
        test = [u for u in units if u["day"] == day]
        if not test:
            continue
        cut = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) - timedelta(hours=6)
        train = [u for u in units if u["start"] < cut]
        fits[day] = {"train_n": len(train)}
        for c in cands:
            for prop in set(u["prop"] for u in test):
                tr = [u for u in train if u["prop"] == prop]
                te = [u for u in test if u["prop"] == prop]
                art = fit(c, [u["p"] for u in tr], [u["y"] for u in tr]) if (prop in PROPS[sport] and tr) else {"kind": "identity", "n": len(tr)}
                fits[day].setdefault(c, {})[prop] = {k: v for k, v in art.items() if k not in ("doc", "x", "y")} | (
                    {"tables": {k: {kk: vv for kk, vv in t.items() if kk not in ("x", "y", "data_hash")} for k, t in art["doc"]["tables"].items()}}
                    if art["kind"] == "gradecore" else {}) | ({"knots": len(art["x"])} if art["kind"] == "isotonic" else {})
                pc = apply(art, [u["p"] for u in te])
                for u, v in zip(te, pc):
                    u.setdefault("pc", {})[c] = float(v)
        for u in test:
            u.setdefault("pc", {})["C0"] = u["p"]
        tested += test
    return tested, fits


# -------------------------------------------------------------------------------------------------------- scoring
def boot(units, fn, b=B, seed=SEED):
    """Match-clustered percentile 95% interval of sum(fn(u)) / n over units."""
    g = defaultdict(lambda: [0.0, 0])
    for u in units:
        g[u["cluster"]][0] += fn(u)
        g[u["cluster"]][1] += 1
    s = np.array([v[0] for v in g.values()])
    n = np.array([v[1] for v in g.values()])
    if len(s) < 5:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(s), size=(b, len(s)))
    st = s[idx].sum(1) / n[idx].sum(1)
    return [round(float(np.percentile(st, 2.5)), 5), round(float(np.percentile(st, 97.5)), 5)]


def ll1(p, y):
    p = min(max(p, CLIP_Z[0]), CLIP_Z[1])
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def side_of(p):
    return "Over" if p >= 0.5 else "Under"


def fav(p):
    return max(p, 1 - p)


def fav_hit(p, y):
    return y if p >= 0.5 else 1 - y


def paired(units, c):
    if not units:
        return {"n": 0}
    gl = lambda u: ll1(u["pc"]["C0"], u["y"]) - ll1(u["pc"][c], u["y"])
    gb = lambda u: (u["pc"]["C0"] - u["y"]) ** 2 - (u["pc"][c] - u["y"]) ** 2
    n = len(units)
    return {"n": n, "matches": len({u["cluster"] for u in units}),
            "logloss_live": round(sum(ll1(u["pc"]["C0"], u["y"]) for u in units) / n, 5),
            "logloss_cand": round(sum(ll1(u["pc"][c], u["y"]) for u in units) / n, 5),
            "logloss_gain": round(sum(gl(u) for u in units) / n, 5), "logloss_gain_ci": boot(units, gl),
            "brier_live": round(sum((u["pc"]["C0"] - u["y"]) ** 2 for u in units) / n, 5),
            "brier_cand": round(sum((u["pc"][c] - u["y"]) ** 2 for u in units) / n, 5),
            "brier_gain": round(sum(gb(u) for u in units) / n, 5), "brier_gain_ci": boot(units, gb)}


BINS = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.75), (0.75, 0.80), (0.80, 1.01)]


def reliability(units, c):
    out = []
    for lo, hi in BINS:
        g = [u for u in units if lo <= fav(u["pc"][c]) < hi]
        if not g:
            out.append({"bin": f"{int(lo * 100)}-{min(int(hi * 100), 100)}", "n": 0})
            continue
        out.append({"bin": f"{int(lo * 100)}-{min(int(hi * 100), 100)}", "n": len(g), "matches": len({u["cluster"] for u in g}),
                    "stated": round(sum(fav(u["pc"][c]) for u in g) / len(g), 4),
                    "hit": round(sum(fav_hit(u["pc"][c], u["y"]) for u in g) / len(g), 4),
                    "hit_ci": boot(g, lambda u: fav_hit(u["pc"][c], u["y"]))})
    return out


def in_large(units, c):
    n = len(units)
    st = sum(fav(u["pc"][c]) for u in units) / n
    h = sum(fav_hit(u["pc"][c], u["y"]) for u in units) / n
    return {"n": n, "stated": round(st, 4), "hit": round(h, 4), "gap": round(st - h, 4)}


def balance(units, c):
    n = len(units)
    share = sum(1 for u in units if u["pc"][c] >= 0.5) / n
    rate = sum(u["y"] for u in units) / n
    return {"share_over": round(share, 4), "over_rate": round(rate, 4), "diff": round(share - rate, 4), "ok": abs(share - rate) <= 0.15}


# ------------------------------------------------------------------------------------------------- results cohort
def lol_offered(u, side):
    from lol.center.forecast import side_offered
    return side_offered(u["platform"], u["sides"], side.lower()) is True


def results_picks(units, c):
    """The member Results rule applied to the arm's own chance. CS2: favoured side >= 57%, Map 1 held off (live rule).
    LoL: every graded unit whose favoured side is offered; stated = the unconditional printed chance (push mass kept)."""
    out = []
    for u in units:
        p = u["pc"][c]
        s = side_of(p)
        if u["sport"] == "cs2":
            if u["scope"] == "1" or fav(p) < 0.57:
                continue
            stated = fav(p)
        else:
            if c == "C0":                                  # live: the exact shown() side / offered flag / printed chance
                if u["live_offered"] != 1:
                    continue
                s = u["live_side"].title()
                stated = u["live_printed"]
            else:
                if not lol_offered(u, s):
                    continue
                stated = fav(p) * (1 - u["push_p"])
        out.append({"cluster": u["cluster"], "stated": stated, "hit": u["y"] if s == "Over" else 1 - u["y"], "side": s,
                    "prop": u["prop"], "letter": letter(u["sport"], stated), "u": u})
    return out


def picks_summary(ps):
    if not ps:
        return {"n": 0}
    n = len(ps)
    st = sum(x["stated"] for x in ps) / n
    h = sum(x["hit"] for x in ps) / n
    return {"n": n, "matches": len({x["cluster"] for x in ps}), "stated": round(st, 4), "hit": round(h, 4),
            "hit_ci": boot(ps, lambda x: x["hit"]), "hit_minus_stated_ci": boot(ps, lambda x: x["hit"] - x["stated"]),
            "over_share": round(sum(1 for x in ps if x["side"] == "Over") / n, 4)}


def letter(sport, p):
    if sport == "cs2":
        from grader import grade_from_p
        return grade_from_p(p)
    from lol.center.forecast import grade_and_units
    g = grade_and_units(p)[0]
    return g


def letters(units, c):
    from collections import Counter
    out = Counter()
    for u in units:
        out[letter(u["sport"], fav(u["pc"][c]) * (1 - u["push_p"]))] += 1
    return dict(out)
