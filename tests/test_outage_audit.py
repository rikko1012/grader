"""2026-10-11 consistency audit: a transient failure (HLTV database unreachable / timed out, BO3.gg blip) must never become a
different projection, a lost input remembered for its cache TTL, a dropped board row, a void settlement or a wrong label."""
import os, sys, time, argparse, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import hltv, cs2_scout, server, roles, records, comparison, picklog


class Boom(Exception):
    pass


def test_is_transient_covers_driver_subclasses():
    class OperationalError(Exception): pass
    class AdminShutdown(OperationalError): pass
    class QueryCanceled(OperationalError): pass
    assert hltv.is_transient(AdminShutdown("server closed the connection unexpectedly"))
    assert hltv.is_transient(QueryCanceled("canceling statement due to statement timeout"))
    assert hltv.is_transient(hltv.Unavailable("breaker"))
    assert hltv.is_transient(TimeoutError())
    assert not hltv.is_transient(KeyError("x"))
    assert not hltv.is_transient(ValueError("player 'x' (y) not found in HLTV"))


def test_remember_never_keeps_a_failure_for_the_ttl():
    key = ("t", "remember")
    hltv._CACHE.pop(key, None)
    assert hltv._remember(key, {"found": True, "v": 1}) == {"found": True, "v": 1}
    # a failed lookup keeps the previous good value and is retried after FAIL_TTL, not TTL
    assert hltv._remember(key, {"found": False, "error": "x"}, ok=False) == {"found": True, "v": 1}
    ts, val, ok = hltv._CACHE[key]
    assert val == {"found": True, "v": 1} and ok and time.time() - ts > hltv.TTL - hltv.FAIL_TTL - 1
    # no previous value: the failure is kept only FAIL_TTL
    key2 = ("t", "remember2"); hltv._CACHE.pop(key2, None)
    assert hltv._remember(key2, [], ok=False) == []
    assert time.time() - hltv._CACHE[key2][0] > hltv.TTL - hltv.FAIL_TTL - 1
    # a later success replaces it fully
    assert hltv._remember(key2, [1], ok=True) == [1] and time.time() - hltv._CACHE[key2][0] < 2


def test_player_numbers_error_is_not_cached_for_15_minutes(monkeypatch):
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://x")
    key = (hltv._norm("saadzin"), hltv._norm("Imperial"), "")
    hltv._CACHE.pop(key, None)
    good = {"found": True, "rating": 1.1}
    monkeypatch.setattr(hltv, "_player_numbers", lambda n, t, d: good)
    assert hltv.player_numbers("saadzin", "Imperial") is good
    hltv._CACHE.pop(key, None)                        # expire
    monkeypatch.setattr(hltv, "_player_numbers", lambda n, t, d: (_ for _ in ()).throw(hltv.Unavailable("down")))
    out = hltv.player_numbers("saadzin", "Imperial")
    assert out["found"] is False and "Unavailable" in out["error"]
    assert time.time() - hltv._CACHE[key][0] > hltv.TTL - hltv.FAIL_TTL - 1   # retried in FAIL_TTL s


def test_connection_pool_reuses_and_drops_broken(monkeypatch):
    import types
    made = []

    class Con:
        closed = 0
        def __init__(self): self.status = 0; made.append(self)
        def set_session(self, **k): pass
        def get_transaction_status(self): return self.status
        def cursor(self): raise AssertionError("no SET expected on a non-plain connection")
        def close(self): self.closed = 1
    fake = types.SimpleNamespace(OperationalError=ConnectionError, connect=lambda *a, **k: Con())
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://u:p@db.example.com/x")
    monkeypatch.setattr(hltv, "_cursor_factory", lambda: None)
    hltv._DOWN_UNTIL[0] = 0.0; hltv._STATE["plain_connect"] = False
    hltv._CONNS = hltv._Pool()
    c1 = hltv._connect(); raw = c1.raw; c1.close()
    c2 = hltv._connect()
    assert c2.raw is raw and len(made) == 1            # reused, one handshake
    c2.raw.status = 2                                   # broke mid-query: dropped, not reused
    c2.close()
    assert raw.closed == 1 and hltv._CONNS.stats() == {"open": 0, "idle": 0, "max": hltv.POOL_MAX}
    assert hltv.status()["host"] == "db.example.com" and hltv.status()["pool"]["max"] == hltv.POOL_MAX


