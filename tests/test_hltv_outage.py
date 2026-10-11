"""An HLTV database blip must never turn into a different projection (2026-10-11: saadzin Under 30.5 on the Underdog board,
Over 29.5 on PrizePicks and in Match players - one connection timeout after a restart tripped the 5-minute breaker and
every grade in that window silently used the old pipeline).
- hltv._connect: half-open breaker (one probe every PROBE_SECONDS; success clears it)
- cs2_scout._perf_input: one retry, then a transient error (never "performance model failed" -> legacy silently)
- server.keep_last_hltv_grade: the board keeps its last HLTV grade and retries soon"""
import os, sys, argparse, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import hltv, cs2_scout, server


def test_breaker_is_half_open(monkeypatch):
    calls = []
    import types
    fake = types.SimpleNamespace(OperationalError=ConnectionError)

    def connect(*a, **k):
        calls.append(time.time())
        raise ConnectionError("timeout expired")
    fake.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://x")
    hltv._DOWN_UNTIL[0] = 0.0; hltv._LAST_PROBE[0] = 0.0; hltv._STATE["plain_connect"] = False
    monkeypatch.setattr(hltv, "_cursor_factory", lambda: None)
    hltv._CONNS = hltv._Pool()
    with pytest.raises(ConnectionError):
        hltv._connect()                           # trips the breaker
    with pytest.raises(hltv.Unavailable):
        hltv._connect()                           # skipped instantly
    assert len(calls) == 1
    hltv._LAST_PROBE[0] = time.time() - hltv.PROBE_SECONDS - 1
    with pytest.raises(ConnectionError):
        hltv._connect()                           # the probe really tries
    assert len(calls) == 2
    with pytest.raises(hltv.Unavailable):
        hltv._connect()                           # and everyone else keeps skipping

    class Con:
        closed = 0
        def set_session(self, **k): pass
        def get_transaction_status(self): return 0
    fake.connect = lambda *a, **k: Con()
    hltv._LAST_PROBE[0] = 0.0
    c = hltv._connect()
    assert isinstance(c.raw, Con)                 # probe succeeds -> breaker off
    assert hltv._DOWN_UNTIL[0] == 0.0
    c.close()
    assert isinstance(hltv._connect().raw, Con)
    hltv._DOWN_UNTIL[0] = 0.0


def _args(**k):
    a = argparse.Namespace(scope="12", prop="kills", opponent="NRG", match_start=None)
    a.__dict__.update(k); return a


def test_perf_input_retries_then_reports_transient(monkeypatch):
    import perf_live
    n = []
    def boom(*a, **k):
        n.append(1); raise hltv.Unavailable("HLTV database unreachable a moment ago")
    monkeypatch.setattr(perf_live, "projection", boom)
    monkeypatch.setattr(cs2_scout, "PERF_RETRY_WAIT", 0)
    out = cs2_scout._perf_input({"player": {"nick": "saadzin"}, "current_team": "Imperial"}, _args())
    assert out["transient"] is True and "unreachable" in out["error"] and len(n) == 2
    # the second try succeeding is the normal case after a blip
    n.clear()
    def flaky(*a, **k):
        n.append(1)
        if len(n) == 1: raise hltv.Unavailable("blip")
        return {"lambda": 30.5, "sd": 6.0, "version": "perf-2"}
    monkeypatch.setattr(perf_live, "projection", flaky)
    assert cs2_scout._perf_input({"player": {"nick": "saadzin"}, "current_team": "Imperial"}, _args())["lambda"] == 30.5
    # not found stays what it was (BO3.gg provisional / plain error), never transient
    monkeypatch.setattr(perf_live, "projection", lambda *a, **k: {"error": "player 'x' (y) not found in HLTV"})
    assert not cs2_scout._perf_input({"player": {"nick": "x"}, "current_team": "y", "recent_maps": []}, _args()).get("transient")


def test_grade_names_the_outage():
    import grader
    d = {"prop": "kills", "line": 30.5, "line_type": "standard", "scope": "12", "maps_played": 40, "kpr": 0.75, "hs_pct": 0.45,
         "sample_maps": 40, "rating": 1.1, "teammate_ratings": [1.0] * 4, "per_map": [], "map_stats": None, "is_igl": False,
         "sniping": 0, "perf": {"error": "HLTV database unreachable right now (retried once)", "transient": True}}
    g = grader.grade_leg(d)
    assert server.perf_transient(g) and g["projection_source"].startswith("legacy")
    assert any("OLD PROJECTION" in f and "unreachable" in f for f in g["flags"])
    assert not server.perf_transient({"projection_source": "perf-2"})
    assert not server.perf_transient({"projection_source": "legacy (performance model unavailable: player 'x' (y) not found in HLTV)"})


def test_board_keeps_last_hltv_grade():
    old = {"source": "HLTV", "grade": "B-", "side": "More", "p": 0.55, "proj": 30.52, "at": 1000.0}
    T = {"projection_source": "legacy (performance model unavailable: HLTV database unreachable right now (retried once): x)"}
    kept = server.keep_last_hltv_grade(old, T, now=2000.0)
    assert kept["side"] == "More" and kept["proj"] == 30.52 and kept["hltv_retry"]
    assert kept["at"] == 2000.0 - server.BOARD_EVERY + server.HLTV_RETRY      # due again in HLTV_RETRY s, not BOARD_EVERY
    assert server.keep_last_hltv_grade(old, {"projection_source": "perf-2"}) is None                  # a real grade replaces it
    assert server.keep_last_hltv_grade({"source": "older model (BO3.gg)", "grade": "B"}, T) is not None   # any grade of the line beats churn
    assert server.keep_last_hltv_grade(None, T) is None                 # nothing to keep
