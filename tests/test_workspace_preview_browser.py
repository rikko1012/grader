"""Research workspace preview in a real browser: the slip gate, receipts, results wording, focus and layout. Skipped where
Playwright or Chromium is missing. The page is static here: these checks are about web/workspace*, not server.py."""
import http.server
import json
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

WEB = Path(__file__).resolve().parent.parent / "web"
FILES = {"/preview/workspace": ("workspace.html", "text/html; charset=utf-8"),
         "/preview/workspace/workspace.css": ("workspace.css", "text/css"),
         "/preview/workspace/workspace.js": ("workspace.js", "text/javascript"),
         "/preview/workspace/workspace-data.js": ("workspace-data.js", "text/javascript")}
DESK = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
VARGO = "cs2.cs2-e1.vargo.kills.maps12.38.5"
RECHECK = '#rail-body [data-act="recheck"]'
TRACK_PP = '#rail-body [data-act="track"][data-id="prizepicks|cs2"]'
MIN = 60_000


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        f = FILES.get(self.path.split("?")[0])
        if not f:
            self.send_response(404)
            self.end_headers()
            return
        body = (WEB / f[0]).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f[1])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def url():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/preview/workspace"
    httpd.shutdown()


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
def ctx(browser):
    c = browser.new_context(viewport=DESK)
    c.route("https://fonts.*/**", lambda r: r.abort())
    yield c
    c.close()


def open_page(ctx, url, clock=False, path="", size=None):
    p = ctx.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    if size:
        p.set_viewport_size(size)
    if clock:
        p.clock.install()
    p.goto(url + path)
    if clock:
        p.clock.run_for(400)
    else:
        p.wait_for_timeout(350)
    return p


def recheck(p, clock=False, sel=RECHECK):
    p.click(sel)
    if clock:
        p.clock.run_for(800)
    else:
        p.wait_for_timeout(750)


def force(p, state):
    p.evaluate("""s => { const e = document.getElementById('force-state'); e.value = s; e.dispatchEvent(new Event('change', { bubbles: true })); }""", state)


def stored(p, key):
    return p.evaluate("k => JSON.parse(localStorage.getItem(k))", key)


def go(p, h):
    p.evaluate("h => { location.hash = h; }", h)
    p.wait_for_timeout(60)


def active(p):
    return p.evaluate("""() => { const a = document.activeElement; return a.tagName + ':' + (a.dataset.fk || a.id || ''); }""")


def enter(p, sel):
    p.focus(sel)
    p.keyboard.press("Enter")


# ---- the slip gate
def test_a_recheck_ages_on_screen_with_no_input(ctx, url):
    p = open_page(ctx, url, clock=True)
    recheck(p, clock=True)
    assert p.is_enabled(TRACK_PP)
    p.clock.run_for(6 * MIN)
    assert "Last rechecked" in p.inner_text("#rail-body .slip-status") and p.is_disabled(TRACK_PP)
    assert p.locator("#rail-body .leg-ok").count() == 0


def test_rows_and_slip_go_off_when_the_game_starts(ctx, url):
    p = open_page(ctx, url, clock=True)
    recheck(p, clock=True)
    p.clock.run_for(70 * MIN)
    row = p.locator(f'.row[data-row="{VARGO}"]')
    assert "Game started" in row.inner_text() and row.locator(".add").is_disabled()
    assert "in 55m" not in row.inner_text()
    assert p.is_disabled(TRACK_PP)


def test_track_and_copy_ask_again_at_the_click(ctx, url):
    p = open_page(ctx, url, clock=True)
    recheck(p, clock=True)
    p.evaluate("""() => { const t = Date.now() + 70 * 60000; Date.now = () => t;
      document.querySelector('#rail-body [data-act="copy"][data-id="prizepicks|cs2"]').click();
      document.querySelector('#rail-body [data-act="track"][data-id="prizepicks|cs2"]').click(); }""")
    p.clock.run_for(50)
    assert stored(p, "ws:receipts:v1") in (None, [])
    assert "Recheck before" in p.inner_text("body > .toast")


