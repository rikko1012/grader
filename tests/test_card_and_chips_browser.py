"""card-and-chips (owner 2026-11-06: "there's not enough data in the cards people have no idea why they should take the props", "on
phone the L5/10/20 h2h look clumped together", "why are dabble and other books not showing L5/10/20"). Real Handler + fixtures shaped
like the live research docs of 2026-11-06 (NFL game logs + targets averages + injury report + factor-engine rows; LoL model factors +
recent form + series totals), with the flags production runs (server.DEFAULT_ON), 1440 and 360 / 390 / 414:
- the "Why" section only lists facts that point to the side; facts against it go under "What could go wrong"; even records, the
  format and other lines under "Also good to know"; the forecast is never green under 50%;
- dots only where a grade exists (CS2 / LoL); NBA / NFL (letters held) show none, and factor-engine rows are worded as not used;
- LoL compares series totals with the series line (no per-game average against a 3-map line), its model factors in plain words;
- NFL / LoL / NBA cards show L5 / L10 / L20 counts (never "—" when the player has games) and at least three facts;
- the player's own definite injury status leads "What could go wrong"; the teammates-out count covers the whole report;
- phone board: Sleeper / Dabble rows on NBA AND NFL show counted L5 / L10 / L20 in the labelled strip; a row with no results at all
  has no strip line; "Over-Under" counts are labelled.
CARD_SHOTS=<folder> keeps a screenshot of every case."""
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import book_providers as bp
import server
import workspace_books as wb
from tests.test_card_why_books_browser import offer
from tests.test_phase4_browser import lines_read, page, SHOWN_BOOKS
from tests.test_pickem_readers import event_of
from tests.test_upgrade_browser import browser, env, context, no_h_scroll, DESK  # noqa: F401
from tests.workspace_other_fixture import iso

SHOTS = Path(os.environ['CARD_SHOTS']) if os.environ.get('CARD_SHOTS') else None
PHONES = [{"width": w, "height": 844} for w in (360, 390, 414)]
PHONE = PHONES[1]
JARGON = ('model inputs', 'margin removed', 'model factors', 'pace factor', 'opponent factor', 'share of team output', 'devig',
          'questionable', 'day_to_day', 'day-to-day', 'no side', 'no saved chance', 'undefined', 'nan', 'null')


def nfl_games(stat, values, opp="KC"):
    t = datetime.now(timezone.utc) - timedelta(days=5)
    return {"version": "ui-snap-1", "league": "nfl", "player_id": "x", "family": "football",
            "games": [{"event_id": f"n{i}", "start_utc": iso(t - timedelta(days=7 * i)), "season": 2026, "team": {"abbr": "BUF"}, "opponent": {"team_id": "t", "abbr": opp if i % 3 == 0 else "MIA", "name": opp},
                       "home": bool(i % 2), "participation": "played", "parts": {}, "values": {stat: float(v)}, "period_values": {}, "source": ["espn"]} for i, v in enumerate(values)]}


def nfl_player(name, pos, stats, injury=None, mates=(), engine=()):
    return {"version": "ui-snap-1", "player_id": "x", "player": name, "position": pos, "status": "active", "team": {"abbr": "BUF"}, "injury": injury,
            "next_event": {"event_id": "e", "home": True, "opponent": {"abbr": "KC"}}, "markets": [],
            "factors": {"stats": [{"key": k, "label": label, "windows": [{"window": w, "n": 3, "mean": v} for w, v in zip(("L3", "L5", "L10", "Season 2026"), vals)]} for k, label, vals in stats],
                        "context": [{"key": "rest_days", "label": "Days since last game", "value": 6.0}, {"key": "venue", "label": "Venue", "value": "home"},
                                    {"key": "teammates_out", "label": "Teammates on the injury report", "value": len(mates), "items": [{"player": n, "status": s} for n, s in mates]}],
                        "engine": {"rows": [{"group": "Defensive matchup", "label": lab, "value": v, "n": 8} for lab, v in engine]}, "not_held": []}}


