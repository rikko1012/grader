"""ml_features.py - point-in-time features for the moneyline research model. Fixtures only (no network, no database)."""
import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pytest

import ml_eval as M
import ml_features as F

D0 = date(2026, 1, 5)


def league(n_days=260, seed=3, extra=()):
    """A small deterministic league: 6 teams (true strengths differ), 5 players each, team 3 swaps two players on day 200.
    Returns the from_rows inputs. extra: additional (mid, day offset, team a, team b, maps [(winner, a score, b score)])."""
    rnd = random.Random(seed)
    strength = {1: 1.0, 2: 0.6, 3: 0.2, 4: 0.0, 5: -0.4, 6: -0.8}
    teams = [(1, "Alpha", "DK"), (2, "Bravo", "RU"), (3, "Charlie", "US"), (4, "Delta", "BR"), (5, "Echo", None),
             (6, "Team Foxtrot", "EU")]
    matches, maps, players = [], [], []
    mapnames = ["Mirage", "Inferno", "Nuke", "Ancient", "Anubis", "Dust2", "Train"]
    mid = 1000
    sched = []
    for day in range(0, n_days, 2):
        a, b = rnd.sample(sorted(strength), 2)
        bo = 1 if rnd.random() < 0.3 else 3
        res = []
        wa = wb = 0
        while max(wa, wb) < (bo + 1) // 2:
            p = 1 / (1 + math.exp(-(strength[a] - strength[b])))
            w = a if rnd.random() < p else b
            wa += w == a
            wb += w == b
            res.append((w, 13 if w == a else rnd.randint(3, 11), 13 if w == b else rnd.randint(3, 11)))
        sched.append((mid, day, a, b, res, rnd.random() < 0.4))
        mid += 1
    for mid_, day, a, b, res in extra:
        sched.append((mid_, day, a, b, res, False))
    for mid_, day, a, b, res, lan in sched:
        d = D0 + timedelta(days=day)
        matches.append((mid_, d, "ESL Challenger League Europe Cup" if not lan else "IEM Cologne", lan, 0))
        for i, (w, sa, sb) in enumerate(res):
            mapid = 2 + (mid_ * 7 + i) % len(mapnames)
            nm = mapnames[mapid - 2]
            maps.append((mid_, a, mapid, sa, sb, nm))
            maps.append((mid_, b, mapid, sb, sa, nm))
            for t in (a, b):
                roster = [t * 100 + p for p in range(5)]
                if t == 3 and day >= 200:
                    roster = roster[:3] + [390, 391]
                for pid in roster:
                    players.append((mid_, t, pid, mapid, round(0.8 + 0.1 * (strength[t] + 1) + rnd.random() * 0.3, 3)))
        na = sum(1 for w, _, _ in res if w == a)
        maps.append((mid_, a, 0, na, len(res) - na, "All"))
        maps.append((mid_, b, 0, len(res) - na, na, "All"))
    events = [("IEM Cologne", True, "Cologne, Germany")]
    return teams, matches, maps, players, events


def snap(**kw):
    return F.Snapshot.from_rows(*league(**kw))


@pytest.fixture(scope="module")
def eng():
    return F.FeatureEngine(snap())


# ------------------------------------------------------------------ point in time
def test_features_identical_on_a_snapshot_cut_at_the_day(eng):
    full = snap()
    for day in (D0 + timedelta(days=121), D0 + timedelta(days=201), D0 + timedelta(days=250)):
        a = eng.features(1, 3, day, lan=True, bo=3, event="IEM Cologne")
        for cut_snap in (full.restrict(day), F.Snapshot.from_rows(*league(), before=day)):
            b = F.FeatureEngine(cut_snap).features(1, 3, day, lan=True, bo=3, event="IEM Cologne")
            assert set(a) == set(b)
            bad = [k for k in a if not F._same(a[k], b[k])]
            assert bad == []


def test_future_matches_never_change_features(eng):
    day = D0 + timedelta(days=150)
    base = eng.features(2, 4, day, bo=3)
    # a blow-out on the feature day itself and later: rows dated >= day are never read
    extra = [(9001, 150, 4, 2, [(4, 13, 0), (4, 13, 0)]), (9002, 151, 4, 2, [(4, 13, 0), (4, 13, 0)])]
    other = F.FeatureEngine(snap(extra=extra)).features(2, 4, day, bo=3)
    assert all(F._same(base[k], other[k]) for k in base)
    # ... while the same blow-out the day before does move them
    moved = F.FeatureEngine(snap(extra=[(9003, 149, 4, 2, [(4, 13, 0), (4, 13, 0)])])).features(2, 4, day, bo=3)
    assert moved["elo_diff"] < base["elo_diff"]


