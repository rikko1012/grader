"""Bounded, immutable forecasts for the two expansion sports.

Version one is an explicitly uncalibrated historical count model, not a claim
of improved accuracy. It models game-to-game dispersion and uncertainty in the
mean; recent hit rates are never substituted for predicted probabilities.
Only the worker fits it. Member requests read the materialized publication.
"""
from contextlib import closing
import hashlib
import json
import math
import sqlite3
import time
from functools import lru_cache

from gradecore import policy
from . import expansion_board as boards, expansion_research as research, expansion_scope, store
from .contracts import now_utc, parse_utc
from .expansion import COMPETITIONS, isolated_database
from .expansion_history import atomic_json

VERSION = 'expansion-count-predictive-v1.1'
MIN_GAMES = 5
MAX_BYTES = 128 * 1024 * 1024
# These competitions do not use extra time in league fixtures. Knockout,
# playoff and international schedules are deliberately not inferred from names.
REGULATION_LEAGUES = {'eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'bra.1', 'por.1', 'sco.1', 'tur.1'}
SOCCER_COUNTS = {'shots', 'shots_on_target', 'passes_attempted', 'tackles', 'saves',
                 'goals', 'assists', 'crosses', 'clearances', 'fouls', 'attempted_dribbles', 'shots_assisted'}


def _inputs():
    """The (competition, source, stat) history rows the forecast may read: exactly what 53dad0b's collectors wrote,
    written out here (NOT derived from the collectors' tables, which grow). A new competition, a new source or a new
    stat name gets no forecast input until a walk-forward test promotes it (factor rule). Research shows everything."""
    base_soccer = ('eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'usa.1', 'bra.1', 'arg.1', 'mex.1', 'ned.1', 'por.1',
                   'sco.1', 'bel.1', 'tur.1', 'uefa.champions', 'fifa.friendly', 'uefa.nations', 'fifa.worldq.uefa',
                   'fifa.worldq.conmebol', 'fifa.worldq.concacaf', 'fifa.worldq.afc', 'fifa.worldq.caf', 'fifa.world',
                   'concacaf.nations.league')
    espn_soccer = ('shots', 'shots_on_target', 'goals', 'assists', 'saves', 'fouls', 'fouls_suffered', 'yellow_cards',
                   'red_cards')
    fotmob = ('minutes', 'passes_attempted', 'tackles', 'crosses', 'clearances', 'attempted_dribbles', 'chances_created',
              'xg', 'xa', 'touches', 'touches_opposition_box', 'interceptions', 'corners_taken')
    sets = ('games_won', 'games_played', 'sets_won', 'sets_played', 'tie_breakers_played')
    rows = {(c, 'espn', s) for c in base_soccer for s in espn_soccer}
    rows |= {(c, 'fotmob', s) for c in ('bra.1', 'eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1') for s in fotmob}
    rows |= {(c, 'espn', s) for c in ('atp', 'wta') for s in sets}
    rows |= {(c, 'thescore', s) for c in ('atp', 'wta')
             for s in ('aces', 'double_faults', 'breakpoints_won', 'points_won', 'tiebreaks_won')}
    rows |= {(c, '365scores', s) for c in ('atp_challenger', 'wta125') for s in sets + (
        'aces', 'double_faults', 'breakpoints_won', 'service_games_won', 'points_won', 'service_points_won',
        'first_serve_points_won', 'second_serve_points_won')}
    return frozenset(rows)


def _promoted():
    """sg-scope (2026-11-20): rows promoted by the pre-registered walk-forward (cs2_scout_ops/logs/soccer_grades_1120/
    scope/walkforward_report.json + walkforward_fotmob.json; criterion: upper 95% bound of ECE minus the graded
    eng.1/ita.1 control <= 0.03 and |mean chance - over rate| <= 0.03, chronological, inputs known before each fixture).
    aus.1 ESPN: 40,598 line tests on 321 matches, ECE 0.0117 vs control 0.0114 (diff CI -0.0041 .. 0.0047), bias
    +0.0096 -> PASS. FotMob (the five modelled count stats; control eng.1 FotMob, ECE 0.0153): mex.1 26,245 tests
    ECE 0.0159 (diff CI -0.0078 .. 0.0096), aus.1 14,796 / 0.0197 (-0.0068 .. 0.0153), usa.1 43,135 / 0.0176
    (-0.0048 .. 0.0095), uefa.champions 10,371 / 0.0104 (-0.0100 .. 0.0075), |bias| <= 0.0081 -> PASS.
    Written out, never derived from the collectors' growing tables."""
    espn_soccer = ('shots', 'shots_on_target', 'goals', 'assists', 'saves', 'fouls', 'fouls_suffered', 'yellow_cards',
                   'red_cards')
    fotmob_counts = ('passes_attempted', 'tackles', 'crosses', 'clearances', 'attempted_dribbles')
    rows = {('aus.1', 'espn', s) for s in espn_soccer}
    rows |= {(c, 'fotmob', s) for c in ('mex.1', 'aus.1', 'usa.1', 'uefa.champions') for s in fotmob_counts}
    return frozenset(rows)


PROMOTED_INPUTS = _promoted()
# Grade leak guard (2026-11-18). Frozen: changing it changes grades and needs a walk-forward test first.
FORECAST_INPUTS = _inputs() | PROMOTED_INPUTS


def encode(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(x):
    return hashlib.sha256(encode(x).encode()).hexdigest()


def offer_key(o):
    return digest([o[k] for k in ('sport', 'platform', 'offer_id', 'revision')])


def fit(offer, facts, now):
    """Return a pre-match predictive distribution or an explicit missing input.

    Full tennis matches require known format in both current and historical
    matches. First-set scores have their own finite support. No full-match
    history is borrowed for a half/set, or from another competition.
    """
    ident = facts.get('identity') or {}
    if not ident or parse_utc(offer['start']) <= parse_utc(now):
        return None, 'identity_or_start'
    event = facts.get('event') or {}
    if (event.get('event_id') != ident['event_id'] or event.get('status') != 'scheduled' or
            not event.get('start') or parse_utc(event['start']) <= parse_utc(now)):
        return None, 'canonical_event_not_scheduled'
    sport, stat, scope = offer['sport'], offer['stat'], offer['scope']
    if COMPETITIONS.get(ident.get('competition'),(None,))[0] != sport:
        return None, 'competition_mismatch'
    expected_scope = scope
    support = None
    scope_rule = extra_time_p = tennis_rule = None
    context = facts.get('context') or {}
    if any(context.get(k) and parse_utc(context[k])>parse_utc(now) for k in ('surface_known_at','format_known_at')):
        return None, 'future_context'
    if sport == 'soccer':
        if stat not in SOCCER_COUNTS:
            return None, 'stat_not_modelled'
        if scope == 'full_game':
            if ident['competition'] not in REGULATION_LEAGUES:
                # sg-scope: decided per fixture (expansion_scope): no extra time possible -> the line is the
                # regulation total; extra time possible -> the platform rule (PrizePicks counts extra time: mixture
                # model below; Underdog does not: regulation total). REGULATION_LEAGUES keep their unchanged path.
                scope_rule = facts.get('scope_rule') or {}
                settle_as = expansion_scope.settlement(offer['platform'], scope_rule.get('extra_time'))
                if settle_as is None:
                    return None, 'extra_time_scope_unverified'
                if settle_as == 'regulation_plus_extra_time':
                    extra_time_p = scope_rule.get('p_extra_time')
                    if not isinstance(extra_time_p, (int, float)) or not 0 <= extra_time_p < 1:
                        return None, 'extra_time_scope_unverified'
            expected_scope = 'regulation'
        if scope not in ('full_game', 'regulation', 'first_half', 'second_half'):
            return None, 'period_not_modelled'
    elif sport == 'tennis':
        if scope == 'set1' and stat in ('games_won', 'games_played'):
            support = list(range(8)) if stat == 'games_won' else list(range(6, 14))
        elif scope == 'full_game' and stat in ('aces', 'double_faults', 'breakpoints_won',
                                              'games_won','games_played','sets_won','sets_played','tie_breakers_played'):
            rule = facts.get('format_rule') or {}
            if context.get('best_of') is None and rule.get('best_of') in (3, 5):
                # sg-scope: tour rule from ESPN's Grand Slam flag (expansion_formats.tour_rule), full-match lines only.
                context, tennis_rule = {**context, 'best_of': rule['best_of']}, rule
            if context.get('best_of') not in (3, 5):
                return None, 'match_format_missing'
            sets = context['best_of']
            to_win = (sets+1)//2
            support = {'games_won':list(range(7*to_win+6*(to_win-1)+1)), 'games_played':list(range(6*to_win,13*sets+1)),
                       'sets_won':list(range(to_win+1)), 'sets_played':list(range(to_win,sets+1)),
                       'tie_breakers_played':list(range(sets+1))}.get(stat)
        else:
            return None, 'stat_or_period_not_modelled'
    else:
        return None, 'wrong_sport'
    if facts.get('scope') != expected_scope:
        return None, 'history_period_mismatch'
    games = []
    seen = set()
    for g in sorted(facts.get('games') or [], key=lambda x: x['date'], reverse=True):
        value = g.get('value')
        if (ident.get('competition'), g.get('source'), stat) not in FORECAST_INPUTS:
            continue   # weight 0: research-only source / competition / stat (grade leak guard)
        if (g['event_id'] == ident['event_id'] or g['event_id'] in seen or
                not g.get('known_at') or parse_utc(g['known_at']) > parse_utc(now) or
                parse_utc(g['date']) >= parse_utc(now) or isinstance(value, bool) or
                not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value):
            continue
        if sport == 'tennis' and scope == 'full_game' and g.get('best_of') != context.get('best_of'):
            continue
        if sport == 'tennis' and context.get('surface') and g.get('surface') != context['surface']:
            continue
        if support is not None and value not in support:
            continue
        seen.add(g['event_id'])
        games.append(g)
        if len(games) == 20:
            break
    if len(games) < MIN_GAMES:
        return None, 'more_exact_history_needed'
    values = [g['value'] for g in games]
    n = len(values)
    avg = sum(values) / n
    if support is not None:
        # One total pseudogame spread across the finite score support. A market
        # with more possible scores must not silently receive a stronger prior.
        mass = [(values.count(k) + 1/len(support)) / (n + 1) for k in support]
        model = 'finite-score-dirichlet'
    else:
        # Poisson-Gamma posterior predictive; observed overdispersion can only
        # widen this distribution. The .5 prior avoids certainty after all zeros.
        mu = (sum(values) + .5) / n
        variance = sum((v-avg)**2 for v in values) / (n-1)
        alpha = max(1 / (sum(values)+.5), (variance-mu)/(mu*mu))
        shape = 1 / alpha
        prob = shape / (shape + mu)
        support, mass, cumulative = [], [], 0.0
        for k in range(4096):
            pk = math.exp(math.lgamma(k+shape)-math.lgamma(shape)-math.lgamma(k+1)
                          +shape*math.log(prob)+k*math.log1p(-prob))
            support.append(k)
            mass.append(pk)
            cumulative += pk
            if k > mu and cumulative > 1-1e-10:
                break
        if sum(mass) < 1-1e-6:
            return None, 'distribution_tail_unresolved'
        total = sum(mass)
        mass = [p/total for p in mass]
        model = 'overdispersed-count-predictive'
        if extra_time_p is not None and expansion_scope.ET_SHARE > 0:
            # Same player rate for 30 more minutes: with P(extra time), the count is Poisson((1+share)*rate) under
            # the same Gamma posterior, i.e. the same shape at mean (1+share)*mu. Mixture of the two predictives.
            mu2 = mu*(1+expansion_scope.ET_SHARE)
            prob2 = shape / (shape + mu2)
            mass2, cumulative = [], 0.0
            for k in range(4096):
                pk = math.exp(math.lgamma(k+shape)-math.lgamma(shape)-math.lgamma(k+1)
                              +shape*math.log(prob2)+k*math.log1p(-prob2))
                mass2.append(pk)
                cumulative += pk
                if k > mu2 and cumulative > 1-1e-10:
                    break
            if sum(mass2) < 1-1e-6:
                return None, 'distribution_tail_unresolved'
            total2 = sum(mass2)
            width = max(len(mass), len(mass2))
            mass = [(1-extra_time_p)*(mass[k] if k < len(mass) else 0.0) +
                    extra_time_p*(mass2[k]/total2 if k < len(mass2) else 0.0) for k in range(width)]
            support = list(range(width))
            model = 'overdispersed-count-predictive+extra-time-mixture'
    line = offer['line']
    probs = {name: sum(p for k,p in zip(support,mass) if rule(k)) for name,rule in
             [('over', lambda k:k>line), ('under', lambda k:k<line), ('push', lambda k:k==line)]}
    cumulative, quantiles = 0, []
    for k,p in zip(support,mass):
        cumulative += p
        if not quantiles and cumulative >= .1:
            quantiles.append(k)
        if len(quantiles) == 1 and cumulative >= .9:
            quantiles.append(k)
            break
    out = {'model_version': VERSION, 'distribution': model, 'sample': n,
           'projection': sum(k*p for k,p in zip(support,mass)), 'range80': quantiles,
           'p_over': probs['over'], 'p_under': probs['under'], 'p_push': probs['push'],
           'inputs': games, 'history_scope': expected_scope,
           'context_used': {k:context[k] for k in ('best_of','surface') if k in context} if sport=='tennis' else {},
           'calibrated': False}
    if scope_rule is not None:
        # sg-scope: only on lines this release unlocked (never on a REGULATION_LEAGUES line - their bytes stay).
        out['scope_rule'] = {k: scope_rule[k] for k in ('version','extra_time','basis','stage','stage_type',
                             'p_extra_time','p_basis') if scope_rule.get(k) is not None}
        out['settlement_scope'] = 'regulation_plus_extra_time' if extra_time_p is not None else 'regulation'
        if extra_time_p is not None:
            out['extra_time'] = {'p': extra_time_p, 'share_of_regulation_rate': expansion_scope.ET_SHARE,
                                 'settlement_rule': 'expansion_scope.PLATFORM_RULES:' + offer['platform']}
            out['settle_scopes'] = ['full_game', 'regulation']   # extra-time box totals first, else the 90 minutes
    if tennis_rule is not None:
        out['format_rule'] = {k: tennis_rule[k] for k in ('best_of', 'basis') if k in tennis_rule}
    return out, None


def make_forecast(offer, facts, now):
    fitted, reason = fit(offer, facts, now)
    if fitted is None:
        return None, reason
    nonpush = 1-fitted['p_push']
    if nonpush <= 1e-9:
        return None, 'no_decisive_probability'
    # Pick'em probability and the shared policy are conditional on a non-push.
    side_probs = {s:fitted['p_'+s]/nonpush for s in ('over','under')}
    offered = [s for s in side_probs if s in offer['sides']]
    if not offered:
        return None, 'no_offered_side'
    # Preserve the model's direction even when an app lists only the other side.
    # Availability must never turn a strong Under forecast into an Over lean.
    chosen = max(side_probs, key=side_probs.get)
    grade = (policy.grade(side_probs[chosen], offer['platform'], offer.get('variant') or 'unknown',
                         multiplier=offer['sides'][chosen].get('multiplier')) if chosen in offered
             else policy.Grade(None, None, 0.0, ('Model side not offered on this line.',)))
    result = {**fitted, 'identity': facts['identity'], 'sport':offer['sport'], 'stat':offer['stat'],
              'scope':offer['scope'], 'line':offer['line'], 'platform':offer['platform'],
              'offer_id':offer['offer_id'], 'offer_revision':offer['revision'], 'start':offer['start'],
              'made_at':now, 'side':chosen, 'p_side':side_probs[chosen], 'grade':grade.letter,
              'grade_policy':grade.policy, 'grade_reason':list(grade.reasons),
              'units':None, 'stake_reason':'Stakes are not assigned by this new model.',
              'probability_basis':'Conditional on a completed, non-push result; model estimate, not calibrated.'}
    result['id'] = digest({k:v for k,v in result.items() if k != 'made_at'})
    return result, None


def connect(sport):
    root = boards.root(sport)
    root.mkdir(parents=True, exist_ok=True)
    if sum(p.stat().st_size for p in root.glob('forecasts.db*')) >= MAX_BYTES:
        raise ValueError('expansion forecast budget reached')
    c = sqlite3.connect(root/'forecasts.db', timeout=2)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS owner(sport TEXT PRIMARY KEY)')
    owners = [r[0] for r in c.execute('SELECT sport FROM owner')]
    if owners and owners != [sport]:
        c.close()
        raise ValueError('forecast store belongs to another sport')
    c.execute('INSERT OR IGNORE INTO owner VALUES (?)',(sport,))
    c.execute('CREATE TABLE IF NOT EXISTS forecasts(id TEXT PRIMARY KEY, offer_key TEXT, competition TEXT, event_id TEXT, start TEXT, payload TEXT)')
    c.execute('CREATE INDEX IF NOT EXISTS forecast_events ON forecasts(competition,event_id,start)')
    c.execute('CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, forecast_id TEXT, made_at TEXT, payload TEXT)')
    c.commit()
    return c


def publish(sport, now=None, budget_s=8):
    from types import SimpleNamespace
    now = now or now_utc()
    deadline = time.monotonic()+budget_s
    status, board = boards.board(SimpleNamespace(query={'sport':sport}))
    if status != 200:
        return {'state':'quotes_unavailable'}
    facts = research.saved(sport).get('players',{})
    counts, current, detail = {}, {}, {}
    with closing(connect(sport)) as c:
        for offer in sorted(board['offers'],key=lambda x:x['start']):
            if time.monotonic() >= deadline:
                counts['budget'] = counts.get('budget',0)+1
                break
            fact = facts.get(research.key(offer),{})
            forecast, reason = make_forecast(offer, fact, now)
            if forecast is None:
                counts[reason] = counts.get(reason,0)+1
                # sg-scope: the competition / stat / platform of each pending reason (learning-status.json, ops)
                comp = (fact.get('identity') or {}).get('competition') or offer.get('competition_code') or 'unlinked'
                k = '|'.join((reason, comp, offer['stat'], offer['platform']))
                detail[k] = detail.get(k,0)+1
                continue
            ident = forecast['identity']
            key = offer_key(offer)
            c.execute('INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?,?,?)',
                      (forecast['id'],key,ident['competition'],ident['event_id'],offer['start'],encode(forecast)))
            # Reuse the original issuance time; repeated runs never rewrite it.
            frozen = json.loads(c.execute('SELECT payload FROM forecasts WHERE id=?',(forecast['id'],)).fetchone()[0])
            current[key] = {k:v for k,v in frozen.items() if k != 'inputs'}
        c.commit()
    atomic_json(boards.root(sport)/'forecasts.json', {'sport':sport,'at':now,'forecasts':current})
    return {'published':len(current),'letters':sum(bool(v['grade']) for v in current.values()),'pending':counts,
            'pending_by':dict(sorted(detail.items(), key=lambda kv:(-kv[1],kv[0]))[:60])}


@lru_cache(maxsize=2)
def _saved(path, stamp, size):
    from pathlib import Path
    return json.loads(Path(path).read_text(encoding='utf-8'))


def attach(sport, data):
    path = boards.root(sport)/'forecasts.json'
    try:
        st = path.stat()
        if st.st_size > 4_000_000:
            return data
        saved = _saved(str(path),st.st_mtime_ns,st.st_size)
        if saved.get('sport') != sport:
            return data
        eligible = {offer_key(o) for o in data['offers']}
        return {**data, 'forecasts':{k:v for k,v in saved['forecasts'].items() if k in eligible}}
    except (OSError, ValueError, KeyError):
        return data


def settle(sport, now=None, budget_s=3):
    """Append model outcome reviews, including corrections; never mutate a pick.

    This is model evaluation conditional on completed play, not bookmaker
    settlement. Missing, conflicting, retired and void results never become 0.
    """
    now = now or now_utc()
    deadline = time.monotonic()+budget_s
    added = 0
    with closing(connect(sport)) as c:
        c.execute('CREATE TABLE IF NOT EXISTS checked(event_key TEXT PRIMARY KEY, at TEXT)')
        rows = c.execute('''SELECT f.competition,f.event_id FROM forecasts f
            LEFT JOIN checked k ON k.event_key=f.competition||'|'||f.event_id
            WHERE f.start<? GROUP BY f.competition,f.event_id ORDER BY COALESCE(k.at,''),f.start LIMIT 50''',(now,)).fetchall()
        for event in rows:
            if time.monotonic() >= deadline:
                break
            comp, eid = event['competition'],event['event_id']
            path = isolated_database(boards.root(sport).parent,comp)
            if not path.exists():
                continue
            with closing(store.connect(path,read_only=True)) as source:
                observations = store._latest_rows(source,'stat','event_id=?',[eid],now,2000)
            by_key = {}
            for row in observations:
                obs = store._rec(row)
                by_key.setdefault((obs.participant_id,obs.stat,obs.scope),[]).append((row,obs))
            for row in c.execute('SELECT id,payload FROM forecasts WHERE competition=? AND event_id=?',(comp,eid)):
                f = json.loads(row['payload'])
                candidates = []
                for result_scope in f.get('settle_scopes') or [f['history_scope']]:
                    candidates = by_key.get((f['identity']['player_id'],f['stat'],result_scope),[])
                    if candidates:
                        break
                if not candidates:
                    continue
                values = {o.value for _,o in candidates}
                valid = all(o.final and o.participation=='played' and o.value is not None and math.isfinite(o.value)
                            for _,o in candidates) and len(values)==1
                actual = next(iter(values)) if valid else None
                result_ids = sorted(r['record_id'] for r,_ in candidates)
                rid = digest([f['id'],result_ids])
                outcome = None if actual is None else 'push' if actual==f['line'] else 'over' if actual>f['line'] else 'under'
                report = {'forecast_id':f['id'],'actual':actual,'outcome':outcome,
                          'won':None if outcome in (None,'push') else outcome==f['side'],
                          'projection_error':None if actual is None else f['projection']-actual,
                          'result_ids':result_ids,'known_at':max(o.known_at for _,o in candidates),
                          'kind':'model_evaluation','book_settlement':False}
                added += c.execute('INSERT OR IGNORE INTO reviews VALUES (?,?,?,?)',
                                   (rid,f['id'],now,encode(report))).rowcount
            c.execute('INSERT OR REPLACE INTO checked VALUES (?,?)',(comp+'|'+eid,now))
            c.commit()
    return {'added':added}
