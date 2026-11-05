"""HLTV map chances (2026-11-04): the hltv_mapchances reader (hltv.py's read path; fixture shaped from the real schema), the vm-2 shadow entry (eligible
and ineligible paths, frozen identity), the pick-log writer (the shadow never fails or changes a pick row), the card /
research-hub display (weight 0, hidden without a projection) and the prospective map check. Nothing here touches a grade.

Fixture: GenOne vs MAYBE, HLTV match 2400124, as the production tables held it on 2026-11-04 18:13 UTC (read-only probe);
times are shifted relative to the test clock where a test needs a future start."""
import copy
import datetime as dt
import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import hltv
import hltv_mapchances as hmc
import mapchances
import picklog
import vm2

ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime.now(dt.timezone.utc)
CAP = (NOW - dt.timedelta(hours=1)).isoformat()
START = (NOW + dt.timedelta(hours=6)).isoformat(timespec="seconds")

# the real columns (information_schema on the production HLTV database, 2026-11-04) - every SQL below may use only these
SCHEMA = {
    "map_chances": {"matchid", "map", "chance", "position", "updated_at"},
    "map_chances_history": {"matchid", "map", "chance", "seen_at"},
    "simulated_vetoes": {"matchid", "rank", "maps", "chance", "updated_at"},
    "simulated_veto_steps": {"matchid", "rank", "step", "team", "action", "map", "chance"},
    "map_chances_status": {"matchid", "checked_at", "found", "last_error"},
    "upcoming_matches": {"matchid", "time", "team1", "team2", "event", "format", "stars", "live", "title", "url", "scraped_at"},
    "map_player_stats": {"mapstatsid", "matchid", "playerid", "teamid", "sideid", "k", "hs", "a", "fa", "d", "td", "ok", "od", "mk",
                         "clutches", "kast", "adr", "swing", "rating", "ek", "ehs", "ed", "etd", "eok", "eod", "ekast", "eadr"},
    "match_maps": {"matchid", "number", "mapid", "mapstatsid", "url", "start_ct", "picked_by"},
    "maps": {"mapid", "name"},
    "match_overview": {"matchid", "event", "date", "parser_version", "source", "forfeited_maps", "lan"},
}

CHANCES = [("Ancient", 68, 1), ("Dust 2", 68, 2), ("Anubis", 56, 3), ("Cache", 51, 4), ("Nuke", 50, 5), ("Inferno", 7, 6), ("Mirage", 1, 7)]
STEPS1 = [(1, "GenOne", "removed", "Mirage", 56), (2, "MAYBE", "removed", "Inferno", 52), (3, "GenOne", "picked", "Dust 2", 30),
          (4, "MAYBE", "picked", "Ancient", 26), (5, "GenOne", "removed", "Nuke", 17), (6, "MAYBE", "removed", "Cache", 12),
          (7, None, "leftover", "Anubis", 31)]


def mc_row(cap=CAP, start=START, fmt="bo3", chances=CHANCES, found=True, vetoes=True):
    """One _MC_SQL row as psycopg2 returns it (json columns already parsed): chances, vetoes, steps, status, upcoming."""
    ch = [{"map": m, "chance": c, "position": p, "updated_at": cap} for m, c, p in chances] if chances else None
    vs = [{"rank": 1, "maps": ["Dust 2", "Ancient", "Anubis"], "chance": 16, "updated_at": cap},
          {"rank": 2, "maps": ["Dust 2", "Ancient", "Nuke"], "chance": 12, "updated_at": cap}] if vetoes else None
    st = [{"rank": 1, "step": s, "team": t, "action": a, "map": m, "chance": c} for s, t, a, m, c in STEPS1] if vetoes else None
    return (ch, vs, st, {"checked_at": cap, "found": found, "last_error": None},
            {"time": start, "format": fmt, "team1": "GenOne", "team2": "MAYBE"} if start else None)


# per-map kills rows (map name, maps, kills) as _PMK_SQL returns them (shape of the real ZywOo read; numbers are fixtures)
PK_ROWS = [("Anubis", 15, 237), ("Cache", 12, 221), ("Dust 2", 25, 429), ("Inferno", 13, 202), ("Mirage", 17, 301),
           ("Nuke", 13, 234), ("Overpass", 3, 59), ("Ancient", 4, 100)]


