"""2026-10-13 audit, server API + member site (server.py, whop_auth.py, slip_builder.py, comparison.py): one regression test
per confirmed finding. Every upstream is a stub - no network, no database."""
import email.message
import io
import json
import os
import sys
import threading
import time
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import comparison
import cs2_scout
import hltv
import perf_live
import picklog
import prizepicks
import server
import slip_builder
import underdog
import whop_auth


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class H(server.Handler):
    """server.Handler without a socket: the real send() writes into memory."""

    def __init__(self, path, headers=None, body=b"", ip="10.0.0.1"):
        self.path = path
        m = email.message.Message()
        for k, v in (headers or {}).items():
            m[k] = v
        if body and "Content-Length" not in (headers or {}):
            m["Content-Length"] = str(len(body))
        self.headers, self.rfile, self.wfile = m, io.BytesIO(body), io.BytesIO()
        self.client_address, self.close_connection = (ip, 1234), False
        self.code, self.sent = None, []

    def send_response(self, code, message=None):
        self.code = code

    def send_header(self, k, v):
        self.sent.append((k, v))

    def end_headers(self):
        pass

    def body(self):
        raw = self.wfile.getvalue()
        try:
            return json.loads(raw)
        except ValueError:
            return raw


def get(path, **kw):
    h = H(path, **kw)
    h.do_GET()
    return h


def post(path, **kw):
    h = H(path, **kw)
    h.do_POST()
    return h


@pytest.fixture
def site(monkeypatch):
    """Open local access (no Whop), clean shared caches."""
    for k in ("REQUIRE_WHOP", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(whop_auth, "enabled", lambda: False)
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "_INFLIGHT", {})
    monkeypatch.setattr(server, "_HITS", {})
    monkeypatch.setattr(server, "_SLIP_LOG_HITS", {}, raising=False)
    monkeypatch.setattr(server, "_BS_CACHE", [0.0, []])
    monkeypatch.setattr(server, "BOARD", {})
    return monkeypatch


# ------------------------------------------------------------------ A1 match players: the answer belongs to its match
def test_match_compare_answer_is_never_shared_between_matches(site):
    calls = []

    def fake_mc(team_slug, opp_slug, team, opponent, start, scope="12", book="underdog", match_slug=None):
        calls.append((team, opponent))
        return {"match": {"team": team, "opponent": opponent, "start": start, "scope": scope}, "teams": []}
    site.setattr(server, "match_compare", fake_mc)
    st = "2026-10-14T17:00:00Z"                     # matches found on HLTV / Polymarket / PandaScore: no BO3.gg slugs
    a = get(f"/api/match_compare?team_slug=&opp_slug=&team=Team+A&opponent=Team+B&start={st}&maps=12&book=underdog")
    b = get(f"/api/match_compare?team_slug=&opp_slug=&team=Team+C&opponent=Team+D&start={st}&maps=12&book=underdog")
    assert a.body()["match"]["team"] == "Team A" and b.body()["match"]["team"] == "Team C"
    assert calls == [("Team A", "Team B"), ("Team C", "Team D")]
    get(f"/api/match_compare?team_slug=&opp_slug=&team=Team+C&opponent=Team+D&start={st}&maps=12&book=underdog")
    assert len(calls) == 2                           # the same match is still shared (one answer per match for 120 s)


# ------------------------------------------------------------------ A2 one stake per line on typed cards
START = iso(datetime.now(timezone.utc) + timedelta(hours=5))


def _pp_cut_row(**kw):
    r = {"book": "prizepicks", "nick": "s0und", "player": "s0und", "prop": "kills", "scope": "12", "line": 36.5, "side": "Less",
         "units": 0.75, "units_full": 1.0, "cap_n": 4, "grade": "B+", "p": 0.64, "start": START, "team_name": "T", "team": "T",
         "reason": "x"}
    r.update(kw)
    return r


def _grade_stub(line=36.5, flags=None, projection_source="perf-2", **kw):
    g = {"p_more": 0.36, "p_less": 0.64, "grade": "C", "grade_less": "B+", "line": line, "line_type": "standard",
         "lambda_final": 33.0, "units": 0.0, "units_less": 1.0, "prop": "kills", "scope": "12", "flags": list(flags or []),
         "projection_source": projection_source}
    g.update(kw)
    return {"grade": g, "player_block": {"player": {"nick": "s0und", "slug": "s0und", "id": 7}, "playing_for": "T"},
            "next_match": {"start": START, "opponent": "O"}}


@pytest.fixture
def card(site):
    """/api/grade with cs2_scout.grade stubbed; returns (set_grade, logged)."""
    state = {"res": _grade_stub()}
    logged = []
    site.setattr(cs2_scout, "grade", lambda *a, **k: json.loads(json.dumps(state["res"])))
    site.setattr(underdog, "line_for", lambda *a, **k: None)
    site.setattr(underdog, "board", lambda wait=False: {"at": time.time(), "rows": [], "error": None})
    site.setattr(prizepicks, "board", lambda now=None, wait=False: {"state": "ok", "at": time.time(), "rows": [], "error": None})
    site.setattr(picklog, "log_async", lambda res, v, test=False: logged.append(v))
    site.setattr(server, "mark_warm", lambda *a: None)
    site.setattr(server, "_both_boards", lambda: [_pp_cut_row()])

    def set_grade(res):
        state["res"] = res
    return set_grade, logged


