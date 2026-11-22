"""ac-2: NBA assists displayed-chance calibration (round2 nba-cal, 2026-11-21). Flag NBA_ASSISTS_CAL2 (default OFF;
NBA_ASSISTS_CAL2=1 turns it on, =0 / unset -> assists shown at the raw sm-1 chance, the current behaviour).
It PASSED the registered calibration test below (PREREG a-d, lag check included); it is held OFF under the owner's
graded-count and preview-first rules (grades-accuracy-not-fewer: no chance-lowering / letter-removing change board-wide
without an owner board preview): graded assists rows fall ~34% on the test days (A letters 1,179 -> 240; that drop is
also the intended effect - assists stops taking most A letters). On the higher-chance-side population (no N4) no side
changes, so all-row hit is identical and best-prop hit shows no difference (1491-1034 vs 1483-1042). ADVERSE N4
interaction (post-hoc, not pre-registered; prover-confirmed, 689 rows with a saved market chance): the board's N4 rule
compares the SHOWN chance with the market, so the shrunk chance drops leans - the props ac-2 newly holds went 78-48
(61.9%, game CI [52.5%, 71.1%]) and the kept-lean hit falls 51.4% -> 47.9%. Live board replay 2026-11-22 02:26Z: assists
leans 71 -> 53 (Over 28 -> 12, Under 43 -> 41), 18 graded quotes lose side and letter. Do not enable as built.

Why: points, rebounds and threes are shown at the cal-mi-1 chance (sports/model/cal.py), but cal-mi-1 fails A8a for
assists every day (its fitted intercept pushes the share favouring Over to ~0.32 vs an over rate of ~0.48), so assists
was the only NBA stat shown at the raw, overconfident sm-1 chance (A rows hit ~57.7% vs ~66% stated) and took most A
letters and best-prop slots.

    z = logit P_raw(Over | no push)            (the saved sm-1 chance, never changed)
    q = sigmoid(b * z)                          zero intercept: the higher-chance side never changes (A8a balance on
                                                that population holds by construction); N4 can still drop a lean
    shown: P(Over) = (1 - pp) q, P(Under) = (1 - pp)(1 - q), P(Push) = pp

b is fitted by penalised logistic ML (ridge 20 toward b = 1, the identity), the V1 variant of the pre-registered family.
Evidence (C:/Users/tikic/cs2_scout_ops/logs/audit_1121/round2/nba-cal/, PREREG.md + harness.py): chronological
walk-forward, 23 test game days 2026-10-28..11-20, 3,368 P1 assists rows, 179 games: sports.model.cal.report verdict
PASS (i) - log loss -0.0055, game CI [-0.0106, -0.0002], day CI [-0.0108, -0.0009]; share favouring Over 0.40 vs over
rate 0.48; d-2 lag check -0.0058 [-0.0109, -0.0005].

Order on the board (board_view.calibrated): pc-1 (off) -> cal-mi-1 (if ON for nba:assists it wins: the registered daily
rule) -> ac-2. Pure python, cached artifact; nothing here touches the saved forecast record.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path

VERSION = "ac-2"
FLAG = "NBA_ASSISTS_CAL2"
MARKET = ("nba", "assists")
ART_PATH = Path(__file__).resolve().parent / "artifacts" / "nba_assists_cal2.json"
LAMBDA = 20.0
B_MIN, B_MAX = 0.05, 1.0              # a served slope outside this range is refused (never sharpens, never flat)
EPS = 1e-6
_OFF = {"0", "false", "no", "off"}


_ON = {"1", "true", "yes", "on"}


def enabled():
    # default OFF (round2 nba-cal): the registered calibration test passed; held off under the graded-count /
    # preview-first rules (graded assists rows -34%) and the adverse N4 interaction; only NBA_ASSISTS_CAL2=1/true/yes/on
    # turns it on (allowlist: an empty or unknown value stays OFF)
    return os.environ.get(FLAG, "0").strip().lower() in _ON


def _logit(p):
    p = min(max(float(p), EPS), 1 - EPS)
    return math.log(p / (1 - p))


def _sig(x):
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def validate(obj):
    if not isinstance(obj, dict) or obj.get("version") != VERSION:
        raise ValueError("not an ac-2 artifact")
    p = obj.get("params")
    b = (p or {}).get("b") if isinstance(p, dict) else None
    if not isinstance(b, (int, float)) or isinstance(b, bool) or not math.isfinite(b) or not (B_MIN <= b <= B_MAX):
        raise ValueError(f"b must be a finite number in [{B_MIN}, {B_MAX}]")
    if obj.get("sha") != _sha(p):
        raise ValueError("sha mismatch")
    return obj


_cache = {"t": 0.0, "obj": None, "src": None}
CHECK_S = 300.0


def load(force=False):
    """-> (artifact, "default") or (None, None) when off / invalid. Cached; the artifact is committed (no Shell file)."""
    if not enabled():
        return None, None
    now = time.time()
    if not force and now - _cache["t"] < CHECK_S and _cache["t"]:
        return _cache["obj"], _cache["src"]
    try:
        obj, src = validate(json.loads(ART_PATH.read_text(encoding="utf-8"))), "default"
    except (OSError, ValueError):
        obj, src = None, None
    _cache.update(t=now, obj=obj, src=src)
    return obj, src


def reset_cache():
    _cache.update(t=0.0, obj=None, src=None)


def q_of(b, ps):
    return _sig(b * _logit(ps))


def adjust(league, stat, po, pu, pp, obj=None):
    """THE serving function -> {"po", "pu", "pp", "raw": [po, pu, pp], "v": "ac-2:<sha>", "mode": "S", "src"} for an
    NBA assists sm-1 chance, else None (other market / flag off / invalid artifact / no chance)."""
    if (league, stat) != MARKET or po is None or pu is None:
        return None
    if obj is None:
        obj, src = load()
    else:
        src = "given"
    if not obj:
        return None
    s = float(po) + float(pu)
    if s <= 0:
        return None
    ps = min(max(float(po) / s, EPS), 1 - EPS)
    ppv = float(pp or 0.0)
    q = q_of(obj["params"]["b"], ps)
    dec = 1.0 - ppv
    return {"po": round(dec * q, 6), "pu": round(dec * (1 - q), 6), "pp": round(ppv, 6),
            "raw": [round(float(po), 6), round(float(pu), 6), round(ppv, 6)],
            "v": f"{VERSION}:{obj['sha']}", "mode": "S", "src": src}


# ===================================================================================== fit + walk-forward (gradecheck)
def fit_b(rows, lam=LAMBDA, iters=60):
    """Penalised ML slope (zero intercept) on [{"ps", "hit"}] rows -> b (clipped to [B_MIN, B_MAX]) or None."""
    xs = [(_logit(r["ps"]), float(r["hit"])) for r in rows if r.get("ps") is not None and r.get("hit") is not None]
    if len(xs) < 50:
        return None
    b = 1.0
    for _ in range(iters):
        g, h = lam * (b - 1.0), lam
        for z, y in xs:
            p = _sig(b * z)
            g += (p - y) * z
            h += p * (1 - p) * z * z
        step = g / h
        b -= step
        if abs(step) < 1e-10:
            break
    if not math.isfinite(b):
        return None
    return min(max(b, B_MIN), B_MAX)


def walk_q(rows, game_day):
    """{fid: walk-forward ac-2 q} for NBA assists rows: b refitted per game-day ONLY on assists props settled before
    that day's first forecast (as cal.walk). rows: gradecheck rows with fid, league, stat, ps, value, line, status,
    forecast_at, settled_at. Days without >= 50 earlier decided rows keep the raw chance (absent from the dict)."""
    ast = [r for r in rows if (r.get("league"), r.get("stat")) == MARKET and r.get("ps") is not None]
    dec = [{"ps": r["ps"], "hit": 1.0 if float(r["value"]) > float(r["line"]) else 0.0, "settled_at": r.get("settled_at")}
           for r in ast if r.get("status") in ("win", "loss") and r.get("value") is not None
           and float(r["value"]) != float(r["line"])]
    days = {}
    for r in ast:
        days.setdefault(game_day(r["forecast_at"]), []).append(r)
    out = {}
    for d in sorted(days):
        cutoff = min(r["forecast_at"] for r in days[d])
        b = fit_b([x for x in dec if (x["settled_at"] or "9") < cutoff])
        if b is None:
            continue
        for r in days[d]:
            out[r["fid"]] = q_of(b, r["ps"])
    return out
