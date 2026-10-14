"""2026-10-14 audit: repeated-map records are resolved or quarantined, never counted twice; genuine rematches stay."""
import os, sys
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cs2_scout

D = (date.today() - timedelta(days=3)).isoformat()


def _s(mid, maps, kills, hltv=False, game0=100, opp="Rival"):
    out = []
    for n, ((mp, rounds, won), k) in enumerate(zip(maps, kills), 1):
        r = {"match_id": mid, "number": n, "date": D, "opponent": opp, "team": "X", "map": mp, "rounds": rounds, "won": won,
             "kills": k, "headshots": k // 2, "deaths": 15}
        if hltv:
            r.update(source="hltv", hltv_match=int(str(mid).split("-")[1]))
        else:
            r["game_id"] = game0 + n
        out.append(r)
    return out


def _last10(monkeypatch, b3, hl):
    pb = {"recent_maps": b3, "_older_maps": [], "player": {"nick": "x"}}
    monkeypatch.setattr(cs2_scout, "_hltv_series", lambda pb, back_days=None: [dict(r) for r in hl])
    rf = cs2_scout.recent_form(cs2_scout._form_rows(pb, "kills", keep_n=40, back_days=365), 0.7, 26.5)
    return sorted(s["total"] for s in rf["series"]), pb.get("quarantined_series") or {}


NM = [("de_nuke", 24, True), ("de_mirage", 26, True)]


def test_the_same_game_listed_twice_is_one_row(monkeypatch):
    b3 = _s(1, NM, (20, 21))
    b3 = b3 + [dict(b3[1])]                                    # BO3.gg repeats game 102 (same provider id)
    totals, q = _last10(monkeypatch, b3, [])
    assert totals == [41] and not q


def test_one_map_twice_with_different_records_is_quarantined_and_hltv_counts(monkeypatch):
    bad = _s(1, [("de_nuke", 24, True), ("de_nuke", 22, False)], (20, 15))      # two different games both "Nuke"
    totals, q = _last10(monkeypatch, bad, [])
    assert totals == [] and "one map twice" in q[1]           # never a made-up Maps 1-2 total
    hl = _s("hltv-7", NM, (20, 22), hltv=True)                 # HLTV has the real series: it counts, once
    totals, q = _last10(monkeypatch, bad, hl)
    assert totals == [42] and 1 in q


def test_genuine_same_day_rematch_with_identical_scores_stays_two_series(monkeypatch):
    b3 = _s(1, NM, (20, 21), game0=100) + _s(2, NM, (11, 12), game0=200)
    totals, q = _last10(monkeypatch, b3, [])                   # BO3.gg only: two provider series, both real
    assert totals == [23, 41] and not q
    hl = _s("hltv-1", NM, (20, 21), hltv=True) + _s("hltv-2", NM, (11, 12), hltv=True)
    totals, q = _last10(monkeypatch, b3, hl)                   # HLTV has both: paired, each once
    assert totals == [23, 41] and not q


def test_ambiguous_look_alikes_pair_only_on_complete_records(monkeypatch):
    b3 = _s(1, NM, (20, 21), game0=100) + _s(2, NM, (11, 12), game0=200)
    hl = _s("hltv-1", NM, (20, 21), hltv=True)                 # one HLTV series, identical kills to BO3.gg series 1
    totals, q = _last10(monkeypatch, b3, hl)
    assert totals == [41] and set(q) == {2}                   # 1 pairs on its complete record; 2 is a look-alike: quarantined
    assert "look-alike" in q[2]


def test_twins_never_guess_on_names_or_scorelines_alone():
    b3 = {1: _s(1, NM, (20, 21)), 2: _s(2, NM, (11, 12), game0=200)}
    hl = {"hltv-1": _s("hltv-1", NM, (19, 22), hltv=True)}
    q = {}
    assert cs2_scout._twins(hl, b3, q) == {} and set(q) == {1, 2}
