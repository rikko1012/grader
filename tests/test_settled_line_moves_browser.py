"""SCOUT_LINE_MOVE_NOTES in a real browser (real server.Handler, fixture board / pick log / closing_lines.db, no network), 1440 px and
390 px: a past game or a settled pick whose line was bumped between opening and close says so under its row - "Opened 47.5 (Over won)
· closed 48 (push)" - and the result beside it stays as it was. Flag off: nothing changes. MOVE_SHOTS=<folder> keeps screenshots."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import closing_lines as cl
import server
import workspace_api
from tests.test_line_history_browser import line_doc, open_card
from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401 (fixtures)
from tests.workspace_fixture import leg_of

SHOTS = Path(os.environ["MOVE_SHOTS"]) if os.environ.get("MOVE_SHOTS") else None
FLIP0 = "Opened 47.5 (Over won) · closed 48 (push)"              # the B1eany shape: Over won at the opening line, push at the close
FLIP3 = "Opened 29.5 (Over won) · closed 30.5 (Under won)"


def shot(p, name, size):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(200)
        p.screenshot(path=str(SHOTS / f"{name}-{size['width']}.png"), full_page=False)


def flags(monkeypatch, notes=True):
    monkeypatch.setenv("SCOUT_LINE_HISTORY", "1")
    monkeypatch.setenv("SCOUT_CLOSING_LINES", "1")
    if notes:
        monkeypatch.setenv(cl.MOVE_FLAG, "1")
    else:
        monkeypatch.delenv(cl.MOVE_FLAG, raising=False)


def seed_card(line_types=("standard",)):
    """vargo's last games (tests/test_upgrade_browser: 48, 48, 48, 30 ... kills on Maps 1-2) with a saved closing row each."""
    cl.reset()
    c = cl._connect()
    rows = {0: (47.5, 48.0), 1: (46.5, 47.5), 2: (47.5, 47.5), 3: (29.5, 30.5)}          # game -> (opening, closing)
    for b, lt in [("underdog", "standard")] + [("prizepicks", t) for t in line_types]:
        for i, (o, line) in rows.items():
            c.execute("INSERT INTO closing_lines (ident, version, sport, book, event_id, player_key, prop, scope, line_type, line, open_line, lock_utc, "
                      "last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (f"{b}-{lt}-{i}", "cl-1", "cs2", b, f"hltv:{i}", "vargo", "kills", "12",
                                                                         lt, line, o, f"2026-10-{28 - i:02d}T12:00:00+00:00",
                                                                         f"2026-10-{28 - i:02d}T11:59:00+00:00"))
    c.commit()
    c.close()


