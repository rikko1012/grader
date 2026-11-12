"""DraftKings (PR #66) on the redesign looks like the other books (owner 2026-11-12: "fix the draftkings book so it looks like the
other ones"): the same pill, row, ladder rung, card and meta components as PrizePicks / Underdog / FanDuel. Display only: DraftKings
data, joins and odds handling are PR #66's and unchanged (no slip add, no consensus, reported odds without a guessed side)."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import workspace_api
from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401

SHOTS = Path(os.environ.get("DK_SHOTS", "")) if os.environ.get("DK_SHOTS") else None


@pytest.fixture
def dk(env, monkeypatch):
    import workspace_game_books
    import book_providers
    monkeypatch.delenv("UI_NEXT", raising=False)
    for k in ("SCOUT_WORKSPACE_V2", "SCOUT_BOOK_LINES", "SCOUT_BOOK_DRAFTKINGS", "SCOUT_BOOK_FANDUEL", "SCOUT_PARITY_PLAYS"):
        monkeypatch.setenv(k, "1")
    book_providers.reset()
    now = datetime.now(timezone.utc)
    doc = {"league": "nba", "events": [{"id": "espn:1", "start_utc": (now + timedelta(hours=3)).isoformat(), "reference_props": [
        {"player_id": "301", "player": "Karl Anders", "market": "points", "period": "game", "line": 20.5, "kind": "line", "revision_id": 99,
         "seen_at": now.isoformat(), "reported_prices": [-117, 105]},
        {"player_id": "302", "player": "Jay Brown", "market": "points", "period": "game", "line": 24.5, "kind": "line", "revision_id": 98,
         "seen_at": now.isoformat(), "reported_prices": [-110, -110]}]}]}
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
        p.wait_for_selector(dkb, timeout=20000)
        # the pill: same element, classes and look as FanDuel's / PrizePicks' (count badge included when the others have one)
        a, b, fd = p.evaluate(CLASSES, dkb), p.evaluate(CLASSES, '#hub-platform [data-hub=book][data-id=prizepicks]'), p.evaluate(CLASSES, '#hub-platform [data-hub=book][data-id=fanduel]')
        ref = fd or b
        assert a["cls"] == ref["cls"] and a["font"] == ref["font"] and a["h"] == ref["h"] and a["radius"] == ref["radius"], (a, ref)
        assert p.locator(dkb).evaluate("b=>b.firstChild.textContent.trim()") == "DK"
        assert (p.locator(dkb + " .h-plays").count() > 0) == (p.locator('#hub-platform [data-hub=book][data-id=prizepicks] .h-plays').count() > 0)
        shot(p, f"board-all-{width}-{theme}")
        if SHOTS and p.locator('#hub-platform [data-hub=book][data-id=fanduel]:not([hidden])').count():
            p.locator('#hub-platform [data-hub=book][data-id=fanduel]').click()
            p.wait_for_timeout(1500)
            shot(p, f"board-fd-{width}-{theme}")
        p.locator(dkb).click()
        p.wait_for_selector('#hub-rows tr[data-record*="draftkings"]', timeout=20000)
        shot(p, f"board-dk-{width}-{theme}")
        row = p.locator('#hub-rows tr[data-record*="draftkings"]').first
        # the row: the same cells as any other row, the app + age meta in the Line cell, odds in the American form other books use
        cells = row.evaluate("r=>[...r.children].map(td=>td.className.split(' ').filter(c=>c.startsWith('h-c-')).join(' '))")
        assert {"h-c-player", "h-c-market", "h-c-line", "h-c-scout", "h-c-add"} <= set(cells), cells
        meta = row.locator(".h-c-line small").inner_text()
        assert meta.startswith("DK ·"), meta
        txt = " ".join(row.inner_text().split())
        assert "-117 / +105" in txt or "-110 / -110" in txt
        assert row.locator(".h-c-scout .h-reported").count() == 1                 # the shared reported-odds look (h-grade-text family)
        assert row.locator(".h-c-add button").get_attribute("class").split()[:2] == ["h-add", "h-add-info"]   # like any view-only row
        no_h_scroll(p)
        # the card: the same header, decision strip and tabs as another book's card
        row.locator(".h-player").click()
        p.wait_for_selector("#hub-detail .h-card-head", timeout=20000)
        p.wait_for_timeout(300)
        shot(p, f"card-dk-{width}-{theme}")
        assert p.locator("#hub-detail .h-decision").count() == 1 and p.locator("#hub-detail .h-card-tabs").count() == 1
        assert not p.errors
    finally:
        c.close()
