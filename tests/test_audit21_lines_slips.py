"""2026-10-21 audit (lines / slips). ALL DATA HERE ARE LABELLED TEST FIXTURES - never production.
#7   one failed PrizePicks direct read never puts back an OLDER scraper copy over the last direct board
#8   the pre-copy line check reads the platform's own opponent spelling, and team identity follows hltv.same_team
#9   Underdog: a side is offered only when its option is present and active; an adjusted line_type is never standard
#40  Underdog move log keyed per maps: a Maps 1-2 and a Map 1 line of one player log no false moves"""
import copy
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

import comparison
import prizepicks as pp
import underdog

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("HLTV_DB_DSN", raising=False)
    monkeypatch.delenv("HLTV_DATABASE_URL", raising=False)


# ---------------------------------------------------------------- #7 PrizePicks: an older copy never replaces direct
@pytest.fixture
def pp_feed(tmp_path, monkeypatch):
    import hltv
    monkeypatch.setattr(pp, "HISTORY", tmp_path / "h.jsonl")
    monkeypatch.setattr(pp, "SNAPSHOT", tmp_path / "s.json")
    monkeypatch.setattr(pp, "INGEST", tmp_path / "i.jsonl")
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://test-fixture")
    fut = (datetime.now(timezone.utc) + timedelta(hours=6)).replace(microsecond=0)

    def rec(line):
        return {"line_id": "1", "player": "TestPlayer", "team": "Alpha", "opponent": "Bravo", "start_time": fut,
                "market": "kills", "maps": "1-2", "line": line, "odds_type": "standard", "higher": 1.0, "lower": 1.0,
                "stat": "MAPS 1-2 Kills", "updated_at": fut - timedelta(hours=8)}
    copy_ = {"rows": [rec(16.0)], "last_ok": time.time() - 50 * 60}         # the hourly scraper copy, 50 min old
    monkeypatch.setattr(pp, "parse_direct", lambda body, seen: [rec(17.0)])
    monkeypatch.setattr(pp, "_read_db", lambda: ([dict(r) for r in copy_["rows"]],
                                                 {"ok": True, "lines": 1, "source": "prizepicks", "error": None,
                                                  "checked_at": copy_["last_ok"], "last_ok": copy_["last_ok"]}))

    def load(direct_up):
        if direct_up:
            monkeypatch.setattr(pp, "_direct", lambda: {"data": []})
        else:
            def down():
                raise requests.ReadTimeout("TEST FIXTURE read timeout")
            monkeypatch.setattr(pp, "_direct", down)
        pp._S.update(tried=0.0, busy=False)
        pp._load()
    pp._S.update(at=0.0, tried=0.0, rows=[], status={}, error=None, busy=False)
    yield load, copy_, rec
    pp._S.update(at=0.0, tried=0.0, rows=[], status={}, error=None, busy=False, detail={})


def _lines(b):
    return [(r["line"], r["stale"]) for r in b["rows"]]


def test_7_one_failed_direct_read_keeps_the_direct_board(pp_feed, monkeypatch):
    load, copy_, rec = pp_feed
    load(True)
    monkeypatch.setattr(pp, "REFRESH", 10 ** 9)                          # board() only reads _S here
    assert _lines(pp.board()) == [(17.0, False)] and pp.board()["source"] == pp.DIRECT_SOURCE
    at = pp._S["at"]
    load(False)                                                          # one timeout, the copy is older
    b = pp.board()
    assert _lines(b) == [(17.0, False)] and b["state"] == "ok" and b["source"] == pp.DIRECT_SOURCE
    assert pp._S["at"] == at and pp._S["tried"] > 0 and pp._S["busy"] is False
    assert "ReadTimeout" in pp.status_detail()["direct"] and pp.status_detail()["kept"]
    # the kept board ages like any other: stale once the last direct read is older than STALE_MIN
    assert pp.feed_state(now=at + pp.STALE_MIN * 60 + 1) == "stale"


def test_7_a_newer_copy_still_backs_up_a_failed_direct_read(pp_feed):
    load, copy_, rec = pp_feed
    load(True)
    copy_.update(rows=[rec(18.5)], last_ok=time.time() + 5)               # the scraper read after our last direct read
    load(False)
    assert [r["line"] for r in pp._S["rows"]] == [18.5] and "via the HLTV scraper" in pp._S["status"]["source"]


