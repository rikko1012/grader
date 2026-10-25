"""Compare tool (compare agent, cmp-1): web/sports/compare.js + compare.css, route #/compare, share-link check
GET /api/sports/compare/check (sports/api_compare.py). Node: ranking rule, ineligible rows, same-game warning,
better-book line, share strings (ui_compare_test.js). Browser (ui_mock harness): add from the board table, the
side-by-side page with the verdict, same-game note, actions, device storage, shared link with a moved line refused,
360 px stacked without page overflow. Every name / number here is a TEST FIXTURE."""
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sports import api_board, api_compare, web

WEB = Path(__file__).resolve().parents[2] / "web"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_compare_rules_in_node():
    out = subprocess.run(["node", str(Path(__file__).with_name("ui_compare_test.js")), str(WEB / "sports")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stdout + out.stderr


def test_assets_wired_once_scoped_and_no_money_words():
    page = (WEB / "sports.html").read_text(encoding="utf-8")
    assert page.count('data-view="compare"') == 1 and page.count("/sports/compare.js") == 1 and "/sports/compare.css" in page
    assert page.index("/sports/table.js") < page.index("/sports/compare.js") < page.index("/sports/app.js")
    app = (WEB / "sports" / "app.js").read_text(encoding="utf-8")
    assert app.count("window.SportsCompare.render(") == 1 and app.count("HOOKS.afterActions(") == 1
    css = re.sub(r"/\*.*?\*/", "", (WEB / "sports" / "compare.css").read_text(encoding="utf-8"), flags=re.S)
    for rule in re.findall(r"(?:^|})\s*([^{}@][^{}]*)\{", css):
        for part in rule.strip().split(","):
            if part.strip() and not part.strip().startswith("@"):
                assert part.strip().startswith(".sw .sw-cmp"), part
    js = (WEB / "sports" / "compare.js").read_text(encoding="utf-8")
    shown = " ".join(re.findall(r'"([^"]*)"', js))
    for word in ("ROI", "profit", "EV", "expected value", "joint", "parlay odds"):
        assert not re.search(r"\b" + word + r"\b", shown, flags=re.I), word
    assert "Same game - results are related" in js and "Research" in js and "No pick" in js
    assert "localStorage.setItem" in js and "try {" in js
    assert sum(1 for k in web.ROUTES if k.startswith("/api/sports/compare")) == 1


# ------------------------------------------------------------------------------------------- share-link check
def _board(markets, events):
    return {"markets": markets, "events": events, "fc": {}, "stats": {}}


def _q(oid, p, line):
    return {"r": f"offer:{p}:{oid}:r2:ab", "p": p, "l": line, "o": [1, 1]}


@pytest.fixture()
def snaps(tmp_path, monkeypatch):
    monkeypatch.setattr(api_board, "_root", lambda: tmp_path)
    api_compare._INDEX.clear()
    lg = tmp_path / "nba"
    lg.mkdir()
    fut = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    ev = {"g1": {"start_utc": fut, "status": "scheduled"}, "g2": {"start_utc": past, "status": "scheduled"},
          "g3": {"start_utc": fut, "status": "in_progress"}}
    (lg / "board.json").write_text(json.dumps(_board(
        [{"k": "g1|a|points", "e": "g1", "q": [_q("a1", "prizepicks", 20.5), _q("a~2", "underdog", 21.5)], "kn": []},
         {"k": "g2|b|points", "e": "g2", "q": [_q("b1", "prizepicks", 18.5)], "kn": []}], ev)), encoding="utf-8")
    (lg / "board_more.json").write_text(json.dumps(_board(
        [{"k": "g3|c|rebounds", "e": "g3", "q": [_q("c1", "underdog", 7.5)], "kn": [_q("KX-1", "kalshi", 8)]}], ev)), encoding="utf-8")
    return lg


def _check(items, league="nba"):
    code, body, _ = web.dispatch(web.Request(path="/api/sports/compare/check", query={"league": league, "items": items},
                                             role="member", member="m_1"))
    return code, json.loads(body)


def test_check_accepts_open_lines_and_refuses_moved_pulled_started(snaps):
    code, d = _check("prizepicks~a1~20.5,underdog~a%7E2~22.5,prizepicks~zz~20.5,prizepicks~b1~18.5,underdog~c1~7.5,kalshi~KX-1~8")
    assert code == 200 and d["rule"] == "cmp-1" and d["ok"] == 1
    st = [(i["status"], i.get("text")) for i in d["items"]]
    assert st[0] == ("ok", None) and d["items"][0]["r"] == "offer:prizepicks:a1:r2:ab" and d["items"][0]["k"] == "g1|a|points"
    assert st[1] == ("moved", "Line moved 22.5 -> 21.5") and d["items"][1]["now_line"] == 21.5       # escaped ~ in the id
    assert st[2][0] == "pulled" and "No longer on the board" in st[2][1]
    assert st[3] == ("started", "Game has started")                                                  # start time passed
    assert st[4][0] == "started" and st[5][0] == "started"                                           # live status; Kalshi row too


def test_check_validates_input_and_snapshot_state(snaps, tmp_path):
    assert _check("prizepicks~a1~20.5", league="mlb")[0] == 400
    assert _check("")[0] == 400
    assert _check(",".join(["prizepicks~a1~20.5"] * 7))[0] == 400
    code, d = _check("nonsense,prizepicks~a1~abc")
    assert code == 200 and [i["status"] for i in d["items"]] == ["bad", "bad"]
    code, body, _ = web.dispatch(web.Request(path="/api/sports/compare/check", query={"league": "nba", "items": "prizepicks~a1~20.5"}, role=None))
    assert code == 401                                                                               # members only
    (tmp_path / "nfl").mkdir()
    assert _check("prizepicks~a1~20.5", league="nfl")[0] == 503


def test_check_index_is_cached_per_file_change(snaps, monkeypatch):
    _check("prizepicks~a1~20.5")
    calls = []
    real = api_compare.build_index
    monkeypatch.setattr(api_compare, "build_index", lambda b: calls.append(1) or real(b))
    for _ in range(20):
        _check("prizepicks~a1~20.5")
    assert calls == []                                                                               # no re-parse per request
    b = json.loads((snaps / "board.json").read_text(encoding="utf-8"))
    b["markets"][0]["q"][0]["l"] = 21.0
    (snaps / "board.json").write_text(json.dumps(b) + " ", encoding="utf-8")
    code, d = _check("prizepicks~a1~20.5")
    assert calls == [1] and d["items"][0]["status"] == "moved"


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


def _iso(minutes=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _team(abbr, tid):
    return {"abbr": abbr, "team_id": tid, "name": abbr}


def _fixture_board():
    T = _iso(-1)
    X = lambda ln, pl, e, c: {"v": "cf-2", "ln": ln, "pl": pl, "e": e, "g": 0, "c": c, "cw": ["fixture"], "s": e, "n": 2}  # noqa: E731
    def q(oid, p, line, f, x=None):
        return {"r": f"offer:{p}:{oid}:r1:ab", "p": p, "l": line, "v": "standard", "o": [1, 1], "t": T, "su": T, "g": 1, "f": f, **({"x": x} if x else {})}
    def m(k, e, name, tm, tid, quotes, at, stat="points"):
        return {"k": k, "e": e, "pid": f"nba:player:espn:{k}", "n": name, "pos": "G", "tm": tm, "tid": tid, "st": stat, "pr": 1,
                "cap": "supported", "q": quotes, "kn": [], "kr": [], "alt": [], "promo": [], "mk": {"at": at}, "sc": 1}
    mk = lambda po: {"po": po, "pu": round(1 - po, 4), "pp": 0, "n": 3}  # noqa: E731
    fc = {"f1": {"m": "sm-1", "mu": 24.0, "po": 0.62, "pu": 0.38, "pp": 0, "at": T, "iv": [18, 30]},
          "f2": {"m": "sm-1", "mu": 23.0, "po": 0.60, "pu": 0.40, "pp": 0, "at": T},
          "f3": {"m": "sm-1", "mu": 23.0, "po": 0.58, "pu": 0.42, "pp": 0, "at": T},
          "f4": {"m": "sm-1", "mu": 6.0, "po": 0.30, "pu": 0.70, "pp": 0, "at": T},
          "f5": {"m": "sm-1", "mu": 20.6, "po": 0.51, "pu": 0.49, "pp": 0, "at": T}}
    events = {"g1": {"start_utc": _iso(180), "status": "scheduled", "home": _team("BOS", "nba:team:espn:2"), "away": _team("NY", "nba:team:espn:18")},
              "g2": {"start_utc": _iso(240), "status": "scheduled", "home": _team("GS", "nba:team:espn:9"), "away": _team("LAL", "nba:team:espn:13")}}
    markets = [
        m("a", "g1", "Fixture Alpha", "BOS", "nba:team:espn:2",
          [q("a1", "prizepicks", 20.5, "f1", X("over", 0.62, 6, 4)), q("a2", "underdog", 21.5, "f3", X("over", 0.58, 6, 3))],
          {"offer:prizepicks:a1:r1:ab": mk(0.56), "offer:underdog:a2:r1:ab": mk(0.52)}),
        m("b", "g1", "Fixture Bravo", "NY", "nba:team:espn:18", [q("b1", "prizepicks", 20.5, "f2", X("over", 0.60, 6, 3))],
          {"offer:prizepicks:b1:r1:ab": mk(0.54)}),
        m("c", "g2", "Fixture Charlie", "GS", "nba:team:espn:9", [q("c1", "underdog", 7.5, "f4", X("under", 0.70, 3, 3))],
          {"offer:underdog:c1:r1:ab": mk(0.33)}, stat="rebounds"),
        m("d", "g2", "Fixture Delta", "LAL", "nba:team:espn:13",
          [q("d1", "prizepicks", 20.5, "f5", {"b": "ratio_low", "t": "Held: projection is under 25% of the line", "v": "sg-1"})], {}),
    ]
    return {"markets": markets, "events": events, "fc": fc, "stats": {"points": "Points", "rebounds": "Rebounds"},
            "injuries": {}, "models": [{"model_version": "sm-1", "settled": 0, "forecasts": 5}]}


def _api(check=None):
    from sports.tests import test_ui_uifix as U
    B = _fixture_board()

    def board(q):
        if q.get("part") == "view":
            return {"rows": {}}
        if q.get("part") == "more":
            return {"markets": [], "events": {}, "fc": {}}
        return B
    return U._app_api({"/api/sports/board": board, "/api/sports/compare/check": check or (lambda q: (404, {"error": "x"}))})


def _page(browser, answers, width=1280):
    from sports.tests import ui_mock
    ctx = browser.new_context(viewport={"width": width, "height": 900}, base_url=ui_mock.BASE)
    page = ctx.new_page()
    return ctx, page, ui_mock.install(page, answers)


def test_add_from_board_compare_verdict_same_game_and_storage(browser):
    ctx, page, errors = _page(browser, _api())
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    for rid in ("offer:prizepicks:a1:r1:ab", "offer:prizepicks:b1:r1:ab", "offer:underdog:c1:r1:ab", "offer:prizepicks:d1:r1:ab"):
        page.locator(f'tr[data-rid="{rid}"] [data-cmp-add]').click()
    assert page.locator('#sw-views a[data-view="compare"] .sw-cmp-n').inner_text() == "4"
    assert "Added to Compare (4 of 6)" in page.locator("#sw-toast").inner_text()
    btn = page.locator('tr[data-rid="offer:prizepicks:a1:r1:ab"] [data-cmp-add]')
    assert btn.get_attribute("aria-pressed") == "true" and btn.inner_text() == "In Compare"
    page.goto("/sports#/compare?lg=nba")
    page.wait_for_selector(".sw-cmp-card")
    assert page.locator('#sw-views a[aria-current="page"]').get_attribute("data-view") == "compare"
    # verdict: a (edge 6, Scout 62%) > b (edge 6, Scout 60%) > c (edge 3); d held -> No pick with the reason
    assert page.locator(".sw-cmp-best").get_attribute("data-best") == "offer:prizepicks:a1:r1:ab"
    ranks = page.evaluate("() => [...document.querySelectorAll('.sw-cmp-card')].map(c => c.dataset.rid + '=' + c.querySelector('[data-rank]').dataset.rank)")
    assert ranks == ["offer:prizepicks:a1:r1:ab=1", "offer:prizepicks:b1:r1:ab=2", "offer:underdog:c1:r1:ab=3", "offer:prizepicks:d1:r1:ab=none"], ranks
    vb = page.locator(".sw-cmp-verdict-box").inner_text()
    assert "Better line = the highest edge" in vb and "No pick: Fixture Delta" in vb and "Held: projection is under 25% of the line" in vb
    assert "Same game - results are related" in vb and "Fixture Alpha Points, Fixture Bravo Points" in vb
    assert "joint" not in vb.lower()
    first = page.locator(".sw-cmp-card").first.text_content()
    for txt in ("Over 62.0%", "Over 56.0%", "(3 books)", "+6.0", "4/5", "Research", "80% range 18.0–30.0", "Same game", "line checked"):
        assert txt in first, (txt, first)
    assert page.locator(".sw-cmp-sgw").count() == 2                                                # g1: a + b, g2: c + d
    # same player + stat on two books: Over -> lowest line (20.5 PrizePicks), Under -> highest (21.5 Underdog)
    bk = page.locator('.sw-cmp-bk[data-key="a"]')
    assert bk.locator('[data-bestbook="over"]').get_attribute("data-bestline") == "20.5"
    assert bk.locator('[data-bestbook="under"]').get_attribute("data-bestline") == "21.5"
    assert "Scout 62.0%" in bk.locator('[data-bestbook="over"]').inner_text()
    # actions from the comparison (the ux agent's selector): Add to slip after choosing a side
    card = page.locator(".sw-cmp-card").first
    assert card.locator("[data-act-add]").is_disabled()
    card.locator("label.sw-act-side.over").click()
    card.locator("[data-act-add]").click()
    leg = page.evaluate("() => JSON.parse(localStorage.getItem('ss:draft:nba:prizepicks')).legs[0]")
    assert leg["record_id"] == "offer:prizepicks:a1:r1:ab" and leg["side"] == "over" and abs(leg["p_win"] - 0.62) < 1e-9
    stored = page.evaluate("() => JSON.parse(localStorage.getItem('scout.sports.compare.v1')).nba.map(x => x.p + '~' + x.o + '~' + x.l)")
    assert stored == ["prizepicks~a1~20.5", "prizepicks~b1~20.5", "underdog~c1~7.5", "prizepicks~d1~20.5"]
    page.reload(); page.wait_for_selector(".sw-cmp-card")
    assert page.locator(".sw-cmp-card").count() == 4                                               # kept on the device
    page.locator('.sw-cmp-card[data-rid="offer:prizepicks:d1:r1:ab"] [data-cmp-rm]').click()
    page.wait_for_function("document.querySelectorAll('.sw-cmp-card').length === 3")
    assert page.locator(".sw-cmp-sgw").count() == 1                                                # c alone in its game now
    assert page.locator('.sw-cmp-card[data-rid="offer:underdog:c1:r1:ab"] .sw-cmp-sg').count() == 0
    page.click("#sw-league button[data-lg=nfl]")
    page.wait_for_selector(".sw-cmp .sw-state")
    assert "Nothing to compare yet" in page.locator("#sw-main").inner_text()                        # per league
    assert not errors, errors
    ctx.close()


def test_shared_link_refuses_moved_line_and_saves(browser):
    def check(q):
        items = q["items"].split(",")
        out = [{"item": items[0], "p": "prizepicks", "o": "a1", "l": 20.5, "status": "ok", "r": "offer:prizepicks:a1:r1:ab", "k": "a"},
               {"item": items[1], "p": "underdog", "o": "c1", "l": 6.5, "status": "moved", "now_line": 7.5, "text": "Line moved 6.5 -> 7.5"}]
        return {"rule": "cmp-1", "league": "nba", "checked_at": _iso(), "items": out, "ok": 1}
    ctx, page, errors = _page(browser, _api(check))
    page.goto("/sports#/compare?lg=nba&c=" + "prizepicks~a1~20.5,underdog~c1~6.5".replace(",", "%2C"))
    page.wait_for_selector(".sw-cmp-shared")
    body = page.locator("#sw-main").inner_text()
    assert "1 of 2 props are still open at the same line" in body
    assert "Not loaded from the link" in body and "Fixture Charlie 6.5 on Underdog - Line moved 6.5 -> 7.5" in body
    assert page.locator(".sw-cmp-card").count() == 1
    page.click("#sw-cmp-keep")
    page.wait_for_function("!location.hash.includes('c=')")
    page.wait_for_selector(".sw-cmp-card")
    stored = page.evaluate("() => JSON.parse(localStorage.getItem('scout.sports.compare.v1')).nba.map(x => x.o)")
    assert stored == ["a1"]
    assert not errors, errors
    ctx.close()


def test_phone_360_stacked_verdict_first(browser):
    ctx, page, errors = _page(browser, _api(), width=360)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row, .sw-research-grid")
    page.evaluate("""() => localStorage.setItem('scout.sports.compare.v1', JSON.stringify({nba: [
      {p: 'prizepicks', o: 'a1', l: 20.5}, {p: 'underdog', o: 'c1', l: 7.5}, {p: 'prizepicks', o: 'b1', l: 20.5}]}))""")
    page.goto("/sports#/compare?lg=nba")
    page.wait_for_selector(".sw-cmp-card")
    over = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 1, over
    boxes = page.evaluate("() => [...document.querySelectorAll('.sw-cmp-verdict-box, .sw-cmp-card')].map(e => { const r = e.getBoundingClientRect(); return [r.top, r.left, r.width]; })")
    assert boxes[0][0] < boxes[1][0] < boxes[2][0] < boxes[3][0]                                   # verdict on top, cards stacked
    assert all(b[2] <= 360 for b in boxes)
    assert not errors, errors
    ctx.close()


def test_player_card_action_row_carries_compare(browser):
    from sports.tests import test_ui_uifix as U
    ctx, page, errors = _page(browser, _api())
    page.goto("/sports#/player/" + U.PID + "?lg=nba&stat=points")
    page.wait_for_selector(".sw-act")
    btn = page.locator(".sw-act .sw-act-btns [data-cmp-add]").first           # inside the Add to slip / Track pick row
    assert btn.count() == 1 and btn.inner_text() == "Compare"
    btn.click()
    it = page.evaluate("() => JSON.parse(localStorage.getItem('scout.sports.compare.v1')).nba[0]")
    assert it["p"] == "prizepicks" and it["o"] == "prizepicks0" and it["l"] == 20.5 and it["pid"] == U.PID and it["st"] == "points"
    assert btn.get_attribute("aria-pressed") == "true"
    assert not errors, errors
    ctx.close()
