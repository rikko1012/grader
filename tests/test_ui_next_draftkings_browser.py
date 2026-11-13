"""DraftKings (PR #66) on the redesign looks like the other books (owner 2026-11-12: "fix the draftkings book so it looks like the
other ones"): the same pill, row, ladder rung, card and meta components as PrizePicks / Underdog / FanDuel. Display only: DraftKings
data, joins and odds handling are PR #66's and unchanged (no slip add, no consensus, reported odds without a guessed side)."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import workspace_api
from tests.test_upgrade_browser import in_sheet, close_sheet, browser, env, context, no_h_scroll  # noqa: F401

SHOTS = Path(os.environ.get("DK_SHOTS", "")) if os.environ.get("DK_SHOTS") else None


@pytest.fixture
def dk(env, monkeypatch):
    import workspace_game_books
    import book_providers
    monkeypatch.setenv("UI_NEXT", "1")
    for k in ("SCOUT_WORKSPACE_V2", "SCOUT_BOOK_LINES", "SCOUT_BOOK_DRAFTKINGS", "SCOUT_BOOK_FANDUEL", "SCOUT_PARITY_PLAYS"):
        monkeypatch.setenv(k, "1")
    book_providers.reset()
    now = datetime.now(timezone.utc)
    doc = {"league": "nba", "events": [{"id": "espn:1", "start_utc": (now + timedelta(hours=3)).isoformat(), "reference_props": [
        {"player_id": "301", "player": "Karl Anders", "market": "points", "period": "game", "line": 20.5, "kind": "line", "revision_id": 99,
         "seen_at": now.isoformat(), "reported_prices": [-117, 105]},
        {"player_id": "302", "player": "Jay Brown", "market": "points", "period": "game", "line": 24.5, "kind": "line", "revision_id": 98,
         "seen_at": now.isoformat(), "reported_prices": [-110, -110]}]},
        {"id": "espn:2", "start_utc": (now + timedelta(hours=5)).isoformat(), "reference_props": [   # graded on the board (B Over 6.5)
            {"player_id": "304", "player": "Reed Lean", "market": "assists", "period": "game", "line": 6.5, "kind": "line", "revision_id": 97,
             "seen_at": now.isoformat(), "reported_prices": [-120, 100]}]}]}
    monkeypatch.setattr(workspace_game_books, "shared", lambda srv, lg: (200, json.dumps(doc if lg == "nba" else {"league": lg, "events": []}).encode()))
    workspace_api.reset()
    yield env
    book_providers.reset()


def open_(browser, env, width, theme, path="/sports#/board?sport=nba"):
    c = context(browser, env, {"width": width, "height": 900 if width > 500 else 844})
    c.add_init_script("try{localStorage.setItem('scout-theme','%s')}catch(e){}" % theme)
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.goto(env.url + path)
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    return c, p


def shot(p, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"{name}-{os.environ.get('DK_TAG', 'after')}.png"))


CLASSES = """(sel)=>{const e=document.querySelector(sel);if(!e)return null;const cs=getComputedStyle(e);
 return {cls:[...e.classList].filter(c=>!/^is-|selected/.test(c)).sort().join(' '),font:cs.fontFamily+' '+cs.fontSize+' '+cs.fontWeight,h:Math.round(e.getBoundingClientRect().height),bg:cs.backgroundColor,color:cs.color,radius:cs.borderRadius}}"""


@pytest.mark.parametrize("width", (390, 1440))
@pytest.mark.parametrize("theme", ("dark", "light"))
def test_draftkings_uses_the_same_components_as_the_other_books(browser, dk, width, theme):
    c, p = open_(browser, dk, width, theme)
    try:
        dkb = '#hub-platform [data-hub=book][data-id=draftkings]'
        if width < 600:                                       # the phone keeps the app pills in the Filters sheet (2026-11-12)
            p.locator("#hub-filters-toggle").click()
        p.wait_for_selector(dkb, timeout=20000)
        # the pill: same element, classes and look as FanDuel's / PrizePicks' (count badge included when the others have one)
        a, b, fd = p.evaluate(CLASSES, dkb), p.evaluate(CLASSES, '#hub-platform [data-hub=book][data-id=prizepicks]'), p.evaluate(CLASSES, '#hub-platform [data-hub=book][data-id=fanduel]')
        ref = fd or b
        assert a["cls"] == ref["cls"] and a["font"] == ref["font"] and a["h"] == ref["h"] and a["radius"] == ref["radius"], (a, ref)
        assert p.locator(dkb).evaluate("b=>b.firstChild.textContent.trim()") == "DK"
        assert (p.locator(dkb + " .h-plays").count() > 0) == (p.locator('#hub-platform [data-hub=book][data-id=prizepicks] .h-plays').count() > 0)
        shot(p, f"board-all-{width}-{theme}")
        if SHOTS and p.locator('#hub-platform [data-hub=book][data-id=fanduel]:not([hidden])').count():
            in_sheet(p, '#hub-platform [data-hub=book][data-id=fanduel]').click(); close_sheet(p)
            p.wait_for_timeout(1500)
            shot(p, f"board-fd-{width}-{theme}")
        p.locator(dkb).click()
        if width < 600:
            p.locator("#hub-filter-pop .h-pop-foot [data-hub=filters]").click()
        p.wait_for_selector('#hub-rows tr[data-record*="draftkings"]', timeout=20000)
        shot(p, f"board-dk-{width}-{theme}")
        row = p.locator('#hub-rows tr[data-record*="draftkings"]').first
        # the row: the same cells as any other row, the app + age meta in the Line cell, odds in the American form other books use
        cells = row.evaluate("r=>[...r.children].map(td=>td.className.split(' ').filter(c=>c.startsWith('h-c-')).join(' '))")
        assert {"h-c-player", "h-c-market", "h-c-line", "h-c-scout", "h-c-add"} <= set(cells), cells
        meta = row.locator(".h-c-line small").first.text_content()
        assert meta.startswith("DK ·"), meta
        txt = " ".join(row.text_content().split())
        assert "-117 / +105" in txt or "-110 / -110" in txt
        # owner 2026-11-13: a DraftKings line carries Scout's saved grade at the exact same line (side, chance, letter), else the
        # shared reported-odds look (h-grade-text family)
        assert row.locator(".h-c-scout .h-direction").count() == 1 or row.locator(".h-c-scout .h-reported").count() == 1
        assert row.locator(".h-c-add button").get_attribute("class").split()[:2] == ["h-add", "h-add-info"]
        txt = txt   # like any view-only row
        no_h_scroll(p)
        # the card: the same header, decision strip and tabs as another book's card
        (row.locator(".h-player") if row.locator(".h-player").is_visible() else row.locator(".h-c-line")).click()      # a phone row opens its card by a tap anywhere
        p.wait_for_selector("#hub-detail .h-card-head", timeout=20000)
        p.wait_for_timeout(300)
        shot(p, f"card-dk-{width}-{theme}")
        assert p.locator("#hub-detail .h-decision").count() == 1 and p.locator("#hub-detail .h-card-tabs").count() == 1
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize("width", (375, 1440))
def test_draftkings_rows_carry_the_exact_line_grade_on_the_previous_design(browser, dk, monkeypatch, width):
    """owner 2026-11-13 ("draftking section should grades and look like the other sections"): on the previous design (the default)
    every DraftKings row whose exact player / event / stat / period / line has a saved Scout grade shows that side, chance and letter
    in the same Scout cell as the other books; nothing is stakeable or addable (view only); no letter is invented for other lines."""
    monkeypatch.delenv("UI_NEXT", raising=False)
    c, p = open_(browser, dk, width, "dark")
    try:
        assert p.locator("html").get_attribute("data-ui") is None
        d = p.evaluate("fetch('/api/upgrade/book-board?sport=nba&book=draftkings',{credentials:'same-origin'}).then(r=>r.json())")
        graded = [r for r in d["records"] if r["scout"].get("grade")]
        assert graded, d["records"]
        assert all(r["scout"]["units"] is None and not r["offer"]["slip"] for r in d["records"])
        dkb = '#hub-platform [data-hub=book][data-id=draftkings]'
        p.wait_for_selector(dkb, timeout=20000)
        p.locator(dkb).click()
        p.wait_for_selector('#hub-rows tr[data-record*="draftkings"]', timeout=20000)
        shot(p, f"prev-board-dk-{width}")
        for r in graded:
            row = p.locator("#hub-rows tr[data-record*=draftkings]").filter(has_text=r["player"]["name"]).first
            cell = " ".join(row.locator(".h-c-scout").inner_text().split())
            assert r["scout"]["grade"] in cell and f"{r['scout']['p_side'] * 100:.1f}%" in cell, cell
            assert ("Over" if r["scout"]["side"] == "over" else "Under") in cell
        no_h_scroll(p)
        row = p.locator("#hub-rows tr[data-record*=draftkings]").filter(has_text=graded[0]["player"]["name"]).first
        (row.locator(".h-player") if row.locator(".h-player").is_visible() else row.locator(".h-c-line")).click()
        p.wait_for_selector("#hub-detail .h-card-head", timeout=20000)
        p.wait_for_timeout(300)
        shot(p, f"prev-card-dk-{width}")
        assert graded[0]["scout"]["grade"] in p.locator("#hub-detail .h-decision").inner_text()
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()
