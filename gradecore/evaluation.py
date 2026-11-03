"""Chronological calibration experiment. Never modifies a production artifact.

Rows are immutable forecast-side observations. Group splits use complete events;
only outcomes known before the next split may be fitted. A failed experiment
still produces a report, never permission to publish new grades.
"""
from datetime import datetime, timezone
import hashlib
import json
import math

from . import blend, calibrate, scorecard


def timestamp(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('timestamps must include timezone')
    return d.astimezone(timezone.utc)


def validate(rows):
    seen, clean, versions = set(), [], {}
    for r in rows:
        key = (r['forecast_id'], r['side'])
        if key in seen:
            raise ValueError('duplicate forecast side')
        seen.add(key)
        if r['sport'] not in ('cs2', 'lol', 'nba', 'nfl') or r['side'] not in ('over', 'under'):
            raise ValueError('unsupported sport or side')
        if r['status'] not in ('win', 'loss', 'push', 'void'):
            raise ValueError('only settled observations may be evaluated')
        if not r['match_key'] or not r['market']:
            raise ValueError('event and market identities required')
        cohort = (r['model_version'], r['probability_stage'], r['artifact_version'])
        if not all(isinstance(x, str) and x.strip() for x in cohort):
            raise ValueError('model version and probability stage required')
        if r['sport'] in versions and versions[r['sport']] != cohort:
            raise ValueError('select one model version, fitted artifact and probability stage per sport')
        versions[r['sport']] = cohort
        for name in ('p', 'market_p'):
            p = r.get(name)
            if p is None and name == 'market_p':
                continue
            if isinstance(p, bool) or not isinstance(p, (float, int)) or not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError('invalid probability')
        made, start, settled = map(timestamp, (r['forecast_at'], r['start_at'], r['settled_at']))
        if not made < start <= settled:
            raise ValueError('forecast must precede event and settlement')
        if r.get('market_p') is not None and timestamp(r['market_at']) > made:
            raise ValueError('market quote comes after forecast')
        clean.append(dict(r))
    return clean


def partition(rows, fit_end, calibration_end):
    """Purge boundary-spanning events, not individual correlated legs."""
    a, b = timestamp(fit_end), timestamp(calibration_end)
    if a >= b:
        raise ValueError('split boundaries must increase')
    groups = {}
    for r in validate(rows):
        groups.setdefault((r['sport'], r['match_key']), []).append(r)
    parts = {'fit': [], 'calibration': [], 'test': [], 'purged': []}
    for group in groups.values():
        first = min(timestamp(r['forecast_at']) for r in group)
        last = max(timestamp(r['settled_at']) for r in group)
        name = ('fit' if last < a else 'calibration' if first >= a and last < b
                else 'test' if first >= b else 'purged')
        parts[name].extend(group)
    return parts


def evaluate(rows, fit_end, calibration_end, min_test=500, reps=2000):
    rows = list(rows)
    parts = partition(rows, fit_end, calibration_end)
    weights, docs, comparisons = {}, {}, {}
    for sport in ('cs2', 'lol', 'nba', 'nfl'):
        fit_rows = [r for r in parts['fit'] if r['sport'] == sport and r['status'] in ('win', 'loss')]
        for market in sorted({r['market'] for r in fit_rows}):
            # Fit one canonical side per forecast: mirrored over/under rows must
            # not double the apparent sample size or flip the chosen-side market.
            rs = [r for r in fit_rows if r['market'] == market and r['side'] == 'over' and r.get('market_p') is not None]
            w = blend.fit_weight([r['p'] for r in rs], [r['market_p'] for r in rs],
                                 [r['status'] == 'win' for r in rs])
            if w is not None:
                weights[sport + '|' + market] = w
        def probability(r):
            # Width adjustments must match fitting. This experiment uses no
            # width shift; later width-aware experiments need separate fitting.
            return blend.blend(r['p'], r.get('market_p'), blend.weight(weights, sport, r['market']))
        cal = [{**r, 'p': probability(r)} for r in parts['calibration'] if r['sport'] == sport]
        doc = calibrate.build(cal, sport, fit_date=calibration_end)
        doc['blend_weights'] = {k: v for k, v in weights.items() if k.startswith(sport + '|')}
        doc['width_adjustment'] = False
        docs[sport] = doc
        source_rows = [r for r in rows if r['sport'] == sport]
        doc['model_version'] = source_rows[0]['model_version'] if source_rows else None
        doc['probability_stage'] = source_rows[0]['probability_stage'] if source_rows else None
        doc['artifact_version'] = source_rows[0]['artifact_version'] if source_rows else None
        old = [r for r in parts['test'] if r['sport'] == sport]
        new = [{**r, 'p': calibrate.apply(calibrate.lookup(doc, r['market'], r['side']), probability(r))} for r in old]
        ds = [(o, n) for o, n in zip(old, new) if o['status'] in ('win', 'loss')]
        diffs = [scorecard.log_loss([n['p']], [o['status'] == 'win']) -
                 scorecard.log_loss([o['p']], [o['status'] == 'win']) for o, n in ds]
        mean, lo, hi = scorecard.cluster_ci(diffs, [sport + ':' + o['match_key'] for o, _ in ds], reps=reps)
        events = len({o['match_key'] for o, _ in ds})
        comparisons[sport] = {'old': scorecard.score(old, reps=reps), 'candidate': scorecard.score(new, reps=reps),
                              'log_loss_delta': mean, 'delta_ci': [lo, hi], 'events': events,
                              'unique_forecasts': len({o['forecast_id'] for o, _ in ds}),
                              'historical_gate_pass': len({o['forecast_id'] for o, _ in ds}) >= max(500, min_test) and events >= 2 and hi is not None and hi < 0,
                              'release_allowed': False, 'reason': 'Prospective comparison and release review still required'}
    digest = hashlib.sha256(json.dumps(sorted(validate(rows), key=lambda r: (r['forecast_id'], r['side'])),
                                      sort_keys=True).encode()).hexdigest()
    return {'schema': 'grade-evaluation-1', 'data_hash': digest, 'fit_end': fit_end,
            'calibration_end': calibration_end, 'counts': {k: len(v) for k, v in parts.items()},
            'artifacts': docs, 'comparison': comparisons, 'release_allowed': False}
