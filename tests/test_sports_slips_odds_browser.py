"""Owner 2026-11-12 ("sports should be able to go in slips now", "make sure they all have grades and odds"), in a real browser against
the real Handler and fixture data (no network): an NBA / NFL line with a letter adds to the slip from the board and passes Scout's own
slip check; Auto-build with NBA / NFL ticked uses their legs (no opt-in, no research label); every NBA / NFL row
shows a letter, a Lean or the plain reason it has none, and an odds line (the app's payout or needed price, and a sportsbook's price
at that exact line or "No book odds at this line"). Display and slip pool only: no grade, chance, side or stake moves."""
import re

import pytest

from tests.test_audit_board_browser import FLAGS, board, sport
from tests.test_research_hub_browser import saved_state
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, DESK  # noqa: F401

GRADE = re.compile(r"^(A\+|A-|A|B\+|B-|B|C|Lean|Lean (Over|Under)|Over only|Under only)$")


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    for k in FLAGS:
        monkeypatch.setenv(k, "1")


ROWS = """() => [...document.querySelectorAll('#hub-rows tr[data-record]')].map(tr => ({
  key: tr.dataset.record,
  grade: [...tr.querySelectorAll('.h-c-scout .h-grade, .h-c-scout .h-grade-text, .h-c-scout .h-one-sided')].map(e => e.textContent.trim()),
  why: [...tr.querySelectorAll('.h-c-scout .h-hold-why')].map(e => [e.textContent.trim(), e.title]),
  odds: [...tr.querySelectorAll('.h-c-line .h-odds')].map(e => [e.textContent.trim(), e.title]),
  add: tr.querySelector('.h-c-add button')?.textContent.trim(),
}))"""


def test_every_sports_row_shows_a_grade_or_its_reason_and_odds(ctx, env):
    p = board(ctx, env)
    seen = 0
    for lg in ("nba", "nfl"):
        sport(p, lg)
        rows = p.evaluate(ROWS)
        assert rows, lg
        for r in rows:
            seen += 1
            book = r["key"].split("|")[1]
            # a letter / Lean / one-sided label, or the plain reason (with its full sentence) - never the old status label
            assert r["why"] or any(GRADE.match(g) for g in r["grade"]), r
            assert all(t and t != "Research estimate" and title for t, title in r["why"]), r
            assert r["odds"] and all(t for t, _ in r["odds"]), r
            if book in ("prizepicks", "underdog"):
                books = r["odds"][-1][0]
                assert books == "No book odds at this line" or re.search(r"[+−]\d{3}|\d\.\d\d", books), r
            assert "Research grade" not in " ".join(r["grade"])
    assert seen


def test_book_odds_on_a_pickem_row_are_the_same_line_only(ctx, env):
    p = board(ctx, env)
    sport(p, "nba")
    bad = p.evaluate("""() => {
      const out = [];
      for (const tr of document.querySelectorAll('#hub-rows tr[data-record]')) {
        const o = [...tr.querySelectorAll('.h-c-line .h-odds')].pop(); if (!o) continue;
        const line = parseFloat(tr.querySelector('td.h-c-line>b').firstChild.textContent);
        const m = /exactly ([\\d.]+)/.exec(o.title);
        if (m && parseFloat(m[1]) !== line) out.push([tr.dataset.record, o.title]);
      }
      return out; }""")
    assert not bad, bad


def test_nba_leg_adds_from_the_board_and_passes_the_slip_check(ctx, env):
    p = board(ctx, env)
    sport(p, "nba")
    btn = p.locator('#hub-rows tr[data-record^="nba|"] .h-c-add button.h-add:not([disabled])', has_text="+ Add").first
    key = btn.evaluate("b => b.closest('tr').dataset.record")
    btn.click()
    state = saved_state(p, lambda d: any(l["sport"] == "nba" for l in ((d.get("slip") or {}).get("legs") or [])))
    leg = next(l for l in state["slip"]["legs"] if l["sport"] == "nba")
    assert key.split("|")[2] == leg["row_id"]
    doc = p.evaluate("""async (l) => { const r = await fetch('/api/upgrade/recheck', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({idem: 'tsl' + Date.now(), legs: [{id: l.id, sport: l.sport, book: l.book,
      row_id: l.row_id, side: l.side, line: l.line, rev: l.rev, read_rev: l.read_rev, shown_p: l.shown_p, team: l.team, player: l.player,
      event: l.event_id}], entry_types: {[l.book]: l.book === 'prizepicks' ? 'power' : 'standard'}})}); return [r.status, await r.json()]; }""", leg)
    assert doc[0] == 200 and doc[1]["legs"][leg["id"]]["ok"], doc
    assert "Research grade" not in leg["shown_label"]


def test_auto_build_includes_nba_and_nfl_legs_when_ticked(ctx, env):
    p = open_page(ctx, env, '/#/slips?tab=build', DESK)
    p.wait_for_selector('#hub-generate')
    assert p.locator('#hub-generation-research').count() == 0                     # no opt-in: released letters are in
    p.locator('#hub-generation-sports input[value=lol]').uncheck()        # the fixture has one NBA/NFL letter per app: it pairs with CS2
    p.locator('#hub-generation-book').select_option('prizepicks')
    p.locator('#hub-generation-type').select_option('power')
    p.locator('#hub-generation-count').select_option('2')
    p.locator('#hub-generation-slips').select_option('8')
    p.locator('#hub-generation-lines').select_option('all')
    p.locator('#hub-generate').click()
    p.wait_for_function(r"document.querySelector('#hub-generated .h-generated-card li') || /\w/.test(document.querySelector('#hub-generation-status').textContent) && !document.querySelector('#hub-generate').disabled", timeout=30000)
    legs = p.locator('#hub-generated .h-generated-card li')
    assert legs.count(), p.locator('#hub-generation-status').inner_text()
    sports = {x.split(' · ')[0] for x in legs.locator('div > small:first-of-type').all_inner_texts()}
    assert sports & {"NBA", "NFL"}, sports
    assert "research estimate" not in p.locator('#hub-generated').inner_text().lower()
