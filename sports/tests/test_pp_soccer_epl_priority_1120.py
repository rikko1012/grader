"""soccer-epl (owner 2026-11-20: "add EPL league for soccer"): the EPL PrizePicks tab (league 14) is read right after
tab 82 whenever it is due, has standard lines and kicks off inside 48 h, so it stays on the board even at coverage level 1
(live 2026-11-21 01:23Z: EPL listed 2,966 projections, 114 standard lines kept, 0 on the board, last read 893 s ago).
The request count, level, spacing and back-off are unchanged; a streak guard keeps the other leagues rotating."""
import json
from collections import Counter

import pytest

from sports import expansion_board as board
from sports.tests.test_pp_soccer_leagues_1119 import env, run, status, visible_late, pp_tab  # noqa: F401

SOON = '2026-11-20T15:00:00Z'          # 23 h after the harness clock (2026-11-19 16:00Z)


def live_like(env, epl_start=SOON):
    """82 + EPL + MLS + Liga MX + Serie A + La Liga, every league tab with one standard line (the live shape)."""
    env['tabs'][14] = pp_tab(14, 'EPL', [('14a', 'p2', 'Shots', 1.5, 'standard')], teams=('Arsenal', 'Spurs'),
                             full=(('Arsenal', 'Arsenal FC'), ('Tottenham', 'Tottenham Hotspur')), start=epl_start)
    env['tabs'][531] = pp_tab(531, 'La Liga', [('531a', 'p6', 'Shots', 0.5, 'standard')], teams=('Betis', 'Girona'),
                              full=(('Betis', 'Real Betis'), ('Girona', 'Girona FC')))
    env['listed'][531] = ('La Liga', 20, 'soccer', None)


def coverage(env, monkeypatch, n=120, priority=None, level=1):
    monkeypatch.setenv('SCOUT_SOCCER_PP_MAX_LEVEL', str(level))
    monkeypatch.setenv('SCOUT_SOCCER_PP_START_LEVEL', str(level))
    if priority is not None:
        monkeypatch.setenv('SCOUT_SOCCER_PP_PRIORITY', priority)
    seen, reads = Counter(), Counter()
    runs = run(env, n)
    for calls, snap in runs[8:]:                       # after the first full round
        for k, v in visible_late(snap, env).items():
            seen[k] += bool(v)
        reads.update(c for c in calls if c != 'leagues')
    return {k: round(v / (n - 8), 3) for k, v in seen.items()}, reads, runs


def test_priority_tabs_parse_only_mapped_league_tabs(monkeypatch):
    monkeypatch.delenv('SCOUT_SOCCER_PP_PRIORITY', raising=False)
    assert board.priority_tabs() == [14]
    for raw, want in (('', []), ('0', []), ('82', []), ('14,536,14', [14, 536]), ('9999, 6', [6]), ('x', [])):
        monkeypatch.setenv('SCOUT_SOCCER_PP_PRIORITY', raw)
        assert board.priority_tabs() == want


def test_level_one_keeps_epl_on_the_board_and_other_leagues_still_rotate(env, monkeypatch):
    live_like(env)
    seen, reads, runs = coverage(env, monkeypatch)
    assert seen['SOCCER'] == 1.0                       # tab 82 never dark (its 180 s rate is untouched)
    assert seen['EPL'] >= 0.8
    for name in ('MLS', 'Liga MX', 'Serie A', 'La Liga'):
        assert seen[name] > 0                          # the streak guard: every other league still gets turns
    assert all(len([c for c in calls if c != 'leagues']) <= 1 for calls, _ in runs)   # still one request per 90 s
    st = status(env)
    assert st['coverage']['level'] == 1
    assert st['priority']['tabs'] == [14] and st['priority']['max_streak'] == 2
    assert [lg['id'] for lg in st['leagues'] if lg['priority']] == [14]


def test_before_after_numbers_at_level_one(env, monkeypatch, tmp_path):
    """The trade-off, stated with numbers: same requests, EPL coverage up, the other leagues' share down."""
    live_like(env)
    after, reads_after, _ = coverage(env, monkeypatch)
    total = sum(reads_after.values())
    assert reads_after[14] > reads_after[6] and reads_after[82] >= total // 2 - 1
    print(json.dumps({'after': after, 'reads_after': dict(reads_after)}))


def test_priority_off_is_the_plain_rotation(env, monkeypatch):
    live_like(env)
    seen, reads, _ = coverage(env, monkeypatch, priority='')
    # least-recently-attempted: every league tab gets the same number of reads (+-1), EPL included
    league = [reads[k] for k in (14, 6, 536, 534, 531)]
    assert max(league) - min(league) <= 1
    assert seen['EPL'] < 0.8
    assert status(env)['priority']['tabs'] == []
    print(json.dumps({'before': seen, 'reads_before': dict(reads)}))


def test_epl_far_from_kickoff_or_without_standard_lines_takes_no_priority(env, monkeypatch):
    live_like(env, epl_start='2030-01-01T20:00:00Z')
    _, reads, _ = coverage(env, monkeypatch, n=60)
    league = [reads[k] for k in (14, 6, 536, 534, 531)]
    assert max(league) - min(league) <= 1              # outside 48 h: plain rotation


def test_epl_demon_only_tab_is_not_promoted(env, monkeypatch):
    env['tabs'][14] = pp_tab(14, 'EPL', [('14a', 'p2', 'Shots', 1.5, 'demon')], start=SOON)
    _, reads, _ = coverage(env, monkeypatch, n=60)
    assert reads[14] <= 1                              # read once to learn it has no standard line, then 30-min discovery


def test_full_level_still_covers_every_source(env, monkeypatch):
    live_like(env)
    seen, _, _ = coverage(env, monkeypatch, n=40, level=5)
    assert all(seen[k] == 1.0 for k in ('SOCCER', 'EPL', 'MLS', 'Liga MX', 'Serie A', 'La Liga'))


@pytest.mark.parametrize('bad', ['nope', {'streak': 'x'}, {'streak': -4}])
def test_a_broken_priority_state_never_crashes(env, monkeypatch, bad):
    live_like(env)
    run(env, 2)
    st = json.loads((env['dir'] / board.PP_STATE).read_text(encoding='utf-8'))
    st['priority'] = bad
    (env['dir'] / board.PP_STATE).write_text(json.dumps(st), encoding='utf-8')
    run(env, 2)
    assert isinstance(status(env)['priority']['streak'], int)
