"""Thin adapters over immutable projection facts. No sport model is imported.

The callers supply records at their forecast-write boundary, before display
calibration. Missing identities fail closed; display probabilities are never a
fallback for missing raw probabilities.
"""
import json
from dataclasses import dataclass
from .contracts import Offer, Prediction


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

    def offers(self, as_of): return [self.offer] if self.forecast_at <= as_of else []
    def _check(self, offer):
        if offer != self.offer: raise ValueError('projection belongs to another offer')
    def predict(self, offer): self._check(offer); return self.prediction
    def market(self, offer): self._check(offer); return None
    def flags(self, offer): self._check(offer); return self.facts
    def settle(self, offer): self._check(offer); return None
    def match_key(self, offer): self._check(offer); return self.sport + ':' + offer.event_id
    def team_key(self, offer): self._check(offer); return self.team
    def correlation(self, a, b):
        return 0.0 if (a.sport, a.event_id) != (b.sport, b.event_id) else None


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
    offer = Offer(sport,sport,str(event),str(player),stat,str(scope),line,book,variant or 'standard',start)
    return SavedProjection(sport,offer,prediction,str(fid),at,str(team or ''),old_letter,old_p)


def cs2(row, extra, forecast_id):
    raw = extra.get('probs_raw')
    if isinstance(raw,str): raw=json.loads(raw)
    if not isinstance(raw,dict): raise ValueError('raw CS2 distribution missing')
    return _make('cs2',row.get('match_slug'),row.get('slug'),row.get('prop'),row.get('scope'),
                 row['line'],row.get('book'),row.get('line_type'),row.get('match_start'),
                 [raw[k] for k in ('over','under','push')],row.get('lambda'),row.get('model_version'),
                 row.get('model_version'),forecast_id,row['logged_at'],row.get('team'),row.get('grade'),row.get('p_pick'),True)


def lol(row, forecast_id, forecast_at):
    return _make('lol',row.get('sid'),row.get('pid'),row.get('stat'),row.get('scope'),row['line'],
                 row.get('platform'),row.get('odds_type'),row.get('series_start'),
                 [row[k] for k in ('p_over','p_under','p_push')],row.get('lambda'),row.get('model_version'),
                 row.get('fit_sha'),forecast_id,forecast_at,row.get('tid'),row.get('grade_label'),
                 row.get('cal_p_over') if row.get('model_side')=='over' else row.get('cal_p_under'),True)


def sports(forecast, offer, start):
    if forecast.league not in ('nba','nfl') or forecast.model_version != 'sm-1':
        raise ValueError('only primary NBA/NFL raw model forecasts enter this candidate')
    if (forecast.league,forecast.event_id,forecast.player_id,forecast.stat,forecast.scope,forecast.line) != (
            offer.league,offer.event_id,offer.player_id,offer.stat,offer.scope,offer.line):
        raise ValueError('forecast/offer identity mismatch')
    return _make(forecast.league,forecast.event_id,forecast.player_id,forecast.stat,forecast.scope,forecast.line,
                 offer.platform,offer.variant,start,
                 [forecast.p_over,forecast.p_under,forecast.p_push],forecast.projection_mean,
                 forecast.model_version,forecast.artifact_version,forecast.forecast_id,forecast.forecast_at,
                 '',forecast.grade,None)
