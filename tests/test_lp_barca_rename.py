"""LP -> BARCA dated team rename (matchid match-rules-3, 2026-10-23). Offline: the HLTV teams / finished matches below
mirror the HLTV database as read 2026-10-23 (team and match ids are HLTV's, kills invented, labelled TEST FIXTURE).

Evidence (HLTV DB, read-only): LP 13304's last match 2399205 vs Galorys 2026-10-14; BARCA 14107's first matches 2399805 vs
paiN and 2399808 vs Galorys, 2026-10-21; the same five HLTV player ids in both. PrizePicks listed 'LP vs paiN' 2026-10-21
14:00 UTC = HLTV 2399805. 'LP' is itself an exact HLTV team, so this is a dated rename keyed on team ids, never an alias."""
import json
import threading
from datetime import datetime, timezone

import pytest

import hltv
import mapping
import matchid
import picklog

LP, BARCA, PAIN, GALORYS, EXKEYD, FLUXO = 13304, 14107, 4773, 12677, 13423, 9999
TEAMS = [(LP, "LP"), (BARCA, "BARCA"), (PAIN, "paiN"), (GALORYS, "Galorys"), (EXKEYD, "ex-Keyd Stars"), (FLUXO, "Fluxo")]
FINISHED = [  # HLTV dates finished matches (UTC); matches as in the HLTV database 2026-10-23
    {"matchid": 2399202, "date": datetime(2026, 10, 12).date(), "team_ids": [EXKEYD, LP]},
    {"matchid": 2399205, "date": datetime(2026, 10, 14).date(), "team_ids": [GALORYS, LP]},
    {"matchid": 2399805, "date": datetime(2026, 10, 21).date(), "team_ids": [BARCA, PAIN]},
    {"matchid": 2399808, "date": datetime(2026, 10, 21).date(), "team_ids": [BARCA, GALORYS]},
]
PAIN_LP_START = "2026-10-21T14:00:00.000+00:00"         # PrizePicks 'LP vs paiN' (forecasts 69327-69364)


@pytest.fixture
def mi(monkeypatch, tmp_path):
    """matchid on fixture HLTV tables and the MAINTAINED team_aliases.json (with its renames entry)."""
    monkeypatch.setattr(matchid, "DB_PATH", tmp_path / "match_links.db")
    monkeypatch.setattr(matchid, "_local", threading.local())
    monkeypatch.setattr(matchid, "BACKGROUND", False)
    data = {"teams": list(TEAMS), "schedule": [], "finished": list(FINISHED)}
    for n in ("teams", "schedule", "finished"):
        monkeypatch.setattr(matchid, f"_db_{n}", (lambda n: lambda: list(data[n]))(n))

    def load(**kw):
        data.update(kw)
        matchid._reset()
        matchid.refresh(force=True)
        matchid._S["loaded"] = True          # these fixture tables, never a stored copy from an earlier load
    load()
    yield load
    matchid._reset()


def without_renames(monkeypatch, tmp_path):
    d = json.loads(matchid.ALIASES_PATH.read_text(encoding="utf-8"))
    d.pop("renames", None)
    p = tmp_path / "no_renames.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(matchid, "ALIASES_PATH", p)


# ---------------------------------------------------------------- the rename entry itself
def test_maintained_rename_entry_is_dated_and_keyed_on_team_ids():
    d = json.loads(matchid.ALIASES_PATH.read_text(encoding="utf-8"))
    rn = [e for e in d.get("renames", []) if e.get("from_team_id") == LP]
    assert len(rn) == 1 and rn[0]["to_team_id"] == BARCA and rn[0]["valid_from"] == "2026-10-20T18:00:00Z" and rn[0]["proof"]
    assert sorted(rn[0]["players"]) == [14010, 16314, 20521, 21284, 23550]          # the five HLTV ids in LP's and BARCA's lineups
    assert not any(matchid.norm(a["name"]) in ("lp", "barca") for a in d["aliases"])      # never a name alias


