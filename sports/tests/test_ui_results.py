"""rx-1 Results layout (owner 2026-10-25: "make the results page for sports easier to understand, less crowded"):
the server helpers it reads (results_public.recent / model_gate), node tests of the pure renderers
(ui_results_test.js) and self-contained browser flows (ui_mock: every request answered in-process) for the in-app
Results tab (#/results) and the public page (/sports/results): summary present, Details collapsed by default, shown
numbers equal the fixture data, recent list + Show more, NFL "model not validated", 360 px without sideways scroll.
Every name and number in the fixtures is a TEST FIXTURE."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sports import results_public as rp

WEB = Path(__file__).resolve().parents[2] / "web" / "sports"
SHOTS = Path(os.environ.get("SPORTS_SHOTS_DIR") or (Path(__file__).resolve().parent / "_shots"))


# ------------------------------------------------------------------------------------------- server helpers
def _row(fid, ev, pid, stat, line, model, lean, result, at, settled, po=0.6):
    return {"forecast_id": fid, "model": model, "event_id": ev, "player_id": pid, "stat": stat, "line": line,
            "p_over": po, "p_under": 1 - po, "status": "settled", "lean": lean, "lean_result": result,
            "outcome": lean if result == "win" else ("push" if result == "push" else ("under" if lean == "over" else "over")),
            "actual": 25.0, "forecast_at": at, "settled_at": settled}


def test_recent_is_one_row_per_prop_newest_first_with_grade():
    rows = [_row("a1", "g1", "nba:p1", "points", 20.5, "sm-1", "over", "win", "2026-10-20T10:00Z", "2026-10-21T05:00Z", 0.62),
            _row("a2", "g1", "nba:p1", "points", 20.5, "mi-1", "over", "win", "2026-10-20T11:00Z", "2026-10-21T05:00Z", 0.56),
            _row("b1", "g2", "nba:p2", "rebounds", 8.5, "sm-1", "over", "loss", "2026-10-22T10:00Z", "2026-10-23T05:00Z", 0.52),
            dict(_row("c1", "g2", "nba:p3", "points", 10.5, "mc-1", None, None, "2026-10-22T10:00Z", "2026-10-23T05:00Z")),
            dict(_row("d1", "g3", "nba:p4", "points", 10.5, "sm-1", "over", None, "2026-10-22T10:00Z", None), status="void")]
    out = rp.recent(rows, names={"nba:p2": "Test Player Two"})
    assert [r["forecast_id"] for r in out] == ["b1", "a2"]                  # newest settlement first; one per prop
    assert out[0]["player"] == "Test Player Two" and out[0]["result"] == "loss" and out[0]["side"] == "over"
    assert out[0]["grade"] == "Lean" and out[1]["grade"] == "B"           # rg-1 of the unrounded lean-side chance
    assert out[1]["p_side"] == 0.56 and out[1]["actual"] == 25.0
    assert len(rp.recent(rows * 1, n=1)) == 1


@pytest.mark.parametrize("contain", [True, False])
def test_contained_league_carries_no_research_letters(monkeypatch, contain):
    """vg-1: with VG_CONTAIN_LEAN on, NFL (no stat passed its gate) has no grade rows, no letter in recent, no picks
    by grade; NBA keeps its letters. With it off, NFL letters come back."""
    from sports import board_view
    monkeypatch.setattr(board_view, "VG_CONTAIN_LEAN", contain)
    nfl = [_row("n1", "nfl:g1", "nfl:p1", "rec_yds", 50.5, "sm-1", "over", "win", "2026-10-20T10:00Z", "2026-10-21T05:00Z", 0.62)]
    nba = [_row("b1", "nba:g1", "nba:p1", "points", 20.5, "sm-1", "over", "win", "2026-10-20T10:00Z", "2026-10-21T05:00Z", 0.62)]
    G, R, M = rp.grades(nfl, "nfl"), rp.recent(nfl, league="nfl"), rp.model_gate("nfl")
    if contain:
        assert G["rows"] == [] and G["withheld"] == "NFL model not validated"
        assert R[0]["grade"] is None and M["contained"] is True
    else:
        assert [g["n"] for g in G["rows"]] == [1, 0, 0, 0] and "withheld" not in G
        assert R[0]["grade"] == "A" and M["contained"] is False
    assert M["not_validated"] == ("NFL model not validated" if contain else None)   # A9a 4: only while contained
    assert rp.grades(nba, "nba")["rows"][0]["n"] == 1 and rp.recent(nba, league="nba")[0]["grade"] == "A"
    assert rp.model_gate("nba")["contained"] is False


def test_model_gate_names_a_league_with_no_validated_stat():
    nfl, nba = rp.model_gate("nfl"), rp.model_gate("nba")
    assert nfl["not_validated"] == "NFL model not validated" and nfl["validated_stats"] == []
    assert nba["not_validated"] is None and "points" in nba["validated_stats"]


def test_built_file_carries_recent_and_model_gate(built):                  # noqa: F811
    from sports import store
    con = store.connect(read_only=True)
    rp.build(con, now=NOW)
    con.close()
    d = json.loads(rp.path().read_text(encoding="utf-8"))
    for lg in ("nba", "nfl"):
        assert isinstance(d["leagues"][lg]["recent"], list) and "model_gate" in d["leagues"][lg]
    assert d["leagues"]["nfl"]["model_gate"]["not_validated"] == "NFL model not validated"


from sports.tests.test_ui_snapshots import NOW, built  # noqa: E402,F401  (the shared built world)


# ------------------------------------------------------------------------------------------- node helpers
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_results_helpers_in_node():
    script = Path(__file__).resolve().parent / "ui_results_test.js"
    r = subprocess.run(["node", str(script), str(WEB)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


# ----------------------------------------------------------------------------------------- fixtures (TEST)
def _recent(n, lg="nba"):
    stats = ["points", "rebounds", "assists"] if lg == "nba" else ["rec_yds", "rush_yds"]
    return [{"forecast_id": f"f{i}", "model": "sm-1", "event_id": f"{lg}:g{i // 3}", "player_id": f"{lg}:p{i}",
             "player": f"Fixture Player {i}", "stat": stats[i % len(stats)], "line": 20.5 + i, "side": "over" if i % 2 == 0 else "under",
             "result": ["win", "loss", "push"][i % 3], "outcome": "over", "actual": 22.0 + i, "p_side": 0.58,
             "grade": ["A", "B", "C", "Lean"][i % 4], "settled_at": f"2026-10-{24 - i // 10:02d}T05:{i % 60:02d}:00Z"} for i in range(n)]


def _league(lg, props, leans, grades, recent, hh, nv=None):
    return {"forecasts": props + 40, "settled": props + 20, "void": 3, "unresolved": 2, "props": props, "games": props // 9,
            "projection": {"n": props, "mae": 4.12, "mae_ci95": [3.9, 4.4], "bias": -0.31, "bias_ci95": [-0.6, 0.0]},
            "leans": leans, "gate": {"settled_props": props, "required": 300, "validated": False,
                                     "under_review": props >= 300, "text": rp.gate_text(props), "rule": rp.GATE_RULE},
            "models": {"sm-1": {"settled": props, "props": props, "games": 20, "projection": {}, "leans": leans, "research_only": True}},
            "stats": {"points": {"settled": props, "props": props, "games": 20, "projection": {}, "leans": leans}},
            "calibration": [{"band": "50-60%", "n": 100, "stated": 0.55, "observed": 0.53, "observed_ci95": [0.44, 0.62]}],
            "bands": [{"band": "50-55%", "label": "probability band", "n": 50, "wins": 26, "losses": 24, "pushes": 0, "stated": 0.52,
                       "hit_rate": 0.52, "hit_rate_ci95": [0.4, 0.64]}],
            "grades": {"label": "Research grade - not validated", "rule": "rg-1", "rows": grades},
            "recent": [dict(r, grade=None) for r in recent] if nv else recent,
            "model_gate": {"rule": "vg-1", "validated_stats": [] if nv else ["points"], "not_validated": nv, "contained": bool(nv)},
            "head_to_head": {"version": "hh-1", "method": "fixture method", "rows": hh}}


NBA_LEANS = {"n": 1586, "wins": 812, "losses": 760, "pushes": 14, "hit_rate": 812 / 1572, "hit_rate_ci95": [0.4903, 0.5411]}
NBA_GRADES = [{"grade": "A", "n": 120, "wins": 70, "losses": 48, "pushes": 2, "hit_rate": 70 / 118, "hit_rate_ci95": [0.5, 0.68]},
              {"grade": "B", "n": 240, "wins": 128, "losses": 110, "pushes": 2, "hit_rate": 128 / 238, "hit_rate_ci95": [0.47, 0.6]},
              {"grade": "C", "n": 300, "wins": 150, "losses": 148, "pushes": 2, "hit_rate": 150 / 298, "hit_rate_ci95": [0.45, 0.56]},
              {"grade": "Lean", "n": 926, "wins": 464, "losses": 454, "pushes": 8, "hit_rate": 464 / 918, "hit_rate_ci95": [0.47, 0.54]}]
HH = [{"scout": "sm-1", "market": "mc-1", "stat": "all", "pairs": 820, "pushes": 9, "games": 140, "scout_brier": 0.2431,
       "market_brier": 0.2489, "scout_log_loss": 0.68, "market_log_loss": 0.69, "scout_hit_rate": 0.5312, "market_hit_rate": 0.5178,
       "diff": {"brier": -0.0058, "brier_ci95": [-0.0121, 0.0009], "log_loss": -0.01, "log_loss_ci95": [-0.03, 0.01],
                "hit_rate": 0.01, "hit_rate_ci95": [-0.0102, 0.0361]}}]
PUBLIC = {"version": "rp-1", "built_at": "2026-10-25T06:00:00Z", "as_of": "2026-10-25T06:00:00Z",
          "leagues": {"nba": _league("nba", 1566, NBA_LEANS, NBA_GRADES, _recent(23), HH),
                      "nfl": _league("nfl", 40, {"n": 30, "wins": 14, "losses": 16, "pushes": 0, "hit_rate": 14 / 30, "hit_rate_ci95": [0.3, 0.63]},
                                     [],
                                     _recent(4, "nfl"), [], nv="NFL model not validated")},
          "picks": {"leagues": {"nba": {"picks": 12, "games": 5, "wins": 6, "losses": 5, "pushes": 1, "voids": 0, "pending": 2,
                                        "hit_rate": 6 / 11, "hit_rate_ci95_games": None}}, "note": "fixture"},
          "grades": "fixture grades", "returns": "none", "method": {"settled": "fixture definition"}}
LEDGER = {"summary": {"sm-1": {"settled": 1566, "games": 174, "void": 3, "pushes": 14,
                               "projection": {"n": 1566, "mae": 4.12, "mae_ci": [3.9, 4.4], "bias": -0.31, "bias_ci": [-0.6, 0.0]},
                               "probability": {"n": 1566, "brier": 0.48, "brier_ci": [0.47, 0.49], "log_loss": 0.69, "log_loss_ci": [0.68, 0.7],
                                               "brier_binary": 0.2431, "brier_binary_ci": [0.24, 0.25], "brier_binary_n": 1552},
                               "returns": {"recorded": 0}}},
          "rows": [{"forecast_id": "f1", "model_version": "sm-1", "event_id": "nba:g1", "player_id": "nba:p1", "player": "Fixture Player 1",
                    "stat": "points", "line": 20.5, "platform": "prizepicks", "projection_mean": 22.1, "p_over": 0.58, "p_under": 0.4,
                    "p_push": 0.02, "status": "settled", "outcome": "over", "actual": 24, "p_outcome": 0.58, "brier": 0.33,
                    "settlement_ids": ["s1"], "settlement_revision": 1, "rule_version": "st-1", "settled_at": "2026-10-24T05:00:00Z"}],
          "total_rows": 1, "head_to_head": {"version": "hh-1", "method": "fixture", "rows": HH}, "stats": {"points": "Points"},
          "method": {"brier": "fixture brier", "ci": "fixture ci"}}
MANIFEST = {"models": [], "platforms": {}}


def _api(public=PUBLIC):
    return {"/api/sports/results": LEDGER, "/api/sports/results/public": public, "/api/sports/manifest": MANIFEST}


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


def _page(browser, api, width):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, base_url=ui_mock.BASE)
    page = ctx.new_page()
    errors = ui_mock.install(page, api)
    return ctx, page, errors


def _fits(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def _check_summary(page, lg="nba"):
    L = PUBLIC["leagues"][lg]
    assert page.locator("[data-rx-status]").inner_text() == f"{L['props']:,} settled props · under review (needs 300 + passing checks)"
    lv = L["leans"]
    assert page.locator("[data-rx-wlp]").inner_text() == f"{lv['wins']}-{lv['losses']}-{lv['pushes']}"
    assert page.locator("[data-rx-rate]").inner_text() == f"{round(lv['hit_rate'] * 100, 1):.1f}%"
    assert "49.0%–54.1%" in page.locator("[data-rx-range]").inner_text()
    tiles = page.locator(".rx-tile")
    assert tiles.count() == 3
    for i, g in enumerate(L["grades"]["rows"][:3]):
        t = tiles.nth(i)
        assert t.get_attribute("data-grade") == g["grade"]
        assert t.locator("[data-rx-gn]").inner_text() == f"{g['n']:,}"
        assert t.locator("[data-rx-grate]").inner_text() == f"{round(g['hit_rate'] * 100, 1):.1f}%"
    assert page.locator(".rx-cmp").get_attribute("data-verdict") == "even"     # hit-rate gap CI includes 0
    assert page.locator("[data-rx-cmp-head]").inner_text() == "Scout hit 53.1% vs 51.8% for the market consensus on the same 820 props"
    assert page.locator("[data-rx-cmp]").inner_text().startswith("No clear difference (95% range of the gap −1.0% to +3.6%)")
    for i, g in enumerate(L["grades"]["rows"][:3]):                              # tile ranges are percentages too
        lo, hi = g["hit_rate_ci95"]
        assert tiles.nth(i).locator("[data-rx-grange]").inner_text() == f"{lo * 100:.1f}%–{hi * 100:.1f}%"
    surface_txt = page.evaluate("[...document.querySelectorAll('.rx-sum,.rx-cmp,.rx-recent')].map(e => e.innerText).join(' ')")
    import re
    assert not re.search(r"(^|[^\d.])0\.\d", surface_txt), "raw decimal on the surface"
    # recent: 10, then Show more adds 10, then 3
    assert page.locator(".rx-pick").count() == 10
    assert "Fixture Player 0" in page.locator(".rx-pick").first.inner_text()
    page.click("[data-rx-more]")
    assert page.locator(".rx-pick").count() == 20
    page.click("[data-rx-more]")
    assert page.locator(".rx-pick").count() == 23 and page.locator("[data-rx-more]").count() == 0
    # details: present, every section closed by default; no model codes above Details
    d = page.locator("details.rx-d")
    assert d.count() >= 4 and page.evaluate("[...document.querySelectorAll('details.rx-d')].every(x => !x.open)")
    surface = page.evaluate("[...document.querySelectorAll('.rx-sum,.rx-cmp,.rx-recent')].map(e => e.innerText).join(' ')")
    for code in ("sm-1", "mc-1", "mi-1", "rg-1", "hh-1", "rp-1", "ROI", "profit"):
        assert code not in surface, code


@pytest.mark.parametrize("width", [360, 1280])
def test_in_app_results_tab_summary_details_and_fit(browser, width):
    SHOTS.mkdir(parents=True, exist_ok=True)
    ctx, page, errors = _page(browser, _api(), width)
    page.goto(ui_mock.BASE + "/sports#/results?lg=nba")
    page.wait_for_selector(".rx-sum")
    page.screenshot(path=str(SHOTS / f"results_app_{width}.png"), full_page=True)
    _check_summary(page)
    assert _fits(page)
    # opening one Details section shows the full head-to-head table and keeps the page width
    page.locator('details.rx-d[data-rx="h2h"] > summary').click()
    assert page.locator('details.rx-d[data-rx="h2h"] table').count() == 1 and _fits(page)
    page.locator('details.rx-d[data-rx="ledger"] > summary').click()
    assert "Fixture Player 1" in page.locator('details.rx-d[data-rx="ledger"]').inner_text() and _fits(page)
    assert not errors, errors
    ctx.close()


def test_in_app_results_without_public_file_still_shows_details(browser):
    api = _api()
    api["/api/sports/results/public"] = lambda q: (503, {"error": "snapshot_not_ready"})
    ctx, page, errors = _page(browser, api, 360)
    page.goto(ui_mock.BASE + "/sports#/results?lg=nba")
    page.wait_for_selector(".rx-details")
    assert "summary is being prepared" in page.inner_text("main") and _fits(page)
    assert not errors, errors
    ctx.close()


@pytest.mark.parametrize("width", [360, 1280])
def test_public_results_page_league_toggle_and_fit(browser, width):
    SHOTS.mkdir(parents=True, exist_ok=True)
    ctx, page, errors = _page(browser, _api(), width)
    page.goto(ui_mock.BASE + "/sports/results")
    page.wait_for_selector(".rx-sum")
    page.screenshot(path=str(SHOTS / f"results_public_{width}.png"), full_page=True)
    _check_summary(page)
    assert _fits(page)
    assert page.locator(".rx-nv").count() == 0
    page.click('[data-rx-lg="nfl"]')
    page.wait_for_selector('.rx-sum[data-league="nfl"]')
    assert page.locator("[data-rx-nv]").inner_text() == "NFL model not validated"
    assert page.locator("[data-rx-wlp]").inner_text() == "14-16-0" and page.locator(".rx-pick").count() == 4
    assert page.locator(".rx-cmp").get_attribute("data-verdict") == "none"
    # contained league (vg-1): no grade tiles, no letters in Recent, no grade table in Details
    assert page.locator(".rx-tile").count() == 0 and page.locator(".rx-gr").count() == 0
    assert page.locator(".rx-recent .rx-g").count() == 0 and "research letters" not in page.locator(".rx-recent").inner_text()
    assert page.locator('details.rx-d[data-rx="grades"]').count() == 0
    assert page.evaluate("[...document.querySelectorAll('details.rx-d')].every(x => !x.open)") and _fits(page)
    page.screenshot(path=str(SHOTS / f"results_public_nfl_{width}.png"), full_page=True)
    assert not errors, errors
    ctx.close()
