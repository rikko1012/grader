"""2026-10-26 esports site audit: one regression test per fix. No grading code is touched by these fixes; every upstream
is a stub or a temporary database - no network, never production."""
import json
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import picklog
import prizepicks
import server
import underdog
from tests.test_audit13_site import get, site  # noqa: F401  (site = the shared fixture)

ROOT = Path(__file__).resolve().parent.parent
TOK = "tok-audit-1026"


@pytest.fixture
def admin(site):
    site.setattr(server, "ADMIN_TOKEN", TOK)
    return site


def body(h):
    return json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[-1] or b"{}")


# ------------------------------------------------------------------ 1: feed refresh starts once under a member burst
class SlowList(list):
    """A busy flag whose read yields the interpreter: every thread of a burst reads it before any of them sets it."""
    def __getitem__(self, i):
        v = list.__getitem__(self, i)
        time.sleep(0.005)                           # the value read, then a thread switch before the caller acts on it
        return v


class SlowDict(dict):
    def __getitem__(self, k):
        v = dict.__getitem__(self, k)
        if k == "busy":
            time.sleep(0.005)
        return v


def _burst(fn, n=40):
    go = threading.Event()

    def run():
        go.wait()
        fn()
    ts = [threading.Thread(target=run) for _ in range(n)]
    for t in ts:
        t.start()
    go.set()
    for t in ts:
        t.join(10)


def test_1_underdog_stale_board_starts_one_refresh_for_a_burst(monkeypatch):
    calls = []

    def slow_refresh():
        calls.append(1)
        time.sleep(0.3)
        underdog._BUSY[0] = False
    monkeypatch.setattr(underdog, "_refresh", slow_refresh)
    monkeypatch.setattr(underdog, "_BOARD", {"at": 1.0, "rows": [], "error": None, "tried": 1.0})   # loaded long ago: stale
    monkeypatch.setattr(underdog, "_BUSY", SlowList([False]))
    _burst(underdog.board)
    time.sleep(0.4)
    assert len(calls) == 1


def test_1_prizepicks_stale_board_starts_one_load_for_a_burst(monkeypatch):
    calls = []

    def slow_load():
        calls.append(1)
        time.sleep(0.3)
        prizepicks._S["busy"] = False
    monkeypatch.setattr(prizepicks, "_load", slow_load)
    monkeypatch.setattr(prizepicks, "_S", SlowDict(prizepicks._S, at=1.0, tried=1.0, busy=False, rows=[], status={}, error=None))
    _burst(prizepicks.board)
    time.sleep(0.4)
    assert len(calls) == 1


