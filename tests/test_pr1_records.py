"""pr-1 pre-match prediction records (pr1.py): immutability, dedupe, corrections kept apart, exact reconstruction, the flag,
and grades / board / card output byte-identical with the flag on and off. The grader inputs are a TEST FIXTURE (live
structure, names replaced)."""
import copy
import json
import os
import sqlite3
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import pr1

FIX = json.loads((Path(__file__).parent / "fixtures" / "pr1_grader_inputs.json").read_text())


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


START = _iso(datetime.now(timezone.utc) + timedelta(hours=6))


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(pr1, "DB", tmp_path / "pr1_records.db")
    monkeypatch.setattr(pr1, "_READY", [None])
    monkeypatch.setattr(pr1, "_LAST", {})
    monkeypatch.setattr(pr1, "_HCACHE", {})
    monkeypatch.setattr(pr1, "_FETCH_SEEN", {})
    monkeypatch.setattr(pr1, "_RECS_BY_HLTV", {})
    monkeypatch.delenv("PR1_RECORDS", raising=False)
    fetch = {"p": {1: ("2026-11-01", "2026-11-02T10:00:00+00:00", "2026-11-01T22:00:00+00:00"),
                   2: ("2026-11-10", "2026-11-11T09:00:00+00:00", "2026-11-10T21:00:00+00:00")},
             "t": {3: ("2026-11-09", "2026-11-10T08:00:00+00:00", None)}}
    monkeypatch.setattr(pr1, "_hltv_fetch_times", lambda kind, key, since: dict(fetch[kind]))
    monkeypatch.setattr(pr1, "_roles_entry", lambda ids, created: {"t": "2026-11-08T00:00:00+00:00", "status": "verified"})
    monkeypatch.setattr(pr1, "_bo3_entry", lambda pid, at: {"t": None, "status": "unverified", "basis": "test"})
    pr1._db().close()
    return tmp_path


def _res(gi=None, line=27.5, price_over=None, start=START, book="prizepicks", **g_kw):
    import grader
    from pick_verdict import verdict
    gi = copy.deepcopy(gi or FIX["gi"])
    gi["line"] = line
    g = grader.grade_leg(gi)
    g.update(g_kw)
    res = {"grade": g, "grader_inputs": gi, "player_block": {"player": {"nick": "testplayer", "slug": "testplayer", "id": 70001},
                                                            "playing_for": "Team A"},
           "next_match": {"start": start, "opponent": "Team B", "slug": "team-a-vs-team-b-14-11-2026", "event": "Test Event", "bo": 3},
           "generated": pr1._now()}
    if book:
        res["book"] = {"book": book, "market_id": "m-1", "quote": {"board_at": pr1._now(), "updated_at": "2026-11-13T21:22:00+00:00",
                                                                   "over_available": True, "under_available": True,
                                                                   "price_over": price_over, "feed_odds_type": "standard",
                                                                   "anchors": {"built_at": pr1._now()}}}
    return res, verdict(g)


def _write(res, v, source="board", test=2):
    assert pr1.submit(res, v, test=test, source=source)
    assert pr1.flush()


def _rows(sql="SELECT * FROM pr1_records ORDER BY id"):
    con = sqlite3.connect(pr1.DB)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


