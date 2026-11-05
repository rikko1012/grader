"""SCOUT_LINE_HISTORY (lh-1) in the real hub card at 390 px and 1440 px: flag off = the card exactly as before; flag on = the
/line block (every app's moves from the stored history, closing markers on the bars, avg closing line, L10 vs closing) and the
Line history chart, hover detail and every-move table in the member's time zone. LINEHIST_SHOTS=<folder> keeps screenshots."""
import os
import sqlite3
import time
from pathlib import Path

import pytest

import prizepicks
import workspace_linehist as lh
from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401 (fixtures)

SHOTS = Path(os.environ["LINEHIST_SHOTS"]) if os.environ.get("LINEHIST_SHOTS") else None


def _iso(t):
    return lh._iso(t)


def seed(fx, monkeypatch):
    tmp = Path(os.environ["CS2_DATA_DIR"])
    (tmp / ".cache").mkdir(exist_ok=True)
    monkeypatch.setattr(prizepicks, "HISTORY", tmp / ".cache" / "pp_lines.jsonl")
    lh.reset()
    now = time.time()
    ud = next(r for r in fx.rows if r["book"] == "underdog" and r["nick"] == "vargo")
    pp = next(r for r in fx.rows if r["id"] == "pp:7001")
    con = sqlite3.connect(tmp / ".cache" / "ud_moves.db")
    con.execute("CREATE TABLE moves (at TEXT, player TEXT, team TEXT, opponent TEXT, start TEXT, prop TEXT, line REAL, higher REAL, lower REAL, line_id TEXT, scope TEXT)")
    con.executemany("INSERT INTO moves VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        (_iso(now - 9 * 3600), "vargo", "Halcyon", "Norrland", ud["start"], "kills", 36.5, 1.0, 1.0, ud["id"], "12"),
        (_iso(now - 6 * 3600), "vargo", "Halcyon", "Norrland", ud["start"], "kills", 37.5, 1.0, 1.0, ud["id"], "12"),
        (_iso(now - 4 * 3600), "vargo", "Halcyon", "Norrland", ud["start"], "kills", 37.5, 1.06, 0.94, ud["id"], "12")])
    con.commit()
    con.close()
    import json
    key = prizepicks.market_key(pp)
    with open(prizepicks.HISTORY, "w", encoding="utf-8") as f:
        for at, ev, line in ((now - 8 * 3600, "open", 37.5), (now - 5 * 3600, "move", 38.0), (now - 2 * 3600, "move", 38.5)):
            f.write(json.dumps({"at": _iso(at), "book": "prizepicks", "market": key, "line": line, "start": pp["start"], "event": ev}) + "\n")
    import closing_lines as cl
    cl.reset()
    c = cl._connect()
    for b in ("underdog", "prizepicks"):
        for i in range(0, 10, 2):
            c.execute("INSERT INTO closing_lines (ident, version, sport, book, event_id, player_key, prop, scope, line_type, line, lock_utc, last_seen_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (f"{b}-{i}", "cl-1", "cs2", b, f"hltv:{i}", "vargo", "kills", "12", "standard",
                                                           37.5 + i / 2, f"2026-10-{28 - i:02d}T12:00:00+00:00", f"2026-10-{28 - i:02d}T11:59:00+00:00"))
    c.commit()
    c.close()
    return ud


def open_card(c, env, size):
    p = open_page(c, env, "/#all-sports", size)
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    p.locator("#hub-sports [data-id=cs2]").click()
    p.locator("#hub-rows .h-player", has_text="vargo").first.click()
    p.wait_for_selector("#hub-card:not([hidden]) #hub-detail .h-history-chart svg", timeout=20000)
    return p


def line_doc(p, book, rid):
    return p.evaluate("([b,i]) => fetch(`/api/upgrade/line?book=${b}&id=${encodeURIComponent(i)}`).then(r=>r.json())", [book, rid])


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_flag_off_the_card_is_unchanged(browser, env, monkeypatch, size):  # noqa: F811
    monkeypatch.delenv("SCOUT_LINE_HISTORY", raising=False)
    monkeypatch.delenv("SCOUT_CLOSING_LINES", raising=False)
    seed(env, monkeypatch)
    c = context(browser, env, size)
    try:
        p = open_card(c, env, size)
        assert p.locator("#hub-detail .h-lh").count() == 0
        assert p.locator("#hub-detail .close-mark").count() == 0
        assert "Avg closing line" not in p.locator("#hub-detail").inner_text()
        doc = line_doc(p, "underdog", next(r for r in env.rows if r["book"] == "underdog" and r["nick"] == "vargo")["id"])
        assert "line_history" not in doc["history"] and "closing" not in doc["history"]
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_flag_on_line_history_closing_marks_and_moves(browser, env, monkeypatch, size):  # noqa: F811
    monkeypatch.setenv("SCOUT_LINE_HISTORY", "1")
    monkeypatch.delenv("SCOUT_CLOSING_LINES", raising=False)
    ud = seed(env, monkeypatch)
    c = context(browser, env, size)
    try:
        p = open_card(c, env, size)
        doc = line_doc(p, "underdog", ud["id"])
        b = doc["history"]["line_history"]
        u = next(a for a in b["apps"] if a["platform"] == "underdog")
        assert [(x["kind"], x["line"]) for x in u["points"]] == [("open", 36.5), ("move", 37.5), ("payout", 37.5), ("move", 38.5)]
        pp = next(a for a in b["apps"] if a["platform"] == "prizepicks")
        assert [x["line"] for x in pp["points"]] == [37.5, 38.0, 38.5]
        assert b["avg_closing_l10"] == 39.5 and b["closing_n_l10"] == 5 and doc["history"]["closing"]["n"] == 5

        p.wait_for_selector("#hub-detail .h-lh [data-hub-lh] svg .lh-path")
        assert p.locator("#hub-detail .h-lh .lh-path").count() == len(b["apps"]) == 4            # UD, PP, PP Goblin, PP Demon
        assert p.locator("#hub-detail .h-history-chart .close-mark").count() == 5          # the five past games with a closing line
        text = p.locator("#hub-detail").inner_text()
        assert "Avg closing line" in text and "39.5" in text and "L10 vs closing" in text
        zone = p.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
        assert f"times in {zone}" in p.locator("#hub-detail .h-lh .h-sec-head").inner_text()
        tips = p.locator("#hub-detail .h-lh .lh-hit title").evaluate_all("ts => ts.map(t => t.textContent)")
        assert any("payout changed at 37.5" in t and "1.06x more" in t for t in tips)
        assert any(t.startswith("PrizePicks moved to 38.5") for t in tips)
        p.locator("#hub-detail .h-lh-table summary").click()
        assert p.locator("#hub-detail .h-lh-table tbody tr").count() == 9                   # 4 UD + 3 PP + Goblin + Demon
        first = p.locator("#hub-detail .h-lh-table tbody tr").first.inner_text()
        assert "Underdog" in first and "38.5" in first                                     # newest first
        assert "on the board at 34.5" in p.locator("#hub-detail .h-lh-table").inner_text()   # no stored opening: never called one
        no_h_scroll(p)
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)
            p.locator("#hub-detail .h-lh").scroll_into_view_if_needed()
            p.wait_for_timeout(250)
            p.screenshot(path=str(SHOTS / f"line_history-{size['width']}.png"))
            p.locator("#hub-detail .h-history-panel").screenshot(path=str(SHOTS / f"closing_marks-{size['width']}.png"))
            p.locator("#hub-detail .h-lh").screenshot(path=str(SHOTS / f"line_history_block-{size['width']}.png"))
        assert not p.errors
    finally:
        c.close()
