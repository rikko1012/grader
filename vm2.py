"""vm-2 - HLTV map chances x the player's per-map kills (research/map_chances/REGISTER.md). SHADOW ONLY: never changes a
grade, chance, stake or projection; nothing on a member path reads it. Logged as forecasts.shadow["vm2"] on every CS2
forecast row the pick-log writer thread appends (picklog), beside vm-1 (shadow["vm1"], veto-1's map distribution).

One forecast (player P, prop kills | HS, scope Map 1 / Maps 1-2 / Maps 1-3, offered line L):
- snapshot: HLTV's map chances for the match (hltv_mapchances.map_chances: map_chances rows updated BEFORE the match start, with
  their capture time) - chance_m in % per map, ~100 x maps in the format in all;
- a_m: P's kills per map on map m over the window (hltv_mapchances.player_map_kills), his overall per-map average on a thin map;
- projection (kills): Maps 1-2 / 1-3 = sum(chance_m x a_m) / sum(chance_m) x maps in the prop; Map 1 = a_m of the
  likeliest simulated veto's Map 1 (its step chance is logged);
- control C0-pooled: his overall per-map average x maps in the prop (the same arithmetic without any map weighting);
- chance: the vm-1 / rm-1 arithmetic at the new projection with the LIVE spread (cands_shadow.kills_probs / hs_probs).
Every choice is frozen in vm2_params.json (version vm-2-<params sha>); this file's own sha is logged with each entry.
Pure arithmetic in shadow_entry(); the two database reads (both cached + single flight in hltv_mapchances, on hltv.py's read path) happen in prefetch(),
called by the writer thread outside the pick-log lock. A missing / late snapshot or a failed read logs an INELIGIBLE entry
with its reason - it never becomes a number and never touches the live row."""
import datetime as dt
import hashlib
import json
import math
import os

import cands_shadow as rm

VERSION_BASE = "vm-2"
HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS_PATH = os.path.join(HERE, "vm2_params.json")
ARM, CONTROL = "vm-2", "C0-pooled"


# ------------------------------------------------------------------ frozen params
def load_params(path=PARAMS_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def params_sha(params):
    core = {k: params[k] for k in sorted(params) if k != "meta"}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:10]


def version(params):
    return f"{VERSION_BASE}-{params_sha(params)}"


def _code_sha():
    """sha of this file with LF line endings (the same on a Windows checkout and on the server)."""
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()[:10]
    except OSError:
        return None


CODE_SHA = _code_sha()
_P = {"p": None}


def _params(params=None):
    if params is not None:
        return params
    if _P["p"] is None:
        _P["p"] = load_params()
    return _P["p"]


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


def _side(p):
    return "Over" if p > 0.5 else ("Under" if p < 0.5 else None)


def cell_check(gi, P=None):
    """(prop, scope, line, perf) or (None, reason): the forecast-only part of eligibility (no database needed)."""
    P = _params(P)
    gi = gi if isinstance(gi, dict) else {}
    pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
    prop, scope = gi.get("prop"), str(gi.get("scope") or "12")
    if prop not in ("kills", "hs") or scope not in P["maps_in_prop"]:
        return None, "not a vm-2 cell"
    if str(pf.get("version") or "") not in P["sources"] or pf.get("lambda") is None or not pf.get("sd"):
        return None, "live projection not perf-2 / perf-2-bo3 / perf-cm2"
    if prop == "hs" and (pf.get("lambda_kills") is None or pf.get("hs_per_kill") is None):
        return None, "live HS projection has no kills projection / HS per kill"
    try:
        line = float(gi["line"])
    except (KeyError, TypeError, ValueError):
        return None, "no line"
    if not math.isfinite(line) or line < 0 or line * 2 != int(line * 2):
        return None, "line is not a whole or half number"
    return (prop, scope, line, pf), None


# ------------------------------------------------------------------ the entry (pure)
def _arm(name, prop, scope, line, pf, lam_k, P, live_p_more, per_map):
    """One arm's chance at kills projection lam_k (the vm-1 / rm-1 arithmetic with the live spread)."""
    if prop == "kills":
        lam_live = float(pf["lambda"])
        c = float(pf["sd"]) / math.sqrt(max(lam_live, 1.0))
        o, u, pu = rm.kills_probs(line, [(1.0, lam_k, c * math.sqrt(max(lam_k, 1.0)))])
        proj = lam_k
    else:
        import perf
        c = perf.kills_c(pf, scope)
        h = float(pf["hs_per_kill"])
        o, u, pu = rm.hs_probs(rm.kills_pmf([(1.0, lam_k, c * math.sqrt(max(lam_k, 1.0)))]), h, line)
        proj = lam_k * h
    raw_p = o / (o + u) if o + u > 0 else 0.5
    cap = float(P["kills_cap"])
    p = min(max(raw_p, 1 - cap), cap) if prop == "kills" else raw_p
    out = {"arm": name, "eligible": True, "p_over": round(p, 6), "p_over_uncapped": round(raw_p, 6),
           "raw": {"over": round(o, 6), "under": round(u, 6), "push": round(pu, 6)}, "lam": round(proj, 4),
           "lam_kills": round(lam_k, 4), "per_map_kills": round(per_map, 4), "side": _side(p)}
    if live_p_more is not None:
        out["delta_p"] = round(p - float(live_p_more), 6)
    return out


