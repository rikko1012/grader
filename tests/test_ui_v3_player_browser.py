"""v3 PLAYER group (web/UI_V3.md): the hub player card (#hub-card / #hub-detail) in the approved v3 look, in a real browser against the
real Handler with fixture data only (no network, no production data). SCOUT_UI_V3=1, 390x844 and 1280x900, dark and light.

What it checks:
- every field the card shows today is still on screen (every Stats-tab panel, the decision strip, Lines, Ask, the star, the tabs,
  the past-series chart) with the v3 blocks added on top;
- the v3 lead (match strip with VRS rank changes, stat grid, pick summary) mirrors the hub's own numbers and never changes them;
- CS2 map cards + the 2-map combine: the pooled number, its signed gap and percentage are the shown maps' raw per-map results
  (integers) pooled exactly (half-up at display), for every pair, and picking maps never changes the pick or the strip;
- the combined bar's three wordings (0, 1, 2 maps picked): never "Pick 2 maps to combine them" when 2 are picked;
- NBA / NFL / LoL (incl. a card with no saved line history) get the lead without CS2-only blocks; no sideways scroll; no script errors.
Flag off, the card carries none of it. V3_SHOTS=<folder> keeps a screenshot per sport, size and theme."""
import itertools
import os
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction as F
from pathlib import Path

import pytest

import hltv
from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401
from tests.test_card_factors_browser import seed, sections, WANT, FLAGS
from tests.test_expansion_main_board import expansion  # noqa: F401

PHONE, DESK = {"width": 390, "height": 844}, {"width": 1280, "height": 900}
SHOTS = Path(os.environ.get("V3_SHOTS") or "")
POOL = ["Anubis", "Dust2", "Ancient", "Mirage", "Inferno"]


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    for k in FLAGS:
        monkeypatch.setenv(k, "1")


def maps_fixture():
    """Maps 1-2 series with the same totals as the shared fixture (6 of the last 10 over 38.5: the saved tally still reproduces), split
    over a real-shaped map pool so the map cards and the combine have several maps."""
    out = []
    for i in range(20):
        total = 48 if i % 5 < 3 else 30
        a = 21 + (i * 7) % 9 if total == 48 else 12 + (i * 5) % 7
        pair = (POOL[i % 5], POOL[(i + 2) % 5])
        for n, (m, v) in enumerate(zip(pair, (a, total - a)), 1):
            out.append({"match_id": f"hltv-{i}", "number": n, "date": f"2026-10-{28 - i:02d}", "kills": v, "headshots": 5 + (v + i) % 6,
                        "opponent": f"Team{i}", "map": m, "rounds": 21 + (i + n) % 6, "won": (i + n) % 2})
    return out


MAP_CHANCES = {"at": "2026-11-09T10:00:00Z", "maps": [{"name": "Anubis", "pct": 68.6}, {"name": "Dust2", "pct": 62.0}, {"name": "Ancient", "pct": 35.9}],
               "veto": None}


def setup(env, monkeypatch, theme, v3=True):
    if v3:
        monkeypatch.setenv("SCOUT_UI_V3", "1")
    monkeypatch.setattr(hltv, "recent_series", lambda *a, **k: maps_fixture())
    import mapchances
    monkeypatch.setattr(mapchances, "for_match", lambda mid: MAP_CHANCES)
    seed(env, monkeypatch)


def page(browser, env, size, theme):
    c = context(browser, env, size)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.goto(env.url + "/#all-sports")
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    return c, p


def shot(p, name, full=False):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(300)
        p.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def card_shots(p, name):
    """the card top, then each scroll step of the card's scroller, so a long card is seen end to end"""
    if not SHOTS.parts:
        return
    shot(p, name + "-a")
    sc = p.evaluate("""() => { const d = document.getElementById('hub-detail'), b = document.getElementById('hub-card-body');
      const s = [d, b].find(e => e && e.scrollHeight > e.clientHeight + 4); if (!s) return 0; s.dataset.v3shot = '1'; return Math.ceil(s.scrollHeight / s.clientHeight); }""")
    for i in range(1, min(sc, 6)):
        p.evaluate(f"(() => {{ const s = document.querySelector('[data-v3shot]'); s.scrollTop = s.clientHeight * {i} * 0.9; }})()")
        shot(p, f"{name}-{chr(97 + i)}")
    p.evaluate("(() => { const s = document.querySelector('[data-v3shot]'); if (s) { s.scrollTop = 0; delete s.dataset.v3shot; } })()")


