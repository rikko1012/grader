"""Owner site audit 2026-11-06 (5:40-6:05 PM CST), Slips and Tools items: regression tests for each one that reproduced on
origin/main 2f96bab. T1 (break-even) and T5 (no return figure on NBA / NFL legs with no book price) are covered in
tests/test_slip_quality.py, tests/test_slip_generate_api.py and tests/test_pickem_ev.py; this file holds T2-T4, T6 and T8-T11.
Server checks call the real routes; browser checks use the real Handler with the fixtures of test_upgrade_browser (no network,
no production data). Every player, team and number is a TEST FIXTURE."""
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

import server
import workspace_api
import workspace_markets as wm
from tests.test_phase4_markets import snap
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401
from tests.test_pickem_ev_browser import ev_on, ev_page  # noqa: F401
from tests.test_hub_shell_browser import market_doc, mt_page

SECRET = "t" * 40


# ------------------------------------------------------------------------------------------------ T8 grading comparison (owner)

class _H:
    def __init__(self):
        self.headers, self.sent = {}, None

    def authed(self, token):
        return token == "owner-token"

    def _sports_owner(self):
        return False

    def send(self, code, body):
        self.sent = (code, body)


def _shadow(path, sport, player, event):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS gradecore_shadow (id TEXT PRIMARY KEY, sport TEXT NOT NULL, forecast_id TEXT NOT NULL, "
               "forecast_at TEXT NOT NULL, captured_at TEXT NOT NULL, payload TEXT NOT NULL)")
    body = {"forecast_id": "f1", "forecast_at": "2026-11-06T20:00:00+00:00", "old_letter": "B", "old_p": 0.61, "artifact_hash": "x",
            "offer": {"event_id": event, "player": player, "stat": "kills", "scope": "maps12", "line": 7.5, "book": "prizepicks"},
            "candidate": {"sides": {"over": {"p": 0.6}, "under": {"p": 0.4}}, "policy": "shadow"}}
    db.execute("INSERT INTO gradecore_shadow VALUES (?,?,?,?,?,?)", ("k-" + sport + player, sport, "f1", body["forecast_at"],
                                                                     "2026-11-06T20:00:01+00:00", json.dumps(body)))
    db.commit()
    db.close()


@pytest.mark.parametrize("league", ["nba", "nfl"])
def test_t8_nba_nfl_comparison_reaches_the_sports_admin_route(tmp_path, monkeypatch, league):
    """Audit: NBA / NFL showed "Comparison unavailable" on every retry. The request carried X-Admin-Token, which the sports
    service accepts on /api/sports/admin/ops only; every other admin route needs an st1 token of role admin. The fake opener
    below applies the service's own check (sports/service.py) and route table (sports/web.dispatch)."""
    from sports import authtoken, config, web
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("SPORTS_API_BASE", "https://sports.example.test")
    monkeypatch.setenv("ADMIN_TOKEN", "owner-token")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _shadow(tmp_path / "canonical.db", league, "Fixture Guard", "e-1")
    seen = []

    class Resp:
        def __init__(self, raw):
            self.raw = raw

        def read(self, n=-1):
            return self.raw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Opener:
        def open(self, req, timeout=None):
            import urllib.error
            from urllib.parse import parse_qs, urlparse
            seen.append(req)
            tok = authtoken.bearer(req.get_header("Authorization"))
            claims = None
            if tok:
                try:
                    claims = authtoken.verify(tok)
                except authtoken.TokenError:
                    claims = None
            if claims is None:
                raise urllib.error.HTTPError(req.full_url, 401, "members_only", {}, None)
            u = urlparse(req.full_url)
            code, data, _ = web.dispatch(web.Request(path=u.path, query={k: v[0] for k, v in parse_qs(u.query).items()},
                                                     method="GET", body=b"", member=claims["sub"], role=claims["role"]))
            if code != 200:
                raise urllib.error.HTTPError(req.full_url, code, "refused", {}, None)
            return Resp(data)

    import urllib.request
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    h = _H()
    workspace_api.get_grade_review(h, SimpleNamespace(sports_admin_token=server.sports_admin_token), {"sport": league, "token": "owner-token"}, None, None, "grade-review")
    code, doc = h.sent
    assert code == 200, doc
    assert doc["sport"] == league and [r["player"] for r in doc["rows"]] == ["Fixture Guard"]
    assert seen and seen[0].get_header("X-admin-token") is None                       # the admin secret is never forwarded
    assert authtoken.verify(authtoken.bearer(seen[0].get_header("Authorization")))["role"] == "admin"