def _ineligible(base, reason, eligible=False, **extra):
    """Both arms ineligible on the same unit (coverage is identical by construction). eligible None = the shadow failed."""
    out = dict(base, eligible=eligible, reason=reason, **extra)
    out[ARM] = {"arm": ARM, "eligible": eligible, "reason": reason}
    out[CONTROL] = {"arm": CONTROL, "eligible": eligible, "reason": reason}
    return out


def _snap_info(snap, logged_at):
    cap, la = _utc((snap or {}).get("captured_at") or (snap or {}).get("updated_at")), _utc(logged_at)
    st = _utc((snap or {}).get("start_used"))
    return {"captured_at": (snap or {}).get("captured_at") or (snap or {}).get("updated_at"),
            "start_used": (snap or {}).get("start_used"), "read_at": (snap or {}).get("read_at"),
            "age_min": round((la - cap).total_seconds() / 60, 1) if cap and la else None,
            "lead_min": round((st - cap).total_seconds() / 60, 1) if cap and st else None,
            "logged_before_start": (la < st) if la and st else None,
            "format": (snap or {}).get("format"), "sum": (snap or {}).get("sum"),
            "status_found": ((snap or {}).get("status") or {}).get("found"),
            "chances": {c["map"]: c["chance"] for c in (snap or {}).get("chances") or []} or None}


def shadow_entry(gi, snap, pk, live_p_more=None, params=None, logged_at=None, match_id=None, player_id=None):
    """vm-2 shadow record of one forecast. gi = the grader's saved inputs (prop, line, scope, perf{...}); snap =
    hltv_mapchances.map_chances(match, start) (pre-start snapshot); pk = hltv_mapchances.player_map_kills(player,
    match day, window). Returns
    {version, params_sha, code_sha, eligible, reason?, snapshot{...}, player{...}, map1?, map_factor, live_replay,
    "vm-2": arm, "C0-pooled": arm}. Never raises (an error comes back as eligible None + error)."""
    try:
        return _entry(gi, snap, pk, live_p_more, _params(params), logged_at, match_id, player_id)
    except Exception as e:  # noqa: BLE001 - a shadow never stops the caller
        P = params or _P["p"] or {}
        base = {"version": version(P) if P else VERSION_BASE, "code_sha": CODE_SHA, "match_id": match_id}
        return _ineligible(base, "shadow failed", eligible=None, error=f"{type(e).__name__}: {str(e)[:160]}")


def _entry(gi, snap, pk, live_p_more, P, logged_at, match_id, player_id):
    gi = gi if isinstance(gi, dict) else {}
    pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
    base = {"version": version(P), "params_sha": params_sha(P), "code_sha": CODE_SHA, "match_id": match_id,
            "prop": gi.get("prop"), "scope": str(gi.get("scope") or "12"), "source": str(pf.get("version") or "")}
    if live_p_more is not None:
        base["live_p_more"] = live_p_more
    cell, why = cell_check(gi, P)
    if cell is None:
        return _ineligible(base, why)
    prop, scope, line, pf = cell
    base["line"] = line
    # ---- the map chances snapshot (captured before the match start)
    if not isinstance(snap, dict) or not snap.get("ok"):
        s = snap if isinstance(snap, dict) else {}
        extra = {"transient": True} if s.get("transient") else {}
        if s.get("late"):
            extra["snapshot"] = _snap_info(s, logged_at)
        return _ineligible(base, s.get("reason") or "no HLTV map chances for this match", **extra)
    sinfo = _snap_info(snap, logged_at)
    base["snapshot"] = sinfo
    chances = [c for c in snap.get("chances") or [] if isinstance(c.get("chance"), int) and c["chance"] >= 0]
    tot = sum(c["chance"] for c in chances)
    if not chances or tot <= 0:
        return _ineligible(base, "no HLTV map chances for this match")
    fm = P["maps_in_format"]
    k = fm.get(snap.get("format")) if snap.get("format") else None
    if k is None:
        k = int(round(tot / 100.0))
        if k not in fm.values():
            return _ineligible(base, f"map chances add up to {tot}%: not a BO1 / BO3 / BO5")
    if abs(tot - 100 * k) > P["chance_sum_tol"]:
        return _ineligible(base, f"map chances add up to {tot}%, not {100 * k}% for the format")
    n_maps = P["maps_in_prop"][scope]
    if n_maps > k:
        return _ineligible(base, "scope longer than the format")
    # ---- the player's per-map kills (window before the match day)
    if not isinstance(pk, dict) or not pk.get("ok"):
        s = pk if isinstance(pk, dict) else {}
        return _ineligible(base, s.get("reason") or "no HLTV per-map kills for the player",
                           **({"transient": True} if s.get("transient") else {}))
    n_all, k_all = int(pk.get("n") or 0), int(pk.get("kills") or 0)
    pinfo = {"id": player_id if player_id is not None else pk.get("player_id"), "since": pk.get("since"), "before": pk.get("before"),
             "n": n_all}
    base["player"] = pinfo
    if n_all < P["min_maps_total"]:
        return _ineligible(base, f"fewer than {P['min_maps_total']} HLTV maps in the {P['window_days']}-day window")
    a0 = k_all / n_all
    pinfo["avg"] = round(a0, 4)
    pm = pk.get("maps") or {}

    def a_of(key):
        e = pm.get(key) or {}
        return (e["kills"] / e["n"], e["n"], False) if (e.get("n") or 0) >= P["min_maps_on_map"] else (a0, e.get("n") or 0, True)
    per = {}
    for c in chances:
        a, n, fb = a_of(c["key"])
        per[c["map"]] = {"n": n, "avg": round(a, 4), **({"fallback": "overall"} if fb else {})}
    pinfo["per_map"] = per
    # ---- the projection
    if scope == "1":
        v1 = next((v for v in snap.get("vetoes") or [] if v.get("rank") == 1), None)
        m1 = (v1 or {}).get("map1")
        if not m1:
            return _ineligible(base, "no likeliest simulated veto before the match start")
        a, n, fb = a_of(m1["key"])
        base["map1"] = {"map": m1["map"], "step": m1.get("step"), "step_chance": m1.get("chance"), "veto_chance": v1.get("chance"),
                        "n": n, "avg": round(a, 4), **({"fallback": "overall"} if fb else {})}
        per_map = a
    else:
        per_map = sum(c["chance"] * a_of(c["key"])[0] for c in chances) / tot
    lp = live_p_more
    base["map_factor"] = round(per_map / a0, 6) if a0 > 0 else None
    base["eligible"] = True
    base[ARM] = _arm(ARM, prop, scope, line, pf, per_map * n_maps, P, lp, per_map)
    base[CONTROL] = _arm(CONTROL, prop, scope, line, pf, a0 * n_maps, P, lp, a0)
    lam_live_k = float(pf["lambda"]) if prop == "kills" else float(pf["lambda_kills"])
    base["live_replay"] = _arm("live-replay", prop, scope, line, pf, lam_live_k, P, None, lam_live_k / n_maps)["p_over"]
    return base


