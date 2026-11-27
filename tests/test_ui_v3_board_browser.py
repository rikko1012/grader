"""v3 boards (page group A, web/v3/board.css + board.js) in a real browser against the real Handler with fixture data only.

SCOUT_UI_V3=1 plus the board flags the running server turns on by default (server.DEFAULT_ON: H2H / Season columns, Edge, hit
samples, Market edge), so the columns the owner approved are on screen. Every board (CS2 hub, LoL, Sports; NBA / NFL tabs; soccer and
tennis through the expansion fixture) at 390x844 and 1280, dark and light: the hub's own cells are all still there and visible in
each row (nothing the board shows today is dropped), no sideways scroll, no script error. Plus the pop-overs, By game, a picked
row, the empty board and the 1024-1100px band where the hub is in its card mode under the v3 desktop header.
V3_BOARD_SHOTS=<folder> keeps a screenshot of each."""
import os
import re
from pathlib import Path

import pytest

from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401
from tests.test_ui_v3_browser import PHONE, DESK

SHOTS = Path(os.environ.get("V3_BOARD_SHOTS") or "")
PROD_FLAGS = ("SCOUT_BOARD_H2H", "SCOUT_PARITY_EDGE", "SCOUT_PARITY_HIT_SAMPLES", "SCOUT_PARITY_HIT_SORT", "SCOUT_PARITY_EV_BAND", "SCOUT_PARITY_PLAYS",
              "SCOUT_PICKEM_EV", "SCOUT_MARKET_EDGE")
PAGES = {"cs2": "/#all-sports", "lol": "/lol#all-sports", "sports": "/sports#/board"}
ROW = "#hub-rows>tr[data-record]"
# every cell the hub's board row draws today (web/upgrade-hub.js row()), by class. A row with no past result in any window (h-nohits)
# has no hit line on the hub's own phone card today, so its hits cell is not required there.
CELLS = ("h-c-watch", "h-c-player", "h-c-market", "h-c-line", "h-c-proj", "h-c-scout", "h-c-hits", "h-c-compare", "h-c-add")


def open_board(browser, env, monkeypatch, path, size, theme, flags=True):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    for k in PROD_FLAGS if flags else ():
        monkeypatch.setenv(k, "1")
    c = context(browser, env, size)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.goto(env.url + path)
    p.wait_for_selector(ROW, timeout=30000)
    p.wait_for_function("!!window.ScoutV3 && !!document.querySelector('#hub-rows>tr[data-sc=\"1\"]')")
    # integration: the hub may repaint once more as later sport boards arrive (/sports, /#all-sports); wait until that repaint is
    # decorated too (legend in place, no undecorated row) so the checks below never read a half-painted board
    p.wait_for_function("!!document.querySelector('#sc-bd-legend') && !document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')", timeout=20000)
    p.wait_for_timeout(300)
    return c, p


def shot(p, name, full=False):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(350)
        p.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def visible_cells(p):
    """Per decorated row: each hub cell's on-screen box (display:contents cells report their children's)."""
    return p.evaluate("""(cells) => [...document.querySelectorAll('#hub-rows>tr[data-record]')].slice(0, 12).map(tr => {
      const box = el => { if (!el) return 0; if (getComputedStyle(el).display === 'contents') return [...el.children].some(c => box(c)) ? 1 : 0;
        const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? 1 : 0; };
      return Object.fromEntries(cells.map(k => [k, box(tr.querySelector('td.' + k))]).concat([['edge', tr.querySelector('td.h-c-edge') ? box(tr.querySelector('td.h-c-edge')) : -1],
        ['h-c-hits', tr.classList.contains('h-nohits') && innerWidth <= 1100 ? -1 : box(tr.querySelector('td.h-c-hits'))]]));
    })""", list(CELLS))


