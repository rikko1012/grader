"""v3 views (web/v3/views.js): Moneylines, Line moves (bumps) and Lines differ (discrepancies) in a real browser against the real
Handler, SCOUT_UI_V3=1, at 390x844 and 1280x900, dark and light.

Moneylines are fed the real GET answers captured on 2026-11-20 (tests/data/v3_moneylines: /api/moneylines, /api/moneyline-games),
every timestamp moved by the same amount so the captured ages hold now. The view lists the same matches with the same price strings
as the classic Moneylines tab on the same payload. Line moves / Lines differ are the board with the hub's own quick filter on: every
row listed shows the line it moved from, or each book's line. No sideways scroll, no script error.
V3_SHOTS=<folder> keeps a screenshot each."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401 (fixtures)
from tests.test_ui_v3_browser import v3_page, GROUPS, PHONE, DESK

SHOTS = Path(os.environ.get("V3_SHOTS") or "")
DATA = Path(__file__).parent / "data" / "v3_moneylines"
SIZES = [PHONE, DESK]
THEMES = ["dark", "light"]
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


def shot(p, name, full=False):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(350)
        p.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def _shift(x, d):
    if isinstance(x, dict):
        return {k: _shift(v, d) for k, v in x.items()}
    if isinstance(x, list):
        return [_shift(v, d) for v in x]
    if isinstance(x, str) and ISO.match(x):
        t = datetime.fromisoformat(x.replace("Z", "+00:00"))
        return (t + d).isoformat().replace("+00:00", "Z")
    return x


def payloads():
    """The captured answers, moved to now (the CS2 build time = now - 5 s): every age and start stays what it was at capture."""
    ml = json.loads((DATA / "moneylines.json").read_text(encoding="utf-8"))
    at = datetime.fromisoformat(ml["at"])
    d = datetime.now(timezone.utc) - at
    from datetime import timedelta
    d -= timedelta(seconds=5)
    out = {"cs2": _shift(ml, d)}
    for k in ("nba", "nfl", "lol"):
        out[k] = _shift(json.loads((DATA / f"ml_{k}.json").read_text(encoding="utf-8")), d)
    return out


def feed(c, data=None, games_status=None, cs2_status=200):
    data = data or payloads()

    def games(route):
        league = parse_qs(urlsplit(route.request.url).query)["league"][0]
        st = (games_status or {}).get(league)
        if st:
            return route.fulfill(status=st, json={"error": "off"})
        doc = data.get(league)
        if doc is None:
            return route.fulfill(status=404, json={"error": "off"})
        route.fulfill(json=doc)
    c.route("**/api/moneyline-games?*", games)
    served = datetime.now(timezone.utc).isoformat()
    if cs2_status == 200:
        c.route("**/api/moneylines", lambda r: r.fulfill(json=data["cs2"], headers={"X-Served-At": served}))
    else:
        c.route("**/api/moneylines", lambda r: r.fulfill(status=cs2_status, json={"error": "moneylines_unavailable", "label": "Market probability unavailable"}))
    return data


def open_ml(browser, env, monkeypatch, size, theme, **kw):
    """v3_page's set-up (SCOUT_UI_V3=1, member cookie, stored theme) with the captured answers routed before the page loads."""
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    c = context(browser, env, size)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    data = feed(c, **kw)
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.goto(env.url + "/#/tools?v3=moneylines")
    p.wait_for_function("!!window.ScoutV3")
    p.wait_for_selector("#v3-ml-section:not([hidden])", timeout=30000)
    return c, p, data