def test_saved_pass_is_not_restored_over_a_stale_feed(ctx, url):
    p = open_page(ctx, url)
    p.fill("#search", "")
    p.evaluate("""() => { document.querySelector('[data-act="retry-feed"]').click(); }""")
    p.wait_for_timeout(800)
    for key in ("lol.lol-e1.ashen.kills.maps12.7.5", "lol.lol-e1.pell.kills.maps12.6.5"):
        go(p, "#/research/p/" + key)
        p.wait_for_timeout(100)
        p.evaluate("""() => { const b = [...document.querySelectorAll('.book-opt')].find(x => x.textContent === 'Underdog'); if (b) b.click(); }""")
        p.click('[data-act="p-add"]')
        go(p, "#/research")
    recheck(p)
    assert p.locator('#rail-body [data-act="track"][data-id="underdog|lol"]').is_enabled()
    p.reload()
    p.wait_for_timeout(350)
    status = p.inner_text("#rail-body .slip-status")
    assert "Changed since" in status and p.locator('#rail-body [data-act="track"][data-id="underdog|lol"]').is_disabled()
    assert p.locator("#rail-body .leg-ok").count() == 0


def test_outage_blocks_recheck_and_withdrawn_shows_no_pass(ctx, url):
    p = open_page(ctx, url)
    force(p, "unavailable")
    before = stored(p, "ws:slip:v1")["check"]
    p.locator(RECHECK).click(force=True)
    p.wait_for_timeout(750)
    assert stored(p, "ws:slip:v1")["check"] == before
    assert p.get_attribute(RECHECK, "aria-disabled") == "true"
    force(p, "ok")
    recheck(p)
    assert p.locator("#rail-body .leg-ok").count() == 2
    force(p, "withdrawn")
    assert p.locator("#rail-body .leg-ok").count() == 0
    assert "withdrawn" in p.inner_text("#rail-body .slip-status") and p.is_disabled(TRACK_PP)


def test_stale_with_no_recheck_claims_nothing(ctx, url):
    p = open_page(ctx, url)
    force(p, "stale")
    text = p.inner_text("#rail-body")
    assert "Not rechecked" in text and "Last rechecked" not in text and "Recheck last updated" not in text


def test_forced_loading_has_a_retry_everywhere(ctx, url):
    p = open_page(ctx, url)
    force(p, "loading")
    for sel in ("#board-list", "#rail-body"):
        assert p.locator(f'{sel} [data-act="retry"]').count() == 1, sel


# ---- receipts
def test_two_tabs_never_erase_receipts(ctx, url):
    a = open_page(ctx, url)
    b = open_page(ctx, url)
    go(b, "#/research/p/" + "nba.nba-e2.brandt.pra.full_game.33.5")
    b.wait_for_timeout(50)
    b.click('[data-act="p-add"]')
    go(b, "#/research/p/" + "nba.nba-e1.lind.reb.full_game.8.5")
    b.wait_for_timeout(50)
    b.click('[data-act="p-add"]')
    go(b, "#/research")
    a.wait_for_timeout(100)
    recheck(a)
    a.click(TRACK_PP)
    recheck(b)
    b.click('#rail-body [data-act="track"][data-id="prizepicks|nba"]')
    b.wait_for_timeout(100)
    assert sorted(r["player"] for r in stored(a, "ws:receipts:v1")) == ["Cal Brandt", "Theo Lind", "brandr", "vargo"]
    a.wait_for_timeout(100)
    assert a.evaluate("document.querySelectorAll('#view-picks .pick-card').length") == 12


def test_receipt_link_opens_from_any_filter(ctx, url):
    p = open_page(ctx, url, path="#/picks")
    p.click('[data-act="picks-filter"][data-id="pending"]')
    go(p, "#/results")
    p.locator('#view-results a[href="#/picks/rc-5012"]').click()
    p.wait_for_timeout(100)
    assert p.locator("#pick-rc-5012").is_visible()


def test_withdrawn_is_not_settled(ctx, url):
    p = open_page(ctx, url, path="#/picks")
    p.click('[data-act="picks-filter"][data-id="settled"]')
    assert "Ashen" not in p.inner_text("#view-picks") and "Yuna" in p.inner_text("#view-picks")


