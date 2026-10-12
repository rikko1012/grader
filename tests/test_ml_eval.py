"""ml_eval.py - offline evaluation helpers. No network: every upstream answer is a fixture."""
import json
import math
import os
import sqlite3
from datetime import date, datetime, timezone

import pytest

import ml_eval as M


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


# ------------------------------------------------------------------ fixtures
def make_hltv(drop_from=None):
    """Teams 1 Alpha, 2 Bravo, 3 Alpha Academy, 4 Ninjas in Pyjamas; a few matches with maps and players."""
    h = M.Hltv()
    for tid, name in ((1, "Alpha"), (2, "Bravo"), (3, "Alpha Academy"), (4, "Ninjas in Pyjamas"), (5, "Team Charlie")):
        h.add_team(tid, name)
    days = {10: date(2026, 9, 1), 11: date(2026, 9, 5), 12: date(2026, 9, 10), 13: date(2026, 9, 10),
            14: date(2026, 9, 20), 15: date(2026, 9, 21)}
    teams = {10: (1, 2), 11: (1, 2), 12: (1, 4), 13: (1, 4), 14: (1, 2), 15: (1, 5)}
    for mid, d in days.items():
        h.matches[mid] = {"matchid": mid, "date": d, "event": "Ev", "lan": mid % 2 == 0, "forfeited": 0,
                          "source": "hltv", "series": {}, "maps_won": __import__("collections").defaultdict(int),
                          "teams": set()}
    h.matches[15]["forfeited"] = 1
    if drop_from:                       # the same data with nothing on or after drop_from
        for mid in [m for m, d in days.items() if d >= drop_from]:
            del h.matches[mid]
    maps, players = [], []
    # match 10: Alpha 2-0 Bravo; 11: Bravo 2-1 Alpha; 14 (future, after the scored match): Alpha 2-0
    results = {10: [(1, 1)] * 2, 11: [(2, 1), (2, 1), (1, 1)], 12: [(1, 1)], 13: [(4, 1)], 14: [(1, 1)] * 2,
               15: [(1, 1)]}
    for mid, res in results.items():
        if mid not in h.matches:
            continue
        a, b = teams[mid]
        won = {a: 0, b: 0}
        for i, (w, _) in enumerate(res):
            mapid = 2 + i
            maps.append((mid, a, mapid, 13 if w == a else 7, 7 if w == a else 13, f"Map{mapid}"))
            maps.append((mid, b, mapid, 13 if w == b else 7, 7 if w == b else 13, f"Map{mapid}"))
            won[w] += 1
            for t in (a, b):
                for p in range(5):
                    rating = 1.3 if (t == 1 and mid != 14) else (2.5 if mid == 14 else 0.9)
                    players.append((mid, t, t * 100 + p, rating))
        maps.append((mid, a, 0, won[a], won[b], "All"))
        maps.append((mid, b, 0, won[b], won[a], "All"))
    h.build(maps, players)
    return h


# ------------------------------------------------------------------ names / mapping
def test_team_ids_exact_variants_and_alias_never_fuzzy():
    h = make_hltv()
    assert h.team_ids("Alpha") == {1}
    assert h.team_ids("Team Alpha") == {1}                  # affix dropped, exact after that
    assert h.team_ids("Alpha Esports") == {1}
    assert h.team_ids("Charlie") == {5}                     # HLTV side has the "Team" prefix
    assert h.team_ids("Alpha Academy") == {3}               # exact wins: never the main team
    assert h.team_ids("Alpha Junior") == set()              # no fuzzy matching
    assert h.team_ids("NIP") == {4}                         # listed alias
    assert h.team_ids("Alph") == set()


def test_winner_from_maps_and_series_crosscheck():
    h = make_hltv()
    assert h.winner(10) == 1
    assert h.winner(11) == 2
    h.matches[10]["series"] = {1: 0, 2: 2}                  # series row contradicts the maps -> no result
    assert h.winner(10) is None


def test_find_match_unique_and_ambiguous():
    h = make_hltv()
    mid, a, b, why = h.find_match({2}, {1}, date(2026, 9, 6))     # +-1 day, either order
    assert (mid, a, b, why) == (11, 2, 1, None)
    assert h.find_match({1}, {4}, date(2026, 9, 10))[3] == "ambiguous HLTV match"
    assert h.find_match({1}, {2}, date(2026, 9, 14))[3].startswith("no HLTV match")


