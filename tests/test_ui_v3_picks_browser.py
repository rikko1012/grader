"""v3 group C (web/UI_V3.md) in a real browser against the real Handler with fixture data only, SCOUT_UI_V3=1: My picks, the
member Record, the public /record, EV (Best prices), Arbitrage / price check, Sportsbooks game lines and line-move chips, each at
390x844 and 1280x900, dark and light. Every field the page shows today is still there (the v3 files only restyle and add derived
items computed from the numbers beside them), no sideways scroll, no script error. V3_SHOTS=<folder> keeps a screenshot each."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import closing_lines as cl
import workspace_api
from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401 (fixtures)
from tests.test_ui_v3_browser import v3_page, PHONE, DESK
from tests.test_pickem_ev_browser import ev_on  # noqa: F401 (fixture)
from tests.test_hub_shell_browser import market_doc
from tests.test_settled_line_moves_browser import flags
from tests.workspace_fixture import leg_of

SHOTS = Path(os.environ.get("V3_SHOTS") or "")
SIZES = [PHONE, DESK]
THEMES = ["dark", "light"]


def shot(p, name, full=False):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(350)
        p.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


CONTRAST_JS = """(sel) => {
  const lum = (c) => { const m = c.match(/[\\d.]+/g); if (!m) return null; const [r, g, b] = m.slice(0, 3).map(Number).map(v => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); }); return .2126 * r + .7152 * g + .0722 * b; };
  const alpha = (c) => { const m = c.match(/[\\d.]+/g); return m && m.length > 3 ? Number(m[3]) : 1; };
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const b = getComputedStyle(e).backgroundColor; if (alpha(b) > .5) return b; } return getComputedStyle(document.body).backgroundColor; };
  const out = [];
  document.querySelectorAll(sel + ' *').forEach((el) => {
    if (!el.childNodes.length || ![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) return;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (!r.width || !r.height || cs.visibility === 'hidden' || cs.display === 'none' || el.closest('[hidden]') || Number(cs.opacity) < .5) return;
    if (el.closest('option, select')) return;
    const a = lum(cs.color), b = lum(bgOf(el)); if (a == null || b == null) return;
    const ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    if (ratio < 3) out.push(el.tagName + '.' + el.className + ' "' + el.textContent.trim().slice(0, 30) + '" ' + ratio.toFixed(2));
  });
  return out.slice(0, 10);
}"""


def readable(p, sel):
    """Every visible text in the section reads at 3:1 or better against the surface it sits on (catches hub hard-coded colours)."""
    bad = p.evaluate(CONTRAST_JS, sel)
    assert not bad, bad


def api(p, method, path, body=None):
    return p.evaluate("""async ([m, path, body]) => { const r = await fetch('/api/upgrade/' + path, {method: m, headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined}); return [r.status, await r.json()]; }""", [method, path, body])


def seed_picks(p, env):
    """Two tracked slips through the real track route: hit, miss, push, void and two pending receipts (picklog settles, as in prod)."""
    st, doc = api(p, "GET", "board")
    assert st == 200
    r = {(x["player"]["name"], x["offer"]["platform"]): x for x in doc["records"]}
    ud = [leg_of(r[(n, "underdog")]) for n in ("vargo", "orlo", "brandr", "raiko")]
    st, out = api(p, "POST", "track", {"idem": "idem-v3c-000001", "entry_key": "underdog", "entry_type": "standard", "legs": ud})
    assert st == 200, out
    pp = [leg_of(r[(n, "prizepicks")]) for n in ("vargo", "orlo")]
    st, out = api(p, "POST", "track", {"idem": "idem-v3c-000002", "entry_key": "prizepicks", "entry_type": "power", "legs": pp})
    assert st == 200, {k: v for k, v in out.items() if k not in ("legs",)}
    env.settle(env.by("vargo"), actual=41.0)                    # Over 38.5: hit
    env.settle(env.by("orlo"), actual=30.0)                     # Over 33.5: miss
    env.settle(env.by("brandr"), status="void")                 # void
    env.settle(env.by("raiko"), status="push", actual=36.5)    # push (the PrizePicks pair stays pending)
    workspace_api.store().settle_once()


def picks_page(browser, env, monkeypatch, size, theme):
    c, p = v3_page(browser, env, monkeypatch, "board", size, theme)
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    seed_picks(p, env)
    p.goto(env.url + "/#/picks")
    p.wait_for_selector("#hub-picks-body .u-pick", timeout=20000)
    p.wait_for_selector("#hub-picks-section .v3c-score .v3c-tile", timeout=10000)
    return c, p


# ------------------------------------------------------------------------------------------------ My picks
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_my_picks_v3_keeps_every_field(browser, env, monkeypatch, size, theme):
    c, p = picks_page(browser, env, monkeypatch, size, theme)
    try:
        sec = "#hub-picks-section"
        # today's fields: the filters (both selects, unchanged), the summary line, slip groups, each receipt with status + final
        assert p.locator("#hub-pick-sport").count() == 1 and p.locator("#hub-pick-status").count() == 1
        summary = p.locator(f"{sec} .h-pick-summary").inner_text()
        assert "1–1" in summary and "won–lost" in summary and "void" in summary and "pending" in summary
        assert p.locator(f"{sec} .h-picks-group").count() >= 2
        assert p.locator(f"{sec} .u-pick").count() == 6
        sts = " ".join(p.locator(f"{sec} .u-st").all_inner_texts())
        for word in ("Hit", "Miss", "Push", "Void", "Pending"):
            assert word in sts, word
        assert "Pending" in p.locator(f"{sec} .h-picks-part h3").first.inner_text()
        assert p.locator(f"{sec} .h-picks-foot [data-hub=section][data-id=record]").count() == 1
        # v3: the server's own record (the tray's Record tab numbers): tiles, ticks and key reconcile with each other
        tiles = p.locator(f"{sec} .v3c-score .v3c-tile b").all_inner_texts()
        assert tiles[0] == "50.0%" and tiles[1].replace("–", "-").replace(" ", "") == "1-1-1" and tiles[2] == "1" and tiles[3] == "2"
        assert "Hit rate, 2 decided" in p.locator(f"{sec} .v3c-score").inner_text()
        assert p.locator(f"{sec} .v3c-ticks i").count() == 6
        shot(p, f"picks-{size['width']}-{theme}-top")
        # chance you saw, from the saved receipt, beside each status (never a 0 for a missing one)
        saw = p.locator(f"{sec} .u-pick .v3c-saw").all_inner_texts()
        assert len(saw) == 6 and all(s.startswith("Chance you saw ") for s in saw)
        assert not any(s.endswith(" 0%") or s.endswith(" 0.0%") for s in saw)
        # pills mirror the selects and press them
        pills = p.locator(f"{sec} .v3c-pills[data-for=hub-pick-status] button")
        assert [t.strip() for t in pills.all_inner_texts()] == ["All outcomes", "Pending", "Won", "Lost", "Pushed", "Void"]
        p.locator(f"{sec} .v3c-pills[data-for=hub-pick-status] button", has_text="Won").click()
        p.wait_for_function("document.getElementById('hub-pick-status').value==='hit'")
        p.wait_for_function("document.querySelectorAll('#hub-picks-body .u-pick').length===1")
        assert p.locator(f"{sec} .v3c-pills[data-for=hub-pick-status] button[aria-pressed=true]").inner_text().strip() == "Won"
        p.locator(f"{sec} .v3c-pills[data-for=hub-pick-status] button", has_text="All outcomes").click()
        p.wait_for_function("document.querySelectorAll('#hub-picks-body .u-pick').length===6")
        # an open receipt keeps its full field list
        p.locator(f"{sec} .u-pick button[data-u=pick]").first.click()
        p.wait_for_selector(f"{sec} .u-pick-body .u-kv")
        kv = p.locator(f"{sec} .u-pick-body .u-kv").inner_text()
        for k in ("Chance you saw", "Scout read", "Projection", "Book", "Tracked", "Receipt"):
            assert k in kv, k
        assert p.locator(f"{sec} .u-pick-body .u-time li").count() >= 1
        # the light / dark ground is the v3 one, and the page clears the phone bottom bar
        bg = p.evaluate("getComputedStyle(document.getElementById('hub-center')).backgroundColor")
        assert bg == ("rgb(246, 247, 249)" if theme == "light" else "rgb(8, 11, 19)")
        readable(p, "#hub-picks-section")
        no_h_scroll(p)
        shot(p, f"picks-{size['width']}-{theme}")
        if size is PHONE:
            p.evaluate("document.getElementById('hub-center').scrollTop=1e6; window.scrollTo(0,1e6)")
            p.wait_for_timeout(200)
            foot = p.locator(f"{sec} .h-picks-foot").bounding_box()
            bar = p.locator(".sc-v3-tabs").bounding_box()
            assert foot["y"] + foot["height"] <= bar["y"] + 1
            shot(p, f"picks-{size['width']}-{theme}-end")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_my_picks_v3_empty_state_keeps_todays_words(browser, env, monkeypatch):
    c, p = v3_page(browser, env, monkeypatch, "picks", PHONE, "dark")
    try:
        p.wait_for_selector("#hub-picks-body .h-empty")
        t = p.locator("#hub-picks-body .h-empty").inner_text()
        assert "No tracked picks yet" in t and "Find a player prop" in t
        assert p.locator("#hub-picks-section .v3c-score").count() == 0
        shot(p, "picks-empty-390-dark")
        assert not p.errors, p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ member Record (hub)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_member_record_v3_keeps_every_field(browser, env, monkeypatch, size, theme):
    c, p = v3_page(browser, env, monkeypatch, "record-hub", size, theme)
    try:
        p.wait_for_selector("#hub-record-body .h-record-card .h-score", timeout=30000)
        card = p.locator("#hub-record-body .h-record-card").first
        txt = card.inner_text()
        for k in ("Wins / losses", "Actual hit rate", "Average stated chance", "decided picks", "Updated"):
            assert k in txt, k
        assert p.locator("#hub-record-sport").count() == 1
        assert card.locator(".h-rel-row").count() >= 1 and card.locator(".h-record-calendar").count() == 1
        # v3: the delta is the two numbers shown beside it, never computed from anything else
        nums = p.evaluate("""() => { const c = document.querySelector('#hub-record-body .h-record-card'), b = [...c.querySelectorAll('.h-score > div > b')].map(x => x.textContent);
          return [b[1], b[2], (c.querySelector('.v3c-delta') || {}).textContent || ''] }""")
        obs, pr, delta = float(nums[0].rstrip("%")), float(nums[1].rstrip("%")), nums[2]
        want = round(obs - pr, 1)
        assert ("pts vs stated" in delta) and (f"{abs(want):.1f}" in delta)
        # the pill sport switch presses the select
        assert p.locator("#hub-record-section .v3c-pills[data-for=hub-record-sport] button").count() >= 2
        bg = p.evaluate("getComputedStyle(document.getElementById('hub-center')).backgroundColor")
        assert bg == ("rgb(246, 247, 249)" if theme == "light" else "rgb(8, 11, 19)")
        readable(p, "#hub-record-section")
        no_h_scroll(p)
        shot(p, f"record-hub-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


def stated_doc():
    return {"totals": {"wins": 1357, "losses": 1211, "pushes": 28, "observed": 0.528, "printed": 0.607, "ci": [0.502, 0.553], "matches": 487},
            "population": "The favoured side when its printed chance was 57% or more", "rule": "Each pick is scored on its own line.",
            "by_letter": [{"name": "A", "picks": 20, "printed": 0.76, "observed": 0.6}, {"name": "B", "picks": 2063, "printed": 0.593, "observed": 0.524},
                          {"name": "A+", "picks": 1, "printed": 0.943, "observed": None}],
            "calendar": {"basis": "Match start date · US Central", "missing_dates": 0, "days": [
                {"date": "2026-11-16", "decided": 38, "wins": 18, "losses": 20, "pushes": 0, "hit_rate": 0.474},
                {"date": "2026-11-17", "decided": 75, "wins": 46, "losses": 29, "pushes": 1, "hit_rate": 0.613},
                {"date": "2026-11-18", "decided": 69, "wins": 41, "losses": 28, "pushes": 0, "hit_rate": 0.594},
                {"date": "2026-11-19", "decided": 0, "wins": 0, "losses": 0, "pushes": 0, "hit_rate": None}]},
            "as_of": "2026-11-19T23:35:00+00:00"}


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_member_record_v3_delta_gap_bars_day_marks_and_legend(browser, env, monkeypatch, size, theme):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    c = context(browser, env, size)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    c.route("**/api/upgrade/stated", lambda r: r.fulfill(json=stated_doc()))
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    try:
        p.goto(env.url + "/#/record")
        p.wait_for_selector("#hub-record-body .h-record-card .h-score", timeout=30000)
        p.locator("#hub-record-section .v3c-pills button", has_text="CS2").click()
        p.wait_for_function("document.getElementById('hub-record-sport').value==='cs2'")
        p.wait_for_selector("#hub-record-body .h-record-card .v3c-delta")
        card = p.locator("#hub-record-body .h-record-card").first
        assert "−7.9 pts vs stated" in card.locator(".v3c-delta").inner_text()
        assert "is-poor" in card.locator(".v3c-delta").get_attribute("class")
        assert card.locator(".h-rel-scale .v3c-gap").count() == 2                      # A+ has no actual: no bar, its stated ring stays
        assert card.locator(".h-calendar-grid details[data-v3=poor]").count() >= 1 and card.locator(".h-calendar-grid details[data-v3=good]").count() >= 1
        assert "average stated chance, 60.7%" in card.locator(".v3c-cal-key").inner_text()
        readable(p, "#hub-record-section")
        no_h_scroll(p)
        card.locator(".h-reliability").scroll_into_view_if_needed()
        shot(p, f"record-hub-data-{size['width']}-{theme}")
        card.locator(".h-record-calendar").scroll_into_view_if_needed()
        shot(p, f"record-hub-calendar-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ public /record
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_public_record_v3_keeps_every_field(browser, env, monkeypatch, size, theme):
    c, p = v3_page(browser, env, monkeypatch, "record", size, theme)
    try:
        p.wait_for_selector("#record h2", timeout=30000)
        assert p.locator("a.skip").count() == 1
        for b in ("CS2", "LoL", "NBA", "NFL"):
            assert p.locator(f"nav[aria-label='Record sport'] button", has_text=b).count() == 1
        assert p.locator("main > header a[href='/reviews']").count() == 1 and p.locator("main > header a[href='/']").count() >= 1
        txt = p.locator("#record").inner_text()
        for k in ("Wins–losses", "Actual hit rate", "Average stated chance", "How this record is counted", "Stated vs actual, by tier", "Snapshot"):
            assert k in txt, k
        assert p.locator("main > footer").inner_text().startswith("Forecasts were saved before their games")
        assert p.evaluate("getComputedStyle(document.body).fontFamily").startswith("Outfit")
        bg = p.evaluate("getComputedStyle(document.body).backgroundColor")
        assert bg == ("rgb(246, 247, 249)" if theme == "light" else "rgb(8, 11, 19)")
        # sport switch still works (record.js's own buttons)
        p.locator("nav[aria-label='Record sport'] button", has_text="LoL").click()
        p.wait_for_function("document.querySelector('#record h2') && document.querySelector('#record h2').textContent==='LoL'")
        p.locator("nav[aria-label='Record sport'] button", has_text="CS2").click()
        p.wait_for_function("document.querySelector('#record h2').textContent==='CS2'")
        readable(p, "main")
        no_h_scroll(p)
        shot(p, f"record-public-{size['width']}-{theme}", full=True)
        assert not p.errors, p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ EV (Best prices), price check, books
def v3_member(browser, env, monkeypatch, size, theme, path, ready, route=None):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    c = context(browser, env, size)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    if route:
        route(p)
    p.goto(env.url + path)
    p.wait_for_selector(ready, timeout=30000)
    p.wait_for_function("!!window.ScoutV3")
    return c, p


def ground(p, theme):
    bg = p.evaluate("getComputedStyle(document.getElementById('hub-center')).backgroundColor")
    assert bg == ("rgb(246, 247, 249)" if theme == "light" else "rgb(8, 11, 19)")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_ev_v3_keeps_every_control_and_column(browser, env, ev_on, monkeypatch, size, theme):  # noqa: F811
    c, p = v3_member(browser, env, monkeypatch, size, theme, "/#scout/ev", "#hub-ev-body #ev-entry")
    try:
        p.wait_for_selector("#hub-ev-body .ev-list, #hub-ev-body .ev-banner", timeout=20000)
        assert p.locator('[data-ev="app"]').count() == 2 and p.locator("#ev-entry").count() == 1
        assert p.locator("#hub-ev-body .ev-search, #hub-ev-body input[type=search]").count() >= 1
        ground(p, theme)
        no_h_scroll(p)
        shot(p, f"ev-{size['width']}-{theme}")
        # rows (Underdog standard 2 in the fixture): every column, the books table, the positive figure in the good ink
        p.locator('[data-ev="app"][data-id="underdog"]').click()
        p.wait_for_selector("#hub-ev-body .ev-row", timeout=30000)
        rows = p.locator("#hub-ev-body .ev-list:not(.ev-list-model) .ev-row")
        assert rows.count() >= 1 and "Austin Reaves" in rows.first.inner_text()
        labels = rows.first.evaluate("r => [...r.querySelectorAll('.ev-cell')].map(c => c.dataset.label || '')")
        assert "Play" in labels and len(labels) >= 5
        assert p.evaluate("getComputedStyle(document.querySelector('#hub-ev-body .ev-list')).backgroundColor") == ("rgb(255, 255, 255)" if theme == "light" else "rgb(15, 21, 36)")
        pos = p.locator("#hub-ev-body .ev-ev.is-pos b")
        if pos.count():
            good = p.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--sc-good').trim()")
            assert pos.first.evaluate("e => getComputedStyle(e).color") == {"#5BD69A": "rgb(91, 214, 154)", "#0F7A4A": "rgb(15, 122, 74)"}[good.upper()]
        gen = p.locator('#hub-ev-body [data-ev="generate"]')
        if gen.count():
            assert gen.evaluate("e => getComputedStyle(e).backgroundColor") == ("rgb(14, 19, 32)" if theme == "light" else "rgb(232, 236, 243)")
        rows.first.locator('[data-ev="books"]').click()
        p.wait_for_selector("#hub-ev-body .ev-src table")
        src = p.locator("#hub-ev-body .ev-src").first.inner_text()
        assert "FanDuel" in src and "Left out" in src
        readable(p, "#hub-ev-section")
        no_h_scroll(p)
        shot(p, f"ev-rows-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_arbitrage_v3_keeps_the_price_check(browser, env, monkeypatch, size, theme):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    doc = market_doc()
    c, p = v3_member(browser, env, monkeypatch, size, theme, "/#/arb", "#hub-arb-section:not([hidden]) #hub-market-tools",
                     route=lambda p: p.route("**/api/upgrade/market-tools", lambda r: r.fulfill(json=doc)))
    try:
        p.wait_for_selector("#hub-market-filters .h-market-filters", timeout=20000)
        assert p.locator('#hub-market-tools [data-hub="refresh-markets"]').count() == 1
        assert p.locator("#hub-market-results").count() == 1
        p.wait_for_selector("#hub-market-results .h-pc-row:not(.h-pc-head)", timeout=20000)
        assert p.locator("#hub-market-results .h-pc-row:not(.h-pc-head)").count() >= 1
        ground(p, theme)
        readable(p, "#hub-arb-section")
        no_h_scroll(p)
        shot(p, f"arb-{size['width']}-{theme}")
        p.locator("#hub-market-results .h-pc-row:not(.h-pc-head)").first.scroll_into_view_if_needed()
        shot(p, f"arb-rows-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_sportsbooks_v3_keeps_game_lines(browser, env, monkeypatch, size, theme):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    now = datetime.now(timezone.utc)

    def response(route):
        league = "nfl" if "league=nfl" in route.request.url else "nba"
        route.fulfill(json={"league": league, "source": "ESPN", "observed_at": now.isoformat(), "events": [{
            "id": "espn:test", "away": "Golden State Warriors", "home": "Minnesota Timberwolves",
            "start_utc": (now + timedelta(days=1)).isoformat(), "seen_at": None,
            "books": {"draftkings": {"id": "draftkings", "label": "DraftKings", "markets": {
                "money-line": {"awayOdds": 150, "homeOdds": -170},
                "spread": {"awaySpread": 3.5, "homeSpread": -3.5, "awayOdds": -110, "homeOdds": -110},
                "totals": {"total": 231.5, "overOdds": -115, "underOdds": -105}}}}}]})
    c, p = v3_member(browser, env, monkeypatch, size, theme, "/#/books", "#hub-book-board summary",
                     route=lambda p: p.route("**/api/upgrade/game-books?*", response))
    try:
        panel = p.locator("#hub-book-board")
        assert "Quote age unavailable" in panel.inner_text()
        panel.locator("summary").first.click()
        t = panel.inner_text()
        assert "+150" in t and "Over 231.5" in t
        for k in ("Moneyline", "Spread", "Game total"):
            assert k.lower() in t.lower(), k
        for g in ("league", "book", "refresh"):
            assert panel.locator(f"[data-gb={g}]").count() == 1, g
        ground(p, theme)
        readable(p, "#hub-books-section")
        no_h_scroll(p)
        shot(p, f"books-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ line move (bump) chip on a receipt
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_bump_chip_on_a_receipt_keeps_its_words_and_title(browser, env, monkeypatch, size, theme):
    flags(monkeypatch)
    cl.reset()
    con = cl._connect()
    con.execute("INSERT INTO closing_lines (ident, version, sport, book, event_id, player_key, prop, scope, line_type, line, open_line, lock_utc, last_seen_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (cl.ident(env.by("orlo")), "cl-1", "cs2", "underdog", "hltv:901", "orlo", "kills", "12", "standard",
                                                      33.5, 32.5, "2026-11-07T12:00:00+00:00", "2026-11-07T11:59:00+00:00"))
    con.commit()
    con.close()
    c, p = picks_page(browser, env, monkeypatch, size, theme)
    try:
        o = p.locator("#hub-picks-body .u-pick", has_text="orlo Over 33.5")
        o.locator(".u-move").wait_for()
        assert " ".join(o.locator(".u-move").inner_text().split()) == "▲ bumped from 32.5"
        assert o.locator(".u-bump").get_attribute("title")
        assert o.locator(".v3c-saw").count() == 1
        o.scroll_into_view_if_needed()
        readable(p, "#hub-picks-section")
        no_h_scroll(p)
        shot(p, f"bump-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ line discrepancy chip on the board
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_line_discrepancy_chip_keeps_its_words_and_tip(browser, env, monkeypatch, size, theme):
    c, p = v3_page(browser, env, monkeypatch, "board", size, theme)
    try:
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
        # the fixture board carries no market-edge doc, so the chip is the hub's own gapChip() markup placed in the first row
        p.evaluate("""() => { const td = document.querySelector('#hub-rows tr[data-record] td:nth-child(2)') || document.querySelector('#hub-rows tr[data-record] td');
          td.insertAdjacentHTML('beforeend', '<span class="h-gap" title="Line discrepancy: PrizePicks lists 34.5 for the same player, stat and maps, 1 higher than this line." tabindex="0">≠ PrizePicks 34.5</span>'); }""")
        g = p.locator("#hub-rows .h-gap").first
        assert g.evaluate("e => getComputedStyle(e).fontFamily").startswith("Outfit")
        assert g.evaluate("e => getComputedStyle(e).color") != "rgb(255, 155, 138)"            # neutral ink, never a value colour
        no_h_scroll(p)
        g.scroll_into_view_if_needed()
        shot(p, f"gap-chip-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ the mine read never loops
def test_my_picks_reads_mine_once_per_receipt_set_even_when_it_cannot_cover_the_page(browser, env, monkeypatch):
    c, p = v3_page(browser, env, monkeypatch, "board", DESK, "dark")
    try:
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
        seed_picks(p, env)
        calls = []

        def stale(route):          # every read answers with one receipt still pending: the doc never covers the page
            calls.append(1)
            r = route.fetch()
            doc = r.json()
            for x in doc["receipts"]:
                if x["status"] == "hit":
                    x["status"] = "pending"
            route.fulfill(response=r, json=doc)
        p.route("**/api/upgrade/mine", stale)
        p.goto(env.url + "/#/picks")
        p.wait_for_selector("#hub-picks-body .u-pick", timeout=20000)
        p.wait_for_timeout(1500)
        n = len(calls)
        p.wait_for_timeout(1500)
        assert len(calls) == n and n <= 3, calls            # the hub's own read + one v3 read; nothing after
        assert not p.errors, p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ public /record: slips view, bin chart, errors, OS theme
def record_doc():
    return {"sport": "cs2", "totals": {"wins": 120, "losses": 100, "pushes": 3, "void": 2, "observed": 0.5455, "printed": 0.583, "ci": [0.48, 0.61],
                                       "matches": 80, "match_word": "matches"},
            "population": "The favoured side when its printed chance was 57% or more", "rule": "Each pick is scored on its own line.",
            "bands": [{"name": "A", "wins": 30, "losses": 20, "printed": 0.66, "observed": 0.6, "ci": [0.45, 0.72]},
                      {"name": "B", "wins": 90, "losses": 80, "printed": 0.58, "observed": 0.53, "ci": [0.46, 0.6]}],
            "reliability": [{"lo": 0.55, "hi": 0.6, "n": 150, "stated": 0.575, "hit": 0.54, "ci": [0.47, 0.61]},
                            {"lo": 0.6, "hi": 0.65, "n": 70, "stated": 0.62, "hit": 0.6, "ci": [0.5, 0.7]}],
            "calendar": {"basis": "Match start date · US Central", "days": [
                {"date": "2026-11-17", "decided": 20, "wins": 12, "losses": 8, "pushes": 0, "hit_rate": 0.6},
                {"date": "2026-11-18", "decided": 25, "wins": 14, "losses": 11, "pushes": 1, "hit_rate": 0.56},
                {"date": "2026-11-19", "decided": 10, "wins": 4, "losses": 6, "pushes": 0, "hit_rate": 0.4}]},
            "as_of": "2026-11-19T12:00:00+00:00", "status": {"text": "The CS2 board shows letter grades.", "letters": "shown"}, "slips_tab": True}


def slips_doc():
    return {"all_hit": 12, "missed": 30, "all_hit_rate": 0.2857, "settled": 42, "tracked": 50, "pending": 6, "push_or_void": 2, "mixed": 1,
            "legs": {"hit": 90, "miss": 40, "push": 2, "void": 1, "pending": 9},
            "by_size": [{"name": "2 picks", "settled": 20, "all_hit": 8, "missed": 12, "push_or_void": 1}],
            "by_platform": [{"name": "Underdog", "settled": 42, "all_hit": 12, "missed": 30, "push_or_void": 2}]}


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_public_record_v3_chart_calendar_slips_and_error(browser, env, monkeypatch, size, theme):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    c = browser.new_context(viewport=size)
    c.route("https://fonts.*/**", lambda r: r.abort())
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    fail = {"on": False}
    c.route(lambda u: "/api/record?" in u, lambda r: r.fulfill(status=503, json={"error": "busy"}) if fail["on"] else r.fulfill(json=record_doc()))
    c.route(lambda u: "/api/record/slips?" in u, lambda r: r.fulfill(json=slips_doc()))
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    try:
        p.goto(env.url + "/record")
        p.wait_for_selector("#record .rel-chart", timeout=30000)
        txt = p.locator("#record").inner_text()
        for k in ("120–100", "95% range", "decided (wins plus losses)", "pushes, counted apart", "voids, left out", "Stated vs actual, in 5-point bins",
                  "Stated bin", "Last 30 days", "The CS2 board shows letter grades."):
            assert k in txt, k
        delta = p.locator("#record .scores .v3-delta").inner_text()
        assert "−3.8 pts vs stated" in delta                            # 54.5% - 58.3%, both shown beside it
        assert p.locator("#record .calendar details[data-v3=poor]").count() == 1 and p.locator("#record .calendar details[data-v3=good]").count() == 1
        assert "average stated chance, 58.3%" in p.locator("#record .v3-cal-key").inner_text()
        text_fill = p.evaluate("getComputedStyle(document.querySelector('.rel-chart text')).fill")
        assert text_fill == ("rgb(93, 104, 128)" if theme == "light" else "rgb(122, 134, 158)")
        readable(p, "main")
        no_h_scroll(p)
        shot(p, f"record-public-chart-{size['width']}-{theme}", full=True)
        p.locator("nav.view [data-view=slips]").click()
        p.wait_for_function("location.hash==='#cs2/slips' && document.querySelector('#record .scores b').textContent.startsWith('12–')")
        t = p.locator("#record").inner_text()
        for k in ("Every leg hit", "Settled slips", "slips tracked", "By size", "By platform"):
            assert k in t, k
        assert p.locator("#record .scores .v3-delta").count() == 0             # the slip view has no stated chance
        readable(p, "main")
        no_h_scroll(p)
        shot(p, f"record-public-slips-{size['width']}-{theme}", full=True)
        fail["on"] = True
        p.locator("nav[aria-label='Record sport'] button", has_text="LoL").click()
        p.wait_for_selector("#record .error #retry")
        assert "No result has been replaced with zero." in p.locator("#record .error").inner_text()
        shot(p, f"record-public-error-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_public_record_v3_follows_the_os_theme_with_nothing_stored(browser, env, monkeypatch):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    c = browser.new_context(viewport=PHONE, color_scheme="light")
    c.route("https://fonts.*/**", lambda r: r.abort())
    p = c.new_page()
    try:
        p.goto(env.url + "/record")
        p.wait_for_selector("#record h2", timeout=30000)
        assert p.evaluate("document.documentElement.dataset.theme || ''") == ""
        assert p.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(246, 247, 249)"
        assert p.evaluate("getComputedStyle(document.documentElement).colorScheme") == "light"
    finally:
        c.close()
