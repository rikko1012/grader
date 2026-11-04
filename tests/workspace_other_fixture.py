"""Fixtures for the research workspace's LoL, NBA and NFL paths. No network and no production data.

Sports: a stub of the sports service on 127.0.0.1. Its answers use the field names sports/snapshots.py writes to board.json,
board_view.json, history/<player>.json and players/<player>.json (read from real builder output), and server.py's real hook and token
path reach it in the default service mode. The CS2 tests cannot import sports, so the stub is standard library only.

LoL: the worker's board.json, a lol.db made from the real schema text in lol/center/store.py, and learning.json. Rows in
fixtures/workspace_lol_board.json are trimmed from a real admin board of 2026-10-17, moved to the future at load."""
import ast
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def soon(hours=3):
    return datetime.now(timezone.utc) + timedelta(hours=hours)


# ---------------------------------------------------------------------------------------------------------------- sports

def quote(platform, oid, line, fid=None, over=1, under=1, mult=None, rev=1, decimal=None, **kw):
    q = {"r": f"offer:{platform}:{oid}:r{rev}:{hashlib.sha1(f'{oid}{rev}'.encode()).hexdigest()[:10]}", "p": platform, "l": line, "v": "standard", "o": [over, under],
         "t": iso(datetime.now(timezone.utc) - timedelta(minutes=2)), "su": None, "g": 1, "f": fid}
    if mult:
        q["m"] = mult
    if decimal:
        q["d"] = decimal
    q.update(kw)
    return q


def market(lg, event, pid, name, pos, team, tid, stat, quotes, mk=None, **kw):
    return dict({"k": f"{event}|{pid}|{stat}|full_game", "e": event, "pid": pid, "n": name, "pos": pos, "tm": team, "tid": tid, "st": stat, "pr": 1, "cap": "supported", "q": quotes, "kn": [], "kr": 0,
                 "alt": 0, "promo": 0, "mk": mk, "sc": sum(1 for q in quotes if q.get("f")), "hid": None}, **kw)


def forecast(po, pu, mu, model="sm-1", pp=0.0, line=None, iv=None):
    return {"m": model, "mu": mu, "po": po, "pu": pu, "pp": pp, "at": iso(datetime.now(timezone.utc) - timedelta(hours=2)), "ro": 1, "rv": None, "ln": line, "k": "scout", **({"iv": iv, "md": mu} if iv else {})}


def event(home, away, start, lg="nba"):
    return {"start_utc": iso(start), "status": "scheduled", "home": {"team_id": f"{lg}:team:espn:{home}", "abbr": home, "name": home + " Home"}, "away": {"team_id": f"{lg}:team:espn:{away}", "abbr": away, "name": away + " Away"},
            "started": False, "locked": False, "status_label": "Scheduled"}