# ------------------------------------------------------------------ Polymarket helpers
def test_series_market_moneyline_only():
    ev = {"markets": [
        {"sportsMarketType": "child_moneyline", "question": "Map 1 Winner"},
        {"sportsMarketType": "moneyline", "question": "Counter-Strike: A vs B (BO3) - X", "id": "s"}]}
    m, how = M.series_market(ev)
    assert m["id"] == "s" and how == "moneyline"
    assert M.series_market({"markets": [{"sportsMarketType": "child_moneyline"}]}) == (None, "no series-winner market")
    old = {"markets": [{"question": "Counter-Strike: A vs B", "outcomes": '["A", "B"]', "id": "o"}]}
    assert M.series_market(old)[1] == "untyped single market"
    old_map = {"markets": [{"question": "Counter-Strike: A vs B - Map 1", "outcomes": '["A", "B"]'}]}
    assert M.series_market(old_map)[0] is None


def test_prematch_point_rules():
    start = ts("2026-10-01T18:00:00")
    cut = start - 1800
    hist = [{"t": cut - 7 * 3600, "p": 0.40}, {"t": cut - 600, "p": 0.55}, {"t": cut, "p": 0.57},
            {"t": cut + 600, "p": 0.60}, {"t": start + 600, "p": 0.90}]
    pt, why = M.prematch_point(hist, start)
    assert why is None and pt == {"t": int(cut), "p": 0.57}          # last at or before the cutoff
    assert M.prematch_point([], start)[1] == "no price history"
    assert M.prematch_point([{"t": start + 60, "p": 0.5}], start)[1] == "post-start points only"
    assert M.prematch_point([{"t": cut + 60, "p": 0.5}], start)[1] == "no price before cutoff"
    assert M.prematch_point([{"t": cut - 6 * 3600 - 1, "p": 0.5}], start)[1] == "no price before cutoff"   # > 6 h old
    assert M.prematch_point([{"t": cut - 6 * 3600, "p": 0.5}], start)[0]["p"] == 0.5
    assert "resolved" in M.prematch_point([{"t": cut - 60, "p": 0.995}], start)[1]


def test_market_winner_idx():
    assert M.market_winner_idx({"outcomePrices": '["0", "1"]'}) == 1
    assert M.market_winner_idx({"outcomePrices": '["0.4", "0.6"]'}) is None


def test_parse_ts_formats():
    assert M.parse_ts("2026-02-08 12:30:00+00") == ts("2026-02-08T12:30:00")
    assert M.parse_ts("2026-02-08T12:30:00Z") == ts("2026-02-08T12:30:00")
    assert M.parse_ts(None) is None and M.parse_ts("garbage") is None


# ------------------------------------------------------------------ Kalshi helpers
def test_kalshi_start_edt_est():
    r = "... CS2 match originally scheduled for Oct 11, 2026 at 3:30 AM EDT, then the market resolves to Yes."
    assert M.kalshi_start(r) == ts("2026-10-11T07:30:00")
    assert M.kalshi_start("originally scheduled for Dec 2, 2026 at 12:05 PM EST") == ts("2026-12-02T17:05:00")
    assert M.kalshi_start("no date here") is None


def test_kalshi_prematch_needs_tight_two_sided_book():
    start = ts("2026-10-01T18:00:00")
    cut = start - 1800

    def c(t, bid, ask):
        return {"end_period_ts": int(t), "yes_bid": {"close_dollars": f"{bid:.4f}"},
                "yes_ask": {"close_dollars": f"{ask:.4f}"}}
    pt, why = M.kalshi_prematch([c(cut - 120, 0.50, 0.54), c(cut, 0.60, 0.64), c(start + 60, 0.9, 0.95)], start)
    assert why is None and abs(pt["p"] - 0.62) < 1e-9
    assert "two-sided" in M.kalshi_prematch([c(cut, 0.0, 0.64)], start)[1]
    assert "two-sided" in M.kalshi_prematch([c(cut, 0.40, 0.64)], start)[1]
    assert M.kalshi_prematch([c(start + 60, 0.4, 0.45)], start)[1] == "post-start points only"


