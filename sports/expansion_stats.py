"""sg-stats (2026-11-20): models for the soccer/tennis stat types the count model does not cover.

Every model here is built from component stats the history workers already store - nothing new is collected:

- Sums of counts (Goal + Assist, Cards, Shots Assisted): the per-game total is itself a count, so it gets the same
  overdispersed count predictive as every other count stat (expansion_forecasts.fit), on the summed series.
- First-half goals: regulation goals predictive, each goal falling in the first half with the league-wide share
  measured on past ESPN goal events (binomial thinning). No first-half box score is stored, so no first-half history.
- Fantasy scores (PrizePicks outfield / goalie, PrizePicks tennis): the book's scoring formula applied to each past
  game's components, then a predictive for a continuous score fitted on that per-game series (never a convolution
  of independent components: shots, shots on target and goals move together).
- Tennis 2nd-set total games: both players' second-set games from the stored set scores (finite score support).

Absent FotMob fields: FotMob's playerStats lists a player's stat only when it is non-zero (total_shots, crosses,
dribbles appear for 11-14 of 32 players in a probed EPL match while minutes / clearances / tackles appear for all).
A FotMob component is therefore 0 when FotMob reported that player's match (a stored 'minutes' row) and the field is
absent; without a minutes row the game is not used.

Grade leak guard: every component row used must be in expansion_forecasts.FORECAST_INPUTS (same frozen list).
Stakes: the soccer/tennis count model assigns no stakes; these models carry 0 units and a definite stake reason by
walk-forward tier (STAKE_TIERS, from C:/Users/tikic/cs2_scout_ops/logs/soccer_grades_1120/stats/). Forecasts of the
stats the count model already handles are untouched (their payloads and ids stay byte-identical).
"""
import math

VERSION = 'expansion-components-v1'

# PrizePicks soccer fantasy scoring (PrizePicks playbook, read 2026-11-20).
PP_OUTFIELD = {'goals': 10.0, 'assists': 5.0, 'shots': 1.0, 'shots_on_target': 1.0, 'passes_attempted': 0.05,
               'chances_created': 0.5, 'clearances': 1.0, 'tackles': 1.0, 'attempted_dribbles': 1.0, 'crosses': 0.5,
               'yellow_cards': -1.0, 'red_cards': -2.0, 'fouls': -0.5}
PP_GOALIE = {'start': 5.0, 'saves': 2.0, 'goals_conceded': -2.0, 'clean_sheet': 5.0}
# PrizePicks tennis fantasy scoring (Rotowire DFS Tennis 101 / PrizePicks): match played 10, game won 1, game lost -1,
# set won 3, set lost -3, ace 0.5, double fault -0.5.
PP_TENNIS = {'played': 10.0, 'games_won': 1.0, 'games_lost': -1.0, 'sets_won': 3.0, 'sets_lost': -3.0,
             'aces': 0.5, 'double_faults': -0.5}
FOTMOB_PARTS = frozenset({'passes_attempted', 'chances_created', 'clearances', 'tackles', 'attempted_dribbles',
                          'crosses', 'minutes'})

# stat -> (sport, kind, components)
MODELS = {
    'goals_assists': ('soccer', 'count_sum', {'goals': 1, 'assists': 1}),
    'cards': ('soccer', 'count_sum', {'yellow_cards': 1, 'red_cards': 1}),
    'shots_assisted': ('soccer', 'count_sum', {'chances_created': 1}),
    'fantasy_outfield': ('soccer', 'score', PP_OUTFIELD),
    'fantasy_goalie': ('soccer', 'score', PP_GOALIE),
    'fantasy_tennis': ('tennis', 'score', PP_TENNIS),
}
# (sport, stat, scope) handled here although the stat name is an ordinary one.
SCOPED = {('soccer', 'goals', 'first_half'): 'first_half_goals', ('tennis', 'games_played', 'set2'): 'set2_games'}
# Share of regulation goals scored in the first half (incl. first-half stoppage time), measured on ESPN goal events
# in the walk-forward tune window (stats/first_half_share.json). Set only from that measurement.
FIRST_HALF_GOAL_SHARE = 0.4420     # 2,393 of 5,414 scorer-credited goals, TUNE window
# Cards: the summed count settles 'any card' identically for a 0.5 line whatever a red card counts for; above it the
# Underdog rule (red = 1 or 2) is unverified, so those lines are not graded.
CARDS_MAX_LINE = 1.0

