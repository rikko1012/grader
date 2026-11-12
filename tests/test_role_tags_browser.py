"""Player role tags in a real browser (owner 2026-11-07: "put the roles of every player prop on their cards also under their name or
wherever it'll fit - Like IGL, Star rifler, Top mid, Sf PF, WR"). Real Handler + fixtures (no network), 1440 / 390 / 360 / 414:
- the hub card header carries the role under the name for CS2 ("IGL · Rifler"), LoL ("ADC"), NBA ("PG") and NFL ("WR"): the
  same text as the board record the row is drawn from;
- the hub rows carry the same tag on the player cell's second line (owner 2026-11-08 "i dont see players roles"; it sits before the
  matchup, never beside the name) and stay compact (<= 72 px) on a desktop; no sideways scroll at 360 / 390 / 414
  (tests/test_role_hub_rows_browser.py covers widths, sub-rows and By game);
- a player with no role source has no tag element and no placeholder text ("unknown", "Player", "AWP" from a stat reading);
- classic CS2: one role pill on the board row (in place of the AWP / IGL pills), the same text in the card hero and the
  matchroom desk; the card's "IGL reference list" row is gone.
ROLE_SHOTS=<folder> keeps a screenshot of every case."""
import json
import os
import re
from pathlib import Path

import pytest

import book_providers as bp
import server
import workspace_books as wb
from tests.test_card_and_chips_browser import open_board, sport
from tests.test_audit13_site_ui import KEPT, _outage_card, needs_node, run_page
from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

SHOTS = Path(os.environ["ROLE_SHOTS"]) if os.environ.get("ROLE_SHOTS") else None
PHONES = [{"width": w, "height": 844} for w in (360, 390, 414)]
CS2_ROLE = "IGL · Rifler"
CASES = [("cs2", "vargo", CS2_ROLE), ("lol", "Harky", "ADC"), ("nba", "Reed Lean", "PG"), ("nfl", "Dee Route", "WR")]
NONE = [("cs2", "brandr"), ("lol", "Guigo")]
PLACEHOLDER = re.compile(r"\b(unknown|Player|AWP)\b")


def shot(p, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"roles-{name}-{p.viewport_size['width']}.png"))


@pytest.fixture
def roles_env(env, monkeypatch):
    """the role texts each source gives: CS2 role_label on the board rows (role_display.py), LoL "role" as the worker now
    writes it, sports "ro" on the compact rows. brandr / Guigo have no source; brandr carries a stat-read AWP chip."""
    for k in server.DEFAULT_ON:                                  # the flags production runs (as the card tests)
        monkeypatch.setenv(k, "1")
    bp.reset(); wb.reset()
    monkeypatch.setattr(bp, "start", lambda d: False)
    for r in env.rows:
        r.pop("role_label", None)
        if r["nick"] == "vargo":
            r.update(role_label=CS2_ROLE, igl=True)
        if r["nick"] == "brandr":
            r.update(awp=True, awp_conf="inferred", awp_src="HS% 31 (3 months) - AWPer range")
    for r in env.lol.rows:
        r["role"] = {"Harky": "ADC", "Guigo": None}.get(r.get("player"), r.get("role"))
    env.lol.write()
    ro = {"Reed Lean": "PG", "Karl Anders": "C", "Dee Route": "WR", "Sam Field": "QB"}
    for lg in ("nba", "nfl"):
        for m in env.sports.boards[lg][0]["markets"]:
            if m["n"] in ro:
                m["ro"] = ro[m["n"]]
    yield env
    bp.reset(); wb.reset()


def hub_row(p, name):
    p.locator("#hub-q").fill(name)
    p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record] .h-player b')].some(b => b.innerText === {name!r})", timeout=20000)
    return p.evaluate("""(name) => { const b = [...document.querySelectorAll('#hub-rows tr[data-record] .h-player b')].find(x => x.innerText === name);
      const tr = b.closest('tr'), i = tr.querySelector('.h-role');
      return {tag: i ? i.textContent : null, h: Math.round(tr.getBoundingClientRect().height), text: tr.querySelector('.h-player').innerText}; }""", name)


def record_role(p, sport_, name):
    """the role on the board record the hub draws the row from (/api/upgrade/board), as the page reads it"""
    recs = p.evaluate("(s) => fetch('/api/upgrade/board?sport=' + s, {credentials: 'same-origin'}).then(r => r.json()).then(d => d.records.map(x => x.player))", sport_)
    got = {x.get("role") or None for x in recs if x["name"] == name}
    assert len(got) == 1, got
    return got.pop()


def hub_card(p, name):
    p.locator("#hub-rows tr[data-record] .h-player", has_text=name).first.click()
    p.wait_for_selector("#hub-detail .h-card-id p", timeout=20000)
    p.wait_for_timeout(200)
    return p.evaluate("""() => { const pp = document.querySelector('#hub-detail .h-card-id p'), i = pp.querySelector('.h-role');
      return {tag: i ? i.textContent : null, text: pp.innerText}; }""")


