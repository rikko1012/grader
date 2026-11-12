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
    for k in ("REQUIRE_WHOP", "RENDER", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "SCOUT_WORKSPACE_PREVIEW", "SCOUT_SLIP_HEADER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    # The suites built on this fixture were written for the design before the 2026-11-11 redesign. They run on the rollback switch
    # (UI_NEXT=0), which keeps that design and its behaviour under test; tests/test_ui_next_*.py cover the redesign, the default.
    monkeypatch.setenv("UI_NEXT", "0")
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


def context(browser, env, size=DESK, user=OWNER):
    c = browser.new_context(viewport=size)
    c.route("https://fonts.*/**", lambda r: r.abort())
    c.route("https://a.espncdn.com/**", lambda r: r.abort())
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
    # Legacy specialist tools remain available through an explicit classic route.
    # The default member workspace is covered by test_research_hub_browser.
    p = open_page(ctx, env, "/?view=classic#board", size)
    p.wait_for_selector("#boardout .brow2.crow .u-hits", timeout=20000)
    p.wait_for_selector("#boardout .u-books .u-bk b", timeout=20000)
    return p


def lol_board(ctx, env, size=None):
    p = open_page(ctx, env, "/lol?view=classic", size)
    p.wait_for_selector(".u-rows .u-lol", timeout=20000)
    return p


def nba_board(ctx, env, size=None):
    p = open_page(ctx, env, "/sports?view=classic#/board", size)
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
    assert "All-hit payout" in text and "Expected return" not in text
    # Show the verified payout, without empty metric boxes or invented probability/return figures.
    assert p.locator('#u-tray .u-slip-metrics>div').count() == 1
    assert p.locator('#u-tray .u-slip-metrics b').inner_text() != '—'
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


def _calibrated_export():
    from tests.workspace_fixture import MODEL
    rows = [{"match": f"m{k}-{i}", "p": p, "over": 1 if i < round(p * 400) else 0, "prop": "kills", "scope": "12", "version": MODEL,
             "start": "2026-10-20T10:00:00+00:00", "units": 0.5, "forecast_id": k * 1000 + i}
            for k, p in enumerate((0.612, 0.598)) for i in range(400)]
    return {"at": iso(datetime.now(timezone.utc)), "rows": rows, "pushes": [], "excluded": {}}


@pytest.mark.parametrize("flag", ["0", "1"])
def test_slip_header_in_the_tray_only_with_its_flag(ctx, env, monkeypatch, flag):
    """SCOUT_SLIP_HEADER: off = the three EV boxes of before; on = gradecore/slip.py payout, all-win chance (pushes excluded),
    expected return and quarter-Kelly, plus per-pick EV, on a verified payout with validated chances. No overflow at 390."""
    monkeypatch.setenv("SCOUT_SLIP_HEADER", flag)
    monkeypatch.setattr(server, "_p1_export", _calibrated_export)
    p = cs2_board(ctx, env, {"width": 390, "height": 844})
    add_two_on_underdog(p)
    p.locator("#u-tray-btn").click()
    p.wait_for_selector("#u-tray [data-u=track]:not([disabled])", timeout=15000)
    p.wait_for_selector("#u-tray .u-slip-metrics>div", timeout=15000)
    text = p.locator("#u-tray").inner_text()
    if flag == "1":
        assert p.locator("#u-tray .u-slip-metrics.u-slip-head>div").count() == 3
        assert "Kelly" not in text and "pushes excluded" in text and "Expected return" in text
        assert p.locator("#u-tray .u-leg-ev").count() == 2
        boxes = p.evaluate("[...document.querySelectorAll('#u-tray .u-slip-head>div')].map(d=>d.getBoundingClientRect().height)")
        assert min(boxes) > 0
    else:
        assert p.locator("#u-tray .u-slip-head").count() == 0 and p.locator("#u-tray .u-leg-ev").count() == 0
        assert p.locator("#u-tray .u-slip-metrics>div").count() == 3 and "Quarter-Kelly" not in text and "Win chance" in text
    assert p.evaluate("document.documentElement.scrollWidth") <= 390
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


def test_no_member_nav_shows_results(ctx, env):
    """Owner 2026-11-09: no member-facing Results page. No link, tab or button in the classic CS2 nav and rail, the LoL nav,
    the Sports tabs or the hub sidebar leads to one (the hub's Record and My picks stay)."""
    def no_results(p, sel):
        p.wait_for_selector(sel, state="attached", timeout=20000)
        bad = p.evaluate(r"""(sel) => [...document.querySelectorAll(sel)].flatMap((n) => [...n.querySelectorAll('a,button')])
          .filter((e) => /\bresults?\b/i.test(e.textContent + ' ' + (e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('title') || ''))
            || /(^|[#/])results(\.html)?([/?#]|$)/.test(e.getAttribute('href') || '') || /results/.test(e.dataset.v || e.dataset.view || e.dataset.u || e.dataset.scoutRoute || ''))
          .map((e) => e.outerHTML.slice(0, 120))""", sel)
        assert not bad, bad
    p = cs2_board(ctx, env)
    no_results(p, "#mode, .scout-rail, .tools-nav")
    p = lol_board(ctx, env)
    p.wait_for_timeout(300)
    no_results(p, ".workspace-nav")
    assert p.locator("[data-u=lol-results]").count() == 0
    p = nba_board(ctx, env)
    no_results(p, "nav.sw-tabs")
    assert p.locator("#sw-views a").count() >= 5            # Slate, Prop board, Sheets, Compare, My picks, Slip Studio stay
    p = open_page(ctx, env, "/#all-sports")
    p.wait_for_selector(".h-sidebar nav a, .h-sidebar nav button", timeout=30000)
    no_results(p, ".h-sidebar nav")
    assert "Record" in p.locator(".h-sidebar nav").inner_text()


def test_sports_board_keeps_its_table_and_adds_books_and_research(ctx, env):
    p = nba_board(ctx, env)
    head = p.locator("table.sw-tb thead").inner_text().upper()
    for word in ("PLAYER", "STAT", "LINE", "BOOK", "LEAN", "SCOUT", "MARKET", "EDGE", "L10", "L5", "CONF"):
        assert word in head, word
    assert p.locator("tr.sw-tb-row .u-bk").count() >= 2
    assert p.evaluate("document.querySelector('.sw-tb-wrap').scrollWidth <= document.querySelector('.sw-tb-wrap').clientWidth + 1"), p.evaluate("""() => {
      const box=document.querySelector('.sw-tb-wrap'), right=box.getBoundingClientRect().right;
      return {width:box.clientWidth,scroll:box.scrollWidth,outside:[...box.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>right+1).slice(0,20).map(e=>({tag:e.tagName,cls:e.className,width:e.getBoundingClientRect().width,text:e.textContent.slice(0,100)}))};
    }""")
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


def test_the_sports_results_page_sends_a_member_to_the_slate(ctx, env):
    """Owner 2026-11-09: /sports/results is admin only; a member's old link lands on the Sports slate."""
    p = open_page(ctx, env, "/sports/results")
    p.wait_for_selector("#sw-views", timeout=20000)
    assert p.url.endswith("/sports#/slate") and p.locator("#rp-main").count() == 0
    assert "Results" not in p.locator("#sw-views").inner_text()


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


# ---- SCOUT_GENIE: the drawer's Ask the Genie tab

def open_genie(p):
    p.locator("#boardout .brow2.crow").filter(has_text="vargo").first.locator(".u-res-btn").click()
    p.wait_for_selector("#u-drawer[open] .u-tiles")
    p.wait_for_function("!document.querySelector('#u-drawer .u-skel')", timeout=15000)
    p.locator("#u-drawer .u-tabs [data-id=genie]").click()
    p.wait_for_selector("#u-drawer .u-genie")


def test_genie_flag_off_keeps_the_evidence_guide_alone(ctx, env, monkeypatch):
    monkeypatch.delenv("SCOUT_GENIE", raising=False)
    p = cs2_board(ctx, env)
    open_genie(p)
    assert p.locator("#u-ask").count() == 0 and "Scout evidence guide" in p.locator("#u-drawer").inner_text()
    assert not p.errors


@pytest.mark.parametrize("size", (DESK, PHONE))
def test_genie_flag_on_answers_with_checked_numbers_and_a_daily_count(browser, env, monkeypatch, size):
    monkeypatch.setenv("SCOUT_GENIE", "1")
    monkeypatch.setenv("SCOUT_GENIE_DAILY", "5")
    c = context(browser, env, size)
    try:
        p = cs2_board(c, env)
        open_genie(p)
        assert p.locator("#u-ask-q").count() == 1 and p.locator("label[for=u-ask-q]").inner_text() == "Ask about this line"
        # review 2026-11-04: the page-wide form{display:grid} squeezed the box into a 96-220px column (input 37px wide at 390)
        lay = p.evaluate("""() => { const f = document.getElementById('u-ask'), i = document.getElementById('u-ask-q');
          return {display: getComputedStyle(f).display, form: f.getBoundingClientRect().width, input: i.getBoundingClientRect().width}; }""")
        assert lay["display"] == "block" and lay["input"] >= lay["form"] * 0.45, lay
        p.locator("#u-ask-q").fill("How often did this line hit?")
        p.locator("#u-ask-q").press("Enter")
        p.wait_for_selector("#u-ask-out p", timeout=15000)
        out = p.locator("#u-ask-out").inner_text()
        assert "of the last 10 results went" in out and "4 of 5 questions left today" in p.locator("#u-ask").inner_text()
        assert p.evaluate("document.activeElement.id") == "u-ask-q"
        p.locator("#u-ask [data-u=genie-ask][data-id='What is the projection?']").click()
        p.wait_for_function("document.querySelector('#u-ask').innerText.includes('3 of 5')", timeout=15000)
        assert "saved projection" in p.locator("#u-ask-out").inner_text()
        small = p.evaluate("""() => [...document.querySelectorAll('#u-ask button, #u-ask input')].filter(e => e.getBoundingClientRect().height < 40).length""")
        assert small == 0
        if size == PHONE:
            no_h_scroll(p)
        assert p.locator("#u-ask-out").get_attribute("aria-live") == "polite"
        text = p.locator("#u-drawer").inner_text().lower()
        assert "financial advice" not in text and "not betting" not in text
        assert not p.errors
    finally:
        c.close()
