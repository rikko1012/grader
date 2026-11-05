"""map_weighted.py - CS2_MAP_WEIGHTED (default 0): the vm-2 map weighting as the LIVE CS2 projection (rule mw-1).
NOT PROMOTED: vm-2 is a registered shadow (research/map_chances/REGISTER.md); turning this flag on in production needs the
owner's approval after the registered forward test passes (MODEL_VERSIONS.md, section mw-1).

Flag off (unset / anything but "1"): activate() does nothing - no function is wrapped, the model tag stays the frozen
picklog.MODEL_VERSION (3007c59b74 from the committed blobs), every grade, chance, stake, projection and forecast row is
byte-identical to the code without this file. Only the display labels below read it.

Flag on ("1", read once when server.py imports this module): activate()
- wraps cs2_scout.finish_line / cs2_scout.grader_inputs (module globals; no hashed file is edited - cs2_scout.py, grader.py,
  perf*.py and the rest of picklog's hash list are frozen since 4877218) so that, for every CS2 grade, the performance-model
  projection d["perf"] is replaced by vm-2's map-weighted one when it is eligible (apply()), and
- sets picklog.MODEL_VERSION to a NEW tag through the existing versioning: sha1(frozen tag + "|CS2_MAP_WEIGHTED=1|" + the LF
  bytes of map_weighted.py, vm2.py, vm2_params.json, hltv_mapchances.py)[:10] (tag_on()). Every pick / forecast row, the board's
  graded_model and model_versions.json carry it, so flag-on and flag-off grades are never pooled. The frozen tag itself never
  moves. CS2_MAP_WEIGHTED_CELLS (e.g. "kills:12,hs:12"; unset = all six cells) limits the weighting to the cells the registered
  rule passed; a limited set is part of the tag.

The live projection with the flag on (exact formula, vm2.py / vm2_params.json vm-2-37ec7435b0, nothing refitted):
  chance_m  = HLTV's % for map m (map_chances), from the snapshot captured at or before the forecast time AND before the start
  a_m       = the player's kills per map on map m, maps dated in [match day - 180 d, match day); < 5 maps -> a0
  a0        = all his kills / all his maps in that window (>= 10 maps, else no weighting)
  Maps 1-2 / Maps 1-3: per_map = sum(chance_m * a_m) / sum(chance_m) over every listed map; kills = per_map * (2 | 3)
  Map 1:    per_map = a_m of the likeliest simulated veto's (rank 1) Map 1; kills = per_map
  kills line: lambda = kills; sd = c * sqrt(max(lambda, 1)), c = live sd / sqrt(max(live lambda, 1))  (the live spread)
  HS line:    lambda_kills = kills; lambda = kills * live HS per kill; sd scaled the same way; kills spread = the live one
The grader then runs unchanged on that projection (its own cut rule, caps, letters and stakes).

Fallback = the current projection, untouched, with the reason logged in the forecast record (grader_inputs.map_weighted,
saved in forecasts.inputs - append-only): not a vm-2 cell / not a perf-2 / perf-2-bo3 / perf-cm2 projection; team unknown
(no team / opponent, or the match has no HLTV id); no HLTV player id; no map chances; chances captured after the forecast
time; captured at or after the start; stale (captured more than STALE_MIN minutes before the forecast); chances that do not
add up to the format; fewer than 10 window maps; Map 1 without a likeliest veto. An HLTV database failure is NOT a fallback
(owner rule: a transient failure never changes a grade): the projection becomes the existing transient error, so the board
keeps its last grade and the card says the database is unreachable."""
import datetime as dt
import hashlib
import math
import os
import threading
from pathlib import Path

FLAG = "CS2_MAP_WEIGHTED"
CELLS_ENV = "CS2_MAP_WEIGHTED_CELLS"   # the registered rule promotes per cell: e.g. "kills:12,hs:12"; unset / "all" = all six
ALL_CELLS = tuple(f"{p}:{s}" for p in ("kills", "hs") for s in ("1", "12", "123"))
RULE = "mw-1"
STALE_MIN = 360            # the scraper re-reads a match every 3 h (every scan within 6 h of the start): > 6 h old = stale
LABEL_USED = "HLTV map chances used"
LABEL_SHADOW = "Shadow only – not in this grade"
HERE = Path(__file__).resolve().parent
TAG_FILES = ("map_weighted.py", "vm2.py", "vm2_params.json", "hltv_mapchances.py")
_STATE = {"active": False, "base": None, "orig": {}, "cells": ALL_CELLS}
_CTX = threading.local()
_ACT_LOCK = threading.Lock()


