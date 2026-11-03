"""ct-2: who may show letters, for cs2, lol, nba and nfl. Generalised from sports/containment.py (ct-1, REGISTER A9a 4).

    contained(sport, league, stat) -> True = no letter for that market.

Order (first that answers wins):
  1. the owner's env switch, 1 (contained) | 0 (released): SCOUT_CONTAIN_CS2, SCOUT_CONTAIN_LOL, SPORTS_CONTAIN_NBA,
     SPORTS_CONTAIN_NFL;
  2. the state file. Scopes are "<sport>" and "<sport>:<league>"; each is {"contained": bool, "stats": [stat, ...]}. Either
     scope containing the market contains it;
  3. nothing known: contained. A market shows no letters until it has passed the check.

The check (brief 3.4 step 8, verdict()): the last 300+ settled legs must have |mean(p_cal) - hit rate| <= 2.5 pts and a
match-clustered EV interval wholly above 0. Pushes and voids do not count. Under 300 legs nothing changes.
apply_verdicts() is the daily step: decide, append the log, write atomically. Nothing here runs by itself.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from . import scorecard

VERSION = "ct-2"
SPORTS = ("cs2", "lol", "nba", "nfl")
ENV = {"cs2": "SCOUT_CONTAIN_CS2", "lol": "SCOUT_CONTAIN_LOL", "nba": "SPORTS_CONTAIN_NBA", "nfl": "SPORTS_CONTAIN_NFL"}
MIN_LEGS = 300
MAX_GAP = 0.025
STAT_CONTAIN_N = 60             # A9 5: a stat this large with a hit interval wholly under 50% is contained alone
LOG_MAX = 400
CHECK_S = 30.0
_ON, _OFF = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}

_path = None
_cache = {"t": 0.0, "key": None, "obj": None}
_states = {"t": 0.0, "by": {}}


def set_path(path):
    """Where the state file lives. Supplied by the caller: this package knows no sport's data folder."""
    global _path
    _path = Path(path) if path else None
    reset_cache()


def reset_cache():
    _cache.update(t=0.0, key=None, obj=None)
    _states.update(t=0.0, by={})


def env_override(sport):
    """True / False from the sport's switch, None when unset or not a switch value."""
    name = ENV.get(sport)
    v = os.environ.get(name, "").strip().lower() if name else ""
    return True if v in _ON else (False if v in _OFF else None)


def _valid(obj):
    if not isinstance(obj, dict) or obj.get("version") != VERSION or not isinstance(obj.get("scopes"), dict):
        return None
    for s in obj["scopes"].values():
        if not isinstance(s, dict) or not isinstance(s.get("contained"), bool) or \
                not all(isinstance(x, str) for x in (s.get("stats") or [])):
            return None
    return obj


def load(force=False):
    """-> the state, or None. Cached; the file's mtime is re-checked every CHECK_S seconds."""
    now = time.time()
    if not force and _cache["key"] is not None and now - _cache["t"] < CHECK_S:
        return _cache["obj"]
    try:
        key = (str(_path), _path.stat().st_mtime_ns) if _path else ("", None)
    except OSError:
        key = (str(_path), None)
    if force or key != _cache["key"]:
        try:
            obj = _valid(json.loads(_path.read_text(encoding="utf-8"))) if key[1] is not None else None
        except (OSError, ValueError):
            obj = None
        _cache.update(key=key, obj=obj)
    _cache["t"] = now
    return _cache["obj"]


def _resolve(sport, league):
    ov = env_override(sport)
    if ov is not None:
        return {"contained": ov, "stats": [], "source": "env"}
    scopes = (load() or {}).get("scopes") or {}
    found = [scopes[k] for k in (sport, f"{sport}:{league}") if k in scopes]
    if not found:
        return {"contained": True, "stats": [], "source": "default"}
    return {"contained": any(s["contained"] for s in found), "source": "file",
            "stats": sorted({x for s in found for x in (s.get("stats") or [])})}


def state(sport, league=None):
    """-> {"contained", "stats", "source"}, resolved once per CHECK_S window: a dict read on the request path."""
    now = time.time()
    if now - _states["t"] >= CHECK_S:
        _states.update(t=now, by={})
    k = (sport, league)
    if k not in _states["by"]:
        _states["by"][k] = _resolve(sport, league)
    return _states["by"][k]


def contained(sport, league, stat):
    if sport not in ENV:
        raise ValueError(f"unknown sport {sport!r}")
    st = state(sport, league)
    return bool(st["contained"]) or stat in st["stats"]