def test_typed_card_gets_the_cap_of_the_board_that_lists_the_pick(card):
    h = get("/api/grade?player=s0und&line=36.5&prop=kills&maps=12")           # typed: no platform
    assert h.code == 200 and h.body()["verdict"]["units"] == 0.75             # PrizePicks board: 0.75U (was 1.0U)
    assert h.body()["verdict"]["units_full"] == 1.0 and h.body()["verdict"]["cap_n"] == 4
    h = get("/api/grade?player=s0und&line=36.5&prop=kills&maps=12&book=underdog")   # an Underdog card: Underdog's own board
    assert h.body()["verdict"]["units"] == 1.0
    # both boards list the exact pick: Underdog decides (the uncut Underdog stake stands), never a mix
    server._BS_CACHE[:] = [0.0, []]
    res = {"verdict": {"side": "Less", "units": 1.0}, "player_block": {"player": {"nick": "s0und"}}, "next_match": {"start": START}}
    server._BS_CACHE[:] = [time.time(), [_pp_cut_row(), _pp_cut_row(book="underdog", units=1.0, units_full=None, cap_n=None)]]
    assert server.board_stake(res, "kills", "12", 36.5, "standard", ("underdog", "prizepicks"))["units"] == 1.0
    server._BS_CACHE[:] = [time.time(), [_pp_cut_row()]]
    assert server.board_stake(res, "kills", "12", 36.5, "standard", ("underdog", "prizepicks"))["units"] == 0.75
    assert server.board_stake(res, "kills", "12", 36.5)["units"] == 1.0       # an explicit Underdog card: unchanged


# ------------------------------------------------------------------ A3 the board is pruned, never wiped
def test_board_drops_only_lines_that_left_the_feed_and_never_wipes(site):
    fut = iso(datetime.now(timezone.utc) + timedelta(hours=3))
    live = [{"id": f"live{i}", "scope": "12", "player": f"p{i}", "team": "A", "opponent": "B", "matchup": "A vs B", "start": fut,
             "prop": "kills", "line": 20.5, "alt": False} for i in range(30)]
    feed = {"rows": list(live)}
    site.setattr(underdog, "board", lambda wait=False: {"at": time.time(), "rows": list(feed["rows"]), "error": None})
    site.setattr(prizepicks, "board", lambda wait=False, now=None: {"state": "ok", "at": time.time(), "rows": [], "error": None})
    site.setattr(server, "_board_load", lambda: None)
    site.setattr(server, "_board_save", lambda: None)
    for name in ("maybe_batch", "settle_all"):
        site.setattr(server.slip_batches, name, lambda *a, **k: None)
    site.setattr(server.slips, "log_recommended", lambda *a, **k: None)
    site.setattr(server.roles, "refresh_hltv", lambda *a, **k: None)
    site.setattr(server.roles, "refresh", lambda *a, **k: None)
    site.setattr(cs2_scout, "grade", lambda *a, **k: {"error": "stub"})
    for i in range(4980):                            # lines graded on earlier days (their matches are over)
        server.BOARD[f"old{i}"] = {"at": time.time(), "side": "More", "grade": "B", "graded_line": 20.5, "graded_model": picklog.MODEL_VERSION}
    server.BOARD["pp:gone"] = {"at": time.time(), "side": "More", "grade": "B", "graded_line": 20.5}
    for r in live:
        server.BOARD[r["id"]] = {"at": time.time(), "side": "More", "grade": "B", "p": 0.6, "units": 0.5, "graded_line": 20.5,
                                 "graded_model": picklog.MODEL_VERSION, "nick": r["player"]}
    feed["rows"].append(dict(live[0], id="new1", player="pnew", line=22.5))   # one new line this pass

    class Stop(BaseException):
        pass
    n = [0]

    def fake_sleep(s):
        n[0] += 1
        if n[0] > 1:
            raise Stop()
    site.setattr(server, "time", types.SimpleNamespace(time=time.time, sleep=fake_sleep))
    with pytest.raises(Stop):
        server.board_forever()
    assert len(server.board_payload()["rows"]) == 30                 # every current line still shown (was 0: BOARD.clear())
    assert not any(k.startswith("old") for k in server.BOARD) and "pp:gone" not in server.BOARD
    assert {f"live{i}" for i in range(30)} <= set(server.BOARD)
    # a platform whose read failed keeps its entries (a failed read never drops kept grades)
    server.BOARD["pp:kept"] = {"side": "More", "grade": "B"}
    server.BOARD["ud-kept"] = {"side": "More", "grade": "B"}
    server.prune_board({"underdog": {"at": 0.0, "rows": [], "error": "ConnectionError: x"},
                        "prizepicks": {"state": "failed", "rows": [], "error": "x"}})
    assert "pp:kept" in server.BOARD and "ud-kept" in server.BOARD
    server.prune_board({"underdog": {"at": time.time(), "rows": [], "error": None}, "prizepicks": {"state": "empty", "rows": []}})
    assert not server.BOARD                                         # both feeds answered: nothing is offered any more


def test_board_load_drops_lines_whose_match_started(site, tmp_path):
    site.setattr(server, "BOARD_FILE", tmp_path / "board.json")
    past, fut = iso(datetime.now(timezone.utc) - timedelta(hours=2)), iso(datetime.now(timezone.utc) + timedelta(hours=2))
    (tmp_path / "board.json").write_text(json.dumps({"version": server._grader_version(), "rows": {
        "a": {"side": "More", "match_start": past, "graded_model": picklog.MODEL_VERSION},
        "b": {"side": "More", "match_start": fut, "graded_model": picklog.MODEL_VERSION},
        "c": {"state": "RETRY"}}}), encoding="utf-8")
    server._board_load()
    assert set(server.BOARD) == {"b", "c"}


