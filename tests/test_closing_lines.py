"""SCOUT_CLOSING_LINES (closing_lines.py): append-only closing line at lock, CLV on admin diagnostics, L10 vs closing in the
research drawer. Flag off: nothing is created and no response changes. Every upstream is a stub or a temporary database."""
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import closing_lines as cl
import diagnostics
import picklog
import server
import workspace_api
from tests.test_audit13_site import get as site_get, site  # noqa: F401  (site = the shared fixture)
from tests.test_workspace_api import env, get  # noqa: F401  (env = the workspace fixture)

ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc).timestamp()
HEDGE = ("no projection yet", "estimate", "unconfirmed", "provisional", "not confirmed", "may ", "might")
FORBIDDEN = ("not a bet", "not betting", "financial advice", "place a bet", "places a bet", "roi", "profit", "return")


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.delenv(cl.FLAG, raising=False)
    cl.reset()
    yield tmp_path
    cl.reset()


def offer(nick="vargo", line=38.5, book="underdog", match="hltv:900", lock=T0, prop="kills", scope="12", **kw):
    r = {"id": f"{book}-{nick}-{prop}-{match}", "book": book, "nick": nick, "player": nick, "team": "Halcyon", "opponent": "Norrland",
         "prop": prop, "scope": scope, "line": line, "match_id": match, "match_slug": f"slug-{match}", "start": iso(lock), "lock_at": iso(lock),
         "higher": 1.0, "lower": 1.0, "price_higher": 1.9, "price_lower": 1.9, "over_available": True, "under_available": True, "alt": False}
    r.update(kw)
    return r


def saved(tmp):
    con = sqlite3.connect(str(tmp / "closing_lines.db"))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM closing_lines ORDER BY id")]
    finally:
        con.close()


# ---------------------------------------------------------------------------------------------------- flag off
def test_off_observes_nothing_and_creates_no_database(data):
    assert cl.observe("underdog", [offer()], now=T0 - 600) == 0
    assert cl.observe("underdog", [offer()], now=T0 + 60) == 0
    assert cl.flush(now=T0 + 60) == 0 and cl.pending_count() == 0
    assert not (data / "closing_lines.db").exists()
    assert cl.report_cached(picklog._read_db) is None
    assert cl.rows_for("vargo", "kills", "12", "underdog", "standard") == []      # a read never creates it either
    assert not (data / "closing_lines.db").exists()


def test_off_graded_board_rebuild_never_imports_the_store(data, monkeypatch):
    monkeypatch.setattr(server, "_GB", {})
    monkeypatch.setattr(server, "board_payload", lambda book="underdog": {"rows": [offer()]})
    monkeypatch.setattr(cl, "observe", lambda *a, **k: pytest.fail("observed with the flag off"))
    assert server.graded_board("underdog")["rows"][0]["nick"] == "vargo"
    assert not (data / "closing_lines.db").exists()


# ---------------------------------------------------------------------------------------------------- capture at lock
def test_last_pre_lock_line_is_saved_once_at_lock(data, monkeypatch):
    monkeypatch.setenv(cl.FLAG, "1")
    assert cl.observe("underdog", [offer(line=38.5)], now=T0 - 3600) == 0
    assert cl.observe("underdog", [offer(line=39.5, higher=None, over_available=False)], now=T0 - 300) == 0
    assert cl.pending_count() == 1 and not (data / "closing_lines.db").exists()
    assert cl.observe("underdog", [offer(line=41.5)], now=T0 + 30) == 1        # after lock: the post-lock 41.5 never counts
    assert cl.observe("underdog", [offer(line=42.5)], now=T0 + 90) == 0        # a started row still on the board: no second row
    (r,) = saved(data)
    assert r["line"] == 39.5 and r["first_line"] == 38.5 and r["observations"] == 2
    assert r["over_offered"] == 0 and r["under_offered"] == 1 and r["event_id"] == "hltv:900" and r["player_key"] == "vargo"
    assert r["last_seen_at"] == iso(T0 - 300) and r["lock_utc"] == iso(T0) and r["captured_at"] == iso(T0 + 30) and r["version"] == cl.VERSION
    assert cl.pending_count() == 0


def test_a_line_pulled_before_lock_is_written_by_the_next_pass(data, monkeypatch):
    monkeypatch.setenv(cl.FLAG, "1")
    cl.observe("prizepicks", [offer(book="prizepicks", line=36.0, mod_higher=1.0, mod_lower=1.0)], now=T0 - 1200)
    assert cl.observe("prizepicks", [], now=T0 - 100) == 0                       # gone from the board, not locked yet
    assert cl.flush(now=T0 + 5) == 1
    assert saved(data)[0]["line"] == 36.0 and saved(data)[0]["book"] == "prizepicks"


