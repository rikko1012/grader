"""The upgraded CS2, LoL and Sports pages in a real browser against a local server.Handler with fixture data: the original pages keep
every tool, and the upgrade adds rows, the drawer, the account slip tray and Results on top. Skipped where Playwright or Chromium is
missing. Nothing here reaches a network or production data. UPGRADE_SHOTS=<folder> keeps screenshots of every page at both sizes."""
import json
import os
import threading
import time
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
PHONE = {"width": 390, "height": 844}
OWNER = "the-owner"
SHOTS = Path(os.environ.get("UPGRADE_SHOTS") or "")


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
    for k in ("REQUIRE_WHOP", "RENDER", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "SCOUT_WORKSPACE_PREVIEW"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    monkeypatch.setenv("SPORTS_UI", "1")
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: {"user": v[5:], "name": "Tester", "ok": True} if v and v.startswith("good-") else None)
    monkeypatch.setattr(whop_auth, "cookie_valid", lambda v: bool(v and v.startswith("good-")))
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_ENC_LOCKS", {}), ("_RES_BYTES", [0, 0.0]), ("_MAP1_STATUS", [0.0, None]), ("BOARD", {})):
        monkeypatch.setattr(server, name, val)
    fx = Fixture(tmp_path)
    soon = iso(datetime.now(timezone.utc) + timedelta(hours=3))
    goblin = row("pp:7011", "prizepicks", "vargo", "Halcyon", "Norrland", 34.5, "More", 0.702, 900, soon, odds_type="goblin")
    demon = row("pp:7012", "prizepicks", "vargo", "Halcyon", "Norrland", 42.5, "Less", 0.611, 900, soon, odds_type="demon")
    fx.rows += [goblin, demon]
    fx.install(monkeypatch.setattr)
    maps = []
    for i in range(20):
        val = 24 if i % 5 < 3 else 15                  # 6 of 10 over 38.5, the saved tally
        for n in (1, 2):
            maps.append({"match_id": f"hltv-{i}", "number": n, "date": f"2026-10-{28 - i:02d}", "kills": val, "headshots": 8, "opponent": f"Team{i}", "map": "Mirage"})
    monkeypatch.setattr(hltv, "recent_series", lambda *a, **k: maps)
    workspace_api.reset()
    fx.sports = Sports()
    stub = fx.sports.route
    fx.sports.route = lambda path, q: {"summary": {}, "rows": [], "stats": {}, "method": {}} if path == "/api/sports/results" else stub(path, q)
    for k, v in fx.sports.env().items():
        monkeypatch.setenv(k, v)
    fx.lol = Lol(tmp_path / "lol_center")
    monkeypatch.setattr(server, "LOL_CENTER", fx.lol.dir)
    monkeypatch.setattr(server, "lol_public", lambda: True)
    monkeypatch.setattr(server, "_LOL_MEMBER", {"mtime": None, "body": None, "lock": threading.Lock()})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    fx.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield fx
    httpd.shutdown()
    httpd.server_close()
    workspace_api.reset()
    fx.sports.close()
    fx.lol.close()


def cors(route):
    """The page reaches the stub sports service across origins, as it does the real one, and the stub sends no CORS headers."""
    allow = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "GET, POST, OPTIONS"}
    if route.request.method == "OPTIONS":
        return route.fulfill(status=204, headers=allow)
    r = route.fetch()
    return route.fulfill(response=r, headers={**r.headers, **allow})


def context(browser, env, size=DESK, user=OWNER):
    c = browser.new_context(viewport=size)
    c.route("https://fonts.*/**", lambda r: r.abort())
    c.route("https://a.espncdn.com/**", lambda r: r.abort())
    c.route(env.sports.base + "/**", cors)
    c.add_cookies([{"name": whop_auth.COOKIE, "value": f"good-{user}", "url": env.url}])
    return c


@pytest.fixture
def ctx(browser, env):
    c = context(browser, env)
    yield c
    c.close()


def open_page(ctx, env, path, size=None):
    p = ctx.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    if size:
        p.set_viewport_size(size)
    p.goto(env.url + path)
    return p