def test_t8_grade_review_still_refuses_members(tmp_path, monkeypatch):
    h = _H()
    workspace_api.get_grade_review(h, SimpleNamespace(), {"sport": "nba"}, None, None, "grade-review")
    assert h.sent[0] == 404


def test_t8_lol_comparison_rows_show_names_not_ids(tmp_path):
    center = tmp_path / "lol"
    center.mkdir()
    db = center / "lol.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE entities (eid TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT, created_at TEXT NOT NULL)")
    con.execute("CREATE TABLE c_series (sid TEXT PRIMARY KEY, team1_name TEXT, team2_name TEXT)")
    con.execute("INSERT INTO entities VALUES ('lol:player:000396', 'player', 'Fixture Mid', '2026-01-01')")
    con.execute("INSERT INTO c_series VALUES ('lol:series:000077', 'Alpha Esports', 'Bravo Gaming')")
    con.commit()
    con.close()
    _shadow(db, "lol", "lol:player:000396", "lol:series:000077")
    _shadow(db, "lol", "lol:player:000999", "lol:series:000999")                       # no saved name: left as it is
    h = _H()
    workspace_api.get_grade_review(h, SimpleNamespace(LOL_CENTER=str(center)), {"sport": "lol", "token": "owner-token"}, None, None, "grade-review")
    code, doc = h.sent
    assert code == 200
    by = {r["player"]: r for r in doc["rows"]}
    assert "Fixture Mid" in by and by["Fixture Mid"]["event"] == "Alpha Esports vs Bravo Gaming"
    assert "lol:player:000396" not in json.dumps(doc)
    assert by["lol:player:000999"]["event"] == "lol:series:000999"


# ------------------------------------------------------------------------------------------------ T6 Price check: closest prices

@pytest.fixture
def no_value(monkeypatch):
    import book_providers as bp
    import workspace_books as wb
    bp.reset()
    wb.reset()
    now = time.time()
    same = [("fanduel", 1.87, 1.95, 30), ("betrivers", 1.87, 1.95, 40), ("oddsapi:draftkings", 1.85, 1.97, 20)]
    snaps = {"nba": snap([("p1", 20.5, same), ("p2", 8.5, [("fanduel", 1.80, 2.02, 30), ("betrivers", 1.83, 1.99, 50)])], now)}
    monkeypatch.setattr(wb, "snap", lambda srv, sport: snaps.get(sport))
    monkeypatch.setattr(wm, "model_released", lambda sport, stat: False)
    return now


def test_t6_sheet_lists_the_closest_prices_when_nothing_beats_its_fair_chance(no_value):
    """Audit: Price check said "Nothing worth acting on" for every filter. With no price above its fair chance the sheet now
    carries the priced single bets that come closest, best first; none of them is counted as worth acting on."""
    s = wm.sheet(None, "nba", no_value)
    assert s["ev"] == [] and s["quotes_checked"] > 0
    near = s["closest"]
    assert near and len(near) <= wm.CLOSEST_ROWS
    assert all(r["ev"] <= 0 and r["per_100"] <= 0 for r in near)
    assert [r["ev"] for r in near] == sorted((r["ev"] for r in near), reverse=True)
    assert all(r["kind"] in ("sportsbook", "prediction") for r in near)
    body = json.dumps({"as_of": "x", "sports": {"nba": s}, "books": [], "method": {}}).encode()
    m = wm.for_member(body, {"nba": True})
    assert m["sports"]["nba"]["closest"] and all("p_source" not in r for r in m["sports"]["nba"]["closest"])


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_t6_price_check_shows_a_ranked_closest_list_instead_of_only_the_empty_line(browser, env, monkeypatch, size):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    doc = market_doc(empty=True)
    now = time.time()
    base = {k: v for k, v in market_doc()["sports"]["nba"]["ev"][0].items()}
    doc["sports"]["nba"]["closest"] = [dict(base, player="Nba Near", ev=-0.012, per_100=-1.2, p_cal=.47, age_s=30),
                                       dict(base, player="Nba Far", ev=-0.041, per_100=-4.1, p_cal=.45, age_s=30)]
    c = context(browser, env, size)
    try:
        p = mt_page(c, env, doc)
        p.wait_for_selector("#hub-market-results .h-pc-near")
        text = p.locator("#hub-market-results").inner_text()
        assert "Closest to their fair chance" in text and "No price pays more than its fair chance right now" in text
        assert "Nothing worth acting on" not in text
        rows = p.locator("#hub-market-results .h-pc-near .h-pc-row:not(.h-pc-head)")
        assert rows.count() == 2 and "Nba Near" in rows.nth(0).inner_text() and "Nba Far" in rows.nth(1).inner_text()
        assert "−$1.20" in rows.nth(0).inner_text() and "+$" not in rows.nth(0).inner_text()
        no_h_scroll(p)
        assert not p.errors
        # no price at all: the one plain line stays
        p.unroute("**/api/upgrade/market-tools")
        p.route("**/api/upgrade/market-tools", lambda r: r.fulfill(json=market_doc(empty=True)))
        p.locator('[data-hub="refresh-markets"]').click()
        p.wait_for_function("document.querySelector('#hub-market-results').innerText.includes('Nothing worth acting on right now')")
        assert p.locator("#hub-market-results .h-pc-near").count() == 0
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ T3 / T4 / T5 Best prices