# ---------------------------------------------------------------- team identity unchanged
def test_team_identity_and_history_unchanged(mi):
    assert matchid.team_key("LP") == f"team:{LP}" and matchid.team_key("BARCA") == f"team:{BARCA}"
    assert matchid.team_key("largadosypelados") == f"team:{LP}"                 # the existing alias still names LP
    assert not matchid.same_team("LP", "BARCA") and not hltv.same_team("LP", "BARCA")
    assert hltv.alias_team_id("LP") == LP and hltv.alias_team_id("BARCA") == BARCA   # no date context: own ids
    # history reads (no date context) keep each identity's own rows: LP's are LP's, BARCA's are BARCA's
    rows = [{"opponent": "LP", "opponent_id": LP, "hltv_match": 2399205}, {"opponent": "BARCA", "opponent_id": BARCA, "hltv_match": 2399808}]
    lookup = {"lp": LP, "barca": BARCA}

    class Cur:
        def execute(s, q, a=None): s.v = a[0]
        def fetchone(s): return (lookup.get(s.v), 1) if s.v in lookup else (None, 0)
    on, how = hltv.opponent_rows(Cur(), rows, "LP")
    assert how == "team id" and [r["hltv_match"] for r in on] == [2399205]
    on, _ = hltv.opponent_rows(Cur(), rows, "BARCA")
    assert [r["hltv_match"] for r in on] == [2399808]


# ---------------------------------------------------------------- match linking around the transition
def test_lp_before_the_date_is_lp_13304(mi):
    for a, b, st, mid in (("LP", "ex-Keyd Stars", "2026-10-12T21:00:00Z", 2399202), ("Galorys", "LP", "2026-10-14T21:00:00Z", 2399205),
                          ("largadosypelados", "ex-Keyd Stars", "2026-10-12T21:00:00Z", 2399202)):
        cm = matchid.canonical_match(a, b, st)
        assert cm["match_id"] == f"hltv:{mid}" and "renamed" not in cm, (a, b)
        assert f"team:{LP}" in cm["team_keys"]


def test_pain_vs_lp_on_the_barca_date_links_2399805(mi):
    for a, b in (("LP", "paiN"), ("paiN", "LP")):
        cm = matchid.canonical_match(a, b, PAIN_LP_START)
        assert cm["match_id"] == "hltv:2399805" and cm["source"] == "hltv", (a, b, cm)
        assert cm["renamed"]["from"] == [matchid.team_key(a), matchid.team_key(b)]
        assert cm["team_keys"] == sorted([f"team:{BARCA}", f"team:{PAIN}"]) and "dated team rename" in cm["reason"]
    assert matchid.canonical_match("BARCA", "paiN", PAIN_LP_START)["match_id"] == "hltv:2399805"   # same id either name
    assert matchid.canonical_match("LP", "Galorys", "2026-10-21T20:00:00Z")["match_id"] == "hltv:2399808"
    assert matchid.match_id_for_row({"team": "paiN", "opponent": "LP", "start": PAIN_LP_START}) == "hltv:2399805"


def test_without_the_rename_entry_the_line_stays_unlinked_to_hltv(mi, monkeypatch, tmp_path):
    """The previous behaviour (and the regression): 'LP' = 13304 has no match vs paiN, so the line got a hashed id."""
    without_renames(monkeypatch, tmp_path)
    mi()
    cm = matchid.canonical_match("LP", "paiN", PAIN_LP_START)
    assert cm["source"] == "hash" and "renamed" not in cm


def test_unrelated_lp_lines_are_unaffected(mi):
    # before valid_from: no rename even against a team BARCA later played
    cm = matchid.canonical_match("LP", "paiN", "2026-10-14T21:00:00Z")
    assert cm["source"] == "hash" and cm["team_keys"] == sorted([f"team:{LP}", f"team:{PAIN}"]) and "renamed" not in cm
    # after valid_from but no BARCA match with that opponent at that time: unchanged (LP 13304 key, hashed)
    cm = matchid.canonical_match("LP", "Fluxo", "2026-10-22T18:00:00Z")
    assert cm["source"] == "hash" and f"team:{LP}" in cm["team_keys"] and "renamed" not in cm
    cm = matchid.canonical_match("LP", "paiN", "2026-10-21T22:00:00Z", mint=False)  # date-only finished match, same day
    assert cm["match_id"] is None or cm["match_id"] == "hltv:2399805"


def test_a_real_lp_match_after_the_date_wins_over_the_rename(mi):
    """If HLTV ever lists LP 13304 itself against the opponent in the window, that match is linked, never BARCA's."""
    mi(finished=FINISHED + [{"matchid": 2400001, "date": datetime(2026, 10, 21).date(), "team_ids": [LP, PAIN]}])
    cm = matchid.canonical_match("LP", "paiN", PAIN_LP_START)
    assert cm["match_id"] == "hltv:2400001" and "renamed" not in cm