def test_7_no_direct_board_yet_uses_the_copy(pp_feed):
    load, copy_, rec = pp_feed
    load(False)                                                          # a restart while PrizePicks is down
    assert [r["line"] for r in pp._S["rows"]] == [16.0] and "via the HLTV scraper" in pp._S["status"]["source"]


# ---------------------------------------------------------------- #8 pre-copy check: the platform's opponent spelling
def test_8_verify_before_copy_reads_the_platform_opponent(tmp_path, monkeypatch):
    import slip_builder as sb
    monkeypatch.setattr(sb, "DB_PATH", tmp_path / "s.db")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    model = "TEST-MODEL"

    def row(i, match, **kw):
        r = {"id": f"r{i}", "book": "underdog", "player": f"p{i}", "nick": f"p{i}", "player_id": 1000 + i,
             "team": f"T{match}a", "team_name": f"T{match}a", "opponent": f"T{match}b", "opp_name": f"T{match}b",
             "match_slug": f"m{match}", "start": (now + timedelta(hours=3)).isoformat(), "prop": "kills", "scope": "12",
             "line": 30.5, "graded_line": 30.5, "graded_model": model, "side": "More", "p": 0.60, "grade": "B+",
             "units": 1.0, "state": "GRADE", "graded_at": (now - timedelta(minutes=5)).isoformat(), "awp": False,
             "awp_conf": "confirmed", "igl": False, "higher": 1.0, "lower": 1.0, "alt": False, "ud_player_id": f"u{i}"}
        r.update(kw)
        return r
    # the platform spells the opponent 'Fixture Apogee Esports'; our grade's opp_name is 'Fixture'
    rows = [row(0, 0), row(1, 1, opponent="Fixture Apogee Esports", opp_name="Fixture")]
    slip = sb.build(rows, {"sizes": [2], "count": 1}, now=now, model=model)["slips"][2][0]
    feed = {"at": now.timestamp(), "error": None,
            "rows": [{k: r[k] for k in ("id", "player", "team", "opponent", "start", "prop", "scope", "line", "higher",
                                        "lower", "alt", "ud_player_id")} for r in rows]}
    graded = {"book": "underdog", "updated": now.timestamp(), "error": None, "rows": rows, "version": model}
    v = sb.verify_before_copy(slip, {"underdog": {"feed": feed, "graded": graded}}, now=now, model=model)
    assert v["ok"], v["changes"]


def test_8_team_same_follows_canonical_hltv_ids(monkeypatch):
    import matchid
    ids = {"fixturealpha": "team:1", "fixturealphaesports": "team:1", "novaesports": "team:2", "novagaming": "team:3"}
    monkeypatch.setattr(matchid, "ready", lambda: True)
    monkeypatch.setattr(matchid, "team_key", lambda n: ids.get(comparison._norm(n)))
    assert comparison._team_same("Fixture Alpha Esports", "Fixture Alpha") is True       # a verified alias spelling
    assert comparison._team_same("Fixture Alpha", "fixturealpha") is True
    assert comparison._team_same("Nova Esports", "Nova Gaming") is False               # two HLTV teams, shared variant
    assert comparison._team_same("Unknown One", "Unknown One") is True                  # unresolved: exact variants
    assert comparison._team_same("Unknown One", "Unknown Two") is False


# ---------------------------------------------------------------- #9 Underdog: offered sides follow the feed
def _ud_fixture():
    return json.loads((FIX / "underdog_cs2_2026-10-14.json").read_text(encoding="utf-8"))


def test_9_both_sides_active_is_unchanged():
    rows = underdog.parse(_ud_fixture())
    assert rows and all(r["over_available"] is True and r["under_available"] is True and not r["alt"]
                        and r["higher"] == 1.0 and r["lower"] == 1.0 for r in rows)
    assert all(comparison.offer_side(r, "Less", "underdog") and comparison.offer_side(r, "More", "underdog") for r in rows)