def test_verify_point_in_time_reports_no_mismatch(eng):
    full = snap()
    cases = [(1, 2, D0 + timedelta(days=d), {"bo": 3}) for d in (90, 180)]
    out = F.verify_point_in_time(eng, cases, lambda day: full.restrict(day))
    assert [c["mismatches"] for c in out] == [[], []]
    assert all(c["n_features"] > 60 for c in out)


def test_tune_k_uses_only_rows_before_the_month():
    s = snap()
    e = F.FeatureEngine(s)
    day = D0 + timedelta(days=240)
    k1 = e.tune_k(day)
    ms = day.replace(day=1)
    # rewrite every result in the asked month: the chosen K cannot change
    e2 = F.FeatureEngine(s.restrict(ms))
    assert e2.tune_k(day) == k1


def test_feature_day_is_the_earlier_of_cutoff_date_and_hltv_date():
    cut = datetime(2026, 10, 2, 0, 10, tzinfo=timezone.utc).timestamp()
    assert F.feature_day(cut) == date(2026, 10, 2)
    assert F.feature_day(cut, date(2026, 10, 1)) == date(2026, 10, 1)
    assert F.feature_day(cut, "2026-10-03") == date(2026, 10, 2)


# ------------------------------------------------------------------ ratings
def test_p_series():
    assert F.p_series(0.6, 1) == pytest.approx(0.6)
    assert F.p_series(0.6, 3) == pytest.approx(0.6 ** 2 * (3 - 2 * 0.6))
    assert F.p_series(0.6, 5) == pytest.approx(0.6 ** 3 * (1 + 3 * 0.4 + 6 * 0.16))
    assert F.p_series(0.6, 2) == pytest.approx(0.6)              # even best-of: no series formula


def test_elo_is_margin_aware_and_strength_ordered(eng):
    day = D0 + timedelta(days=255)
    elos = {t: eng.elo(t, day, F.K_DEFAULT) for t in range(1, 7)}
    assert elos[1] > elos[3] > elos[6]
    two_0 = F.FeatureEngine(F.Snapshot.from_rows([(1, "A", None), (2, "B", None)],
                                                 [(1, D0, "E", False, 0)],
                                                 [(1, 1, 2, 13, 5, "Nuke"), (1, 2, 2, 5, 13, "Nuke"),
                                                  (1, 1, 3, 13, 5, "Ancient"), (1, 2, 3, 5, 13, "Ancient")], []))
    two_1 = F.FeatureEngine(F.Snapshot.from_rows([(1, "A", None), (2, "B", None)],
                                                 [(1, D0, "E", False, 0)],
                                                 [(1, 1, 2, 13, 5, "Nuke"), (1, 2, 2, 5, 13, "Nuke"),
                                                  (1, 1, 3, 5, 13, "Ancient"), (1, 2, 3, 13, 5, "Ancient"),
                                                  (1, 1, 4, 13, 5, "Inferno"), (1, 2, 4, 5, 13, "Inferno")], []))
    nxt = D0 + timedelta(days=1)
    assert two_0.elo(1, nxt, 24) - F.ELO_INIT == pytest.approx(24 * (2 - 2 * 0.5))
    assert two_1.elo(1, nxt, 24) - F.ELO_INIT == pytest.approx(24 * (2 - 3 * 0.5))
    assert two_0.elo(1, D0, 24) == F.ELO_INIT                     # same day: not visible yet


def test_glicko_rd_shrinks_with_games_and_grows_with_rest(eng):
    day = D0 + timedelta(days=200)
    r, rd, ns, nm = eng.glicko(1, day)
    assert ns > 10 and nm >= ns and rd < 150
    _, rd_late, _, _ = eng.glicko(1, day + timedelta(days=300))
    assert rd_late > rd
    assert eng.glicko(99, day) == (F.ELO_INIT, F.GLICKO_INIT_RD, 0, 0)


def test_roster_elo_starters_standin_and_stability(eng):
    st, n = eng.starters(3, D0 + timedelta(days=199))
    assert sorted(st) == [300, 301, 302, 303, 304]
    f = eng.features(3, 1, D0 + timedelta(days=205), bo=3)
    # 60-day "most maps" starters still are the old five; the latest lineup shows the two new players
    assert f["standin_a"] == 0 and f["stab30_a"] == 5
    assert f["lastlu_lt5_a"] == 2 and f["lastlu_overlap_a"] == 3
    f2 = eng.features(3, 1, D0 + timedelta(days=259), bo=3)
    assert sorted(eng.starters(3, D0 + timedelta(days=259))[0]) == [300, 301, 302, 390, 391]
    assert f2["stab90_a"] == 3                       # 3 of today's 5 were starters 90 days ago
    assert f2["standin_a"] == 0 and f2["n_starters_a"] == 5 and f2["lastlu_lt5_a"] == 0
    assert f["relo_diff"] is not None and f["relo_diff"] < 0


