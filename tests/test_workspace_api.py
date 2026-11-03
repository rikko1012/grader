"""The workspace API over real HTTP on 127.0.0.1 with server.Handler: every route, every refusal, tracking, restart persistence,
settlement, two devices and the gate. Fixture board, feeds and pick log only (tests/workspace_fixture.py)."""
import ast
import http.client
import json
import re
import sqlite3
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest

import server
import whop_auth
import workspace_api
import workspace_rules
import workspace_store
from tests.workspace_fixture import MODEL, Fixture, iso, leg_of, row

API = "/preview/workspace/api/"
ADMIN = "B" * 40
ROOT = Path(server.__file__).parent
GET_ROUTES = ("me", "board", "line?book=underdog&id=x", "mine", "results", "state", "rules", "diagnostics", "grade-review")
POST_ROUTES = ("recheck", "track", "state", "book-line")


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in ("SPORTS_UI_ALLOW", "REQUIRE_WHOP", "RENDER", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SCOUT_WORKSPACE_PREVIEW", "1")
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: {"user": v[5:], "name": "Tester", "ok": True} if v and v.startswith("good-") else None)
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_ENC_LOCKS", {}), ("_RES_BYTES", [0, 0.0]), ("_MAP1_STATUS", [0.0, None]), ("BOARD", {})):
        monkeypatch.setattr(server, name, val)
    fx = Fixture(tmp_path)
    fx.install(monkeypatch.setattr)
    workspace_api.reset()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    fx.port = httpd.server_address[1]
    yield fx
    httpd.shutdown()
    httpd.server_close()
    workspace_api.reset()


def call(fx, method, path, body=None, user="u1", headers=None):
    c = http.client.HTTPConnection("127.0.0.1", fx.port, timeout=30)
    h = dict(headers or {})
    if user:
        h["Cookie"] = f"{whop_auth.COOKIE}=good-{user}"
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        doc = json.loads(raw) if raw else None
    except ValueError:
        doc = raw
    return r.status, doc


def get(fx, name, **kw):
    return call(fx, "GET", API + name, **kw)


def post(fx, name, body, **kw):
    return call(fx, "POST", API + name, body, **kw)


def fresh():
    """Next board read rebuilds: tests edit fixture rows and need the 5 s copies to go."""
    server._GB.clear()
    server._RESULTS.clear()


def records(fx):
    fresh()
    st, doc = get(fx, "board")
    assert st == 200
    return {(r["player"]["name"], r["offer"]["platform"]): r for r in doc["records"]}


IDEM = [0]


def idem():
    IDEM[0] += 1
    return f"idem-{IDEM[0]:06d}"


def check(fx, legs, user="u1", entry_types=None):
    return post(fx, "recheck", {"idem": idem(), "legs": legs, "entry_types": entry_types or {}}, user=user)


def track(fx, legs, key="underdog", entry_type="standard", user="u1", i=None):
    return post(fx, "track", {"idem": i or idem(), "entry_key": key, "entry_type": entry_type, "legs": legs}, user=user)


def pair(fx, book="underdog"):
    r = records(fx)
    return [leg_of(r[("vargo", book)]), leg_of(r[("orlo", book)])]


def codes(doc, leg):
    return [p["code"] for p in doc["legs"][leg["id"]]["problems"]]


# ---- gate
@pytest.mark.parametrize("name", GET_ROUTES)
def test_get_off_answers_as_an_unknown_route(env, monkeypatch, name):
    monkeypatch.delenv("SCOUT_WORKSPACE_PREVIEW")
    st, doc = get(env, name)
    assert st == 404 and doc == {"error": "not found"}


@pytest.mark.parametrize("name", POST_ROUTES)
def test_post_off_answers_as_an_unknown_route(env, monkeypatch, name):
    monkeypatch.delenv("SCOUT_WORKSPACE_PREVIEW")
    assert post(env, name, {"x": 1}) == (403, {"error": "forbidden"})


def test_off_creates_no_database_and_no_thread(env, monkeypatch, tmp_path):
    monkeypatch.delenv("SCOUT_WORKSPACE_PREVIEW")
    get(env, "board")
    assert not (tmp_path / "workspace.db").exists()
    assert not [t for t in threading.enumerate() if t.name.startswith("workspace-")]


@pytest.mark.parametrize("name", ("me", "board", "mine", "state"))
def test_a_signed_out_visitor_gets_the_unknown_route_answer(env, name):
    assert get(env, name, user=None)[0] == 404


def test_paid_check_decides_with_the_flag_on(env, monkeypatch):
    monkeypatch.setattr(whop_auth, "session", lambda v: {"user": "u", "name": "x", "ok": False})
    assert get(env, "board")[0] == 404
    assert post(env, "state", {"rev": 0, "prefs": {}})[0] == 403


def test_unlisted_paid_member_is_refused_with_the_flag_off(env, monkeypatch):
    monkeypatch.delenv("SCOUT_WORKSPACE_PREVIEW")
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id("owner"))
    assert get(env, "board", user="someone")[0] == 404
    assert get(env, "board", user="owner")[0] == 200


def test_owner_token_with_no_session_is_a_fixed_scratch_member(env):
    st, doc = get(env, "me", user=None, headers={"X-Admin-Token": ADMIN})
    assert st == 200 and doc["scratch"] is True and doc["member"] == workspace_api.OWNER_ID
    st, doc = get(env, "me", user=None, headers={"Referer": f"http://x/preview/workspace?token={ADMIN}"})
    assert st == 200 and doc["scratch"] is True


def test_unknown_api_paths(env):
    assert get(env, "nope") == (404, {"error": "not found"})
    assert post(env, "nope", {}) == (403, {"error": "forbidden"})


def test_shared_body_is_never_served_before_the_gate(env, monkeypatch):
    assert get(env, "board")[0] == 200
    monkeypatch.delenv("SCOUT_WORKSPACE_PREVIEW")
    assert get(env, "board")[0] == 404


# ---- /me /rules /results
def test_me(env):
    st, doc = get(env, "me")
    assert st == 200 and doc["sports"] == {"cs2": True, "lol": False, "nba": False, "nfl": False} and doc["rules_version"] == workspace_rules.RULES_VERSION
    assert doc["member"].startswith("wm-") and "u1" not in doc["member"] and doc["scratch"] is False


def test_rules_carry_a_source_and_date_for_each_combination(env):
    st, doc = get(env, "rules")
    assert st == 200 and doc["version"] == workspace_rules.RULES_VERSION
    keys = {r["key"] for r in doc["rules"]}
    assert {"entry_size", "two_teams", "same_player", "both_sides", "mixed_sports", "opposing_players", "goblins_demons", "two_platforms"} <= keys
    for r in doc["rules"]:
        for p in ("prizepicks", "underdog"):
            assert r[p]["read_at"] == "2026-11-02" and r[p]["status"] in ("published", "not_published", "scout")
    by = {r["key"]: r for r in doc["rules"]}
    assert by["mixed_sports"]["prizepicks"]["status"] == "published" and by["mixed_sports"]["underdog"]["status"] == "not_published"
    assert by["opposing_players"]["prizepicks"]["status"] == "not_published" and by["opposing_players"]["underdog"]["status"] == "published"


def test_results_is_the_same_shared_answer_as_the_member_route(env):
    a = get(env, "results")
    b = call(env, "GET", "/api/results")
    assert a == b and a[0] == 200 and a[1]["picks"]["picks"] == 60


# ---- /board
def test_board_maps_saved_rows_and_recomputes_nothing(env):
    st, doc = get(env, "board")
    assert st == 200 and doc["version"] == MODEL and set(doc["feeds"]) == {"underdog", "prizepicks"}
    rec = {(r["player"]["name"], r["offer"]["platform"]): r for r in doc["records"]}
    v = rec[("vargo", "underdog")]
    x = env.by("vargo")
    assert v["sport"] == "cs2" and v["event"]["id"] == "hltv:900" and v["event"]["id_kind"] == "canonical"
    assert v["scout"]["side"] == "over" and v["scout"]["p_side"] == x["p"] and v["scout"]["grade"] == "B" and v["scout"]["units"] == x["units"]
    assert v["scout"]["model_version"] == MODEL and v["scout"]["forecast_at"] == x["graded_at"]
    assert v["offer"]["revision_kind"] == "derived" and v["offer"]["over"] == {"offered": True, "payout": 1.0, "price": 1.92}
    assert v["history"]["l10"] == {"over": 6, "under": 4, "push": 0, "n": 10}
    assert env.grades == 0


def test_a_moved_line_says_where_it_came_from(env):
    env.by("vargo").update(open_line=37.5, moved_at="2026-11-02T10:00:00+00:00")
    r = records(env)
    assert r[("vargo", "underdog")]["offer"]["moved_from"] == 37.5 and r[("orlo", "underdog")]["offer"]["moved_from"] is None


def test_off_side_chance_is_not_invented(env):
    doc = get(env, "board")[1]
    r = next(x for x in doc["records"] if x["player"]["name"] == "brandr")
    assert r["scout"]["side"] == "under" and r["scout"]["p_under"] == r["scout"]["p_side"] and r["scout"]["p_over"] is None


def test_every_row_matches_the_member_board(env):
    doc = get(env, "board")[1]
    for book in ("underdog", "prizepicks"):
        pay = server.board_payload(book)
        rec = {r["offer"]["offer_id"]: r for r in doc["records"]}
        for x in pay["rows"]:
            r = rec[str(x["id"])]
            assert r["scout"]["side"] == {"More": "over", "Less": "under"}[x["side"]] and r["scout"]["p_side"] == x["p"]
            assert (r["scout"]["grade"] or "LEAN") == x["model_grade"] and r["scout"]["units"] == x["units"]
            assert (r["scout"]["stake_reason"] or None) == (x.get("stake_reason") or x.get("cap_reason") or None)


def test_board_is_one_build_per_window_and_identical_bytes_stay_one_object(env):
    get(env, "board")
    n = env.boards
    with ThreadPoolExecutor(32) as ex:
        out = list(ex.map(lambda i: get(env, "board", user=f"m{i}")[0], range(64)))
    assert set(out) == {200} and env.boards == n
    first = workspace_api._SNAP["body"]
    server._GB.clear()
    server._RESULTS.clear()
    workspace_api.snapshot(server)
    assert workspace_api._SNAP["body"] is first


def test_board_failure_keeps_the_last_copy(env):
    a = get(env, "board")[1]
    env.board_ok = False
    fresh()
    st, b = get(env, "board")
    assert st == 200 and len(b["records"]) == len(a["records"]) and b["feeds"]["underdog"]["state"] == "failed"