# Walk-forward tier per model, holdout Sep 1 - Nov 19 2026 (stats/wf_final/walkforward_summary.json):
# 'passed' = Brier beats the line base rate and every printed-chance band's 95% interval covers its mean chance;
# 'close' = Brier beats the base rate and every band is within 3 points (the live count model, scored the same way on
# fouls suffered, is within 2.5); 'failed' = neither. Tennis fantasy: scored on its games/sets part (no past
# aces / double faults in the test data) and failed. Every tier shows its letter and chance; all stake 0 units.
STAKE_TIERS = {'first_half_goals': 'passed', 'cards': 'close', 'shots_assisted': 'close', 'fantasy_outfield': 'close',
               'goals_assists': 'failed', 'fantasy_goalie': 'failed', 'set2_games': 'failed', 'fantasy_tennis': 'failed'}
STAKE_REASONS = {
    'passed': '0U: new model - passed its walk-forward calibration test; stakes wait for settled real lines.',
    'close': '0U: new model - walk-forward chances within 3 points of results; stakes wait for settled real lines.',
    'failed': '0U: new model - missed its walk-forward calibration test.',
}


def model_of(offer):
    """The sg-stats model name for an offer, or None when the count model handles it."""
    sport, stat, scope = offer.get('sport'), offer.get('stat'), offer.get('scope')
    if (sport, stat, scope) in SCOPED:
        return SCOPED[(sport, stat, scope)]
    spec = MODELS.get(stat)
    return stat if spec and spec[0] == sport else None


def history_scope(offer):
    """The stored scope a model's per-game series is built from."""
    m = model_of(offer)
    if m == 'set2_games':
        return 'set2'
    if offer['sport'] == 'soccer':
        return 'regulation'
    return 'full_game'


# ---------------------------------------------------------------- per-game series (worker side, research.publish)

def _rows(con, store, player, stat, scope, before):
    """{event_id: (value, source, known_at, start)} - the same validity rules as expansion_history._history: final,
    played, a value, every source agreeing, event started before `before`."""
    by_event = {}
    for row in store._latest_rows(con, 'stat', 'player_id=? AND stat=?', [player, stat], before, 2000):
        obs = store._rec(row)
        if obs.scope != scope:
            continue
        event = store._any_event(con, obs.event_id, before)
        if event and event.start_utc and event.start_utc < before:
            by_event.setdefault(obs.event_id, []).append((obs, event))
    out = {}
    for eid, observations in by_event.items():
        if not all(o.final and o.participation == 'played' and o.value is not None for o, _ in observations) or \
                len({o.value for o, _ in observations}) != 1:
            continue
        obs, event = sorted(observations, key=lambda item: item[0].source)[0]
        out[eid] = (obs.value, obs.source, max(o.known_at for o, _ in observations), event.start_utc)
    return out


def _opponent_sets(catalog, eid, player):
    past = (catalog or {}).get(eid) or {}
    sets = past.get('set_scores') or {}
    if player not in sets or len(sets) != 2:
        return None, None
    other = next(k for k in sets if k != player)
    return sets[player], sets[other]


def _conceded(catalog, eid, player):
    past = (catalog or {}).get(eid) or {}
    team = next((p.get('team_id') for p in past.get('players', []) if p.get('id') == player), None)
    scores = past.get('scores') or {}
    if not team or team not in scores or len(scores) != 2:
        return None
    return next(v for k, v in scores.items() if k != team)