# ------------------------------------------------------------------ A4 no raw upstream / driver text to members
def test_unexpected_failure_shows_a_reference_not_the_upstream_error(site):
    raw = "API request to https://api.bo3.gg/api/v1/filters/players failed: Cannot connect to host api.bo3.gg:443 ssl:default [getaddrinfo failed]"

    class B:
        def __init__(self, *a, **k):
            pass

        def search_players(self, q, n):
            raise Exception(raw)

        def close(self):
            pass
    site.setattr(server, "BO3", B)
    site.setattr(hltv, "search_players", lambda q, n=8: None)
    h = get("/api/search?q=auditnobody")
    txt = json.dumps(h.body())
    assert h.code == 503                                             # an unreachable upstream: the page retries it itself
    assert "bo3.gg" not in txt and "443" not in txt and "getaddrinfo" not in txt and "https" not in txt
    assert "(ref " in h.body()["error"] and "try again" in h.body()["error"]
    site.setattr(server, "page", lambda: (_ for _ in ()).throw(KeyError("secret table name")))
    h = get("/")
    assert h.code == 500 and "secret" not in json.dumps(h.body()) and "(ref " in h.body()["error"]


def test_feed_errors_reach_members_in_words_only(site):
    raw_src = ("prizepicks via the HLTV scraper (direct read failed: ReadTimeout: HTTPSConnectionPool(host='partner-api.prizepicks.com', "
               "port=443): Read timed out. (read timeout=25))")
    site.setattr(prizepicks, "board", lambda now=None, wait=False: {
        "state": "failed", "at": time.time() - 900, "checked_at": time.time(), "lines": 3, "ingested_at": time.time(), "source": raw_src,
        "error": "RuntimeError: PrizePicks: direct read failed (ReadTimeout: HTTPSConnectionPool(host='partner-api.prizepicks.com', "
                 "port=443)); scraper copy unavailable (OperationalError: connection to server at \"db.x.com\" (1.2.3.4), port 5432 failed)",
        "rows": []})
    site.setattr(underdog, "board", lambda wait=False: {"at": 0.0, "rows": [], "error": "ConnectionError: HTTPSConnectionPool(host='api.underdogfantasy.com', port=443)"})
    pp, ud = server.board_payload("prizepicks"), server.board_payload("underdog")
    txt = json.dumps([pp, ud])
    for bad in ("host=", "port", "5432", "db.x.com", "HTTPSConnectionPool", "partner-api", "underdogfantasy"):
        assert bad not in txt, bad
    assert pp["feed"]["error"] and ud["error"]                      # still says there is a problem
    assert pp["feed"]["via_scraper"] is True and pp["feed"]["source"] == "PrizePicks via the scraper's copy"
    raw_reason = ("stale: kalshi refresh failing (HTTPError: 429 Client Error: Too Many Requests for url: https://api.elections.kalshi.com/x); "
                  "last good data 40s old; match started - in-play price, not pre-match")
    out = server._member_reason(raw_reason)
    assert "https" not in out and "HTTPError" not in out and "rate-limited by the provider" in out and "match started" in out
    assert server._member_reason("no market") == "no market" and server._member_reason(None) is None


# ------------------------------------------------------------------ A5 the card's data badge is the board's
def test_card_carries_the_boards_data_status(card):
    set_grade, _ = card
    thin = "Projection 30.0 from the performance model (perf-2: 0.700 kills/round x opponent 1.00 x 21.0 rounds/map; data: thin history) - old pipeline 29.0"
    set_grade(_grade_stub(flags=[thin]))
    h = get("/api/grade?player=s0und&line=36.5&prop=kills&maps=12&book=underdog")
    v = h.body()["verdict"]
    g = _grade_stub(flags=[thin])["grade"]
    assert v["dstat"] == comparison.data_status(g, v) == ["limited", "small recent sample"]   # the board's badge
    assert thin not in h.body()["grade"]["flags"]                    # the raw flag stays private (why the page needs dstat)


# ------------------------------------------------------------------ A6 / A7 Match players: promo lines graded, benched excluded
class FakeBO3:
    ROSTERS = {}

    def __init__(self, *a, **k):
        pass

    def team(self, slug):
        return {"players": self.ROSTERS.get(slug, [])}

    def close(self):
        pass


def _mc_setup(site, rows, book="underdog"):
    site.setattr(server, "BO3", FakeBO3)
    site.setattr(perf_live, "projection", lambda *a, **k: {"error": "no HLTV history"})
    if book == "prizepicks":
        site.setattr(prizepicks, "board", lambda now=None, wait=False: {"state": "ok", "at": time.time(), "rows": rows, "error": None, "source": prizepicks.DIRECT_SOURCE})
        site.setattr(underdog, "board", lambda wait=False: {"at": time.time(), "rows": [], "error": None})
    else:
        site.setattr(underdog, "board", lambda wait=False: {"at": time.time(), "rows": rows, "error": None})
        site.setattr(prizepicks, "board", lambda now=None, wait=False: {"state": "ok", "at": time.time(), "rows": [], "error": None})
    site.setattr(underdog, "_BOARD", {"at": time.time(), "tried": time.time(), "rows": rows, "error": None})
    site.setattr(server, "_row_match_id", lambda row: None)


def test_match_players_shows_a_graded_promo_line(site):
    row = {"id": "pp:1", "book": "prizepicks", "market_id": "1", "player": "ZywOo", "team": "Vitality", "opponent": "MOUZ", "start": START,
           "prop": "kills", "scope": "12", "line": 30.5, "alt": False, "odds_type": "promo"}
    _mc_setup(site, [row], "prizepicks")
    FakeBO3.ROSTERS = {"vit": [{"nickname": n, "status": 1} for n in ("ZywOo", "apEX", "flameZ", "mezii", "ropz")],
                       "mouz": [{"nickname": n, "status": 1} for n in ("torzsi", "Spinx", "Jimpphat", "xertioN", "Brollan")]}
    server.BOARD["pp:1"] = {"at": time.time(), "graded_line": 30.5, "graded_model": picklog.MODEL_VERSION, "grade": "B+", "side": "More",
                            "p": 0.645, "units": 1.0, "proj": 32.4, "state": "GRADE", "reason": "r", "nick": "ZywOo",
                            "team_name": "Vitality", "opp_name": "MOUZ", "dstat": ["ok", ""], "line_type": "standard"}
    mc = server.match_compare("vit", "mouz", "Vitality", "MOUZ", START, "12", "prizepicks")
    z = next(e for t in mc["teams"] for e in t["players"] if e["nick"] == "ZywOo")
    assert z["kills"]["side"] == "More" and z["kills"]["grade"] == "B+" and not z["kills"].get("pending")   # was 'grading...'