def test_board_with_nothing_ever_read_is_unavailable(env):
    env.board_ok = False
    assert get(env, "board") == (503, {"error": "board_unavailable"})


def test_stale_underdog_feed_is_stated(env, monkeypatch):
    real = env.payload
    monkeypatch.setattr(server, "board_payload", lambda book="underdog": dict(real(book), updated=time.time() - 3600) if book == "underdog" else real(book))
    assert get(env, "board")[1]["feeds"]["underdog"]["state"] == "stale"


# ---- /line
def test_line_detail_with_series_books_context_and_market(env, monkeypatch):
    x = env.by("vargo")
    maps = []
    for i in range(10):
        val = 25 if i < 6 else 15                      # 6 of 10 over 38.5 at two maps each
        for n in (1, 2):
            maps.append({"match_id": f"hltv-{i}", "number": n, "date": f"2026-10-{20 - i:02d}", "kills": val, "headshots": 8, "opponent": f"Team {i}", "map": "Mirage"})
    monkeypatch.setattr(server.hltv, "recent_series", lambda *a, **k: maps)
    st, doc = get(env, f"line?book=underdog&id={x['id']}")
    assert st == 200 and doc["forecast"]["p_saved"] == x["p"] and doc["forecast"]["model_version"] == MODEL
    assert len(doc["history"]["series"]) == 10 and doc["history"]["series"][0]["value"] == 50 and doc["history"]["series"][-1]["value"] == 30
    assert doc["history"]["series"][0]["maps"] == ["Mirage", "Mirage"] and doc["history"]["series"][0]["values"] == [25, 25]
    assert {b["platform"] for b in doc["books"]} == {"underdog", "prizepicks"}
    labels = {r["key"] for r in doc["context"]["rows"]}
    assert "maps_played" in labels and "adr" in labels and "secret" not in json.dumps(doc["context"])
    m = doc["market"]
    assert m["available"] and abs(m["p_over"] + m["p_under"] - 1) < 1e-3 and m["p_over"] < 0.5


def test_bars_never_disagree_with_the_saved_tally(env, monkeypatch):
    x = env.by("vargo")
    maps = [{"match_id": f"hltv-{i}", "number": n, "date": f"2026-10-{20 - i:02d}", "kills": 30, "headshots": 8, "opponent": "T", "map": "Mirage"} for i in range(10) for n in (1, 2)]
    monkeypatch.setattr(server.hltv, "recent_series", lambda *a, **k: maps)
    st, doc = get(env, f"line?book=underdog&id={x['id']}")
    assert st == 200 and doc["history"]["series"] is None and doc["history"]["tally"]["over"] == 6


def test_line_outside_the_snapshot_reads_nothing(env, monkeypatch):
    monkeypatch.setattr(workspace_api, "forecast_for", lambda *a: pytest.fail("no database read"))
    assert get(env, "line?book=underdog&id=not-on-board") == (404, {"error": "not found"})
    assert get(env, "line?book=nope&id=x")[0] == 404


def test_prizepicks_has_no_market_chance(env, monkeypatch):
    monkeypatch.setattr(server.hltv, "recent_series", lambda *a, **k: [])
    x = env.by("vargo", "prizepicks")
    st, doc = get(env, f"line?book=prizepicks&id={x['id']}")
    assert st == 200 and doc["market"]["available"] is False and "PrizePicks" in doc["market"]["reason"]


# ---- /recheck: a clean pass, then each refusal
def test_clean_entry_passes_every_check(env):
    legs = pair(env)
    st, doc = check(env, legs)
    assert st == 200 and doc["fails"] == [0, 0, 0, 0, 0] and doc["ttl_s"] == 300
    e = doc["entries"]["underdog"]
    assert e["ok"] and e["trackable"] and e["status"] == "valid" and e["ev"]["available"] is False and "_x" not in json.dumps(doc)
    assert all(doc["legs"][l["id"]]["ok"] and doc["legs"][l["id"]]["current"]["forecast_id"] for l in legs)


def test_line_started(env):
    legs = pair(env)
    past = iso(datetime.now(timezone.utc) - timedelta(minutes=2))
    x = env.by("vargo")
    x.update(lock_at=past, start=past, match_start=past)
    fresh()
    st, doc = check(env, legs)
    assert "locked" in codes(doc, legs[0]) and doc["fails"][0] >= 1 and not doc["entries"]["underdog"]["ok"]


def test_no_start_time(env):
    legs = pair(env)
    x = env.by("vargo")
    x.update(lock_at=None, start=None, match_start=None)
    fresh()
    assert "start_unknown" in codes(check(env, legs)[1], legs[0])


def test_line_gone(env):
    legs = pair(env)
    env.rows.remove(env.by("orlo"))
    fresh()
    doc = check(env, legs)[1]
    assert codes(doc, legs[1]) == ["gone"] and doc["legs"][legs[1]["id"]]["current"] is None


def test_line_moved_offers_a_swap(env):
    legs = pair(env)
    env.by("vargo").update(line=39.5, graded_line=39.5)
    fresh()
    doc = check(env, legs)[1]
    p = doc["legs"][legs[0]["id"]]["problems"][0]
    assert p["code"] == "moved" and p["swap"] is True and "from 38.5 to 39.5" in p["text"] and doc["legs"][legs[0]["id"]]["current"]["line"] == 39.5


def test_client_fields_are_compared_not_trusted(env):
    legs = pair(env)
    legs[0]["line"] = 20.5
    assert "moved" in codes(check(env, legs)[1], legs[0])
    legs = pair(env)
    legs[0]["rev"] = "d-forged"
    assert "moved" in codes(check(env, legs)[1], legs[0])


def test_side_not_offered(env):
    legs = pair(env)
    legs[0]["side"], legs[0]["shown_p"] = "under", None
    x = env.by("vargo")
    x["under_available"] = False
    fresh()
    legs[0]["rev"] = next(r for r in get(env, "board")[1]["records"] if r["offer"]["offer_id"] == x["id"])["offer"]["revision"]
    assert "side" in codes(check(env, legs)[1], legs[0])


def test_feed_down_is_a_retry_never_a_pass(env):
    legs = pair(env)
    env.feed_ok["underdog"] = False
    server._SLIP_IDX.clear()
    doc = check(env, legs)[1]
    assert "retry" in codes(doc, legs[0])
    p = next(x for x in doc["legs"][legs[0]["id"]]["problems"] if x["code"] == "retry")
    assert p["retry"] is True and not doc["entries"]["underdog"]["ok"]


def test_grade_held_during_an_outage(env):
    legs = pair(env)
    env.by("vargo")["hltv_retry"] = True
    fresh()
    assert "grade_held" in codes(check(env, legs)[1], legs[0])


def test_kept_side_the_feed_dropped(env):
    legs = pair(env)
    env.by("vargo")["unavailable"] = "Unavailable—side no longer offered"
    fresh()
    assert "side" in codes(check(env, legs)[1], legs[0])


def test_scout_state_has_no_forecast(env):
    legs = pair(env)
    env.by("vargo")["state"] = "SCOUT"
    fresh()
    assert "no_forecast" in codes(check(env, legs)[1], legs[0])


def _replace_pick(env, r, **kw):
    import picklog
    con = sqlite3.connect(picklog.DB, timeout=10)
    pid = con.execute("SELECT id FROM picks WHERE market_id = ?", (str(r["id"]),)).fetchone()[0]
    con.execute("DELETE FROM forecasts WHERE pick_id = ?", (pid,))
    con.execute("DELETE FROM picks WHERE id = ?", (pid,))
    if kw is not None and kw.get("insert", True):
        kw.pop("insert", None)
        env.seed_row(con, r, **kw)
    con.commit()
    con.close()
    fresh()


def test_newly_graded_not_yet_logged_is_pending_and_kept_two_seconds(env):
    x = env.by("vargo")
    x["graded_at"] = iso(datetime.now(timezone.utc) - timedelta(seconds=10))
    _replace_pick(env, x, insert=False)
    legs = pair(env)
    doc = check(env, legs)[1]
    p = next(a for a in doc["legs"][legs[0]["id"]]["problems"] if a["code"] == "forecast_pending")
    assert p["retry"] is True
    env.seed([x])                                       # the writer thread reaches this row
    time.sleep(2.1)
    assert "forecast_pending" not in codes(check(env, legs)[1], legs[0])


def test_board_a_little_off_the_saved_forecast_passes(env):
    x = env.by("vargo")
    _replace_pick(env, x, p_saved=x["p"] - 0.006)
    legs = pair(env)
    assert check(env, legs)[1]["legs"][legs[0]["id"]]["ok"]


def test_board_too_far_from_the_saved_forecast_refuses(env):
    x = env.by("vargo")
    _replace_pick(env, x, p_saved=x["p"] - 0.011)
    legs = pair(env)
    assert "forecast_mismatch" in codes(check(env, legs)[1], legs[0])


def test_other_model_version_refuses(env):
    x = env.by("vargo")
    _replace_pick(env, x, model="OLDMODEL")
    legs = pair(env)
    assert "forecast_mismatch" in codes(check(env, legs)[1], legs[0])


def test_forecast_logged_after_the_lock_refuses(env):
    x = env.by("vargo")
    _replace_pick(env, x, logged_at=iso(datetime.now(timezone.utc) + timedelta(days=2)))
    legs = pair(env)
    assert "forecast_mismatch" in codes(check(env, legs)[1], legs[0])


def test_same_player_and_line_on_another_match_is_not_this_forecast(env):
    x = env.by("vargo")
    _replace_pick(env, x, slug="slug-other")
    legs = pair(env)
    assert "no_forecast" in codes(check(env, legs)[1], legs[0])


def test_same_player_and_line_on_other_maps_is_not_this_forecast(env):
    x = env.by("vargo")
    _replace_pick(env, dict(x, scope="1"))
    legs = pair(env)
    assert "no_forecast" in codes(check(env, legs)[1], legs[0])


def test_page_showing_a_different_chance_refuses(env):
    legs = pair(env)
    legs[0]["shown_p"] += 0.002
    doc = check(env, legs)[1]
    p = next(a for a in doc["legs"][legs[0]["id"]]["problems"] if a["code"] == "display_mismatch")
    assert p["swap"] is True


