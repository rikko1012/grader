"""Browser checks of the Prop board table (board agent). Same preview as test_ui_browser.py:

    SPORTS_UI_BROWSER_URL=http://localhost:8040 python -m pytest sports/tests/test_ui_table_browser.py -q

Sort (headers + phone select, nulls last), lean never contradicts the shown Scout chances / projection, edge = Scout
minus market of the lean side, L10 counts = the player's history API, one group per player with expandable stats,
details toggle, the Cards toggle saved per device, 360 px without page overflow."""
import json
import os

import pytest

from sports.tests.test_ui_browser import URL, _no_overflow, _page, pw, shots  # noqa: F401

pytestmark = pytest.mark.skipif(not URL, reason="SPORTS_UI_BROWSER_URL not set (needs the running dev preview)")
LGS = [os.environ.get("SPORTS_UI_BROWSER_LG")] if os.environ.get("SPORTS_UI_BROWSER_LG") else ["nba", "nfl"]

ROWS_JS = """() => [...document.querySelectorAll('.sw-tb-row:not([hidden])')].map(tr => {
  const t = s => { const e = tr.querySelector(s); return e ? e.innerText.trim() : null; };
  const lean = tr.querySelector('[data-lean]');
  return {key: tr.dataset.key, rid: tr.dataset.rid, group: tr.closest('tbody').dataset.group, lean: lean ? lean.dataset.lean : null,
          scout: t('.sw-tb-scout'), mkt: t('.sw-tb-mkt'), edge: tr.querySelector('[data-edge]').dataset.edge,
          hit: tr.querySelector('[data-hit]').dataset.hit, line: parseFloat(t('.sw-tb-line'))};
})"""


def _open(page, shots_dir=None, width=1280):
    for lg in LGS:
        page.goto(URL + f"/sports#/board?lg={lg}")
        page.wait_for_selector(".sw-tb, .sw-state", timeout=30000)
        if page.locator(".sw-tb-row").count():
            return lg
    pytest.skip("no open props on the preview board")


def test_table_is_default_and_rows_follow_the_rules(pw, shots):
    ctx, page, errors = _page(pw, view="table")
    lg = _open(page)
    page.screenshot(path=str(shots / f"table_{lg}_1280.png"))
    rows = page.evaluate(ROWS_JS)
    assert rows and not errors, errors
    for r in rows:
        if not r["scout"] or r["scout"] == "—":
            assert r["lean"] is None
            continue
        parts = r["scout"].replace("%", "").split()
        mu = None if parts[0] == "—" else float(parts[0])
        po, pu = float(parts[parts.index("O") + 1]), float(parts[parts.index("U") + 1])
        if r["lean"] == "over":                                         # never a side the distribution does not support
            assert po >= 50 and (mu is None or mu >= r["line"]), r
        if r["lean"] == "under":
            assert pu >= 50 and (mu is None or mu <= r["line"]), r
        if r["edge"]:
            m = float(r["mkt"].split()[1].replace("%", "").replace("<", "").replace(">", ""))
            side = po if r["lean"] == "over" else pu
            assert abs(float(r["edge"]) - (side - m)) <= 1.01, r                  # whole-number display rounding
    groups = [r["group"] for r in rows]
    assert len(groups) == len(set(groups))                                  # collapsed: one row per player
    ctx.close()


def test_sort_headers_and_nulls_last(pw, shots):
    ctx, page, errors = _page(pw, view="table")
    _open(page)
    for key in ("scout", "l10", "player"):
        page.click(f'[data-tsort="{key}"]')
        page.wait_for_timeout(150)
        assert page.locator(f'th[aria-sort] [data-tsort="{key}"]').count() == 1
        vals = page.evaluate(ROWS_JS)
        if key == "player":
            names = page.evaluate("() => [...document.querySelectorAll('.sw-tb-name')].map(a => a.innerText.trim().toLowerCase())")
            assert names == sorted(names)
    page.click('[data-tsort="player"]')                                      # second click flips the direction
    page.wait_for_timeout(150)
    names = page.evaluate("() => [...document.querySelectorAll('.sw-tb-name')].map(a => a.innerText.trim().toLowerCase())")
    assert names == sorted(names, reverse=True)
    page.click('[data-tsort="edge"]')
    page.wait_for_timeout(150)
    edges = [r["edge"] for r in page.evaluate(ROWS_JS)]
    filled = [float(e) for e in edges if e]
    assert filled == sorted(filled, reverse=True) and edges[:len(filled)] == [e for e in edges if e]   # nulls last
    page.reload(); page.wait_for_selector(".sw-tb")
    assert page.locator('th[aria-sort] [data-tsort="edge"]').count() == 1       # sort saved on the device
    assert not errors, errors
    ctx.close()


