"""A7 / A7.1 (owner 2026-10-19): the factor features are pre-match only, live uses the tested code, kills / assists carry the
fitted factors (lol-1f), a stat without a passing fit keeps lol-1 with the factors shown at weight 0, and the team's market
win chance is saved with the forecast (never an input). Every team, player and number is a TEST FIXTURE."""
import json
import math
from datetime import date

from lol import factors
from lol.center import forecast
from lol.tests.test_consolidation import _future_world  # noqa: F401 - fixture helper
from lol.tests.test_consolidation import con  # noqa: F401 - pytest fixture

D0 = date(2026, 5, 10).toordinal()


def _rows():
    out = []
    for i, d in enumerate((D0 - 30, D0 - 20, D0 - 10, D0, D0 + 3)):          # the last two: ON and AFTER the match day
        iso = date.fromordinal(d).isoformat() + "T12:00:00Z"
        for team, opp, base in (("lol:team:1", "lol:team:2", 0), ("lol:team:2", "lol:team:1", 10)):
            for j, role in enumerate(("top", "jungle", "mid", "bot", "support")):
                big = 30 if d >= D0 else 0                                    # a huge game on / after the day
                out.append({"game_key": f"g{i}", "match_key": f"m{i}", "game_number": 1, "played_at": iso,
                            "player_id": f"lol:player:{base + j + (100 if i == 2 and team == 'lol:team:2' and role == 'mid' else 0)}",
                            "team_id": team, "opponent_id": opp, "team": team, "opponent": opp, "role": role, "champion": f"c{i}{j}",
                            "kills": 3 + big, "deaths": 2, "assists": 5, "team_kills": 15 + big, "opp_kills": 10, "duration_s": 1800,
                            "damage": 15000 + 1000 * j, "cs": 250, "vision": 40, "win": int(team == "lol:team:1"), "league": "LCK"})
    return out


def _games():
    return {f"g{i}": {"patch": "26.9" if i < 2 else "26.10", "tournament": "LCK 2026 Playoffs", "league": "LCK",
                      "played_at": date.fromordinal(d).isoformat() + "T12:00:00Z"} for i, d in enumerate((D0 - 30, D0 - 20, D0 - 10, D0, D0 + 3))}


CONSTS = {"len_avg": 31.0, "ckpm_avg": 0.8, "role": {r: {"kills": 3.0, "deaths": 3.0, "assists": 6.0, "kp": 0.6, "kill_share": 0.2,
                                                       "death_share": 0.2, "dmg_share": 0.2, "dpm": 500.0, "cspm": 8.0, "vspm": 1.3}
                                                   for r in ("top", "jungle", "mid", "bot", "support")}}


def test_features_use_only_games_before_the_match_day_and_the_last_fielded_lineup():
    ix = factors.Index(_rows(), _games())
    f = ix.features("lol:player:2", "lol:team:1", "lol:team:2", "mid", D0, "LCK", 0.6, "LCK 2026 Playoffs", CONSTS)
    rows_before = [r for r in _rows() if r["played_at"] < date.fromordinal(D0).isoformat()]
    g = factors.Index(rows_before, _games()).features("lol:player:2", "lol:team:1", "lol:team:2", "mid", D0, "LCK", 0.6,
                                                     "LCK 2026 Playoffs", CONSTS)
    assert f == g                                                            # the match day's and later games change nothing
    assert ix.last_lineup("lol:team:2", D0)["mid"] == "lol:player:112"     # the lineup of the team's LAST game before the day
    assert f["playoffs"] == 1.0 and f["lg_lck"] == 1.0 and f["lane_missing"] == 0.0
    assert f["patch_share"] == 1 / 3 and f["no_history"] == 0.0             # current patch 26.10: one of his three games


