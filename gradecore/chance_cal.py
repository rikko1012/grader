"""ec-1: the printed chance on every CS2 and LoL prop = the rate such chances have actually hit (owner decision 2026-11-05).

Owner: "we're grading every prop on the board these props will never hit 60% printed chance" -> CALIBRATE. This supersedes
the old "never lower percentages / never turn B into Lean" rule for CS2 and LoL. Still forbidden: an untested cut, raising
stakes to compensate, forcing an Over/Under balance. The projection models are untouched (CS2 tag 3007c59b74 stays frozen):
this is one layer on the printed probability, applied after the forecast is logged.

The layer (one per game, per side, per stat where the data supports it):

    p      = the model's own chance of the side it picks (no push), >= 50% by construction
    c(p)   = fitted on settled forecasts of that game: (stat, side) when it has >= MIN_N settled forecasts, else (all
             stats, side); identity below MIN_N. Served by default with INTERCEPT 0: logit(c) = a * logit(p), 0 <= a <= 1 (a
             slope-only shrink toward 50% - the side can never flip). A variant with an intercept (shrunk, free =
             calibrate.fit) is served only when it beats intercept 0 on held-out log loss with a match-clustered 95% interval
             entirely below zero (select(); coordinator rule 2026-11-05 after an NFL intercept flipped 641 sides to Under).
    shown  = max(FLOOR, c(p))   - the side is the model's (never flipped by this layer); a side whose calibrated chance is
             under 50% prints 50.0%. The other side prints 1 - shown. Pushes keep the model's push mass.

Letters come from `shown` through the existing thresholds (55 / 57 / 63 / 68 / 74 / 80); under 55% = "Lean Over/Under".
Stakes follow the calibrated letter through the existing unit rules and are never raised above the model's own stake.

The held-out test (evaluate()): chronological, no leakage. For each UTC day of the held-out window (the most recent
TEST_DAYS days) the tables are fitted ONLY on forecasts whose match started >= SETTLE_H hours before that day began, then
applied to that day's forecasts (the procedure that is served: tables refitted on everything older). A game passes when,
on its held-out forecasts (ct-3's calibration rule, gradecore/containment.py):
    n >= MIN_TEST decided; |mean shown - hit| <= MAX_GAP overall; each side (Over, Under) >= MIN_SIDE decided with
    |mean shown - hit| <= MAX_GAP; and log loss of the shown chance <= log loss of the raw chance.
A game that fails keeps its printed chance calibrated but its letters are held (letter_containment reads held()).

Artifact: gradecore/chance_cal_ec1.json (committed; written only by `python -m gradecore.chance_cal fit`). Serving reads
it in pure Python (no numpy on the request path). Switch: SCOUT_CAL_CHANCE=1 (the running server sets it by default -
server.DEFAULT_ON; Render "0" turns it off). Off = every surface prints the model's raw chance exactly as before.

This module imports no sport code. Fitting / evaluation import numpy lazily.
"""
import hashlib
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "ec-1"
FLAG = "SCOUT_CAL_CHANCE"
GAMES = ("cs2", "lol")
SIDES = ("over", "under")
FLOOR = 0.5
CEIL = 0.99
MIN_N = 300                      # = calibrate.MIN_N: a (stat, side) table needs this many settled forecasts
TEST_DAYS = 7
SETTLE_H = 6                     # a forecast enters a fit only when its match started >= 6 h before the fit's cutoff
MIN_TEST, MIN_SIDE, MAX_GAP = 300, 60, 0.025          # ct-3 (gradecore/containment.py)
LETTER_CUTS = ((0.80, "A+"), (0.74, "A"), (0.68, "A-"), (0.63, "B+"), (0.57, "B"), (0.55, "B-"))
PATH = Path(__file__).resolve().parent / "chance_cal_ec1.json"
_ON = {"1", "true", "yes", "on"}
CHECK_S = 30.0
EPS = 1e-6

_cache = {"t": 0.0, "mt": None, "obj": None}