# ---- slip content
def test_leg_against_scout_names_the_side_of_its_chance(ctx, url):
    p = open_page(ctx, url, path="#/research/p/" + VARGO)
    p.click('#rail-body [data-act="slip-clear"]')
    p.click('[data-act="p-side"][data-id="under"]')
    p.click('[data-act="p-add"]')
    meta = p.inner_text("#rail-body .leg-meta")
    assert "Scout side is Over. Under chance 38.8%" in meta


def test_research_footer_follows_the_slip(ctx, url):
    p = open_page(ctx, url, path="#/research/p/" + VARGO)
    assert "On your slip" in p.inner_text(".p-foot")
    p.click('#rail-body [aria-label="Remove vargo Over 38.5"]')
    assert "On your slip" not in p.inner_text(".p-foot") and p.get_attribute('[data-act="p-add"]', "aria-disabled") is None


def test_row_tick_sees_every_platform(ctx, url):
    p = open_page(ctx, url, path="#/research/p/" + VARGO)
    p.click('#rail-body [data-act="slip-clear"]')
    p.click('[data-act="p-book"][data-id="underdog"]')
    p.click('[data-act="p-add"]')
    go(p, "#/research")
    add = p.locator(f'.row[data-row="{VARGO}"] .add')
    assert add.get_attribute("aria-pressed") == "true" and "Underdog" in add.get_attribute("aria-label")
    add.click()
    assert stored(p, "ws:slip:v1")["legs"] == []


def test_copy_failure_says_so(ctx, url):
    p = open_page(ctx, url)
    recheck(p)
    p.evaluate("""() => { navigator.clipboard.writeText = () => Promise.reject(new Error('no')); document.execCommand = () => false; }""")
    p.click('#rail-body [data-act="copy"][data-id="prizepicks|cs2"]')
    p.wait_for_timeout(50)
    assert "Copy failed" in p.inner_text("body > .toast")


def test_wrong_period_forecast_is_not_shown(ctx, url):
    p = open_page(ctx, url)
    p.select_option("#f-day", "all")
    row = p.locator('.row[data-row="cs2.cs2-e2.orlo.kills.map1.17.5"]')
    assert "Forecast covers Maps 1-2" in row.inner_text() and "55.7" not in row.inner_text() and row.locator(".add").is_disabled()
    row.locator(".row-open").click()
    p.wait_for_timeout(100)
    body = p.inner_text("#panel")
    assert "55.7" not in body and "No Map 1 forecast" in body
    assert p.get_attribute('[data-act="p-add"]', "aria-disabled") == "true"


def test_sample_tag_is_visible_over_the_phone_panel_and_sheet(ctx, url):
    p = open_page(ctx, url, path="#/research/p/" + VARGO, size=PHONE)
    assert p.locator("#panel .sample-tag").is_visible()
    go(p, "#/research")
    p.click("#slip-fab")
    assert p.locator("#slip-dialog .sample-tag").is_visible()


# ---- results
def test_results_filter_covers_the_tracked_list_and_settled_counts_pushes(ctx, url):
    p = open_page(ctx, url, path="#/results")
    p.select_option("#res-sport", "lol")
    tracked = p.inner_text("#view-results .res-sec:last-of-type")
    assert "Ashen" in tracked and "vargo" not in tracked and "Marcus Delvane" not in tracked
    p.select_option("#res-sport", "all")
    p.click('[data-act="group"][data-id="market"]')
    first = p.locator("#view-results tbody tr").first
    assert "CS2 Kills" in first.inner_text()
    settled = {r.locator("td").first.inner_text(): r.locator('td[data-l="Settled"]').inner_text() for r in p.locator("#view-results tbody tr").all()}
    assert settled["CS2 Kills"] == str(164 + 151 + 4 + 142 + 118 + 6)


def test_results_says_whose_picks_the_table_covers(ctx, url):
    text = open_page(ctx, url, path="#/results").inner_text("#view-results")
    assert "not added to this table" in text and "kept apart from the table above" in text
    assert "Counts as a hit in your record" in text and "Not counted" in text and "\nCounted." not in text