def test_duplicate_and_conflict_inside_a_slip(env):
    a = pair(env)
    dup = dict(a[0], id="other-id")
    doc = check(env, [a[0], dup, a[1]])[1]
    assert "duplicate" in codes(doc, a[0]) and doc["fails"][4] >= 2
    conflict = dict(a[0], id="flip", side="under", shown_p=None)
    doc = check(env, [a[0], conflict, a[1]])[1]
    assert "conflict" in codes(doc, a[0])


def test_entry_size_published_refusal(env):
    r = records(env)
    legs = [leg_of(r[("vargo", "underdog")])]
    doc = check(env, legs)[1]
    e = doc["entries"]["underdog"]
    assert e["status"] == "refused" and e["problems"][0]["code"] == "entry" and "2-8" in e["problems"][0]["text"] and doc["fails"][3] >= 1


def test_one_team_published_refusal(env):
    r = records(env)
    env.rows.append(row("11111111-0000-4000-8000-000000000009", "underdog", "tessen", "Halcyon", "Norrland", 30.5, "More", 0.6, 900, env.by("vargo")["start"]))
    env.seed([env.rows[-1]])
    r = records(env)
    doc = check(env, [leg_of(r[("vargo", "underdog")]), leg_of(r[("tessen", "underdog")])])[1]
    assert doc["entries"]["underdog"]["status"] == "refused" and "at least 2 different teams" in doc["entries"]["underdog"]["problems"][0]["text"]


def test_plain_prizepicks_entry_passes_despite_the_always_on_note(env):
    legs = pair(env, "prizepicks")
    doc = check(env, legs)[1]
    assert doc["entries"]["prizepicks"]["ok"] and doc["entries"]["prizepicks"]["status"] == "valid"


def test_prizepicks_opposing_players_of_one_match_are_not_offered(env):
    r = records(env)
    doc = check(env, [leg_of(r[("vargo", "prizepicks")]), leg_of(r[("brandr", "prizepicks")])])[1]
    e = doc["entries"]["prizepicks"]
    assert e["status"] == "not_published" and e["problems"][0]["code"] == "not_published" and not e["trackable"] and "PrizePicks" in e["problems"][0]["text"]


def test_underdog_opposing_players_are_published_as_allowed(env):
    r = records(env)
    doc = check(env, [leg_of(r[("vargo", "underdog")]), leg_of(r[("brandr", "underdog")])])[1]
    assert doc["entries"]["underdog"]["ok"]


def other_leg(book="underdog", sport="nba", team="Boston", player="Jay Brown"):
    return {"id": f"s-{sport}-{player}-{book}", "sport": sport, "book": book, "row_id": "pp-1001", "side": "over", "line": 25.5, "rev": "x", "shown_p": 0.54,
            "team": team, "player": player, "event": f"{sport}-e1"}


def test_a_leg_of_a_sport_this_member_does_not_see_cannot_be_tracked(env):
    r = records(env)
    legs = [leg_of(r[("vargo", "prizepicks")]), other_leg("prizepicks")]
    st, doc = check(env, legs)
    e = doc["entries"]["prizepicks"]
    assert e["status"] == "valid" and e["rule_ok"] and not e["ok"] and not e["trackable"] and e["sports"] == ["cs2", "nba"]
    assert [p["code"] for p in doc["legs"][legs[1]["id"]]["problems"]] == ["gone"]
    assert track(env, legs, key="prizepicks", entry_type="power")[0] == 409


def test_mixed_sports_underdog_is_not_offered_and_says_why(env):
    r = records(env)
    legs = [leg_of(r[("vargo", "underdog")]), other_leg("underdog")]
    e = check(env, legs)[1]["entries"]["underdog"]
    assert e["status"] == "not_published" and "does not publish whether one entry can hold CS2 and NBA picks" in e["problems"][0]["text"]


def test_two_platforms_make_two_entries(env):
    r = records(env)
    doc = check(env, [leg_of(r[("vargo", "underdog")]), leg_of(r[("orlo", "prizepicks")])])[1]
    assert set(doc["entries"]) == {"underdog", "prizepicks"}


def test_unknown_platform_is_not_offered(env):
    legs = [other_leg("fanduel"), other_leg("fanduel", team="Miami", player="Theo Lind")]
    e = check(env, legs)[1]["entries"]["fanduel"]
    assert e["status"] == "not_published" and not e["ok"]


@pytest.mark.parametrize("body", [
    {"idem": "short", "legs": []}, {"idem": "idem-ok-12345"}, {"idem": "idem-ok-12345", "legs": "x"},
    {"idem": "idem-ok-12345", "legs": [{"id": "a"}]}, [1, 2], b"not json", b'{"idem": "idem-ok-12345", "legs": NaN}'])
def test_bad_check_bodies(env, body):
    assert post(env, "recheck", body)[0] == 400


def test_too_many_legs(env):
    legs = pair(env) * 5
    assert post(env, "recheck", {"idem": idem(), "legs": legs})[0] == 400


def test_recheck_rate_limit(env):
    legs = pair(env)
    for _ in range(workspace_api.RECHECK_PER_MIN):
        assert post(env, "recheck", {"idem": idem(), "legs": legs})[0] == 200
    assert post(env, "recheck", {"idem": idem(), "legs": legs})[0] == 429


def test_repeating_an_idem_key_buys_no_extra_checks(env):
    """A check is the expensive thing, and a member chooses the key: reusing one with any legs costs the same as a new one."""
    legs = pair(env)
    i = idem()
    for k in range(workspace_api.RECHECK_PER_MIN):
        assert post(env, "recheck", {"idem": i, "legs": [dict(legs[0], line=38.5 + k * 0.001), legs[1]]})[0] == 200
    assert post(env, "recheck", {"idem": i, "legs": legs})[0] == 429


def test_refused_tracks_with_one_idem_key_reach_the_limit(env):
    legs = pair(env)
    legs[0]["rev"] = "d-stale"
    i = idem()
    out = [track(env, legs, i=i)[0] for _ in range(workspace_api.TRACK_PER_MIN + 1)]
    assert out[:-1] == [409] * workspace_api.TRACK_PER_MIN and out[-1] == 429


def test_an_idem_key_names_one_press_of_one_set_of_legs(env):
    """The same key with other legs is another press: it can never answer with the first press's receipts."""
    r = records(env)
    i = idem()
    a, b = [leg_of(r[("vargo", "underdog")]), leg_of(r[("orlo", "underdog")])], [leg_of(r[("brandr", "underdog")]), leg_of(r[("raiko", "underdog")])]
    one = track(env, a, i=i)
    two = track(env, b, i=i)
    assert one[0] == two[0] == 200 and one[1]["slip_id"] != two[1]["slip_id"]
    assert get(env, "mine")[1]["record"]["picks"] == 4


def test_board_down_with_nothing_read_answers_503(env):
    env.board_ok = False
    assert post(env, "recheck", {"idem": idem(), "legs": [other_leg(sport="cs2")]})[0] == 503


# ---- tracking
def test_track_freezes_what_the_member_saw_and_never_touches_the_pick_log(env):
    import picklog
    before = sqlite3.connect(picklog.DB).execute("SELECT COUNT(*), SUM(id) FROM picks").fetchone()
    legs = pair(env)
    st, doc = track(env, legs)
    assert st == 200 and doc["ok"] and len(doc["receipts"]) == 2 and doc["slip_id"].startswith("sl-")
    mine = get(env, "mine")[1]
    assert mine["record"]["pending"] == 2 and mine["record"]["hit"] == 0 and len(mine["slips"]) == 1
    r = next(x for x in mine["receipts"] if x["player"] == "vargo")
    x = env.by("vargo")
    assert r["displayed_p"] == x["p"] and r["grade"] == "B" and r["units"] == x["units"] and r["forecast_p"] == x["p"] and r["status"] == "pending"
    assert r["model_version"] == MODEL and r["timeline"][0]["text"] == "Tracked Over 38.5 at 61.2%"
    assert sqlite3.connect(picklog.DB).execute("SELECT COUNT(*), SUM(id) FROM picks").fetchone() == before


def test_a_retry_of_the_same_press_returns_the_same_receipts(env):
    legs = pair(env)
    i = idem()
    a = track(env, legs, i=i)
    b = track(env, legs, i=i)
    assert a[0] == b[0] == 200 and a[1]["receipts"] == b[1]["receipts"] and a[1]["slip_id"] == b[1]["slip_id"]
    assert get(env, "mine")[1]["record"]["picks"] == 2


def test_tracking_the_same_pick_again_is_refused(env):
    legs = pair(env)
    assert track(env, legs)[0] == 200
    st, doc = track(env, legs)
    assert st == 409 and doc["error"] == "refused" and "duplicate_tracked" in codes(doc, legs[0])
    assert get(env, "mine")[1]["record"]["picks"] == 2


def test_tracking_a_conflicting_side_is_refused(env):
    legs = pair(env)
    assert track(env, legs)[0] == 200
    flip = [dict(legs[0], side="under", shown_p=None, id="flip"), dict(legs[1], side="under", shown_p=None, id="flip2")]
    st, doc = track(env, flip)
    assert st == 409 and "conflict_tracked" in codes(doc, flip[0]) and "You already tracked Over 38.5" in json.dumps(doc)


def test_concurrent_presses_track_once(env):
    legs = pair(env)
    with ThreadPoolExecutor(8) as ex:
        out = list(ex.map(lambda i: track(env, legs)[0], range(8)))
    assert out.count(200) == 1 and set(out) <= {200, 409}
    assert get(env, "mine")[1]["record"]["picks"] == 2


def test_members_cannot_read_each_others_receipts(env, tmp_path):
    legs = pair(env)
    assert track(env, legs, user="alice")[0] == 200
    assert get(env, "mine", user="bob")[1]["receipts"] == []
    assert get(env, "state", user="bob")[1]["rev"] == 0
    # bob tracks the same picks on his own
    assert track(env, legs, user="bob")[0] == 200
    con = sqlite3.connect(tmp_path / "workspace.db")
    assert con.execute("SELECT COUNT(DISTINCT member) FROM receipts").fetchone()[0] == 2


def test_raw_member_id_never_reaches_the_database(env, tmp_path):
    legs = pair(env)
    assert track(env, legs, user="whop-secret-id")[0] == 200
    assert post(env, "state", {"rev": 0, "prefs": {"a": 1}}, user="whop-secret-id")[0] == 200
    workspace_api.reset()
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("workspace.db*"))
    assert b"whop-secret-id" not in raw and b"whop:" not in raw