def shot(p, name):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        w = p.viewport_size["width"]
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"{name}-{w}.png"))


def cs2_board(ctx, env, size=None):
    p = open_page(ctx, env, "/#board", size)
    p.wait_for_selector("#boardout .brow2.crow .u-hits", timeout=20000)
    p.wait_for_selector("#boardout .u-books .u-bk b", timeout=20000)
    return p


def lol_board(ctx, env, size=None):
    p = open_page(ctx, env, "/lol", size)
    p.wait_for_selector(".u-rows .u-lol", timeout=20000)
    return p


def nba_board(ctx, env, size=None):
    p = open_page(ctx, env, "/sports#/board", size)
    p.wait_for_selector("tr.sw-tb-row .u-add", timeout=20000)
    p.wait_for_selector(".u-books .u-bk b", timeout=20000)
    return p


def no_h_scroll(p):
    wide = p.evaluate("""() => { const w = document.documentElement.clientWidth, out = [];
      if (document.documentElement.scrollWidth <= w + 1) return out;
      document.querySelectorAll('body *').forEach((e) => { const r = e.getBoundingClientRect(); if (r.right > w + 1 && r.width > 0 && !e.closest('[hidden]')) out.push(e.tagName + '.' + e.className); });
      return out.slice(0, 8); }""")
    assert not wide


def test_cs2_board_keeps_every_filter_and_adds_aligned_rows(ctx, env):
    p = cs2_board(ctx, env)
    for sel in ("#bbar", "#bbook", "#bprop", "#bscope", "#bside", "#bgrade", "#bchance", "#bquick", "#bsort", "#bbets", "#bflag", "#bviews", "#brefresh", "#scout-density", "#scout-guide"):
        assert p.locator(sel).count() == 1, sel
    assert p.evaluate("getComputedStyle(document.querySelector('#bbar')).position") == "static"
    head = p.locator("#boardout .crow-head").first.inner_text().upper()
    for word in ("GRADE", "PLAYER", "MARKET", "LINE", "PROJ", "PICK", "HIT RATE", "BOOKS", "STAKE"):
        assert word in head, word
    tops = p.evaluate("""() => { const h = document.querySelector('#boardout .crow-head'), r = document.querySelector('#boardout .brow2.crow');
      return [...h.children].slice(0, 9).map((x, i) => Math.abs(x.getBoundingClientRect().left - r.children[i].getBoundingClientRect().left)); }""")
    assert max(tops) < 13                               # each label sits over its column, the row's padding apart
    shot(p, "cs2-board")
    p.evaluate("document.querySelector('#boardout').scrollIntoView()")
    shot(p, "cs2-rows")
    assert not p.errors


def test_cs2_board_groups_a_players_lines_under_one_row(ctx, env):
    p = cs2_board(ctx, env)
    lead = p.locator("#boardout .brow2.crow", has=p.locator(".u-alt-btn")).filter(has_text="vargo").first
    btn = lead.locator(".u-alt-btn")
    assert "+" in btn.inner_text() and btn.get_attribute("aria-expanded") == "false"
    box = p.locator("#" + btn.get_attribute("aria-controls"))
    assert not box.is_visible()
    n = box.locator(".brow2.crow").count()
    assert n >= 2
    btn.click()
    assert box.is_visible() and btn.get_attribute("aria-expanded") == "true"
    assert box.locator(".brow2.crow").first.locator("[data-bslip]").count() == 1      # a sub-row keeps the page's own tools
    assert p.locator("#cardpop").is_hidden()
    p.evaluate("drawBoard()")                           # a redraw keeps it open
    p.wait_for_selector("#boardout .u-alts:not([hidden]) .brow2.crow")
    assert not p.errors


def test_cs2_matches_view_gets_the_same_rows_and_the_desk_keeps_its_own(ctx, env):
    p = open_page(ctx, env, "/#matches")
    p.wait_for_selector("#boardout .mgroup[open] .brow2.crow .u-hits", timeout=20000)
    p.locator("#boardout .ot-open-match").first.click()
    p.wait_for_selector("#ot-match-desk[open]")
    assert p.locator("#ot-match-desk .u-hits").count() == 0
    assert not p.errors