def test_match_players_never_counts_a_benched_player(site):
    active, benched = ["ZywOo", "apEX", "flameZ", "mezii", "ropz"], "Spinx"
    FakeBO3.ROSTERS = {"vit": [{"nickname": n, "status": 1} for n in active] + [{"nickname": benched, "status": 2}],
                       "mouz": [{"nickname": n, "status": 1} for n in ("a1", "a2", "a3", "a4", "a5")]}
    rows = []
    for i, (n, prop) in enumerate([(n, p) for n in active[:4] + [benched] for p in ("kills", "hs")]):   # the book still lists him
        rid = f"u{i}"
        line = 30.5 if prop == "kills" else 15.5
        rows.append({"id": rid, "scope": "12", "player": n, "team": "Vitality", "opponent": "MOUZ", "matchup": "Vitality vs MOUZ",
                     "start": START, "prop": prop, "line": line, "alt": False})
        server.BOARD[rid] = {"at": time.time(), "graded_line": line, "graded_model": picklog.MODEL_VERSION, "grade": "B", "side": "More",
                             "p": 0.58, "units": 0.5, "proj": 31.0 + i, "state": "GRADE", "reason": "x", "nick": n,
                             "team_name": "Vitality", "opp_name": "MOUZ", "dstat": ["ok", ""]}
    _mc_setup(site, rows)
    t = server.match_compare("vit", "mouz", "Vitality", "MOUZ", START, "12", "underdog")["teams"][0]
    sp = next(e for e in t["players"] if e["nick"] == benched)
    assert sp.get("benched") is True and not sp.get("not_on_roster_page")          # shown, labelled benched
    assert sp["kills"]["line"] == 30.5                                             # his line is still listed
    assert benched not in (t["summary"]["lineup"] or []) and t["summary"]["basis"] != "Underdog lineup"
    assert t["summary"]["kills"] is None or benched not in t["summary"]["lineup"]


# ------------------------------------------------------------------ A8 slip studio change list: Over / Under, Lean
def test_studio_change_list_uses_member_words():
    start = iso(datetime.now(timezone.utc) + timedelta(hours=5))
    leg = {"nick": "ZywOo", "player": "ZywOo", "prop": "kills", "line": 27.5, "side": "More", "scope": "12", "book": "underdog",
           "start": start, "team": "Vitality", "market_type": "standard", "row_id": "x1", "graded_model": "M", "grade": "B+", "p": 0.64}
    row = {"id": "x1", "book": "underdog", "player": "ZywOo", "nick": "ZywOo", "team": "Vitality", "prop": "kills", "scope": "12",
           "line": 27.5, "graded_line": 27.5, "side": "Less", "grade": "LEAN", "p": 0.52, "units": 0, "start": start,
           "graded_model": "M", "alt": False, "higher": 1.0, "lower": 1.0}
    feed = {"at": time.time(), "rows": [dict(row)], "error": None}
    v = slip_builder.verify_before_copy({"legs": [leg], "book": "underdog", "settings": {"book": "underdog"}},
                                        {"underdog": {"feed": feed, "graded": {"rows": [row], "version": "M"}}}, model="M")
    txt = " | ".join(v["changes"])
    assert "ZywOo kills 27.5 Over: model side now Under" in txt and "grade B+ -> Lean" in txt
    assert " More" not in txt and "Less" not in txt and "LEAN" not in txt


# ------------------------------------------------------------------ A9 PrizePicks source flag
def test_prizepicks_feed_says_when_it_came_from_the_scrapers_copy(site):
    for src, via in ((prizepicks.DIRECT_SOURCE, False), ("prizepicks via the HLTV scraper (direct read failed: ReadTimeout: x)", True),
                     (None, False)):
        site.setattr(prizepicks, "board", lambda now=None, wait=False, s=src: {"state": "ok", "at": time.time(), "rows": [], "error": None, "source": s})
        f = server.board_payload("prizepicks")["feed"]
        assert f["via_scraper"] is via, src


