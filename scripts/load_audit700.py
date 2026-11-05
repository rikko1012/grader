"""Audit load test (2026-10-26): 700 simultaneous members against EVERY hot member endpoint of the esports server, in
process, fixture upstreams only (no network, never production). Extends scripts/load_sports700.py ("off" mode) with the
card (/api/grade), match compare, results, line check, veto and search routes, and records response bytes and
compression, so per-request heavy work and uncompressed bodies show up.

usage: python scripts/load_audit700.py <code_dir> [members=700] [workspace] [history=0] [allsports]
With UPGRADE_LOAD=1 in the environment, "workspace" runs the same visit through the upgraded pages' own gate and prefix (SCOUT_UPGRADE, /api/upgrade/) and adds the page, its script and stylesheet, and the hit-rate and stated reads.
"workspace" runs the research workspace API instead (board, line, recheck, track, mine, state, results) as one signed-in member
per thread, on fixture rows backed by a seeded pick log, and fails loudly on a 5xx, a grade call, more than one board build
per 5 s, or a lost receipt. history = settled receipts already on each member's account before the run starts: a first visit has
none, a member after a month has a couple of hundred, and that is the case that fills read slots and caches. "allsports" adds LoL,
NBA and NFL to every member's visit: the LoL worker's files, and a stub sports service on a local port with a 240-market NBA board,
reached through server.py's real hook and token path; each member also checks and tracks one NBA and one LoL entry and reads all
four Results tabs.
Stubs (load harness only, never in the server): cs2_scout.grade sleeps 80 ms and returns a ~60 KB card (a real card
with ladder / recent maps / comparison is that size); match_compare sleeps 50 ms and returns ~25 KB; results_payload
returns ~40 KB. Every member sends Accept-Encoding: gzip like a browser.
Prints one line: RESULT {json}."""
import gzip
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

code = sys.argv[1]
MEMBERS = int(sys.argv[2]) if len(sys.argv) > 2 else 700
WORKSPACE = len(sys.argv) > 3 and sys.argv[3] == "workspace"
HISTORY = int(sys.argv[4]) if len(sys.argv) > 4 else 0
ALLSPORTS = len(sys.argv) > 5 and sys.argv[5] == "allsports"
UPGRADE = os.environ.get("UPGRADE_LOAD") == "1"
sys.path.insert(0, code)
os.chdir(code)
tmp = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)))
os.environ["CS2_DATA_DIR"] = tmp
for f in ("SPORTS_UI", "SPORTS_API", "SPORTS_INGEST", "SPORTS_TRAIN"):
    os.environ.pop(f, None)
if WORKSPACE:
    os.environ["SCOUT_UPGRADE" if UPGRADE else "SCOUT_WORKSPACE_PREVIEW"] = "1"

import hltv_store  # noqa: E402
hltv_store.DB_PATH = Path(tmp) / "hltv_store.db"
import cs2_scout, kalshi, moneyline, picklog, polymarket_ml, prizepicks, server, slip_builder, underdog, whop_auth  # noqa: E402,E401
from tests.test_api_moneyline_slips import Boards  # noqa: E402
from tests.test_moneyline import kalshi_raw, poly_raw  # noqa: E402

whop_auth.enabled = lambda: True
whop_auth.session = lambda v: {"user": v[5:], "name": "", "ok": True} if v and v.startswith("good-") else None
slip_builder.DB_PATH = Path(tmp) / "slip_studio.db"
b = Boards(n_matches=30)
server.board_payload = b.payload
underdog.board = b.feed
prizepicks.board = lambda now=None, wait=False: {"state": "ok", "at": time.time(), "rows": [], "error": None}
# Opt-in fixtures exercise the real easier-line adapter and board cache.
LINE_ADVANTAGE_LOAD = os.environ.get("LINE_ADVANTAGE_LOAD") == "1"
ADVICE_RESPONSES = []
if LINE_ADVANTAGE_LOAD:
    feed_at = time.time() - 1  # fixed cached observation, not a new read on every request
    underdog.board = lambda **kw: dict(b.feed(), at=feed_at)
    pp_rows = [dict(r, id="pp:" + r["id"], book="prizepicks", line=29.5, graded_line=29.5,
                    odds_type="standard", over_available=True, under_available=True) for r in b.rows]
    server.board_payload = lambda book="underdog": dict(book=book, updated=time.time(), error=None,
        rows=b.rows if book == "underdog" else pp_rows, version="TESTMODEL")
    prizepicks.board = lambda **kw: dict(state="ok", at=feed_at, ingested_at=feed_at, rows=pp_rows, error=None)
