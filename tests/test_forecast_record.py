"""Forecast record (2026-10-13): what each saved forecast says about its market input (odds source, policy, signature,
alias / matching revision, deployed commit), when a regrade writes a new forecast, exact replay of a saved forecast, the
pick's team label (the UNO MILLE chain) and settlement keys, the learning-ledger copy and the optional shadow entry.
ALL quotes, ids and results are LABELLED TEST FIXTURES (names and ids follow the audited ALLE chain: HLTV player 11922,
ex-Keyd Stars 13423 vs Galorys 12677, hltv:2399204, BO3.gg player 21721; prices and kills are invented)."""
import copy
import json
import sqlite3
import sys
import types
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import grader
import market_odds as M
import picklog
from pick_verdict import verdict

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "grader_inputs_sample.json").read_text(encoding="utf-8"))
START = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(timespec="seconds")
SLUG = "ex-keyd-stats-vs-galorys-cs-13-10-2026"


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: "matches=1;max_id=1;max_date=2026-10-13")
    return tmp_path / "picks.db"


def tp_answer(team="ex-Keyd Stars", opp="Galorys", **over):
    pv = {"source": "thunderpick", "policy": "tp-first-2", "market": "Match Winner", "scope": "series winner",
          "match_id": "hltv:2399204", "source_event_id": "thunderpick:fx:2816445", "team_is_a": False, "flipped": True,
          "odds_team": 2.47, "odds_opp": 1.48, "p_team": 0.374684, "availability": "current", "label": "TEST FIXTURE",
          "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "snapshot_sha256": "a" * 64}
    pv.update(over)
    return {"found": True, "team": team, "opponent": opp, "start": START, "url": "https://thunderpick.io/match/2816445",
            "title": "TEST FIXTURE", "book": "thunderpick", "team_prob": pv["p_team"], "opp_prob": round(1 - pv["p_team"], 6),
            "team_ml": 167, "opp_ml": -167, "maps": {}, "flags": [], "provenance": pv}


NO_PM = lambda *a: {"found": False, "note": "No open Polymarket market (TEST FIXTURE)"}
NO_OP = lambda *a, **k: {"found": False, "note": "OddsPapi: none (TEST FIXTURE)"}


def grader_in(odds, line=None):
    """Grader inputs as cs2_scout.grader_inputs builds the market part (TEST: the rest is one saved production input)."""
    pv = odds.get("provenance") or {}
    gi = dict(copy.deepcopy(SAMPLE["inputs"]), odds_provenance=copy.deepcopy(pv), p_team_market=M.p_team(odds),
              odds_policy=pv.get("odds_policy"), mapping_rev=pv.get("mapping_rev"),
              market_quality="SOFT" if odds.get("found") else "NONE",
              map_win_probs=[None, None] if odds.get("found") else None)
    gi.pop("team_ml", None), gi.pop("opp_ml", None)
    if odds.get("found"):
        gi.update(team_ml=odds["team_ml"], opp_ml=odds["opp_ml"])
    if line is not None:
        gi["line"] = line
    return gi


def mk_res(odds, line=24.5, player=("ALLE", "alle", 21721), pb_over=None, nm_over=None, team_hint="ex-Keyd Stars"):
    gi = grader_in(odds, line)
    g = grader.grade_leg(copy.deepcopy(gi))
    pb = {"player": {"nick": player[0], "slug": player[1], "id": player[2]}, "current_team": "UNO MILLE",
          "profile_team": "UNO MILLE", "playing_for": "ex-Keyd Stars", "standin_for": "ex-Keyd Stars", "overall": {"maps_played": 40}}
    pb.update(pb_over or {})
    nm = {"slug": SLUG, "opponent": "Galorys", "start": START, "maps": []}
    nm.update(nm_over or {})
    return {"grade": g, "player_block": pb, "next_match": nm, "odds": odds, "grader_inputs": gi, "team_hint": team_hint}


def log(res):
    return picklog.write_row(picklog.pick_row(res, verdict(res["grade"])))


def forecasts(db, pid=None):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM forecasts" + (" WHERE pick_id = ?" if pid else "") + " ORDER BY id"
    out = [dict(r) for r in con.execute(q, (pid,) if pid else ())]
    con.close()
    return out


def raw_row(**over):
    base = {"key": "k1", "logged_at": "2026-10-13T10:00:00+00:00", "player": "p", "prop": "kills", "line": 30.5,
            "pick": "More", "status": "open", "match_start": "2099-01-01T00:00:00Z", "p_pick": 0.6, "p_more": 0.6,
            "grade": "B", "units": 0.5, "lambda": 31.0, "test": 2, "_odds_src": "thunderpick", "_odds_sig": "s1",
            "_odds_policy": "odds-source-2|tp-first-2", "_mapping_rev": "map-aaaaaaaaaaaa"}
    base.update(over)
    return base


# ------------------------------------------------------------------------------------------------ when a forecast is written
def test_unchanged_polling_is_not_logged(db):
    for i in range(3):    # the board re-grades every 15 min on a re-polled, unchanged quote: one forecast
        picklog.write_row(raw_row(logged_at=f"2026-10-13T10:{i:02d}:00+00:00"))
    assert len(forecasts(db)) == 1


def test_an_odds_change_from_the_same_source_is_logged(db):
    picklog.write_row(raw_row())
    picklog.write_row(raw_row(_odds_sig="s2"))                      # same source, same outputs, moved price
    f = forecasts(db)
    assert [x["odds_sig"] for x in f] == ["s1", "s2"] and [x["odds_source"] for x in f] == ["thunderpick"] * 2


def test_a_source_change_is_logged_even_when_outputs_are_unchanged(db):
    picklog.write_row(raw_row(_odds_src="polymarket", _odds_sig="pm"))
    picklog.write_row(raw_row(_odds_src="thunderpick", _odds_sig="tp"))
    assert [x["odds_source"] for x in forecasts(db)] == ["polymarket", "thunderpick"]


def test_an_odds_policy_change_is_logged(db):
    picklog.write_row(raw_row(_odds_policy="odds-source-1"))
    picklog.write_row(raw_row(_odds_policy="odds-source-2|tp-first-2"))
    assert len(forecasts(db)) == 2


def test_mapping_rev_is_recorded_on_every_row_but_not_compared(db, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "0123abcd")             # TEST FIXTURE commit
    picklog.write_row(raw_row(_mapping_rev="map-111111111111"))
    picklog.write_row(raw_row(_mapping_rev="map-222222222222"))      # mapping changed but the line's input did not
    picklog.write_row(raw_row(_mapping_rev="map-222222222222", grade="A", units=1.0))
    f = forecasts(db)
    assert len(f) == 2 and [x["mapping_rev"] for x in f] == ["map-111111111111", "map-222222222222"]
    assert all(x["git_commit"] == "0123abcd" and x["odds_policy"] == "odds-source-2|tp-first-2" for x in f)


def test_alias_change_writes_a_new_forecast_only_for_the_lines_it_affects(db, monkeypatch, tmp_path):
    """A maintained alias makes Thunderpick link line A's match (no market -> Thunderpick); line B (another match, priced
    the same before and after) gets no new forecast. Both record the revision they were graded under, and each revision's
    full content is kept for replay."""
    import mapping, matchid
    f = tmp_path / "team_aliases.json"
    f.write_text(json.dumps({"aliases": [{"name": "NIP", "team_id": 4411, "proof": "TEST FIXTURE"}]}), encoding="utf-8")
    monkeypatch.setattr(matchid, "ALIASES_PATH", f)

    def tp(team, opponent, start_iso, now=None, bo=None):            # links ex-Keyd only once the alias exists (TEST)
        names = {e["name"] for e in mapping.current_content()[1]["team_aliases"]["entries"]}
        if team == "ex-Keyd Stats" and "ex-Keyd Stats" not in names:
            return {"found": False, "reason": "match not linked ('ex-Keyd Stats' not on HLTV)", "availability": "unlinked"}
        if team == "ex-Keyd Stats":
            return tp_answer(team="ex-Keyd Stats")
        return tp_answer(team=team, opp=opponent, match_id="hltv:2399999", source_event_id="thunderpick:fx:1", odds_team=1.8,
                         odds_opp=2.0, p_team=0.526316)

    fixed = mk_res(tp_answer())["grade"]                               # ONE grade for every write: only the market input differs

    def grade_both():
        a = M.select("ex-Keyd Stats", "Galorys", START, tp=tp, pm=NO_PM, op=NO_OP, bo=3)
        b = M.select("Fluxo", "Imperial", START, tp=tp, pm=NO_PM, op=NO_OP, bo=3)
        ra = mk_res(a)
        rb = mk_res(b, player=("xureba", "xureba", 1), nm_over={"slug": "fluxo-vs-imperial-13-10-2026", "opponent": "Imperial"},
                    pb_over={"playing_for": "Fluxo", "standin_for": None})
        ra["grade"] = rb["grade"] = copy.deepcopy(fixed)
        return log(ra), log(rb), a["provenance"]["mapping_rev"]
    pa, pb_, rev1 = grade_both()
    f.write_text(json.dumps({"aliases": [{"name": "NIP", "team_id": 4411, "proof": "TEST FIXTURE"},
                                         {"name": "ex-Keyd Stats", "team_id": 13423, "proof": "TEST FIXTURE"}]}), encoding="utf-8")
    pa2, pb2, rev2 = grade_both()
    assert (pa, pb_) == (pa2, pb2) and rev1 != rev2
    fa, fb = forecasts(db, pa), forecasts(db, pb_)
    assert [x["odds_source"] for x in fa] == [None, "thunderpick"] and [x["mapping_rev"] for x in fa] == [rev1, rev2]
    assert len(fb) == 1 and fb[0]["mapping_rev"] == rev1               # unaffected line: no new forecast
    old, new = mapping.content(rev1, sqlite3.connect(db)), mapping.content(rev2, sqlite3.connect(db))
    assert len(old["team_aliases"]["entries"]) == 1 and len(new["team_aliases"]["entries"]) == 2
    with pytest.raises(sqlite3.DatabaseError):                         # append-only
        c = sqlite3.connect(db)
        c.execute("DELETE FROM mapping_revisions")


# ------------------------------------------------------------------------------------------------ replay
def test_a_saved_forecast_replays_exactly(db):
    odds = M.select("ex-Keyd Stars", "Galorys", START, tp=lambda *a, **k: tp_answer(), pm=NO_PM, op=NO_OP, bo=3)
    res = mk_res(odds)
    v = verdict(res["grade"])
    pid = log(res)
    f = forecasts(db, pid)[0]
    saved = json.loads(zlib.decompress(f["inputs"]))
    g2 = grader.grade_leg(saved)
    v2 = verdict(g2)
    for k in ("p_more", "lambda_final", "probs_raw", "grade", "units", "units_less"):
        assert g2.get(k) == res["grade"].get(k)
    assert (v2["side"], v2["grade"], v2["units"], v2["p"]) == (v["side"], v["grade"], v["units"], v["p"])
    assert (f["pick"], f["grade"], f["units"], f["p_pick"]) == (v["side"], v["grade"], v["units"], v["p"])
    pv = saved["odds_provenance"]
    assert f["odds_sig"] == pv["sig"] == odds["provenance"]["sig"] and f["odds_source"] == "thunderpick"
    assert saved["p_team_market"] == 0.374684 and f["odds_policy"] == pv["odds_policy"] and f["mapping_rev"] == pv["mapping_rev"]


# ------------------------------------------------------------------------------------------------ UNO MILLE chain
def _hltv_fakes(monkeypatch, confirm=lambda team: team == "ex-Keyd Stars"):
    import hltv
    calls = {"ids": [], "confirm": [], "opp": [], "series": []}

    class Cur:
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def execute(s, q, a=None): pass
        def fetchall(s): return []
        def fetchone(s): return None

    class Con:
        def cursor(s): return Cur()
        def close(s): pass
    series = [{"hltv_match": 2399204, "opponent": "Galorys", "opponent_id": 12677, "number": n, "date": "2026-10-13",
               "map": m, "kills": k, "headshots": h, "won": w, "rounds": 24}
              for n, m, k, h, w in ((1, "Mirage", 13, 6, True), (2, "Nuke", 12, 5, True))]
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, nick, team: calls["ids"].append((nick, team)) or ([11922] if nick == "ALLE" else []))
    monkeypatch.setattr(picklog, "_team_confirmed", lambda cur, ids, team: calls["confirm"].append((tuple(ids), team)) or confirm(team))
    monkeypatch.setattr(hltv, "_series_rows", lambda cur, ids, since, extra, params, **k: calls["series"].append((list(ids), params)) or series)
    monkeypatch.setattr(hltv, "opponent_rows", lambda cur, rows, opp: calls["opp"].append(opp) or ([x for x in rows if x["opponent_id"] == 12677], "team id"))
    monkeypatch.setattr(picklog, "_forfeited", lambda cur, mid: 0)
    return calls