def usable_cs2(data):
    """The matches the classic tab lists (ML_RENDER rules) - only to size expectations; the parity test compares the pages."""
    return [m for m in data["cs2"]["matches"] if not m.get("started")]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_moneylines_v3_view_reads_the_real_answers(browser, env, monkeypatch, size, theme):
    c, p, data = open_ml(browser, env, monkeypatch, size, theme)
    try:
        p.wait_for_selector("#v3-ml-section .v3x-ml[data-ml-game*='cs2']", timeout=20000)
        p.wait_for_function("document.querySelectorAll('#v3-ml-section .v3x-division').length >= 4")
        assert p.evaluate("document.documentElement.dataset.uiV3") == "1"
        assert p.locator("#hub-tools-section").is_hidden() and p.locator("#v3-ml-title").inner_text() == "Moneylines"
        count = p.locator(".v3x-ml-count").inner_text()
        assert re.search(r"CS2 · \d+ match(es)? with prices", count) and "Thunderpick odds checked" in count, count
        heads = p.locator(".v3x-division h2").all_inner_texts()
        assert [h.split("\n")[0].split("Full")[0].split("Series")[0].strip() for h in heads] == ["NBA", "NFL", "LoL", "CS2"], heads
        # soccer / tennis answer 404 here: their pills are hidden, as the classic tab hides them
        assert p.locator("[data-v3x-sport=soccer]").is_hidden() and p.locator("[data-v3x-sport=tennis]").is_hidden()
        assert p.locator("[data-v3x-sport=nba]").is_visible() and p.locator("[data-v3x-sport=cs2]").is_visible()
        # NBA: DraftKings American odds as listed, the Scout pick and its factors
        nba = p.locator(".v3x-division[aria-label=NBA] .v3x-ml").first
        assert nba.locator("tbody th").first.inner_text().startswith("DraftKings")
        tds = nba.locator("tbody td").all_inner_texts()
        assert all(re.fullmatch(r"[+-]\d+|—", t) for t in tds), tds
        assert "Scout pick" in nba.locator(".v3x-ml-pick").inner_text()
        # CS2: Thunderpick first, price strings to 0.1%, the context and quote details one tap away
        shot(p, f"moneylines-{size['width']}-{theme}")
        cs = p.locator(".v3x-division[aria-label=CS2] .v3x-ml").first
        assert all(re.fullmatch(r"\d+\.\d%|—", t) for t in cs.locator("tbody td").all_inner_texts())
        cs.locator("details[data-k=ctx] > summary").click()
        ctx = cs.locator(".v3x-ctx-grid").inner_text()
        assert "Market-implied probability" in ctx or "midpoint" in ctx, ctx
        assert "Which price is shown" in p.locator(".v3x-which").inner_text()
        # every price cell reads as a price or a dash, never 0 / NaN / undefined
        bad = p.evaluate("[...document.querySelectorAll('#v3-ml-section td')].map(t=>t.textContent).filter(t=>/NaN|undefined|null|Infinity/.test(t))")
        assert not bad, bad
        no_h_scroll(p)
        shot(p, f"moneylines-{size['width']}-{theme}-ctx")
        if size is DESK:
            assert p.locator(".sc-v3-bar .sc-nav [aria-current=page]").inner_text() == "Tools"
        assert not p.errors, p.errors
    finally:
        c.close()


