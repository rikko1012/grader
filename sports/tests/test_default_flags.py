"""Owner 2026-11-05: sports display switches on unless Render turns them off."""
from sports import service


def test_service_defaults_and_render_off_wins():
    env = {"SPORTS_MARKET_FALLBACK": "0"}
    service.apply_default_flags(env)
    assert env["SPORTS_MARKET_FALLBACK"] == "0" and env["SPORTS_CONTEXT_RECONCILE"] == "1" and env["SCOUT_BOARD_H2H"] == "1"