def close_card(p):
    p.locator("#hub-detail [data-hub=card-close]").first.click()
    p.wait_for_function("!document.querySelector('#hub-detail .h-card-id')", timeout=10000)
    p.locator("#hub-q").fill("")                                     # the next sport's rows are not filtered by this name


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_hub_card_carries_the_records_role_and_rows_stay_compact(browser, roles_env, size):
    p = open_board(browser, roles_env, size)
    try:
        for sp, name, want in CASES:
            sport(p, sp)
            row = hub_row(p, name)
            assert row["tag"] == want, (sp, name, row)                                # owner 2026-11-08: the row carries it too
            if size["width"] > 1100:
                assert row["h"] <= 72, (sp, row)                                      # the compact desktop row
            shot(p, f"row-{sp}")
            card = hub_card(p, name)
            assert card["tag"] == want == record_role(p, sp, name), (sp, card)     # the card = the board's record, one text
            shot(p, f"card-{sp}")
            no_h_scroll(p)
            close_card(p)
        for sp, name in NONE:
            sport(p, sp)
            row = hub_row(p, name)
            assert row["tag"] is None and not PLACEHOLDER.search(row["text"]), (sp, row)
            assert record_role(p, sp, name) is None
            card = hub_card(p, name)
            assert card["tag"] is None and not PLACEHOLDER.search(card["text"]), (sp, card)
            close_card(p)
        assert not p.errors
    finally:
        p.ctx.close()


@pytest.mark.parametrize("size", PHONES)
def test_phone_cards_carry_the_tag_and_never_scroll_sideways(browser, roles_env, size):
    p = open_board(browser, roles_env, size)
    try:
        for sp, name, want in CASES:
            sport(p, sp)
            row = hub_row(p, name)
            assert row["tag"] == want
            no_h_scroll(p)
            card = hub_card(p, name)
            assert card["tag"] == want
            no_h_scroll(p)
            close_card(p)
        assert not p.errors
    finally:
        p.ctx.close()


def classic_board(c, env, size, path="/?view=classic#board"):
    p = open_page(c, env, path, size)
    p.wait_for_selector("#boardout .brow2.crow", timeout=30000)
    p.wait_for_timeout(300)
    return p


ROW = """(nick) => [...document.querySelectorAll('#boardout .brow2.crow')].filter(r => r.querySelector('.c-who b').textContent === nick)
  .map(r => { const w = r.querySelector('.c-who'), pill = w.querySelector('.pill.role');
    return {pill: pill ? pill.textContent : null, pills: [...w.querySelectorAll('.pill')].map(x => x.textContent), text: w.innerText}; })"""