# ------------------------------------------------------------------ B1 a failure is handed to the waiters, never cached
def _crowd(n, key, compute):
    """n members asking for the same key at once; compute() is held until all of them have arrived, so none comes after
    the leader finished. -> (answers, errors, runs)."""
    arrived, out, errs, runs = [0], [], [], [0]
    lock = threading.Lock()

    def held():
        runs[0] += 1
        t0 = time.time()
        while arrived[0] < n and time.time() - t0 < 20:
            time.sleep(0.01)
        time.sleep(0.3)                  # the last arrivals reach the wait
        return compute()

    def member():
        with lock:
            arrived[0] += 1
        try:
            out.append(server.shared(key, held))
        except Exception as e:          # noqa: BLE001 - the leader's failure, handed on
            errs.append(e)
    th = [threading.Thread(target=member) for _ in range(n)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    return out, errs, runs[0]


def test_shared_hands_the_leaders_failure_to_its_waiters(monkeypatch):
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "FAIL_KEEP", 0.3, raising=False)
    t0 = time.time()
    out, errs, runs = _crowd(700, ("audit13", "boom"), lambda: (_ for _ in ()).throw(ConnectionError("upstream timeout")))
    assert len(errs) == 700 and all(isinstance(e, ConnectionError) for e in errs) and not out
    assert runs == 1 and time.time() - t0 < 30                       # one run, not 700 one after another
    out, errs, runs = _crowd(50, ("audit13", "503"), lambda: (503, b"{}"))
    assert runs == 1 and out == [(503, b"{}")] * 50 and not errs
    n = []
    for _ in range(50):                                              # members arriving one after another right after it
        assert server.shared(("audit13", "503"), lambda: n.append(1) or (503, b"{}")) == (503, b"{}")
    assert n == []                                                   # handed the same failure: no new upstream request
    time.sleep(0.35)
    server.shared(("audit13", "503"), lambda: n.append(1) or (503, b"{}"))
    assert n == [1] and ("!fail", ("audit13", "503")) in server._RESULTS          # never kept beyond FAIL_KEEP: asked again
    time.sleep(0.35)
    assert server.shared(("audit13", "503"), lambda: n.append(1) or (200, b"ok")) == (200, b"ok") and n == [1, 1]
    assert ("!fail", ("audit13", "503")) not in server._RESULTS                   # a success clears it (and is cached as before)


def test_expired_answers_leave_the_result_cache(monkeypatch):
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "_SWEPT", [0.0])
    server.shared(("audit13", "old"), lambda: (200, b"x", 0.01))
    time.sleep(0.05)
    server._SWEPT[0] = 0.0                                           # the next insert sweeps
    server.shared(("audit13", "new"), lambda: (200, b"y"))
    assert ("audit13", "old") not in server._RESULTS and ("audit13", "new") in server._RESULTS


# ------------------------------------------------------------------ B3 Whop: unclear answers kept a few minutes, cookie renewed
def _whop(monkeypatch, code=503, data=None, delay=0.0):
    calls = []

    class R:
        status_code, text = code, ""

        def json(self):
            return data if data is not None else {"error": "busy"}

    def fake_get(url, **k):
        calls.append(url)
        time.sleep(delay)
        return R()
    monkeypatch.setattr(whop_auth.requests, "get", fake_get)
    monkeypatch.setenv("WHOP_CLIENT_ID", "app_x"); monkeypatch.setenv("WHOP_API_KEY", "k"); monkeypatch.setenv("SESSION_SECRET", "s")
    monkeypatch.setattr(whop_auth, "_ACCESS_CACHE", {})
    monkeypatch.setattr(whop_auth, "_UNCLEAR", {})
    monkeypatch.setattr(whop_auth, "_DOWN", {"until": 0.0}, raising=False)
    return calls