def test_two_barca_candidates_stay_unresolved(mi):
    mi(finished=FINISHED + [{"matchid": 2400002, "date": datetime(2026, 10, 21).date(), "team_ids": [BARCA, PAIN]}])
    cm = matchid.canonical_match("LP", "paiN", PAIN_LP_START)
    assert cm["hltv_match"] is None and "renamed" not in cm


def test_successor_key_and_renamed_team_id(mi):
    assert matchid.renamed_team_id("LP", PAIN_LP_START) == BARCA
    assert matchid.renamed_team_id("LP", "2026-10-20T17:59:00Z") is None             # before the 18:00 UTC announcement
    assert matchid.renamed_team_id("LP", "2026-10-20T18:00:00Z") == BARCA
    assert matchid.renamed_team_id("paiN", PAIN_LP_START) is None
    assert matchid.renamed_team_id("BARCA", PAIN_LP_START) is None


def test_mapping_revision_records_the_rename_and_rules():
    rev, content = mapping.current_content()
    assert content["matching_rules"] == "match-rules-4"
    assert any(e.get("to_team_id") == BARCA for e in content["team_aliases"].get("renames", []))


# ---------------------------------------------------------------- settlement identity
def _fake_hltv(monkeypatch, series, teams=None, lineup=None):
    """teams: {matchid: [HLTV team ids]}, lineup: {(matchid, teamid): [HLTV player ids]} (the rename's lineup gate)."""
    class Cur:
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def execute(s, q, a=None): s.q, s.a = q, a
        def fetchone(s): return (0,)

        def fetchall(s):
            if "FROM matches WHERE matchid" in s.q:
                return [(t,) for t in (teams or {}).get(s.a[0], ())]
            if "FROM player_stats WHERE matchid" in s.q:
                return [(p,) for p in (lineup or {}).get(tuple(s.a), ())]
            return []

    class Con:
        def cursor(s): return Cur()
        def close(s): pass

    def series_rows(cur, ids, since, extra="", params=None, maxn=2, need=(1, 2)):
        got = [dict(x) for x in series if x["hltv_match"] == int((params or {})["mid"]) and x["number"] <= maxn]
        return got if {x["number"] for x in got} >= set(need) else []
    names = {"lp": LP, "barca": BARCA, "pain": PAIN, "galorys": GALORYS}
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, nick, team: [1])
    monkeypatch.setattr(picklog, "_team_confirmed", lambda cur, ids, team: True)
    monkeypatch.setattr(hltv, "_series_rows", series_rows)
    monkeypatch.setattr(hltv, "resolve_team_id", lambda cur, name: names.get(hltv._norm(name)))


def rows_2399805(side):
    """TEST FIXTURE: one player's maps 1-2 in HLTV 2399805 (paiN vs BARCA)."""
    opp, oid = ("BARCA", BARCA) if side == "pain" else ("paiN", PAIN)
    return [{"match_id": "hltv-2399805", "hltv_match": 2399805, "date": "2026-10-21", "number": n, "map": f"de_m{n}",
             "kills": k, "headshots": 9, "rounds": 24, "won": True, "opponent": opp, "opponent_id": oid, "source": "hltv",
             "env": "lan"} for n, k in ((1, 20), (2, 18))]


def pick(team, opponent, slug, scope="12"):
    return {"id": 1, "player": "x", "team": team, "opponent": opponent, "match_slug": slug, "prop": "kills", "line": 35.5,
            "pick": "More", "match_start": PAIN_LP_START, "scope": scope}


def test_pain_player_vs_lp_settles_from_2399805(mi, monkeypatch):
    _fake_hltv(monkeypatch, rows_2399805("pain"))
    res = picklog.scope_result(pick("paiN", "LP", "pain-vs-lp-21-10-2026"))           # BO3.gg slug: canonical match
    assert res["status"] == "ok" and res["hltv_match"] == 2399805 and res["total"] == 38 and res["identity"] == "canonical match"
    res = picklog.scope_result(pick("paiN", "LP", "hltv-2399805", scope="1"))          # own HLTV id (Map 1)
    assert res["status"] == "ok" and res["hltv_match"] == 2399805 and res["total"] == 20 and res["identity"] == "hltv id"