underdog.line_for = lambda *a, **k: None
picklog.log_async = lambda *a, **k: None
server.mark_warm = lambda *a: None
srcs = {"polymarket": moneyline.Source("polymarket", poly_raw, polymarket_ml.normalize, canonical_fn=moneyline._fallback_canonical),
        "kalshi": moneyline.Source("kalshi", kalshi_raw, kalshi.normalize, canonical_fn=moneyline._fallback_canonical)}
for s in srcs.values():
    s.refresh_once()
moneyline.SOURCES = srcs

GRADE_CALLS = [0]
START = b.rows[0]["start"]


def fake_grade(name, line, prop="kills", lt="standard", **k):
    GRADE_CALLS[0] += 1
    time.sleep(0.08)
    maps = [{"date": f"2026-10-{1 + i % 25:02d}", "map": "Mirage", "kills": 18 + i % 9, "opp": f"Team {i}", "rating": 1.05,
             "adr": 80.1 + i % 7, "hs": 9, "event": "Fixture Cup " * 3} for i in range(120)]
    g = {"p_more": 0.41, "p_less": 0.59, "grade": "C", "grade_less": "B", "line": line, "line_type": lt, "lambda_final": line - 1,
         "units": 0.0, "units_less": 1.0, "prop": prop, "scope": "12", "flags": [], "projection_source": "perf-2",
         "factors": [{"name": f"factor {i}", "weight": 0.0, "value": i / 10, "note": "shown at weight 0 " * 4} for i in range(40)]}
    return {"grade": g, "player_block": {"player": {"nick": name, "slug": name, "id": 7}, "playing_for": "T", "maps": maps},
            "next_match": {"start": START, "opponent": "O"},
            "ladder": [{"line": line + d, "p": 0.5} for d in (-2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2)]}


cs2_scout.grade = fake_grade
MC_CALLS = [0]


def fake_mc(*a, **k):
    MC_CALLS[0] += 1
    time.sleep(0.05)
    return {"match": {"team": a[2], "opponent": a[3]},
            "players": [{"nick": f"p{i}", "rows": [{"k": j, "v": "x" * 20} for j in range(30)]} for i in range(10)]}


server.match_compare = fake_mc
RES_CALLS = [0]


def fake_results():
    RES_CALLS[0] += 1
    time.sleep(0.05)
    return {"groups": [{"name": f"g{i}", "rows": [{"date": "2026-10-20", "w": 1, "l": 0, "note": "x" * 30} for _ in range(40)]}
                       for i in range(10)]}


if not WORKSPACE:
    server.results_payload = fake_results
import hltv  # noqa: E402
hltv.search_players = lambda q, n=8: [{"slug": "s0und", "nick": "s0und", "name": "A B", "country": "DK", "image": None}]
server.BO3 = None                                       # never BO3.gg from a load test (no network)

ThreadingHTTPServer.request_queue_size = 2048
ThreadingHTTPServer.daemon_threads = True
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_address[1]
REFUSED = [0]


def call(path, member):
    t0 = time.time()
    for attempt in range(20):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        try:
            c.request("GET", path, headers={"Cookie": f"{whop_auth.COOKIE}=good-{member}", "Accept-Encoding": "gzip"})
            break
        except OSError:
            REFUSED[0] += 1
            c.close()
            time.sleep(0.25 * (attempt + 1))
    r = c.getresponse()
    body = r.read()
    enc = r.getheader("Content-Encoding") or ""
    if LINE_ADVANTAGE_LOAD and path.startswith("/api/line_check"):
        payload = json.loads(gzip.decompress(body) if enc == "gzip" else body)
        ADVICE_RESPONSES.append(payload.get("status") == "verified" and
            any(x.get("line") == 29.5 and x.get("book") == "prizepicks" for x in payload.get("line_advantages", [])))
    c.close()
    return r.status, time.time() - t0, len(body), enc