def snapshot():
    """Admin view: each sport's state and switch."""
    obj = load()
    return {"version": VERSION, "updated_at": (obj or {}).get("updated_at"), "sports": {s: state(s) for s in SPORTS},
            "env": {ENV[s]: os.environ.get(ENV[s]) for s in SPORTS}}


def verdict(rows, min_legs=MIN_LEGS, max_gap=MAX_GAP):
    """The check for one scope. rows, oldest first: {"p", "status", "match_key", "break_even", "stat"}. -> a dict with
    "verdict" = release | contain | contain:<stat,...> | insufficient. Legs with no known break-even cannot show an EV,
    so they do not count."""
    d = [r for r in scorecard.decided(rows) if r.get("break_even")]
    v = {"rule": VERSION, "n": len(d), "stats_failed": []}
    by_stat = {}
    for r in d:
        by_stat.setdefault(r.get("stat"), []).append(r)
    for stat, legs in sorted(by_stat.items(), key=lambda x: str(x[0])):
        if stat is not None and len(legs) >= STAT_CONTAIN_N:
            ci = scorecard.score(legs).get("hit_ci")
            if ci and ci[1] < 0.5:
                v["stats_failed"].append(stat)
    if v["stats_failed"]:
        return {**v, "verdict": "contain:" + ",".join(v["stats_failed"]),
                "why": f"stat(s) with >= {STAT_CONTAIN_N} legs and a hit interval wholly below 50%"}
    if len(d) < min_legs:
        return {**v, "verdict": "insufficient", "why": f"needs >= {min_legs} settled legs (have {len(d)})"}
    w = d[-min_legs:]
    sc = scorecard.score(w)
    _, lo, hi = scorecard.ev_ci(w)
    v.update({"window": len(w), "matches": sc["matches"], "hit": sc["hit_rate"], "stated": sc["stated"], "gap": sc["gap"],
              "hit_ci": sc["hit_ci"], "ev_ci": None if lo is None else [lo, hi]})
    if abs(sc["gap"]) > max_gap:
        return {**v, "verdict": "contain", "why": f"stated and hit rate differ by more than {max_gap:.3f}"}
    if lo is None or lo <= 0:
        return {**v, "verdict": "contain", "why": "EV interval does not sit above 0"}
    return {**v, "verdict": "release", "why": "both conditions hold"}


def decide(before, verdict_text):
    """(before {"contained", "stats"}, verdict) -> after. release -> shown; contain -> scope hidden; contain:<a,b> -> those
    stats hidden too (a scope hidden as a whole stays hidden); insufficient or anything unknown -> same."""
    b = {"contained": bool(before.get("contained")), "stats": sorted(before.get("stats") or [])}
    v = (verdict_text or "").strip()
    if v == "release":
        return {"contained": False, "stats": []}
    if v == "contain":
        return {"contained": True, "stats": b["stats"]}
    if v.startswith("contain:"):
        named = [x for x in v[len("contain:"):].split(",") if x]
        return {"contained": b["contained"], "stats": sorted(set(b["stats"]) | set(named))}
    return b


def apply_verdicts(results, day, source, out=None):
    """results: {scope: verdict dict}. Decides each scope, appends the log, writes the state atomically to `out` (default:
    the set_path file). -> {scope: {"verdict", "before", "after", "changed"}}. A scope never seen starts contained."""
    p = Path(out) if out else _path
    if p is None:
        raise ValueError("no state file: call set_path or pass out")
    try:
        cur = _valid(json.loads(p.read_text(encoding="utf-8"))) or {}
    except (OSError, ValueError):
        cur = {}
    scopes = {k: dict(s) for k, s in (cur.get("scopes") or {}).items()}
    log = list(cur.get("log") or [])
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = {}
    for scope, v in sorted((results or {}).items()):
        s = scopes.get(scope) or {"contained": True, "stats": []}
        before = {"contained": s["contained"], "stats": sorted(s.get("stats") or [])}
        after = decide(before, v.get("verdict"))
        changed = after != before
        scopes[scope] = {**after, "verdict": v.get("verdict"), "decided": day,
                         "since": day if changed else s.get("since") or day}
        log.append({"date": day, "at": at, "scope": scope, "verdict": v.get("verdict"), "why": v.get("why"), "n": v.get("n"),
                    "hit": v.get("hit"), "stated": v.get("stated"), "ev_ci": v.get("ev_ci"), "before": before, "after": after,
                    "changed": changed, "source": source})
        summary[scope] = {"verdict": v.get("verdict"), "before": before, "after": after, "changed": changed}
    doc = {"version": VERSION, "updated_at": at, "scopes": scopes, "log": log[-LOG_MAX:]}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    reset_cache()
    return summary
