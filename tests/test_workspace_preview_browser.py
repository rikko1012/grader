"""The research workspace in a real browser against a local server.Handler with fixture data (tests/workspace_fixture.py and
tests/workspace_other_fixture.py): the full flow from research through settlement on all four sports, two devices, real failure
states, and focus and layout. Skipped where Playwright or Chromium is missing. Nothing here reaches a network or production data."""
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import hltv
import server
import underdog
import whop_auth
import workspace_api
from tests.workspace_fixture import Fixture, iso, row
from tests.workspace_other_fixture import Lol, Sports

sync_api = pytest.importorskip("playwright.sync_api")

DESK = {"width": 1440, "height": 900}
TAB = {"width": 1024, "height": 768}
PHONE = {"width": 390, "height": 844}
WIDE = {"width": 1920, "height": 1080}
MIN = 60_000
NBA_ROW = "nba.nba:espn:1.nbaplayerespn301.points.full_game.20.5"
OWNER = "the-owner"
SHOTS = Path(os.environ.get("WORKSPACE_SHOTS") or "")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except sync_api.Error as e:
            pytest.skip(f"chromium missing: {e}")
        yield b
        b.close()


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in ("SPORTS_UI_ALLOW", "REQUIRE_WHOP", "RENDER", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SCOUT_WORKSPACE_PREVIEW", "1")
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: {"user": v[5:], "name": "Tester", "ok": True} if v and v.startswith("good-") else None)
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_ENC_LOCKS", {}), ("_RES_BYTES", [0, 0.0]), ("_MAP1_STATUS", [0.0, None]), ("BOARD", {})):
        monkeypatch.setattr(server, name, val)
    fx = Fixture(tmp_path)
    fx.install(monkeypatch.setattr)
    maps = []
    for i in range(20):
        val = 24 if i % 5 < 3 else 15                  # 6 of 10 over 38.5, the saved tally
        for n in (1, 2):
            maps.append({"match_id": f"hltv-{i}", "number": n, "date": f"2026-10-{28 - i:02d}", "kills": val, "headshots": 8, "opponent": f"Team{i}", "map": "Mirage"})
    monkeypatch.setattr(hltv, "recent_series", lambda *a, **k: maps)
    workspace_api.reset()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    fx.url = f"http://127.0.0.1:{httpd.server_address[1]}/preview/workspace"
    yield fx
    httpd.shutdown()
    httpd.server_close()
    workspace_api.reset()


def context(browser, env, size=DESK, user="u1"):
    c = browser.new_context(viewport=size)
    c.route("https://fonts.*/**", lambda r: r.abort())
    c.add_cookies([{"name": whop_auth.COOKIE, "value": f"good-{user}", "url": env.url}])
    return c


@pytest.fixture
def ctx(browser, env):
    c = context(browser, env)
    yield c
    c.close()


@pytest.fixture
def sports(env, monkeypatch, tmp_path):
    """LoL from a worker folder and NBA and NFL from a stub sports service, reached through the real hook and token path."""
    env.sports = Sports()
    for k, v in env.sports.env().items():
        monkeypatch.setenv(k, v)
    env.lol = Lol(tmp_path / "lol_center")
    monkeypatch.setattr(server, "LOL_CENTER", env.lol.dir)
    monkeypatch.setattr(server, "lol_public", lambda: True)
    monkeypatch.setattr(server, "_LOL_MEMBER", {"mtime": None, "body": None, "lock": threading.Lock()})
    yield env
    env.sports.close()
    env.lol.close()


@pytest.fixture
def octx(browser, env, sports):
    """A member who sees all four sports."""
    c = context(browser, env, user=OWNER)
    yield c
    c.close()


def open_page(ctx, env, path="", size=None, wait=True):
    p = ctx.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    if size:
        p.set_viewport_size(size)
    p.goto(env.url + path)
    if wait:
        p.wait_for_selector("#app[data-boot=done]", state="attached", timeout=15000)
    return p


def all_days(p):
    p.evaluate("""() => { const e = document.getElementById('f-day'); e.value = 'all'; e.dispatchEvent(new Event('change', { bubbles: true })); }""")


def chip(p, sport):
    p.click(f'[data-act="chip"][data-id="{sport}"]')


def refresh(p):
    """What the page does on its 30 s timer and on coming back to the tab."""
    p.evaluate("""() => window.dispatchEvent(new Event('focus'))""")


def fresh():
    server._GB.clear()
    server._RESULTS.clear()


def record_clipboard(p):
    p.evaluate("""() => { window.__clip = []; navigator.clipboard.writeText = (t) => { window.__clip.push(t); return Promise.resolve(); }; }""")


def key_of(p, nick, book="underdog", sport=None):
    return p.evaluate("""async ([n, b, s]) => { const res = await fetch('/preview/workspace/api/board' + (s ? '?sport=' + s : '')), r = await res.json();
      if (!r.records) throw new Error('board answered ' + res.status + ' ' + JSON.stringify(r));
      return r.records.find((x) => x.player.name === n && x.offer.platform === b).key; }""", [nick, book, sport])


def go(p, h):
    p.evaluate("h => { location.hash = h; }", h)
    p.wait_for_timeout(80)


def panel(p, nick, book="underdog", sport=None):
    go(p, "#/research/p/" + key_of(p, nick, book, sport).replace("%", "%25"))
    p.wait_for_selector("#panel-name")
    if p.locator(f'[data-act="p-book"][data-id="{book}"]').count():
        p.locator(f'[data-act="p-book"][data-id="{book}"]').first.click()


def add(p, nick, book="underdog", sport=None):
    panel(p, nick, book, sport)
    p.click('[data-act="p-add"]')
    p.wait_for_function("document.querySelector('[data-act=\"p-add\"]').textContent.includes('Remove')")


def ready(p, scope="#rail-body"):
    p.wait_for_function("s => /ready\\./.test(document.querySelector(s + ' .slip-status')?.textContent || '')", arg=scope)


def active(p):
    return p.evaluate("""() => { const a = document.activeElement; return a.tagName + ':' + (a.dataset.fk || a.id || ''); }""")


def enter(p, sel):
    p.focus(sel)
    p.keyboard.press("Enter")


def state(env, user="u1"):
    s = workspace_api.store()
    return s.get_state(workspace_api.member_key("whop:" + user))