m0 = b.rows[0]
CARDS = [f"/api/grade?player=p{i}&line={28.5 + i % 5}&prop=kills&maps=12&book=underdog" for i in range(40)]
COMPARES = [f"/api/match_compare?team_slug=&opp_slug=&team=T{m}a&opponent=T{m}b&start={START}&maps=12&book=underdog"
            for m in range(15)]
LINE_QUERY = "&rid=r1&side=More&book=underdog&opponent=T0b" if LINE_ADVANTAGE_LOAD else ""
if os.environ.get("LINE_ADVANTAGE_CARD") == "1":
    LINE_QUERY = LINE_QUERY.replace("&rid=r1", "")
OTHER = ["/api/results", f"/api/line_check?player=p1&prop=kills&maps=12&line=30.5&team=T0a&start={quote(START, safe='')}{LINE_QUERY}",
         "/api/veto?match=hltv:900", "/auth/me", "/api/search?q=s0", "/api/board?book=prizepicks"]


def member(i):
    paths = ["/", "/api/board", CARDS[i % len(CARDS)], COMPARES[i % len(COMPARES)], "/api/moneylines", OTHER[i % len(OTHER)]]
    out = []
    for p in paths:
        st, dt, n, enc = call(p, f"u{i}")
        out.append((p.split("?")[0], st, dt, n, enc))
    return out


def rss():
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("a", wintypes.DWORD)] + [(n, ctypes.c_size_t) for n in
                        ("PeakWS", "WS", "q1", "q2", "q3", "q4", "pf", "ppf")]
        k32, ps = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        m = PMC()
        m.cb = ctypes.sizeof(PMC)
        ps.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(m), m.cb)
        return round(m.WS / 1e6, 1), round(m.PeakWS / 1e6, 1)
    except Exception:
        return None


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))] * 1000, 1)