def test_slip_page_is_the_only_slip_on_desktop(ctx, url):
    p = open_page(ctx, url, path="#/slips")
    assert not p.locator("#rail").is_visible() and p.locator("#view-slips .slip").is_visible()
    tracked = p.inner_text("#view-slips .res-sec")
    assert "Underdog · NBA" in tracked and "Underdog · NFL" in tracked


def test_consensus_only_counts_books_it_lists(ctx, url):
    p = open_page(ctx, url, path="#/research/p/nba.nba-e1.delvane.pts.full_game.24.5/books")
    assert "Market at" not in p.inner_text("#panel")
    go(p, "#/research/p/nba.nba-e2.brandt.pra.full_game.33.5/books")
    p.wait_for_timeout(100)
    assert "from Underdog, Kalshi" in p.inner_text("#panel")


def test_stale_lol_never_carries_a_fresh_time(ctx, url):
    p = open_page(ctx, url, path="#/research/p/lol.lol-e1.ashen.kills.maps12.7.5/books")
    now = p.evaluate("Date.now()")
    seen = p.evaluate("""() => WORKSPACE_DATA.records.filter(r => r.sport === 'lol').map(r => r.offer.seen_at)""")
    assert seen and all(now - p.evaluate("s => Date.parse(s)", s) >= 30 * MIN for s in seen)
    go(p, "#/research")
    p.click('[data-act="chip"][data-id="lol"]')
    assert p.evaluate("""() => { const f = WORKSPACE_DATA.feeds.lol.at; return document.getElementById('board-meta').textContent.includes(new Date(f).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })); }""")


# ---- search and routing
def test_search_from_another_view_shows_the_board_and_keeps_typing(ctx, url):
    p = open_page(ctx, url, path="#/results")
    p.locator("#search").press_sequentially("vargo")
    p.wait_for_timeout(100)
    assert p.locator("#view-research").is_visible() and p.locator(".row").count() == 2
    assert p.evaluate("document.activeElement.id") == "search" and p.input_value("#search") == "vargo"


def test_phone_search_closes_by_touch_and_keyboard(ctx, url):
    p = open_page(ctx, url, size=PHONE)
    p.click("#search-toggle")
    assert p.locator(".sample-tag").first.is_visible() and p.locator(".brand").is_visible()
    box = p.locator("#search-toggle").bounding_box()
    hit = p.evaluate("([x, y]) => document.elementFromPoint(x, y).closest('#search-toggle') !== null", [box["x"] + 20, box["y"] + 20])
    assert hit
    p.keyboard.press("Escape")
    assert p.evaluate("document.activeElement.id") == "search-toggle"
    assert p.get_attribute("#search-toggle", "aria-expanded") == "false" and not p.locator("#search").is_visible()


@pytest.mark.parametrize("bad", ["#/research/p/%E0%A4%A", "#/research/p/%"])
def test_bad_link_still_boots(ctx, url, bad):
    p = open_page(ctx, url, path=bad)
    assert p.locator(".row").count() > 5 and p.errors == []


@pytest.mark.parametrize("key,value", [
    ("ws:slip:v1", "{}"), ("ws:slip:v1", '{"legs":[{"id":1}],"check":7}'), ("ws:slip:v1", "not json"),
    ("ws:receipts:v1", '{"a":1}'), ("ws:receipts:v1", '[{"id":"x","timeline":[{"at":"nope"}]}]'),
    ("ws:views:v1", '[{"id":"x"}]'), ("ws:views:v1", '[{"id":"x","name":"v","state":{"chip":"all","f":{"sort":"nope"}}}]')])
def test_bad_saved_data_still_boots(ctx, url, key, value):
    ctx.add_init_script(f"localStorage.setItem({json.dumps(key)}, {json.dumps(value)});")
    p = open_page(ctx, url)
    assert p.locator(".row").count() > 5 and p.errors == []