HIDE_ALL = {"p": {"app": "prizepicks", "entry": "power", "n": 2, "preset": "equal", "method": "worst", "min_books": 1,
                  "req": ["fanduel", "betrivers"], "w": {}, "min_liq": 0, "max_width": 0.1},
            "f": {"q": "", "sport": "nba", "lo": "", "hi": "", "quick": "", "evOnly": True}}


def test_t3_a_saved_view_that_hides_every_leg_opens_on_the_defaults(ctx, env, ev_on):
    """Audit: Best prices opened on "0 of 0 legs - plays hidden: a required book is missing" from a saved Required books
    FanDuel + Polymarket view (here FanDuel + BetRivers, which the fixture's PrizePicks leg lacks). Such a view is not restored (plain message, defaults shown) and is never saved."""
    ctx.add_init_script("try { localStorage.setItem('scout-ev-view', %s); } catch (e) {}" % json.dumps(json.dumps(HIDE_ALL)))
    p = ev_page(ctx, env)
    text = p.locator("#hub-ev-body").inner_text()
    assert "Your saved view showed no legs right now, so the default view is shown." in text
    assert p.locator("#hub-ev-body .ev-list:not(.ev-list-model) .ev-row").count() >= 1
    assert p.locator("#hub-ev-body .ev-chips").count() == 0                              # the defaults: no filter chip
    # the same view cannot be saved again
    p.evaluate("localStorage.removeItem('scout-ev-view')")
    p.locator("#hub-ev-body .ev-drawer summary").click()
    for b in ("fanduel", "betrivers"):
        p.locator(f'[data-ev-req="{b}"]').check()
        p.wait_for_function("!document.querySelector('[data-ev=\"refresh\"]').disabled", timeout=30000)
    p.wait_for_function("document.querySelectorAll('#hub-ev-body .ev-list:not(.ev-list-model) .ev-row').length === 0", timeout=30000)
    assert "FanDuel required" in p.locator("#hub-ev-body .ev-chips").inner_text()        # what hides them is shown, removable
    p.locator('[data-ev="save"]').click()
    assert "not saved" in p.locator("#ev-saved").inner_text()
    assert p.evaluate("localStorage.getItem('scout-ev-view')") is None
    assert not p.errors


def test_t3_active_filters_show_as_removable_chips(ctx, env, ev_on):
    p = ev_page(ctx, env)
    p.locator("#hub-ev-body .ev-drawer summary").click()
    p.locator('[data-ev="quick"][data-id="favorites"]').click()
    p.wait_for_selector("#hub-ev-body .ev-chips")
    chips = p.locator("#hub-ev-body .ev-chip-on")
    assert chips.count() == 1 and "Favorites" in chips.first.inner_text()
    chips.first.click()
    p.wait_for_function("!document.querySelector('#hub-ev-body .ev-chips')")
    assert p.locator('[data-ev="quick"][data-id="favorites"]').get_attribute("aria-pressed") == "false"
    assert not p.errors