def test_store_is_append_only(data, monkeypatch):
    monkeypatch.setenv(cl.FLAG, "1")
    cl.observe("underdog", [offer()], now=T0 - 60)
    cl.flush(now=T0 + 1)
    con = sqlite3.connect(str(data / "closing_lines.db"))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE closing_lines SET line = 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM closing_lines")
    finally:
        con.close()
    cl.reset()                                                                   # a restart that sees the same offer again
    cl.observe("underdog", [offer(line=50.5)], now=T0 - 30)
    assert cl.flush(now=T0 + 2) == 0 and len(saved(data)) == 1 and saved(data)[0]["line"] == 38.5


def test_alternates_skipped_goblins_and_maps_are_their_own_offers(data, monkeypatch):
    monkeypatch.setenv(cl.FLAG, "1")
    rows = [offer(line=38.5), offer(line=33.5, alt=True), offer(line=21.5, scope="1"),
            offer(book="prizepicks", line=38.5), offer(book="prizepicks", line=34.5, odds_type="goblin", alt=True),
            offer(line=None), offer(nick="x", start=None, lock_at=None), offer(nick="")]
    cl.observe("underdog", rows, now=T0 - 60)
    assert cl.flush(now=T0 + 1) == 4
    got = {(r["book"], r["scope"], r["line_type"]): r["line"] for r in saved(data)}
    assert got == {("underdog", "12", "standard"): 38.5, ("underdog", "1", "standard"): 21.5,
                   ("prizepicks", "12", "standard"): 38.5, ("prizepicks", "12", "goblin"): 34.5}


def test_memory_is_bounded(data, monkeypatch):
    monkeypatch.setenv(cl.FLAG, "1")
    monkeypatch.setattr(cl, "PENDING_MAX", 5)
    cl.observe("underdog", [offer(nick=f"p{i}") for i in range(12)], now=T0 - 60)
    assert cl.pending_count() == 5


def test_graded_board_hook_observes_each_fresh_copy(data, monkeypatch):
    monkeypatch.setenv(cl.FLAG, "1")
    monkeypatch.setattr(server, "_GB", {})
    seen = []
    monkeypatch.setattr(server, "board_payload", lambda book="underdog": {"rows": [offer(book=book)]})
    monkeypatch.setattr(cl, "observe", lambda book, rows, now=None: seen.append((book, len(rows))))
    server.graded_board("underdog")
    server.graded_board("underdog")                                              # the 5 s copy: not observed twice
    assert seen == [("underdog", 1)]
    monkeypatch.setattr(cl, "observe", lambda *a, **k: 1 / 0)                     # a store failure never breaks the board
    server._GB.clear()
    assert server.graded_board("underdog")["rows"]


def test_store_failure_never_raises(data, monkeypatch):
    monkeypatch.setenv(cl.FLAG, "1")
    real = cl._connect
    monkeypatch.setattr(cl, "_connect", lambda: 1 / 0)
    cl.observe("underdog", [offer()], now=T0 - 60)
    assert cl.observe("underdog", [], now=T0 + 1) == 0 and cl.pending_count() == 1   # kept for the next pass
    monkeypatch.setattr(cl, "_connect", real)
    assert cl.flush(now=T0 + 2) == 1 and saved(data)[0]["line"] == 38.5


# ---------------------------------------------------------------------------------------------------- L10 vs closing
def test_l10_vs_closing_pairs_each_game_with_its_own_match():
    ser = [{"date": f"2026-10-{20 - i:02d}", "opponent": f"T{i}", "value": v} for i, v in enumerate([45, 40, 38, 30, 50, 41, 39, 20, 44, 42, 60])]
    mids = [f"hltv-{i}" for i in range(11)]
    rows = [{"event_id": "hltv:0", "line": 40.5}, {"event_id": "hltv:1", "line": 40.0}, {"event_id": "hltv:3", "line": 33.5},
            {"event_id": "hltv:10", "line": 10.5}, {"event_id": "cs2:x", "line": 1.0}]
    out = cl.l10_vs_closing(ser, mids, rows, platform_label="Underdog")
    assert (out["n"], out["over"], out["under"], out["push"]) == (3, 1, 1, 1)    # game 10 is outside L10
    assert [g["at"] for g in out["games"]] == ["Over", "Push", "Under"] and out["games"][0]["closing"] == 40.5
    assert cl.l10_vs_closing(ser, mids, [{"event_id": "hltv:77", "line": 3}]) is None
    assert cl.l10_vs_closing(ser, None, rows) is None and cl.l10_vs_closing([], [], rows) is None