# eight teammates on the report, five of them definitely out, two of the outs past the first six rows the doc lists
MATES = [("Ty One", "out"), ("Al Two", "questionable"), ("Bo Three", "out"), ("Cy Four", "day_to_day"), ("Di Five", "out"), ("Ed Six", "questionable"),
         ("Fa Seven", "out"), ("Gi Eight", "injured_reserve")]
# LoL forecast inputs as the worker saves them (lol/center/forecast.py _drivers wording), from the live Caliste row of 2026-11-06
LOL_INPUTS = {"drivers": ["player share of team output 5.06 (125 earlier games)", "team pace factor 0.92", "opponent factor 0.88", "match-strength factor 0.99 (win chance 60%)"],
              "recent": {"games": 10, "per_game": 5.1}, "median": 13, "team_win": {"elo": 0.598, "market": {"found": True, "p": 0.175}}}


@pytest.fixture
def rich(env, monkeypatch):
    """the production flags, real-shaped NFL / LoL research, and Sleeper / Dabble lines on CS2, NBA and NFL (no network)"""
    for k in server.DEFAULT_ON:
        monkeypatch.setenv(k, '1')
    for k in ('SCOUT_BOOK_FANDUEL', 'SCOUT_BOOK_BETRIVERS'):
        monkeypatch.delenv(k, raising=False)
    for k in ('SCOUT_BOOK_SLEEPER', 'SCOUT_BOOK_DABBLE'):
        monkeypatch.setenv(k, '1')
    sp = env.sports
    sp.history["nfl:player:espn:402"] = nfl_games("receptions", [6, 4, 7, 5, 6, 3, 8, 6, 4, 7, 5, 6, 2, 7])
    sp.history["nfl:player:espn:401"] = nfl_games("pass_yds", [281, 240, 302, 255, 270, 233, 290, 262, 249, 301])
    sp.players["nfl:player:espn:402"] = nfl_player("Dee Route", "WR", [("tgt", "Targets", (9.3, 8.6, 7.4, 7.6)), ("rec", "Receptions", (6.3, 5.8, 5.4, 5.5))], mates=MATES[:3])
    sp.players["nfl:player:espn:401"] = nfl_player("Sam Field", "QB", [("att", "Pass attempts", (31.0, 33.2, 34.1, 34.0))], injury={"status": "out", "detail": "Ankle"},
                                                     mates=MATES, engine=[("Passing yards allowed per game to QB", 241.5)])
    for r in env.lol.rows[:2]:                              # the first LoL player (the one with saved past series)
        r["forecast"]["inputs"] = dict(r["forecast"].get("inputs") or {}, **LOL_INPUTS)
        r["forecast"]["grade"] = "B+"                     # graded, as the live LoL rows are (lol-1f, 0U)
    env.lol.write()
    bp.reset(); wb.reset()
    monkeypatch.setattr(bp, 'start', lambda d: False)
    o = lambda *a, **k: dict(offer(*a, **k), raw_stat=a[5])
    sl = [o('sleeper', 'cs2', 'vargo', 'Halcyon', 37.5, 'kills', 'maps12', '1'),
          o('sleeper', 'nba', 'Karl Anders', 'NYK', 20.5, 'points', 'full_game', '4'), o('sleeper', 'nba', 'Reed Lean', 'CHI', 7.5, 'assists', 'full_game', '3'),
          o('sleeper', 'nfl', 'Dee Route', 'BUF', 5.0, 'receptions', 'full_game', '11'), o('sleeper', 'nfl', 'Sam Field', 'KC', 259.5, 'pass_yds', 'full_game', '12')]
    db = [o('dabble', 'cs2', 'vargo', 'Halcyon', 38.5, 'kills', 'maps12', '5', price=True),
          o('dabble', 'nba', 'Karl Anders', 'NYK', 20.5, 'points', 'full_game', '8', price=True), o('dabble', 'nba', 'Reed Lean', 'CHI', 5.5, 'assists', 'full_game', '7', price=True),
          o('dabble', 'nfl', 'Dee Route', 'BUF', 4.5, 'receptions', 'full_game', '13', price=True), o('dabble', 'nfl', 'Sam Field', 'KC', 264.5, 'pass_yds', 'full_game', '14', price=True)]
    real = wb.records

    def with_events(srv, sport):                    # each extra line carries its board match's teams and start, as the feeds do
        recs, s = real(srv, sport)
        for x in sl + db:
            rec = next((r for r in recs or () if r['player']['name'] == x['player'] and r['sport'] == x['sport']), None)
            if rec:
                x.update(event_of(rec), team=rec['player']['team'], team_norm=bp.norm(rec['player']['team']))
        return recs, s
    monkeypatch.setattr(wb, 'records', with_events)
    bp.state('sleeper').update(at=time.time(), offers=sl)
    bp.state('dabble').update(at=time.time(), offers=db)
    yield env
    bp.reset(); wb.reset()