def seed_history(workspace_api, n):
    """n settled receipts per member in four-leg slips, written straight to the tables the way a month of use leaves them."""
    from datetime import datetime, timedelta, timezone
    store = workspace_api.store()
    payload = json.dumps({"player": "seeded", "team": "Halcyon Esports", "opponent": "Norrland Five", "matchup": "Halcyon Esports vs Norrland Five", "competition": "Meridian Cup Season 4",
                          "stat_label": "Kills", "period_label": "Maps 1-2", "platform_label": "Underdog", "scout_side": "over", "on_side": True, "displayed_p": 0.612, "grade": "B", "units": 0.5,
                          "stake_reason": None, "projection": 40.0, "forecast_p": 0.612, "forecast_at": "2026-10-01T10:00:00+00:00", "forecast_grade": "B", "forecast_units": 0.5,
                          "l10": {"over": 6, "under": 4, "push": 0, "n": 10}, "feed_at": "2026-10-01T10:00:00+00:00", "rules_version": "ws-rules", "entry_type": "standard"}, separators=(",", ":"))
    t0 = datetime.now(timezone.utc) - timedelta(days=30)
    rows, setts, slips = [], [], []
    for i in range(MEMBERS):
        who = workspace_api.member_key(f"whop:u{i}")
        for j in range(n):
            rid, sid, ts = f"rc-h{i}-{j}", f"sl-h{i}-{j // 4}", (t0 + timedelta(minutes=j // 4 * 30)).isoformat(timespec="seconds")
            rows.append((rid, who, sid, j % 4, ts, "cs2", "underdog", "r", "d", "over", 30.5, "standard", -1 - i, 1, f"h-{i}-{j // 4}", None, f"h-{i}-{j // 4}", f"n{j}", "kills", "12", ts, "SEED", 0, payload))
            setts.append((rid, 1, "hit" if j % 2 else "miss", 33.0, "[1,2]", "hltv", ts, ts, None, None))
            if j % 4 == 0:
                slips.append((sid, who, ts, "underdog", "standard", '["cs2"]', 4, "v", "{}", 0))

    def op(con):
        con.executemany("INSERT INTO receipts(receipt_id, member, slip_id, leg_no, tracked_at, sport, platform, row_id, offer_rev, side, line, line_type, pick_id, forecast_id, match_slug, "
                        "match_id, match_key, nick, prop, scope, start_utc, model_version, scratch, payload) VALUES(" + ",".join("?" * 24) + ")", rows)
        con.executemany("INSERT INTO settlements(receipt_id, revision, result, actual, actual_maps, source, picklog_settled_at, recorded_at, supersedes, reason) VALUES(?,?,?,?,?,?,?,?,?,?)", setts)
        con.executemany("INSERT INTO slips(slip_id, member, tracked_at, platform, entry_type, sports, legs, rules_version, checks, scratch) VALUES(?,?,?,?,?,?,?,?,?,?)", slips)
    store.submit(op, wait=300)
    return len(rows)


def workspace_run():
    """Every member loads the board, researches a line, checks and tracks a two-pick entry, reads the receipts and saves the slip."""
    from datetime import datetime, timedelta, timezone
    from tests.workspace_fixture import Fixture, iso, leg_of, row
    import workspace_api
    api = "/api/upgrade/" if UPGRADE else "/preview/workspace/api/"
    fx = Fixture(tmp)
    start = iso(datetime.now(timezone.utc) + timedelta(hours=4))
    fx.rows = [row(f"11111111-0000-4000-8000-{m * 2 + k:012d}", "underdog", f"p{m}x{k}", f"T{m}a", f"T{m}b", 30.5, "More", round(0.58 + 0.01 * (m % 9), 3), 900 + m, start)
               for m in range(30) for k in range(2)]
    fx.install(setattr)
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_GB", {})):
        setattr(server, name, val)
    def slow_series(*a, **k):                                   # one HLTV read, for the shared L5 and L20 pass
        time.sleep(0.01 if UPGRADE else 0)
        return []
    server.hltv.recent_series = slow_series
    if UPGRADE:
        whop_auth.cookie_valid = lambda v: bool(v and v.startswith("good-"))
    other = None
    if ALLSPORTS:
        from tests.workspace_other_fixture import Lol, Sports, big_nba_board, leg as any_leg
        sports_stub = Sports()
        sports_stub.boards["nba"] = big_nba_board(240)
        lol_fx = Lol(Path(tmp) / "lol_center")
        os.environ.update(sports_stub.env())
        server.LOL_CENTER = lol_fx.dir
        server.lol_public = lambda: True
        other = {"sports": sports_stub, "lol": lol_fx}
    workspace_api.reset()
    seeded = seed_history(workspace_api, HISTORY) if HISTORY else 0
    GEN_BUILDS = [0]                                            # Slip Studio sq-1: one search per option set per GEN_TTL, shared
    real_generate = workspace_api._generate

    def counted_generate(srv, o):
        GEN_BUILDS[0] += 1
        return real_generate(srv, o)
    workspace_api._generate = counted_generate

    tls = threading.local()

    def req(method, name, member, body=None):
        """One request. allsports keeps one connection per member, as a browser does: 23 requests a member on a new connection each
        would run a Windows machine out of client ports before the server is under any load."""
        t0 = time.time()
        data = json.dumps(body).encode() if body is not None else None
        head = {"Cookie": f"{whop_auth.COOKIE}=good-{member}", "Accept-Encoding": "gzip", "Content-Type": "application/json"}
        for attempt in range(20):
            c = getattr(tls, "c", None) if ALLSPORTS else None
            c = c or http.client.HTTPConnection("127.0.0.1", port, timeout=120)
            try:
                c.request(method, name if name.startswith("/") else api + name, body=data, headers=head)
                r = c.getresponse()
                raw = r.read()
                break
            except (OSError, http.client.HTTPException):
                REFUSED[0] += 1
                c.close()
                tls.c = None
                time.sleep(0.25 * (attempt + 1))
        enc = r.getheader("Content-Encoding") or ""
        if ALLSPORTS:
            tls.c = c
        else:
            c.close()
        return r.status, time.time() - t0, len(raw), enc, raw

    st, _, _, _, raw = req("GET", "board", "warm")
    assert st == 200, st
    records = {r["player"]["name"]: r for r in json.loads(gzip.decompress(raw) if raw[:1] == bytes([0x1f]) else raw)["records"]}
    names = sorted(records)

    def other_board(sport):
        st, _, _, _, raw = req("GET", f"board?sport={sport}", "warm")
        assert st == 200, (sport, st)
        return json.loads(gzip.decompress(raw) if raw[:1] == bytes([0x1f]) else raw)["records"]

    if ALLSPORTS:
        nba = [r for r in other_board("nba") if r["offer"]["platform"] == "underdog"]
        lol = [r for r in other_board("lol") if r["offer"]["platform"] == "underdog"]
        other_board("nfl")
    start_boards, t_start = fx.boards, time.time()

    def member(i):
        who, out = f"u{i}", []
        a, b = records[names[i % 30 * 2]], records[names[(i % 29 + 1 + i % 30) % 30 * 2]]
        if a["event"]["id"] == b["event"]["id"]:
            b = records[names[(i % 30 + 1) % 30 * 2]]
        legs = [leg_of(a), leg_of(b)]
        steps = [("GET", "me", None), ("GET", "board", None), ("GET", f"line?book=underdog&id={a['offer']['offer_id']}", None), ("GET", "state", None),
                 ("POST", "recheck", {"idem": f"check-{i:06d}", "legs": legs, "entry_types": {"underdog": "standard"}}),
                 ("POST", "track", {"idem": f"track-{i:06d}", "entry_key": "underdog", "entry_type": "standard", "legs": legs}),
                 ("GET", "mine", None), ("POST", "state", {"rev": 0, "slip": {"legs": legs, "types": {}}}), ("GET", "state", None), ("GET", "results", None)]
        gen_sports = "cs2,lol,nba,nfl" if ALLSPORTS else "cs2"
        steps += [("GET", f"generate?platform=underdog&type=standard&slips={8 if i % 2 else 4}&picks={2 if i % 2 else 3}&sports={gen_sports}&repeats={'none' if i % 3 else 'allow'}", None),
                  ("POST", "slip-advice", {"legs": [dict(l, stat=r["market"]["stat"], period=r["market"]["period"], player_key=r["player"]["key"], player=r["player"]["name"],
                                                         team=r["player"]["team"], event=r["event"]["id"]) for l, r in zip(legs, (a, b))],
                                          "entry_types": {"underdog": "standard"}})]
        if ALLSPORTS:
            n1, n2 = nba[i % 120], nba[(i % 120 + 121) % len(nba)]
            l1, l2 = next(r for r in lol if r["player"]["name"] == "Harky"), next(r for r in lol if r["player"]["name"] == "Kratos")
            nl, ll = [any_leg(n1, "over"), any_leg(n2, "under")], [any_leg(l1), any_leg(l2)]
            steps += [("GET", "board?sport=lol", None), ("GET", "board?sport=nba", None), ("GET", "board?sport=nfl", None),
                      ("GET", f"line?sport=nba&book=underdog&id={n1['offer']['offer_id']}", None), ("GET", f"line?sport=lol&book=underdog&id={l1['offer']['offer_id']}", None),
                      ("POST", "recheck", {"idem": f"chkn-{i:06d}", "legs": nl, "entry_types": {"underdog": "standard"}}),
                      ("POST", "track", {"idem": f"trkn-{i:06d}", "entry_key": "underdog", "entry_type": "standard", "legs": nl}),
                      ("POST", "recheck", {"idem": f"chkl-{i:06d}", "legs": ll, "entry_types": {"underdog": "standard"}}),
                      ("POST", "track", {"idem": f"trkl-{i:06d}", "entry_key": "underdog", "entry_type": "standard", "legs": ll}),
                      ("GET", "results?sport=lol", None), ("GET", "results?sport=nba", None), ("GET", "results?sport=nfl", None), ("GET", "mine", None)]
        if UPGRADE:
            steps = [("GET", "/", None), ("GET", "/upgrade/upgrade.js", None), ("GET", "/upgrade/upgrade.css", None)] + steps + [("GET", "hits", None), ("GET", "stated", None)]
        for method, name, body in steps:
            st, dt, n, enc, _ = req(method, name, who, body)
            out.append((method + " " + name.split("&")[0] if "sport=" in name else method + " " + name.split("?")[0], st, dt, n, enc))
        return out

    r0 = rss()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MEMBERS) as ex:
        res = [x for r in ex.map(member, range(MEMBERS)) for x in r]
    wall = time.time() - t0
    r1 = rss()
    builds = (fx.boards - start_boards) / 2                     # one build reads both platforms
    store = workspace_api.store()
    diag = store.diagnostics()
    t_settle = time.time()
    store.settle_once()
    settle_s = time.time() - t_settle
    lat = [x[2] for x in res]
    rep = {"mode": "workspace", "members": MEMBERS, "requests": len(res), "wall_s": round(wall, 2), "connect_retries": REFUSED[0], "rss_before_after_peak_mb": [r0, r1],
           "errors_5xx": sum(1 for x in res if x[1] >= 500), "non_200": sum(1 for x in res if x[1] != 200), "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95), "p99_ms": pct(lat, .99),
           "board_builds": builds, "allowed_builds": int((time.time() - t_start) / 5) + 2, "grade_calls": fx.grades, "receipts": diag["counts"]["receipts"],
           "expected_receipts": (6 if ALLSPORTS else 2) * MEMBERS + seeded, "history_per_member": HISTORY, "settle_pass_s": round(settle_s, 3), "slips": diag["counts"]["slips"],
           "states": diag["counts"]["member_state"], "generate_builds": GEN_BUILDS[0], "allowed_generate_builds": 6 * (int((time.time() - t_start) / workspace_api.GEN_TTL) + 2),
           "routes": {}}
    if other:
        rep["sports_service_calls"] = other["sports"].calls
    for p in sorted({x[0] for x in res}):
        rows = [x for x in res if x[0] == p]
        codes = {}
        for x in rows:
            codes[x[1]] = codes.get(x[1], 0) + 1
        rep["routes"][p] = {"n": len(rows), "codes": codes, "p50_ms": pct([x[2] for x in rows], .5), "p99_ms": pct([x[2] for x in rows], .99),
                            "avg_kb": round(sum(x[3] for x in rows) / len(rows) / 1e3, 1), "gzip_share": round(sum(1 for x in rows if x[4] == "gzip") / len(rows), 2)}
    rep["pass"] = (rep["non_200"] == 0 and rep["grade_calls"] == 0 and rep["board_builds"] <= rep["allowed_builds"] and rep["receipts"] == rep["expected_receipts"]
                   and rep["generate_builds"] <= rep["allowed_generate_builds"])
    print("RESULT " + json.dumps(rep))
    workspace_api.reset()
    if other:
        other["sports"].close()
        other["lol"].close()
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if rep["pass"] else 1)