# ------------------------------------------------------------------ features: no future data
def test_features_use_only_rows_before_the_match_date():
    h = make_hltv()
    d = date(2026, 9, 20)                     # match 14 is ON this date: must not feed its own features
    before = (h.f_rating(1, 2, d), h.f_mappool(1, 2, d), h.f_form(1, 2, d), h.f_h2h(1, 2, d))
    # the same features from a database that never saw anything on or after that date
    h2 = make_hltv(drop_from=d)
    assert 14 not in h2.matches
    assert (h2.f_rating(1, 2, d), h2.f_mappool(1, 2, d), h2.f_form(1, 2, d), h2.f_h2h(1, 2, d)) == before
    # rating: Alpha's players at 1.3 (match 14's 2.5 must be ignored), Bravo's at 0.9; 60-day window, k = 20
    n_alpha = sum(1 for p, r in h.player_rows[100] if p < d)
    exp_a = (1.3 * n_alpha + 20) / (n_alpha + 20)
    n_bravo = sum(1 for p, r in h.player_rows[200] if p < d)
    exp_b = (0.9 * n_bravo + 20) / (n_bravo + 20)
    assert abs(before[0] - (exp_a - exp_b)) < 1e-9
    # h2h before 9-20: Alpha won match 10, lost 11, same rosters -> (1 - 1) / (2 + 3) = 0
    assert before[3] == (0.0, 2)
    # form: shrunk win rate over the series strictly before d
    alpha = [r for r in h.team_series[1] if r[0] < d]
    w = sum(1 for r in alpha if r[3])
    bravo = [r for r in h.team_series[2] if r[0] < d]
    wb = sum(1 for r in bravo if r[3])
    assert abs(before[2] - ((w + 2.5) / (len(alpha) + 5) - (wb + 2.5) / (len(bravo) + 5))) < 1e-9


def test_roster_is_most_recent_five():
    h = make_hltv()
    assert sorted(h.roster(1, date(2026, 9, 20))) == [100, 101, 102, 103, 104]
    assert h.roster(1, date(2026, 8, 1)) == []


# ------------------------------------------------------------------ scoring
def rows_synthetic(n=600, seed=3, effect=0.0):
    import random
    r = random.Random(seed)
    out = []
    for i in range(n):
        p = r.uniform(0.2, 0.8)
        x = r.gauss(0, 1)
        q = M.sigmoid(M.logit(p) + effect * x)
        out.append({"hltv_match": i, "start": 1_000_000 + i * 3600, "p": p, "y": int(r.random() < q),
                    "base_logit": M.logit(p), "x": x, "lan": i % 2 == 0, "volume": i})
    return out


def test_metrics_and_calibration():
    rows = [{"hltv_match": i, "p": 0.5, "y": i % 2} for i in range(10)]
    m = M.metrics(rows, boot=False)
    assert abs(m["logloss"] - math.log(2)) < 1e-4 and m["brier"] == 0.25 and m["n"] == 10
    cal = M.calibration(rows)
    assert cal[5]["n"] == 10 and cal[5]["observed"] == 0.5
    lo, hi = M.wilson(5, 10)
    assert lo < 0.5 < hi


def test_group_bootstrap_resamples_matches_not_rows():
    rows = [{"hltv_match": 1, "v": 1.0}] * 50 + [{"hltv_match": 2, "v": 0.0}]
    ci = M.group_bootstrap(M._groups(rows), lambda rs: sum(r["v"] for r in rs) / len(rs), B=300)
    assert ci[0] == 0.0                     # a resample of only match 2 exists -> lower bound 0


def test_chrono_split_keeps_matches_together_and_in_order():
    rows = rows_synthetic(100)
    rows += [dict(rows[59], start=rows[59]["start"] + 10)]      # second observation of match 59
    dev, val, test = M.chrono_split(rows)
    assert {r["hltv_match"] for r in dev} & {r["hltv_match"] for r in val} == set()
    assert max(r["start"] for r in dev) <= min(r["start"] for r in val)
    assert max(r["start"] for r in val) <= min(r["start"] for r in test)
    assert sum(1 for r in dev if r["hltv_match"] == 59) in (0, 2) and len({r["hltv_match"] for r in dev}) == 60


def test_fit_b_recovers_effect_and_l2_shrinks():
    rows = rows_synthetic(3000, effect=0.6)
    b = M.fit_b(rows, "x", 0.0)
    assert 0.45 < b < 0.75
    assert abs(M.fit_b(rows, "x", 1e7)) < 0.01


def test_test_feature_promotes_real_signal_not_noise():
    rows = rows_synthetic(4000, effect=0.5)
    dev, val, _ = M.chrono_split(rows)
    res = M.test_feature(dev, val, "x")
    assert res["promoted"] and res["val_gain_ci"][0] > 0
    rows = rows_synthetic(4000, effect=0.0, seed=11)
    dev, val, _ = M.chrono_split(rows)
    assert not M.test_feature(dev, val, "x")["promoted"]


def test_edge_returns_arithmetic():
    rows = [{"hltv_match": 1, "p": 0.40, "m": 0.46, "y": 1},     # +6 pts on A, A wins at 0.41 cost
            {"hltv_match": 2, "p": 0.60, "m": 0.55, "y": 1}]     # +5 pts on B (0.45 vs 0.40), B loses
    out = {e["threshold_pts"]: e for e in M.edge_returns(rows, "m")}
    assert out[6]["bets"] == 1 and abs(out[6]["net_units"] - (1 / 0.41 - 1)) < 1e-3
    assert out[5]["bets"] == 2 and abs(out[5]["net_units"] - (1 / 0.41 - 2)) < 1e-3
    assert out[3]["bets"] == 2