def shot(p, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"{name}-{p.viewport_size['width']}.png"))


def open_board(browser, env, size):
    c = context(browser, env, size)
    p = page(c, env)
    p.ctx = c
    lines_read(p, '/#all-sports')
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')", timeout=30000)
    p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
    return p


def sport(p, s):
    p.locator(f'#hub-sports [data-id={s}]').click()
    p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].length && [...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('{s}|'))", timeout=20000)


def open_card(p, name=None):
    if name:
        p.locator('#hub-q').fill(name)
        p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record] .h-player b')].some(b => b.innerText === {name!r})", timeout=20000)
        p.locator('#hub-rows tr[data-record] .h-player', has_text=name).first.click()
    else:
        p.locator('#hub-rows tr[data-record] .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-why', timeout=20000)
    p.wait_for_selector('#hub-detail .h-history-summary', timeout=20000)
    p.wait_for_function("!document.querySelector('#hub-detail .h-history-note') || !document.querySelector('#hub-detail .h-why').innerText.includes('Loading the rest')", timeout=20000)
    p.wait_for_timeout(250)


WHY = """() => { const w = document.querySelector('#hub-detail .h-why');
  const items = sel => { const ul = w.querySelector(sel); return ul ? [...ul.querySelectorAll(':scope > li')].map(li => ({text: li.querySelector(':scope > span:last-child').innerText.trim(),
    used: !!li.querySelector('.h-why-dot.is-used'), dot: !!li.querySelector('.h-why-dot'), cls: li.className})) : []; };
  const body = document.querySelector('#hub-card-body');
  return {title: w.querySelector('h4').innerText, head: w.querySelector('.h-sec-head').innerText, scout: items('.h-why-scout'), for: items('.h-why-for'), risk: items('.h-why-risk'),
          also: items('.h-why-also'), heads: [...w.querySelectorAll('h5')].map(h => h.innerText), key: w.querySelector('.h-why-key')?.innerText || null,
          dots: w.querySelectorAll('.h-why-dot').length, first: [...body.children].findIndex(e => e === w) <= 1,
          tiles: [...document.querySelectorAll('#hub-detail .h-history-summary .h-tile')].slice(0, 3).map(t => t.querySelector('b').innerText.replace('\\n', '').trim()),
          member: w.innerText}; }"""