def test_cs2_original_tools_still_work_on_an_upgraded_row(ctx, env):
    p = cs2_board(ctx, env)
    row_ = p.locator("#boardout .brow2.crow", has=p.locator("[data-bwhy]")).first
    row_.locator("[data-bwhy]").click()
    assert row_.locator(".c-why").is_visible()
    row_.locator("[data-bslip]").click()
    p.wait_for_function("document.querySelector('#slipbtn') && !document.querySelector('#slipbtn').hidden")
    assert "On slip" in p.locator("#boardout").inner_text() or p.locator("#slip").count()
    assert not p.errors


def test_cs2_drawer_history_forecast_books_context_then_track_to_receipt(ctx, env):
    p = cs2_board(ctx, env)
    r = p.locator("#boardout .brow2.crow").filter(has_text="vargo").first
    r.locator(".u-res-btn").click()
    p.wait_for_selector("#u-drawer[open] .u-tiles")
    p.wait_for_selector("#u-drawer .u-chart svg", timeout=15000)
    body = p.locator("#u-drawer").inner_text()
    assert "vargo" in body and "L5" in body and "L10" in body and "L20" in body and "Forecast chance" in body
    assert p.locator("#cardpop").is_hidden()            # the drawer sits beside the card, it does not open it
    shot(p, "cs2-drawer")
    p.locator("#u-drawer .u-tabs [data-id=books]").click()
    assert "PrizePicks" in p.locator("#u-drawer").inner_text() and "Underdog" in p.locator("#u-drawer").inner_text()
    p.locator("#u-drawer .u-tabs [data-id=context]").click()
    p.wait_for_selector("#u-drawer .u-kv")
    assert "Rating" in p.locator("#u-drawer").inner_text() and "secret" not in p.locator("#u-drawer").inner_text()
    p.locator("#u-drawer .u-tabs [data-id=overview]").click()
    p.locator("#u-drawer .u-gold").click()
    p.locator("#u-drawer [data-u=tray]").click()
    p.wait_for_selector("#u-tray[open] .u-entry")
    assert "Checking" in p.locator("#u-tray").inner_text() or p.locator("#u-tray .u-entry").count() == 1
    p.wait_for_selector("#u-tray .u-status b:has-text('ready')", timeout=15000)
    shot(p, "cs2-tray")
    assert not p.errors


def add_two_on_underdog(p):
    p.locator("#bbook [data-v=underdog]").click()
    p.wait_for_selector("#boardout .brow2.crow .u-bk b")
    for nick in ("vargo", "orlo"):
        p.locator("#boardout .brow2.crow").filter(has_text=nick).first.locator(".u-add").click()
    p.wait_for_function("document.querySelector('#u-tray-btn').textContent.includes('· 2')")


def test_a_repaint_keeps_the_keyboard_where_it_was(ctx, env):
    p = cs2_board(ctx, env)
    b = p.locator("#boardout .brow2.crow").filter(has_text="vargo").first.locator(".u-add")
    b.focus()
    p.keyboard.press("Enter")
    p.wait_for_function("document.querySelector('#u-tray-btn').textContent.includes('· 1')")
    assert p.evaluate("document.activeElement.className").startswith("u-add") and p.evaluate("document.activeElement.textContent") == "On tray"
    p.locator("#boardout .brow2.crow").filter(has_text="vargo").first.locator(".u-res-btn").click()
    p.wait_for_selector("#u-drawer .u-chart svg", timeout=15000)
    p.locator("#u-drawer .u-tabs [data-id=history]").focus()
    p.keyboard.press("Enter")
    p.wait_for_selector("#u-drawer .u-tbl")
    assert p.evaluate("document.activeElement.dataset.id") == "history"
    p.keyboard.press("Escape")
    assert p.locator("#u-drawer").is_hidden()
    assert not p.errors