def check_rows(p, edge=True, allow=()):
    rows = visible_cells(p)
    assert rows
    for r in rows:
        missing = [k for k, v in r.items() if v == 0 and k not in allow]
        assert not missing, (missing, r)
        if edge:
            assert r["edge"] == 1
    # the hub's own values are on screen, in the approved wording
    seen = p.locator(f"{ROW} .sc-seen").all_inner_texts()
    assert seen and all(re.match(r"^(PrizePicks|Underdog|[A-Z][\w ]+)( · checked (just now|\d+ (min|h) ago))?", s) for s in seen), seen
    assert not any(re.search(r"(?<![\d.])0(\.0)?%", t) for t in p.locator(f"{ROW} .h-direction").all_inner_texts())
    words = " ".join(p.locator(f"{ROW} .h-scout").all_inner_texts())
    assert "Results" not in words


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
@pytest.mark.parametrize("page", list(PAGES))
def test_v3_board_keeps_every_cell_and_takes_the_approved_look(browser, env, monkeypatch, page, size, theme):
    c, p = open_board(browser, env, monkeypatch, PAGES[page], size, theme)
    try:
        assert p.evaluate("document.documentElement.dataset.uiV3") == "1"
        top = p.locator("#u-hub .h-top.sc-bd-band")
        assert top.count() == 1 and top.is_visible()
        assert p.locator("#hub-title").inner_text().strip().endswith("player props") or p.locator("#hub-title").inner_text().strip() == "Player props"
        # the count shows once, under the title (the toolbar copy is kept for the hub, not drawn)
        meta = p.locator("#u-hub .sc-bd-meta").inner_text()
        assert meta == p.locator("#hub-count").inner_text(), meta
        assert p.evaluate("document.getElementById('hub-count').getBoundingClientRect().width") <= 1
        check_rows(p)
        # one stake per line: every row with a letter or a Lean shows its stake beside it; no "No grade yet" chip is drawn
        bad = p.evaluate("""[...document.querySelectorAll('#hub-rows>tr[data-record]')].filter(tr => {
          const g = tr.querySelector('.h-scout-grade'); if (!g) return false;
          const shown = [...g.children].filter(e => e.getClientRects().length && getComputedStyle(e).display !== 'none');
          const letter = shown.some(e => e.matches('.h-grade, .sc-lean') || /^Lean (Over|Under)$/.test(e.textContent.trim()));
          return letter && !g.querySelector('.sc-stake');
        }).map(tr => tr.dataset.record)""")
        assert not bad, bad
        assert p.locator("#hub-rows .h-scout-grade :visible", has_text="No grade yet").count() == 0
        # value inks only where the legend says: 60%+ green, under 40% red
        legend = p.locator("#sc-bd-legend")
        assert legend.is_visible() and "60%+" in legend.inner_text() and "under 40%" in legend.inner_text()
        assert p.evaluate("getComputedStyle(document.querySelector('#hub-table .h-scout .h-direction>span')).fontWeight") == "600"
        if size is PHONE:
            # the 4-line card: name and chance on one line, line / market / Proj. on the next, meta on the last
            assert p.evaluate("getComputedStyle(document.querySelector('#hub-table>thead')).display") == "none"
            assert p.evaluate("getComputedStyle(document.querySelector('#hub-rows>tr[data-record]')).display") == "grid"
            first = p.locator(ROW).first
            y = lambda sel: first.locator(sel).first.bounding_box()["y"]
            assert abs(y(".h-player b") - y(".h-direction")) < 14
            assert y("td.h-c-line>b") < y(".sc-seen")
            assert p.locator("#hub-q").is_hidden()                                       # behind the header's search button
        else:
            assert p.evaluate("getComputedStyle(document.querySelector('#hub-table>thead')).display") != "none"
            wrap = p.evaluate("(()=>{const w=document.querySelector('#hub-board-section .h-table-wrap');return [w.scrollWidth,w.clientWidth]})()")
            assert wrap[0] <= wrap[1] + 1, wrap                                          # beside the slip column, no inner sideways scroll
        no_h_scroll(p)
        shot(p, f"{page}-{theme}-{size['width']}-top")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_board_sport_tabs_set_the_accent_and_band(browser, env, monkeypatch, size):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, "dark")
    try:
        for sport, label in (("cs2", "CS2"), ("lol", "LoL"), ("nba", "NBA"), ("nfl", "NFL")):
            tab = p.locator(f"#hub-sports [data-hub=sport][data-id={sport}]")
            if not tab.count():
                continue
            tab.click()
            p.wait_for_function(f"document.documentElement.dataset.scSport==='{sport}'")
            p.wait_for_function(f"document.getElementById('hub-title').textContent.trim()==='{label} player props'")
            p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
            if p.locator(ROW).count():
                check_rows(p)
            no_h_scroll(p)
            shot(p, f"tab-{sport}-dark-{size['width']}")
        assert p.evaluate("getComputedStyle(document.querySelector('#hub-sports [aria-pressed=true]')).boxShadow").startswith("rgb")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_board_controls_popovers_by_game_picked_and_empty(browser, env, monkeypatch, size, theme):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, theme)
    w = size["width"]
    try:
        # Filters pop-over (the hub's own controls, restyled)
        p.locator("#hub-filters-toggle").click()
        p.wait_for_selector("#hub-filter-pop:not([hidden])")
        for sel in ("#hub-stat", "#hub-side", "#hub-minGrade", "#hub-diff", "#hub-watch"):
            assert p.locator(sel).is_visible(), sel
        shot(p, f"filters-{theme}-{w}")
        p.locator("#hub-filter-pop .h-pop-foot [data-hub=filters]").click()
        p.wait_for_selector("#hub-filter-pop", state="hidden")
        # Views
        p.locator("#hub-views-toggle").click()
        p.wait_for_selector("#hub-views-pop:not([hidden])")
        assert p.locator("#hub-views-pop [data-hub=save-view]").is_visible()
        shot(p, f"views-{theme}-{w}")
        p.locator("#hub-views-toggle").click()
        # a picked row: raised surface + accent spine, the add button shows the tick
        add = p.locator(f"{ROW} .h-add:not(.h-add-info):not(.selected)").first
        add.click()
        p.wait_for_selector(f"{ROW} .h-add.is-on")
        picked = p.locator(f"{ROW}:has(.h-add.is-on)").first
        assert "inset" in p.evaluate("e=>getComputedStyle(e).boxShadow", picked.element_handle())
        p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
        check_rows(p)
        shot(p, f"picked-{theme}-{w}")
        # By game
        if size is PHONE:
            p.locator(".sc-v3-tabs [data-v3=more]").click()
            p.locator(".sc-v3-more .sc-more__a", has_text="By game").first.click()
        else:
            p.locator("#hub-board-section [data-hub=view][data-id=games]").click()
        p.wait_for_selector("#hub-games-body:not([hidden]) .h-game-card")
        cards = p.locator("#hub-games-body .h-game-card")
        assert cards.count() >= 1
        for sel in ("h2", "p", ".h-game-books span", "time", "button"):
            assert cards.first.locator(sel).first.is_visible(), sel
        no_h_scroll(p)
        shot(p, f"bygame-{theme}-{w}")
        p.locator("#hub-board-section [data-hub=view][data-id=list]").click()
        # the empty board: the hub's own reason + action
        if size is PHONE:
            p.locator(".sc-v3-bar [data-v3=search]").click()
            p.wait_for_function("document.activeElement && document.activeElement.id==='hub-q'")
        p.locator("#hub-q").fill("zzzz-no-such-player")
        p.wait_for_selector("#hub-empty:not([hidden]) [data-hub=clear-filters]")
        assert p.locator("#hub-q").is_visible()                                         # a typed search keeps the box open
        shot(p, f"empty-{theme}-{w}")
        p.locator("#hub-empty [data-hub=clear-filters]").click()
        p.wait_for_selector(ROW)
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_board_between_1024_and_1100_keeps_the_card_layout_under_the_desktop_header(browser, env, monkeypatch):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", {"width": 1060, "height": 900}, "dark")
    try:
        assert p.locator(".sc-v3-bar .sc-nav").is_visible()
        assert p.evaluate("getComputedStyle(document.querySelector('#hub-rows>tr[data-record]')).display") == "grid"
        check_rows(p)
        no_h_scroll(p)
        shot(p, "cs2-dark-1060")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_board_without_the_default_on_columns(browser, env, monkeypatch):
    """The board flags off (as in the other suites): no Edge / H2H / SZN columns, the card and the table still hold every cell."""
    for size in (PHONE, DESK):
        c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, "dark", flags=False)
        try:
            check_rows(p, edge=False)
            no_h_scroll(p)
            assert not p.errors, p.errors
        finally:
            c.close()