def test_pool_busy_is_transient_not_a_hang(monkeypatch):
    import types

    class Con:
        closed = 0
        def set_session(self, **k): pass
        def get_transaction_status(self): return 0
    fake = types.SimpleNamespace(OperationalError=ConnectionError, connect=lambda *a, **k: Con())
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://x")
    monkeypatch.setattr(hltv, "_cursor_factory", lambda: None)
    monkeypatch.setattr(hltv, "POOL_MAX", 1)
    monkeypatch.setattr(hltv, "POOL_WAIT", 0.05)
    hltv._DOWN_UNTIL[0] = 0.0
    hltv._CONNS = hltv._Pool()
    held = hltv._connect()
    with pytest.raises(hltv.Unavailable) as e:
        hltv._connect()
    assert hltv.is_transient(e.value)
    held.close()
    hltv._connect().close()


def test_perf_input_env_transient_is_reported_not_projected(monkeypatch):
    import perf_live
    monkeypatch.setattr(perf_live, "projection", lambda *a, **k: {"lambda": 30.5, "sd": 6, "version": "perf-2"})
    a = argparse.Namespace(scope="12", prop="kills", opponent="NRG", match_start=None)
    pb = {"player": {"nick": "saadzin"}, "current_team": "Imperial", "_env_transient": True}
    out = cs2_scout._perf_input(pb, a)
    assert out["transient"] and "environment" in out["error"]
    pb["_env_transient"] = False
    assert cs2_scout._perf_input(pb, a)["lambda"] == 30.5


def test_perf_input_error_text_has_no_driver_details(monkeypatch):
    import perf_live
    class OperationalError(Exception): pass
    monkeypatch.setattr(perf_live, "projection", lambda *a, **k: (_ for _ in ()).throw(OperationalError("could not connect to server: host 1.2.3.4 user secret")))
    monkeypatch.setattr(cs2_scout, "PERF_RETRY_WAIT", 0)
    out = cs2_scout._perf_input({"player": {"nick": "x"}, "current_team": "y"}, argparse.Namespace(scope="12", prop="kills", opponent=None, match_start=None))
    assert out["transient"] and "1.2.3.4" not in out["error"] and "secret" not in out["error"]


def test_event_type_failure_is_not_remembered(monkeypatch):
    class B:
        class cs2:
            class _api:
                @staticmethod
                def _make_request(path, params): raise Boom("503")
        def _run(self, x): return x
    monkeypatch.setattr(cs2_scout, "EVENT_TYPES", {})
    monkeypatch.setattr(cs2_scout, "EVENT_TYPES_FILE", type("P", (), {"parent": type("D", (), {"mkdir": lambda *a, **k: None})(), "write_text": lambda *a, **k: None})())
    assert cs2_scout._event_type(B(), "slug-x") is None
    assert cs2_scout.match_envs(B(), [{"id": 1, "tournament": {"slug": "slug-x"}}]) == {}
    assert "slug:slug-x" not in cs2_scout.EVENT_TYPES       # asked again next time, not stored as ""


def test_context_key_shares_book_name_variants():
    f = cs2_scout._ckey_name
    assert f("Imperial Esports") == f("Imperial") == f("imperial")
    assert f("NRG Esports") == f("NRG") and f("Team Spirit") == f("Spirit") and f("G2 Esports") == f("G2")
    assert f("Imperial Academy") != f("Imperial") and f("MOUZ NXT") != f("MOUZ") and f("Imperial Female") != f("Imperial")
    assert f("Team") == "team" and f("") == "" and f("TeamOne") == "teamone"      # only a separate word "Team" goes