def games(con, store, offer, player, before, catalog=None, limit=20):
    """Per-game values of the offer's model, newest first, each with the component sources it read."""
    model = model_of(offer)
    if model is None:
        return []
    scope = history_scope(offer)
    out = []
    if model == 'first_half_goals':
        for eid, (v, src, known, start) in _rows(con, store, player, 'goals', scope, before).items():
            out.append({'event_id': eid, 'date': start, 'value': v, 'source': src, 'known_at': known,
                        'sources': [[src, 'goals']]})
    elif model == 'set2_games':
        for eid, (v, src, known, start) in _rows(con, store, player, 'games_won', 'set2', before).items():
            mine, theirs = _opponent_sets(catalog, eid, player)
            if not mine or len(mine) < 2 or mine[1] != v:
                continue
            out.append({'event_id': eid, 'date': start, 'value': v + theirs[1], 'source': src, 'known_at': known,
                        'sources': [[src, 'games_won']]})
    else:
        _, kind, parts = MODELS[model]
        need = [p for p in parts if p not in ('start', 'goals_conceded', 'clean_sheet', 'played', 'games_lost',
                                              'sets_lost')]
        if model == 'fantasy_tennis':
            need = ['games_won', 'sets_won', 'aces', 'double_faults']
        fotmob = [p for p in need if p in FOTMOB_PARTS]
        espn = [p for p in need if p not in FOTMOB_PARTS]
        series = {p: _rows(con, store, player, p, scope, before) for p in espn}
        minutes = _rows(con, store, player, 'minutes', scope, before) if fotmob else {}
        fm = {p: _rows(con, store, player, p, scope, before) for p in fotmob}
        anchor = series[espn[0]] if espn else minutes
        for eid in anchor:
            if any(eid not in series[p] for p in espn) or (fotmob and eid not in minutes):
                continue
            vals, srcs, known = {}, [], []
            for p in espn:
                v, src, k, start = series[p][eid]
                vals[p] = v
                srcs.append([src, p])
                known.append(k)
            if fotmob:
                v, src, k, start = minutes[eid]
                srcs.append([src, 'minutes'])
                known.append(k)
                for p in fotmob:
                    hit = fm[p].get(eid)
                    vals[p] = hit[0] if hit else 0.0          # FotMob lists non-zero fields only (module doc)
                    if hit:
                        srcs.append([hit[1], p])
                        known.append(hit[2])
            start = anchor[eid][3]
            if model == 'fantasy_goalie':
                conceded = _conceded(catalog, eid, player)
                if conceded is None:
                    continue
                value = PP_GOALIE['start'] + PP_GOALIE['saves'] * vals['saves'] + \
                    PP_GOALIE['goals_conceded'] * conceded + (PP_GOALIE['clean_sheet'] if conceded == 0 else 0.0)
                srcs.append(['catalog', 'score'])
            elif model == 'fantasy_tennis':
                mine, theirs = _opponent_sets(catalog, eid, player)
                if not mine or sum(mine) != vals['games_won']:
                    continue
                lost_games = sum(theirs)
                won_sets = vals['sets_won']
                lost_sets = sum(b > a for a, b in zip(mine, theirs))
                value = (PP_TENNIS['played'] + vals['games_won'] - lost_games + 3 * won_sets - 3 * lost_sets +
                         0.5 * vals['aces'] - 0.5 * vals['double_faults'])
            else:
                value = sum(w * vals[p] for p, w in parts.items())
            value = round(value, 4)
            out.append({'event_id': eid, 'date': start, 'value': value, 'source': srcs[0][0],
                        'known_at': max(known), 'sources': sorted(srcs)})
    return sorted(out, key=lambda r: (r['date'], r['event_id']), reverse=True)[:limit]


# ---------------------------------------------------------------- predictive distributions (pure)

def count_predictive(values):
    """The count model's Poisson-Gamma posterior predictive (expansion_forecasts.fit, same formulas) -> support, mass."""
    n = len(values)
    avg = sum(values) / n
    mu = (sum(values) + .5) / n
    variance = sum((v - avg) ** 2 for v in values) / (n - 1)
    alpha = max(1 / (sum(values) + .5), (variance - mu) / (mu * mu))
    shape = 1 / alpha
    prob = shape / (shape + mu)
    support, mass, cumulative = [], [], 0.0
    for k in range(4096):
        pk = math.exp(math.lgamma(k + shape) - math.lgamma(shape) - math.lgamma(k + 1)
                      + shape * math.log(prob) + k * math.log1p(-prob))
        support.append(k)
        mass.append(pk)
        cumulative += pk
        if k > mu and cumulative > 1 - 1e-10:
            break
    if sum(mass) < 1 - 1e-6:
        return None, None
    total = sum(mass)
    return support, [p / total for p in mass]


