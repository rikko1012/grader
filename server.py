#!/usr/bin/env python3
"""
server.py - the web interface for cs2_scout.

    python server.py            then open http://localhost:8000

GET /                   the page (web/index.html)
GET /api/search?q=      player suggestions from BO3.gg
GET /api/grade?player=&line=&prop=kills|hs&line_type=standard|goblin|demon[&slug=]
                        the full grade + supporting data

One grade runs at a time (keeps requests to BO3.gg polite). Standard library only.
"""
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import time
from datetime import datetime, timezone

import cs2_scout
from bo3 import BO3
from grader import units_for
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
GRADE_LOCK = threading.Lock()
PLAYABLE = ("A+", "A", "A-", "B+", "B")
PRELOAD_STATUS = {"enabled": PRELOAD, "players_ready": 0, "last_pass": None, "current": None, "errors": 0}


def preload_forever():
    """Grade every rostered player of every upcoming match once, so members' look-ups hit
    saved data. Matches with a Polymarket market first (those get prop lines), then by start."""
    warmed = {}   # slug -> time loaded
    while True:
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
            for nick, slug in queue:
                if time.time() - warmed.get(slug, 0) < REFRESH_AFTER:
                    continue
                PRELOAD_STATUS["current"] = nick
                try:
                    cs2_scout.grade(nick, 20.5, slug=slug, faceit_key=None, background=True)
                    warmed[slug] = time.time()
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
    """Which side to play, for the headline. Sizing uses the framework's unit table."""
    if not g or "p_more" not in g:
        return {"side": None, "grade": (g or {}).get("grade", "NO GRADE"), "units": 0.0,
                "reason": (g or {}).get("reason")}
    small = any("Gate D" in f for f in g["flags"])
    more, less = g["grade"], g.get("grade_less")
    if g["status"] == "PLAY":
        return {"side": "More", "grade": more, "p": g["p_more"], "units": g["units"]}
    if less in PLAYABLE:
        return {"side": "Less", "grade": less, "p": g["p_less"], "units": units_for(less, small)}
    return {"side": None, "grade": "C", "p": max(g["p_more"], g["p_less"]), "units": 0.0,
            "reason": "Neither side reaches B (57%)" if g["line_type"] == "standard"
            else "More is below B and this line type has no Less"}


def public(res):
    """What members see: drop admin-only details."""
    fi = res.pop("faceit_identity", None) or {}
    if not fi.get("show_to_members"):
        res.pop("faceit", None)
    res.pop("grader_inputs", None)
    pb = res.get("player_block") or {}
    for r in pb.get("recent_maps") or []:
        r.pop("steam_id", None)
        r.pop("lineup", None)
    pb.pop("steam_id", None)
    return res


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        sys.stderr.write("  " + fmt % a + "\n")

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/health":
                return self.send(200, {"ok": True, "faceit_key": bool(faceit_key()), "preload": PRELOAD_STATUS})
            if u.path in ("/", "/index.html"):
                return self.send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/search":
                return self.search(q.get("q", "").strip())
            if u.path == "/api/grade":
                return self.grade(q)
            return self.send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": f"Something went wrong: {e}"})

    def search(self, q):
        if len(q) < 2:
            return self.send(200, {"results": []})
        b = BO3()
        try:
            hits = b.search_players(q, 8).get("results") or []
        finally:
            b.close()
        return self.send(200, {"results": [
            {"slug": h["slug"], "nick": h.get("nickname"),
             "name": " ".join(x for x in (h.get("first_name"), h.get("last_name")) if x),
             "country": (h.get("country") or {}).get("code"), "image": h.get("image_url")}
            for h in hits]})

    def grade(self, q):
        name = q.get("player", "").strip()
        try:
            line = float(q.get("line", ""))
        except ValueError:
            return self.send(400, {"error": "Enter the line as a number, e.g. 27.5"})
        if not name:
            return self.send(400, {"error": "Enter a player name"})
        prop = q.get("prop", "kills") if q.get("prop") in ("kills", "hs") else "kills"
        lt = q.get("line_type", "standard") if q.get("line_type") in ("standard", "goblin", "demon") else "standard"
        with GRADE_LOCK:
            res = cs2_scout.grade(name, line, prop, lt, slug=q.get("slug") or None, faceit_key=faceit_key())
        if res.get("error"):
            return self.send(404, {"error": res["error"][0].upper() + res["error"][1:]})
        res["verdict"] = verdict(res.get("grade"))
        return self.send(200, public(res))


if __name__ == "__main__":
    if PRELOAD:
        threading.Thread(target=preload_forever, daemon=True, name="preload").start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"cs2_scout running on {HOST}:{PORT} - open http://localhost:{PORT}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
