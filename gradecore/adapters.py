"""Thin adapters over immutable projection facts. No sport model is imported.

The callers supply records at their forecast-write boundary, before display
calibration. Missing identities fail closed; display probabilities are never a
fallback for missing raw probabilities.

market()  the quote SAVED WITH the forecast, never one looked up later: CS2 = the
          board's per-side decimal prices in forecasts.quote (Underdog; PrizePicks
          lists none), LoL = Underdog's decimal prices in lol_forecasts.sides,
          NBA/NFL = the mc-1 consensus forecast written in the same forward run.
          Given as MarketQuote(P(Over | no push), P(Under | no push), source,
          width = overround when prices are known, age_s = forecast time minus the
          quote's observation). A one-sided, unpriced or later-than-the-forecast
          quote is no quote (None) - the shadow then grades on the model alone.
settle()  the saved settlement attached by settled(): status relative to the
          saved side; actual == line is a push and a push is never a win.
"""
import json
import math
from dataclasses import dataclass, replace
from .contracts import MarketQuote, Offer, Prediction, Settlement

SIDES = {'More': 'over', 'Less': 'under', 'Over': 'over', 'Under': 'under', 'over': 'over', 'under': 'under',
         'higher': 'over', 'lower': 'under'}
CONSENSUS_MODELS = ('mc-1',)                 # sports/model/market.VERSION_CONSENSUS; mkt-<source> = one source


@dataclass
class SavedProjection:
    sport: str
    offer: Offer
    prediction: Prediction
    forecast_id: str
    forecast_at: str
    team: str
    old_letter: str | None
    old_p: float | None
    facts: tuple = ()
    side: str | None = None                  # the side the saved forecast selected / published (None = not saved)
    quote: MarketQuote | None = None         # the market quote saved with the forecast (None = none usable)
    settlement: Settlement | None = None     # attached by settled(); None until a result is joined

    def offers(self, as_of): return [self.offer] if self.forecast_at <= as_of else []
    def _check(self, offer):
        if offer != self.offer: raise ValueError('projection belongs to another offer')
    def predict(self, offer): self._check(offer); return self.prediction
    def market(self, offer): self._check(offer); return self.quote
    def flags(self, offer): self._check(offer); return self.facts
    def settle(self, offer): self._check(offer); return self.settlement
    def match_key(self, offer): self._check(offer); return self.sport + ':' + offer.event_id
    def team_key(self, offer): self._check(offer); return self.team
    def correlation(self, a, b):
        return 0.0 if (a.sport, a.event_id) != (b.sport, b.event_id) else None


# The books' own names for their ORDINARY line: Underdog 'balanced' (LoL odds_type and the sports adapter), sports book
# 'main'. They are the standard payout line, so the policy must see payouts.STANDARD_VARIANT, not an unknown alt
# (bug sweep 2026-11-06 #15: every Underdog row read 'Alt line: payout unknown'). Other variants pass through unchanged.
ORDINARY_VARIANTS = (None, '', 'standard', 'balanced', 'main')


def ordinary_variant(variant):
    from .payouts import STANDARD_VARIANT
    return STANDARD_VARIANT if variant in ORDINARY_VARIANTS else variant


def _make(sport, event, player, stat, scope, line, book, variant, start,
          raw, projection, model, artifact, fid, at, team, old_letter, old_p, rounded=False):
    if not all(str(x or '').strip() for x in (event, player, stat, scope, book, model, artifact, fid, at, start)):
        raise ValueError('incomplete immutable projection identity')
    from .evaluation import timestamp
    if timestamp(at) >= timestamp(start): raise ValueError('projection was not saved before start')
    # Legacy logs rounded each component separately. Correct only rounding
    # drift, retaining the original triple in the journal for exact replay.
    values = tuple(raw)
    if rounded:
        if any(isinstance(x, bool) or not isinstance(x, (int,float)) or not 0 <= x <= 1 for x in values):
            raise ValueError('invalid rounded probabilities')
        total = sum(values)
        if abs(total-1) > .0002: raise ValueError('probabilities differ beyond rounding precision')
        values = tuple(x/total for x in values)
    prediction = Prediction(*values, projection=projection, versions={
        'model_version':model, 'artifact_version':artifact,
        'probability_stage':'raw_distribution_rounded_4dp' if rounded else 'saved_model_before_board_calibration',
        'saved_probabilities':list(raw)})
    offer = Offer(sport,sport,str(event),str(player),stat,str(scope),line,book,ordinary_variant(variant),start)
    return SavedProjection(sport,offer,prediction,str(fid),at,str(team or ''),old_letter,old_p)