# Population prior for the component count models: (prior mean per game, strength in pseudo-games). The count model's
# fixed half-count prior over-states rare events (walk-forward: Goal + Assist printed 12.4% Over vs 7.7% observed),
# so these models shrink each player's rate toward the population mean instead. Both numbers per model come from the
# walk-forward TUNE window only (stats/count_prior_tuning.json); k = None keeps the count model's own formula.
COUNT_PRIOR = {'goals_assists': (0.1531, 8), 'cards': (0.1308, 16), 'shots_assisted': (0.6568, 4),
               'first_half_goals': (0.0897, 8)}   # first_half_goals: the prior is on regulation goals, then thinned


def prior_count_predictive(values, model):
    """Gamma-Poisson predictive with a population prior: rate mean (sum + k*m0) / (n + k); dispersion as the count
    model (observed over-dispersion only widens it). Falls back to count_predictive when no prior is set."""
    m0, k = COUNT_PRIOR.get(model, (None, None))
    if not k:
        return count_predictive(values)
    n = len(values)
    total = sum(values)
    mu = (total + k * m0) / (n + k)
    avg = total / n
    variance = sum((v - avg) ** 2 for v in values) / (n - 1)
    alpha = max(1 / (total + k * m0), (variance - mu) / (mu * mu))
    shape = 1 / alpha
    prob = shape / (shape + mu)
    support, mass, cumulative = [], [], 0.0
    for x in range(4096):
        px = math.exp(math.lgamma(x + shape) - math.lgamma(shape) - math.lgamma(x + 1)
                      + shape * math.log(prob) + x * math.log1p(-prob))
        support.append(x)
        mass.append(px)
        cumulative += px
        if x > mu and cumulative > 1 - 1e-10:
            break
    if sum(mass) < 1 - 1e-6:
        return None, None
    t = sum(mass)
    return support, [p / t for p in mass]


def thin(support, mass, share):
    """Binomial thinning: each counted event lands in the sub-period with probability `share`."""
    out = [0.0] * len(support)
    for n, pn in zip(support, mass):
        if pn < 1e-15:
            continue
        for k in range(n + 1):
            out[k] += pn * math.comb(n, k) * share ** k * (1 - share) ** (n - k)
    total = sum(out)
    return support, [p / total for p in out]


def _t_cdf(x, df):
    """Student-t CDF via the regularized incomplete beta function (continued fraction)."""
    if df <= 0:
        raise ValueError('df')
    t = df / (df + x * x)
    ib = _betainc(df / 2, .5, t)
    return 1 - .5 * ib if x > 0 else .5 * ib


def _betainc(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbeta) * _cf(a, b, x) / a
    return 1 - math.exp(lbeta) * _cf(b, a, 1 - x) / b


