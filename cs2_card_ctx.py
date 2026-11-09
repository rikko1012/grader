"""The research hub's CS2 player card: the classic card's stat panels, copied from the graded result (display only).

Owner 2026-11-09: the all-sports hub card had dropped the classic card's panels (vs similar-ranked teams, LAN vs online, per-map,
HLTV, teammates, checks, FACEIT...). The board pass already builds the exact result the classic card prints (cs2_scout.grade);
build() copies the panels' numbers out of it, untouched, when the board grades a line, and the hub's /line doc serves that copy.
Nothing here is computed again and nothing here is a grade input: the grade, chance, stake and side never read it.

The copy lives in memory only (bounded, newest kept), so the board file on disk does not grow; a line graded before a restart shows
the panels once it is regraded. from_inputs() reads the few the saved forecast itself holds (the grade's own inputs: LAN / online,
similar-ranked teams, per map, rating...) for a line that has no copy yet."""
import copy
import threading
from collections import OrderedDict

MAX_ROWS = 4000
_LOCK = threading.Lock()
_STORE = OrderedDict()

# What each factor is to the grade (usesBlock in web/index.html: the classic card's "What this grade uses"). Factors move a grade
# only after a walk-forward test; every other factor is listed at weight 0.
USED_PERF = ("kpr", "environment", "sample", "opponent", "hs_per_kill", "rounds")
USED_CM2 = USED_PERF + ("elo", "map_length")


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _pick(d, keys):
    return {k: copy.deepcopy(d.get(k)) for k in keys if isinstance(d, dict) and d.get(k) is not None}


def _hltv(hl):
    if not isinstance(hl, dict) or not hl.get("found"):
        return None
    return _pick(hl, ("window", "maps", "rating", "ct_rating", "t_rating", "round_swing", "adr", "kast", "kd", "newest_map", "backup_maps",
                      "team_mismatch"))


def _ctx(hc):
    if not isinstance(hc, dict) or not hc.get("found"):
        return None
    out = _pick(hc, ("eco", "ranking", "opp_ranking", "environment", "next_match", "backup_source"))
    if isinstance(hc.get("prop_maps"), list):
        out["prop_maps"] = [_pick(m, ("map", "maps", "kpr", "rating", "adr")) for m in hc["prop_maps"][:7]]
    if isinstance(hc.get("h2h"), dict):
        out["h2h"] = _pick(hc["h2h"], ("wins", "losses", "map_win_rates"))
    if isinstance(hc.get("last5"), list):
        out["last5"] = [_pick(m, ("date", "map", "number", "opponent", "score", "kd", "rating", "backup", "match")) for m in hc["last5"][:10]]
    return out