def test_roles_refresh_failure_retries_in_30s(monkeypatch):
    monkeypatch.setattr(roles, "_HLTV", {"at": 0.0, "rows": {}})
    monkeypatch.setattr(roles, "_last_snapshot", lambda: {"zywoo": [{"team": "Vitality", "awper": True}]})
    import hltv as h
    monkeypatch.setattr(h, "dsn", lambda: "postgres://x")
    monkeypatch.setattr(h, "_connect", lambda: (_ for _ in ()).throw(h.Unavailable("down")))
    roles.refresh_hltv()
    assert roles._HLTV["rows"] == {"zywoo": [{"team": "Vitality", "awper": True}]}   # snapshot fallback, not empty
    assert time.time() - roles._HLTV["at"] > roles.HLTV_TTL - 31                       # tried again in 30 s, not 30 min


def test_roles_team_match_is_exact_variants_only():
    assert roles._team_ok("Imperial Esports", ["Imperial"]) and roles._team_ok("Imperial", ["Imperial Esports"])
    assert not roles._team_ok("Imperial", ["Impact Gaming"])          # no 4-letter prefix guesses
    assert roles._team_ok("ex-Imperial", ["anything"]) and roles._team_ok("", ["x"])


def test_records_failure_keeps_last_rows(monkeypatch):
    import hltv as h
    monkeypatch.setattr(h, "dsn", lambda: "postgres://x")
    records._CACHE.clear()
    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    class Con:
        def cursor(self): return Cur()
        def close(self): pass
    monkeypatch.setattr(h, "_connect", lambda: Con())
    monkeypatch.setattr(h, "_player_ids", lambda cur, n, t: [1])
    monkeypatch.setattr(h, "_series_rows", lambda *a, **k: [{"number": 1}])
    assert records._hltv_rows("x", "y", "2026-01-01") == [{"number": 1}]
    records._CACHE.clear()
    records._CACHE[(h._norm("x"), h._norm("y"), "2026-01-01")] = (0, [{"number": 1}])   # expired good value
    monkeypatch.setattr(h, "_connect", lambda: (_ for _ in ()).throw(h.Unavailable("down")))
    assert records._hltv_rows("x", "y", "2026-01-01") == [{"number": 1}]                 # kept, not []


def test_source_labels_name_the_outage():
    assert comparison.source_label({"projection_source": "legacy (performance model unavailable: HLTV database unreachable right now (retried once): OperationalError)"}) == "older model (HLTV unreachable)"
    assert comparison.source_label({"projection_source": "legacy (performance model unavailable: player 'x' (y) not found in HLTV)"}) == "older model (BO3.gg)"
    assert comparison.source_label({"projection_source": "perf-2"}) == "HLTV"
    assert comparison.source_label({"projection_source": "perf-2-bo3"}) == "BO3.gg"


T = {"projection_source": "legacy (performance model unavailable: HLTV database unreachable right now (retried once): x)"}


def test_board_keep_rules_line_move_bo3_and_retry_cap():
    old = {"source": "HLTV", "grade": "B-", "side": "More", "p": 0.55, "proj": 30.52, "graded_line": 29.5, "at": 1000.0}
    assert server.keep_last_hltv_grade(old, T, {"line": 29.5}, now=2000.0)["hltv_retries"] == 1
    assert server.keep_last_hltv_grade(old, T, {"line": 30.5}, now=2000.0) is None            # the line moved
    assert server.keep_last_hltv_grade(dict(old, source="BO3.gg"), T, {"line": 29.5}) is not None   # provisional model grade kept too
    assert server.keep_last_hltv_grade(dict(old, source="older model (BO3.gg)"), T, {"line": 29.5}) is not None   # any grade of this line beats churn
    assert server.keep_last_hltv_grade(dict(old, hltv_retries=server.HLTV_MAX_RETRIES), T, {"line": 29.5}) is None
    k = server.keep_last_hltv_grade(dict(old, hltv_retries=3), T, {"line": 29.5}, now=2000.0)
    assert k["hltv_retries"] == 4 and k["at"] == 2000.0 - server.BOARD_EVERY + server.HLTV_RETRY


