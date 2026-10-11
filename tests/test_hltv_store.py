"""Owner 2026-10-11: "keep all the hltv data stored so these outages won't affect anything". Every good HLTV answer is
kept on the data disk; when the database is unreachable - including right after a restart, with empty memory - the
grader uses the stored answer, so the projection is the one it had before the outage."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import hltv, hltv_store, perf_live, records


def test_store_roundtrip_and_age_limit():
    hltv_store.save(("k", 1), {"a": [1, 2], "d": __import__("datetime").date(2026, 10, 11)})
    at, v = hltv_store.load(("k", 1))
    assert v == {"a": [1, 2], "d": __import__("datetime").date(2026, 10, 11)} and time.time() - at < 5
    assert hltv_store.load(("k", 2)) is None
    assert hltv_store.load(("k", 1), max_age_days=-1) is None          # too old = not used
    assert hltv_store.report()["entries"] == 1


def _restart():
    """A deploy / restart: every in-memory cache is empty, only the disk store remains."""
    hltv._CACHE.clear(); perf_live._CACHE.clear(); records._CACHE.clear()
    hltv_store._LAST.clear()


def test_perf_inputs_survive_an_outage_after_a_restart(monkeypatch):
    rows = [(739000 + i, 20 + i % 7, 24, 9, bool(i % 2), True) for i in range(40)]
    monkeypatch.setattr(perf_live, "_CACHE", {})
    calls = {"n": 0}

    def good():
        calls["n"] += 1
        return [5], rows
    assert perf_live._cached(("p", "saadzin", "imperial", "2025-08-01"), good) == ([5], rows)
    _restart()

    def down():
        raise hltv.Unavailable("HLTV database unreachable")
    assert perf_live._cached(("p", "saadzin", "imperial", "2025-08-01"), down) == ([5], rows)   # from disk
    with pytest.raises(hltv.Unavailable):
        perf_live._cached(("p", "nobody", "x", "2025-08-01"), down)                            # never stored: still transient
    def broken():
        raise KeyError("a bug, not an outage")
    perf_live._CACHE.clear()
    with pytest.raises(KeyError):
        perf_live._cached(("p", "saadzin", "imperial", "2025-08-01"), broken)                  # real errors are not hidden


def test_projection_identical_during_outage(monkeypatch):
    rows = [(740000 - i * 3, 18 + (i * 5) % 11, 22 + i % 5, 8 + i % 4, bool(i % 3), True) for i in range(60)]
    opp = [(740000 - i * 2, 70 + i % 9, 24) for i in range(50)]
    state = {"up": True}

    def pm(nick, team, since):
        return perf_live._cached(("p", nick, team, since), lambda: ([7], rows) if state["up"] else (_ for _ in ()).throw(hltv.Unavailable("down")))

    def om(opponent, since):
        return perf_live._cached(("o", opponent, since), lambda: (3, opp) if state["up"] else (_ for _ in ()).throw(hltv.Unavailable("down")))
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://x")
    real_pm, real_om = perf_live.player_maps, perf_live.opp_maps
    monkeypatch.setattr(perf_live, "player_maps", pm)
    monkeypatch.setattr(perf_live, "opp_maps", om)
    before = perf_live.projection("saadzin", "Imperial", "NRG", None, "12", "kills", 740001)
    _restart(); state["up"] = False
    during = perf_live.projection("saadzin", "Imperial", "NRG", None, "12", "kills", 740001)
    assert during["lambda"] == before["lambda"] and during["sd"] == before["sd"] and during["version"] == before["version"]


def test_remember_serves_disk_copy_after_restart():
    key = ("ctx", "saadzin", "imperial", "nrg", ())
    good = {"found": True, "environment": {"tonight": "lan"}, "opp_ranking": {"rank": 30}}
    hltv._remember(key, good)
    _restart()
    out = hltv._remember(key, {"found": False, "transient": True}, ok=False)
    assert out == good and not hltv.lookup_failed(key)               # a good answer is served, not a failure
    assert time.time() - hltv._CACHE[key][0] > hltv.TTL - hltv.FAIL_TTL - 1   # and the database is tried again in 30 s
    k2 = ("ctx", "nobody", "", "", ())
    assert hltv._remember(k2, {"found": False, "transient": True}, ok=False)["transient"] and hltv.lookup_failed(k2)


def test_records_rows_from_disk_after_restart(monkeypatch):
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://x")
    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    class Con:
        def cursor(self): return Cur()
        def close(self): pass
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, n, t: [1])
    monkeypatch.setattr(hltv, "_series_rows", lambda *a, **k: [{"number": 1}, {"number": 2}, {"number": 3}])
    assert len(records._hltv_rows("x", "y", "2026-01-01")) == 3
    _restart()
    monkeypatch.setattr(hltv, "_connect", lambda: (_ for _ in ()).throw(hltv.Unavailable("down")))
    assert len(records._hltv_rows("x", "y", "2026-01-01")) == 3


def test_loose_key_covers_midnight_and_veto():
    hltv_store.save(("p", "saadzin", "imperial", "2025-08-17"), ([5], [(1, 2, 3, 4, True, True)]))
    assert hltv_store.load(("p", "saadzin", "imperial", "2025-08-18"))[1] == ([5], [(1, 2, 3, 4, True, True)])   # next day
    hltv_store.save(("ctx", "saadzin", "imperial", "nrg", ()), {"found": True, "x": 1})
    assert hltv_store.load(("ctx", "saadzin", "imperial", "nrg", ("ancient", "nuke")))[1]["x"] == 1           # maps known now
    assert hltv_store.load(("ctx", "saadzin", "imperial", "mouz", ()))is None                                  # other opponent: no
    assert hltv_store.load(("p", "nitr0", "imperial", "2025-08-17")) is None                                     # other player: no
    assert hltv_store.loose("schedule") is None and hltv_store.loose(("ranks",)) is None
