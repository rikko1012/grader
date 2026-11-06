"""Screenshots of every surface the 2026-11-05 sports audit fix changed, at 390 px and 1440 px (ui_mock harness: no
server, no network). Every name / number is a TEST FIXTURE.

    python -m sports.tests.shots_audit_1105 <out dir>
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from sports.tests import test_ui_compare as TC
from sports.tests import test_ui_results as TR
from sports.tests import ui_mock


def _iso(m=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")


def sheet_doc(platform):
    T = _iso(-1)
    ev = {"g1": {"s": _iso(180), "st": "scheduled", "a": "LAL", "h": "UTAH"},
          "g2": {"s": _iso(240), "st": "scheduled", "a": "GS", "h": "BOS"}}

    def q(r, line, v="balanced", o=(1, 1), m=None, f=None):
        d = {"r": f"offer:{platform}:{r}:r1:ab", "p": platform, "l": line, "v": v, "o": list(o), "t": T, "g": 0}
        if m:
            d["m"] = m
        if f:
            d["f"] = f
        return d

    def mk(k, e, n, st, sl, qs):
        return {"k": f"{e}|{k}|{st}|full_game", "e": e, "pid": f"nba:player:espn:{k}", "n": n, "pos": "G", "tm": "LAL",
                "st": st, "sl": sl, "pr": 1, "cap": "supported", "mk": None, "q": qs}
    view = {f"g1|a|points|full_game": {"lines": {"21.5": [6, 4, 0, 10, 3, 2, 0, 5]}},
            f"g1|b|pra|full_game": {"lines": {"35.5": [7, 3, 0, 10, 4, 1, 0, 5]}},
            f"g2|c|rebounds|full_game": {"lines": {"7.5": [3, 7, 0, 10, 1, 4, 0, 5], "5.5": [8, 2, 0, 10, 4, 1, 0, 5]}}}
    markets = [mk("a", "g1", "Fixture Alpha", "points", "Points", [q("a1", 21.5)]),
               mk("b", "g1", "Fixture Bravo", "pra", "Pts+Reb+Ast", [q("b1", 35.5)]),
               mk("c", "g2", "Fixture Charlie", "rebounds", "Rebounds",
                  [q("c1", 7.5), q("c2", 5.5, v="one_sided" if platform == "underdog" else "goblin", o=(1, 0), m=[0.86, None])])]
    return {"version": "sh-1", "league": "nba", "platform": platform, "built_at": T, "board_built_at": T, "events": ev,
            "stats": {}, "markets": markets, "fc": {}, "view": view, "count": 4, "method": {}}


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for width in (390, 1440):
            # board table (A8) + compare (A10)
            api = TC._api()
            ctx = b.new_context(viewport={"width": width, "height": 1000}, base_url=ui_mock.BASE)
            page = ctx.new_page()
            ui_mock.install(page, api)
            page.goto("/sports#/board?lg=nba")
            page.wait_for_selector(".sw-tb-row")
            page.locator(".sw-tb-wrap").screenshot(path=str(out / f"board_{width}.png"))
            for rid in ("offer:prizepicks:a1:r1:ab", "offer:prizepicks:d1:r1:ab", "offer:underdog:c1:r1:ab"):
                page.locator(f'tr[data-rid="{rid}"] [data-cmp-add]').click()
            page.goto("/sports#/compare?lg=nba")
            page.wait_for_selector("[data-cmp-side]")
            page.locator(".sw-cmp-verdict-box").screenshot(path=str(out / f"compare_side_by_side_{width}.png"))
            ctx.close()
            # sheets (S1-S4, A11)
            for sheet in ("underdog", "prizepicks"):
                from sports.tests import test_ui_uifix as U
                api = U._app_api({"/api/sports/sheets": (lambda s: (lambda q: sheet_doc(s)))(sheet)})
                ctx = b.new_context(viewport={"width": width, "height": 1000}, base_url=ui_mock.BASE)
                page = ctx.new_page()
                ui_mock.install(page, api)
                page.add_init_script("try{localStorage.removeItem('scout.sports.sheets.v1')}catch(e){}")
                page.goto(f"/sports#/sheets/{sheet}?lg=nba")
                page.wait_for_selector("#shx-body tr")
                page.screenshot(path=str(out / f"sheet_{sheet}_{width}.png"), full_page=True)
                ctx.close()
            # results (A7)
            ctx = b.new_context(viewport={"width": width, "height": 1000}, base_url=ui_mock.BASE)
            page = ctx.new_page()
            ui_mock.install(page, TR._api())
            page.goto("/sports#/results?lg=nba")
            page.wait_for_selector(".rx-cmp")
            page.screenshot(path=str(out / f"results_{width}.png"), full_page=False)
            ctx.close()
        b.close()
    print("\n".join(sorted(str(x) for x in out.glob("*.png"))))


if __name__ == "__main__":
    main(sys.argv[1])