def test_board_payload_hides_a_moved_line(monkeypatch):
    import underdog
    monkeypatch.setattr(underdog, "board", lambda *a, **k: {"rows": [{"id": "L1", "line": 30.5, "player": "saadzin", "prop": "kills", "start": "2099-01-01T00:00:00Z"},
                                                                     {"id": "L2", "line": 29.5, "player": "nitr0", "prop": "kills", "start": "2099-01-01T00:00:00Z"}], "at": 1, "error": None})
    monkeypatch.setattr(server, "BOARD", {"L1": {"side": "Less", "grade": "B", "graded_line": 29.5, "p": 0.6, "units": 0.5},
                                          "L2": {"side": "More", "grade": "B", "graded_line": 29.5, "p": 0.6, "units": 0.5, "hltv_retry": "2026-10-11T14:00:00+00:00"}})
    monkeypatch.setattr(server, "awp_role", lambda *a, **k: (None, None))
    rows = server.board_payload()["rows"]
    assert [r["id"] for r in rows] == ["L2"] and rows[0]["hltv_retry"]


def test_shared_result_can_ask_for_a_short_reuse():
    server._RESULTS.clear()
    n = []
    def compute():
        n.append(1); return 200, b"x", 0.05
    assert server.shared(("k", 1), compute) == (200, b"x")
    assert server.shared(("k", 1), compute) == (200, b"x") and len(n) == 1
    time.sleep(0.08)
    server.shared(("k", 1), compute)
    assert len(n) == 2                                   # reused 0.05 s, not RESULT_TTL


def test_public_flag_names_the_outage():
    f = "OLD PROJECTION: the performance model is unavailable here (HLTV database unreachable right now (retried once): x) - graded on the old pipeline"
    assert "unreachable right now" in server._public_flag(f) and "Prop Board" in server._public_flag(f)
    assert "no HLTV data" in server._public_flag("OLD PROJECTION: the performance model is unavailable here (player 'x' (y) not found in HLTV) - graded on the old pipeline")


def test_settlement_never_voids_when_hltv_is_unreachable(monkeypatch):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc); start = now - timedelta(days=picklog.VOID_AFTER_D + 1)
    r = {"id": 1, "player": "x", "team": "y", "opponent": "z", "prop": "kills", "line": 20.5, "pick": "More", "match_slug": "hltv-123", "match_start": start.isoformat()}
    monkeypatch.setattr(picklog, "scope_result", lambda rr, same_day_only=False: (_ for _ in ()).throw(hltv.Unavailable("down")))
    assert picklog._settle_scope(r, now, start) is None                   # not void
    monkeypatch.setattr(picklog, "scope_result", lambda rr, same_day_only=False: {"status": "none"})
    assert picklog._settle_scope(r, now, start)[0] == "void"               # genuinely missing after VOID_AFTER_D days: void as before
    monkeypatch.setattr(hltv, "settle_totals", lambda *a: (_ for _ in ()).throw(hltv.Unavailable("down")), raising=False)
    assert picklog._settle_hltv(r, now, start) is None


def test_settle_scope_prefers_the_matchs_own_day(monkeypatch):
    from datetime import datetime, timezone, timedelta
    seen = []
    monkeypatch.setattr(picklog, "scope_result", lambda rr, same_day_only=False: seen.append(same_day_only) or {"status": "none"})
    now = datetime.now(timezone.utc)
    picklog._settle_scope({"id": 1}, now, now - timedelta(hours=2))
    picklog._settle_scope({"id": 1}, now, now - timedelta(hours=48))
    assert seen == [True, False]


def test_outage_placeholder_is_hidden_and_retried():
    ph = server.outage_placeholder(None, {"line": 29.5}, now=2000.0)
    assert not ph.get("side") and ph["state"] == "RETRY" and ph["hltv_retries"] == 1 and ph["graded_line"] == 29.5
    assert ph["at"] == 2000.0 - server.BOARD_EVERY + server.HLTV_RETRY
    ph2 = server.outage_placeholder(dict(ph, hltv_retries=server.HLTV_MAX_RETRIES), {"line": 29.5}, now=3000.0)
    assert ph2["hltv_retries"] == server.HLTV_MAX_RETRIES + 1 and ph2["at"] == 3000.0        # regular cycle from here on


