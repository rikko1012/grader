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


def test_active_map_pool_filters_retired_maps(monkeypatch):
    """Maps that left the pool (Overpass in July 2026, Train earlier) are never predicted live."""
    import cs2_scout
    monkeypatch.setattr(hltv, "active_map_pool", lambda *a, **k: {"ancient", "anubis", "cache", "dust2", "inferno", "mirage", "nuke"})
    prof = lambda maps: {"n": 10, "picks": {m: 2 for m in maps}, "bans": {}, "pool": set(maps)}
    old = ["de_ancient", "de_anubis", "de_dust2", "de_inferno", "de_mirage", "de_nuke", "de_overpass", "de_cache", "de_train"]
    monkeypatch.setattr(cs2_scout, "veto_profile", lambda b, t, before=None: prof(old))
    out = cs2_scout.predict_maps(None, 1, 2)
    assert out and not ({"de_overpass", "de_train"} & (set(out["map1"]) | set(out["map2"])))
    assert hltv.map_key("de_dust2") == hltv.map_key("Dust 2") == "dust2"