if WORKSPACE:
    workspace_run()
for _ in range(10):
    call("/api/moneylines", "warm")
r0 = rss()
t0 = time.time()
with ThreadPoolExecutor(max_workers=MEMBERS) as ex:
    res = [x for r in ex.map(member, range(MEMBERS)) for x in r]
wall = time.time() - t0
r1 = rss()


lat = [x[2] for x in res]
rep = {"members": MEMBERS, "requests": len(res), "wall_s": round(wall, 2), "connect_retries": REFUSED[0],
       "rss_before_after_peak_mb": [r0, r1], "errors_5xx": sum(1 for x in res if x[1] >= 500),
       "non_200": sum(1 for x in res if x[1] != 200), "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95), "p99_ms": pct(lat, .99),
       "bytes_out_mb": round(sum(x[3] for x in res) / 1e6, 2),
       "computes": {"grade": GRADE_CALLS[0], "match_compare": MC_CALLS[0], "results": RES_CALLS[0]}, "routes": {}}
for p in sorted({x[0] for x in res}):
    rows = [x for x in res if x[0] == p]
    codes = {}
    for x in rows:
        codes[x[1]] = codes.get(x[1], 0) + 1
    rep["routes"][p] = {"n": len(rows), "codes": codes, "p50_ms": pct([x[2] for x in rows], .5),
                        "p99_ms": pct([x[2] for x in rows], .99), "avg_kb": round(sum(x[3] for x in rows) / len(rows) / 1e3, 1),
                        "gzip_share": round(sum(1 for x in rows if x[4] == "gzip") / len(rows), 2)}
rep["line_advantage"] = {"enabled": LINE_ADVANTAGE_LOAD, "responses": len(ADVICE_RESPONSES),
                         "correct": sum(ADVICE_RESPONSES)}
print("RESULT " + json.dumps(rep))
if rep["non_200"] or (LINE_ADVANTAGE_LOAD and (not ADVICE_RESPONSES or not all(ADVICE_RESPONSES))):
    raise SystemExit(1)
