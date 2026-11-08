"""Player roles on the hub board rows in a real browser (owner 2026-11-08, with a screenshot of the hub CS2 list: "i dont see players
roles"). Real Handler + fixtures (no network), the roles_env fixture of tests/test_role_tags_browser.py:
- every sport's row (CS2 "IGL · Rifler", LoL "ADC", NBA "PG", NFL "WR") opens the player cell's second line with the role tag, the
  exact text of the card's tag and of the board record; the "+N more props" sub-rows and a game opened from By game carry it too;
- a player with no role source has no tag element and no placeholder text;
- a start-time board's game header rows span only the columns on screen, so the player column keeps its width;
- the tag never costs the name width or the row height: measured against the same row with the tag taken out, at 1440 (slip
  empty and with a leg on the slip), 1280, 1101, 1100 and 390; desktop rows stay <= 72 px; the tag stays inside its cell; no
  sideways scroll at 390.
ROLE_SHOTS=<folder> keeps a screenshot of every width."""
import re

import pytest

from tests.test_card_and_chips_browser import open_board, sport
from tests.test_role_tags_browser import CASES, NONE, PLACEHOLDER, SHOTS, hub_card, hub_row, record_role, close_card, roles_env  # noqa: F401
from tests.test_upgrade_browser import browser, env, no_h_scroll, DESK, PHONE  # noqa: F401

SIZES = [DESK, {"width": 1280, "height": 900}, {"width": 1101, "height": 900}, {"width": 1100, "height": 900}, PHONE]
ids = lambda s: str(s["width"])  # noqa: E731

# every visible row: its role tag, the name's box and the row's height, then the same row measured again with the tag taken out
MEASURE = """() => { const rows = [...document.querySelectorAll('#hub-rows tr[data-record]')].filter(r => r.offsetParent);
  const box = (tr) => { const b = tr.querySelector('.h-player b'), r = b.getBoundingClientRect(), c = tr.querySelector('td.h-c-player').getBoundingClientRect();
    return {nw: Math.round(r.width * 10) / 10, nh: Math.round(r.height * 10) / 10, cut: b.scrollWidth > b.clientWidth + 1, h: Math.round(tr.getBoundingClientRect().height * 10) / 10,
      nameIn: r.left >= c.left - 1 && r.right <= c.right + 1}; };
  const out = rows.map(tr => { const i = tr.querySelector('.h-role'), cell = tr.querySelector('td.h-c-player').getBoundingClientRect(), sm = tr.querySelector('.h-player small');
    const t = i && i.getBoundingClientRect();
    return Object.assign(box(tr), {name: tr.querySelector('.h-player b').innerText, sub: tr.classList.contains('is-sub'), tag: i ? i.textContent : null,
      tagIn: !t || (t.left >= cell.left - 1 && t.right <= cell.right + 1 && t.width > 0), tagFull: !i || i.scrollWidth <= i.clientWidth + 1,
      line2: Math.round(sm.getBoundingClientRect().height), text: tr.querySelector('.h-player').innerText}); });
  const tags = rows.map(tr => { const i = tr.querySelector('.h-role'); if (!i) return null; const at = [i.parentNode, i.nextSibling]; i.remove(); return [i, at]; });
  const bare = rows.map(box);
  tags.forEach(x => { if (x) x[1][0].insertBefore(x[0], x[1][1]); });
  return out.map((o, k) => Object.assign(o, {bare: bare[k]})); }"""


def shot(p, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"roles-hub-{name}-{p.viewport_size['width']}.png"))


def check_rows(p, size, where):
    got = p.evaluate(MEASURE)
    assert got, where
    for r in got:
        assert r["nw"] == r["bare"]["nw"] and r["nh"] == r["bare"]["nh"] and not r["cut"], (where, r)   # the name keeps its width
        assert r["h"] == r["bare"]["h"], (where, r)                                                     # the tag adds no height
        # the name and the tag stay inside the player cell (a start-time board's game header rows counted a hidden Edge / Market edge
        # column in their colspan: the extra column took the player column's width and hid the names at 1101-1280px)
        assert r["tagIn"] and r["bare"]["nameIn"], (where, r)
        assert r["line2"] <= 18, (where, r)                                                             # the second line stays one line
        if r["tag"] is not None:
            assert r["tag"] == r["tag"].strip() and r["tag"], (where, r)
        else:
            assert not PLACEHOLDER.search(r["text"]), (where, r)
        if size["width"] > 1100:
            assert r["h"] <= 72, (where, r)                                                             # the compact desktop row
        if size["width"] >= 1280:
            assert r["tagFull"], (where, r)                                                             # the whole role is read
    if size["width"] <= 1100:
        no_h_scroll(p)
    return got


def add_a_leg(p):
    """put one offered line on the slip (the desktop slip column then holds a leg)"""
    p.locator("#hub-rows tr[data-record] .h-add", has_text="+ Add").first.click()
    p.wait_for_function("document.querySelector('#hub-slip-count').textContent !== '0'", timeout=20000)
    p.wait_for_function("!document.querySelector('#u-hub').classList.contains('h-slip-empty')", timeout=20000)