def test_saved_markup_stays_text(ctx, url):
    evil = "<img src=x onerror=window.__xss=1>"
    p = open_page(ctx, url)
    slip = stored(p, "ws:slip:v1")
    slip["legs"][0]["player"] = evil
    slip["legs"][0]["stat_label"] = evil
    slip["check"] = {"at": p.evaluate("new Date().toISOString()"), "sig": ";".join(l["id"] for l in slip["legs"]), "unknown": 0, "fails": [0, 0, 0, 0, 0],
                     "legs": {l["id"]: {"ok": False, "problems": [{"c": 1, "text": evil}]} for l in slip["legs"]},
                     "entries": {"prizepicks|cs2": {"ok": False, "problems": [{"c": 3, "text": evil}]}, "underdog|nba": {"ok": False, "problems": []}}}
    receipt = {"id": "x", "slip_id": "y", "sport": "cs2", "player": evil, "matchup": evil, "stat_label": evil, "period_label": evil, "platform": "underdog", "platform_label": evil,
               "side": "over", "line": 1, "line_type": "standard", "offer_revision": evil, "revision_kind": "exact", "model_version": evil, "forecast_id": evil,
               "forecast_at": "2026-01-01T00:00:00Z", "displayed_p": 0.5, "tracked_at": "2026-01-01T00:00:00Z", "final": None,
               "timeline": [{"at": "2026-01-01T00:00:00Z", "kind": evil, "text": evil}]}
    p.evaluate("""([s, r]) => { localStorage.setItem('ws:slip:v1', JSON.stringify(s)); localStorage.setItem('ws:receipts:v1', JSON.stringify([r])); }""", [slip, receipt])
    p.reload()
    p.wait_for_timeout(350)
    go(p, "#/picks/x")
    p.wait_for_timeout(200)
    assert p.evaluate("window.__xss") is None and p.locator("img[src=x]").count() == 0
    # a line that is not a number never reaches the page at all
    slip["legs"][0]["line"] = evil
    p.evaluate("s => localStorage.setItem('ws:slip:v1', JSON.stringify(s))", slip)
    p.reload()
    p.wait_for_timeout(350)
    assert p.evaluate("window.__xss") is None and p.errors == []


# ---- keyboard
def test_sport_chip_keeps_focus(ctx, url):
    p = open_page(ctx, url)
    enter(p, '[data-fk="chip:nba"]')
    assert active(p) == "BUTTON:chip:nba"


@pytest.mark.parametrize("size,sel", [(DESK, RECHECK), (DESK, '#view-slips [data-act="recheck"]')])
def test_recheck_keeps_focus(ctx, url, size, sel):
    p = open_page(ctx, url, size=size, path="#/slips" if "view-slips" in sel else "")
    enter(p, sel)
    p.wait_for_timeout(100)
    assert active(p) == "BUTTON:recheck:"
    assert p.evaluate("document.activeElement.closest('#view-slips,#rail-body').id") == ("view-slips" if "view-slips" in sel else "rail-body")
    p.wait_for_timeout(750)
    assert active(p) == "BUTTON:recheck:"


def test_recheck_in_the_sheet_keeps_focus_and_shows_the_toast(ctx, url):
    p = open_page(ctx, url, size=PHONE)
    p.click("#slip-fab")
    enter(p, '#sheet-body [data-act="recheck"]')
    p.wait_for_timeout(800)
    assert active(p) == "BUTTON:recheck:"
    toast = p.locator("#slip-dialog .toast")
    assert "Recheck done" in toast.inner_text() and toast.is_visible()


@pytest.mark.parametrize("sel,want", [
    ('[data-fk="view:p-nba"]', "BUTTON:view:p-nba"),
    ('#rail-body .leg-x', "BUTTON:recheck:"),
    ('#rail-body [data-act="slip-clear"]', "SPAN:slip-head")])
def test_slip_and_view_actions_keep_focus(ctx, url, sel, want):
    p = open_page(ctx, url)
    enter(p, sel)
    assert active(p) == want