# ------------------------------------------------------------------ http cache + inventory end to end
class FakeResp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, timeout=None):
        assert headers and headers.get("User-Agent") == "cs2-scout/1.0" and timeout
        self.calls.append((url, params))
        for key, body in self.routes.items():
            if key in url:
                return FakeResp(body(params) if callable(body) else body)
        return FakeResp(None, 404)


def test_fetcher_caches_and_works_offline(tmp_path):
    s = FakeSession({"x": {"ok": 1}})
    f = M.Fetcher(str(tmp_path), rate=1000, session=s)
    assert f.get_json("https://h/x", {"a": 1}) == {"ok": 1}
    assert f.get_json("https://h/x", {"a": 1}) == {"ok": 1}
    assert len(s.calls) == 1
    off = M.Fetcher(str(tmp_path), offline=True)
    assert off.get_json("https://h/x", {"a": 1}) == {"ok": 1}
    with pytest.raises(RuntimeError):
        off.get_json("https://h/x", {"a": 2})


def test_build_inventory_end_to_end(tmp_path):
    h = make_hltv()

    def ev(i, a, b, start, prices='["1", "0"]'):
        return {"id": str(i), "endDate": start, "volume": 100 * i, "markets": [
            {"sportsMarketType": "child_moneyline", "question": "Map 1 Winner", "outcomes": json.dumps([a, b])},
            {"sportsMarketType": "moneyline", "id": f"m{i}", "question": f"Counter-Strike: {a} vs {b} (BO3)",
             "outcomes": json.dumps([a, b]), "clobTokenIds": json.dumps([f"tok{i}", f"tokB{i}"]),
             "gameStartTime": start, "outcomePrices": prices, "volumeNum": 100 * i}]}
    events = [ev(1, "Alpha", "Bravo", "2026-09-01 18:00:00+00"),         # ok, Alpha won
              ev(2, "Team Bravo", "Alpha", "2026-09-05 18:00:00+00", '["1", "0"]'),  # ok, Bravo won
              ev(3, "Alpha", "Unknown Five", "2026-09-07 18:00:00+00"),  # unresolved
              ev(4, "Alpha", "NIP", "2026-09-10 18:00:00+00"),           # ambiguous (two matches that day)
              ev(5, "Alpha", "Charlie", "2026-09-21 18:00:00+00"),       # forfeit
              ev(6, "Alpha", "Bravo", "2026-09-20 18:00:00+00", '["0", "1"]')]   # PM resolution disagrees
    hist = {"tok1": [{"t": ts("2026-09-01T17:20:00"), "p": 0.62}, {"t": ts("2026-09-01T18:10:00"), "p": 0.9}],
            "tok2": [{"t": ts("2026-09-05T18:10:00"), "p": 0.7}]}               # post-start only

    s = FakeSession({"/events/keyset": {"events": events, "next_cursor": None},
                     "/prices-history": lambda p: {"history": hist.get(p["market"], [])}})
    f = M.Fetcher(str(tmp_path), rate=1000, session=s)
    inv = M.build_inventory(f, h)
    assert [o["hltv_match"] for o in inv["eligible"]] == [10]
    o = inv["eligible"][0]
    assert o["p"] == 0.62 and o["y"] == 1 and o["hltv_a"] == 1
    ex = inv["excluded"]
    assert ex["post-start points only"] == 1 and ex["unresolved team mapping"] == 1
    assert ex["ambiguous HLTV match"] == 1 and ex["forfeit"] == 1
    assert ex["Polymarket resolution disagrees with HLTV winner"] == 1
    assert inv["unresolved_names"] == [("Unknown Five", 1)]
    # only series-winner outcome-0 tokens were ever priced (never the map market)
    assert {p["market"] for u, p in s.calls if "prices-history" in u} == {"tok1", "tok2"}


