"""Extra research sources as budgeted phases of the existing <sport>-history job (2026-11-18). Weight 0 everywhere.

Why phases and not new scheduler jobs: the stall replays (sports/tests/test_sports_stall_1108.py and the numeric
dump of its 6-h replay) got worse on many board / collector gaps with ANY extra expansion job, even 20 s LIGHT runs
in the history serial group, and with any longer quotes run. Inside the history job the job table, caps, timeout
and run envelope are unchanged: the main ESPN phases stop requesting RESERVE_S before the job's request deadline,
and each source below has its own request cap and seconds budget inside that reserve, so the job never requests
past the same 150 s deadline as before.

  soccer: FotMob matchDetails (verified league ids), football-data.co.uk (1 CSV a run), Kalshi + Polymarket prices
  tennis: theScore (finals stats + pre-match context of offered matches), Kalshi + Polymarket prices

Every phase keeps its last good output on failure (atomic writes) and goes through expansion_sources holds.

Switches (review 2026-11-18): research switches never stop a graded input. With every research phase of a sport off
(SCOUT_EXPANSION_SOURCES=0, or each SCOUT_EXPANSION_PHASE_<NAME>=0) the job is 53dad0b's: no reserve, FotMob six /
theScore finals on the shared budget at the old call site, no new-competition roster or summary requests.
"""
import sqlite3
import time

from . import net

RESERVE_S = 45
# sg-squads (soccer-grades-1120 fair): identity links for offered players ESPN's current roster leaves out - last, so it
# only uses time the other phases left; 0 requests when every offered player resolves (expansion_squads).
PHASES = {'soccer': (('fotmob', 6, 20), ('football_data', 1, 10), ('exchanges', 12, 15), ('squads', 6, 12)),
          'tennis': (('thescore', 10, 25), ('exchanges', 6, 15))}


def enabled(name):
    """Research kill switches, no deploy needed: SCOUT_EXPANSION_SOURCES=0 stops every research phase;
    SCOUT_EXPANSION_PHASE_<NAME>=0 (FOTMOB, THESCORE, FOOTBALL_DATA, EXCHANGES) stops one. For FotMob / theScore
    this stops only their research part (new leagues, pre-match context, research stat names): the graded inputs of
    53dad0b (FotMob's six leagues, theScore finals counts) keep their own switch, graded_enabled()."""
    import os
    return (os.environ.get('SCOUT_EXPANSION_SOURCES', '1') != '0'
            and os.environ.get('SCOUT_EXPANSION_PHASE_' + name.upper(), '1') != '0')


def graded_enabled():
    """FotMob's six 53dad0b leagues and theScore finals counts feed forecasts (FORECAST_INPUTS): always collected,
    whatever the research switches say. Only SCOUT_EXPANSION_GRADED_SOURCES=0 stops them (separate, default on)."""
    import os
    return os.environ.get('SCOUT_EXPANSION_GRADED_SOURCES', '1') != '0'


GRADED = {'soccer': 'fotmob', 'tennis': 'thescore'}   # the phase whose collector also gathers graded inputs


def research_on(sport):
    """Any research phase of this sport switched on? When none is, the history job runs as at 53dad0b: ESPN keeps the
    whole request window (no reserve) and the graded collectors run inline on its shared budget (expansion_history)."""
    return any(enabled(name) for name, _, _ in PHASES[sport])


def graded_inline(sport, catalogs, get, persist, directory):
    """The 53dad0b call: graded-only collection on the main job's shared get (its 16-request cap, full deadline)."""
    if not graded_enabled():
        return []
    if sport == 'soccer':
        from .expansion_soccer_detail import collect_details
        return collect_details(catalogs, get, persist, directory, recent={})
    from .expansion_tennis import collect_details
    return collect_details(catalogs, get, persist, directory, research=False)


def _phase(name, sport, catalogs, persist, directory, get, get_text):
    from .expansion_history import offered
    if name == 'fotmob':
        from .expansion_soccer_detail import collect_details
        # recent={}: the six original leagues only (the research part switched off, graded part still on).
        recent = offered(directory) if enabled(name) else {}
        return {'errors': collect_details(catalogs, get, persist, directory, recent=recent)}
    if name == 'thescore':
        from .expansion_tennis import collect_details
        return {'errors': collect_details(catalogs, get, persist, directory, research=enabled(name))}
    if name == 'football_data':
        from .expansion_footballdata import collect
        return collect(get_text, directory, catalogs)
    if name == 'squads':
        from datetime import datetime, timedelta, timezone
        from .expansion_squads import collect
        earliest = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        current = [m for rows in catalogs.values() for m in rows.values() if (m.get('start') or '') >= earliest]
        return collect(catalogs, get, directory, current)
    if name == 'exchanges':
        from .expansion_exchanges import collect
        return collect(sport, get, directory, catalogs, offered(directory) if sport == 'soccer' else None)
    raise ValueError(name)


def run(sport, catalogs, persist, directory, deadline):
    """-> {phase: {'requests', 'ok', 'rows', 'seconds', 'state', 'errors', ...}}; never raises for a source."""
    from .expansion_sources import get_json, get_text
    out = {}
    for name, max_requests, max_seconds in PHASES[sport]:
        info = {'requests': 0, 'ok': 0, 'rows': 0, 'errors': [], 'state': 'ok'}
        out[name] = info
        if name == GRADED[sport] and not graded_enabled():
            info['state'] = 'off'          # one shared queue: the graded switch stops the whole collector
            continue
        if not enabled(name):
            if name != GRADED[sport]:
                info['state'] = 'off'
                continue
            info['state'] = 'graded_only'  # research part off: FotMob six leagues / theScore finals counts only
        began = time.monotonic()
        end = min(deadline, began + max_seconds)
        if began >= deadline:
            info['state'] = 'skipped_deadline'
            continue

        def gate():
            if info['requests'] >= max_requests or time.monotonic() >= end:
                raise StopIteration
            info['requests'] += 1
            return max(1.0, min(15.0, end - time.monotonic()))

        def get(url, params, timeout=10):
            body = get_json(sport, url, params=params, timeout=min(timeout, gate()), tries=1)
            info['ok'] += 1
            return body

        def text(url, params, timeout=20):
            body = get_text(sport, url, params=params, timeout=min(timeout, gate()), tries=1)
            info['ok'] += 1
            return body

        def counted(feed, competition, *a):
            info['rows'] += len(feed.records)
            return persist(feed, competition, *a)
        try:
            extra = _phase(name, sport, catalogs, counted, directory, get, text) or {}
            info['errors'].extend(extra.pop('errors', []))
            if extra:
                info['detail'] = extra                     # football-data: file / rows / joined; exchanges: events
        except StopIteration:
            info['state'] = 'budget'
        except (net.HttpError, ValueError, OSError, sqlite3.Error) as e:
            info['errors'].append(type(e).__name__)
            info['state'] = 'failed'
        info['seconds'] = round(time.monotonic() - began, 1)
    return out