def test_whop_unclear_answer_is_kept_not_asked_per_request(monkeypatch):
    calls = _whop(monkeypatch)
    now = int(time.time())
    cookie = whop_auth._sign({"u": "user_1", "n": "m", "exp": now + 6 * 86400, "chk": now - 2 * 86400, "ok": True})
    first = whop_auth.session(cookie)
    n1 = len(calls)
    assert first["ok"] is True and n1 >= 1 and "renew" not in first          # unclear: last known kept, no new cookie
    out = []
    th = [threading.Thread(target=lambda: out.append(whop_auth.session(cookie)["ok"])) for _ in range(700)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert out == [True] * 700 and len(calls) == n1                         # no Whop call per request (was 3 each)
    # a member Whop clearly said "no" to is never let in by an unclear answer
    whop_auth._ACCESS_CACHE["user_2"] = (time.time() - 120, False)
    c2 = whop_auth._sign({"u": "user_2", "n": "m", "exp": now + 86400, "chk": now - 2 * 86400, "ok": True})
    assert whop_auth.session(c2)["ok"] is False


def test_whop_clear_answer_renews_the_cookie_check(monkeypatch):
    calls = _whop(monkeypatch, code=200, data={"data": [{"user_id": "user_1", "plan_id": "plan_4zOzRl7roBDFf", "status": "active"}]})
    now = int(time.time())
    cookie = whop_auth._sign({"u": "user_1", "n": "m", "exp": now + 6 * 86400, "chk": now - 2 * 86400, "ok": True})
    s = whop_auth.session(cookie)
    assert s["ok"] is True and s["renew"]
    new = whop_auth._unsign(s["renew"][0])
    assert new["chk"] >= now and new["ok"] is True and new["exp"] == now + 6 * 86400 and 0 < s["renew"][1] <= 6 * 86400
    n = len(calls)
    whop_auth._ACCESS_CACHE.clear()                                          # a restart: memory empty, the new cookie remembers
    assert whop_auth.session(s["renew"][0])["ok"] is True and len(calls) == n
    # the server re-issues it on the response
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    h = get("/auth/me", headers={"Cookie": f"{whop_auth.COOKIE}={cookie}"})
    assert any(k == "Set-Cookie" and v.startswith(whop_auth.COOKIE + "=") and "HttpOnly" in v for k, v in h.sent)
    h = get("/auth/me", headers={"Cookie": f"{whop_auth.COOKIE}={s['renew'][0]}"})
    assert not any(k == "Set-Cookie" for k, v in h.sent)                     # nothing to renew


def test_whop_outage_is_probed_one_at_a_time_not_by_every_member(monkeypatch):
    calls = _whop(monkeypatch, delay=0.2)
    now = int(time.time())
    first = whop_auth._sign({"u": "user_0", "n": "m", "exp": now + 86400, "chk": now - 2 * 86400, "ok": True})
    assert whop_auth.session(first)["ok"] is True and len(calls) == 3          # Whop failing: one member's check, 3 calls
    cookies = [whop_auth._sign({"u": f"user_{i}", "n": "m", "exp": now + 86400, "chk": now - 2 * 86400, "ok": i % 2 == 0})
               for i in range(1, 701)]
    out = {}
    th = [threading.Thread(target=lambda c=c, i=i: out.__setitem__(i, whop_auth.session(c)["ok"])) for i, c in enumerate(cookies, 1)]
    t0 = time.time()
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert len(calls) - 3 <= 3 * 10 and time.time() - t0 < 10               # a few probes, not 700 x 3 calls
    assert len(out) == 700 and all(out[i] is (i % 2 == 0) for i in out)     # last known kept; a "no" never turns into a "yes"
    n = len(calls)
    whop_auth.has_access("user_new", force=True)                             # sign-in always asks Whop
    assert len(calls) > n


def test_whop_rechecks_after_a_restart_are_bounded_not_one_per_member(monkeypatch):
    calls = _whop(monkeypatch, code=200, data={"data": []}, delay=0.3)       # Whop healthy but slow; nobody cached (restart)
    monkeypatch.setattr(whop_auth, "_SLOTS", threading.BoundedSemaphore(whop_auth.WHOP_PARALLEL))
    peak, live, lock = [0], [0], threading.Lock()
    real = whop_auth.requests.get

    def counted(url, **k):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        try:
            return real(url, **k)
        finally:
            with lock:
                live[0] -= 1
    monkeypatch.setattr(whop_auth.requests, "get", counted)
    now = int(time.time())
    cookies = [whop_auth._sign({"u": f"user_{i}", "n": "m", "exp": now + 86400, "chk": now - 2 * 86400, "ok": True}) for i in range(700)]
    out, t0 = [], time.time()
    th = [threading.Thread(target=lambda c=c: out.append(whop_auth.session(c)["ok"])) for c in cookies]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert peak[0] <= whop_auth.WHOP_PARALLEL * 2                            # at most 8 checks (2 calls each) at once
    assert len(out) == 700 and time.time() - t0 < 10                         # nobody queued behind 700 checks
    checked = len(whop_auth._ACCESS_CACHE)                                   # Whop's clear "no" (these members cancelled)
    assert 0 < checked < 700 and out.count(False) == checked                 # every checked member gets Whop's answer ...
    assert out.count(True) == 700 - checked                                  # ... the rest keep their access until checked


# ------------------------------------------------------------------ B5 the cached HLTV context is never written into
def test_public_copies_the_hltv_context_before_writing():
    cached = {"found": True, "rank_band": {"src": "hltv", "kpr": 0.7}}
    res = {"hltv_context": cached, "player_block": {"_rank_band": {"src": "bo3", "kpr": 0.6}}, "grade": {}}
    out = server.public(res)
    assert out["hltv_context"]["rank_band"]["src"] == "bo3" and cached["rank_band"]["src"] == "hltv"


# ------------------------------------------------------------------ B6 bodies, rate-limit keys, the slip log
def test_post_body_is_capped_before_reading(site):
    h = post("/admin/closing", headers={"Content-Length": str(10 ** 9)})
    assert h.code == 413 and h.close_connection and h.rfile.tell() == 0
    h = post("/api/slip_log", headers={"Content-Length": str(10 ** 9)})
    assert h.code == 413 and h.rfile.tell() == 0
    site.setattr(whop_auth, "enabled", lambda: True)
    site.setattr(whop_auth, "session", lambda v: None)
    body = json.dumps({"cid": "x", "legs": [{}, {}]}).encode()
    h = post("/api/slip_log", body=body)
    assert h.code == 401 and h.rfile.tell() == 0                             # members only, before the body is read


def test_slip_log_is_rate_limited_per_member(site, monkeypatch):
    got = []
    monkeypatch.setattr(server.slips, "log_loaded", lambda d: got.append(d) or True)
    codes = []
    for i in range(server.SLIP_LOG_PER_MIN + 3):
        body = json.dumps({"cid": f"c{i}", "legs": [{"id": 1}, {"id": 2}]}).encode()
        codes.append(post("/api/slip_log", body=body).code)
    assert codes[:server.SLIP_LOG_PER_MIN] == [200] * server.SLIP_LOG_PER_MIN and codes[-1] == 429
    assert len(got) == server.SLIP_LOG_PER_MIN


def test_rate_limit_key_cannot_be_spoofed(site, monkeypatch):
    a = H("/x", headers={"X-Forwarded-For": "1.1.1.1, 9.9.9.9"})
    b = H("/x", headers={"X-Forwarded-For": "2.2.2.2, 9.9.9.9"})
    a._sess = b._sess = False
    assert a.rate_key() == b.rate_key() == "ip:9.9.9.9"                     # the hop the proxy appended
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: {"user": "user_9", "name": "", "ok": True} if v else None)
    c = H("/x", headers={"X-Forwarded-For": "3.3.3.3", "Cookie": f"{whop_auth.COOKIE}=good"})
    c._sess = False
    assert c.rate_key() == "whop:user_9"


def test_grade_rate_limit_ignores_a_made_up_first_hop(card, monkeypatch):
    monkeypatch.setattr(server, "RATE_LIMIT", 2)
    codes = [get(f"/api/grade?player=s0und&line={30 + i}.5&prop=kills&maps=12",
                 headers={"X-Forwarded-For": f"7.7.7.{i}, 9.9.9.9"}).code for i in range(3)]
    assert codes == [200, 200, 429]


# ------------------------------------------------------------------ D9 an outage grade is never logged as a live forecast
OUTAGE_SRC = "legacy (performance model unavailable: HLTV database unreachable right now (retried once): OperationalError)"


