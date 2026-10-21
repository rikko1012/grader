"""2026-10-21 audit, group B (HLTV reads / performance-model inputs): one regression test per fix. Fakes only - no database,
no network. ALL inputs are TEST FIXTURES."""
import os, sys, threading, time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hltv, hltv_store, identity, perf, perf_live, perf_market, perf_shadow


class OperationalError(Exception):            # named like psycopg2's: hltv.is_transient matches on the class name
    pass


class QueryCanceled(Exception):
    pass


@pytest.fixture
def no_store(monkeypatch):
    saved = {}
    monkeypatch.setattr(hltv_store, "save", lambda k, v: saved.__setitem__(k, v))
    monkeypatch.setattr(hltv_store, "load", lambda k, *a, **kw: (time.time(), saved[k]) if k in saved else None)
    return saved


# ---------------------------------------------------------------- audit 4: a failed identity lookup serves the last good context
def test_4_failed_identity_lookup_serves_the_last_good_context_once(monkeypatch, no_store):
    calls = []

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): pass

        def execute(self, *a, **k):
            time.sleep(0.2)
            raise OperationalError("server closed the connection unexpectedly")

    class Con:
        def cursor(self): return Cur()
        def close(self): pass

    monkeypatch.setattr(hltv, "dsn", lambda: "x")
    monkeypatch.setattr(hltv, "_connect", lambda: calls.append(1) or Con())
    monkeypatch.setattr(hltv, "_CACHE", {})
    monkeypatch.setattr(hltv, "_META", {})
    key = ("ctx", "s1mple", "navi", "faze", ())
    good = {"found": True, "last5": [1, 2, 3]}
    hltv._CACHE[key] = (time.time() - hltv.TTL - 5, good, True)       # expired good context in memory
    out, errs = [], []

    def run():
        try:
            out.append(hltv.context("s1mple", "NaVi", "FaZe"))
        except Exception as e:
            errs.append(e)
    th = [threading.Thread(target=run) for _ in range(20)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert not errs and len(out) == 20 and all(o == good for o in out)
    assert len(calls) == 1                                               # one database read (connection) for the one key


def test_4_single_flight_followers_take_the_leaders_error():
    n, gate = [], threading.Event()

    def read():
        n.append(1)
        gate.wait(1)
        raise OperationalError("down")
    errs = []

    def run():
        try:
            hltv.single_flight(("t4",), read, lambda: hltv.MISS)
        except OperationalError as e:
            errs.append(e)
    th = [threading.Thread(target=run) for _ in range(8)]
    th[0].start(); time.sleep(0.1)
    [t.start() for t in th[1:]]
    time.sleep(0.1); gate.set()
    [t.join() for t in th]
    assert len(n) == 1 and len(errs) == 8


# ---------------------------------------------------------------- audit 5: no network I/O under the pool lock
def test_5_liveness_check_runs_outside_the_pool_lock(monkeypatch):
    class Raw:
        closed = False
        def close(self): pass
    p = hltv._Pool()
    now = time.time()
    p.idle, p.n = [(Raw(), now - 40), (Raw(), now - 45)], 2
    monkeypatch.setattr(hltv._Pool, "_alive", staticmethod(lambda raw: time.sleep(1.0) or False))
    monkeypatch.setattr(hltv, "_raw_connect", lambda: Raw())
    t = threading.Thread(target=p.get)
    t.start(); time.sleep(0.1)
    t0 = time.time()
    st = p.stats()
    took = time.time() - t0
    t.join()
    assert took < 0.5, took
    assert st["max"] == hltv.POOL_MAX and p.n == 1                       # both dead ones dropped, one new opened


# ---------------------------------------------------------------- audit 18: a shrunken N0 set is refused
def _n0(pre, late_in=(), sha="s"):
    return {"pre": frozenset(pre), "late_in": frozenset(late_in), "counts": {"history": 0}, "snapshots": [],
            "snapshots_used": [], "sha": sha, "rule": perf_live.INPUT_RULE}


def test_18_n0_refuses_an_evaluation_that_drops_seed_matches(monkeypatch, no_store):
    monkeypatch.setattr(perf_live, "_IN", {"val": None, "next": 0.0, "loading": False, "error": None, "loaded_at": None,
                                           "stale_since": None})
    evals = [_n0({100, 300}, {500}, "a"), _n0({300}, {500}, "b"), _n0({100, 300}, {500, 600}, "c")]
    monkeypatch.setattr(perf_live, "_load_n0", lambda: evals.pop(0))
    assert perf_live.n0()["sha"] == "a"
    perf_live._IN["next"] = 0
    v = perf_live.n0()                                                   # the scraper re-fetched seed match 100
    assert v["sha"] == "a" and 100 in v["pre"]
    assert "STALE" in perf_live.input_revision() and "refused" in perf_live._IN["error"]
    perf_live._IN["next"] = 0
    v = perf_live.n0()                                                   # a superset is accepted again
    assert v["sha"] == "c" and perf_live._IN["stale_since"] is None


def test_18_after_a_restart_the_stored_verified_set_is_the_baseline(monkeypatch, no_store):
    no_store[("perf_n0", perf_live.INPUT_REV, perf_live.INPUT_RULE)] = _n0({100, 300}, (), "a")
    monkeypatch.setattr(perf_live, "_IN", {"val": None, "next": 0.0, "loading": False, "error": None, "loaded_at": None,
                                           "stale_since": None})
    monkeypatch.setattr(perf_live, "_load_n0", lambda: _n0(set(), (), "empty"))   # a rebuild re-fetched the whole seed
    v = perf_live.n0()
    assert v["sha"] == "a" and perf_live._IN["stale_since"]


# ---------------------------------------------------------------- audit 19: cm-2 team id keeps its last good answer
def test_19_team_id_serves_the_last_good_answer_during_an_outage(monkeypatch, no_store):
    def down():
        raise OperationalError("could not connect to server")
    monkeypatch.setattr(hltv, "_connect", down)
    monkeypatch.setattr(perf_shadow, "_RAW", {})
    monkeypatch.setattr(perf_shadow, "_FAIL", {})
    perf_shadow._RAW[("tid", hltv._norm("Team"))] = (time.time() - 16 * 60, 4242)    # memory copy past its TTL
    assert perf_shadow._team_id("Team") == 4242
    assert perf_shadow._team_id("Team") == 4242                                       # inside FAIL_WAIT too
    no_store[("shadow_tid", hltv._norm("Other"))] = 77                                # a restart during the outage
    assert perf_shadow._team_id("Other") == 77
    with pytest.raises(OperationalError):
        perf_shadow._team_id("Nobody")                                                # nothing anywhere: still transient


# ---------------------------------------------------------------- audit 31: namesakes by canonical team id / 12-month maps
class _PidCur:
    """Two HLTV players called 'bob': 101 a veteran (300 maps, 5 in 12 months, never on DENDELE), 202 on HLTV team 55
    'DENDELE' (40 maps, all recent). Answers like the SQL would: the old query had no tid / 12-month column."""
    def execute(self, q, p=None):
        self.q, self.p = q, p or {}

    def fetchall(self):
        if "FILTER" not in self.q:
            return [(101, False, 300), (202, False, 40)]
        tid = self.p.get("tid")
        return [(101, tid == 1, 5, 300), (202, tid == 55, 40, 40)]

    def fetchone(self):
        return None


def test_31_namesake_resolved_by_alias_team_id(monkeypatch):
    monkeypatch.setattr(hltv, "resolve_team_id", lambda cur, name: 55 if name == "DENDELE CS" else None)
    assert hltv._player_ids(_PidCur(), "bob", "DENDELE CS") == [202]
    assert hltv._player_ids(_PidCur(), "bob", "Somebody") == [202]      # unconfirmed: 12-month maps, not all-time rows


# ---------------------------------------------------------------- audit 32: an events-query timeout is transient
def test_32_events_timeout_raises_instead_of_changing_tonight(monkeypatch):
    monkeypatch.setattr(hltv, "_next_match", lambda *a, **k: {"event": "Some LAN Major"})

    class Cur:
        connection = type("C", (), {"rollback": lambda self: None})()

        def execute(self, q, p=None):
            self.q = q
            if "FROM events" in q:
                raise QueryCanceled("canceling statement due to statement timeout")

        def fetchall(self):
            return [(True, 300, 480, 20)] if "GROUP BY mo.lan" in self.q else []

        def fetchone(self):
            return None
    with pytest.raises(QueryCanceled):
        hltv._environment(Cur(), [1], "Mine", "Foe", None)


# ---------------------------------------------------------------- audit 33: two HLTV teams under one name = unresolved
def test_33_ambiguous_team_name_is_unresolved(monkeypatch):
    class Cur:
        def execute(self, q, p):
            self.v = p[0]

        def fetchone(self):
            return {"rebels": (111, 2), "vitality": (9565, 1)}.get(self.v, (None, 0))
    assert hltv._team_id(Cur(), "Rebels") is None
    assert hltv.canonical_team_id(Cur(), "Rebels") is None
    assert hltv.canonical_team_id(Cur(), "Team Vitality") == 9565


# ---------------------------------------------------------------- audit 34: the loose ctx key keeps the match start
def test_34_loose_context_key_keeps_the_match_start():
    a = hltv_store.loose(("ctx", "nick", "teama", "teamb", ("mirage",), "2026-10-21T18:00"))
    b = hltv_store.loose(("ctx", "nick", "teama", "teamb", ("mirage",), "2026-10-15T18:00"))
    c = hltv_store.loose(("ctx", "nick", "teama", "teamb", ("nuke", "inferno"), "2026-10-21T18:00"))
    assert a != b and a == c
    assert hltv_store.loose(("p", "nick", "team", "2025-08-17")) == ("~loose", "p", "nick", "team")   # others unchanged


# ---------------------------------------------------------------- audit 35: one evaluation identity for 'FaZe Clan' / 'FaZe'
def test_35_clan_affix_links_the_bo3_slug_and_the_hltv_id():
    out = identity.assign([{"id": 1, "match_slug": "faze-clan-vs-vitality-21-10-2026", "team": "FaZe Clan", "opponent": "Vitality",
                            "match_start": "2026-10-21T15:00:00Z"},
                           {"id": 2, "match_slug": "hltv-2390001", "team": "FaZe", "opponent": "Vitality",
                            "match_start": "2026-10-21T15:00:00Z"}])
    assert out == {1: "hltv:2390001", 2: "hltv:2390001"} and identity.RULE_VERSION == 4


# ---------------------------------------------------------------- audit 36: a failed index load backs off
def test_36_failed_index_load_is_not_restarted_by_the_next_search(monkeypatch):
    def down():
        raise OperationalError("canceling statement due to statement timeout")
    monkeypatch.setattr(hltv, "dsn", lambda: "x")
    monkeypatch.setattr(hltv, "_connect", down)
    monkeypatch.setattr(hltv, "_INDEX", {"at": 0.0, "rows": [{"nick": "a", "key": "a", "team": "T", "recent": 1, "maps": 1,
                                                               "loose": "a"}], "busy": False})
    started = []
    real = threading.Thread

    class T(real):
        def start(self):
            started.append(1)
            self.run()
    monkeypatch.setattr(hltv.threading, "Thread", T)
    hltv.search_players("a")
    hltv.search_players("a")
    assert len(started) == 1 and time.time() - hltv._INDEX["at"] < 3600 and not hltv._INDEX["busy"]


# ---------------------------------------------------------------- audit 48: rr-1 keeps logging on perf-cm2 lines
def test_48_rr1_shadow_entry_on_a_cm2_live_line():
    pf2 = {"version": "perf-2", "lambda": 30.0, "lambda_kills": 30.0, "sd": perf.SPREAD[("kills", "12")] * 30 ** 0.5,
           "rounds_per_map": perf.PARAMS["base_r"], "maps": perf.MAPS["12"], "hs_per_kill": 0.5, "spread_c_kills": 1.457,
           "data_quality": "ok", "round_share": {"team": 0.53, "opp": 0.48, "team_maps": 40, "opp_maps": 35}}
    keep = {k: pf2[k] for k in ("version", "lambda", "lambda_kills", "hs_per_kill", "sd", "data_quality")}
    cm2pf = dict(pf2, version="perf-cm2", perf2=keep, lambda_kills=31.2, **{"lambda": 31.2})
    gi = {"prop": "kills", "line": 29.5, "scope": "12", "bo": 3, "maps_played": 40, "kpr": 0.7, "hs_pct": 48,
          "roster_confirmed": True, "perf": cm2pf, "p_team_market": 0.66, "market_quality": "SOFT",
          "odds_provenance": {"selected": "thunderpick", "retrieved_at": "t"}}
    e = perf_market.shadow_entry(gi, None)
    assert e is not None and e["perf2_lambda"] == 30.0 and e["published_version"] == "perf-cm2"
    assert e == dict(perf_market.shadow_entry(dict(gi, perf=pf2), None), published_version="perf-cm2")


# ---------------------------------------------------------------- audit 49: a NULL kill / score is never read
def test_49_player_query_leaves_out_maps_with_missing_kills_or_scores():
    for sql in (perf_live._PLAYER_SQL, perf_live._PLAYER_SQL_N0):
        assert "ps.k IS NOT NULL" in sql and "own.score IS NOT NULL" in sql and "opp.score IS NOT NULL" in sql
    assert "ps.matchid\nFROM" in perf_live._PLAYER_SQL_N0                 # the N0 form still carries the match id


# ---------------------------------------------------------------- audit 50: abandoned cache keys are dropped
def test_50_live_and_shadow_caches_drop_abandoned_keys(monkeypatch, no_store):
    monkeypatch.setattr(perf_live, "_CACHE", {("old",): (time.time() - 7 * 3600, (None, [])), ("recent",): (time.time() - 60, (1, []))})
    monkeypatch.setattr(perf_live, "_SERVED", {("old",): ("live", 0)})
    monkeypatch.setattr(perf_live, "_PRUNED", [0.0])
    perf_live._read(("new",), lambda: (2, []), None)
    assert set(perf_live._CACHE) == {("recent",), ("new",)} and ("old",) not in perf_live._SERVED
    monkeypatch.setattr(perf_shadow, "_RAW", {("old",): (time.time() - 7 * 3600, 1), ("recent",): (time.time() - 60, 2)})
    monkeypatch.setattr(perf_shadow, "_FAIL", {("f",): time.time() - 600})
    monkeypatch.setattr(perf_shadow, "_PRUNED", [0.0])
    perf_shadow._cached(("new",), lambda: 3)
    assert set(perf_shadow._RAW) == {("recent",), ("new",)} and not perf_shadow._FAIL


# ---------------------------------------------------------------- audit 51: the shadow's match day is the live forecast's
def test_51_shadow_uses_the_schedule_start_like_the_live_forecast(monkeypatch):
    import cs2_scout
    seen = []

    class Stop(Exception):
        pass

    def md(s):
        seen.append(s)
        raise Stop
    monkeypatch.setattr(cs2_scout, "match_day", md)
    res = {"grader_inputs": {"perf": {"version": perf.VERSION, "lambda": 20.0}},
           "grade": {"projection_source": perf.VERSION, "p_more": 0.5},
           "player_block": {"player": {"nick": "t"}, "playing_for": "Team"},
           "next_match": {"start": "2026-10-22T00:00:00Z", "opponent": "Opp"}}
    with pytest.raises(Stop):
        perf_shadow.build(res, {"start": "2026-10-21T23:30:00Z"})
    assert seen == ["2026-10-22T00:00:00Z"]