def _maps():
    maps = []
    for i in range(10):
        val = 25 if i < 6 else 15                      # 6 of 10 over 38.5 at two maps each (the saved L10)
        for n in (1, 2):
            maps.append({"match_id": f"hltv-{i}", "number": n, "date": f"2026-10-{20 - i:02d}", "kills": val, "headshots": 8, "opponent": f"Team {i}", "map": "Mirage"})
    return maps


def _seed_closing(monkeypatch, lines):
    monkeypatch.setenv(cl.FLAG, "1")
    cl.reset()
    for i, line in lines.items():
        lock = T0 - (i + 1) * 86400
        cl.observe("underdog", [offer(match=f"hltv:{i}", line=line, lock=lock)], now=lock - 600)
    cl.flush(now=T0)


def test_line_detail_off_has_no_closing_field_and_the_same_series(env, monkeypatch):
    x = env.by("vargo")
    monkeypatch.setattr(server.hltv, "recent_series", lambda *a, **k: _maps())
    monkeypatch.delenv(cl.FLAG, raising=False)
    st, off = get(env, f"line?book=underdog&id={x['id']}")
    assert st == 200 and "closing" not in off["history"] and set(off["history"]) == {"tally", "avg", "series"}
    assert all(set(g) == {"date", "opponent", "value", "maps", "values"} for g in off["history"]["series"])   # match ids never leak
    _seed_closing(monkeypatch, {0: 55.5})
    monkeypatch.delenv(cl.FLAG)
    server._RESULTS.clear()
    assert get(env, f"line?book=underdog&id={x['id']}")[1] == off               # saved rows, flag off: identical answer


def test_line_detail_on_carries_l10_vs_closing_only_with_data(env, monkeypatch):
    x = env.by("vargo")
    monkeypatch.setattr(server.hltv, "recent_series", lambda *a, **k: _maps())
    monkeypatch.setenv(cl.FLAG, "1")
    cl.reset()
    st, doc = get(env, f"line?book=underdog&id={x['id']}")
    assert st == 200 and "closing" not in doc["history"]                         # flag on, nothing saved yet: no block
    _seed_closing(monkeypatch, {0: 49.5, 1: 50.5, 7: 30.0, 8: 31.5})             # values 50, 50, 30, 30
    server._RESULTS.clear()
    st, doc = get(env, f"line?book=underdog&id={x['id']}")
    c = doc["history"]["closing"]
    assert (c["n"], c["over"], c["under"], c["push"]) == (4, 1, 2, 1) and c["platform_label"] == "Underdog"
    assert doc["history"]["series"][0]["value"] == 50 and "mids" not in doc["history"]
    st, pp = get(env, f"line?book=prizepicks&id={env.by('vargo', 'prizepicks')['id']}")
    assert "closing" not in pp["history"]                                        # another platform's closing lines never mix in