def test_moneylines_lists_what_the_classic_tab_lists(browser, env, monkeypatch):
    """Same payload, same clock: the v3 view and the classic tab show the same CS2 matches with the same price strings, and the same
    NFL games with older odds included."""
    c, p, data = open_ml(browser, env, monkeypatch, DESK, "dark")
    try:
        p.locator("[data-v3x-sport=cs2]").click()
        p.wait_for_selector("#v3-ml-section .v3x-ml")
        v3 = p.evaluate("""[...document.querySelectorAll('#v3-ml-section .v3x-ml')].map(a=>[a.querySelector('h3').textContent,
            [...a.querySelectorAll('tbody tr')].map(r=>[r.querySelector('th').firstChild.textContent,...[...r.querySelectorAll('td')].map(t=>t.textContent)])])""")
        p.locator("[data-v3x-sport=nfl]").click()
        p.locator(".v3x-older input").check()
        v3_nfl = p.evaluate("[...document.querySelectorAll('#v3-ml-section .v3x-ml h3')].map(h=>h.textContent)")
        q = c.new_page()
        q.errors = []
        q.on("pageerror", lambda e: q.errors.append(str(e)))
        q.goto(env.url + "/?view=classic#moneylines")
        q.locator("[data-mlsport=cs2]").click()
        q.wait_for_selector(".mlrow")
        classic = q.evaluate("""[...document.querySelectorAll('.mlrow')].map(a=>[a.querySelector('h4').textContent,
            [...a.querySelectorAll('.ml-compare tbody tr')].map(r=>[r.querySelector('th').firstChild.textContent,...[...r.querySelectorAll('td')].map(t=>t.textContent)])])""")
        q.locator("[data-mlsport=nfl]").click()
        q.locator("#mlolder").check()
        classic_nfl = q.evaluate("[...document.querySelectorAll('.ml-game h4')].map(h=>h.textContent)")
        assert v3 and v3 == classic, (v3[:3], classic[:3])
        assert v3_nfl and v3_nfl == classic_nfl
        assert len(v3) <= len(usable_cs2(data))
        assert not p.errors and not q.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_moneylines_open_from_more_close_on_nav_and_keep_open_details(browser, env, monkeypatch, size):
    c, p = v3_page(browser, env, monkeypatch, "board", size, "dark")
    try:
        feed(c)
        p.locator(".sc-v3-tabs [data-v3=more]" if size is PHONE else ".sc-v3-bar [data-v3=more]").click()
        row = p.locator(".sc-v3-more .sc-more__a", has_text="Moneylines")
        assert row.count() == 1
        assert not row.locator("svg").count() or "↗" not in row.inner_text()
        row.click()
        p.wait_for_selector("#v3-ml-section:not([hidden]) .v3x-ml")
        assert "v3=moneylines" in p.evaluate("location.hash")
        assert p.evaluate("location.pathname") == "/" and not p.evaluate("location.search")
        card = p.locator("#v3-ml-section .v3x-ml").first
        card.locator("details > summary").last.click()
        key = card.get_attribute("data-ml-game")
        p.locator("[data-v3x='ml-refresh']").click()
        p.wait_for_function("!document.querySelector('[data-v3x=ml-refresh][aria-busy]')")
        again = p.locator(f"#v3-ml-section .v3x-ml[data-ml-game='{key}']")
        assert again.locator("details").last.get_attribute("open") is not None
        # search narrows every section and says so
        p.locator("#v3-ml-section input[type=search]").fill("zzzz-no-team")
        p.wait_for_function("document.querySelectorAll('#v3-ml-section .v3x-ml').length === 0")
        assert "No match for" in p.locator(".v3x-division[aria-label=CS2]").inner_text()
        p.locator("#v3-ml-section input[type=search]").fill("")
        # the board tab / nav closes the view and shows the board
        p.locator(".sc-v3-tabs [data-v3-nav=board]" if size is PHONE else ".sc-v3-bar .sc-nav [data-v3-nav=board]").click()
        p.wait_for_selector("#v3-ml-section", state="hidden")
        assert p.locator("#hub-board-section").is_visible()
        # Back returns to Moneylines
        p.go_back()
        p.wait_for_selector("#v3-ml-section:not([hidden])")
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def test_moneylines_errors_and_hidden_leagues(browser, env, monkeypatch):
    c, p, _ = open_ml(browser, env, monkeypatch, PHONE, "light", cs2_status=503, games_status={"nfl": 500})
    try:
        p.wait_for_function("document.querySelector('.v3x-division[aria-label=CS2]')?.textContent.includes('could not be refreshed')")
        assert "Prices could not be refreshed" in p.locator(".v3x-division[aria-label=NFL]").inner_text()
        assert p.locator(".v3x-division[aria-label=NBA] .v3x-ml").count() >= 1
        assert p.locator(".v3x-ml-count").is_hidden()
        no_h_scroll(p)
        shot(p, "moneylines-390-light-errors")
        assert not p.errors, p.errors
    finally:
        c.close()


# ------------------------------------------------------------------ Line moves / Lines differ
def board_rows(env, moves=True):
    """A bump on two rows (opening line differs from the line now); orlo is listed 33.5 on Underdog and 34.5 on PrizePicks."""
    for r in env.rows:
        if moves and r["id"] == "pp:7001":
            r["open_line"] = 37.5
        if moves and r["id"] == "11111111-0000-4000-8000-000000000004":
            r["open_line"] = 37.5


