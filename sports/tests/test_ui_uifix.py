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
    page.goto("/sports#/slips?lg=nba")
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
    assert "No pick can go into" in t and "1,027" in t and "Game in progress" in t
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
    # wait for the NFL page itself ('Settled' is a tab label on the NBA page too, so it can't be the signal)
    page.wait_for_function("(function () { var e = document.querySelector('#sw-main .sw-eyebrow');"
                           " return !!(e && e.textContent.indexOf('NFL') === 0 && document.querySelector('#sw-main .sw-mp-rec')); })()")
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


# ------------------------------------------------------------------- slate / match context / player fixtures
def _team(i, abbr, name):
    return {"team_id": f"nba:team:espn:{i}", "abbr": abbr, "name": name}


BOS, NY = _team(2, "BOS", "Boston Celtics"), _team(18, "NY", "New York Knicks")
EV = {"event_id": "nba:espn:9001", "record_id": "event:espn:9001:r1:abc", "start_utc": "2030-01-01T00:00:00Z",
      "start_source": "espn", "status": "scheduled", "reschedules": [], "observed_at": "2029-12-31T20:00:00Z",
      "home": BOS, "away": NY, "started": False,
      "coverage": {"markets": 154, "primary": 120, "forecasts": 75, "players": 18, "by_platform": {"prizepicks": 60, "underdog": 50}},
      "injuries": [{"player_id": "p1", "player": "Test Guard", "team_id": NY["team_id"], "team": "NY", "status": "out",
                    "detail": "Not Specified Shoulder", "as_of": "2029-12-31T18:00:00Z", "record_id": "ctx:1"}],
      "game_lines": {"spread": {"home": -4.5, "away": 4.5, "source_label": "ESPN (DraftKings)", "observed_at": "2029-12-31T19:00:00Z",
                                "history": [["2029-12-30T19:00:00Z", -3.5], ["2029-12-31T19:00:00Z", -4.5]]},
                     "total": {"line": 221.5, "source_label": "ESPN (DraftKings)", "observed_at": "2029-12-31T19:00:00Z"},
                     "moneyline": {"home_p": 0.66, "away_p": 0.34, "source_label": "Kalshi", "observed_at": "2029-12-31T19:30:00Z"}},
      "team_context": {"as_of": "2029-12-31T12:00:00Z", "source": "Scout game store (ESPN box scores)",
                       "home": {"pace": {"value": 99.1, "rank": 12, "n": 20},
                                "def_vs_pos": [{"pos": "G", "stat_label": "Points", "rank": 3, "allowed_pg": 41.2, "n": 20}]},
                       "away": {"pace": {"value": 96.4, "rank": 27, "n": 20}}}}
EV2 = dict(EV, event_id="nba:espn:9002", record_id="event:espn:9002:r1:x", status="in_progress", started=True, game_lines=None,
           team_context=None, injuries=[], coverage={"markets": 0, "primary": 0, "forecasts": 0, "players": 0, "by_platform": {}})
MANIFEST = {"models": [{"model_version": "sm-1", "forecasts": 75, "settled": 0, "research_only": True},
                       {"model_version": "mkt-polymarket", "forecasts": 10, "settled": 0, "research_only": True}],
            "platforms": {}, "version": "ui-snap-1", "contracts": "sc-1", "capabilities": "cap-1",
            "built_at": "2099-01-01T00:00:00Z", "as_of": "2099-01-01T00:00:00Z", "sources": {}}
PID = "nba:player:espn:77"


def _game(k, season, value, opp):
    return {"event_id": f"nba:espn:{k}", "event_record_id": f"ev{k}",
            "start_utc": f"2029-{12 - k // 2:02d}-{20 - (k % 2) * 10:02d}T00:00:00Z",   # newest first
            "season": season, "season_type": "regular", "participation": "played", "values": {"points": value},
            "parts": {"min": 30}, "home": True, "opponent": opp, "source": ["espn_gamelog"], "record_ids": {"points": f"s{k}"},
            "observed_at": "2029-12-31T00:00:00Z"}


GAMES = [_game(k, 2030 if k < 5 else 2029, 18 + (k % 7), NY if k % 3 == 0 else BOS) for k in range(14)]
QUOTES = [{"record_id": f"offer:{pf}:{pf}{i}:r1:ab", "platform": pf, "line": 20.5 + i, "variant": "standard",
           "sides": [{"side": "over", "offered": True, "multiplier": 1.0}, {"side": "under", "offered": True, "multiplier": 1.0}],
           "last_seen_at": "2099-01-01T00:00:00Z", "forecast_id": None, "gradable": True, "not_gradable_because": []}
          for i, pf in enumerate(["prizepicks", "underdog"] * 5)]