@pytest.mark.parametrize("size", [DESK, PHONE, PHONES[0]])
def test_classic_board_row_card_and_matchroom_agree(browser, roles_env, size):
    c = context(browser, roles_env, size)
    try:
        card = dict(_outage_card(dict(KEPT, side="More"), {"rows": [], "causes": []}), role_label=CS2_ROLE)
        card["player_block"].update(player={"nick": "vargo", "id": 1}, current_team="Halcyon")
        p = classic_board(c, roles_env, size)
        p.route("**/api/grade?*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(card)))
        rows = p.evaluate(ROW, "vargo")
        assert rows and all(r["pill"] == CS2_ROLE for r in rows), rows
        none = p.evaluate(ROW, "brandr")
        assert none and all(r["pill"] is None and r["pills"] == [] and not PLACEHOLDER.search(r["text"]) for r in none), none
        no_h_scroll(p)
        if SHOTS:
            p.locator("#boardout .brow2.crow", has_text="brandr").first.scroll_into_view_if_needed()
        shot(p, "classic-board")
        # the card: the hero names the role next to the team, the same text as the row
        p.locator("#boardout .brow2.crow", has_text="vargo").first.click()
        p.wait_for_selector("#cardpop .herocard:not([data-instant]) .sub .rtag", timeout=20000)
        hero = p.evaluate("[...document.querySelectorAll('#cardpop .herocard:not([data-instant]) .sub .rtag')].map(x => x.textContent)")
        assert hero == [CS2_ROLE]
        body = p.locator("#cardpop").inner_text()
        assert "IGL reference list" not in body and "Not listed" not in body
        no_h_scroll(p)
        shot(p, "classic-card")
        p.keyboard.press("Escape")
        p.wait_for_function("document.querySelector('#cardpop').hidden")
        # the matchroom desk of vargo's match
        p.goto(roles_env.url + "/?view=classic#matches/all")
        p.wait_for_selector("[data-ot-match]", timeout=20000)
        p.locator("[data-ot-match]").first.click()
        p.wait_for_function("document.querySelector('#ot-match-desk').open")
        desk = p.evaluate("""() => [...document.querySelectorAll('#ot-match-desk .ot-desk-player')].map(d => ({n: d.querySelector('strong').textContent,
            tag: (d.querySelector('small') || {}).textContent || null}))""")
        by = {d["n"]: d["tag"] for d in desk}
        assert by.get("vargo") == CS2_ROLE and by.get("brandr") is None, desk
        assert all(t in (None, CS2_ROLE) for t in by.values()) and "Player" not in by.values()
        no_h_scroll(p)
        shot(p, "classic-matchroom")
        assert not p.errors
    finally:
        c.close()


@needs_node
def test_instant_card_and_hero_carry_the_rows_role(tmp_path):
    card = dict(_outage_card(dict(KEPT, side="More"), {"rows": [], "causes": []}), role_label=CS2_ROLE)
    data = {"row": {"nick": "vargo", "team_name": "Halcyon", "opp_name": "Norrland", "side": "More", "line": 38.5, "prop": "kills", "scope": "12",
                    "p": 0.61, "units": 0.5, "grade": "B", "model_grade": "B", "state": "GRADE", "book": "underdog", "role_label": CS2_ROLE},
            "bare": {"nick": "brandr", "team_name": "Norrland", "side": "Less", "line": 34.5, "prop": "kills", "scope": "12", "p": 0.58, "units": 0.5,
                     "grade": "B", "model_grade": "B", "state": "GRADE", "book": "underdog", "awp": True, "igl": False},
            "card": card, "nocard": dict(card, role_label=None)}
    out = run_page(tmp_path, """(() => { const r = {instant: instantHtml(INPUT.row), bare: instantHtml(INPUT.bare)};
      for (const k of ["card", "nocard"]) { try { render(INPUT[k]); } catch (e) { if (!/nextSibling/.test(e.message)) throw e; } r[k] = $("#out").innerHTML; }
      return r; })()""", data)
    assert f'<span class="rtag">{CS2_ROLE}</span>' in out["instant"] and "rtag" not in out["bare"]
    assert re.sub(r"<[^>]+>", " ", out["bare"]).count("AWP") == 0
    assert f'Vitality · <span class="rtag">{CS2_ROLE}</span>' in out["card"]
    assert "rtag" not in out["nocard"] and "IGL reference list" not in out["card"] + out["nocard"]


AWP_NOTES = {
    "none": None,                                                                     # ZywOo, no reading: no "AWPer" from a list
    "hltv": "AWPer (HLTV weapon stats: 27% of 1204 kills with the AWP (90 days to 2026-11-06))",
    "rifle": "Rifler (HLTV weapon stats: 0% of 880 kills with the AWP (90 days to 2026-11-06))",
    "liq": "Rifler (Liquipedia via HLTV scraper (cxzi: rifle, awp, igl), checked 2026-11-06)",
    "bo3": "AWPer (body-shot kill rate 0.61 >= 0.55)",
    "hs": "AWPer (Maps 1-2 HS% 31 < 35 with KPR 0.81)",
    "unsure": "AWPer status unclear (body-shot kill rate 0.48, between 0.4 and 0.55)",
    "unknown": "AWP: unknown (only 12 body-shot kills on record)",
}


@needs_node
def test_card_checks_show_the_awp_reading_never_a_role_word(tmp_path):
    """review 1107: the Checks row printed "AWPer" for a known-AWPer nick and "AWPer / Rifler (HLTV weapon stats ...)" from the AWP kill
    share - a second role word on the card that could contradict the tag. Now: the stat alone, or no row."""
    base = dict(_outage_card(dict(KEPT, side="More"), {"rows": [], "causes": []}), role_label="Rifler · AWPer")
    data = {k: dict(base, player_block=dict(base["player_block"], awp_note=v)) for k, v in AWP_NOTES.items()}
    out = run_page(tmp_path, r"""(() => { const r = {};
      for (const k of Object.keys(INPUT)) { try { render(INPUT[k]); } catch (e) { if (!/nextSibling/.test(e.message)) throw e; }
        const h = $("#out").innerHTML, m = /<span>AWP use<\/span><span>([^<]*)<\/span>/.exec(h);
        r[k] = {row: m ? m[1] : null, tags: (h.match(/class="rtag"/g) || []).length, words: (h.replace(/<[^>]+>/g, " ").match(/\b(AWPer|Rifler)\b/g) || [])}; }
      return r; })()""", data)
    assert out["hltv"]["row"] == "HLTV weapon stats: 27% of 1204 kills with the AWP (90 days to 2026-11-06)"
    assert out["rifle"]["row"] == "HLTV weapon stats: 0% of 880 kills with the AWP (90 days to 2026-11-06)"
    assert out["bo3"]["row"] == "body-shot kill rate 0.61 &gt;= 0.55"
    assert out["hs"]["row"] == "Maps 1-2 HS% 31 &lt; 35 with KPR 0.81"
    for k in ("none", "liq", "unsure", "unknown"):
        assert out[k]["row"] is None, (k, out[k])
    for k, v in out.items():
        assert v["tags"] == 1, (k, v)
        assert v["words"] == ["Rifler", "AWPer"], (k, v)                                 # the tag's two words, nowhere else