def nba_board(start=None):
    s, late = start or soon(3), (start or soon(3)) + timedelta(hours=2)
    lg = "nba"
    towns = market(lg, "nba:espn:1", "nba:player:espn:301", "Karl Anders", "C", "NYK", "nba:team:espn:NYK", "points", [
        quote("prizepicks", "pp:1", 20.5, "fc-aaaaaaaa00000001"),
        quote("underdog", "ud:1", 20.5, "fc-aaaaaaaa00000002", mult=[1.0, 1.0]),
        quote("fanduel", "fd:1", 20.5, None, decimal=[1.91, 1.91]),
    ], mk={"l": 20.5, "hp": "prizepicks", "c": {"po": 0.52, "pu": 0.48, "pp": 0.0, "n": 2, "used": ["fanduel", "kalshi"]},
           "bk": [{"p": "fanduel", "l": 20.5, "kind": "devig", "c": 0.5, "over": 1.91, "under": 1.91, "t": iso(s), "su": None, "age": 60, "in": True, "why": None},
                  {"p": "kalshi", "l": 21.5, "kind": "mid", "c": 0.44, "over": [0.42, 0.46], "under": None, "t": iso(s), "su": None, "age": 60, "in": True, "why": None}], "at": {}})
    other_line = market(lg, "nba:espn:1", "nba:player:espn:302", "Jay Brown", "F", "BOS", "nba:team:espn:BOS", "points", [
        quote("prizepicks", "pp:2", 24.5, "fc-aaaaaaaa00000003"),
        quote("underdog", "ud:2", 25.5, "fc-aaaaaaaa00000004", mult=[1.0, 1.0]),
    ])
    held = market(lg, "nba:espn:2", "nba:player:espn:303", "Alex Hold", "G", "MIA", "nba:team:espn:MIA", "rebounds", [
        quote("underdog", "ud:3", 8.5, "fc-aaaaaaaa00000005", mult=[1.03, None], over=1, under=0, x={"b": "ratio_low", "t": "Held: projection is under 25% of the line", "v": "sg-1"}),
    ])
    lean = market(lg, "nba:espn:2", "nba:player:espn:304", "Reed Lean", "G", "CHI", "nba:team:espn:CHI", "assists", [
        quote("prizepicks", "pp:4", 6.5, "fc-aaaaaaaa00000006", x={"v": "cf-2", "ln": "over", "pl": 0.58, "gr": "B", "e": 3.1, "g": 3.1, "c": 3, "cw": [], "s": 3.0}),
        quote("underdog", "ud:4", 6.5, "fc-aaaaaaaa00000007", mult=[1.0, 1.0], x={"v": "cf-2", "ln": "over", "pl": 0.58, "gr": "B", "e": 3.1, "g": 3.1, "c": 3, "cw": [], "s": 3.0}),
    ])
    doc = {"version": "ui-snap-1", "league": lg, "built_at": iso(datetime.now(timezone.utc)), "as_of": iso(datetime.now(timezone.utc)), "part": "primary", "secondary_markets": 0,
           "stats": {"points": "Points", "rebounds": "Rebounds", "assists": "Assists"}, "models": [],
           "events": {"nba:espn:1": event("BOS", "NYK", s), "nba:espn:2": event("MIA", "CHI", late)},
           "injuries": {"nba:player:espn:301": {"status": "questionable", "detail": "Knee"}},
           "markets": [towns, other_line, held, lean],
           "fc": {"fc-aaaaaaaa00000001": forecast(0.4412, 0.5588, 19.6, line=20.5, iv=[13.2, 26.8]), "fc-aaaaaaaa00000002": forecast(0.4412, 0.5588, 19.6, line=20.5, iv=[13.2, 26.8]),
                  "fc-aaaaaaaa00000003": forecast(0.541, 0.459, 25.4, line=24.5), "fc-aaaaaaaa00000004": forecast(0.502, 0.498, 25.4, line=25.5),
                  "fc-aaaaaaaa00000005": forecast(0.61, 0.39, 1.2, line=8.5), "fc-aaaaaaaa00000006": forecast(0.58, 0.42, 7.2, line=6.5), "fc-aaaaaaaa00000007": forecast(0.58, 0.42, 7.2, line=6.5)}}
    view = {"version": "ui-snap-1", "league": lg, "part": "view", "rule": "bv-1", "rows": {
        towns["k"]: {"lines": {"20.5": [4, 6, 0, 10, 2, 3, 0, 5]}, "played": 14, "seasons10": [2027], "from10": iso(s), "to10": iso(s), "sd10": 4.2},
        other_line["k"]: {"lines": {"24.5": [6, 4, 0, 10, 3, 2, 0, 5], "25.5": [5, 4, 1, 10, 3, 2, 0, 5]}, "played": 12, "seasons10": [2027], "from10": iso(s), "to10": iso(s), "sd10": 5.0},
        lean["k"]: {"lines": {"6.5": [7, 3, 0, 10, 4, 1, 0, 5]}, "played": 10, "seasons10": [2027], "from10": iso(s), "to10": iso(s), "sd10": 2.0}}}
    return doc, view


