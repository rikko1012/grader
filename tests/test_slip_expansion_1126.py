"""Soccer and tennis in Auto-build and on slips (owner 2026-11-26 "add soccer & tennis to the auto builder"), over real HTTP with
server.Handler (tests/test_workspace_all_sports.py's env). The sports service is a stub at workspace_sports.call: a soccer / tennis
board with saved forecasts, the settle route and the settled-record route. No network and no production data."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

import server
import slip_quality as sq
import workspace_api
import workspace_expansion as adapter
import workspace_sports
from tests.test_workspace_all_sports import API, env  # noqa: F401  (env is the fixture)
from tests.test_workspace_api import call

GRADES = ("A+", "A", "A-", "B+", "Lean", "A")         # the last one is offered on one side only


def _fkey(o):
    return hashlib.sha256(json.dumps([o[k] for k in ("sport", "platform", "offer_id", "revision")], separators=(",", ":")).encode()).hexdigest()


def board_doc(sport, now):
    offers, research, forecasts = [], {}, {}
    for i, grade in enumerate(GRADES):
        one_sided = i == len(GRADES) - 1
        o = {"sport": sport, "platform": "prizepicks", "offer_id": str(9100 + i), "revision": f"rev-{sport}-{i}",
             "player_id": f"n-player-{i}", "event_id": f"n-event-{i}", "player": f"{sport.title()} Player {i}",
             "team": f"Club {i}" if sport == "soccer" else "", "opponent": f"Rival {i}", "role": "Forward" if sport == "soccer" else "",
             "match": f"Match {i}", "stat": "shots" if sport == "soccer" else "aces", "label": "Shots" if sport == "soccer" else "Aces",
             "scope": "full_game", "variant": "standard", "line": 2.5,
             "start": (now + timedelta(hours=3 + i)).isoformat(), "observed_at": now.isoformat(),
             "sides": {"over": {}} if one_sided else {"over": {}, "under": {}}}
        ident = {"event_id": f"{sport}:event:{i}", "player_id": f"{sport}:player:{i}", "competition": "eng.1" if sport == "soccer" else "atp"}
        games = [{"event_id": f"{sport}:old:{i}:{g}", "date": (now - timedelta(days=g + 1)).isoformat(), "opponent": "Old", "value": g % 5,
                  "source": "espn", "known_at": now.isoformat()} for g in range(10)]
        research[adapter._key(o)] = {"identity": ident, "scope": "full_game", "games": games, "stats": [], "context": {}}
        p = 0.74 - 0.03 * i if grade != "Lean" else 0.53
        side = "over" if i % 2 == 0 or one_sided else "under"
        po, pu = (p, 1 - p) if side == "over" else (1 - p, p)
        forecasts[_fkey(o)] = {"id": f"fc-{sport}-{i}", "identity": ident, "sport": sport, "stat": o["stat"], "scope": o["scope"], "line": o["line"],
                               "platform": "prizepicks", "offer_id": o["offer_id"], "offer_revision": o["revision"], "start": o["start"],
                               "made_at": (now - timedelta(minutes=5)).isoformat(), "p_over": po, "p_under": pu, "p_push": 0, "p_side": p,
                               "side": side, "projection": 3.4 if side == "over" else 1.6, "range80": [1, 5], "grade": grade, "units": 0.0,
                               "sample": 10, "model_version": "fixture-exp-1", "grade_reason": [], "context_used": {}}
        offers.append(o)
    return {"sport": sport, "offers": offers, "research": research, "research_at": now.isoformat(), "built_at": now.isoformat(),
            "forecasts": forecasts, "forecasts_at": now.isoformat(), "game_odds": [],
            "sources": {"prizepicks": {"ok": True, "checked_at": now.isoformat()}}}


@pytest.fixture
def exp(env, monkeypatch):
    monkeypatch.setenv("SCOUT_SOCCER_ENABLED", "1")
    monkeypatch.setenv("SCOUT_TENNIS_ENABLED", "1")
    monkeypatch.setattr(server.Handler, "_sports_owner", lambda self: True)
    now = datetime.now(timezone.utc)
    state = {"docs": {s: board_doc(s, now) for s in adapter.SPORTS}, "settle": {}, "record": {}, "calls": []}
    original = workspace_sports.call

    def stub(srv, path, query=None, **kw):
        if path == "/api/sports/expansion/board":
            return state["docs"][query["sport"]], None
        if path == "/api/sports/expansion/settle":
            state["calls"].append(query)
            ids = query["ids"].split(",")
            return {"sport": query["sport"], "results": {k: v for k, v in state["settle"].items() if k in ids}}, None
        if path == "/api/sports/expansion/record":
            if query["sport"] not in state["record"]:
                raise workspace_sports.Down("no record")
            return {"sport": query["sport"], "bands": state["record"][query["sport"]], "forecasts": 1}, None
        return original(srv, path, query, **kw)
    monkeypatch.setattr(workspace_sports, "call", stub)
    adapter.reset()
    workspace_api.reset()
    env.exp = state
    return env


def gen(fx, **q):
    q = dict({"platform": "prizepicks", "type": "power", "slips": 6, "picks": 2, "sports": "soccer,tennis"}, **q)
    return call(fx, "GET", API + "generate?" + "&".join(f"{k}={v}" for k, v in q.items()), user="u1")


def board(fx, sport):
    st, b = call(fx, "GET", API + f"board?sport={sport}", user="u1")
    assert st == 200, b
    return {(r["offer"]["platform"], r["offer"]["offer_id"]): r for r in b["records"]}


def wire(r, side):
    """The leg the page sends (web/upgrade.js makeLeg + wireLeg)."""
    p = r["scout"]["p_over"] if side == "over" else r["scout"]["p_under"]
    return {"id": "|".join(str(x) for x in (r["sport"], r["event"]["id"], r["player"]["id"], r["market"]["stat"], r["market"]["period"],
                                            r["offer"]["platform"], side, r["offer"]["line"])),
            "sport": r["sport"], "book": r["offer"]["platform"], "row_id": r["offer"]["offer_id"], "side": side, "line": r["offer"]["line"],
            "rev": r["offer"]["revision"], "read_rev": r["scout"]["read_rev"], "shown_p": p, "team": r["player"]["team"],
            "player": r["player"]["name"], "event": r["event"]["id"]}


def test_generate_builds_soccer_and_tennis_slips_from_lettered_two_sided_legs(exp):
    st, doc = gen(exp)
    assert st == 200, doc
    assert doc["slips"], doc
    legs = [l for s in doc["slips"] for l in s["legs"]]
    assert {l["sport"] for l in legs} == {"soccer", "tennis"}                   # tennis legs enter although the feed gives no team
    boards = {s: board(exp, s) for s in adapter.SPORTS}
    for l in legs:
        r = boards[l["sport"]][(l["book"], l["row_id"])]
        assert r["scout"]["grade"] in sq.EXPANSION_GRADES                       # never a Lean
        assert r["offer"]["over"]["offered"] and r["offer"]["under"]["offered"]   # never a one-sided line
        assert l["side"] == r["scout"]["side"]
        assert l["record"] == "No settled record for this band yet"            # no settled record: no number invented
        # research weight: a 74% statement ranks at 62%, never above its own chance
        assert l["rank_p"] == pytest.approx(0.5 + (l["p"] - 0.5) * sq.RESEARCH_WEIGHT, abs=1e-4)
    assert doc["pool"]["excluded"].get("grade") == 2 and doc["pool"]["excluded"].get("side") == 2


def test_soccer_tennis_rank_by_their_settled_band_when_the_record_is_up(exp):
    exp.exp["record"]["soccer"] = [{"name": "65%+", "wins": 40, "losses": 60, "pushes": 0, "printed": 0.71}]
    workspace_api.reset()
    st, doc = gen(exp, sports="soccer", slips=2)
    assert st == 200, doc
    top = [l for s in doc["slips"] for l in s["legs"] if l["p"] >= 0.65]
    assert top, doc
    for l in top:
        assert l["record"] == "Soccer picks stated 65%+: won 40.0% of 100 settled"
        base = 0.5 + (l["p"] - 0.5) * sq.RESEARCH_WEIGHT
        assert l["rank_p"] == pytest.approx((100 * 0.4 + sq.PRIOR_K * base) / (100 + sq.PRIOR_K), abs=1e-4)


def test_unticked_or_unseen_sports_stay_out(exp, monkeypatch):
    st, doc = gen(exp, sports="cs2")
    assert st == 200 and all(l["sport"] == "cs2" for s in doc["slips"] for l in s["legs"])
    monkeypatch.setenv("SCOUT_TENNIS_ENABLED", "0")
    st, doc = gen(exp, sports="tennis")
    assert st == 400 and doc == {"error": "Choose at least one sport."}


def test_recheck_track_and_settle_a_soccer_tennis_slip(exp):
    st, doc = gen(exp, slips=1)
    assert st == 200 and doc["slips"], doc
    slip = doc["slips"][0]
    boards = {s: board(exp, s) for s in adapter.SPORTS}
    legs = [wire(boards[l["sport"]][(l["book"], l["row_id"])], l["side"]) for l in slip["legs"]]
    st, chk = call(exp, "POST", API + "recheck", {"idem": "idem-exp-1", "legs": legs, "entry_types": {"prizepicks": "power"}}, user="u1")
    assert st == 200, chk
    assert all(chk["legs"][l["id"]]["ok"] for l in legs), chk
    e = chk["entries"]["prizepicks"]
    assert e["ok"], e
    assert e["ev"]["available"] is False                                       # soccer / tennis chances have no settled EV check

    st, tr = call(exp, "POST", API + "track", {"idem": "idem-exp-2", "legs": legs, "entry_key": "prizepicks", "entry_type": "power"}, user="u1")
    assert st == 200 and tr["ok"], tr
    ids = [rc["id"] for rc in tr["receipts"]]
    with workspace_api.store().reader(wait=5.0) as c:
        saved = {r["receipt_id"]: dict(r) for r in c.execute(f"SELECT * FROM receipts WHERE receipt_id IN ({','.join('?' * len(ids))})", ids)}
        watch = {r["settle_key"]: dict(r) for r in c.execute("SELECT * FROM leg_watch")}
    rows = []
    for rid, l in zip(ids, legs):
        rc, r = saved[rid], boards[l["sport"]][(l["book"], l["row_id"])]
        assert rc["sport"] == l["sport"] and rc["forecast_id"] == r["scout"]["read_rev"]
        assert rc["settle_key"] == f"{l['sport']}|{rc['forecast_id']}|{l['side']}|{l['line']}"
        assert json.loads(rc["payload"])["units"] == 0 and json.loads(rc["payload"])["grade"] == r["scout"]["grade"]
        w = watch[rc["settle_key"]]
        assert w["sport"] == l["sport"]
        rows.append({"settle_key": rc["settle_key"], "start_utc": "2020-01-01T00:00:00Z", "ref": json.loads(w["ref"])})

    # settlement: each leg from its own forecast's review, at its own line and side; no review = pending
    soccer = [w for w in rows if w["ref"]["sport"] == "soccer"]
    for w in soccer:
        exp.exp["settle"][w["ref"]["forecast_id"]] = {"actual": 4 if w["ref"]["side"] == "over" else 1, "line": 2.5, "side": w["ref"]["side"],
                                                      "review_id": "rv1", "known_at": "2026-11-26T22:00:00Z"}
    got = adapter.settle(server, "soccer", soccer)
    assert {k: v["result"] for k, v in got.items()} == {w["settle_key"]: "hit" for w in soccer}
    tennis = [w for w in rows if w["ref"]["sport"] == "tennis"]
    assert adapter.settle(server, "tennis", tennis) == {}                      # no review yet: no answer, never a miss


def test_settle_rules_push_miss_other_line_and_not_started():
    rows = [{"settle_key": "a", "start_utc": "2020-01-01T00:00:00Z", "ref": {"forecast_id": "f1", "side": "over", "line": 2.5}},
            {"settle_key": "b", "start_utc": "2020-01-01T00:00:00Z", "ref": {"forecast_id": "f2", "side": "under", "line": 3.0}},
            {"settle_key": "c", "start_utc": "2020-01-01T00:00:00Z", "ref": {"forecast_id": "f3", "side": "over", "line": 1.5}},
            {"settle_key": "d", "start_utc": "2999-01-01T00:00:00Z", "ref": {"forecast_id": "f4", "side": "over", "line": 1.5}}]
    answers = {"f1": {"actual": 2, "line": 2.5, "review_id": "x"}, "f2": {"actual": 3, "line": 3.0, "review_id": "y"},
               "f3": {"actual": 9, "line": 2.5, "review_id": "z"}, "f4": {"actual": 9, "line": 1.5, "review_id": "w"}}
    asked = []

    def fake(srv, path, query=None, **kw):
        asked.append(query["ids"].split(","))
        return {"results": {k: v for k, v in answers.items() if k in query["ids"].split(",")}}, None
    orig = workspace_sports.call
    workspace_sports.call = fake
    try:
        got = adapter.settle(server, "soccer", rows)
    finally:
        workspace_sports.call = orig
    assert {k: v["result"] for k, v in got.items()} == {"a": "miss", "b": "push"}   # c: made at another line; d: not started
    assert asked == [["f1", "f2", "f3"]]                                            # in page order (open tier first)
    assert got["a"]["final"] and got["a"]["mark"] != got["b"]["mark"]


def test_one_sided_and_lean_legs_fail_recheck_with_member_reasons(exp):
    b = board(exp, "soccer")
    one = next(r for r in b.values() if not r["offer"]["under"]["offered"])
    st, chk = call(exp, "POST", API + "recheck", {"idem": "idem-exp-3", "legs": [wire(one, "over")], "entry_types": {}}, user="u1")
    assert st == 200
    probs = chk["legs"][wire(one, "over")["id"]]["problems"]
    assert any(p["text"] == "Over only: Grader grades lines that offer both sides." for p in probs), probs


def test_a_history_only_line_is_not_slip_ok_and_fails_recheck(exp):
    exp.exp["docs"]["tennis"]["forecasts"] = {}
    adapter.reset()
    r = next(iter(board(exp, "tennis").values()))
    assert r["research_only"] and not r.get("slip_ok") and r["scout"]["read_rev"] == "history-only"
    leg = dict(wire(r, "over"), shown_p=None)
    st, chk = call(exp, "POST", API + "recheck", {"idem": "idem-exp-4", "legs": [leg], "entry_types": {}}, user="u1")
    assert st == 200
    assert [p["code"] for p in chk["legs"][leg["id"]]["problems"]] == ["no_forecast"]


def test_store_migrates_a_four_sport_receipts_table_without_losing_rows(tmp_path):
    """A workspace.db written before soccer / tennis (receipts CHECK took cs2/lol/nba/nfl): opening it rebuilds the table in one
    transaction, keeps every row, and the append-only triggers still refuse edits."""
    import sqlite3
    import workspace_store
    old = workspace_store.RECEIPTS_DDL.replace(",'soccer','tennis'", "")
    assert "'tennis'" not in old
    path = tmp_path / "workspace.db"
    con = sqlite3.connect(path)
    con.executescript(old)
    con.execute("INSERT INTO receipts(receipt_id, member, slip_id, leg_no, tracked_at, sport, platform, row_id, offer_rev, side, line, line_type, "
                "forecast_id, match_slug, match_key, nick, prop, scope, payload, settle_key) VALUES('rc-1','m','sl-1',0,'2026-11-01T00:00:00Z','nba',"
                "'prizepicks','r1','v1','over',20.5,'standard','f1','g1','g1','p1','pts','full_game','{}','nba|prizepicks|r1|over|20.5')")
    con.commit()
    con.close()
    st = workspace_store.Store(path)
    try:
        with st.reader(wait=5.0) as c:
            assert "'tennis'" in c.execute("SELECT sql FROM sqlite_master WHERE name = 'receipts'").fetchone()[0]
            assert [r[0] for r in c.execute("SELECT receipt_id FROM receipts")] == ["rc-1"]
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('trigger', 'index') AND tbl_name = 'receipts'")}
        assert {"receipts_no_update", "receipts_no_delete", "receipts_settle"} <= names
        c = sqlite3.connect(path)
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE receipts SET line = 1")
        c.close()
    finally:
        st.close()


def test_700_members_generate_and_recheck_soccer_tennis_at_once(exp, monkeypatch):
    """700 members press Generate (soccer + tennis) and recheck a soccer + tennis slip at once: no 5xx, one shared board read per
    sport per snapshot (never per member), one shared batch per option set."""
    import gzip
    import http.client
    import time
    from concurrent.futures import ThreadPoolExecutor
    from http.server import ThreadingHTTPServer
    import whop_auth
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)
    st, doc = gen(exp, slips=1)
    assert st == 200 and doc["slips"], doc
    boards = {s: board(exp, s) for s in adapter.SPORTS}
    legs = [wire(boards[l["sport"]][(l["book"], l["row_id"])], l["side"]) for l in doc["slips"][0]["legs"]]
    body = json.dumps({"idem": "idem-load", "legs": legs, "entry_types": {"prizepicks": "power"}}).encode()
    reads = {"n": 0}
    stub = workspace_sports.call

    def counting(srv, path, query=None, **kw):
        if path == "/api/sports/expansion/board":
            reads["n"] += 1
        return stub(srv, path, query, **kw)
    monkeypatch.setattr(workspace_sports, "call", counting)
    workspace_api.reset()
    server._RESULTS.clear()                      # reset() empties the adapters' snapshots; the shared cache must go with them
    import traceback
    real_build = adapter._build
    errs = []

    def logged(srv, sport):
        try:
            return real_build(srv, sport)
        except Exception:
            errs.append(traceback.format_exc())
            raise
    monkeypatch.setattr(adapter, "_build", logged)

    def member(i):
        h = http.client.HTTPConnection("127.0.0.1", exp.port, timeout=120)
        cookie = {"Cookie": f"{whop_auth.COOKIE}=good-m{i}", "Accept-Encoding": "gzip"}
        out = []
        try:
            h.request("GET", API + "generate?platform=prizepicks&type=power&slips=6&picks=2&sports=soccer,tennis", headers=cookie)
            x = h.getresponse(); x.read(); out.append(x.status)
            h.request("POST", API + "recheck", body=body, headers=dict(cookie, **{"Content-Type": "application/json"}))
            x = h.getresponse(); raw = x.read(); out.append(x.status)
            got = json.loads(gzip.decompress(raw) if x.getheader("Content-Encoding") == "gzip" else raw)
            out.append(True if x.status == 200 and all(got["legs"][l["id"]]["ok"] for l in legs) else [p for l in legs for p in got["legs"][l["id"]]["problems"]])
        finally:
            h.close()
        return out
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=700) as ex:
        res = list(ex.map(member, range(700)))
    took = time.time() - t0
    assert not errs, errs[:1]
    codes = [c for r in res for c in r[:2]]
    assert codes.count(200) == len(codes), {k: codes.count(k) for k in set(codes)}
    bad = [r[2] for r in res if r[2] is not True]
    assert not bad, (len(bad), bad[:3])
    assert reads["n"] <= 2 * len(adapter.SPORTS) * (1 + int(took // 20)), (reads, took)    # shared snapshot (ttl 20 s), never per member
    assert took < 150, took


def _tracked(fx):
    st, doc = gen(fx, slips=1)
    assert st == 200 and doc["slips"], doc
    boards = {s: board(fx, s) for s in adapter.SPORTS}
    legs = [wire(boards[l["sport"]][(l["book"], l["row_id"])], l["side"]) for l in doc["slips"][0]["legs"]]
    st, tr = call(fx, "POST", API + "track", {"idem": "idem-exp-s", "legs": legs, "entry_key": "prizepicks", "entry_type": "power"}, user="u1")
    assert st == 200 and tr["ok"], tr
    return legs, boards


def test_settler_pass_settles_soccer_tennis_receipts_in_my_picks(exp):
    """The store's own settle pass (workspace_api._sources): a started soccer / tennis leg settles from its forecast's review; a leg
    with no review stays pending; My picks shows the result and the frozen 0U letter."""
    import sqlite3
    legs, boards = _tracked(exp)
    con = sqlite3.connect(workspace_api.store().path)
    con.execute("UPDATE leg_watch SET start_utc = '2026-01-01T00:00:00Z'")
    con.commit()
    con.close()
    first = legs[0]
    r0 = boards[first["sport"]][(first["book"], first["row_id"])]
    exp.exp["settle"][r0["scout"]["read_rev"]] = {"actual": 1 if first["side"] == "over" else 4, "line": first["line"], "review_id": "rv-1",
                                                  "known_at": "2026-11-26T22:00:00Z"}
    out = workspace_api.store().settle_once()
    assert out["first"] == 1, out
    st, m = call(exp, "GET", API + "mine", user="u1")
    assert st == 200
    got = {x["player"]: x for x in m["receipts"]}
    a = got[r0["player"]["name"]]
    assert a["sport"] == first["sport"] and a["status"] == "miss" and a["grade"] == r0["scout"]["grade"] and a["units"] == 0
    other = next(x for x in m["receipts"] if x["player"] != r0["player"]["name"])
    assert other["status"] == "pending"


def test_slip_adder_rates_soccer_tennis_legs_from_the_same_pool(exp):
    st, doc = gen(exp, slips=1)
    assert st == 200 and doc["slips"], doc
    boards = {s: board(exp, s) for s in adapter.SPORTS}
    body = []
    for l in doc["slips"][0]["legs"]:
        r = boards[l["sport"]][(l["book"], l["row_id"])]
        body.append(dict(wire(r, l["side"]), stat=r["market"]["stat"], period=r["market"]["period"], player_key=r["player"]["key"]))
    st, adv = call(exp, "POST", API + "slip-advice", {"legs": body, "entry_types": {"prizepicks": "power"}}, user="u1")
    assert st == 200, adv
    assert all(a["in_pool"] for a in adv["legs"]), adv



def test_settle_asks_every_leg_of_a_full_pass_and_voids_did_not_play():
    """review 2026-11-27: a pass hands over up to 2 x LEG_PAGE legs; every one is asked (never the same first 120 forever)."""
    rows = [{"settle_key": f"k{i}", "start_utc": "2020-01-01T00:00:00Z", "ref": {"forecast_id": f"f{i:03d}", "side": "over", "line": 2.5}}
            for i in range(2 * workspace_api.LEG_PAGE)]
    asked = []

    def fake(srv, path, query=None, **kw):
        ids = query["ids"].split(",")
        asked.extend(ids)
        return {"results": {i: ({"actual": None, "void": True, "line": 2.5} if i == "f239" else {"actual": 3, "line": 2.5}) for i in ids}}, None
    orig = workspace_sports.call
    workspace_sports.call = fake
    try:
        got = adapter.settle(server, "tennis", rows)
    finally:
        workspace_sports.call = orig
    assert len(asked) == len(rows) and len(got) == len(rows)
    assert got["k239"]["result"] == "void" and got["k0"]["result"] == "hit"


def test_extra_time_lines_stay_off_slips(exp):
    doc = exp.exp["docs"]["soccer"]
    for f in doc["forecasts"].values():
        f["settlement_scope"] = "regulation_plus_extra_time"
    adapter.reset()
    b = board(exp, "soccer")
    assert b and all(not r["slip_ok"] for r in b.values())
    st, g = gen(exp, sports="soccer")
    assert st == 200 and not g["slips"] and g["pool"]["excluded"].get("settle")
    r = next(r for r in b.values() if r["offer"]["under"]["offered"] and r["scout"]["grade"] != "Lean")
    leg = wire(r, r["scout"]["side"])
    st, chk = call(exp, "POST", API + "recheck", {"idem": "idem-exp-et", "legs": [leg], "entry_types": {}}, user="u1")
    assert [p["code"] for p in chk["legs"][leg["id"]]["problems"]] == ["not_gradable"]