def enabled(env=None):
    return (os.environ if env is None else env).get(FLAG, "0").strip() == "1"


def cells(env=None):
    """The cells the flag weights (prop:scope), from CS2_MAP_WEIGHTED_CELLS; unknown entries are ignored."""
    raw = (os.environ if env is None else env).get(CELLS_ENV, "all").strip().lower()
    if raw in ("", "all"):
        return ALL_CELLS
    want = {x.strip() for x in raw.split(",")}
    return tuple(c for c in ALL_CELLS if c in want)


def tag_on(base, cell_set=ALL_CELLS):
    """The flag-on model tag: the frozen tag + the weighted cells + the map-weighting files (LF line endings, as Render
    builds them)."""
    h = hashlib.sha1()
    h.update(str(base).encode())
    h.update(b"|CS2_MAP_WEIGHTED=1|")
    if tuple(cell_set) != ALL_CELLS:
        h.update(("cells=" + ",".join(cell_set) + "|").encode())
    for f in TAG_FILES:
        try:
            h.update((HERE / f).read_bytes().replace(b"\r\n", b"\n"))
        except OSError:
            h.update(b"missing:" + f.encode())
    return h.hexdigest()[:10]


def active():
    return _STATE["active"]


def activate(force=None):
    """Turn the map-weighted live projection on when CS2_MAP_WEIGHTED=1 (force True / False overrides, for tests).
    Returns whether it is active. Flag off: nothing is touched."""
    on = enabled() if force is None else bool(force)
    if not on:
        return _STATE["active"]
    with _ACT_LOCK:
        if _STATE["active"]:
            return True
        import cs2_scout
        import picklog
        _STATE["orig"] = {"finish_line": cs2_scout.finish_line, "grader_inputs": cs2_scout.grader_inputs}
        _STATE["base"] = picklog.MODEL_VERSION
        _STATE["cells"] = cells()
        cs2_scout.finish_line = _finish_line
        cs2_scout.grader_inputs = _grader_inputs
        picklog.MODEL_VERSION = tag_on(_STATE["base"], _STATE["cells"])
        _STATE["active"] = True
    return True


def deactivate():
    """Undo activate() (tests; a production switch is an env change + restart)."""
    with _ACT_LOCK:
        if not _STATE["active"]:
            return
        import cs2_scout
        import picklog
        cs2_scout.finish_line = _STATE["orig"]["finish_line"]
        cs2_scout.grader_inputs = _STATE["orig"]["grader_inputs"]
        picklog.MODEL_VERSION = _STATE["base"]
        _STATE.update(active=False, base=None, orig={}, cells=ALL_CELLS)


def _finish_line(res, args):
    prev = getattr(_CTX, "res", None)
    _CTX.res = res
    try:
        return _STATE["orig"]["finish_line"](res, args)
    finally:
        _CTX.res = prev


def _grader_inputs(pb, args, odds=None, hh=None):
    d = _STATE["orig"]["grader_inputs"](pb, args, odds, hh)
    try:
        apply(d, getattr(_CTX, "res", None), pb, args)
    except Exception as e:  # noqa: BLE001 - the weighting never stops a grade: the current projection stays, reason logged
        d["map_weighted"] = {"rule": RULE, "used": False, "reason": f"map weighting failed ({type(e).__name__})"}
    return d


# ------------------------------------------------------------------ helpers
def _utc(t):
    if t in (None, ""):
        return None
    if isinstance(t, dt.datetime):
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    try:
        x = dt.datetime.fromisoformat(str(t).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)


def _iso(t):
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds") if t else None


def match_context(res, pb=None, args=None):
    """(match_id, start, player_id, why) for a card result - why = the fallback reason when one of them is unknown."""
    res = res if isinstance(res, dict) else {}
    pb = pb if isinstance(pb, dict) else (res.get("player_block") or {})
    nm = res.get("next_match") or {}
    team = pb.get("playing_for") or pb.get("current_team")
    opp = nm.get("opponent") or getattr(args, "opponent", None)
    if not team or not opp:
        return None, None, None, "team unknown (no team or opponent for the match)"
    try:
        import veto_serve
        mid = veto_serve.match_id_of(res)
    except Exception:
        mid = None
    import hltv_mapchances as hmc
    if hmc.hltv_match_number(mid) is None:
        return mid, None, None, "team unknown (the match has no HLTV id)"
    start = nm.get("start") or getattr(args, "match_start", None)
    hv = res.get("hltv") if isinstance(res.get("hltv"), dict) else (pb.get("hltv") if isinstance(pb.get("hltv"), dict) else {})
    pid = hv.get("hltv_id") if hv.get("found") else None
    if not pid:
        return mid, start, None, "no HLTV player id"
    return mid, start, pid, None