def _pick(db, pid):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM picks WHERE id = ?", (pid,)).fetchone()
    con.close()
    return r


def test_alle_chain_team_label_odds_query_and_settlement_all_target_ex_keyd_vs_galorys(db, monkeypatch):
    """Audit (UNO MILLE chain): picks.team was ALLE's BO3.gg profile team 'UNO MILLE' while the grader played him for
    ex-Keyd Stars (playing_for) vs Galorys. Now the row's team, the odds query and the settlement lookup all name
    ex-Keyd Stars vs Galorys; the profile team is kept as profile_team; the settlement keys are unchanged."""
    seen = []

    def tp(team, opponent, start_iso, now=None, bo=None):
        seen.append((team, opponent, bo))
        return tp_answer(team=team, opp=opponent)
    odds = M.select("ex-Keyd Stars", "Galorys", START, tp=tp, pm=NO_PM, op=NO_OP, bo=3)   # = run(): playing_for vs opponent
    pid = log(mk_res(odds))
    r = _pick(db, pid)
    assert r["team"] == "ex-Keyd Stars" and r["profile_team"] == "UNO MILLE" and r["opponent"] == "Galorys"
    assert seen == [("ex-Keyd Stars", "Galorys", 3)] and odds["provenance"]["query"]["team"] == "ex-Keyd Stars"
    # settlement keys: the pick key, BO3.gg player id + match slug - unchanged by the label
    assert r["key"] == f"alle|kills|24.5|standard|{SLUG}" and r["player_id"] == 21721 and r["match_slug"] == SLUG
    calls = _hltv_fakes(monkeypatch)
    res = picklog.scope_result(r)
    assert res["status"] == "ok" and res["hltv_match"] == 2399204 and res["total"] == 25
    assert calls["ids"] == [("ALLE", "ex-Keyd Stars")] and calls["confirm"] == [((11922,), "ex-Keyd Stars")]
    assert calls["opp"] == ["Galorys"] and calls["series"][0] == ([11922], {"d": START[:10]})