def test_lp_player_vs_pain_settles_from_2399805(mi, monkeypatch):
    _fake_hltv(monkeypatch, rows_2399805("lp"))
    res = picklog.scope_result(pick("LP", "paiN", "lp-vs-pain-21-10-2026"))
    assert res["status"] == "ok" and res["hltv_match"] == 2399805 and res["total"] == 38


def test_pain_player_vs_lp_before_the_fix_was_not_settled(mi, monkeypatch, tmp_path):
    """Regression baseline: without the rename entry the paiN player's line had no canonical HLTV match."""
    without_renames(monkeypatch, tmp_path)
    mi()
    _fake_hltv(monkeypatch, rows_2399805("pain"))
    res = picklog.scope_result(pick("paiN", "LP", "pain-vs-lp-21-10-2026"))
    assert res["status"] == "none"


def test_own_id_pick_before_the_date_keeps_the_old_opponent_rule(mi, monkeypatch):
    """An hltv-<id> pick vs 'LP' before valid_from: the rows must be against LP 13304 (BARCA rows never count)."""
    rows = [dict(x, hltv_match=2399205, date="2026-10-14") for x in rows_2399805("pain")]     # rows vs BARCA id
    _fake_hltv(monkeypatch, rows)
    r = dict(pick("Galorys", "LP", "hltv-2399205", scope="1"), match_start="2026-10-14T21:00:00.000+00:00")
    assert picklog.scope_result(r)["status"] == "none"


# ---------------------------------------------------------------- closeout 2026-10-23: stricter rule (match-rules-4)
FIVE = [14010, 16314, 20521, 21284, 23550]                 # zmb, pancc, Alisson, happ, divine (HLTV ids, LP and BARCA)


def test_effective_time_is_the_announcement_2026_10_20_1800_utc(mi):
    """@LyPcs2 (now 'BARCA esports') announced the rename 2026-10-20 18:00 UTC: a line before that is LP's, after it BARCA's."""
    mi(finished=FINISHED + [{"matchid": 2400010, "date": datetime(2026, 10, 20).date(), "team_ids": [BARCA, FLUXO]}])
    before = matchid.canonical_match("LP", "Fluxo", "2026-10-20T17:30:00Z")
    assert before["hltv_match"] is None and "renamed" not in before
    after = matchid.canonical_match("LP", "Fluxo", "2026-10-20T19:00:00Z")
    assert after["match_id"] == "hltv:2400010" and after["renamed"]["to"] == [f"team:{BARCA}", f"team:{FLUXO}"]


def test_lp_playing_its_own_match_that_day_is_never_renamed(mi):
    """LP 13304 and BARCA both on HLTV the same day: LP is still its own team - an 'LP vs paiN' line stays unlinked."""
    mi(finished=FINISHED + [{"matchid": 2400011, "date": datetime(2026, 10, 21).date(), "team_ids": [LP, FLUXO]}])
    cm = matchid.canonical_match("LP", "paiN", PAIN_LP_START)
    assert cm["hltv_match"] is None and "renamed" not in cm and f"team:{LP}" in cm["team_keys"]
    assert matchid.canonical_match("LP", "Fluxo", PAIN_LP_START)["match_id"] == "hltv:2400011"     # its own fixture


def test_lp_scheduled_within_the_window_blocks_the_rename_outside_it_does_not(mi):
    sched = lambda h: [{"matchid": 2400012, "start": datetime(2026, 10, 21, h, tzinfo=timezone.utc), "team1": "LP",
                        "team2": "Fluxo", "event": "", "format": "bo3"}]
    mi(schedule=sched(16))                                 # LP's own match 2 h after the line: inside WINDOW_H
    assert matchid.canonical_match("paiN", "LP", PAIN_LP_START)["hltv_match"] is None
    mi(schedule=sched(23))                                 # 9 h later: outside the window (a timed match, same UTC date)
    cm = matchid.canonical_match("paiN", "LP", PAIN_LP_START)
    assert cm["hltv_match"] == 2399805 and cm["renamed"]