def test_v3_board_rows_keep_working_after_repaints(browser, env, monkeypatch):
    """The hub repaints #hub-rows on every change: each new row is decorated once (no doubled meta or struck line)."""
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", DESK, "dark")
    try:
        p.locator("#hub-sort").select_option("player")
        p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
        p.locator("#hub-sort").select_option("chance")
        p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
        assert p.evaluate("[...document.querySelectorAll('#hub-rows>tr[data-record]')].every(tr=>tr.querySelectorAll('.sc-seen').length===1&&tr.querySelectorAll('.sc-was').length<=1)")
        toggle = p.locator(f"{ROW} .h-prop-toggle").first
        if toggle.count():
            assert re.match(r"^\+\d+ more props?$", toggle.inner_text().strip()), toggle.inner_text()
            label = toggle.get_attribute("aria-label")
            assert label.startswith("Show ")                                             # the hub's own label, unchanged
            toggle.click()
            p.wait_for_selector(f"{ROW}.is-sub")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_board_soccer_and_tennis(browser, env, monkeypatch, size, theme):
    import workspace_expansion as adapter
    import workspace_sports
    from tests.test_expansion_main_board import source
    monkeypatch.setenv("SCOUT_SOCCER_ENABLED", "1")
    monkeypatch.setenv("SCOUT_TENNIS_ENABLED", "1")
    original = workspace_sports.call
    docs = {s: source(s) for s in adapter.SPORTS}
    monkeypatch.setattr(workspace_sports, "call", lambda srv, path, query=None, **kw: (docs[query["sport"]], None)
                        if path == "/api/sports/expansion/board" else original(srv, path, query, **kw))
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, theme)
    try:
        for sport, label in (("soccer", "Soccer"), ("tennis", "Tennis")):
            tab = p.locator(f"#hub-sports [data-hub=sport][data-id={sport}]")
            assert tab.count() == 1, sport
            tab.click()
            p.wait_for_function(f"document.documentElement.dataset.scSport==='{sport}'")
            p.wait_for_selector(f"{ROW}[data-sc='1']")
            p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
            rows = visible_cells(p)
            assert rows and all(all(v != 0 for k, v in r.items()) for r in rows), rows
            # ST-4 (sweep 2026-11-23): no coverage status note on the member board; the counts stay in the board data
            assert p.locator("#hub-health .h-history-note").count() == 0
            assert "await supporting game logs" not in p.locator("#hub-health").inner_text()
            no_h_scroll(p)
            shot(p, f"{sport}-{theme}-{size['width']}")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_flag_off_board_carries_none_of_it(browser, env, monkeypatch):
    monkeypatch.setenv("SCOUT_UI_V3", "0")
    c = context(browser, env, PHONE)
    p = c.new_page()
    try:
        p.goto(env.url + "/#all-sports")
        p.wait_for_selector(ROW, timeout=30000)
        p.wait_for_timeout(300)
        assert p.evaluate("document.documentElement.dataset.uiV3") is None
        assert p.evaluate("!window.ScoutV3 && !document.querySelector('.sc-seen,.sc-bd-band,#sc-bd-legend,[data-sc]')")
        assert p.evaluate("[...document.querySelectorAll('link[rel=stylesheet],script[src]')].every(e=>!(e.href||e.src).includes('/v3/'))")
    finally:
        c.close()