PLAYER = {"player_id": PID, "player": "Test Wing", "position": "G", "team": BOS, "built_at": "2099-01-01T00:00:00Z",
          "as_of": "2099-01-01T00:00:00Z",
          "injury": {"status": "questionable", "detail": "Left Ankle Sprain", "record_id": "ctx:2"},
          "next_event": {"event_id": EV["event_id"], "start_utc": EV["start_utc"], "home": True, "opponent": NY},
          "markets": [{"stat": "points", "stat_label": "Points", "primary": True, "quotes": QUOTES, "alternates": [], "kalshi": [],
                       "market": None, "event_id": EV["event_id"], "team": "BOS", "capability": "supported"}],
          "forecasts": {}, "forecast_detail": {},
          "factors": {"stats": [{"label": "Minutes", "windows": [{"window": "L5", "mean": 31, "n": 5},
                                                                 {"window": "Season 2030", "mean": 30, "n": 5}]}],
                      "context": [], "not_held": [],
                      "engine": {"label": "Matchup context", "as_of": "2029-12-31T00:00:00Z", "not_held": [],
                                 "by_stat": {"points": [
                                     {"key": "dvp", "label": "Opponent points allowed to guards vs league", "group": "Opponent",
                                      "value": 1.08, "n": 20, "source": "ESPN box scores"},
                                     {"key": "pace", "label": "Opponent pace", "group": "Pace", "value": 98.2}]}}}}


def _app_api(extra=None):
    d = {"/api/sports/slate": {"events": [EV, EV2]}, "/api/sports/manifest": MANIFEST,
         "/api/sports/player": PLAYER, "/api/sports/history": {"games": GAMES},
         "/api/sports/board": {"markets": [], "events": {}, "fc": {}, "stats": {}, "injuries": {}, "models": []},
         "/api/sports/model/features": {"features": []}, "/api/sports/model/registry": {"artifacts": {}}}
    d.update(extra or {})
    return d


def test_slate_bettor_wording_details_toggle_lines_and_placeholder(browser):
    ctx, page, errors = _page(browser, _app_api())
    page.goto("/sports#/slate?lg=nba")
    page.wait_for_selector(".sw-game")
    body = page.locator("#sw-main").inner_text()
    assert "core markets" not in body and "Scout chances" not in body
    assert "154 props" in body and "75 lines with Scout projection" in body
    # a started game leaves the main list: counted, and listed in the collapsed "Started or finished" section
    assert "1 upcoming game" in body and "1 started or finished" in body and "Started or finished (1)" in body
    assert page.locator(".sw-past .sw-game").count() == 1 and not page.locator(".sw-past .sw-game").is_visible()
    page.locator(".sw-past > summary").click()
    body = page.locator("#sw-main").inner_text()
    assert "Game in progress" in body and "in_progress" not in body
    card = page.locator(".sw-game").first
    assert card.locator(".sw-srcbtn").count() == 1 and not card.locator(".sw-srcbtn").is_visible()   # behind Details
    gl = card.locator(".sw-glines").inner_text()
    assert "BOS -4.5" in gl and "221.5" in gl and "BOS win 66%" in gl and "ESPN (DraftKings)" in gl
    assert card.locator(".sw-spark").count() == 1                   # spread has 2 snapshots; total 1 (no sparkline)
    assert page.locator(".sw-game").nth(1).locator(".sw-glines").count() == 0      # nothing blank without lines
    assert page.get_attribute("#sw-sq", "placeholder") == "e.g. BOS or Celtics"
    card.locator("summary", has_text="on the injury report").click()
    assert "Out · Shoulder" in card.inner_text() and "Not Specified" not in card.inner_text()
    page.click("#sw-league button[data-lg=nfl]")
    page.wait_for_function("document.getElementById('sw-sq') && document.getElementById('sw-sq').placeholder === 'e.g. KC or Chiefs'")
    assert not errors, errors
    ctx.close()


def test_match_context_nav_state_and_team_context(browser):
    ctx, page, errors = _page(browser, _app_api())
    page.goto("/sports#/game/" + EV["event_id"] + "?lg=nba")
    page.wait_for_selector("#sw-tc-h")
    assert page.locator("#sw-views a[aria-current=page]").get_attribute("data-view") == "slate"
    assert page.locator("#sw-views a[data-view=board]").get_attribute("aria-current") is None
    tc = page.locator("#sw-tc-h").locator("xpath=..").inner_text()
    tc = tc.lower()
    assert "spread" in tc and "pace" in tc and "99.1" in tc and "#3" in tc and "41.2 per game" in tc
    body = page.locator("#sw-main").inner_text()
    assert "75 lines with Scout projection" in body and "Scout chances" not in body
    assert "Out · Shoulder" in body
    assert not errors, errors
    ctx.close()


