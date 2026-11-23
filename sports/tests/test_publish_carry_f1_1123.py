"""F1 (sweep 2026-11-23): a soccer / tennis publish run cut by its time budget carries the previous publication only for
offers it did NOT reach. An offer the run reached and declined (canonical_event_not_scheduled = postponed / cancelled,
identity_or_start = identity lost, storage_full, archived_conflict) never gets its old letter back. Unreached offers keep
the sg-stability carry; a run that is not cut is unchanged. carry_declined counts what the old rule would have shown."""
import json

from sports import expansion_board as board, expansion_forecasts as fc, expansion_research as research
from sports.tests.test_expansion_forecasts import fixture


def _three(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    offer, facts = fixture()
    a = dict(offer, offer_id='A', start='2026-11-18T18:00:00Z')
    k = dict(offer, offer_id='K', start='2026-11-18T19:00:00Z')
    l_ = dict(offer, offer_id='L', start='2026-11-18T20:00:00Z')
    monkeypatch.setattr(board, 'board', lambda req: (200, {'offers': [a, k, l_]}))
    monkeypatch.setattr(research, 'saved', lambda sport: {'players': {research.key(o): facts for o in (a, k, l_)}})
    full = fc.publish('soccer', '2026-11-17T23:00:00Z')
    assert full['published'] == 3 and full['letters'] >= 1
    return a, k, l_


def _saved(tmp_path):
    return json.loads((tmp_path / 'soccer' / 'forecasts.json').read_text(encoding='utf-8'))['forecasts']


def _decline(monkeypatch, offer_id, reason):
    real = fc.make_forecast

    def make(offer, fact, now):
        if offer['offer_id'] == offer_id:
            return None, reason
        return real(offer, fact, now)
    monkeypatch.setattr(fc, 'make_forecast', make)


def _clock(monkeypatch, times):
    seq = iter(times)
    monkeypatch.setattr(fc.time, 'monotonic', lambda: next(seq))


def test_cut_run_never_republishes_an_offer_it_reached_and_declined(tmp_path, monkeypatch):
    a, k, l_ = _three(tmp_path, monkeypatch)
    before = _saved(tmp_path)
    ka, kk, kl = (fc.offer_key(o) for o in (a, k, l_))
    _decline(monkeypatch, 'K', 'canonical_event_not_scheduled')    # the match was postponed
    _clock(monkeypatch, [0, 0, 0, 100])        # deadline 5: A and K are processed, the check before L is past it
    out = fc.publish('soccer', '2026-11-17T23:01:00Z', budget_s=5)
    pub = _saved(tmp_path)
    assert kk not in pub                                            # reached + declined: no old letter
    assert pub[ka] == before[ka] and pub[kl] == before[kl]          # A republished as frozen; L (not reached) carried
    assert out['pending']['budget'] == 1 and out['pending']['carried'] == 1
    assert out['pending']['carry_declined'] == 1 and out['pending']['canonical_event_not_scheduled'] == 1
    assert out['published'] == 2


def test_cut_run_still_carries_every_offer_it_did_not_reach(tmp_path, monkeypatch):
    a, k, l_ = _three(tmp_path, monkeypatch)
    before = _saved(tmp_path)
    _clock(monkeypatch, [0, 0, 100])           # only A is processed: K and L are unreached and keep their publication
    out = fc.publish('soccer', '2026-11-17T23:01:00Z', budget_s=5)
    assert _saved(tmp_path) == before
    assert out['pending']['carried'] == 2 and 'carry_declined' not in out['pending']


def test_repro_carry_declined_first_offer(tmp_path, monkeypatch):
    """The sweep repro (code-audit/repro_carry.py): the first offer is declined, the cut comes right after it."""
    a, k, l_ = _three(tmp_path, monkeypatch)
    before = _saved(tmp_path)
    _decline(monkeypatch, 'A', 'identity_or_start')                 # identity lost
    _clock(monkeypatch, [0, 0, 100])
    out = fc.publish('soccer', '2026-11-17T23:01:00Z', budget_s=5)
    pub = _saved(tmp_path)
    assert fc.offer_key(a) not in pub
    assert {fc.offer_key(k), fc.offer_key(l_)} == set(pub) and all(pub[x] == before[x] for x in pub)
    assert out['pending']['carried'] == 2 and out['pending']['carry_declined'] == 1


def test_run_not_cut_is_unchanged(tmp_path, monkeypatch):
    a, k, l_ = _three(tmp_path, monkeypatch)
    before = _saved(tmp_path)
    _decline(monkeypatch, 'K', 'canonical_event_not_scheduled')
    out = fc.publish('soccer', '2026-11-17T23:01:00Z')
    pub = _saved(tmp_path)
    assert fc.offer_key(k) not in pub and {x: before[x] for x in pub} == pub and len(pub) == 2
    assert 'budget' not in out['pending'] and 'carried' not in out['pending'] and 'carry_declined' not in out['pending']


def test_declined_offer_is_not_brought_back_by_the_off_board_carry(tmp_path, monkeypatch):
    """carry_off_board (ST-2) only carries keys that are off the board; a reached offer is on it."""
    a, k, l_ = _three(tmp_path, monkeypatch)
    _decline(monkeypatch, 'K', 'storage_full')
    _clock(monkeypatch, [0, 0, 0, 0])          # never cut
    fc.publish('soccer', '2026-11-17T23:01:00Z', budget_s=5)
    assert fc.offer_key(k) not in _saved(tmp_path)