def test_track_refuses_a_platform_mismatch_and_bad_bodies(env):
    legs = pair(env)
    assert track(env, legs, key="prizepicks")[0] == 400
    assert post(env, "track", {"idem": "x", "legs": legs})[0] == 400


def test_track_rechecks_at_the_click(env):
    legs = pair(env)
    assert check(env, legs)[1]["entries"]["underdog"]["trackable"]
    env.by("vargo").update(line=39.5, graded_line=39.5)
    fresh()
    st, doc = track(env, legs)
    assert st == 409 and "moved" in codes(doc, legs[0])


def test_track_rate_limit(env):
    legs = pair(env)
    for _ in range(workspace_api.TRACK_PER_MIN):
        post(env, "track", {"idem": idem(), "entry_key": "underdog", "legs": [dict(legs[0], side="under", shown_p=None)]})
    assert post(env, "track", {"idem": idem(), "entry_key": "underdog", "legs": legs})[0] == 429


def test_receipts_survive_a_restart(env):
    legs = pair(env)
    assert track(env, legs)[0] == 200
    assert post(env, "state", {"rev": 0, "slip": {"legs": [{"id": "x"}]}, "prefs": {"sort": "chance"}})[0] == 200
    before = get(env, "mine")[1]["receipts"]
    workspace_api.reset()                               # the process is gone: every cache and thread with it
    after = get(env, "mine")[1]
    assert after["receipts"] == before and after["record"]["picks"] == 2
    st = get(env, "state")[1]
    assert st["rev"] == 1 and st["slip"] == {"legs": [{"id": "x"}]} and st["prefs"] == {"sort": "chance"}


def test_writer_saturation_is_503_the_slip_stays_and_the_retry_is_safe(env, monkeypatch):
    legs = pair(env)
    i = idem()
    s = workspace_api.store()
    gate = threading.Event()
    blocked = threading.Thread(target=lambda: s.submit(lambda con: gate.wait(6) and None, wait=8), daemon=True)
    blocked.start()
    time.sleep(0.05)
    monkeypatch.setattr(workspace_store, "WRITE_WAIT", 0.2)
    monkeypatch.setattr(s.submit.__func__, "__defaults__", (0.2,))
    st, doc = post(env, "track", {"idem": i, "entry_key": "underdog", "entry_type": "standard", "legs": legs})
    assert st == 503 and doc == {"error": "busy"}
    gate.set()
    blocked.join()
    assert s._q.join is not None
    time.sleep(0.2)
    st, doc = post(env, "track", {"idem": i, "entry_key": "underdog", "entry_type": "standard", "legs": legs})
    assert st == 200 and doc["ok"]
    assert get(env, "mine")[1]["record"]["picks"] == 2


def test_full_queue_is_busy(tmp_path):
    s = workspace_store.Store(tmp_path / "w.db", queue_max=1, start=False)
    s._q.put_nowait(object())
    with pytest.raises(workspace_store.Busy):
        s.submit(lambda con: 1)
    s._q.get_nowait()
    s.close()


# ---- settlement
def settle(env):
    return workspace_api.store().settle_once()


def tracked(env, legs=None):
    legs = legs or pair(env)
    assert track(env, legs)[0] == 200
    return legs


def status_of(env, nick):
    return next(x for x in get(env, "mine")[1]["receipts"] if x["player"] == nick)


def test_settlement_hit_miss_and_record(env):
    tracked(env)
    env.settle(env.by("vargo"), actual=44.0)
    env.settle(env.by("orlo"), actual=20.0)
    out = settle(env)
    assert out["first"] == 2
    v, o = status_of(env, "vargo"), status_of(env, "orlo")
    assert v["status"] == "hit" and v["final"] == 44 and o["status"] == "miss"
    assert v["timeline"][-1]["text"] == "Settled: Hit. Final 44"
    rec = get(env, "mine")[1]["record"]
    assert (rec["hit"], rec["miss"], rec["push"], rec["void"], rec["pending"]) == (1, 1, 0, 0, 0) and rec["hit_rate"] == 0.5 and rec["matches"] == 2


def test_push_on_a_whole_line_is_apart_and_never_a_win(env):
    r = records(env)
    legs = [leg_of(r[("mika7", "prizepicks")]), leg_of(r[("vargo", "prizepicks")])]
    assert check(env, legs)[1]["entries"]["prizepicks"]["ok"]
    assert track(env, legs, key="prizepicks", entry_type="power")[0] == 200
    env.settle(env.by("mika7", "prizepicks"), status="push", actual=36.0)
    env.settle(env.by("vargo", "prizepicks"), actual=44.0)
    settle(env)
    assert status_of(env, "mika7")["status"] == "push"
    rec = get(env, "mine")[1]["record"]
    assert rec["push"] == 1 and rec["hit"] == 1 and rec["decided"] == 1 and rec["hit_rate"] == 1.0
    assert "exactly on the line" in status_of(env, "mika7")["timeline"][-1]["text"]


def test_void_is_counted_apart(env):
    tracked(env)
    env.settle(env.by("vargo"), status="void", actual=None)
    settle(env)
    v = status_of(env, "vargo")
    assert v["status"] == "void" and v["final"] is None
    rec = get(env, "mine")[1]["record"]
    assert rec["void"] == 1 and rec["decided"] == 0 and rec["hit_rate"] is None


def test_receipts_side_decides_not_the_first_forecast(env):
    r = records(env)
    legs = [leg_of(r[("vargo", "underdog")], side="under"), leg_of(r[("orlo", "underdog")])]
    assert track(env, legs)[0] == 200
    env.settle(env.by("vargo"), actual=20.0)              # the pick row's side is More: this receipt is Under
    settle(env)
    assert status_of(env, "vargo")["status"] == "hit"


def test_one_platforms_settlement_never_settles_another_platforms_leg(env):
    r = records(env)
    assert track(env, [leg_of(r[("vargo", "underdog")]), leg_of(r[("orlo", "underdog")])])[0] == 200
    assert track(env, [leg_of(r[("vargo", "prizepicks")]), leg_of(r[("orlo", "prizepicks")])], key="prizepicks", entry_type="power")[0] == 200
    env.settle(env.by("vargo"), actual=44.0)              # Underdog settled; the PrizePicks row stays rule pending
    env.settle(env.by("vargo", "prizepicks"), status="pending", actual=None)
    settle(env)
    rows = [x for x in get(env, "mine")[1]["receipts"] if x["player"] == "vargo"]
    assert {x["platform"]: x["status"] for x in rows} == {"underdog": "hit", "prizepicks": "pending"}


def test_open_and_held_write_nothing_before_a_first_result(env, tmp_path):
    tracked(env)
    assert settle(env)["first"] == 0
    env.settle(env.by("vargo"), actual=44.0, review=True)  # held for review
    assert settle(env)["first"] == 0 and status_of(env, "vargo")["status"] == "pending"
    assert sqlite3.connect(tmp_path / "workspace.db").execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == 0
    env.settle(env.by("vargo"), actual=44.0)
    assert settle(env)["first"] == 1


@pytest.mark.parametrize("how,text", [("open", "reopened this result"), ("review", "holding this result for review")])
def test_a_hold_or_reopen_takes_the_result_back_and_the_receipt_shows_every_step(env, tmp_path, how, text):
    """The pick log treats these as unresolved, so the receipt and the record do: appended, the first result stays readable."""
    tracked(env)
    env.settle(env.by("vargo"), actual=44.0)
    settle(env)
    assert get(env, "mine")[1]["record"]["hit"] == 1
    if how == "open":
        env.settle(env.by("vargo"), status="open", actual=None)
    else:
        env.settle(env.by("vargo"), actual=44.0, review=True)
    assert settle(env)["corrected"] == 1
    v = status_of(env, "vargo")
    rec = get(env, "mine")[1]["record"]
    assert v["status"] == "pending" and v["final"] is None and v["corrected"] is True and rec["hit"] == 0 and rec["pending"] == 2
    assert [t["kind"] for t in v["timeline"]] == ["tracked", "settled", "correction"] and text in v["timeline"][-1]["text"] and "Settled: Hit" in v["timeline"][1]["text"]
    assert settle(env)["corrected"] == 0                # still held: nothing new to add
    env.settle(env.by("vargo"), actual=44.0)
    assert settle(env)["corrected"] == 1
    v = status_of(env, "vargo")
    assert v["status"] == "hit" and [t["kind"] for t in v["timeline"]] == ["tracked", "settled", "correction", "correction"]
    rows = sqlite3.connect(tmp_path / "workspace.db").execute("SELECT revision, result, supersedes FROM settlements ORDER BY revision").fetchall()
    assert rows == [(1, "hit", None), (2, "reopened", 1), (3, "hit", 2)]


def test_a_missing_pick_row_keeps_the_last_result(env, tmp_path):
    """No row to read is not an answer about the pick: this is the outage case, and the result stays."""
    import picklog
    tracked(env)
    env.settle(env.by("vargo"), actual=44.0)
    settle(env)
    con = sqlite3.connect(picklog.DB)
    con.execute("DELETE FROM picks WHERE id = ?", (env.pick_id(env.by("vargo")),))
    con.commit()
    con.close()
    assert settle(env)["corrected"] == 0 and status_of(env, "vargo")["status"] == "hit"