# ------------------------------------------------------------------ forward cohort
def test_forward_rows_latest_usable_series_snapshot(tmp_path):
    db = tmp_path / "ml_snapshots.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE snapshots (source TEXT, scope TEXT, team_a TEXT, team_b TEXT, p_a REAL, "
                "start_ts INTEGER, captured_at INTEGER)")
    st = int(ts("2026-10-12T18:00:00"))
    con.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)", [
        ("polymarket", "series", "Alpha", "Bravo", 0.55, st, st - 7200),
        ("polymarket", "series", "Alpha", "Bravo", 0.58, st, st - 1800),     # latest usable (exactly 30 min)
        ("polymarket", "series", "Alpha", "Bravo", 0.70, st, st - 600),      # too late
        ("polymarket", "map", "Alpha", "Bravo", 0.20, st, st - 3600),        # map scope: never series
        ("kalshi", "series", "Alpha", "Bravo", 0.57, st, st - 3600)])
    con.commit()
    con.close()
    rows, why = M.forward_rows(str(db))
    assert why is None
    got = {r["source"]: r["p"] for r in rows}
    assert got == {"polymarket": 0.58, "kalshi": 0.57}
    assert M.forward_rows(str(tmp_path / "missing.db"))[0] is None


def test_forward_eval_scores_against_hltv(tmp_path):
    h = make_hltv()
    db = tmp_path / "ml_snapshots.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE s (source TEXT, team_a TEXT, team_b TEXT, p_a REAL, start_ts INTEGER, captured_at INTEGER)")
    st = int(ts("2026-09-01T18:00:00"))
    con.execute("INSERT INTO s VALUES ('polymarket', 'Alpha', 'Bravo', 0.6, ?, ?)", (st, st - 3600))
    con.commit()
    con.close()
    res = M.forward_eval(str(db), h)
    assert res["n"] == 1 and res["by_source"]["polymarket"]["overall"]["n"] == 1
    assert abs(res["by_source"]["polymarket"]["overall"]["logloss"] - (-math.log(0.6))) < 1e-4


def test_forward_reads_moneyline_schema(tmp_path):
    """The real moneyline.py schema: ml_forecasts (frozen baseline) and ml_quotes (per platform), hltv:<id> links."""
    moneyline = pytest.importorskip("moneyline")
    h = make_hltv()
    db = tmp_path / "ml_snapshots.db"
    con = sqlite3.connect(db)
    con.executescript(moneyline._SCHEMA)
    start = "2026-09-05T18:00:00Z"
    con.execute("INSERT INTO ml_forecasts (match_id, model_version, logged_ts, platform, prob_a, team_a, team_b, "
                "start, available) VALUES ('hltv:11', 'ml-baseline-1', '2026-09-05T17:00:00Z', 'kalshi', 0.4, "
                "'Alpha', 'Bravo', ?, 1)", (start,))
    con.execute("INSERT INTO ml_forecasts (match_id, model_version, logged_ts, platform, prob_a, team_a, team_b, "
                "start, available) VALUES ('hltv:11', 'ml-baseline-1', '2026-09-05T17:45:00Z', 'kalshi', 0.9, "
                "'Alpha', 'Bravo', ?, 1)", (start,))                     # 15 min before start: too late
    con.execute("INSERT INTO ml_quotes (platform, market_id, outcome_id, received_ts, match_id, usable, start, source, "
                "scope, team_a, team_b, prob_a, captured_at) VALUES ('polymarket', 'm', 'o', '2026-09-05T16:00:00Z', "
                "'hltv:11', 1, ?, 'polymarket', 'series', 'Alpha', 'Bravo', 0.45, '2026-09-05T16:00:00Z')", (start,))
    con.execute("INSERT INTO ml_quotes (platform, market_id, outcome_id, received_ts, match_id, usable, start, source, "
                "scope, team_a, team_b, prob_a, captured_at) VALUES ('kalshi', 'k', 'o', '2026-09-05T16:00:00Z', "
                "'hltv:11', 1, ?, 'kalshi', 'series', 'Alpha', 'Bravo', NULL, '2026-09-05T16:00:00Z')", (start,))
    con.commit()
    con.close()
    res = M.forward_eval(str(db), h)
    assert set(res["by_source"]) == {"baseline ml-baseline-1", "polymarket"}
    base = res["by_source"]["baseline ml-baseline-1"]["overall"]
    assert base["n"] == 1 and abs(base["logloss"] - (-math.log(0.6))) < 1e-4      # Bravo won match 11; p(Alpha)=0.4


class StubHltv:
    def f_rating(self, a, b, d):
        return 0.01

    def f_mappool(self, a, b, d):
        return None

    def f_form(self, a, b, d):
        return 0.0

    def f_h2h(self, a, b, d):
        return 0.0, 0


