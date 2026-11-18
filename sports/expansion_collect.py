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
"""
import sqlite3
import time

from . import net

RESERVE_S = 45
PHASES = {'soccer': (('fotmob', 6, 20), ('football_data', 1, 10), ('exchanges', 12, 15)),
          'tennis': (('thescore', 10, 25), ('exchanges', 6, 15))}


def enabled(name):
    """Kill switches, no deploy needed: SCOUT_EXPANSION_SOURCES=0 stops every research phase (the history job then
    runs as at 53dad0b, FotMob / theScore included - they are phases now, so they stop too);
    SCOUT_EXPANSION_PHASE_<NAME>=0 (FOTMOB, THESCORE, FOOTBALL_DATA, EXCHANGES) stops one."""
    import os
    return (os.environ.get('SCOUT_EXPANSION_SOURCES', '1') != '0'
            and os.environ.get('SCOUT_EXPANSION_PHASE_' + name.upper(), '1') != '0')


def _phase(name, sport, catalogs, persist, directory, get, get_text):
    from .expansion_history import offered
    if name == 'fotmob':
        from .expansion_soccer_detail import collect_details
        return {'errors': collect_details(catalogs, get, persist, directory, recent=offered(directory))}
    if name == 'thescore':
        from .expansion_tennis import collect_details
        return {'errors': collect_details(catalogs, get, persist, directory)}
    if name == 'football_data':
        from .expansion_footballdata import collect
        return collect(get_text, directory, catalogs)
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
        if not enabled(name):
            info['state'] = 'off'
            continue
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