def test_transient_pick_log_failure_never_settles(env, monkeypatch, tmp_path):
    import picklog
    tracked(env)
    env.settle(env.by("vargo"), actual=44.0)
    monkeypatch.setattr(picklog, "_read_db", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    with pytest.raises(sqlite3.OperationalError):
        settle(env)
    assert sqlite3.connect(tmp_path / "workspace.db").execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == 0


def test_a_correction_is_appended_and_never_overwrites(env, tmp_path):
    tracked(env)
    env.settle(env.by("vargo"), actual=30.0)
    settle(env)
    assert status_of(env, "vargo")["status"] == "miss"
    env.settle(env.by("vargo"), actual=44.0)
    out = settle(env)
    assert out["corrected"] == 1
    v = status_of(env, "vargo")
    assert v["status"] == "hit" and v["corrected"] is True and [t["kind"] for t in v["timeline"]] == ["tracked", "settled", "correction"]
    assert "Final 30" in v["timeline"][1]["text"] and "Correction added" in v["timeline"][2]["text"]
    con = sqlite3.connect(tmp_path / "workspace.db")
    rows = con.execute("SELECT revision, result, actual, supersedes FROM settlements ORDER BY revision").fetchall()
    assert rows == [(1, "miss", 30.0, None), (2, "hit", 44.0, 1)]
    assert settle(env)["corrected"] == 0                # the same answer again adds nothing


@pytest.mark.parametrize("sql", [
    "UPDATE receipts SET line = 1", "DELETE FROM receipts", "UPDATE slips SET legs = 9", "DELETE FROM slips",
    "UPDATE settlements SET result = 'hit'", "DELETE FROM settlements"])
def test_append_only_tables_refuse_changes(env, tmp_path, sql):
    tracked(env)
    env.settle(env.by("vargo"), actual=44.0)
    settle(env)
    con = sqlite3.connect(tmp_path / "workspace.db")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute(sql)


def test_only_cs2_can_be_stored(env, tmp_path):
    workspace_api.store()
    con = sqlite3.connect(tmp_path / "workspace.db")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO receipts(receipt_id, member, slip_id, leg_no, tracked_at, sport, platform, row_id, offer_rev, side, line, line_type, pick_id, forecast_id, "
                    "match_slug, match_key, nick, prop, scope, payload) VALUES('a','m','s',0,'t','nba','underdog','r','o','over',1,'standard',1,1,'m','k','n','kills','12','{}')")


def test_slug_switch_keeps_the_receipt_on_its_own_row_and_tells_the_owner(env):
    import picklog
    tracked(env)
    x = env.by("vargo")
    con = sqlite3.connect(picklog.DB)
    sib = dict(x, id="sib-row")
    pid = env.seed_row(con, sib, slug="slug-renamed")
    con.execute("UPDATE picks SET status = 'settled', actual = 44 WHERE id = ?", (pid,))
    con.commit()
    con.close()
    env.settle(x, status="void", actual=None)
    settle(env)
    assert status_of(env, "vargo")["status"] == "void"
    st, doc = get(env, "diagnostics", user=None, headers={"X-Admin-Token": ADMIN})
    assert st == 200 and doc["void_siblings"][0]["sibling_pick"] == pid and doc["counts"]["receipts"] == 2


def test_diagnostics_are_owner_only(env):
    assert get(env, "diagnostics")[0] == 404


def test_grade_comparison_requires_owner_and_is_read_only(env, monkeypatch, tmp_path):
    assert get(env, 'grade-review?sport=cs2')[0] == 404
    path = tmp_path / 'nonexistent-shadow.db'
    monkeypatch.setattr(server.picklog, 'DB', str(path))
    st, doc = get(env, 'grade-review?sport=cs2', user=None, headers={'X-Admin-Token': ADMIN})
    assert st == 200 and doc['state'] == 'awaiting_capture'
    assert not path.exists()
    assert get(env, 'grade-review?sport=other', user=None, headers={'X-Admin-Token': ADMIN})[0] == 400
    assert get(env, 'grade-review?sport=cs2&before=bad', user=None, headers={'X-Admin-Token': ADMIN})[0] == 400


# ---- /state: two devices
def test_two_devices_stale_rev_is_refused_with_the_current_copy(env):
    assert post(env, "state", {"rev": 0, "slip": {"legs": [{"id": "phone"}]}})[1] == {"rev": 1}
    st, doc = post(env, "state", {"rev": 0, "slip": {"legs": [{"id": "laptop"}]}})
    assert st == 409 and doc["error"] == "stale" and doc["rev"] == 1 and doc["slip"]["legs"] == [{"id": "phone"}]
    merged = {"legs": doc["slip"]["legs"] + [{"id": "laptop"}]}
    assert post(env, "state", {"rev": doc["rev"], "slip": merged})[1] == {"rev": 2}
    got = get(env, "state")[1]
    assert [l["id"] for l in got["slip"]["legs"]] == ["phone", "laptop"] and got["rev"] == 2


def test_state_keeps_fields_not_sent_and_prefs_views_round_trip(env):
    assert post(env, "state", {"rev": 0, "prefs": {"sort": "chance"}, "views": [{"id": "v", "name": "Mine"}]})[0] == 200
    assert post(env, "state", {"rev": 1, "slip": {"legs": []}})[0] == 200
    got = get(env, "state")[1]
    assert got["prefs"] == {"sort": "chance"} and got["views"] == [{"id": "v", "name": "Mine"}] and got["slip"] == {"legs": []}


@pytest.mark.parametrize("body", [{"slip": {}}, {"rev": "1", "slip": {}}, {"rev": True, "slip": {}}, {"rev": 0}, {"rev": 0, "slip": []}, {"rev": 0, "views": {}}, b"nope"])
def test_bad_state_bodies(env, body):
    assert post(env, "state", body)[0] == 400


def test_state_size_and_rate_limits(env):
    assert post(env, "state", {"rev": 0, "prefs": {"x": "y" * 17000}})[0] == 413
    for i in range(workspace_api.STATE_PER_MIN):
        assert post(env, "state", {"rev": i, "prefs": {"n": i}})[0] == 200
    assert post(env, "state", {"rev": workspace_api.STATE_PER_MIN, "prefs": {}})[0] == 429


# ---- /mine
def test_mine_paging_and_conditional_requests(env):
    r = records(env)
    assert track(env, [leg_of(r[("vargo", "underdog")]), leg_of(r[("orlo", "underdog")])])[0] == 200
    st, doc = get(env, "mine?limit=1")
    assert st == 200 and len(doc["receipts"]) == 2 and doc["rev"] == 1 and doc["next_before"] is None   # one slip is never split
    c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=30)
    ck = {"Cookie": f"{whop_auth.COOKIE}=good-u1"}
    c.request("GET", API + "mine", headers=ck)
    a = c.getresponse()
    a.read()
    c.request("GET", API + "mine", headers=dict(ck, **{"If-None-Match": a.getheader("ETag")}))
    b = c.getresponse()
    b.read()
    c.close()
    assert a.status == 200 and b.status == 304 and a.getheader("Cache-Control") == server.MEMBER_CACHE
    assert get(env, "mine?before=!!")[0] == 400


def test_mine_never_carries_ev_or_returns(env):
    tracked(env)
    text = json.dumps(get(env, "mine")[1]).lower()
    assert '"ev"' not in text and "payout" not in text and "return" not in text


def test_private_cache_is_bounded_by_bytes_and_entries():
    c = workspace_api.Lru(n=3, cap=100)
    for i in range(5):
        c.put(i, (b"x" * 10, False, "e", 1))
    assert len(c.d) == 3 and c.get(0) is None and c.get(4) is not None
    c.put("big", (b"x" * 95, False, "e", 1))
    assert c.size <= 100


def test_seven_hundred_members_with_full_pages_fit_the_cache():
    """One /mine and one /state entry each, at the size a 50-receipt page compresses to."""
    c = workspace_api.Lru()
    page = workspace_api._entry(json.dumps([{"player": f"p{i}", "line": i, "timeline": [{"text": "Tracked Over 38.5 at 61.2%"}] * 3, "x": "y" * 200} for i in range(50)]).encode(), 1)
    for m in range(700):
        c.put((f"m{m}", "mine"), page)
        c.put((f"m{m}", "state"), page)
    assert len(c.d) == 1400 and page[1] is True


# ---- open links and EV through the route
def test_open_link_only_behind_its_flag(env, monkeypatch):
    legs = pair(env, "prizepicks")
    assert "open" not in check(env, legs)[1]["entries"]["prizepicks"]
    monkeypatch.setattr(server, "PP_OPEN_LINK", True)
    e = check(env, legs)[1]["entries"]["prizepicks"]
    assert e["open"]["label"] == "Open in PrizePicks" and "projections=7001-o-38.5,7003-o-34.5" in e["open"]["url"]
    u = check(env, pair(env))[1]["entries"]["underdog"]
    assert u["open"]["label"] == "Open Underdog" and u["open"]["url"].startswith("https://app.underdogsports.com/")


def test_open_link_is_never_offered_on_a_refused_entry(env, monkeypatch):
    monkeypatch.setattr(server, "PP_OPEN_LINK", True)
    r = records(env)
    legs = [leg_of(r[("vargo", "prizepicks")]), leg_of(r[("brandr", "prizepicks")])]
    assert "open" not in check(env, legs)[1]["entries"]["prizepicks"]


def calibrated_export():
    rows = []
    for k, (p, n) in enumerate(((0.612, 400), (0.598, 400))):
        for i in range(n):
            rows.append({"match": f"m{k}-{i}", "p": p, "over": 1 if i < round(p * n) else 0, "prop": "kills", "scope": "12", "version": MODEL,
                         "start": "2026-10-20T10:00:00+00:00", "units": 0.5, "forecast_id": k * 1000 + i})
    return {"at": iso(datetime.now(timezone.utc)), "rows": rows, "pushes": [], "excluded": {}}


def test_ev_shows_only_when_every_gate_passes_and_never_on_a_receipt(env, monkeypatch):
    monkeypatch.setattr(server, "_p1_export", calibrated_export)
    legs = pair(env)
    e = check(env, legs)[1]["entries"]["underdog"]
    assert e["ev"].get("available") is True, e["ev"]
    assert e["ev"]["payout"]["multiple"] == 3.5 and e["ev"]["hit_chance"] == round(0.612 * 0.598, 4)
    assert e["ev"]["ev"] == pytest.approx(0.612 * 0.598 * 3.5 - 1, abs=1e-3)
    assert track(env, legs)[0] == 200
    assert '"ev"' not in json.dumps(get(env, "mine")[1]) and '"ev"' not in json.dumps(get(env, "results")[1])


def test_ev_reason_comes_through_the_route(env):
    legs = pair(env)
    e = check(env, legs)[1]["entries"]["underdog"]
    assert e["ev"]["available"] is False and "This model version has" in e["ev"]["reason"] and "of 300 settled matches" in e["ev"]["reason"]
    assert MODEL not in e["ev"]["reason"] and e["ev"]["payout"]["multiple"] == 3.5
    p = check(env, pair(env, "prizepicks"))[1]["entries"]["prizepicks"]
    assert p['ev']['available'] is False
    assert p['ev']['payout']['multiple'] == 3 and p['ev']['payout']['reference_only']
    assert 'final payout' in p['ev']['reason']


def test_a_failed_export_read_is_named_as_that(env, monkeypatch):
    monkeypatch.setattr(server, "_p1_export", lambda: (_ for _ in ()).throw(RuntimeError("learning db locked")))
    e = check(env, pair(env))[1]["entries"]["underdog"]
    assert e["ev"]["available"] is False and e["ev"]["reason"] == "The results export is not available." and e["ev"]["payout"]["multiple"] == 3.5


