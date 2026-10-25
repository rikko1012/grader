"""rg-1 research grades (owner decision 2026-10-25): sports-gp-1 band() letters on the unrounded chosen-side chance,
labelled "Research grade - not validated", only for a lean under cf-2 / N4, never for a held row (sg-1, vg-1 when
contained), NFL labelled "NFL model not validated", the same letter on every surface, and the letter recorded with a
tracked pick. Every name, line and probability here is a TEST FIXTURE."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sports import board_view, picks, settlement as settlement_mod, store, web
from sports.model import grade_policy
from sports.slips import pool
from sports.tests import slip_world as w

WEB = Path(__file__).resolve().parents[2] / "web" / "sports"
L10 = [5, 5, 0, 10]


def test_band_boundaries_are_grade_policy_bands():
    rg = board_view.research_grade
    assert rg(0.60) == "A" and rg(0.5999999) == "B"
    assert rg(grade_policy.BE_HIGH) == "B" and rg(grade_policy.BE_HIGH - 1e-9) == "C"
    assert rg(grade_policy.BE_LOW) == "C" and rg(grade_policy.BE_LOW - 1e-9) == "Lean"
    assert rg(0.5001) == "Lean" and rg(None) is None
    # the letter comes from the UNROUNDED chance: 0.59996 rounds to 0.6000 on the wire but is a B
    x, _ = board_view.quote_x("nba", "points", 0.59996, 0.40004, 25.0, 20.5, None, L10)
    assert x["pl"] == 0.6 and x["gr"] == "B" and x["gp"] == grade_policy.VERSION


def test_no_grade_without_a_lean_or_on_held_rows():
    # no lean: chance <= 50%, or mean on the other side of the line
    x, _ = board_view.quote_x("nba", "points", 0.50, 0.50, 20.5, 20.5, None, L10)
    assert x["ln"] is None and "gr" not in x
    x, _ = board_view.quote_x("nba", "points", 0.62, 0.38, 19.0, 20.5, None, L10)
    assert x["ln"] is None and "gr" not in x
    # N4: the market prices the side higher -> no lean, no grade
    x, _ = board_view.quote_x("nba", "points", 0.62, 0.38, 25.0, 20.5, {"po": 0.70, "pu": 0.30, "n": 3}, L10)
    assert x["ln"] is None and "gr" not in x
    # sg-1 holds (ratio, L10 contradiction): no grade
    x, code = board_view.quote_x("nba", "points", 0.0, 1.0, 0.1, 11.5, None, L10)
    assert code == "ratio_low" and "gr" not in x and x["b"] == "ratio_low"
    x, code = board_view.quote_x("nba", "points", 0.62, 0.38, 25.0, 20.5, None, [3, 7, 0, 10])
    assert code == "l10_contra" and "gr" not in x
    # positive edge -> graded
    x, _ = board_view.quote_x("nba", "points", 0.62, 0.38, 25.0, 20.5, {"po": 0.55, "pu": 0.45, "n": 3}, L10)
    assert x["ln"] == "over" and x["gr"] == "A" and x["e"] > 0


def test_nfl_contained_by_default_again():
    # owner 2026-10-25: NFL letters off after the real book-line check (A pass_yds 1 of 27); one flag lifts it again
    assert board_view.VG_CONTAIN_LEAN is True


def test_nfl_lean_and_grade_lifted_behind_the_rules_with_label(monkeypatch):
    monkeypatch.setattr(board_view, "VG_CONTAIN_LEAN", False)
    x, code = board_view.quote_x("nfl", "rec_yds", 0.3, 0.7, 40.0, 50.5, {"po": 0.45, "pu": 0.55, "n": 3}, L10)
    assert code is None and x["ln"] == "under" and x["gr"] == "A" and x["nv"] == "NFL model not validated"
    assert "e" not in x and "s" not in x and "g" not in x and x["c"] is None       # no edge / confidence / rank
    # the rules still apply to NFL: sanity hold, N4
    x, code = board_view.quote_x("nfl", "rec_yds", 0.0, 1.0, 0.1, 11.5, None, L10)
    assert code == "ratio_low" and "gr" not in x
    x, _ = board_view.quote_x("nfl", "rec_yds", 0.3, 0.7, 40.0, 50.5, {"po": 0.2, "pu": 0.8, "n": 3}, L10)
    assert x["ln"] is None and "gr" not in x
    # NFL is never a slip leg (selection stays contained)
    from types import SimpleNamespace
    f = SimpleNamespace(model_version="sm-1", stat="rec_yds", projection_mean=40.0, line=50.5, chosen_side="under",
                        forecast_id="f1")
    assert pool.guard_reason(f, "nfl").startswith("model not validated")
    # one flag turns the containment back on
    monkeypatch.setattr(board_view, "VG_CONTAIN_LEAN", True)
    x, _ = board_view.quote_x("nfl", "rec_yds", 0.3, 0.7, 40.0, 50.5, {"po": 0.45, "pu": 0.55, "n": 3}, L10)
    assert x["b"] == "gate" and "gr" not in x
    assert board_view.contain_reason("nfl", "rec_yds") == "NFL model not validated - projection only"


# --------------------------------------------------------------------------- cross-surface consistency + pick record
@pytest.fixture
def con():
    c = store.connect()
    yield c
    c.close()


@pytest.fixture
def gw(monkeypatch, con):
    monkeypatch.setattr(web, "_CACHE", {})
    monkeypatch.setattr(picks, "clock", lambda: w.NOW)
    legs = w.world(con, [("h1", "T1", 1, "over", 0.61, {"_f": {"model": "sm-1"}}),
                         ("h2", "T2", 2, "under", 0.545, {"_f": {"model": "sm-1"}})])
    yield con, legs


def _req(path, body=None, member="m_owner"):
    return web.Request(path=path, method="POST" if body is not None else "GET", query={},
                       body=json.dumps(body).encode() if body is not None else b"", member=member, role="member")


def _call(req):
    code, body, _ = web.dispatch(req)
    return code, json.loads(body)


def _item(rid, f, side, grade):
    return {"platform": "underdog", "record_id": rid, "side": side, "forecast_id": f.forecast_id, "grade": grade,
            "shown": {"p_over": f.p_over, "p_under": f.p_under, "p_push": f.p_push}, "surface": "board"}


def test_same_letter_on_board_sheet_slip_and_tracked_pick(gw):
    con, legs = gw
    (r1, f1), (r2, f2) = legs["h1"], legs["h2"]
    # board row / player file / sheet: all carry quote_x (snapshots.guard_row and sheets call the same function)
    bx1, _ = board_view.quote_x("nba", f1.stat, f1.p_over, f1.p_under, f1.projection_mean, f1.line, None, L10)
    bx2, _ = board_view.quote_x("nba", f2.stat, f2.p_over, f2.p_under, f2.projection_mean, f2.line, None, L10)
    assert (bx1["gr"], bx2["gr"]) == ("A", "C")
    # slip leg
    built = pool.build(con, "nba", "underdog", now=w.NOW)
    by = {l["record_id"]: l for l in built["legs"]}
    assert by[r1]["research_grade"] == "A" and by[r2]["research_grade"] == "C"
    # tracked pick (server recomputes the letter from the forecast record and stores the one shown)
    assert picks.research_grade(f1, "over", "nba") == "A" and picks.research_grade(f2, "under", "nba") == "C"
    assert picks.research_grade(f1, "under", "nba") is None                    # not the lean side: no letter
    _call(_req("/api/sports/picks/track", {"picks": [_item(r1, f1, "over", "A"), _item(r2, f2, "under", "B")]}))
    picks.drain()
    pc = picks.connect()
    picks.ingest(pc)
    rows = {json.loads(p)["record_id"]: json.loads(p) for (p,) in pc.execute("SELECT payload FROM picks")}
    assert rows[r1]["grade_shown"] == "A" and rows[r1]["grade_rule"] == "rg-1"
    assert rows[r2]["grade_shown"] is None and rows[r2]["grade_not_recorded"] == "B"    # never a wrong letter
    # settled picks keep the letter they had when tracked + per-grade table with n and clustered CI fields
    settlement_mod.record(con, [w.settlement(store.get(con, r1), "over", "win", 25.0),
                                w.settlement(store.get(con, r2), "under", "loss", 30.0)])
    picks.settle_all(con, pc)
    pc.close()
    code, mine = _call(_req("/api/sports/picks/mine"))
    assert code == 200
    got = {p["record_id"]: p for p in mine["picks"]}
    assert got[r1]["grade_shown"] == "A" and got[r1]["result"]["status"] == "win"
    G = {r["grade"]: r for r in mine["by_grade"]["rows"]}
    assert list(G) == ["A", "B", "C", "Lean"] and mine["by_grade"]["label"].startswith("Research grade - not validated")
    assert G["A"]["n"] == 1 and G["A"]["hit_rate"] == 1.0 and "hit_rate_ci95_games" in G["A"]
    assert mine["by_grade"]["ungraded"] == 1
    agg = picks.aggregate(picks.connect_ro())
    assert agg["by_grade"]["rows"][0]["n"] == 1 and agg["by_grade_league"]["nba"]["rows"][0]["grade"] == "A"


def test_results_grades_table():
    from sports import results_public
    rows = [{"status": "settled", "lean": "over", "p_over": 0.61, "p_under": 0.39, "lean_result": "win",
             "event_id": "e1", "game": "e1"},
            {"status": "settled", "lean": "under", "p_over": 0.45, "p_under": 0.55, "lean_result": "loss",
             "event_id": "e2", "game": "e2"},
            {"status": "settled", "lean": None, "p_over": 0.5, "p_under": 0.5, "lean_result": None,
             "event_id": "e3", "game": "e3"}]
    try:
        G = results_public.grades(rows)
    except KeyError as e:                          # cluster key name of the results rows
        pytest.fail(f"results rows need key {e}")
    by = {r["grade"]: r for r in G["rows"]}
    assert G["label"] == "Research grade - not validated" and by["A"]["n"] == 1 and by["A"]["hit_rate"] == 1.0
    assert by["C"]["n"] == 1 and by["C"]["hit_rate"] == 0.0 and by["B"]["n"] == 0 and by["Lean"]["n"] == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_table_renders_the_server_letter_in_node(tmp_path):
    js = tmp_path / "t.js"
    js.write_text("""