def test_member_visible_error_text_is_clean():
    class OperationalError(Exception): pass
    e = OperationalError('connection to server at "dpg-secret-a.ohio-postgres.render.com" (18.1.2.3), port 5432 failed: SSL error')
    assert hltv.err_text(e) == "OperationalError: HLTV database unreachable"
    assert "render.com" not in hltv.err_text(e) and "18.1.2.3" not in hltv.err_text(e)
    assert hltv.err_text(KeyError("secret table name")) == "KeyError: lookup failed"   # non-transient: class only
    assert set(hltv.public_status()) == {"configured", "breaker_on", "pool"}                   # /health: no host, no error text


def test_board_load_hides_old_pipeline_rows_from_another_version(monkeypatch, tmp_path):
    import picklog
    f = tmp_path / "board.json"
    f.write_text(json.dumps({"version": "other", "rows": {
        "A": {"side": "Less", "grade": "B", "source": "older model (BO3.gg)", "graded_model": "old"},
        "B": {"side": "More", "grade": "B", "source": "HLTV", "graded_model": "old"}}}), encoding="utf-8")
    monkeypatch.setattr(server, "BOARD_FILE", f)
    monkeypatch.setattr(server, "BOARD", {})
    server._board_load()
    assert not server.BOARD["A"].get("side") and server.BOARD["A"]["state"] == "RETRY" and server.BOARD["A"]["at"] == 0
    assert server.BOARD["B"]["side"] == "More" and server.BOARD["B"]["at"] == 0           # an HLTV grade stays visible until regraded


def test_allow_probe_one_at_a_time(monkeypatch):
    hltv._DOWN_UNTIL[0] = 0.0
    assert hltv.allow_probe() is True                       # breaker off: anyone may retry
    hltv._DOWN_UNTIL[0] = time.time() + 300; hltv._LAST_FORCED[0] = 0.0; hltv._STATE["probing"] = False
    assert hltv.allow_probe() is True                       # first forced probe
    assert hltv.allow_probe() is False                      # within 2 s: everyone else reports at once (no sleep, no wait)
    hltv._LAST_FORCED[0] = 0.0; hltv._STATE["probing"] = True
    assert hltv.allow_probe() is False                      # a probe is already connecting
    hltv._STATE["probing"] = False; hltv._DOWN_UNTIL[0] = 0.0


def test_context_stops_after_a_transient_section(monkeypatch):
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://x")
    hltv._CACHE.pop(("ctx", "x", "y", "", ()), None)
    calls = []
    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, *a, **k): pass
        def fetchall(self): return []
        def fetchone(self): return None
        connection = None
    class Con:
        def cursor(self): return Cur()
        def close(self): pass
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, n, t: [1])
    monkeypatch.setattr(hltv, "_team_id", lambda cur, t: 1)
    def boom(*a, **k):
        calls.append("x"); raise hltv.Unavailable("statement timeout")
    for name in ("_prop_maps", "_eco", "_last5", "_h2h", "_ranking", "_rank_band", "_next_match", "_environment", "_backup"):
        monkeypatch.setattr(hltv, name, boom)
    out = hltv.context("x", "y", None, [], None)
    assert out["transient"] and len(calls) == 1              # the first timeout ends the section loop
    assert time.time() - hltv._CACHE[("ctx", "x", "y", "", ())][0] > hltv.TTL - hltv.FAIL_TTL - 1   # retried in FAIL_TTL s


def test_host_parse_never_shows_password(monkeypatch):
    monkeypatch.setattr(hltv, "dsn", lambda: "postgresql://user:p%40ss@db.internal:5432/x")
    assert hltv._host() == "db.internal"
    monkeypatch.setattr(hltv, "dsn", lambda: "")
    assert hltv._host() is None


def test_load_test_header_is_constant_time_and_token_bound(monkeypatch):
    monkeypatch.setattr(server, "ADMIN_TOKEN", "tok")
    class H:
        def __init__(self, v): self.headers = {"X-Load-Test": v} if v is not None else {}
    assert server.Handler.load_test(H("tok")) and not server.Handler.load_test(H("wrong")) and not server.Handler.load_test(H(None))
    monkeypatch.setattr(server, "ADMIN_TOKEN", None)
    assert not server.Handler.load_test(H("tok"))