def test_an_empty_export_is_named_as_that(env, monkeypatch):
    monkeypatch.setattr(server, "_p1_export", lambda: {})
    assert check(env, pair(env))[1]["entries"]["underdog"]["ev"]["reason"] == "The results export is not available."


def test_gate_is_read_once_and_only_when_an_entry_reaches_it(env, monkeypatch):
    calls = []
    real = server._p1_export
    monkeypatch.setattr(server, "_p1_export", lambda: calls.append(1) or real())
    legs, pp = pair(env), pair(env, "prizepicks")
    check(env, pp)
    assert calls == []
    check(env, legs)
    check(env, legs)
    assert calls == [1]


# ---- regressions from the 91bf2bf review
def test_a_scout_side_flip_at_the_same_line_is_refused_until_the_member_takes_the_new_read(env):
    legs = pair(env)
    env.flip(env.by("vargo"))
    fresh()
    st, doc = check(env, legs)
    p = next(a for a in doc["legs"][legs[0]["id"]]["problems"] if a["code"] == "display_mismatch")
    assert p["swap"] is True and "now Under 58.0%" in p["text"] and not doc["entries"]["underdog"]["ok"]
    assert doc["legs"][legs[0]["id"]]["current"]["scout_side"] == "under"
    assert track(env, legs)[0] == 409
    assert get(env, "mine")[1]["record"]["picks"] == 0
    r = records(env)
    took = [leg_of(r[("vargo", "underdog")], side="over"), legs[1]]             # the member keeps Over, knowing Scout moved
    assert track(env, took)[0] == 200
    v = status_of(env, "vargo")
    assert v["side"] == "over" and v["scout_side"] == "under" and v["on_side"] is False and v["displayed_p"] is None


def test_a_chance_shown_for_a_side_scout_left_is_refused(env):
    legs = pair(env)
    r = records(env)
    legs[0] = dict(leg_of(r[("vargo", "underdog")], side="under"), shown_p=0.388)
    assert "display_mismatch" in codes(check(env, legs)[1], legs[0])


def test_a_leg_with_no_read_is_refused(env):
    legs = pair(env)
    del legs[0]["read_rev"]
    assert "display_mismatch" in codes(check(env, legs)[1], legs[0])


def test_a_stake_moved_by_the_same_match_cap_does_not_ask_for_a_new_read(env):
    x = env.by("vargo")
    legs = pair(env)
    x.update(units=0.25, units_full=0.5, cap_n=5)
    fresh()
    assert check(env, legs)[1]["legs"][legs[0]["id"]]["ok"]


def test_a_failed_board_read_stops_check_and_track(env):
    legs = pair(env)
    env.board_ok = False
    fresh()
    doc = check(env, legs)[1]
    p = next(a for a in doc["legs"][legs[0]["id"]]["problems"] if a["code"] == "board_failed")
    assert p["retry"] is True and not doc["entries"]["underdog"]["ok"]
    assert track(env, legs)[0] == 409
    assert get(env, "mine")[1]["record"]["picks"] == 0
    env.board_ok = True
    fresh()
    assert check(env, legs)[1]["entries"]["underdog"]["ok"]


def test_one_failed_book_leaves_the_other_books_legs_alone(env):
    legs = pair(env, "prizepicks")
    real = env.payload

    def half(book="underdog"):
        if book == "underdog":
            raise RuntimeError("underdog board down")
        return real(book)
    server.board_payload = half
    fresh()
    assert check(env, legs)[1]["entries"]["prizepicks"]["ok"]


def test_a_snapshot_that_stopped_rebuilding_is_never_tracked_on(env):
    legs = pair(env)
    assert check(env, legs)[1]["entries"]["underdog"]["ok"]
    workspace_api._SNAP["at"] -= workspace_api.SNAP_MAX_AGE_S + 5
    server._GB.clear()
    server._RESULTS[("ws_board",)] = (time.time(), 200, b"{}") + tuple(server._RESULTS.get(("ws_board",), ())[3:])
    assert "board_failed" in codes(check(env, legs)[1], legs[0])


@pytest.mark.parametrize("status,actual", [("void", None), ("settled", 44.0), ("push", 38.5)])
def test_a_closed_pick_cannot_be_tracked(env, status, actual):
    legs = pair(env)
    env.settle(env.by("vargo"), status=status, actual=actual)
    server._RESULTS.clear()
    doc = check(env, legs)[1]
    assert "pick_closed" in codes(doc, legs[0]) and not doc["entries"]["underdog"]["ok"]
    assert track(env, legs)[0] == 409


def _saved(env, x, **kw):
    import picklog
    con = sqlite3.connect(picklog.DB, timeout=10)
    for k, v in kw.items():
        con.execute(f"UPDATE forecasts SET {k} = ? WHERE pick_id = ?", (v, env.pick_id(x)))
    con.commit()
    con.close()
    server._RESULTS.clear()


@pytest.mark.parametrize("kw", [{"grade": "LEAN"}, {"units": 0.0}, {"grade": "LEAN", "units": 0.0}, {"units": 1.0}])
def test_a_board_grade_or_stake_the_forecast_never_saved_is_refused(env, kw):
    x = env.by("vargo")
    x["graded_at"] = iso(datetime.now(timezone.utc) - timedelta(hours=1))
    legs = pair(env)
    _saved(env, x, **kw)
    doc = check(env, legs)[1]
    assert "forecast_mismatch" in codes(doc, legs[0])
    assert track(env, legs)[0] == 409


def test_a_regrade_still_in_the_writers_queue_says_retry(env):
    x = env.by("vargo")
    legs = pair(env)
    x["graded_at"] = iso(datetime.now(timezone.utc) - timedelta(seconds=20))
    _saved(env, x, units=1.0)
    fresh()
    p = next(a for a in check(env, legs)[1]["legs"][legs[0]["id"]]["problems"] if a["code"] == "forecast_pending")
    assert p["retry"] is True


def test_the_same_match_cap_is_an_allowed_gap_and_the_receipt_keeps_both(env):
    x = env.by("vargo")
    legs = pair(env)
    x.update(units=0.25, units_full=0.5, cap_n=5)
    fresh()
    assert track(env, legs)[0] == 200
    v = status_of(env, "vargo")
    assert v["units"] == 0.25 and v["forecast_units"] == 0.5 and v["forecast_grade"] == "B" and v["forecast_p"] == x["p"]


def test_the_cap_turning_a_row_into_a_zero_stake_lean_is_an_allowed_gap(env):
    x = env.by("vargo")
    legs = pair(env)
    x.update(units=0.0, units_full=0.5, cap_n=9, grade="LEAN")                  # model_grade stays: display gd-1
    fresh()
    assert check(env, legs)[1]["legs"][legs[0]["id"]]["ok"]
    assert track(env, legs)[0] == 200
    v = status_of(env, "vargo")
    assert v["grade"] == "B" and v["units"] == 0.0 and v["forecast_grade"] == "B" and v["forecast_units"] == 0.5


def test_a_capped_row_whose_saved_stake_is_not_the_uncapped_one_is_refused(env):
    x = env.by("vargo")
    x["graded_at"] = iso(datetime.now(timezone.utc) - timedelta(hours=1))
    legs = pair(env)
    x.update(units=0.25, units_full=1.0, cap_n=5)
    fresh()
    assert "forecast_mismatch" in codes(check(env, legs)[1], legs[0])


def test_a_receipt_against_scouts_side_says_whose_grade_it_is(env):
    r = records(env)
    assert track(env, [leg_of(r[("vargo", "underdog")], side="under"), leg_of(r[("orlo", "underdog")])])[0] == 200
    v = status_of(env, "vargo")
    assert v["on_side"] is False and v["scout_side"] == "over" and v["side"] == "under" and v["grade"] == "B" and v["displayed_p"] is None


def renamed(env, x, slug, new_id, **kw):
    """The same line after the slug switch: same canonical match, a new row id, slug and pick."""
    import picklog
    x.update(settle_slug=slug, match_slug=slug, id=new_id, **kw)
    con = sqlite3.connect(picklog.DB, timeout=10)
    env.seed_row(con, x)
    con.commit()
    con.close()
    fresh()


def test_the_same_prop_under_two_slugs_is_one_prop(env):
    legs = pair(env)
    assert track(env, legs)[0] == 200
    renamed(env, env.by("vargo"), "halcyon-vs-norrland", "11111111-0000-4000-8000-0000000000aa")
    r = records(env)
    a, b = leg_of(r[("vargo", "underdog")]), leg_of(r[("raiko", "underdog")])
    st, doc = check(env, [a, b])
    assert "duplicate_tracked" in codes(doc, a) and not doc["entries"]["underdog"]["ok"]
    assert track(env, [a, b])[0] == 409
    rec = get(env, "mine")[1]["record"]
    assert rec["picks"] == 2 and rec["matches"] == 2


def test_the_opposite_side_under_a_new_slug_is_a_conflict(env):
    legs = pair(env)
    assert track(env, legs)[0] == 200
    renamed(env, env.by("vargo"), "halcyon-vs-norrland", "11111111-0000-4000-8000-0000000000aa")
    r = records(env)
    flipped = leg_of(r[("vargo", "underdog")], side="under")
    assert "conflict_tracked" in codes(check(env, [flipped, leg_of(r[("raiko", "underdog")])])[1], flipped)


def test_the_writer_refuses_a_slip_it_cannot_write_whole(env):
    """The check can pass on a stale read: the writer's own test is what keeps a slip from ending up with fewer receipts than legs."""
    s = workspace_api.store()
    legs = pair(env)
    assert track(env, legs)[0] == 200
    mine = get(env, "mine")[1]["receipts"]
    rid, who = mine[0]["id"], workspace_api.member_key("whop:u1")
    row = dict(receipt_id=rid, member=who, slip_id="sl-new", leg_no=0, tracked_at=iso(datetime.now(timezone.utc)), sport="cs2", platform="underdog", row_id="r2", offer_rev="d",
               side="over", line=1.5, line_type="standard", pick_id=1, forecast_id=1, match_slug="other-slug", match_key="hltv:777", nick="someone-else", prop="kills", scope="12",
               payload={})
    other = dict(row, receipt_id="rc-fresh", leg_no=1, nick="third")
    out = s.track({"slip_id": "sl-new", "member": who, "tracked_at": row["tracked_at"], "platform": "underdog", "sports": ["cs2"]}, [row, other])
    assert out["ok"] is False and out["duplicate"][0]["receipt_id"] == rid
    assert s.slip_receipts(who, "sl-new") == [] and s.diagnostics()["counts"]["slips"] == 1