global.SportsLabels = require(process.argv[2] + "/labels.js");
const R = require(process.argv[2] + "/table.js");
const T = new Date(Date.now() - 60000).toISOString();
const fc = {f1: {id: "f1", m: "sm-1", mu: 25, po: 0.62, pu: 0.38, pp: 0, at: T}};
const ev = {start_utc: "2099-01-01T00:00:00Z"};
const q = {r: "r1", p: "underdog", l: 20.5, o: [1, 1], t: T, f: "f1",
           x: {v: "cf-2", ln: "over", pl: 0.62, gr: "A", c: 3, cw: [], n: 0}};
const base = {k: "k", pid: "p", n: "P", st: "points", e: "e", ev, kn: []};
let r = R.propRow(Object.assign({}, base, {q: [q]}), q, {fc, view: {}, now: Date.now()});
if (r.grade !== "A") throw new Error("grade " + r.grade);
const qn = Object.assign({}, q, {x: {v: "cf-2", ln: "under", pl: 0.7, gr: "A", c: null, cw: [], n: 0, nv: "NFL model not validated"}});
r = R.propRow(Object.assign({}, base, {q: [qn]}), qn, {fc, view: {}, now: Date.now()});
if (r.grade !== "A" || r.notValidated !== "NFL model not validated" || r.edge !== null || r.conf !== null) throw new Error("nfl");
const qh = Object.assign({}, q, {x: {b: "ratio_low", t: "Held"}});
r = R.propRow(Object.assign({}, base, {q: [qh]}), qh, {fc, view: {}, now: Date.now()});
if (r.grade) throw new Error("held row graded");
const h = SportsLabels.gradeHtml("Lean", "under", "NFL model not validated");
if (h.indexOf("Lean Under") < 0 || h.indexOf("Research grade") < 0 || h.indexOf("NFL model not validated") < 0) throw new Error(h);
if (SportsLabels.gradeWords("B", "over") !== "B Over") throw new Error("words");
console.log("ok");
""", encoding="utf-8")
    out = subprocess.run(["node", str(js), str(WEB)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stdout + out.stderr


def test_no_raw_gp_band_text_in_member_scripts():
    for p in WEB.glob("*.js"):
        txt = p.read_text(encoding="utf-8")
        assert not re.search(r"['\"]gp_band:", txt), p.name           # raw codes are filtered, letters rendered


# ------------------------------------------------------------------------------------- rendered (ui_mock harness)
@pytest.fixture(scope="module")
def browser():
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                               # noqa: BLE001 - no browser binary on this machine
            pytest.skip(f"chromium not available: {e}")
        yield b
        b.close()


def _page(browser, answers):
    from sports.tests import ui_mock
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, base_url=ui_mock.BASE)
    page = ctx.new_page()
    return ctx, page, ui_mock.install(page, answers)


def test_slip_leg_and_my_picks_render_the_letter(browser):
    from sports.tests import test_ui_uifix as U
    legs = [dict(U._leg(1, True), research_grade="A"), dict(U._leg(2, True), research_grade="Lean"), U._leg(3, True)]
    pool_doc = dict(U.POOL_RESEARCH, legs=legs)
    ctx, page, errors = _page(browser, U._slips_api(pool_doc))
    U._open_slips(page)
    page.click("#ss-build")
    page.wait_for_selector(".ss-card")
    gs = [g.inner_text().strip() for g in page.locator(".ss-card .sw-grade").all()]
    assert gs == ["A Over", "Lean Over"], gs
    assert "research grade" in page.locator(".ss-card").first.inner_text().lower()
    assert not errors, errors
    ctx.close()

    def mine(q):
        d = U._mine(q)
        d["picks"] = [dict(p, grade_shown="B" if i == 0 else None) for i, p in enumerate(d["picks"])]
        d["by_grade"] = {"label": picks.GRADE_TABLE_LABEL, "rule": "rg-1", "ungraded": 1,
                         "rows": [{"grade": g, "picks": 1 if g == "B" else 0, "n": 1 if g == "B" else 0,
                                   "wins": 1 if g == "B" else 0, "losses": 0, "pushes": 0,
                                   "hit_rate": 1.0 if g == "B" else None, "hit_rate_ci95_games": None}
                                  for g in ("A", "B", "C", "Lean")]}
        return d
    ctx, page, errors = _page(browser, {"/api/sports/picks/mine": mine,
                                        "/api/sports/manifest": {"models": [], "platforms": {}}})
    page.goto("/sports#/picks?lg=nba")
    page.wait_for_selector(".sw-mp-grades")
    body = page.locator("#sw-main").inner_text()
    assert "Hit rate by grade" in body and "research grade - not validated" in body.lower()
    page.locator('[data-mptab="settled"]').click()
    assert page.locator(".sw-mp-row .sw-grade").first.inner_text().strip() == "B Over"
    assert "gp_band:" not in body
    assert not errors, errors
    ctx.close()


def test_board_table_renders_letter_banner_and_nfl_label(browser):
    from sports.tests import test_ui_uifix as U
    ctx, page, errors = _page(browser, U._app_api())
    page.goto("/sports#/slate?lg=nba")
    page.wait_for_selector(".sw-game")
    html = page.evaluate("""() => {
      const T = new Date(Date.now() - 60000).toISOString();
      const ev = {start_utc: "2099-01-01T00:00:00Z"};
      const q = (r, x) => ({r, p: "underdog", l: 20.5, o: [1, 1], t: T, f: "f1", x});
      const m = (k, x) => ({k, pid: "nba:player:espn:" + k, n: "Player " + k, st: "points", e: "e" + k, ev, kn: [], q: [q("r" + k, x)]});
      const B = {fc: {f1: {id: "f1", m: "sm-1", mu: 25, po: 0.62, pu: 0.38, pp: 0, at: T}}, view: {}, stats: {points: "Points"}, events: {}};
      const rows = [m("1", {v: "cf-2", ln: "over", pl: 0.62, gr: "A", e: 5, s: 5, c: 3, cw: [], n: 2}),
                    m("2", {v: "cf-2", ln: "over", pl: 0.62, gr: "Lean", c: null, cw: [], n: 2, nv: "NFL model not validated"}),
                    m("3", {b: "ratio_low", t: "Held: projection is under 25% of the line", v: "sg-1"})];
      return SportsTable.html({lg: "nba", B, rows, href: () => "#", bookName: (p) => p, now: Date.now()}, {sort: "player"});
    }""")
    page.set_content("<div class='sw'>" + html + "</div>")
    leans = [c.inner_text().strip() for c in page.locator(".sw-tb-leanc").all()]
    assert leans[0].startswith("A Over") and "research grade" in leans[0].lower(), leans
    assert leans[1].startswith("Lean Over") and "nfl model not validated" in leans[1].lower(), leans
    assert leans[2].startswith("Held") and page.locator(".sw-tb-leanc").nth(2).locator(".sw-grade").count() == 0
    assert page.locator(".sw-tb-row").nth(1).locator(".sw-tb-edge").inner_text().strip() == "—"
    assert not errors, errors
    ctx.close()