def next_row_text(p, table, opp):
    return p.evaluate("""([sel, opp]) => { const tr = [...document.querySelectorAll(sel + ' tbody tr')].find(t => !t.classList.contains('h-moved') && t.textContent.includes(opp));
      const n = tr && tr.nextElementSibling; return n && n.classList.contains('h-moved') ? n.innerText.replace(/\\s+/g, ' ').trim() : null; }""", [table, opp])


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_card_past_games_show_the_move_under_their_row(browser, env, monkeypatch, size):  # noqa: F811
    flags(monkeypatch)
    seed_card()
    c = context(browser, env, size)
    try:
        p = open_card(c, env, size)
        ud = next(r for r in env.rows if r["book"] == "underdog" and r["nick"] == "vargo")
        doc = line_doc(p, "underdog", ud["id"])
        games = doc["history"]["closing"]["games"]
        assert [g.get("move", {}).get("kind") for g in games] == ["flip", "bump", None, "flip"]
        assert [g["at"] for g in games] == ["Push", "Over", "Over", "Under"]                    # still at the closing line
        assert doc["history"]["line_history"]["closing_marks"][0]["opened"] == 47.5

        # today's line (38.5): the game row is unchanged, the move sits directly under it
        p.locator("#hub-detail .h-history-panel .h-history-table:not(.h-closing-table) summary").click()
        today = "#hub-detail .h-history-panel .h-history-table:not(.h-closing-table)"
        row0 = p.locator(f"{today} tbody tr:not(.h-moved)").first.inner_text()
        assert "Team0" in row0 and "Over" in row0
        assert next_row_text(p, today, "Team0") == f"▲ bumped {FLIP0}"
        assert next_row_text(p, today, "Team3") == f"▲ bumped {FLIP3}"
        assert next_row_text(p, today, "Team1") == "▲ bumped from 46.5"                      # same result at both lines: the chip only
        assert next_row_text(p, today, "Team2") is None                                        # not moved: nothing
        tip = p.locator(f"{today} tr.h-moved .u-bump").first.get_attribute("title")
        assert tip == "Bumped up: Underdog opened this line at 47.5; it closed at 48."

        # L10 vs closing: "Push" at the closing line stays, with the same sentence under it
        p.locator("#hub-detail .h-closing-table summary").click()
        close = "#hub-detail .h-closing-table"
        first = p.locator(f"{close} tbody tr:not(.h-moved)").first.inner_text()
        assert "Team0" in first and "Push" in first
        assert next_row_text(p, close, "Team0") == f"▲ bumped {FLIP0}"

        # the chart's closing tick says where the line opened
        titles = p.locator("#hub-detail .h-history-chart .close-mark title").evaluate_all("ts => ts.map(t => t.textContent)")
        assert any("Team0: opened 47.5, closed at 48, finished 48 (Over won at 47.5, push at 48)" in t for t in titles)
        assert any("Team1: opened 46.5, closed at 47.5, finished 48 (Over won at both lines)" in t for t in titles)
        assert any("Team2: closed at 47.5, finished 48" in t for t in titles)

        # wraps inside its table at phone width: no page scroll, the note never widens the table
        no_h_scroll(p)
        fit = p.evaluate("""() => [...document.querySelectorAll('#hub-detail tr.h-moved td')].every(td => {
          const box = td.closest('.h-scroll-x') || td.closest('section'); return td.getBoundingClientRect().width <= box.clientWidth + 1; })""")
        assert fit
        text = p.locator("#hub-detail").inner_text().lower()
        assert not [w for w in ("estimate", "unconfirmed", "provisional", "first seen") if w in text.split("research estimate")[0]]
        p.locator(f"{today} tr.h-moved").first.scroll_into_view_if_needed()
        shot(p, "card-move", size)
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_card_flag_off_shows_no_note(browser, env, monkeypatch, size):  # noqa: F811
    flags(monkeypatch, notes=False)
    seed_card()
    c = context(browser, env, size)
    try:
        p = open_card(c, env, size)
        ud = next(r for r in env.rows if r["book"] == "underdog" and r["nick"] == "vargo")
        doc = line_doc(p, "underdog", ud["id"])
        assert all("move" not in g for g in doc["history"]["closing"]["games"])
        assert all("move" not in m and "opened" not in m for m in doc["history"]["line_history"]["closing_marks"])
        p.locator("#hub-detail .h-history-panel .h-history-table:not(.h-closing-table) summary").click()
        p.locator("#hub-detail .h-closing-table summary").click()
        assert p.locator("#hub-detail tr.h-moved").count() == 0 and p.locator("#hub-detail .u-bump").count() == 0
        titles = p.locator("#hub-detail .h-history-chart .close-mark title").evaluate_all("ts => ts.map(t => t.textContent)")
        assert titles and not any("opened" in t for t in titles)
        assert not p.errors
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------- My picks
def api(p, method, path, body=None):
    return p.evaluate("""async ([m, path, body]) => { const r = await fetch('/api/upgrade/' + path, {method: m, headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined}); return [r.status, await r.json()]; }""", [method, path, body])