def big_nba_board(n):
    """A board the size of a full slate, for the load harness: n markets over 12 games, an offer on each platform."""
    s = soon(3)
    lg = "nba"
    events, markets, fc, view = {}, [], {}, {}
    for i in range(n):
        g = i % 12
        eid = f"nba:espn:b{g}"
        home, away = f"H{g:02d}", f"A{g:02d}"
        events[eid] = event(home, away, s + timedelta(minutes=30 * g))
        team = home if i % 2 else away
        pid = f"nba:player:espn:b{i}"
        fu, fp = f"fc-cccccccc{2 * i:08x}", f"fc-cccccccc{2 * i + 1:08x}"
        po = round(0.44 + (i % 13) * 0.01, 4)
        fc[fu] = forecast(po, round(1 - po, 4), 20.0 + (i % 7), line=20.5, iv=[12.0, 28.0])
        fc[fp] = dict(fc[fu])
        m = market(lg, eid, pid, f"Player {i:03d}", "G", team, f"nba:team:espn:{team}", "points", [quote("underdog", f"ud:b{i}", 20.5, fu, mult=[1.0, 1.0]), quote("prizepicks", f"pp:b{i}", 20.5, fp)])
        markets.append(m)
        view[m["k"]] = {"lines": {"20.5": [5, 5, 0, 10, 3, 2, 0, 5]}, "played": 12, "seasons10": [2027], "from10": iso(s), "to10": iso(s), "sd10": 4.0}
    doc = {"version": "ui-snap-1", "league": lg, "built_at": iso(datetime.now(timezone.utc)), "as_of": iso(datetime.now(timezone.utc)), "part": "primary", "secondary_markets": 0, "stats": {"points": "Points"}, "models": [],
           "events": events, "injuries": {}, "markets": markets, "fc": fc}
    return doc, {"version": "ui-snap-1", "league": lg, "part": "view", "rule": "bv-1", "rows": view}


def nfl_board(start=None):
    s = start or soon(26)
    lg = "nfl"
    m = market(lg, "nfl:espn:1", "nfl:player:espn:401", "Sam Field", "QB", "KC", "nfl:team:espn:KC", "pass_yds", [
        quote("underdog", "ud:n1", 264.5, "fc-bbbbbbbb00000001", mult=[1.0, 1.0]), quote("prizepicks", "pp:n1", 264.5, "fc-bbbbbbbb00000002")], nv="NFL model not validated - projection only")
    r = market(lg, "nfl:espn:1", "nfl:player:espn:402", "Dee Route", "WR", "BUF", "nfl:team:espn:BUF", "receptions", [
        quote("underdog", "ud:n2", 5.0, "fc-bbbbbbbb00000003", mult=[1.0, 1.0])], nv="NFL model not validated - projection only")
    doc = {"version": "ui-snap-1", "league": lg, "built_at": iso(datetime.now(timezone.utc)), "as_of": iso(datetime.now(timezone.utc)), "part": "primary", "secondary_markets": 0,
           "stats": {"pass_yds": "Passing yards", "receptions": "Receptions"}, "models": [], "events": {"nfl:espn:1": event("KC", "BUF", s, lg)}, "injuries": {}, "markets": [m, r],
           "fc": {"fc-bbbbbbbb00000001": forecast(0.5614, 0.4386, 274.9, line=264.5), "fc-bbbbbbbb00000002": forecast(0.5614, 0.4386, 274.9, line=264.5), "fc-bbbbbbbb00000003": forecast(0.4781, 0.3922, 5.4, pp=0.1297, line=5.0)}}
    view = {"version": "ui-snap-1", "league": lg, "part": "view", "rule": "bv-1", "rows": {m["k"]: {"lines": {"264.5": [5, 3, 0, 8, 3, 2, 0, 5]}, "played": 8, "seasons10": [2027], "from10": iso(s), "to10": iso(s), "sd10": 40.0}}}
    return doc, view


def games(stat, values, opp="BOS"):
    t = datetime.now(timezone.utc) - timedelta(days=2)
    return {"version": "ui-snap-1", "league": "nba", "player_id": "x", "family": "basketball",
            "games": [{"event_id": f"e{i}", "start_utc": iso(t - timedelta(days=2 * i)), "season": 2027, "team": {"abbr": "NY"}, "opponent": {"team_id": "t", "abbr": opp, "name": opp}, "home": bool(i % 2), "participation": "played", "parts": {}, "values": {stat: float(v)}, "source": ["espn"]}
                      for i, v in enumerate(values)]}