def test_add_in_panel_keeps_focus(ctx, url):
    p = open_page(ctx, url, path="#/research/p/cs2.cs2-e1.vargo.kills.maps12.39.5")
    enter(p, '[data-act="p-add"]')
    assert active(p) == "BUTTON:p-add:" and p.get_attribute('[data-act="p-add"]', "aria-disabled") == "true"


def test_board_retry_and_reset_keep_focus(ctx, url):
    p = open_page(ctx, url)
    force(p, "unavailable")
    enter(p, '#board-list [data-act="retry"]')
    assert active(p) == "DIV:board-list"
    p.wait_for_timeout(800)
    assert active(p) == "DIV:board-list"
    p.click("#account summary")
    enter(p, "#reset-sample")
    assert p.evaluate("document.activeElement.tagName") == "SUMMARY"


def test_escape_returns_to_the_control_that_opened_the_panel(ctx, url):
    p = open_page(ctx, url)
    add = '.row[data-row="nba.nba-e2.moon.pts.full_game.18.5"] .add'
    p.focus(add)
    p.keyboard.press("Enter")
    p.wait_for_timeout(100)
    p.keyboard.press("Escape")
    p.wait_for_timeout(100)
    assert active(p) == "BUTTON:row-choose:nba.nba-e2.moon.pts.full_game.18.5"


def test_phone_panel_is_modal_and_keeps_tab_inside(ctx, url):
    p = open_page(ctx, url, path="#/research/p/" + VARGO, size=PHONE)
    assert p.get_attribute("#panel", "aria-modal") == "true"
    for _ in range(45):
        p.keyboard.press("Tab")
        assert p.evaluate("document.activeElement === document.body || !!document.activeElement.closest('#panel')")
    p.set_viewport_size(DESK)
    p.wait_for_timeout(200)
    assert p.get_attribute("#panel", "aria-modal") == "false"


def test_account_menu_leaves_on_escape_and_tab(ctx, url):
    p = open_page(ctx, url)
    p.click("#account summary")
    p.keyboard.press("Escape")
    assert p.evaluate("document.getElementById('account').open") is False and p.evaluate("document.activeElement.tagName") == "SUMMARY"
    p.keyboard.press("Enter")
    for _ in range(5):
        p.keyboard.press("Tab")
    assert p.evaluate("document.getElementById('account').open") is False


def test_skip_link_keeps_the_view(ctx, url):
    p = open_page(ctx, url, path="#/results")
    p.keyboard.press("Tab")
    p.keyboard.press("Enter")
    assert p.locator("#view-results").is_visible() and "#/results" in p.url and active(p) == "H1:results-h"


def test_focus_rings_show(ctx, url):
    p = open_page(ctx, url, path="#/research/p/" + VARGO)
    p.focus(".tab[aria-selected=true]")
    p.keyboard.press("Tab")
    assert p.evaluate("document.activeElement.id") == "p-body"
    assert p.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "solid"
    go(p, "#/research/p/" + VARGO + "/history")
    p.wait_for_timeout(100)
    p.focus('[data-fk="hist-n:5"]')
    p.keyboard.press("ArrowLeft")
    p.keyboard.press("Tab")
    p.keyboard.press("Shift+Tab")
    for sel in ('[data-fk="hist-n:5"]', '[data-fk="hist-n:10"]', ".tab[aria-selected=true]"):
        p.focus(sel)
        p.keyboard.press("Shift+Tab")
        p.keyboard.press("Tab")
        assert p.evaluate("(() => { const c = getComputedStyle(document.activeElement); return c.outlineStyle === 'solid' && parseFloat(c.outlineOffset) < 0; })()"), sel


# ---- naming
def test_control_names_carry_what_they_act_on(ctx, url):
    p = open_page(ctx, url)
    p.select_option("#f-day", "all")
    add = p.locator('.row[data-row="cs2.cs2-e3.sable.kills.maps12.36.5"] .add')
    assert add.get_attribute("aria-label") == "sable: Game started"
    assert "Scout" not in p.get_attribute('.row[data-row="nba.nba-e2.moon.pts.full_game.18.5"] .row-open', "aria-label")
    pressed = p.locator(f'.row[data-row="{VARGO}"] .add')
    assert pressed.get_attribute("aria-pressed") == "true" and pressed.get_attribute("aria-label").startswith("Add ")
    assert "feed is behind" in p.get_attribute('[data-fk="chip:lol"]', "aria-label")
    assert p.locator(".hist-n[aria-label], .nav-count[aria-label]").count() == 0
    assert "Owner" in p.get_attribute("#account summary", "aria-label")