# ------------------------------------------------------------------ immutability
def test_records_featuresets_and_corrections_are_append_only(db):
    _write(*_res())
    pr1.add_correction(_rows()[0]["id"], "test", note="x")
    con = sqlite3.connect(pr1.DB)
    for sql in ("UPDATE pr1_records SET line = 1", "DELETE FROM pr1_records", "UPDATE pr1_featuresets SET kind = 'x'",
                "DELETE FROM pr1_featuresets", "UPDATE pr1_corrections SET note = 'y'", "DELETE FROM pr1_corrections"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute(sql)
    con.close()
    assert len(_rows()) == 1


# ------------------------------------------------------------------ dedupe
def test_identical_predictions_are_written_once_even_when_read_times_move(db):
    res, v = _res()
    _write(res, v)
    res2, v2 = _res()
    gi = res2["grader_inputs"]
    gi["odds_provenance"]["retrieved_at"] = "2026-11-13T23:00:00+00:00"       # re-polled, same price
    gi["odds_provenance"]["quote_age_s"] = 5.0
    gi["odds_check"]["checked_at"] = "2026-11-13T23:00:01+00:00"
    gi["hltv_series"]["as_of"] = "2026-11-13T23:00:00Z"
    gi["perf"]["hltv_data"]["player_maps"]["age_min"] = 0.1
    gi["perf"]["cm2_live"]["tables_sha"] = gi["perf"]["cm2_live"]["tables_sha"]
    _write(res2, v2)
    assert len(_rows()) == 1 and pr1.STATS["deduped"] >= 1


def test_a_changed_input_offer_or_prediction_writes_a_new_record(db):
    _write(*_res())
    res, v = _res()
    res["grader_inputs"]["kast"] = 70.1                                          # an input value moved, prediction unchanged
    _write(res, v)
    _write(*_res(price_over=-120))                                               # a new offer revision
    _write(*_res(line=28.5))                                                     # another line = another prediction chain
    rows = _rows()
    assert len(rows) == 4
    chains = [r["chain"] for r in rows]
    assert chains[0] == chains[1] == chains[2] != chains[3]
    assert [r["seq"] for r in rows[:3]] == [1, 2, 3]
    assert rows[1]["inputs"] != rows[0]["inputs"] and rows[2]["offer_rev"] != rows[1]["offer_rev"]


def test_repeated_feature_sets_are_stored_once(db):
    _write(*_res())
    _write(*_res(price_over=-120))
    _write(*_res(price_over=-130))
    sets = _rows("SELECT kind, COUNT(*) n FROM pr1_featuresets GROUP BY kind")
    by = {r["kind"]: r["n"] for r in sets}
    assert by["v:perf"] == 1 and by["v:stats"] == 1 and by["v:market"] == 1   # the offer moved, the inputs did not


# ------------------------------------------------------------------ corrections kept apart
def test_corrections_reference_the_record_and_never_change_it(db):
    _write(*_res())
    before = _rows()[0]
    _write(*_res(price_over=-120))                                               # offer moved -> correction on record 1
    after = _rows()[0]
    assert before == after
    corr = _rows("SELECT * FROM pr1_corrections")
    assert len(corr) == 1 and corr[0]["record_id"] == before["id"] and corr[0]["kind"] == "offer_moved"
    with pytest.raises(KeyError):
        pr1.add_correction(999, "x")


def test_rating_snapshot_change_is_a_correction(db):
    _write(*_res())
    res, v = _res()
    res["grader_inputs"]["perf"]["cm2_live"]["tables_sha"] = "ffffffffffffffff"
    res["grader_inputs"]["perf"]["cm2_live"]["inputs"]["elo_own"] += 3.0
    _write(res, v)
    kinds = [c["kind"] for c in _rows("SELECT * FROM pr1_corrections")]
    assert "rating_snapshot_superseded" in kinds and len(_rows()) == 2


# ------------------------------------------------------------------ reconstruction
def test_record_reconstructs_the_exact_inputs_and_prediction(db):
    res, v = _res()
    gi0 = json.loads(json.dumps(res["grader_inputs"]))
    _write(res, v)
    con = pr1._read_db()
    try:
        rec = pr1.expand(con, con.execute("SELECT * FROM pr1_records").fetchone())
    finally:
        con.close()
    assert rec["grader_inputs"] == gi0 and list(rec["grader_inputs"]) == list(gi0)
    out = pr1.reconstruct(rec)
    assert out["ok"], out
    assert out["fields"]["projection"][0] == res["grade"]["lambda_final"] and out["fields"]["side"][0] == v["side"]
    e = FIX["expected"]                                                          # the live forecast this fixture came from
    assert round(rec["projection"], 2) == e["lambda"] and rec["p_more"] == e["p_more"] and rec["letter"] == e["grade"]
    # availability: every source has an entry, HLTV / odds / offer carry times
    a = rec["availability"]
    assert set(a) == {"offer", "odds", "hltv_player", "hltv_opp", "rating", "bo3", "roles", "rank", "maps", "other"}
    assert a["offer"]["status"] == "verified" and a["odds"]["t"] == res["grader_inputs"]["odds_provenance"]["retrieved_at"]
    assert a["hltv_player"]["t"] == "2026-11-11T09:00:00+00:00" and a["hltv_player"]["n"] == 2
    assert a["bo3"]["t"] is None and a["bo3"]["status"] == "unverified"                    # no time -> null + unverified


def test_reconstruct_script_runs_on_a_local_db(db):
    _write(*_res())
    import subprocess
    p = subprocess.run([sys.executable, str(Path(__file__).parent.parent / "scripts" / "pr1_reconstruct.py"), "--db", str(pr1.DB)],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "1 of 1 reproduced exactly" in p.stdout


# ------------------------------------------------------------------ availability rules
def test_hltv_availability_is_verified_only_when_every_feeding_match_is_stamped_before_the_read():
    f = {1: ("2026-11-01", "2026-11-02T00:00:00+00:00", None), 2: ("2026-11-02", "2026-11-03T00:00:00+00:00", None)}
    e = pr1._hltv_entry(f, "2026-11-04T00:00:00Z", "2026-11-04T00:00:01Z")
    assert e["status"] == "verified" and e["t"] == "2026-11-03T00:00:00+00:00"
    assert pr1._hltv_entry({**f, 3: ("2026-11-03", None, None)}, "2026-11-04T00:00:00Z", "x")["status"] == "unverified"
    late = pr1._hltv_entry(f, "2026-11-02T12:00:00Z", "2026-11-02T12:00:01Z")      # match 2 re-fetched after the read
    assert late["status"] == "unverified" and late["after_read"] == 1
    assert pr1._hltv_entry(None, None, "x") == {"t": None, "status": "unverified", "basis": "HLTV database not reachable at record time"}


def test_bo3_cache_key_is_the_one_bo3_uses():
    import hashlib
    import asyncio
    from cs2api import CS2
    seen = {}

    class Api:
        async def _make_request(self, endpoint, params=None):
            seen["k"] = endpoint + "?" + json.dumps(params or {}, sort_keys=True, default=str)
            return {}
    c = CS2.__new__(CS2)
    c._api = Api()
    asyncio.run(c.get_player_matches(12345))
    assert pr1.bo3_key(12345) == hashlib.sha1(seen["k"].encode()).hexdigest()


# ------------------------------------------------------------------ scope of what is written
def test_after_start_owner_tests_and_flag_off_write_nothing(db, monkeypatch):
    _write(*_res(start=_iso(datetime.now(timezone.utc) - timedelta(minutes=1))))
    assert _rows() == [] and pr1.STATS["after_start"] >= 1
    res, v = _res()
    assert pr1.submit(res, v, test=1, source="member") is False                 # owner load test
    monkeypatch.setenv("PR1_RECORDS", "0")
    assert pr1.submit(res, v, test=2) is False
    assert pr1.submit(res, {"state": "SCOUT"}, test=2) is False
    pr1.flush()
    assert _rows() == []


def test_member_and_board_grades_are_separate_chains(db):
    _write(*_res())
    _write(*_res(book=None), source="member", test=0)
    rows = _rows()
    assert len(rows) == 2 and rows[0]["app"] == "prizepicks" and rows[1]["app"] == "member" and rows[1]["offer_rev"] is None


def test_admin_page_is_bounded(db):
    for i in range(3):
        _write(*_res(line=20.5 + i))
    p = pr1.page(limit=10 ** 6)
    assert p["page_max"] == pr1.PAGE_MAX and len(p["records"]) == 3 and p["done"]
    p = pr1.page(limit=2)
    assert len(p["records"]) == 2 and not p["done"]
    p2 = pr1.page(after_id=p["next_after"], cutoff_id=p["cutoff_id"], verify=True)
    assert len(p2["records"]) == 1 and p2["records"][0]["reconstruct"]["ok"] and p2["page_max"] == pr1.PAGE_MAX_FULL
    st = pr1.status()
    assert st["records"] == 3 and st["db_bytes"] > 0


# ------------------------------------------------------------------ the grade is never touched
def test_submit_never_changes_the_grade_or_verdict(db):
    res, v = _res()
    b_res, b_v = json.dumps(res, sort_keys=True, default=str).encode(), json.dumps(v, sort_keys=True).encode()
    _write(res, v)
    assert json.dumps(res, sort_keys=True, default=str).encode() == b_res
    assert json.dumps(v, sort_keys=True).encode() == b_v


_CLOCK = ("at", "graded_at", "generated", "updated", "board_at", "built_at", "ms", "timings", "server_ms")


def _mask(o):
    if isinstance(o, dict):
        return {k: ("<clock>" if k in _CLOCK else _mask(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [_mask(x) for x in o]
    return o


def _board_and_card(monkeypatch, flag):
    """One board pass and one member card through server.py with cs2_scout.grade stubbed to a real grade of the fixture."""
    import cs2_scout
    import picklog
    import prizepicks
    import server
    import underdog
    from tests.test_audit13_site import get, site as _site  # noqa: F401
    monkeypatch.setenv("PR1_RECORDS", flag)
    monkeypatch.setattr(server, "BOARD", {})
    monkeypatch.setattr(server, "_BS_CACHE", [0.0, []])
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "_INFLIGHT", {})
    monkeypatch.setattr(server, "_HITS", {})
    import whop_auth
    monkeypatch.setattr(whop_auth, "enabled", lambda: False)
    for k in ("REQUIRE_WHOP", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(k, raising=False)
    row = {"id": "ud-pr1", "scope": "12", "player": "testplayer", "team": "Team A", "opponent": "Team B", "matchup": "A vs B",
           "start": START, "prop": "kills", "line": 27.5, "alt": False, "under_available": True, "over_available": True}
    monkeypatch.setattr(underdog, "board", lambda wait=False: {"at": time.time(), "rows": [dict(row)], "error": None})
    monkeypatch.setattr(prizepicks, "board", lambda wait=False, now=None: {"state": "ok", "at": time.time(), "rows": [], "error": None})
    monkeypatch.setattr(underdog, "line_for", lambda *a, **k: None)
    monkeypatch.setattr(server, "_board_load", lambda: None)
    monkeypatch.setattr(server, "_board_save", lambda: None)
    for name in ("maybe_batch", "settle_all"):
        monkeypatch.setattr(server.slip_batches, name, lambda *a, **k: None)
    monkeypatch.setattr(server.slips, "log_recommended", lambda *a, **k: None)
    monkeypatch.setattr(server.roles, "refresh_hltv", lambda *a, **k: None)
    monkeypatch.setattr(server.roles, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(server, "mark_warm", lambda *a: None)
    res, _ = _res(book=None)
    res["generated"] = "2026-11-13T00:00:00+00:00"
    frozen = json.dumps(res, default=str)
    monkeypatch.setattr(cs2_scout, "grade", lambda *a, **k: json.loads(frozen))
    logged = []
    monkeypatch.setattr(picklog, "log_async", lambda r, v, test=False: logged.append(json.dumps([r.get("grade"), v], sort_keys=True, default=str)))

    class Stop(BaseException):
        pass
    n = [0]

    def fake_sleep(s):
        n[0] += 1
        if n[0] > 1:
            raise Stop()
    monkeypatch.setattr(server, "time", types.SimpleNamespace(time=time.time, sleep=fake_sleep))
    with pytest.raises(Stop):
        server.board_forever()
    monkeypatch.setattr(server, "time", time)
    board = json.dumps(_mask(server.board_payload()), sort_keys=True, default=str).encode()
    h = get("/api/grade?player=testplayer&line=27.5&prop=kills&maps=12&book=underdog")
    card = json.dumps(_mask(h.body()), sort_keys=True, default=str).encode()
    pr1.flush()
    return board, card, logged


def test_grades_board_and_card_are_byte_identical_with_the_flag_on_and_off(db, monkeypatch):
    on = _board_and_card(monkeypatch, "1")
    n_on = len(_rows())
    off = _board_and_card(monkeypatch, "0")
    assert n_on >= 1 and len(_rows()) == n_on                                    # records only with the flag on
    assert on[0] == off[0]                                                       # board payload
    assert on[1] == off[1]                                                       # member card
    assert on[2] == off[2] and on[2]                                             # the grades / verdicts the pick log received
    b, c = json.loads(on[0]), json.loads(on[1])
    assert len(b["rows"]) == 1 and b["rows"][0]["side"] in ("More", "Less") and c["verdict"]["side"] in ("Over", "Under", "More", "Less")
