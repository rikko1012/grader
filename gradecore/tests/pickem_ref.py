"""The PrizePicks help-center table in gradecore/pickem_payouts.json is VERIFIED by owner sign-off 2026-11-05 (it superseded the
2026-11-04 "unknown payouts stay unknown" rule for this table), so payouts.pickem_config() and payouts.CONFIG carry it live.
reference_tables lays every file table over CONFIG regardless of its verified mark, so the formula tests keep running on the
same numbers whatever the file says."""
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