def test_slip_tray_recheck_track_receipt_record(ctx, env):
    p = cs2_board(ctx, env)
    add_two_on_underdog(p)
    p.locator("#u-tray-btn").click()
    p.wait_for_selector("#u-tray .u-status b:has-text('ready')", timeout=15000)
    p.wait_for_selector("#u-tray [data-u=track]:not([disabled])", timeout=15000)
    text = p.locator("#u-tray").inner_text()
    assert "All-hit payout" in text and "Expected return" in text
    # Current validation failures keep probability/return figures absent, not fabricated from leg chances.
    assert p.locator('#u-tray .u-slip-metrics>div').nth(1).locator('b').inner_text() == '—'
    assert p.locator('#u-tray .u-slip-metrics>div').nth(2).locator('b').inner_text() == '—'
    p.locator("#u-tray [data-u=track]").first.click()
    p.wait_for_selector("#u-tray .u-note.good", timeout=15000)
    p.locator("#u-tray .u-note.good [data-u=tab]").click()
    p.wait_for_selector("#u-tray .u-pick", timeout=15000)
    assert "vargo" in p.locator("#u-tray").inner_text() and "Pending" in p.locator("#u-tray").inner_text()
    shot(p, "cs2-receipts")
    p.locator("#u-tray .u-pick button").first.click()
    assert "Chance you saw" in p.locator("#u-tray").inner_text()
    p.locator("#u-tray-tabs [data-id=record]").click()
    assert "Hit, miss, push" in p.locator("#u-tray").inner_text()
    assert not p.errors


def test_the_slip_tray_follows_the_account_across_the_three_pages(ctx, env):
    p = cs2_board(ctx, env)
    p.locator("#boardout .brow2.crow").filter(has_text="vargo").first.locator(".u-add").click()
    p.wait_for_function("document.querySelector('#u-tray-btn').textContent.includes('· 1')")
    deadline = time.time() + 10
    while time.time() < deadline:                       # the save is debounced
        if (json.loads(p.evaluate("fetch('/api/upgrade/state').then(r => r.text())")).get("slip") or {}).get("legs"):
            break
        p.wait_for_timeout(200)
    q = lol_board(ctx, env)
    q.wait_for_function("document.querySelector('#u-tray-btn').textContent.includes('· 1')")
    q.locator(".u-lol .u-add").first.click()
    q.wait_for_function("document.querySelector('#u-tray-btn').textContent.includes('· 2')")
    deadline = time.time() + 10
    while time.time() < deadline:
        if len((json.loads(q.evaluate("fetch('/api/upgrade/state').then(r => r.text())")).get("slip") or {}).get("legs") or []) == 2:
            break
        q.wait_for_timeout(200)
    s = nba_board(ctx, env)
    s.wait_for_function("document.querySelector('#u-tray-btn').textContent.includes('· 2')")
    s.locator("tr.sw-tb-row .u-add").first.click()
    s.wait_for_function("document.querySelector('#u-tray-btn').textContent.includes('· 3')")
    s.locator("#u-tray-btn").click()
    s.wait_for_selector("#u-tray .u-entry")
    text = s.locator("#u-tray").inner_text()
    assert "CS2" in text and "LoL" in text and "NBA" in text and "Research estimate" in text
    shot(s, "sports-tray")
    assert not p.errors + q.errors + s.errors


def test_lol_keeps_its_layouts_and_adds_rows(ctx, env):
    p = lol_board(ctx, env)
    seg = p.locator("#layout")
    assert seg.locator("button").all_inner_texts() == ["Rows", "Cards", "Table"]
    head = p.locator(".u-rows .u-head").inner_text().upper()
    for word in ("GRADE", "PLAYER", "LINE", "PROJ", "HIT RATE", "BOOKS"):
        assert word in head, word
    assert p.locator(".u-lol .u-hc").count() >= 3
    shot(p, "lol-board")
    assert p.locator(".cards").first.is_hidden() and p.locator(".tablebox.board").is_hidden()
    seg.locator("button", has_text="Table").click()
    p.wait_for_selector(".tablebox.board tr.row")
    assert p.locator(".u-board").count() == 0 and p.locator(".tablebox.board").is_visible()
    seg.locator("button", has_text="Cards").click()
    p.wait_for_selector(".cards .mcard")
    assert p.locator(".u-board").count() == 0
    seg.locator("button", has_text="Rows").click()
    p.wait_for_selector(".u-board .u-lol")
    assert p.locator(".cards").first.is_hidden()
    assert not p.errors