def tracked(p, env):
    st, doc = api(p, "GET", "board")
    assert st == 200
    r = {(x["player"]["name"], x["offer"]["platform"]): x for x in doc["records"]}
    legs = [leg_of(r[("vargo", "underdog")]), leg_of(r[("orlo", "underdog")])]
    st, out = api(p, "POST", "track", {"idem": "idem-move-000001", "entry_key": "underdog", "entry_type": "standard", "legs": legs})
    assert st == 200, out
    env.settle(env.by("vargo"), actual=39.0)        # Over 38.5: hit; the line closed at 39 (push there)
    env.settle(env.by("orlo"), actual=40.0)         # Over 33.5: hit; opened 32.5, closed 33.5 - Over won at both
    workspace_api.store().settle_once()


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_my_picks_receipt_shows_the_move_beside_its_result(browser, env, monkeypatch, size):  # noqa: F811
    flags(monkeypatch)
    cl.reset()
    c = cl._connect()
    for nick, (o, line) in (("vargo", (38.5, 39.0)), ("orlo", (32.5, 33.5))):
        c.execute("INSERT INTO closing_lines (ident, version, sport, book, event_id, player_key, prop, scope, line_type, line, open_line, lock_utc, last_seen_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (cl.ident(env.by(nick)), "cl-1", "cs2", "underdog", "hltv:900" if nick == "vargo" else "hltv:901",
                                                        nick, "kills", "12", "standard", line, o, "2026-11-07T12:00:00+00:00", "2026-11-07T11:59:00+00:00"))
    c.commit()
    c.close()
    ctx = context(browser, env, size)
    try:
        p = open_page(ctx, env, "/#all-sports", size)
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
        tracked(p, env)
        p.goto(env.url + "/#scout/picks")
        p.wait_for_selector("#hub-picks-body .u-pick", timeout=20000)
        v = p.locator("#hub-picks-body .u-pick", has_text="vargo")
        o = p.locator("#hub-picks-body .u-pick", has_text="orlo")
        assert v.locator(".u-st").inner_text().startswith("Hit")                               # the result stays at the member's line
        assert v.locator(".u-move").is_visible()                                               # beside it, without opening the receipt
        assert " ".join(v.locator(".u-move").inner_text().split()) == "▲ bumped Opened 38.5 (Over won) · closed 39 (push)"
        assert v.locator(".u-move .u-bump").get_attribute("title") == "Bumped up: Underdog opened this line at 38.5; it closed at 39."
        assert v.locator("button").get_attribute("aria-expanded") == "false"
        assert " ".join(o.locator(".u-move").inner_text().split()) == "▲ bumped from 32.5"
        assert "2–0" in p.locator("#hub-picks-body .h-pick-summary").inner_text()               # the record is unchanged
        colors = p.evaluate("""() => { const box = document.createElement('div'); box.innerHTML = '<span class="u-st push">Push</span><span class="u-st miss">Miss</span>';
          document.getElementById('hub-picks-body').appendChild(box); const [a, b] = box.children; const out = [getComputedStyle(a).color, getComputedStyle(b).color]; box.remove(); return out; }""")
        assert colors[0] != colors[1]                                                          # a push never reads as a loss
        no_h_scroll(p)
        shot(p, "picks-move", size)
        assert not p.errors
    finally:
        ctx.close()


# ---------------------------------------------------------------------------------------------------- classic Slip studio history
def history_doc():
    put = {"book": "prizepicks", "match_id": "hltv:900", "nick": "vargo", "prop": "kills", "scope": "12"}
    cl.reset()
    c = cl._connect()
    c.execute("INSERT INTO closing_lines (ident, version, sport, book, event_id, player_key, prop, scope, line_type, line, open_line, lock_utc, last_seen_at) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (cl.ident(put), "cl-1", "cs2", "prizepicks", "hltv:900", "vargo", "kills", "12", "standard",
                                                    39.0, 38.5, "2026-11-07T12:00:00+00:00", "2026-11-07T11:59:00+00:00"))
    c.commit()
    c.close()
    start = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(timespec="seconds")
    legs = [{"nick": "vargo", "player": "vargo", "prop": "kills", "scope": "12", "line": 38.5, "side": "More", "book": "prizepicks", "match_id": "hltv:900",
             "match_slug": "slug-900", "start": start, "team": "Halcyon", "opponent": "Norrland"},
            {"nick": "orlo", "player": "orlo", "prop": "kills", "scope": "12", "line": 33.5, "side": "More", "book": "prizepicks", "match_id": "hltv:901",
             "match_slug": "slug-901", "start": start, "team": "Vanta", "opponent": "Kestrel Five"}]
    batches = [{"batch_id": "20261107T0600", "created_at": start, "slips": {"2": [{"slip_id": "20261107T0600/s1", "n": 2, "book": "prizepicks",
                                                                                   "entry_type": "standard", "legs": legs}]}}]
    import picklog
    real = picklog.rows_for_matches
    picklog.rows_for_matches = lambda slugs, cap=200: [
        {"player": "vargo", "prop": "kills", "scope": "12", "match_slug": "slug-900", "status": "settled", "actual": 39.0, "result": '{"source": "hltv"}'},
        {"player": "orlo", "prop": "kills", "scope": "12", "match_slug": "slug-901", "status": "settled", "actual": 30.0, "result": '{"source": "hltv"}'}]
    try:
        return server.studio_history_payload(batches)
    finally:
        picklog.rows_for_matches = real


