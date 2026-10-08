"""HLTV opponent lookup: Underdog's team names reach HLTV's (exact matches only, never fuzzy)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hltv


def test_team_name_variants():
    assert hltv.team_name_variants("Team Vitality") == ["teamvitality", "vitality"]
    assert hltv.team_name_variants("Chicken Coop Esports") == ["chickencoopesports", "chickencoop"]
    assert hltv.team_name_variants("Aurora Gaming") == ["auroragaming", "aurora"]
    assert hltv.team_name_variants("Spirit Academy") == ["spiritacademy"]          # a different team stays different
    assert hltv.team_name_variants("Falcons") == ["falcons"]
    assert hltv.team_name_variants("") == []


class _Cur:
    def __init__(self, names):
        self.names, self.row = names, None

    def execute(self, sql, args):
        self.row = (self.names[args[0]],) if args[0] in self.names else None

    def fetchone(self):
        return self.row


def test_team_id_prefers_exact_then_variant():
    cur = _Cur({"vitality": 9565, "teamvitality": 1})
    assert hltv._team_id(cur, "Team Vitality") == 1                                # exact name wins
    assert hltv._team_id(_Cur({"vitality": 9565}), "Team Vitality") == 9565
    assert hltv._team_id(_Cur({"vitality": 9565}), "Vitality Academy") is None     # no fuzzy matching