def test_lol_row_opens_its_card_and_research_opens_the_drawer(ctx, env):
    p = lol_board(ctx, env)
    r = p.locator(".u-lol").first
    r.locator(".u-res-btn").click()
    p.wait_for_selector("#u-drawer[open] .u-tiles")
    p.wait_for_function("!document.querySelector('#u-drawer .u-skel')", timeout=15000)
    assert "Forecast chance" in p.locator("#u-drawer").inner_text()
    shot(p, "lol-drawer")
    p.locator("#u-drawer [data-u=pclose]").click()
    r.locator(".u-who").click()
    p.wait_for_selector("#modal.open", timeout=5000)
    assert not p.errors


def test_lol_results_tab_shows_stated_and_observed(ctx, env):
    p = lol_board(ctx, env)
    p.locator("#v [data-u=lol-results]").click()
    p.wait_for_selector(".u-lol-results .u-res-tbl", timeout=10000)
    text = p.locator(".u-lol-results").inner_text()
    for word in ("BY LETTER", "BY MARKET", "STATED", "OBSERVED", "SAMPLE", "PUSHES"):
        assert word in text.upper(), word
    shot(p, "lol-results")
    assert p.locator("#app > .bar").is_hidden() and p.locator(".u-rows").count() == 0
    p.locator("#v button[data-v=board]").click()
    p.wait_for_selector(".u-board .u-lol")
    assert p.locator(".u-lol-results").count() == 0 and p.locator("#app > .bar").is_visible()
    assert not p.errors


def test_sports_board_keeps_its_table_and_adds_books_and_research(ctx, env):
    p = nba_board(ctx, env)
    head = p.locator("table.sw-tb thead").inner_text().upper()
    for word in ("PLAYER", "STAT", "LINE", "BOOK", "LEAN", "SCOUT", "MARKET", "EDGE", "L10", "L5", "CONF"):
        assert word in head, word
    assert p.locator("tr.sw-tb-row .u-bk").count() >= 2
    assert p.evaluate("document.querySelector('.sw-tb-wrap').scrollWidth <= document.querySelector('.sw-tb-wrap').clientWidth + 1")
    shot(p, "sports-board")
    p.locator("tr.sw-tb-row .u-res-btn").first.click()
    p.wait_for_selector("#u-drawer[open] .u-tiles")
    p.wait_for_function("!document.querySelector('#u-drawer .u-skel')", timeout=15000)
    text = p.locator("#u-drawer").inner_text()
    assert "Research estimate" in text and "Forecast chance" in text
    assert "Grade" not in p.locator("#u-drawer .u-forecast").inner_text()
    shot(p, "sports-drawer")
    assert not p.errors


def test_sports_cards_and_the_other_league_get_research_and_the_tray(ctx, env):
    p = nba_board(ctx, env)
    p.locator("[data-tview=cards]").click()
    p.wait_for_selector("#sw-main [data-ukey] .u-res-btn", timeout=15000)
    p.locator("#sw-main [data-ukey] .u-res-btn").first.click()
    p.wait_for_selector("#u-drawer[open] .u-tiles")
    p.locator("#u-drawer [data-u=pclose]").click()
    p.locator("[data-tview=table]").click()
    p.wait_for_selector("tr.sw-tb-row .u-add")
    p.locator("#sw-league [data-lg=nfl]").click()
    p.wait_for_selector("tr.sw-tb-row .u-res-btn", timeout=15000)
    p.wait_for_function("document.querySelector('tr.sw-tb-row .u-add') && [...document.querySelectorAll('.u-bk')].length > 0", timeout=15000)
    p.locator("tr.sw-tb-row .u-res-btn").first.click()
    p.wait_for_selector("#u-drawer[open] .u-tiles")
    assert "NFL" in p.locator("#u-drawer").inner_text()
    assert not p.errors