def test_a_slug_only_row_and_a_canonical_row_are_one_match_for_the_opposing_rule_and_ev(env, monkeypatch):
    gate = {"at_ts": time.time(), "cells": {f"{MODEL}|kills|12": {"matches": 300, "bands": [{"lo": 0.57, "hi": 0.70, "printed": 0.6, "observed": 0.6, "ci": [0.55, 0.65]}]}}}
    monkeypatch.setattr(workspace_api, "_gate", lambda srv: gate)
    a = env.by("vargo")
    a.update(settle_slug="hltv-900", match_slug=None, match_id=None)
    con = sqlite3.connect(__import__("picklog").DB, timeout=10)
    env.seed_row(con, a, slug="hltv-900")
    con.commit()
    con.close()
    r = records(env)
    assert r[("vargo", "underdog")]["event"]["id"] == r[("brandr", "underdog")]["event"]["id"] == "hltv:900"
    ud = check(env, [leg_of(r[("vargo", "underdog")]), leg_of(r[("brandr", "underdog")])])[1]["entries"]["underdog"]
    assert ud["ev"]["available"] is False and "share a match" in ud["ev"]["reason"]


def test_a_failed_void_time_costs_one_note_and_never_a_settlement(env):
    import picklog
    tracked(env)
    r = records(env)
    assert track(env, [leg_of(r[("brandr", "underdog")]), leg_of(r[("raiko", "underdog")])], user="u2")[0] == 200
    env.settle(env.by("vargo"), actual=44.0)
    env.settle(env.by("brandr"), status="void", actual=None)
    con = sqlite3.connect(picklog.DB, timeout=10)
    con.execute("UPDATE picks SET match_start = 'TBD' WHERE id = ?", (env.pick_id(env.by("brandr")),))
    con.commit()
    con.close()
    out = settle(env)
    assert out["first"] == 2 and status_of(env, "vargo")["status"] == "hit"
    assert next(x for x in get(env, "mine", user="u2")[1]["receipts"] if x["player"] == "brandr")["status"] == "void"


def test_diagnostics_failing_after_the_writes_lose_no_settlement(env, monkeypatch):
    tracked(env)
    env.settle(env.by("vargo"), status="void", actual=None)
    s = workspace_api.store()
    monkeypatch.setattr(s, "_void_siblings", lambda voids: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    assert settle(env)["first"] == 1 and status_of(env, "vargo")["status"] == "void"


def test_one_bad_pick_row_costs_that_row_only(env, monkeypatch):
    import slip_batches
    tracked(env)
    env.settle(env.by("vargo"), actual=44.0)
    env.settle(env.by("orlo"), actual=20.0)
    real = slip_batches.leg_result_from_picks

    def bad(picks):
        f = real(picks)
        return (lambda leg: (_ for _ in ()).throw(TypeError("bad total"))) if picks[0]["player"] == "orlo" else f
    monkeypatch.setattr(slip_batches, "leg_result_from_picks", bad)
    out = settle(env)
    assert out["first"] == 1 and out["skipped"] == 1 and status_of(env, "vargo")["status"] == "hit"


def many(s, n, pick_id, base, who="wm-other", cols=None):
    """n receipts on one pick, newest at base, written straight to the table the way volume would leave it."""
    rows = [(f"rc-v{pick_id}-{i}", who, f"sl-v{pick_id}-{i}", 0, base, "cs2", "underdog", "r", "d", "over", 30.5, "standard", pick_id, 1, "s", None, "k", f"n{i}", "kills", "12", None, None, 0, "{}")
            for i in range(n)]

    def op(con):
        con.executemany("INSERT INTO receipts(receipt_id, member, slip_id, leg_no, tracked_at, sport, platform, row_id, offer_rev, side, line, line_type, pick_id, forecast_id, match_slug, "
                        "match_id, match_key, nick, prop, scope, start_utc, model_version, scratch, payload) VALUES(" + ",".join("?" * 24) + ")", rows)
        con.execute("INSERT INTO pick_watch(pick_id, mark, active, last_tracked) VALUES(?, NULL, 1, ?) ON CONFLICT(pick_id) DO NOTHING", (pick_id, base))
    s.submit(op, wait=60)


def test_an_older_receipt_settles_under_any_number_of_newer_ones(env):
    tracked(env)
    env.settle(env.by("vargo"), actual=44.0)
    s = workspace_api.store()
    many(s, 5001, env.pick_id(env.by("brandr")), iso(datetime.now(timezone.utc) + timedelta(seconds=2)))
    out = s.settle_once()
    assert out["first"] == 1 and status_of(env, "vargo")["status"] == "hit"


def test_every_pick_gets_its_turn_when_the_list_is_longer_than_a_pass(env, monkeypatch):
    r = records(env)
    assert track(env, [leg_of(r[(n, "underdog")]) for n in ("vargo", "orlo", "brandr", "raiko")][:2])[0] == 200
    assert track(env, [leg_of(r[(n, "underdog")]) for n in ("brandr", "raiko")])[0] == 200
    for n in ("vargo", "orlo", "brandr", "raiko"):
        env.settle(env.by(n), actual=44.0)
    monkeypatch.setattr(workspace_store, "SETTLE_PICKS", 1)
    first = sum(settle(env)["first"] for _ in range(5))
    assert first == 4 and {x["status"] for x in get(env, "mine")[1]["receipts"]} <= {"hit", "miss"}


def test_a_correction_reaches_a_finished_pick_when_its_turn_comes(env, monkeypatch):
    tracked(env)
    env.settle(env.by("vargo"), actual=30.0)
    env.settle(env.by("orlo"), actual=20.0)
    settle(env)
    assert status_of(env, "vargo")["status"] == "miss"
    monkeypatch.setattr(workspace_store, "SETTLE_PICKS", 1)
    env.settle(env.by("vargo"), actual=44.0)
    for _ in range(3):
        settle(env)
    assert status_of(env, "vargo")["status"] == "hit"


def test_an_unchanged_pick_costs_no_receipt_read(env):
    tracked(env)
    assert settle(env)["seen"] == 2                     # the first look at each pick
    assert settle(env)["seen"] == 0                     # nothing moved: a pass reads marks only
    env.settle(env.by("vargo"), actual=44.0)
    assert settle(env)["seen"] == 1                     # one pick moved: only its receipt is read
    assert settle(env)["seen"] == 0


def test_a_receipt_tracked_on_a_finished_pick_is_still_read(env):
    """The closed-pick check reads a 20 s copy, so this can happen once. The pick's old mark must not hide the new receipt."""
    s = workspace_api.store()
    tracked(env)
    x = env.by("vargo")
    env.settle(x, actual=44.0)
    settle(env)
    pid = env.pick_id(x)
    who = workspace_api.member_key("whop:late")
    row = dict(receipt_id="rc-late", member=who, slip_id="sl-late", leg_no=0, tracked_at=iso(datetime.now(timezone.utc) + timedelta(seconds=1)), sport="cs2", platform="underdog", row_id="r",
               offer_rev="d", side="over", line=38.5, line_type="standard", pick_id=pid, forecast_id=1, match_slug="slug-900", match_key="hltv:900", nick="vargo", prop="kills", scope="12", payload={})
    assert s.track({"slip_id": "sl-late", "member": who, "tracked_at": row["tracked_at"], "platform": "underdog", "sports": ["cs2"]}, [row])["ok"]
    assert s.settle_once()["first"] == 1
    assert get(env, "mine", user="late")[1]["receipts"][0]["status"] == "hit"


def test_a_pick_past_its_window_leaves_the_list(env):
    s = workspace_api.store()
    tracked(env)
    assert s.diagnostics()["counts"]["pick_watch"] == 2
    s.settle_once(datetime.now(timezone.utc) + timedelta(days=61))
    assert s.diagnostics()["counts"]["pick_watch"] == 0


def test_a_cached_page_reads_no_database(env, monkeypatch):
    tracked(env)
    assert get(env, "mine")[0] == 200 and get(env, "state")[0] == 200
    s = workspace_api.store()

    def no_read(*a, **k):
        raise AssertionError("a cache hit must not read the database")
    monkeypatch.setattr(s, "reader", no_read)
    assert get(env, "mine")[0] == 200 and get(env, "state")[0] == 200


def test_a_revision_moves_the_moment_its_commit_lands(env):
    who = workspace_api.member_key("whop:u1")
    s = workspace_api.store()
    assert s.rev(who) == 0
    tracked(env)
    assert s.rev(who) == 1 and get(env, "mine")[1]["record"]["picks"] == 2
    env.settle(env.by("vargo"), actual=44.0)
    settle(env)
    assert s.rev(who) == 2 and status_of(env, "vargo")["status"] == "hit"
    assert s.state_rev(who) == 0
    assert post(env, "state", {"rev": 0, "prefs": {"a": 1}})[1] == {"rev": 1}
    assert s.state_rev(who) == 1 and get(env, "state")[1]["prefs"] == {"a": 1}


def test_a_failed_operation_leaves_no_revision_behind(env):
    who = workspace_api.member_key("whop:u1")
    s = workspace_api.store()

    def bad(con):
        s._bump(con, who)
        raise sqlite3.IntegrityError("no")
    with pytest.raises(sqlite3.IntegrityError):
        s.submit(bad)
    assert s.rev(who) == 0


def test_mine_pages_hold_track_slots_open(env):
    """Page reads run at most PAGE_SLOTS at once, so point reads never wait behind a crowd of pages."""
    s = workspace_api.store()
    for _ in range(workspace_store.PAGE_SLOTS):
        assert s._pages.acquire(timeout=1)
    try:
        t0 = time.time()
        assert s.slip_receipts("m", "s") == [] and s.tracked_for("m", ["k"], ["s"]) == [] and time.time() - t0 < 0.5
    finally:
        for _ in range(workspace_store.PAGE_SLOTS):
            s._pages.release()


def test_a_page_with_no_slot_answers_busy(env, monkeypatch):
    s = workspace_api.store()
    monkeypatch.setattr(workspace_store, "PAGE_WAIT", 0.01)
    for _ in range(workspace_store.PAGE_SLOTS):
        s._pages.acquire()
    try:
        assert get(env, "mine") == (503, {"error": "busy"})
    finally:
        for _ in range(workspace_store.PAGE_SLOTS):
            s._pages.release()


def test_history_never_costs_a_page_more_than_a_page(env):
    s = workspace_api.store()
    who = workspace_api.member_key("whop:u1")
    many(s, 300, 1, iso(datetime.now(timezone.utc)), who=who)
    st, doc = get(env, "mine")
    assert st == 200 and doc["record"]["picks"] == 300 and doc["record"]["pending"] == 300
    assert len(doc["receipts"]) <= workspace_api.MINE_PAGE + workspace_store.PAGE_SPILL + 1


def test_older_pages_walk_the_whole_history_once(env):
    s = workspace_api.store()
    who = workspace_api.member_key("whop:u1")
    t0 = datetime.now(timezone.utc)
    for i in range(30):
        many(s, 4, i + 10, iso(t0 - timedelta(seconds=i)), who=who)
    doc, seen, pages = get(env, "mine")[1], [], 0
    while True:
        seen += [r["id"] for r in doc["receipts"]]
        pages += 1
        if not doc["next_before"]:
            break
        doc = get(env, "mine?before=" + quote(doc["next_before"]))[1]
    assert len(seen) == len(set(seen)) == 120 and pages == 3


def test_one_member_cannot_flood_mine_or_empty_anothers_cache(env):
    assert get(env, "mine", user="victim")[0] == 200
    victim = (workspace_api.member_key("whop:victim"), "mine")
    codes_ = [get(env, f"mine?before=2026-01-01T00:00:{i % 60:02d}.{i:06d}", user="flood")[0] for i in range(workspace_api.MINE_PER_MIN + 5)]
    assert codes_.count(429) == 5 and set(codes_) == {200, 429}
    assert victim in workspace_api._PRIVATE.d and len(workspace_api._PRIVATE.d) == 1


def test_a_member_holds_one_cache_entry_per_kind(env):
    for k in range(5):
        assert post(env, "state", {"rev": k, "prefs": {"n": k}})[0] == 200
        assert get(env, "state")[1]["rev"] == k + 1
    assert get(env, "mine?limit=7")[0] == 200
    assert len(workspace_api._PRIVATE.d) == 1


def test_a_client_with_no_gzip_gets_the_same_page(env):
    workspace_api._PRIVATE.clear()
    s = workspace_api.store()
    many(s, 60, 1, iso(datetime.now(timezone.utc)), who=workspace_api.member_key("whop:u1"))
    a = get(env, "mine")
    b = get(env, "mine")
    assert a == b and a[0] == 200 and len(a[1]["receipts"]) >= 50


@pytest.mark.parametrize("field,value", [("line", 10 ** 400), ("line", -10 ** 400), ("shown_p", 10 ** 400), ("line", 1e6), ("shown_p", 1.5), ("shown_p", -0.1)])
def test_crafted_numbers_are_refused_as_bad_requests(env, field, value):
    legs = pair(env)
    legs[0][field] = value
    assert post(env, "recheck", {"idem": idem(), "legs": legs})[0] == 400


def test_an_infinite_number_is_a_bad_request(env):
    legs = pair(env)
    raw = json.dumps({"idem": idem(), "legs": legs}).replace('"line": 38.5', '"line": 1e999', 1).encode()
    assert post(env, "recheck", raw)[0] == 400


def test_deep_nesting_is_a_bad_request(env):
    assert post(env, "recheck", b'{"idem":"idem-ok-12345","legs":' + b"[" * 9000 + b"]" * 9000 + b"}")[0] == 400
    assert post(env, "state", b'{"rev":0,"prefs":' + b"[" * 8100 + b"]" * 8100 + b"}")[0] == 400
    deep = {}
    for _ in range(workspace_api.STATE_MAX_DEPTH + 1):
        deep = {"a": deep}
    assert post(env, "state", {"rev": 0, "prefs": deep})[0] == 400
    assert post(env, "state", {"rev": 0, "prefs": {"chip": "all", "f": {"sort": "start"}}})[0] == 200


def test_two_legs_with_one_id_are_a_bad_request(env):
    legs = pair(env)
    legs[1]["id"] = legs[0]["id"]
    assert post(env, "recheck", {"idem": idem(), "legs": legs})[0] == 400


def test_context_labels_say_what_the_saved_input_is(env, monkeypatch):
    import picklog
    x = env.by("vargo")
    blob = zlib.compress(json.dumps({"kpr_last5": 0.7, "recent_avg": 40.1, "recent_median": 39.0, "recent_n": 10}).encode())
    con = sqlite3.connect(picklog.DB, timeout=10)
    con.execute("UPDATE forecasts SET inputs = ? WHERE pick_id = ?", (blob, env.pick_id(x)))
    con.commit()
    con.close()
    monkeypatch.setattr(server.hltv, "recent_series", lambda *a, **k: [])
    labels = {r["key"]: r["label"] for r in get(env, f"line?book=underdog&id={x['id']}")[1]["context"]["rows"]}
    assert labels == {"kpr_last5": "Kills per round, last 10 matches, Maps 1-2", "recent_avg": "Recent average, Maps 1-2", "recent_median": "Recent median, Maps 1-2",
                      "recent_n": "Recent matches, Maps 1-2"}


def test_rules_used_carry_their_note(env):
    e = check(env, pair(env, "prizepicks"))[1]["entries"]["prizepicks"]
    same = next(r for r in e["rules"] if r["rule"] == "same_player")
    assert "Stacks" in same["text"] and "carry no Stacks" in same["note"] and same["source"] == workspace_rules.PP_PICKS


def test_me_says_owner_for_the_allow_list_and_never_for_a_member(env, monkeypatch):
    assert get(env, "me")[1]["owner"] is False
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id("boss"))
    assert get(env, "me", user="boss")[1]["owner"] is True and get(env, "me")[1]["owner"] is False


