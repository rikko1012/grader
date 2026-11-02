"""The research workspace in a real browser against a local server.Handler with fixture data (tests/workspace_fixture.py): the full
CS2 flow from research through settlement, two devices, real failure states, and focus and layout. Skipped where Playwright or
Chromium is missing. Nothing here reaches a network or production data."""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

import hltv
import server
import whop_auth
import workspace_api
from tests.workspace_fixture import Fixture, iso

sync_api = pytest.importorskip("playwright.sync_api")

DESK = {"width": 1440, "height": 900}
TAB = {"width": 1024, "height": 768}
PHONE = {"width": 390, "height": 844}
WIDE = {"width": 1920, "height": 1080}
MIN = 60_000
NBA_ROW = "nba.nba-e2.brandt.pra.full_game.33.5"


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


def key_of(p, nick, book="underdog"):
    return p.evaluate("""async ([n, b]) => { const r = await (await fetch('/preview/workspace/api/board')).json();
      return r.records.find((x) => x.player.name === n && x.offer.platform === b).key; }""", [nick, book])


def go(p, h):
    p.evaluate("h => { location.hash = h; }", h)
    p.wait_for_timeout(80)


def panel(p, nick, book="underdog"):
    go(p, "#/research/p/" + key_of(p, nick, book).replace("%", "%25"))
    p.wait_for_selector("#panel-name")
    if p.locator(f'[data-act="p-book"][data-id="{book}"]').count():
        p.locator(f'[data-act="p-book"][data-id="{book}"]').first.click()


def add(p, nick, book="underdog"):
    panel(p, nick, book)
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
    assert "Scout board record" in res and "Your tracked picks" in res and "Sample sports" in res and "1-1-0" in res
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
def test_mixed_sports_by_platform(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    add(p, "vargo", "prizepicks")
    go(p, "#/research/p/" + NBA_ROW)
    p.wait_for_selector("#panel-name")
    p.click('[data-act="p-book"][data-id="prizepicks"]')
    p.click('[data-act="p-add"]')
    p.keyboard.press("Escape")
    ready(p)
    entry = p.inner_text("#rail-body .entry")
    assert "CS2 + NBA" in entry and "NBA legs are sample data." in entry and p.is_disabled('#rail-body [data-act="track"][data-id="prizepicks"]')
    assert p.is_enabled('#rail-body [data-act="copy"][data-id="prizepicks"]')
    p.click('#rail-body [data-act="slip-clear"]')
    add(p, "vargo")
    go(p, "#/research/p/" + NBA_ROW)
    p.wait_for_selector("#panel-name")
    p.click('[data-act="p-book"][data-id="underdog"]')
    p.click('[data-act="p-add"]')
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
def test_board_down_never_falls_back_to_sample_cs2(ctx, env):
    env.board_ok = False
    p = open_page(ctx, env)
    p.wait_for_selector("#board-list .state")
    assert "CS2 board unavailable" in p.inner_text("#board-list")
    assert p.evaluate("""() => WORKSPACE_DATA.records.every((r) => r.sport !== 'cs2') && WORKSPACE_DATA.receipts.every((r) => r.sport !== 'cs2')""")
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
    p.wait_for_function("document.getElementById('toast').textContent.includes('Scout is busy')")
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
    assert p.locator(f'.row[data-row="{NBA_ROW}"] .best').count() >= 1


def test_sample_tag_is_on_sample_rows_only(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    assert p.locator('.row:has-text("vargo") .sample-tag').count() == 0
    assert p.locator(f'.row[data-row="{NBA_ROW}"] .sample-tag').count() == 1


def test_match_groups_and_other_lines(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    assert p.locator(".match-head", has_text="Halcyon vs Norrland").count() == 1
    assert p.locator(".row.is-sub").count() >= 1                # the sample prop with two lines


def test_sort_header_works(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    p.click('#board-head [data-act="sort"][data-id="chance"]')
    assert p.locator(".match-head").count() == 0
    first = p.locator(".row .pct").first.inner_text()
    assert first == "61.2%"


def test_decimal_odds_are_not_written_as_multipliers(ctx, env):
    p = open_page(ctx, env, path="#/research/p/" + NBA_ROW + "/books")
    p.wait_for_selector("#panel-name")
    assert "1.93" in p.inner_text("#panel") and "1.93x" not in p.inner_text("#panel") and "1.90x" in p.inner_text("#panel")


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


def test_line_picker_and_remove_toggle(ctx, env):
    p = open_page(ctx, env, path="#/research/p/nba.nba-e1.delvane.pts.full_game.24.5")
    p.wait_for_selector(".line-pick")
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
    p = open_page(ctx, env, path="#/research/p/nba.nba-e1.delvane.pts.full_game.25.5")
    p.wait_for_selector("#panel-name")
    p.click('[data-act="p-book"][data-id="underdog"]')
    assert "Moved from 24.5" in p.inner_text("#panel .p-sub")


def test_a_slip_stops_at_the_servers_limit_with_a_reason(ctx, env):
    p = open_page(ctx, env)
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
    assert "38.8" not in body and "Scout saved a chance for Over only" in body
    p.click('[data-act="p-add"]')
    assert "Scout side is Over" in p.inner_text("#rail-body .leg-meta")


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


def test_results_keeps_three_populations_apart(ctx, env):
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
    p.wait_for_function("document.getElementById('view-results').textContent.includes('Scout board record unavailable')")


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
    assert p.locator(".row").count() > 3 and p.errors == []


@pytest.mark.parametrize("bad", ["#/research/p/%E0%A4%A", "#/research/p/%"])
def test_bad_link_still_boots(ctx, env, bad):
    p = open_page(ctx, env, path=bad)
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
def test_sport_chip_keeps_focus(ctx, env):
    p = open_page(ctx, env)
    enter(p, '[data-fk="chip:nba"]')
    assert active(p) == "BUTTON:chip:nba"


def test_escape_returns_to_the_control_that_opened_the_panel(ctx, env):
    p = open_page(ctx, env)
    all_days(p)
    add_btn = '.row[data-row="nba.nba-e2.moon.pts.full_game.18.5"] .add'
    p.focus(add_btn)
    p.keyboard.press("Enter")
    p.wait_for_timeout(100)
    p.keyboard.press("Escape")
    p.wait_for_timeout(150)
    assert active(p) == "BUTTON:row-choose:nba.nba-e2.moon.pts.full_game.18.5"


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
    panel(p, "vargo")
    small = p.evaluate("""() => [...document.querySelectorAll('#panel button')].filter(b => b.offsetParent && b.getBoundingClientRect().height < 44).map(b => b.className)""")
    assert small == []
    go(p, "#/research")
    p.wait_for_timeout(100)
    small = p.evaluate("""() => [...document.querySelectorAll('.add, .chip, .more-btn')].filter(b => b.offsetParent && b.getBoundingClientRect().height < 44).map(b => b.className)""")
    assert small == []
    assert p.evaluate("""() => { const lim = document.getElementById('slip-fab').getBoundingClientRect().top; return [...document.querySelectorAll('.row')].filter(r => r.getBoundingClientRect().bottom <= lim).length; }""") >= 6


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