def test_the_public_sports_results_page_gets_both_leagues(ctx, env):
    p = open_page(ctx, env, "/sports/results")
    p.wait_for_selector(".u-res-box .u-res-tbl", timeout=20000)
    assert "NBA STATED AND OBSERVED" in p.locator(".u-res-box").first.inner_text().upper()
    assert p.locator(".u-res-box").count() == 2 and p.locator("#u-tray-btn").count() == 0


def test_sports_results_add_stated_and_observed_above_the_page(ctx, env):
    p = open_page(ctx, env, "/sports#/results")
    p.wait_for_selector("#sw-main .u-res-box .u-res-tbl", timeout=20000)
    text = p.locator("#sw-main .u-res-box").inner_text()
    for word in ("BY CHANCE BAND", "BY MARKET", "PUSHES", "RESEARCH ESTIMATES"):
        assert word in text.upper(), word
    assert p.locator("#sw-main h1").inner_text() == "Results"
    shot(p, "sports-results")
    assert not p.errors


def test_cs2_results_add_letters_and_markets_above_the_page(ctx, env):
    p = open_page(ctx, env, "/#results")
    p.wait_for_selector("#resultsout .u-res-box .u-res-tbl", timeout=20000)
    text = p.locator("#resultsout .u-res-box").inner_text()
    assert "BY LETTER" in text.upper() and "BY MARKET" in text.upper() and "Kills Maps 1" in text
    assert "By stat and maps" in p.locator("#resultsout").inner_text()   # the page's own tables stay
    shot(p, "cs2-results")
    assert not p.errors


def test_a_failing_upgrade_api_leaves_the_page_as_it_was(ctx, env):
    p = ctx.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.route("**/api/upgrade/**", lambda r: r.abort())
    p.goto(env.url + "/#board")
    p.wait_for_selector("#boardout .brow2.crow", timeout=20000)
    p.wait_for_timeout(600)
    assert p.locator("#boardout [data-bslip]").count() >= 1 and p.locator("#u-tray-btn").is_hidden()
    assert not p.errors


def test_phone_pages_have_no_sideways_scroll(browser, env):
    c = context(browser, env, PHONE)
    try:
        p = cs2_board(c, env)
        no_h_scroll(p)
        shot(p, "cs2-board")
        p.evaluate("document.querySelector('#boardout').scrollIntoView()")
        shot(p, "cs2-rows")
        p.locator("#boardout .brow2.crow").filter(has_text="vargo").first.locator(".u-res-btn").click()
        p.wait_for_selector("#u-drawer[open] .u-tiles")
        p.wait_for_selector("#u-drawer .u-chart svg", timeout=15000)
        shot(p, "cs2-drawer")
        p.locator("#u-drawer .u-gold").click()
        p.locator("#u-drawer [data-u=tray]").click()
        p.wait_for_selector("#u-tray[open] .u-status b:has-text('ready')", timeout=15000)
        shot(p, "cs2-tray")
        q = open_page(c, env, "/#results")
        q.wait_for_selector("#resultsout .u-res-box .u-res-tbl", timeout=20000)
        no_h_scroll(q)
        shot(q, "cs2-results")
        q = lol_board(c, env)
        no_h_scroll(q)
        shot(q, "lol-board")
        q.locator(".u-lol .u-res-btn").first.click()
        q.wait_for_selector("#u-drawer[open] .u-tiles")
        shot(q, "lol-drawer")
        q = lol_board(c, env)
        q.locator("#v [data-u=lol-results]").click()
        q.wait_for_selector(".u-lol-results .u-res-tbl", timeout=10000)
        no_h_scroll(q)
        shot(q, "lol-results")
        q = nba_board(c, env)
        no_h_scroll(q)
        shot(q, "sports-board")
        q.locator("tr.sw-tb-row .u-res-btn").first.click()
        q.wait_for_selector("#u-drawer[open] .u-tiles")
        shot(q, "sports-drawer")
        q = open_page(c, env, "/sports#/results")
        q.wait_for_selector("#sw-main .u-res-box .u-res-tbl", timeout=20000)
        no_h_scroll(q)
        shot(q, "sports-results")
    finally:
        c.close()
