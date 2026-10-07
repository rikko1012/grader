#!/usr/bin/env python3
"""
server.py - the web interface for cs2_scout.

    python server.py            then open http://localhost:8000

GET /                   the page (web/index.html)
GET /api/search?q=      player suggestions from BO3.gg
GET /api/grade?player=&line=&prop=kills|hs&line_type=standard|goblin|demon[&slug=][&match=][&opponent=]
                        the full grade + supporting data

One grade runs at a time (keeps requests to BO3.gg polite). Standard library only.
"""
import collections
import json
import shutil
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import html as htmllib
from urllib.parse import parse_qs, quote, urlparse

import time
from datetime import datetime, timezone

import cs2_scout
import hltv
import picklog
import underdog
import whop_auth
import bo3
from bo3 import BO3, DATA_DIR, prune, storage_report

from polymarket import match_odds, upcoming_events

BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
PORT = int(os.environ.get("PORT", 8000))
# on a host (Railway sets PORT) listen on all interfaces; locally only this computer
HOST = os.environ.get("HOST") or ("0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
# pre-load upcoming matches in the background: on by default on the host, off on a PC
PRELOAD = os.environ.get("PRELOAD", "1" if "PORT" in os.environ else "0") == "1"
PRELOAD_HOURS = 36          # matches starting within this window
PRELOAD_EVERY = 30 * 60     # seconds between passes
REFRESH_AFTER = 6 * 3600    # re-load a player at most every 6 hours
# ---------------------------------------------------------------- load handling (700 at once)
# Grades run side by side (a single global lock once froze every member for ~60 s behind one
# cold look-up). Three layers:
# 1. identical requests share ONE computation, and its answer is reused for RESULT_TTL seconds
#    (a slate drop = hundreds of members grading the same props)
# 2. players already loaded run in the fast lane; players never seen get a few cold slots of
#    their own, so cold look-ups can never fill the fast lane
# 3. the same player is only assembled by one thread at a time (no duplicate BO3.gg fetches)
RESULT_TTL = 60
FAST_SLOTS = threading.BoundedSemaphore(6)    # CPU-bound (one Python core): more threads only thrash
COLD_SLOTS = threading.BoundedSemaphore(3)    # mostly waiting on BO3.gg (4 req/s shared)
WARM_FOR = 6 * 3600
_WARM = {}                                   # player key -> last successful grade time
WARM_FILE = DATA_DIR / ".cache" / "warm_players.json"   # survives restarts/deploys (volume)
_WARM_SAVED = [0.0]


def _load_warm():
    try:
        now = time.time()
        _WARM.update({k: v for k, v in json.loads(WARM_FILE.read_text("utf-8")).items() if now - v < WARM_FOR})
    except Exception:
        pass


def mark_warm(*keys):
    now = time.time()
    for k in keys:
        if k:
            _WARM[k.strip().lower()] = now
    if now - _WARM_SAVED[0] > 30:                # write at most every 30 s
        _WARM_SAVED[0] = now
        try:
            WARM_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = WARM_FILE.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({k: v for k, v in list(_WARM.items()) if now - v < WARM_FOR}), "utf-8")
            os.replace(tmp, WARM_FILE)
        except Exception:
            pass


_load_warm()
WARM_START = {"done": 0, "total": 0, "finished": False}


def warm_start():
    """After a restart/deploy, memory is empty and each player's first grade re-reads ~500 files
    (4-6 s). Reload the most recently used players in the background, newest first, so members
    don't pay that. Uses background priority: members' own look-ups always go first."""
    keys = sorted(_WARM, key=_WARM.get, reverse=True)[:400]
    WARM_START["total"] = len(keys)
    seen = set()
    for k in keys:
        try:
            res = cs2_scout.grade(k, 20.5, faceit_key=None, background=True)
            slug = ((res.get("player_block") or {}).get("player") or {}).get("slug")
            if slug in seen:
                continue
            seen.add(slug)
        except Exception:
            pass
        WARM_START["done"] += 1
    WARM_START["finished"] = True


_RESULTS, _INFLIGHT, _RG = {}, {}, threading.Lock()
_SEARCH = {}                                  # query -> (time, body, n_results, rows)
_PLAYER_LOCKS, _PLAYER_LOCKS_GUARD = {}, threading.Lock()
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", 60))   # grades per minute per IP (phone carriers share IPs)
_HITS, _HITS_GUARD = {}, threading.Lock()


_STORE = {"at": 0.0, "data": {}, "lock": threading.Lock()}


def storage_cached():
    """Disk usage for /health - scanning the cache folder is slow, so at most once a minute and
    never by two requests at once (400 parallel /health calls used to jam the server)."""
    if time.time() - _STORE["at"] > 60 and _STORE["lock"].acquire(blocking=False):
        try:
            _STORE["data"] = storage_report()
            _STORE["at"] = time.time()
        finally:
            _STORE["lock"].release()
    return _STORE["data"]


def rate_ok(ip):
    now = time.time()
    with _HITS_GUARD:
        if len(_HITS) > 50000:
            _HITS.clear()
        hits = [t for t in _HITS.get(ip, []) if now - t < 60]
        ok = len(hits) < RATE_LIMIT
        if ok:
            hits.append(now)
        _HITS[ip] = hits
        return ok


def player_lock(name):
    key = (name or "").strip().lower()
    with _PLAYER_LOCKS_GUARD:
        if len(_PLAYER_LOCKS) > 5000:
            _PLAYER_LOCKS.clear()
        return _PLAYER_LOCKS.setdefault(key, threading.Lock())


def shared(key, compute, ttl=RESULT_TTL):
    """Run compute() once per key: callers arriving while it runs wait for that answer, callers
    within `ttl` seconds after get it instantly. compute() returns (status, body_bytes)."""
    now = time.time()
    with _RG:
        hit = _RESULTS.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1], hit[2]
        ev = _INFLIGHT.get(key)
        leader = ev is None
        if leader:
            ev = _INFLIGHT[key] = threading.Event()
    if not leader:
        ev.wait(900)
        with _RG:
            hit = _RESULTS.get(key)
        if hit:
            return hit[1], hit[2]
        return shared(key, compute, ttl)                # the leader failed - try once ourselves
    try:
        status, body = compute()
        if status in (200, 404):
            with _RG:
                if len(_RESULTS) > 20000:
                    cut = time.time() - ttl
                    for k in [k for k, v in _RESULTS.items() if v[0] < cut]:
                        del _RESULTS[k]
                _RESULTS[key] = (time.time(), status, body)
        return status, body
    finally:
        with _RG:
            _INFLIGHT.pop(key, None)
        ev.set()


ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")   # set on Railway; no token = admin page disabled

PRELOAD_STATUS = {"enabled": PRELOAD, "players_ready": 0, "last_pass": None, "current": None, "errors": 0}


STORAGE = {"last_cleanup": None, "freed_mb": 0.0}