def test_staged_tests_scores_test_set_only_when_final(tmp_path):
    rows = rows_synthetic(300)
    for r in rows:
        r.update(date="2026-09-01", hltv_a=1, hltv_b=2)
    out = M.staged_tests([dict(r) for r in rows], StubHltv(), freeze_path=str(tmp_path / "f.json"))
    assert out["test"] is None and out["freeze"]["test_n"] == 60
    assert (tmp_path / "f.json").exists()
    assert out["features"]["d_lan_x_rating"]["status"].startswith("not tested")
    fin = M.staged_tests([dict(r) for r in rows], StubHltv(), freeze_path=str(tmp_path / "f.json"), final=True)
    assert fin["test"]["baseline"]["n"] == 60 and fin["test"]["scored_count"] == 1 and fin["test"]["rescore"] is False
    assert set(fin["test"]) - {"scored_count", "rescore", "scored_at"} == {"baseline", *fin["freeze"]["promoted"]}
    assert fin["features_missing_as_zero"]["b_mappool"] == 300
    assert fin["freeze"]["sha256"] == out["freeze"]["sha256"] and fin["freeze"]["verified_at_final"]


def _staged_rows(n=300):
    rows = rows_synthetic(n)
    for r in rows:
        r.update(date="2026-09-01", hltv_a=1, hltv_b=2)
    return rows


def test_final_without_a_freeze_is_refused(tmp_path):
    with pytest.raises(M.FreezeError, match="needs the freeze"):
        M.staged_tests(_staged_rows(), StubHltv(), freeze_path=str(tmp_path / "none.json"), final=True)
    with pytest.raises(M.FreezeError):
        M.staged_tests(_staged_rows(), StubHltv(), final=True)
    assert not (tmp_path / "none.json.scored.json").exists()


def test_existing_freeze_is_never_silently_rewritten(tmp_path):
    fp = str(tmp_path / "f.json")
    a = M.staged_tests(_staged_rows(), StubHltv(), freeze_path=fp)
    before = open(fp, encoding="utf-8").read()
    with pytest.raises(M.FreezeError, match="--refreeze"):
        M.staged_tests(_staged_rows(), StubHltv(), freeze_path=fp)
    assert open(fp, encoding="utf-8").read() == before
    b = M.staged_tests(_staged_rows(), StubHltv(), freeze_path=fp, refreeze=True)
    assert b["freeze"]["refrozen"] is True and b["freeze"]["previous_sha256"] == a["freeze"]["sha256"]
    assert json.load(open(fp, encoding="utf-8"))["refrozen"] is True


def test_final_aborts_when_the_split_or_decisions_moved(tmp_path):
    fp = str(tmp_path / "f.json")
    M.staged_tests(_staged_rows(), StubHltv(), freeze_path=fp)
    # the pinned split is rebuilt from match ids: a pinned match that is no longer eligible -> refuse
    with pytest.raises(M.FreezeError, match="no longer eligible"):
        M.staged_tests(_staged_rows()[:-5], StubHltv(), freeze_path=fp, final=True)
    # the same pinned matches, but the decisions moved (a feature that was promoted at the freeze is not any more,
    # e.g. HLTV back-filled the rows it reads) -> the recomputed sha differs -> refuse
    sig = rows_synthetic(1500, effect=0.8, seed=8)
    for i, r in enumerate(sig):
        r.update(date="2026-09-01", hltv_a=i, hltv_b=-1)

    class XHltv(StubHltv):
        def __init__(self, xs):
            self.xs = xs

        def f_rating(self, a, b, d):
            return self.xs.get(a, 0.0)
    fp2 = str(tmp_path / "f2.json")
    frozen = M.staged_tests([dict(r) for r in sig], XHltv({i: r["x"] for i, r in enumerate(sig)}), freeze_path=fp2)
    assert "a_rating" in frozen["freeze"]["promoted"]
    with pytest.raises(M.FreezeError, match="differ from the freeze"):
        M.staged_tests([dict(r) for r in sig], XHltv({}), freeze_path=fp2, final=True)
    assert not os.path.exists(fp2 + ".scored.json")              # refused before any test number existed
    # a tampered freeze file
    d = json.load(open(fp, encoding="utf-8"))
    d["core"]["promoted"] = ["a_rating"]
    json.dump(d, open(fp, "w", encoding="utf-8"))
    with pytest.raises(M.FreezeError, match="sha256"):
        M.staged_tests(_staged_rows(), StubHltv(), freeze_path=fp, final=True)


def test_second_final_needs_rescore_and_is_reported(tmp_path):
    fp = str(tmp_path / "f.json")
    M.staged_tests(_staged_rows(), StubHltv(), freeze_path=fp)
    one = M.staged_tests(_staged_rows(), StubHltv(), freeze_path=fp, final=True)
    assert one["test"]["scored_count"] == 1
    with pytest.raises(M.FreezeError, match="already scored 1 time"):
        M.staged_tests(_staged_rows(), StubHltv(), freeze_path=fp, final=True)
    two = M.staged_tests(_staged_rows(), StubHltv(), freeze_path=fp, final=True, rescore=True)
    assert two["test"]["scored_count"] == 2 and two["test"]["rescore"] is True
    res = {"generated_at": "x", "staged": two, "forward": {}, "notes": []}
    rep = M.render_report(res)
    assert "rescore #2" in rep and "touched once" not in rep
    res["staged"] = one
    assert "touched once" in M.render_report(res)