def test_settlement_falls_back_to_the_profile_team(db, monkeypatch):
    """A match team HLTV cannot confirm for him (first stand-in match not scraped under that name) never settles less
    than before: the BO3.gg profile team (what picks.team held until now) is tried next."""
    odds = M.select("ex-Keyd Stars", "Galorys", START, tp=lambda *a, **k: tp_answer(), pm=NO_PM, op=NO_OP)
    r = _pick(db, log(mk_res(odds)))
    calls = _hltv_fakes(monkeypatch, confirm=lambda team: team == "UNO MILLE")
    res = picklog.scope_result(r)
    assert res["status"] == "ok" and calls["ids"] == [("ALLE", "ex-Keyd Stars"), ("ALLE", "UNO MILLE")]
    calls = _hltv_fakes(monkeypatch, confirm=lambda team: False)
    assert picklog.scope_result(r)["reason"] == "player/team identity not confirmed in HLTV"


def test_older_rows_settle_exactly_as_before(monkeypatch):
    calls = _hltv_fakes(monkeypatch, confirm=lambda team: team == "UNO MILLE")
    old = {"id": 1, "player": "ALLE", "team": "UNO MILLE", "opponent": "Galorys", "prop": "kills", "scope": "12",
           "line": 24.5, "pick": "More", "match_start": START}          # no profile_team column value
    assert picklog.scope_result(old)["status"] == "ok" and calls["ids"] == [("ALLE", "UNO MILLE")]