# ---------------------------------------------------------------- Underdog board (graded in the background)
BOARD = {}              # Underdog line id -> board row + its grade summary
BOARD_WORKERS = 3       # lines graded side by side (one at a time left most of the board ungraded for hours)
BOARD_EVERY = 15 * 60   # re-grade a line at most this often (members' grades are cached anyway)
BOARD_FILE = DATA_DIR / ".cache" / "board.json"


def _grader_version():
    """Changes whenever the grading formula (grader.py) changes: saved board grades from an older
    formula are dropped on startup instead of being shown."""
    import hashlib
    return hashlib.sha1(Path(__file__).with_name("grader.py").read_bytes()).hexdigest()[:12]


_BOARD_SAVE = threading.Lock()


def _board_save():
    with _BOARD_SAVE:
        _board_save_locked()


def _board_save_locked():
    try:
        tmp = BOARD_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": _grader_version(), "rows": BOARD}, default=str), encoding="utf-8")
        os.replace(tmp, BOARD_FILE)
    except Exception:
        pass


def _board_load():
    try:
        saved = json.loads(BOARD_FILE.read_text(encoding="utf-8"))
        if saved.get("version") == _grader_version():
            BOARD.update(saved.get("rows") or {})
    except Exception:
        pass


def _board_l10(rf, r):
    """Last-10 numbers in the line's own maps: Map 1 lines use his map-1 values, Maps 1-3 has no
    comparable record (shown as -), Maps 1-2 as before."""
    sc = r.get("scope") or "12"
    if sc == "12":
        return {"l10_over": rf.get("overs"), "l10_n": rf.get("matches"), "l10_avg": rf.get("avg_total")}
    if sc == "1":
        v = [s["values"][0] for s in (rf.get("series") or []) if s.get("values") and s["values"][0] is not None]
        return {"l10_over": sum(x > r["line"] for x in v) if v else None, "l10_n": len(v) or None,
                "l10_avg": round(sum(v) / len(v), 1) if v else None}
    return {"l10_over": None, "l10_n": None, "l10_avg": None}


def board_payload():
    b = underdog.board()
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in b["rows"]:
        if r.get("start") and r["start"] < now:
            continue                                  # started: locked on Underdog
        g = BOARD.get(r["id"]) or {}
        if not g.get("side"):
            continue                                  # not graded yet: never shown
        rows.append(dict(r, **{k: g.get(k) for k in ("grade", "side", "p", "units", "proj", "reason", "graded_at",
                                                     "nick", "state", "edge", "l10_over", "l10_n", "l10_avg",
                                                     "hs_pct", "p_win", "market", "team_name", "opp_name")}))
        rows[-1]["scope"] = r.get("scope") or "12"
    _cap_same_result(rows)
    return {"updated": b["at"], "error": b["error"], "rows": rows, "total": len(b["rows"])}


SAME_RESULT_CAP = 3.0   # units (on the 0.5-3U scale), all bets that need the same team to do well / badly in one match


def _cap_same_result(rows):
    """Bets on several players of one team, same side, in one match are one bet on that match's result
    (an upset or an overtime sinks them all). Their units together are capped at SAME_RESULT_CAP.
    Real lines Oct 2-6: total +15.75U on 236U staked -> +13.85U on 151U, worst day -6.8U -> -0.4U."""
    groups = {}
    for r in rows:
        if (r.get("units") or 0) > 0:
            groups.setdefault((r.get("matchup"), r.get("start"), r.get("team"), r.get("side")), []).append(r)
    for lst in groups.values():
        tot = sum(r["units"] for r in lst)
        if tot <= SAME_RESULT_CAP:
            continue
        f = SAME_RESULT_CAP / tot
        for r in lst:
            r["units_full"] = r["units"]
            r["units"] = max(0.5, round(r["units"] * f / 0.25) * 0.25)        # quarter units, never below 0.5U
            r["cap_n"] = len(lst)


REQ_TIMES = collections.deque(maxlen=300)   # member grade requests, step by step (admin timings)
MEMBER_GRADES = [0]     # member grades running right now (the board steps aside while any are)
_MG_LOCK = threading.Lock()
_BOARD_BUSY = threading.Lock()   # while members are grading, only one board grade runs at a time


def members_idle(max_wait=45):
    """Wait until no member grade is running (at most `max_wait` - then one board grade goes
    through, so the board never freezes during a rush). Quiet site: no wait at all."""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        if MEMBER_GRADES[0] <= 0:
            return
        time.sleep(2)


def board_forever():
    """Grade every Underdog CS2 line in the background (gives way to members), so the Board tab is
    instant. Each graded line is also logged (test=2: not a member's pick) so it settles and the
    grader can be measured on a large, unbiased set of real lines."""
    _board_load()                                     # last grades show right away after a restart
    time.sleep(20)
    while True:
        try:
            rows = underdog.board(wait=True)["rows"]
            now = datetime.now(timezone.utc).isoformat()
            todo = [r for r in rows if not r["alt"] and (not r.get("start") or r["start"] > now)
                    and time.time() - (BOARD.get(r["id"]) or {}).get("at", 0) > BOARD_EVERY]
            todo.sort(key=lambda r: r.get("start") or "9")   # soonest matches first

            def one(r):
                members_idle()                            # members first, but the board never stalls
                try:
                    if MEMBER_GRADES[0] > 0:              # members still busy: one board grade at a time
                        with _BOARD_BUSY:
                            res = cs2_scout.grade(r["player"], r["line"], r["prop"], "standard", scope=r.get("scope") or "12", team_hint=r.get("team"), opponent=r.get("opponent") or None,
                                                  faceit_key=faceit_key(), background=True, light=True)
                    else:
                        res = cs2_scout.grade(r["player"], r["line"], r["prop"], "standard", scope=r.get("scope") or "12", team_hint=r.get("team"), opponent=r.get("opponent") or None, faceit_key=faceit_key(),
                                              background=True, light=True)
                    if res.get("error"):
                        BOARD[r["id"]] = {"at": time.time(), "state": "NOT FOUND", "reason": res["error"]}
                        return
                    v = verdict(res.get("grade"))
                    g = res.get("grade") or {}
                    pb = res.get("player_block") or {}
                    rf = pb.get("recent_form") or {}
                    fw = ((pb.get("overall") or {}).get("form_windows") or {}).get("3m") or {}
                    BOARD[r["id"]] = {"at": time.time(), "graded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "grade": v.get("grade"), "side": v.get("side"), "p": v.get("p"),
                                      "units": v.get("units"), "proj": g.get("lambda_final"), "state": v.get("state"),
                                      "reason": v.get("reason"), "nick": (pb.get("player") or {}).get("nick"),
                                      "edge": round(g["lambda_final"] - r["line"] - 0.5, 1) if g.get("lambda_final") is not None else None,
                                      **_board_l10(rf, r),
                                      "hs_pct": fw.get("hs_pct"), "p_win": (g.get("detail") or {}).get("p_win"),
                                      "market": g.get("market_quality"),
                                      "team_name": pb.get("playing_for") or pb.get("current_team"),
                                      "opp_name": (res.get("next_match") or {}).get("opponent")}
                    res["verdict"] = v
                    try:
                        picklog.log_async(res, v, test=2)
                    except Exception:
                        traceback.print_exc()
                except Exception:
                    traceback.print_exc()
                if len(BOARD) % 10 == 0:
                    _board_save()

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(BOARD_WORKERS, thread_name_prefix="board") as ex:
                list(ex.map(one, todo))
            _board_save()
            if len(BOARD) > 5000:
                BOARD.clear()
        except Exception:
            traceback.print_exc()
        time.sleep(60)