# ---- what this code may not do
BANNED = ("cs2_scout.grade", "log_async", "write_row", "_set_result", "log_loaded", "/api/grade")


@pytest.mark.parametrize("name", ["workspace_api.py", "workspace_store.py", "workspace_rules.py"])
def test_new_code_never_grades_or_logs(name):
    src = (ROOT / name).read_text(encoding="utf-8")
    for bad in BANNED:
        assert bad not in src, bad
    tree = ast.parse(src)
    imports = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imports |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module}
    assert not imports & {"lol", "sports", "server", "cs2_scout", "grader"}, imports


def test_client_never_calls_the_grader():
    for f in ("workspace.js", "workspace.html", "workspace-data.js"):
        assert "/api/grade" not in (ROOT / "web" / f).read_text(encoding="utf-8")


WORDING = [r"financial advice", r"betting advice", r"not advice", r"not validated", r"unvalidated", r"unconfirmed", r"not confirmed", r"provisional", r"tentative",
           r"uncertain", r"unsure", r"\bmaybe\b", r"hypothetical", r"\broi\b", r"profit", r"win[- ]rate", r"guarantee", r"no bet\b", r"\bmore\b(?= \d)", r"\bless\b(?= \d)"]


@pytest.mark.parametrize("bad", WORDING)
def test_owner_wording_rules_cover_server_text(bad):
    for name in ("workspace_rules.py", "workspace_api.py", "workspace_store.py"):
        text = "\n".join(s.value for s in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))) if isinstance(s, ast.Constant) and isinstance(s.value, str))
        text = re.sub(r'"""[\s\S]*?"""', "", text)
        doc = ast.get_docstring(ast.parse((ROOT / name).read_text(encoding="utf-8"))) or ""
        assert not re.search(bad, text.replace(doc, ""), re.I), (name, bad)


def test_model_hash_files_are_untouched():
    import subprocess
    files = ["grader.py", "sim.py", "cs2_scout.py", "hltv.py", "perf.py", "perf_live.py", "scope.py", "hltv_store.py", "market_odds.py", "thunderpick.py", "pick_verdict.py",
             "bo3.py", "roles.py", "polymarket.py", "oddspapi.py", "cm1.py", "cm2.py", "cm2_model.json", "platform_rules.py", "learning.py"]
    if subprocess.run(["git", "cat-file", "-e", "4877218"], cwd=ROOT, capture_output=True).returncode != 0:
        pytest.skip("base commit not in this clone (shallow checkout)")
    assert subprocess.run(["git", "diff", "--exit-code", "--quiet", "4877218", "--", *files], cwd=ROOT).returncode == 0
    # Only the disabled-by-default shadow observer is added to the immutable
    # forecast writer. Everything else must still equal the frozen baseline.
    baseline_picklog = subprocess.run(['git', 'show', '4877218:picklog.py'], cwd=ROOT,
                                     capture_output=True, text=True, encoding='utf-8', check=True).stdout
    class WithoutShadow(ast.NodeTransformer):
        def visit_If(self, node):
            if ast.unparse(node.test) == "os.getenv('SCOUT_GRADECORE_SHADOW', '0') == '1'":
                assert len(node.body) == 2
                assert isinstance(node.body[0], ast.ImportFrom) and node.body[0].module == 'gradecore.capture'
                assert isinstance(node.body[1], ast.Expr) and isinstance(node.body[1].value, ast.Call)
                assert isinstance(node.body[1].value.func, ast.Name) and node.body[1].value.func.id == 'capture'
                assert not node.orelse
                return None
            return self.generic_visit(node)
    current_picklog = WithoutShadow().visit(ast.parse((ROOT / 'picklog.py').read_text(encoding='utf-8')))
    assert ast.dump(current_picklog) == ast.dump(ast.parse(baseline_picklog))
    # Results' scoring/selection rules stay frozen too. Only the bootstrap's
    # implementation is optimized; exact seeded equivalence is exercised in
    # test_results_readonly.py against the original expanded resampling.
    baseline = subprocess.run(["git", "show", "4877218:results_view.py"], cwd=ROOT,
                              capture_output=True, text=True, encoding="utf-8", check=True).stdout

    def rules_only(source):
        tree = ast.parse(source)
        tree.body = [n for n in tree.body if not (isinstance(n, ast.FunctionDef) and n.name == "_ci")]
        return ast.dump(tree)

    assert rules_only((ROOT / "results_view.py").read_text(encoding="utf-8")) == rules_only(baseline)


def test_a_mine_page_never_queues_behind_point_reads(tmp_path):
    # final check 2026-11-02: 700 members, 200 receipts each: a page holding its slot still timed out on a read slot (503)
    import workspace_store as ws
    st = ws.Store(tmp_path / "w.db")
    held = [st._open() for _ in range(ws.READ_SLOTS)]
    st._n_readers = ws.READ_SLOTS
    try:
        t0 = time.time()
        assert st.mine("m" * 16)["receipts"] == []
        assert time.time() - t0 < ws.READ_WAIT
    finally:
        for con in held:
            con.close()
        st.close()
