"""The workspace API over real HTTP on 127.0.0.1 with server.Handler: every route, every refusal, tracking, restart persistence,
settlement, two devices and the gate. Fixture board, feeds and pick log only (tests/workspace_fixture.py)."""
import ast
import http.client
import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

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
GET_ROUTES = ("me", "board", "line?book=underdog&id=x", "mine", "results", "state", "rules", "diagnostics")
POST_ROUTES = ("recheck", "track", "state")


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
    assert st == 200 and doc["sample_sports"] == ["lol", "nba", "nfl"] and doc["rules_version"] == workspace_rules.RULES_VERSION
    assert doc["member"].startswith("wm-") and "u1" not in doc["member"] and doc["scratch"] is False


def test_rules_carry_a_source_and_date_for_each_combination(env):
    st, doc = get(env, "rules")
    assert st == 200 and doc["version"] == workspace_rules.RULES_VERSION
    keys = {r["key"] for r in doc["rules"]}
    assert {"entry_size", "two_teams", "same_player", "both_sides", "mixed_sports", "opposing_players", "goblins_demons", "two_platforms"} <= keys
    for r in doc["rules"]:
        for p in ("prizepicks", "underdog"):
            assert r[p]["read_at"] == "2026-11-02" and r[p]["status"] in ("published", "not_published")
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


def sample_leg(book="underdog", sport="nba", team="PRM", player="Marcus Delvane"):
    return {"id": f"s-{sport}-{player}-{book}", "sport": sport, "book": book, "row_id": "pp-1001", "side": "over", "line": 25.5, "rev": "x", "shown_p": 0.54,
            "team": team, "player": player, "event": f"{sport}-e1"}


def test_mixed_sports_prizepicks_is_valid_but_sample_legs_cannot_be_tracked(env):
    r = records(env)
    legs = [leg_of(r[("vargo", "prizepicks")]), sample_leg("prizepicks")]
    st, doc = check(env, legs)
    e = doc["entries"]["prizepicks"]
    assert e["status"] == "valid" and e["ok"] and not e["trackable"] and e["track_note"] == "NBA legs are sample data." and e["sports"] == ["cs2", "nba"]
    assert doc["legs"][legs[1]["id"]]["sample"] is True
    assert track(env, legs, key="prizepicks", entry_type="power")[0] == 409


def test_mixed_sports_underdog_is_not_offered_and_says_why(env):
    r = records(env)
    legs = [leg_of(r[("vargo", "underdog")]), sample_leg("underdog")]
    e = check(env, legs)[1]["entries"]["underdog"]
    assert e["status"] == "not_published" and "does not publish whether one entry can hold CS2 and NBA picks" in e["problems"][0]["text"]


def test_two_platforms_make_two_entries(env):
    r = records(env)
    doc = check(env, [leg_of(r[("vargo", "underdog")]), leg_of(r[("orlo", "prizepicks")])])[1]
    assert set(doc["entries"]) == {"underdog", "prizepicks"}


def test_unknown_platform_is_not_offered(env):
    legs = [sample_leg("fanduel"), sample_leg("fanduel", team="SFC", player="Theo Lind")]
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


def test_recheck_rate_limit_and_retry_is_free(env):
    legs = pair(env)
    i = idem()
    for _ in range(3):
        assert post(env, "recheck", {"idem": i, "legs": legs})[0] == 200
    for _ in range(workspace_api.RECHECK_PER_MIN - 1):
        assert post(env, "recheck", {"idem": idem(), "legs": legs})[0] == 200
    assert post(env, "recheck", {"idem": idem(), "legs": legs})[0] == 429


def test_board_down_with_nothing_read_answers_503(env):
    env.board_ok = False
    assert post(env, "recheck", {"idem": idem(), "legs": [sample_leg()]})[0] == 503


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


def test_open_held_and_outage_write_nothing_and_a_reopen_keeps_the_result(env, tmp_path):
    tracked(env)
    assert settle(env)["first"] == 0
    env.settle(env.by("vargo"), actual=44.0, review=True)  # held for review
    assert settle(env)["first"] == 0 and status_of(env, "vargo")["status"] == "pending"
    env.settle(env.by("vargo"), actual=44.0)
    assert settle(env)["first"] == 1
    env.settle(env.by("vargo"), status="open", actual=None)
    out = settle(env)
    assert out["corrected"] == 0 and out["first"] == 0 and status_of(env, "vargo")["status"] == "hit"
    assert sqlite3.connect(tmp_path / "workspace.db").execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == 1


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
                    "match_slug, nick, prop, scope, payload) VALUES('a','m','s',0,'t','nba','underdog','r','o','over',1,'standard',1,1,'m','n','kills','12','{}')")


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
        c.put(i, (b"x" * 10, None, "e"))
    assert len(c.d) == 3 and c.get(0) is None and c.get(4) is not None
    c.put("big", (b"x" * 90, b"y" * 5, "e"))
    assert c.size <= 100


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
    assert e["ev"]["available"] is False and "of 300 settled matches" in e["ev"]["reason"] and e["ev"]["payout"]["multiple"] == 3.5
    p = check(env, pair(env, "prizepicks"))[1]["entries"]["prizepicks"]
    assert p["ev"] == {"available": False, "reason": "PrizePicks shows lineup payouts only in its app."}


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
             "bo3.py", "roles.py", "polymarket.py", "oddspapi.py", "cm1.py", "cm2.py", "cm2_model.json", "picklog.py", "platform_rules.py", "results_view.py", "learning.py"]
    if subprocess.run(["git", "cat-file", "-e", "4877218"], cwd=ROOT, capture_output=True).returncode != 0:
        pytest.skip("base commit not in this clone (shallow checkout)")
    assert subprocess.run(["git", "diff", "--exit-code", "--quiet", "4877218", "--", *files], cwd=ROOT).returncode == 0
