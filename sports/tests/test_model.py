"""Model agent tests: distributions vs known values, numpy frame helpers, point-in-time features, grade policy,
market rule mc-1, ledger budgets, the forward path end to end (forecast -> replay identical) and the API snapshot."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from sports import capabilities, identity, model_checks as mc, store
from sports.contracts import Membership, Player, SideQuote, Team
from sports.model import datasets, dists, featurize, forward, grade_policy, market, model, pipeline, study
from sports.model.frame import ew_prior, group_starts, gshift, roll_prior
from sports.tests import helpers as h

KS = np.arange(0, 80, dtype=float)


# ------------------------------------------------------------------------------------------------- distributions
def test_negbin_poisson_normal_match_math():
    r, m = 1 / 0.2, 11.3
    p = dists.negbin(np.array([m]), 0.2, 0, KS)[0]
    ref = [math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(r / (r + m))
                    + k * math.log(m / (r + m))) for k in range(80)]
    assert max(abs(a - b) for a, b in zip(p, ref)) < 1e-9          # support truncated at 79, renormalised
    q = dists.poisson(np.array([4.2]), 0, KS)[0]
    assert abs(q[3] - math.exp(-4.2) * 4.2 ** 3 / 6) < 1e-12
    assert max(abs(dists.erf(x) - math.erf(x)) for x in np.linspace(-6, 6, 241)) < 2e-7
    n = dists.normal(np.array([50.0]), np.array([100.0]), -20, np.arange(-20, 200, dtype=float))[0]
    assert abs(n.sum() - 1) < 1e-12 and abs((n * np.arange(-20, 200)).sum() - 50) < 0.05


def test_nb_loglik_matches_lgamma():
    y, mu, a = np.array([0, 3, 7, 12]), np.array([2.0, 4.0, 6.5, 9.0]), 0.3
    r = 1 / a
    ref = sum(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(r / (r + u))
              + k * math.log(u / (r + u)) for k, u in zip(y, mu))
    assert abs(dists.nb_loglik(y, mu, a) - ref) < 1e-9


def test_crps_and_pit_cover_on_a_known_case():
    p = np.zeros((1, 3))
    p[0] = [0.25, 0.5, 0.25]
    assert abs(dists.crps(p, 0, [1])[0] - (0.25 ** 2 + 0.25 ** 2)) < 1e-12
    # a calibrated discrete forecast: randomised-PIT coverage averages exactly the level
    cov = sum(w * dists.pit_cover(p, 0, [k], 0.5)[0] for k, w in enumerate(p[0]))
    assert abs(cov - 0.5) < 1e-12


def _pmf_dist(pmf, lo):
    return lambda line: dists.line_probs(pmf, lo, line)


SM1_FAMILIES = {
    "negbin points": (dists.negbin(np.array([24.0]), 0.08, 0, np.arange(0, 101, dtype=float))[0], 0),
    "poisson threes": (dists.poisson(np.array([1.4]), 0, np.arange(0, 21, dtype=float))[0], 0),
    "normal rec yds": (dists.normal(np.array([55.0]), np.array([900.0]), -20, np.arange(-20, 331, dtype=float))[0],
                       -20),
}


@pytest.mark.parametrize("name", sorted(SM1_FAMILIES))
def test_sm1_family_properties(name):
    pmf, lo = SM1_FAMILIES[name]
    lines = mc.standard_lines(float(round(float((pmf * np.arange(lo, lo + len(pmf))).sum()))))
    d = _pmf_dist(pmf, lo)
    assert mc.check_bounds_and_sum(d, lines) == []
    assert mc.check_push(d, lines) == []
    assert mc.check_monotonic(d, lines) == []


def test_mixtures_keep_physical_constraints():
    # receptions <= targets: a player with exactly <= 3 targets can never have 4 catches
    T = np.zeros((1, 26))
    T[0, :4] = 0.25
    B = model._binom_pmf_matrix(25, np.array([0.7]), 25)
    rec = np.einsum("rn,rnk->rk", T, B)[0]
    assert rec[4:].sum() < 1e-12 and abs(rec.sum() - 1) < 1e-12
    # threes made <= attempts and the minutes mixture's mean equals mu
    lo, ks = dists.grid("threes")
    mix = {"rho": [0.5, 1.0, 1.5], "alpha_3a": 0.1}
    p = model.mixture_pmf("threes", np.array([2.0]), {"p3": np.array([0.36])}, mix, lo, ks)[0]
    assert abs((p * ks).sum() - 2.0) < 0.02
    lo, ks = dists.grid("points")
    p = model.mixture_pmf("points", np.array([20.0]), {}, {"rho": [0.6, 1.0, 1.4], "alpha_c": 0.05}, lo, ks)[0]
    assert abs((p * ks).sum() - 20.0) < 0.01


def test_yards_mixture_is_skewed_and_sums_to_one():
    lo, ks = dists.grid("rec_yds")
    ctx = {"e_opp": np.array([7.0]), "p": np.array([0.65]), "unit_mean": np.array([11.0])}
    e = sorted(np.random.default_rng(1).gamma(2.0, 1.0, 400) - 2.0)
    e = list(np.asarray(e) / np.std(e))
    p = model.mixture_pmf("rec_yds", np.array([50.0]), ctx, {"alpha_opp": 0.1, "sigma": 6.0, "e": e}, lo, ks)[0]
    assert abs(p.sum() - 1) < 1e-9
    mean = (p * ks).sum()
    med = ks[np.searchsorted(np.cumsum(p), 0.5)]
    assert med < mean                                        # right skew kept (normal shape would not)


# ---------------------------------------------------------------------------------------------- frame helpers
def test_frame_helpers_use_prior_rows_only():
    keys = np.array(["a", "a", "a", "b", "b"])
    x = np.array([10.0, 20.0, 30.0, 5.0, 7.0])
    gs = group_starts(keys)
    assert list(gs) == [0, 0, 0, 3, 3]
    assert np.isnan(gshift(x, gs)[0]) and gshift(x, gs)[2] == 20.0 and np.isnan(gshift(x, gs)[3])
    m, w = ew_prior(x, gs, 1.0)                              # weights 1, 0.5 for the two prior rows of row 2
    assert abs(m[2] - (20 + 0.5 * 10) / 1.5) < 1e-12 and np.isnan(m[0]) and np.isnan(m[3])
    r = roll_prior(x, gs, 2, 1)
    assert r[2] == 15.0 and np.isnan(r[0]) and r[4] == 5.0


# ------------------------------------------------------------------------------------- synthetic ds-1 history
TEAMS = (("NY", "18"), ("BOS", "2"))


def _hist(tmp_path, n_games=40, seed=7):
    """A tiny ds-1 NBA history: NY vs BOS every 2 days, 8 players a side, deterministic random stats."""
    con = datasets.connect(tmp_path / "history.db")
    rng = np.random.default_rng(seed)
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2025, 11, 1, 0, 0, tzinfo=timezone.utc)
    for g in range(n_games):
        start = (t0 + timedelta(days=2 * g)).strftime("%Y-%m-%dT%H:%M:%SZ")
        gid = f"nba:espn:{9000 + g}"
        home, away = TEAMS if g % 2 == 0 else TEAMS[::-1]
        con.execute("INSERT INTO games (league, game_id, season, season_type, start_utc, home, away, status, source, "
                    "observed_at, known_at) VALUES ('nba',?,2026,'regular',?,?,?,'final','test',?,?)",
                    (gid, start, home[0], away[0], start, start))
        for side, (abbr, _) in enumerate(TEAMS):
            opp = TEAMS[1 - side][0]
            tot = {k: 0.0 for k in ("pts", "fga", "fta", "oreb", "tov", "min")}
            for j in range(8):
                pid = f"nba:player:espn:{side * 100 + j}"
                mins = float(max(0, rng.normal(30 - 2 * j, 4)))
                pts = float(rng.poisson(0.6 * mins)) if mins > 0 else 0.0
                fga = float(rng.poisson(0.4 * mins))
                con.execute("INSERT INTO nba_pg (game_id, player_id, name, team, opp, home, starter, played, position, "
                            "min, pts, fga, fta, fg3m, fg3a, reb, ast, tov, oreb) VALUES "
                            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (gid, pid, f"P{side}{j}", abbr, opp, 1 if abbr == home[0] else 0, 1 if j < 5 else 0, 1,
                             "G" if j % 2 else "F", mins, pts, fga, 2.0, float(rng.poisson(1.5)), 5.0,
                             float(rng.poisson(5)), float(rng.poisson(3)), 1.0, 1.0))
                for k, v in (("pts", pts), ("fga", fga), ("fta", 2.0), ("oreb", 1.0), ("tov", 1.0), ("min", mins)):
                    tot[k] += v
            con.execute("INSERT INTO nba_tg (game_id, team, opp, home, pts, fga, fta, oreb, tov, player_min) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)", (gid, abbr, opp, 1 if abbr == home[0] else 0, tot["pts"],
                                                         tot["fga"], tot["fta"], tot["oreb"], tot["tov"], tot["min"]))
    con.commit()
    return con


def test_features_are_point_in_time(tmp_path):
    con = _hist(tmp_path)
    a = featurize.nba_frame(con)
    # change the LAST game's box score: no feature of any earlier row may move
    con.execute("UPDATE nba_pg SET pts = 999, min = 48 WHERE game_id = 'nba:espn:9039'")
    con.commit()
    b = featurize.nba_frame(con)
    last = np.asarray(a["game_id"]) == "nba:espn:9039"
    for c in ("ew_min", "S_pts", "pace_l10", "allow_pts", "l10_min", "absent_share_lag"):
        x, y = a.f(c)[~last], b.f(c)[~last]
        assert np.allclose(np.nan_to_num(x, nan=-1), np.nan_to_num(y, nan=-1)), c


def test_as_of_cut_and_upcoming_rows(tmp_path):
    con = _hist(tmp_path)
    up = [{"game_id": "nba:espn:UP", "player_id": "nba:player:espn:0", "team": "NY", "opp": "BOS", "home": 1,
           "start_utc": "2026-01-15T00:00:00Z", "season": 2026, "season_type": "regular", "position": "F"}]
    df = featurize.nba_frame(con, upcoming=up, as_of="2025-12-01T00:00:00Z")
    i = np.flatnonzero(df["is_up"] == 1)
    assert len(i) == 1
    known = np.asarray(df["start_utc"])[df["is_up"] == 0]
    assert max(known) <= "2025-11-30T20:00:00Z"            # rows not public at as_of are cut
    assert np.isnan(df.f("y_points")[i[0]]) and df.f("prior_n")[i[0]] > 5
    assert np.isnan(df.f("absent_share")[i[0]])               # this game's absences are unknown, never "nobody"


# ---------------------------------------------------------------------------------------------- grade policy
def test_grade_policy_bands_states_and_unavailable_sides():
    g = grade_policy.apply(0.62, 0.38, ("over", "under"))
    assert g["band"] == "A" and g["grade"] is None and g["stake_units"] == 0.0
    assert g["label"] == grade_policy.TEXT_UNVALIDATED and "not forward-validated" in g["stake_reason"]
    g = grade_policy.apply(0.30, 0.70, ("over",))           # Under not offered -> Over is the only side
    assert g["chosen_side"] == "over" and g["band"] == "Lean" and g["sides"]["under"]["available"] is False
    assert g["sides"]["under"]["p"] is None
    g = grade_policy.apply(0.58, 0.42, ("over", "under"), "forward-validated", ("thin_history",), True)
    assert g["grade"] == "B" and g["stake_units"] == 0.0 and "thin_history" in g["stake_reason"]   # 0U keeps letter
    assert grade_policy.band(0.5345) == "C" and grade_policy.band(0.5344) == "Lean"


# ---------------------------------------------------------------------------------------------- market rule
def _pm(line, bid, ask, liq=500.0, observed_at="2026-10-23T22:50:00Z"):
    return h.offer(platform="polymarket", oid=f"polymarket:{line}", line=line, variant="main",
                   sides=(SideQuote("over", True, "exchange_yes_no", bid=bid, ask=ask),
                          SideQuote("under", True, "exchange_yes_no", bid=round(1 - ask, 4), ask=round(1 - bid, 4))),
                   liquidity=(("liquidity", liq),), observed_at=observed_at)


def test_market_rule_eligibility_consensus_and_leakage():
    ev = {"nba:espn:1": h.event()}
    tgt = h.offer(line=21.5)
    fat = "2026-10-23T22:55:00Z"
    quotes = [("k1", h.kalshi_rung(21.5, 0.55, 0.57, oid="kalshi:a", observed_at="2026-10-23T22:50:00Z"), None),
              ("p1", _pm(21.5, 0.50, 0.54), None),
              ("p2", _pm(22.5, 0.30, 0.60), None),                          # spread 0.30: not eligible
              ("t1", h.offer(platform="prizepicks", oid="pp:9", line=21.5), None)]   # target platform: never
    per, why = market.eligible_quotes(tgt, quotes, ev, fat, dnp_share=0.08)
    assert set(per) == {"kalshi", "polymarket"}
    assert abs(per["polymarket"][2] - 0.52 / 0.92) < 1e-12          # Polymarket NO includes DNP: conditional
    po, pu, pp, used = market.consensus(tgt, per)
    lk = (math.log(0.56 / 0.44) + math.log((0.52 / 0.92) / (1 - 0.52 / 0.92))) / 2
    assert abs(po - 1 / (1 + math.exp(-lk))) < 1e-9 and pp == 0 and used == ["kalshi", "polymarket"]
    stale = [("k1", h.kalshi_rung(21.5, 0.55, 0.57, oid="kalshi:a", observed_at="2026-10-23T22:00:00Z"), None)]
    assert market.eligible_quotes(tgt, stale, ev, fat, 0.0)[0] == {}
    inplay = [("p1", _pm(21.5, 0.50, 0.54, observed_at="2026-10-23T23:05:00Z"), None)]
    assert market.eligible_quotes(tgt, inplay, ev, "2026-10-23T23:06:00Z", 0.0)[0] == {}


def test_market_whole_line_is_unconditional_with_push_mass():
    tgt = h.offer(line=21.0)
    per = {"kalshi": ("k", 20.5, 0.55)}
    po, pu, pp, _ = market.consensus(tgt, per)
    assert pp > 0 and abs(po + pu + pp - 1) < 1e-12


def test_ledger_monthly_budget_refuses(monkeypatch):
    from sports.books import ledger
    monkeypatch.setitem(ledger.LIMITS, "oddsapi", {"monthly": 10, "reserve": 2, "gap_s": 0.0, "per_min": 100})
    assert ledger.reserve("oddsapi", 5)[0] is True
    ok, why = ledger.reserve("oddsapi", 5)
    assert ok is False and "monthly budget" in why
    assert ledger.usage()["oddsapi"]["used"] == 5


def test_polymarket_parser_reads_a_player_prop():
    from sports.books import sources
    ev = {"id": "1", "title": "Knicks vs. Celtics", "slug": "nba-nyk-bos-2026-10-23",
          "markets": [{"id": "6008962", "question": "Jayson Tatum: Points O/U 26.5", "outcomes": "[\"Yes\", \"No\"]",
                       "bestBid": 0.48, "bestAsk": 0.51, "line": 26.5, "gameStartTime": "2026-10-23 23:00:00+00",
                       "liquidityNum": 1240.4, "active": True, "closed": False, "acceptingOrders": True,
                       "sportsMarketType": "points", "updatedAt": "2026-10-23T22:34:37Z"},
                      {"id": "2", "question": "Knicks vs. Celtics: O/U 223.5", "sportsMarketType": "totals"}]}
    b = sources.polymarket_offers([ev], "nba", "2026-10-23T22:40:00Z", h.resolver())
    assert len(b.records) == 1
    o = b.records[0]
    assert (o.stat, o.line, o.platform, o.listed_start_utc) == ("points", 26.5, "polymarket", "2026-10-23T23:00:00Z")
    assert o.side("over").bid == 0.48 and abs(o.side("under").ask - 0.52) < 1e-9 and o.source_time_unknown
    assert o.event_id == "nba:espn:1" and o.player_id == "nba:player:espn:4065648"


def test_unverified_sources_are_capture_only_without_keys(monkeypatch):
    from sports.books import SOURCES, sources
    for k in ("THUNDERPICK_API_KEY", "ODDSPAPI_API_KEY", "ODDS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert sources.capture("thunderpick", "nba")["status"] == "key not set here"
    assert SOURCES["thunderpick"]["consensus"] is False and SOURCES["oddspapi"]["consensus"] is False
    assert sources.key_state()["oddsapi"] == "key not set here"


# ---------------------------------------------------------------------------------- forward path end to end
def _store_world(scon):
    for abbr, eid in TEAMS:
        store.put(scon, Team(team_id=identity.team_id("nba", eid), league="nba", abbr=abbr, name=abbr,
                             observed_at=h.T0, known_at=h.T0))
    for side, (abbr, eid) in enumerate(TEAMS):
        for j in range(8):
            pid = f"nba:player:espn:{side * 100 + j}"
            store.put(scon, Player(player_id=pid, league="nba", name=f"P{side}{j}", observed_at=h.T0, known_at=h.T0))
            store.put(scon, Membership(player_id=pid, team_id=identity.team_id("nba", eid),
                                       valid_from="2025-10-01T00:00:00Z", observed_at=h.T0, known_at=h.T0))
    store.put(scon, h.event(start="2026-02-01T00:00:00Z"))
    o = h.offer(line=17.5, player_id="nba:player:espn:0", observed_at="2026-01-31T20:00:00Z",
                listed_start_utc="2026-02-01T00:00:00Z")
    return store.put(scon, o)["record_id"]


def test_forward_forecast_replays_identically(tmp_path, monkeypatch):
    hcon = _hist(tmp_path)
    monkeypatch.setattr(datasets, "db_path", lambda: tmp_path / "history.db")
    art = tmp_path / "artifacts"
    art.mkdir()
    monkeypatch.setattr(forward, "ART_DIR", art)
    cfg = {"markets": {mk: {"market": mk, "half_lives": [5, 20], "features": ["nba.home"], "family": fam,
                            "cal": [1.0, 1.05]} for mk, fam in (("points", "negbin"), ("rebounds", "mixture"),
                                                                ("assists", "poisson"), ("threes", "ratio"))}}
    (art / "model_config_nba.json").write_text(json.dumps(cfg))
    a = forward.fit("nba", hcon, as_of="2026-01-31T20:00:00Z")
    assert a["markets"]["points"]["fit_rows"] > 100 and len(a["sha"]) == 16
    with store.opened(tmp_path / "canonical.db") as scon:
        rid = _store_world(scon)
        res = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert res["written"].get("sm-1") == 1, res
        fcs = [f for _, f in store.forecasts(scon, model_version="sm-1")]
        fc = fcs[0]
        assert mc.check_forecast(fc) == [] and fc.offer_snapshot_ids == (rid,)
        assert fc.research_only and fc.grade is None and fc.stake_units == 0.0
        assert "gp:unvalidated" in fc.reason_codes
        orig, again = forward.replay(fc.forecast_id, scon)
        assert again == orig                                 # identical id, probabilities, versions
        # the same offer forecast twice -> the same immutable record (duplicate, not a second id)
        res2 = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert res2["written"].get("sm-1") == 0
        snap = pipeline.write_snapshot(now="2026-01-31T21:30:00Z")
        assert snap["leagues"]["nba"]["forecasts"] >= 1
        # settlement -> scorecard + diagnostic queue, automatically and once; a correction supersedes
        from sports.contracts import Settlement
        from sports.model import learning
        offer = store.get(scon, rid)
        st = Settlement(settlement_id="s1", platform=offer.platform, offer_id=offer.offer_id, side=fc.chosen_side,
                        line=offer.line, event_id=offer.event_id, player_id=offer.player_id, stat="points",
                        status="win" if fc.chosen_side == "over" else "loss", outcome="over", source_value=40.0,
                        source="espn", source_ref="x", rule_version="test", settled_at="2026-02-01T04:00:00Z")
        store.put(scon, st)
        r1 = learning.on_settled(scon, now="2026-02-02T00:00:00Z")
        assert r1["scorecard_rows_added"] == 1
        assert learning.on_settled(scon, now="2026-02-02T00:00:00Z")["scorecard_rows_added"] == 0
        sc = learning.scorecard()
        assert sc[0]["model_version"] == "sm-1" and sc[0]["graded"] == 1
        assert any(d["kind"] == "large_miss" for d in learning.diagnostics())      # 40 points vs a 17.5 line


def test_api_model_reads_snapshots_only(tmp_path, monkeypatch):
    from sports import api_model, web
    d = tmp_path / "model" / "api"
    d.mkdir(parents=True)
    fid = "fc-" + "a" * 24
    (d / "pmf_nba.json").write_text(json.dumps({"written_at": "t", "pmf": {fid: {"lo": 0, "p": [0.2, 0.3, 0.5]}}}))
    (d / "forecasts_nba.json").write_text(json.dumps({"written_at": "t", "forecasts": [
        {"forecast_id": fid, "event_id": "e", "player_id": "p", "stat": "points", "model_version": "sm-1"}]}))
    req = web.Request(path="/api/sports/model/ladder", query={"league": "nba", "forecast_id": fid, "lines": "1,1.5"},
                      rest="ladder")
    code, body = api_model.model(req)
    assert code == 200 and body["lines"][0] == {"line": 1.0, "p_over": 0.5, "p_under": 0.2, "p_push": 0.3}
    assert body["lines"][1]["p_push"] == 0.0
    code, body = api_model.model(web.Request(path="x", query={"league": "nba", "stat": "points"}, rest="forecasts"))
    assert code == 200 and body["count"] == 1
    code, _ = api_model.model(web.Request(path="x", query={}, rest="registry"))
    assert code == 503                                       # snapshot not written yet -> explicit, never a guess
    assert "/api/sports/model/*" in web.ROUTES and web.ROUTES["/api/sports/admin/model/*"][1] == web.ADMIN


def test_nba_summary_parser_and_nfl_kickoff_dst():
    t = {"boxscore": {"players": [{"team": {"abbreviation": "BOS"}, "statistics": [{
        "keys": ["minutes", "points", "fieldGoalsMade-fieldGoalsAttempted",
                 "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "rebounds"],
        "athletes": [{"athlete": {"id": "1", "displayName": "A"}, "starter": True, "stats": ["30", "20", "7-15", "2-6",
                                                                                              "8"]},
                     {"athlete": {"id": "2", "displayName": "B"}, "didNotPlay": True, "reason": "ANKLE",
                      "stats": []}]}]}, {"team": {"abbreviation": "NY"}, "statistics": []}]},
         "header": {"competition": {"competitors": [{"homeAway": "home", "team": {"abbreviation": "BOS"}}]}}}
    players, teams, absent, odds = datasets.parse_nba_summary(t, "g")
    a = next(p for p in players if p["player_id"].endswith(":1"))
    assert (a["pts"], a["fgm"], a["fga"], a["fg3m"], a["home"], a["starter"], a["played"]) == (20, 7, 15, 2, 1, 1, 1)
    b = next(p for p in players if p["player_id"].endswith(":2"))
    assert b["played"] == 0 and b["dnp_reason"] == "ANKLE" and absent[0][3] == "ANKLE"
    assert datasets.nfl_start_utc("2025-09-07", "13:00") == "2025-09-07T17:00:00Z"      # EDT
    assert datasets.nfl_start_utc("2025-12-07", "13:00") == "2025-12-07T18:00:00Z"      # EST