def build(res):
    """The classic card's display panels of one graded result (cs2_scout.grade), as server.public() would print them. None when
    the result has no player block."""
    if not isinstance(res, dict):
        return None
    pb = res.get("player_block") or {}
    if not pb:
        return None
    o = pb.get("overall") or {}
    rf = pb.get("recent_form") or {}
    benched = {str(x).lower() for x in pb.get("benched") or []}          # benched players are not in the lineup (owner rule)
    mates = [{"n": n, "r": _num(r)} for n, r in (pb.get("teammate_ratings") or {}).items() if str(n).lower() not in benched]
    g = res.get("grade") or {}
    hc = _ctx(res.get("hltv_context"))
    rb = pb.get("_rank_band") or ((res.get("hltv_context") or {}).get("rank_band") if isinstance(res.get("hltv_context"), dict) else None)
    env = (hc or {}).get("environment") or pb.get("_env")      # the classic card prints HLTV's own split
    fi = res.get("faceit_identity") or {}
    fc = res.get("faceit") if fi.get("show_to_members") else None
    ca = res.get("csapi") if isinstance(res.get("csapi"), dict) and res["csapi"].get("found") else None
    nm = res.get("next_match") or {}
    src = str(g.get("projection_source") or "")
    return {
        "v": 1,
        "nick": (pb.get("player") or {}).get("nick"),
        "stats": {"window": o.get("window"), "maps_played": o.get("maps_played"), "rating": _num(o.get("rating")), "kast": _num(pb.get("kast")),
                  "dpr": _num(o.get("dpr")), "adr": _num(o.get("adr")), "kpr": _num(o.get("kpr")),
                  "form_windows": copy.deepcopy(o.get("form_windows")) or None, "env": o.get("form_windows_env"),
                  "kpr_recent": _num(rf.get("kpr_recent")), "trend_pct": _num(rf.get("trend_pct_vs_window"))},
        "per_map": [_pick(m, ("map", "kpr", "maps_played", "hs_pct")) for m in pb.get("per_map") or []][:10],
        "teammates": mates[:6], "me": {"n": (pb.get("player") or {}).get("nick"), "r": _num(o.get("rating"))},
        "team_kill_share": _num(pb.get("team_kill_share")),
        "checks": {"roster_confirmed": bool(pb.get("roster_confirmed")), "standin_for": pb.get("standin_for"), "awp_note": pb.get("awp_note"),
                   "trade_pr": _num(pb.get("trade_pr")), "opening": _pick(pb.get("opening") or {}, ("kills_per_round", "duel_win")) or None,
                   "sample": list(o.get("flags") or [])[:4], "days_since_last_map": _num(pb.get("days_since_last_map"))},
        "hltv": _hltv(res.get("hltv") or pb.get("hltv")),
        "context": hc,
        "rank_band": copy.deepcopy(rb) if isinstance(rb, dict) else None,
        "environment": copy.deepcopy(env) if isinstance(env, dict) else None,
        "recent_env": rf.get("env") if rf.get("env") in ("lan", "online") else None,
        "csapi": _pick(ca, ("window", "maps", "team", "vrs_rank", "on_roster", "kills_per_map", "rating", "swing", "adr", "kast", "ct", "t",
                            "newest_match", "team_mismatch")) if ca else None,
        "faceit": _pick(fc, ("nickname", "level", "elo", "recent_n", "avg_kills", "kpr", "avg_hs_pct")) if isinstance(fc, dict) else None,
        "match": {"event": nm.get("event"), "bo": nm.get("bo"), "maps": list(nm.get("maps") or [])[:5], "opponent": nm.get("opponent"),
                  "opponent_country": (res.get("opponent_nationality") or {}).get("label")},
        "p_win": _num((g.get("detail") or {}).get("p_win")) if g.get("market_quality") not in ("NO MARKET", "NONE") else None,
        "used": list(USED_CM2 if src == "perf-cm2" else USED_PERF if src and not src.startswith("legacy") else ()),
        "source": src or None,
    }


def from_inputs(gi):
    """The panels the saved forecast's own grader inputs hold (the same numbers the grade read): for a line with no copy yet."""
    if not isinstance(gi, dict):
        return None
    pm = [{"map": m.get("map"), "kpr": _num(m.get("kpr")), "maps_played": m.get("maps_played"), "hs_pct": _num(m.get("hs_pct"))}
          for m in gi.get("per_map") or [] if isinstance(m, dict) and m.get("map")]
    out = {"v": 1, "from_inputs": True,
           "stats": {k: _num(gi.get(k)) for k in ("rating", "kast", "dpr", "adr", "kpr")} | {"maps_played": gi.get("maps_played"), "kpr_recent": _num(gi.get("kpr_last5"))},
           "per_map": pm[:10],
           "checks": {"trade_pr": _num(gi.get("trade_pr")), "days_since_last_map": _num(gi.get("days_since_last_map")), "standin_for": gi.get("standin_for"),
                      "roster_confirmed": bool(gi.get("roster_confirmed"))},
           "rank_band": copy.deepcopy(gi.get("rank_band")) if isinstance(gi.get("rank_band"), dict) else None,
           "environment": copy.deepcopy(gi.get("environment")) if isinstance(gi.get("environment"), dict) else None}
    perf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
    src = "perf" if perf and not perf.get("error") else None
    out["used"] = list(USED_PERF) if src else []
    if not any(out["stats"].values()) and not pm and not out["rank_band"] and not out["environment"]:
        return None
    return out


def put(row_id, ctx):
    if row_id is None or ctx is None:
        return
    with _LOCK:
        _STORE[str(row_id)] = ctx
        _STORE.move_to_end(str(row_id))
        while len(_STORE) > MAX_ROWS:
            _STORE.popitem(last=False)


def get(row_id):
    with _LOCK:
        return _STORE.get(str(row_id))


def reset():
    with _LOCK:
        _STORE.clear()