def check_snapshot(snap, forecast_at):
    """(snapshot limited to what was known at forecast_at, None) or (None, reason): captured at or before the forecast time,
    not older than STALE_MIN minutes, before the start (hltv_mapchances.map_chances already cut it at the start)."""
    if not isinstance(snap, dict) or not snap.get("ok"):
        return None, (snap or {}).get("reason") or "no HLTV map chances for this match"
    fa = _utc(forecast_at)
    cap = _utc(snap.get("captured_at") or snap.get("updated_at"))
    st = _utc(snap.get("start_used"))
    if fa is None or cap is None:
        return None, "HLTV map chances carry no capture time"
    if cap > fa:
        return None, "HLTV map chances captured after the forecast time"
    if st is not None and fa >= st:
        return None, "forecast at or after the match start"
    if (fa - cap).total_seconds() > STALE_MIN * 60:
        return None, f"HLTV map chances are stale (captured {round((fa - cap).total_seconds() / 3600, 1)} h before the forecast)"
    vetoes = [v for v in snap.get("vetoes") or [] if _utc(v.get("updated_at")) is not None and _utc(v["updated_at"]) <= fa]
    return dict(snap, vetoes=vetoes), None


def weighted_perf(pf, prop, lam_k):
    """The live projection dict with vm-2's kills projection lam_k and the live spread (formula in the module docstring)."""
    out = dict(pf)
    lam_live, sd_live = float(pf["lambda"]), float(pf["sd"])
    c = sd_live / math.sqrt(max(lam_live, 1.0))
    if prop == "hs":
        lam = lam_k * float(pf["hs_per_kill"])
        out["lambda_kills"] = lam_k
    else:
        lam = lam_k
        if "lambda_kills" in out:
            out["lambda_kills"] = lam_k
    out["lambda"] = lam
    out["sd"] = c * math.sqrt(max(lam, 1.0))
    return out


def _transient(d, rec, reason):
    """An HLTV database failure while reading the map chances: the existing transient projection error (never a quietly
    different grade - the board keeps its last one)."""
    d["perf"] = {"error": f"HLTV database unreachable right now ({reason})", "transient": True}
    d["map_weighted"] = dict(rec, used=False, reason=reason, transient=True)


# ------------------------------------------------------------------ the live projection (flag on)
def apply(d, res, pb=None, args=None, now=None, cell_set=None):
    """Replace d["perf"]'s projection by vm-2's map-weighted one when eligible; log grader_inputs.map_weighted either way.
    now = the forecast time (default: the clock); cell_set = the weighted cells (default: the active set). Returns the record."""
    import hltv_mapchances as hmc
    import vm2
    fa = _utc(now) or dt.datetime.now(dt.timezone.utc)
    rec = {"rule": RULE, "flag": FLAG, "forecast_at": _iso(fa), "vm2_version": vm2.version(vm2._params()), "vm2_code_sha": vm2.CODE_SHA}

    def fallback(reason, **kw):
        d["map_weighted"] = dict(rec, used=False, reason=reason, **kw)
        return d["map_weighted"]
    cell, why = vm2.cell_check(d)
    if cell is None:
        return fallback(why)
    prop, scope, line, pf = cell
    if f"{prop}:{scope}" not in (cell_set if cell_set is not None else _STATE["cells"]):
        return fallback(f"cell {prop} {scope} not weighted ({CELLS_ENV})")
    mid, start, pid, why = match_context(res, pb, args)
    rec.update(match_id=mid, player_id=pid)
    if why:
        return fallback(why)
    snap = hmc.map_chances(mid, start)
    if not snap.get("ok") and snap.get("transient"):
        _transient(d, rec, snap.get("reason") or "HLTV database unreachable")
        return d["map_weighted"]
    snap, why = check_snapshot(snap, fa)
    if why:
        return fallback(why)
    pk = hmc.player_map_kills(pid, _utc(snap["start_used"]).date(), vm2._params()["window_days"])
    if not pk.get("ok") and pk.get("transient"):
        _transient(d, rec, pk.get("reason") or "HLTV database unreachable")
        return d["map_weighted"]
    ent = vm2.shadow_entry(d, snap, pk, None, logged_at=_iso(fa), match_id=mid, player_id=pid)
    sinfo = ent.get("snapshot") or {}
    rec["snapshot"] = {k: sinfo.get(k) for k in ("captured_at", "start_used", "read_at", "age_min", "lead_min", "format", "sum", "chances")}
    if not ent.get("eligible"):
        return fallback(ent.get("reason") or "vm-2 not eligible")
    arm = ent["vm-2"]
    lam_k = float(arm["lam_kills"])
    rec.update(used=True, prop=prop, scope=scope, line=line, source=str(pf.get("version") or ""),
               per_map_kills=arm["per_map_kills"], lambda_kills=lam_k, map_factor=ent.get("map_factor"),
               player=ent.get("player"), map1=ent.get("map1"),
               unweighted={k: pf.get(k) for k in ("lambda", "sd", "lambda_kills")})
    d["perf"] = weighted_perf(pf, prop, lam_k)
    rec["weighted"] = {k: d["perf"].get(k) for k in ("lambda", "sd", "lambda_kills")}
    d["map_weighted"] = rec
    return rec


