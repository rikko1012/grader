"""v3 PLAYER card, QA round 2026-11-20 (lane qa-player): regressions for the bugs found on REAL live line docs (GET, rendered locally
with SCOUT_UI_V3=1). The fixture card's line doc / board answer is reshaped per test to the live shape that showed the bug; nothing
here changes a grade, chance, stake, projection or side (the tests check the card SHOWS the record's values).

- sports grade "Lean" (sub-threshold, 0U) reads "Lean Over" / "Lean Under", never a bare "Lean";
- a competition that repeats the sport chip ("NBA" beside the NBA chip) is printed once;
- delta arrows follow the sign (an Under pick's -0.1 is green and points down), colour follows good / poor;
- a record with no line (LoL no-line state): no "0", no "null", Gap to line reads a dash, no hit strip;
- CS2: the Market chance cell reads the line doc's own market read (the figure the Lines tab prints), not a dash beside it;
- soccer: the opponent comes from the event label when the feed leaves it empty; ESPN league slugs read as league names;
- tennis: 'atp_challenger' reads 'ATP Challenger'; role "Player" and his own name as team are not printed under his name;
- Lines tab: the Books and lines "Seen" column says "N min ago"; "Across 1 book".
"""
import json

import pytest

from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401
from tests.test_ui_v3_player_browser import setup, page, open_card, tab, PHONE, DESK, search
from tests.test_expansion_main_board import expansion  # noqa: F401


def reshape(p, pattern, fn):
    """route the hub's own GET through fn(doc) (the server's fixture answer, reshaped to the live shape under test)"""
    def handle(route):
        resp = route.fetch()
        try:
            doc = resp.json()
        except Exception:
            return route.fulfill(response=resp)
        doc = fn(doc) or doc
        h = {k: v for k, v in resp.headers.items() if k.lower() not in ("etag", "content-length", "content-encoding")}
        route.fulfill(status=resp.status, body=json.dumps(doc), headers={**h, "content-type": "application/json"})
    p.route(pattern, handle)


def page_with(browser, env, size, theme, routes):
    """tests/test_ui_v3_player_browser.page, with the reshaping routes in place BEFORE the hub's first board read"""
    c = context(browser, env, size)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    p = c.new_page()
    for pat, fn in routes:
        reshape(p, pat, fn)
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.goto(env.url + "/#all-sports")
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    return c, p


def board_records(doc, fn):
    for r in doc.get("records") or []:
        fn(r)