# ------------------------------------------------------------------ 2: /admin/picks.json is a bounded keyset page
@pytest.fixture
def pickdb(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    con = picklog._db()
    for i in range(1, 8):
        con.execute("INSERT INTO picks (id, key, player, prop, line, factors) VALUES (?, ?, ?, 'kills', 29.5, ?)",
                    (i, f"k{i}", f"p{i}", json.dumps({"big": "x" * 3000})))
    con.commit()
    con.close()
    return tmp_path


def test_2_picks_json_pages_and_never_loads_the_whole_log(admin, pickdb):
    admin.setattr(picklog, "PICKS_PAGE_MAX", 3)
    admin.setattr(picklog, "export", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unbounded export")))
    seen, after, cutoff, pages = [], 0, None, 0
    while True:
        h = get(f"/admin/picks.json?token={TOK}&after={after}" + (f"&cutoff={cutoff}" if cutoff else "") + "&limit=999")
        assert h.code == 200
        b = body(h)
        assert len(b["picks"]) <= 3 and b["page_max"] == 3
        seen += [r["id"] for r in b["picks"]]
        after, cutoff, pages = b["next_after"], b["cutoff_id"], pages + 1
        if b["done"]:
            break
        assert pages < 10
    assert seen == list(range(1, 8)) and b["total_in_cutoff"] == 7
    assert body(get(f"/admin/picks.json?token={TOK}"))["picks"][0]["factors"]      # the full row, factors included


# ------------------------------------------------------------------ 3: /admin/ud_moves pages instead of returning the table
def test_3_ud_moves_pages_in_insertion_order(admin, tmp_path):
    admin.setattr(server, "BOARD_FILE", tmp_path / "board.json")
    con = sqlite3.connect(tmp_path / "ud_moves.db")
    underdog._moves_db(con)
    con.executemany("INSERT INTO moves (at, player, line) VALUES (?, ?, ?)",
                    [(f"2026-10-2{i // 4}T00:00:0{i % 4}", f"p{i}", 20.5 + i) for i in range(10)])
    con.commit()
    con.close()
    got, after = [], 0
    for _ in range(10):
        b = body(get(f"/admin/ud_moves?token={TOK}&since=2026-10-21&limit=3&after={after}"))
        assert len(b["rows"]) <= 3 and b["limit"] == 3
        got += [r[1] for r in b["rows"]]
        after = b["next_after"]
        if b["done"]:
            break
    assert got == [f"p{i}" for i in range(4, 10)]                    # since= still filters; every row once, in order
    assert body(get(f"/admin/ud_moves?token={TOK}&limit=999999"))["limit"] == 5000       # a ceiling on the page size


# ------------------------------------------------------------------ 4: malformed admin parameters are a 400 / default, never a 500
def test_4_admin_export_windows_answer_400_with_the_reason(admin):
    for path in ("/admin/hltv_export", "/admin/hltv_sides"):
        for qs, why in (("", "must be dates"), ("&from=2026-10-01", "must be dates"), ("&from=x&to=y", "must be dates"),
                        ("&from=2026-10-10&to=2026-10-01", "before"), ("&from=2026-08-01&to=2026-10-01", "max 31 days")):
            h = get(f"{path}?token={TOK}{qs}")
            assert h.code == 400 and why in body(h)["error"], (path, qs)


def test_4_admin_numeric_parameters_never_crash(admin, tmp_path):
    import comparison
    import hltv
    seen = {}
    admin.setattr(hltv, "veto_backtest", lambda days, limit: seen.setdefault("vb", (days, limit)) and {"ok": True})
    assert get(f"/admin/veto_backtest?token={TOK}&days=abc&limit=-5").code == 200 and seen["vb"] == (30, 1)
    assert get(f"/admin/timings?token={TOK}&n=abc&s=x").code == 200
    snap = tmp_path / "snaps.jsonl"
    snap.write_text("".join(json.dumps({"i": i}) + "\n" for i in range(20)), encoding="utf-8")
    admin.setattr(comparison, "snapshot_path", lambda: snap)
    b = body(get(f"/admin/comparisons.json?token={TOK}&last=abc"))
    assert b["total"] == 20 and len(b["snapshots"]) == 20
    b = body(get(f"/admin/comparisons.json?token={TOK}&last=3"))
    assert [x["i"] for x in b["snapshots"]] == [17, 18, 19] and b["total"] == 20
    b = body(get(f"/admin/comparisons.json?token={TOK}&last=-4"))
    assert [x["i"] for x in b["snapshots"]] == [19]                        # clamped to at least one, never lines[4:]
    lim = {}
    admin.setattr(prizepicks, "history", lambda market=None, limit=500: lim.setdefault("n", limit) and [])
    admin.setattr(prizepicks, "board", lambda now=None, wait=False: {"state": "ok", "rows": []})
    admin.setattr(prizepicks, "ingests", lambda: [])
    assert get(f"/admin/pp_history?token={TOK}&limit=99999999").code == 200 and lim["n"] == 5000


def test_4_qint_and_qdates():
    assert server.qint({}, "n", 7) == 7 and server.qint({"n": "abc"}, "n", 7) == 7 and server.qint({"n": " 12 "}, "n", 7) == 12
    assert server.qint({"n": "-3"}, "n", 7, 1) == 1 and server.qint({"n": "9e9"}, "n", 7, 1, 100) == 7
    assert server.qint({"n": "999"}, "n", 7, 1, 100) == 100
    assert server.qdates({"from": "2026-10-01", "to": "2026-10-31"})[0] is not None
    assert server.qdates({"from": "2026-10-01", "to": "2026-11-02"})[1] == "max 31 days per call"


# ------------------------------------------------------------------ 5: LoL team win chance to 0.1% like every chance on the site
def test_5_lol_team_win_chance_is_shown_to_one_decimal():
    html = (ROOT / "web" / "lol.html").read_text(encoding="utf-8")
    fn = html[html.index("function factorsHtml"):html.index("function factorsHtml") + 1500]
    assert "Math.round(v * 100)" not in fn and 'pw = v => v == null ? "–" : (v * 100).toFixed(1) + "%"' in fn