def rows_text(p):
    return p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record]')].filter(t=>t.offsetParent).map(t=>[t.dataset.record,t.innerText])")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_line_moves_view_is_the_board_with_the_hubs_filter(browser, env, monkeypatch, size, theme):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    board_rows(env)
    GROUPS["v3-moves"] = ("/#/board?sport=all&v3=moves", "#hub-rows tr[data-record]", True)
    c, p = v3_page(browser, env, monkeypatch, "v3-moves", size, theme)
    try:
        p.wait_for_function("document.getElementById('hub-movedOnly')?.getAttribute('aria-pressed') === 'true'")
        p.wait_for_selector("#v3x-view-note:not([hidden])")
        assert "moved from where they opened" in p.locator("#v3x-view-note").inner_text()
        assert "v3=moves" not in p.evaluate("location.hash")
        chip = p.locator("#hub-chips [data-id=movedOnly]")
        # the hub may repaint its chips between two reads; views.js relabels them on the next frame
        p.wait_for_function("(c => c && c.getAttribute('aria-label') === 'Remove Line moved filter')"
                            "(document.querySelector('#hub-chips [data-id=movedOnly]'))")
        assert chip.inner_text().replace("×", "").strip() == "Line moved"
        assert chip.get_attribute("aria-label") == "Remove Line moved filter"
        rows = rows_text(p)
        assert rows and all("37.5" in t for _, t in rows), rows                    # each row shows the line it moved from
        assert {r.split("|")[2] for r, _ in rows} <= {"pp:7001", "11111111-0000-4000-8000-000000000004"}
        no_h_scroll(p)
        shot(p, f"line-moves-{size['width']}-{theme}")
        chip.click()                                                                 # the hub's own chip turns the view off
        p.wait_for_selector("#v3x-view-note", state="hidden")
        assert p.evaluate("document.getElementById('hub-movedOnly').getAttribute('aria-pressed')") == "false"
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_lines_differ_view_states_each_books_line(browser, env, monkeypatch, size, theme):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    GROUPS["v3-differ"] = ("/#/board?sport=all&v3=differ", "#hub-rows tr[data-record]", True)
    c, p = v3_page(browser, env, monkeypatch, "v3-differ", size, theme)
    try:
        p.wait_for_function("document.getElementById('hub-diff')?.getAttribute('aria-pressed') === 'true'")
        p.wait_for_selector("#hub-rows .v3x-lines")
        assert "different line for the same player" in p.locator("#v3x-view-note").inner_text()
        assert p.locator("#hub-chips [data-id=onlyDiff]").inner_text().replace("×", "").strip() == "Lines differ"
        rows = rows_text(p)
        assert rows
        for rid, t in rows:
            assert "Lines now:" in p.locator(f"#hub-rows tr[data-record='{rid}'] .v3x-lines").inner_text(), rid
        orlo = [t for _, t in rows if "orlo" in t]
        assert orlo and "33.5" in orlo[0] and "34.5" in orlo[0], orlo
        assert not [t for _, t in rows if "brandr" in t]                              # the same line on both books: not listed
        no_h_scroll(p)
        shot(p, f"lines-differ-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_board_views_from_views_menu_and_more(browser, env, monkeypatch, size):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    board_rows(env)
    c, p = v3_page(browser, env, monkeypatch, "board", size, "dark")
    try:
        p.locator("#hub-views-toggle").click()
        grp = p.locator("#v3x-views-group")
        assert grp.is_visible() and grp.locator("[data-v3x-view]").all_inner_texts() == ["Line moves", "Lines differ"]
        grp.locator("[data-v3x-view=differ]").click()
        p.wait_for_function("document.getElementById('hub-diff').getAttribute('aria-pressed') === 'true'")
        assert grp.locator("[data-v3x-view=differ]").get_attribute("aria-pressed") == "true"
        shot(p, f"views-menu-{size['width']}")
        grp.locator("[data-v3x-view=differ]").click()
        p.wait_for_function("document.getElementById('hub-diff').getAttribute('aria-pressed') === 'false'")
        p.keyboard.press("Escape")
        # More: Line moves opens the board with the filter on, from another section too
        p.locator(".sc-v3-tabs [data-v3-nav=picks]" if size is PHONE else ".sc-v3-bar .sc-nav [data-v3-nav=picks]").click()
        p.wait_for_selector("#hub-picks-section:not([hidden])")
        p.locator(".sc-v3-tabs [data-v3=more]" if size is PHONE else ".sc-v3-bar [data-v3=more]").click()
        labels = p.locator(".sc-v3-more .sc-more__a").all_inner_texts()
        assert "Line moves" in labels and "Lines differ" in labels and "Moneylines" in labels, labels
        p.locator(".sc-v3-more .sc-more__a", has_text="Line moves").click()
        p.wait_for_selector("#hub-board-section:not([hidden])")
        p.wait_for_function("document.getElementById('hub-movedOnly').getAttribute('aria-pressed') === 'true'")
        assert p.locator("#v3x-view-note").is_visible()
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def test_without_workspace_v2_line_moves_has_no_entry(browser, env, monkeypatch):
    """The hub has no Line moved control without workspace_v2: the v3 entries for it are not offered (Lines differ still is)."""
    c, p = v3_page(browser, env, monkeypatch, "board", DESK, "dark")
    try:
        assert not p.locator("#hub-movedOnly").count()
        p.locator(".sc-v3-bar [data-v3=more]").click()
        labels = p.locator(".sc-v3-more .sc-more__a").all_inner_texts()
        assert "Line moves" not in labels and "Lines differ" in labels, labels
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("group,path", [("lol", "/lol"), ("sports", "/sports")])
def test_moneylines_open_in_place_on_lol_and_sports(browser, env, monkeypatch, group, path):
    c, p = v3_page(browser, env, monkeypatch, group, PHONE, "dark")
    try:
        feed(c)
        p.wait_for_selector("#hub-more [data-v3x-ml]", state="attached")
        p.locator(".sc-v3-tabs [data-v3=more]").click()
        p.locator(".sc-v3-more .sc-more__a", has_text="Moneylines").click()
        p.wait_for_selector("#v3-ml-section:not([hidden]) .v3x-ml")
        assert p.evaluate("location.pathname") == path and "v3=moneylines" in p.evaluate("location.hash")
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()
