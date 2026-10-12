"""Settlement by canonical identity (owner review 2026-10-12): the opponent's HLTV team id first, exact names only as a
fallback, a rematch inside the date window never settled by guess."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hltv

IDS = {"vitality": 9565, "vitalityacademy": 13001, "ninjasinpyjamas": 4411, "renamedteam": 7777}


class Cur:
    def __init__(self): self.last = None
    def execute(self, q, args=None): self.last = args
    def fetchone(self):
        v = self.last[0] if self.last else None
        return (IDS[v],) if v in IDS else None


def rows(*spec):
    return [{"hltv_match": m, "opponent": name, "opponent_id": oid, "number": n, "date": d}
            for m, name, oid, d in spec for n in (1, 2)]


def test_academy_never_settles_the_main_team(monkeypatch):
    r = rows((1, "Vitality Academy", 13001, "2026-10-10"))
    on, how = hltv.opponent_rows(Cur(), r, "Vitality")
    assert on == [] and how == "team id"
    on, _ = hltv.opponent_rows(Cur(), rows((2, "Vitality", 9565, "2026-10-10")), "Vitality Academy")
    assert on == []


def test_alias_spelling_resolves_to_the_same_id(monkeypatch):
    import matchid
    monkeypatch.setattr(matchid, "ready", lambda: True)
    monkeypatch.setattr(matchid, "team_key", lambda n: "team:4411" if hltv._norm(n) in ("nip", "ninjasinpyjamas") else None)
    on, how = hltv.opponent_rows(Cur(), rows((3, "Ninjas in Pyjamas", 4411, "2026-10-10")), "NIP")
    assert len(on) == 2 and how == "team id"


def test_renamed_team_keeps_its_id(monkeypatch):
    # HLTV renamed the team (stored name differs) - the id still matches the pick's team
    on, how = hltv.opponent_rows(Cur(), rows((4, "New Org Name", 7777, "2026-10-10")), "Renamed Team")
    assert len(on) == 2 and how == "team id"


def test_reversed_order_and_variants_fallback_when_no_id(monkeypatch):
    import matchid
    monkeypatch.setattr(matchid, "ready", lambda: False)
    on, how = hltv.opponent_rows(Cur(), rows((5, "Chicken Coop", None, "2026-10-10")), "Chicken Coop Esports")
    assert len(on) == 2 and how == "name variants"
    on, _ = hltv.opponent_rows(Cur(), rows((6, "Chicken Coop Academy", None, "2026-10-10")), "Chicken Coop")
    assert on == []                                              # never a substring


def test_rematch_in_the_window_is_not_settled(monkeypatch):
    r = rows((7, "Vitality", 9565, "2026-10-10"), (8, "Vitality", 9565, "2026-10-11"))
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://x")
    class Con:
        def cursor(self):
            class C(Cur):
                def __enter__(s): return s
                def __exit__(s, *a): pass
            return C()
        def close(self): pass
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, n, t: [1])
    monkeypatch.setattr(hltv, "_series_rows", lambda *a, **k: r)
    out = hltv.settle_by_day("x", "Team", "2026-10-10", "Vitality")
    assert out == {"found": False, "ambiguous": True}


def test_picklog_scope_result_uses_identity(monkeypatch):
    import inspect, picklog
    assert "hltv.opponent_rows(cur, rows, opp)" in inspect.getsource(picklog.scope_result)
