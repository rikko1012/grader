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
    assert len(rn) == 1 and rn[0]["to_team_id"] == BARCA and rn[0]["valid_from"] == "2026-10-21" and rn[0]["proof"]
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
    assert matchid.renamed_team_id("LP", "2026-10-20T23:00:00Z") is None
    assert matchid.renamed_team_id("paiN", PAIN_LP_START) is None
    assert matchid.renamed_team_id("BARCA", PAIN_LP_START) is None


def test_mapping_revision_records_the_rename_and_rules():
    rev, content = mapping.current_content()
    assert content["matching_rules"] == "match-rules-3"
    assert any(e.get("to_team_id") == BARCA for e in content["team_aliases"].get("renames", []))


# ---------------------------------------------------------------- settlement identity
def _fake_hltv(monkeypatch, series):
    class Cur:
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def execute(s, q, a=None): s.q = q
        def fetchone(s): return (0,)
        def fetchall(s): return []

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
