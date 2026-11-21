"""doubles-1 (soccer-grades-1120 fair): tennis DOUBLES lines (PrizePicks 'Harrison C / Skupski N').

Before, ESPN's scoreboard parser dropped doubles (singles stores only) and the resolver knew single players, so a doubles
pair line had no fixture, no identity and no letter.

Fixtures: the ESPN ATP / WTA scoreboards the history job already reads (no extra request) carry doubles matches with
both players of each pair (grouping mens-doubles / womens-doubles; mixed doubles are not read). They are kept in their
own bounded file, doubles-matches.json (never in the singles stores): fixture, pairs, status and, for finals, the set
games (a match tie-break decider counts one game).

Matching a book pair to a fixture (deterministic, no cross-fixture guessing): each book name is 'Surname X' (initial
after), 'X. Surname' or a full name; it identifies a fixture player when the surname equals the player's surname (the
words after the first name, or the last word) and the initial equals his first name's first letter (or the full name
is equal). A fixture is a match only if, among ITS 4 players, each of the pair's two names identifies exactly one player,
both on the same side, the opponent pair named by the book identifies the other side the same way, and its start is
within 2 h. Exactly one such fixture, or no identity.

Model (walk-forward cs2_scout_ops/logs/soccer_grades_1120/fair_1120/wf_doubles1.py, sha256 frozen before the run,
doubles1_walkforward.json): population pmf of total games / sets played per (tour, Grand Slam), ESPN doubles finals of
the tune window (before 2026-09-01); no pair-specific input. Holdout 2026-09-01..11-19: sets played passed everywhere;
total games passed for WTA Grand Slams, failed for ATP and non-Slam WTA (holdout matches ran longer than the tune window).
Every doubles line is 0U with its tier reason. Fantasy score / break points of a pair: no source has the doubles inputs
-> 'doubles_stat_not_modelled'.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from .contracts import parse_utc

VERSION = 'doubles-1'
PATH = 'doubles-matches.json'
MAX_BYTES = 1_500_000
KEEP_DAYS = 100
GROUPS = {'atp': 'mens-doubles', 'wta': 'womens-doubles'}
STATS = ('games_played', 'sets_played')

DOUBLES_PRIOR = {
    'games_played|atp|0': {"11": 0.000647, "12": 0.000647, "13": 0.001942, "14": 0.004531, "15": 0.008414, "16": 0.026537, "17": 0.069256, "18": 0.087379, "19": 0.106796, "20": 0.135275, "21": 0.091262, "22": 0.140453, "23": 0.152104, "24": 0.07055, "25": 0.029126, "26": 0.048544, "27": 0.025243, "28": 0.000647, "29": 0.000647},
    'games_played|atp|1': {"13": 0.002571, "14": 0.002571, "15": 0.017995, "16": 0.028278, "17": 0.069409, "18": 0.043702, "19": 0.125964, "20": 0.064267, "21": 0.033419, "22": 0.069409, "23": 0.069409, "24": 0.002571, "25": 0.03856, "26": 0.069409, "27": 0.023136, "28": 0.03856, "29": 0.012853, "30": 0.033419, "31": 0.033419, "32": 0.033419, "33": 0.03856, "34": 0.03856, "35": 0.043702, "36": 0.017995, "37": 0.002571, "38": 0.023136, "39": 0.017995, "40": 0.002571, "41": 0.002571},
    'games_played|wta|0': {"10": 0.000456, "11": 0.000456, "12": 0.001369, "13": 0.008668, "14": 0.023266, "15": 0.05885, "16": 0.08896, "17": 0.126369, "18": 0.119069, "19": 0.140967, "20": 0.117245, "21": 0.081661, "22": 0.100821, "23": 0.067974, "24": 0.028741, "25": 0.01688, "26": 0.015055, "27": 0.002281, "28": 0.000456, "29": 0.000456},
    'games_played|wta|1': {"11": 0.002558, "12": 0.002558, "13": 0.017903, "14": 0.028133, "15": 0.033248, "16": 0.048593, "17": 0.094629, "18": 0.063939, "19": 0.084399, "20": 0.084399, "21": 0.058824, "22": 0.043478, "23": 0.038363, "24": 0.048593, "25": 0.033248, "26": 0.028133, "27": 0.043478, "28": 0.048593, "29": 0.048593, "30": 0.017903, "31": 0.053708, "32": 0.038363, "33": 0.012788, "34": 0.007673, "35": 0.012788, "36": 0.002558, "37": 0.002558},
    'sets_played|atp|0': {"0": 0.000653, "1": 0.000653, "2": 0.571149, "3": 0.42624, "4": 0.000653, "5": 0.000653},
    'sets_played|atp|1': {"0": 0.002732, "1": 0.002732, "2": 0.614754, "3": 0.374317, "4": 0.002732, "5": 0.002732},
    'sets_played|wta|0': {"0": 0.000459, "1": 0.000459, "2": 0.638659, "3": 0.359504, "4": 0.000459, "5": 0.000459},
    'sets_played|wta|1': {"0": 0.002703, "1": 0.002703, "2": 0.618919, "3": 0.37027, "4": 0.002703, "5": 0.002703},
}
DOUBLES_TIERS = {'atp|0': {'games_played': 'failed', 'sets_played': 'passed'}, 'atp|1': {'games_played': 'failed', 'sets_played': 'passed'}, 'wta|0': {'games_played': 'failed', 'sets_played': 'passed'}, 'wta|1': {'games_played': 'passed', 'sets_played': 'passed'}}
REASONS = {
    'passed': '0U: doubles population model passed its walk-forward test; stakes wait for settled real lines.',
    'close': '0U: doubles population model within 3 points in its walk-forward test.',
    'failed': '0U: doubles population model missed its walk-forward test.',
    'untested': '0U: doubles population model, no walk-forward test for this tour.',
}


def is_pair(offer):
    return offer.get('sport') == 'tennis' and ' / ' in str(offer.get('player') or '')


def _norm(s):
    from .expansion_history import normalized
    return normalized(s)


def _games(home, away, major):
    """Total games and sets of a final from the two sides' linescore games; a non-Slam third-set match tie-break
    (ESPN 1-0, or the tie-break points such as 10-8) counts one game. -> (games, sets) or None."""
    if len(home) != len(away) or not 2 <= len(home) <= (5 if major else 3):
        return None
    total = 0
    for i, (a, b) in enumerate(zip(home, away)):
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return None
        if i == 2 and not major and ((max(a, b) == 1 and min(a, b) == 0) or max(a, b) >= 10):
            total += 1
        else:
            total += int(a + b)
    return total, len(home)


def _iso(start):
    s = str(start or '')
    if re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\dZ', s):
        s = s[:-1] + ':00Z'
    parse_utc(s)
    return s


def parse(body, tour, observed_at):
    """Doubles fixtures of one ESPN tennis scoreboard body (tour 'atp' / 'wta'). Never raises for one bad match."""
    out = []
    for parent in (body or {}).get('events') or []:
        pid = str(parent.get('id') or '')
        major = bool(parent.get('major'))
        for g in parent.get('groupings') or []:
            if ((g.get('grouping') or {}).get('slug') or '') != GROUPS[tour]:
                continue
            for c in g.get('competitions') or []:
                try:
                    cid = str(c.get('id') or '')
                    sides = c.get('competitors') or []
                    if not pid or not cid or len(sides) != 2:
                        continue
                    pairs = []
                    for s in sides:
                        ids = str(s.get('id') or '').split('-')
                        people = (s.get('roster') or {}).get('athletes') or []
                        names = [a.get('displayName') or a.get('fullName') for a in people]
                        if (s.get('type') != 'team' or len(ids) != 2 or len(names) != 2 or not all(ids) or
                                not all(names) or any(n.strip().lower() in ('tbd', 'bye') for n in names)):
                            break
                        pairs.append({'id': f"{tour}:pair:espn:{'+'.join(sorted(ids))}",
                                      'players': [{'id': f'{tour}:player:espn:{i}', 'name': n} for i, n in zip(ids, names)],
                                      'games': [x.get('value') for x in s.get('linescores') or []]})
                    if len(pairs) != 2 or pairs[0]['id'] == pairs[1]['id']:
                        continue
                    typ = (c.get('status') or {}).get('type') or {}
                    name = str(typ.get('name') or '').upper()
                    detail = ' '.join(str(typ.get(k) or '') for k in ('description', 'detail', 'shortDetail'))
                    abnormal = (any(x in name for x in ('RETIR', 'WALK', 'SUSPEND', 'CANCEL', 'POSTPON', 'ABANDON',
                                                        'DEFAULT')) or
                                bool(re.search(r'\b(retired|walkover|abandoned|defaulted|w/o)\b', detail, re.I)))
                    final = typ.get('completed') is True and name == 'STATUS_FINAL' and not abnormal
                    state = ('unknown' if abnormal else 'final' if final else
                             {'pre': 'scheduled', 'in': 'in_progress'}.get(typ.get('state'), 'unknown'))
                    row = {'event_id': f'{tour}:espn:{tour}_doubles:{pid}:{cid}', 'tour': tour, 'major': major,
                           'start': _iso(c.get('startDate') or c.get('date')), 'status': state,
                           'name': parent.get('name'), 'observed_at': observed_at,
                           'sides': [{'id': p['id'], 'players': p['players']} for p in pairs]}
                    if final:
                        res = _games(pairs[0]['games'], pairs[1]['games'], major)
                        if res:
                            row['games_played'], row['sets_played'] = res
                    out.append(row)
                except (AttributeError, TypeError, ValueError):
                    continue
    return out


def absorb(directory, rows, now=None):
    """Merge parsed fixtures into doubles-matches.json (a final never goes back to scheduled; kept KEEP_DAYS; bounded:
    oldest starts dropped first over MAX_BYTES). -> rows given."""
    from .expansion_history import atomic_json
    if not rows:
        return 0
    now = now or datetime.now(timezone.utc)
    path = Path(directory) / PATH
    try:
        doc = json.loads(path.read_text(encoding='utf-8')) if path.stat().st_size <= 2 * MAX_BYTES else {}
    except (OSError, ValueError):
        doc = {}
    matches = doc.get('matches') if isinstance(doc, dict) and isinstance(doc.get('matches'), dict) else {}
    for r in rows:
        old = matches.get(r['event_id']) or {}
        if old.get('status') == 'final' and r['status'] != 'final':
            continue
        matches[r['event_id']] = r
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%dT%H:%M:%SZ')
    keep = {k: v for k, v in matches.items() if (v.get('start') or '') >= cutoff}
    out, size = {}, 0
    for k in sorted(keep, key=lambda k: keep[k].get('start') or '', reverse=True):
        n = len(json.dumps(keep[k], separators=(',', ':'))) + len(k) + 4
        if size + n > MAX_BYTES:
            break
        out[k] = keep[k]
        size += n
    atomic_json(path, {'at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'matches': out})
    return len(rows)


@lru_cache(maxsize=2)
def _read(path, stamp, size):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load(directory):
    """{event_id: fixture} (bounded read, never raises)."""
    try:
        path = Path(directory) / PATH
        st = path.stat()
        if st.st_size > 2 * MAX_BYTES:
            return {}
        doc = _read(str(path), st.st_mtime_ns, st.st_size)
        return (doc.get('matches') or {}) if isinstance(doc, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def book_name(text):
    """'Harrison C' -> surname + initial; 'C. Harrison' -> the same; 'Christian Harrison' -> the full name."""
    raw = str(text or '').strip()
    m = re.fullmatch(r'(.+?)\s+([^\W\d_])\.?', raw)
    if m and len(_norm(m.group(1))) > 1:
        return {'surname': _norm(m.group(1)), 'initial': _norm(m.group(2))}
    m = re.fullmatch(r'([^\W\d_])\.\s*(.+)', raw)
    if m:
        return {'surname': _norm(m.group(2)), 'initial': _norm(m.group(1))}
    return {'full': _norm(raw)}


def _identifies(book, player):
    name = _norm(player.get('name'))
    words = name.split()
    if 'full' in book:
        return bool(book['full']) and book['full'] == name
    if len(words) < 2 or not book['initial']:
        return False
    return book['initial'] == words[0][:1] and book['surname'] in (' '.join(words[1:]), words[-1])


def _side_of(names, fixture):
    """Index of the one side whose two players are identified one-to-one by the two book names, else None."""
    if len(names) != 2:
        return None
    people = [(i, p) for i, s in enumerate(fixture.get('sides') or []) for p in s.get('players') or []]
    if len(people) != 4:
        return None
    hits = []
    for b in names:
        h = [(i, p['id']) for i, p in people if _identifies(b, p)]
        if len(h) != 1:
            return None
        hits.append(h[0])
    if hits[0][0] != hits[1][0] or hits[0][1] == hits[1][1]:
        return None
    return hits[0][0]


def resolve(offer, fixtures):
    """Identity of a book pair: {'competition': tour, 'event_id', 'player_id': pair id} or None."""
    if not is_pair(offer):
        return None
    mine = [book_name(x) for x in str(offer['player']).split(' / ')]
    others = [t for t in offer.get('teams') or [] if _norm(t) != _norm(offer['player'])]
    if len(others) != 1 or ' / ' not in others[0]:
        return None
    theirs = [book_name(x) for x in others[0].split(' / ')]
    found = []
    for f in fixtures.values():
        try:
            if abs((parse_utc(f['start']) - parse_utc(offer['start'])).total_seconds()) > 7200:
                continue
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        side = _side_of(mine, f)
        if side is None or _side_of(theirs, f) != 1 - side:
            continue
        found.append({'competition': f['tour'], 'event_id': f['event_id'], 'player_id': f['sides'][side]['id']})
    return found[0] if len(found) == 1 else None


def facts(offer, fixtures, now_iso):
    """The research entry of a doubles line."""
    ident = resolve(offer, fixtures)
    entry = {'identity': ident, 'scope': offer['scope'], 'games': [], 'checked_at': now_iso, 'doubles': True}
    if ident:
        f = fixtures[ident['event_id']]
        entry['event'] = {'event_id': f['event_id'], 'start': f['start'], 'status': f['status']}
        entry['context'] = {'major': bool(f.get('major'))}
        entry['match'] = ' vs '.join(' / '.join(p['name'] for p in s['players']) for s in f['sides'])
    return entry


def fit(offer, facts_, now):
    """Pre-match distribution of a doubles line -> (out, None) or (None, reason). Same keys as the count fit()."""
    stat, scope = offer['stat'], offer['scope']
    if stat not in STATS or scope != 'full_game':
        return None, 'doubles_stat_not_modelled'
    ident = facts_['identity']
    group = f"{ident['competition']}|{int(bool((facts_.get('context') or {}).get('major')))}"
    pmf = DOUBLES_PRIOR.get(f'{stat}|{group}')
    if not pmf:
        return None, 'doubles_stat_not_modelled'
    support = sorted(int(k) for k in pmf)
    mass = [pmf[str(k)] for k in support]
    total = sum(mass)
    mass = [m / total for m in mass]
    line = offer['line']
    probs = {'over': sum(p for k, p in zip(support, mass) if k > line),
             'under': sum(p for k, p in zip(support, mass) if k < line),
             'push': sum(p for k, p in zip(support, mass) if k == line)}
    cumulative, quantiles = 0.0, []
    for k, p in zip(support, mass):
        cumulative += p
        if not quantiles and cumulative >= .1:
            quantiles.append(k)
        if len(quantiles) == 1 and cumulative >= .9:
            quantiles.append(k)
            break
    tier = (DOUBLES_TIERS.get(group) or {}).get(stat, 'untested')
    out = {'model_version': VERSION, 'distribution': 'doubles-population-prior', 'sample': 0,
           'projection': sum(k * p for k, p in zip(support, mass)), 'range80': quantiles,
           'p_over': probs['over'], 'p_under': probs['under'], 'p_push': probs['push'], 'inputs': [],
           'history_scope': 'full_game', 'context_used': {'major': group.endswith('|1')}, 'calibrated': False,
           'doubles_model': {'version': VERSION, 'group': group, 'tier': tier},
           'thin_model': {'version': VERSION, 'group': group, 'games': 0, 'tier': tier}, 'stake_tier': tier}
    return out, None


def actual(forecast, directory):
    """Settlement value of a doubles forecast from the stored final -> (value, [fixture id]) or (None, [])."""
    f = load(directory).get((forecast.get('identity') or {}).get('event_id'))
    if not f or f.get('status') != 'final' or forecast['stat'] not in f:
        return None, []
    return f[forecast['stat']], [f['event_id']]
