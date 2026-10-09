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
    """The event's verified pool is preferred; otherwise an inferred pool (recent play) filters maps that left it."""
    import cs2_scout
    monkeypatch.setattr(hltv, "active_map_pool", lambda *a, **k: {"ancient", "anubis", "cache", "dust2", "inferno", "mirage", "nuke"})
    prof = lambda maps: {"n": 10, "picks": {m: 2 for m in maps}, "bans": {}, "pool": set(maps)}
    old = ["de_ancient", "de_anubis", "de_dust2", "de_inferno", "de_mirage", "de_nuke", "de_overpass", "de_cache", "de_train"]
    monkeypatch.setattr(cs2_scout, "veto_profile", lambda b, t, before=None: prof(old))
    out = cs2_scout.predict_maps(None, 1, 2)
    assert out and not ({"de_overpass", "de_train"} & (set(out["map1"]) | set(out["map2"])))
    assert out["pool_source"].startswith("inferred")                      # the fallback is labelled as inferred
    ev = cs2_scout.predict_maps(None, 1, 2, pool_keys=["ancient", "anubis", "cache", "dust2", "inferno", "mirage", "overpass"])
    assert ev["pool_source"] == "event veto" and "de_nuke" not in set(ev["map1"]) | set(ev["map2"])   # the event's own pool wins
    assert hltv.map_key("de_dust2") == hltv.map_key("Dust 2") == "dust2"


def test_event_pool_uses_this_events_latest_full_veto():
    import cs2_scout
    seven = lambda maps: {"bo_type": 3, "match_maps": [{"map_name": m} for m in maps]}
    new = ["de_ancient", "de_anubis", "de_cache", "de_dust2", "de_inferno", "de_mirage", "de_nuke"]
    old = ["de_ancient", "de_anubis", "de_overpass", "de_dust2", "de_inferno", "de_mirage", "de_nuke"]
    class B:
        def team_recent(self, tid, limit=30):
            return {"results": [
                {"slug": "a", "status": "finished", "start_date": "2026-10-05", "tournament": {"slug": "ev"}},
                {"slug": "b", "status": "finished", "start_date": "2026-10-08", "tournament": {"slug": "ev"}},
                {"slug": "c", "status": "finished", "start_date": "2026-10-09", "tournament": {"slug": "other"}},
                {"slug": "d", "status": "upcoming", "start_date": "2026-10-10", "tournament": {"slug": "ev"}}]}
        def match_veto(self, slug):
            return {"a": seven(old), "b": seven(new), "c": seven(old), "d": {"bo_type": 3, "match_maps": []}}[slug]
    out = cs2_scout.event_pool(B(), {"event_slug": "ev", "team1_id": 1, "team2_id": 2})
    assert out["from_match"] == "b" and "cache" in out["keys"] and "overpass" not in out["keys"]
    assert cs2_scout.event_pool(B(), {"event_slug": "none-yet", "team1_id": 1}) is None


def test_search_order_and_typos(monkeypatch):
    import time
    rows = [{"nick": n, "team": "T", "recent": 0, "maps": 1, "key": hltv._norm(n), "loose": hltv._loose(hltv._norm(n))}
            for n in ("ZywOo", "zylayw", "s1mple", "m0NESY", "xyzywo")]
    monkeypatch.setattr(hltv, "dsn", lambda: "x"); monkeypatch.setattr(hltv, "_INDEX", {"at": time.time(), "rows": rows, "busy": False})
    assert [r["nick"] for r in hltv.search_players("zy", 8)] == ["ZywOo", "zylayw", "xyzywo"]   # prefix matches first, then contains
    assert [r["nick"] for r in hltv.search_players("simple", 8)] == ["s1mple"]                  # typo: 'simple' = 's1mple'
    assert [r["nick"] for r in hltv.search_players("monesy", 8)] == ["m0NESY"]