def text(p, sel):
    return p.locator(sel).first.inner_text().replace("\n", " ")


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_sports_lean_reads_with_its_side_and_competition_not_twice(browser, env, monkeypatch, size):
    setup(env, monkeypatch, "dark")

    def lean(r):
        if r.get("sport") == "nba" and (r.get("scout") or {}).get("side") in ("over", "under"):
            r["scout"].update(grade="Lean", units=0, hold=None)
            r["event"]["competition"] = "NBA"

    def line(doc):
        lean(doc["record"])
    c, p = page_with(browser, env, size, "dark", [("**/api/upgrade/board?sport=nba*", lambda d: board_records(d, lean)),
                                                  ("**/api/upgrade/line?sport=nba*", line)])
    try:
        open_card(p, "nba", "Karl")
        p.wait_for_selector("#hub-detail > .v3p-lead .v3p-pick", timeout=20000)
        side = p.evaluate("document.querySelector('#hub-decision .h-side-toggle [aria-pressed=true]').dataset.id")
        word = "Lean " + ("Over" if side == "over" else "Under")
        p.wait_for_function(f"document.querySelector('#hub-decision .h-dec-pick') && document.querySelector('#hub-decision .h-dec-pick').textContent.includes('{word}')")
        assert word in text(p, "#hub-detail .v3p-pick .v3p-pickchip")
        # the hub's own letter is unchanged underneath: the record still says "Lean" (display adds the side word only)
        assert p.evaluate("document.querySelector('#hub-decision .h-dec-chance > b').textContent") == text(p, "#hub-detail .v3p-pick .sc-pick__chance b")
        meta = p.locator("#hub-detail .v3p-meta").inner_text()
        assert meta.count("NBA") == 1, meta
        # the line panel survives the hub's repaints (side toggle back and forth) with the side word, never a bare "Lean"
        other = "under" if side == "over" else "over"
        p.locator(f'#hub-decision [data-hub=history-side][data-id={other}]').click()
        p.locator(f'#hub-decision [data-hub=history-side][data-id={side}]').click()
        p.wait_for_function(f"document.querySelector('#hub-decision .h-dec-pick') && document.querySelector('#hub-decision .h-dec-pick').textContent.trim() === '{word}'")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_delta_arrows_follow_the_sign(browser, env, monkeypatch):
    setup(env, monkeypatch, "light")
    c, p = page(browser, env, PHONE, "light")
    try:
        open_card(p, "nba", "Karl")
        p.wait_for_selector("#hub-detail .v3p-grid .sc-stat", timeout=20000)
        rows = p.evaluate("""[...document.querySelectorAll('#hub-detail .v3p-grid .sc-stat:is(.is-good,.is-poor) i')].map(i => {
            const b = getComputedStyle(i, '::before');
            return {t: i.textContent, cls: i.className, up: parseFloat(b.borderBottomWidth), dn: parseFloat(b.borderTopWidth)}; })""")
        assert rows, "no toned delta on the fixture card"
        for r in rows:
            if r["t"].startswith("−"):
                assert "v3p-dn" in r["cls"] and r["dn"] > 0 and r["up"] == 0, r
            elif r["t"].startswith("+"):
                assert "v3p-up" in r["cls"] and r["up"] > 0 and r["dn"] == 0, r
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_no_line_is_never_zero(browser, env, monkeypatch, size):
    """the LoL no-line state: offer.line null. The card shows dashes; nothing reads 0, null or NaN, no hit strip is counted against 0"""
    setup(env, monkeypatch, "dark")
    c, p = page(browser, env, size, "dark")

    def noline(doc):
        doc["record"]["offer"]["line"] = None
    reshape(p, "**/api/upgrade/line?sport=lol*", noline)
    try:
        p.locator('#hub-sports [data-id=lol]').click()
        search(p, "Kratos")
        p.wait_for_timeout(300)
        p.locator('#hub-rows tr[data-record] .h-player').first.click()
        p.wait_for_selector("#hub-detail > .v3p-lead", timeout=20000)
        p.wait_for_timeout(500)
        facts = p.locator("#hub-decision .v3p-facts")
        if facts.count():
            t = facts.inner_text().replace("\n", " ")
            assert "Gap to line —" in t, t
        assert p.locator("#hub-decision .v3p-hits").count() == 0
        txt = p.locator("#hub-detail").inner_text()
        for bad in ("null", "NaN", "undefined", "vs the line 0", "Over 0 ", "Under 0 "):
            assert bad not in txt, bad
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_cs2_market_chance_reads_the_line_docs_market(browser, env, monkeypatch):
    setup(env, monkeypatch, "dark")
    c, p = page(browser, env, PHONE, "dark")

    def mk(doc):
        doc["record"]["market_chance"] = None
        doc["market"] = {"available": True, "p_over": 0.5, "p_under": 0.5, "source": "Underdog prices, margin removed"}
    reshape(p, "**/api/upgrade/line?book=*", mk)
    try:
        open_card(p, "cs2", "vargo")
        p.wait_for_selector("#hub-decision .v3p-facts", timeout=20000)
        t = p.locator("#hub-decision .v3p-facts").inner_text().replace("\n", " ")
        assert "Market chance 50.0% From Underdog prices" in t, t
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_soccer_and_tennis_names(browser, env, expansion, monkeypatch, size):  # noqa: F811
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    c = context(browser, env, size)
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))

    def soccer(doc):
        r = doc["record"]
        team = r["player"].get("team") or "Home FC"
        r["player"]["team"] = team
        r["player"]["opponent"] = ""
        r["event"]["label"] = f"{team} · Away United"
        r["event"]["competition"] = "ger.1"

    def tennis(doc):
        r = doc["record"]
        r["player"]["role"] = "Player"
        r["player"]["team"] = r["player"]["name"]
        r["event"]["competition"] = "atp_challenger"
    reshape(p, "**/api/upgrade/line?sport=soccer*", soccer)
    reshape(p, "**/api/upgrade/line?sport=tennis*", tennis)
    try:
        p.goto(env.url + "/#scout/board?sport=soccer")
        p.wait_for_selector("#hub-rows .h-player", timeout=30000)
        p.locator("#hub-rows .h-player").first.click()
        p.wait_for_selector("#hub-detail > .v3p-lead .v3p-meta", timeout=20000)
        assert text(p, "#hub-detail .v3p-match .sc-matchup__team--b b") == "Away United"
        assert "Bundesliga" in text(p, "#hub-detail .v3p-meta") and "ger.1" not in text(p, "#hub-detail .v3p-meta")
        p.locator('#hub-detail [data-hub=card-close]').first.click()
        p.wait_for_function("document.getElementById('hub-card').hidden")
        p.locator('#hub-sports [data-id="tennis"]').click()
        p.wait_for_function("document.querySelector('#hub-rows').textContent.includes('Aces')")
        p.locator("#hub-rows .h-player").first.click()
        p.wait_for_selector("#hub-detail > .v3p-lead .v3p-meta", timeout=20000)
        assert "ATP Challenger" in text(p, "#hub-detail .v3p-meta")
        name = p.evaluate("document.querySelector('#hub-detail .h-card-id h3').childNodes[0].textContent.trim()")
        who = p.locator("#hub-detail .v3p-who")
        assert who.count() == 0 or (name not in who.inner_text() and "Player" not in who.inner_text())
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_lines_tab_wording(browser, env, monkeypatch):
    setup(env, monkeypatch, "dark")
    c, p = page(browser, env, DESK, "dark")
    try:
        open_card(p, "cs2", "vargo")
        tab(p, "lines")
        p.wait_for_timeout(300)
        seen = p.evaluate("[...document.querySelectorAll('#hub-card-body .h-line-shop tbody td:last-child')].map(t => t.textContent)")
        assert seen, "no Books and lines table"
        for s in seen:
            assert s == "—" or s.endswith(" min ago") or s.endswith(" h ago") or s.endswith("d ago") or s == "just now", s
        body = p.locator("#hub-card-body").inner_text()
        assert "Across 1 books" not in body
        assert not p.errors, p.errors
    finally:
        c.close()