class FakeCur:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def execute(self, sql, params=None):
        self.db.calls.append(sql)
        if self.db.fail:
            raise self.db.fail
        if self.db.delay:
            time.sleep(self.db.delay)
        self.sql = sql

    def fetchone(self):
        return self.db.mc

    def fetchall(self):
        return self.db.pk


class FakeDB:
    def __init__(self, mc=None, pk=None, fail=None, delay=0.0):
        self.mc, self.pk, self.fail, self.delay, self.calls = mc, pk if pk is not None else PK_ROWS, fail, delay, []

    def cursor(self):
        return FakeCur(self)

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB(mc=mc_row())
    monkeypatch.setattr(hltv, "dsn", lambda: "postgresql://test")
    monkeypatch.setattr(hltv, "_connect", lambda: fake)
    monkeypatch.setattr(hmc, "_MC", {})
    return fake


# ---------------------------------------------------------------- the SQL only names real columns
def _aliases_ok(sql, aliases):
    bad = []
    for a, col in re.findall(r"\b([a-z]{1,2})\.([a-z_]+)\b", sql):
        if a in aliases and col not in SCHEMA[aliases[a]]:
            bad.append(f"{a}.{col}")
    return bad


def test_every_query_names_only_real_columns():
    assert _aliases_ok(hmc._MC_SQL, {"c": "map_chances", "v": "simulated_vetoes", "s": "simulated_veto_steps",
                                      "st": "map_chances_status", "u": "upcoming_matches"}) == []
    assert _aliases_ok(hmc._PMK_SQL, {"s": "map_player_stats", "mm": "match_maps", "mp": "maps", "mo": "match_overview"}) == []
    assert _aliases_ok(mapchances._REC_SQL, {"c": "map_chances", "u": "upcoming_matches", "v": "simulated_vetoes"}) == []
    assert _aliases_ok(mapchances._PLAYED_SQL, {"mm": "match_maps", "mp": "maps"}) == []
    assert "map_chances_history" not in hmc._MC_SQL + mapchances._REC_SQL       # history = line-movement display only
    assert "sideid = 0" in hmc._PMK_SQL and "mo.date < %(before)s" in hmc._PMK_SQL


# ---------------------------------------------------------------- reader parsing (fixture from the real schema)
def test_parse_map_chances_fixture():
    p = hmc.parse_map_chances(mc_row(), 2400124, "2026-11-04T18:20:00+00:00")
    assert p["ok"] and p["match_id"] == "hltv:2400124" and p["format"] == "bo3" and p["sum"] == 301
    assert [c["map"] for c in p["chances"]] == [m for m, _, _ in CHANCES] and p["chances"][1]["key"] == "dust2"
    assert p["updated_at"] == dt.datetime.fromisoformat(CAP).isoformat() and p["teams"] == ["GenOne", "MAYBE"]
    v1 = p["vetoes"][0]
    assert v1["rank"] == 1 and v1["maps"] == ["Dust 2", "Ancient", "Anubis"] and v1["chance"] == 16
    assert v1["map1"] == {"map": "Dust 2", "key": "dust2", "step": 3, "chance": 30} and len(v1["steps"]) == 7
    assert p["vetoes"][1]["map1"] is None                                  # rank 2 has no steps in this fixture
    # JSON delivered as text (another driver setting) parses the same
    as_text = tuple(json.dumps(x) if x is not None else None for x in mc_row())
    assert hmc.parse_map_chances(as_text, 2400124)["chances"] == p["chances"]


def test_parse_bo1_and_empty_and_no_simulation():
    row = list(mc_row(fmt="bo1"))
    row[2] = [{"rank": 1, "step": s, "team": "A" if s % 2 else "B", "action": "removed", "map": m, "chance": 50}
              for s, m in enumerate(["Ancient", "Anubis", "Cache", "Dust 2", "Inferno", "Nuke"], 1)] + \
             [{"rank": 1, "step": 7, "team": None, "action": "leftover", "map": "Mirage", "chance": 40}]
    assert hmc.parse_map_chances(row, 1)["vetoes"][0]["map1"]["map"] == "Mirage"        # BO1: the left-over map
    empty = hmc.parse_map_chances((None,) * 5, 7)
    assert empty["ok"] is False and empty["reason"] == "no HLTV map chances for this match"
    nf = hmc.parse_map_chances(mc_row(chances=None, found=False, vetoes=False), 7)
    assert nf["ok"] is False and nf["reason"] == "HLTV showed no map simulation for this match"
    assert hmc.parse_map_chances((), 7)["ok"] is False