def _cf(a, b, x):
    tiny = 1e-300
    c, d = 1.0, 1 - (a + b) * x / (a + 1)
    d = 1 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((a + m2 - 1) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > tiny else tiny)
        c = 1 + aa / c if abs(1 + aa / c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (a + b + m) * x / ((a + m2) * (a + m2 + 1))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > tiny else tiny)
        c = 1 + aa / c if abs(1 + aa / c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-12:
            break
    return h


# Fantasy-score predictive: Student-t on the per-game series. SCORE_SD_FLOOR keeps a short run of near-identical
# games from looking certain; SCORE_WIDEN is the walk-forward-tuned scale on the sample spread (tune window only).
SCORE_SD_FLOOR = {'fantasy_outfield': 2.0, 'fantasy_goalie': 3.0, 'fantasy_tennis': 4.0}
SCORE_WIDEN = {'fantasy_outfield': 1.0, 'fantasy_goalie': 1.0, 'fantasy_tennis': 1.0}


def score_predictive(values, model):
    """-> (mean, scale, df): location-scale Student-t predictive for a new game's score."""
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    sd = max(sd, SCORE_SD_FLOOR[model]) * SCORE_WIDEN[model]
    return mean, sd * math.sqrt(1 + 1 / n), n - 1


# Per-model predictive family, chosen on the walk-forward TUNE window by log loss (stats/fantasy_widen_tuning.json):
# 'kde' = Gaussian-kernel mixture on the past games (keeps the right skew a goal adds), 't' = Student-t.
SCORE_METHOD = {'fantasy_outfield': 'kde', 'fantasy_goalie': 't', 'fantasy_tennis': 'kde'}
# Regression to the population: past scores move toward m0 by share lam before the predictive is fitted (TUNE window,
# stats/fantasy_widen_tuning.json). lam 0 = no shrinkage.
SCORE_SHRINK = {'fantasy_outfield': (8.0463, 0.2), 'fantasy_goalie': (9.3569, 0.4), 'fantasy_tennis': (0.0, 0.0)}


def shrunk(values, model):
    m0, lam = SCORE_SHRINK.get(model, (0.0, 0.0))
    if not lam:
        return list(values)
    mean = sum(values) / len(values)
    return [v + lam * (m0 - mean) for v in values]


def _phi(z):
    return .5 * math.erfc(-z / math.sqrt(2))


def kde_bandwidth(values, model):
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    return max(sd, SCORE_SD_FLOOR[model]) * 1.06 * n ** -.2 * SCORE_WIDEN[model]


def score_probs(values, model, line, method=None):
    """-> (mean, scale, df, p_over) for a new game's score; df None for the kernel mixture."""
    method = method or SCORE_METHOD[model]
    values = shrunk(values, model)
    if method == 'kde':
        h = kde_bandwidth(values, model)
        over = sum(1 - _phi((line - v) / h) for v in values) / len(values)
        return sum(values) / len(values), h, None, over
    mean, scale, df = score_predictive(values, model)
    over = 1 - _t_cdf((line - mean) / scale, df)
    return mean, scale, df, over


def score_quantile(p, values, model, method=None):
    method = method or SCORE_METHOD[model]
    if method != 'kde':
        mean, scale, df = score_predictive(shrunk(values, model), model)
        return t_quantile(p, mean, scale, df)
    lo, hi = min(values) - 60.0, max(values) + 60.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if 1 - score_probs(values, model, mid, 'kde')[3] < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def t_quantile(p, mean, scale, df):
    lo, hi = -60.0, 60.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return mean + scale * (lo + hi) / 2


# ---------------------------------------------------------------- the forecast fit

def fit(offer, facts, now, inputs, parse_utc, min_games):
    """Same contract as expansion_forecasts.fit for the sg-stats models: (fitted, None) or (None, reason).
    Called after fit()'s identity / event / competition / future-context checks."""
    model = model_of(offer)
    ident = facts.get('identity') or {}
    sport, scope = offer['sport'], offer['scope']
    context = facts.get('context') or {}
    if model == 'cards' and offer['line'] >= CARDS_MAX_LINE:
        return None, 'cards_rule_unverified'
    if sport == 'soccer' and scope not in ('full_game', 'regulation', 'first_half'):
        return None, 'period_not_modelled'
    if sport == 'soccer' and scope == 'full_game' and model != 'first_half_goals':
        from .expansion_forecasts import REGULATION_LEAGUES
        if ident['competition'] not in REGULATION_LEAGUES:
            return None, 'extra_time_scope_unverified'
    if sport == 'soccer' and model != 'first_half_goals' and scope == 'first_half':
        return None, 'period_not_modelled'
    if model == 'fantasy_tennis' and context.get('best_of') not in (3, 5):
        return None, 'match_format_missing'
    expected = history_scope(offer)
    if facts.get('scope') != expected:
        return None, 'history_period_mismatch'
    support = list(range(6, 14)) if model == 'set2_games' else None
    picked, seen = [], set()
    for g in sorted(facts.get('games') or [], key=lambda x: x['date'], reverse=True):
        value = g.get('value')
        srcs = g.get('sources') or []
        if not srcs or any((ident.get('competition'), s, p) not in inputs for s, p in srcs if s != 'catalog'):
            continue   # weight 0: a component outside FORECAST_INPUTS (grade leak guard)
        if (g['event_id'] == ident['event_id'] or g['event_id'] in seen or
                not g.get('known_at') or parse_utc(g['known_at']) > parse_utc(now) or
                parse_utc(g['date']) >= parse_utc(now) or isinstance(value, bool) or
                not isinstance(value, (int, float)) or not math.isfinite(value)):
            continue
        if MODELS.get(model, (None, 'count_sum'))[1] == 'count_sum' or model in ('first_half_goals', 'set2_games'):
            if value < 0 or int(value) != value:
                continue
        if sport == 'tennis' and model == 'fantasy_tennis' and g.get('best_of') != context.get('best_of'):
            continue
        if sport == 'tennis' and context.get('surface') and g.get('surface') != context['surface']:
            continue
        if support is not None and value not in support:
            continue
        seen.add(g['event_id'])
        picked.append(g)
        if len(picked) == 20:
            break
    if len(picked) < min_games:
        return None, 'more_exact_history_needed'
    values = [g['value'] for g in picked]
    n, line = len(values), offer['line']
    if model in ('fantasy_outfield', 'fantasy_goalie', 'fantasy_tennis'):
        mean, scale, df, p_over = score_probs(values, model, line)
        probs = {'over': p_over, 'under': 1 - p_over, 'push': 0.0}
        projection = mean
        distribution = 'score-kernel-mixture' if SCORE_METHOD[model] == 'kde' else 'score-student-t'
        quantiles = [round(score_quantile(.1, values, model), 2), round(score_quantile(.9, values, model), 2)]
    else:
        if support is not None:
            mass = [(values.count(k) + 1 / len(support)) / (n + 1) for k in support]
            distribution = 'finite-score-dirichlet'
        else:
            support, mass = prior_count_predictive(values, model)
            if support is None:
                return None, 'distribution_tail_unresolved'
            distribution = 'overdispersed-count-predictive'
            if model == 'first_half_goals':
                support, mass = thin(support, mass, FIRST_HALF_GOAL_SHARE)
                distribution = 'regulation-count-thinned-first-half'
        probs = {name: sum(p for k, p in zip(support, mass) if rule(k)) for name, rule in
                 [('over', lambda k: k > line), ('under', lambda k: k < line), ('push', lambda k: k == line)]}
        projection = sum(k * p for k, p in zip(support, mass))
        cumulative, quantiles = 0, []
        for k, p in zip(support, mass):
            cumulative += p
            if not quantiles and cumulative >= .1:
                quantiles.append(k)
            if len(quantiles) == 1 and cumulative >= .9:
                quantiles.append(k)
                break
    tier = STAKE_TIERS.get(model, 'failed')
    return {'model_version': VERSION, 'component_model': model, 'distribution': distribution, 'sample': n,
            'projection': projection, 'range80': quantiles,
            'p_over': probs['over'], 'p_under': probs['under'], 'p_push': probs['push'],
            'inputs': picked, 'history_scope': expected,
            'context_used': {k: context[k] for k in ('best_of', 'surface') if k in context} if sport == 'tennis' else {},
            'calibrated': False, 'stake_tier': tier}, None


# ---------------------------------------------------------------- settlement of a component forecast (model review)

def actual(forecast, by_key):
    """Component total for a settled soccer forecast from the event's stored observations, or None (missing,
    conflicting or non-participation never becomes 0). by_key: {(player, stat, scope): [(row, obs), ...]}."""
    model = forecast.get('component_model')
    if model not in ('goals_assists', 'cards', 'shots_assisted', 'fantasy_outfield'):
        return None, []
    _, _, parts = MODELS[model]
    pid, scope = forecast['identity']['player_id'], forecast['history_scope']
    total, used = 0.0, []

    def one(stat):
        cands = by_key.get((pid, stat, scope), [])
        if not cands:
            return None, []
        values = {o.value for _, o in cands}
        ok = all(o.final and o.participation == 'played' and o.value is not None and math.isfinite(o.value)
                 for _, o in cands) and len(values) == 1
        return (next(iter(values)) if ok else False), cands

    fotmob_seen = None
    for stat, w in parts.items():
        v, cands = one(stat)
        if v is None and stat in FOTMOB_PARTS:
            if fotmob_seen is None:
                fotmob_seen, mc = one('minutes')
                used += mc
            if fotmob_seen in (None, False):
                return None, []
            v = 0.0
        if v is None or v is False:
            return None, []
        used += cands
        total += w * v
    return round(total, 4), used