@pytest.mark.parametrize("size", [DESK, PHONE])
@pytest.mark.parametrize("notes", [True, False])
def test_classic_studio_history_leg_shows_the_move(browser, env, monkeypatch, size, notes):  # noqa: F811
    flags(monkeypatch, notes=notes)
    doc = history_doc()
    s = doc["batches"][0]["slips"][0]
    assert s["leg_results"] == ["hit", "miss"] and s["outcome"] == "lost"
    assert ("leg_moves" in s) is notes
    c = context(browser, env, size)
    try:
        c.route("**/api/slip_studio/history", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(doc)))
        p = open_page(c, env, "/?view=classic#board", size)
        p.wait_for_selector("#boardout .brow2.crow", timeout=30000)
        p.evaluate("location.hash = '#slips'")
        p.wait_for_selector("#sliphist .aslip.hist", timeout=20000)
        legs = p.locator("#sliphist .aslip.hist .lg")
        assert "✓ hit" in legs.nth(0).inner_text() and "✗ missed" in legs.nth(1).inner_text()
        if notes:
            assert " ".join(legs.nth(0).locator(".lmove").inner_text().split()) == "▲ bumped Opened 38.5 (Over won) · closed 39 (push)"
            assert legs.nth(0).locator(".lbump.up").get_attribute("data-tip") == "Bumped up: PrizePicks opened this line at 38.5; it closed at 39."
            assert legs.nth(1).locator(".lmove").count() == 0
        else:
            assert p.locator("#sliphist .lmove, #sliphist .lbump").count() == 0
        no_h_scroll(p)
        if notes:
            legs.nth(0).scroll_into_view_if_needed()
            shot(p, "studio-move", size)
        assert not p.errors
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------- classic page research drawer
@pytest.mark.parametrize("size", [DESK, PHONE])
def test_classic_drawer_history_table_shows_the_move(browser, env, monkeypatch, size):  # noqa: F811
    flags(monkeypatch)
    seed_card(("standard", "goblin", "demon"))     # the classic row opens its first PrizePicks line type
    c = context(browser, env, size)
    try:
        p = open_page(c, env, "/?view=classic#board", size)
        p.wait_for_selector("#boardout .brow2.crow .u-hits", timeout=30000)
        p.locator("#boardout .brow2.crow").filter(has_text="vargo").first.locator(".u-res-btn").click()
        p.wait_for_selector("#u-drawer .u-chart svg", timeout=15000)
        p.locator("#u-drawer .u-tabs [data-id=history]").click()
        p.wait_for_selector("#u-drawer .u-tbl tr.u-moved")
        rows = p.locator("#u-drawer .u-tbl tbody tr")
        assert "Over" in rows.nth(0).inner_text() and "48" in rows.nth(0).inner_text()      # Against 38.5: unchanged
        assert " ".join(rows.nth(1).inner_text().split()) == f"▲ bumped {FLIP0}"
        assert p.locator("#u-drawer .u-tbl tr.u-moved").count() == 3
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()