# ============================================================================================================ serving
def enabled():
    return os.environ.get(FLAG, "").strip().lower() in _ON


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def _fit_ok(f):
    if not isinstance(f, dict):
        return False
    k = f.get("kind")
    if k == "identity":
        return True
    if k == "scale":
        return isinstance(f.get("a"), (int, float)) and math.isfinite(f["a"]) and 0 <= f["a"] <= 1
    if k == "platt":
        return all(isinstance(f.get(x), (int, float)) and math.isfinite(f[x]) for x in ("a", "b")) and f["a"] >= 0
    if k == "isotonic":
        x, y = f.get("x"), f.get("y")
        return (isinstance(x, list) and isinstance(y, list) and len(x) == len(y) >= 1
                and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in x + y)
                and all(a <= b for a, b in zip(x, x[1:])) and all(a <= b for a, b in zip(y, y[1:])))
    return False


def validate(obj):
    """-> obj or raise ValueError."""
    if not isinstance(obj, dict) or obj.get("version") != VERSION or not isinstance(obj.get("games"), dict):
        raise ValueError("not an ec-1 artifact")
    for g, d in obj["games"].items():
        if g not in GAMES or not isinstance(d, dict) or d.get("letters") not in ("live", "held"):
            raise ValueError(f"bad game block {g!r}")
        for k, f in (d.get("tables") or {}).items():
            if not isinstance(k, str) or k.count("|") != 1 or k.split("|")[1] not in SIDES or not _fit_ok(f):
                raise ValueError(f"{g}: bad table {k!r}")
    if obj.get("sha") != _sha(obj["games"]):
        raise ValueError("sha mismatch")
    return obj