@pytest.mark.parametrize("width", [1110, 1180, 1240, 1440])
def test_v3_board_narrow_and_wide_desktop_keep_the_player_column(browser, env, monkeypatch, width):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", {"width": width, "height": 900}, "dark")
    try:
        # 1101-1180px: Compare and the watch star step aside, as the hub's own table does there today (both stay on the card)
        check_rows(p, edge=True, allow=("h-c-watch", "h-c-compare") if width <= 1180 else ())
        wrap = p.evaluate("(()=>{const w=document.querySelector('#hub-board-section .h-table-wrap'),t=document.getElementById('hub-table');return [t.getBoundingClientRect().width,w.clientWidth]})()")
        assert wrap[0] <= wrap[1] + 1, wrap
        name = p.evaluate("document.querySelector('#hub-rows>tr[data-record] td.h-c-player').getBoundingClientRect().width")
        assert name >= 110, name
        if width >= 1181:
            assert p.locator(f"{ROW} td.h-c-compare").first.is_visible()
        no_h_scroll(p)
        shot(p, f"cs2-dark-{width}")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_board_scrolled_header_and_phone_end_of_list(browser, env, monkeypatch):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", DESK, "dark")
    try:
        p.evaluate("document.getElementById('hub-center').scrollTop=600")
        p.wait_for_timeout(300)
        bar = p.evaluate("document.querySelector('#hub-board-section .h-toolbar').getBoundingClientRect().bottom")
        th = p.evaluate("document.querySelector('#hub-table>thead th').getBoundingClientRect().top")
        assert th >= bar - 1, (th, bar)
        shot(p, "cs2-dark-1280-rows")
    finally:
        c.close()
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", PHONE, "dark")
    try:
        p.evaluate("document.getElementById('hub-center').scrollTop=1e6")
        p.wait_for_timeout(300)
        last = p.evaluate("Math.max(...[...document.querySelectorAll('#hub-rows>tr[data-record], #hub-pagination')].map(e=>e.getBoundingClientRect().bottom))")
        tabs = p.evaluate("document.querySelector('.sc-v3-tabs').getBoundingClientRect().top")
        assert last <= tabs + 1, (last, tabs)
        shot(p, "cs2-dark-390-end")
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_board_row_opens_the_player_card(browser, env, monkeypatch, size):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, "dark")
    try:
        p.locator(f"{ROW} .h-player").first.click()
        p.wait_for_selector("#hub-card:not([hidden]) #hub-detail *")
        assert p.locator("#hub-card").is_visible()
        no_h_scroll(p)
        shot(p, f"card-dark-{size['width']}")
        if size is DESK:
            check_rows(p)
        assert not p.errors, p.errors
    finally:
        c.close()