# ------------------------------------------------------------------------------------------------ saved quotes
def _number(x):
    """A finite number from a saved value (numbers or numeric strings), else None."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x) if math.isfinite(x) else None
    if isinstance(x, str):
        try:
            v = float(x.strip())
        except ValueError:
            return None
        return v if math.isfinite(v) else None
    return None


def _age(observed_at, forecast_at):
    """Seconds from the quote's observation to the forecast, or None when the quote is later / unreadable."""
    from .evaluation import timestamp
    try:
        age = (timestamp(str(forecast_at)) - timestamp(str(observed_at))).total_seconds()
    except (TypeError, ValueError, AttributeError):
        return None
    return age if age >= 0 else None


def quote_from_prices(price_over, price_under, source, observed_at, forecast_at):
    """Two decimal prices -> MarketQuote (multiplicative de-vig, width = overround), or None when a side is missing, a
    price is not above 1, or the quote was not observed at or before the forecast."""
    po, pu = _number(price_over), _number(price_under)
    if po is None or pu is None or po <= 1.0 or pu <= 1.0 or not observed_at:
        return None
    age = _age(observed_at, forecast_at)
    if age is None:
        return None
    io, iu = 1.0 / po, 1.0 / pu
    return MarketQuote(io / (io + iu), iu / (io + iu), str(source), round(io + iu - 1.0, 6), age)


def quote_from_probabilities(p_over, p_under, source, observed_at, forecast_at, p_push=0.0, width=None):
    """Saved unconditional chances -> MarketQuote given no push, or None (invalid numbers, no decided mass, or a quote
    later than the forecast)."""
    vals = [_number(p_over), _number(p_under), _number(p_push if p_push is not None else 0.0)]
    if any(v is None or not 0.0 <= v <= 1.0 for v in vals) or vals[0] + vals[1] <= 0.0 or not observed_at:
        return None
    age = _age(observed_at, forecast_at)
    if age is None:
        return None
    d = vals[0] + vals[1]
    return MarketQuote(vals[0] / d, vals[1] / d, str(source), width, age)


def cs2_quote(saved, book, forecast_at):
    """forecasts.quote (JSON text or dict: price_over / price_under / updated_at / board_at) -> (MarketQuote, observed_at)
    or (None, None). PrizePicks saves no prices; Underdog's two decimal prices de-vig like LoL's."""
    q = saved
    if isinstance(q, (str, bytes)):
        try:
            q = json.loads(q)
        except ValueError:
            return None, None
    if not isinstance(q, dict):
        return None, None
    observed = q.get('updated_at') or q.get('board_at')
    quote = quote_from_prices(q.get('price_over'), q.get('price_under'), (book or 'underdog') + ' prices', observed, forecast_at)
    return (quote, observed) if quote else (None, None)


def lol_quote(row, forecast_at):
    """lol_forecasts.sides (Underdog: higher / lower with decimal_price and status) + quote_observed_at -> (MarketQuote,
    observed_at) or (None, None). PrizePicks sides carry no price."""
    sides = row.get('sides')
    if isinstance(sides, (str, bytes)):
        try:
            sides = json.loads(sides)
        except ValueError:
            return None, None
    if row.get('platform') != 'underdog' or not isinstance(sides, dict):
        return None, None
    hi, lo = sides.get('higher') or {}, sides.get('lower') or {}
    if not isinstance(hi, dict) or not isinstance(lo, dict):
        return None, None
    if any(s.get('status') not in (None, 'active') for s in (hi, lo)):
        return None, None
    observed = row.get('quote_observed_at')
    quote = quote_from_prices(hi.get('decimal_price'), lo.get('decimal_price'), 'underdog prices', observed, forecast_at)
    return (quote, observed) if quote else (None, None)