def load(force=False, path=None):
    """The committed artifact (validated), or None. Cached; the file's mtime is re-checked every CHECK_S seconds."""
    p = Path(path) if path else PATH
    now = time.time()
    if not force and path is None and now - _cache["t"] < CHECK_S and _cache["mt"] is not None:
        return _cache["obj"]
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        mt = None
    if path is None and not force and mt == _cache["mt"] and _cache["mt"] is not None:
        _cache["t"] = now
        return _cache["obj"]
    obj = None
    if mt is not None:
        try:
            obj = validate(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            obj = None
    if path is None:
        _cache.update(t=now, mt=mt if mt is not None else 0, obj=obj)
    return obj


def reset_cache():
    _cache.update(t=0.0, mt=None, obj=None)


def tag(obj=None):
    obj = obj if obj is not None else load()
    return f"{VERSION}:{obj['sha']}" if obj else None


def _sig(x):
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def apply_fit(f, p):
    """gradecore.calibrate.apply in pure Python (same result; no numpy on the request path)."""
    kind = (f or {}).get("kind", "identity")
    p = float(p)
    if kind == "identity":
        return p
    if kind == "scale":                              # intercept 0: logit(c) = a * logit(p), 0 <= a <= 1 (shrink toward 50%)
        c = min(max(p, EPS), 1 - EPS)
        return min(max(_sig(f["a"] * math.log(c / (1 - c))), 0.01), 0.99)
    if kind == "platt":
        c = min(max(p, EPS), 1 - EPS)
        v = _sig(f["a"] * math.log(c / (1 - c)) + f["b"])
        return min(max(v, 0.01), 0.99)
    if kind == "isotonic":
        xs, ys = f["x"], f["y"]
        if p <= xs[0]:
            return ys[0]
        if p >= xs[-1]:
            return ys[-1]
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= p:
                lo = mid
            else:
                hi = mid
        x0, x1 = xs[lo], xs[hi]
        return ys[lo] if x1 == x0 else ys[lo] + (ys[hi] - ys[lo]) * (p - x0) / (x1 - x0)
    raise ValueError(f"unknown calibration kind {kind!r}")


def table_for(tables, stat, side):
    """(key, fit): the (stat, side) table when it is fitted, else the side's pooled table, else identity."""
    for k in (f"{stat}|{side}", f"*|{side}"):
        f = (tables or {}).get(k)
        if f and f.get("kind") != "identity":
            return k, f
    return None, {"kind": "identity"}


def side_chance(game, stat, side, p, obj=None):
    """The printed chance of `side` (over / under) given the model's own no-push chance p of that side, or None when the
    layer is off / has no artifact / does not know the game (callers then print p unchanged)."""
    obj = obj if obj is not None else (load() if enabled() else None)
    if not obj or p is None:
        return None
    d = (obj.get("games") or {}).get(game)
    if not d or side not in SIDES:
        return None
    _, f = table_for(d.get("tables"), stat, side)
    return round(min(max(apply_fit(f, p), FLOOR), CEIL), 6)


def pair(game, stat, p_over, side=None, obj=None):
    """(shown Over, shown Under) - no push - for a prop whose model P(Over | no push) is p_over; side = the model's side
    (default: the larger chance). None when the layer is off."""
    if p_over is None:
        return None
    side = side or ("over" if p_over >= 0.5 else "under")
    ps = p_over if side == "over" else 1 - p_over
    c = side_chance(game, stat, side, ps, obj)
    if c is None:
        return None
    return (c, 1 - c) if side == "over" else (1 - c, c)


def letter(p):
    for cut, g in LETTER_CUTS:
        if p is not None and p >= cut:
            return g
    return None


def held(game, obj=None):
    """True when the layer is on and the game's held-out test failed: its letters are held (chance still calibrated)."""
    if not enabled():
        return False
    obj = obj if obj is not None else load()
    d = ((obj or {}).get("games") or {}).get(game)
    return bool(d) and d.get("letters") == "held"


def status():
    """Admin view (no numpy)."""
    obj = load()
    return {"version": VERSION, "flag": {FLAG: os.environ.get(FLAG), "on": enabled()}, "tag": tag(obj),
            "fitted_at": (obj or {}).get("fitted_at"),
            "games": {g: {"letters": d.get("letters"), "why": d.get("why"), "test": (d.get("test") or {}).get("summary"),
                          "tables": {k: f.get("kind") for k, f in (d.get("tables") or {}).items()}}
                      for g, d in ((obj or {}).get("games") or {}).items()}}


# ============================================================================================================ rows
def rows_from_cs2_p1(export):
    """CS2 settled forecasts from the learning cycle's P1 export (learning.p1_units_export: the latest BOARD forecast of
    each unit >= 30 min before the start, on a standard offered line; p = the model's P(Over | no push) as logged)."""
    out = []
    for r in (export or {}).get("rows") or []:
        p, over = r.get("p"), r.get("over")
        if p is None or over not in (0, 1) or r.get("prop") not in ("kills", "hs") or not r.get("start"):
            continue
        p = float(p)
        side = "over" if p >= 0.5 else "under"
        out.append({"game": "cs2", "stat": r["prop"], "side": side, "p": p if side == "over" else 1 - p,
                    "win": float(over if side == "over" else 1 - over), "match": str(r.get("match")), "t": str(r["start"]),
                    "book": r.get("book"), "scope": r.get("scope") or "12", "version": r.get("version")})
    return out


LOL_ROWS_SQL = (
    "SELECT f.id, f.stat, f.platform, f.scope, f.odds_type, f.sid, f.series_start, f.forecast_at, f.p_over, f.p_under, "
    "f.model_version, s.status AS s_status, s.over_hit AS s_over_hit, s.push AS s_push "
    "FROM lol_forecasts f "
    "JOIN (SELECT platform, market_key, line, MAX(id) AS id FROM lol_forecasts WHERE forecast_at < series_start "
    "GROUP BY platform, market_key, line) z ON z.id = f.id "
    "JOIN (SELECT unit_key, MAX(version) AS v FROM lol_settlements GROUP BY unit_key) lv ON "
    "lv.unit_key = f.platform || '|' || f.market_key || '|' || f.line "
    "JOIN lol_settlements s ON s.unit_key = lv.unit_key AND s.version = lv.v")


def rows_from_lol(recs):
    """LoL settled forecasts: the latest pre-match forecast per (platform, market_key, line) with the latest settlement
    (LOL_ROWS_SQL, the lc-1 / learning.json population). p = the model's RAW chance (p_over / p_under, never a calibrated
    one); graded, no push."""
    out = []
    for r in recs or []:
        if r.get("s_status") != "graded" or r.get("s_push") or r.get("s_over_hit") not in (0, 1):
            continue
        try:
            po, pu = float(r["p_over"]), float(r["p_under"])
        except (KeyError, TypeError, ValueError):
            continue
        if po + pu <= 0 or not r.get("series_start"):
            continue
        q = po / (po + pu)
        side = "over" if po >= pu else "under"
        h = int(r["s_over_hit"])
        out.append({"game": "lol", "stat": r.get("stat"), "side": side, "p": q if side == "over" else 1 - q,
                    "win": float(h if side == "over" else 1 - h), "match": str(r.get("sid")), "t": str(r["series_start"]),
                    "book": r.get("platform"), "scope": r.get("scope"), "odds_type": r.get("odds_type"),
                    "version": r.get("model_version")})
    return out


# ============================================================================================================ fitting
VARIANTS = ("zero", "shrunk", "free")
# zero   = intercept 0: logit(c) = a * logit(p), 0 <= a <= 1 - a slope-only shrink toward 50%; the side can never flip and the
#          50% floor never binds (THE DEFAULT, coordinator rule 2026-11-05 after the NFL intercept flipped 641 sides to Under)
# shrunk = Platt (slope + intercept) with the intercept halved
# free   = gradecore.calibrate.fit as is (Platt 300+, isotonic 1,000+: a free level shift)
# A variant with an intercept is served only when its held-out log loss beats intercept 0 with a match-clustered 95% interval
# entirely below zero (select()).


def fit_scale(p, y):
    """a of logit(c) = a * logit(p), maximum likelihood by Newton, clipped to [0, 1]."""
    xs = [math.log(min(max(v, EPS), 1 - EPS) / (1 - min(max(v, EPS), 1 - EPS))) for v in p]
    a = 1.0
    for _ in range(50):
        g = h = 0.0
        for x, t in zip(xs, y):
            q = _sig(a * x)
            g += (q - t) * x
            h += q * (1 - q) * x * x
        if h <= 1e-12:
            break
        step = g / h
        a -= step
        if abs(step) < 1e-10:
            break
    return min(max(a, 0.0), 1.0)


def fit_tables(rows, min_n=MIN_N, variant="zero"):
    """{"<stat>|<side>": fit, "*|<side>": fit} on settled rows; identity under min_n. variant: VARIANTS."""
    from . import calibrate
    groups = {}
    for r in rows:
        for k in (f"{r['stat']}|{r['side']}", f"*|{r['side']}"):
            g = groups.setdefault(k, ([], []))
            g[0].append(r["p"])
            g[1].append(r["win"])
    out = {}
    for k, (p, y) in sorted(groups.items()):
        if len(y) < min_n or min(y) == max(y):
            out[k] = {"kind": "identity", "n": len(y)}
            continue
        if variant == "zero":
            out[k] = {"kind": "scale", "n": len(y), "a": round(fit_scale(p, y), 6)}
            continue
        if variant == "shrunk":
            ab = calibrate.fit_platt(p, y)
            out[k] = {"kind": "identity", "n": len(y)} if ab is None else {"kind": "platt", "n": len(y), "a": ab[0], "b": ab[1] / 2}
            continue
        f = calibrate.fit(p, y, k, None, min_n)
        f = {x: f[x] for x in ("kind", "n", "a", "b", "x", "y", "data_hash") if x in f}
        if f["kind"] == "isotonic":
            f["x"] = [round(v, 6) for v in f["x"]]
            f["y"] = [round(v, 6) for v in f["y"]]
        out[k] = f
    return out


def _ts(s):
    s = str(s).replace("Z", "+00:00")
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def walk_forward(rows, test_from, settle_h=SETTLE_H, min_n=MIN_N, variant="zero", unfloored=None):
    """Held-out predictions: each UTC day >= test_from scored with tables fitted only on rows whose match started
    >= settle_h hours before that day began. -> [(row, shown, fit key)] (rows of the held-out window). unfloored: a list that
    receives each row's calibrated chance before the 50% floor (a value under 50% = a side the fit would have flipped)."""
    days = sorted({r["t"][:10] for r in rows if r["t"][:10] >= test_from})
    out = []
    for d in days:
        cut = _ts(d + "T00:00:00+00:00") - timedelta(hours=settle_h)
        train = [r for r in rows if _ts(r["t"]) < cut]
        tables = fit_tables(train, min_n, variant)
        for r in rows:
            if r["t"][:10] != d:
                continue
            k, f = table_for(tables, r["stat"], r["side"])
            c = apply_fit(f, r["p"])
            if unfloored is not None:
                unfloored.append(c)
            out.append((r, min(max(c, FLOOR), CEIL), k or "identity"))
    return out


# ============================================================================================================ evaluation
def _scores(rows, key):
    import numpy as np
    from . import scorecard
    if not rows:
        return {"n": 0}
    p = np.array([r[key] for r in rows], float)
    y = np.array([r["win"] for r in rows], float)
    g = [r["match"] for r in rows]
    hit, lo, hi = scorecard.cluster_ci(y, g)
    return {"n": len(rows), "matches": len(set(g)), "stated": round(float(p.mean()), 4), "hit": round(hit, 4),
            "hit_ci": None if lo is None else [round(lo, 4), round(hi, 4)], "gap": round(hit - float(p.mean()), 4),
            "brier": round(scorecard.brier(p, y), 5), "log_loss": round(scorecard.log_loss(p, y), 5)}


def _bins(rows, key):
    from . import scorecard
    if not rows:
        return []
    rel = scorecard.reliability([r[key] for r in rows], [r["win"] for r in rows], [r["match"] for r in rows])
    return [{"bin": f"{100 * b['lo']:.0f}-{100 * b['hi']:.0f}%", "n": b["n"], "stated": round(b["stated"], 4),
             "hit": round(b["hit"], 4), "ci": b["ci"] and [round(b["ci"][0], 4), round(b["ci"][1], 4)],
             "gap": round(b["gap"], 4)} for b in rel]


def _letters(rows, key):
    out = {}
    for r in rows:
        out.setdefault(letter(r[key]) or "Lean", []).append(r)
    order = [g for _, g in LETTER_CUTS] + ["Lean"]
    return {g: {"n": len(out[g]), "stated": round(sum(x[key] for x in out[g]) / len(out[g]), 4),
                "hit": round(sum(x["win"] for x in out[g]) / len(out[g]), 4)} for g in order if g in out}


def summarize(scored):
    """scored: [(row, shown, key)] -> the before / after tables of one game's held-out window."""
    import numpy as np
    from . import scorecard
    rows = [dict(r, c=c, fit=k) for r, c, k in scored]
    if not rows:
        return {"n": 0}

    def block(rs):
        if not rs:
            return {"n": 0}
        ll_raw = [-math.log(max(r["p"] if r["win"] else 1 - r["p"], 1e-12)) for r in rs]
        ll_cal = [-math.log(max(r["c"] if r["win"] else 1 - r["c"], 1e-12)) for r in rs]
        d, lo, hi = scorecard.cluster_ci(np.array(ll_cal) - np.array(ll_raw), [r["match"] for r in rs])
        return {"raw": _scores(rs, "p"), "calibrated": _scores(rs, "c"),
                "log_loss_cal_minus_raw": {"mean": round(d, 5), "ci": None if lo is None else [round(lo, 5), round(hi, 5)]},
                "bins_raw": _bins(rs, "p"), "bins_calibrated": _bins(rs, "c"),
                "letters_raw": _letters(rs, "p"), "letters_calibrated": _letters(rs, "c"),
                "printed_60_plus": {"raw": sum(r["p"] >= 0.6 for r in rs), "calibrated": sum(r["c"] >= 0.6 for r in rs)},
                "lettered": {"raw": sum(r["p"] >= 0.55 for r in rs), "calibrated": sum(r["c"] >= 0.55 for r in rs)}}
    out = {"all": block(rows), "sides": {s: block([r for r in rows if r["side"] == s]) for s in SIDES},
           "stats": {st: block([r for r in rows if r["stat"] == st]) for st in sorted({r["stat"] for r in rows})},
           "stat_sides": {f"{st}|{s}": block([r for r in rows if r["stat"] == st and r["side"] == s])
                          for st in sorted({r["stat"] for r in rows}) for s in SIDES},
           "fits_used": {k: sum(1 for r in rows if r["fit"] == k) for k in sorted({r["fit"] for r in rows})},
           # A14-style balance: the share of props favouring Over vs the Over rate (the layer never flips a side, so this
           # is the model's own balance - reported, never forced)
           "balance": {"share_over_favoured": round(sum(r["side"] == "over" for r in rows) / len(rows), 4),
                       "over_rate": round(sum((r["win"] if r["side"] == "over" else 1 - r["win"]) for r in rows) / len(rows), 4)}}
    a = out["all"]
    out["summary"] = {"n": a["calibrated"]["n"], "matches": a["calibrated"]["matches"],
                      "stated_raw": a["raw"]["stated"], "stated_cal": a["calibrated"]["stated"], "hit": a["calibrated"]["hit"],
                      "hit_ci": a["calibrated"]["hit_ci"], "log_loss_raw": a["raw"]["log_loss"], "log_loss_cal": a["calibrated"]["log_loss"],
                      "brier_raw": a["raw"]["brier"], "brier_cal": a["calibrated"]["brier"],
                      "sides": {s: {k: out["sides"][s].get("calibrated", {}).get(k) for k in ("n", "stated", "hit", "gap", "hit_ci")}
                                | {"stated_raw": out["sides"][s].get("raw", {}).get("stated")} for s in SIDES}}
    return out


def verdict(summary, min_test=MIN_TEST, min_side=MIN_SIDE, max_gap=MAX_GAP):
    """ct-3's calibration rule on the held-out window -> ("pass" | "fail", why)."""
    a = (summary or {}).get("all") or {}
    c, r = a.get("calibrated") or {}, a.get("raw") or {}
    if (c.get("n") or 0) < min_test:
        return "fail", f"needs >= {min_test} decided held-out forecasts (have {c.get('n') or 0})"
    why = []
    if abs(c["gap"]) > max_gap:
        why.append(f"all: stated {c['stated']:.3f} vs hit {c['hit']:.3f} (gap {100 * c['gap']:+.1f} pts > {100 * max_gap:.1f})")
    for s in SIDES:
        sc = ((summary.get("sides") or {}).get(s) or {}).get("calibrated") or {}
        if (sc.get("n") or 0) < min_side:
            why.append(f"{s}: {sc.get('n') or 0} decided (< {min_side})")
        elif abs(sc["gap"]) > max_gap:
            why.append(f"{s}: stated {sc['stated']:.3f} vs hit {sc['hit']:.3f} (gap {100 * sc['gap']:+.1f} pts > {100 * max_gap:.1f})")
    if c["log_loss"] > r["log_loss"]:
        why.append(f"log loss {c['log_loss']:.5f} worse than raw {r['log_loss']:.5f}")
    return ("fail", "; ".join(why)) if why else ("pass", "overall and both sides within "
                                                           f"{100 * max_gap:.1f} pts on {c['n']} held-out forecasts; log loss not worse than raw")


def _variant_facts(scored, unfloored):
    """Side-by-side facts of one variant on the held-out rows: log loss, Over share of the printed sides, side flips the fit
    would make without the floor, "No lean" (printed exactly 50%), Lean (under 55%), letters, 60%+."""
    rows = [r for r, _, _ in scored]
    cs = [c for _, c, _ in scored]
    n = len(rows) or 1
    flips = sum(1 for u in unfloored if u < 0.5)
    over_unfloored = sum(1 for r, u in zip(rows, unfloored) if (r["side"] == "over") == (u >= 0.5))
    ll = sum(-math.log(max(c if r["win"] else 1 - c, 1e-12)) for r, c in zip(rows, cs)) / n
    return {"n": len(rows), "log_loss": round(ll, 5), "stated": round(sum(cs) / n, 4),
            "hit": round(sum(r["win"] for r in rows) / n, 4),
            "over_share_printed": round(sum(r["side"] == "over" for r in rows) / n, 4),       # the side is the model's
            "over_share_if_unfloored": round(over_unfloored / n, 4), "side_flips_if_unfloored": flips, "side_flips_served": 0,
            "no_lean_50": sum(1 for c in cs if c <= FLOOR + 1e-9), "lean_under_55": sum(1 for c in cs if c < 0.55),
            "lettered": sum(1 for c in cs if c >= 0.55), "printed_60_plus": sum(1 for c in cs if c >= 0.6)}


def select(by_variant):
    """Intercept 0 unless a variant with an intercept beats it on held-out log loss with a match-clustered 95% interval of
    the paired difference entirely below zero (free first, then shrunk). -> (variant, comparison)."""
    import numpy as np
    from . import scorecard
    base = by_variant["zero"]
    ll = lambda sc: np.array([-math.log(max(c if r["win"] else 1 - c, 1e-12)) for r, c, _ in sc])
    comp = {}
    for v in ("free", "shrunk"):
        d, lo, hi = scorecard.cluster_ci(ll(by_variant[v]) - ll(base), [r["match"] for r, _, _ in base])
        comp[f"{v}_minus_zero"] = {"mean": round(d, 5), "ci": None if lo is None else [round(lo, 5), round(hi, 5)],
                                   "beats_zero": hi is not None and hi < 0}
    for v in ("free", "shrunk"):
        if comp[f"{v}_minus_zero"]["beats_zero"]:
            return v, comp
    return "zero", comp


def evaluate(rows_by_game, test_from=None, test_days=TEST_DAYS):
    """{game: rows} -> {game: {"test_from", "variants", "served", "summary", "verdict", "why"}} (held-out, walk-forward by
    day; every variant scored on the same rows; the served one chosen by select())."""
    out = {}
    for game, rows in rows_by_game.items():
        rows = sorted(rows, key=lambda r: r["t"])
        if not rows:
            out[game] = {"verdict": "fail", "why": "no settled rows", "served": "zero"}
            continue
        tf = test_from or (_ts(rows[-1]["t"]).date() - timedelta(days=test_days - 1)).isoformat()
        scored, facts = {}, {}
        for v in VARIANTS:
            uf = []
            scored[v] = walk_forward(rows, tf, variant=v, unfloored=uf)
            facts[v] = _variant_facts(scored[v], uf)
        raw = [(r, r["p"], "raw") for r, _, _ in scored["zero"]]
        facts["raw"] = _variant_facts(raw, [r["p"] for r, _, _ in raw])
        served, comp = select(scored)
        s = summarize(scored[served])
        v, why = verdict(s)
        out[game] = {"test_from": tf, "test_to": rows[-1]["t"][:10], "fit_rows_before": sum(1 for r in rows if r["t"][:10] < tf),
                     "rows": len(rows), "variants": facts, "selection": comp, "served": served, "summary": s,
                     "verdict": v, "why": why}
    return out


def build(rows_by_game, sources, test_from=None):
    """The ec-1 artifact: per game, tables fitted on ALL settled rows (the served procedure: refitted on everything older)
    and the held-out verdict that decides whether its letters are live or held."""
    ev = evaluate(rows_by_game, test_from)
    games = {}
    for game, rows in rows_by_game.items():
        e = ev[game]
        games[game] = {"letters": "live" if e["verdict"] == "pass" else "held", "verdict": e["verdict"], "why": e["why"],
                       "variant": e.get("served", "zero"), "tables": fit_tables(rows, variant=e.get("served", "zero")), "rows": len(rows),
                       "data_from": min((r["t"] for r in rows), default=None), "data_to": max((r["t"] for r in rows), default=None),
                       "source": sources.get(game),
                       "test": {"from": e.get("test_from"), "to": e.get("test_to"), "summary": (e.get("summary") or {}).get("summary")}}
    obj = {"version": VERSION, "rule": "printed chance = max(50%, calibrated chance of the model's side); letters from the "
                                       "existing thresholds on that chance; stakes never above the model's own",
           "fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "min_n": MIN_N, "floor": FLOOR,
           "games": games}
    obj["sha"] = _sha(games)
    return validate(obj), ev


# ============================================================================================================ CLI
def _read_rows(cs2=None, lol=None, lol_db=None, cs2_db=None):
    import sqlite3
    by = {}
    if cs2:
        doc = json.loads(Path(cs2).read_text(encoding="utf-8"))
        by["cs2"] = rows_from_cs2_p1(doc if isinstance(doc, dict) and "rows" in doc else {"rows": doc})
    elif cs2_db:                                   # the learning database (read-only): its stored P1 export
        con = sqlite3.connect(f"file:{Path(cs2_db).as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            row = con.execute("SELECT v FROM meta WHERE k = 'p1_units'").fetchone()
        finally:
            con.close()
        by["cs2"] = rows_from_cs2_p1(json.loads(row[0]) if row and row[0] else {})
    if lol:
        doc = json.loads(Path(lol).read_text(encoding="utf-8"))
        by["lol"] = rows_from_lol(doc.get("rows") if isinstance(doc, dict) else doc)
    elif lol_db:
        con = sqlite3.connect(f"file:{Path(lol_db).as_posix()}?mode=ro", uri=True, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            by["lol"] = rows_from_lol([dict(r) for r in con.execute(LOL_ROWS_SQL)])
        finally:
            con.close()
    return by


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m gradecore.chance_cal")
    ap.add_argument("cmd", choices=["eval", "fit", "status"])
    ap.add_argument("--cs2", help="CS2 P1 export JSON (/admin/learning/p1_units.json pages joined: {rows: [...]})")
    ap.add_argument("--cs2-db", help="learning.db copy (read-only): its stored P1 export")
    ap.add_argument("--lol", help="LoL settled forecasts JSON (rows of LOL_ROWS_SQL)")
    ap.add_argument("--lol-db", help="lol.db copy (read-only)")
    ap.add_argument("--test-from", help="first held-out day (default: the last 7 days of each game's data)")
    ap.add_argument("--out", help="fit: artifact path (default gradecore/chance_cal_ec1.json)")
    ap.add_argument("--report", help="eval / fit: write the full held-out report JSON here")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(status(), indent=1))
        return 0
    by = _read_rows(a.cs2, a.lol, a.lol_db, a.cs2_db)
    if not by:
        raise SystemExit("give --cs2 / --cs2-db and / or --lol / --lol-db")
    if a.cmd == "eval":
        ev = evaluate(by, a.test_from)
    else:
        obj, ev = build(by, {g: (a.cs2 or a.cs2_db) if g == "cs2" else (a.lol or a.lol_db) for g in by}, a.test_from)
        out = Path(a.out) if a.out else PATH
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
        os.replace(tmp, out)
        reset_cache()
    if a.report:
        Path(a.report).write_text(json.dumps(ev, indent=1, default=float), encoding="utf-8")
    print(json.dumps({g: {"verdict": e["verdict"], "why": e["why"], "test_from": e.get("test_from"), "served": e.get("served"),
                          "selection": e.get("selection"), "variants": e.get("variants"),
                          "summary": (e.get("summary") or {}).get("summary")} for g, e in ev.items()}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