@pytest.mark.parametrize("quick", ["sharp", "favorites", "longshots"])
def test_t4_quick_filters_are_real_toggles_with_a_visible_on_state(ctx, env, ev_on, quick):
    p = ev_page(ctx, env)
    p.locator("#hub-ev-body .ev-drawer summary").click()
    sel = f'#hub-ev-body [data-ev="quick"][data-id="{quick}"]'
    assert p.locator(sel).get_attribute("aria-pressed") == "false"
    p.locator(sel).click()
    p.wait_for_function(f"document.querySelector('{sel}').getAttribute('aria-pressed') === 'true'", timeout=30000)
    on = p.locator(sel)
    assert "✓" in on.inner_text()
    bg = on.evaluate("e => getComputedStyle(e).backgroundColor")
    p.wait_for_function("!document.querySelector('[data-ev=\"refresh\"]').disabled", timeout=30000)
    p.locator(sel).click()                                                              # a second press turns it off
    p.wait_for_function(f"document.querySelector('{sel}').getAttribute('aria-pressed') === 'false'", timeout=30000)
    assert "✓" not in p.locator(sel).inner_text()
    assert p.locator(sel).evaluate("e => getComputedStyle(e).backgroundColor") != bg
    assert not p.errors


def test_t5_nba_leg_with_no_book_price_shows_no_return_figure(ctx, env, ev_on):
    p = ev_page(ctx, env)
    p.locator('[data-ev="app"][data-id="underdog"]').click()
    p.wait_for_function("document.querySelector('#hub-ev-body').innerHTML.includes('need 53.5%')", timeout=30000)
    p.locator('[data-ev="model"]').click()
    p.wait_for_selector("#hub-ev-body .ev-list-model .ev-row")
    row = p.locator("#hub-ev-body .ev-list-model .ev-row", has_text="LeBron James").first
    assert row.count() == 1
    assert row.locator('[data-label="Average return on Scout\'s chance"]').count() == 0
    assert "%" in row.locator(".ev-hit").inner_text()                                    # Scout's chance itself stays
    low = p.locator("#hub-ev-body").inner_text().lower()
    assert "unvalidated" not in low and "estimate" not in low and "beta" not in low
    assert not p.errors


# ------------------------------------------------------------------------------------------------ T2 / T9 / T10 / T11 Slips

def slips_page(ctx, env, path="/#/slips", prefs=None):
    if prefs is not None:
        def state(route):
            r = route.fetch()
            d = r.json()
            if route.request.method == "GET":
                d["prefs"] = dict(d.get("prefs") or {}, researchHub=prefs)
            route.fulfill(response=r, json=d)
        ctx.route("**/api/upgrade/state*", state)
    p = open_page(ctx, env, path)
    p.wait_for_selector("#hub-generation-lines", state="attached", timeout=30000)
    return p


def test_t2_goblins_default_is_no_goblins_even_with_an_unmarked_saved_any_lines(ctx, env, monkeypatch):
    """Audit: Auto-build > Goblins read "Any lines" while the help says Scout leaves goblins out unless you allow them. The
    'all' came from the 2026-11-05 batch carry-over, saved without a choice in the Goblins control."""
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    p = slips_page(ctx, env, prefs={"slipLines": "all"})
    p.wait_for_timeout(600)
    assert p.locator("#hub-generation-lines").input_value() == "no_goblins"
    assert not p.errors


def test_t2_any_lines_chosen_in_the_goblins_control_is_kept(ctx, env, monkeypatch):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    p = slips_page(ctx, env, prefs={"slipLines": "all", "slipLinesSet": True})
    p.wait_for_function("document.getElementById('hub-generation-lines').value === 'all'", timeout=10000)
    assert not p.errors


def test_t9_t10_t11_empty_slip_image_button_props_link_and_slip_count(ctx, env, monkeypatch):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    p = open_page(ctx, env, "/#/board?sport=lol")
    p.wait_for_selector("#u-hub", timeout=30000)
    p.wait_for_timeout(600)
    p.locator('#u-hub [data-hub="section"][data-id="studio"]').locator("visible=true").first.click()
    p.wait_for_selector('#hub-studio-section [data-hub="browse-props"]')
    # T9: an empty slip has no lineup image to make, and the button says so
    share = p.locator('#hub-studio-section [data-hub="share-card"]')
    assert share.count() == 1 and share.is_disabled() and "add picks first" in share.inner_text().lower()
    # T11: one slip reads "1 slip"
    opts = p.locator("#hub-generation-slips option").all_inner_texts()
    assert "1 slip" in opts and "1 slips" not in opts and "8 slips" in opts
    # T10: "+ Add player props" opens the board on All sports, with one # in the address
    p.locator('#hub-studio-section .h-studio-intro [data-hub="browse-props"]').click()
    p.wait_for_function("location.hash.startsWith('#/board')")
    href = p.evaluate("location.href")
    assert href.endswith("#/board?sport=all") and href.count("#") == 1, href
    assert not p.locator("#hub-board-section").is_hidden()
    assert not p.errors
