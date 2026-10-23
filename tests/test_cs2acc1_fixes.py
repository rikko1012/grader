"""2026-10-23 CS2 accuracy audit bug fixes (fix/cs2acc-1): verified player aliases (I3), verified team aliases (I5 / D1) and
the perf-2-bo3 opponent-history window on the match day. Fixtures only - no database, no network."""
import os
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cs2_scout  # noqa: E402
import hltv  # noqa: E402
import matchid  # noqa: E402
import perf  # noqa: E402
import perf_live  # noqa: E402


# ---------------------------------------------------------------- player aliases (I3)
def test_player_aliases_are_exact_and_exclude_dev1ce():
    assert hltv.player_nicks("Techno4K") == ["techno4k", "techno"]
    assert hltv.player_nicks("brain") == ["brain", "brain47"]
    assert hltv.player_nicks("Brain47") == ["brain47"]
    assert hltv.player_nicks("dev1ce") == ["dev1ce"]                 # owner decision pending: not aliased
    assert hltv.player_nicks("techno4") == ["techno4"]               # never fuzzy


class _Cur:
    """Answers _player_ids' query from fixture rows {playerid: (nick, on_team, maps_12m, maps_all)}."""
    def __init__(self, players):
        self.players, self.params = players, None

    def execute(self, sql, params=None):
        self.params = params

    def fetchall(self):
        p = self.params
        return [(pid, on, m12, mall, nick == p["nick"]) for pid, (nick, on, m12, mall) in self.players.items()
                if nick in p["nicks"]]


def test_alias_reaches_the_hltv_player_who_played_for_the_team():
    cur = _Cur({5615: ("brain", False, 0, 6), 23294: ("brain47", True, 300, 504)})
    assert hltv._player_ids(cur, "brain", "Phantom Academy", team_id=13766) == [23294]
    assert cur.params["nicks"] == ["brain", "brain47"]
    cur = _Cur({20275: ("techno", True, 400, 2298)})
    assert hltv._player_ids(cur, "Techno4K", "The MongolZ", team_id=6248) == [20275]


def test_alias_candidate_off_the_team_never_wins():
    """A BO3.gg 'brain' on another team keeps today's lookup (HLTV 'brain'), never Brain47's history."""
    cur = _Cur({5615: ("brain", False, 0, 6), 23294: ("brain47", False, 300, 504)})
    assert hltv._player_ids(cur, "brain", "Someone Else", team_id=999) == [5615]
    cur = _Cur({20275: ("techno", False, 400, 2298)})
    assert hltv._player_ids(cur, "Techno4K", "Other Team", team_id=999) == []


def test_player_numbers_alias_needs_the_team(monkeypatch):
    d = date.today() - timedelta(days=5)
    rows = [(23294, "Brain47", "Phantom Academy", d, 1, s, 1.1, 0.0, 80.0, 70.0, 20, 15, "hltv") for s in ("total", "ct", "t")]

    class Cur:
        def execute(self, sql, params=None):
            self.params = params

        def fetchall(self):
            return [r for r in rows if hltv._norm(r[1]) in self.params["nicks"]]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Con:
        def cursor(self):
            return Cur()

        def close(self):
            pass
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    assert hltv._player_numbers("brain", "Phantom Academy", None).get("found") is not False
    assert hltv._player_numbers("brain", "Other Team", None)["found"] is False


# ---------------------------------------------------------------- team aliases (I5 / D1)
def test_verified_team_aliases_resolve_to_the_hltv_team(monkeypatch):
    monkeypatch.setattr(matchid, "ALIASES_PATH", matchid.ALIASES_PATH)        # the maintained file itself
    idx = matchid._build_index([(13976, "LFO 8"), (14084, "LFO 10"), (14055, "Fruju"), (13304, "LP"), (14107, "BARCA")], [])
    assert matchid._resolve(idx, "Orgless Aust") == ("team:13976", "alias")
    assert matchid._resolve(idx, "Sökerorg") == ("team:14084", "alias")
    assert matchid._resolve(idx, "Sokerorg") == ("team:14084", "alias")
    assert matchid._resolve(idx, "Martyrdom") == ("team:14055", "alias")
    assert matchid._resolve(idx, "MARTYRDOM") == ("team:14055", "alias")
    assert matchid._resolve(idx, "LP") == ("team:13304", "exact")            # LP -> BARCA rename not aliased (date-bound)
    assert matchid._resolve(idx, "Orgless")[0] is None                       # generic name: never an alias


# ---------------------------------------------------------------- perf-2-bo3 history window
def _bo3_window(monkeypatch, day):
    seen = {}
    monkeypatch.setattr(perf_live, "projection", lambda *a, **k: {"error": "player 'x' (T) not found in HLTV"})
    monkeypatch.setattr(cs2_scout, "bo3_perf_rows", lambda pb, rule=None: [(day - 3, 20, 24, 8, False, True)])

    def opp_maps(opp, since):
        seen["since"] = since
        return None, []
    monkeypatch.setattr(perf_live, "opp_maps", opp_maps)
    monkeypatch.setattr(perf_live, "g7_fields", lambda p: {})
    monkeypatch.setattr(perf, "project", lambda *a, **k: {"lambda": 30.0, "rate": 0.8, "data_quality": "ok"})
    args = SimpleNamespace(match_start=None, opponent="Opp", prop="kills")
    pb = {"player": {"nick": "x"}, "playing_for": "T"}
    out = cs2_scout._perf_input_once(pb, args, "12")
    assert out["source"].startswith("BO3.gg maps")
    return seen["since"]


def test_bo3_fallback_window_uses_the_match_day_in_the_past(monkeypatch):
    day = date(2026, 10, 12).toordinal()
    monkeypatch.setattr(cs2_scout, "match_day", lambda start: day)
    assert _bo3_window(monkeypatch, day) == (date(2026, 10, 12) - timedelta(days=420)).isoformat()


def test_bo3_fallback_window_matches_perf_live_for_a_future_match_day(monkeypatch):
    day = (datetime.now(timezone.utc).date() + timedelta(days=2)).toordinal()
    monkeypatch.setattr(cs2_scout, "match_day", lambda start: day)
    assert _bo3_window(monkeypatch, day) == (date.fromordinal(day) - timedelta(days=420)).isoformat()
