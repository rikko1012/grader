"""The combined CS2 role text in a real browser (desktop 1440 + phone 390): the Liquipedia listing + the cs2_roles stat entry,
computed by server.card_role_label (the same inputs as the board row - tests/test_roles_combined.py), shown identically on the
classic board row pill, the card hero, the matchroom desk and the hub card. vargo: listed "rifle, awp" + the team's AWPer by
HLTV's 47% share -> "AWPer · Rifler" (AWPer first); brandr: no listing, stat role -> "Star rifler". ROLE_SHOTS=<folder> keeps
screenshots."""
import json

import pytest

import cs2_roles
import roles
import server
from tests.test_audit13_site_ui import KEPT, _outage_card
from tests.test_card_and_chips_browser import sport
from tests.test_role_tags_browser import ROW, classic_board, close_card, hub_card, hub_row, record_role, roles_env, shot  # noqa: F401
from tests.test_card_and_chips_browser import open_board
from tests.test_upgrade_browser import browser, env, context, no_h_scroll, DESK, PHONE  # noqa: F401

WANT = {"vargo": "AWPer · Rifler", "brandr": "Star rifler"}


def entry(nick, team, stat_role, source=None, share=None):
    return {"nick": nick, "team": team, "tid": "t-" + team, "role": stat_role, "stat_role": stat_role, "igl": False, "igl_block": False,
            "map_note": None, "source": source, "maps": 90, "last": "2026-11-06", "signals": {}, "pct": {}, "awp_share": share}


def fulfil(body):
    return lambda route: route.fulfill(status=200, content_type="application/json", body=body)


def pb(nick, team):
    return {"player": {"nick": nick, "id": 1}, "playing_for": team, "current_team": team, "profile_team": team, "benched": []}


@pytest.fixture
def combined(roles_env, monkeypatch):
    monkeypatch.delenv("CS2_STAT_ROLES", raising=False)
    monkeypatch.setitem(roles._HLTV, "rows", {"vargo": [dict(team="Halcyon", wiki="Vargo", roles=["rifle", "awp"], igl=False, awper=True,
                                                             team_match=True, updated="2026-11-06", kills=900, awp_kills=423,
                                                             awp_share=0.47, end="2026-11-05")]})
    monkeypatch.setitem(roles._STATE, "data", {"teams": {}, "players": {}})
    monkeypatch.setattr(cs2_roles, "_STATE", {"art": None, "by_nick": {}, "at": 0.0, "tried": 0.0, "error": None, "loaded": True})
    cs2_roles._install({"players": {"1": entry("vargo", "Halcyon", "AWPer", "hltv-awp", 0.47),
                                    "2": entry("brandr", "Norrland", "Star rifler", "stats", 0.01)}, "teams": {}, "counts": {}}, 1.0)
    for r in roles_env.rows:
        team = r.get("team_name") or r.get("team")
        r["role_label"] = server.card_role_label({"player_block": pb(r["nick"], team)}) if r["nick"] in WANT else None
        r.pop("awp", None) if r["nick"] == "brandr" else None
    assert {r["nick"]: r["role_label"] for r in roles_env.rows if r["nick"] in WANT} == WANT
    return roles_env


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_classic_row_card_and_matchroom_show_the_combined_text(browser, combined, size):
    c = context(browser, combined, size)
    try:
        p = classic_board(c, combined, size)
        for nick, want in WANT.items():
            rows = p.evaluate(ROW, nick)
            assert rows and all(r["pill"] == want for r in rows), (nick, rows)
        no_h_scroll(p)
        shot(p, "combined-classic-board")
        for nick, team in (("vargo", "Halcyon"), ("brandr", "Norrland")):
            card = dict(_outage_card(dict(KEPT, side="More"), {"rows": [], "causes": []}))
            card["player_block"].update(pb(nick, team))
            server.card_role_label(card)                                   # the real card path: sets card["role_label"]
            assert card["role_label"] == WANT[nick]
            p.unroute("**/api/grade?*")
            p.route("**/api/grade?*", fulfil(json.dumps(card, default=str)))
            p.locator("#boardout .brow2.crow", has_text=nick).first.click()
            p.wait_for_selector("#cardpop .herocard:not([data-instant]) .sub .rtag", timeout=20000)
            hero = p.evaluate("[...document.querySelectorAll('#cardpop .herocard:not([data-instant]) .sub .rtag')].map(x => x.textContent)")
            assert hero == [WANT[nick]], (nick, hero)
            no_h_scroll(p)
            shot(p, f"combined-classic-card-{nick}")
            p.keyboard.press("Escape")
            p.wait_for_function("document.querySelector('#cardpop').hidden")
        p.goto(combined.url + "/?view=classic#matches/all")
        p.wait_for_selector("[data-ot-match]", timeout=20000)
        seen = {}
        for i in range(p.locator("[data-ot-match]").count()):
            p.locator("[data-ot-match]").nth(i).click()
            p.wait_for_function("document.querySelector('#ot-match-desk').open")
            for d in p.evaluate("""() => [...document.querySelectorAll('#ot-match-desk .ot-desk-player')].map(d => ({n: d.querySelector('strong').textContent,
                tag: (d.querySelector('small') || {}).textContent || null}))"""):
                seen.setdefault(d["n"], d["tag"])
            no_h_scroll(p)
            shot(p, f"combined-matchroom-{i}")
            p.keyboard.press("Escape")
            p.wait_for_function("!document.querySelector('#ot-match-desk').open")
        for nick, want in WANT.items():
            assert seen.get(nick) == want, (nick, seen)
        assert set(seen.values()) <= {None, *WANT.values()}, seen
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_hub_card_shows_the_combined_text(browser, combined, size):
    p = open_board(browser, combined, size)
    try:
        sport(p, "cs2")
        for nick, want in WANT.items():
            row = hub_row(p, nick)
            assert row["tag"] == want, (nick, row)                          # owner 2026-11-08: the hub row carries it too
            card = hub_card(p, nick)
            assert card["tag"] == want == row["tag"] == record_role(p, "cs2", nick), (nick, card)
            no_h_scroll(p)
            shot(p, f"combined-hub-card-{nick}")
            close_card(p)
        assert not p.errors
    finally:
        p.ctx.close()