def test_group_expand_hit_counts_and_cards_toggle(pw, shots):
    ctx, page, errors = _page(pw, view="table")
    lg = _open(page)
    exp = page.locator(".sw-tb-exp").first
    if exp.count():
        g = exp.get_attribute("data-tgroup")
        before = page.locator(f'tbody[data-group="{g}"] .sw-tb-row:not([hidden])').count()
        exp.click(); page.wait_for_timeout(150)
        after = page.locator(f'tbody[data-group="{g}"] .sw-tb-row:not([hidden])').count()
        assert before == 1 and after > 1
        assert page.locator(f'[data-tgroup="{g}"]').get_attribute("aria-expanded") == "true"
    # L10 = the player's own history (strict, pushes apart)
    rows = [r for r in page.evaluate(ROWS_JS) if r["hit"]][:5]
    for r in rows:
        pid, stat = r["key"].split("|")[1], r["key"].split("|")[2]
        got = page.evaluate("""async ([lg, pid, stat, ln]) => { const a = window.ScoutSportsApp.api;
          const h = (await a.get('/api/sports/history', {league: lg, id: pid})).data.games;
          const w = window.SportsStats.windowSummary(h, stat, ln, 10); return [w.over, w.under, w.push, w.n].join(','); }""",
                            [lg, pid, stat, r["line"]])
        assert got == r["hit"], (r, got)
    page.click('[data-tview="cards"]'); page.wait_for_selector(".sw-research-grid")
    page.reload(); page.wait_for_selector(".sw-research-grid, .sw-state")
    assert page.locator('[data-tview="cards"][aria-pressed="true"]').count() == 1   # saved per device
    assert not errors, errors
    ctx.close()


def test_phone_360_fits(pw, shots):
    ctx, page, errors = _page(pw, width=360, height=780, view="table")
    lg = _open(page)
    assert _no_overflow(page)
    assert page.locator("#sw-tb-sortsel").is_visible()
    page.select_option("#sw-tb-sortsel", "player"); page.wait_for_timeout(150)
    assert _no_overflow(page)
    h = page.evaluate("() => { const g = document.querySelector('.sw-tb-group'); return g ? g.getBoundingClientRect().height : 0; }")
    assert 0 < h < 260, h                                                     # compact rows, not 700 px cards
    page.screenshot(path=str(shots / f"table_{lg}_360.png"), full_page=False)
    assert not errors, errors
    ctx.close()


def test_cards_grouped_details_toggle_one_stat_filter_show_first(pw, shots):
    ctx, page, errors = _page(pw, view="cards")
    for lg in LGS:
        page.goto(URL + f"/sports#/board?lg={lg}")
        page.wait_for_selector(".sw-research-card, .sw-state", timeout=30000)
        if page.locator(".sw-research-card").count():
            break
    else:
        pytest.skip("no open props on the preview board")
    page.screenshot(path=str(shots / f"cards_{lg}_1280.png"))
    info = page.evaluate("""() => {
      const cards = [...document.querySelectorAll('.sw-research-card')];
      const ids = cards.map(c => c.querySelector('a.sw-player-name').getAttribute('href').split('?')[0]);
      const visible = el => !!el.getClientRects().length && !el.closest('details:not([open])');
      const txt = [...document.querySelectorAll('#sw-bbody *')].filter(e => visible(e) && e.children.length === 0).map(e => e.textContent).join(' ');
      return {n: cards.length, unique: new Set(ids).size, validTop: document.querySelectorAll('#sw-validation').length,
              researchOnCards: /forward validation/.test(txt), versionOnCards: /\bsm-1\b|\bsharp-2\b|offer:/.test(txt),
              tech: document.querySelectorAll('#sw-bbody details.sw-tech').length,
              statLabels: [...document.querySelectorAll('#sw-bf label, #sw-bf .sw-fx-lab')].filter(l => /^Stat$/i.test(l.textContent.trim())).length,
              showFirst: [...document.querySelectorAll('#sw-prefbook option')].map(o => o.value).filter(Boolean)};
    }""")
    assert info["n"] == info["unique"], info                                  # one card per player
    assert info["validTop"] <= 1 and not info["researchOnCards"] and not info["versionOnCards"], info
    assert info["tech"] > 0 and info["statLabels"] == 1, info                 # details toggle; one Stat filter
    priced = page.evaluate("""async (lg) => { const b = (await window.ScoutSportsApp.api.get('/api/sports/board', {league: lg})).data;
      return window.SportsFilters.pricedBooks(b.markets); }""", lg)
    assert sorted(info["showFirst"]) == sorted(priced)                       # Kalshi and every priced book
    d = page.locator("#sw-bbody details.sw-tech").first
    d.locator("summary").click()
    assert d.get_attribute("open") is not None
    assert not errors, errors
    ctx.close()