def check_why(w, sp, extra=False):
    """the rules every card's Why section keeps, on any sport"""
    assert w['first'], w
    assert w['title'].startswith(('Why Scout leans Over', 'Why Scout leans Under', 'The numbers for Over', 'The numbers for Under')), w['title']
    assert w['head'].strip() == w['title'].strip(), w['head']                            # no status line beside the title
    side = 'Over' if ' Over ' in w['title'] + ' ' else 'Under'
    assert w['scout'] and re.match(r'Scout (projects|gives)', w['scout'][0]['text']), w['scout']
    # the forecast's colour follows its chance on the side shown: never green under 50%
    m = re.search(r'([\d.]+)% chance (Over|Under)', w['scout'][0]['text'])
    if m:
        c = float(m.group(1))
        assert ('is-for' in w['scout'][0]['cls']) == (c > 50) and ('is-against' in w['scout'][0]['cls']) == (c < 50), w['scout'][0]
    assert all('is-for' in x['cls'] for x in w['for']), w['for']                         # under "points to": only facts for the side
    assert all('is-against' in x['cls'] for x in w['risk']), w['risk']
    assert all('is-for' not in x['cls'] and 'is-against' not in x['cls'] for x in w['also']), w['also']
    for x in w['for'] + w['risk']:                                                        # a 50-50 record is never a reason
        r = re.search(r'in (\d+) of (?:the last )?(\d+)', x['text'])
        if r and x['text'].startswith(('Went', 'Vs')):
            assert int(r.group(1)) * 2 != int(r.group(2)), x
    if w['for']:
        assert any(h.endswith(f'to {side}') for h in w['heads']), w['heads']
    text = w['member'].lower()
    for word in JARGON:
        assert not re.search(r'(?<![\w-])' + re.escape(word) + r'(?![\w-])', text), (word, w['member'])
    if sp in ('nba', 'nfl') or (extra and w['key'] is None):   # letters held / an extra line with no letter: no grade, no dot at all
        assert w['dots'] == 0 and w['key'] is None, w
    else:
        assert w['key'] and 'Used in the grade' in w['key'] and 'Not used in the grade' in w['key'], w
        assert all(x['dot'] for x in w['scout'] + w['for'] + w['risk'] + w['also'])
        assert all(not x['used'] for x in w['for'] + w['risk'] + w['also']), w            # weight-0 facts are never "used"
        assert w['scout'][0]['used']


CARDS = [('cs2', None), ('lol', 'Guigo'), ('nba', 'Karl Anders'), ('nfl', 'Dee Route'), ('nfl', 'Sam Field')]


@pytest.mark.parametrize('size', [DESK, PHONE], ids=['1440', '390'])
def test_every_sport_card_explains_the_prop_with_real_data(browser, rich, size):
    p = open_board(browser, rich, size)
    try:
        for sp, name in CARDS:
            sport(p, sp)
            open_card(p, name)
            w = p.evaluate(WHY)
            check_why(w, sp)
            facts = w['scout'] + w['for'] + w['risk'] + w['also']
            assert len(facts) >= 3, (sp, w)
            assert all(t not in ('—', '') for t in w['tiles']), (sp, name, w['tiles'])     # L5 / L10 / L20 counted, never dashes with games on file
            for cls in ('h-history-summary', 'h-history-panel'):
                assert p.locator(f'#hub-detail .{cls}').count() == 1
            no_h_scroll(p)
            shot(p, f'card-{sp}{"-" + name.split()[0].lower() if name else ""}')
            p.locator('#hub-card [data-hub="card-close"]').first.click() if p.locator('#hub-card [data-hub="card-close"]').count() else p.keyboard.press('Escape')
            p.wait_for_selector('#hub-card', state='hidden', timeout=10000)
            p.locator('#hub-q').fill('')
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