# ---- layout and contrast
@pytest.mark.parametrize("width", [761, 768, 800, 900, 960, 961, 1024, 1180, 1200, 1280, 1366, 1440])
@pytest.mark.parametrize("group", ["sport", "market", "side", "version"])
def test_results_never_scrolls_sideways(ctx, url, width, group):
    p = open_page(ctx, url, path="#/results", size={"width": width, "height": 800})
    p.click(f'[data-act="group"][data-id="{group}"]')
    w = p.evaluate("(() => { const v = document.getElementById('view-results'); return [v.scrollWidth, v.clientWidth]; })()")
    assert w[0] <= w[1]


@pytest.mark.parametrize("width", [768, 900, 1180, 1280])
def test_prop_identity_is_not_clipped(ctx, url, width):
    p = open_page(ctx, url, size={"width": width, "height": 800})
    p.select_option("#f-day", "all")
    cut = p.evaluate("""() => [...document.querySelectorAll('.pl-sub, .c-line span, .pl-name')].filter(e => e.scrollWidth > e.clientWidth).map(e => e.textContent)""")
    assert cut == []


def test_phone_targets_are_44(ctx, url):
    p = open_page(ctx, url, size=PHONE, path="#/research/p/" + VARGO)
    small = p.evaluate("""() => [...document.querySelectorAll('#panel button')].filter(b => b.offsetParent && b.getBoundingClientRect().height < 44).map(b => b.className)""")
    assert small == []
    go(p, "#/research")
    p.wait_for_timeout(100)
    small = p.evaluate("""() => [...document.querySelectorAll('.add, .chip, .more-btn')].filter(b => b.offsetParent).filter(b => b.getBoundingClientRect().height < 44).map(b => b.className)""")
    assert small == []
    assert p.evaluate("""() => { const lim = document.getElementById('slip-fab').getBoundingClientRect().top; return [...document.querySelectorAll('.row')].filter(r => r.getBoundingClientRect().bottom <= lim).length; }""") >= 6
    go(p, "#/slips")
    p.wait_for_timeout(100)
    small = p.evaluate("""() => [...document.querySelectorAll('#view-slips button')].filter(b => b.offsetParent).filter(b => { const r = b.getBoundingClientRect(); return r.height < 44; }).map(b => b.className)""")
    assert small == []


def luminance(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2])


def test_off_and_stale_rows_keep_contrast(ctx, url):
    p = open_page(ctx, url)
    p.select_option("#f-day", "all")
    force(p, "stale")
    samples = p.evaluate("""() => {
      const out = [];
      document.querySelectorAll('.row.is-off, .row.is-stale').forEach((row) => {
        row.querySelectorAll('.bk, .s-tag, .pl-sub, .pl-name, .c-line b, .c-line span, .hist-n, .sc-sub, .verdict, .g-letter, .g-est, .pct').forEach((e) => {
          let o = 1, n = e; while (n && n !== row.parentElement) { o *= +getComputedStyle(n).opacity; n = n.parentElement; }
          out.push([e.className, o, getComputedStyle(e).color]);
        });
      });
      return out;
    }""")
    assert samples
    for name, opacity, colour in samples:
        rgb = [float(x) for x in colour[colour.index("(") + 1:-1].split(",")[:3]]
        assert (luminance([c * opacity for c in rgb]) + 0.05) / 0.05 >= 4.5, (name, colour, opacity)


def test_no_runtime_errors_across_views(ctx, url):
    p = open_page(ctx, url)
    for h in ("#/slips", "#/picks", "#/results", "#/research/p/" + VARGO, "#/research"):
        go(p, h)
    for state in ("loading", "stale", "unavailable", "withdrawn", "ok"):
        force(p, state)
    assert p.errors == []
