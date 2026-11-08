"""Owner site audit 2026-11-06 (5:40-6:05 PM CST), Slips and Tools items: regression tests for each one that reproduced on
origin/main 2f96bab. T5 (no return figure on NBA / NFL legs with no book price) is covered in tests/test_pickem_ev.py; this
file holds T3, T4, T6 and T8. The Slips items (T1 break-even, T2 goblins carry-over, T9-T11) were rolled back on the owner's
2026-11-07 request (Slip studio as it was on 2f96bab), with their tests.
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


def _st1_claims(tok):
    """The st1 token's claims when its signature checks against the test secret (sports/authtoken.py's format, re-checked here
    because tests/ may not import sports: sports/tests/test_sports_isolation.py), else None."""
    import base64
    import hashlib
    import hmac
    parts = (tok or "").split(".")
    if len(parts) != 3 or parts[0] != "st1":
        return None
    mac = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), f"st1.{parts[1]}".encode(), hashlib.sha256).digest()).decode().rstrip("=")
    if not hmac.compare_digest(mac, parts[2]):
        return None
    return json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))


@pytest.mark.parametrize("league", ["nba", "nfl"])
def test_t8_nba_nfl_comparison_reaches_the_sports_admin_route(monkeypatch, league):
    """Audit: NBA / NFL showed "Comparison unavailable" on every retry. The request carried X-Admin-Token, which the sports
    service accepts on /api/sports/admin/ops only; every other admin route needs an st1 token of role admin. The token is
    minted by server.py's one sports hook (the real Handler._sports_route); the fake service below admits only a correctly
    signed admin token. The service's own route check is sports/tests/test_audit_slips_tools_1106_sports.py."""
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("SPORTS_API_BASE", "https://sports.example.test")
    monkeypatch.setenv("ADMIN_TOKEN", "owner-token")
    seen = []
    doc = {"sport": league, "rows": [{"player": "Fixture Guard"}]}

    class Resp:
        def read(self, n=-1):
            return json.dumps(doc).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Opener:
        def open(self, req, timeout=None):
            import urllib.error
            from urllib.parse import parse_qs, urlparse
            seen.append(req)
            auth = req.get_header("Authorization") or ""
            claims = _st1_claims(auth[7:] if auth.startswith("Bearer ") else None)
            u = urlparse(req.full_url)
            if (claims or {}).get("role") != "admin" or claims.get("exp", 0) <= time.time():
                raise urllib.error.HTTPError(req.full_url, 401, "members_only", {}, None)
            assert u.path == "/api/sports/admin/model/gradecore-comparison" and parse_qs(u.query)["league"] == [league]
            return Resp()

    import urllib.request
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    h = _H()
    workspace_api.get_grade_review(h, server, {"sport": league, "token": "owner-token"}, None, None, "grade-review")
    code, got = h.sent
    assert code == 200, got
    assert got["sport"] == league and [r["player"] for r in got["rows"]] == ["Fixture Guard"]
    assert seen and seen[0].get_header("X-admin-token") is None                       # the admin secret is never forwarded
    assert _st1_claims(seen[0].get_header("Authorization")[7:])["role"] == "admin"


def test_t8_without_a_token_secret_the_report_says_unavailable(monkeypatch):
    monkeypatch.delenv("SPORTS_TOKEN_SECRET", raising=False)
    monkeypatch.setenv("SPORTS_API_BASE", "https://sports.example.test")
    h = _H()
    workspace_api.get_grade_review(h, server, {"sport": "nba", "token": "owner-token"}, None, None, "grade-review")
    assert h.sent[0] == 503


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