def test_hltv_only_matches_settle_by_the_match_team_then_the_profile_team(monkeypatch):
    import hltv
    got = []

    def totals(nick, team, mid):
        got.append((nick, team, mid))
        return {"found": True, "kills": 25, "headshots": 11, "maps": ["Mirage", "Nuke"]} if team == "UNO MILLE" else {"found": False}
    monkeypatch.setattr(hltv, "settle_totals", totals)
    monkeypatch.setattr(hltv, "_connect", lambda: (_ for _ in ()).throw(RuntimeError("no db (TEST)")))
    r = {"id": 1, "player": "ALLE", "team": "ex-Keyd Stars", "profile_team": "UNO MILLE", "match_slug": "hltv-2399204",
         "prop": "kills", "line": 24.5, "pick": "More"}
    now = datetime.now(timezone.utc)
    assert picklog._settle_hltv(r, now, now) == ("settled", json.dumps(["Mirage", "Nuke"]), 25, 1)
    assert got == [("ALLE", "ex-Keyd Stars", 2399204), ("ALLE", "UNO MILLE", 2399204)]


def test_team_label_falls_back_to_the_platform_then_the_profile(db):
    odds = M.select("x", "y", START, tp=lambda *a, **k: tp_answer(), pm=NO_PM, op=NO_OP)
    res = mk_res(odds, pb_over={"playing_for": None})
    assert picklog.pick_row(res, verdict(res["grade"]))["team"] == "ex-Keyd Stars"          # the platform's label
    res = mk_res(odds, pb_over={"playing_for": None}, team_hint=None)
    row = picklog.pick_row(res, verdict(res["grade"]))
    assert row["team"] == "UNO MILLE" and row["profile_team"] == "UNO MILLE"