def test_unknown_route_shows_not_found(browser):
    ctx, page, errors = _page(browser, _app_api())
    page.goto("/sports#/props")
    page.wait_for_selector(".sw-state h2")
    assert page.locator(".sw-state h2").inner_text() == "Page not found" and "#/props" in page.locator(".sw-state").inner_text()
    assert page.locator("#sw-views a[aria-current=page]").count() == 0
    assert not errors, errors
    ctx.close()


def test_footer_hides_builder_terms_behind_details(browser):
    ctx, page, errors = _page(browser, _app_api())
    page.goto("/sports#/slate?lg=nba")
    page.wait_for_selector(".sw-game")
    page.locator("#sw-foot summary").first.click()
    vis = page.evaluate("() => { const o = document.querySelector('#sw-foot > details').cloneNode(true);"
                        " o.querySelector('details').remove(); return o.textContent; }")
    for term in ("cap-1", "sc-1", "mkt-polymarket", "contracts", "capabilities"):
        assert term not in vis, term
    assert "Scout projection" in vis and "Market (Polymarket)" in vis
    assert "cap-1" in page.locator("#sw-foot details details").text_content()
    assert not errors, errors
    ctx.close()


def test_player_workspace_season_breaks_one_games_list_matchup_and_strip(browser):
    ctx, page, errors = _page(browser, _app_api(), width=900)
    page.goto(f"/sports#/player/{PID}?lg=nba&stat=points")
    page.wait_for_selector("#sw-hi-h")
    page.click('[data-win="10"]')
    page.wait_for_selector(".sw-finals")
    hist = page.locator("#sw-hi-h").locator("xpath=..")
    assert "spans 2 seasons (2028-29 and 2029-30)" in hist.inner_text()
    assert hist.locator(".sw-finals .sw-season-break").count() == 1
    assert page.locator("svg.sw-chart .sw-sbreak").count() == 1
    assert page.locator("svg.sw-chart .sw-bar-val").count() == 0          # the finals row is the one list of finals
    main = page.locator("#sw-main").inner_text()
    k = main.lower().find("season 2030")
    assert k < 0 and "season 2029-30" in main.lower(), main[max(0, k - 200):k + 40]
    mu = page.locator("#sw-mu-h").locator("xpath=..").inner_text()
    assert "1.08× the league average allowed" in mu and "n=20" in mu and "vs ny" in mu.lower() and "hit rate Over" in mu
    assert page.locator("#sw-without").count() == 1
    assert not page.locator(".sw-stripbtn.prev").is_visible()   # at the start: only "more lines"
    nxt = page.locator(".sw-stripbtn.next")
    assert nxt.is_visible()                                       # 10 lines at 900 px: more to the right
    nxt.click()
    page.wait_for_function("document.getElementById('sw-strip').scrollLeft > 0")
    page.wait_for_selector(".sw-stripbtn.prev:not([hidden])")
    assert "Questionable · Left ankle · Sprain" in page.locator(".sw-phead").inner_text()
    assert not errors, errors
    ctx.close()


def test_without_teammate_module_mount_is_called(browser):
    ctx, page, errors = _page(browser, _app_api())
    # the real without.js is on the page now (sheets agent): pin a stub that the module's own assignment can't replace
    page.add_init_script("(function () { var stub = {render: function (el, p, g, o) { el.textContent = 'without:' + p.player + ':' + g.length + ':' + o.stat; }};"
                         " Object.defineProperty(window, 'SportsWithout', {get: function () { return stub; }, set: function () {}, configurable: true}); })();")
    page.goto(f"/sports#/player/{PID}?lg=nba&stat=points")
    page.wait_for_function("(document.getElementById('sw-without') || {textContent: ''}).textContent.indexOf('without:') === 0")
    assert page.locator("#sw-without").inner_text() == f"without:Test Wing:{len(GAMES)}:points"
    assert not errors, errors
    ctx.close()


def test_slip_studio_is_inside_the_main_shell(browser):
    ctx, page, errors = _page(browser, _slips_api(POOL_RESEARCH))
    page.goto("/sports#/slips?lg=nba")
    page.wait_for_selector("#ss-form")
    assert page.locator(".sw-products a[aria-current=page]").inner_text() == "Sports"
    assert page.locator("#sw-league button[data-lg=nba]").get_attribute("aria-pressed") == "true"
    assert page.locator("#sw-views a[aria-current=page]").get_attribute("data-view") == "slips"
    assert not page.locator(".ss-sport").is_visible()             # the page toggle picks the league
    page.click("#sw-league button[data-lg=nfl]")
    page.wait_for_function("document.getElementById('ss-league') && document.getElementById('ss-league').value === 'nfl'")
    assert "#/slips" in page.url
    page.goto("/sports/slips.html")
    page.wait_for_url("**/sports#/slips**")
    assert not errors, errors
    ctx.close()