def search(p, text):
    """Type into the hub's search box. On the phone the v3 board keeps it behind the header's search button (group A), so open it
    the way a member does first."""
    q = p.locator('#hub-q')
    if not q.is_visible():
        if not text and not q.input_value():
            return                                   # hidden box is empty already (the board keeps it open while it has text)
        p.locator(".sc-v3-bar [data-v3=search]").click()
        p.wait_for_function("document.activeElement && document.activeElement.id==='hub-q'")
    q.fill(text)



def open_card(p, sport, q):
    """tests/test_card_factors_browser.open_card, with the search typed through search() (the phone box sits behind the header's
    search button under the v3 board)."""
    p.locator(f'#hub-sports [data-id={sport}]').click()
    p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].length && [...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('{sport}|'))")
    search(p, q)
    p.wait_for_timeout(300)
    p.locator('#hub-rows tr[data-record] .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-why', timeout=20000)
    p.wait_for_function("!document.querySelector('#hub-detail .h-detail-loading')", timeout=20000)


def close(p):
    p.locator('#hub-detail [data-hub=card-close]').first.click()
    p.wait_for_function("document.getElementById('hub-card').hidden")
    search(p, '')
    p.wait_for_timeout(200)


def vis(p, sel):
    return p.locator(sel).first.is_visible()


def tab(p, name):
    p.locator(f'#hub-detail [role=tab][data-hub=card-tab][data-id={name}]').click()
    p.wait_for_function(f"document.querySelector('#hub-detail').dataset.tab === '{name}' && !document.querySelector('#hub-detail .h-detail-loading')", timeout=20000)
    p.wait_for_timeout(150)


def no_hedge(p):
    text = p.locator("#hub-detail").inner_text().lower()
    for w in ("questionable", "day-to-day", "unconfirmed", "uncertain", "undefined", "nan%", "null"):
        assert w not in text, w