# ------------------------------------------------------------------ the pick-log glue (writer thread)
def inputs_of(res, g, gi, match_id=None):
    """What a pick row carries for the writer thread (small): the canonical match id, the forecast's match start, the HLTV
    player id the grade's HLTV numbers resolved to, the live chance and the grader inputs vm-2 reads."""
    nm = res.get("next_match") or {}
    hv = res.get("hltv") if isinstance(res.get("hltv"), dict) else {}
    if match_id is None:
        import veto_serve
        match_id = veto_serve.match_id_of(res)
    return {"match_id": match_id, "start": nm.get("start"), "player_id": hv.get("hltv_id") if hv.get("found") else None,
            "live_p_more": g.get("p_more"),
            "gi": {k: (dict(gi[k]) if isinstance(gi.get(k), dict) else gi.get(k)) for k in ("prop", "line", "scope", "bo", "perf")}}


def prefetch(vin, params=None):
    """The two cached reads for one forecast, OUTSIDE the pick-log lock: {"snap", "pk"} (either None when not needed).
    Skips both when the forecast is not a vm-2 cell, and the player read when there is no usable snapshot. Never raises."""
    try:
        P = _params(params)
        if not isinstance(vin, dict):
            return None
        cell, _ = cell_check(vin.get("gi"), P)
        if cell is None:
            return {"snap": None, "pk": None}
        import hltv_mapchances as hmc
        snap = hmc.map_chances(vin.get("match_id"), vin.get("start"))
        if not snap.get("ok"):
            return {"snap": snap, "pk": None}
        if not vin.get("player_id"):
            return {"snap": snap, "pk": {"ok": False, "reason": "no HLTV player id"}}
        day = _utc(snap.get("start_used"))            # the match day = the day of the start the snapshot was cut at
        pk = hmc.player_map_kills(vin["player_id"], day.date(), P["window_days"])
        return {"snap": snap, "pk": pk}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


def shadow(vin, pre, logged_at=None, params=None):
    """shadow['vm2'] of one CS2 forecast row (writer thread). Never raises."""
    try:
        vin = vin if isinstance(vin, dict) else {}
        pre = pre if isinstance(pre, dict) else {}
        if pre.get("error"):
            P = _params(params)
            base = {"version": version(P), "params_sha": params_sha(P), "code_sha": CODE_SHA, "match_id": vin.get("match_id")}
            return _ineligible(base, "shadow failed", eligible=None, error=pre["error"])
        return shadow_entry(vin.get("gi"), pre.get("snap"), pre.get("pk"), vin.get("live_p_more"), params, logged_at,
                            vin.get("match_id"), vin.get("player_id"))
    except Exception as e:  # noqa: BLE001
        return {"eligible": None, "reason": "shadow failed", "error": f"{type(e).__name__}: {str(e)[:120]}"}
