"""lpf-1 (lol/lpf.py, lol/lpf_data.py, lol/lpf_eval.py): no look-ahead in features or the baseline reconstruction, the
distribution arithmetic, the scope settlement rules, and that nothing live imports the research modules."""
import ast
import math
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from lol import lpf, lpf_data as D, lpf_eval as E, model

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ a tiny history database (production's column names)
def _db(path, extra_days=()):
    """Two leagues (LA strong, LB weak), 4 teams x 5 players, best-of-3 series every 2 days for 60 days, then an international
    series on day 70; extra_days adds series on those days (to test that later games never move earlier features)."""
    con = sqlite3.connect(path)
    pcols = ("_row", "game_key", "player_key", "player", "player_id", "team", "team_id", "opponent", "opponent_id", "side", "role",
             "champion", "kills", "deaths", "assists", "cs", "gold", "team_kills", "opp_kills", "win", "duration_s", "match_key",
             "game_number", "best_of", "played_at", "league", "source", "damage", "vision")
    gcols = ("_row", "game_key", "source", "match_key", "game_number", "best_of", "played_at", "league", "tournament", "blue_team",
             "red_team", "blue_team_id", "red_team_id", "blue_kills", "red_kills", "winner", "winner_basis", "duration_s", "patch",
             "fetched_at")
    con.execute(f"CREATE TABLE hist_player_games ({', '.join(pcols)})")
    con.execute(f"CREATE TABLE hist_games ({', '.join(gcols)})")
    con.execute("CREATE TABLE center_c_series (sid, tournament_page, tournament, best_of, scheduled_utc, first_game_utc, n_games, "
                "score1, score2, ff, nullified)")
    con.execute("CREATE TABLE lp_Tournaments (skey, rev, event_time, gone_at, data)")
    con.execute("INSERT INTO lp_Tournaments VALUES ('Worlds', 1, NULL, NULL, '{\"Region\": \"International\", \"TournamentLevel\": \"Primary\"}')")
    rng = np.random.default_rng(5)
    roles = ("top", "jungle", "mid", "bot", "support")
    sched = []
    for d in range(1, 61, 2):
        sched.append((d, "a1", "a2", "LA"))
        sched.append((d, "b1", "b2", "LB"))
    sched.append((70, "a1", "b1", "World Championship"))
    for d in extra_days:
        sched.append((d, "a1", "b1", "World Championship"))
    rid = gid = 0
    base = 739000
    for si, (d, ta, tb, lg) in enumerate(sched):
        sid = f"s{si}"
        page = "Worlds" if lg == "World Championship" else lg
        con.execute("INSERT INTO center_c_series VALUES (?,?,?,?,?,?,?,?,?,?,?)", (sid, page, page, 3, None, None, 2, 2, 0, 0, 0))
        for gn in (1, 2):
            gid += 1
            gk = f"g{gid}"
            when = f"{__import__('datetime').date.fromordinal(base + d).isoformat()}T1{gn}:00:00Z"
            ka, kb = int(rng.poisson(18 if ta.startswith("a") else 14)), int(rng.poisson(12))
            awin = int(ka > kb)
            con.execute(f"INSERT INTO hist_games VALUES ({','.join('?' * len(gcols))})",
                        (gid, gk, "lp", sid, gn, 3, when, lg, page, ta, tb, ta, tb, ka, kb, ta if awin else tb, "official", 1800.0,
                         "26.1", when))
            for team, opp, tk, ok, win in ((ta, tb, ka, kb, awin), (tb, ta, kb, ka, 1 - awin)):
                for ri, role in enumerate(roles):
                    rid += 1
                    k = int(rng.binomial(tk, 0.2))
                    con.execute(f"INSERT INTO hist_player_games VALUES ({','.join('?' * len(pcols))})",
                                (rid, gk, f"{team}{ri}", f"{team}{ri}", f"p:{team}{ri}", team, team, opp, opp, "blue", role,
                                 f"c{ri}", k, int(rng.poisson(3)), int(rng.binomial(tk, 0.5)), 200, 10000, tk, ok, win, 1800.0, sid,
                                 gn, 3, when, lg, "lp", 15000, 30))
    con.commit()
    con.close()
    return path


@pytest.fixture()
def store(tmp_path):
    return D.Store(str(_db(tmp_path / "h.db")))


def test_scope_total_rules():
    t = {"games": {1: (2, 1, 3), 2: (4, 0, 5), 3: (1, 2, 2)}, "n_series": 3}
    assert D.scope_total(t, "kills", "1") == 2
    assert D.scope_total(t, "assists", "12") == 8
    assert D.scope_total(t, "kills", "123") == 7
    t2 = {"games": {1: (2, 1, 3), 2: (4, 0, 5)}, "n_series": 2}           # best-of-3 ended 2-0: Maps 1-3 = the 2 games
    assert D.scope_total(t2, "kills", "123") == 6
    t3 = {"games": {1: (2, 1, 3), 3: (1, 1, 1)}, "n_series": 3}           # missed game 2 (sub): not settleable for 12 / 123
    assert D.scope_total(t3, "kills", "12") is None and D.scope_total(t3, "kills", "123") is None


def test_features_never_see_the_match_day_or_later(tmp_path):
    a = D.Store(str(_db(tmp_path / "a.db")))
    b = D.Store(str(_db(tmp_path / "b.db", extra_days=(70, 71, 90))))      # more games ON and AFTER the target day
    Fa, Fb = lpf.Features(a, log=lambda *x: None), lpf.Features(b, log=lambda *x: None)
    ta = [t for t in D.targets(a) if t["day"] == a.match_day["s60"]]
    tb = {(t["match"], t["pid"]): t for t in D.targets(b)}
    assert ta
    for t in ta:
        fa, fb = Fa.target(t), Fb.target(tb[(t["match"], t["pid"])])
        for k in fa:
            assert (fa[k] == fb[k]) or (isinstance(fa[k], float) and math.isnan(fa[k]) and math.isnan(fb[k])), k


def test_live_baseline_uses_only_earlier_days_and_matches_model_project(tmp_path):
    a = D.Store(str(_db(tmp_path / "a.db")))
    b = D.Store(str(_db(tmp_path / "b.db", extra_days=(70, 80))))
    Ba, Bb = D.LiveBaseline(a, log=lambda *x: None), D.LiveBaseline(b, log=lambda *x: None)
    t = [t for t in D.targets(a) if t["match"] == "s60"][0]
    ra, pa = Ba.project(t)
    rb, pb = Bb.project(t)
    assert pa == pytest.approx(pb) and ra["kills"][0] == pytest.approx(rb["kills"][0])
    # lol-1 part equals model.project on the same inputs (deaths carry no lol-1f fit -> multiplier 1)
    pin = [x for x in Ba.pin[t["pid"]] if x[0] < t["day"]]
    tin = [x for x in Ba.tin[t["team"]] if x[0] < t["day"]]
    oin = [x for x in Ba.tin[t["opp"]] if x[0] < t["day"]]
    pj = model.project(pin, tin, oin, t["role"], t["day"], "1", "deaths", p_win=pa, league=t["league"])
    assert ra["deaths"][0] == pytest.approx(pj["lambda_game"]) and ra["deaths"][1] == 1.0


def test_mix_matches_live_negative_binomial():
    mx = lpf.Mix.single(np.array([4.3]), 5.4)
    for line in (2.5, 4.5, 6.0):
        po, push = mx.p_over(np.array([line]))
        o, u, p = model.probs(4.3, 5.4, line)
        assert po[0] == pytest.approx(o, abs=1e-6) and push[0] == pytest.approx(p, abs=1e-6)
    ys = np.arange(0, 80)
    tot = np.exp(lpf.Mix.single(np.full(80, 4.3), 5.4).logpmf(ys)).sum()
    assert tot == pytest.approx(1.0, abs=1e-6)


def test_mixture_weights_sum_to_one_and_bo3_paths():
    for p in (0.1, 0.5, 0.83):
        assert sum(w for w, _, _ in lpf.bo3_paths(p)) == pytest.approx(1.0)
    mx = lpf.Mix(np.array([[0.3, 0.7]]), np.array([[3.0, 6.0]]), np.array([[4.0, 8.0]]))
    assert mx.mean()[0] == pytest.approx(0.3 * 3 + 0.7 * 6)
    po, _ = mx.p_over(np.array([4.5]))
    o1 = model.probs(3.0, 4.0, 4.5)[0]
    o2 = model.probs(6.0, 8.0, 4.5)[0]
    assert po[0] == pytest.approx(0.3 * o1 + 0.7 * o2, abs=1e-6)


def test_pit_is_uniform_for_the_true_distribution():
    rng = np.random.default_rng(0)
    mu, size = np.full(20000, 4.0), 6.0
    y = rng.negative_binomial(size, size / (size + mu))
    pit = lpf.Mix.single(mu, size).pit(y, rng)
    h = np.histogram(pit, bins=10, range=(0, 1))[0] / len(y)
    assert np.abs(h - 0.1).max() < 0.01


def test_cluster_ci_resamples_series():
    v = np.array([1.0, 1.0, 0.0, 0.0])
    m, lo, hi = E.cluster_ci(v, np.array(["a", "a", "b", "b"]), B=500)
    assert m == 0.5 and lo <= m <= hi


def test_no_live_module_imports_research_modules():
    research = {"lpf", "lpf_data", "lpf_eval", "lp1"}
    live = [p for p in (ROOT / "lol").rglob("*.py") if p.stem not in research and "tests" not in p.parts]
    live += list(ROOT.glob("*.py"))
    for p in live:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom):
                mods = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                assert not ({x for x in m.split(".")} & research), f"{p.relative_to(ROOT)} imports {m}"
