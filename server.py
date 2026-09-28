#!/usr/bin/env python3
"""
server.py - the web interface for cs2_scout.

    python server.py            then open http://localhost:8000

GET /                   the page (web/index.html)
GET /api/search?q=      player suggestions from BO3.gg
GET /api/grade?player=&line=&prop=kills|hs&line_type=standard|goblin|demon[&slug=][&match=]
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
import html as htmllib
from urllib.parse import parse_qs, urlparse

import time
from datetime import datetime, timezone

import cs2_scout
import picklog
from bo3 import BO3, prune, storage_report

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
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")   # set on Railway; no token = admin page disabled

PRELOAD_STATUS = {"enabled": PRELOAD, "players_ready": 0, "last_pass": None, "current": None, "errors": 0}


STORAGE = {"last_cleanup": None, "freed_mb": 0.0}


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
    if side == "Less" and g["line_type"] != "standard":
        side = "More"   # goblin/demon lines have no Less on PrizePicks
    p = g["p_more"] if side == "More" else g["p_less"]
    grade = g["grade"] if side == "More" else g["grade_less"]
    units = g.get("units", 0) if side == "More" else g.get("units_less", 0)
    stat = "headshots" if g.get("prop") == "hs" else "kills"
    gap = g["lambda_final"] - g["line"]
    if g.get("scout"):
        return {"side": side, "state": "SCOUT", "grade": "SCOUT", "p": p, "units": 0.0,
                "confidence": "scout",
                "reason": f"No upcoming match found for his team - scouting view only. Leans {side} "
                          f"({p:.0%}): projection {g['lambda_final']:.1f} vs line {g['line']}"}
    if g.get("form_fight"):
        why = f"FORM FIGHT - his last 10 matches disagree with the model, so confidence is halved"
    elif g.get("tape_fight"):
        why = (f"TAPE FIGHT - model {side} {p:.0%}, but his recent record at this line disagrees - max 0.25U")
    elif units > 0:
        why = f"Projection {g['lambda_final']:.1f} vs line {g['line']} ({gap:+.1f} {stat})"
    else:
        why = (f"Low confidence: projection {g['lambda_final']:.1f} vs line {g['line']} ({gap:+.1f} {stat}) "
               f"is small next to the normal swing (about ±{g['sd']:.0f}) - 0 units suggested")
    return {"side": side, "state": "GRADE", "grade": grade, "p": p, "units": units, "reason": why,
            "market": g.get("market_quality"),
            "confidence": "high" if p >= 0.68 else "medium" if p >= 0.63 else "small" if p >= 0.57 else "low"}


def public(res):
    """What members see: drop admin-only details."""
    fi = res.pop("faceit_identity", None) or {}
    if not fi.get("show_to_members"):
        res.pop("faceit", None)
    res.pop("grader_inputs", None)
    res.pop("opponent_defense", None)      # shadow feature - admin log only until it is proven
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
                return self.send(200, {"ok": True, "faceit_key": bool(faceit_key()), "preload": PRELOAD_STATUS,
                                       "storage": dict(storage_report(), **STORAGE)})
            if u.path in ("/", "/index.html"):
                return self.send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/search":
                return self.search(q.get("q", "").strip())
            if u.path == "/api/grade":
                return self.grade(q)
            if u.path == "/admin":
                return self.admin(q)
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
            res = cs2_scout.grade(name, line, prop, lt, slug=q.get("slug") or None, faceit_key=faceit_key(),
                                    match=q.get("match") or None)
        if res.get("error"):
            return self.send(404, {"error": res["error"][0].upper() + res["error"][1:]})
        res["verdict"] = verdict(res.get("grade"))
        try:
            picklog.log_grade(res, res["verdict"])     # Phase 10: real lines only, GRADE state only
        except Exception:
            traceback.print_exc()
        return self.send(200, public(res))

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
    if PRELOAD:
        threading.Thread(target=preload_forever, daemon=True, name="preload").start()   # cleans up first
    else:
        threading.Thread(target=cleanup, daemon=True, name="cleanup").start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"cs2_scout running on {HOST}:{PORT} - open http://localhost:{PORT}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