# ---------------------------------------------------------------- qa-boards (2026-11-20): regressions from the live-data pass
TABLET = {"width": 768, "height": 1024}
LONG_EVENT = "Extraordinarily Long Team Name Esports Club vs Another Remarkably Long Organisation"


def open_patched(browser, env, monkeypatch, size, theme="dark", patch=None):
    """The board with the hub's own CS2 board answer patched in flight (route.fetch + fulfill): a long matchup and a soccer-style
    'Lean' grade on fixture rows, as live data has them."""
    import json as _json
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    for k in PROD_FLAGS:
        monkeypatch.setenv(k, "1")
    c = context(browser, env, size)

    def handle(route):
        resp = route.fetch()
        try:
            d = resp.json()
            if patch:
                patch(d)
            route.fulfill(response=resp, body=_json.dumps(d), headers={**resp.headers, "content-type": "application/json"})
        except Exception:
            route.fulfill(response=resp)
    c.route(re.compile(r".*/api/upgrade/board$"), handle)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.goto(env.url + "/#all-sports")
    p.wait_for_selector(ROW, timeout=30000)
    p.wait_for_function("!!window.ScoutV3 && !!document.querySelector('#sc-bd-legend') && !document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')", timeout=20000)
    p.wait_for_timeout(300)
    return c, p