def cleanup():
    """Keep the volume from filling: slim/compress old files, drop stale ones."""
    try:
        freed = prune() + cs2_scout.prune_misc()
        STORAGE["freed_mb"] = round(STORAGE["freed_mb"] + freed / 1e6, 1)
        STORAGE["last_cleanup"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        traceback.print_exc()


def preload_forever():
    """Grade every rostered player of every upcoming match once, so members' look-ups hit
    saved data. Matches with a Polymarket market first (those get prop lines), then by start."""
    warmed = {}   # slug -> time loaded
    while True:
        cleanup()
        try:
            b = BO3(background=True)
            try:
                slate = cs2_scout.slate(b, PRELOAD_HOURS)
                try:
                    events = upcoming_events()
                    for m in slate:
                        m["pm"] = match_odds(m["team1"], m["team2"], events).get("found", False)
                except Exception:
                    for m in slate:
                        m["pm"] = False
                slate.sort(key=lambda m: (not m["pm"], m["start"] or ""))
                queue = []
                for m in slate:
                    for side, other in (("team1", "team2"), ("team2", "team1")):
                        try:
                            roster = [p for p in (b.team(m[side + "_slug"]).get("players") or [])
                                      if not p.get("is_coach")]
                        except Exception:
                            continue
                        queue += [(p["nickname"], p["slug"]) for p in roster]
            finally:
                b.close()
            try:
                sb = BO3(background=True)
                try:
                    PRELOAD_STATUS["settled_last_pass"] = picklog.settle(sb, cs2_scout.map_rows)
                finally:
                    sb.close()
            except Exception:
                traceback.print_exc()
            for nick, slug in queue:
                if time.time() - warmed.get(slug, 0) < REFRESH_AFTER:
                    continue
                PRELOAD_STATUS["current"] = nick
                try:
                    cs2_scout.grade(nick, 20.5, slug=slug, faceit_key=None, background=True)
                    warmed[slug] = time.time()
                    mark_warm(slug, nick)
                except Exception:
                    PRELOAD_STATUS["errors"] += 1
                PRELOAD_STATUS["players_ready"] = sum(1 for t in warmed.values() if time.time() - t < REFRESH_AFTER * 2)
            PRELOAD_STATUS["current"] = None
            PRELOAD_STATUS["last_pass"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        except Exception:
            traceback.print_exc()
            PRELOAD_STATUS["errors"] += 1
        time.sleep(PRELOAD_EVERY)


def faceit_key():
    """FACEIT_API_KEY from this process, else from the Windows user environment (setx)."""
    key = os.environ.get("FACEIT_API_KEY")
    if key or sys.platform != "win32":
        return key
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, "FACEIT_API_KEY")[0]
    except OSError:
        return None


def verdict(g):
    """Always a side: the one the numbers favour, with its grade. Units follow P: B = 0.25U
    (small edge), C = 0U (loses in testing) but is still a pick."""
    if not g or "p_more" not in g:
        return {"side": None, "grade": (g or {}).get("grade", "NO GRADE"), "units": 0.0,
                "reason": (g or {}).get("reason")}
    if g["p_more"] != g["p_less"]:
        side = "More" if g["p_more"] > g["p_less"] else "Less"
    else:
        side = "More" if g["lambda_final"] > g["line"] else "Less"
    # goblin/demon lines have no Less on PrizePicks. If the model expects UNDER, say so (Lean
    # Under, 0 units - skip it) instead of headlining a More it thinks loses (azukay 17.5 demon:
    # printed "More 42%", he went under).
    no_less_side = side == "Less" and g["line_type"] != "standard"
    p = g["p_more"] if side == "More" else g["p_less"]
    grade = g["grade"] if side == "More" else (g["grade_less"] if not no_less_side else "C")
    units = g.get("units", 0) if side == "More" else g.get("units_less", 0)
    if no_less_side:
        units = 0.0
    stat = "headshots" if g.get("prop") == "hs" else "kills"
    need = g["line"] + 0.5                  # 15.5 line -> he needs 16 for More
    gap = g["lambda_final"] - need          # > 0 always means More, < 0 always Less
    if g.get("scout"):
        return {"side": side, "state": "SCOUT", "grade": "SCOUT", "p": p, "units": 0.0,
                "confidence": "scout",
                "reason": f"No upcoming match found for his team - scouting view only. Leans {side} "
                          f"({p:.0%}): projection {g['lambda_final']:.1f} vs line {g['line']}"}
    if no_less_side:
        why = (f"{g['line_type'].title()} line - PrizePicks only offers More here, but the model expects "
               f"UNDER ({p:.0%}); More is only {g['p_more']:.0%}. Skip it.")
    elif g.get("form_fight"):
        why = f"FORM FIGHT - his last 10 matches disagree with the projection - no units"
    elif g.get("tape_fight"):
        why = (f"TAPE FIGHT - model {side} {p:.0%}, but his recent record at this line disagrees - max 0.5U")
    elif units > 0:
        why = f"Projection {g['lambda_final']:.1f} vs {need:g} needed ({gap:+.1f} {stat})"
    else:
        why = (f"Projection {g['lambda_final']:.1f} vs {need:g} needed ({gap:+.1f} {stat}) is small next "
               f"to how much his totals swing from match to match - leans {side} {p:.0%}, 0 units")
    if g.get("zero_reason") and not no_less_side:
        # a gate (AWPer on HS, stand-in, transfer, tiny sample): still a side, never "no play"
        grade, units = "LEAN", 0.0
        why = (f"Leans {side} {p:.0%} (projection {g['lambda_final']:.1f} vs {need:g} needed) - 0 units: "
               f"{g['zero_reason']}")
    if grade == "C":
        # owner 2026-09-29: under 57% the card says "Lean Over" / "Lean Under" instead of a C
        grade = "LEAN"
    return {"side": side, "state": "GRADE", "grade": grade, "p": p, "units": units, "reason": why,
            "market": g.get("market_quality"),
            "confidence": "high" if p >= 0.68 else "medium" if p >= 0.63 else "small" if p >= 0.57 else "low"}


def public(res):
    """What members see: drop admin-only details."""
    fi = res.pop("faceit_identity", None) or {}
    if not fi.get("show_to_members"):
        res.pop("faceit", None)
    res.pop("grader_inputs", None)
    res.pop("_h2h_rows", None)
    res.pop("_h2h_opp", None)
    (res.get("player_block") or {}).pop("_rf_kpr", None)
    (res.get("player_block") or {}).pop("_hltv_overall_tried", None)
    res.pop("_opp_roster", None)
    (res.get("player_block") or {}).pop("_env", None)
    for k in ("_opp_rows", "_opp_rank"):
        (res.get("player_block") or {}).pop(k, None)
    rb = (res.get("player_block") or {}).pop("_rank_band", None)
    if rb and isinstance(res.get("hltv_context"), dict):
        res["hltv_context"]["rank_band"] = rb          # the one the grade used
    res.pop("opponent_defense", None)      # shadow feature - admin log only until it is proven
    pb = res.get("player_block") or {}
    for r in pb.get("recent_maps") or []:
        r.pop("steam_id", None)
        r.pop("lineup", None)
    pb.pop("steam_id", None)
    protect_recipe(res)
    return res


# ---------------------------------------------------------------- keep the recipe private
# Members see inputs (stats, form, H2H, match, odds) and outputs (projection, chance, grade,
# units). The recipe - weights, constants, spread, multipliers, internal factors - never leaves
# the server (the page AND the raw API response), so the grader can't be copied from it.
KEEP_DETAIL = {"p_win", "p_map1", "p_map2", "maps_used", "role", "rank"}
_FLAG_RULES = (   # (starts with, member-friendly text or None = not shown)
    ("Gate C: roster", "Roster not confirmed - extra caution"),
    ("Gate D: only", "Small sample of recent official maps - extra caution, unit size limited"),
    ("Thin data", "Small sample of recent official maps - extra caution, unit size limited"),
    ("Gate A", None), ("Heavy favourite", None), ("Underdog HS", None), ("Role unconfirmed", None),
    ("Support profile", None), ("Stack capped", None), ("Kills props beat", None),
    ("No moneyline", None), ("Only one moneyline", None),
    ("SOFT BOOK", "Thin betting market - unit size limited"),
    ("NO MARKET", "No betting market for this match - unit size limited"),
    ("STAND-IN", "Stand-in - playing for another team, unit size limited"),
    ("VETO UNCERTAINTY", "Map veto is hard to predict - extra caution"),
    ("FORM FIGHT", "FORM FIGHT - his recent record at this line disagrees - no units"),
    ("TAPE FIGHT", "TAPE FIGHT - his recent record at this line disagrees - small stake only"),
    ("0 units: SCOUT", "No upcoming match found - scouting view only"),
    ("0 units", None),
)
_REASONS = (("Gate A", "AWPer on a headshot prop"), ("Gate D", "No recent official maps to grade"),
            ("Gate C", "Not on this team's roster"), ("Missing", "Not enough data to grade"))


def _public_flag(f):
    if f.startswith("RUST") or f.startswith("Note: last official map"):
        days = next((w for w in f.split() if w.isdigit()), None)
        return (f"Last official map {days} days ago" + (" - extra caution" if f.startswith("RUST") else "")) if days else None
    if f.startswith("TAPE:"):
        return "Projection differs from his last-10 average"
    if ": no Maps 1-2 on this map" in f:
        return f.split(" - ")[0]
    for start, text in _FLAG_RULES:
        if f.startswith(start):
            return text
    return None                                     # anything not listed stays private


def protect_recipe(res):
    g = res.get("grade")
    if isinstance(g, dict):
        flags = []
        for f in g.get("flags") or []:
            t = _public_flag(f)
            if t and t not in flags:
                flags.append(t)
        g["flags"] = flags
        g["detail"] = {k: v for k, v in (g.get("detail") or {}).items() if k in KEEP_DETAIL}
        for k in ("lambda_base", "sd", "value", "market", "model_grade", "p_more_model", "h2h", "regional"):
            g.pop(k, None)
        if g.get("reason"):
            g["reason"] = next((t for k, t in _REASONS if g["reason"].startswith(k)), "Not graded")
    sim = res.get("simulation")
    if isinstance(sim, dict):
        res["simulation"] = {k: sim.get(k) for k in ("p_more", "mean", "p10", "p50", "p90", "scenarios", "anchored")}
    res.pop("simulation_error", None)
    o = (res.get("player_block") or {}).get("overall") or {}
    for k in ("kpr", "hs_pct", "form_blend", "kpr_single_window", "hs_single_window"):
        o.pop(k, None)                              # blended numbers + their weights stay private


def memory_mb():
    """Resident memory of this process (Linux/Railway); None elsewhere."""
    try:
        with open("/proc/self/status") as f:
            return round(int(next(l for l in f if l.startswith("VmRSS")).split()[1]) / 1024)
    except Exception:
        return None


_PAGE = {"mtime": None, "data": b""}


def page():
    """index.html from memory (re-read only when the file changes)."""
    f = WEB / "index.html"
    mt = f.stat().st_mtime
    if _PAGE["mtime"] != mt:
        _PAGE["data"], _PAGE["mtime"] = f.read_bytes(), mt
    return _PAGE["data"]


STATS = {"requests": 0, "ms_total": 0.0, "since": time.time(), "errors_5xx": 0}
LAST_MEMBER = [0.0]     # last member request on /api/* (the board worker steps aside while members are active)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"      # keep-alive: browsers/Railway's proxy reuse connections
    timeout = 30                       # idle keep-alive connections are closed after 30 s

    def log_message(self, fmt, *a):
        pass                           # no line per request (700 members = thousands of log lines)

    def log_error(self, fmt, *a):
        sys.stderr.write("  " + fmt % a + "\n")

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        STATS["requests"] += 1
        STATS["ms_total"] += (time.time() - getattr(self, "_t0", time.time())) * 1000
        if code >= 500:
            STATS["errors_5xx"] += 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def member(self):
        """The signed-in Whop member's session (or None)."""
        return whop_auth.session(self.cookie(whop_auth.COOKIE)) if whop_auth.enabled() else None

    def allowed(self):
        """Grading/search/board: paid Whop members (and the owner's load tests) only. On Railway the data
        stays locked if the Whop settings are ever missing - it never opens up to everyone."""
        if not whop_auth.enabled():
            return not (os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_ENVIRONMENT_NAME")
                        or os.environ.get("REQUIRE_WHOP"))
        if ADMIN_TOKEN and self.headers.get("X-Load-Test") == ADMIN_TOKEN:
            return True
        sess = self.member()
        return bool(sess and sess["ok"])

    def redirect(self, location, cookies=()):
        STATS["requests"] += 1
        self.send_response(302)
        self.send_header("Location", location)
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        self._t0 = time.time()
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/health":
                return self.send(200, {"ok": True, "faceit_key": bool(faceit_key()), "preload": PRELOAD_STATUS, "warm_start": WARM_START,
                                       "whop_signin": {"on": whop_auth.enabled(), "recent_errors": whop_auth.LAST_ERRORS[-3:]},
                                       "memory_mb": memory_mb(), "threads": threading.active_count(),
                                       "caches": {"results": len(_RESULTS), "contexts": len(cs2_scout._CONTEXT),
                                                  "scoreboards": len(bo3._MEM), "expiring": len(bo3._TMEM)},
                                       "traffic": {"requests": STATS["requests"], "errors_5xx": STATS["errors_5xx"],
                                                   "avg_server_ms": round(STATS["ms_total"] / max(STATS["requests"], 1), 1),
                                                   "since": datetime.fromtimestamp(STATS["since"], timezone.utc).isoformat(timespec="seconds")},
                                       "storage": dict(storage_cached(), **STORAGE)})
            if u.path in ("/", "/index.html"):
                return self.send(200, page(), "text/html; charset=utf-8")
            # ---- Sign in with Whop (paid product only). Off until the WHOP_* variables are set.
            if u.path == "/auth/me":
                sess = self.member()
                return self.send(200, {"required": whop_auth.enabled(), "signed_in": bool(sess),
                                       "member": bool(sess and sess["ok"]), "name": (sess or {}).get("name"),
                                       "upgrade": whop_auth.upgrade_url()})
            if u.path == "/auth/login" and whop_auth.enabled():
                url, pkce = whop_auth.start_login()
                return self.redirect(url, [f"{whop_auth.PKCE_COOKIE}={pkce}; Max-Age=900; Path=/; HttpOnly; Secure; SameSite=Lax"])
            if u.path == "/auth/callback" and whop_auth.enabled():
                value, msg = whop_auth.finish_login(q.get("code", ""), q.get("state", ""),
                                                     self.cookie(whop_auth.PKCE_COOKIE))
                if not value:
                    return self.redirect("/?signin_error=" + quote(msg))
                return self.redirect("/", [f"{whop_auth.COOKIE}={value}; Max-Age={whop_auth.SESSION_DAYS * 86400}; "
                                           f"Path=/; HttpOnly; Secure; SameSite=Lax",
                                           f"{whop_auth.PKCE_COOKIE}=; Max-Age=0; Path=/"])
            if u.path == "/auth/logout":
                return self.redirect("/", [f"{whop_auth.COOKIE}=; Max-Age=0; Path=/"])
            if u.path in ("/api/search", "/api/grade", "/api/board") and not self.allowed():
                sess = self.member()
                return self.send(401, {"error": "members_only", "signed_in": bool(sess),
                                       "upgrade": whop_auth.upgrade_url()})
            if u.path == "/api/search":
                return self.search(q.get("q", "").strip())
            if u.path.startswith("/api/") and u.path != "/api/board":
                LAST_MEMBER[0] = time.time()
            if u.path == "/api/grade":
                with _MG_LOCK:
                    MEMBER_GRADES[0] += 1
                try:
                    return self.grade(q)
                finally:
                    with _MG_LOCK:
                        MEMBER_GRADES[0] -= 1
            if u.path == "/api/board":
                return self.send(200, board_payload())
            if u.path == "/admin":
                return self.admin(q)
            if u.path == "/admin/picks.json" and self.authed(q.get("token")):
                return self.send(200, {"picks": picklog.export()})
            if u.path == "/admin/mark_tests" and self.authed(q.get("token")):
                ids = [int(x) for x in (q.get("ids") or "").split(",") if x.strip().isdigit()]
                picklog.mark_tests(ids)
                return self.send(200, {"marked": len(ids), "pushes_fixed": picklog.resettle_pushes()})
            if u.path == "/admin/whop_plan" and self.authed(q.get("token")) and whop_auth.enabled():
                # private: members on one plan and whether the grader lets each in (Whop read-only)
                import requests as _rq
                r = _rq.get(whop_auth.MEMBERSHIPS, timeout=20,
                            params={"plan_id": q.get("plan", ""), "first": int(q.get("n", 15))},
                            headers={"Authorization": f"Bearer {os.environ.get('WHOP_API_KEY', '')}"})
                if r.status_code != 200:
                    return self.send(200, {"whop_status": r.status_code, "whop_error": whop_auth._why(r)})
                rows = []
                for m in r.json().get("data") or []:
                    uid = m.get("user_id")
                    rows.append({"membership": m.get("id"), "status": m.get("status"), "plan": m.get("plan_id"),
                                 "product": m.get("product_id"),
                                 "grader_allows": whop_auth.has_access(uid, force=True) if uid else None})
                return self.send(200, {"plan": q.get("plan"), "memberships": rows})
            if u.path == "/admin/hltv_schema" and self.authed(q.get("token")):
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("""SELECT table_name, string_agg(column_name || ':' || data_type, ', ' ORDER BY ordinal_position)
                                       FROM information_schema.columns WHERE table_schema = 'public'
                                       GROUP BY table_name ORDER BY table_name""")
                        return self.send(200, {"tables": {t: c for t, c in cur.fetchall()}})
                finally:
                    con.close()
            if u.path == "/admin/disk" and self.authed(q.get("token")):
                # private: what takes space on the volume (bytes vs blocks actually allocated)
                from bo3 import DATA_DIR
                rows = {}
                for p in Path(DATA_DIR).rglob("*"):
                    try:
                        if p.is_file():
                            st = p.stat()
                            top = p.relative_to(DATA_DIR).parts
                            k = "/".join(top[:2]) if len(top) > 2 else top[0]
                            r = rows.setdefault(k, [0, 0, 0])
                            r[0] += 1; r[1] += st.st_size; r[2] += getattr(st, "st_blocks", 0) * 512
                    except OSError:
                        pass
                du = shutil.disk_usage(DATA_DIR)
                return self.send(200, {"dir": str(DATA_DIR), "total_mb": round(du.total / 1e6), "used_mb": round(du.used / 1e6),
                                       "parts": sorted(({"path": k, "files": a, "mb": round(b / 1e6, 1), "on_disk_mb": round(c / 1e6, 1)}
                                                        for k, (a, b, c) in rows.items()), key=lambda x: -x["on_disk_mb"])[:40]})
            if u.path == "/admin/hltv_match" and self.authed(q.get("token")):
                # private, read-only: a team's HLTV matches on a date + the stored raw page (inspection)
                import gzip as _gz, hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("""SELECT DISTINCT mo.matchid, mo.event, mo.date FROM match_overview mo
                                       JOIN matches m USING (matchid) JOIN teams t ON t.teamid = m.teamid
                                       WHERE lower(t.name) = lower(%s) AND mo.date = %s ORDER BY mo.matchid""",
                                    (q.get("team", ""), q.get("date", "")))
                        rows = [{"matchid": a, "event": b, "date": str(c)} for a, b, c in cur.fetchall()]
                        for r in rows:
                            cur.execute("SELECT url, html_gz FROM raw_pages WHERE matchid = %s", (r["matchid"],))
                            pg = cur.fetchall()
                            r["pages"] = [{"url": u_, "html": _gz.decompress(bytes(h)).decode("utf-8", "replace")
                                           if q.get("html") else None, "bytes": len(h or b"")} for u_, h in pg]
                        return self.send(200, {"matches": rows})
                finally:
                    con.close()
            if u.path == "/admin/recheck_hltv" and self.authed(q.get("token")):
                changed = picklog.recheck_with_hltv()
                return self.send(200, {"changed": len(changed), "rows": changed})
            if u.path == "/admin/ud_moves" and self.authed(q.get("token")):
                import sqlite3
                db = BOARD_FILE.parent / "ud_moves.db"
                if not db.exists():
                    return self.send(200, {"rows": []})
                con = sqlite3.connect(db, timeout=10)
                try:
                    rows = con.execute("SELECT * FROM moves WHERE at >= ? ORDER BY at", (q.get("since", ""),)).fetchall()
                finally:
                    con.close()
                return self.send(200, {"rows": rows})
            if u.path == "/admin/hltv_env_rows" and self.authed(q.get("token")):
                # private analysis: every player-map since `since` with LAN / online (match_overview.lan)
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 120000")
                        cur.execute("""SELECT p.name, t.name, mo.date::text, mo.lan, mo.event, mo.matchid, ps.k, own.score + opp.score
                                       FROM player_stats ps JOIN players p USING (playerid) JOIN teams t ON t.teamid = ps.teamid
                                       JOIN match_overview mo ON mo.matchid = ps.matchid
                                       JOIN match_maps mm ON mm.matchid = ps.matchid AND mm.mapid = ps.mapid
                                       JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid AND own.mapid = ps.mapid AND own.sideid = 0
                                       JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
                                       WHERE ps.sideid = 0 AND mo.date >= %s""", (q.get("since", "2025-09-01"),))
                        rows = cur.fetchall()
                finally:
                    con.close()
                return self.send(200, {"rows": rows})
            if u.path == "/admin/hltv_team_maps" and self.authed(q.get("token")):
                # private analysis: every map since `since` - date, event, number, map, both teams + scores
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 60000")
                        cur.execute("""SELECT mo.matchid, mo.date::text, mo.event, mm.number, mp.name, t1.name, a.score,
                                              t2.name, b.score
                                       FROM match_maps mm JOIN match_overview mo USING (matchid) JOIN maps mp ON mp.mapid = mm.mapid
                                       JOIN matches a ON a.matchid = mm.matchid AND a.mapid = mm.mapid AND a.sideid = 0
                                       JOIN matches b ON b.matchid = mm.matchid AND b.mapid = mm.mapid AND b.sideid = 0
                                                      AND b.teamid > a.teamid
                                       JOIN teams t1 ON t1.teamid = a.teamid JOIN teams t2 ON t2.teamid = b.teamid
                                       WHERE mo.date >= %s ORDER BY mo.date, mo.matchid, mm.number""",
                                    (q.get("since", "2025-09-01"),))
                        rows = cur.fetchall()
                finally:
                    con.close()
                return self.send(200, {"rows": rows})
            if u.path == "/admin/hltv_rank_rows" and self.authed(q.get("token")):
                # private analysis: every player-map (total side) with both teams' VRS rank at the time
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 60000")
                        cur.execute("""
                            SELECT mo.matchid, mo.date::text, ps.playerid, ps.teamid, opp.teamid, ps.mapid, ps.k,
                                   own.score, opp.score, mm.number,
                                   (SELECT rank FROM team_rankings tr WHERE tr.teamid = ps.teamid ORDER BY abs(tr.date - mo.date) LIMIT 1),
                                   (SELECT rank FROM team_rankings tr WHERE tr.teamid = opp.teamid ORDER BY abs(tr.date - mo.date) LIMIT 1)
                            FROM player_stats ps
                            JOIN match_overview mo USING (matchid)
                            JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid AND own.mapid = ps.mapid AND own.sideid = 0
                            JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
                            LEFT JOIN match_maps mm ON mm.matchid = ps.matchid AND mm.mapid = ps.mapid
                            WHERE ps.sideid = 0 AND ps.mapid <> 0 AND mo.date >= current_date - %s""", (int(q.get("days") or 120),))
                        rows = cur.fetchall()
                    return self.send(200, {"rows": rows})
                finally:
                    con.close()
            if u.path == "/admin/hltv_player_opps" and self.authed(q.get("token")):
                # private: a player's HLTV opponents (12 months) with their latest rank
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("""SELECT p.playerid, p.name, count(*) FROM players p JOIN player_stats ps USING (playerid)
                                       WHERE lower(regexp_replace(p.name, '[^a-zA-Z0-9]', '', 'g')) = %s GROUP BY 1, 2""",
                                    (hltv._norm(q.get("nick")),))
                        cands = cur.fetchall()
                        ids = hltv._player_ids(cur, q.get("nick"), q.get("team"))
                        cur.execute("""WITH ranks AS (SELECT DISTINCT ON (teamid) teamid, rank FROM team_rankings ORDER BY teamid, date DESC)
                                       SELECT t2.name, rk.rank, count(*), min(mo.date)::text, max(mo.date)::text
                                       FROM player_stats ps JOIN match_overview mo USING (matchid)
                                       JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
                                       JOIN teams t2 ON t2.teamid = opp.teamid LEFT JOIN ranks rk ON rk.teamid = opp.teamid
                                       WHERE ps.playerid = ANY(%s) AND ps.sideid = 0 AND ps.mapid <> 0
                                       GROUP BY 1, 2 ORDER BY 2 NULLS LAST""", (ids,))
                        opps = cur.fetchall()
                        cur.execute("SELECT min(date)::text, max(date)::text, count(*) FROM match_overview")
                        span = cur.fetchone()
                    return self.send(200, {"candidates": cands, "ids_used": ids, "opponents": opps, "db_span": span})
                finally:
                    con.close()
            if u.path == "/admin/hltv_status" and self.authed(q.get("token")):
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        out = {}
                        for lab, sql in (
                            ("matches_45d", "SELECT count(*) FROM match_overview WHERE date >= current_date - 45"),
                            ("matches_7d", "SELECT count(*) FROM match_overview WHERE date >= current_date - 7"),
                            ("maps_45d", "SELECT count(*) FROM match_maps mm JOIN match_overview mo USING (matchid) WHERE mo.date >= current_date - 45"),
                            ("maps_with_hs_45d", "SELECT count(DISTINCT h.mapstatsid) FROM map_headshots h JOIN match_overview mo USING (matchid) WHERE mo.date >= current_date - 45"),
                            ("maps_with_hs_7d", "SELECT count(DISTINCT h.mapstatsid) FROM map_headshots h JOIN match_overview mo USING (matchid) WHERE mo.date >= current_date - 7"),
                            ("maps_7d", "SELECT count(*) FROM match_maps mm JOIN match_overview mo USING (matchid) WHERE mo.date >= current_date - 7"),
                            ("hs_failures", "SELECT count(*) FROM mapstats_status"),
                            ("teams", "SELECT count(*) FROM teams"),
                            ("newest_match", "SELECT max(date)::text FROM match_overview")):
                            try:
                                cur.execute(sql); out[lab] = cur.fetchone()[0]
                            except Exception as e:
                                con.rollback() if not con.autocommit else None
                                out[lab] = f"error: {str(e)[:80]}"
                        cur.execute("SELECT last_error, count(*) FROM mapstats_status GROUP BY 1 ORDER BY 2 DESC LIMIT 3")
                        out["hs_failure_reasons"] = cur.fetchall()
                        return self.send(200, out)
                finally:
                    con.close()
            if u.path == "/admin/veto_backtest" and self.authed(q.get("token")):
                import hltv
                return self.send(200, hltv.veto_backtest(int(q.get("days") or 30), int(q.get("limit") or 250)))
            if u.path == "/admin/hltv_maps" and self.authed(q.get("token")):
                import hltv
                items = json.loads(q.get("items") or "[]")[:80]      # [[nick, team, day], ...]
                out = []
                for n, t, dday in items:
                    try:
                        out.append({"nick": n, "team": t, "day": dday, "maps": hltv.match_maps_detail(n, t, dday)})
                    except Exception as e:
                        out.append({"nick": n, "team": t, "day": dday, "error": str(e)[:200]})
                return self.send(200, {"rows": out})
            if u.path == "/admin/hltv_history" and self.authed(q.get("token")):
                import hltv
                items = json.loads(q.get("items") or "[]")[:60]      # [[nick, cutoff, team], ...]
                return self.send(200, {"rows": [dict(hltv.history(n, c, t), nick=n, cutoff=c) for n, c, t in items]})
            if u.path == "/admin/reopen" and self.authed(q.get("token")):
                ids = [int(x) for x in (q.get("ids") or "").split(",") if x.strip().isdigit()]
                picklog.reopen(ids)
                return self.send(200, {"reopened": len(ids)})
            if u.path == "/admin/settle_now" and self.authed(q.get("token")):
                sb = BO3(background=True)
                try:
                    n = picklog.settle(sb, cs2_scout.map_rows)
                finally:
                    sb.close()
                return self.send(200, {"settled": n})
            if u.path == "/admin/timings" and self.authed(q.get("token")):
                return self.send(200, {"member_grades_now": MEMBER_GRADES[0], "board_size": len(BOARD),
                                       "threads": [t.name for t in threading.enumerate()],
                                       "requests": list(REQ_TIMES)[-int(q.get("n", 60)):],
                                       "recent": list(cs2_scout.TIMINGS)[-int(q.get("n", 60)):]})
            if u.path == "/admin/probe" and self.authed(q.get("token")):
                # private: raw shape of the backup data sources (keys never included)
                import oddspapi
                return self.send(200, {"oddspapi": oddspapi.probe()})
            return self.send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": f"Something went wrong: {e}"})

    def search(self, q):
        if len(q) < 2:
            return self.send(200, {"results": []})
        ql = q.lower()
        # typing: a longer query can be answered from a shorter one's complete list (<8 hits)
        try:                                             # HLTV first (instant, in memory); BO3.gg only if it's down
            hl = hltv.search_players(q, 8)
        except Exception:
            hl = None
        if hl is not None:
            return self.send(200, {"results": hl})
        hit = _SEARCH.get(ql)
        if hit and time.time() - hit[0] < 3600:
            return self.send(200, hit[1])
        for k in range(len(ql) - 1, 1, -1):              # typing: a shorter query's complete list
            prev = _SEARCH.get(ql[:k])
            if prev and time.time() - prev[0] < 3600 and prev[2] < 8:
                keep = [h for h in prev[3] if ql in (h["nick"] or "").lower() or ql in (h["name"] or "").lower()]
                body = json.dumps({"results": keep}).encode()
                _SEARCH[ql] = (time.time(), body, len(keep), keep)
                return self.send(200, body)

        def compute():
            b = BO3()
            try:
                hits = b.search_players(q, 8).get("results") or []
            finally:
                b.close()
            return 200, json.dumps({"results": [
                {"slug": h["slug"], "nick": h.get("nickname"),
                 "name": " ".join(x for x in (h.get("first_name"), h.get("last_name")) if x),
                 "country": (h.get("country") or {}).get("code"), "image": h.get("image_url")}
                for h in hits]}).encode()
        status, body = shared(("search", ql), compute, ttl=3600)
        if status == 200:
            rows = json.loads(body)["results"]
            if len(_SEARCH) > 20000:
                _SEARCH.clear()
            _SEARCH[ql] = (time.time(), body, len(rows), rows)
        return self.send(status, body)

    def client_ip(self):
        fwd = self.headers.get("X-Forwarded-For") or ""          # Railway's proxy puts the member first
        return fwd.split(",")[0].strip() or self.client_address[0]

    def grade(self, q):
        load_test = bool(ADMIN_TOKEN) and self.headers.get("X-Load-Test") == ADMIN_TOKEN   # owner's own tests
        if not load_test and not rate_ok(self.client_ip()):
            return self.send(429, {"error": f"Too many grades - limit is {RATE_LIMIT} a minute. "
                                            f"Wait a moment and try again."})
        name = q.get("player", "").strip()
        try:
            line = float(q.get("line", ""))
        except ValueError:
            return self.send(400, {"error": "Enter the line as a number, e.g. 27.5"})
        if not name:
            return self.send(400, {"error": "Enter a player name"})
        prop = q.get("prop", "kills") if q.get("prop") in ("kills", "hs") else "kills"
        lt = q.get("line_type", "standard") if q.get("line_type") in ("standard", "goblin", "demon") else "standard"
        scope = q.get("maps") if q.get("maps") in ("12", "123", "1") else "12"
        slug, match = q.get("slug") or None, q.get("match") or None
        opp = (q.get("opponent") or "")[:60].strip() or None
        pkey = (slug or name).lower()
        key = (pkey, line, prop, lt, match, (opp or "").lower(), scope)

        t_in = time.time()

        def compute():
            tm = {"queued": round(time.time() - t_in, 2)}
            t0 = time.time()
            with player_lock(pkey):                     # one assembly per player at a time
                tm["player_lock"] = round(time.time() - t0, 2)
                warm = time.time() - _WARM.get(pkey, 0) < WARM_FOR
                t0 = time.time()
                with (FAST_SLOTS if warm else COLD_SLOTS):
                    tm["slot"], tm["warm"] = round(time.time() - t0, 2), warm
                    t0 = time.time()
                    res = cs2_scout.grade(name, line, prop, lt, slug=slug, faceit_key=faceit_key(), scope=scope,
                                          match=match, opponent=opp)
                    tm["grade"] = round(time.time() - t0, 2)
            t0 = time.time()
            if res.get("error"):
                return 404, json.dumps({"error": res["error"][0].upper() + res["error"][1:]}).encode()
            pb_ = res.get("player_block") or {}
            mark_warm(pkey, name, (pb_.get("player") or {}).get("slug"), (pb_.get("player") or {}).get("nick"))
            res["verdict"] = verdict(res.get("grade"))
            try:
                res["underdog"] = underdog.line_for((pb_.get("player") or {}).get("nick") or name, prop)
            except Exception:
                res["underdog"] = None
            tm["underdog"] = round(time.time() - t0, 2)
            t0 = time.time()
            try:
                picklog.log_async(res, res["verdict"], test=load_test)   # written in the background
            except Exception:
                traceback.print_exc()
            tm["log"] = round(time.time() - t0, 2)
            t0 = time.time()
            body = json.dumps(public(res), default=str).encode()
            tm["json"] = round(time.time() - t0, 2)
            REQ_TIMES.append(dict(tm, player=name, at=round(time.time()), total=round(time.time() - t_in, 2)))
            return 200, body

        status, body = shared(key, compute)
        return self.send(status, body)

    # ------------------------------------------------------------ Phase 10 admin (private)
    def authed(self, token):
        return bool(ADMIN_TOKEN) and token == ADMIN_TOKEN

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        if u.path != "/admin/closing" or not self.authed(form.get("token")):
            return self.send(403, {"error": "forbidden"})
        try:
            picklog.set_closing(int(form["id"]), float(form["closing"]))
        except (KeyError, ValueError):
            pass
        self.send_response(303)
        self.send_header("Location", "/admin?token=" + form.get("token", ""))
        self.send_header("Content-Length", "0")      # keep-alive needs an explicit length
        self.end_headers()

    def admin(self, q):
        if not self.authed(q.get("token")):
            return self.send(403, b"Forbidden", "text/plain")
        r = picklog.report()
        e = htmllib.escape
        tok = e(q.get("token"))

        def table(rows, cols):
            head = "".join(f"<th>{e(c)}</th>" for c in cols)
            body = "".join("<tr>" + "".join(f"<td>{e(str(x.get(c, '')))}</td>" for c in cols) + "</tr>" for x in rows)
            return f"<table><tr>{head}</tr>{body}</table>"

        def pct(rows):
            return [dict(x, printed=f"{x['printed']:.1%}", actual=f"{x['actual']:.1%}") for x in rows]

        picks = []
        for x in r["rows"]:
            form = (f"<form method=post action=/admin/closing><input type=hidden name=token value='{tok}'>"
                    f"<input type=hidden name=id value={x['id']}><input name=closing size=4 "
                    f"value='{'' if x['closing_line'] is None else x['closing_line']}'><button>save</button></form>")
            picks.append(f"<tr><td>{x['id']}</td><td>{e(x['logged_at'][:16])}</td><td>{e(x['player'])}</td>"
                         f"<td>{e(x['opponent'] or '')}</td><td>{x['prop']} {x['line']} {e(x['line_type'])}</td>"
                         f"<td><b>{x['pick']}</b> {e(x['grade'])} {x['p_pick']:.0%} {x['units']}U</td>"
                         f"<td>{x['lambda']}</td><td>{e(x['market'] or '')}</td><td>{e(x['rec_hit'] or '')}</td>"
                         f"<td>{x['status']}</td><td>{'' if x['actual'] is None else x['actual']}</td>"
                         f"<td>{'' if x['hit'] is None else ('HIT' if x['hit'] else 'miss')}</td>"
                         f"<td>{form}</td><td>{'' if x['clv'] is None else x['clv']}</td><td>{x['requests']}</td></tr>")
        page = f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Pick log</title><style>body{{font:14px system-ui;background:#0b0e13;color:#e8ecf3;margin:16px}}
table{{border-collapse:collapse;margin:8px 0 20px}}td,th{{border:1px solid #252e3f;padding:4px 8px;text-align:left}}
th{{color:#8b95a8}}input{{background:#1a2130;color:#e8ecf3;border:1px solid #252e3f}}.k{{color:#8b95a8}}
.scroll{{overflow-x:auto}}</style>
<h2>Phase 10 - real lines</h2>
<p><span class=k>Logged</span> {r['total']} · <span class=k>open</span> {r['open']} · <span class=k>settled</span> {r['settled']}
 · <span class=k>void</span> {r['void']} · <span class=k>hit rate</span> {'' if r['hit_rate_all'] is None else f"{r['hit_rate_all']:.1%}"}
 · <span class=k>CLV avg</span> {r['clv_avg']} ({r['clv_n']} lines, {'' if r['clv_positive'] is None else f"{r['clv_positive']:.0%} positive"})
 · <span class=k>units net (flat ±1, not payout-adjusted)</span> {r['units_net_flat']}</p>
<h3>Printed % vs actual (settled)</h3>{table(pct(r['by_printed_pct']), ['bucket', 'n', 'printed', 'actual'])}
<h3>By units</h3>{table(pct(r['by_units']), ['bucket', 'n', 'printed', 'actual'])}
<h3>Picks (newest first) - enter PrizePicks' closing line for CLV</h3><div class=scroll><table>
<tr><th>#</th><th>logged</th><th>player</th><th>vs</th><th>line</th><th>pick</th><th>λ</th><th>market</th><th>last10</th>
<th>status</th><th>actual</th><th>result</th><th>closing</th><th>CLV</th><th>asks</th></tr>{''.join(picks)}</table></div>"""
        return self.send(200, page.encode(), "text/html; charset=utf-8")


if __name__ == "__main__":
    threading.Thread(target=warm_start, daemon=True, name="warm-start").start()
    threading.Thread(target=board_forever, daemon=True, name="underdog-board").start()
    def _pack_cache():   # one-time: old per-file cache -> one packed file (same data, ~1/3 the disk)
        try:
            import kvstore
            n = kvstore.migrate()
            STORAGE["migrated_files"] = n
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_pack_cache, daemon=True, name="pack-cache").start()
    if PRELOAD:
        threading.Thread(target=preload_forever, daemon=True, name="preload").start()   # cleans up first
    else:
        threading.Thread(target=cleanup, daemon=True, name="cleanup").start()
    try:
        hltv.search_players("a")                      # loads the HLTV player-search index in the background
    except Exception:
        pass
    ThreadingHTTPServer.request_queue_size = 2048     # accept hundreds of connections at once
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"cs2_scout running on {HOST}:{PORT} - open http://localhost:{PORT}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
