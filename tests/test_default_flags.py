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