def test_parse_player_map_kills():
    p = hmc.parse_player_map_kills(PK_ROWS + [("Train", 0, None), (None, 2, 30)])
    assert p["ok"] and p["n"] == 102 and p["kills"] == 1783 and p["maps"]["dust2"] == {"name": "Dust 2", "n": 25, "kills": 429}
    assert hmc.hltv_match_number("hltv:2400124") == 2400124 and hmc.hltv_match_number("hltv-5") == 5
    assert hmc.hltv_match_number("cs2:48b551a2bfc2") is None and hmc.hltv_match_number(None) is None


# ---------------------------------------------------------------- reader: pre-start rule, cache, single flight, failures
def test_map_chances_before_start_and_late(db):
    s = hmc.map_chances("hltv:2400124", START)
    assert s["ok"] and s["captured_at"] == dt.datetime.fromisoformat(CAP).isoformat() and s["start_used"].startswith(START[:16])
    earlier = (NOW + dt.timedelta(hours=2)).isoformat()                    # the forecast's start is earlier than HLTV's
    assert hmc.map_chances("hltv:2400124", earlier)["start_used"].startswith(earlier[:16])
    late = hmc.map_chances("hltv:2400124", (NOW - dt.timedelta(hours=2)).isoformat())
    assert late["ok"] is False and late["late"] and "after the match start" in late["reason"]
    assert len(db.calls) == 1                                              # one query served all three
    assert hmc.map_chances("cs2:abc", START)["reason"] == "match has no HLTV id" and len(db.calls) == 1


def test_map_chances_start_unknown(db):
    db.mc = mc_row(start=None)
    assert hmc.map_chances("hltv:9", None)["reason"] == "match start unknown"
    assert hmc.map_chances("hltv:9", START)["ok"]