# ---- the full path
def test_full_cs2_flow_from_research_through_settlement(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    p.fill("#search", "vargo")
    p.keyboard.press("Enter")
    assert p.locator(".row").count() == 1
    row = p.locator(".row").first
    assert "61.2%" in row.inner_text() and "B Over" in row.inner_text() and "SAMPLE" not in row.inner_text()
    row.locator(".row-open").click()
    p.wait_for_selector("#panel .chart svg")
    body = p.inner_text("#panel")
    assert "Hit rate, last 10, Over" in body and "Forecast chance, Over" in body and "Market chance, Over" in body
    p.click('[data-act="p-add"]')
    p.keyboard.press("Escape")
    p.fill("#search", "")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    assert "1 of 1 entry ready" in p.inner_text("#rail-body .slip-status")
    entry = p.inner_text("#rail-body .entry")
    assert "Pays 3.5x if every pick hits" in entry and "EV:" in entry
    p.click('#rail-body [data-act="track"][data-id="underdog"]')
    p.wait_for_selector("#rail-body .tracked")
    assert "2 picks tracked on Underdog" in p.inner_text("#rail-body .tracked")
    p.click("#rail-body .tracked a")
    p.wait_for_selector("#view-picks .pick-row")
    text = p.inner_text("#view-picks")
    assert "vargo Over 38.5" in text and "Pending" in text and "Underdog · Standard" in text and "0-0-0" in text
    # settlement, as the pick log would record it
    env.settle(env.by("vargo"), actual=44.0)
    env.settle(env.by("orlo"), actual=20.0)
    workspace_api.store().settle_once()
    p.reload()
    p.wait_for_selector("#view-picks .pick-row")
    text = p.inner_text("#view-picks")
    assert "Final 44, line 38.5" in text and "Final 20, line 33.5" in text and "1-1-0" in text and "Hit" in text and "Miss" in text
    go(p, "#/results")
    p.wait_for_function("document.getElementById('view-results').textContent.includes('Your tracked picks')")
    res = p.inner_text("#view-results")
    assert "Grader board record" in res and "Your tracked picks" in res and "Sample sports" not in res and "1-1-0" in res
    assert p.errors == []


def test_track_needs_no_page_math_the_server_recheck_decides(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    env.by("vargo").update(line=39.5, graded_line=39.5)         # the line moves between the check and the click
    server._GB.clear()
    server._RESULTS.clear()
    p.click('#rail-body [data-act="track"][data-id="underdog"]')
    p.wait_for_selector("#rail-body .problem")
    text = p.inner_text("#rail-body")
    assert "Line moved from 38.5 to 39.5" in text and "Use current offer" in text and p.locator("#rail-body .tracked").count() == 0
    assert workspace_api.store().diagnostics()["counts"]["receipts"] == 0
    env.relog(env.by("vargo"))                                  # the writer logs the regrade at the new line
    server._GB.clear()
    server._RESULTS.clear()
    p.click('#rail-body [data-act="swap-leg"]')
    p.wait_for_function("document.querySelector('#rail-body .leg-pick').textContent.includes('39.5')")
    p.wait_for_function("document.querySelector('#rail-body .slip-status').textContent.includes('1 of 1 entry ready')")
    p.click('#rail-body [data-act="track"][data-id="underdog"]')
    p.wait_for_selector("#rail-body .tracked")


def test_auto_check_runs_on_open_and_on_change(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    p.keyboard.press("Escape")
    ready(p)
    assert "take 2-8 picks" in p.inner_text("#rail-body")      # one leg: the server's rule, with no press of a button
    assert p.is_disabled('#rail-body [data-act="track"][data-id="underdog"]')


def test_failing_entry_comes_first(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo", "prizepicks")
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    heads = p.locator("#rail-body .entry-head").all_inner_texts()
    assert heads[0].startswith("PrizePicks") and heads[1].startswith("Underdog")


def test_entry_type_changes_the_check(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    p.click('#rail-body [data-act="entry-type"][data-id="underdog:flex"]')
    p.wait_for_function("document.getElementById('rail-body').textContent.includes('flex entries take 3-8')")


# ---- mixed slips
def test_mixed_sports_by_platform(octx, env):
    p = open_page(octx, env)
    all_days(p)
    add(p, "vargo", "prizepicks")
    add(p, "Reed Lean", "prizepicks", "nba")
    p.keyboard.press("Escape")
    ready(p)
    entry = p.inner_text("#rail-body .entry")
    assert "CS2 + NBA" in entry and "1 of 1 entry ready" in p.inner_text("#rail-body .slip-status") and not p.is_disabled('#rail-body [data-act="track"][data-id="prizepicks"]')
    p.click('#rail-body [data-act="slip-clear"]')
    add(p, "vargo")
    add(p, "Reed Lean", "underdog", "nba")
    p.keyboard.press("Escape")
    ready(p)
    assert "Underdog does not publish whether one entry can hold CS2 and NBA picks" in p.inner_text("#rail-body .entry")


def test_rules_show_their_source(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo", "prizepicks")
    add(p, "orlo", "prizepicks")
    p.keyboard.press("Escape")
    ready(p)
    p.locator("#rail-body .entry details summary").first.click()
    assert p.locator('#rail-body .entry a[href*="prizepicks.com/help-center/player-picks"]').count() >= 1


# ---- two devices
def test_slip_and_views_follow_the_member_to_another_device(browser, env):
    a_ctx, b_ctx = context(browser, env), context(browser, env, PHONE)
    a = open_page(a_ctx, env)
    add(a, "vargo")
    a.wait_for_timeout(1000)                                    # the save is debounced
    assert [l["player"] for l in state(env)["slip"]["legs"]] == ["vargo"]
    b = open_page(b_ctx, env)
    b.wait_for_function("document.getElementById('slip-fab').textContent.includes('1 pick')")
    a_ctx.close()
    b_ctx.close()


def test_two_devices_editing_at_once_keep_both_legs(browser, env):
    a_ctx, b_ctx = context(browser, env), context(browser, env)
    a, b = open_page(a_ctx, env), open_page(b_ctx, env)
    add(a, "vargo")
    add(b, "orlo")                                              # b still holds rev 0
    a.wait_for_timeout(1500)
    legs = sorted(l["player"] for l in state(env)["slip"]["legs"])
    assert legs == ["orlo", "vargo"]
    b.wait_for_function("document.getElementById('rail-body').textContent.includes('2 picks')")
    a_ctx.close()
    b_ctx.close()


def test_a_leg_removed_on_one_device_stays_removed(browser, env):
    a_ctx, b_ctx = context(browser, env), context(browser, env)
    a = open_page(a_ctx, env)
    add(a, "vargo")
    a.wait_for_timeout(1000)
    b = open_page(b_ctx, env)                                   # b holds vargo at this revision
    b.wait_for_function("document.getElementById('rail-body').textContent.includes('1 pick')")
    a.keyboard.press("Escape")
    a.click('#rail-body [aria-label="Remove vargo Over 38.5"]')
    a.wait_for_timeout(1000)
    add(b, "orlo")                                              # a stale save that still holds vargo
    b.wait_for_timeout(1500)
    assert [l["player"] for l in state(env)["slip"]["legs"]] == ["orlo"]
    a_ctx.close()
    b_ctx.close()


def test_a_failed_first_state_read_recovers_and_keeps_the_visit(browser, env):
    workspace_api.store().put_state(workspace_api.member_key("whop:u1"), 0, {"slip": None})
    a_ctx = context(browser, env)
    a = open_page(a_ctx, env)
    add(a, "vargo")
    a.wait_for_timeout(1000)
    a_ctx.close()
    b_ctx = context(browser, env)
    b_ctx.route("**/preview/workspace/api/state", lambda r: r.abort())
    b = open_page(b_ctx, env)
    add(b, "orlo")
    b.wait_for_timeout(1000)
    assert [l["player"] for l in state(env)["slip"]["legs"]] == ["vargo"]    # nothing saved over the account's slip
    b_ctx.unroute("**/preview/workspace/api/state")
    refresh(b)
    b.wait_for_timeout(1500)
    assert sorted(l["player"] for l in state(env)["slip"]["legs"]) == ["orlo", "vargo"]
    b_ctx.close()


def test_other_members_see_nothing(browser, env):
    a_ctx, b_ctx = context(browser, env, user="alice"), context(browser, env, user="bob")
    a = open_page(a_ctx, env)
    add(a, "vargo")
    add(a, "orlo")
    a.keyboard.press("Escape")
    ready(a)
    a.click('#rail-body [data-act="track"][data-id="underdog"]')
    a.wait_for_selector("#rail-body .tracked")
    b = open_page(b_ctx, env, path="#/picks")
    b.wait_for_selector("#view-picks .rec-strip")
    assert "vargo" not in b.inner_text("#view-picks")
    a_ctx.close()
    b_ctx.close()


# ---- real failure states
def test_board_down_says_so_and_shows_no_rows(ctx, env):
    env.board_ok = False
    p = open_page(ctx, env)
    p.wait_for_selector("#board-list .state")
    assert "CS2 board unavailable" in p.inner_text("#board-list")
    assert p.locator(".row").count() == 0 and p.evaluate("() => !('records' in WORKSPACE_DATA) && !('receipts' in WORKSPACE_DATA)")
    env.board_ok = True
    server._RESULTS.clear()
    p.click('#board-list [data-act="retry-board"]')
    p.wait_for_selector("#app .chip .n")
    chip(p, "cs2")
    all_days(p)
    p.wait_for_function("document.getElementById('board-list').textContent.includes('vargo')")


def test_lost_connection_keeps_the_last_copy_with_its_time(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    ctx.route("**/preview/workspace/api/board", lambda r: r.abort())
    refresh(p)
    p.wait_for_function("document.getElementById('feed-strips').textContent.includes('CS2 lines as of')")
    assert "vargo" in p.inner_text("#board-list") and p.locator('#feed-strips [data-act="retry-board"]').count() == 1


def test_check_outage_says_one_thing_and_keeps_track_off(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    ctx.route("**/preview/workspace/api/recheck", lambda r: r.abort())
    p.click('#rail-body [data-act="check-now"]')
    p.wait_for_function("document.querySelector('#rail-body .slip-status').textContent.includes('did not answer')")
    assert p.is_disabled('#rail-body [data-act="track"][data-id="underdog"]') and p.locator('#rail-body [data-act="check-now"]').count() == 1
    ctx.unroute("**/preview/workspace/api/recheck")
    p.click('#rail-body [data-act="check-now"]')
    ready(p)


def test_busy_track_keeps_the_slip_and_the_retry_is_the_same_press(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    seen = []

    def busy(route):
        seen.append(json.loads(route.request.post_data)["idem"])
        if len(seen) == 1:
            route.fulfill(status=503, content_type="application/json", body='{"error":"busy"}')
        else:
            route.continue_()
    ctx.route("**/preview/workspace/api/track", busy)
    p.click('#rail-body [data-act="track"][data-id="underdog"]')
    p.wait_for_function("document.getElementById('toast').textContent.includes('Grader is busy')")
    assert p.locator("#rail-body .leg").count() == 2
    p.click('#rail-body [data-act="track"][data-id="underdog"]')
    p.wait_for_selector("#rail-body .tracked")
    assert seen[0] == seen[1]


def test_picks_outage_shows_the_saved_copy(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    p.click('#rail-body [data-act="track"][data-id="underdog"]')
    p.wait_for_selector("#rail-body .tracked")
    p.wait_for_timeout(300)
    ctx.route("**/preview/workspace/api/mine*", lambda r: r.abort())
    refresh(p)
    go(p, "#/picks")
    p.wait_for_function("document.getElementById('view-picks').textContent.includes('Saved copy')")
    assert "vargo Over 38.5" in p.inner_text("#view-picks")


def test_skeleton_is_shaped_like_rows_and_has_no_retry_while_loading(ctx, env):
    ctx.route("**/preview/workspace/api/board", lambda r: None)
    p = open_page(ctx, env, wait=False)
    p.wait_for_selector(".skel-row")
    assert p.locator('#board-list [data-act="retry-board"]').count() == 0


# ---- what a row says
def test_last_ten_reads_for_the_side_shown(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    row = p.locator(".row", has_text="brandr").first
    assert "4/10" in row.inner_text()                           # 6-4-0 at this line, shown for the Under
    assert "6-4-0" not in row.inner_text()


def test_book_columns_show_each_platforms_own_line(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    row = p.locator(".row", has_text="orlo").first
    assert row.locator(".c-ud .bc").inner_text().startswith("O")
    assert "34.5" in row.locator(".c-pp .bc.other").inner_text()


def test_best_price_is_marked_only_among_prices_that_compare(octx, env):
    env.sports.boards["nba"][0]["markets"][0]["q"].append({**env.sports.boards["nba"][0]["markets"][0]["q"][2], "p": "betrivers", "r": "offer:betrivers:br:1:r1:aaaaaaaaaa", "d": [1.95, 1.87]})
    p = open_page(octx, env)
    all_days(p)
    assert p.locator(f'.row[data-row="{NBA_ROW}"] .best').count() >= 1
    assert p.locator('.row:has-text("vargo") .best').count() == 0              # one priced book: nothing to compare


def test_every_sport_is_on_the_board_with_its_tag(octx, env):
    p = open_page(octx, env)
    all_days(p)
    assert p.locator("#chips .chip").count() == 5
    for name, tag in (("vargo", "CS2"), ("Harky", "LoL"), ("Karl Anders", "NBA"), ("Sam Field", "NFL")):
        assert tag in p.locator(f'.row:has-text("{name}") .s-tag').first.inner_text(), name


def test_match_groups_and_other_lines(octx, env):
    p = open_page(octx, env)
    all_days(p)
    assert p.locator(".match-head", has_text="Halcyon vs Norrland").count() == 1
    assert p.locator(".row.is-sub").count() >= 1                # one prop at two lines stays two rows


def test_sort_header_works(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    p.click('#board-head [data-act="sort"][data-id="chance"]')
    assert p.locator(".match-head").count() == 0
    first = p.locator(".row .pct").first.inner_text()
    assert first == "61.2%"


def test_decimal_odds_are_not_written_as_multipliers(octx, env):
    p = open_page(octx, env)
    go(p, "#/research/p/" + NBA_ROW + "/books")
    p.wait_for_selector("#panel-name")
    p.wait_for_function("document.querySelector('#panel').textContent.includes('Market chance')")
    body = p.inner_text("#panel")
    assert "1.91" in body and "1.91x" not in body and "0.42 / 0.46" in body and "Different line" in body


# ---- research panel
@pytest.mark.parametrize("size", [DESK, TAB, PHONE])
def test_chart_is_on_the_first_screen(ctx, env, size):
    p = open_page(ctx, env, size=size)
    panel(p, "vargo")
    p.wait_for_selector("#panel .chart svg")
    box = p.locator("#panel .chart").bounding_box()
    assert box["y"] + box["height"] * 0.8 < size["height"]
    assert p.locator("#panel .p-head").bounding_box()["height"] <= 130


def test_chance_shows_once_above_the_fold(ctx, env):
    p = open_page(ctx, env)
    panel(p, "vargo")
    p.wait_for_selector("#panel .chart svg")
    assert p.locator("#panel .hit-strip").inner_text().count("61.2%") == 1


def test_next_and_previous_move_through_the_board(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    chip(p, "cs2")
    p.locator(".row .row-open").first.click()
    p.wait_for_selector("#panel-name")
    one = p.inner_text("#panel-name")
    p.keyboard.press("j")
    p.wait_for_timeout(100)
    two = p.inner_text("#panel-name")
    assert two != one
    p.keyboard.press("k")
    p.wait_for_timeout(100)
    assert p.inner_text("#panel-name") == one
    p.click('[data-act="p-next"]')
    p.wait_for_timeout(100)
    assert p.inner_text("#panel-name") == two


def test_board_stays_live_beside_the_panel_when_there_is_room(ctx, env):
    p = open_page(ctx, env, size=WIDE)
    all_days(p)
    chip(p, "cs2")
    p.locator(".row .row-open").first.click()
    p.wait_for_selector("#panel-name")
    assert p.evaluate("document.getElementById('main').inert") is False and p.locator("#panel-scrim").is_hidden()
    one = p.inner_text("#panel-name")
    p.locator(".row .row-open").nth(1).click()
    p.wait_for_timeout(150)
    assert p.inner_text("#panel-name") != one and p.locator('.row[data-open="true"]').count() == 1


def test_line_picker_and_remove_toggle(octx, env):
    p = open_page(octx, env)
    all_days(p)
    panel(p, "Jay Brown", "prizepicks", "nba")
    p.wait_for_selector(".line-pick")
    p.click('[data-act="p-side"][data-id="over"]')
    p.click('[data-act="p-add"]')
    assert "Remove from slip" in p.inner_text(".p-foot")
    p.click('[data-act="p-add"]')
    assert "Add Over 24.5" in p.inner_text(".p-foot") and p.locator("#rail-body .leg").count() == 0
    p.locator(".line-pick button", has_text="25.5").click()
    p.wait_for_timeout(100)
    assert "Add Over 25.5" in p.inner_text(".p-foot")


def test_history_tab_splits_by_map(ctx, env):
    p = open_page(ctx, env)
    panel(p, "vargo")
    p.wait_for_selector("#panel .chart svg")
    p.click("#tab-history")
    body = p.inner_text("#panel")
    assert "By map, last 20 series" in body and "Mirage" in body and "Average per map" in body


def test_line_move_is_shown(ctx, env):
    env.by("vargo").update(open_line=37.5, moved_at=iso(datetime.now(timezone.utc)))
    p = open_page(ctx, env)
    all_days(p)
    panel(p, "vargo")
    assert "Moved from 37.5" in p.inner_text("#panel .p-sub")


def test_a_slip_stops_at_the_servers_limit_with_a_reason(octx, env):
    p = open_page(octx, env)
    all_days(p)
    buttons = p.locator('.row .add[data-act="row-add"]')
    for i in range(9):
        buttons.nth(i).click()
    assert p.locator("#rail-body .leg").count() == 8 and "A slip holds 8 picks" in p.inner_text("#toast")
    ready(p)                                                    # eight legs still get an answer from the server


def test_off_side_has_no_invented_chance(ctx, env):
    p = open_page(ctx, env)
    panel(p, "vargo")
    p.click('[data-act="p-side"][data-id="under"]')
    body = p.inner_text("#panel")
    assert "38.8" not in body and "Grader saved a chance for Over only" in body
    p.click('[data-act="p-add"]')
    assert "Grader side is Over" in p.inner_text("#rail-body .leg-meta")


def test_bars_stay_off_when_the_tally_does_not_match(ctx, env, monkeypatch):
    monkeypatch.setattr(hltv, "recent_series", lambda *a, **k: [])
    p = open_page(ctx, env)
    panel(p, "vargo")
    p.wait_for_function("document.getElementById('panel').textContent.includes('6 over, 4 under, 0 push')")
    assert p.locator("#panel .chart").count() == 0


def test_line_leaving_the_board_says_so(ctx, env):
    p = open_page(ctx, env)
    panel(p, "orlo")
    env.rows = [r for r in env.rows if r["nick"] != "orlo"]
    server._GB.clear()
    server._RESULTS.clear()
    refresh(p)
    p.wait_for_function("document.getElementById('panel').textContent.includes('This line left the board')")


# ---- my picks and results
def seed_receipts(p):
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    p.click('#rail-body [data-act="track"][data-id="underdog"]')
    p.wait_for_selector("#rail-body .tracked")


def test_receipt_shows_a_correction_below_the_first_result(ctx, env):
    p = open_page(ctx, env)
    seed_receipts(p)
    env.settle(env.by("vargo"), actual=30.0)
    workspace_api.store().settle_once()
    env.settle(env.by("vargo"), actual=44.0)
    workspace_api.store().settle_once()
    p.reload()
    go(p, "#/picks")
    p.wait_for_selector("#view-picks .pick-row")
    p.locator(".pick-row .pick-head", has_text="vargo").click()
    body = p.inner_text("#view-picks")
    assert "Settled: Miss. Final 30" in body and "Correction added. Settled: Hit. Final 44" in body and "Corrected" in body


def test_void_reads_void_and_push_is_not_a_win(ctx, env):
    p = open_page(ctx, env)
    seed_receipts(p)
    env.settle(env.by("vargo"), status="void", actual=None)
    env.settle(env.by("orlo"), status="push", actual=33.5)
    workspace_api.store().settle_once()
    p.reload()
    go(p, "#/picks")
    p.wait_for_selector("#view-picks .pick-row")
    text = p.inner_text("#view-picks")
    assert "Void" in text and "Withdrawn" not in text and "Push" in text and "0-0-1" in text


def test_ids_stay_folded_under_details(ctx, env):
    p = open_page(ctx, env)
    seed_receipts(p)
    go(p, "#/picks")
    p.wait_for_selector("#view-picks .pick-row")
    p.locator(".pick-row .pick-head", has_text="vargo").click()
    d = p.locator("#view-picks details.details")
    assert d.count() == 1 and d.evaluate("e => e.open") is False


def test_results_keeps_each_population_apart(ctx, env):
    p = open_page(ctx, env, path="#/results")
    p.wait_for_function("document.getElementById('view-results').textContent.includes('Hit rate, settled picks')")
    text = p.inner_text("#view-results")
    assert "Forecast chance" in text and "Hit rate" in text and "Market chance" in text and "never added together" in text
    assert "36-24" in text                                      # the fixture export
    assert not p.locator("#rail").is_visible()
    assert p.locator("#view-results thead").first.is_visible()


@pytest.mark.parametrize("group", ["chance", "stat", "side", "version"])
def test_results_groups(ctx, env, group):
    p = open_page(ctx, env, path="#/results")
    p.wait_for_selector('[data-act="group"]')
    p.click(f'[data-act="group"][data-id="{group}"]')
    assert p.locator("#view-results .res-sec.first tbody tr").count() >= 1


def test_no_market_column_of_dashes_and_a_fifty_tick(ctx, env):
    p = open_page(ctx, env, path="#/results")
    p.wait_for_selector(".dumb .tick")
    assert p.locator(".dumb .tick").count() >= 1


def test_results_outage_is_stated(ctx, env):
    ctx.route("**/preview/workspace/api/results", lambda r: r.abort())
    p = open_page(ctx, env, path="#/results")
    p.wait_for_function("document.getElementById('view-results').textContent.includes('Grader board record unavailable')")


# ---- saved data is another writer's output
def test_saved_markup_stays_text(ctx, env):
    evil = "<img src=x onerror=window.__xss=1>"
    s = workspace_api.store()
    leg = {k: evil for k in ("id", "key", "row_id", "event_id", "event_label", "player", "player_key", "team", "stat", "stat_label", "period", "period_label", "platform_label", "rev", "shown_label")}
    leg.update(sport="cs2", book="underdog", side="over", line=1.5, shown_p=0.5, added_at="2026-11-01T00:00:00+00:00")
    s.put_state(workspace_api.member_key("whop:u1"), 0, {"slip": {"legs": [leg], "types": {}}, "views": [{"id": "v", "name": evil, "state": {"chip": "all", "f": {}}}]})
    p = open_page(ctx, env)
    p.wait_for_timeout(300)
    p.click("#open-filters")
    p.wait_for_timeout(100)
    assert p.evaluate("window.__xss") is None and p.locator("img[src=x]").count() == 0


@pytest.mark.parametrize("bad", [{"slip": {"legs": [{"id": 1}]}}, {"slip": {"legs": "x"}}, {"prefs": {"f": 3, "chip": "nope"}}, {"views": [{"id": "x"}]}])
def test_bad_saved_state_still_boots(ctx, env, bad):
    workspace_api.store().put_state(workspace_api.member_key("whop:u1"), 0, bad)
    p = open_page(ctx, env)
    # Fixture games span now+3h and now+5h; "today" can contain only two
    # players near local midnight. This checks recovery, not the day filter.
    all_days(p)
    assert p.locator(".row").count() > 3 and p.errors == []


@pytest.mark.parametrize("bad", ["#/research/p/%E0%A4%A", "#/research/p/%"])
def test_bad_link_still_boots(ctx, env, bad):
    p = open_page(ctx, env, path=bad)
    all_days(p)
    assert p.locator(".row").count() > 3 and p.errors == []


def test_deep_link_to_a_cs2_line_opens_after_the_board_loads(ctx, env):
    p0 = open_page(ctx, env)
    k = key_of(p0, "vargo")
    p0.close()
    p = open_page(ctx, env, path="#/research/p/" + k.replace("%", "%25"))
    p.wait_for_selector("#panel-name")
    assert p.inner_text("#panel-name").lower() == "vargo"


# ---- search and routing
def test_search_from_another_view_shows_the_board_and_keeps_typing(ctx, env):
    p = open_page(ctx, env, path="#/results")
    p.locator("#search").press_sequentially("vargo")
    p.wait_for_timeout(100)
    assert p.locator("#view-research").is_visible()
    assert p.evaluate("document.activeElement.id") == "search" and p.input_value("#search") == "vargo"


def test_enter_in_search_lands_on_the_first_row(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    p.fill("#search", "brandr")
    p.keyboard.press("Enter")
    assert p.evaluate("document.activeElement.classList.contains('row-open')")


def test_phone_search_closes_by_touch_and_keyboard(ctx, env):
    p = open_page(ctx, env, size=PHONE)
    p.click("#search-toggle")
    p.keyboard.press("Escape")
    assert p.evaluate("document.activeElement.id") == "search-toggle"
    assert p.get_attribute("#search-toggle", "aria-expanded") == "false" and not p.locator("#search").is_visible()


# ---- keyboard
def test_sport_chip_keeps_focus(octx, env):
    p = open_page(octx, env)
    enter(p, '[data-fk="chip:nba"]')
    assert active(p) == "BUTTON:chip:nba"


def test_escape_returns_to_the_control_that_opened_the_panel(octx, env):
    p = open_page(octx, env)
    all_days(p)
    row_key = "nfl.nfl:espn:1.nflplayerespn401.pass_yds.full_game.264.5"
    p.focus(f'.row[data-row="{row_key}"] .add')
    p.keyboard.press("Enter")
    p.wait_for_timeout(100)
    p.keyboard.press("Escape")
    p.wait_for_timeout(150)
    assert active(p) == "BUTTON:row-choose:" + row_key


def test_phone_panel_is_modal_and_keeps_tab_inside(ctx, env):
    p = open_page(ctx, env, size=PHONE)
    panel(p, "vargo")
    assert p.get_attribute("#panel", "aria-modal") == "true"
    for _ in range(30):
        p.keyboard.press("Tab")
        assert p.evaluate("document.activeElement === document.body || !!document.activeElement.closest('#panel')")


def test_skip_link_keeps_the_view(ctx, env):
    p = open_page(ctx, env, path="#/results")
    p.keyboard.press("Tab")
    p.keyboard.press("Enter")
    assert p.locator("#view-results").is_visible() and "#/results" in p.url and active(p) == "H1:results-h"


def test_filter_drawer_controls(ctx, env):
    p = open_page(ctx, env)
    p.click("#open-filters")
    assert p.locator("#filters-body .view-link").first.is_visible()
    box = p.locator("#filters-dialog").bounding_box()
    assert box["x"] + box["width"] == DESK["width"]
    p.click('[data-act="f-set"][data-id="day:all"]')
    assert p.inner_text("#filters-done").startswith("Show ")
    p.click("#filters-done")
    assert not p.locator("#filters-dialog").is_visible()


def test_saved_view_goes_to_the_account(ctx, env):
    p = open_page(ctx, env)
    p.click("#open-filters")
    p.fill("#view-name", "Mine")
    p.click("#save-view button")
    p.wait_for_timeout(1000)
    assert [v["name"] for v in state(env)["views"]] == ["Mine"]


def test_phone_filter_drawer_fills_the_width(ctx, env):
    p = open_page(ctx, env, size=PHONE)
    p.click("#open-filters")
    box = p.locator("#filters-dialog").bounding_box()
    assert box["x"] == 0 and box["width"] == PHONE["width"]


# ---- layout and contrast
@pytest.mark.parametrize("width", [360, 390, 768, 1024, 1200, 1280, 1440, 1920])
def test_no_page_scrolls_sideways(ctx, env, width):
    p = open_page(ctx, env, size={"width": width, "height": 800})
    all_days(p)
    for h in ("#/research", "#/slips", "#/picks", "#/results"):
        go(p, h)
        w = p.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert w[0] <= w[1], (h, w)
        over = p.evaluate("""() => [...document.querySelectorAll('#view-results,#view-picks,#view-slips,#board-list')].filter((v) => v.offsetParent && v.scrollWidth > v.clientWidth + 1).map((v) => v.id)""")
        assert over == [], (h, over)


@pytest.mark.parametrize("width", [1100, 1200, 1280, 1366])
def test_results_table_keeps_its_header_on_normal_desktops(ctx, env, width):
    p = open_page(ctx, env, path="#/results", size={"width": width, "height": 800})
    p.wait_for_selector("#view-results .res-sec.first thead")
    assert p.locator("#view-results .res-sec.first thead").is_visible()


@pytest.mark.parametrize("width", [768, 900, 1180, 1280])
def test_chips_and_columns_do_not_collide(ctx, env, width):
    p = open_page(ctx, env, size={"width": width, "height": 800})
    all_days(p)
    bad = p.evaluate("""() => { const out = [];
      document.querySelectorAll('.row').forEach((r) => { const kids = [...r.children].filter((c) => c.tagName === 'DIV' && c.offsetParent);
        for (let i = 1; i < kids.length; i++) { const a = kids[i - 1].getBoundingClientRect(), b = kids[i].getBoundingClientRect(); if (a.right > b.left + 1 && Math.abs(a.top - b.top) < 4) out.push(r.dataset.row); } });
      return out; }""")
    assert bad == []


def test_phone_targets_are_44(ctx, env):
    p = open_page(ctx, env, size=PHONE)
    all_days(p)
    panel(p, "vargo")
    small = p.evaluate("""() => [...document.querySelectorAll('#panel button')].filter(b => b.offsetParent && b.getBoundingClientRect().height < 44).map(b => b.className)""")
    assert small == []
    go(p, "#/research")
    p.wait_for_timeout(100)
    small = p.evaluate("""() => [...document.querySelectorAll('.add, .chip, .more-btn')].filter(b => b.offsetParent && b.getBoundingClientRect().height < 44).map(b => b.className)""")
    assert small == []
    assert p.evaluate("""() => { const lim = document.getElementById('bottom').getBoundingClientRect().top; return [...document.querySelectorAll('.row')].filter(r => r.getBoundingClientRect().bottom <= lim).length; }""") >= 6


def test_toast_never_covers_the_slip_action_on_a_phone(ctx, env):
    p = open_page(ctx, env, size=PHONE)
    assert p.evaluate("getComputedStyle(document.getElementById('toast')).top") != "auto"


def luminance(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2])


def test_off_rows_keep_contrast(ctx, env):
    past = iso(datetime.now(timezone.utc) + timedelta(minutes=1))
    env.by("vargo").update(lock_at=past, start=past, match_start=past)
    p = open_page(ctx, env)
    all_days(p)
    p.evaluate("""() => { document.querySelectorAll('.row').forEach((r) => r.classList.add('is-off')); }""")
    samples = p.evaluate("""() => { const out = [];
      document.querySelectorAll('.row.is-off').forEach((row) => row.querySelectorAll('.pl-sub, .pl-name, .c-line b, .hist-n, .bc').forEach((e) => {
        let o = 1, n = e; while (n && n !== row.parentElement) { o *= +getComputedStyle(n).opacity; n = n.parentElement; } out.push([e.className, o, getComputedStyle(e).color]); }));
      return out; }""")
    assert samples
    for name, opacity, colour in samples:
        rgb = [float(x) for x in colour[colour.index("(") + 1:-1].split(",")[:3]]
        assert (luminance([c * opacity for c in rgb]) + 0.05) / 0.05 >= 4.5, (name, colour, opacity)


def test_one_filled_gold_action_per_view(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    add(p, "vargo", "prizepicks")
    add(p, "orlo", "prizepicks")
    p.keyboard.press("Escape")
    ready(p)
    assert p.locator("#rail-body .btn-gold").count() == 1


def test_no_demo_or_engineering_words_reach_the_page(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    for h in ("#/research", "#/slips", "#/picks", "#/results"):
        go(p, h)
        p.wait_for_timeout(120)
        text = p.evaluate("document.body.innerText")
        for bad in ("Recheck", "Derived id", "Offer revision", "Invented for this preview", "EV stays hidden", "Saved in this browser", "sample-cs2", "Sample live row", "Withdrawn", "Unknown"):
            assert bad not in text, (h, bad)


def test_no_runtime_errors_across_views(ctx, env):
    p = open_page(ctx, env)
    panel(p, "vargo")
    for t in ("history", "books", "context", "overview"):
        p.click(f'#tab-{t}')
    for h in ("#/slips", "#/picks", "#/results", "#/research"):
        go(p, h)
    assert p.errors == []


def test_page_never_calls_the_grader(ctx, env):
    seen = []
    ctx.on("request", lambda r: seen.append(r.url))
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    assert not [u for u in seen if "/api/grade" in u or "/api/slip_log" in u] and env.grades == 0


# ---- regressions from the 91bf2bf review
def test_a_member_with_cs2_only_sees_cs2_only(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    assert p.locator("#chips .chip").count() == 1 and "CS2" in p.inner_text("#chips")
    p.click("#open-filters")
    assert "NBA points" not in p.inner_text("#filters-body")


def test_no_sample_row_reaches_any_view(octx, env):
    p = open_page(octx, env)
    all_days(p)
    for h in ("#/research", "#/matches", "#/slips", "#/picks", "#/results"):
        go(p, h)
        p.wait_for_timeout(100)
        text = p.evaluate("document.body.innerText")
        assert not re.search(r"sample", text, re.I) and "Delvane" not in text and "Tidefall" not in text, h


def test_a_saved_sport_the_member_lost_falls_back_to_all(ctx, env):
    workspace_api.store().put_state(workspace_api.member_key("whop:u1"), 0, {"prefs": {"chip": "nba", "f": {"day": "all"}}})
    p = open_page(ctx, env)
    assert p.locator(".row").count() >= 5


def test_next_days_props_fill_a_first_visit_with_nothing_today(ctx, env):
    late = iso(datetime.now(timezone.utc) + timedelta(hours=60))
    for x in env.rows:
        x.update(start=late, match_start=late, lock_at=late)
    p = open_page(ctx, env)
    assert p.locator(".row").count() >= 5 and "No props match" not in p.inner_text("#board-list")


def test_the_sport_switch_stays_on_the_account(octx, env):
    p = open_page(octx, env)
    all_days(p)
    chip(p, "lol")
    assert p.locator(".row").count() >= 5 and p.locator('.row:has-text("vargo")').count() == 0
    p.wait_for_timeout(2800)
    p.reload()
    p.wait_for_selector("#app[data-boot=done]", state="attached")
    assert p.locator('#chips [data-act="chip"][data-id="lol"]').get_attribute("aria-pressed") == "true"


def test_the_underdog_column_shows_its_real_prices(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    cell = p.locator(".row", has_text="vargo").first.locator(".c-ud .bc").inner_text()
    assert "1.92" in cell and "1.88" in cell and "1.00x" not in p.inner_text("#board-list")
    panel(p, "vargo")
    p.wait_for_selector("#panel .chart svg")
    assert "1.92" in p.inner_text('[data-act="p-side"][data-id="over"]')
    p.click("#tab-books")
    assert "1.92" in p.inner_text("#panel .dt") and "1.00x" not in p.inner_text("#panel")


def test_a_failed_save_is_sent_again_without_a_click(ctx, env):
    p = open_page(ctx, env)
    failed = []

    def once(route):
        if route.request.method == "POST" and not failed:
            failed.append(1)
            route.abort()
        else:
            route.continue_()
    ctx.route("**/preview/workspace/api/state", once)
    add(p, "vargo")
    p.wait_for_function("document.getElementById('rail-body').textContent.includes('will save when the connection returns')")
    assert not (state(env)["slip"] or {}).get("legs")
    p.wait_for_function("!document.getElementById('rail-body').textContent.includes('will save when the connection returns')", timeout=9000)
    assert [l["player"] for l in state(env)["slip"]["legs"]] == ["vargo"]


def test_a_limit_hit_on_a_save_is_sent_again(ctx, env):
    p = open_page(ctx, env)
    hits = []

    def busy(route):
        if route.request.method == "POST" and len(hits) < 1:
            hits.append(1)
            route.fulfill(status=429, content_type="application/json", body='{"error":"Too many requests."}')
        else:
            route.continue_()
    ctx.route("**/preview/workspace/api/state", busy)
    add(p, "vargo")
    p.wait_for_function("document.getElementById('rail-body').textContent.includes('will save when the connection returns')")
    p.wait_for_function("!document.getElementById('rail-body').textContent.includes('will save when the connection returns')", timeout=9000)
    assert [l["player"] for l in state(env)["slip"]["legs"]] == ["vargo"]


def test_a_change_made_while_a_save_is_running_is_saved_too(ctx, env):
    p = open_page(ctx, env)
    gate = []

    def slow(route):
        if route.request.method == "POST":
            gate.append(route)
            return
        route.continue_()
    ctx.route("**/preview/workspace/api/state", slow)
    add(p, "vargo")
    p.wait_for_timeout(1000)                                    # the first save is in flight, held here
    assert len(gate) == 1
    add(p, "orlo")
    p.wait_for_timeout(1000)
    ctx.unroute("**/preview/workspace/api/state")
    gate[0].continue_()
    p.wait_for_timeout(2000)
    assert sorted(l["player"] for l in state(env)["slip"]["legs"]) == ["orlo", "vargo"]


def test_a_leg_added_just_before_a_reload_is_still_there(ctx, env):
    p = open_page(ctx, env)
    panel(p, "vargo")
    p.click('[data-act="p-add"]')
    p.reload()
    p.wait_for_selector("#app[data-boot=done]", state="attached")
    p.wait_for_function("document.getElementById('rail-body').textContent.includes('1 pick')")
    p.wait_for_timeout(1200)
    assert [l["player"] for l in state(env)["slip"]["legs"]] == ["vargo"]


def test_a_draft_stays_with_its_member(browser, env):
    """Two members on one browser: what one left unsaved never lands on the other's slip, and it is waiting when they come back."""
    c = context(browser, env, user="alice")
    c.route("**/preview/workspace/api/state", lambda r: r.abort() if r.request.method == "POST" else r.continue_())
    p = open_page(c, env)
    panel(p, "vargo")
    p.click('[data-act="p-add"]')
    p.wait_for_timeout(1000)
    assert not (state(env, "alice")["slip"] or {}).get("legs")
    p.goto("about:blank")                                       # alice left, and her last save never arrived
    c.unroute("**/preview/workspace/api/state")
    c.add_cookies([{"name": whop_auth.COOKIE, "value": "good-bob", "url": env.url}])
    p.goto(env.url)
    p.wait_for_selector("#app[data-boot=done]", state="attached")
    p.wait_for_timeout(1200)
    assert "0 picks" in p.inner_text("#rail-body") and not (state(env, "bob")["slip"] or {}).get("legs")
    p.goto("about:blank")
    c.add_cookies([{"name": whop_auth.COOKIE, "value": "good-alice", "url": env.url}])
    p.goto(env.url)
    p.wait_for_selector("#app[data-boot=done]", state="attached")
    p.wait_for_function("document.getElementById('rail-body').textContent.includes('1 pick')")
    p.wait_for_timeout(1200)
    assert [l["player"] for l in state(env, "alice")["slip"]["legs"]] == ["vargo"]
    c.close()


def test_an_open_device_picks_up_another_devices_leg(browser, env):
    a_ctx, b_ctx = context(browser, env), context(browser, env)
    a, b = open_page(a_ctx, env), open_page(b_ctx, env)
    add(a, "orlo")
    a.wait_for_timeout(1200)
    refresh(b)
    b.wait_for_function("document.getElementById('rail-body').textContent.includes('1 pick')")
    a_ctx.close()
    b_ctx.close()


def test_a_prefs_save_never_costs_another_devices_leg(browser, env):
    a_ctx, b_ctx = context(browser, env), context(browser, env)
    a, b = open_page(a_ctx, env), open_page(b_ctx, env)
    add(b, "vargo")
    b.wait_for_timeout(1200)                                    # b saved: rev moved, a does not know yet
    assert [l["player"] for l in state(env)["slip"]["legs"]] == ["vargo"]
    chip(a, "cs2")                                              # a saves preferences on the old rev
    a.wait_for_timeout(3200)
    add(a, "orlo")
    a.wait_for_timeout(1200)
    assert sorted(l["player"] for l in state(env)["slip"]["legs"]) == ["orlo", "vargo"]
    add(b, "brandr")
    b.wait_for_timeout(1500)
    assert sorted(l["player"] for l in state(env)["slip"]["legs"]) == ["brandr", "orlo", "vargo"]
    a_ctx.close()
    b_ctx.close()


def test_a_merge_stops_at_the_leg_limit_and_the_slip_keeps_working(browser, env):
    s = env.by("vargo")["start"]
    extra = [row(f"11111111-0000-4000-8000-00000000{100 + i:04d}", "underdog", f"xp{i}", f"T{i}a", f"T{i}b", 30.5, "More", 0.6, 950 + i, s) for i in range(10)]
    env.rows.extend(extra)
    env.seed(extra)
    a_ctx, b_ctx = context(browser, env), context(browser, env)
    a, b = open_page(a_ctx, env), open_page(b_ctx, env)
    for i in range(5):
        add(a, f"xp{i}")
    a.wait_for_timeout(1200)
    for i in range(5, 10):
        add(b, f"xp{i}")
    b.keyboard.press("Escape")
    b.wait_for_function("document.getElementById('toast').textContent.includes('A slip holds 8 picks')")
    b.wait_for_timeout(1500)
    assert len(state(env)["slip"]["legs"]) == 8
    ready(b)
    assert "did not answer" not in b.inner_text("#rail-body") and b.is_enabled('#rail-body [data-act="track"][data-id="underdog"]')
    a_ctx.close()
    b_ctx.close()


def test_a_deleted_view_stays_deleted(browser, env):
    a_ctx, b_ctx = context(browser, env), context(browser, env)
    a = open_page(a_ctx, env)
    a.click("#open-filters")
    a.fill("#view-name", "Mine")
    a.click("#save-view button")
    a.wait_for_timeout(1000)
    a.click("#filters-done")
    b = open_page(b_ctx, env)
    add(b, "vargo")                                             # moves the rev while a still holds the old one
    b.wait_for_timeout(1200)
    a.click("#open-filters")
    a.click('[aria-label="Delete view Mine"]')
    a.wait_for_timeout(1500)
    assert state(env)["views"] == []
    a.wait_for_function("!document.getElementById('filters-body').textContent.includes('Mine')")
    a_ctx.close()
    b_ctx.close()


def test_copy_asks_the_server_first_and_a_refusal_leaves_the_clipboard_alone(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    record_clipboard(p)
    env.by("vargo").update(line=41.5, graded_line=41.5)
    past = iso(datetime.now(timezone.utc) - timedelta(minutes=1))
    env.by("orlo").update(start=past, lock_at=past, match_start=past)
    fresh()
    with p.expect_request("**/preview/workspace/api/recheck"):
        p.click('#rail-body [data-act="copy"][data-id="underdog"]')
    p.wait_for_function("document.getElementById('toast').textContent.includes('Nothing from the Underdog entry')")
    assert p.evaluate("window.__clip") == []
    assert "Line moved" in p.inner_text("#rail-body")


def test_copy_writes_after_a_passing_check(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    record_clipboard(p)
    with p.expect_request("**/preview/workspace/api/recheck"):
        p.click('#rail-body [data-act="copy"][data-id="underdog"]')
    p.wait_for_function("window.__clip.length === 1")
    assert "vargo Over 38.5" in p.evaluate("window.__clip[0]")


def test_copy_with_the_check_down_leaves_the_clipboard_alone(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    record_clipboard(p)
    ctx.route("**/preview/workspace/api/recheck", lambda r: r.abort())
    p.click('#rail-body [data-act="copy"][data-id="underdog"]')
    p.wait_for_function("document.getElementById('toast').textContent.includes('Nothing from the Underdog entry')")
    assert p.evaluate("window.__clip") == []


def test_a_side_flip_reaches_the_slip_and_the_member_takes_it_knowingly(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    env.flip(env.by("vargo"))
    fresh()
    p.click('#rail-body [data-act="check-now"]')
    p.wait_for_function("document.getElementById('rail-body').textContent.includes(\"Grader's read changed: now Under 58.0%\")")
    assert p.is_disabled('#rail-body [data-act="track"][data-id="underdog"]') and p.is_disabled('#rail-body [data-act="copy"][data-id="underdog"]')
    p.click('#rail-body [data-act="swap-leg"]')
    p.wait_for_function("document.getElementById('rail-body').textContent.includes('Grader side is Under')")
    ready(p)
    assert "61.2%" not in p.inner_text("#rail-body .leg")
    p.click('#rail-body [data-act="track"][data-id="underdog"]')
    p.wait_for_selector("#rail-body .tracked")


def test_a_receipt_against_scout_names_scouts_side(ctx, env):
    p = open_page(ctx, env)
    panel(p, "vargo")
    p.click('[data-act="p-side"][data-id="under"]')
    p.click('[data-act="p-add"]')
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    p.click('#rail-body [data-act="track"][data-id="underdog"]')
    p.wait_for_selector("#rail-body .tracked")
    go(p, "#/picks")
    p.wait_for_selector("#view-picks .pick-row")
    p.locator(".pick-row .pick-head", has_text="vargo").click()
    body = p.inner_text("#view-picks .pick-body")
    assert "B Over" in body and "0.5U on Over" in body and "Under, against Grader" in body and "None saved for this side" in body


def test_a_leg_the_sports_check_refuses_is_never_ready_and_never_copied(octx, env):
    p = open_page(octx, env)
    all_days(p)
    env.sports.answers[next(q["r"] for m in env.sports.boards["nba"][0]["markets"] for q in m["q"] if q["p"] == "prizepicks" and q["l"] == 6.5)] = {"ok": False, "code": "stale", "text": "Price not fresh"}
    add(p, "Reed Lean", "prizepicks", "nba")
    add(p, "vargo", "prizepicks")
    p.keyboard.press("Escape")
    ready(p)
    assert "0 of 1 entry ready" in p.inner_text("#rail-body .slip-status") and "has not been re-read recently" in p.inner_text("#rail-body .entry")
    assert p.is_disabled('#rail-body [data-act="copy"][data-id="prizepicks"]') and p.is_disabled('#rail-body [data-act="track"][data-id="prizepicks"]')


def test_research_never_changes_book_silently(ctx, env):
    p = open_page(ctx, env)
    panel(p, "vargo")
    assert "Underdog" in p.inner_text("#panel .p-stat") and "on Underdog" in p.inner_text(".p-foot")
    env.by("vargo").update(line=39.5, graded_line=39.5)
    env.relog(env.by("vargo"))
    fresh()
    refresh(p)
    p.wait_for_function("document.getElementById('panel').textContent.includes('Underdog no longer lists 38.5. Showing PrizePicks.')")
    assert "on PrizePicks" in p.inner_text(".p-foot") and "PrizePicks" in p.inner_text("#panel .p-stat")
    p.locator('#panel .strip [data-act="p-line"]').click()
    p.wait_for_function("document.querySelector('.p-foot').textContent.includes('Add Over 39.5 on Underdog')")


def test_a_moved_line_is_reported_as_moved(ctx, env):
    p = open_page(ctx, env)
    panel(p, "orlo")
    env.by("orlo").update(line=34.5, graded_line=34.5)
    env.relog(env.by("orlo"))
    fresh()
    refresh(p)
    p.wait_for_function("document.getElementById('panel').textContent.includes('This line moved')")
    text = p.inner_text("#panel")
    assert "33.5 is off the board" in text and "left the board" not in text
    p.locator('#panel [data-act="p-line"]').first.click()
    p.wait_for_function("document.querySelector('.p-foot').textContent.includes('34.5')")


def test_an_ended_session_asks_for_sign_in_and_stops_retrying(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    ctx.clear_cookies()
    p.click('#rail-body [data-act="check-now"]')
    p.wait_for_selector("#session-strip:not([hidden])")
    assert p.locator('#session-strip a[href="/auth/login"]').count() == 1
    text = p.inner_text("#rail-body")
    assert "Your session ended" in text and "did not answer" not in text and p.locator("#rail-body .leg").count() == 2
    seen = []
    p.on("request", lambda r: seen.append(r.url) if "/api/" in r.url else None)
    refresh(p)
    p.wait_for_timeout(300)
    assert seen == []


def test_open_check_details_stay_open_through_a_new_check(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    p.locator("#rail-body details.checks summary", has_text="Check details").click()
    p.locator("#rail-body details.checks summary", has_text="Platform rules checked").click()
    assert p.locator("#rail-body details[open]").count() == 2
    p.click('#rail-body [data-act="check-now"]')
    ready(p)
    assert p.locator("#rail-body details[open]").count() == 2


def test_every_disclosure_shows_it_opens(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    marks = p.evaluate("""() => [...document.querySelectorAll('#rail-body summary')].map((s) => getComputedStyle(s, '::before').borderBottomStyle)""")
    assert marks and set(marks) == {"solid"}


def test_a_line_updated_days_ago_shows_its_date(ctx, env):
    env.by("vargo")["updated_at"] = iso(datetime.now(timezone.utc) - timedelta(days=3))
    p = open_page(ctx, env)
    panel(p, "vargo")
    p.click("#tab-books")
    assert re.search(r"[A-Z][a-z]{2} \d+, \d", p.locator("#panel .dt tbody tr").first.inner_text()) and "Updated" in p.inner_text("#panel .dt thead")


def test_an_empty_export_says_so(ctx, env, monkeypatch):
    monkeypatch.setattr(server, "_p1_export", lambda: {})
    p = open_page(ctx, env, path="#/results")
    p.wait_for_function("document.getElementById('view-results').textContent.includes('No settled picks meet this rule yet')")
    assert "undefined" not in p.inner_text("#view-results")


def test_the_phone_slip_bar_waits_for_a_pick(ctx, env):
    p = open_page(ctx, env, size=PHONE)
    assert p.locator("#slip-fab").is_hidden()
    panel(p, "vargo")
    p.click('[data-act="p-add"]')
    p.keyboard.press("Escape")
    p.wait_for_function("!document.getElementById('slip-fab').hidden")


def test_phone_chips_that_do_not_fit_say_so(octx, env):
    p = open_page(octx, env, size=PHONE)
    assert p.evaluate("document.getElementById('chips').scrollWidth > document.getElementById('chips').clientWidth")
    assert "scrolls" in p.get_attribute("#chips", "class")


def test_each_underdog_leg_gets_its_own_link(ctx, env, monkeypatch):
    monkeypatch.setattr(server, "PP_OPEN_LINK", True)
    monkeypatch.setattr(underdog, "ud_ids", lambda rid: (rid,))
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    links = p.locator("#rail-body .leg-meta a")
    assert links.count() == 2 and links.first.get_attribute("href").startswith("https://") and "Open Underdog" in p.inner_text("#rail-body .entry-foot")


def test_no_internal_ids_on_a_members_panel_or_slip(ctx, env):
    p = open_page(ctx, env)
    add(p, "vargo")
    add(p, "orlo")
    p.keyboard.press("Escape")
    ready(p)
    assert "TESTMODEL" not in p.inner_text("#rail-body")
    panel(p, "vargo")
    p.wait_for_selector("#panel .chart svg")
    assert "Offer" not in p.evaluate("document.querySelector('#panel details.details').textContent")


def test_no_empty_other_books_column(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    assert p.locator(".h-other").is_hidden() and p.locator(".row .c-other").first.is_hidden()


def test_the_board_keeps_its_columns_beside_the_panel_at_a_normal_desktop(ctx, env):
    p = open_page(ctx, env, size={"width": 1440, "height": 900})
    all_days(p)
    panel(p, "vargo")
    assert p.locator("#rail").is_hidden() and p.locator("#view-research").evaluate("e => e.clientWidth") >= 800
    r = p.locator(".row").first
    assert r.locator(".c-hist").is_visible() and r.locator(".c-pp").is_visible() and r.locator(".c-ud").is_visible()
    assert p.locator("#f-day").is_visible() and p.locator("#f-market").is_visible() and p.locator("[data-mini-slip]").is_visible()


def test_the_rail_stays_when_there_is_room_for_all_three(ctx, env):
    p = open_page(ctx, env, size=WIDE)
    all_days(p)
    panel(p, "vargo")
    assert p.locator("#rail").is_visible() and p.locator(".row .c-hist").first.is_visible()


def test_a_phone_row_keeps_its_stat_and_maps(ctx, env):
    p = open_page(ctx, env, size=PHONE)
    all_days(p)
    assert p.locator(".row", has_text="sable").first.locator(".pl-sub").inner_text().startswith("Headshots · Map 1")


def test_the_board_record_label_claims_only_what_it_counts(ctx, env):
    p = open_page(ctx, env, path="#/results")
    p.wait_for_function("document.getElementById('view-results').textContent.includes('Hit rate, settled picks')")
    text = p.inner_text("#view-results")
    assert "Every saved Grader forecast" not in text and "Saved Grader forecasts that meet the rule and definition below" in text


def test_older_picks_load_on_request(ctx, env):
    who = workspace_api.member_key("whop:u1")
    s = workspace_api.store()
    t0 = datetime.now(timezone.utc)
    rows = [(f"rc-{i}", who, f"sl-{i // 4}", i % 4, iso(t0 - timedelta(seconds=i // 4)), "cs2", "underdog", "r", "d", "over", 30.5, "standard", 1, 1, "s", None, "k", f"n{i}", "kills", "12", None, None, 0,
             json.dumps({"player": f"n{i}", "stat_label": "Kills", "period_label": "Maps 1-2", "matchup": "A vs B", "platform_label": "Underdog"})) for i in range(80)]
    slips = sorted({(r[2], who, r[4], "underdog", "standard", "[\"cs2\"]", 4, "v", "{}", 0) for r in rows})

    def op(con):
        con.executemany("INSERT INTO receipts(receipt_id, member, slip_id, leg_no, tracked_at, sport, platform, row_id, offer_rev, side, line, line_type, pick_id, forecast_id, match_slug, match_id, "
                        "match_key, nick, prop, scope, start_utc, model_version, scratch, payload) VALUES(" + ",".join("?" * 24) + ")", rows)
        con.executemany("INSERT INTO slips(slip_id, member, tracked_at, platform, entry_type, sports, legs, rules_version, checks, scratch) VALUES(?,?,?,?,?,?,?,?,?,?)", slips)
        s._bump(con, who)
    s.submit(op, wait=30)
    p = open_page(ctx, env, path="#/picks")
    p.wait_for_selector("#view-picks .pick-row")
    before = p.locator("#view-picks .pick-row").count()
    assert before < 80
    p.click('#view-picks [data-act="mine-older"]')
    p.wait_for_function("document.querySelectorAll('#view-picks .pick-row').length === 80")
    assert p.locator('#view-picks [data-act="mine-older"]').count() == 0 and p.errors == []


# ---- all four sports, end to end
def shot(p, name):
    if str(SHOTS) != ".":
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(150)
        p.screenshot(path=str(SHOTS / name), full_page=False)


def phone_add(p, nick, book, sport, side=None):
    panel(p, nick, book, sport)
    if side:
        p.click(f'[data-act="p-side"][data-id="{side}"]')
    p.click('[data-act="p-add"]')
    p.wait_for_function("document.querySelector('[data-act=\"p-add\"]').textContent.includes('Remove')")


@pytest.mark.parametrize("size,tag", [(DESK, "desk"), (PHONE, "phone")])
def test_four_sport_flow(browser, env, sports, size, tag):
    c = context(browser, env, size, user="fourway")
    p = open_page(c, env)
    all_days(p)
    p.wait_for_selector('.row:has-text("Karl Anders")')
    shot(p, f"{tag}-1-board.png")

    # research on real records: hit cells, market, books, context, by sport
    panel(p, "Karl Anders", "underdog", "nba")
    p.wait_for_function("document.querySelector('#panel').textContent.includes('Market chance')")
    p.wait_for_selector("#panel .chart svg")
    body = p.inner_text("#panel")
    assert "Research estimate" in body and "Hit rate, last 5" in body and "Hit rate, last 10" in body and "55.9%" in body
    shot(p, f"{tag}-2-research-nba.png")
    p.click("#tab-context")
    p.wait_for_selector("#panel .ctx-list")
    assert "questionable" in p.inner_text("#panel") and "Points allowed per game to C" in p.inner_text("#panel")
    p.click("#tab-books")
    assert "FanDuel" in p.inner_text("#panel")
    shot(p, f"{tag}-3-books-nba.png")
    panel(p, "Harky", "prizepicks", "lol")
    p.wait_for_selector("#panel .chart svg, #panel .hit-tile")
    assert "0U" in p.inner_text("#panel") or "Stake 0U" in p.inner_text("#panel")
    p.click("#tab-context")
    p.wait_for_selector("#panel .ctx-list")
    assert "Role" in p.inner_text("#panel")

    # one tray, four sports, one PrizePicks entry
    phone_add(p, "vargo", "prizepicks", None)
    phone_add(p, "Harky", "prizepicks", "lol")
    phone_add(p, "Reed Lean", "prizepicks", "nba")
    phone_add(p, "Sam Field", "prizepicks", "nfl", "over")
    go(p, "#/slips")
    p.wait_for_selector("#view-slips .entry")
    ready(p, "#view-slips")
    entry = p.inner_text("#view-slips .entry")
    assert "CS2 + LoL + NBA + NFL" in entry and "1 of 1 entry ready" in p.inner_text("#view-slips .slip-status")
    shot(p, f"{tag}-4-slip.png")

    # the saved slip follows the member to another device
    p.wait_for_timeout(1200)
    c2 = context(browser, env, PHONE if size is DESK else DESK, user="fourway")
    p2 = open_page(c2, env)
    go(p2, "#/slips")
    p2.wait_for_selector("#view-slips .entry")
    ready(p2, "#view-slips")
    assert p2.locator("#view-slips .leg").count() == 4 and "1 of 1 entry ready" in p2.inner_text("#view-slips .slip-status")
    c2.close()

    # track, then receipts
    p.click('#view-slips [data-act="track"][data-id="prizepicks"]')
    p.wait_for_selector("#view-slips .tracked")
    go(p, "#/picks")
    p.wait_for_selector("#view-picks .pick-row")
    assert p.locator("#view-picks .pick-row").count() == 4
    text = p.inner_text("#view-picks")
    for name in ("CS2", "LoL", "NBA", "NFL"):
        assert name in text
    shot(p, f"{tag}-5-picks.png")

    # settlement from each sport's own results: NBA and NFL from the sports service, LoL from its results, CS2 from the pick log
    con = __import__("sqlite3").connect(workspace_api.store().path)
    con.execute("UPDATE leg_watch SET start_utc = '2026-01-01T00:00:00Z'")
    con.commit()
    con.close()
    env.sports.settled[("pp:4", "over")] = {"status": "win", "value": 8.0, "settlement_id": "st-1", "revision": 1}
    env.sports.settled[("pp:n1", "over")] = {"status": "push", "value": 264.5, "settlement_id": "st-2", "revision": 1}
    env.lol.settle(next(x for x in env.lol.rows if x["player"] == "Harky" and x["platform"] == "prizepicks"), "graded", 9, 0)
    env.settle(env.by("vargo", "prizepicks"), "settled", 41.0)
    workspace_api.store().settle_once()
    p.evaluate("window.dispatchEvent(new Event('focus'))")
    p.wait_for_function("document.querySelector('#view-picks').textContent.includes('Hit')")
    p.wait_for_timeout(300)
    text = p.inner_text("#view-picks")
    assert "Push" in text and "Hit" in text
    assert "3-0-1" in text
    go(p, "#/results")
    p.wait_for_selector("#view-results .rec-strip")
    shot(p, f"{tag}-6-results-cs2.png")
    for k, word in (("lol", "LoL forecast record"), ("nba", "NBA research record"), ("nfl", "NFL research record")):
        p.click(f'[data-act="res-sport"][data-id="{k}"]')
        p.wait_for_function("w => document.querySelector('#view-results').textContent.includes(w)", arg=word)
        p.wait_for_selector("#view-results .res-tbl")
        assert "Forecast chance" in p.inner_text("#view-results") and "Hit rate" in p.inner_text("#view-results")
        shot(p, f"{tag}-7-results-{k}.png")
    assert p.errors == []
    c.close()


def test_watchlist_and_saved_view_follow_the_account(browser, env, sports):
    c = context(browser, env, user="watcher")
    p = open_page(c, env)
    all_days(p)
    panel(p, "Karl Anders", "underdog", "nba")
    p.click('[data-act="watch"]')
    p.wait_for_timeout(1100)
    assert state(env, "watcher")["watch"][0]["name"] == "Karl Anders"
    p.keyboard.press("Escape")
    assert p.locator('.row:has-text("Karl Anders") .w-mark').count() >= 1
    p.click("#open-filters")
    p.click('[data-act="view"][data-id="p-watch"]')
    p.click("#filters-done")
    assert p.locator(".row").count() >= 1 and p.locator('.row:has-text("vargo")').count() == 0
    c.close()
    c2 = context(browser, env, PHONE, user="watcher")
    p2 = open_page(c2, env)
    all_days(p2)
    assert p2.locator('.row:has-text("Karl Anders") .w-mark').count() >= 1
    c2.close()


def test_matches_lists_every_sport_and_opens_its_props(octx, env):
    p = open_page(octx, env)
    all_days(p)
    go(p, "#/matches")
    p.wait_for_selector(".match-card")
    text = p.inner_text("#view-matches")
    assert "NYK at BOS" in text and "Halcyon vs Norrland" in text and "BUF at KC" in text
    shot(p, "desk-8-matches.png")
    p.locator(".match-card", has_text="NYK at BOS").click()
    p.wait_for_timeout(150)
    assert "All props" in p.inner_text("#feed-strips") and p.locator('.row:has-text("Karl Anders")').count() >= 1 and p.locator('.row:has-text("vargo")').count() == 0
    p.click('[data-act="event-clear"]')
    assert p.locator('.row:has-text("vargo")').count() >= 1


def test_one_sport_down_leaves_the_others_on_the_board(octx, env):
    env.sports.down = True
    p = open_page(octx, env)
    all_days(p)
    assert "NBA, NFL board unavailable" in p.inner_text("#board-list") and p.locator('.row:has-text("vargo")').count() >= 1 and p.locator('.row:has-text("Harky")').count() >= 1


def test_results_outage_for_one_sport_is_stated(octx, env):
    env.sports.public = None
    p = open_page(octx, env)
    go(p, "#/results")
    p.click('[data-act="res-sport"][data-id="nfl"]')
    p.wait_for_function("document.querySelector('#view-results').textContent.includes('NFL record unavailable')")


def test_held_rows_show_no_letter_and_lol_letters_show_0u(octx, env):
    p = open_page(octx, env)
    all_days(p)
    held = p.locator('.row:has-text("Sam Field")').first.inner_text()
    assert "Research estimate" in held and not re.search(r"\b[ABC][+-]?\s+(Over|Under)", held)
    assert "0U" in p.locator('.row:has-text("Harky")').first.inner_text()