def check_card(p, sport, size, theme):
    """every field the card shows today, visible, plus the v3 blocks; tabs walked; shots kept"""
    name = f"{sport}-{size['width']}-{theme}"
    p.wait_for_selector("#hub-detail > .v3p-lead", timeout=15000)
    # the hub's own nodes: title, star, decision strip (line, sides, chance), tabs, Why, the past-series chart
    for sel in ("#hub-card-title", "#hub-detail .h-card-head .h-star", "#hub-decision", "#hub-card-line", "#hub-decision .h-side-toggle",
                "#hub-decision .h-dec-chance", "#hub-detail .h-card-tabs", "#hub-card-body .h-why"):
        assert vis(p, sel), sel
    assert p.locator("#hub-detail .h-card-tabs [role=tab]").all_inner_texts() == ["Overview", "Lines", "Stats", "Ask"]
    if p.locator("#hub-card-body [data-hub-chart]").count():
        assert vis(p, "#hub-card-body [data-hub-chart]")
    assert p.locator("#hub-detail > .v3p-lead").count() == 1
    assert vis(p, "#hub-detail .v3p-match")
    # header: the avatar centred on the name block; one absolute time on the card, with its zone
    box = p.evaluate("""(() => { const h = document.querySelector('#hub-detail .h-card-head'), a = h.querySelector(':scope > .u-face, :scope > .v3p-avatar'),
      n = h.querySelector(':scope > .h-card-id'); if (!a) return null; const ra = a.getBoundingClientRect(), rn = n.getBoundingClientRect();
      return [ra.top + ra.height / 2, rn.top + rn.height / 2, rn.height]; })()""")
    if box:
        assert abs(box[0] - box[1]) <= max(6, box[2] / 4), box
    # the hub's head line (time, countdown pill) is kept for screen readers only; on screen the time sits once, in the meta row
    assert p.evaluate("[...document.querySelectorAll('#hub-detail .h-card-head .h-countdown')].every(e => { const r = e.getBoundingClientRect(); return r.width <= 1 || r.height <= 1 || !e.checkVisibility || !e.checkVisibility() || e.closest('p').getBoundingClientRect().width <= 1; })")
    who = p.locator("#hub-detail .h-card-head .v3p-who")
    if who.count():
        assert who.inner_text().strip()
    meta = p.locator("#hub-detail .v3p-meta").inner_text()
    import re as _re
    assert _re.search(r"\d:\d\d\s?(AM|PM)?\s?[A-Z]{2,5}|GMT|UTC", meta), meta
    # the line panel: the big line, the book's freshness, Projection and Gap to line, L5 / L10 / L20 on the side shown
    line = p.locator("#hub-card-line option:checked").inner_text()
    assert p.locator("#hub-decision .v3p-lh b").inner_text() in line
    assert " checked " in p.locator("#hub-decision .v3p-lmeta").inner_text()
    facts = p.locator("#hub-decision .v3p-facts").inner_text()
    assert "Projection" in facts and "Gap to line" in facts, facts
    hits = p.locator("#hub-decision .v3p-hits").inner_text()
    assert "L5, " in hits and "L10, " in hits and "L20, " in hits, hits
    # the pick summary mirrors the strip (never recomputed)
    if p.locator("#hub-detail .v3p-pick").count():
        assert p.locator(".v3p-pick .sc-pick__chance b").inner_text() == p.locator("#hub-decision .h-dec-chance > b").inner_text()
        side = p.locator("#hub-decision .h-side-toggle [aria-pressed=true]").inner_text().strip()
        assert p.locator(".v3p-pick .sc-pick__side b").inner_text().startswith(side)
        assert "Model estimate" in p.locator(".v3p-pick").inner_text()
        assert " checked " in p.locator(".v3p-pick .sc-book").inner_text()
    # source notes readable in both themes: 4.5:1 or better against the surface they sit on
    low = p.evaluate("""[...document.querySelectorAll('#hub-detail .sc-src, #hub-detail .sc-statgrid__legend')].map(e => {
      const rgb = s => s.match(/[\d.]+/g).slice(0, 3).map(Number), lum = c => { const v = c.map(x => { x /= 255; return x <= .03928 ? x / 12.92 : Math.pow((x + .055) / 1.055, 2.4); });
        return .2126 * v[0] + .7152 * v[1] + .0722 * v[2]; };
      let b = e, bg; while (b && ((bg = getComputedStyle(b).backgroundColor) === 'rgba(0, 0, 0, 0)' || bg.startsWith('rgba') && bg.endsWith(', 0)'))) b = b.parentElement;
      if (!b) return null; const a = lum(rgb(getComputedStyle(e).color)), z = lum(rgb(bg)), r = (Math.max(a, z) + .05) / (Math.min(a, z) + .05);
      return r < 4.5 ? [e.className, r] : null; }).filter(Boolean)""")
    if theme == "light":
        assert not low, low
    if sport == "cs2":
        m = p.locator("#hub-detail .v3p-match").inner_text()
        assert "VRS #14" in m and "▲3" in m and "VRS #21" in m and "▼2" in m      # rank changes restored
        assert p.locator("#hub-detail .v3p-map").count() >= 2
        assert "Kills/map" in p.locator(".v3p-grid").inner_text()
    else:
        assert p.locator("#hub-detail .v3p-map").count() == 0
    if size is PHONE:
        # phone: the combined bar waits until the card's own Add has scrolled away, so it never sits over the pick summary
        assert not vis(p, "#hub-detail > .v3p-dock")
        card_shots(p, name)
        p.evaluate("(() => { const d = document.getElementById('hub-detail'), a = document.querySelector('#hub-decision'); d.scrollTop = a.offsetTop + a.offsetHeight + 40; })()")
        p.wait_for_function("document.getElementById('hub-detail').classList.contains('v3p-dock-on')", timeout=5000)
        assert vis(p, "#hub-detail > .v3p-dock")
        p.evaluate("document.getElementById('hub-detail').scrollTop = 0")
    else:
        assert vis(p, "#hub-detail > .v3p-dock")
        card_shots(p, name)
    tab(p, "stats")
    want = WANT[sport][1]
    try:
        p.wait_for_function(f"(w => w.every(x => document.querySelector('#hub-card-body .h-fx[data-fx=\"' + x + '\"]')))({want!r})", timeout=10000)
    except Exception:
        pass
    got = sections(p)
    for fx in WANT[sport][1]:
        assert fx in got, (fx, got)
        assert p.locator(f'#hub-card-body .h-fx[data-fx="{fx}"]').is_visible(), fx
    assert p.locator("#hub-detail > .v3p-lead").count() == 1                   # survived the repaint, once
    p.evaluate("(() => { const t = document.querySelector('#hub-detail > .h-card-tabs'); document.getElementById('hub-detail').scrollTop = t.offsetTop; })()")
    shot(p, name + "-stats")
    tab(p, "lines")
    assert vis(p, "#hub-card-body .h-line-shop")
    p.evaluate("(() => { const t = document.querySelector('#hub-detail > .h-card-tabs'); document.getElementById('hub-detail').scrollTop = t.offsetTop; })()")
    shot(p, name + "-lines")
    tab(p, "ask")
    assert p.locator("#hub-card-body").inner_text().strip()
    tab(p, "overview")
    no_hedge(p)
    no_h_scroll(p)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_card_keeps_every_field_for_every_sport(browser, env, monkeypatch, size, theme):
    setup(env, monkeypatch, theme)
    c, p = page(browser, env, size, theme)
    try:
        assert p.evaluate("document.documentElement.dataset.uiV3") == "1"
        for sport, (q, _) in WANT.items():
            open_card(p, sport, q)
            check_card(p, sport, size, theme)
            close(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def rd(x, dp):
    return str((Decimal(x.numerator) / Decimal(x.denominator)).quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP))