def test_single_flight_700_callers_one_query(db):
    db.delay = 0.2
    out, ths = [], [threading.Thread(target=lambda: out.append(hmc.map_chances_snapshot("hltv:2400124"))) for _ in range(700)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert len(out) == 700 and all(o["ok"] for o in out) and len(db.calls) == 1


def test_failure_is_ineligible_and_kept_only_fail_ttl(db, monkeypatch):
    class OperationalError(Exception):
        pass
    db.fail = OperationalError("could not connect to server: Connection refused")
    a = hmc.map_chances("hltv:2400124", START)
    assert a["ok"] is False and a["transient"] and a["reason"] == "HLTV database unreachable" and "postgresql" not in json.dumps(a)
    hmc.map_chances("hltv:2400124", START)
    assert len(db.calls) == 1                                              # within FAIL_TTL: no retry storm
    db.fail = None
    key = ("map_chances", 2400124)
    hmc._MC[key] = (time.time() - hltv.FAIL_TTL - 1,) + hmc._MC[key][1:]
    assert hmc.map_chances("hltv:2400124", START)["ok"] and len(db.calls) == 2   # retried after FAIL_TTL, never a stale number
    pk_fail = FakeDB(fail=OperationalError("timeout"))
    monkeypatch.setattr(hltv, "_connect", lambda: pk_fail)
    p = hmc.player_map_kills(11893, "2026-11-05", 180)
    assert p["ok"] is False and p["transient"]


def test_display_never_waits_on_a_slow_database(db):
    db.delay = 2.0
    t0 = time.time()
    outs, ths = [], [threading.Thread(target=lambda: outs.append(hmc.map_chances_display("hltv:2400124"))) for _ in range(700)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert time.time() - t0 < 2.0 + 1.5 and outs == [None] * 700          # 700 members: nobody waits for the slow read
    assert len(db.calls) == 1                                              # ... and the database sees one query
    time.sleep(2.2)
    assert hmc.map_chances_display("hltv:2400124")["ok"] and len(db.calls) == 1     # the background read filled the cache


def test_display_keeps_the_last_good_answer_but_the_shadow_does_not(db):
    assert hmc.map_chances_display("hltv:2400124")["ok"]                  # cold: one quick read within the wait
    key = ("map_chances", 2400124)
    hmc._MC[key] = (time.time() - hmc.MC_TTL - 1,) + hmc._MC[key][1:]   # due for a refresh
    db.fail = TimeoutError("timed out")
    assert hmc.map_chances_display("hltv:2400124")["ok"]                  # the refresh failed: last good answer shown
    s = hmc.map_chances("hltv:2400124", START)
    assert s["ok"] is False and s["reason"] == "HLTV database unreachable"  # the shadow logs the failure as ineligible
    good = hmc._MC[key][3]
    hmc._MC[key] = hmc._MC[key][:3] + ((time.time() - hmc.MC_STALE_S - 1, good[1]),)
    assert hmc.map_chances_display("hltv:2400124") is None               # too old to show: no block


def test_player_map_kills_cached_per_player_day_window(db):
    a = hmc.player_map_kills(11893, "2026-11-05", 180)
    b = hmc.player_map_kills("11893", dt.date(2026, 11, 5), 180)
    assert a is b and a["ok"] and a["since"] == "2026-05-09" and a["before"] == "2026-11-05" and len(db.calls) == 1
    hmc.player_map_kills(11893, "2026-11-06", 180)
    assert len(db.calls) == 2                                              # another match day = its own window
    assert hmc.player_map_kills(None, "2026-11-05", 180)["reason"] == "no HLTV player id" and len(db.calls) == 2


def test_not_configured_reads_nothing(monkeypatch):
    monkeypatch.setattr(hltv, "dsn", lambda: "")
    monkeypatch.setattr(hltv, "_connect", lambda: 1 / 0)
    assert hmc.map_chances("hltv:1", START)["reason"] == "HLTV database not configured"
    assert hmc.player_map_kills(1, "2026-11-05", 180)["reason"] == "HLTV database not configured"


# ---------------------------------------------------------------- vm-2 shadow entry
P = vm2.load_params()
FROZEN = "vm-2-37ec7435b0"


def gi(prop="kills", scope="12", line=34.5, lam=35.0, version="perf-2"):
    c = 1.457
    return {"prop": prop, "scope": scope, "line": line, "bo": 3,
            "perf": {"version": version, "lambda": lam if prop == "kills" else lam * 0.4, "lambda_kills": lam, "hs_per_kill": 0.4,
                     "sd": c * math.sqrt(lam if prop == "kills" else lam * 0.4), "spread_c_kills": c}}


def snap_ok(**kw):
    return dict(hmc.parse_map_chances(mc_row(**kw), 2400124), captured_at=CAP, start_used=START)


def pk_ok(rows=PK_ROWS):
    return dict(hmc.parse_player_map_kills(rows), player_id=11893, since="2026-05-09", before="2026-11-05")


def test_frozen_identity():
    assert vm2.version(P) == FROZEN and vm2.params_sha(P) == FROZEN.split("-")[-1]
    assert P["window_days"] == 180 and P["min_maps_on_map"] == 5 and P["min_maps_total"] == 10
    assert vm2.params_sha(dict(P, meta={"anything": 1})) == vm2.params_sha(P)          # meta is not hashed
    assert vm2.params_sha(dict(P, window_days=90)) != vm2.params_sha(P)


def test_eligible_maps12_weighting_exact():
    e = vm2.shadow_entry(gi(), snap_ok(), pk_ok(), live_p_more=0.55, logged_at=NOW.isoformat(), match_id="hltv:2400124", player_id=11893)
    assert e["eligible"] is True and e["version"] == FROZEN and e["code_sha"] == vm2.CODE_SHA
    a0 = 1783 / 102
    avg = {"ancient": a0, "dust2": 429 / 25, "anubis": 237 / 15, "cache": 221 / 12, "nuke": 234 / 13, "inferno": 202 / 13, "mirage": 301 / 17}
    w = {"ancient": 68, "dust2": 68, "anubis": 56, "cache": 51, "nuke": 50, "inferno": 7, "mirage": 1}
    per = sum(w[k] * avg[k] for k in w) / sum(w.values())                   # Ancient: 4 maps < 5 -> overall average
    arm, c0 = e["vm-2"], e["C0-pooled"]
    assert abs(arm["per_map_kills"] - per) < 1e-4 and abs(arm["lam"] - 2 * per) < 1e-3
    assert abs(c0["lam"] - 2 * a0) < 1e-3 and e["player"]["per_map"]["Ancient"]["fallback"] == "overall"
    assert abs(e["map_factor"] - per / a0) < 1e-6 and arm["eligible"] and c0["eligible"]
    assert 0.22 <= arm["p_over"] <= 0.78 and abs(arm["delta_p"] - (arm["p_over"] - 0.55)) < 1e-6
    assert e["snapshot"]["chances"]["Dust 2"] == 68 and e["snapshot"]["age_min"] == pytest.approx(60, abs=1)
    assert e["snapshot"]["logged_before_start"] is True
    # the arithmetic is the vm-1 / rm-1 one at the new projection with the live spread
    import cands_shadow as rm
    lam = 2 * per
    o, u, _ = rm.kills_probs(34.5, [(1.0, lam, 1.457 * math.sqrt(35.0) / math.sqrt(35.0) * math.sqrt(lam))])
    assert abs(arm["p_over_uncapped"] - o / (o + u)) < 1e-6


def test_uniform_map_kills_equal_control():
    rows = [(m, 10, 170) for m, _, _ in CHANCES]
    e = vm2.shadow_entry(gi(), snap_ok(), pk_ok(rows))
    assert e["vm-2"]["p_over"] == e["C0-pooled"]["p_over"] and e["map_factor"] == 1.0


def test_map1_uses_likeliest_veto_first_pick_and_logs_step_chance():
    e = vm2.shadow_entry(gi(scope="1", line=17.5, lam=17.5), snap_ok(), pk_ok())
    assert e["eligible"] and e["map1"]["map"] == "Dust 2" and e["map1"]["step"] == 3 and e["map1"]["step_chance"] == 30
    assert e["map1"]["veto_chance"] == 16 and abs(e["vm-2"]["lam"] - 429 / 25) < 1e-3
    no_veto = vm2.shadow_entry(gi(scope="1", line=17.5, lam=17.5), snap_ok(vetoes=False), pk_ok())
    assert no_veto["eligible"] is False and "likeliest simulated veto" in no_veto["reason"]


def test_hs_and_maps123():
    h = vm2.shadow_entry(gi("hs", "12", line=13.5), snap_ok(), pk_ok())
    assert h["eligible"] and abs(h["vm-2"]["lam"] - h["vm-2"]["lam_kills"] * 0.4) < 1e-3 and 0 < h["vm-2"]["p_over"] < 1
    t = vm2.shadow_entry(gi(scope="123", line=51.5, lam=52.0), snap_ok(), pk_ok())
    assert t["eligible"] and abs(t["vm-2"]["lam"] - 3 * t["vm-2"]["per_map_kills"]) < 1e-3


@pytest.mark.parametrize("case,expect", [
    ("no_snapshot", "no HLTV map chances for this match"),
    ("late", "after the match start"),
    ("transient", "HLTV database unreachable"),
    ("bad_sum", "add up to"),
    ("bo1_maps12", "scope longer than the format"),
    ("thin", "fewer than 10 HLTV maps"),
    ("no_player", "no HLTV player id"),
    ("not_cell", "not a vm-2 cell"),
    ("legacy", "live projection not perf-2"),
    ("odd_line", "whole or half"),
])
def test_ineligible_paths(case, expect):
    g, s, p = gi(), snap_ok(), pk_ok()
    if case == "no_snapshot":
        s = hmc.parse_map_chances((None,) * 5, 1)
    elif case == "late":
        s = {"ok": False, "late": True, "reason": "HLTV map chances were updated only at or after the match start", "captured_at": CAP,
             "start_used": CAP}
    elif case == "transient":
        s = {"ok": False, "transient": True, "reason": "HLTV database unreachable"}
    elif case == "bad_sum":
        s = snap_ok(chances=CHANCES[:4])
    elif case == "bo1_maps12":
        s = dict(snap_ok(chances=[("Ancient", 60, 1), ("Nuke", 40, 2)], fmt="bo1"))
    elif case == "thin":
        p = pk_ok([("Anubis", 9, 150)])
    elif case == "no_player":
        p = {"ok": False, "reason": "no HLTV player id"}
    elif case == "not_cell":
        g = dict(gi(), prop="assists")
    elif case == "legacy":
        g = gi(version="legacy")
    elif case == "odd_line":
        g = dict(gi(), line=30.3)
    e = vm2.shadow_entry(g, s, p, live_p_more=0.5)
    assert e["eligible"] is False and expect in e["reason"], e
    assert e["vm-2"]["eligible"] is False and e["C0-pooled"]["eligible"] is False and "p_over" not in e["vm-2"]
    if case == "transient":
        assert e["transient"] is True
    if case == "late":
        assert e["snapshot"]["captured_at"] == CAP


def test_garbage_never_raises():
    e = vm2.shadow_entry({"prop": "kills", "scope": "12", "line": 30.5, "perf": {"version": "perf-2", "lambda": "x", "sd": 1}},
                         snap_ok(), pk_ok())
    assert e["eligible"] is None and e["reason"] == "shadow failed" and e["vm-2"]["eligible"] is None
    assert vm2.shadow(None, None)["eligible"] is False
    assert vm2.shadow({"gi": gi()}, {"error": "RuntimeError"})["eligible"] is None
    assert vm2.prefetch({"gi": {"prop": "kills"}}) == {"snap": None, "pk": None}   # not a cell: no database read


def test_prefetch_skips_player_read_without_snapshot(db):
    db.mc = (None,) * 5
    pre = vm2.prefetch({"match_id": "hltv:3", "start": START, "player_id": 11893, "gi": gi()})
    assert pre["pk"] is None and pre["snap"]["ok"] is False and len(db.calls) == 1
    db.mc = mc_row()
    pre = vm2.prefetch({"match_id": "hltv:2400124", "start": START, "player_id": 11893, "gi": gi()})
    assert pre["snap"]["ok"] and pre["pk"]["ok"] and pre["pk"]["before"] == START[:10] and len(db.calls) == 3


# ---------------------------------------------------------------- the pick-log writer never fails a row
def test_writer_logs_vm2_and_never_changes_the_live_row(db, monkeypatch, tmp_path):
    from tests import test_forecast_record as tfr
    cols = "line, pick, p_pick, p_more, grade, units, lambda, p_win, model_version, inputs, probs_raw, odds_source, odds_sig"
    answer = tfr.tp_answer()
    rows, locked = {}, []
    real_prefetch = vm2.prefetch
    monkeypatch.setattr(vm2, "prefetch", lambda vin, params=None: (locked.append(picklog._LOCK.locked()), real_prefetch(vin, params))[1])
    for mode in ("on", "shadow_raises", "prefetch_raises", "db_down", "off"):
        monkeypatch.setattr(picklog, "DB", tmp_path / f"picks_{mode}.db")
        monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
        monkeypatch.setattr(picklog, "data_revision", lambda: "matches=1")
        monkeypatch.setattr(hmc, "_MC", {})
        db.fail = None
        with monkeypatch.context() as m:
            if mode == "shadow_raises":
                m.setattr(vm2, "shadow", lambda *a, **k: 1 / 0)
            if mode == "prefetch_raises":
                m.setattr(vm2, "prefetch", lambda *a, **k: 1 / 0)
            if mode == "db_down":
                db.fail = TimeoutError("timed out")
            res = tfr.mk_res(copy.deepcopy(answer), nm_over={"slug": "hltv-2400124", "start": START})
            res["hltv"] = {"found": True, "hltv_id": 11922}
            row = picklog.pick_row(res, tfr.verdict(res["grade"]))
            assert row["_vm2"]["match_id"] == "hltv:2400124" and row["_vm2"]["player_id"] == 11922
            if mode == "off":
                row.pop("_vm2")
            pid = picklog.write_row(row)
        con = sqlite3.connect(picklog.DB)
        con.row_factory = sqlite3.Row
        f = con.execute(f"SELECT {cols}, shadow FROM forecasts WHERE pick_id = ?", (pid,)).fetchone()
        assert "_vm2" not in [c[1] for c in con.execute("PRAGMA table_info(picks)")]
        con.close()
        rows[mode] = (tuple(f[c.strip()] for c in cols.split(",")), json.loads(f["shadow"]))
    assert len({r[0] for r in rows.values()}) == 1                          # every live column identical in every mode
    on = rows["on"][1]["vm2"]
    assert on["eligible"] is True and on["version"] == FROZEN and on["match_id"] == "hltv:2400124" and on["player"]["id"] == 11922
    assert on["vm-2"]["p_over"] is not None and on["C0-pooled"]["p_over"] is not None and on["live_p_more"] == rows["on"][0][3]
    assert rows["shadow_raises"][1]["vm2"]["eligible"] is None
    assert rows["prefetch_raises"][1]["vm2"]["eligible"] is None
    assert rows["db_down"][1]["vm2"]["eligible"] is False and rows["db_down"][1]["vm2"]["reason"] == "HLTV database unreachable"
    assert "vm2" not in rows["off"][1] and "vm1" in rows["off"][1]
    assert locked and not any(locked)                                       # the reads happen outside the pick-log lock


# ---------------------------------------------------------------- display (classic card + research hub), weight 0
HEDGE = ("no projection yet", "estimate", "unconfirmed", "provisional", "not confirmed", "may ", "might")


def test_member_payload_and_hidden_without_projection():
    b = mapchances.member_payload(hmc.parse_map_chances(mc_row(), 2400124))
    assert [m["pct"] for m in b["maps"]] == sorted((c for _, c, _ in CHANCES), reverse=True) and b["weight"] == 0
    assert b["note"] == "The grade does not use HLTV map chances yet." and b["veto"]["pct"] == 16
    assert [(x["slot"], x["name"]) for x in b["veto"]["maps"]] == [("Map 1", "Dust 2"), ("Map 2", "Ancient"), ("Decider", "Anubis")]
    assert not any(h in json.dumps(b).lower() for h in HEDGE)
    assert mapchances.member_payload(hmc.parse_map_chances((None,) * 5, 1)) is None
    assert mapchances.member_payload(dict(hmc.parse_map_chances(mc_row(), 1), status={"found": False})) is None
    assert mapchances.member_payload({"ok": False, "reason": "HLTV database unreachable"}) is None


def test_card_and_hub_carry_the_block(db, monkeypatch):
    import server
    import workspace_api
    res = {"next_match": {"slug": "hltv-2400124"}, "player_block": {}}
    assert server.card_map_chances(res)["maps"][0]["name"] in ("Ancient", "Dust 2")
    assert workspace_api._map_chances({"match_id": "hltv:2400124"})["veto"]["pct"] == 16
    assert workspace_api._map_chances({"match_id": "cs2:abc"}) is None
    monkeypatch.setattr(hltv, "dsn", lambda: "")
    assert server.card_map_chances(res) is None                            # nothing to show = no block


def test_pages_render_the_block_without_hedge_labels():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    hub = (ROOT / "web" / "upgrade-hub.js").read_text(encoding="utf-8")
    # 2026-11-04 mw-1: the weight line moved into mapChancesUse (map_weighted labels), called at the end of each block
    for src, fn, use, close in ((html, "function mapChancesPanel", "function mapChancesUse", "\n}\n"),
                                (hub, "function mapChancesHtml", "function mapChancesUse", "\n  }\n")):
        body = src[src.index(fn):src.index(close, src.index(fn))]
        body += src[src.index(use):src.index(close, src.index(use))]
        assert "mapChancesUse(m)" in body
        assert "HLTV map chances" in body and "Weight in the grade: 0." in body and "Likeliest veto" in body
        assert not any(h in body.lower() for h in HEDGE)
    assert "if (nm && d.map_chances) h += mapChancesPanel(d.map_chances)" in html
    assert "mapChancesHtml(doc?.map_chances)" in hub


def test_cs2_shadow_modules_never_import_lol():
    for f in ("vm2.py", "mapchances.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+lol", src, re.M) and "lol_scout" not in src


# ---------------------------------------------------------------- prospective check: last pre-start snapshot vs maps played
def rec_rows(mid, cap, start, chances=CHANCES, veto=("Dust 2", "Ancient", "Anubis")):
    return [(mid, m, c, p, cap, start, "bo3", list(veto) if veto else None, 16) for m, c, p in chances]


@pytest.fixture
def check_db(monkeypatch, tmp_path):
    monkeypatch.setattr(mapchances, "CHECK_DB", tmp_path / "mc.db")
    monkeypatch.setattr(mapchances, "_DB_INIT", [None])
    monkeypatch.setattr(mapchances, "_STATE", dict(mapchances._STATE, report=None, report_at=0.0))
    return tmp_path / "mc.db"


def test_score_match():
    s = mapchances.score_match([list(c) for c in CHANCES], {"maps": ["Dust 2", "Ancient", "Anubis"]}, ["Dust2", "Nuke"], "bo3")
    # top 3 = Ancient, Dust 2, Anubis; played Dust 2 + Nuke -> 1 of 2; top 2 = Ancient, Dust 2 -> 1; Map 1 Dust 2 = Dust2
    assert s == {"n_played": 2, "k": 3, "top_k_hits": 1, "top_n_hits": 1, "hit_rate": 0.5, "map1_hit": True}
    assert mapchances.score_match([list(c) for c in CHANCES], None, [], "bo3") is None
    assert mapchances.score_match([list(c) for c in CHANCES], None, ["Mirage"], None)["k"] == 3        # k from the 301% sum


def test_record_keeps_only_pre_start_snapshots_and_settles(check_db):
    t0 = dt.datetime(2026, 11, 5, 9, 0, tzinfo=dt.timezone.utc)
    early, later, after = t0 - dt.timedelta(hours=5), t0 - dt.timedelta(minutes=20), t0 + dt.timedelta(minutes=5)
    assert mapchances.record(rec_rows(1, early, t0), now=early) == 1
    assert mapchances.record(rec_rows(1, later, t0), now=later) == 1               # a newer pre-start snapshot replaces it
    assert mapchances.record(rec_rows(1, after, None), now=after) == 0             # captured after the start (start remembered)
    assert mapchances.record(rec_rows(2, after, t0), now=after) == 0               # never seen before its start: not recorded
    con = sqlite3.connect(check_db)
    assert con.execute("SELECT captured_at FROM mc_checks WHERE matchid = 1").fetchone()[0] == later.isoformat()
    con.close()
    assert mapchances.settle({1: ["Dust 2", "Ancient", "Nuke"]}, now=t0 + dt.timedelta(hours=1)) == 0   # too early
    assert mapchances.settle({1: ["Dust 2", "Ancient", "Nuke"]}, now=t0 + dt.timedelta(hours=4)) == 1
    rep = mapchances.report()
    assert rep["settled"] == 1 and rep["hit_rate_mean"] == pytest.approx(2 / 3, abs=1e-4) and rep["map1_hit_rate"] == 1.0
    assert rep["last"][0]["matchid"] == 1 and rep["matches"] == {"settled": 1}
    assert mapchances.record(rec_rows(1, later + dt.timedelta(minutes=5), t0), now=later) == 0    # settled rows stay as scored


def test_no_result_after_give_up(check_db):
    t0 = dt.datetime(2026, 11, 5, 9, 0, tzinfo=dt.timezone.utc)
    mapchances.record(rec_rows(5, t0 - dt.timedelta(hours=1), t0), now=t0)
    mapchances.settle({}, now=t0 + dt.timedelta(days=mapchances.GIVE_UP_DAYS + 1))
    assert mapchances.report()["matches"] == {"no result": 1}


def test_maybe_run_is_throttled_and_safe(check_db, monkeypatch):
    monkeypatch.setattr(mapchances, "_STATE", dict(mapchances._STATE, at=0.0, busy=False))
    monkeypatch.setattr(hltv, "dsn", lambda: "postgresql://test")
    monkeypatch.setattr(hltv, "_connect", lambda: (_ for _ in ()).throw(TimeoutError("timed out")))
    out = mapchances.maybe_run()
    assert out["error"] == "TimeoutError" and mapchances.maybe_run() is None        # failed pass: retried next window only
    assert mapchances.report_cached()["settled"] == 0
