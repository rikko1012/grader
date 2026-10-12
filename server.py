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
import re
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
from datetime import datetime, timedelta, timezone

import cs2_scout
import hltv
import picklog
import slips
import slip_batches
import comparison
import prizepicks
import records
import roles
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
        out = compute()
        status, body = out[0], out[1]
        keep = out[2] if len(out) > 2 and out[2] else ttl    # a result may ask to be reused for less time (outage grades)
        if status in (200, 404):
            with _RG:
                if len(_RESULTS) > 20000:
                    cut = time.time() - ttl
                    for k in [k for k, v in _RESULTS.items() if v[0] < cut]:
                        del _RESULTS[k]
                _RESULTS[key] = (time.time() - max(0, ttl - keep), status, body)
        return status, body
    finally:
        with _RG:
            _INFLIGHT.pop(key, None)
        ev.set()


PLAUSIBLE_MAX = {"kills": {"1": 50, "12": 90, "123": 130}, "hs": {"1": 35, "12": 60, "123": 90}}   # line sanity (see /api/grade)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")   # set on Railway; no token = admin page disabled

PRELOAD_STATUS = {"enabled": PRELOAD, "players_ready": 0, "last_pass": None, "current": None, "errors": 0}


STORAGE = {"last_cleanup": None, "freed_mb": 0.0}


# ---------------------------------------------------------------- Underdog board (graded in the background)
BOARD = {}              # Underdog line id -> board row + its grade summary
BOARD_WORKERS = 3       # lines graded side by side (one at a time left most of the board ungraded for hours)
BOARD_EVERY = 15 * 60   # re-grade a line at most this often (members' grades are cached anyway)
HLTV_RETRY = 90         # a line graded while the HLTV database was unreachable is tried again this soon


def _hltv_status(public=False):
    try:
        import hltv
        return hltv.public_status() if public else hltv.status()
    except Exception as e:
        return {"error": str(e)[:80]}


def perf_transient(g):
    """True when this grade fell back to the old pipeline only because the HLTV database was unreachable (cs2_scout._perf_input
    marks that in the grade's projection_source; grader output itself is unchanged)."""
    return "HLTV database unreachable" in str((g or {}).get("projection_source") or "")


HLTV_MAX_RETRIES = 20   # a line kept this often (~30 min) while its model stays 'unreachable' drops to a hidden placeholder on the
                        # regular cycle (a slow query, not an outage) - an old-pipeline grade still never goes on the board for it