def player(position="C", injury=None):
    return {"version": "ui-snap-1", "player_id": "x", "player": "Karl Anders", "position": position, "status": "active", "team": {"abbr": "NY"}, "injury": injury, "next_event": {"event_id": "e", "home": False, "opponent": {"abbr": "BOS"}},
            "markets": [], "factors": {"stats": [{"key": "pts", "label": "Points", "windows": [{"window": "L5", "n": 5, "mean": 21.4}, {"window": "L10", "n": 10, "mean": 20.1}]}],
                                       "context": [{"key": "rest_days", "label": "Days since last game", "value": 2.0}, {"key": "back_to_back", "label": "Back-to-back", "value": False}, {"key": "venue", "label": "Venue", "value": "away"},
                                                   {"key": "teammates_out", "label": "Teammates on the injury report", "value": 1, "items": [{"player": "Mitch Out", "status": "out"}]}],
                                       "engine": {"rows": [{"group": "Defensive matchup", "label": "Points allowed per game to C", "value": 24.1, "n": 12}]}, "not_held": []}}


def public_results():
    def league(n):
        return {"forecasts": n, "settled": n, "void": 3, "unresolved": 1, "props": n, "games": 40, "projection": {"n": n}, "leans": {"n": n, "wins": 60, "losses": 52, "pushes": 4, "hit_rate": 0.5357, "hit_rate_ci95": [0.49, 0.58]},
                "gate": {"settled_props": n, "required": 300, "validated": False, "under_review": False, "text": f"{n} settled (minimum 300)", "rule": "x"},
                "stats": {"points": {"games": 30, "leans": {"n": 80, "wins": 44, "losses": 34, "pushes": 2, "hit_rate": 0.5641, "hit_rate_ci95": None}}},
                "bands": [{"band": "50-55%", "label": "probability band", "n": 70, "wins": 36, "losses": 32, "pushes": 2, "stated": 0.527, "hit_rate": 0.5294, "hit_rate_ci95": None},
                          {"band": "55-60%", "label": "probability band", "n": 46, "wins": 24, "losses": 20, "pushes": 2, "stated": 0.571, "hit_rate": 0.5455, "hit_rate_ci95": None}],
                "calibration": [], "grades": {"rows": []}, "recent": [], "model_gate": {}, "head_to_head": {"version": "hh-1", "rows": [{"scout": "sm-1", "market": "mc-1", "stat": "all", "pairs": 90, "pushes": 2, "games": 31, "scout_brier": 0.2471, "market_brier": 0.2462}]}}
    return {"version": "rp-1", "built_at": iso(datetime.now(timezone.utc)), "as_of": iso(datetime.now(timezone.utc)), "leagues": {"nba": league(116), "nfl": league(116)}, "picks": {}}


class Sports:
    """The sports service, as far as the workspace reads it. Set `down` to fail every read, `answers` to refuse a pick, `settled` to settle."""

    def __init__(self):
        self.boards = {"nba": nba_board(), "nfl": nfl_board()}
        self.history = {"nba:player:espn:301": games("points", [24, 18, 25, 19, 22, 17, 26, 15, 21, 16, 20, 23]), "nba:player:espn:304": games("assists", [7, 8, 5, 7, 9, 6, 7, 8, 7, 4])}
        self.players = {"nba:player:espn:301": player("C", {"status": "questionable", "detail": "Knee"})}
        self.public = public_results()
        self.down = False
        self.answers = {}                       # record id -> {ok: False, code, text}
        self.settled = {}                       # (offer id, side) -> leg result
        self.calls = {}
        self.etag_hits = 0
        self.tokens = set()
        self.lock = threading.Lock()
        owner = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def end_headers(self):
                # This isolated local fixture is called directly by browser tests.
                # Native CORS avoids an asynchronous route.fetch/fulfill proxy at teardown.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                super().end_headers()

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def reply(self, code, doc, extra=None):
                body = json.dumps(doc).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                with owner.lock:
                    owner.calls[u.path] = owner.calls.get(u.path, 0) + 1
                    owner.tokens.add((self.headers.get("Authorization") or "")[:11])
                if owner.down:
                    return self.reply(503, {"error": "sports_unavailable"})
                if not (self.headers.get("Authorization") or "").startswith("Bearer st1."):
                    return self.reply(401, {"error": "members_only"})
                doc = owner.route(u.path, q)
                if doc is None:
                    return self.reply(404, {"error": "player_not_on_board"})
                tag = '"' + hashlib.blake2b(json.dumps(doc, sort_keys=True).encode(), digest_size=8).hexdigest() + '"'
                if self.headers.get("If-None-Match") == tag:
                    owner.etag_hits += 1
                    self.send_response(304)
                    self.send_header("Content-Length", "0")
                    self.send_header("ETag", tag)
                    self.end_headers()
                    return None
                return self.reply(200, doc, {"ETag": tag})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def route(self, path, q):
        lg = q.get("league")
        if path == "/api/sports/board":
            doc, view = self.boards[lg]
            return view if q.get("part") == "view" else ({"version": "ui-snap-1", "part": "secondary", "markets": [], "fc": {}} if q.get("part") == "more" else doc)
        if path == "/api/sports/history":
            return self.history.get(q.get("id"))
        if path == "/api/sports/player":
            return self.players.get(q.get("id"))
        if path == "/api/sports/results/public":
            return self.public
        if path == "/api/sports/picks/check":
            out = []
            for it in json.loads(q["picks"]):
                a = self.answers.get(it["record_id"])
                out.append(a or {"ok": True, "record_id": it["record_id"], "side": it["side"]})
            return {"version": "pk-1", "results": out}
        if path == "/api/sports/slips/settle":
            slip = json.loads(q["slip"])
            return {"status": "pending", "leg_results": [dict(self.settled.get((l["offer_id"], l["side"]), {"status": None, "value": None}), offer_id=l["offer_id"], side=l["side"]) for l in slip["legs"]]}
        return None

    def env(self):
        return {"SPORTS_API": "1", "SPORTS_API_MODE": "service", "SPORTS_TOKEN_SECRET": "w" * 40, "SPORTS_API_BASE": self.base}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------------------------------------------------------- LoL