# ------------------------------------------------------------------------------------------------ record plumbing
def test_pick_verdict_is_part_of_the_model_version():
    import inspect
    assert '"pick_verdict.py"' in inspect.getsource(picklog._model_version)


def test_learning_copy_keeps_the_odds_record(db, tmp_path):
    import learning
    odds = M.select("ex-Keyd Stars", "Galorys", START, tp=lambda *a, **k: tp_answer(), pm=NO_PM, op=NO_OP, bo=3)
    log(mk_res(odds))
    picks, fr = learning.read_picklog(db)
    con = learning.connect(tmp_path / "ledger.db")
    n, _ = learning._copy_forecasts(con, picks, fr, datetime.now(timezone.utc))
    row = dict(con.execute("SELECT odds_source, odds_policy, mapping_rev, odds_selected FROM forecast_copy").fetchone())
    con.close()
    pv = odds["provenance"]
    assert n == 1 and row == {"odds_source": "thunderpick", "odds_policy": pv["odds_policy"], "mapping_rev": pv["mapping_rev"],
                              "odds_selected": "thunderpick"}


def test_market_rounds_shadow_entry_is_optional(monkeypatch):
    odds = M.select("ex-Keyd Stars", "Galorys", START, tp=lambda *a, **k: tp_answer(), pm=NO_PM, op=NO_OP)
    res = mk_res(odds)
    monkeypatch.setitem(sys.modules, "perf_market", None)            # module absent: no entry, no error
    assert "market_rounds" not in (picklog._factors(res).get("shadow") or {})
    fake = types.ModuleType("perf_market")
    fake.shadow_entry = lambda gi, g: {"version": "TEST FIXTURE", "p_more": 0.5, "p_team_market": gi.get("p_team_market")}
    monkeypatch.setitem(sys.modules, "perf_market", fake)
    sh = picklog._factors(res)["shadow"]
    assert sh["market_rounds"] == {"version": "TEST FIXTURE", "p_more": 0.5, "p_team_market": 0.374684}
    fake.shadow_entry = lambda gi, g: (_ for _ in ()).throw(RuntimeError("fixture"))
    assert "market_rounds" not in (picklog._factors(res).get("shadow") or {})


def test_mapping_revision_is_order_independent_and_replayable(monkeypatch, tmp_path):
    import mapping, matchid
    e1 = {"name": "NIP", "team_id": 4411, "proof": "TEST FIXTURE"}
    e2 = {"name": "Overtake", "team_id": 13855, "proof": "TEST FIXTURE"}
    f = tmp_path / "a.json"
    monkeypatch.setattr(matchid, "ALIASES_PATH", f)
    f.write_text(json.dumps({"aliases": [e1, e2]}), encoding="utf-8")
    r1 = mapping.revision()
    f.write_text(json.dumps({"aliases": [e2, e1], "_doc": "reordered"}) + " ", encoding="utf-8")
    assert mapping.revision() == r1 and r1.startswith("map-") and len(r1) == 16
    monkeypatch.setattr(matchid, "MATCHING_RULES_VERSION", "match-rules-TEST")
    assert mapping.revision() != r1                                   # the rules' own version is part of it
    con = sqlite3.connect(":memory:")
    rev = mapping.record(con)
    assert mapping.content(rev, con)["matching_rules"] == "match-rules-TEST"
    mapping.record(con)                                               # idempotent
    assert con.execute("SELECT COUNT(*) FROM mapping_revisions").fetchone()[0] == 1