def test_nfl_card_injury_teammates_usage_and_engine_rows(browser, rich):
    p = open_board(browser, rich, DESK)
    try:
        sport(p, 'nfl')
        open_card(p, 'Sam Field')
        w = p.evaluate(WHY)
        assert w['risk'] and w['risk'][0]['text'].startswith('Sam Field is listed out on the injury report'), w['risk']
        mates = [x['text'] for x in w['also'] + w['for'] + w['risk'] if x['text'].startswith('Teammates out')]
        # five definite outs on an eight-name report (two of them past the six rows the stats tab lists): four names and "1 more"
        assert mates == ['Teammates out: Ty One, Bo Three, Di Five, Fa Seven and 1 more'], mates
        # factor-engine rows (weight 0) never sit with Scout's reasons; where the card has room for them they say they are not used
        facts = w['scout'] + w['for'] + w['risk'] + w['also']
        assert all('241.5' not in x['text'] for x in w['scout'] + w['for'] + w['risk']), facts
        assert all('does not use' in x['text'] for x in facts if '241.5' in x['text']), facts
        assert 'Model inputs' not in p.locator('#hub-detail').inner_text()
        p.locator('#hub-detail [data-hub=card-tab][data-id=stats]').click()
        p.wait_for_timeout(200)
        stats = p.locator('#hub-detail').inner_text()
        assert "matchup numbers (not part of scout's forecast)" in stats.lower() and 'model inputs' not in stats.lower()
        p.locator('#hub-q').fill('')
        p.keyboard.press('Escape')
        open_card(p, 'Dee Route')
        w = p.evaluate(WHY)
        usage = [x for x in w['for'] + w['risk'] + w['also'] if x['text'].startswith('Targets')]
        assert usage and '9.3 last 3' in usage[0]['text'] and '7.4 last 10' in usage[0]['text'], w
        went = [x['text'] for x in w['for'] + w['risk'] + w['also'] if x['text'].startswith('Went ')]
        assert went, w
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


def test_lol_card_compares_series_totals_and_words_the_model_plainly(browser, rich):
    p = open_board(browser, rich, DESK)
    try:
        sport(p, 'lol')
        open_card(p, 'Guigo')
        w = p.evaluate(WHY)
        texts = [x['text'] for x in w['scout'] + w['for'] + w['risk'] + w['also']]
        avg = [t for t in texts if t.startswith('Averaged')]
        # the fixture's past series are 5 + 6 kills on Maps 1-2: the series total (11), never the 5.1 per-game form against the line
        assert avg and avg[0].startswith('Averaged 11, median 11'), texts
        assert any(t.startswith('His base rate: 5.1 kills a game at an average team’s pace (125 earlier games)') for t in texts), texts
        assert any('give up 12% fewer kills a game than an average team' in t for t in texts), texts
        assert any(t.startswith('Recent form: 5.1 kills a game over his last 10 games') for t in texts), texts
        assert not any('factor' in t.lower() for t in texts), texts
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


def phone_rows(p):
    return p.evaluate("""() => [...document.querySelectorAll('#hub-rows tr[data-record]')].filter(r => r.offsetParent).map(r => {
      const td = r.querySelector('td.h-c-hits'), cells = [...td.querySelectorAll('.h-strip-cell')];
      return {name: r.querySelector('.h-player b').innerText, nohits: r.classList.contains('h-nohits'), shown: getComputedStyle(td).display !== 'none',
              h: r.getBoundingClientRect().height, scout: r.querySelector('td.h-c-scout').innerText.trim(),
              cells: cells.map(c => ({label: getComputedStyle(c, '::before').content.replace(/"/g, ''), text: c.querySelector('.h-hit').textContent.trim(),
                                      w: c.getBoundingClientRect().width, top: Math.round(c.getBoundingClientRect().top)}))}; })""")


@pytest.mark.parametrize('size', PHONES, ids=lambda s: str(s['width']))
def test_phone_extra_book_rows_on_nba_and_nfl_show_counted_windows(browser, rich, size):
    p = open_board(browser, rich, size)
    try:
        for sp in ('nba', 'nfl'):
            sport(p, sp)
            for b in ('sleeper', 'dabble'):
                p.wait_for_function(f"{SHOWN_BOOKS}.includes('{b}')", timeout=20000)
                p.locator(f'#hub-platform [data-hub="book"][data-id="{b}"]').click()
                p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length>0 && !document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
                p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('{sp}|'))", timeout=20000)
                rows = phone_rows(p)
                assert len(rows) == 2, (sp, b, rows)
                for r in rows:
                    assert r['shown'] and not r['nohits'], (sp, b, r)
                    w = {c['label'].split()[0]: c for c in r['cells']}
                    for k in ('L5', 'L10', 'L20'):
                        assert re.fullmatch(r'\d+(/|-)\d+', w[k]['text']), (sp, b, r['name'], w[k])   # counted at the book's line
                    assert len({c['top'] for c in r['cells']}) == 1 and min(c['w'] for c in r['cells']) >= 50, r
                    if '-' in w['L10']['text']:
                        assert all(c['label'].endswith('O-U') for c in r['cells']), r              # "3-2" says which number is Over
                    assert 'saved chance' not in r['scout'].lower(), r
                no_h_scroll(p)
                shot(p, f'board-{sp}-{b}')
            p.locator('#hub-platform [data-hub="book"][data-id="all"]').click()
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