def test_member_card_during_hltv_outage_is_not_logged(card):
    set_grade, logged = card
    set_grade(_grade_stub(projection_source=OUTAGE_SRC))
    h = get("/api/grade?player=s0und&line=36.5&prop=kills&maps=12")
    assert h.code == 200 and logged == [] and h.body()["hltv_outage"]["causes"] == ["hltv"]
    set_grade(_grade_stub(line=35.5))
    get("/api/grade?player=s0und&line=35.5&prop=kills&maps=12")
    assert len(logged) == 1                                                  # a normal grade still is


def test_outage_cards_share_one_board_snapshot(card, monkeypatch):
    set_grade, _ = card
    builds = []
    monkeypatch.setattr(server, "_both_boards", lambda: builds.append(1) or [_pp_cut_row()])
    for i in range(40):                                                      # 40 members, 40 different outage cards
        set_grade(_grade_stub(line=20.5 + i, projection_source=OUTAGE_SRC))
        h = get(f"/api/grade?player=s0und&line={20.5 + i}&prop=kills&maps=12")
        assert h.code == 200 and h.body()["hltv_outage"]["rows"] == [] and h.body()["verdict"]["unavailable"]   # another line's
    assert len(builds) == 1                                                  # one board build, not two per card   grade: never


# 2026-10-14 owner: an outage card reuses a saved board grade only for exactly this pick, shown with its time and stale;
# otherwise no grade - and its temporary numbers are never shown as a prediction
def test_outage_card_reuses_only_the_exact_saved_grade_and_withholds_its_own_numbers(card, monkeypatch):
    set_grade, logged = card
    kept = _pp_cut_row(graded_line=36.5, line_type="standard", graded_at="2026-10-14T16:40:00+00:00",
                       hltv_retry="2026-10-14T17:00:00+00:00", retry_cause="hltv")
    monkeypatch.setattr(server, "_both_boards", lambda: [kept])
    set_grade(dict(_grade_stub(projection_source=OUTAGE_SRC), ladder=[{"line": 35.5, "side": "More", "p": 0.5}],
                   simulation={"anchored": True}))
    b = get("/api/grade?player=s0und&line=36.5&prop=kills&maps=12&book=prizepicks").body()
    v, g = b["verdict"], b["grade"]
    assert (v["side"], v["grade"], v["p"], v["units"]) == ("Less", "B+", 0.64, 0.75) and v["kept_from_board"] and v["stale"]
    assert v["graded_at"] == "2026-10-14T16:40:00+00:00" and b["hltv_outage"]["rows"][0]["stale"]
    assert g["withheld"] and "p_more" not in g and "lambda_final" not in g and b["ladder"] == [] and b["simulation"] is None
    assert logged == []                                                      # never recorded as a fresh forecast
    b = get("/api/grade?player=s0und&line=36.5&prop=kills&maps=12&book=underdog").body()    # another platform
    assert b["verdict"]["unavailable"] and b["verdict"]["side"] is None and b["verdict"]["p"] is None
    assert b["verdict"]["units"] == 0.0 and "p_more" not in b["grade"] and logged == []


def test_board_answer_is_shared_by_every_member(site, monkeypatch):
    builds = []
    monkeypatch.setattr(server, "board_payload", lambda book="underdog": builds.append(book) or {"book": book, "rows": []})
    for _ in range(50):
        assert get("/api/board").body()["book"] == "underdog"
    assert get("/api/board?book=prizepicks").body()["book"] == "prizepicks"
    assert builds == ["underdog", "prizepicks"]                              # one build per platform per BOARD_TTL s


# ------------------------------------------------------------------ C11 a missing HLTV database on the member deployment
NOCONF_SRC = "legacy (performance model unavailable: HLTV database not configured)"
NOCONF_FLAG = "OLD PROJECTION: the performance model is unavailable here (HLTV database not configured) - graded on the old pipeline"


def test_missing_hltv_database_is_an_outage_on_the_member_deployment(monkeypatch):
    monkeypatch.delenv("REQUIRE_WHOP", raising=False)
    assert not server.perf_transient({"projection_source": NOCONF_SRC})      # locally: today's behaviour
    assert "not configured on this server" in server._public_flag(NOCONF_FLAG)
    monkeypatch.setenv("REQUIRE_WHOP", "1")
    assert server.perf_transient({"projection_source": NOCONF_SRC}) and server.grade_transient({"projection_source": NOCONF_SRC})
    t = server._public_flag(NOCONF_FLAG)
    assert t.startswith("Grading resumes") and "no HLTV data for this player" not in t
    assert server.has_warning({"flags": [NOCONF_FLAG]})
    old = {"grade": "B", "side": "More", "graded_line": 29.5, "p": 0.6}
    assert server.keep_last_hltv_grade(old, {"projection_source": NOCONF_SRC}, {"line": 29.5})["hltv_retry"]


# ------------------------------------------------------------------ E1 a Thunderpick odds blip is handled like an HLTV blip
ODDS_T = {"projection_source": "perf-2", "odds_transient": True, "odds_transient_reason": "quote 240 s old (limit 180 s)"}


def test_odds_blip_keeps_the_last_grade_and_says_why():
    old = {"grade": "B+", "side": "More", "graded_line": 29.5, "p": 0.64, "units": 1.0}
    k = server.keep_last_hltv_grade(old, ODDS_T, {"line": 29.5}, now=2000.0)
    assert k["side"] == "More" and k["hltv_retry"] and k["hltv_retries"] == 1 and k["retry_cause"] == "odds"
    assert server.keep_last_hltv_grade(old, ODDS_T, {"line": 30.5}) is None             # same line only
    ph = server.outage_placeholder(None, {"line": 29.5}, now=2000.0, g=ODDS_T)
    assert "side" not in ph and ph["retry_cause"] == "odds" and ph["reason"].startswith("Thunderpick lists this match but has no current quote (quote 240 s old")
    assert "HLTV" not in ph["reason"] and ph["at"] == 2000.0 - server.BOARD_EVERY + server.HLTV_RETRY
    assert server.outage_placeholder(None, {"line": 29.5}, now=2000.0)["reason"].startswith("HLTV database unreachable")
    assert not server.odds_transient({"projection_source": "perf-2"}) and server.grade_transient(ODDS_T)


