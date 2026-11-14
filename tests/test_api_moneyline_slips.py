"""HTTP endpoints for the research tools (server.py): GET /api/moneylines, the slip studio (build / batch / verify / event),
match_id on board rows, and the admin views. A real ThreadingHTTPServer on 127.0.0.1 with server.Handler; every upstream
is a fixture (no network), the slip store and snapshot store live in tmp_path."""
import http.client
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

import kalshi
import moneyline
import polymarket_ml
import server
import slip_batches
import slip_builder
import underdog
import whop_auth
from tests.test_moneyline import kalshi_raw, poly_raw

TOKEN = "test-admin-token-123"
MODEL = "TESTMODEL"


def _row(i, match, start, **kw):
    """One graded board row (server.board_payload shape), eligible for the studio."""
    r = {"id": f"r{i}", "book": "underdog", "player": f"p{i}", "nick": f"p{i}", "player_id": 1000 + i,
         "team": f"T{match}a", "team_name": f"T{match}a", "opponent": f"T{match}b", "opp_name": f"T{match}b",
         "match_slug": f"m{match}", "match_id": f"hltv:{900 + match}", "start": start, "prop": "kills", "scope": "12",
         "line": 30.5, "graded_line": 30.5, "graded_model": MODEL, "side": "More", "p": round(0.58 + 0.01 * (i % 9), 3),
         "grade": "B+", "units": 1.0, "state": "GRADE", "graded_at": start, "awp": False, "awp_conf": "confirmed",
         "igl": False, "higher": 1.0, "lower": 1.0, "alt": False, "ud_player_id": f"u{i}"}
    r.update(kw)
    return r


class Boards:
    """The graded board (board_payload) and the raw Underdog feed the endpoints read; tests move lines in place."""

    def __init__(self, n_matches=12):
        start = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(timespec="seconds")
        self.rows = [_row(m * 2 + k, m, start) for m in range(n_matches) for k in range(2)]
        self.calls = 0

    def payload(self, book="underdog"):
        self.calls += 1
        rows = [dict(r) for r in self.rows] if book == "underdog" else []
        return {"book": book, "updated": time.time(), "error": None, "rows": rows, "total": len(rows), "version": MODEL}

    def feed(self, wait=False):
        return {"at": time.time(), "error": None,
                "rows": [{k: r[k] for k in ("id", "player", "prop", "scope", "line", "team", "opponent", "start", "alt",
                                             "higher", "lower", "ud_player_id")} for r in self.rows]}



@pytest.fixture(autouse=True)
def recorded_clock(monkeypatch):
    """The fixtures were recorded on 2026-10-11/12: run as if it were still then (see conftest.pin_clock)."""
    import sys
    from datetime import datetime as _d, timezone as _tz
    from tests.conftest import pin_clock
    mods = [sys.modules[n] for n in ("moneyline", "kalshi", "polymarket_ml", "matchid", "server", "slip_builder") if n in sys.modules]
    pin_clock(monkeypatch, _d(2026, 10, 12, 0, 45, tzinfo=_tz.utc), mods)

@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "ADMIN_TOKEN", TOKEN)
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: {"user": v[5:], "name": "", "ok": True} if v and v.startswith("good-") else None)
    monkeypatch.setattr(slip_builder, "DB_PATH", tmp_path / "slip_studio.db")
    b = Boards()
    monkeypatch.setattr(server, "board_payload", b.payload)
    monkeypatch.setattr(underdog, "board", b.feed)
    for name, val in (("_GB", {}), ("_RESULTS", {}), ("_INFLIGHT", {}), ("_STUDIO_HITS", {}), ("_STUDIO_EV_HITS", {}),
                      ("_SETTLED", [0.0, None]), ("_MODEL_STATE", {}), ("_MLE_SUMMARY", {}),
                      ("_SNAPC", {"at": 0.0, "data": None}), ("SLIP_STUDIO", True)):      # studio on for these tests
        monkeypatch.setattr(server, name, val)
    monkeypatch.setattr(moneyline, "_GROUP", {})
    monkeypatch.setattr(moneyline, "_LAST", {"path": None, "map": {}})
    monkeypatch.setattr(slip_batches, "results", lambda last=2000: [{"batch": "a"}, {"batch": "b"}, {"batch": "c"}])
    # moneylines from the recorded fixtures (fallback name keys - no HLTV in unit tests)
    monkeypatch.setattr(moneyline, "_BOARD", {"at": 0.0, "key": None, "data": None})
    srcs = {"polymarket": moneyline.Source("polymarket", poly_raw, polymarket_ml.normalize, canonical_fn=moneyline._fallback_canonical),
            "kalshi": moneyline.Source("kalshi", kalshi_raw, kalshi.normalize, canonical_fn=moneyline._fallback_canonical)}
    for s in srcs.values():
        s.refresh_once()
    monkeypatch.setattr(moneyline, "SOURCES", srcs)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield {"port": httpd.server_address[1], "boards": b, "sources": srcs, "tmp": tmp_path}
    httpd.shutdown()
    httpd.server_close()