def test_same_opponent_twice(mi):
    """BARCA vs paiN twice: two on the same date (no times) = unresolved; timed and 6 h apart = the one in the window."""
    mi(finished=FINISHED + [{"matchid": 2400013, "date": datetime(2026, 10, 21).date(), "team_ids": [PAIN, BARCA]}])
    assert matchid.canonical_match("LP", "paiN", PAIN_LP_START)["hltv_match"] is None
    t = lambda m, h: {"matchid": m, "start": datetime(2026, 10, 21, h, tzinfo=timezone.utc), "team1": "paiN", "team2": "BARCA",
                      "event": "Circuit X Curitiba Season 4", "format": "bo3"}
    mi(finished=[f for f in FINISHED if f["matchid"] != 2399805], schedule=[t(2399805, 14), t(2400014, 20)])
    assert matchid.canonical_match("LP", "paiN", PAIN_LP_START)["match_id"] == "hltv:2399805"
    assert matchid.canonical_match("LP", "paiN", "2026-10-21T20:00:00Z")["match_id"] == "hltv:2400014"


def test_platform_spellings_of_both_teams(mi):
    """PrizePicks 'LP', Polymarket / Kalshi 'largadosypelados' (maintained alias of 13304), 'paiN Gaming' (affix)."""
    for a, b in (("LP", "paiN"), ("largadosypelados", "paiN"), ("largadosypelados", "paiN Gaming"), ("paiN Gaming", "LP")):
        cm = matchid.canonical_match(a, b, PAIN_LP_START)
        assert cm["match_id"] == "hltv:2399805" and cm["renamed"], (a, b)
    assert matchid.renamed_team_id("largadosypelados", PAIN_LP_START) == BARCA
    assert matchid.renamed_team_id("Largados y Pelados", PAIN_LP_START) == BARCA    # same normalised alias (spaces dropped)
    assert matchid.renamed_team_id("Largados", PAIN_LP_START) is None               # not an HLTV name or alias: unresolved


def test_rename_players_lists_the_entry_ids(mi):
    assert matchid.rename_players(LP, BARCA) == frozenset(FIVE)
    assert matchid.rename_players(BARCA, LP) == frozenset() and matchid.RENAME_MIN_PLAYERS == 4


def test_settlement_requires_the_barca_lineup_to_match_the_lp_roster(mi, monkeypatch):
    """Linking keeps the date / opponent rule; settling on the renamed link needs >= 4 of LP's five in BARCA's lineup."""
    teams = {2399805: [PAIN, BARCA]}
    for lineup, ok in ((FIVE, True), (FIVE[:4] + [99], True), (FIVE[:3] + [98, 99], False), ([], False)):
        _fake_hltv(monkeypatch, rows_2399805("pain"), teams=teams, lineup={(2399805, BARCA): lineup})
        for slug, sc in (("pain-vs-lp-21-10-2026", "12"), ("hltv-2399805", "1")):        # canonical link and own HLTV id
            res = picklog.scope_result(pick("paiN", "LP", slug, scope=sc))
            assert (res["status"] == "ok") is ok, (lineup, slug, res)
            if not ok:                                   # pending, never "none" (which voids after VOID_AFTER_D days)
                assert res["status"] == "pending" and "rename not confirmed" in res["reason"]
        _fake_hltv(monkeypatch, rows_2399805("lp"), teams=teams, lineup={(2399805, BARCA): lineup})
        res = picklog.scope_result(pick("LP", "paiN", "lp-vs-pain-21-10-2026"))           # an LP player's own line
        assert (res["status"] == "ok") is ok, (lineup, res)
        if not ok:
            assert res["status"] == "pending", res


def test_lineup_gate_never_touches_lines_without_a_rename(mi, monkeypatch):
    """A Galorys player vs LP on 2026-10-14 (LP's own match 2399205) and paiN vs BARCA by name: no lineup needed."""
    rows = [dict(x, hltv_match=2399205, date="2026-10-14", opponent="LP", opponent_id=LP) for x in rows_2399805("pain")]
    _fake_hltv(monkeypatch, rows, teams={2399205: [GALORYS, LP]}, lineup={})
    r = dict(pick("Galorys", "LP", "galorys-vs-lp-14-10-2026"), match_start="2026-10-14T21:00:00.000+00:00")
    assert picklog.scope_result(r)["status"] == "ok"
    _fake_hltv(monkeypatch, rows_2399805("pain"), teams={2399805: [PAIN, BARCA]}, lineup={})
    assert picklog.scope_result(pick("paiN", "BARCA", "pain-vs-barca-21-10-2026"))["status"] == "ok"