@pytest.mark.parametrize("size", [PHONE, TABLET], ids=["390", "768"])
def test_qa_card_rows_give_the_market_the_room_and_hits_stay_whole(browser, env, monkeypatch, size):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, "dark")
    try:
        # A long market gets its own full-width row, never a tiny track beside the line.
        geometry = p.evaluate("""() => {
          const tr = document.querySelector('#hub-rows>tr[data-record]');
          const m = tr.querySelector('td.h-c-market .h-mcell>div');
          m.querySelector('b').textContent = 'Outfield Fantasy Score';
          const r = m.getBoundingClientRect(), row = tr.getBoundingClientRect();
          const grade = tr.querySelector('.h-scout-grade').getBoundingClientRect();
          return {width:r.width, row:row.width, height:r.height, bottom:r.bottom, gradeTop:grade.top};
        }""")
        assert geometry['width'] >= geometry['row'] * .85, geometry
        assert geometry['height'] < 60, geometry
        assert geometry['bottom'] <= geometry['gradeTop'] + 1, geometry
        # a few-games hit ("16/18") is one token on the strip's baseline, its dashed mark no taller than the text line
        d = p.evaluate("""() => { const h = document.querySelector('#hub-rows>tr[data-record] .h-strip .h-hit'); h.classList.add('h-few');
          h.innerHTML = '<b>16</b><em>/18</em>'; const r = h.getBoundingClientRect(), s = getComputedStyle(h);
          return [h.getClientRects().length, r.height, s.whiteSpace, s.display]; }""")
        assert d[0] == 1 and d[1] <= 20 and d[2] == "nowrap" and d[3] == "flex", d
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def test_qa_desktop_line_cell_keeps_checked_min_ago_whole_and_by_game_drops_the_legend(browser, env, monkeypatch):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", DESK, "light")
    try:
        assert p.evaluate("document.getElementById('u-hub').classList.contains('h-focus-board')")          # the rail starts closed
        assert p.evaluate("document.querySelector('#hub-table th.h-c-line').getBoundingClientRect().width") >= 127
        cut = p.evaluate("[...document.querySelectorAll('#hub-rows .sc-seen>*')].filter(e=>e.scrollWidth>e.clientWidth+1).map(e=>e.textContent)")
        assert not cut, cut
        # the Compare button's per-side payouts wrap, never cut ("O 0.82x · U 1…" on live tennis)
        cut = p.evaluate("""(() => { const s = document.querySelector('#hub-rows td.h-c-compare .h-compare small'); if (!s) return null;
          s.textContent = 'O 0.82x · U 1.15x'; return s.scrollWidth > s.clientWidth + 1 ? s.textContent : ''; })()""")
        assert not cut, cut
        assert p.evaluate("[...document.querySelectorAll('#hub-rows .h-hit')].every(h=>h.getClientRects().length<=1 && getComputedStyle(h).whiteSpace==='nowrap')")
        assert p.locator("#sc-bd-legend").is_visible()
        p.locator("#hub-board-section [data-hub=view][data-id=games]").click()
        p.wait_for_selector("#hub-games-body:not([hidden]) .h-game-card")
        assert p.locator("#sc-bd-legend").is_hidden()                                 # it explains rows, not game cards
        p.locator("#hub-board-section [data-hub=view][data-id=list]").click()
        p.wait_for_selector(ROW)
        assert p.locator("#sc-bd-legend").is_visible()
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("width", [1110, 1280])
def test_qa_desktop_with_the_slip_rail_open_keeps_the_tighter_columns(browser, env, monkeypatch, width):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", {"width": width, "height": 900}, "dark")
    try:
        p.locator(f"{ROW} .h-add:not(.h-add-info)").first.click()
        p.wait_for_selector(f"{ROW} .h-add.is-on")
        if p.evaluate("document.getElementById('u-hub').classList.contains('h-focus-board')"):
            p.locator("#hub-focus").click()
        p.wait_for_function("!document.getElementById('u-hub').classList.contains('h-focus-board')")
        p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
        assert p.evaluate("document.querySelector('#hub-table th.h-c-line').getBoundingClientRect().width") <= 117
        wrap = p.evaluate("(()=>{const w=document.querySelector('#hub-board-section .h-table-wrap'),t=document.getElementById('hub-table');return [t.getBoundingClientRect().width,w.clientWidth]})()")
        assert wrap[0] <= wrap[1] + 1, wrap
        assert p.evaluate("document.querySelector('#hub-rows>tr[data-record] td.h-c-player').getBoundingClientRect().width") >= 110
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_qa_browser_back_closes_the_player_card(browser, env, monkeypatch, size):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, "dark")
    try:
        u0 = p.url
        p.locator(f"{ROW} .h-player").first.click()
        p.wait_for_selector("#hub-card:not([hidden])")
        p.go_back()
        p.wait_for_selector("#hub-card", state="hidden")
        assert p.url == u0 and p.locator("#hub-board-section").is_visible()             # Back closed the card, the board stayed
        # closed with the card's own Back: no dead history step is left behind
        p.locator(f"{ROW} .h-player").first.click()
        p.wait_for_selector("#hub-card:not([hidden])")
        p.locator("#hub-card [data-hub=card-close]").first.click()
        p.wait_for_selector("#hub-card", state="hidden")
        p.wait_for_timeout(300)
        assert p.evaluate("!(history.state && (history.state.scV3Card || history.state.hubCard))")
        if size is DESK:
            # a marker left behind (card up, then another page, then Back onto it; or a reload with the card up) is adopted: Back
            # still closes the next card
            p.locator(f"{ROW} .h-player").first.click()
            p.wait_for_selector("#hub-card:not([hidden])")
            p.locator(".sc-v3-bar .sc-nav [data-v3-nav=picks]").click()
            p.wait_for_selector("#hub-picks-section:not([hidden])")
            p.go_back()
            p.wait_for_selector("#hub-board-section:not([hidden])")
            p.wait_for_selector(ROW)
            for step in ("after another page", "after a reload"):
                if step == "after a reload":
                    p.locator(f"{ROW} .h-player").first.click()
                    p.wait_for_selector("#hub-card:not([hidden])")
                    p.reload()
                    p.wait_for_selector(ROW, timeout=30000)
                    p.wait_for_function("!!window.ScoutV3")
                    if p.locator("#hub-card:not([hidden])").count():
                        p.locator("#hub-card [data-hub=card-close]").first.click()
                        p.wait_for_selector("#hub-card", state="hidden")
                p.locator(f"{ROW} .h-player").first.click()
                p.wait_for_selector("#hub-card:not([hidden])")
                p.go_back()
                p.wait_for_selector("#hub-card", state="hidden", timeout=5000)
                assert p.locator("#hub-board-section").is_visible(), step
        assert not p.errors, p.errors
    finally:
        c.close()


