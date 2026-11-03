"""The workspace API on LoL, NBA and NFL over real HTTP with server.Handler. LoL reads a worker folder made from a real board and the
real schema; NBA and NFL reach a stub sports service through server.py's real hook and token path (tests/workspace_other_fixture.py).
No network and no production data."""
import ast
import http.client
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import server
import whop_auth
import workspace_api
import workspace_lol
import workspace_rules as rules
import workspace_sports
import workspace_store
from tests.workspace_fixture import Fixture, leg_of
from tests.workspace_other_fixture import Lol, Sports, leg

API = "/preview/workspace/api/"
ROOT = Path(server.__file__).parent


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in ("SPORTS_UI_ALLOW", "SPORTS_UI", "REQUIRE_WHOP", "RENDER", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SCOUT_WORKSPACE_PREVIEW", "1")
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: {"user": v[5:], "name": "Tester", "ok": True} if v and v.startswith("good-") else None)
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_ENC_LOCKS", {}), ("_RES_BYTES", [0, 0.0]), ("_MAP1_STATUS", [0.0, None]), ("BOARD", {})):
        monkeypatch.setattr(server, name, val)
    fx = Fixture(tmp_path)
    fx.install(monkeypatch.setattr)
    fx.sports = Sports()
    for k, v in fx.sports.env().items():
        monkeypatch.setenv(k, v)
    fx.lol = Lol(tmp_path / "lol_center")
    monkeypatch.setattr(server, "LOL_CENTER", fx.lol.dir)
    monkeypatch.setattr(server, "lol_public", lambda: True)
    monkeypatch.setattr(server, "_LOL_MEMBER", {"mtime": None, "body": None, "lock": threading.Lock()})
    workspace_api.reset()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    fx.port = httpd.server_address[1]
    yield fx
    httpd.shutdown()
    httpd.server_close()
    workspace_api.reset()
    fx.sports.close()
    fx.lol.close()


def call(fx, method, path, body=None, user="u1"):
    c = http.client.HTTPConnection("127.0.0.1", fx.port, timeout=30)
    h = {"Cookie": f"{whop_auth.COOKIE}=good-{user}"} if user else {}
    if body is not None:
        h["Content-Type"] = "application/json"
    c.request(method, API + path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw) if raw else None
    except ValueError:
        return r.status, raw


def get(fx, path, user="u1"):
    return call(fx, "GET", path, user=user)


def fresh():
    server._RESULTS.clear()


def recs(fx, sport):
    st, doc = get(fx, f"board?sport={sport}")
    assert st == 200, doc
    return {(r["player"]["name"], r["offer"]["platform"]): r for r in doc["records"]}


def cs2(fx):
    st, doc = get(fx, "board")
    assert st == 200
    return {(r["player"]["name"], r["offer"]["platform"]): r for r in doc["records"]}


N = 0


def idem():
    global N
    N += 1
    return f"key-{N:06d}-aaaa"


def check(fx, legs, types=None):
    return call(fx, "POST", "recheck", {"idem": idem(), "legs": legs, "entry_types": types or {}})


def track(fx, legs, key, et=None, user="u1"):
    return call(fx, "POST", "track", {"idem": idem(), "entry_key": key, "entry_type": et, "legs": legs}, user=user)


def problems(doc, l):
    return [p["code"] for p in doc["legs"][l["id"]]["problems"]]


def mine(fx, user="u1"):
    return get(fx, "mine", user=user)[1]


# ---- who sees what
def test_me_lists_the_sports_this_member_sees(env):
    st, doc = get(env, "me")
    assert st == 200 and doc["sports"] == {"cs2": True, "lol": True, "nba": True, "nfl": True}


def test_lol_follows_its_release_switch_and_sports_its_flag(env, monkeypatch):
    monkeypatch.setattr(server, "lol_public", lambda: False)
    monkeypatch.delenv("SPORTS_API")
    doc = get(env, "me")[1]
    assert doc["sports"] == {"cs2": True, "lol": False, "nba": False, "nfl": False}
    for path in ("board?sport=lol", "board?sport=nba", "results?sport=nfl", "line?sport=nba&book=underdog&id=x"):
        assert get(env, path)[0] == 404, path


def test_owner_only_sports_stay_owner_only(env, monkeypatch):
    monkeypatch.setenv("SPORTS_UI", "owner")
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id("the-owner"))
    assert get(env, "me", user="someone")[1]["sports"]["nba"] is False and get(env, "board?sport=nba", user="someone")[0] == 404
    assert get(env, "me", user="the-owner")[1]["sports"]["nba"] is True and get(env, "board?sport=nba", user="the-owner")[0] == 200


def test_a_line_of_a_sport_the_member_does_not_see_cannot_be_checked_or_tracked(env, monkeypatch):
    l = leg(recs(env, "nba")[("Karl Anders", "underdog")], "under")
    monkeypatch.delenv("SPORTS_API")
    doc = check(env, [l])[1]
    assert problems(doc, l) == ["gone"] and not doc["entries"]["underdog"]["ok"]
    assert track(env, [l, dict(l, id="x2")], "underdog")[0] == 409