def call(env, method, path, body=None, member=None, load_test=False, admin=False, raw=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", env["port"], timeout=30)
    h = dict(headers or {})
    if member:
        h["Cookie"] = f"{whop_auth.COOKIE}=good-{member}"
    if load_test:
        h["X-Load-Test"] = TOKEN
    if admin:
        h["X-Admin-Token"] = TOKEN
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    c.close()
    try:
        return r.status, json.loads(out)
    except ValueError:
        return r.status, out


def build(env, member, idem, settings=None, **kw):
    return call(env, "POST", "/api/slip_studio/build", dict({"settings": settings or {"book": "underdog", "sizes": [2, 3, 4], "count": 2},
                                                             "idem": idem}, **kw), member=member)


# ------------------------------------------------------------------ auth gate
def test_auth_gate_same_as_board(env):
    for path in ("/api/moneylines", "/api/moneyline-games?league=nfl", "/api/slip_studio/batch", "/api/board"):
        st, d = call(env, "GET", path)
        assert st == 401 and d["error"] == "members_only", path
    for path in ("/api/slip_studio/build", "/api/slip_studio/verify", "/api/slip_studio/event"):
        st, d = call(env, "POST", path, {"idem": "abcdefgh-1"})
        assert st == 401, path
    st, _ = call(env, "GET", "/api/moneylines", member="alice")
    assert st == 200
    st, _ = call(env, "GET", "/api/moneylines", load_test=True)
    assert st == 200
    st, d = call(env, "GET", "/api/slip_studio/batch", load_test=True)
    assert st == 200 and d["batch_id"]
    # admin views: the admin token only (a member cookie or the load-test header is not admin)
    for path in ("/admin/moneylines_status", "/admin/ml_eval"):
        assert call(env, "GET", path, member="alice")[0] == 404
        assert call(env, "GET", path, load_test=True)[0] == 404


# ------------------------------------------------------------------ moneylines
def test_winner_feed_checks_league_before_reading(env, monkeypatch):
    import workspace_api
    import workspace_game_books
    monkeypatch.setattr(workspace_api, "access", lambda *args: {"lol": True, "nba": False})
    reads = []
    def feed(srv, league):
        reads.append(league)
        return 200, b'{"league":"lol","games":[]}'
    monkeypatch.setattr(workspace_game_books, "moneylines", feed)
    for league, expected in [("lol", 200), ("nba", 404), ("other", 404)]:
        assert call(env, "GET", "/api/moneyline-games?league=" + league, member="alice")[0] == expected
    assert reads == ["lol"]


def test_moneylines_shape_shadow_labels_and_no_raw_payloads(env):
    st, d = call(env, "GET", "/api/moneylines", member="alice")
    assert st == 200
    assert d["recommendation"] == "Shadow - not a staking recommendation"
    assert d["model"]["available"] is False and d["model"]["probability"] is None
    assert d["model"]["label"] == server.MODEL_NOT_VALIDATED
    assert set(d["sources"]) == {"polymarket", "kalshi"} and d["rule"].startswith("ml-baseline-1")
    assert d["matches"], "fixture matches expected"
    for m in d["matches"]:
        assert m["scope"] == "series"
        assert set(m["platforms"]) == {"polymarket", "kalshi"}
        for p, blk in m["platforms"].items():
            if blk:
                assert "quotes" not in blk and "rules" not in blk          # no raw platform payloads to members
                if p == "kalshi":
                    assert blk["url"] is None                           # unverified page URL form - never linked
        b = m["baseline"]
        if b["prob_a"] is None:
            assert b["label"] == "Market probability unavailable"       # never 50%
        else:
            assert 0 < b["prob_a"] < 1 and b["platform"] in ("polymarket", "kalshi")
    assert any(m["platforms"]["polymarket"] and m["platforms"]["kalshi"] for m in d["matches"])
    assert b"\"raw\"" not in json.dumps(d).encode()


def test_moneylines_one_shared_cached_response(env, monkeypatch):
    n = {"calls": 0}
    real = server.moneylines_payload

    def counted():
        n["calls"] += 1
        time.sleep(0.2)                       # slow build: everyone arriving meanwhile waits for the same one
        return real()
    monkeypatch.setattr(server, "moneylines_payload", counted)
    out = []

    def one(i):
        out.append(call(env, "GET", "/api/moneylines", member=f"m{i}"))
    ts = [threading.Thread(target=one, args=(i,)) for i in range(40)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(out) == 40 and all(st == 200 for st, _ in out)
    assert n["calls"] == 1
    assert len({json.dumps(d, sort_keys=True) for _, d in out}) == 1
    call(env, "GET", "/api/moneylines", member="late")
    assert n["calls"] == 1                    # within 30 s: still the shared copy


def test_moneyline_failure_is_isolated(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("exchange adapters broken")
    monkeypatch.setattr(moneyline, "board", boom)
    st, d = call(env, "GET", "/api/moneylines", member="alice")
    assert st == 503 and d["label"] == "Market probability unavailable"
    assert call(env, "GET", "/api/board", member="alice")[0] == 200          # the prop board is untouched
    assert call(env, "GET", "/health")[0] == 200
    st, d = call(env, "GET", "/admin/moneylines_status", admin=True)
    assert st == 200 and "moneyline" in d["error"]


# ------------------------------------------------------------------ slip studio: bad input
def test_build_rejects_bad_input(env):
    ok = {"book": "underdog", "sizes": [2], "count": 1}
    cases = [
        ({"settings": ok}, "idem"),                                                       # no request id
        ({"settings": ok, "idem": "short"}, "idem"),
        ({"settings": ok, "idem": "has space 123"}, "idem"),
        ({"settings": ok, "idem": "abcdefgh-1", "evil": 1}, "unknown field"),
        ({"settings": {"book": "draftkings"}, "idem": "abcdefgh-2"}, "book"),
        ({"settings": {"sizes": [5]}, "idem": "abcdefgh-3"}, "sizes"),
        ({"settings": {"sizes": [2, 3, 4, 2]}, "idem": "abcdefgh-4"}, "sizes"),
        ({"settings": {"count": 99}, "idem": "abcdefgh-5"}, "count"),
        ({"settings": {"count": True}, "idem": "abcdefgh-6"}, "count"),
        ({"settings": {"market": "moneyline"}, "idem": "abcdefgh-7"}, "moneyline"),
        ({"settings": {"market": "goblin", "book": "underdog"}, "idem": "abcdefgh-8"}, "goblin"),
        ({"settings": {"overlap": "limited", "max_uses": {"player": 9}}, "idem": "abcdefgh-9"}, "max_uses"),
        ({"settings": {"side": ["More"]}, "idem": "abcdefgh-10"}, "side"),
        ({"settings": "all", "idem": "abcdefgh-11"}, "settings"),
        ({"settings": ok, "idem": "abcdefgh-12", "new_batch": "yes"}, "new_batch"),
        ({"settings": ok, "idem": "abcdefgh-13", "batch_id": "../../etc"}, "batch"),
    ]
    for body, word in cases:
        st, d = call(env, "POST", "/api/slip_studio/build", body, member="alice")
        assert st == 400 and word in d["error"], (body, d)
    st, d = call(env, "POST", "/api/slip_studio/build", raw=b"[1, 2]", member="alice")
    assert st == 400
    st, d = call(env, "POST", "/api/slip_studio/build", raw=b"{not json", member="alice")
    assert st == 400
    st, d = call(env, "POST", "/api/slip_studio/build", raw=b"x" * (server.STUDIO_BODY_MAX + 1), member="alice")
    assert st == 413
    st, d = call(env, "POST", "/api/slip_studio/nope", {"idem": "abcdefgh-1"}, member="alice")
    assert st == 404
    st, d = call(env, "GET", "/api/slip_studio/batch?id=DROP%20TABLE", member="alice")
    assert st == 400
    assert not (env["tmp"] / "slip_studio.db").exists() or slip_builder.get_batch("x") is None


def test_build_rate_limited_per_member(env):
    codes = [build(env, "spammer", f"spam-req-{i:03d}", {"sizes": [2], "count": 1})[0] for i in range(server.STUDIO_BUILDS_PER_MIN + 2)]
    assert codes[:server.STUDIO_BUILDS_PER_MIN] == [200] * server.STUDIO_BUILDS_PER_MIN and codes[-1] == 429
    assert build(env, "someone-else", "other-req-001")[0] == 200              # per member, not global


# ------------------------------------------------------------------ slip studio: idempotency + batches
def test_build_idempotent_and_batch_exposure(env):
    st, a = build(env, "alice", "press-0001")
    assert st == 200 and a["batch_id"] and a["build_id"].endswith("/b1")
    # the studio's own record (nothing settles it yet) - never the shadow batch-3 builder's 3 settled batches
    assert a["settled_batches"] == 0 and a["board"]["graded_rows"] == 24
    assert a["track_record"] == {"builder": slip_builder.VERSION, "studio_settled": 0, "shadow_builder": slip_batches.VERSION,
                                 "shadow_settled": 3}
    legs = [l for n in ("2", "3", "4") for s in a["slips"][n] for l in s["legs"]]
    assert legs and all(l["match_id"].startswith("hltv:") for l in legs)    # canonical id carried on every leg
    st, a2 = build(env, "alice", "press-0001")                                # retry: same answer, nothing written
    assert st == 200 and json.dumps(a2["slips"], sort_keys=True) == json.dumps(a["slips"], sort_keys=True)
    assert a2["build_id"] == a["build_id"]
    st, b = build(env, "alice", "press-0002")                                 # a new press in the same batch
    assert st == 200 and b["batch_id"] == a["batch_id"] and b["build_id"].endswith("/b2")
    # the default (Repeats "When needed", owner 2026-10-16) reuses only once fresh picks run out: 12 matches fill the first
    # press (2+2 x 2/3/4 = 18 legs) partly with reuse, never twice in one slip; the strict rule is checked on its own batch
    for res in (a, b):
        for n in ("2", "3", "4"):
            for s in res["slips"][n]:
                assert len({l["match_id"] for l in s["legs"]}) == len(s["legs"])
    STRICT = {"book": "underdog", "sizes": [2, 3, 4], "count": 2, "overlap": "none"}
    st, a = build(env, "alice", "press-0002s", STRICT, new_batch=True)
    st, b = build(env, "alice", "press-0002t", STRICT)
    assert st == 200 and b["batch_id"] == a["batch_id"]
    seen = {}
    for res in (a, b):
        for n in ("2", "3", "4"):
            for s in res["slips"][n]:
                for l in s["legs"]:
                    assert l["match_id"] not in seen, "a match repeated across presses of one batch"
                    seen[l["match_id"]] = s["slip_id"]
    st, batch = call(env, "GET", "/api/slip_studio/batch", member="alice")
    assert st == 200 and batch["batch_id"] == a["batch_id"] and batch["builds"] == 2 and batch["current"] is True
    assert batch["exposure"]["max_uses"] == {"prop": 1, "player": 1, "match": 1}
    # new batch: idempotent too, and the old batch is closed for builds
    st, c = build(env, "alice", "press-0003", new_batch=True)
    st2, c2 = build(env, "alice", "press-0003", new_batch=True)
    assert st == st2 == 200 and c["batch_id"] == c2["batch_id"] != a["batch_id"] and c["build_id"] == c2["build_id"]
    st, d = build(env, "alice", "press-0004", batch_id=a["batch_id"])
    assert st == 409 and d["current_batch"] == c["batch_id"]
    st, old = call(env, "GET", f"/api/slip_studio/batch?id={a['batch_id']}", member="alice")
    assert st == 200 and old["current"] is False and old["closed_at"]


def test_members_never_see_each_others_batches(env):
    st, a = build(env, "alice", "alice-req-1")
    bid = a["batch_id"]
    sid = next(s["slip_id"] for n in ("4", "3", "2") for s in a["slips"][n])
    assert call(env, "GET", f"/api/slip_studio/batch?id={bid}", member="bob")[0] == 404
    assert build(env, "bob", "bob-req-0001", batch_id=bid)[0] == 404
    assert call(env, "POST", "/api/slip_studio/verify", {"slip": sid}, member="bob")[0] == 404
    assert call(env, "POST", "/api/slip_studio/event", {"batch_id": bid, "slip_id": sid, "kind": "copy"}, member="bob")[0] == 404
    st, b = call(env, "GET", "/api/slip_studio/batch", member="bob")
    assert st == 200 and b["batch_id"] != bid and b["builds"] == 0


# ------------------------------------------------------------------ verify + events
def test_verify_then_copy_and_line_move_means_rebuild(env):
    st, a = build(env, "alice", "verify-req-1", {"sizes": [2], "count": 1})
    slip = a["slips"]["2"][0]
    st, v = call(env, "POST", "/api/slip_studio/verify", {"slip": {"slip_id": slip["slip_id"]}, "idem": "verify-idem-1"}, member="alice")
    assert st == 200 and v["ok"] is True and v["action"] == "copy" and v["copy_text"]
    assert "Over 30.5" in v["copy_text"] and "Underdog" in v["copy_text"]
    calls = env["boards"].calls
    # the line of the first leg moves on Underdog (and the board regrades it at the new line)
    leg = slip["legs"][0]
    for r in env["boards"].rows:
        if r["id"] == leg["row_id"]:
            r["line"] = r["graded_line"] = 31.5
    time.sleep(2.1)                                                       # verify refreshes the board (<= 2 s old)
    st, v2 = call(env, "POST", "/api/slip_studio/verify", {"slip": slip["slip_id"]}, member="alice")
    assert env["boards"].calls > calls
    assert st == 200 and v2["ok"] is False and v2["action"] == "rebuild" and v2["copy_text"] is None
    assert any("31.5" in c for c in v2["changes"])
    st, e = call(env, "POST", "/api/slip_studio/event", {"batch_id": a["batch_id"], "slip_id": slip["slip_id"], "kind": "copy",
                                                          "idem": "copy-idem-001"}, member="alice")
    assert st == 200 and e == {"ok": True, "duplicate": False}
    st, e = call(env, "POST", "/api/slip_studio/event", {"batch_id": a["batch_id"], "slip_id": slip["slip_id"], "kind": "copy",
                                                          "idem": "copy-idem-001"}, member="alice")
    assert st == 200 and e["duplicate"] is True
    kinds = [x["kind"] for x in slip_builder.events(a["batch_id"])]
    assert kinds == ["verify_ok", "verify_changed", "copy"]
    for bad in ({"batch_id": a["batch_id"], "slip_id": slip["slip_id"], "kind": "delete"},
                {"batch_id": a["batch_id"], "slip_id": "nope", "kind": "copy"},
                {"batch_id": a["batch_id"], "slip_id": slip["slip_id"], "kind": "copy", "extra": 1}):
        assert call(env, "POST", "/api/slip_studio/event", bad, member="alice")[0] == 400
    assert call(env, "POST", "/api/slip_studio/verify", {"slip": "preview/s1"}, member="alice")[0] == 400


# ------------------------------------------------------------------ board rows carry match_id, nothing else changes
def test_board_payload_adds_match_id_only(monkeypatch):
    import matchid
    import prizepicks
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(timespec="seconds")
    feed = {"at": time.time(), "error": None, "rows": [
        {"id": "x1", "player": "ZywOo", "prop": "kills", "scope": "12", "line": 35.5, "team": "Vitality", "opponent": "MOUZ",
         "start": start, "alt": False, "higher": 1.0, "lower": 1.0, "matchup": "VIT vs MOUZ", "ud_player_id": "u1"}]}
    monkeypatch.setattr(underdog, "board", lambda wait=False: feed)
    monkeypatch.setattr(prizepicks, "board", lambda wait=False: dict(feed, rows=[]))
    monkeypatch.setitem(server.BOARD, "x1", {"side": "More", "grade": "B", "p": 0.6, "units": 1.0, "graded_line": 35.5,
                                             "team_name": "Vitality", "opp_name": "MOUZ", "nick": "ZywOo"})
    monkeypatch.setattr(matchid, "ready", lambda: False)
    before = server.board_payload()["rows"][0]
    assert before["match_id"] is None                                      # tables still loading: no wait, no id
    monkeypatch.setattr(matchid, "ready", lambda: True)
    monkeypatch.setattr(matchid, "match_id_for_row", lambda r: "hltv:4242" if r.get("team_name") == "Vitality" else None)
    after = server.board_payload()["rows"][0]
    assert after["match_id"] == "hltv:4242"
    assert {k: v for k, v in after.items() if k != "match_id"} == {k: v for k, v in before.items() if k != "match_id"}

    def broken(r):
        raise RuntimeError("matchid down")
    monkeypatch.setattr(matchid, "match_id_for_row", broken)
    assert server.board_payload()["rows"][0]["match_id"] is None          # linking failing never breaks the board


# ------------------------------------------------------------------ admin
def test_admin_views(env, monkeypatch):
    st, d = call(env, "GET", "/admin/moneylines_status", admin=True)
    assert st == 200
    assert set(d["sources"]) == {"polymarket", "kalshi"}
    assert d["links"]["matches"] > 0 and d["links"]["linked"] + d["links"]["unlinked"] == d["links"]["matches"]
    assert "snapshots" in d and "matchid" in d
    ran = []
    monkeypatch.setattr(server, "_ml_eval_forward", lambda: (ran.append(1), server._MLE.update(busy=False, at=time.time())))
    monkeypatch.setattr(server, "_MLE", {"at": 0.0, "forward": None, "error": None, "busy": False, "took_s": None})
    st, d = call(env, "GET", "/admin/ml_eval", admin=True)
    assert st == 200 and "polymarket_rows" not in d["results"] and d["results"].get("polymarket_rows_n", 0) >= 0
    assert d["model"]["label"] == server.MODEL_NOT_VALIDATED
    time.sleep(0.1)
    assert ran == [1]


def test_studio_treats_one_canonical_match_id_as_one_match(monkeypatch, tmp_path):
    """Platform spellings differ ("Team Vitality" / "Vitality", no BO3.gg slug on one row): the canonical match_id the board
    now carries makes them one match, so they never share a slip and count once in the batch."""
    monkeypatch.setattr(slip_builder, "DB_PATH", tmp_path / "s.db")
    start = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(timespec="seconds")
    rows = [_row(0, 0, start, team="Team Vitality", team_name="Team Vitality", opponent="MOUZ", opp_name="MOUZ", match_slug=None,
                 match_id="hltv:1"),
            _row(1, 1, start, team="mouz", team_name="mouz", opponent="Vitality", opp_name="Vitality", match_slug="vit-mouz",
                 match_id="hltv:1", p=0.7),
            _row(2, 2, start, match_id="hltv:2"), _row(3, 3, start, match_id="hltv:3")]
    res = slip_builder.build(rows, {"sizes": [2], "count": 3}, model=MODEL)
    for s in res["slips"][2]:
        assert len({l["match_id"] for l in s["legs"]}) == 2
    assert res["exposure"]["distinct_matches"] <= 3 and res["eligible"]["matches"] == 3


def test_unlinked_match_on_both_exchanges_shown_once_never_baseline():
    """moneyline.py keeps an unlinked match as one entry per exchange; the member view joins the two only on exactly the
    same team names (accents / case ignored) within 3 h - still unlinked, still no baseline, each block keeps its own
    team order."""
    def entry(plat, a, b, start, p):
        return {"match_id": None, "link_status": "unlinked", "link_reason": "not on HLTV", "teams": {"a": a, "b": b,
                "names_by_platform": {plat: {"a": a, "b": b}}}, "event": "X", "start": start, "bo": None, "scope": "series",
                "platforms": {"polymarket": None, "kalshi": None} | {plat: {"prob_a": p, "team_a_name": a, "team_b_name": b}},
                "baseline": {"prob_a": None, "label": "Market probability unavailable"}, "disagreement": False}
    rows = [entry("polymarket", "Sökerorg", "MOUZ NXT", "2026-10-12T15:00:00Z", 0.4),
            entry("kalshi", "MOUZ NXT", "Sokerorg", "2026-10-12T15:30:00Z", 0.62),
            entry("kalshi", "Rooster", "Orgless", "2026-10-11T15:00:00Z", 0.5),        # a day apart: two entries
            entry("polymarket", "Rooster", "Orgless", "2026-10-12T15:00:00Z", 0.5),
            entry("kalshi", "Vitality Academy", "Elite Klan", "2026-10-12T15:00:00Z", 0.5),   # academy != main team
            entry("polymarket", "Vitality", "Elite Klan", "2026-10-12T15:00:00Z", 0.5)]
    out = server._merge_unlinked(rows)
    assert len(out) == 5
    m = out[0]
    assert m["platforms"]["polymarket"]["team_a_name"] == "Sökerorg" and m["platforms"]["kalshi"]["team_a_name"] == "MOUZ NXT"
    assert m["match_id"] is None and m["baseline"]["prob_a"] is None


# ------------------------------------------------------------------ review fixes (2026-10-12)
def test_studio_is_off_by_default_and_the_page_is_told(env, monkeypatch):
    assert server.studio_flag({}) is True and server.studio_flag({"SLIP_STUDIO": "0"}) is False and server.studio_flag({"SLIP_STUDIO": "1"}) is True
    monkeypatch.setattr(server, "SLIP_STUDIO", False)
    st, d = call(env, "GET", "/api/slip_studio/batch", member="alice")
    assert st == 404 and d["error"] == "slip_studio_off"
    assert build(env, "alice", "off-req-0001")[0] == 404
    st, me = call(env, "GET", "/auth/me", member="alice")
    assert st == 200 and me["slip_studio"] is False
    monkeypatch.setattr(server, "SLIP_STUDIO", True)
    assert call(env, "GET", "/auth/me", member="alice")[1]["slip_studio"] is True
    html = (server.BASE / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="bstudio" hidden' in html                     # the button never shows before the flag arrives


def test_studio_record_never_shows_the_shadow_builders_count(env):
    st, b = call(env, "GET", "/api/slip_studio/batch", member="alice")
    assert st == 200 and b["settled_batches"] == 0
    assert b["track_record"]["shadow_settled"] == 3 and b["track_record"]["studio_settled"] == 0
    assert b["track_record"]["builder"] == slip_builder.VERSION != b["track_record"]["shadow_builder"]


def test_idempotent_retries_are_free_new_presses_are_limited(env):
    n = server.STUDIO_BUILDS_PER_MIN
    first = None
    for i in range(n):
        st, d = build(env, "retrier", f"retry-req-{i:03d}", {"sizes": [2], "count": 1})
        assert st == 200
        first = first or d
    st, d = build(env, "retrier", "retry-req-000", {"sizes": [2], "count": 1})          # a retry of press 1: stored answer
    assert st == 200 and d["build_id"] == first["build_id"]
    assert build(env, "retrier", "retry-req-new", {"sizes": [2], "count": 1})[0] == 429  # a new press: limited
    codes = [build(env, "same-key", "same-key-0001", {"sizes": [2], "count": 1})[0] for _ in range(n + 2)]
    assert codes == [200] * (n + 2)


def test_infinite_or_float_sizes_are_400_not_500(env):
    before = server.STATS["errors_5xx"]
    for raw in (b'{"settings": {"sizes": [Infinity]}, "idem": "abcdefgh-inf"}',
                b'{"settings": {"sizes": [NaN]}, "idem": "abcdefgh-nan"}',
                b'{"settings": {"sizes": [-Infinity, 2]}, "idem": "abcdefgh-nin"}'):
        st, d = call(env, "POST", "/api/slip_studio/build", raw=raw, member="alice")
        assert st == 400, (raw, d)
    for sizes in ([2.0], [True], [2, 3.5], [1e300]):
        st, d = call(env, "POST", "/api/slip_studio/build", {"settings": {"sizes": sizes}, "idem": "abcdefgh-flt"}, member="alice")
        assert st == 400 and "sizes" in d["error"], (sizes, d)
    with pytest.raises(ValueError):
        slip_builder.normalize_settings({"sizes": [float("inf")]})
    assert server.STATS["errors_5xx"] == before


def test_verify_and_event_are_rate_limited_and_deduped_without_idem(env, monkeypatch):
    st, a = build(env, "alice", "loop-req-0001", {"sizes": [2], "count": 1})
    slip = a["slips"]["2"][0]
    for _ in range(4):
        assert call(env, "POST", "/api/slip_studio/verify", {"slip": slip["slip_id"]}, member="alice")[0] == 200
        assert call(env, "POST", "/api/slip_studio/event", {"batch_id": a["batch_id"], "slip_id": slip["slip_id"],
                                                           "kind": "load"}, member="alice")[0] == 200
    kinds = [e["kind"] for e in slip_builder.events(a["batch_id"])]
    assert kinds.count("load") <= 2 and sum(k.startswith("verify") for k in kinds) <= 2      # one row per 30 s window
    monkeypatch.setattr(server, "STUDIO_EVENTS_PER_MIN", 3)
    monkeypatch.setattr(server, "_STUDIO_EV_HITS", {})
    codes = [call(env, "POST", "/api/slip_studio/verify", {"slip": slip["slip_id"], "idem": f"verify-loop-{i:02d}"},
                  member="alice")[0] for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert call(env, "POST", "/api/slip_studio/verify", {"slip": slip["slip_id"], "idem": "verify-loop-00"},
                member="alice")[0] == 200                                                     # a retry is free
    assert call(env, "POST", "/api/slip_studio/event", {"batch_id": a["batch_id"], "slip_id": slip["slip_id"],
                                                       "kind": "copy", "idem": "copy-loop-0001"}, member="alice")[0] == 429
    assert build(env, "alice", "loop-req-0002", {"sizes": [2], "count": 1})[0] == 200        # build quota untouched


def test_replace_after_a_changed_line_keeps_one_live_slip(env):
    st, a = build(env, "alice", "repl-req-0001", {"sizes": [2], "count": 2})
    slips = a["slips"]["2"]
    assert len(slips) == 2
    old = slips[0]
    st, r = call(env, "POST", "/api/slip_studio/build", {"settings": {"sizes": [2, 3, 4], "count": 3}, "idem": "repl-req-0002",
                                                         "replace": old["slip_id"]}, member="alice")
    assert st == 200 and r["replaced"] == old["slip_id"]
    made = [s for n in ("2", "3", "4") for s in r["slips"][n]]
    assert len(made) == 1 and made[0]["n"] == 2                     # one slip of the replaced slip's size, not a new set
    st, b = call(env, "GET", "/api/slip_studio/batch", member="alice")
    assert b["live_slips"] == 2 and b["superseded"] == 1 and b["exposure"]["slips"] == 2
    gone = next(s for s in b["slips"]["2"] if s["slip_id"] == old["slip_id"])
    assert gone["status"] == "replaced" and gone["replaced_by"] == r["build_id"]
    st, r2 = call(env, "POST", "/api/slip_studio/build", {"idem": "repl-req-0002", "replace": old["slip_id"]}, member="alice")
    assert st == 200 and r2["build_id"] == r["build_id"]           # a retry: the stored result
    st, d = call(env, "POST", "/api/slip_studio/build", {"idem": "repl-req-0003", "replace": old["slip_id"]}, member="alice")
    assert st == 400 and "already replaced" in d["error"]
    assert call(env, "POST", "/api/slip_studio/build", {"idem": "repl-req-0004", "replace": old["slip_id"]}, member="bob")[0] == 404


def _ml_entry(plat, a, b, start, p, usable=True):
    return {"match_id": None, "link_status": "unlinked", "link_reason": "not on HLTV",
            "teams": {"a": a, "b": b, "names_by_platform": {plat: {"a": a, "b": b}}}, "event": "X", "start": start, "bo": None,
            "scope": "series",
            "platforms": {"polymarket": None, "kalshi": None} | {plat: {"prob_a": p, "team_a_name": a, "team_b_name": b,
                                                                       "usable": usable}},
            "baseline": {"prob_a": None, "label": "Market probability unavailable"}, "disagreement": False,
            "disagreement_gap": None}


def test_merged_unlinked_card_never_claims_agreement_without_comparing():
    # reversed team order on the two exchanges: 0.30 for Alpha (Polymarket) and 0.80 for Beta (Kalshi) = 0.20 for Alpha
    out = server._merge_unlinked([_ml_entry("polymarket", "Alpha", "Beta", "2026-10-12T15:00:00Z", 0.30),
                                  _ml_entry("kalshi", "Beta", "Alpha", "2026-10-12T15:10:00Z", 0.80)])
    assert len(out) == 1
    m = out[0]
    assert m["merged_unlinked"] is True and m["match_id"] is None
    assert m["disagreement_gap"] == pytest.approx(0.10) and m["disagreement"] is True        # oriented, never "agree"
    # Polymarket Alpha 0.70 vs Kalshi Beta 0.60 -> Alpha 0.40: gap 0.30
    m = server._merge_unlinked([_ml_entry("polymarket", "Alpha", "Beta", "2026-10-12T15:00:00Z", 0.70),
                                _ml_entry("kalshi", "Beta", "Alpha", "2026-10-12T15:00:00Z", 0.60)])[0]
    assert m["disagreement_gap"] == pytest.approx(0.30) and m["disagreement"] is True
    # one quote not usable: nothing compared - no gap, no agreement claim
    m = server._merge_unlinked([_ml_entry("polymarket", "Alpha", "Beta", "2026-10-12T15:00:00Z", 0.30),
                                _ml_entry("kalshi", "Beta", "Alpha", "2026-10-12T15:00:00Z", 0.71, usable=False)])[0]
    assert m["merged_unlinked"] is True and m["disagreement"] is False and m["disagreement_gap"] is None
    # agreement only after orientation: Alpha 0.40 vs Beta 0.61 (= Alpha 0.39) agree
    m = server._merge_unlinked([_ml_entry("polymarket", "Alpha", "Beta", "2026-10-12T15:00:00Z", 0.40),
                                _ml_entry("kalshi", "Beta", "Alpha", "2026-10-12T15:00:00Z", 0.61)])[0]
    assert m["disagreement"] is False and m["disagreement_gap"] == pytest.approx(0.01)
    # the page's guard: an agreement pill needs a gap (null*100 used to print "agree within 5 pts (0.0)")
    html = (server.BASE / "web" / "index.html").read_text(encoding="utf-8")
    assert "gap != null" in html and "Not linked" not in html and "unlinked – teams matched by name" not in html   # owner 2026-10-16


def test_validation_only_promotion_is_not_called_validated(monkeypatch, tmp_path):
    def state(d):
        (tmp_path / "ml_eval_results.json").write_text(json.dumps(d), encoding="utf-8")
        monkeypatch.setattr(server, "BASE", tmp_path)
        monkeypatch.setattr(server, "_MODEL_STATE", {})
        return server.ml_model_state()
    s = state({"staged": {"freeze": {"promoted": ["a_rating"]}, "test": None}})
    assert s["label"] == server.MODEL_NOT_VALIDATED and s["promoted"] == [] and s["val_promoted"] == ["a_rating"]
    s = state({"staged": {"freeze": {"promoted": ["a_rating"]},
                          "test": {"baseline": {}, "a_rating": {"gain_vs_market_ci": [-0.001, 0.01]}}}})
    assert s["label"] == server.MODEL_NOT_VALIDATED
    s = state({"staged": {"freeze": {"promoted": ["a_rating"]},
                          "test": {"baseline": {}, "a_rating": {"gain_vs_market_ci": [0.002, 0.01]}}}})
    assert s["label"] != server.MODEL_NOT_VALIDATED and s["promoted"] == ["a_rating"]


def test_moneylines_payload_carries_freshness_started_and_bo2_fields(env):
    st, d = call(env, "GET", "/api/moneylines", member="alice")
    assert st == 200 and "val_promoted" not in d["model"]
    m = d["matches"][0]
    assert {"started", "tie_possible"} <= set(m)
    blk = next(b for b in m["platforms"].values() if b)
    assert {"fresh", "market_open", "price_only"} <= set(blk)


# ------------------------------------------------------------------ studio capacity + fill order (2026-10-11)
def test_studio_capacity_endpoint_auth_shared_and_counts(env):
    st, d = call(env, "GET", "/api/slip_studio/capacity?book=underdog")
    assert st == 401 and d["error"] == "members_only"                     # same gate as the batch
    n0 = env["boards"].calls
    st, d = call(env, "GET", "/api/slip_studio/capacity?book=underdog&side=either&stat=both&scope=12", member="alice")
    assert st == 200, d
    assert d["lines"] == 24 and d["matches"] == 12 and d["legs"] == 12
    assert {int(k): v for k, v in d["max_slips"].items()} == {2: 6, 3: 4, 4: 3}
    st2, d2 = call(env, "GET", "/api/slip_studio/capacity?book=underdog&side=either&stat=both&scope=12", member="bob")
    assert st2 == 200 and d2 == d and env["boards"].calls - n0 <= 1         # one shared answer, no rebuild per member
    assert call(env, "GET", "/api/slip_studio/capacity?book=nope", member="alice")[0] == 400
    assert call(env, "GET", "/api/slip_studio/capacity?market=moneyline", member="alice")[0] == 400


def test_studio_build_fill_small_and_capacity_in_response(env):
    # the strict mode's leg plan (Repeats "Never"); the default builds every count asked for (test_slip_builder)
    st, d = build(env, "carol", "fill-small-1", {"book": "underdog", "sizes": [2, 3, 4], "counts": {"2": 2, "3": 2, "4": 2},
                                                 "fill": "small", "overlap": "none"})
    assert st == 200, d
    plan = {int(k): v for k, v in d["plan"].items()}
    assert plan == {2: 2, 3: 2, 4: 0}                                       # 12 legs: 2+2 + 3+3 = 10, two left - no 4-pick
    assert {int(k): v for k, v in d["eligible"]["max_slips"].items()} == {2: 6, 3: 4, 4: 3}
    assert d["settings"]["fill"] == "small"
    st, d = build(env, "dave", "fill-large-1", {"book": "underdog", "sizes": [2, 3, 4], "counts": {"2": 2, "3": 2, "4": 2}, "overlap": "none"})
    assert st == 200 and {int(k): v for k, v in d["plan"].items()} == {4: 2, 3: 1, 2: 0} and d["settings"]["fill"] == "large"
    assert build(env, "erin", "fill-bad-1", {"book": "underdog", "fill": "medium"})[0] == 400


def test_grade_not_found_carries_did_you_mean(env, monkeypatch):
    import cs2_scout
    import hltv
    monkeypatch.setattr(cs2_scout, "grade", lambda name, *a, **k: {"error": f"no pro player called '{name}' on BO3.gg - check the spelling"})
    monkeypatch.setattr(hltv, "suggest_nicks", lambda q, limit=5: ["m0NESY", "monk"][:limit])
    st, d = call(env, "GET", "/api/grade?player=monesyy&line=30.5", load_test=True)
    assert st == 404 and d["error"].startswith("No pro player") and d["suggestions"] == ["m0NESY", "monk"]
