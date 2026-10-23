"""2026-10-23 platform rules (platform_rules.py; evidence in research/cs2_accuracy_2y/PLATFORM_RULES.md): forfeits / DNP
per platform in settlement, entry eligibility for slips, the same-team refund. Offline: every HLTV answer below is a
labelled TEST FIXTURE (the forfeit shapes are the ones HLTV's database shows, e.g. matches 2399091 / 2399081 / 2397986)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import hltv
import picklog
import platform_rules as pr
import slip_batches


# ------------------------------------------------------------------------------------------------ pure rules
def test_forfeited_map_numbers_from_hltv_rows():
    assert pr.forfeited_numbers(1, [1, 2], 3) == {3}           # 2-1, maps 1-2 played: map 3 was the forfeit (2399091)
    assert pr.forfeited_numbers(1, [2, 3], 3) == {1}           # map 1 forfeited, slot kept (2399081)
    assert pr.forfeited_numbers(1, [1, 3]) == {2}
    assert pr.forfeited_numbers(2, [1], 3) == {2, 3}           # 2397986
    assert pr.forfeited_numbers(2, [3], 3) == {1, 2}           # 2399485
    assert pr.forfeited_numbers(1, [1], 2) == {2}              # 2-0 with map 2 forfeited (2399904)
    assert pr.forfeited_numbers(0, [1, 2]) == set()
    assert pr.forfeited_numbers(1, [1, 2], 4) is None          # score disagrees with the rows: unknown, never guessed
    assert pr.forfeited_numbers(1, [3]) is None                # two holes, one forfeit: rows missing


def test_forfeit_decision_per_platform():
    # Underdog: only a forfeit during the line's maps voids
    assert pr.forfeit_decision("underdog", "12", {3})["action"] == "grade"
    assert pr.forfeit_decision("underdog", "12", {2})["action"] == "void"
    assert pr.forfeit_decision("underdog", "123", {3})["action"] == "void"
    assert pr.forfeit_decision("underdog", "1", {2})["action"] == "grade"
    assert pr.forfeit_decision("underdog", "12", None)["action"] == "pending"
    # PrizePicks: no published CS2 forfeit rule -> pending; nothing played at all -> DNP (void)
    for f in ({3}, {2}, None):
        d = pr.forfeit_decision("prizepicks", "12", f)
        assert d["action"] == "pending" and d["verified"] is False
    assert pr.forfeit_decision("prizepicks", "12", {1, 2}, any_played=False)["action"] == "void"
    # no platform on the line: the standing rule (any forfeit voids)
    assert pr.forfeit_decision(None, "12", {3})["action"] == "void"
    assert pr.forfeit_decision("thunderpick", "12", {3})["rule"] == "legacy_any_forfeit"


def test_dnp_decision_per_platform():
    assert pr.dnp_decision("underdog", "12", {1, 2}, {1, 2}) is None
    assert pr.dnp_decision("underdog", "12", {1}, {1})                is None   # map 2 not scraped yet: no DNP
    assert pr.dnp_decision("underdog", "12", {1}, {1, 2})["action"] == "void"
    assert pr.dnp_decision("prizepicks", "12", {1}, {1, 2})["action"] == "pending"
    assert pr.dnp_decision("prizepicks", "12", set(), {1, 2})["action"] == "void"
    assert pr.dnp_decision(None, "12", {1}, {1, 2}) is None


def _l(team, nick, side="More", book=None):
    return {"team": team, "nick": nick, "side": side, "prop": "kills", **({"book": book} if book else {})}


def test_entry_one_team_refused_on_both_platforms():
    legs = [_l("Vitality", "ZywOo"), _l("Vitality", "apEX")]
    for p, name in (("prizepicks", "PrizePicks"), ("underdog", "Underdog")):
        c = pr.entry_check(legs, p)
        assert c["eligible"] is False and c["status"] == "refused"
        assert "one team (Vitality)" in c["refusals"][0] and name in c["refusals"][0]


def test_entry_two_teams_same_match_allowed():
    legs = [_l("Vitality", "ZywOo"), _l("FURIA", "KSCERATO", "Less")]
    assert pr.entry_check(legs, "underdog") == dict(pr.entry_check(legs, "underdog"), eligible=True, status="eligible")
    pp = pr.entry_check(legs, "prizepicks")             # published rules pass; the in-app CS2 chart is not public
    assert pp["eligible"] is None and pp["status"] == "unverified" and not pp["refusals"]


def test_entry_same_player_and_sizes():
    assert pr.entry_check([_l("A", "x"), _l("A", "x", "Less"), _l("B", "y")], "prizepicks")["eligible"] is False
    assert pr.entry_check([_l("A", "x"), _l("A", "x"), _l("B", "y")], "underdog")["eligible"] is False
    assert pr.entry_check([_l("A", "x"), _l("A", "x", "Less"), _l("B", "y")], "underdog")["eligible"] is None
    assert pr.entry_check([_l("A", "x"), _l("B", "y")], "underdog", "flex")["eligible"] is False      # flex: 3-8
    assert pr.entry_check([_l(str(i), str(i)) for i in range(7)], "prizepicks")["eligible"] is False  # max 6
    assert pr.entry_check([_l(str(i), str(i)) for i in range(8)], "underdog")["eligible"] is True     # max 8


def test_entry_unknown_inputs_never_block():
    c = pr.entry_check([_l(None, "x"), _l("B", "y")], "underdog")
    assert c["eligible"] is None and c["unverified"]
    c = pr.entry_check([_l("A", "x"), _l("B", "y")])                      # no platform known
    assert c["eligible"] is None and c["status"] == "unverified"
    c = pr.entry_check([_l("A", "x", book="underdog"), _l("B", "y", book="prizepicks")])
    assert c["eligible"] is False


def test_live_legs_valid():
    assert pr.live_legs_valid([_l("A", "x"), _l("B", "y")]) is True
    assert pr.live_legs_valid([_l("A", "x"), _l("A", "z")]) is False
    assert pr.live_legs_valid([_l("A", "x")]) is False
    assert pr.live_legs_valid([_l(None, "x"), _l("A", "z")]) is None


# ------------------------------------------------------------------------------------------------ settlement (picklog)
@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "CORRECTIONS", tmp_path / "corrections.jsonl")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: None)
    return tmp_path / "picks.db"


def _fake(monkeypatch, series, forfeits, map_state, score=None):
    """TEST FIXTURE HLTV: the player's rows, match_overview.forfeited_maps, match_maps x player_stats counts, score."""
    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, q, args=None): self.q = q
        def fetchone(self):
            if "forfeited_maps" in self.q:
                return (forfeits,)
            if "SELECT date FROM match_overview" in self.q:     # the match's UTC date (reschedule rule evidence)
                return ("2026-10-06",)
            if "score1" in self.q:
                return score
            return ("bo3",)
        def fetchall(self):
            return list(map_state) if "match_maps" in self.q else []

    class Con:
        def cursor(self): return Cur()
        def close(self): pass
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, n, t: [1])
    monkeypatch.setattr(picklog, "_team_confirmed", lambda cur, ids, team: True)
    monkeypatch.setattr(hltv, "_series_rows", lambda *a, **k: series)