def _long_and_lean(d):
    recs = d.get("records") or []
    ev = recs[0]["event"]["id"]
    for r in recs:
        if r["event"]["id"] == ev:
            r["event"]["label"] = LONG_EVENT
    for r in recs:
        s = r.get("scout") or {}
        if s.get("side") in ("over", "under") and s.get("grade") and not s.get("hold"):
            s["grade"], s["units"] = "Lean", None
            break


@pytest.mark.parametrize("size", [PHONE, TABLET, DESK], ids=["390", "768", "1280"])
def test_qa_long_matchup_header_and_lean_word(browser, env, monkeypatch, size):
    c, p = open_patched(browser, env, monkeypatch, size, "light", _long_and_lean)
    try:
        # "Lean" alone is never shown: the side is spelled out, and no stake is invented for a line the model gives none
        lean = p.evaluate("""[...document.querySelectorAll('#hub-rows .h-scout-grade .h-grade')].filter(e=>/lean/i.test(e.textContent)).map(e=>[e.textContent.trim(), !!e.closest('.h-scout-grade').querySelector('.sc-stake')])""")
        assert lean and all(re.match(r"^Lean (Over|Under)$", t) and not st for t, st in lean), lean
        if p.locator("#hub-sort option[value=start]").count():
            p.locator("#hub-sort").select_option("start")
            p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
        head = p.locator("#hub-table .h-game-head", has_text="Extraordinarily").first
        assert head.count()
        d = head.evaluate("""h => { const e = h.querySelector('em').getBoundingClientRect(), x = h.querySelector('b');
          const t = document.createRange(); t.selectNodeContents(x);
          const hits = [...t.getClientRects()].some(r => !(r.right <= e.left || e.right <= r.left || r.bottom <= e.top || e.bottom <= r.top));
          return [hits, x.scrollHeight - x.clientHeight]; }""")
        assert not d[0] and d[1] <= 1, d                                              # never under "This game", never cut
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def test_qa_phone_sort_row_keeps_list_by_game_beside_sort_with_a_filter_on(browser, env, monkeypatch):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", PHONE, "dark")
    try:
        p.locator(".sc-v3-bar [data-v3=search]").click()
        p.locator("#hub-q").fill(p.locator(f"{ROW} .h-player b").first.inner_text()[:4])
        p.wait_for_selector("#hub-clear:not([hidden])")
        p.wait_for_timeout(300)
        tops = p.evaluate("['.h-sortbar', '.h-viewmode'].map(s=>Math.round(document.querySelector('#hub-board-section '+s).getBoundingClientRect().top))")
        assert abs(tops[0] - tops[1]) <= 2, tops
        assert p.evaluate("document.getElementById('hub-clear').getBoundingClientRect().top") > tops[0]
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()
