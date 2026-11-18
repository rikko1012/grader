"""ESPN all-soccer league-id map (2026-11-18): 77 more verified ids, limit 1000, bounded summary queue, and a
size guard on current-matches.json. Research only: none of the new slugs is a forecast input."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from sports import expansion_history as history
from sports.expansion import BASE_COMPETITIONS, COMPETITIONS, Feed
from sports.expansion_forecasts import FORECAST_INPUTS

NEW = {'3914': 'eng.2', '3927': 'ger.2', '3931': 'ita.2', '750': 'jpn.1', '3906': 'aus.1', '776': 'uefa.europa',
       '20296': 'uefa.europa.conf', '21231': 'ksa.1', '18992': 'aus.w.1'}


def test_every_mapped_id_round_trips_to_its_slug_and_a_competition():
    assert len(history.SOCCER_LEAGUE_IDS) == 24 + 77
    assert len(set(history.SOCCER_LEAGUE_IDS.values())) == len(history.SOCCER_LEAGUE_IDS)
    for lid, slug in history.SOCCER_LEAGUE_IDS.items():
        assert COMPETITIONS[slug] == ('soccer', 'soccer'), slug
        assert history.soccer_competition({'uid': f's:600~l:{lid}~e:1'}) == slug
    for lid, slug in NEW.items():
        assert history.SOCCER_LEAGUE_IDS[lid] == slug


def test_new_competitions_are_research_only():
    new = {c for c in COMPETITIONS if c not in BASE_COMPETITIONS}
    assert len(new) == 77
    assert not {c for c, _s, _t in FORECAST_INPUTS} & new


@pytest.mark.parametrize('lid', ['20226', '5499', '5487', '5697', '20922', '3904', '19968', '19989', '999999'])
def test_unverified_ids_are_dropped_never_guessed(lid):
    assert history.soccer_competition({'uid': f's:600~l:{lid}~e:1'}) is None


def _event(i, lid):
    return {'id': str(i), 'uid': f's:600~l:{lid}~e:{i}', 'date': '2026-11-21T15:00Z', 'competitions': []}


def test_a_225_event_day_keeps_every_mapped_event(tmp_path, monkeypatch):
    """limit=1000 is requested (200 cut Nov 21) and every mapped event reaches its own competition."""
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    ids = list(history.SOCCER_LEAGUE_IDS)
    events = [_event(i, ids[i % len(ids)]) for i in range(215)] + [_event(1000 + i, '20226') for i in range(10)]
    seen, grouped = [], {}
    import sports.expansion_sources as src

    def fake(sport, url, params=None, **kw):
        seen.append((url, dict(params or {})))
        if url.endswith('/all/scoreboard'):
            return {'events': events}
        raise history.net.HttpError(404, url, '')

    def parse(body, comp, at):
        grouped.setdefault(comp, []).extend(body['events'])
        return Feed()
    monkeypatch.setattr(src, 'get_json', fake)
    monkeypatch.setattr(history, 'parse_scoreboard', parse)
    monkeypatch.setattr('sports.expansion_research.publish', lambda *a, **k: {})
    monkeypatch.setattr('sports.expansion_soccer_detail.collect_details', lambda *a, **k: [])
    history.collect('soccer', max_requests=5)
    assert seen and all(p.get('limit') == 1000 for u, p in seen if u.endswith('/all/scoreboard'))
    ids_seen = {e['id']: comp for comp, evs in grouped.items() for e in evs}
    assert len(ids_seen) == 215                                       # every mapped event, none of the unknown id
    assert all(ids_seen[str(i)] == history.SOCCER_LEAGUE_IDS[ids[i % len(ids)]] for i in range(215))


def _iso(dt):
    return dt.isoformat().replace('+00:00', 'Z')


def test_summary_queue_drains_on_a_400_match_weekend():
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    old_check = _iso(now - timedelta(days=2))
    recent = {'eng.2': _iso(now - timedelta(days=3))}
    weekend = []
    for i in range(400):
        comp = ('eng.2', 'ger.2', 'eng.1', 'jpn.1')[i % 4]
        start = now - timedelta(hours=20 if i % 8 < 4 else 80)
        weekend.append({'event_id': f'soccer:espn:{comp}:{i}:{i}', 'competition': comp, 'status': 'final',
                        'start': _iso(start), 'summary_checked_at': old_check if i % 3 else None})
    due = [m for m in weekend if history.summary_due(m, recent, now)]
    # base competition (eng.1): the 53dad0b rule - every final unchecked or checked over 24 h ago
    assert sum(m['competition'] == 'eng.1' for m in due) == 100
    # never-offered new competitions: nothing
    assert not [m for m in due if m['competition'] in ('ger.2', 'jpn.1')]
    # offered new competition: unchecked finals, plus already-summarised finals only inside the 48 h window
    new = [m for m in due if m['competition'] == 'eng.2']
    assert new and all(not m['summary_checked_at'] or m['start'] == _iso(now - timedelta(hours=20)) for m in new)
    assert len(new) < 100
    # one stored summary each and the queue is empty an hour later
    for m in due:
        m['summary_checked_at'] = _iso(now)
    assert not [m for m in weekend if history.summary_due(m, recent, now + timedelta(hours=1))]


def test_a_recent_attempt_waits_30_minutes_everywhere():
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    m = {'competition': 'eng.1', 'status': 'final', 'start': _iso(now),
         'summary_attempt_at': _iso(now - timedelta(minutes=5))}
    assert not history.summary_due(m, {}, now)
    assert history.summary_due(m, {}, now + timedelta(minutes=30))


def test_offered_competitions_expire_after_30_days(tmp_path):
    history.mark_offered(tmp_path, {'eng.2'})
    assert set(history.offered(tmp_path)) == {'eng.2'}
    old = _iso(datetime.now(timezone.utc) - timedelta(days=31))
    (tmp_path / 'offered-competitions.json').write_text(json.dumps({'eng.2': old, 'ger.2': 'bad'}))
    assert history.offered(tmp_path) == {}


def test_current_matches_guard_keeps_offered_competitions_first():
    rows = [{'event_id': f'e{i:03}', 'competition': ('jpn.1', 'eng.1', 'eng.2')[i % 3],
             'start': f'2026-11-{20 + i % 5}', 'pad': 'x' * 900} for i in range(300)]
    assert history.fit_matches(rows, {}) is rows                      # under the limit: unchanged
    kept = history.fit_matches(rows, {'eng.2': 'x'}, limit=150_000)
    assert len(json.dumps(kept, separators=(',', ':'))) <= 150_000
    comps = [r['competition'] for r in kept]
    assert comps.count('eng.2') == 100 and comps.count('jpn.1') == 0   # offered first, then base (eng.1)
    assert kept == [r for r in rows if r in kept]                      # original order kept


def test_new_competitions_never_take_a_summary_slot_from_a_graded_league():
    def m(comp, i, checked=None):
        return {'event_id': f'soccer:espn:{comp}:{i}:{i}', 'competition': comp, 'status': 'final',
                'start': f'2026-11-{10 + i:02}T12:00:00Z', 'summary_checked_at': checked,
                'participants': [{'id': f'{comp}:t{i}'}, {'id': f'{comp}:u{i}'}]}
    base = [m('eng.1', i, checked='2026-11-01T00:00:00Z') for i in range(8)]       # re-reads, team not offered
    new = [m('eng.2', i) for i in range(5)]                                        # unchecked, offered team
    offered_teams = {f'eng.2:t{i}' for i in range(5)}
    queue = history.summary_queue(base + new, offered_teams)
    assert [x['competition'] for x in queue] == ['eng.1'] * 6 + ['eng.2'] * 2
    assert history.summary_queue(base, set()) == sorted(base, key=lambda x: x['start'], reverse=True)[:6]