# ------------------------------------------------------------------ ml_eval parity (the five features)
def to_ml_eval(teams, matches, maps, players, events):
    h = M.Hltv()
    for tid, name, _ in teams:
        h.add_team(tid, name)
    for mid, d, ev, lan, ff in matches:
        h.matches[mid] = {"matchid": mid, "date": d, "event": ev, "lan": lan, "forfeited": ff, "source": "hltv",
                          "series": {}, "maps_won": defaultdict(int), "teams": set()}
    h.build(maps, [(mid, tid, pid, rating) for mid, tid, pid, mapid, rating in players])
    return h


def test_ml_eval_five_features_match_ml_eval(eng):
    h = to_ml_eval(*league())
    for day in (D0 + timedelta(days=d) for d in (60, 130, 205, 259)):
        for ta, tb in ((1, 2), (3, 5), (6, 4)):
            assert eng.mle_rating(ta, tb, day) == pytest.approx(h.f_rating(ta, tb, day))
            assert eng.mle_mappool(ta, tb, day) == pytest.approx(h.f_mappool(ta, tb, day))
            assert eng.mle_form(ta, tb, day) == pytest.approx(h.f_form(ta, tb, day))
            assert eng.mle_h2h(ta, tb, day) == pytest.approx(h.f_h2h(ta, tb, day))


# ------------------------------------------------------------------ market features
def test_market_features_cutoff_moves_and_exclusions():
    cut = 1_800_000_000
    pts = [(cut - 30 * 3600, 0.40), (cut - 24 * 3600 - 60, 0.45), (cut - 6 * 3600, 0.50), (cut - 600, 0.60),
           (cut, 0.62), (cut + 60, 0.99)]
    f, why = F.market_features(pts, cut)
    assert why is None and f["mkt_p"] == 0.62 and f["mkt_t"] == cut      # at or before the cutoff, never after
    assert f["mkt_p24"] == 0.45 and f["mkt_p6"] == 0.50
    assert f["mkt_mv24"] == pytest.approx(F.logit(0.62) - F.logit(0.45))
    assert f["mkt_max48"] == 0.62 and f["mkt_min48"] == 0.40 and f["mkt_npts48"] == 5
    assert F.market_features([], cut)[1] == "no price history"
    assert F.market_features([(cut + 1, 0.5)], cut)[1] == "no price history"
    assert F.market_features([(cut - 6 * 3600 - 1, 0.5)], cut)[1] == "no price before cutoff"
    assert F.market_features([(cut - 6 * 3600, 0.5)], cut)[0]["mkt_p"] == 0.5
    assert "resolved" in F.market_features([(cut - 60, 0.995)], cut)[1]
    f2, _ = F.market_features([(cut - 60, 0.5)], cut)
    assert f2["mkt_p24"] is None and f2["mkt_mv6"] is None


def test_live_features_equal_history_features(eng):
    start = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc).timestamp()
    live = F.live_features(eng, 1, 4, start, bo=3, event="IEM Cologne",
                           price_points=[(start - 3600, 0.7), (start - 1700, 0.75)])
    hist = eng.features(1, 4, date(2026, 9, 1), lan=True, bo=3, event="IEM Cologne")
    assert all(F._same(hist[k], live[k]) for k in hist)
    assert live["mkt_p"] == 0.7 and live["lan"] == 1                     # 1700 s before start is after the cutoff


# ------------------------------------------------------------------ text / regions
def test_bo_from_text():
    assert F.bo_from_text("Counter-Strike: A vs B (BO3) - X") == 3
    assert F.bo_from_text("best of five") == 5
    assert F.bo_from_text("(BO2)") == 2
    assert F.bo_from_text("Counter-Strike: A vs B") is None


def test_regions():
    assert F.team_region("RU") == "CIS" and F.team_region("EU") == "Europe" and F.team_region(None) is None
    assert F.event_region("ESL Challenger League Season 52 Europe Cup 9") == "Europe"
    assert F.event_region("CCT 2026 South America Series 7") == "South America"
    assert F.event_region("Whatever Cup", "Cologne, Germany") == "Europe"
    assert F.event_region("Whatever Cup", "Europe (Online) |") == "Europe"
    assert F.event_region("BLAST Open", "TBA") is None
    assert F.event_region("Europe vs North America Showmatch") is None        # ambiguous: unknown


def test_home_region_feature(eng):
    f = eng.features(1, 3, D0 + timedelta(days=100), lan=True, bo=3, event="IEM Cologne")
    assert f["event_region"] == "Europe" and f["home_diff"] == 1              # Alpha DK (Europe) vs Charlie US
    g = eng.features(1, 5, D0 + timedelta(days=100), bo=3, event="IEM Cologne")
    assert g["home_diff"] is None                                             # Echo has no country