@pytest.mark.parametrize("size", SIZES, ids=ids)
def test_every_sports_rows_carry_the_cards_role(browser, roles_env, size):
    p = open_board(browser, roles_env, size)
    try:
        for sp, name, want in CASES:
            sport(p, sp)
            p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
            every = check_rows(p, size, sp)
            assert any(r["tag"] for r in every) or sp != "cs2", (sp, every)
            shot(p, f"board-{sp}")
            row = hub_row(p, name)
            assert row["tag"] == want, (sp, row)
            if size["width"] > 1100:
                assert row["h"] <= 72, (sp, row)
            card = hub_card(p, name)
            assert card["tag"] == row["tag"] == record_role(p, sp, name), (sp, card, row)     # board row = card = record, one text
            close_card(p)
        for sp, name in NONE:
            sport(p, sp)
            row = hub_row(p, name)
            assert row["tag"] is None and not PLACEHOLDER.search(row["text"]), (sp, row)
            assert record_role(p, sp, name) is None
            p.locator("#hub-q").fill("")
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


@pytest.mark.parametrize("size", [DESK, {"width": 1101, "height": 900}], ids=ids)
def test_rows_stay_compact_with_a_leg_on_the_slip(browser, roles_env, size):
    p = open_board(browser, roles_env, size)
    try:
        sport(p, "cs2")
        p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
        add_a_leg(p)
        got = check_rows(p, size, "slip")
        assert [r for r in got if r["name"] == "vargo" and r["tag"] == CASES[0][2]], got
        shot(p, "board-cs2-slip")
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


@pytest.mark.parametrize("size", [DESK, PHONE], ids=ids)
def test_more_props_sub_rows_and_by_game_carry_the_tag(browser, roles_env, size):
    p = open_board(browser, roles_env, size)
    try:
        sport(p, "cs2")
        hub_row(p, "vargo")
        p.locator("#hub-rows .h-prop-toggle").first.click()
        p.wait_for_function("document.querySelectorAll('#hub-rows tr[data-record].is-sub').length > 0", timeout=10000)
        got = check_rows(p, size, "sub-rows")
        subs = [r for r in got if r["sub"]]
        assert subs and all(r["tag"] == CASES[0][2] for r in got), got                    # every vargo line, main row and sub-rows
        shot(p, "board-cs2-subrows")
        p.locator("#hub-q").fill("")
        # By game: the game cards name no player; the game's own board (Show this game's props) draws the same rows
        p.locator("[data-hub=view][data-id=games]").first.click()
        p.wait_for_selector("#hub-games-body [data-hub=game-board]", timeout=20000)
        vargo_game = p.evaluate("""() => { const b = [...document.querySelectorAll('#hub-games-body .h-game-card')].find(c => /Halcyon/.test(c.innerText));
          return b ? b.querySelector('[data-hub=game-board]').dataset.id : null; }""")
        assert vargo_game
        p.locator(f"#hub-games-body [data-hub=game-board][data-id='{vargo_game}']").click()
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record] .h-player b')].some(b => b.innerText === 'vargo')", timeout=20000)
        got = check_rows(p, size, "by-game")
        tags = {r["name"]: r["tag"] for r in got}
        assert tags["vargo"] == CASES[0][2] and tags.get("brandr", None) is None, tags
        shot(p, "by-game-cs2")
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


def test_the_row_tag_is_the_cards_tag_markup_text():
    """one source: the row's tag and the card's tag both print r.player.role, escaped, with the card's .h-role look"""
    js = open("web/upgrade-hub.js", encoding="utf-8").read()
    assert re.search(r"const rowRole = r => r\.player\.role\?`<em class=\"h-role h-row-role\">\$\{esc\(r\.player\.role\)\}</em>`:''", js)
    assert "<small>${rowRole(r)}<span class=\"h-vs\">" in js
    assert "${r.player.role?`<i class=\"h-role\">${esc(r.player.role)}</i>`:''}" in js


SPAN = """() => { const th = [...document.querySelector('#hub-table thead tr').cells].filter(c => getComputedStyle(c).display !== 'none');
  const tr = document.querySelector('#hub-rows tr[data-record]'), t = document.querySelector('#hub-table').getBoundingClientRect();
  const cells = [...tr.cells].filter(c => getComputedStyle(c).display !== 'none'), last = cells[cells.length - 1].getBoundingClientRect();
  return {shown: th.length, spans: [...document.querySelectorAll('#hub-rows tr.h-game-row>td')].map(td => td.colSpan),
    fill: Math.round(t.right - last.right), player: Math.round(tr.querySelector('td.h-c-player').getBoundingClientRect().width)}; }"""


def test_game_header_rows_span_only_the_columns_on_screen(browser, roles_env):
    """the NFL fixture board falls back to start time (no line on it has an edge), so game header rows head its rows; across the
    1280 / 1281 and 1180 / 1181 steps their colspan follows the columns on screen and the rows fill the table (no empty extra column
    taking the player's width)"""
    p = open_board(browser, roles_env, DESK)
    try:
        sport(p, "nfl")
        p.wait_for_selector("#hub-rows tr.h-game-row", timeout=20000)
        for w in (1440, 1200, 1101, 1440, 1150, 1300):
            p.set_viewport_size({"width": w, "height": 900})
            p.wait_for_timeout(150)
            got = p.evaluate(SPAN)
            assert got["spans"] and set(got["spans"]) == {got["shown"]}, (w, got)
            assert got["fill"] <= 1 and got["player"] >= 100, (w, got)                  # the rows reach the table's right edge
            shot(p, f"nfl-start-{w}")
        assert not p.errors, p.errors
    finally:
        p.ctx.close()