def test_non_final_results_file_has_no_test_rows(tmp_path):
    rows = _staged_rows()
    st = M.staged_tests(rows, StubHltv(), freeze_path=str(tmp_path / "f.json"))
    test_ids = {r["hltv_match"] for r in rows if r["split"] == "test"}
    assert len(test_ids) == st["freeze"]["test_n"] == 60
    out = tmp_path / "res.json"
    M.write_results({"generated_at": "x", "staged": st}, rows, str(out), final=False,
                    test_rows_path=str(tmp_path / "test_rows.json"))
    written = json.load(open(out, encoding="utf-8"))
    assert written["polymarket_rows"] and not {r["hltv_match"] for r in written["polymarket_rows"]} & test_ids
    assert not (tmp_path / "test_rows.json").exists()
    M.write_results({"generated_at": "x", "staged": st}, rows, str(out), final=True,
                    test_rows_path=str(tmp_path / "test_rows.json"))
    assert not {r["hltv_match"] for r in json.load(open(out, encoding="utf-8"))["polymarket_rows"]} & test_ids
    assert {r["hltv_match"] for r in json.load(open(tmp_path / "test_rows.json", encoding="utf-8"))} == test_ids


def test_l2_strength_is_chosen_inside_development_not_on_validation():
    rows = rows_synthetic(1500, effect=0.4, seed=5)
    dev, val, _ = M.chrono_split(rows)
    res = M.test_feature(dev, val, "x")
    assert "CV inside development" in res["lambda_chosen_by"]
    lam, cv = M.choose_lambda([dict(r, _x=r["x"]) for r in dev], "_x")
    assert res["chosen_lambda"] == lam
    # scrambling validation outcomes cannot move the chosen strength (it never looks at validation)
    flipped = [dict(r, y=1 - r["y"]) for r in val]
    assert M.test_feature([dict(r) for r in dev], flipped, "x")["chosen_lambda"] == lam
    assert all("val_logloss" not in g or g["lambda"] == lam for g in res["grid"])


def test_display_rules_log2_roi_ci_and_coin_flips():
    rows = [{"hltv_match": i, "p": 0.5, "y": i % 2} for i in range(4)] + [{"hltv_match": 9, "p": 0.7, "y": 1}]
    m = M.metrics(rows, boot=False)
    assert m["logloss_50"] == math.log(2) and f"{m['logloss_50']:.4f}" == "0.6931"
    assert m["coin_flips"] == 4 and m["n_fav"] == 1 and m["fav_won"] == 1
    bins = M.by_fav_range(rows)
    assert bins[0]["n"] == 0 and bins[2]["n"] == 1 and bins[-1]["coin_flip"] and bins[-1]["n"] == 4
    one = [{"hltv_match": 1, "p": 0.40, "m": 0.50, "y": 1}]
    e = {x["threshold_pts"]: x for x in M.edge_returns(one, "m")}
    assert e[6]["bets"] == 1 and e[6]["roi_ci95"] == (None, None) and M._fmt_ci(e[6]["roi_ci95"], True) == "n/a"
    many = [{"hltv_match": i, "p": 0.40, "m": 0.50, "y": i % 2} for i in range(M.MIN_BETS_CI)]
    assert M.edge_returns(many, "m")[0]["roi_ci95"][0] is not None


def test_features_never_use_rows_dated_after_the_utc_start():
    """HLTV dates the match a day after its UTC start (CET evening): rows dated ON the UTC start day may be series
    played after the price cutoff - the feature day is the earlier date."""
    h = make_hltv()
    start = ts("2026-09-19T23:30:00")                       # HLTV date of the scored match: 2026-09-20
    r = {"date": "2026-09-20", "start": int(start), "hltv_a": 1, "hltv_b": 2, "p": 0.5}
    M.add_features([r], h)
    assert r["feature_day"] == "2026-09-19"
    assert r["c_form"] == (h.f_form(1, 2, date(2026, 9, 19)) or 0.0)
    r2 = {"date": "2026-09-20", "start": int(ts("2026-09-20T15:00:00")), "hltv_a": 1, "hltv_b": 2, "p": 0.5}
    M.add_features([r2], h)
    assert r2["feature_day"] == "2026-09-20"