def outage_placeholder(old, r, now=None):
    """A line whose grade could not use the HLTV database and that has no earlier grade of this line to keep: not shown (no
    side), tried again in HLTV_RETRY s; after HLTV_MAX_RETRIES it stays hidden but on the regular BOARD_EVERY cycle (a slow
    query, not an outage - still never an old-pipeline grade on the board)."""
    old = old or {}
    n = (old.get("hltv_retries") or 0) + 1
    now = now or time.time()
    return {"at": now - BOARD_EVERY + HLTV_RETRY if n <= HLTV_MAX_RETRIES else now, "state": "RETRY", "reason": "HLTV database unreachable - graded again shortly",
            "graded_line": r.get("line"), "hltv_retries": n, "hltv_retry": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def keep_last_hltv_grade(old, g, r=None, now=None):
    """The HLTV database was unreachable for this grade (g['perf_transient']): the board keeps the last HLTV grade of the
    line instead of showing an old-pipeline grade next to HLTV grades of the same player (2026-10-11 saadzin: Under 30.5 on
    the Underdog board, Over 29.5 on PrizePicks and in Match players, ten minutes apart). Returns the row to keep (due again
    in HLTV_RETRY seconds) or None when the new grade should be stored."""
    if not perf_transient(g) or not old or not old.get("grade"):
        return None
    if r is not None and old.get("graded_line") is not None and float(old["graded_line"]) != float(r.get("line") or -1):
        return None                                   # the line moved: the old grade is for another line
    if (old.get("hltv_retries") or 0) >= HLTV_MAX_RETRIES:
        return None
    now = now or time.time()
    return dict(old, at=now - BOARD_EVERY + HLTV_RETRY, hltv_retry=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                hltv_retries=(old.get("hltv_retries") or 0) + 1)
BOARD_FILE = DATA_DIR / ".cache" / "board.json"


def _grader_version():
    """Changes whenever the grading formula (grader.py) changes: saved board grades from an older
    formula are dropped on startup instead of being shown."""
    import hashlib
    # 2026-10-10: the full model version (every file that moves a grade, picklog.MODEL_VERSION) - the old hash covered
    # grader.py + perf.py only, so a cs2_scout.py / perf_live.py fix kept the old grades until each line was 15 min old
    return picklog.MODEL_VERSION


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
        rows = saved.get("rows") or {}
        for v in rows.values():
            # grading changed: keep showing the last grades (never an empty board) but regrade every line made by
            # another model version first thing - "at" 0 puts them at the front of the queue
            if saved.get("version") != _grader_version() or v.get("graded_model") != picklog.MODEL_VERSION:
                v["at"] = 0
                if str(v.get("source") or "").startswith("older model"):
                    # an old-pipeline grade from another version (an outage artefact before 2026-10-11) is not shown again:
                    # it waits hidden and is regraded first
                    v.pop("side", None)
                    v["state"] = "RETRY"
        BOARD.update(rows)
    except Exception:
        pass


def _board_l10(rf, r):
    """Last-10 numbers in the line's own maps: Map 1 lines use his map-1 values, Maps 1-3 his maps 1-3 totals of series
    where map 3 was played (records.py), Maps 1-2 as before."""
    sc = r.get("scope") or "12"
    if (rf.get("matches") or 0) < 3:                 # 1-2 matches is not a record (one OT map read as "avg 39")
        return {"l10_over": None, "l10_n": None, "l10_avg": None}
    if sc in ("12", "123"):
        return {"l10_over": rf.get("overs"), "l10_n": rf.get("decided", rf.get("matches")), "l10_push": rf.get("pushes") or 0,
                "l10_avg": rf.get("avg_total")}
    if sc == "1":                                    # recent_form_map1: one value per match (BO1s included)
        v = [s["values"][0] for s in (rf.get("series") or []) if s.get("values") and s["values"][0] is not None]
        return {"l10_over": sum(x > r["line"] for x in v) if v else None, "l10_n": sum(x != r["line"] for x in v) or None,
                "l10_push": sum(x == r["line"] for x in v), "l10_avg": round(sum(v) / len(v), 1) if v else None}
    return {"l10_over": None, "l10_n": None, "l10_avg": None}


def ud_id_map(rows=None):
    """Underdog player id -> our BO3.gg player id, learnt from board grades (the board grade resolved that exact
    Underdog line to one player). A Underdog id seen with two different players is dropped (ambiguous)."""
    out, bad = {}, set()
    for r in rows if rows is not None else underdog.board()["rows"]:
        g = BOARD.get(r.get("id")) or {}
        u, p = r.get("ud_player_id"), g.get("player_id")
        if u is None or p is None or u in bad:
            continue
        if out.get(u, p) != p:
            bad.add(u); out.pop(u, None); continue
        out[u] = p
    return out


def match_compare(team_slug, opp_slug, team, opponent, start, scope="12", book="underdog"):
    """Match player projection comparison (2026-10-09): every player of both teams in this match - OUR projection for
    kills and headshots, the graded Underdog line for the same match and maps when one exists (side, chance, grade,
    stake, freshness, data status), otherwise the projection alone and 'no available line' (no pick invented).
    Team totals only when the whole roster has a projection; never any probability arithmetic across players."""
    from datetime import datetime as _dt
    import perf_live
    st = comparison._ts(start)
    # one platform's graded lines (2026-10-10: PrizePicks like Underdog); the table shows each player's STANDARD line -
    # PrizePicks goblin / demon markets stay on the board, never mixed into this table
    rows = [r for r in board_payload(book)["rows"] if (r.get("line_type") or r.get("odds_type") or "standard") == "standard"]
    same = [r for r in rows if (r.get("scope") or "12") == scope and st and comparison._ts(r.get("start"))
            and abs((comparison._ts(r["start"]) - st).total_seconds()) <= comparison.JOIN_START_MIN * 60
            and ({comparison._team_same(r.get("team_name") or r.get("team"), team), comparison._team_same(r.get("team_name") or r.get("team"), opponent)} & {True})]
    b = BO3()
    try:
        rosters = {}
        for slug, name in ((team_slug, team), (opp_slug, opponent)):
            try:
                td = b.team(slug) if slug else {}
                # active players only: BO3.gg status 2 = benched (not in the lineup)
                rosters[name] = [x.get("nickname") for x in (td.get("players") or [])
                                 if not x.get("is_coach") and x.get("nickname") and x.get("status") != 2]
            except Exception:
                rosters[name] = []
    finally:
        b.close()
    def side_of(r):
        if comparison._team_same(r.get("team_name") or r.get("team"), team):
            return team
        return opponent
    players = {}                                    # (team, nick lower) -> entry
    for name, nicks in rosters.items():
        for n in nicks:
            players[(name, n.lower())] = {"nick": n, "team": name, "kills": None, "hs": None}
    for r in same:
        nk = (r.get("nick") or r["player"])
        e = players.setdefault((side_of(r), nk.lower()), {"nick": nk, "team": side_of(r), "kills": None, "hs": None, "not_on_roster_page": True})
        e[r["prop"]] = {"proj": r.get("proj"), "line": r["line"], "side": r.get("side"), "p": r.get("p"), "grade": r.get("grade"),
                        "units": r.get("units"), "units_full": r.get("units_full"), "graded_at": r.get("graded_at"), "dstat": r.get("dstat"),
                        "source": r.get("source"), "state": r.get("state"), "ud_team": r.get("team"), "ud_opponent": r.get("opponent"),
                        "reason": r.get("reason")}
    # same day and LAN / online setting as the graded rows (2026-10-10 audit: these used today's date and no LAN /
    # online blend, so a no-line projection was not comparable with the graded ones next to it)
    import cs2_scout
    day = cs2_scout.match_day(start)
    tn = next((r.get("tonight") for r in same if r.get("tonight")), None)
    lan = None if tn is None else tn == "lan"
    for (tname, _), e in players.items():          # projections for props without an offered line
        for prop in ("kills", "hs"):
            if e[prop] is None:
                try:
                    pr = perf_live.projection(e["nick"], tname, opponent if tname == team else team, lan, scope, prop, day)
                except Exception as ex:
                    pr = {"error": "HLTV database unreachable right now" if cs2_scout._transient_hltv_error(ex) else "failed"}
                e[prop] = ({"proj": round(pr["lambda"], 2), "line": None, "no_line": True, "source": "HLTV", "dq": pr.get("data_quality")}
                           if pr and not pr.get("error") and pr.get("lambda") is not None else {"proj": None, "line": None, "no_line": True,
                                                                                               "note": "no projection (HLTV database unreachable right now)" if "unreachable" in str(pr.get("error")) else "no projection (no HLTV history)"})
    out = []
    for tname in (team, opponent):
        ps = [e for (t, _), e in players.items() if t == tname]
        # team totals need the 5 who actually play: Underdog's lineup when it lists exactly 5, else a roster page of
        # exactly 5; otherwise no total (a 6-man roster page would inflate it)
        lined = [e for e in ps if any((e[p] or {}).get("line") is not None for p in ("kills", "hs"))]
        five = lined if len(lined) == 5 else ps if len(ps) == 5 else None
        basis = "Underdog lineup" if five is lined and len(lined) == 5 else "roster page" if five else None
        covered = sum(1 for e in (five or ps) if all((e[p] or {}).get("proj") is not None for p in ("kills", "hs")))
        full = bool(five) and covered == 5
        summ = {prop: (round(sum((e[prop] or {}).get("proj") or 0 for e in five), 1) if full else None) for prop in ("kills", "hs")}
        out.append({"team": tname, "players": ps, "summary": dict(summ, complete=full, covered=covered, roster=len(ps), basis=basis,
                                                                  lineup=[e["nick"] for e in five] if five else None)})
    return {"match": {"team": team, "opponent": opponent, "start": start, "scope": scope}, "teams": out,
            "at": _dt.now(timezone.utc).isoformat(timespec="seconds")}


def _row_match_id(row):
    """matchid.match_id_for_row, or None while matchid's tables are still loading (a member request never waits for the
    first HLTV read - the boot thread does it) or if linking fails. Identity only: no grade reads it."""
    try:
        import matchid
        return matchid.match_id_for_row(row) if matchid.ready() else None
    except Exception:
        return None


def board_payload(book="underdog"):
    """The graded board for one platform. book="prizepicks": PrizePicks' lines (prizepicks.py), each graded at its own
    line and market type; its feed state (ok / stale / failed / empty) travels with it. Default Underdog, unchanged."""
    b = prizepicks.board() if book == "prizepicks" else underdog.board()
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in b["rows"]:
        if r.get("start") and r["start"] < now:
            continue                                  # started: locked on Underdog
        g = BOARD.get(r["id"]) or {}
        if not g.get("side"):
            continue                                  # not graded yet: never shown
        if g.get("graded_line") is not None and float(g["graded_line"]) != float(r.get("line") or -1):
            continue                                  # the line moved: graded afresh within seconds, never shown with the old grade
        rows.append(dict(r, **{k: g.get(k) for k in ("grade", "side", "p", "units", "proj", "reason", "graded_at",
                                                     "nick", "state", "edge", "l10_over", "l10_n", "l10_push", "l10_avg",
                                                     "hs_pct", "p_win", "market", "team_name", "opp_name", "match_slug",
                                                     "graded_line", "graded_model", "player_id", "event", "bo", "maps_known",
                                                     "dstat", "source", "support", "concern", "tonight", "hltv_retry")}))
        rows[-1]["scope"] = r.get("scope") or "12"
        x = rows[-1]
        nick = x.get("nick") or x.get("player") or ""
        # roles (slip builder + display only; True / False / None = unknown, with the source)
        x["awp"], x["awp_src"] = awp_role(nick, g, x.get("hs_pct"), x.get("team_name") or x.get("team"))
        # confirmed = explicit role data (HLTV weapon stats, Liquipedia); measured = BO3.gg body-shot rate;
        # inferred = known list / HS% - an inferred role is never treated as confirmed (slips)
        src = (x["awp_src"] or "").lower()
        x["awp_conf"] = (None if x["awp"] is None else "confirmed" if src.startswith(("hltv", "liquipedia"))
                         else "measured" if src.startswith("bo3") else "inferred")
        x["igl"], x["igl_src"] = roles.igl_role(nick, x.get("team_name") or x.get("team"))
    for x in rows:
        x["book"] = x.get("book") or "underdog"
        x["match_id"] = _row_match_id(x)         # canonical match id (matchid.py): the same on every platform; None = unlinked
    _cap_same_result(rows)                    # within this platform: a slip is one platform's entry
    out = {"book": book, "updated": b["at"], "error": b["error"], "rows": rows, "total": len(b["rows"]),
           "version": picklog.MODEL_VERSION}
    if book == "prizepicks":
        out["feed"] = {k: b.get(k) for k in ("state", "at", "checked_at", "lines", "error", "ingested_at", "source")}
    return out


KNOWN_AWP = {"zywoo", "m0nesy", "sh1ro", "broky", "w0nderful", "torzsi", "woro2k", "hallzerk", "s1mple", "device", "jame",
             "molodoy", "910", "ultimate"}
AWP_BOARD_HS = 35          # 3-month HS% under this = an AWPer for the slip builder (riflers sit around 45-60%)
RIFLE_BOARD_HS = 42        # and 42%+ with BO3.gg not calling him an AWPer = a rifler


def awp_role(nick, g, hs_pct, team=None):
    """(True / False / None, source). HLTV's real AWP kill share first (weapon stats, last 90 days),
    then Liquipedia's role listing, BO3.gg's body-shot kill rate, the known list, then HS%."""
    lp, src = roles.awp_role(nick, team)            # HLTV AWP share, then Liquipedia (roles.py)
    if lp is not None:
        return lp, src
    snip = g.get("snip")
    if snip == 100:
        return True, "BO3.gg: " + (g.get("awp_note") or "AWPer")
    if (nick or "").lower() in KNOWN_AWP:
        return True, "known AWPer list"
    if hs_pct is not None and hs_pct < AWP_BOARD_HS:
        return True, f"HS% {hs_pct:.0f} (3 months) - AWPer range"
    if snip == 0:
        return False, "BO3.gg: " + (g.get("awp_note") or "rifler")
    if hs_pct is not None and hs_pct >= RIFLE_BOARD_HS:
        return False, f"HS% {hs_pct:.0f} (3 months) - rifler range"
    return None, "unknown"

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


def stake_caps():
    """The stake limits the card's pills quote, read from the grader itself (2026-10-10 audit: the card said max 0.5U
    for a stand-in and max 1U for a thin market while the grader allowed 1U and 1.5U)."""
    from grader import CONFIG
    return {"standin": 1.0, "market": CONFIG["soft_market_cap"], "tape": CONFIG["tape_fight_cap"], "thin": 1.5,
            "same_result": SAME_RESULT_CAP, "kills_chance": CONFIG["p_cap_kills"]}


_BS_CACHE = [0.0, []]
_BS_LOCK = threading.Lock()


def _both_boards():
    """Both platforms' graded rows (a PrizePicks card gets the same-match cap its board applied - 2026-10-11 audit)."""
    rows = list(board_payload()["rows"])
    try:
        rows += board_payload("prizepicks")["rows"]
    except Exception:
        pass
    return rows


def board_stake(res, prop, scope, line, line_type="standard", book=None):
    """The same line on the board with its stake cut by the same-match cap: the card shows that stake too (one stake
    per line on the whole site). Only when the board row is this exact pick (side, line, maps, match) and was cut from
    exactly this card's stake - otherwise the card's own stake stands. Returns the verdict (a new dict when capped)."""
    v = res.get("verdict") or {}
    if not v.get("side") or not v.get("units"):
        return v
    nick = ((res.get("player_block") or {}).get("player") or {}).get("nick") or ""
    st = comparison._ts((res.get("next_match") or {}).get("start"))
    if time.time() - _BS_CACHE[0] > 5:            # board rows at most 5 s old (no board rebuild per member grade)
        # single-flight: one request rebuilds, the rest keep the previous rows (a 700-member rush must not rebuild the
        # board 700 times at once); only the very first build makes others wait
        if _BS_LOCK.acquire(blocking=not _BS_CACHE[0]):
            try:
                if time.time() - _BS_CACHE[0] > 5:
                    _BS_CACHE[:] = [time.time(), _both_boards()]
            finally:
                _BS_LOCK.release()
    book = book or ("prizepicks" if line_type in ("goblin", "demon") else "underdog")   # the card's own platform
    for r in _BS_CACHE[1]:
        if (r.get("book") or "underdog") != book:
            continue
        if (str(r.get("nick") or r.get("player") or "").lower() == nick.lower() and r.get("prop") == prop
                and (r.get("scope") or "12") == scope and float(r.get("line") or -1) == float(line) and r.get("side") == v["side"]
                and r.get("cap_n") and r.get("units_full") == v["units"] and r["units"] < r["units_full"] and st and comparison._ts(r.get("start"))
                and abs((comparison._ts(r["start"]) - st).total_seconds()) <= comparison.JOIN_START_MIN * 60):
            return dict(v, units=r["units"], units_full=r["units_full"], cap_n=r["cap_n"],
                        cap_team=r.get("team_name") or r.get("team"))
    return v


def board_last_hltv(res, prop, scope):
    """During an HLTV outage the card grades on the old pipeline; this is what the Prop Board still shows for the same player /
    stat / maps (its last performance-model grades), so the member is told instead of seeing two different numbers."""
    nick = (((res.get("player_block") or {}).get("player") or {}).get("nick") or "").lower()
    try:
        rows_ = _both_boards()
    except Exception:
        rows_ = _BS_CACHE[1]
    rows = [r for r in rows_ if str(r.get("nick") or r.get("player") or "").lower() == nick and r.get("prop") == prop and r.get("side")
            and (r.get("scope") or "12") == scope and (r.get("line_type") or r.get("odds_type") or "standard") in ("standard", "promo")]
    return {"rows": [{k: r.get(k) for k in ("book", "line", "side", "grade", "p", "units", "proj", "graded_at", "hltv_retry")} for r in rows[:4]]}


# ---------------------------------------------------------------- research tools: moneylines + slip studio (no grading)
# Nothing here feeds or changes a grade. Every member request reads shared in-memory state: moneyline.py keeps one
# background refresher per exchange, the slip studio builds from a graded-board snapshot rebuilt at most every few
# seconds by one thread, and no member request calls an upstream API.
ML_TTL = 30                       # /api/moneylines: one response shared by every member for 30 s


def studio_flag(env=None):
    """The member slip studio is ON (owner 2026-10-12: "rebuild slip selection around portfolio diversity" ... "get it ready",
    after being told the old builder was off pending settled batches). SLIP_STUDIO=0 is the kill switch. The panel itself says
    the diversified builder is still being validated on settled batches."""
    return (os.environ if env is None else env).get("SLIP_STUDIO", "1") != "0"


SLIP_STUDIO = studio_flag()       # on by default (owner 2026-10-12); SLIP_STUDIO=0 turns the endpoints + panel off
STUDIO_BUILDS_PER_MIN = 20        # Build presses per member per minute (a retry with the same idem key is free)
STUDIO_EVENTS_PER_MIN = 40        # verify + event calls per member per minute (its own bucket - never eats build quota)
STUDIO_BODY_MAX = 16 * 1024
SHADOW_LABEL = "Shadow - not a staking recommendation"
MODEL_NOT_VALIDATED = "Model: not validated yet - market baseline shown, no recommendation"
_ML_BLOCK_KEYS = ("available", "prob_a", "bid", "ask", "spread", "bid_size", "ask_size", "depth", "best_depth", "status",
                  "current", "fresh", "market_open", "price_only", "usable", "reason", "source_ts", "received_ts",
                  "team_a_name", "team_b_name", "last_trade", "prob_b_yes", "prob_b_usable", "coherence_gap", "coherence_flag")
RESEARCH = {"matchid_ready": False, "moneyline_started": [], "errors": []}


def start_research():
    """Boot (its own thread, after the grading threads): load matchid's tables, then start the moneyline refreshers.
    Each step in its own try - a failure here never touches grading, /health or the other step."""
    try:
        import matchid
        RESEARCH["matchid_ready"] = matchid.warm()
    except Exception as e:
        RESEARCH["errors"].append(f"matchid: {type(e).__name__}: {str(e)[:160]}")
    if os.environ.get("MONEYLINES", "1") != "1":       # MONEYLINES=0: refreshers off (the tab then shows "unavailable")
        return
    try:
        import moneyline
        RESEARCH["moneyline_started"] = moneyline.start()
    except Exception as e:
        RESEARCH["errors"].append(f"moneyline: {type(e).__name__}: {str(e)[:160]}")


_MODEL_STATE = {}


def validated_features(d):
    """Features that passed BOTH stages: promoted on development / validation (freeze.promoted) AND a frozen test-set
    result whose gain-vs-market CI lower bound is above zero. A validation-only promotion (no --final run yet, or a test
    CI touching zero) is not 'validated'."""
    st = (d or {}).get("staged") or {}
    test = st.get("test") or {}
    out = []
    for k in ((st.get("freeze") or {}).get("promoted") or []):
        ci = (test.get(k) or {}).get("gain_vs_market_ci") if isinstance(test.get(k), dict) else None
        if ci and ci[0] is not None and ci[0] > 0:
            out.append(k)
    return out


def ml_model_state():
    """Is there a walk-forward validated moneyline feature? Read from ml_eval_results.json once. No live model
    exists yet, so members get the market baseline and no model probability either way."""
    if not _MODEL_STATE:
        validated, val_promoted, at = [], [], None
        try:
            with open(BASE / "ml_eval_results.json", encoding="utf-8") as f:
                d = json.load(f)
            val_promoted = list((((d.get("staged") or {}).get("freeze") or {}).get("promoted")) or [])
            validated = validated_features(d)
            at = d.get("generated_at")
        except Exception:
            pass
        _MODEL_STATE.update(promoted=validated, val_promoted=val_promoted, evaluated_at=at, available=False,
                            probability=None,
                            label=MODEL_NOT_VALIDATED if not validated else
                            "Model: validated features exist but no live model is deployed - market baseline shown, no recommendation")
    return dict(_MODEL_STATE)


def moneylines_payload():
    """The member view of moneyline.board(): slimmed (no raw platform payloads / rules text), team A first."""
    import moneyline
    st = moneyline.status()
    out = []
    for e in moneyline.board():
        pl = {}
        for p, b in (e.get("platforms") or {}).items():
            if not b:
                pl[p] = None
                continue
            blk = {k: b.get(k) for k in _ML_BLOCK_KEYS}
            blk["url"] = b.get("url") if p == "polymarket" else None      # Kalshi's page URL form is unverified - not linked
            pl[p] = blk
        base = e.get("baseline") or {}
        out.append({"match_id": e.get("match_id"), "link_status": e.get("link_status"), "link_reason": e.get("link_reason"),
                    "teams": {"a": (e.get("teams") or {}).get("a", {}).get("name"), "b": (e.get("teams") or {}).get("b", {}).get("name"),
                              "names_by_platform": dict((e.get("teams") or {}).get("names_by_platform") or {})},
                    "event": e.get("event"), "start": e.get("start"), "started": bool(e.get("started")),
                    "tie_possible": bool(e.get("tie_possible")), "bo": e.get("bo"), "scope": "series",
                    "platforms": pl,
                    "baseline": {k: base.get(k) for k in ("prob_a", "platform", "label", "reason", "model_version")},
                    "disagreement": bool(e.get("disagreement")), "disagreement_gap": e.get("disagreement_gap")})
    out = _merge_unlinked(out)
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "matches": out,
            "sources": {k: {x: v.get(x) for x in ("ok", "stale", "age_s", "last_success", "note", "listings")} for k, v in st.items()},
            "rule": moneyline.BASELINE_RULE, "model_version": moneyline.MODEL_VERSION,
            "model": {k: v for k, v in ml_model_state().items() if k != "val_promoted"},
            "recommendation": SHADOW_LABEL, "scope": "series"}