def _row(n, k, won=True):
    return {"match_id": "hltv-7", "hltv_match": 7, "date": "2026-10-06", "number": n, "map": f"de_m{n}", "kills": k,
            "headshots": 5, "rounds": 24, "won": won, "opponent": "1win", "team": "Spirit", "source": "hltv"}


def _pick(book, scope="12", slug="spirit-vs-1win-06-10-2026"):
    return {"id": 1, "player": "tN1R", "team": "Spirit", "opponent": "1win", "match_start": "2026-10-06T12:00:00Z",
            "prop": "kills", "scope": scope, "line": 30.5, "pick": "More", "book": book, "match_slug": slug, "result": None}


MAP3_FORFEIT = dict(series=[_row(1, 18), _row(2, 15, False)], forfeits=1, map_state=[(1, 10), (2, 10)], score=(2, 1))


def test_underdog_map3_forfeit_grades_maps12(monkeypatch):
    _fake(monkeypatch, **MAP3_FORFEIT)
    res = picklog.scope_result(_pick("underdog"))
    assert res["status"] == "ok" and res["total"] == 33          # before 2026-10-23: void (any forfeit in the match)
    assert picklog._hltv_totals(_pick("underdog"))[0] == "settled"


def test_underdog_forfeit_inside_scope_voids(monkeypatch):
    _fake(monkeypatch, series=[_row(1, 18), _row(3, 15)], forfeits=1, map_state=[(1, 10), (3, 10)], score=(2, 1))
    res = picklog.scope_result(_pick("underdog"))
    assert res["status"] == "void" and res["forfeited_maps"] == [2]


def test_no_platform_keeps_any_forfeit_void(monkeypatch):
    _fake(monkeypatch, **MAP3_FORFEIT)
    assert picklog.scope_result(_pick(None)) == dict(picklog.scope_result(_pick(None)), status="void",
                                                     reason="1 forfeited map(s)")


