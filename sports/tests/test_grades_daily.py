"""REGISTER A9a: the daily cal-mi fit (hysteresis), the daily grade check, data-driven containment (one source for
every surface), the committed seed (NBA + NFL contained from the owner-run gradecheck 2026-10-28) and the shown-chance
store the public results page scores."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from sports import board_view, config, containment, picks, results_public as rp, snapshots, store, worker
from sports.model import cal, daily, gradecheck as gc, shown
from sports.slips import pool
from sports.tests.test_gradecheck import World
from sports.tests.test_settle_results_link import _fake_espn, _world as _ledger_world

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "sports" / "model" / "artifacts" / "containment_default.json"
L10 = [5, 5, 0, 10]


def _git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=30)


@pytest.fixture(autouse=True)
def _fresh():
    cal.reset_cache()
    containment.reset_cache()
    yield
    cal.reset_cache()
    containment.reset_cache()


def _state(nba, nfl, nba_stats=()):
    p = containment.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": "ct-1", "leagues": {"nba": {"contained": nba, "stats": list(nba_stats)},
                                                            "nfl": {"contained": nfl, "stats": []}}}), encoding="utf-8")
    containment.reset_cache()


# ------------------------------------------------------------------------------------------------ registration
def test_a9a_registered_before_the_code():
    text = (ROOT / "sports" / "research" / "REGISTER.md").read_text(encoding="utf-8")
    a = text[text.index("Amendment A9a"):]
    for s in ("NBA letters CONTAINED and NFL letters CONTAINED", "1,634", "53.43%", "65.56%", "900", "58.78%",
              "67.83%", "2 CONSECUTIVE daily fails", "SPORTS_CAL_AUTOFIT=0", "SPORTS_CONTAIN_NBA=0|1",
              "model/gradecheck.json", "model/containment.json", "model/shown.db", "under 400 lines"):
        assert s in a, s
    assert daily.OFF_AFTER == 2 and daily.KEEP == 30 and daily.DAY_SHIFT_H == cal.DAY_SHIFT_H
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        pytest.skip("shallow clone (CI): commit order is not visible")
    reg = _git("log", "-S", "Amendment A9a", "--format=%H", "--", "sports/research/REGISTER.md").stdout.split()
    code = _git("log", "--diff-filter=A", "--format=%H", "--", "sports/model/daily.py").stdout.split()
    if not reg or not code:
        pytest.skip("git history not available (code not committed yet)")
    assert reg[-1] != code[-1] and _git("merge-base", "--is-ancestor", reg[-1], code[-1]).returncode == 0


# --------------------------------------------------------------------------------------------- seed + sources
def test_committed_seed_contains_both_leagues_with_the_owner_run_numbers(monkeypatch):
    monkeypatch.setattr(containment, "DEFAULT_PATH", SEED)
    containment.reset_cache()
    obj, src = containment.load(force=True)
    assert src == "default" and obj["leagues"]["nba"]["contained"] and obj["leagues"]["nfl"]["contained"]
    nba, nfl = [e for e in obj["log"] if e["league"] == "nba"][0], [e for e in obj["log"] if e["league"] == "nfl"][0]
    assert (nba["verdict"], nba["n"], nba["game_days"], nba["hit"], nba["stated"]) == ("contain", 1634, 3, 0.5343, 0.6556)
    assert (nfl["verdict"], nfl["n"], nfl["game_days"], nfl["hit"], nfl["stated"]) == ("insufficient", 900, 2, 0.5878, 0.6783)
    assert {e["source"] for e in obj["log"]} == {"owner-run gradecheck 2026-10-28"}
    # the A9 5 rule agrees with the seed: NBA hit more than 10 points under stated = contain
    assert nba["hit"] < nba["stated"] - gc.CONTAIN_TOL
    # NBA is contained on the board with the existing contained-league wording, like NFL
    x, _ = board_view.quote_x("nba", "points", 0.62, 0.38, 25.0, 20.5, {"po": 0.55, "pu": 0.45, "n": 3}, L10)
    assert x == {"b": "gate", "t": "NBA model not validated - projection only", "v": "vg-1"}
    assert board_view.contain_reason("nfl", "rec_yds") == "NFL model not validated - projection only"


@pytest.mark.parametrize("off", ["0", "off", "false", "no"])
def test_env_zero_never_releases_a_held_league(monkeypatch, off):
    """Owner absolute rule "Never force letters on" (ct-1a): SPORTS_CONTAIN_<LG>=0 no longer releases a league the daily
    check holds - it is logged as asked and ignored. Release comes only from the daily verdict."""
    _state(nba=True, nfl=True)
    assert containment.contained("nba", "points")
    monkeypatch.setenv("SPORTS_CONTAIN_NBA", off)
    monkeypatch.setenv("SPORTS_CONTAIN_NFL", off)
    containment.reset_cache()            # a restart on Render
    assert containment.contained("nba", "points") and containment.contained("nfl", "rec_yds")
    st = containment.league_state("nba")
    assert st["source"] == "file" and st["env_asked"] == "release" and st["env_applied"] is False
    snap = containment.snapshot()
    assert snap["env_requests"]["nba"] == {"name": "SPORTS_CONTAIN_NBA", "value": off, "asked": "release", "applied": False}
    assert snap["letters"] == {"nba": "contained", "nfl": "contained"}
    _state(nba=False, nfl=True)          # the daily verdict released NBA: the 0 is not what released it
    assert not containment.contained("nba", "points") and containment.contained("nfl", "rec_yds")


def test_env_request_is_logged_once(monkeypatch, capsys):
    _state(nba=True, nfl=True)
    monkeypatch.setenv("SPORTS_CONTAIN_NBA", "0")
    monkeypatch.setattr(containment, "_logged", set())
    containment.reset_cache()
    containment.contained("nba", "points")
    containment.reset_cache()
    containment.contained("nba", "points")
    out = capsys.readouterr().out
    assert out.count("SPORTS_CONTAIN_NBA='0' asked to release; applied: contained (source file)") == 1, out
    assert "the switch can only contain" in out


def test_env_one_contains_over_a_release(monkeypatch):
    _state(nba=False, nfl=False)
    assert not containment.contained("nba", "points")
    monkeypatch.setenv("SPORTS_CONTAIN_NBA", "1")
    containment.reset_cache()            # a restart on Render
    assert containment.contained("nba", "points") and containment.league_state("nba")["source"] == "env"
    assert not containment.contained("nfl", "rec_yds")                        # the other league keeps the file


def test_legacy_rule_when_no_state_exists():
    assert containment.league_state("nba")["source"] == "legacy"
    assert not containment.contained("nba", "points") and containment.contained("nfl", "rec_yds")


def test_decide_reads_the_a9_5_verdicts():
    d = containment.decide
    assert d({"contained": True}, "release") == {"contained": False, "stats": []}
    assert d({"contained": False}, "contain") == {"contained": True, "stats": []}
    assert d({"contained": False}, "contain:points,threes") == {"contained": False, "stats": ["points", "threes"]}
    assert d({"contained": True}, "contain:points") == {"contained": True, "stats": ["points"]}   # stays hidden
    assert d({"contained": True, "stats": ["x"]}, "keep") == {"contained": True, "stats": ["x"]}
    assert d({"contained": False}, "insufficient") == {"contained": False, "stats": []}
    assert d({"contained": False, "stats": ["points"]}, "release") == {"contained": False, "stats": []}


def test_stat_only_containment_hides_that_stat_only():
    _state(nba=False, nfl=True, nba_stats=["points"])
    assert containment.contained("nba", "points") and not containment.contained("nba", "rebounds")
    x, _ = board_view.quote_x("nba", "points", 0.62, 0.38, 25.0, 20.5, None, L10)
    y, _ = board_view.quote_x("nba", "rebounds", 0.62, 0.38, 9.0, 7.5, None, L10)
    assert x["b"] == "gate" and y.get("gr") == "A"


def test_every_surface_reads_the_one_source():
    """Board quote, board row, tracked-pick letter, slip leg, public results: NBA contained -> no letter anywhere,
    with the existing wording; released -> letters back, no 'not validated' text beside them."""
    fc = SimpleNamespace(p_over=0.62, p_under=0.38, model_version="sm-1", line=20.5, projection_mean=25.0,
                         stat="points", chosen_side="over", forecast_id="f1")
    row = {"key": "k", "event_id": "e", "player_id": "p", "player": "P", "position": None, "team": None,
           "stat": "points", "primary": True, "capability": None, "quotes": [], "kalshi_near": [], "kalshi_rungs": [],
           "alternates": [], "promotional": False}
    nba = [{"forecast_id": "b1", "model": "sm-1", "event_id": "nba:g1", "player_id": "nba:p1", "stat": "points",
            "line": 20.5, "p_over": 0.62, "p_under": 0.38, "status": "settled", "lean": "over", "lean_result": "win",
            "outcome": "over", "actual": 25.0, "forecast_at": "2026-10-20T10:00Z", "settled_at": "2026-10-21T05:00Z"}]
    for contained in (True, False):
        _state(nba=contained, nfl=True)
        x, _ = board_view.quote_x("nba", "points", 0.62, 0.38, 25.0, 20.5, None, L10)
        cr = snapshots.compact_row(row, "nba")
        g = picks.research_grade(fc, "over", "nba")
        leg = pool.guard_reason(fc, "nba")
        G, M = rp.grades(nba, "nba"), rp.model_gate("nba")
        if contained:
            assert x["b"] == "gate" and "gr" not in x
            assert cr["nv"] == "NBA model not validated - projection only"
            assert g is None and leg == "model not validated for this market: projection only"
            assert G["rows"] == [] and G["withheld"] == "NBA model not validated"
            assert M["contained"] is True and M["not_validated"] == "NBA model not validated"
        else:
            assert x["gr"] == "A" and "nv" not in x and "nv" not in cr
            assert g == "A" and leg is None
            assert G["rows"][0]["n"] == 1 and M["contained"] is False and M["not_validated"] is None


def test_released_nfl_keeps_edges_withheld_and_never_a_slip_leg():
    _state(nba=False, nfl=False)
    x, _ = board_view.quote_x("nfl", "rec_yds", 0.3, 0.7, 40.0, 50.5, {"po": 0.45, "pu": 0.55, "n": 3}, L10)
    assert x["gr"] == "A" and "e" not in x and "s" not in x and x["c"] is None and "nv" not in x
    f = SimpleNamespace(model_version="sm-1", stat="rec_yds", projection_mean=40.0, line=50.5, chosen_side="under",
                        forecast_id="f1")
    assert pool.guard_reason(f, "nfl").startswith("model not validated")     # failed gate: selection stays held


def test_apply_verdicts_logs_and_the_board_follows(tmp_path):
    res = {"leagues": {"nba": {"verdict": {"verdict": "contain", "n": 1634, "hit": 0.5343, "stated": 0.6556,
                                           "ci": [0.51, 0.56], "games": 40, "game_days": 3, "why": "w"}},
                       "nfl": {"verdict": {"verdict": "insufficient", "n": 900, "game_days": 2}}}}
    out = containment.apply_verdicts(res, "2026-10-29", "daily gradecheck 2026-10-29 (A9a 4)")
    assert out["nba"]["changed"] and out["nba"]["after"]["contained"] and not out["nfl"]["changed"]
    assert containment.contained("nba", "points") and containment.contained("nfl", "rec_yds")
    obj = json.loads(containment.path().read_text(encoding="utf-8"))
    e = [x for x in obj["log"] if x["league"] == "nba"][-1]
    assert (e["date"], e["n"], e["hit"], e["stated"], e["ci"], e["verdict"]) == (
        "2026-10-29", 1634, 0.5343, 0.6556, [0.51, 0.56], "contain")
    assert e["before"] == {"contained": False, "stats": []} and e["source"].startswith("daily gradecheck")
    res["leagues"]["nba"]["verdict"]["verdict"] = "release"
    containment.apply_verdicts(res, "2026-10-30", "daily")
    assert not containment.contained("nba", "points") and len(json.loads(containment.path().read_text())["log"]) == 4


# --------------------------------------------------------------------------------------------- cal-mi hysteresis
def test_hysteresis_on_at_first_pass_off_after_two_consecutive_fails():
    h = daily.hysteresis
    on = {"nba:points": {"on": True}}
    off = {"nba:points": {"on": False}}
    p, f = {"nba:points": {"on": True}}, {"nba:points": {"on": False}}
    assert h(p, off, {})["nba:points"] == {"on": True, "fails": 0, "passed": True, "before": False}   # ON at once
    d1 = h(f, on, {})["nba:points"]
    assert d1["on"] is True and d1["fails"] == 1                                # one fail: stays ON
    d2 = h(f, on, {"nba:points": 1})["nba:points"]
    assert d2["on"] is False and d2["fails"] == 0                               # second fail in a row: OFF
    assert h(p, on, {"nba:points": 1})["nba:points"]["fails"] == 0              # a pass resets the streak
    assert h(f, off, {"nba:points": 5})["nba:points"]["on"] is False            # never ON: a fail stays OFF


def test_due_once_per_game_day_after_settlement_only_when_something_new_settled():
    t = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)   # noqa: E731
    new = "2026-10-29T07:00:00Z"
    assert daily.due(t("2026-10-29T08:00"), {}, new) == (False, "before 10:00 UTC (overnight settlement)")
    assert daily.due(t("2026-10-29T10:30"), {}, new) == (True, None)
    assert daily.due(t("2026-10-29T15:00"), {"last_day": "2026-10-29"}, new)[1].startswith("already ran")
    assert daily.due(t("2026-10-30T11:00"), {"last_day": "2026-10-29", "data_as_of": new}, new)[1].startswith(
        "nothing new settled")
    assert daily.due(t("2026-10-30T11:00"), {"last_day": "2026-10-29", "data_as_of": new}, "2026-10-30T07:00:00Z")[0]
    assert daily.due(t("2026-10-30T11:00"), {}, None) == (False, "no settled props in learning.db")


# ------------------------------------------------------------------------------------------- the daily job e2e
def _world(n_days=3, games=10, per_game=6, win=lambda i: i % 25 < 12, balanced=False):
    (config.DATA_DIR / "model").mkdir(parents=True, exist_ok=True)
    w = World(config.DATA_DIR / "model")
    i = 0
    for d in range(n_days):
        start = f"2026-10-{23 + d:02d}T23:00:00Z"
        for g in range(games):
            for k in range(per_game):
                under = balanced and k % 2 == 1
                hit = win(i)
                if under:
                    w.prop(f"nba:espn:{d}{g}", 0.38, 0.62, mean=9, value=8 if hit else 13, start=start,
                           stat="points" if k < 3 else "rebounds")
                else:
                    w.prop(f"nba:espn:{d}{g}", 0.66 if not balanced else 0.62, 0.34 if not balanced else 0.38,
                           mean=12, value=14 if hit else 8, start=start, stat="points" if k < 3 else "rebounds")
                i += 1
    return w.close()


NOW = datetime(2026, 10, 26, 11, 0, tzinfo=timezone.utc)


def test_daily_run_contains_a_league_whose_letters_miss_and_logs_every_decision(monkeypatch):
    import numpy as np                                                          # noqa: F401 (warm import)
    _world()                                                                    # hit 48% vs stated 66%
    seen = {}
    real = gc.cal_walk_q

    def spy(rows, league):                                                      # gradecheck reads step 1's weights
        obj, src = cal.load(force=True)
        seen[league] = (src, (obj or {}).get("sha"))
        return real(rows, league)
    monkeypatch.setattr(gc, "cal_walk_q", spy)
    assert not containment.contained("nba", "points")                           # legacy start: NBA released
    out = daily.run(now=NOW)
    assert out["ran"] and out["game_day"] == "2026-10-26"
    written = json.loads(cal.weights_path().read_text(encoding="utf-8"))
    assert cal.validate(written) and written["source"].startswith("daily autofit")
    assert seen["nba"] == ("file", written["sha"])                              # same run, the new file
    logl = [json.loads(x) for x in (config.DATA_DIR / "model" / "cal_mi_log.jsonl").read_text().splitlines()]
    assert {x["market"] for x in logl} == {"nba:points", "nba:rebounds"} and all(x["date"] == "2026-10-26" for x in logl)
    assert all({"n", "verdict", "on_before", "on_after", "fails_in_a_row"} <= set(x) for x in logl)
    gtxt = (config.DATA_DIR / "model" / "gradecheck.json").read_text(encoding="utf-8")
    assert gtxt.count("\n") + 1 < daily.MAX_LINES
    assert (config.DATA_DIR / "model" / "gradecheck" / "gradecheck_2026-10-26.json").read_text() == gtxt
    v = out["headline"]["nba"]
    assert v["verdict"] == "contain" and v["n"] == 180 and v["game_days"] == 3
    assert v["letters"]["AB"]["n"] == 180 and v["stated"] > v["hit"] + 0.10
    assert containment.contained("nba", "points")
    e = [x for x in json.loads(containment.path().read_text())["log"] if x["league"] == "nba"][-1]
    assert e["changed"] and e["date"] == "2026-10-26" and (e["after"]["contained"] or bool(e["after"]["stats"]))
    # admin view (route + admin_learning.json) carries the headline
    al = json.loads((config.DATA_DIR / "model" / "api" / "admin_learning.json").read_text())
    assert al["grades"]["headline"]["nba"]["verdict"] == v["verdict"]
    # once per game-day; then only when something new settled
    again = daily.run(now=NOW.replace(hour=15))
    assert again == {"ran": False, "why": "already ran for game-day 2026-10-26"}
    nxt = daily.run(now=NOW.replace(day=27))
    assert nxt["ran"] is False and nxt["why"].startswith("nothing new settled")


def test_daily_run_releases_on_the_a9_5_rule_and_switches_respected(monkeypatch):
    _world(balanced=True, win=lambda i: i % 3 != 0)                            # hit 66.7% vs stated 62%, balanced
    _state(nba=True, nfl=True)
    monkeypatch.setenv("SPORTS_CAL_AUTOFIT", "0")
    out = daily.run(now=NOW)
    assert out["cal"] == {"skipped": "SPORTS_CAL_AUTOFIT=0"} and not cal.weights_path().exists()
    assert out["headline"]["nba"]["verdict"] == "release", out["headline"]["nba"]
    assert not containment.contained("nba", "points") and containment.contained("nfl", "rec_yds")
    monkeypatch.setenv("SPORTS_CONTAIN_NBA", "1")                              # the owner's override still wins
    containment.reset_cache()            # a restart on Render
    assert containment.contained("nba", "points")


def test_gradecheck_switch_off_leaves_containment_alone(monkeypatch):
    _world()
    monkeypatch.setenv("SPORTS_GRADECHECK_DAILY", "0")
    monkeypatch.setenv("SPORTS_CAL_AUTOFIT", "0")
    out = daily.run(now=NOW)
    assert out["gradecheck"] == {"skipped": "SPORTS_GRADECHECK_DAILY=0"} and not containment.path().exists()


def test_dated_copies_keep_thirty(monkeypatch):
    d = config.DATA_DIR / "model" / "gradecheck"
    d.mkdir(parents=True)
    for i in range(35):
        (d / f"gradecheck_2026-09-{i:02d}.json").write_text("{}")
    daily.write_gradecheck(daily.paths(), {"leagues": {}}, "2026-10-26")
    left = sorted(x.name for x in d.glob("gradecheck_*.json"))
    assert len(left) == 30 and left[-1] == "gradecheck_2026-10-26.json"


def test_calibration_can_release_letters_but_not_fake_selection():
    """A9a 5: the verdict is read on the chance members see. Raw 70% stated vs 58% hit = contain; the calibrated
    chance (cal-mi ON) states 60% -> release, but only while the hit interval is above 50%."""
    def s(hit, stated, lo, hi):
        return {"n": 400, "games": 60, "hit": hit, "stated": stated, "ci": [lo, hi], "bal": 0.03}
    raw = gc.verdict(s(0.58, 0.70, 0.53, 0.63), s(0.57, 0.66, 0.53, 0.61), {}, 5)
    calb = gc.verdict(s(0.58, 0.60, 0.53, 0.63), s(0.57, 0.58, 0.53, 0.61), {}, 5)
    weak = gc.verdict(s(0.53, 0.55, 0.48, 0.58), s(0.53, 0.55, 0.48, 0.58), {}, 5)
    assert raw["verdict"] == "contain" and calb["verdict"] == "release" and weak["verdict"] == "keep"


# --------------------------------------------------------------------------------------------- scheduler + route
def test_scheduler_job_is_daily_heavy_and_fits_the_starter_budget():
    from sports import scheduler
    j = scheduler.default_jobs()["grades-daily"]
    assert j["argv"][1:] == ["-m", "sports.model.daily", "run"] and j["flag"] == "SPORTS_INGEST"
    assert j["cls"] == scheduler.HEAVY and j["cap_mb"] <= scheduler.budget_mb() and j["every_s"] == 1800


def test_admin_route_is_admin_only_and_serves_the_headline():
    from sports import web
    assert web.ROUTES["/api/sports/admin/model/*"][1] == web.ADMIN
    from sports import api_model
    code, body = api_model.admin_model(SimpleNamespace(rest="gradecheck", query={"part": "headline"}))
    assert code == 200 and body["headline"] is None and set(body["containment"]["leagues"]) == {"nba", "nfl"}
    code, body = api_model.admin_model(SimpleNamespace(rest="gradecheck", query={}))
    assert code == 200 and body["gradecheck"] is None


def test_diagnostics_page_shows_the_sports_headline():
    html = (ROOT / "web" / "diagnostics.html").read_text(encoding="utf-8")
    assert 'id="sportsgrades"' in html and "/api/sports/admin/model/gradecheck?part=headline" in html
    assert '"X-Admin-Token": tok' in html and "loadSportsGrades();" in html


# --------------------------------------------------------------------------------------------- shown chance (A9a 6)
def _ca(po, f):
    return {"po": po, "pu": round(1 - po, 6), "pp": 0.0, "raw": [0.55, 0.45, 0.0], "v": "cal-mi-1:abc", "mode": "S",
            "f": f}


def test_snapshot_stores_the_shown_chance_of_the_offers_own_forecast_first_write_wins(tmp_path):
    rows = [{"stat": "points", "quotes": [
        {"record_id": "r1", "forecast_id": "fc-1", "line": 20.5, "cal": _ca(0.70, "fc-1")},
        {"record_id": "r2", "forecast_id": "fc-2", "line": 21.5, "cal": _ca(0.66, "fc-9")}]}]   # a line read: never
    meta = {"league": "nba", "built_at": "2026-10-26T12:00:00Z"}
    (tmp_path / "s").mkdir()
    s = snapshots.write_cal_index(tmp_path / "s", meta, rows)
    assert s["shown_new"] == 1
    rows[0]["quotes"][0]["cal"] = _ca(0.60, "fc-1")
    snapshots.write_cal_index(tmp_path / "s", dict(meta, built_at="2026-10-26T13:00:00Z"), rows)
    got = shown.get_many(["fc-1", "fc-2", "fc-9"])
    assert set(got) == {"fc-1"} and got["fc-1"]["p_over"] == 0.70 and got["fc-1"]["v"] == "cal-mi-1:abc"


def test_no_shown_file_while_every_market_is_off(tmp_path):
    (tmp_path / "s").mkdir()
    snapshots.write_cal_index(tmp_path / "s", {"league": "nba"}, [{"stat": "points", "quotes": [
        {"record_id": "r1", "forecast_id": "fc-1", "line": 20.5}]}])
    assert not shown.path().exists() and shown.get_many(["fc-1"]) == {}


def test_results_score_what_members_saw_and_keep_the_raw_chance(monkeypatch):
    _fake_espn(monkeypatch, {"nba:espn:1": 25.0})
    with store.opened() as con:
        _, fc = _ledger_world(con)                                              # raw p_over 0.55 -> C
        worker.settle_league(con, "nba", now="2026-10-24T06:00:00Z")
        before = [r for r in rp.rows_for(con, "nba") if r["forecast_id"] == fc.forecast_id][0]
        shown.record("nba", [(fc.forecast_id, "points", 20.5, _ca(0.70, fc.forecast_id))], "2026-10-23T20:00:00Z")
        after = [r for r in rp.rows_for(con, "nba") if r["forecast_id"] == fc.forecast_id][0]
    assert before["p_over"] == 0.55 and "raw" not in before
    assert after["p_over"] == 0.70 and after["chance"] == "shown" and after["raw"] == [0.55, 0.45, 0.0]
    assert after["lean"] == before["lean"] == "over" and after["lean_p"] == pytest.approx(0.70)
    assert board_view.research_grade(after["p_over"]) == "A" and board_view.research_grade(before["p_over"]) == "C"


def test_stored_gradecheck_is_the_cli_content_under_400_lines_at_production_shape():
    """Both leagues, every supported stat (NBA 4, NFL 5), both platforms: the stored file has the same content as
    `python -m sports.model.tune gradecheck` prints, in under 400 lines (A9a 3)."""
    from sports import capabilities
    (config.DATA_DIR / "model").mkdir(parents=True, exist_ok=True)
    w = World(config.DATA_DIR / "model")
    i = 0
    for lg in ("nba", "nfl"):
        for d in range(3):
            start = f"2026-10-{20 + d:02d}T23:00:00Z"
            for g in range(10):
                for st in capabilities.SUPPORTED[lg]:
                    for k in range(6):
                        hit, plat = (i * 7) % 10 < 6, ("prizepicks" if k < 3 else "underdog")
                        if k % 2:
                            w.prop(f"{lg}:espn:{d}{g}", 0.4, 0.6, mean=9, value=8 if hit else 13, start=start,
                                   stat=st, platform=plat, mkt=0.45 if k == 3 else None)
                        else:
                            w.prop(f"{lg}:espn:{d}{g}", 0.62, 0.38, mean=12, value=14 if hit else 8, start=start,
                                   stat=st, platform=plat, mkt=0.55 if k == 0 else None)
                        i += 1
    w.lc.execute("UPDATE scorecard SET league = 'nfl' WHERE event_id LIKE 'nfl:%'")
    w.close()
    res = gc.run(w.l, w.i, None, None, None)
    assert set(res["leagues"]) == {"nba", "nfl"} and res["leagues"]["nfl"]["counts"]["props"] == 900
    text = daily.compact(res)
    assert json.loads(text) == json.loads(gc.dumps(res))
    assert text.count("\n") + 1 < daily.MAX_LINES, text.count("\n")