def _norm_team():
    try:
        from matchid import norm
        return norm
    except Exception:
        return lambda x: re.sub(r"[^a-z0-9]+", "", str(x or "").lower())


def _oriented_prob(blk, name_a, name_b, norm):
    """A block's probability for the card's team A, read by the block's OWN team_a_name (an unlinked match may list
    the teams the other way round on the other exchange). None when the block's team can't be placed exactly."""
    if not blk or blk.get("prob_a") is None:
        return None
    own = norm(blk.get("team_a_name"))
    if own and own == norm(name_a):
        return blk["prob_a"]
    if own and own == norm(name_b):
        return round(1 - blk["prob_a"], 4)
    return None


def _merge_unlinked(rows):
    """Display only: an unlinked match listed on BOTH exchanges arrives as two one-platform entries (moneyline.py never
    joins what it can't link). Show it once when the two entries name exactly the same two teams (normalised: case,
    accents, punctuation - never fuzzy) and start within 3 h. It stays unlinked: no match id, never a baseline. Each
    platform block keeps its own team_a_name, so a reversed team order is still read correctly.
    The merged card (merged_unlinked) compares the two prices only after orienting both to the card's team A by each
    block's own team_a_name, and only when both quotes are usable; otherwise disagreement_gap stays None (the page then
    says the prices were not compared - never 'agree')."""
    norm = _norm_team()
    keyed, out = {}, []
    for m in rows:
        plats = [p for p, b in m["platforms"].items() if b]
        if m["match_id"] or len(plats) != 1:
            out.append(m)
            continue
        names = frozenset((norm(m["teams"]["a"]), norm(m["teams"]["b"])))
        st = comparison._ts(m.get("start"))
        twin = next((x for x in keyed.get(names, []) if not x["platforms"].get(plats[0]) and st and comparison._ts(x.get("start"))
                     and abs((comparison._ts(x["start"]) - st).total_seconds()) <= 3 * 3600), None)
        if twin is None or len(names) != 2:
            keyed.setdefault(names, []).append(m)
            out.append(m)
            continue
        twin["platforms"][plats[0]] = m["platforms"][plats[0]]
        twin["teams"]["names_by_platform"].update(m["teams"]["names_by_platform"])
        # the agreement fields were computed when the twin had ONE platform: never carry them over
        twin.update(merged_unlinked=True, disagreement=False, disagreement_gap=None)
        blks = [b for b in twin["platforms"].values() if b]
        if len(blks) == 2 and all(b.get("usable") for b in blks):
            ps = [_oriented_prob(b, twin["teams"]["a"], twin["teams"]["b"], norm) for b in blks]
            if None not in ps:
                gap = round(abs(ps[0] - ps[1]), 4)
                twin.update(disagreement_gap=gap, disagreement=gap >= moneyline_disagree() - 1e-9)
    return out


def moneyline_disagree():
    try:
        import moneyline
        return moneyline.DISAGREE
    except Exception:
        return 0.05


_GB = {}                                                      # book -> (built_at, board_payload)
_GB_LOCKS = {"underdog": threading.Lock(), "prizepicks": threading.Lock()}


def graded_board(book, max_age=5.0):
    """board_payload(book), rebuilt by ONE thread at most every `max_age` seconds; everyone else gets that copy (700
    Build presses = one board rebuild, not 700)."""
    hit = _GB.get(book)
    if hit and time.time() - hit[0] <= max_age:
        return hit[1]
    with _GB_LOCKS[book]:
        hit = _GB.get(book)
        if not hit or time.time() - hit[0] > max_age:
            hit = _GB[book] = (time.time(), board_payload(book))
    return hit[1]


_SETTLED = [0.0, None]


def studio_track_record():
    """The studio's honest note, each count under its OWN builder tag (cached 5 min):
    studio_settled = settled batches of the studio builder (slip_builder.VERSION) - 0 until a studio settler exists
    (slip_batches only ever settles its own shadow builder); shadow_settled = slip_batches' shadow builder
    (slip_batches.VERSION, a different algorithm) - never presented as the studio's record."""
    if _SETTLED[1] is None or time.time() - _SETTLED[0] > 300:
        import slip_builder
        rec = {"builder": slip_builder.VERSION, "studio_settled": 0,
               "shadow_builder": getattr(slip_batches, "VERSION", None), "shadow_settled": 0}
        try:
            res = slip_batches.results()
            rec["shadow_settled"] = len(res)
            rec["studio_settled"] = sum(1 for r in res if isinstance(r, dict) and r.get("version") == slip_builder.VERSION)
        except Exception:
            pass
        _SETTLED[:] = [time.time(), rec]
    return dict(_SETTLED[1])


def settled_batches():
    """Settled batches of the STUDIO builder (studio-1) only - see studio_track_record."""
    return studio_track_record()["studio_settled"]


_STUDIO_HITS, _STUDIO_EV_HITS, _STUDIO_GUARD = {}, {}, threading.Lock()


def studio_rate_ok(member, idem=None, bucket=None, limit=None):
    """Per-member sliding 60 s window. A request repeating an idem key already counted in the window is free (a retry of
    the same press - studioCall re-sends 502/503/504 and the 409/404 path re-sends once); a new key is counted only when
    it passes. bucket / limit: the verify + event bucket (_STUDIO_EV_HITS, STUDIO_EVENTS_PER_MIN) is separate."""
    d = _STUDIO_HITS if bucket is None else bucket
    limit = STUDIO_BUILDS_PER_MIN if limit is None else limit
    now = time.time()
    with _STUDIO_GUARD:
        if len(d) > 50000:
            d.clear()
        hits = [(t, k) for t, k in d.get(member, []) if now - t < 60]
        if idem is not None and any(k == idem for _, k in hits):
            d[member] = hits
            return True
        ok = len(hits) < limit
        if ok:
            hits.append((now, idem))
        d[member] = hits
        return ok


def _no_json_constant(c):
    raise ValueError(f"{c} is not allowed")


_IDEM_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,64}$")
_BATCH_RE = re.compile(r"^[0-9a-f]{12}-\d{1,9}$")
_SLIP_RE = re.compile(r"^[0-9a-f]{12}-\d{1,9}/b\d{1,9}/s\d{1,4}$")
_SETTING_KEYS = {"book", "side", "stat", "scope", "market", "sizes", "count", "counts", "overlap", "max_uses"}


def studio_settings(s):
    """Member settings -> slip_builder settings, bounded. ValueError (-> 400) on anything outside the studio's choices."""
    if not isinstance(s, dict) or set(s) - _SETTING_KEYS:
        raise ValueError("settings must be an object with: " + ", ".join(sorted(_SETTING_KEYS)))
    for k in ("book", "side", "stat", "scope", "market", "overlap"):
        if k in s and not isinstance(s[k], str):
            raise ValueError(f"{k} must be text")
    if str(s.get("market") or "standard").lower() == "moneyline":
        raise ValueError("moneylines are not built into slips (shadow only)")
    sizes = s.get("sizes", [2, 3, 4])
    if not isinstance(sizes, list) or not 1 <= len(sizes) <= 3:
        raise ValueError("sizes must be a list of 2, 3 and/or 4")
    if not all(isinstance(n, int) and not isinstance(n, bool) and n in (2, 3, 4) for n in sizes):
        raise ValueError("sizes must be a list of 2, 3 and/or 4")          # floats, Infinity, NaN, bools: 400, never 500
    for k in ("count",):
        if k in s and not (isinstance(s[k], int) and not isinstance(s[k], bool) and 1 <= s[k] <= 10):
            raise ValueError("count must be 1-10")
    if "counts" in s:
        c = s["counts"]
        if not isinstance(c, dict) or len(c) > 3 or not all(str(k) in ("2", "3", "4") and isinstance(v, int)
                                                            and not isinstance(v, bool) and 1 <= v <= 10 for k, v in c.items()):
            raise ValueError("counts must map 2/3/4 to 1-10")
    if "max_uses" in s:
        m = s["max_uses"]
        if not isinstance(m, dict) or set(m) - {"player", "match"} or not all(
                isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 3 for v in m.values()):
            raise ValueError("max_uses must be {player, match} with values 1-3")
    import slip_builder
    return slip_builder.normalize_settings(s)


_SNAPC = {"at": 0.0, "data": None}


def _snapshot_counts():
    """ml_snapshots.db counts (read-only connection, cached 10 min). Never a full scan of ml_quotes: the total is its
    largest rowid (rows are never deleted, so it equals COUNT(*)), per-platform figures cover the last 24 h through the
    received_ts index; ml_forecasts is small (one row per match per change / 30 min)."""
    if _SNAPC["data"] is not None and time.time() - _SNAPC["at"] < 600:
        return _SNAPC["data"]
    import sqlite3
    import moneyline
    p = moneyline.db_path()
    if not p.exists():
        out = {"exists": False}
    else:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        try:
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
            total = con.execute("SELECT MAX(rowid) FROM ml_quotes").fetchone()[0] or 0
            quotes = {pl: {"rows_24h": n, "usable_24h": int(u or 0), "linked_matches_24h": m, "last_received": last}
                      for pl, n, u, m, last in con.execute(
                          "SELECT platform, COUNT(*), SUM(usable), COUNT(DISTINCT match_id), MAX(received_ts) FROM ml_quotes "
                          "WHERE received_ts >= ? GROUP BY platform", (since,))}
            n, mids, avail, last = con.execute("SELECT COUNT(*), COUNT(DISTINCT match_id), SUM(available), MAX(logged_ts) "
                                               "FROM ml_forecasts").fetchone()
            out = {"exists": True, "size_mb": round(moneyline._store_mb(), 2), "cap_mb": moneyline.SNAPSHOT_MAX_MB,
                   "quote_rows_total": total, "quotes": quotes,
                   "forecasts": {"rows": n, "matches": mids, "available": int(avail or 0), "last_logged": last}}
        finally:
            con.close()
    _SNAPC.update(at=time.time(), data=out)
    return out