def test_prizepicks_forfeit_stays_pending_never_voided(monkeypatch, db):
    _fake(monkeypatch, **MAP3_FORFEIT)
    res = picklog.scope_result(_pick("prizepicks"))
    assert res["status"] == "pending" and res["platform"] == "prizepicks"
    late = datetime(2026, 10, 6, 12, tzinfo=timezone.utc) + timedelta(days=picklog.VOID_AFTER_D + 2)
    start = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    assert picklog._settle_scope(_pick("prizepicks", scope="123"), late, start) is None   # was a void
    assert picklog._hltv_totals(_pick("prizepicks")) == "pending"

    class NoBo3:                                                  # the BO3.gg fallback must not run either
        def __getattr__(self, k):
            raise AssertionError("BO3.gg fallback used for a pending pick")
    con = picklog._db()
    con.execute("INSERT INTO picks (id, key, player, prop, line, pick, status, match_slug, match_start, scope, book, team, "
                "opponent) VALUES (1, 'k', 'tN1R', 'kills', 30.5, 'More', 'open', 'spirit-vs-1win-06-10-2026', "
                "'2026-10-06T12:00:00Z', '12', 'prizepicks', 'Spirit', '1win')")
    con.commit()
    r = con.execute("SELECT * FROM picks WHERE id = 1").fetchone()
    con.close()
    assert picklog._settle_one(NoBo3(), None, r, late) == 0
    con = picklog._db()
    row = con.execute("SELECT status, result FROM picks WHERE id = 1").fetchone()
    con.close()
    assert row["status"] == "open" and json.loads(row["result"])["rule_pending"]["rule"] == "forfeit_outside_scope"


def test_dnp_on_a_scraped_map_per_platform(monkeypatch):
    # he has map 1 only; map 2 is scraped for his team (a stand-in played it)
    _fake(monkeypatch, series=[_row(1, 18)], forfeits=0, map_state=[(1, 10), (2, 10)])
    assert picklog.scope_result(_pick("underdog"))["status"] == "void"        # must play each map of the selection
    assert picklog.scope_result(_pick("prizepicks"))["status"] == "pending"   # CS2 activity threshold is in-app only
    # map 2 not scraped yet: not a DNP - the usual wait / error handling
    _fake(monkeypatch, series=[_row(1, 18)], forfeits=0, map_state=[(1, 10), (2, 0)])
    assert picklog.scope_result(_pick("underdog"))["status"] not in ("void", "pending")


def test_settle_hltv_slug_forfeit_per_platform(monkeypatch, db):
    _fake(monkeypatch, **MAP3_FORFEIT)
    monkeypatch.setattr(hltv, "settle_totals", lambda *a, **k: {"found": True, "kills": 33, "headshots": 10, "maps": ["a", "b"]})
    now, start = datetime(2026, 10, 7, tzinfo=timezone.utc), datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    assert picklog._settle_hltv(_pick("underdog", slug="hltv-7"), now, start)[0] == "settled"
    assert picklog._settle_hltv(_pick("prizepicks", slug="hltv-7"), now, start) is None
    assert picklog._settle_hltv(_pick(None, slug="hltv-7"), now, start)[0] == "void"


# ------------------------------------------------------------------------------------------------ slips
def test_batch_slip_refunds_when_live_legs_are_one_team():
    slip = {"legs": [{"p": .6, "mult": 1.0, "team": "A"}, {"p": .6, "mult": 1.0, "team": "A"}, {"p": .6, "mult": 1.0, "team": "B"}]}
    r = slip_batches.settle_slip(slip, ["hit", "hit", "void"])
    assert r["status"] == "refunded"                             # before: won at 2 picks
    assert slip_batches.settle_slip(slip, ["hit", "void", "hit"])["status"] == "won"


def test_batch_candidates_skip_one_team_combinations():
    rows = [{"nick": "a", "team": "A", "match_slug": "m1", "p": .7}, {"nick": "b", "team": "A", "match_slug": "m2", "p": .7},
            {"nick": "c", "team": "C", "match_slug": "m3", "p": .5}]
    combos = {frozenset(x["nick"] for x in legs) for n, p, legs, ms in slip_batches._candidates(rows, (2,))}
    assert frozenset({"a", "b"}) not in combos and frozenset({"a", "c"}) in combos


def test_loaded_slip_records_entry_check(monkeypatch, tmp_path):
    import slips
    monkeypatch.setattr(slips, "LOADED", tmp_path / "loaded.jsonl")
    monkeypatch.setattr(slips, "_SEEN", {"rec": None, "load": None})
    assert slips.log_loaded({"cid": "c1", "legs": [dict(_l("A", "x"), book="underdog", line=20.5),
                                                    dict(_l("A", "y"), book="underdog", line=18.5)]})
    rec = slips.read(slips.LOADED)[0]
    assert rec["entry_check"]["eligible"] is False and rec["legs"][0]["team"] == "A"