def test_flags_off_never_imports_sports(tmp_path):
    code = textwrap.dedent(f"""
        import email.message, io, json, sys
        sys.path.insert(0, {str(ROOT)!r})
        import server
        class H(server.Handler):
            def __init__(self, path):
                self.path = path
                self.headers, self.rfile, self.wfile = email.message.Message(), io.BytesIO(), io.BytesIO()
                self.client_address, self.close_connection = ("10.0.0.1", 1), False
                self.code = None
            def send_response(self, code, message=None):
                self.code = code
            def send_header(self, k, v):
                pass
            def end_headers(self):
                pass
        h = H("/preview/workspace/api/me")
        h.do_GET()
        print("RESULT" + json.dumps({{"code": h.code, "body": h.wfile.getvalue().decode(), "mods": sorted(m for m in sys.modules if m == "sports" or m.startswith("sports.") or m == "lol" or m.startswith("lol."))}}))
    """)
    env_ = {k: v for k, v in os.environ.items() if not k.startswith("SPORTS_")}
    env_.update(CS2_DATA_DIR=str(tmp_path), SCOUT_WORKSPACE_PREVIEW="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), env=env_, capture_output=True, text=True, timeout=240, stdin=subprocess.DEVNULL)
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT")), None)
    assert line, (r.stdout[-1000:], r.stderr[-1000:])
    out = json.loads(line[6:])
    assert out["code"] == 200 and out["mods"] == [] and json.loads(out["body"])["sports"]["nba"] is False


# ---- NBA and NFL board
def test_nba_records_are_the_boards_own_rows(env):
    r = recs(env, "nba")
    a = r[("Karl Anders", "underdog")]
    assert a["sport"] == "nba" and a["event"]["label"] == "NYK at BOS" and a["event"]["id"] == "nba:espn:1" and a["player"]["role"] == "C" and a["player"]["opponent"] == "BOS"
    assert a["market"]["stat_label"] == "Points" and a["market"]["period"] == "full_game" and a["offer"]["line"] == 20.5
    s = a["scout"]
    assert s["p_over"] == 0.4412 and s["p_under"] == 0.5588 and s["projection"] == 19.6 and s["range80"] == [13.2, 26.8]
    assert a["history"]["l10"] == {"over": 4, "under": 6, "push": 0, "n": 10} and a["history"]["l5"] == {"over": 2, "under": 3, "push": 0, "n": 5} and a["history"]["l20"] is None
    assert a["market_chance"] == {"p_over": 0.52, "p_under": 0.48, "sources": 2}


def test_held_grades_never_become_letters_or_stakes(env):
    for lg in ("nba", "nfl"):
        st, doc = get(env, f"board?sport={lg}")
        for r in doc["records"]:
            if r["player"]["name"] == "Reed Lean":
                assert r["scout"]["grade"] == "B" and r["scout"]["units"] == 0
                assert r["scout"]["hold"] is None and r["scout"]["validation"]["kind"] == "research"
                continue
            assert r["scout"]["grade"] is None and r["scout"]["units"] is None and r["scout"]["hold"]["label"] == "Research estimate", r["key"]
    assert "not validated" not in json.dumps(doc)


def test_a_contained_market_shows_its_chance_and_no_lean(env):
    r = recs(env, "nfl")[("Sam Field", "underdog")]
    assert r["scout"]["side"] is None and r["scout"]["p_side"] is None and r["scout"]["p_over"] == 0.5614
    assert r["scout"]["hold"]["reason"] == "NFL grades are held until forward results clear review."


def test_a_sanity_hold_has_no_lean_and_says_why(env):
    r = recs(env, "nba")[("Alex Hold", "underdog")]
    assert r["scout"]["side"] is None and r["scout"]["hold"]["reason"] == "Held: projection is under 25% of the line"


def test_the_servers_signal_decides_the_lean(env):
    r = recs(env, "nba")[("Reed Lean", "prizepicks")]
    assert r["scout"]["side"] == "over" and r["scout"]["p_side"] == 0.58


def test_a_push_chance_comes_through(env):
    assert recs(env, "nfl")[("Dee Route", "underdog")]["scout"]["p_push"] == 0.1297


def test_books_line_up_on_the_same_line_only(env):
    r = recs(env, "nba")
    assert r[("Karl Anders", "prizepicks")]["key"] == r[("Karl Anders", "underdog")]["key"] == r[("Karl Anders", "fanduel")]["key"]
    assert r[("Jay Brown", "prizepicks")]["key"] != r[("Jay Brown", "underdog")]["key"]


def test_a_book_is_compared_but_is_no_slip_leg(env):
    r = recs(env, "nba")
    assert r[("Karl Anders", "fanduel")]["offer"]["over"]["payout"] == 1.91
    doc = get(env, "board?sport=nba")[1]
    assert doc["platforms"]["fanduel"] == {"label": "FanDuel", "short": "FD", "slip": False, "kind": "decimal"} and doc["platforms"]["underdog"]["slip"] is True
    l = leg(r[("Karl Anders", "fanduel")], "under")
    assert problems(check(env, [l])[1], l) == ["gone"]


def test_an_unchanged_board_is_revalidated_not_downloaded(env):
    recs(env, "nba")
    fresh()
    recs(env, "nba")
    assert env.sports.etag_hits >= 1


def test_one_board_read_for_a_crowd(env):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(30) as ex:
        codes = list(ex.map(lambda i: get(env, "board?sport=nba", user=f"u{i}")[0], range(60)))
    assert set(codes) == {200} and env.sports.calls["/api/sports/board"] <= 6     # three parts, at most twice in the test's time


def test_a_failed_board_read_keeps_the_last_copy(env):
    a = recs(env, "nba")
    env.sports.down = True
    fresh()
    workspace_sports._TOKEN.update(token=None, exp=0.0)
    assert recs(env, "nba").keys() == a.keys()


def test_a_failed_read_stops_asking_for_a_moment(env):
    recs(env, "nba")
    env.sports.down = True
    fresh()
    workspace_sports._TOKEN.update(token=None, exp=0.0)
    get(env, "board?sport=nba")
    before = sum(env.sports.calls.values())
    with pytest.raises(workspace_sports.Down):
        workspace_sports.call(server, "/api/sports/board", {"league": "nba"})
    assert sum(env.sports.calls.values()) == before


def test_a_settle_page_never_outruns_what_a_pass_can_read():
    calls_per_tier = workspace_sports.SETTLE_PAGE / workspace_sports.SETTLE_LEGS
    assert workspace_api.LEG_PAGE == workspace_sports.SETTLE_PAGE and 2 * (calls_per_tier + 1) <= workspace_sports.SETTLE_CALLS


def test_every_leg_gets_its_turn_when_more_are_open_than_a_pass_reads(tmp_path):
    s = workspace_store.Store(tmp_path / "w.db")
    seen = []

    def read(rows):
        seen.extend(r["settle_key"] for r in rows)
        return {}
    read.page = 10
    s.sources = {"nba": read}

    def op(con):
        for i in range(25):
            con.execute("INSERT INTO leg_watch(settle_key, sport, ref, last_tracked) VALUES(?, 'nba', '{}', '2026-11-01')", (f"k{i:02d}",))
    s.submit(op)
    for _ in range(3):
        s.settle_once()
    s.close()
    assert len(set(seen)) == 25


def test_a_team_key_never_reaches_a_member():
    legs = [{"sport": "lol", "book": "underdog", "team": "lol:T1", "nick": n, "side": "over", "event": n} for n in "ab"]
    text = rules.entry_status("underdog", legs)["problems"][0]["text"]
    assert "(T1)" in text and "lol:" not in text


def test_board_down_with_nothing_read_answers_503(env):
    env.sports.down = True
    assert get(env, "board?sport=nba")[0] == 503


def test_token_stays_on_the_server(env):
    doc = get(env, "board?sport=nba")[1]
    assert "st1." not in json.dumps(doc) and all(t.startswith("Bearer st1") for t in env.sports.tokens)
    assert "st1." not in json.dumps(get(env, "me")[1])


# ---- LoL board
def test_lol_records_come_from_the_member_view(env):
    r = recs(env, "lol")
    a = r[("Harky", "underdog")]
    assert a["sport"] == "lol" and a["event"]["id"].startswith("lol-") and a["market"]["period"] == "maps13" and a["market"]["stat_label"] == "Kills"
    assert a["scout"]["units"] == 0.0 and a["scout"]["grade"] == "A-" and a["scout"]["side"] == "under" and "0U" in a["scout"]["stake_reason"]
    assert a["player"]["role"] and a["offer"]["over"]["offered"] and a["offer"]["under"]["offered"]
    for k in r:
        assert r[k]["scout"]["units"] == 0.0
    for hidden in ("lol:player", "lol:series", "market_key", "ud:", "pp:"):       # the same line the member LoL board keeps
        assert hidden not in json.dumps(a), hidden


def test_a_lean_stays_lean(env):
    a = recs(env, "lol")[("Guigo", "underdog")]
    assert a["scout"]["grade"] is None and a["scout"]["side"] == "over"


def test_lol_hit_cells_read_finished_series_and_leave_a_forfeit_out(env):
    a = recs(env, "lol")[("Guigo", "underdog")]                     # 11 kills on Maps 1-3 in each of 11 series against a 7.5 line
    assert a["history"]["l5"] == {"over": 5, "under": 0, "push": 0, "n": 5} and a["history"]["l10"]["n"] == 10 and a["history"]["l20"] == {"over": 11, "under": 0, "push": 0, "n": 11}


def test_lol_board_without_a_database_shows_no_hit_cells(env):
    env.lol.con.close()
    (env.lol.dir / "lol.db").unlink()
    env.lol.con = sqlite3.connect(":memory:")
    assert recs(env, "lol")[("Guigo", "underdog")]["history"]["l10"] is None


def test_lol_series_values_follow_settlement():
    series = [{"date": "2026-10-01", "opponent": "x", "best_of": 3, "games": {1: {"kills": 3, "deaths": 1, "assists": 2, "cs": 100}, 2: {"kills": 4, "deaths": 1, "assists": 2, "cs": 100}}},
              {"date": "2026-09-01", "opponent": "y", "best_of": 1, "games": {1: {"kills": 9, "deaths": 1, "assists": 2, "cs": 100}}}]
    assert [v["value"] for v in workspace_lol.series_values(series, "kills", "Maps 1-2")] == [7]
    assert [v["value"] for v in workspace_lol.series_values(series, "kills", "Maps 1-3")] == [7]      # a 2-0 best of three settles on two games
    assert [v["value"] for v in workspace_lol.series_values(series, "kills", "Map 1")] == [3, 9]
    assert workspace_lol.series_values(series, "fantasy_points", "Map 1")[0]["value"] == round(3 * 3 + 2 * 2 - 1 + 0.02 * 100, 2)


# ---- research
def test_nba_research_reads_history_books_context_and_market(env):
    r = recs(env, "nba")[("Karl Anders", "underdog")]
    st, doc = get(env, f"line?sport=nba&book=underdog&id={r['offer']['offer_id']}")
    assert st == 200
    assert [g["value"] for g in doc["history"]["series"][:3]] == [24.0, 18.0, 25.0] and len(doc["history"]["series"]) == 12 and doc["history"]["series"][0]["opponent"] == "BOS"
    assert doc["market"]["available"] and doc["market"]["p_over"] == 0.52
    assert {b["platform"] for b in doc["books"]} == {"prizepicks", "underdog", "fanduel"}
    assert [b["platform"] for b in doc["market_books"]["same"]] == ["fanduel"] and doc["market_books"]["other"][0]["platform"] == "kalshi" and doc["market_books"]["other"][0]["over"] == "0.42 / 0.46"
    ctx = {x["label"]: x["value"] for x in doc["context"]["rows"]}
    assert ctx["Opponent"] == "BOS" and ctx["Injury report"] == "questionable · Knee" and ctx["Back-to-back"] == "No" and ctx["Mitch Out"] == "out" and ctx["Points allowed per game to C"] == 24.1 and ctx["Points, L5"] == 21.4


def test_a_player_file_missing_leaves_the_rest_standing(env):
    r = recs(env, "nfl")[("Sam Field", "underdog")]
    st, doc = get(env, f"line?sport=nfl&book=underdog&id={r['offer']['offer_id']}")
    assert st == 200 and doc["history"]["series"] is None and doc["market"] == {"available": False, "reason": "No market price at this exact line."}


def test_lol_research_reads_past_series_role_and_win_chance(env):
    r = recs(env, "lol")[("Guigo", "underdog")]
    st, doc = get(env, f"line?sport=lol&book=underdog&id={r['offer']['offer_id']}")
    assert st == 200 and len(doc["history"]["series"]) == 11 and doc["history"]["series"][0]["value"] == 11 and doc["history"]["series"][0]["opponent"] == "Old Rivals" and doc["history"]["series"][0]["values"] == [5, 6]
    ctx = {x["label"]: x["value"] for x in doc["context"]["rows"]}
    assert ctx["Role"] == r["player"]["role"] and ctx["Series length"] == "Best of 5" and doc["market"]["available"] is False
    assert sorted(b["platform"] for b in doc["books"]) == ["prizepicks", "underdog"]


# ---- checks: NBA and NFL
def test_a_clean_nba_leg_passes_the_sports_check(env):
    r = recs(env, "nba")
    legs = [leg(r[("Karl Anders", "underdog")], "under"), leg(r[("Reed Lean", "underdog")], "over")]
    st, doc = check(env, legs)
    assert st == 200 and all(doc["legs"][l["id"]]["ok"] for l in legs) and doc["entries"]["underdog"]["ok"] and doc["entries"]["underdog"]["trackable"]
    assert env.sports.calls["/api/sports/picks/check"] >= 2


@pytest.mark.parametrize("code,want,chk", [("started", "locked", 0), ("start_unknown", "start_unknown", 0), ("gone", "gone", 1), ("changed", "moved", 1), ("not_open", "side", 1), ("side_not_offered", "side", 1),
                                           ("not_gradable", "not_gradable", 1), ("stale", "stale", 1), ("forecast_mismatch", "forecast_mismatch", 2), ("display_mismatch", "display_mismatch", 2)])
def test_each_sports_refusal_lands_on_its_check(env, code, want, chk):
    r = recs(env, "nba")[("Karl Anders", "underdog")]
    rec_id = next(q["r"] for m in env.sports.boards["nba"][0]["markets"] for q in m["q"] if q["p"] == "underdog" and q["l"] == 20.5)
    env.sports.answers[rec_id] = {"ok": False, "code": code, "text": "The sports text", "record_id": rec_id, "side": "under"}
    l = leg(r, "under")
    doc = check(env, [l])[1]
    p = doc["legs"][l["id"]]["problems"]
    assert [x["code"] for x in p] == [want] and p[0]["check"] == chk and not doc["entries"]["underdog"]["trackable"] and doc["fails"][chk] >= 1


def test_a_sports_outage_is_a_retry_and_nothing_tracks(env):
    r = recs(env, "nba")
    legs = [leg(r[("Karl Anders", "underdog")], "under"), leg(r[("Reed Lean", "underdog")], "over")]
    env.sports.down = True
    fresh()
    doc = check(env, legs)[1]
    assert all(p["retry"] for l in legs for p in doc["legs"][l["id"]]["problems"]) and not doc["entries"]["underdog"]["trackable"]
    assert track(env, legs, "underdog")[0] == 409


def test_a_chance_that_moved_on_the_board_asks_for_the_current_offer(env):
    r = recs(env, "nba")[("Karl Anders", "underdog")]
    l = dict(leg(r, "under"), shown_p=0.6)
    assert problems(check(env, [l])[1], l) == ["display_mismatch"]


def test_a_line_that_moved_is_refused_with_the_current_offer(env):
    r = recs(env, "nba")[("Karl Anders", "underdog")]
    for m in env.sports.boards["nba"][0]["markets"]:
        for q in m["q"]:
            if q["p"] == "underdog" and q["l"] == 20.5:
                q["l"] = 21.5
    fresh()
    get(env, "board?sport=nba")
    l = leg(r, "under")
    doc = check(env, [l])[1]
    assert "moved" in problems(doc, l) and doc["legs"][l["id"]]["current"]["line"] == 21.5


def test_a_game_that_started_is_refused(env):
    r = recs(env, "nba")[("Karl Anders", "underdog")]
    env.sports.boards["nba"][0]["events"]["nba:espn:1"]["start_utc"] = "2026-01-01T00:00:00Z"
    fresh()
    get(env, "board?sport=nba")
    l = leg(r, "under")
    assert "locked" in problems(check(env, [l])[1], l)


def test_a_line_read_has_no_forecast_for_this_line(env):
    b = env.sports.boards["nba"][0]
    q = b["markets"][0]["q"][1]
    q["lr"] = {"f": q["f"], "po": 0.44, "pu": 0.56, "pp": 0.0, "ln": 21.5}
    q["f"] = None
    r = recs(env, "nba")[("Karl Anders", "underdog")]
    assert r["scout"]["p_over"] == 0.44
    l = leg(r, "under")
    assert problems(check(env, [l])[1], l) == ["no_forecast"]


def test_ev_needs_a_validated_chance(env):
    r = recs(env, "nba")
    legs = [leg(r[("Karl Anders", "underdog")], "under"), leg(r[("Reed Lean", "underdog")], "over")]
    ev = check(env, legs)[1]["entries"]["underdog"]["ev"]
    assert ev == {"available": False, "reason": "NBA chances are research estimates.", "payout": ev["payout"]} and ev["payout"]["multiple"] == 3.5


# ---- entries across sports
def lol_nba_pp(env):
    a, b = recs(env, "lol")[("Harky", "prizepicks")], recs(env, "nba")[("Reed Lean", "prizepicks")]
    return [leg(a), leg(b)]


def test_prizepicks_takes_a_mixed_entry_with_its_source(env):
    legs = lol_nba_pp(env)
    e = check(env, legs)[1]["entries"]["prizepicks"]
    assert e["status"] == "valid" and e["ok"] and e["trackable"] and e["sports"] == ["lol", "nba"]
    keys = {r["rule"]: r for r in e["rules"]}
    assert keys["mixed_sports"]["source"].startswith("https://www.prizepicks.com") and "for the most part" in keys["mixed_sports"]["text"].lower() and "league_limits" in keys
    assert e["ev"] == {"available": False, "reason": "PrizePicks shows lineup payouts only in its app."}


def test_underdog_says_why_a_mixed_entry_is_not_offered(env):
    a, b = recs(env, "lol")[("Harky", "underdog")], recs(env, "nba")[("Reed Lean", "underdog")]
    e = check(env, [leg(a), leg(b)])[1]["entries"]["underdog"]
    assert e["status"] == "not_published" and not e["trackable"] and "does not publish whether one entry can hold LoL and NBA picks" in e["problems"][0]["text"]


def test_four_sports_on_one_prizepicks_entry(env):
    r = cs2(env)
    legs = [leg_of(r[("vargo", "prizepicks")]) | {"sport": "cs2", "team": "Halcyon", "player": "vargo"}, *lol_nba_pp(env), leg(recs(env, "nfl")[("Sam Field", "prizepicks")], "over")]
    e = check(env, legs)[1]["entries"]["prizepicks"]
    assert e["ok"] and e["sports"] == ["cs2", "lol", "nba", "nfl"]
    st, doc = track(env, legs, "prizepicks", "power")
    assert st == 200 and len(doc["receipts"]) == 4
    m = mine(env)
    assert [g["sport"] for g in m["by_sport"]] == ["cs2", "lol", "nba", "nfl"] and m["record"]["pending"] == 4 and {r["sport"] for r in m["receipts"]} == {"cs2", "lol", "nba", "nfl"}


def test_nba_dallas_and_nfl_dallas_are_two_teams():
    legs = [{"sport": "nba", "book": "prizepicks", "team": "nba:DAL", "nick": "a", "side": "over", "event": "e1"}, {"sport": "nfl", "book": "prizepicks", "team": "nfl:DAL", "nick": "b", "side": "over", "event": "e2"}]
    assert rules.entry_status("prizepicks", legs)["ok"]


def test_one_team_is_refused_on_every_sport(env):
    a = recs(env, "lol")
    legs = [leg(a[("Harky", "underdog")]), leg(a[("Chika", "underdog")])]
    teams = {a[("Harky", "underdog")]["player"]["team"], a[("Chika", "underdog")]["player"]["team"]}
    e = check(env, legs)[1]["entries"]["underdog"]
    assert (e["status"] == "refused") == (len(teams) == 1)


def test_a_conflict_on_a_prop_is_refused_and_the_same_prop_cannot_be_tracked_twice(env):
    r = recs(env, "nba")
    a, b = leg(r[("Karl Anders", "underdog")], "under"), leg(r[("Reed Lean", "underdog")], "over")
    flip = dict(a, id="flip", side="over", shown_p=r[("Karl Anders", "underdog")]["scout"]["p_over"])
    assert "conflict" in problems(check(env, [a, flip, b])[1], a)
    assert track(env, [a, b], "underdog")[0] == 200
    again = check(env, [a, b])[1]
    assert "duplicate_tracked" in problems(again, a)
    assert "conflict_tracked" in problems(check(env, [flip, b])[1], flip)


# ---- track and settle: NBA and NFL
def tracked_nba(env, user="u1"):
    r = recs(env, "nba")
    legs = [leg(r[("Karl Anders", "underdog")], "under"), leg(r[("Reed Lean", "underdog")], "over")]
    st, doc = track(env, legs, "underdog", "standard", user=user)
    assert st == 200, doc
    return legs, doc


def past(env):
    con = sqlite3.connect(workspace_api.store().path)
    con.execute("UPDATE leg_watch SET start_utc = '2026-01-01T00:00:00Z'")
    con.commit()
    con.close()


def test_a_receipt_freezes_what_the_member_saw_with_no_letter_and_no_stake(env):
    tracked_nba(env)
    m = mine(env)
    r = next(x for x in m["receipts"] if x["player"] == "Karl Anders")
    assert r["sport"] == "nba" and r["status"] == "pending" and r["side"] == "under" and r["line"] == 20.5 and r["displayed_p"] == 0.5588 and r["grade"] is None and r["units"] is None and r["research"] is True
    assert r["hold"] == "Research estimate" and r["pick_id"] is None and r["forecast_id"] == "fc-aaaaaaaa00000002" and r["matchup"] == "NYK at BOS"


def test_settlement_reads_the_sports_leg_result_at_the_receipts_own_line(env):
    legs, _ = tracked_nba(env)
    past(env)
    env.sports.settled[("ud:1", "under")] = {"status": "win", "value": 18.0, "settlement_id": "st-1", "revision": 1}
    env.sports.settled[("ud:4", "over")] = {"status": "push", "value": 6.5, "settlement_id": "st-2", "revision": 1}
    out = workspace_api.store().settle_once()
    assert out["first"] == 2
    m = mine(env)
    st = {x["player"]: x for x in m["receipts"]}
    assert st["Karl Anders"]["status"] == "hit" and st["Karl Anders"]["final"] == 18.0 and st["Reed Lean"]["status"] == "push"
    assert m["record"]["hit"] == 1 and m["record"]["push"] == 1 and m["record"]["hit_rate"] == 1.0 and m["record"]["decided"] == 1          # a push is never a win


def test_a_void_and_an_unresolved_game(env):
    tracked_nba(env)
    past(env)
    env.sports.settled[("ud:1", "under")] = {"status": "void", "value": None, "settlement_id": "st-1", "revision": 1}
    env.sports.settled[("ud:4", "over")] = {"status": "unresolved", "value": None, "settlement_id": "st-2", "revision": 1}
    workspace_api.store().settle_once()
    st = {x["player"]: x["status"] for x in mine(env)["receipts"]}
    assert st == {"Karl Anders": "void", "Reed Lean": "pending"}


def test_a_correction_is_a_new_revision_and_the_first_stays(env):
    tracked_nba(env)
    past(env)
    env.sports.settled[("ud:1", "under")] = {"status": "win", "value": 18.0, "settlement_id": "st-1", "revision": 1}
    store = workspace_api.store()
    store.settle_once()
    env.sports.settled[("ud:1", "under")] = {"status": "loss", "value": 23.0, "settlement_id": "st-2", "revision": 2}
    con = sqlite3.connect(store.path)
    con.execute("UPDATE leg_watch SET active = 1")                  # the settler's rotating page reaches it; here it is due now
    con.commit()
    con.close()
    assert store.settle_once()["corrected"] == 1
    r = next(x for x in mine(env)["receipts"] if x["player"] == "Karl Anders")
    assert r["status"] == "miss" and r["corrected"] is True and [t["kind"] for t in r["timeline"]] == ["tracked", "settled", "correction"]
    con = sqlite3.connect(store.path)
    with pytest.raises(sqlite3.Error):
        con.execute("DELETE FROM settlements")
    con.close()


def test_an_outage_keeps_the_last_result(env):
    tracked_nba(env)
    past(env)
    env.sports.settled[("ud:1", "under")] = {"status": "win", "value": 18.0, "settlement_id": "st-1", "revision": 1}
    store = workspace_api.store()
    store.settle_once()
    env.sports.down = True
    workspace_sports._TOKEN.update(token=None, exp=0.0)
    con = sqlite3.connect(store.path)
    con.execute("UPDATE leg_watch SET active = 1, mark = NULL")
    con.commit()
    con.close()
    assert store.settle_once()["first"] == 0
    assert next(x for x in mine(env)["receipts"] if x["player"] == "Karl Anders")["status"] == "hit"


def test_nothing_is_asked_before_the_game(env):
    tracked_nba(env)
    workspace_api.store().settle_once()
    assert "/api/sports/slips/settle" not in env.sports.calls


def test_other_sports_settling_does_not_wait_for_the_pick_log(env, monkeypatch):
    tracked_nba(env)
    past(env)
    env.sports.settled[("ud:1", "under")] = {"status": "win", "value": 18.0, "settlement_id": "st-1", "revision": 1}
    r = cs2(env)
    assert track(env, [leg_of(r[("vargo", "underdog")]) | {"sport": "cs2"}, leg_of(r[("orlo", "underdog")]) | {"sport": "cs2"}], "underdog", "standard")[0] == 200
    monkeypatch.setattr(workspace_store.Store, "_read_picks", staticmethod(lambda ids: (_ for _ in ()).throw(sqlite3.OperationalError("locked"))))
    with pytest.raises(sqlite3.OperationalError):
        workspace_api.store().settle_once()
    assert next(x for x in mine(env)["receipts"] if x["player"] == "Karl Anders")["status"] == "hit"


# ---- LoL: checks, track, settle
def lol_legs(env):
    r = recs(env, "lol")
    return r, [leg(r[("Harky", "underdog")]), leg(r[("Kratos", "underdog")])]


def test_a_clean_lol_entry_passes_and_tracks_at_0u(env):
    r, legs = lol_legs(env)
    st, doc = check(env, legs)
    assert st == 200 and doc["entries"]["underdog"]["ok"] and doc["entries"]["underdog"]["sports"] == ["lol"]
    assert doc["entries"]["underdog"]["ev"]["available"] is False                 # these real Underdog lines carry a payout multiplier
    assert doc["entries"]["underdog"]["ev"]["reason"] == "Underdog does not publish how multipliers combine."
    assert track(env, legs, "underdog", "standard")[0] == 200
    x = next(p for p in mine(env)["receipts"] if p["player"] == "Harky")
    row = next(y for y in env.lol.rows if y["player"] == "Harky" and y["platform"] == "underdog")
    assert x["units"] == 0.0 and x["grade"] == "A-" and x["sport"] == "lol" and x["status"] == "pending" and x["forecast_id"] == row["forecast"]["id"]


def test_lol_ev_says_the_chance_is_the_reason_once_the_payout_is_known():
    legs = [{"sport": "lol", "name": n, "match": n, "line": 7.5, "multiplier": 1.0, "on_side": True, "p": 0.6} for n in "ab"]
    out = rules.ev_status("underdog", "standard", legs, lambda: None, time.time())
    assert out == {"available": False, "reason": "LoL chances have no settled record at real lines yet.", "payout": out["payout"]} and out["payout"]["multiple"] == 3.5


def test_a_quote_not_read_in_15_minutes_is_refused(env):
    r, legs = lol_legs(env)
    env.lol.write(checked="2026-01-01T00:00:00Z")
    fresh()
    doc = check(env, legs)[1]
    assert "stale" in problems(doc, legs[0]) and not doc["entries"]["underdog"]["trackable"]


def test_a_lol_line_that_moved_is_refused(env):
    r, legs = lol_legs(env)
    row = next(x for x in env.lol.rows if x["player"] == "Harky" and x["platform"] == "underdog")
    row["line"] = 11.5
    row["forecast"]["line"] = 11.5
    env.lol.write()
    fresh()
    doc = check(env, legs)[1]
    assert "moved" in problems(doc, legs[0])


def test_a_forecast_of_another_event_is_refused(env):
    r, legs = lol_legs(env)
    env.lol.con.execute("UPDATE lol_forecasts SET sid = 'lol:series:other' WHERE id = ?", (next(x for x in env.lol.rows if x["player"] == "Harky" and x["platform"] == "underdog")["forecast"]["id"],))
    env.lol.con.commit()
    assert "forecast_mismatch" in problems(check(env, legs)[1], legs[0])


def test_a_forecast_saved_after_the_start_is_refused(env):
    r, legs = lol_legs(env)
    env.lol.con.execute("UPDATE lol_forecasts SET forecast_at = series_start")
    env.lol.con.commit()
    assert "forecast_mismatch" in problems(check(env, legs)[1], legs[0])


def test_a_series_that_started_is_refused(env):
    r, legs = lol_legs(env)
    env.lol.start = datetime.now(timezone.utc) - timedelta(minutes=1)
    env.lol.write()
    fresh()
    assert "locked" in problems(check(env, legs)[1], legs[0])


def test_the_side_the_line_does_not_offer_is_refused(env):
    r, legs = lol_legs(env)
    row = next(x for x in env.lol.rows if x["player"] == "Harky" and x["platform"] == "underdog")
    row["sides"]["lower"]["status"] = "suspended"
    env.lol.write()
    fresh()
    get(env, "board?sport=lol")
    assert "side" in problems(check(env, legs)[1], legs[0])


@pytest.mark.parametrize("status,over_hit,actual,side,want", [("graded", 0, 9, "under", "hit"), ("graded", 1, 12, "under", "miss"), ("push", None, 10.5, "under", "push"), ("void", None, None, "under", "void"), ("dnp", None, None, "under", "void")])
def test_lol_settles_from_its_own_result(env, status, over_hit, actual, side, want):
    r, legs = lol_legs(env)
    assert track(env, legs, "underdog", "standard")[0] == 200
    env.lol.settle(next(x for x in env.lol.rows if x["player"] == "Harky" and x["platform"] == "underdog"), status, actual, over_hit)
    workspace_api.store().settle_once()
    x = next(p for p in mine(env)["receipts"] if p["player"] == "Harky")
    assert x["status"] == want


def test_a_lol_rule_not_confirmed_settles_nothing(env):
    r, legs = lol_legs(env)
    track(env, legs, "underdog", "standard")
    env.lol.settle(next(x for x in env.lol.rows if x["player"] == "Harky" and x["platform"] == "underdog"), "rule_unverified")
    workspace_api.store().settle_once()
    assert next(p for p in mine(env)["receipts"] if p["player"] == "Harky")["status"] == "pending"


def test_a_lol_correction_adds_a_revision_and_a_missing_file_keeps_the_result(env):
    r, legs = lol_legs(env)
    track(env, legs, "underdog", "standard")
    row = next(x for x in env.lol.rows if x["player"] == "Harky" and x["platform"] == "underdog")
    env.lol.settle(row, "graded", 9, 0)
    store = workspace_api.store()
    store.settle_once()
    env.lol.settle(row, "graded", 12, 1, version=2)
    con = sqlite3.connect(store.path)
    con.execute("UPDATE leg_watch SET active = 1")
    con.commit()
    con.close()
    store.settle_once()
    x = next(p for p in mine(env)["receipts"] if p["player"] == "Harky")
    assert x["status"] == "miss" and x["corrected"]
    env.lol.con.close()
    (env.lol.dir / "lol.db").unlink()
    env.lol.con = sqlite3.connect(":memory:")
    con = sqlite3.connect(store.path)
    con.execute("UPDATE leg_watch SET active = 1, mark = NULL")
    con.commit()
    con.close()
    store.settle_once()
    assert next(p for p in mine(env)["receipts"] if p["player"] == "Harky")["status"] == "miss"


# ---- results
def test_lol_results_are_its_learning_file_with_pushes_apart(env):
    st, doc = get(env, "results?sport=lol")
    assert st == 200 and doc["sport"] == "lol" and "0 units" in doc["population"]
    t = doc["totals"]
    assert t["wins"] == 67 and t["losses"] == 53 and t["pushes"] == 6 and t["observed"] == 0.558 and t["printed"] == 0.571 and t["ci"] == [0.49, 0.62] and t["matches"] == 40 and t["void"] == 3
    assert [g["name"] for g in doc["groups"]["stat"]] == ["Assists", "Kills"] and doc["groups"]["chance"][0]["name"] == "B"
    assert doc["groups"]["chance"][0]["picks"] == 50
    assert doc["groups"]["chance"][0]["printed"] == 0.571


def test_lol_legacy_results_never_substitute_unplayable_groups_or_statuses(env):
    path = env.lol.dir / "learning.json"
    doc = json.loads(path.read_text())
    del doc["offered_side"]
    doc["metrics"]["side offered"].pop("mean_displayed_p_side")
    path.write_text(json.dumps(doc))
    st, report = get(env, "results?sport=lol")
    assert st == 200
    assert report["totals"]["picks"] == 120
    assert report["totals"]["pushes"] is None
    assert report["totals"]["void"] is None
    assert report["totals"]["printed"] is None
    assert all(not g for g in report["groups"].values())
    del doc["metrics"]["side offered"]
    path.write_text(json.dumps(doc))
    server._RESULTS.clear()
    assert get(env, "results?sport=lol")[1]["totals"]["wins"] is None


def test_sports_results_are_the_public_file_with_stated_and_observed_apart(env):
    st, doc = get(env, "results?sport=nba")
    assert st == 200 and doc["label"] == "NBA research record"
    t = doc["totals"]
    assert t["wins"] == 60 and t["losses"] == 52 and t["pushes"] == 4 and t["observed"] == 0.5357 and t["printed"] == round((0.527 * 68 + 0.571 * 44) / 112, 4) and t["ci"] == [0.49, 0.58]
    assert doc["groups"]["chance"][1]["printed"] == 0.571 and doc["groups"]["chance"][1]["observed"] == 0.5455 and doc["market"]["pairs"] == 90


def test_results_not_ready_are_stated_never_zero(env):
    env.sports.public = None
    assert get(env, "results?sport=nfl") == (503, {"error": "results_unavailable"})
    (env.lol.dir / "learning.json").unlink()
    assert get(env, "results?sport=lol") == (503, {"error": "results_unavailable"})


# ---- rules, state, wording, isolation
def test_every_sport_has_rule_rows_with_sources():
    p = rules.rules_payload()
    keys = {r["key"] for r in p["rules"]}
    assert {"mixed_sports", "league_limits", "ties", "entry_size", "two_teams"} <= keys


def test_watchlist_saves_to_the_account(env):
    w = [{"id": "nba:x", "sport": "nba", "key": "x", "name": "Karl Anders", "team": "NYK"}]
    assert call(env, "POST", "state", {"rev": 0, "watch": w})[0] == 200
    assert get(env, "state")[1]["watch"] == w
    assert call(env, "POST", "state", {"rev": 1, "watch": [{"id": "x"}] * 101})[0] == 400


def test_studio_batches_preserve_drafts_and_reject_oversized_shapes(env):
    draft = {"legs": [], "types": {"prizepicks": "power"}}
    assert call(env, "POST", "state", {"rev": 0, "slip": draft})[0] == 200
    batch = {"version": 1, "slips": [{"legs": [{"id": "a"}, {"id": "b"}]}] * 8}
    assert call(env, "POST", "state", {"rev": 1, "studio": batch})[0] == 200
    state = get(env, "state")[1]
    assert state["studio"] == batch and state["slip"] == draft
    assert call(env, "POST", "state", {"rev": 1, "studio": None})[0] == 409
    assert call(env, "POST", "state", {"rev": 2, "studio": {"version": 1, "slips": batch["slips"] * 2}})[0] == 400
    assert call(env, "POST", "state", {"rev": 2, "prefs": {"large": "x" * 17000}})[0] == 413


NEW = ("workspace_lol.py", "workspace_sports.py")


@pytest.mark.parametrize("name", NEW)
def test_new_modules_import_neither_sport_nor_a_grader(name):
    tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
    imports = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            imports.add(n.module.split(".")[0])
    assert not imports & {"lol", "sports", "server", "cs2_scout", "grader"}, imports


@pytest.mark.parametrize("bad", [r"financial advice", r"betting advice", r"not validated", r"unvalidated", r"unconfirmed", r"provisional", r"hypothetical", r"\broi\b", r"profit"])
@pytest.mark.parametrize("name", NEW)
def test_owner_wording_on_new_server_text(bad, name):
    import re
    tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
    doc = ast.get_docstring(tree) or ""
    fns = {ast.get_docstring(n) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    text = "\n".join(s.value for s in ast.walk(tree) if isinstance(s, ast.Constant) and isinstance(s.value, str) and s.value != doc and s.value not in fns)
    assert not re.search(bad, text, re.I), (name, bad)


def test_no_sample_rows_remain_anywhere_a_member_reaches():
    for f in ("workspace-data.js", "workspace.js", "workspace.html", "workspace.css"):
        text = (ROOT / "web" / f).read_text(encoding="utf-8").lower()
        for bad in ("sample data", "sample-tag", "sample sports", "delvane", "tidefall", "sample-on"):
            assert bad not in text, (f, bad)


# ---- the store
OLD_RECEIPTS = """CREATE TABLE receipts(
  receipt_id TEXT PRIMARY KEY, member TEXT NOT NULL, slip_id TEXT NOT NULL, leg_no INTEGER NOT NULL, tracked_at TEXT NOT NULL,
  sport TEXT NOT NULL CHECK(sport='cs2'), platform TEXT NOT NULL, row_id TEXT NOT NULL, offer_rev TEXT NOT NULL, side TEXT NOT NULL,
  line REAL NOT NULL, line_type TEXT NOT NULL, pick_id INTEGER NOT NULL, forecast_id INTEGER NOT NULL, match_slug TEXT NOT NULL,
  match_id TEXT, match_key TEXT NOT NULL, nick TEXT NOT NULL, prop TEXT NOT NULL, scope TEXT NOT NULL, start_utc TEXT, model_version TEXT,
  scratch INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL);
CREATE INDEX receipts_member ON receipts(member, tracked_at DESC);
CREATE TABLE member_state(member TEXT PRIMARY KEY, rev INTEGER NOT NULL, updated_at TEXT NOT NULL, slip TEXT, prefs TEXT, views TEXT);
CREATE TRIGGER receipts_no_update BEFORE UPDATE ON receipts BEGIN SELECT RAISE(ABORT, 'receipts is append-only'); END;
CREATE TRIGGER receipts_no_delete BEFORE DELETE ON receipts BEGIN SELECT RAISE(ABORT, 'receipts is append-only'); END;
"""


def old_db(path):
    con = sqlite3.connect(path)
    con.executescript(OLD_RECEIPTS)
    con.execute("INSERT INTO receipts VALUES('a','m','s',0,'2026-11-01T00:00:00+00:00','cs2','underdog','r','o','over',30.5,'standard',7,8,'slug','hltv:1','hltv:1','nick','kills','12',NULL,'M',0,'{}')")
    con.execute("INSERT INTO member_state VALUES('m',3,'t','{\"legs\":[]}',NULL,NULL)")
    con.commit()
    con.close()


def test_a_cs2_only_store_moves_over_with_every_row(tmp_path):
    p = tmp_path / "workspace.db"
    old_db(p)
    s = workspace_store.Store(p)
    try:
        r = s._open().execute("SELECT * FROM receipts").fetchall()
        assert len(r) == 1 and r[0]["pick_id"] == 7 and r[0]["forecast_id"] == 8 and r[0]["nick"] == "nick" and r[0]["settle_key"] is None
        assert s.get_state("m")["rev"] == 3 and s.get_state("m")["watch"] is None
    finally:
        s.close()
    con = sqlite3.connect(p)
    with pytest.raises(sqlite3.Error):
        con.execute("DELETE FROM receipts")
    with pytest.raises(sqlite3.Error):
        con.execute("UPDATE receipts SET nick = 'x'")
    con.close()
    s = workspace_store.Store(p)                                    # a second open changes nothing
    try:
        assert s._open().execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
    finally:
        s.close()


def test_a_failed_move_changes_nothing(tmp_path, monkeypatch):
    p = tmp_path / "workspace.db"
    old_db(p)
    monkeypatch.setattr(workspace_store, "RECEIPT_COLS", workspace_store.RECEIPT_COLS + ("no_such_column",))
    with pytest.raises(sqlite3.Error):
        workspace_store.Store(p, start=False)
    con = sqlite3.connect(p)
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'receipts'").fetchone()[0]
    assert "sport='cs2'" in sql and con.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()[0] == 2
    con.close()


def test_the_store_takes_only_the_four_sports(tmp_path):
    s = workspace_store.Store(tmp_path / "w.db")
    con = s._open()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO receipts(receipt_id, member, slip_id, leg_no, tracked_at, sport, platform, row_id, offer_rev, side, line, line_type, forecast_id, match_slug, match_key, nick, prop, scope, payload, settle_key) "
                        "VALUES('a','m','s',0,'t','dota','underdog','r','o','over',1,'standard',1,'m','k','n','kills','12','{}','k')")
        with pytest.raises(sqlite3.IntegrityError):                 # a sport other than CS2 settles by its key, so it must have one
            con.execute("INSERT INTO receipts(receipt_id, member, slip_id, leg_no, tracked_at, sport, platform, row_id, offer_rev, side, line, line_type, forecast_id, match_slug, match_key, nick, prop, scope, payload) "
                        "VALUES('a','m','s',0,'t','nba','underdog','r','o','over',1,'standard',1,'m','k','n','kills','12','{}')")
    finally:
        con.close()
        s.close()

def test_released_letter_is_identical_in_board_research_and_receipt(env):
    r = recs(env, 'nba')[('Reed Lean', 'prizepicks')]
    assert r['scout']['grade'] == 'B'
    code, research = get(env, f"line?sport=nba&book=prizepicks&id={r['offer']['offer_id']}")
    assert code == 200 and research['record']['scout']['grade'] == 'B'
    assert all(b['grade'] == 'B' for b in research['books'])
    code, result = track(env, [leg(r, 'over'), leg(recs(env, 'nba')[('Karl Anders', 'prizepicks')], 'under')], 'prizepicks')
    assert code == 200, result
    receipt = next(x for x in mine(env)['receipts'] if x['player'] == 'Reed Lean')
    assert receipt['grade'] == 'B' and receipt['forecast_grade'] == 'B'
    assert receipt['units'] == 0 and receipt['research'] is True


def test_released_letter_does_not_follow_member_to_opposite_side(env):
    r = recs(env, 'nba')[('Reed Lean', 'prizepicks')]
    code, result = track(env, [leg(r, 'under'), leg(recs(env, 'nba')[('Karl Anders', 'prizepicks')], 'under')], 'prizepicks')
    assert code == 200, result
    assert next(x for x in mine(env)['receipts'] if x['player'] == 'Reed Lean')['grade'] is None


def test_read_revision_changes_with_published_letter_not_stake_release():
    sc = {'po': .58, 'pu': .42}
    old = workspace_sports.read_rev('over', sc, None)
    new = workspace_sports.read_rev('over', sc, None, 'B')
    assert old != new
    assert new != workspace_sports.read_rev('over', sc, None, 'A')


def test_player_context_includes_long_windows_and_all_engine_groups(env):
    player = env.sports.players['nba:player:espn:301']
    player['factors']['stats'][0]['windows'] += [{'window': 'L20', 'mean': 19.7, 'n': 20}, {'window': 'Season', 'mean': 20.2, 'n': 45}]
    player['factors']['engine']['rows'] += [{'group': 'Usage', 'label': 'Touches per game', 'value': 57.4}, {'group': 'Pace', 'label': 'Team possessions', 'value': 101.2}]
    r = recs(env, 'nba')[('Karl Anders', 'underdog')]
    code, d = get(env, f"line?sport=nba&book=underdog&id={r['offer']['offer_id']}")
    assert code == 200
    facts = {v['label']: v['value'] for v in d['context']['rows']}
    assert facts['Points, L20'] == 19.7 and facts['Points, Season'] == 20.2
    assert facts['Touches per game'] == 57.4 and facts['Team possessions'] == 101.2

def test_sports_board_reads_prebuilt_l20_at_the_exact_line(env):
    doc, view = env.sports.boards['nba']
    for row in view['rows'].values():
        row['lines20'] = {'20.5': [11, 8, 1, 20]}
    r = recs(env, 'nba')[('Karl Anders', 'underdog')]
    assert r['history']['l20'] == {'over': 11, 'under': 8, 'push': 1, 'n': 20}
    assert recs(env, 'nba')[('Jay Brown', 'underdog')]['history']['l20'] is None