def sports_quote(consensus, forecast):
    """The mc-1 consensus forecast written in the same run (sports/model/forward.py) -> MarketQuote or None. Identity
    must match the raw forecast exactly; the consensus's own forecast_at is its observation time (every quote it used
    was observed at or before that time by market.quote_prob)."""
    if consensus is None:
        return None
    ident = ('league', 'event_id', 'player_id', 'stat', 'scope', 'line')
    if any(getattr(consensus, k, None) != getattr(forecast, k, None) for k in ident):
        return None
    name = str(getattr(consensus, 'model_version', '') or '')
    if name not in CONSENSUS_MODELS and not name.startswith('mkt-'):
        return None
    return quote_from_probabilities(getattr(consensus, 'p_over', None), getattr(consensus, 'p_under', None),
                                    name + ' consensus', getattr(consensus, 'forecast_at', None), forecast.forecast_at,
                                    getattr(consensus, 'p_push', 0.0))


# ------------------------------------------------------------------------------------------- saved settlements
def settlement_of(status, actual, line, side):
    """A saved result -> Settlement relative to `side`: void stays void; actual == line is a push (never a win); else win /
    loss by the side. -> None when the result has no actual yet."""
    a = _number(actual)
    if status == 'void':
        return Settlement('void', a)
    if a is None:
        return None
    if a == line:
        return Settlement('push', a)
    if side not in ('over', 'under'):
        raise ValueError('a decided settlement needs the selected side')
    return Settlement('win' if ((a > line) == (side == 'over')) else 'loss', a)


def settled(projection, status, actual):
    """The same saved projection with its settlement attached, so settle() returns it (None result -> unchanged)."""
    return replace(projection, settlement=settlement_of(status, actual, projection.offer.line, projection.side))


# ---------------------------------------------------------------------------------------------- sport adapters
def cs2(row, extra, forecast_id):
    raw = extra.get('probs_raw')
    if isinstance(raw,str): raw=json.loads(raw)
    if not isinstance(raw,dict): raise ValueError('raw CS2 distribution missing')
    proj = _make('cs2',row.get('match_slug'),row.get('slug'),row.get('prop'),row.get('scope'),
                 row['line'],row.get('book'),row.get('line_type'),row.get('match_start'),
                 [raw[k] for k in ('over','under','push')],row.get('lambda'),row.get('model_version'),
                 row.get('model_version'),forecast_id,row['logged_at'],row.get('team'),row.get('grade'),row.get('p_pick'),True)
    quote, _ = cs2_quote(extra.get('quote'), row.get('book'), row['logged_at'])
    return replace(proj, side=SIDES.get(row.get('pick')), quote=quote)


def lol(row, forecast_id, forecast_at):
    proj = _make('lol',row.get('sid'),row.get('pid'),row.get('stat'),row.get('scope'),row['line'],
                 row.get('platform'),row.get('odds_type'),row.get('series_start'),
                 [row[k] for k in ('p_over','p_under','p_push')],row.get('lambda'),row.get('model_version'),
                 row.get('fit_sha'),forecast_id,forecast_at,row.get('tid'),row.get('grade_label'),
                 row.get('cal_p_over') if row.get('model_side')=='over' else row.get('cal_p_under'),True)
    quote, _ = lol_quote(row, forecast_at)
    side = row.get('model_side')
    return replace(proj, side=side if side in ('over', 'under') else None, quote=quote)


def sports(forecast, offer, start, consensus=None):
    if forecast.league not in ('nba','nfl') or forecast.model_version != 'sm-1':
        raise ValueError('only primary NBA/NFL raw model forecasts enter this candidate')
    if (forecast.league,forecast.event_id,forecast.player_id,forecast.stat,forecast.scope,forecast.line) != (
            offer.league,offer.event_id,offer.player_id,offer.stat,offer.scope,offer.line):
        raise ValueError('forecast/offer identity mismatch')
    proj = _make(forecast.league,forecast.event_id,forecast.player_id,forecast.stat,forecast.scope,forecast.line,
                 offer.platform,offer.variant,start,
                 [forecast.p_over,forecast.p_under,forecast.p_push],forecast.projection_mean,
                 forecast.model_version,forecast.artifact_version,forecast.forecast_id,forecast.forecast_at,
                 '',forecast.grade,None)
    side = getattr(forecast, 'chosen_side', None)
    return replace(proj, side=side if side in ('over', 'under') else None, quote=sports_quote(consensus, forecast))