# ------------------------------------------------------------------ forward cohort: state at the cutoff, staleness, window
def _fwd_db(tmp_path, rows):
    db = tmp_path / "fwd.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE snaps (source TEXT, scope TEXT, team_a TEXT, team_b TEXT, p_a REAL, usable INTEGER, bo INTEGER, "
                "start_ts INTEGER, captured_at INTEGER)")
    con.executemany("INSERT INTO snaps VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return str(db)


def test_forward_uses_the_state_at_the_cutoff_with_a_6h_bound(tmp_path):
    from collections import Counter
    st = int(ts("2026-10-12T18:00:00"))
    cut = st - 30 * 60
    db = _fwd_db(tmp_path, [
        # A: available 3 h before the cutoff, then UNAVAILABLE (book thinned) 1 h before it -> excluded, never the old p
        ("polymarket", "series", "Alpha", "Bravo", 0.62, 1, 3, st, cut - 3 * 3600),
        ("polymarket", "series", "Alpha", "Bravo", 0.64, 0, 3, st, cut - 3600),
        # B: its only snapshot is 6.5 h before the cutoff -> stale
        ("polymarket", "series", "Charlie", "Delta", 0.55, 1, 3, st, cut - int(6.5 * 3600)),
        # C: available 1 h before the cutoff -> scored
        ("polymarket", "series", "Echo", "Foxtrot", 0.58, 1, 3, st, cut - 3600),
        # D: a best-of-2 -> excluded explicitly (not through a missing HLTV winner)
        ("polymarket", "series", "Golf", "Hotel", 0.51, 1, 2, st, cut - 3600)])
    excl = Counter()
    rows, why = M.forward_rows(db, excl)
    assert why is None
    assert [(r["team_a"], r["p"]) for r in rows] == [("Echo", 0.58)]
    assert excl == {"polymarket: unavailable at cutoff": 1, "polymarket: stale snapshot (> 6 h before cutoff)": 1,
                    f"polymarket: {M.BO2_EXCLUSION}": 1}
    res = M.forward_eval(db, make_hltv())
    assert res["excluded"]["polymarket: unavailable at cutoff"] == 1 and res["snapshots_usable"] == 1


def test_forward_cutoff_uses_the_earliest_start_of_the_match(tmp_path):
    """Kalshi's original start is earlier than Polymarket's moved one: a Polymarket quote taken after Kalshi's cutoff is
    in-play for the match and never scored."""
    db = tmp_path / "ml.db"
    moneyline = pytest.importorskip("moneyline")
    con = sqlite3.connect(db)
    con.executescript(moneyline._SCHEMA)
    for plat, start, at, p in (("kalshi", "2026-09-05T18:00:00Z", "2026-09-05T16:00:00Z", 0.41),
                               ("polymarket", "2026-09-05T20:00:00Z", "2026-09-05T19:00:00Z", 0.30)):
        con.execute("INSERT INTO ml_quotes (platform, market_id, outcome_id, received_ts, match_id, usable, start, source, scope, "
                    "team_a, team_b, prob_a, captured_at, team_side) VALUES (?, 'm', 'o', ?, 'hltv:11', 1, ?, ?, 'series', "
                    "'Alpha', 'Bravo', ?, ?, 'a')", (plat, at, start, plat, p, at))
    con.commit()
    con.close()
    rows, _ = M.forward_rows(str(db))
    assert {r["source"]: r["p"] for r in rows} == {"kalshi": 0.41}


def test_forward_reports_matches_outside_the_loaded_hltv_window(tmp_path):
    db = tmp_path / "s.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE s (source TEXT, team_a TEXT, team_b TEXT, p_a REAL, start_ts INTEGER, captured_at INTEGER, "
                "match_id TEXT)")
    st = int(ts("2026-09-01T18:00:00"))
    con.execute("INSERT INTO s VALUES ('polymarket', 'Alpha', 'Bravo', 0.6, ?, ?, 'hltv:10')", (st, st - 3600))
    con.commit()
    con.close()
    narrow = make_hltv()
    narrow.first_date = date(2026, 9, 15)                  # a 'recent' copy that starts after the snapshot's match
    del narrow.matches[10]
    res = M.forward_eval(str(db), narrow)
    assert res["excluded"] == {"polymarket: outside loaded HLTV window": 1} and res["n"] == 0
    oldest, ids = M.forward_span(str(db))
    assert oldest == st and ids == [10]
    wide = make_hltv()                                     # widened to the snapshot span: the row is kept
    wide.first_date = date(2026, 8, 30)
    res = M.forward_eval(str(db), wide)
    assert res["n"] == 1 and not res["excluded"]