def lol_schema():
    """The worker's own CREATE statements, read as text: a CS2 test cannot import lol."""
    tree = ast.parse((ROOT / "lol" / "center" / "store.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and "c_player_games" in node.value.value:
            return node.value.value
    raise AssertionError("no schema found in lol/center/store.py")


class Lol:
    """board.json, lol.db and learning.json in one LOL_CENTER folder."""

    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rows = json.loads((ROOT / "tests" / "fixtures" / "workspace_lol_board.json").read_text(encoding="utf-8"))["rows"]
        for r in self.rows[5:]:                 # the real rows are one match: the last two become a second one
            r["link"]["sid"] = "lol:series:000092"
            r.update(team="Vivo Keyd", opponent="Team Secret", competition="Worlds 2026 Play-In")
        self.start = soon(5)
        self.con = sqlite3.connect(self.dir / "lol.db")
        self.con.executescript(lol_schema())
        for k in ("cal_version", "cal_p_over", "cal_p_under", "cal_p_push", "grade", "grade_label", "units_policy", "units", "stake_restriction"):
            try:
                self.con.execute(f"ALTER TABLE lol_forecasts ADD COLUMN {k}")
            except sqlite3.OperationalError:
                pass
        self.write()
        self.seed_forecasts()
        self.seed_history()
        self.learning()

    def write(self, **extra):
        now = datetime.now(timezone.utc)
        rows = []
        for r in self.rows:
            r = json.loads(json.dumps(r))
            r["series_start"] = iso(self.start)
            r["quote_seen_at"] = iso(now - timedelta(hours=2))
            r["quote_checked_at"] = extra.get("checked") or iso(now - timedelta(minutes=1))
            if r.get("forecast"):
                r["forecast"]["at"] = iso(now - timedelta(hours=1))
            rows.append(r)
        at = iso(now)
        p = self.dir / "board.json"
        p.write_text(json.dumps({"at": at, "released_to_members": True, "feeds": {"underdog": extra.get("feed", at), "prizepicks": extra.get("feed", at)}, "rows": rows, "match_tables": [], "settled_recent": []}), encoding="utf-8")
        self.mtime_bump(p)

    def mtime_bump(self, p):
        import os
        st = p.stat()
        self._t = getattr(self, "_t", st.st_mtime) + 5
        os.utime(p, (self._t, self._t))

    def seed_forecasts(self):
        for r in self.rows:
            f = r.get("forecast")
            if not f:
                continue
            self.con.execute("INSERT INTO lol_forecasts(id, forecast_at, platform, market_key, stat, scope, market_type, line, sid, pid, series_start, model_version, p_over, p_under, p_push, model_side) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (f["id"], iso(datetime.now(timezone.utc) - timedelta(hours=1)), r["platform"], r["market_key"], r["stat"], "123", r["market_type"], r["line"], r["link"]["sid"], r["link"]["pid"], iso(self.start), "lol-1f", f["p_over"], f["p_under"], f["p_push"], f["model_side"]))
        self.con.commit()

    def seed_history(self):
        """Twelve finished best-of-threes for the first player: game 1 and game 2 kills 5 and 6, so Maps 1-2 is 11 every time, and one forfeited series the rule leaves out."""
        pid = self.rows[0]["link"]["pid"]
        self.con.execute("INSERT INTO entities(eid, kind, label, created_at) VALUES('lol:team:9', 'team', 'Old Rivals', '2026-01-01')")
        t = datetime.now(timezone.utc) - timedelta(days=3)
        for i in range(12):
            sid = f"lol:series:h{i}"
            self.con.execute("INSERT INTO c_series(sid, best_of, status, ff, scheduled_utc) VALUES(?,3,'complete',?,?)", (sid, 1 if i == 0 else 0, iso(t)))
            for n, kills in ((1, 5), (2, 6)):
                gid = f"g{i}-{n}"
                self.con.execute("INSERT INTO c_games(gid, sid, game_n, remake, ff) VALUES(?,?,?,0,0)", (gid, sid, n))
                self.con.execute("INSERT INTO c_player_games(gid, pid, sid, game_n, start_utc, kills, deaths, assists, cs, opp_tid) VALUES(?,?,?,?,?,?,1,4,300,'lol:team:9')", (gid, pid, sid, n, iso(t - timedelta(days=i)), kills))
        self.con.commit()

    def settle(self, row, status="graded", actual=None, over_hit=None, version=1):
        unit = f"{row['platform']}|{row['market_key']}|{row['line']}"
        self.con.execute("INSERT INTO lol_settlements(unit_key, version, settled_at, platform, status, actual, over_hit, push, games, fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (unit, version, iso(datetime.now(timezone.utc)), row["platform"], status, actual, over_hit, 1 if status == "push" else 0, "[1,2]", f"f{version}"))
        self.con.commit()

    def learning(self):
        m = {"n": 120, "series": 40, "mae": 3.0, "bias": 0.1, "brier": 0.24, "logloss": 0.67, "side_hit_rate": 0.558, "side_wins": 67, "mean_p_side": 0.601, "mean_displayed_p_side": 0.571, "calibration": []}
        doc = {"at": iso(datetime.now(timezone.utc)), "settled_by_status": {"graded": 130, "push": 6, "void": 2, "dnp": 1}, "counts": {},
               "metrics": {"all": dict(m, n=130, side_wins=72), "side offered": m, "stat kills": dict(m, n=80, side_wins=46), "stat assists": dict(m, n=40, side_wins=21), "grade B": dict(m, n=50, side_wins=29), "role Mid": dict(m, n=30, side_wins=18)},
               "series_grouped_95ci": {"offered_side_only": {"side_hit": {"mean": 0.558, "lo95": 0.49, "hi95": 0.62}}}}
        doc["offered_side"] = {"version": 1, "settled_by_status": {"graded": 120, "push": 6, "void": 2, "dnp": 1},
                               "metrics": {"all": m, **{k: v for k, v in doc["metrics"].items() if k.startswith(("stat ", "grade ", "role "))}}}
        doc["metrics"]["grade B"] = dict(m, n=999, side_wins=900)
        doc["settled_by_status"]["push"] = 99
        (self.dir / "learning.json").write_text(json.dumps(doc), encoding="utf-8")

    def close(self):
        self.con.close()


def lol_leg(record, side=None):
    side = side or record["scout"]["side"]
    return leg(record, side)


def leg(record, side=None):
    """The leg the page sends for one record of any sport."""
    side = side or record["scout"]["side"] or "over"
    p = record["scout"]["p_over"] if side == "over" else record["scout"]["p_under"]
    return {"id": "|".join(str(x) for x in (record["sport"], record["event"]["id"], record["player"]["key"], record["market"]["stat"], record["market"]["period"], record["offer"]["platform"], side, record["offer"]["line"])),
            "sport": record["sport"], "book": record["offer"]["platform"], "row_id": record["offer"]["offer_id"], "side": side, "line": record["offer"]["line"], "rev": record["offer"]["revision"], "read_rev": record["scout"]["read_rev"],
            "shown_p": p, "team": record["player"]["team"], "player": record["player"]["name"], "event": record["event"]["id"]}