# ------------------------------------------------------------------ display (drawer + research hub card)
def _proj(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


def card_status(res):
    """The classic card's (player drawer) label for the map chances block: {"used", "label", "at"?, "projection", "shadow"?,
    "unweighted"?}. None when the card shows a kept board grade (outage) or has no CS2 grade. The shadow projection is vm-2's
    arm computed now from cached reads (the card waits at most 0.5 s for a cold player read); None when not in hand."""
    if not isinstance(res, dict) or res.get("hltv_outage") or not isinstance(res.get("grader_inputs"), dict):
        return None
    gi = res["grader_inputs"]
    live = _proj((res.get("grade") or {}).get("lambda_final"))
    mw = gi.get("map_weighted")
    if isinstance(mw, dict) and mw.get("used"):
        return {"used": True, "label": LABEL_USED, "at": (mw.get("snapshot") or {}).get("captured_at"), "projection": live,
                "unweighted": _proj((mw.get("unweighted") or {}).get("lambda"))}
    return {"used": False, "label": LABEL_SHADOW, "projection": live, "shadow": _proj(_card_shadow(res))}


def _card_shadow(res):
    """vm-2's projection for this card's line (shadow arithmetic, vm2.shadow_entry) from the display snapshot + the player's
    cached per-map kills. Never raises; None = nothing to show."""
    try:
        import hltv_mapchances as hmc
        import vm2
        gi = res["grader_inputs"]
        if vm2.cell_check(gi)[0] is None:
            return None
        mid, start, pid, why = match_context(res)
        if why:
            return None
        snap = hmc.cut_before_start(hmc.map_chances_display(mid), start)
        if not isinstance(snap, dict) or not snap.get("ok"):
            return None
        pk = hmc.player_map_kills_display(pid, _utc(snap["start_used"]).date(), vm2._params()["window_days"])
        if not pk:
            return None
        ent = vm2.shadow_entry(gi, snap, pk, None, match_id=mid, player_id=pid)
        return (ent.get("vm-2") or {}).get("lam") if ent.get("eligible") else None
    except Exception:
        return None


def saved_status(gi, shadow, live_lambda=None):
    """The research hub card's label from the SAVED forecast (forecasts.inputs + forecasts.shadow of the row the card shows):
    the map chances it used, or "Shadow only" with the vm-2 projection logged on that row beside the live one."""
    gi = gi if isinstance(gi, dict) else {}
    mw = gi.get("map_weighted")
    live = _proj(live_lambda)
    if isinstance(mw, dict) and mw.get("used"):
        return {"used": True, "label": LABEL_USED, "at": (mw.get("snapshot") or {}).get("captured_at"), "projection": live,
                "unweighted": _proj((mw.get("unweighted") or {}).get("lambda"))}
    v = (shadow or {}).get("vm2") if isinstance(shadow, dict) else None
    sp = (v.get("vm-2") or {}).get("lam") if isinstance(v, dict) and v.get("eligible") else None
    return {"used": False, "label": LABEL_SHADOW, "projection": live, "shadow": _proj(sp)}


def block_from_record(mw):
    """A minimal map chances block from the chances the grade used (when the display read has nothing in hand)."""
    s = (mw or {}).get("snapshot") or {}
    ch = s.get("chances") or {}
    if not ch:
        return None
    maps = sorted(({"name": k, "pct": int(v)} for k, v in ch.items()), key=lambda m: -m["pct"])
    return {"at": s.get("captured_at"), "format": s.get("format"), "maps": maps, "weight": 1, "note": None, "veto": None}
