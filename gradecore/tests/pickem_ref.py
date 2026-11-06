"""FORMULA TESTS ONLY. The PrizePicks table in gradecore/pickem_payouts.json is the help-center REFERENCE, marked
verified: false (owner rule 2026-11-04: unknown payouts stay unknown), so payouts.pickem_config() leaves it out and every
live surface shows its break-even and EV as unavailable. The arithmetic (closed forms, flex solving, Monte Carlo) is still
checked against those reference numbers, laid over CONFIG here and patched in only inside a test."""
import pytest

from gradecore import payouts

REAL_PICKEM_CONFIG = payouts.pickem_config


def reference_config():
    return payouts.with_tables(payouts._pickem_raw())


@pytest.fixture
def reference_tables(monkeypatch):
    cfg = reference_config()
    monkeypatch.setattr(payouts, "pickem_config", lambda: cfg)
    return cfg