def test_stake_inputs_lost_to_a_blip_make_the_grade_transient(monkeypatch):
    import hltv as h, roles
    monkeypatch.setattr(roles, "refresh_hltv", lambda: None)
    monkeypatch.setattr(roles, "awp_role", lambda n, t: (None, None))
    monkeypatch.setattr(h, "recent_series", lambda n, t, since: [])
    key = ("series", h._norm("saadzin"), h._norm("Imperial"), None)
    pb = {"player": {"nick": "saadzin"}, "current_team": "Imperial", "overall": {"window": "x"}, "hltv": {"found": False, "transient": True}}
    d = {"maps_played": 5}
    cs2_scout.hltv_first_gates(pb, d)
    assert d["hltv_transient"]                                           # player numbers unreadable -> flagged
    pb["hltv"] = {"found": True}
    h._CACHE[("series", h._norm("saadzin"), h._norm("Imperial"), "2025-10-12")] = (time.time(), [], False)   # a served failure
    monkeypatch.setattr(cs2_scout, "WINDOW_DAYS", {"x": 365})
    from datetime import datetime, timezone, timedelta
    since = (datetime.now(timezone.utc).date() - timedelta(days=365)).isoformat()
    h._CACHE[("series", h._norm("saadzin"), h._norm("Imperial"), since)] = (time.time(), [], False)
    d = {"maps_played": 5}
    cs2_scout.hltv_first_gates(pb, d)
    assert "series" in d["hltv_transient"]
    assert not h.lookup_failed(("series", "nobody", "", since))


def test_context_retry_bypasses_the_cached_failure(monkeypatch):
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://x")
    key = ("ctx", "a", "b", "", ())
    hltv._CACHE[key] = (time.time(), {"found": False, "transient": True}, False)
    n = []
    def connect():
        n.append(1); raise hltv.Unavailable("still down")
    monkeypatch.setattr(hltv, "_connect", connect)
    assert hltv.context("a", "b", None, [], None)["transient"] and not n          # cached failure served
    assert hltv.context("a", "b", None, [], None, fresh=True)["transient"] and len(n) == 1   # the retry really tries


def test_tainted_context_is_rebuilt_soon(monkeypatch):
    import hltv as h
    monkeypatch.setattr(cs2_scout, "_CONTEXT", {})
    h._STATE["schedule_failed"] = False
    cs2_scout._store_context("k1", {"hltv_context": {"found": True}})
    cs2_scout._store_context("k2", {"hltv_context": {"found": False, "transient": True}})
    assert time.time() - cs2_scout._CONTEXT["k1"][0] < 2
    assert time.time() - cs2_scout._CONTEXT["k2"][0] > cs2_scout.CONTEXT_STALE - 61    # stale within a minute
    h._STATE["schedule_failed"] = True
    cs2_scout._store_context("k3", {"hltv_context": {"found": True}})
    assert time.time() - cs2_scout._CONTEXT["k3"][0] > cs2_scout.CONTEXT_STALE - 61
    h._STATE["schedule_failed"] = False


def test_card_stake_cap_stays_on_its_own_platform(monkeypatch):
    rows = [{"nick": "x", "prop": "kills", "scope": "12", "line": 20.5, "side": "More", "cap_n": 3, "units_full": 1.0, "units": 0.5,
             "start": "2026-10-12T12:00:00Z", "book": "prizepicks", "team_name": "T"}]
    monkeypatch.setattr(server, "_both_boards", lambda: rows)
    server._BS_CACHE[:] = [0.0, []]
    res = {"verdict": {"side": "More", "units": 1.0}, "player_block": {"player": {"nick": "x"}}, "next_match": {"start": "2026-10-12T12:00:00Z"}}
    assert server.board_stake(res, "kills", "12", 20.5)["units"] == 1.0                       # an Underdog card ignores a PrizePicks cap
    assert server.board_stake(res, "kills", "12", 20.5, "goblin")["units"] == 0.5             # a goblin card is a PrizePicks card
    assert server.board_stake(res, "kills", "12", 20.5, "standard", "prizepicks")["units"] == 0.5   # a PrizePicks board click
