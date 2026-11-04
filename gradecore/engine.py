"""One shared post-projection path; shadow output cannot authorize publication."""
from dataclasses import asdict

from . import blend, calibrate, gates, policy
from .VERSION import GRADE_POLICY_VERSION


def assess(adapter, offer, artifact=None):
    if adapter.sport != offer.sport:
        raise ValueError('adapter and offer sport differ')
    artifact = artifact or {}
    if artifact.get('sport', offer.sport) != offer.sport:
        raise ValueError('calibration artifact belongs to another sport')
    prediction = adapter.predict(offer)
    if artifact.get('model_version') and artifact['model_version'] != prediction.versions.get('model_version'):
        raise ValueError('calibration does not match model version')
    if artifact.get('probability_stage') and artifact['probability_stage'] != prediction.versions.get('probability_stage'):
        raise ValueError('calibration does not match probability stage')
    if artifact.get('artifact_version') and artifact['artifact_version'] != prediction.versions.get('artifact_version'):
        raise ValueError('calibration does not match frozen model artifact')
    market = adapter.market(offer)
    facts = set(adapter.flags(offer))
    # The new path is shadow-only, even if an input accidentally omits a gate.
    facts.add(gates.UNVALIDATED)
    out = {'policy': GRADE_POLICY_VERSION, 'sport': offer.sport,
           'event_id': offer.event_id, 'raw': asdict(prediction), 'sides': {},
           # the saved market quote the shadow used (None = graded on the model alone); shown in the admin comparison
           'market': None if market is None else {'p_over': market.p_over, 'p_under': market.p_under,
                                                  'source': market.source, 'width': market.width, 'age_s': market.age_s},
           'release_allowed': False}
    weight = blend.weight(artifact.get('blend_weights'), offer.sport, offer.stat)
    for side in ('over', 'under'):
        raw = getattr(prediction, 'p_' + side + '_given_no_push')
        if raw is None:
            out['sides'][side] = {'p': None, 'grade': None, 'reason': 'No decided outcome mass'}
            continue
        market_p = getattr(market, 'p_' + side) if market else None
        mixed = blend.blend(raw, market_p, weight,
                            market.width if market and artifact.get('width_adjustment') else None)
        art = calibrate.lookup(artifact, offer.stat, side)
        chance = calibrate.apply(art, mixed)
        g = policy.grade(chance, offer.book, variant=offer.variant, flags=facts)
        out['sides'][side] = {'raw': raw, 'market': market_p, 'blend_weight': weight if market else 1,
                              'blended': mixed, 'calibration': art.get('kind', 'identity'),
                              'p': chance, 'grade': asdict(g)}
    return out