def test_phone_row_with_no_results_has_no_strip_line(browser, rich, monkeypatch):
    """an NBA row whose player has no past result at any window: no row of five dashes on the phone card"""
    del rich.sports.history["nba:player:espn:304"]
    doc, view = rich.sports.boards["nba"]
    view["rows"] = {k: v for k, v in view["rows"].items() if "304" not in k}
    p = open_board(browser, rich, PHONE)
    try:
        sport(p, 'nba')
        rows = {r['name']: r for r in phone_rows(p)}
        assert rows['Reed Lean']['nohits'] and not rows['Reed Lean']['shown'], rows['Reed Lean']
        assert rows['Karl Anders']['shown'] and not rows['Karl Anders']['nohits']
        assert rows['Reed Lean']['h'] < rows['Karl Anders']['h'] - 30, rows                 # the strip's line is gone, not left empty
        shot(p, 'board-nba-nohits')
        p.set_viewport_size(DESK)
        p.wait_for_timeout(200)
        assert p.evaluate("getComputedStyle(document.querySelector('#hub-rows tr.h-nohits td.h-c-hits')).display") != 'none'   # desktop keeps its column
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


VIEWS = [('cs2', None, None), ('lol', None, 'Guigo'), ('nba', None, 'Karl Anders'), ('nfl', None, 'Dee Route'),
         ('nba', 'dabble', None), ('nfl', 'dabble', None), ('nba', 'sleeper', None), ('nfl', 'sleeper', None), ('cs2', 'sleeper', None), ('cs2', 'dabble', None)]


@pytest.mark.parametrize('size', PHONES + [DESK], ids=lambda s: str(s['width']))
def test_board_and_card_fit_every_width(browser, rich, size):
    """CS2, LoL, NBA, NFL, Dabble and Sleeper at 360 / 390 / 414 / 1440: the board and the card fit the screen, the Why section is
    first on the card, and nothing on the page errors (CARD_SHOTS keeps each one)"""
    p = open_board(browser, rich, size)
    try:
        for sp, book, name in VIEWS:
            sport(p, sp)
            b = book or 'all'
            p.wait_for_function(f"{SHOWN_BOOKS}.includes('{b}')", timeout=20000)
            p.locator(f'#hub-platform [data-hub="book"][data-id="{b}"]').click()
            p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].length>0 && [...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('{sp}|'))", timeout=20000)
            p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
            no_h_scroll(p)
            tag = f"{sp}{'-' + book if book else ''}"
            shot(p, f'shot-board-{tag}')
            if book:
                p.locator('#hub-rows tr[data-record] .h-add').first.click()
                p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-why', timeout=20000)
                p.wait_for_selector('#hub-detail .h-history-summary', timeout=20000)
                p.wait_for_timeout(250)
            else:
                open_card(p, name)
            w = p.evaluate(WHY)
            check_why(w, sp, extra=bool(book))
            no_h_scroll(p)
            shot(p, f'shot-card-{tag}')
            p.locator('#hub-card [data-hub="card-close"]').first.click()
            p.wait_for_selector('#hub-card', state='hidden', timeout=10000)
            p.locator('#hub-q').fill('')
        assert not p.errors, p.errors
    finally:
        p.ctx.close()