# ---------------------------------------------------------------------------------------------------- CLV on admin diagnostics
@pytest.fixture
def picks(monkeypatch, data):
    monkeypatch.setattr(picklog, "DB", data / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: None)
    con = picklog._db()
    now = time.time()
    lock = now - 3600
    rows = [  # id, player, line, pick, units, test, book, match, scope
        (1, "vargo", 38.5, "More", 0.5, 2, "underdog", "hltv:1", "12"),     # closing 39.5 -> +1.0
        (2, "vargo", 39.5, "More", 0.0, 2, "underdog", "hltv:1", "12"),     # at the closing line -> 0
        (3, "brandr", 34.5, "Less", 1.0, 2, "underdog", "hltv:1", "12"),    # closing 33.5 -> +1.0 for Under (Under 34.5 beat it)
        (4, "orlo", 33.5, "More", 0.5, 2, "underdog", "hltv:2", "12"),      # closing 32.5 -> -1.0
        (5, "orlo", 33.5, "More", 0.5, 0, "prizepicks", "hltv:2", "12"),    # member line on PrizePicks, closing 34.0 -> +0.5
        (6, "orlo", 33.5, "More", 0.5, 1, "underdog", "hltv:2", "12"),      # owner test: excluded
        (7, "orlo", 33.5, "More", 0.5, 0, None, "hltv:2", "12"),            # research line without a platform
        (8, "nobody", 20.5, "More", 0.5, 2, "underdog", "hltv:2", "12"),    # no saved closing line
    ]
    for i, nick, line, side, units, test, book, match, scope in rows:
        con.execute("INSERT INTO picks (id, key, logged_at, player, prop, line, line_type, pick, units, match_slug, match_start, scope, test, book) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (i, f"k{i}", iso(lock - 7200), nick, "kills", line, "standard", side, units, f"slug-{match}", iso(lock), scope, test, book))
    con.commit()
    con.close()
    monkeypatch.setenv(cl.FLAG, "1")
    cl.observe("underdog", [offer("vargo", 39.5, match="hltv:1", lock=lock), offer("brandr", 33.5, match="hltv:1", lock=lock),
                            offer("orlo", 32.5, match="hltv:2", lock=lock)], now=lock - 900)
    cl.observe("prizepicks", [offer("orlo", 34.0, book="prizepicks", match="hltv:2", lock=lock - 7200)], now=lock - 7200 - 3600)
    cl.flush(now=now)
    return data


def test_clv_report_signs_groups_and_exclusions(picks):
    rep = cl.clv_report(picklog._read_db)
    assert rep["closing_saved"] == 4 and rep["closing_stale"] == 1                 # the PrizePicks line was last seen 60 min before lock
    assert rep["picks_unmatched"] == 1 and rep["picks_without_platform"] == 1
    g = {(x["group"], x["book"]): x for x in rep["groups"]}
    assert set(g) == {("board", "underdog"), ("member", "prizepicks")}
    b = g[("board", "underdog")]
    assert b["n"] == 4 and b["matches"] == 2 and b["clv_mean"] == 0.25
    assert (b["moved_toward"], b["unchanged"], b["moved_against"]) == (2, 1, 1)
    assert b["staked_n"] == 3 and b["staked_clv_mean"] == 0.333 and b["lean_n"] == 1 and b["lean_clv_mean"] == 0.0
    assert b["clv_ci95"][0] < 0.25 < b["clv_ci95"][1]
    m = g[("member", "prizepicks")]
    assert m["n"] == 1 and m["clv_mean"] == 0.5 and m["clv_ci95"] is None and m["stale_closing"] == 1
    text = json.dumps(rep).lower()
    assert not any(w in text for w in ("roi", "profit", "units won"))


def test_diagnostics_endpoint_has_clv_only_with_the_flag(site, picks, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "ADMIN_TOKEN", "tok123")
    monkeypatch.setattr(server, "_DIAG", {})
    monkeypatch.setattr(diagnostics, "build_from_log", lambda pl, batches=(), now=None: {"cohorts": {}, "n": 1})
    monkeypatch.setattr(server.slip_batches, "read", lambda: [])
    monkeypatch.setattr(server, "map_chance_check_status", lambda: None)
    monkeypatch.setattr(server, "map1_results_status", lambda: None)
    monkeypatch.setattr(server.letter_containment, "snapshot", lambda: {"version": "lc-1"})     # admin view, its own tests
    on = site_get("/admin/diagnostics.json", headers={"X-Admin-Token": "tok123"}).body()
    assert on["closing_lines"]["groups"] and on["closing_lines"]["version"] == cl.VERSION
    monkeypatch.delenv(cl.FLAG)
    off = site_get("/admin/diagnostics.json", headers={"X-Admin-Token": "tok123"}).body()
    assert "closing_lines" not in off and off == {"cohorts": {}, "n": 1, "map1_results": None, "map_chance_check": None,
                                              "letter_containment": {"version": "lc-1"}}


def test_report_is_cached_and_a_failure_is_stated(picks, monkeypatch):
    calls = []
    real = cl.clv_report
    monkeypatch.setattr(cl, "clv_report", lambda r: calls.append(1) or real(r))
    cl.report_cached(picklog._read_db)
    cl.report_cached(picklog._read_db)
    assert calls == [1]
    cl.reset()
    monkeypatch.setattr(cl, "clv_report", lambda r: 1 / 0)
    assert "failed" in cl.report_cached(picklog._read_db)["error"]


# ---------------------------------------------------------------------------------------------------- pages
def test_drawer_and_diagnostics_render_only_with_data_and_no_hedge_or_return_words():
    hub = (ROOT / "web" / "upgrade-hub.js").read_text(encoding="utf-8")
    i = hub.index("/* L10 vs closing (SCOUT_CLOSING_LINES)")
    body = hub[i:hub.index("const chart=", i)]
    assert "cl?.n?tile(" in body and "const closingTable=cl?.n?" in body and "L10 vs closing" in body
    assert "${hitTiles}${h2hTile}${seasonTile}${closingTile}" in hub and "${closingTable}</section>" in hub
    assert not any(h in body.lower() for h in HEDGE + FORBIDDEN)
    page = (ROOT / "web" / "diagnostics.html").read_text(encoding="utf-8")
    fn = page[page.index("function clvHtml()"):page.index("function fwdHs()")]
    assert "if (!C) return" in fn and "${clvHtml()}" in page
    assert not any(w in fn.lower() for w in ("roi", "profit", "units won"))


def test_closing_store_never_imports_lol_or_sports():
    src = (ROOT / "closing_lines.py").read_text(encoding="utf-8")
    for bad in ("import lol", "from lol", "import sports", "from sports", "import grader", "import picklog", "import cs2_scout"):
        assert bad not in src


# ---------------------------------------------------------------------------------------------------- review 2026-11-04 fixes
def test_a_late_move_hidden_while_it_is_regraded_still_reaches_the_store(data, monkeypatch):
    """Graded at 20.5, the feed moves to 21.5 before lock and the row leaves the graded board until it is regraded: the store
    must hold 21.5 (the platform's line), never the old 20.5. The member payload is unchanged (the row stays hidden)."""
    monkeypatch.setenv(cl.FLAG, "1")
    start = datetime.now(timezone.utc) + timedelta(hours=2)
    raw = {"id": "u-late", "nick": "vargo", "player": "vargo", "prop": "kills", "scope": "12", "line": 21.5, "start": start.isoformat(),
           "higher": 1.0, "lower": 1.0, "alt": False}
    monkeypatch.setattr(server.underdog, "board", lambda wait=False: {"at": time.time(), "error": None, "rows": [dict(raw)]})
    monkeypatch.setitem(server.BOARD, "u-late", {"side": "More", "grade": "B", "graded_line": 20.5, "nick": "vargo", "match_slug": "hltv-901",
                                                  "settle_slug": "hltv-901", "match_start": start.isoformat()})
    monkeypatch.setattr(server, "_CL_MOVED", {})
    out = server.board_payload("underdog")
    assert [r for r in out["rows"] if r.get("id") == "u-late"] == []                # hidden from members as before
    (m,) = server._CL_MOVED["underdog"]
    assert m["line"] == 21.5 and m["lock_at"] and "grade" not in m and "p" not in m
    cl.observe("underdog", [dict(raw, line=20.5, match_id=m["match_id"], match_slug="hltv-901", lock_at=m["lock_at"])], now=time.time() - 60)
    server._closing_observe("underdog", out)
    cl.flush(now=start.timestamp() + 1)
    (r,) = saved(data)
    assert r["line"] == 21.5 and r["first_line"] == 20.5 and r["observations"] == 2
    monkeypatch.delenv(cl.FLAG)
    server._CL_MOVED.clear()
    server.board_payload("underdog")
    assert server._CL_MOVED == {}                                                    # flag off: nothing collected


def test_drawer_leaves_off_a_closing_line_last_seen_long_before_lock():
    """Review 2026-11-04: a 3-hour-old sighting was shown as 'saved at lock'. Stale rows are left off the drawer, never labelled."""
    ser = [{"date": "2026-10-20", "opponent": "T0", "value": 45}, {"date": "2026-10-19", "opponent": "T1", "value": 20}]
    mids = ["hltv-0", "hltv-1"]
    fresh = {"event_id": "hltv:0", "line": 40.5, "lock_utc": iso(T0), "last_seen_at": iso(T0 - 120)}
    stale = {"event_id": "hltv:1", "line": 30.5, "lock_utc": iso(T0), "last_seen_at": iso(T0 - 3 * 3600)}
    out = cl.l10_vs_closing(ser, mids, [fresh, stale])
    assert out["n"] == 1 and out["games"][0]["closing"] == 40.5
    assert cl.l10_vs_closing(ser, mids, [stale]) is None
    assert cl.is_stale(stale) and not cl.is_stale(fresh) and not cl.is_stale({"event_id": "x", "line": 1})


def test_clv_report_keeps_a_board_line_a_member_opened_in_the_board_group(picks):
    """picklog resets test 2 -> 0 when a member opens a board line; on_board keeps the provenance (review 2026-11-04)."""
    con = picklog._db()
    con.execute("UPDATE picks SET test = 0, on_board = 1 WHERE id = 1")
    con.commit()
    con.close()
    g = {(x["group"], x["book"]): x for x in cl.clv_report(picklog._read_db)["groups"]}
    assert g[("board", "underdog")]["n"] == 4 and ("member", "underdog") not in g
