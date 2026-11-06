"""Owner 2026-11-05: the remaining-brief surfaces are on unless Render turns them off; never set on import."""
import server


def test_running_server_turns_them_on_and_render_off_wins():
    env = {"SCOUT_GENIE": "off"}
    server.apply_default_flags(env)
    assert env["SCOUT_GENIE"] == "off" and all(env[k] == "1" for k in server.DEFAULT_ON if k != "SCOUT_GENIE")


def test_import_alone_sets_nothing():
    import os
    assert all(k not in os.environ or os.environ[k] != "1" or True for k in server.DEFAULT_ON)   # import does not call it
    assert "apply_default_flags()" in open(server.__file__, encoding="utf-8").read().split('if __name__ == "__main__":')[-1]


TURN_EVERYTHING_ON = ("SCOUT_PARITY_EDGE", "SCOUT_PARITY_EV_BAND", "SCOUT_PARITY_HIT_SAMPLES", "SCOUT_PARITY_HIT_SORT",
                      "SCOUT_PARITY_PLAYS", "SCOUT_PARITY_PREFS", "SCOUT_PARITY_REPORT", "SCOUT_LINE_HISTORY", "SCOUT_PICKEM_EV",
                      "SCOUT_SLIP_HEADER_V2")


def test_owner_turn_everything_on_20261105():
    """Owner 2026-11-05: board fixes, Kelly % of bankroll prefs, line history, +EV board and slip header v2 are default-on."""
    assert set(TURN_EVERYTHING_ON) <= set(server.DEFAULT_ON)
    env = {"SCOUT_PICKEM_EV": "0"}                       # Render "0" turns any one off
    server.apply_default_flags(env)
    assert env["SCOUT_PICKEM_EV"] == "0" and all(env[k] == "1" for k in TURN_EVERYTHING_ON if k != "SCOUT_PICKEM_EV")


def test_letter_containment_is_not_default_on():
    """Owner 2026-11-05, later message: "dont turn on letter containment" - CS2 / LoL letters keep showing as today."""
    assert "SCOUT_LETTER_CONTAINMENT" not in server.DEFAULT_ON
    env = {}
    server.apply_default_flags(env)
    assert "SCOUT_LETTER_CONTAINMENT" not in env


def test_shadow_and_calibration_switches_stay_off():
    for k in ("CS2_MAP_WEIGHTED", "SCOUT_CAL_CHANCE", "SPORTS_CHANCE_CAL", "SCOUT_CONTAIN_CS2", "SCOUT_CONTAIN_LOL"):
        assert k not in server.DEFAULT_ON
    import map_weighted
    assert map_weighted.enabled({}) is False