def test_board_never_stores_or_logs_an_odds_blip_grade(site):
    fut = iso(datetime.now(timezone.utc) + timedelta(hours=3))
    rows = [{"id": "L1", "scope": "12", "player": "p1", "team": "A", "opponent": "B", "matchup": "A vs B", "start": fut, "prop": "kills",
             "line": 29.5, "alt": False}, {"id": "L2", "scope": "12", "player": "p2", "team": "A", "opponent": "B", "matchup": "A vs B",
                                           "start": fut, "prop": "kills", "line": 25.5, "alt": False}]
    site.setattr(underdog, "board", lambda wait=False: {"at": time.time(), "rows": [dict(r) for r in rows], "error": None})
    site.setattr(prizepicks, "board", lambda wait=False, now=None: {"state": "ok", "at": time.time(), "rows": [], "error": None})
    for name, val in (("_board_load", lambda: None), ("_board_save", lambda: None)):
        site.setattr(server, name, val)
    for name in ("maybe_batch", "settle_all"):
        site.setattr(server.slip_batches, name, lambda *a, **k: None)
    site.setattr(server.slips, "log_recommended", lambda *a, **k: None)
    site.setattr(server.roles, "refresh_hltv", lambda *a, **k: None)
    site.setattr(server.roles, "refresh", lambda *a, **k: None)
    logged, snaps = [], []
    site.setattr(picklog, "log_async", lambda res, v, test=False: logged.append(v))
    site.setattr(comparison, "snapshot", lambda *a, **k: snaps.append(1))
    res = _grade_stub(line=29.5, **ODDS_T)
    site.setattr(cs2_scout, "grade", lambda *a, **k: json.loads(json.dumps(res)))
    server.BOARD["L1"] = {"at": 0, "grade": "B+", "side": "More", "graded_line": 29.5, "p": 0.64, "units": 1.0, "nick": "p1"}

    class Stop(BaseException):
        pass
    n = [0]

    def fake_sleep(s):
        n[0] += 1
        if n[0] > 1:
            raise Stop()
    site.setattr(server, "time", types.SimpleNamespace(time=time.time, sleep=fake_sleep))
    with pytest.raises(Stop):
        server.board_forever()
    assert server.BOARD["L1"]["side"] == "More" and server.BOARD["L1"]["retry_cause"] == "odds"        # its last grade kept
    assert "side" not in server.BOARD["L2"] and server.BOARD["L2"]["retry_cause"] == "odds"            # nothing to keep: hidden
    assert logged == [] and snaps == []
    rows_ = server.board_payload()["rows"]
    assert [r["id"] for r in rows_] == ["L1"] and rows_[0]["retry_cause"] == "odds"


def test_member_card_during_odds_blip_is_honest_and_not_logged(card):
    set_grade, logged = card
    set_grade(_grade_stub(**ODDS_T))
    h = get("/api/grade?player=s0und&line=36.5&prop=kills&maps=12")
    assert h.code == 200 and logged == [] and h.body()["hltv_outage"]["causes"] == ["odds"]
    assert h.body()["grade"]["odds_transient"] is True
    assert server._RESULTS and all(v[3] - time.time() <= 5.5 for v in server._RESULTS.values())   # reused 5 s, not 60
    note = h.body()["hltv_outage"]["odds_note"]
    assert note.startswith("Thunderpick lists this match but has no current quote (quote 240 s old (limit 180 s))")
    assert "this card has no betting-market odds" in note                    # this stub's grade had no odds at all


def test_odds_blip_note_names_the_odds_the_card_really_uses():
    g = dict(ODDS_T, odds_transient_reason="reader error: ConnectionError: HTTPSConnectionPool(host='api.thunderpick.io', port=443)")
    for odds, want in (({"found": True, "provenance": {"selected": "polymarket"}}, "come from Polymarket (the fallback source), not from Thunderpick"),
                       ({"found": True, "book": "pinnacle", "provenance": {"selected": "oddspapi"}}, "come from pinnacle via OddsPapi (the fallback source)"),
                       ({"found": True, "provenance": {"selected": "thunderpick"}}, "the Thunderpick price on this card is not a current quote"),
                       (None, "this card has no betting-market odds")):
        t = server.odds_blip_note({"grade": g, "odds": odds})
        assert t.startswith("Thunderpick lists this match but has no current quote") and want in t, t
        assert "host=" not in t and "443" not in t and "HTTPSConnectionPool" not in t
        assert "Thunderpick odds" not in t                                   # never called (current) Thunderpick odds


# ------------------------------------------------------------------ minor: /admin by header alone
def test_admin_page_opens_with_the_header_token(site, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_TOKEN", "tok-123456")
    monkeypatch.setattr(picklog, "report", lambda: {"rows": [], "total": 0, "open": 0, "settled": 0, "void": 0, "hit_rate_all": None,
                                                    "clv_avg": None, "clv_n": 0, "clv_positive": None, "units_net_flat": 0,
                                                    "by_printed_pct": [], "by_units": []})
    h = get("/admin", headers={"X-Admin-Token": "tok-123456"})
    assert h.code == 200 and b"Pick log" in h.wfile.getvalue()
    assert get("/admin").code == 403