def moneylines_status():
    """Admin: each source's health, the snapshot store, and how well markets link to canonical matches."""
    out = {"research": RESEARCH, "model": ml_model_state()}
    try:
        import moneyline
        out["sources"] = moneyline.status()
        b = moneyline.board()
        both = [m for m in b if m["platforms"].get("polymarket") and m["platforms"].get("kalshi")]
        out["links"] = {
            "matches": len(b), "linked": sum(1 for m in b if m["match_id"]),
            "unlinked": sum(1 for m in b if not m["match_id"]),
            "by_id_source": dict(collections.Counter(str(m["match_id"]).split(":")[0] for m in b if m["match_id"])),
            "on_both_platforms": len(both),
            "usable": {p: sum(1 for m in b if (m["platforms"].get(p) or {}).get("usable")) for p in moneyline.PLATFORMS},
            "usable_on_both": sum(1 for m in both if m["platforms"]["polymarket"].get("usable") and m["platforms"]["kalshi"].get("usable")),
            "baselines": dict(collections.Counter((m["baseline"] or {}).get("platform") or "unavailable" for m in b)),
            "disagreements": sum(1 for m in b if m["disagreement"]),
            "coherence_flags": sum(1 for m in b if (m["platforms"].get("kalshi") or {}).get("coherence_flag")),
            "unlinked_examples": [f"{m['teams']['a']['name']} vs {m['teams']['b']['name']}: {m.get('link_reason')}"
                                  for m in b if not m["match_id"]][:15]}
    except Exception as e:
        out["error"] = f"moneyline: {type(e).__name__}: {str(e)[:200]}"
    try:
        out["snapshots"] = _snapshot_counts()
    except Exception as e:
        out["snapshots"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    try:
        import matchid
        out["matchid"] = {"ready": matchid.ready(), "sources": matchid.status(), "store": matchid.link_stats()}
    except Exception as e:
        out["matchid"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return out


_MLE = {"at": 0.0, "forward": None, "error": None, "busy": False, "took_s": None}
_MLE_LOCK = threading.Lock()
_MLE_SUMMARY = {}


def _ml_eval_forward():
    t0 = time.time()
    try:
        import ml_eval
        import moneyline
        path = str(moneyline.db_path())
        # light copy (teams + results, no player rows) covering EVERY snapshot match: from the oldest snapshot start
        # (- 2 days) plus each linked hltv:<id>, so the cumulative cohort never shrinks to a rolling 60-day window
        oldest, ids = ml_eval.forward_span(path)
        since = (datetime.fromtimestamp(oldest, timezone.utc).date() - timedelta(days=2)) if oldest else None
        hl = ml_eval.load_hltv_recent(60, since=since, matchids=ids)
        _MLE.update(forward=ml_eval.forward_eval(path, hl), error=None)
    except Exception as e:
        _MLE["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        _MLE.update(at=time.time(), busy=False, took_s=round(time.time() - t0, 1))


def ml_eval_admin(refresh=False):
    """Admin: the frozen historical evaluation (ml_eval_results.json without its per-match rows) plus the forward cohort
    from the live snapshots, recomputed in a background thread at most every 30 min (or on ?refresh=1)."""
    if not _MLE_SUMMARY:
        try:
            with open(BASE / "ml_eval_results.json", encoding="utf-8") as f:
                d = json.load(f)
            _MLE_SUMMARY.update({k: v for k, v in d.items() if k != "polymarket_rows"},
                                polymarket_rows_n=len(d.get("polymarket_rows") or []))
        except Exception as e:
            _MLE_SUMMARY["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    with _MLE_LOCK:
        if not _MLE["busy"] and (refresh or time.time() - _MLE["at"] > 1800):
            _MLE["busy"] = True
            threading.Thread(target=_ml_eval_forward, daemon=True, name="ml-eval-forward").start()
    return {"results": _MLE_SUMMARY, "model": ml_model_state(),
            "forward": {"computing": _MLE["busy"], "computed_at": datetime.fromtimestamp(_MLE["at"], timezone.utc).isoformat(timespec="seconds")
                        if _MLE["at"] else None, "took_s": _MLE["took_s"], "error": _MLE["error"], "result": _MLE["forward"]}}


REQ_TIMES = collections.deque(maxlen=300)
SEARCH_TIMES = collections.deque(maxlen=3000)   # server-side seconds per search: request received -> response sent
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


_SLIP_SETTLED = [0.0]
_DIAG = {}


def board_forever():
    """Grade every Underdog CS2 line in the background (gives way to members), so the Board tab is
    instant. Each graded line is also logged (test=2: not a member's pick) so it settles and the
    grader can be measured on a large, unbiased set of real lines."""
    _board_load()                                     # last grades show right away after a restart
    time.sleep(20)
    while True:
        try:
            # both platforms' lines, each graded at its own line and market type with the same pipeline (same
            # projection for the same player / match / stat / maps and model version; only the line differs)
            rows = underdog.board(wait=True)["rows"] + prizepicks.board(wait=True)["rows"]
            now = datetime.now(timezone.utc).isoformat()
            # Underdog alternates stay ungraded (as before); PrizePicks goblin / demon are their own markets and are graded
            # a line that moved since its grade is regraded at once (its grade was for another line - 2026-10-11 audit)
            moved = lambda r: (BOARD.get(r["id"]) or {}).get("graded_line") is not None and float(BOARD[r["id"]]["graded_line"]) != float(r.get("line") or -1)
            todo = [r for r in rows if (not r["alt"] or r.get("book") == "prizepicks") and (not r.get("start") or r["start"] > now)
                    and (time.time() - (BOARD.get(r["id"]) or {}).get("at", 0) > BOARD_EVERY or moved(r))]
            # lines from an older model version first, then soonest matches; one match's lines and each player's kills /
            # headshots back to back (the second grade of a player is served from his warm data)
            stale = lambda r: (BOARD.get(r["id"]) or {}).get("graded_model") != picklog.MODEL_VERSION
            todo.sort(key=lambda r: (not moved(r), not stale(r), r.get("start") or "9", str(r.get("matchup") or ""),
                                     str(r.get("player") or "").lower(), r.get("prop") or ""))

            def one(r):
                members_idle()                            # members first, but the board never stalls
                # PrizePicks goblin / demon are their own markets (More only); promo and standard grade as standard
                lt = r.get("odds_type") if r.get("book") == "prizepicks" and r.get("odds_type") in ("goblin", "demon") else "standard"
                try:
                    if MEMBER_GRADES[0] > 0:              # members still busy: one board grade at a time
                        with _BOARD_BUSY:
                            res = cs2_scout.grade(r["player"], r["line"], r["prop"], lt, scope=r.get("scope") or "12", team_hint=r.get("team"), opponent=r.get("opponent") or None,
                                                  faceit_key=faceit_key(), background=True, light=True)
                    else:
                        res = cs2_scout.grade(r["player"], r["line"], r["prop"], lt, scope=r.get("scope") or "12", team_hint=r.get("team"), opponent=r.get("opponent") or None, faceit_key=faceit_key(),
                                              background=True, light=True)
                    if res.get("error"):
                        old = BOARD.get(r["id"]) or {}
                        if old.get("side") and (old.get("nf_retries") or 0) < 3:   # a graded line is not dropped on one failed
                            BOARD[r["id"]] = dict(old, at=time.time() - BOARD_EVERY + HLTV_RETRY, nf_retries=(old.get("nf_retries") or 0) + 1)
                            return                                                 # context build (BO3.gg blip): tried again soon
                        BOARD[r["id"]] = {"at": time.time(), "state": "NOT FOUND", "reason": res["error"]}
                        return
                    v = verdict(res.get("grade"))
                    g = res.get("grade") or {}
                    kept = keep_last_hltv_grade(BOARD.get(r["id"]), g, r)
                    if kept:
                        BOARD[r["id"]] = kept
                        return
                    if perf_transient(g):                 # nothing to keep: the line waits (hidden) and is tried again soon -
                        BOARD[r["id"]] = outage_placeholder(BOARD.get(r["id"]), r)   # never an old-pipeline grade for a transient reason
                        return
                    pb = res.get("player_block") or {}
                    if (r.get("scope") or "12") == "123":   # display only: his Maps 1-3 record (never a grade input)
                        try:
                            pb["recent_form_123"] = records.maps123_record(pb, r["line"], "headshots" if r["prop"] == "hs" else "kills")
                        except Exception:
                            traceback.print_exc()
                    rf = comparison.record_for_scope(pb, r.get("scope"))   # the line's own maps - never Maps 1-2 for Map 1
                    fw = ((pb.get("overall") or {}).get("form_windows") or {}).get("3m") or {}
                    BOARD[r["id"]] = {"at": time.time(), "graded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "player_id": (pb.get("player") or {}).get("id"),     # BO3.gg id: Underdog -> player map
                                      "match_start": (res.get("next_match") or {}).get("start"),   # comparison audit
                                      # match headers + compact rows (UI 2026-10-09)
                                      "event": (res.get("next_match") or {}).get("event"), "bo": (res.get("next_match") or {}).get("bo"),
                                      "maps_known": ((res.get("next_match") or {}).get("maps") or [])[:3],
                                      "dstat": comparison.data_status(res.get("grade") or {}, v),
                                      "source": comparison.source_label(res.get("grade") or {}),
                                      "graded_line": r["line"], "graded_model": picklog.MODEL_VERSION,   # slips: exact line + model
                                      "grade": v.get("grade"), "side": v.get("side"), "p": v.get("p"),
                                      "units": v.get("units"), "proj": g.get("lambda_final"), "state": v.get("state"),
                                      "reason": v.get("reason"), "nick": (pb.get("player") or {}).get("nick"),
                                      # projection minus the line (the cut the grade uses since the 2026-10-07 continuity fix)
                                      "edge": round(g["lambda_final"] - r["line"], 2) if g.get("lambda_final") is not None else None,
                                      **_board_l10(rf, r),
                                      "hs_pct": fw.get("hs_pct"), "p_win": (g.get("detail") or {}).get("p_win"),
                                      "snip": pb.get("sniping_equiv"), "awp_note": pb.get("awp_note"),
                                      "market": g.get("market_quality"),
                                      "team_name": pb.get("playing_for") or pb.get("current_team"),
                                      "opp_name": (res.get("next_match") or {}).get("opponent"),
                                      "match_slug": (res.get("next_match") or {}).get("slug"),   # exact match id (settling slips)
                                      "tonight": (pb.get("_env") or {}).get("tonight"),   # LAN / online (match table projections)
                                      "book": r.get("book") or "underdog", "line_type": lt}
                    res["verdict"] = v
                    try:                                  # immutable comparison snapshot (changes only) + the row's evidence
                        ub = underdog.board()
                        pnl = comparison.panel(res, ub["rows"] + prizepicks.board()["rows"], board_at=ub.get("at"), id_map=ud_id_map(ub["rows"]))
                        if not perf_transient(g):             # an outage grade is no evidence of a changed forecast
                            comparison.snapshot(res, pnl, picklog.MODEL_VERSION)
                        if pnl:
                            BOARD[r["id"]].update(support=(pnl["supports"] or [None])[0], concern=pnl["concern"])
                    except Exception:
                        traceback.print_exc()
                    # the platform and the provider's market id travel with the logged forecast (one row per platform)
                    res["book"] = {"book": r.get("book") or "underdog", "market_id": r.get("market_id") or r.get("id"),
                                   "market": r.get("market"), "odds_type": lt}
                    if not perf_transient(g):                 # never logged as a forecast of the live model (calibration cohorts)
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
            now_ = datetime.now(timezone.utc).isoformat()
            live_ids = {r["id"] for r in rows if not r.get("start") or r["start"] > now_}
            for k in [k for k, v in list(BOARD.items()) if v.get("hltv_retry") and k not in live_ids]:
                BOARD.pop(k, None)                    # a line that left the feed / whose match started is not retried (no endless 10 s pause)
            _board_save()
            try:                                      # roles: the HLTV scraper's tables (30 min), then Liquipedia
                roles.refresh_hltv()
            except Exception:
                traceback.print_exc()
            try:
                roles.refresh([((BOARD.get(r["id"]) or {}).get("nick") or r["player"],
                                (BOARD.get(r["id"]) or {}).get("team_name") or r.get("team")) for r in rows])
            except Exception:
                traceback.print_exc()
            try:                                      # rebuilt builder, shadow only: fixed hourly batches (slip_batches.py)
                slip_batches.maybe_batch(board_payload()["rows"], picklog.MODEL_VERSION)
            except Exception:
                traceback.print_exc()
            try:                                      # shadow batches: settle finished ones (each once, durable) every 30 min
                if time.time() - _SLIP_SETTLED[0] > 1800:
                    _SLIP_SETTLED[0] = time.time()
                    slip_batches.settle_all(slip_batches.leg_result_from_picks(picklog.export()))
            except Exception:
                traceback.print_exc()
            try:                                      # the builder's own record: each new recommended slip, once
                slips.log_recommended(board_payload()["rows"], picklog.MODEL_VERSION)
            except Exception:
                traceback.print_exc()
            if len(BOARD) > 5000:
                BOARD.clear()
        except Exception:
            traceback.print_exc()
        # lines waiting on the HLTV database (kept grades / placeholders) are tried again within seconds, not after a full pass
        time.sleep(10 if any(v.get("hltv_retry") for v in list(BOARD.values())) else 60)


def cleanup():
    """Keep the volume from filling: slim/compress old files, drop stale ones."""
    try:
        freed = prune() + cs2_scout.prune_misc()
        try:
            import hltv_store
            hltv_store.prune()                   # stored HLTV answers older than 14 days
        except Exception:
            pass
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
    need = g["line"]                        # the cut the grade uses: projection above the line leans More
    gap = g["lambda_final"] - need          # > 0 More, < 0 Less
    # member wording (2026-10-10 audit): Over / Under everywhere (never More / Less), chances to 0.1% like every other
    # chance on the site, projection and gap to 0.1 like the card's Edge
    ou = "Over" if side == "More" else "Under"
    if g.get("scout"):
        return {"side": side, "state": "SCOUT", "grade": "SCOUT", "p": p, "units": 0.0,
                "confidence": "scout",
                "reason": f"No upcoming match found for his team - scouting view only. Leans {ou} "
                          f"({p:.1%}): projection {g['lambda_final']:.1f} vs line {g['line']}"}
    if no_less_side:
        why = (f"{g['line_type'].title()} line - only Over is offered here, but the model expects "
               f"Under ({p:.1%}); Over is only {g['p_more']:.1%}. Skip it.")
    elif g.get("form_fight"):
        why = f"FORM FIGHT - his last 10 matches at this line disagree with the projection - 0 units"
    elif g.get("tape_fight"):
        why = (f"TAPE FIGHT - model {ou} {p:.1%}, but his recent record at this line disagrees - max 0.5U")
    elif units > 0:
        why = f"Projection {g['lambda_final']:.1f} vs line {need:g} ({gap:+.1f} {stat})"
    else:
        why = (f"Projection {g['lambda_final']:.1f} vs line {need:g} ({gap:+.1f} {stat}) is small next "
               f"to how much his totals swing from match to match - leans {ou} {p:.1%}, 0 units")
    if g.get("zero_reason") and not no_less_side:
        # a gate (AWPer on HS, stand-in, transfer, tiny sample): still a side, never "no play"
        grade, units = "LEAN", 0.0
        why = (f"Leans {ou} {p:.1%} (projection {g['lambda_final']:.1f} vs line {need:g}) - 0 units: "
               f"{g['zero_reason']}")
    from grader import units_for_p
    if units and units < units_for_p(p) and "max" not in why:      # a cap cut the stake: say which one
        cap = next((f for f in g.get("flags", []) if re.search(r"max [0-9.]+U|capped [0-9.]+U|unit size limited", f)), None)
        if cap:
            why += f" · stake capped: {cap.split(' - ')[0].split(':')[0]}"
    if grade == "C" or (not units and grade not in ("LEAN", "C")):
        # owner 2026-09-29: under the bar the card says "Lean Over" / "Lean Under" instead of a C; a
        # letter grade with 0 units (FORM FIGHT, gates) reads as a Lean too - never "B · 0U"
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
    for k in ("_match_env", "overall_env"):
        (res.get("player_block") or {}).pop(k, None)
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
    ("OLD PROJECTION: the performance model is unavailable here (HLTV database unreachable",
     "HLTV database unreachable right now - temporary projection from the older model; the Prop Board keeps each line's last grade until the database answers again"),
    ("OLD PROJECTION", "Projection from the older model - the newer performance model has no HLTV data for this player or matchup"),
    ("CAP:", "Chance capped at 78% on kills props (big kill totals swing more than the model's spread)"),
    ("PROVISIONAL:", "Provisional: projection from BO3.gg history - HLTV has no history for this player"),
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
        res["simulation"] = {k: sim.get(k) for k in ("p_more", "p_push", "mean", "p10", "p50", "p90", "scenarios", "scenarios_note", "anchored")}
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
        if self.path.startswith("/api/search"):        # server-side seconds: request received -> response sent
            SEARCH_TIMES.append(round(time.time() - getattr(self, "_t0", time.time()), 3))
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
        if self.load_test():
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
                                       "hltv": _hltv_status(public=True),
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
                                       "upgrade": whop_auth.upgrade_url(), "slip_studio": SLIP_STUDIO})
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
            if u.path in ("/api/search", "/api/grade", "/api/board", "/api/line_check", "/api/match_compare", "/api/moneylines",
                          "/api/slip_studio/batch") and not self.allowed():
                sess = self.member()
                return self.send(401, {"error": "members_only", "signed_in": bool(sess),
                                       "upgrade": whop_auth.upgrade_url()})
            if u.path == "/api/search":
                return self.search(q.get("q", "").strip())
            if u.path.startswith("/api/") and u.path not in ("/api/board", "/api/moneylines", "/api/slip_studio/batch"):
                LAST_MEMBER[0] = time.time()      # research reads are cheap: they don't make the board worker step aside
            if u.path == "/api/moneylines":
                return self.moneylines()
            if u.path == "/api/slip_studio/batch":
                return self.studio_batch(q)
            if u.path == "/api/match_compare":
                bk_ = "prizepicks" if q.get("book") == "prizepicks" else "underdog"
                k_ = ("mcmp", q.get("team_slug"), q.get("opp_slug"), q.get("start"), q.get("maps") or "12", bk_)

                def _mc():
                    return 200, json.dumps(dict(match_compare(q.get("team_slug"), q.get("opp_slug"), q.get("team", ""), q.get("opponent", ""),
                                                              q.get("start"), q.get("maps") or "12", bk_), book=bk_), default=str).encode()
                st_, body_ = shared(k_, _mc, ttl=120)
                return self.send(st_, body_)
            if u.path == "/api/line_check":
                # before a pick goes on a slip: the line Underdog shows NOW for this player / stat / maps
                book = "prizepicks" if q.get("book") == "prizepicks" else "underdog"
                try:
                    ub = prizepicks.board() if book == "prizepicks" else underdog.board()
                    return self.send(200, comparison.line_check(ub, q.get("player", ""), q.get("prop", "kills"), q.get("maps", "12"),
                                                                q.get("line"), q.get("team"), q.get("start"), pid=q.get("pid") or None,
                                                                id_map=ud_id_map(ub["rows"]) if book == "underdog" else {},
                                                                book=book, odds_type=q.get("lt") or "standard"))
                except Exception:
                    return self.send(200, comparison.line_check(None, "", "", "", failed=True, book=book))
            if u.path == "/api/grade":
                with _MG_LOCK:
                    MEMBER_GRADES[0] += 1
                try:
                    return self.grade(q)
                finally:
                    with _MG_LOCK:
                        MEMBER_GRADES[0] -= 1
            if u.path == "/api/board":
                return self.send(200, board_payload("prizepicks" if q.get("book") == "prizepicks" else "underdog"))
            if u.path == "/admin":
                return self.admin(q)
            if u.path == "/admin/moneylines_status" and self.authed(q.get("token")):
                return self.send(200, moneylines_status())
            if u.path == "/admin/ml_eval" and self.authed(q.get("token")):
                return self.send(200, ml_eval_admin(q.get("refresh") == "1"))
            if u.path == "/admin/slips.json" and self.authed(q.get("token")):
                return self.send(200, {"builder": slips.BUILDER_VERSION, "data_dir": str(DATA_DIR), "hltv_roles": roles.hltv_status(),
                                       "recommended": slips.read(slips.RECOMMENDED), "loaded": slips.read(slips.LOADED)})
            if u.path == "/admin/slip_batches.json" and self.authed(q.get("token")):
                return self.send(200, {"version": slip_batches.VERSION, "batches": slip_batches.read()})
            if u.path == "/admin/diagnostics.json" and self.authed(q.get("token")):
                import diagnostics                        # admin-only diagnostic dashboard (flags, never causes)
                if not _DIAG.get("at") or time.time() - _DIAG["at"] > 600 or q.get("fresh"):
                    _DIAG.update(at=time.time(), data=diagnostics.build(picklog.export(), picklog.export_forecasts(inputs=True),
                                                                        slip_batches.read()))
                return self.send(200, _DIAG["data"])
            if u.path == "/admin/diagnostics" and self.authed(q.get("token")):
                return self.send(200, (WEB / "diagnostics.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/admin/comparison_audit" and self.authed(q.get("token")):
                # ONE cohort at ONE timestamp: every Underdog offer now, and for every graded board line whether the
                # comparison finds its own offer (by id / name) or why not
                ub = underdog.board(); rows = ub["rows"]; idm = ud_id_map(rows)
                out = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "board_fetched": ub.get("at"),
                       "underdog_offers": len(rows), "standard": sum(not r["alt"] for r in rows), "alternate": sum(bool(r["alt"]) for r in rows),
                       "graded_lines": 0, "missing_player_id": 0, "missing_match": 0, "matched_by_id": 0, "matched_by_name": 0,
                       "not_matched": 0, "rejected_reasons": {}, "examples": []}
                for r in rows:
                    gb = BOARD.get(r["id"]) or {}
                    if r["alt"] or not gb.get("side"):
                        continue
                    out["graded_lines"] += 1
                    if not gb.get("player_id"):
                        out["missing_player_id"] += 1
                    if not gb.get("match_start"):
                        out["missing_match"] += 1
                    res_ = {"grade": {"prop": r["prop"], "scope": r.get("scope") or "12"},
                            "player_block": {"player": {"nick": gb.get("nick") or r["player"], "id": gb.get("player_id")}, "playing_for": gb.get("team_name")},
                            "next_match": {"start": gb.get("match_start"), "opponent": gb.get("opp_name")}}
                    offers, rej = comparison.match_offers(res_, rows, id_map=idm)
                    own = next((o for o in offers if o["id"] == r["id"]), None)
                    if own:
                        out["matched_by_" + own["identity"]] += 1
                    else:
                        out["not_matched"] += 1
                        why = [x[1] for x in rej if x[0] == r["line"]] or ["no offer found (name / id)"]
                        k = why[0].split(" (")[0]
                        out["rejected_reasons"][k] = out["rejected_reasons"].get(k, 0) + 1
                        if len(out["examples"]) < 15:
                            out["examples"].append({"player": r["player"], "line": r["line"], "prop": r["prop"], "why": why[0]})
                return self.send(200, out)
            if u.path == "/admin/comparisons.json" and self.authed(q.get("token")):
                try:
                    with open(comparison.snapshot_path(), encoding="utf-8") as f:
                        lines = f.readlines()
                except OSError:
                    lines = []
                n = int(q.get("last") or 500)
                return self.send(200, {"version": comparison.VERSION, "total": len(lines), "sources": comparison.SOURCES,
                                       "snapshots": [json.loads(x) for x in lines[-n:] if x.strip()]})
            if u.path == "/admin/slip_batch_results.json" and self.authed(q.get("token")):
                new = slip_batches.settle_all(slip_batches.leg_result_from_picks(picklog.export()))
                return self.send(200, {"version": slip_batches.VERSION, "new": len(new), "results": slip_batches.results(),
                                       "payout_note": "standard 3x/6x/10x and x Underdog multipliers are ASSUMPTIONS, not real entries"})
            if u.path == "/admin/forecasts.json" and self.authed(q.get("token")):
                return self.send(200, {"forecasts": picklog.export_forecasts(inputs=bool(q.get("inputs")),
                                                                           last=q.get("last") if (q.get("last") or "").isdigit() else None)})
            if u.path == "/admin/picks.json" and self.authed(q.get("token")):
                return self.send(200, {"picks": picklog.export()})
            if u.path == "/admin/mark_tests" and self.authed(q.get("token")):
                ids = [int(x) for x in (q.get("ids") or "").split(",") if x.strip().isdigit()]
                picklog.mark_tests(ids)
                return self.send(200, {"marked": len(ids), "pushes_fixed": picklog.resettle_pushes()})
            if u.path == "/admin/whop_check" and self.authed(q.get("token")):
                # read-only health of the member gate for NEW and existing members (no names, emails or tokens returned)
                import requests as _rq
                H_ = {"Authorization": f"Bearer {os.environ.get('WHOP_API_KEY', '')}"}
                out = {"enabled": whop_auth.enabled(), "client_id_set": bool(os.environ.get("WHOP_CLIENT_ID")),
                       "api_key_set": bool(os.environ.get("WHOP_API_KEY")), "secret_set": bool(os.environ.get("WHOP_CLIENT_SECRET")),
                       "session_secret_set": bool(os.environ.get("SESSION_SECRET")), "products": whop_auth.products(),
                       "paid_plans": sorted(whop_auth.paid_plans()), "callback": whop_auth.public_url() + "/auth/callback",
                       "join_url": whop_auth.upgrade_url(), "recent_errors": whop_auth.LAST_ERRORS[-5:], "plans": {}}
                try:                                    # the user_id filter must really filter
                    r = _rq.get(whop_auth.MEMBERSHIPS, params={"user_id": "user_doesnotexist0000", "first": 5}, headers=H_, timeout=20)
                    out["fake_user_rows"] = len(r.json().get("data") or []) if r.status_code == 200 else f"HTTP {r.status_code}"
                except Exception as e:
                    out["fake_user_rows"] = type(e).__name__
                try:
                    j = _rq.get(whop_auth.JOIN_URL, timeout=20, allow_redirects=True)
                    out["join_url_status"] = j.status_code
                except Exception as e:
                    out["join_url_status"] = type(e).__name__
                sample_user = None
                for plan in sorted(whop_auth.paid_plans()):
                    st = {}
                    try:
                        r = _rq.get(whop_auth.MEMBERSHIPS, params={"plan_id": plan, "first": int(q.get("n", 10))}, headers=H_, timeout=20)
                        if r.status_code != 200:
                            out["plans"][plan] = f"HTTP {r.status_code}: {whop_auth._why(r)}"; continue
                        for m in r.json().get("data") or []:
                            uid = whop_auth._row_user(m)
                            sample_user = sample_user or uid
                            live = m.get("status") in whop_auth.LIVE_STATUSES or (plan == whop_auth.LIFETIME_PLAN and m.get("status") == "completed")
                            allowed = whop_auth.has_access(uid, force=True) if uid else None
                            k = f"{m.get('status')}: {'allowed' if allowed else 'denied'}"
                            st[k] = st.get(k, 0) + 1
                            if live and not allowed:
                                st["LIVE BUT DENIED"] = st.get("LIVE BUT DENIED", 0) + 1
                    except Exception as e:
                        st = {"error": type(e).__name__}
                    out["plans"][plan] = st
                if sample_user:                         # a real member's lookup returns only that member's rows
                    try:
                        r = _rq.get(whop_auth.MEMBERSHIPS, params={"user_id": sample_user, "first": 50}, headers=H_, timeout=20)
                        rows = r.json().get("data") or []
                        out["one_user_rows"] = len(rows)
                        out["one_user_other_rows"] = sum(1 for m in rows if whop_auth._row_user(m) != sample_user)
                    except Exception as e:
                        out["one_user_rows"] = type(e).__name__
                return self.send(200, out)
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
            if u.path == "/admin/pp_history" and self.authed(q.get("token")):
                # every PrizePicks line Scout observed: openings, moves, provider-id changes, removals (prizepicks.py)
                ev = prizepicks.history(q.get("market") or None, int(q.get("limit") or 500))
                b = prizepicks.board()
                return self.send(200, {"feed": {k: b.get(k) for k in ("state", "at", "checked_at", "lines", "error", "ingested_at", "source")},
                                       "ingests": prizepicks.ingests(), "events": ev,
                                       "counts": dict(collections.Counter(e.get("event") for e in ev))})
            if u.path == "/admin/namesakes" and self.authed(q.get("token")):
                # read-only: every board player with 2+ HLTV players of the same name - which one the projection uses and why
                import hltv
                rows_ = board_payload()["rows"] + board_payload("prizepicks")["rows"]
                seen_, out = set(), []
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        for r in rows_:
                            nick, team = r.get("nick") or r.get("player"), r.get("team_name") or r.get("team")
                            k = (hltv._norm(nick), hltv._norm(team))
                            if k in seen_:
                                continue
                            seen_.add(k)
                            cur.execute("""SELECT ps.playerid, string_agg(DISTINCT t.name, ', '), count(*), max(mo.date)
                                           FROM players p JOIN player_stats ps USING (playerid) JOIN teams t ON t.teamid = ps.teamid
                                           JOIN match_overview mo ON mo.matchid = ps.matchid
                                           WHERE lower(regexp_replace(p.name, '[^a-zA-Z0-9]', '', 'g')) = %s GROUP BY ps.playerid""",
                                        (hltv._norm(nick),))
                            cands = cur.fetchall()
                            if len(cands) < 2:
                                continue
                            chosen = hltv._player_ids(cur, nick, team)
                            variants = hltv.team_name_variants(team)
                            out.append({"player": nick, "team": team, "chosen": chosen[0] if chosen else None,
                                        "team_matched": any(hltv._norm(x) in variants for c in cands for x in str(c[1]).split(", ") if c[0] == (chosen[0] if chosen else None)),
                                        "candidates": [{"id": c[0], "teams": c[1], "rows": c[2], "last": str(c[3])} for c in cands]})
                finally:
                    con.close()
                return self.send(200, {"board_players_with_namesakes": len(out), "guessed_without_team_match": sum(1 for o in out if not o["team_matched"]), "rows": out})
            if u.path == "/admin/hltv_status" and self.authed(q.get("token")):
                return self.send(200, _hltv_status())      # database host (name only), breaker, last error, pool
            if u.path == "/admin/prop_feed" and self.authed(q.get("token")):
                # read-only look at the scraper's props tables (PrizePicks receiver): status, volume, freshness, samples
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        def rows(sql, args=None):
                            cur.execute(sql, args)
                            cols = [c[0] for c in cur.description]
                            return [dict(zip(cols, [str(v) if v is not None else None for v in r])) for r in cur.fetchall()]
                        out = {"status": rows("SELECT * FROM prop_status ORDER BY book"),
                               "counts": rows("""SELECT book, maps, market, odds_type, count(*) n, min(start_time) first_start, max(start_time) last_start,
                                                 min(updated_at) oldest_update, max(updated_at) newest_update, sum((moves > 0)::int) moved,
                                                 sum((higher IS NOT NULL)::int) with_higher
                                          FROM prop_lines GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4"""),
                               "upcoming": rows("""SELECT book, count(*) n, sum((updated_at >= now() - interval '90 minutes')::int) seen_90min
                                            FROM prop_lines WHERE start_time > now() GROUP BY 1"""),
                               "sample": rows("""SELECT * FROM prop_lines WHERE book = %s AND start_time > now() ORDER BY start_time, player LIMIT %s""",
                                              (q.get("book") or "prizepicks", int(q.get("n") or 12))),
                               "grades_columns": rows("""SELECT column_name, data_type FROM information_schema.columns
                                                  WHERE table_name = 'prop_grades' ORDER BY ordinal_position"""),
                               "grades_sample": rows("SELECT * FROM prop_grades ORDER BY 1 DESC LIMIT 3")}
                        return self.send(200, out)
                finally:
                    con.close()
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
            if u.path == "/admin/hltv_hs_rows" and self.authed(q.get("token")):
                # private analysis: every player-map with kills, headshots, rounds and the map (played maps only)
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 120000")
                        cur.execute("""SELECT mo.date::text, ps.playerid, mp.name, ps.k, h.hs, own.score + opp.score, mo.matchid
                                       FROM player_stats ps JOIN match_overview mo ON mo.matchid = ps.matchid
                                       JOIN match_maps mm ON mm.matchid = ps.matchid AND mm.mapid = ps.mapid
                                       JOIN maps mp ON mp.mapid = ps.mapid
                                       JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ps.playerid
                                       JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid AND own.mapid = ps.mapid AND own.sideid = 0
                                       JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
                                       WHERE ps.sideid = 0 AND h.hs IS NOT NULL AND mo.date >= %s""", (q.get("since", "2025-09-01"),))
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
            if u.path == "/admin/fix_scope_settlements" and self.authed(q.get("token")):
                # 2026-10-08 incident: dry run by default; apply=1 corrects and logs old + new values
                # scopes=12 (Maps 1-2) is DRY RUN ONLY: a read-only replay of the new shared settlement path
                sc = tuple((q.get("scopes") or "1,123").split(","))
                return self.send(200, picklog.fix_scope_settlements(apply=q.get("apply") == "1" and "12" not in sc, scopes=sc,
                                                                    test=int(q["test"]) if (q.get("test") or "").isdigit() else None))
            if u.path == "/admin/perf_check" and self.authed(q.get("token")):
                # performance model on LIVE data for given players (to prove it equals the training export)
                import perf_live
                from datetime import date as _d
                items = json.loads(q.get("items") or "[]")[:40]   # [[nick, team, opponent, lan, scope, prop, day], ...]
                out = []
                for nick, team, opp, lan, sc, prop, day in items:
                    try:
                        out.append(perf_live.projection(nick, team, opp, lan, sc, prop, _d.fromisoformat(day).toordinal()))
                    except Exception as e:
                        out.append({"error": str(e)[:200]})
                return self.send(200, {"rows": out})
            if u.path == "/admin/hltv_export" and self.authed(q.get("token")):
                # performance-model dataset (2026-10-08 rebuild): one row per player per map, totals side, with the
                # map's rounds / result, map order, LAN flag, event, both teams; date window <= 31 days per call
                import hltv
                from datetime import date as _d
                d0, d1 = _d.fromisoformat(q.get("from")), _d.fromisoformat(q.get("to"))
                if (d1 - d0).days > 31:
                    return self.send(400, {"error": "max 31 days per call"})
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 120000")
                        cur.execute("""SELECT mo.matchid, mo.date, mo.event, mo.lan, mm.number, mp.name, ps.playerid, ps.teamid, opp.teamid,
                                              ps.k, ps.d, ps.ek, ps.ed, ps.adr, ps.eadr, ps.kast, ps.ekast, ps.rating, ps.roundswing, h.hs,
                                              own.score, opp.score
                                       FROM match_maps mm
                                       JOIN match_overview mo USING (matchid)
                                       JOIN maps mp ON mp.mapid = mm.mapid
                                       JOIN player_stats ps ON ps.matchid = mm.matchid AND ps.mapid = mm.mapid AND ps.sideid = 0
                                       JOIN matches own ON own.matchid = mm.matchid AND own.teamid = ps.teamid AND own.mapid = mm.mapid AND own.sideid = 0
                                       JOIN matches opp ON opp.matchid = mm.matchid AND opp.teamid <> ps.teamid AND opp.mapid = mm.mapid AND opp.sideid = 0
                                       LEFT JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ps.playerid
                                       WHERE mo.date >= %s AND mo.date <= %s
                                       ORDER BY mo.date, mo.matchid, mm.number""", (d0, d1))
                        cols = ["match", "date", "event", "lan", "map_no", "map", "player", "team", "opp", "k", "d", "ek", "ed",
                                "adr", "eadr", "kast", "ekast", "rating", "swing", "hs", "own", "opp_score"]
                        rows = [[str(v) if k == "date" else v for k, v in zip(cols, r)] for r in cur.fetchall()]
                        cur.execute("SELECT playerid, name FROM players")
                        names = dict(cur.fetchall())
                        cur.execute("SELECT teamid, name, country FROM teams")
                        teams = {t: [n, c] for t, n, c in cur.fetchall()}
                        return self.send(200, {"cols": cols, "rows": rows, "players": names, "teams": teams})
                finally:
                    con.close()
            if u.path == "/admin/hltv_sides" and self.authed(q.get("token")):
                # COLLECTION ONLY (2026-10-09): per-side player rows (sideid 1/2 as stored by the scraper) and every
                # per-side score row, for the CT/T factor test; date window <= 31 days per call; never used in grading
                import hltv
                from datetime import date as _d
                d0, d1 = _d.fromisoformat(q.get("from")), _d.fromisoformat(q.get("to"))
                if (d1 - d0).days > 31:
                    return self.send(400, {"error": "max 31 days per call"})
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 120000")
                        cur.execute("""SELECT mo.matchid, mo.date, mm.number, ps.playerid, ps.teamid, ps.sideid, ps.k, ps.d, ps.adr, ps.kast, ps.rating
                                       FROM match_maps mm JOIN match_overview mo USING (matchid)
                                       JOIN player_stats ps ON ps.matchid = mm.matchid AND ps.mapid = mm.mapid AND ps.sideid IN (1, 2)
                                       WHERE mo.date >= %s AND mo.date <= %s ORDER BY mo.date, mo.matchid, mm.number""", (d0, d1))
                        prow = [[str(v) if i == 1 else v for i, v in enumerate(r)] for r in cur.fetchall()]
                        cur.execute("""SELECT m.matchid, mm.number, m.teamid, m.sideid, m.score
                                       FROM matches m JOIN match_maps mm ON mm.matchid = m.matchid AND mm.mapid = m.mapid
                                       JOIN match_overview mo ON mo.matchid = m.matchid
                                       WHERE mo.date >= %s AND mo.date <= %s""", (d0, d1))
                        srow = cur.fetchall()
                    return self.send(200, {"player_cols": ["match", "date", "map_no", "player", "team", "sideid", "k", "d", "adr", "kast", "rating"],
                                           "player_rows": prow, "score_cols": ["match", "map_no", "team", "sideid", "score"], "score_rows": srow})
                finally:
                    con.close()
            if u.path == "/admin/role_snapshots" and self.authed(q.get("token")):
                import roles as _roles
                try:
                    with open(_roles.SNAP, encoding="utf-8") as f:
                        lines = f.readlines()
                except OSError:
                    lines = []
                days = [json.loads(x)["day"] for x in lines if x.strip()]
                if q.get("day"):
                    return self.send(200, next((json.loads(x) for x in lines if x.strip() and json.loads(x)["day"] == q["day"]), {}))
                return self.send(200, {"days": days})
            if u.path == "/admin/hltv_ranks" and self.authed(q.get("token")):
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 60000")
                        cur.execute("SELECT date, teamid, rank, points FROM team_rankings ORDER BY date")
                        return self.send(200, {"rows": [[str(d), t, r, p] for d, t, r, p in cur.fetchall()]})
                finally:
                    con.close()
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
                                       "search_wait": list(SEARCH_TIMES)[-int(q.get("s", 0) or 0):] if q.get("s") else [],
                                       "recent": list(cs2_scout.TIMINGS)[-int(q.get("n", 60)):]})
            if u.path == "/admin/probe" and self.authed(q.get("token")):
                # private: raw shape of the backup data sources (keys never included)
                import oddspapi
                return self.send(200, {"oddspapi": oddspapi.probe()})
            if u.path == "/admin/oddspapi_markets" and self.authed(q.get("token")):
                import oddspapi                           # read-only source inventory (2026-10-09)
                return self.send(200, oddspapi.markets_inventory(int(q.get("n") or 3)))
            return self.send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": f"Something went wrong: {e}"})

    def search(self, q):
        if len(q) < 2:
            return self.send(200, {"results": []})
        ql = q.lower()
        # typing: a longer query can be answered from a shorter one's complete list (<8 hits)
        # HLTV first (in memory); BO3.gg only if it's down. 2026-10-09: one scan per distinct query per index load -
        # identical queries arriving together share it (a 700-member burst used to scan the whole index ~2,100 times)
        try:
            k_ = ("hlsearch", hltv._INDEX.get("at"), hltv._norm(q))

            def _hl():
                r_ = hltv.search_players(q, 8)
                return (200, json.dumps({"results": r_}).encode()) if r_ is not None else (503, b"")
            st_, body_ = shared(k_, _hl, ttl=3600)
            if st_ == 200:
                return self.send(200, body_)
        except Exception:
            pass
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

    # ------------------------------------------------------------ research tools (moneylines + slip studio)
    def moneylines(self):
        """GET /api/moneylines: one shared response for everyone, rebuilt at most every ML_TTL seconds (single flight)."""
        def compute():
            return 200, json.dumps(moneylines_payload(), default=str).encode()
        try:
            st_, body_ = shared(("moneylines",), compute, ttl=ML_TTL)
        except Exception as e:
            sys.stderr.write(f"  moneylines: {type(e).__name__}: {str(e)[:200]}\n")
            return self.send(503, {"error": "moneylines_unavailable", "label": "Market probability unavailable"})
        return self.send(st_, body_)

    def studio_member(self):
        """Whose slip batches these are: the signed-in Whop member (user id), a load test (fixed id, optional X-Test-Member
        suffix so a load test can act as many members), or - only where Whop sign-in is off and access is open (local) -
        the client address. None = nobody."""
        if self.load_test():
            sfx = re.sub(r"[^A-Za-z0-9_-]", "", str(self.headers.get("X-Test-Member") or ""))[:32]
            return "load-test" + (":" + sfx if sfx else "")
        if whop_auth.enabled():
            sess = self.member()
            return f"whop:{sess['user']}" if sess and sess.get("ok") and sess.get("user") else None
        return ("anon:" + self.client_ip()) if self.allowed() else None

    def studio_batch(self, q):
        """GET /api/slip_studio/batch[?id=]: the member's batch (default: the open one) with all its slips and exposure."""
        if not SLIP_STUDIO:
            return self.send(404, {"error": "slip_studio_off"})
        sid = self.studio_member()
        if not sid:
            return self.send(401, {"error": "members_only"})
        import slip_builder
        bid = q.get("id") or ""
        if bid and not _BATCH_RE.match(bid):
            return self.send(400, {"error": "bad batch id"})
        cur = slip_builder.current_batch(sid)
        if bid and bid != cur and not slip_builder.owns(sid, bid):
            return self.send(404, {"error": "unknown batch"})
        b = slip_builder.get_batch(bid or cur)
        if b is None:
            return self.send(404, {"error": "unknown batch"})
        return self.send(200, dict(b, current=(b["batch_id"] == cur), settled_batches=settled_batches(),
                                   track_record=studio_track_record(), builder_version=slip_builder.VERSION))

    def studio_post(self, path, n):
        """POST /api/slip_studio/build | verify | event (JSON bodies, members only, every field bounded)."""
        if n > STUDIO_BODY_MAX:
            self.close_connection = True              # the unread body would corrupt a kept-alive connection
            return self.send(413, {"error": f"body over {STUDIO_BODY_MAX} bytes"})
        body = self.rfile.read(n) if n > 0 else b""
        if not SLIP_STUDIO or path not in ("/api/slip_studio/build", "/api/slip_studio/verify", "/api/slip_studio/event"):
            return self.send(404, {"error": "not found"})
        if not self.allowed():
            return self.send(401, {"error": "members_only", "signed_in": bool(self.member()), "upgrade": whop_auth.upgrade_url()})
        sid = self.studio_member()
        if not sid:
            return self.send(401, {"error": "members_only"})
        try:
            d = json.loads(body or b"{}", parse_constant=_no_json_constant)      # NaN / Infinity: 400, never 500
            if not isinstance(d, dict):
                raise ValueError
        except ValueError:
            return self.send(400, {"error": "body must be a JSON object"})
        idem = d.get("idem")
        if idem is not None and (not isinstance(idem, str) or not _IDEM_RE.match(idem)):
            return self.send(400, {"error": "idem must be 8-64 characters of A-Z a-z 0-9 _ . : -"})
        import slip_builder
        try:
            if path == "/api/slip_studio/build":
                return self.studio_build(slip_builder, sid, d, idem)
            if path == "/api/slip_studio/verify":
                return self.studio_verify(slip_builder, sid, d, idem)
            return self.studio_event(slip_builder, sid, d, idem)
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": f"slip studio failed ({type(e).__name__}) - try again"})

    def studio_build(self, sb, sid, d, idem):
        extra = set(d) - {"settings", "batch_id", "new_batch", "idem", "replace"}
        if extra:
            return self.send(400, {"error": "unknown field(s): " + ", ".join(sorted(extra))})
        if not idem:
            return self.send(400, {"error": "idem (the request id) is required"})
        try:
            st = studio_settings(d.get("settings") if d.get("settings") is not None else {})
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        nb, bid, rep = d.get("new_batch", False), d.get("batch_id"), d.get("replace")
        if not isinstance(nb, bool):
            return self.send(400, {"error": "new_batch must be true or false"})
        if bid is not None and (not isinstance(bid, str) or not _BATCH_RE.match(bid)):
            return self.send(400, {"error": "bad batch id"})
        if rep is not None and (not isinstance(rep, str) or not _SLIP_RE.match(rep) or nb):
            return self.send(400, {"error": "replace must be a stored slip id of this batch (not with new_batch)"})
        if not self.load_test() and not studio_rate_ok(sid, idem):
            return self.send(429, {"error": f"Too many builds - limit is {STUDIO_BUILDS_PER_MIN} a minute. Wait a moment."})
        if rep:
            old = sb.get_slip(rep)
            if not old or not old.get("batch_id") or not sb.owns(sid, old["batch_id"]) or (bid and bid != old["batch_id"]):
                return self.send(404, {"error": "unknown slip"})
            batch = old["batch_id"]
            st = dict(st, book=(old.get("settings") or {}).get("book") or old.get("book") or st["book"])
        elif nb:
            batch = sb.new_batch(sid, key="nb:" + idem)
        elif bid:
            if not sb.owns(sid, bid):
                return self.send(404, {"error": "unknown batch"})
            batch = bid
        else:
            batch = sb.current_batch(sid)
        gb = graded_board(st["book"])
        try:
            res = sb.build(gb["rows"], st, batch=batch, key=idem, model=gb.get("version"), replace=rep)
        except ValueError as e:
            if "closed" in str(e):
                return self.send(409, {"error": str(e), "current_batch": sb.current_batch(sid)})
            return self.send(400, {"error": str(e)})
        return self.send(200, dict(res, settled_batches=settled_batches(), track_record=studio_track_record(),
                                   board={"book": st["book"], "updated": gb.get("updated"), "graded_rows": len(gb.get("rows") or []),
                                          "feed_state": (gb.get("feed") or {}).get("state"), "error": gb.get("error")}))

    def _studio_slip(self, sb, sid, d):
        sl = d.get("slip")
        slip_id = sl.get("slip_id") if isinstance(sl, dict) else sl if sl is not None else d.get("slip_id")
        if not isinstance(slip_id, str) or not _SLIP_RE.match(slip_id):
            return None, self.send(400, {"error": "slip must be a stored slip id (preview slips can't be verified)"})
        rec = sb.get_slip(slip_id)
        if not rec or not rec.get("batch_id") or not sb.owns(sid, rec["batch_id"]):
            return None, self.send(404, {"error": "unknown slip"})
        return rec, None

    def _studio_events_ok(self, sid, idem):
        """verify + event: their own per-member bucket (a loop on them can't grow slip_studio.db or eat build quota)."""
        return self.load_test() or studio_rate_ok(sid, idem, bucket=_STUDIO_EV_HITS, limit=STUDIO_EVENTS_PER_MIN)

    def studio_verify(self, sb, sid, d, idem):
        """Before copying: the platform's board is refreshed (graded snapshot <= 2 s old, the shared feed), then every leg is
        checked. Any change -> action 'rebuild' with what changed; the old chance is never carried to a new line."""
        if set(d) - {"slip", "slip_id", "idem"}:
            return self.send(400, {"error": "unknown field(s)"})
        if not self._studio_events_ok(sid, idem):
            return self.send(429, {"error": f"Too many checks - limit is {STUDIO_EVENTS_PER_MIN} a minute. Wait a moment."})
        rec, err = self._studio_slip(sb, sid, d)
        if rec is None:
            return err
        book = rec.get("book") or "underdog"
        feed = prizepicks.board() if book == "prizepicks" else underdog.board()
        graded = graded_board(book, max_age=2)
        v = sb.verify_before_copy(rec, {book: {"feed": feed, "graded": graded}}, model=graded.get("version"))
        # one stored row per request id - or per slip per 30 s without one (the answer is still computed every time)
        key = "v:" + (idem or f"{rec['slip_id']}:{int(time.time() // 30)}")
        try:
            sb.record_event(rec["batch_id"], rec["slip_id"], "verify_ok" if v["ok"] else "verify_changed",
                            key=key, detail={"changes": v["changes"][:10]})
        except ValueError:
            pass
        return self.send(200, dict(v, slip_id=rec["slip_id"], batch_id=rec["batch_id"], book=book,
                                   copy_text=sb.copy_text(rec) if v["ok"] else None))

    def studio_event(self, sb, sid, d, idem):
        if set(d) - {"batch_id", "slip_id", "kind", "idem"}:
            return self.send(400, {"error": "unknown field(s)"})
        kind, bid = d.get("kind"), d.get("batch_id")
        if kind not in sb.EVENT_KINDS:
            return self.send(400, {"error": "kind must be one of " + ", ".join(sb.EVENT_KINDS)})
        if not isinstance(bid, str) or not _BATCH_RE.match(bid):
            return self.send(400, {"error": "bad batch id"})
        if not isinstance(d.get("slip_id"), str) or not _SLIP_RE.match(d["slip_id"]):
            return self.send(400, {"error": "bad slip id"})
        if not self._studio_events_ok(sid, idem):
            return self.send(429, {"error": f"Too many requests - limit is {STUDIO_EVENTS_PER_MIN} a minute. Wait a moment."})
        if not sb.owns(sid, bid):
            return self.send(404, {"error": "unknown batch"})
        key = idem or f"e:{d['slip_id']}:{kind}:{int(time.time() // 30)}"       # no request id: one row per 30 s
        try:
            new = sb.record_event(bid, d["slip_id"], kind, key=key)
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        return self.send(200, {"ok": True, "duplicate": not new})


    def grade(self, q):
        load_test = self.load_test()                     # owner's own tests
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
        # 2026-10-09 audit: 79 member lines were impossible (e.g. a 1675 headshot line); the old pipeline then pulled its
        # projection toward the line (846 HS). Generous plausible ranges - anything outside is a typo, not a prop.
        hi_ = PLAUSIBLE_MAX[prop][scope]
        if not (0 < line <= hi_):
            return self.send(400, {"error": f"{line} doesn't look like a real {'headshots' if prop == 'hs' else 'kills'} line for "
                                            f"{ {'1': 'Map 1', '12': 'Maps 1-2', '123': 'Maps 1-3'}[scope]} (expected between 0 and {hi_}). Check the number."})
        slug, match = q.get("slug") or None, q.get("match") or None
        opp = (q.get("opponent") or "")[:60].strip() or None
        team = (q.get("team") or "")[:60].strip() or None     # board click: the team the board graded him for (namesakes)
        pkey = (slug or name).lower()
        # the card's platform (board / match-players clicks send it; typed cards = Underdog): its stake cap is its own board's
        book = "prizepicks" if q.get("book") == "prizepicks" or lt in ("goblin", "demon") else "underdog"
        key = (pkey, line, prop, lt, match, (opp or "").lower(), scope, (team or "").lower(), book)

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
                                          match=match, opponent=opp, team_hint=team)
                    tm["grade"] = round(time.time() - t0, 2)
            t0 = time.time()
            if res.get("error"):
                return 404, json.dumps({"error": res["error"][0].upper() + res["error"][1:]}).encode()
            pb_ = res.get("player_block") or {}
            mark_warm(pkey, name, (pb_.get("player") or {}).get("slug"), (pb_.get("player") or {}).get("nick"))
            if scope == "123":                           # display only: his Maps 1-3 record (never a grade input)
                try:
                    pb_["recent_form_123"] = records.maps123_record(pb_, line, "headshots" if prop == "hs" else "kills")
                except Exception:
                    traceback.print_exc()
            res["verdict"] = verdict(res.get("grade"))
            res["caps"] = stake_caps()
            outage = perf_transient(res.get("grade") or {})
            try:
                res["underdog"] = underdog.line_for((pb_.get("player") or {}).get("nick") or name, prop)
            except Exception:
                res["underdog"] = None
            try:                                         # comparison panel: context only, never changes the grade
                tc = time.time()
                ub = underdog.board()
                res["comparison"] = comparison.panel(res, ub["rows"] + prizepicks.board()["rows"], board_at=ub.get("at"), id_map=ud_id_map(ub["rows"]))
                tm["comparison"] = round(time.time() - tc, 3)
            except Exception:
                res["comparison"] = {"error": "comparison unavailable"}
            tm["underdog"] = round(time.time() - t0, 2)
            t0 = time.time()
            try:
                picklog.log_async(res, res["verdict"], test=load_test)   # written in the background
            except Exception:
                traceback.print_exc()
            tm["log"] = round(time.time() - t0, 2)
            try:                                         # after logging: the log keeps the grader's own stake
                res["verdict"] = board_stake(res, prop, scope, line, lt, book)
                if outage:                               # outage: tell the member what the board still shows
                    res["hltv_outage"] = board_last_hltv(res, prop, scope)
            except Exception:
                traceback.print_exc()
            t0 = time.time()
            body = json.dumps(public(res), default=str).encode()
            tm["json"] = round(time.time() - t0, 2)
            REQ_TIMES.append(dict(tm, player=name, at=round(time.time()), total=round(time.time() - t_in, 2)))
            return 200, body, (5 if outage else None)    # an outage grade is reused for 5 s, not 60

        status, body = shared(key, compute)
        return self.send(status, body)

    # ------------------------------------------------------------ Phase 10 admin (private)
    def load_test(self):
        """The owner's load-test / audit header - constant-time compare like the admin token."""
        import hmac
        return bool(ADMIN_TOKEN) and hmac.compare_digest(str(self.headers.get("X-Load-Test") or ""), ADMIN_TOKEN)

    def authed(self, token):
        # the admin link's ?token= still works; scripts send the X-Admin-Token header instead, so the token stays out
        # of URLs and logs (2026-10-10). Constant-time compare.
        import hmac
        tok = token or self.headers.get("X-Admin-Token") or ""
        return bool(ADMIN_TOKEN) and hmac.compare_digest(str(tok), ADMIN_TOKEN)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:
            self.close_connection = True
            return self.send(400, {"error": "bad Content-Length"})
        if u.path.startswith("/api/slip_studio/"):
            return self.studio_post(u.path, n)
        if u.path == "/api/slip_log":                  # a member loaded an auto slip (slips.py keeps the log)
            body = self.rfile.read(min(n, 1 << 20))
            if not self.allowed():
                return self.send(401, {"error": "members_only"})
            try:
                d = json.loads(body)
                assert isinstance(d, dict) and isinstance(d.get("legs"), list) and 2 <= len(d["legs"]) <= 6
                assert all(isinstance(l, dict) for l in d["legs"]) and len(body) <= 20000
                new = slips.log_loaded(d)
            except Exception:
                return self.send(400, {"error": "bad slip"})
            return self.send(200, {"ok": True, "duplicate": not new})
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
<h3>Picks (newest first) - enter Underdog's closing line for CLV</h3><div class=scroll><table>
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
    # research tools last (matchid tables, then the moneyline refreshers): their own thread, failures isolated
    threading.Thread(target=start_research, daemon=True, name="research").start()
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
