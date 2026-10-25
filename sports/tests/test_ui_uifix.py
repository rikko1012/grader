"""uifix (owner audit 2026-10-24): Slip Studio research legs (sp-2), My picks league filter + test records hidden,
labels / injuries / seasons, slate wording, match context nav, player workspace, not-found route, Slip Studio inside
the main shell. Node tests of the pure helpers + self-contained browser flows (sports/tests/ui_mock.py: Playwright,
every request answered in-process). Every name and number in the fixtures is a TEST FIXTURE."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sports import store, web
from sports.slips import log, validate
from sports.tests import slip_world as w
from sports.tests.test_slips import _call, _req, api, con  # noqa: F401  (fixtures)

WEB = Path(__file__).resolve().parents[2] / "web" / "sports"


# ------------------------------------------------------------------------------------------- node helpers
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_uifix_helpers_in_node():
    script = Path(__file__).resolve().parent / "ui_uifix_test.js"
    r = subprocess.run(["node", str(script), str(WEB)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


# ------------------------------------------------------------------------------- Slip Studio sp-2 (bug 3)
def test_pool_route_counts_research_legs_and_copy_text_warns(api, con):  # noqa: F811
    o = w.offer("res1", "T9", 9)
    store.put(con, w.ev(9))
    w.put_leg(con, o, research_only=True, grade=None, stake=None)
    web._CACHE.clear()
    code, p = _call(_req("/api/sports/slips/pool", query={"league": "nba", "platform": "underdog"}))
    assert code == 200 and p["policy_version"] == "sp-2"
    res = [l for l in p["legs"] if l["research_only"]]
    assert p["research_legs"] == len(res) == 1 and res[0]["stake_units"] == 0.0 and res[0]["grade"] is None
    assert "research forecast only" not in p["excluded"]
    text = validate.copy_text("underdog", "standard", [dict(res[0])])
    assert "Research estimates - not validated; stakes 0." in text and "not entered" in text
    plain = validate.copy_text("underdog", "standard", [dict(l) for l in p["legs"] if not l["research_only"]][:2])
    assert "Research estimates" not in plain
    log.drain()


def test_non_research_forecast_preferred_over_research():
    from sports.slips import pool
    o = w.offer("x", "T1", 1)
    a = w.forecast("rid", o, research_only=True, forecast_at="2026-10-23T20:04:00Z")
    b = w.forecast("rid", o, research_only=False, forecast_at="2026-10-23T19:00:00Z")
    assert pool._pick_forecast([a, b], "member")[0] is b
    assert pool._pick_forecast([a], "member")[0] is a
    assert pool._pick_forecast([], "member") == (None, "no forecast for this exact line")


# ----------------------------------------------------------------------------------------- browser flows
pytest.importorskip("playwright.sync_api")
from sports.tests import ui_mock  # noqa: E402


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                               # noqa: BLE001 - no browser binary on this machine
            pytest.skip(f"chromium not available: {e}")
        yield b
        b.close()


def _page(browser, api_answers, width=1280, posts=None, member=None):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, base_url=ui_mock.BASE)
    page = ctx.new_page()
    errors = ui_mock.install(page, api_answers, member=member, posts=posts)
    return ctx, page, errors


def _leg(i, research=False, side="over"):
    return {"leg_id": f"underdog|ud:{i}|{side}", "platform": "underdog", "league": "nba", "offer_id": f"ud:{i}",
            "record_id": f"offer:underdog:ud:{i}:r1:abc", "forecast_id": f"fc-{i}", "model_version": "sm-1",
            "research_only": research, "event_id": f"nba:espn:{i}", "event_ref": f"A{i} @ H{i}",
            "player_id": f"nba:player:espn:{i}", "player": f"Player {i}", "team": f"T{i}", "stat": "points",
            "stat_label": "Points", "line": 20.5, "side": side, "side_word": "Higher", "multiplier": 1.0,
            "start_utc": "2030-01-01T00:00:00Z", "p_win": 0.58, "p_loss": 0.42, "p_push": 0.0,
            "grade": None if research else "B", "stake_units": 0.0 if research else 0.5}


POOL_RESEARCH = {"league": "nba", "platform": "underdog", "snapshot_at": "2030-01-01T00:00:00Z", "snapshot_id": "ps-1",
                 "policy_version": "sp-2", "offers": 1027, "eligible": 3, "research_legs": 3,
                 "legs": [_leg(1, True), _leg(2, True), _leg(3, True)],
                 "excluded": {"not gradable": 663, "game in_progress": 48, "price not fresh": 13}, "stale_after_s": 1500}
POOL_EMPTY = dict(POOL_RESEARCH, eligible=0, research_legs=0, legs=[],
                  excluded={"not gradable": 663, "game in_progress": 48, "no forecast for this exact line": 316})


def _batch(legs):
    return {"builder_version": "bs-1", "policy_version": "sp-2", "rules_version": "r", "snapshot_at": "2030-01-01T00:00:00Z",
            "batch_id": "b1", "shortage": [], "exposure": {}, "search": {"exact": True},
            "slips": [{"slip_id": "s1", "size": len(legs), "entry_type": "standard", "platform": "underdog", "legs": legs,
                       "joint": {"available": False, "reason": "test"}, "payout": None, "payout_key": None}]}


def _slips_api(pool):
    return {"/api/sports/slips/pool": pool, "/api/sports/slips/build": _batch(pool["legs"][:3]),
            "/api/sports/manifest": {"models": [], "platforms": {}}}


def _open_slips(page):
    page.goto("/sports/slips.html")
    page.wait_for_selector("#ss-pool p, #ss-pool .ss-locked", timeout=15000)


def test_slip_studio_research_legs_warn_and_tag(browser):
    ctx, page, errors = _page(browser, _slips_api(POOL_RESEARCH))
    _open_slips(page)
    banner = page.locator("#ss-research")
    assert banner.is_visible() and "Research estimates - not validated; stakes 0" in banner.inner_text()
    pool = page.locator("#ss-pool").inner_text()
    assert "3" in pool and "research estimates (stake 0)" in pool
    page.locator("#ss-pool summary").click()
    reasons = page.locator("#ss-pool").inner_text()
    assert "Game in progress" in reasons and "in_progress" not in reasons and "not gradable" not in reasons
    page.click("#ss-build")
    page.wait_for_selector(".ss-card")
    tags = page.locator(".ss-card .ss-research")
    assert tags.count() == 3 and "Research estimate" in tags.first.inner_text()
    res = page.locator("#ss-results").inner_text()
    assert "bs-1" not in res or not page.locator("#ss-results details.ss-details").first.evaluate("d => d.open")
    assert not errors, errors
    ctx.close()


def test_slip_studio_zero_picks_is_a_clear_locked_state(browser):
    ctx, page, errors = _page(browser, _slips_api(POOL_EMPTY))
    _open_slips(page)
    box = page.locator("#ss-pool .ss-locked")
    assert box.is_visible()
    t = box.inner_text()
    assert "No pick can go into" in t and "1027" in t and "Game in progress" in t
    assert "No Scout projection for this exact line yet" in t
    assert page.locator("#ss-build").is_disabled()
    assert not page.locator("#ss-research").is_visible()
    assert not errors, errors
    ctx.close()


# ------------------------------------------------------------------------------------ My picks (bug 6)
def _pick(i, lg, status=None, test=False):
    return {"pick_id": f"pk{i}", "tracked_at": "2026-10-23T20:00:00Z", "league": lg, "platform": "underdog",
            "player": f"{lg.upper()} Player {i}", "stat": "points", "stat_label": "Points", "line": 20.5, "side": "over",
            "record_id": f"offer:underdog:ud:{i}:r1:abc", "p_side": 0.55, "displayed_source": "scout_forecast",
            "model_version": "sm-1", "forecast_id": f"fc-{i}", "stake_units": 0, "test": test, "validation_text": "Projection only",
            "result": None if status is None else {"status": status, "outcome": status, "value": 22, "revision": 1,
                                                    "settled_at": "2026-10-24T05:00:00Z", "corrected": False}}


def _mine(q):
    lg = q.get("league")
    nba = [_pick(1, "nba", "win"), _pick(2, "nba", "loss"), _pick(3, "nba")]
    nfl = [_pick(4, "nfl", "win"), _pick(9, "nfl", None, test=True)]          # a stray test row: never listed
    ps = nba if lg == "nba" else nfl if lg == "nfl" else nba + nfl
    return {"version": "pk-1", "picks": ps, "total": len(ps), "shown": len(ps), "stake_units": 0, "hidden_test": 1,
            "record": {"nba": {"win": 1, "loss": 1, "push": 0, "void": 0, "open": 1, "decided": 2, "hit_rate": 0.5},
                       "nfl": {"win": 1, "loss": 0, "push": 0, "void": 0, "open": 0, "decided": 1, "hit_rate": 1.0}}}


def test_my_picks_follows_the_league_toggle_and_hides_test_rows(browser):
    ctx, page, errors = _page(browser, {"/api/sports/picks/mine": _mine, "/api/sports/manifest": {"models": [], "platforms": {}}})
    page.goto("/sports#/picks?lg=nba")
    page.wait_for_selector(".sw-mp-rec")
    body = page.locator("#sw-main").inner_text()
    assert "NBA 1-1-0" in body and "NFL 1-0-0" in body and "hit rate 50.0% (n = 2)" in body
    assert "NFL Player" not in body and "1 test record is not shown" in body
    assert "$" not in body and "EV" not in body and "profit" not in body.lower()
    page.click("#sw-league button[data-lg=nfl]")
    page.wait_for_function("document.querySelector('#sw-main').innerText.indexOf('NFL Player 4') >= 0 || "
                           "document.querySelector('#sw-main').innerText.indexOf('Settled') >= 0")
    page.wait_for_selector(".sw-mp-rec")
    body = page.locator("#sw-main").inner_text()
    assert "NBA Player" not in body and "NFL Player 9" not in body
    assert page.locator(".sw-mp-cur").inner_text().startswith("NFL")
    assert not errors, errors
    ctx.close()


def test_my_picks_api_filters_league_and_hides_test_unless_admin(monkeypatch, tmp_path):
    from sports import picks
    from sports.tests.test_picks import _req as preq, _call as pcall  # noqa: F401
    monkeypatch.setattr(picks, "db_path", lambda: tmp_path / "picks.db")
    pc = picks.connect()
    mh = picks.member_hash("m_test")
    for i, (lg, scratch) in enumerate([("nba", False), ("nfl", False), ("nfl", True)]):
        payload = {"pick_id": f"p{i}", "tracked_at": f"2026-10-23T20:0{i}:00Z", "league": lg, "platform": "underdog",
                   "line": 20.5, "side": "over", "record_id": f"r{i}", "scratch": scratch}
        pc.execute("INSERT INTO picks (pick_id, member, tracked_at, league, platform, offer_id, record_id, side, line, payload) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)", (f"p{i}", mh, payload["tracked_at"], lg, "underdog", f"o{i}", f"r{i}",
                                                     "over", 20.5, json.dumps(payload)))
    pc.commit()
    m = picks.mine(pc, "m_test", league="nba")
    assert [p["league"] for p in m["picks"]] == ["nba"] and m["hidden_test"] == 0
    m = picks.mine(pc, "m_test", league="nfl")
    assert [p["pick_id"] for p in m["picks"]] == ["p1"] and m["hidden_test"] == 1
    assert m["record"]["nfl"]["open"] == 1 and m["record"]["nba"]["open"] == 1           # test row never in the record
    assert [p["pick_id"] for p in picks.mine(pc, "m_test", league="nfl", include_test=True)["picks"]] == ["p2", "p1"]
    pc.close()