def test_9_suspended_or_missing_lower_is_not_offered():
    body = _ud_fixture()
    for ln in body["over_under_lines"][:1]:
        for o in ln["options"]:
            if o["choice"] == "lower":
                o["status"] = "suspended"
    for ln in body["over_under_lines"][1:2]:                     # Higher-only, the shape seen on the same API (LoL fixture)
        ln["options"] = [o for o in ln["options"] if o["choice"] == "higher"]
    rows = {r["id"]: r for r in underdog.parse(body)}
    for ln in body["over_under_lines"][:2]:
        r = rows[ln["id"]]
        assert r["alt"] is False and r["lower"] is None and r["under_available"] is False and r["over_available"] is True
        assert comparison.offer_side(r, "Less", "underdog") is False and comparison.offer_side(r, "More", "underdog") is True
    third = rows[body["over_under_lines"][2]["id"]]
    assert third["under_available"] is True and third["lower"] == 1.0


def test_9_status_less_options_stay_offered():
    body = _ud_fixture()
    for ln in body["over_under_lines"]:
        for o in ln["options"]:
            o.pop("status", None)
    assert all(r["over_available"] and r["under_available"] for r in underdog.parse(body))


def test_9_an_adjusted_line_type_is_never_standard():
    body = _ud_fixture()
    body["over_under_lines"][0]["line_type"] = "adjusted"
    rows = {r["id"]: r for r in underdog.parse(body)}
    assert rows[body["over_under_lines"][0]["id"]]["alt"] is True
    assert not any(rows[ln["id"]]["alt"] for ln in body["over_under_lines"][1:])


def test_9_grader_rule_gets_the_feed_answer():
    """The board loop passes less_available=row['under_available']; grader.py's rule then follows the feed."""
    body = _ud_fixture()
    body["over_under_lines"][0]["options"] = [o for o in body["over_under_lines"][0]["options"] if o["choice"] == "higher"]
    r = next(x for x in underdog.parse(body) if x["id"] == body["over_under_lines"][0]["id"])
    d = {"less_available": r.get("under_available")}
    less_ok = bool(d["less_available"]) if d.get("less_available") is not None else True
    assert less_ok is False


# ---------------------------------------------------------------- #40 Underdog move log per maps
def test_40_two_maps_lines_of_one_player_log_no_false_moves(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    start = "2026-10-19T15:00:00Z"
    rows = [{"alt": False, "player": "Tester Two", "team": "A", "opponent": "B", "start": start, "prop": "kills",
             "scope": "12", "line": 21.5, "higher": 1.0, "lower": 1.0, "id": "x12"},
            {"alt": False, "player": "Tester Two", "team": "A", "opponent": "B", "start": start, "prop": "kills",
             "scope": "1", "line": 10.5, "higher": 1.0, "lower": 1.0, "id": "x1"}]
    db = tmp_path / ".cache" / "ud_moves.db"
    n = lambda: sqlite3.connect(db).execute("SELECT count(*) FROM moves").fetchone()[0]
    monkeypatch.setattr(underdog, "_LAST_SEEN", {})
    for _ in range(3):
        underdog._log_moves(copy.deepcopy(rows))
    assert n() == 2
    monkeypatch.setattr(underdog, "_LAST_SEEN", {})                         # restart: both lines seeded per maps
    underdog._log_moves(copy.deepcopy(rows))
    assert n() == 2
    assert sorted(r[0] for r in sqlite3.connect(db).execute("SELECT scope FROM moves")) == ["1", "12"]


def test_40_an_old_table_without_scope_is_extended(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    (tmp_path / ".cache").mkdir()
    db = tmp_path / ".cache" / "ud_moves.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE moves (at TEXT, player TEXT, team TEXT, opponent TEXT, start TEXT, prop TEXT, line REAL, "
                "higher REAL, lower REAL, line_id TEXT)")
    con.execute("INSERT INTO moves VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("2026-10-19T08:00:00Z", "Tester Two", "", "", "2026-10-19T15:00:00Z", "kills", 10.5, 1, 1, "old"))
    con.commit(); con.close()
    monkeypatch.setattr(underdog, "_LAST_SEEN", {})
    underdog._log_moves([{"alt": False, "player": "Tester Two", "team": "A", "opponent": "B", "start": "2026-10-19T15:00:00Z",
                          "prop": "kills", "scope": "12", "line": 21.5, "higher": 1.0, "lower": 1.0, "id": "x12"}])
    got = list(sqlite3.connect(db).execute("SELECT line_id, scope FROM moves ORDER BY rowid"))
    assert got == [("old", None), ("x12", "12")]                            # the old row is never guessed into a scope