def sg(d, dp):
    return (("+" if d > 0 else "−") + rd(abs(d), dp)) if float(rd(abs(d), dp)) else rd(abs(d), dp)


def combo_text(p):
    return p.locator("#hub-detail .v3p-dock [data-v3p-combo]").inner_text()


def pressed(p):
    return p.evaluate("[...document.querySelectorAll('#hub-detail .v3p-map[aria-pressed=true]')].map(b => b.dataset.v3pMap)")


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_two_map_combine_pools_exactly_and_never_moves_the_pick(browser, env, monkeypatch, size):
    setup(env, monkeypatch, "dark")
    c, p = page(browser, env, size, "dark")
    try:
        open_card(p, "cs2", "vargo")
        p.wait_for_selector("#hub-detail .v3p-map")
        tot = {}                                   # the per-map raw results the page pools (all 20 fixture series have both maps)
        for m in maps_fixture():
            k = m["map"].lower()
            s_, n_ = tot.get(k, (0, 0))
            tot[k] = (s_ + m["kills"], n_ + 1)
        pace = F(77, 2) / 2                        # line 38.5 over Maps 1-2
        fixed = p.locator("#hub-decision").inner_text() + "|" + p.locator(".v3p-pick").inner_text()
        # default: the two likeliest maps are picked, and the bar never asks for 2 maps when 2 are picked
        assert len(pressed(p)) == 2
        assert "Pick 2 maps to combine them" not in combo_text(p)
        assert "Pick 2" not in p.locator("#hub-detail .v3p-maps-head .sc-muted").inner_text()                  # the Maps heading follows the picks too
        cards = p.evaluate("[...document.querySelectorAll('#hub-detail .v3p-map')].map(b => b.dataset.v3pMap)")
        n = 0
        for a, b in itertools.combinations(cards, 2):
            p.locator("#hub-detail [data-v3p=clear]").click()
            assert combo_text(p) == "Pick 2 maps to combine them"
            assert p.locator("#hub-detail .v3p-pooled-ask").inner_text() == "Pick 2 maps to combine them"
            assert p.locator("#hub-detail .v3p-maps-head .sc-muted").inner_text() == "Pick 2 to combine"
            p.locator(f'#hub-detail .v3p-map[data-v3p-map="{a}"]').click()
            assert "pick 1 more map to combine" in combo_text(p)
            assert p.locator("#hub-detail .v3p-maps-head .sc-muted").inner_text() == "1 of 2 picked"
            p.locator(f'#hub-detail .v3p-map[data-v3p-map="{b}"]').click()
            v = F(tot[a][0] + tot[b][0], tot[a][1] + tot[b][1])
            d = v - pace
            big = p.locator("#hub-detail [data-v3p-big]").inner_text()
            lead = p.locator("#hub-detail [data-v3p-lead]").inner_text()
            assert big == rd(v, 2), (a, b, big, rd(v, 2))
            assert sg(d, 2) in lead and sg(100 * d / pace, 1) + "%" in lead and rd(pace, 2) in lead, (a, b, lead)
            t = combo_text(p)
            assert "Pick 2 maps to combine them" not in t and rd(v, 2) in t and sg(d, 2) in t
            assert "Pick 2" not in p.locator("#hub-detail .v3p-maps-head .sc-muted").inner_text() and p.locator("#hub-detail .v3p-maps-head .sc-muted").inner_text().startswith("2 maps combined")
            assert p.locator(f'#hub-detail .v3p-map[data-v3p-map="{a}"] .sc-badge').inner_text() == "1"
            assert p.locator(f'#hub-detail .v3p-map[data-v3p-map="{b}"] .sc-badge').inner_text() == "2"
            assert p.locator("#hub-decision").inner_text() + "|" + p.locator(".v3p-pick").inner_text() == fixed
            n += 1
        assert n == len(cards) * (len(cards) - 1) // 2 and n >= 6
        # a third pick replaces the oldest; the picks survive the hub's repaints (side, tab)
        p.locator("#hub-detail [data-v3p=clear]").click()
        for k in cards[:3]:
            p.locator(f'#hub-detail .v3p-map[data-v3p-map="{k}"]').click()
        assert pressed(p) == cards[1:3]
        p.locator('#hub-decision [data-hub=history-side][data-id=under]').click()
        p.wait_for_function("document.querySelector('#hub-detail .v3p-pick .sc-pick__side b').textContent.startsWith('Under')")
        assert "Scout" not in p.locator(".v3p-pick .sc-pick__side").inner_text()          # Scout's side is Over: no Scout tag on Under
        assert p.locator(".v3p-pick .sc-pick__chance b").inner_text() == p.locator("#hub-decision .h-dec-chance > b").inner_text()
        tab(p, "stats")
        tab(p, "overview")
        assert pressed(p) == cards[1:3]
        assert p.locator("#hub-detail > .v3p-maps").count() == 1
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_one_confirmed_map_is_always_in_the_default_combine(browser, env, monkeypatch, size):
    """Map 1 confirmed (Mirage, Halcyon pick) and Map 2 still open: the default combine is Mirage + the likeliest Map 2 by Scout's
    simulated veto (Inferno 41%), never two other maps. Each card says how likely it is to be played in Maps 1-2 (Mirage 100%)."""
    setup(env, monkeypatch, "dark")
    c, p = page(browser, env, size, "dark")
    try:
        open_card(p, "cs2", "vargo")
        p.wait_for_selector("#hub-detail .v3p-map")
        assert pressed(p) == ["mirage", "inferno"], pressed(p)
        t = combo_text(p)
        assert t.startswith("Mirage + Inferno: "), t
        assert "In Maps 1-2 100.0%" in p.locator('#hub-detail .v3p-map[data-v3p-map="mirage"]').inner_text().replace("\n", " ")
        assert "In Maps 1-2 41.0%" in p.locator('#hub-detail .v3p-map[data-v3p-map="inferno"]').inner_text().replace("\n", " ")
        assert "Map chance" in p.locator('#hub-detail .v3p-map[data-v3p-map="anubis"]').inner_text().replace("\n", " ")   # no exact veto share: HLTV's
        slot = p.locator('#hub-detail .v3p-map[data-v3p-map="mirage"] .v3p-slot')
        assert slot.inner_text() == "Map 1 · Halcyon pick"
        assert p.evaluate("(e => e.getClientRects().length)(document.querySelector('#hub-detail .v3p-map[data-v3p-map=\"mirage\"] .v3p-slot'))") == 1
        # no stat column of dashes: a map shows only the stats it has
        assert "—" not in p.locator("#hub-detail .v3p-maps-list").inner_text()
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE], ids=["390"])
def test_v3_confirmed_map_with_no_saved_result_is_named_not_left_out(browser, env, monkeypatch, size):
    """Map 1 confirmed on a map he has no saved Maps 1-2 result on: no default combine (it would leave that map out), and the card says so."""
    setup(env, monkeypatch, "dark")
    import veto_serve
    from tests.card_factors_fixture import VETO
    monkeypatch.setattr(veto_serve, "for_match", lambda mid: dict(VETO, confirmed=[{"slot": "Map 1", "name": "Nuke", "by": "Halcyon"}]))
    c, p = page(browser, env, size, "dark")
    try:
        open_card(p, "cs2", "vargo")
        p.wait_for_selector("#hub-detail .v3p-map")
        assert pressed(p) == []
        assert "Nuke is confirmed for Maps 1-2" in p.locator("#hub-detail .v3p-miss").inner_text()
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_lol_card_with_no_saved_results_still_shows_every_field(browser, env, monkeypatch, size):
    """the LoL card with no game log for this line (the mock's no-line state): no stat grid, no hit strip, nothing made up"""
    setup(env, monkeypatch, "dark")
    c, p = page(browser, env, size, "dark")
    import json as _json

    def strip_history(route):
        resp = route.fetch()
        doc = resp.json()
        doc["history"] = {"tally": None, "avg": None, "series": []}
        route.fulfill(response=resp, body=_json.dumps(doc), headers={**resp.headers, "content-type": "application/json"})
    p.route("**/api/upgrade/line?sport=lol*", strip_history)
    try:
        p.locator('#hub-sports [data-id=lol]').click()
        search(p, "Kratos")
        p.wait_for_timeout(300)
        p.locator('#hub-rows tr[data-record] .h-player').first.click()
        p.wait_for_selector("#hub-detail > .v3p-lead", timeout=20000)
        p.wait_for_function("document.querySelector('#hub-card-body').textContent.includes('Game-by-game results are not available')", timeout=20000)
        for sel in ("#hub-card-title", "#hub-decision", "#hub-detail .h-card-tabs", "#hub-detail .v3p-match", "#hub-detail .v3p-pick"):
            assert vis(p, sel), sel
        assert p.locator("#hub-detail .v3p-map").count() == 0 and p.locator("#hub-detail .v3p-grid").count() == 0
        assert p.locator("#hub-detail .v3p-pick .sc-spark").count() == 0
        txt = p.locator("#hub-detail").inner_text()
        assert "NaN" not in txt and "undefined" not in txt
        card_shots(p, f"lol-noresults-{size['width']}-dark")
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_soccer_and_tennis_cards(browser, env, expansion, monkeypatch, size, theme):  # noqa: F811
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    c = context(browser, env, size)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    try:
        p.goto(env.url + "/#scout/board?sport=soccer")
        for sport, label in (("soccer", "Shots"), ("tennis", "Aces")):
            if sport == "tennis":
                p.locator('#hub-sports [data-id="tennis"]').click()
                p.wait_for_function("document.querySelector('#hub-rows').textContent.includes('Aces')")
            p.wait_for_selector("#hub-rows .h-player", timeout=30000)
            p.locator("#hub-rows .h-player").first.click()
            p.wait_for_selector("#hub-detail .h-history-panel", timeout=20000)
            p.wait_for_selector("#hub-detail > .v3p-lead", timeout=20000)
            assert p.evaluate("document.documentElement.dataset.scSport") == sport
            for sel in ("#hub-card-title", "#hub-decision", "#hub-detail .h-card-tabs", "#hub-detail .v3p-match", "#hub-card-body .h-history-panel"):
                assert vis(p, sel), sel
            assert "Opponent 0" in p.locator("#hub-detail").inner_text()                # the game log stays
            assert p.locator("#hub-detail [data-hub=detail-add]").count() == 0           # view-only stays view-only
            assert p.locator("#hub-detail .v3p-map").count() == 0
            assert "Games on file" in p.locator("#hub-detail .v3p-grid").inner_text()
            card_shots(p, f"{sport}-{size['width']}-{theme}")
            tab(p, "stats")
            assert ("Minutes" if sport == "soccer" else "Double Faults") in p.locator("#hub-detail").inner_text()
            no_h_scroll(p)
            p.locator('#hub-detail [data-hub=card-close]').first.click()
            p.wait_for_function("document.getElementById('hub-card').hidden")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_flag_off_card_has_none_of_it(browser, env, monkeypatch):
    setup(env, monkeypatch, "dark", v3=False)
    c, p = page(browser, env, PHONE, "dark")
    try:
        open_card(p, "cs2", "vargo")
        assert p.evaluate("typeof window.ScoutV3") == "undefined"
        assert p.locator("[data-v3p-block]").count() == 0
        assert p.evaluate("document.documentElement.dataset.uiV3") is None
    finally:
        c.close()