def test_adjust_is_exp_of_the_standardised_sum():
    fit = {"beta": {"kp": 0.1, "rest": -0.2}, "mean": {"kp": 0.0, "rest": 1.0}, "sd": {"kp": 2.0, "rest": 0.5}}
    m, c = factors.adjust({"kp": 1.0, "rest": 2.0}, fit)
    assert math.isclose(m, math.exp(0.1 * 0.5 - 0.2 * 2.0)) and set(c) == {"kp", "rest"}
    assert factors.adjust({"kp": 1.0}, None) == (1.0, {})


def _fit_for(stats):
    fs = {"beta": {k: 0.0 for k in factors.FEATS}, "mean": {k: 0.0 for k in factors.FEATS}, "sd": {k: 1.0 for k in factors.FEATS}}
    fs["beta"]["no_history"] = 0.1                                        # a visible, known effect
    return {"version": "lol-1f", "consts": CONSTS, "stats": {s: (fs if s in stats else None) for s in ("kills", "assists", "deaths")}}


def test_kills_carry_the_factors_and_the_market_win_chance_is_saved(con, tmp_path, monkeypatch):  # noqa: F811
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    monkeypatch.setattr(forecast, "factor_fit", lambda: _fit_for({"kills"}))
    forecast._FF.update(index=None, index_key=None)
    seen = []
    market = lambda team, opp, st: seen.append((team, opp)) or {"found": True, "team_prob": 0.61, "source": "midpoint", "liquidity": 1200,
                                                                 "flags": [], "market_id": "pm1", "retrieved_at": "2026-10-19T10:00:00+00:00"}
    assert forecast.run(con, db, market=market)["saved"] == 1
    f = dict(con.execute("SELECT * FROM lol_forecasts ORDER BY id DESC").fetchone())
    inp = json.loads(f["inputs"])
    assert f["stat"] == "kills" and f["model_version"] == "lol-1f"
    F = inp["factors"]
    assert F["model"] == "lol-1f" and math.isclose(f["lambda"], round(inp["lambda_lol1"] * F["multiplier"], 4), abs_tol=2e-4)
    assert {r["key"] for r in F["factors"]} == set(factors.FEATS) and F["not_pre_match"]
    tw = inp["team_win"]
    assert tw["market"]["found"] and tw["market"]["p"] == 0.61 and tw["elo"] is not None and seen
    assert forecast.run(con, db).get("unchanged") == 1                     # a moving price alone never writes a new record


def test_a_stat_without_a_passing_fit_keeps_lol1_and_shows_weight_0(con, tmp_path, monkeypatch):  # noqa: F811
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    monkeypatch.setattr(forecast, "factor_fit", lambda: _fit_for({"assists"}))      # nothing for kills
    forecast._FF.update(index=None, index_key=None)
    forecast.run(con, db)
    f = dict(con.execute("SELECT * FROM lol_forecasts ORDER BY id DESC").fetchone())
    inp = json.loads(f["inputs"])
    assert f["model_version"] == "lol-1" and "lambda_lol1" not in inp
    assert inp["factors"]["model"] == "lol-1" and all(r["effect_pct"] is None for r in inp["factors"]["factors"])
    assert inp["team_win"]["market"] is None                               # no lookup supplied: nothing invented


def test_the_live_forecast_path_never_needs_scipy():
    """2026-10-19 production: the worker loaded the fit through lol.factor_study (scipy, not installed on the server) and
    every LoL cycle failed. The live path - forecast, factor fit, feature index - must import and run without scipy."""
    import subprocess
    import sys
    code = ("import builtins; real = builtins.__import__\n"
            "def block(name, *a, **k):\n"
            "    if name == 'scipy' or name.startswith('scipy.'): raise ModuleNotFoundError('scipy blocked')\n"
            "    return real(name, *a, **k)\n"
            "builtins.__import__ = block\n"
            "from lol.center import forecast\nfrom lol import factors\n"
            "f = forecast.factor_fit(); assert f and f['stats']['kills'] and f['consts']\n"
            "print('ok')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr
