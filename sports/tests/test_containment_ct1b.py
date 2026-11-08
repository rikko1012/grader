"""ct-1b (REGISTER A9b note, owner decision 2026-11-08: publish the NBA and NFL grades): NBA and NFL letters are released
by the owner. SPORTS_CONTAIN_<LG>=1 still contains (kill switch); the daily verdict and the pc-1 held-out result are
computed and logged but never hide a released league's letters; chances, bands and 0U stakes are unchanged; no
"not validated" / "held" text beside a released letter. conftest runs every other test with no release, so the verdict
machinery stays covered; this file switches the shipped release back on."""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sports import board_view, containment, picks, results_public as rp, sheets, snapshots
from sports.model import chance_cal
from sports.slips import pool

SHIPPED = dict(containment.OWNER_RELEASE)              # read at import, before conftest swaps it out per test
ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "sports" / "model" / "artifacts" / "containment_default.json"
PC1 = ROOT / "sports" / "model" / "artifacts" / "chance_cal_pc1.json"
L10 = [5, 5, 0, 10]
NOW = "2026-11-08T21:00:00Z"


@pytest.fixture
def released(monkeypatch):
    """The production configuration: the shipped owner release, the committed seed (NBA + NFL contained by the
    2026-10-28 verdict) and the committed pc-1 parameters (NBA failed its held-out test; pc-1 itself is on only with
    SPORTS_CHANCE_CAL=1, as on the service when the owner turns it on)."""
    monkeypatch.setattr(containment, "OWNER_RELEASE", dict(SHIPPED))
    monkeypatch.setattr(containment, "DEFAULT_PATH", SEED)
    monkeypatch.setattr(chance_cal, "DEFAULT_PATH", PC1)
    containment.reset_cache()
    chance_cal.reset_cache()
    yield
    containment.reset_cache()
    chance_cal.reset_cache()


def _fc(stat="points", po=0.62, pu=0.38, mu=25.0, line=20.5, side="over"):
    return SimpleNamespace(p_over=po, p_under=pu, model_version="sm-1", line=line, projection_mean=mu, stat=stat,
                           chosen_side=side, forecast_id="f1")


def _row(stat="points"):
    return {"key": "k", "event_id": "e", "player_id": "p", "player": "P", "position": None, "team": None,
            "stat": stat, "primary": True, "capability": None, "quotes": [], "kalshi_near": [], "kalshi_rungs": [],
            "alternates": [], "promotional": False}


SETTLED = [{"forecast_id": "b1", "model": "sm-1", "event_id": "nba:g1", "player_id": "nba:p1", "stat": "points",
            "line": 20.5, "p_over": 0.62, "p_under": 0.38, "status": "settled", "lean": "over", "lean_result": "win",
            "outcome": "over", "actual": 25.0, "forecast_at": "2026-10-20T10:00Z", "settled_at": "2026-10-21T05:00Z"}]


# ------------------------------------------------------------------------------------------------ the shipped rule
def test_shipped_release_names_both_leagues_by_owner_decision():
    assert set(SHIPPED) == {"nba", "nfl"}
    for lg, r in SHIPPED.items():
        assert r["rule"] == "ct-1b" and r["date"] == "2026-11-08" and r["by"] == "owner", lg
    assert containment.RULE == "ct-1b" and containment.VERSION == "ct-1"       # the state file format is unchanged
    text = (ROOT / "sports" / "research" / "REGISTER.md").read_text(encoding="utf-8")
    note = text[text.index("A9b amendment note - ct-1b"):]
    assert "2026-11-08" in note and "SPORTS_CONTAIN_<LG>=1" in note and "LOGGED" in note


def test_production_inputs_would_contain_without_the_release(released, monkeypatch):
    """The seed verdict holds both leagues (and pc-1, when on, holds NBA) - exactly what hid every letter before ct-1b."""
    assert containment.league_state("nba")["contained"] and containment.league_state("nfl")["contained"]
    monkeypatch.setattr(containment, "OWNER_RELEASE", {})
    containment.reset_cache()
    assert containment.contained("nba", "points") and containment.contained("nfl", "rec_yds")
    monkeypatch.setenv("SPORTS_CHANCE_CAL", "1")
    chance_cal.reset_cache()
    assert chance_cal.letters_held("nba") is True


def test_pc1_held_league_is_reported_not_applied(released, monkeypatch):
    monkeypatch.setenv("SPORTS_CHANCE_CAL", "1")
    chance_cal.reset_cache()
    containment.reset_cache()
    assert containment.pc_held("nba") and not containment.contained("nba", "points")
    snap = containment.snapshot()
    assert snap["pc1_held"]["nba"] is True and snap["verdict"]["nba"] == "contained" and snap["letters"]["nba"] == "live"
    x, _ = board_view.quote_x("nba", "points", 0.62, 0.38, 25.0, 20.5, None, L10)
    assert "b" not in x and x["gr"] == "A"


# ---------------------------------------------------------------------------------- released: side + letter shown
def test_released_board_row_gets_side_and_grade_on_every_surface(released):
    for lg, stat, args, side in (("nba", "points", (0.62, 0.38, 25.0, 20.5), "over"),
                                 ("nfl", "rec_yds", (0.3, 0.7, 40.0, 50.5), "under")):
        assert containment.released(lg) and not containment.contained(lg, stat)
        assert board_view.contain_reason(lg, stat) is None
        x, code = board_view.quote_x(lg, stat, *args, None, L10)
        assert code is None and "b" not in x and x["ln"] == side and x["gr"] == "A", (lg, x)
        assert "nv" not in snapshots.compact_row(_row(stat), lg)
        fc = _fc(stat, *args, side=side)
        assert picks.research_grade(fc, side, lg) == "A"                # tracked picks
        assert pool.guard_reason(fc, lg) is None                         # slips: NFL failed-gate markets included
    G, M = rp.grades(SETTLED, "nba"), rp.model_gate("nba")              # public results / hub Record
    assert G["rows"][0]["n"] == 1 and "withheld" not in G
    assert M["contained"] is False and M["not_validated"] is None


def test_released_failed_gate_market_keeps_edges_withheld(released):
    x, _ = board_view.quote_x("nfl", "rec_yds", 0.3, 0.7, 40.0, 50.5, {"po": 0.45, "pu": 0.55, "n": 3}, L10)
    assert x["gr"] == "A" and "e" not in x and "s" not in x and x["c"] is None


@pytest.mark.parametrize("pc1", ["0", "1"])
def test_released_chance_and_bands_unchanged(released, monkeypatch, pc1):
    """The printed chance is exactly the same with and without the release (pc-1 on or off); the letter is its band."""
    monkeypatch.setenv("SPORTS_CHANCE_CAL", pc1)
    chance_cal.reset_cache()
    mkc = {"po": 0.55, "pu": 0.45, "n": 3}
    on = [board_view.calibrated(lg, st, 0.70, 0.30, 0.0, "sm-1", mkc) for lg, st in (("nba", "points"), ("nfl", "rec_yds"))]
    monkeypatch.setattr(containment, "OWNER_RELEASE", {})
    containment.reset_cache()
    off = [board_view.calibrated(lg, st, 0.70, 0.30, 0.0, "sm-1", mkc) for lg, st in (("nba", "points"), ("nfl", "rec_yds"))]
    assert on == off
    if pc1 == "1":
        assert on[0]["v"].startswith("pc-1:") and on[0]["raw"][:2] == [0.7, 0.3] and on[0]["po"] < 0.70
    assert board_view.research_grade(0.60) == "A" and board_view.research_grade(0.5559) == "B"
    assert board_view.research_grade(0.5345) == "C" and board_view.research_grade(0.51) == "Lean"


def test_released_sheet_quote_carries_the_letter(released):
    q = {"record_id": "r1", "platform": "prizepicks", "line": 20.5, "variant": "standard", "promotional": False,
         "availability": "open", "sides": [{"side": "over", "offered": True}, {"side": "under", "offered": True}],
         "last_seen_at": NOW, "source_updated_at": None, "gradable": True, "forecast_id": "fa"}
    f = {"forecast_id": "fa", "kind": "scout", "model_version": "mi-1", "line": 20.5, "p_over": 0.66, "p_under": 0.34,
         "p_push": 0.0, "projection_mean": 25.0, "forecast_at": NOW, "research_only": True, "for_revision": None,
         "detail": {}}
    pf = {"forecasts": {"fa": f}, "forecast_detail": {},
          "markets": [{"key": "nba:e1|p1|points|full_game", "event_id": "nba:e1", "player_id": "p1", "player": "K",
                       "position": "C", "team": "NY", "stat": "points", "stat_label": "Points", "primary": True,
                       "capability": "supported", "quotes": [q], "alternates": [], "market": None}]}
    hist = [{"participation": "played", "values": {"points": v}, "season": 2026, "start_utc": f"2026-11-0{i}T00:00:00Z"}
            for i, v in enumerate((24, 18, 26, 22, 19, 25, 21, 23, 17, 27), 1)]           # 6 of 10 over 20.5
    m, _fc_, _v = sheets.sheet_markets(pf, hist, "prizepicks", {"nba:e1": {"start_utc": "2099-01-01T00:00:00Z"}}, NOW)
    assert "nv" not in m[0] and m[0]["q"][0]["x"].get("gr") == "A" and "b" not in m[0]["q"][0]["x"], json.dumps(m[0]["q"][0])


# ------------------------------------------------------------------------------------------- the kill switch
@pytest.mark.parametrize("lg,stat", [("nba", "points"), ("nfl", "rec_yds")])
def test_env_one_still_contains_a_released_league(released, monkeypatch, lg, stat):
    monkeypatch.setenv(f"SPORTS_CONTAIN_{lg.upper()}", "1")
    containment.reset_cache()                                            # a restart on Render
    assert containment.contained(lg, stat) and not containment.released(lg)
    x, _ = board_view.quote_x(lg, stat, 0.62, 0.38, 25.0, 20.5, None, L10)
    assert x["b"] == "gate" and "gr" not in x
    assert snapshots.compact_row(_row(stat), lg)["nv"] == f"{lg.upper()} model not validated - projection only"
    assert pool.guard_reason(_fc(stat), lg).startswith("model not validated")
    snap = containment.snapshot()
    assert snap["letters"][lg] == "contained" and snap["owner_release"][lg]["rule"] == "ct-1b"
    other = "nfl" if lg == "nba" else "nba"
    assert snap["letters"][other] == "live"                               # the other league stays released


@pytest.mark.parametrize("off", ["0", "off"])
def test_env_zero_changes_nothing_and_is_logged(released, monkeypatch, capsys, off):
    monkeypatch.setenv("SPORTS_CONTAIN_NBA", off)
    monkeypatch.setattr(containment, "_logged", set())
    containment.reset_cache()
    assert not containment.contained("nba", "points")
    out = capsys.readouterr().out
    assert f"SPORTS_CONTAIN_NBA={off!r} asked to release; applied: letters shown" in out
    assert "the owner released this league (ct-1b)" in out
    req = containment.snapshot()["env_requests"]["nba"]
    assert req["asked"] == "release" and req["applied"] is False


# ------------------------------------------------------------------- the daily verdict: computed, logged, not applied
def test_daily_verdict_contained_still_shown_and_logged(released, tmp_path):
    res = {"leagues": {"nba": {"verdict": {"verdict": "contain", "n": 2000, "hit": 0.53, "stated": 0.65,
                                           "ci": [0.51, 0.55], "games": 50, "game_days": 5, "why": "hit below stated"}},
                       "nfl": {"verdict": {"verdict": "contain:rec_yds", "n": 900, "game_days": 4}}}}
    out = containment.apply_verdicts(res, "2026-11-09", "daily gradecheck 2026-11-09 (A9a 4)")
    assert out["nba"]["after"]["contained"] and out["nba"]["owner_release"] == "ct-1b"
    assert "rec_yds" in out["nfl"]["after"]["stats"]
    assert not containment.contained("nba", "points") and not containment.contained("nfl", "rec_yds")
    x, _ = board_view.quote_x("nba", "points", 0.62, 0.38, 25.0, 20.5, None, L10)
    assert x["gr"] == "A"
    obj = json.loads(containment.path().read_text(encoding="utf-8"))
    e = [r for r in obj["log"] if r["league"] == "nba"][-1]
    assert e["verdict"] == "contain" and e["after"]["contained"] is True and e["n"] == 2000
    assert e["applied"] == "owner release ct-1b: verdict logged, letters shown"
    snap = containment.snapshot()                                        # the admin gradecheck view
    assert snap["verdict"] == {"nba": "contained", "nfl": "contained"}  # NFL: seed contained + the rec_yds stat
    assert snap["letters"] == {"nba": "live", "nfl": "live"} and snap["rule"] == "ct-1b"
    st = containment.league_state("nba")
    assert st["contained"] is True and st["owner_release"]["date"] == "2026-11-08"


def test_unreleased_league_still_follows_the_verdict(released, monkeypatch):
    """Removing a league from OWNER_RELEASE returns it to the A9a 4 / A10 rule (no other code path changed)."""
    monkeypatch.setattr(containment, "OWNER_RELEASE", {"nfl": SHIPPED["nfl"]})
    containment.reset_cache()
    assert containment.contained("nba", "points") and not containment.contained("nfl", "rec_yds")


# ------------------------------------------------------------------------------- no hedge text beside released letters
def test_no_hedge_text_for_released_leagues_in_member_pages():
    web = ROOT / "web"
    labels = (web / "sports" / "labels.js").read_text(encoding="utf-8")
    assert 'var GRADE_TAG = "Scout grade";' in labels and "Research grade" not in labels
    for f in ("sports/picks.js", "sports/slips.js", "sports/results_public.js", "upgrade-hub.js", "sports/app.js",
              "sports/labels.js"):
        s = (web / f).read_text(encoding="utf-8")
        for bad in ('"NFL model not validated"', "Research grade", "grades are not validated", "Research letters",
                    "Grades are research letters", "grades are research grades", "<b>Research estimates.</b>",
                    "S.validationText(", "Grades and leans stay research",
                    "calibration has not passed validation", "validation gate has not released",
                    "has not yet passed its check", "has not passed its live-line check", "(not validated)"):
            assert bad not in s, (f, bad)
    # My picks: the per-pick validation state ("Projection only - model not yet validated") is not printed
    assert "(p.validation_state ? \"\" : esc(p.validation_text" in (web / "sports" / "picks.js").read_text(encoding="utf-8")
    assert board_view.GRADE_TAG == "Scout grade" and "research" not in picks.GRADE_TABLE_LABEL.lower()
    src = (ROOT / "workspace_sports.py").read_text(encoding="utf-8")       # read as text: sports imports no hub module
    assert "Research grade" not in src and 'HOLD_DETAIL = "{LG} grades are switched off."' in src
    from sports.slips import validate
    legs = [{"player": "P", "team": "NY", "league": "nba", "event_ref": "A @ B", "start_utc": "2099-01-01T00:00:00Z",
             "stat": "points", "side": "over", "line": 20.5, "research_only": True}]
    try:
        text = validate.copy_text("prizepicks", "power", legs)
    except Exception:                                                    # noqa: BLE001 - the row shape is the page's
        text = None
    if text is not None:
        assert "not validated" not in text and "Research estimates; stakes 0." in text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_grade_html_has_no_not_validated_text(tmp_path):
    js = tmp_path / "g.js"
    js.write_text("""
const L = require(%s);
for (const g of ["A", "B", "C", "Lean"]) {
  const h = L.gradeHtml(g, "over", null);
  if (/not validated|research|grade-tag/i.test(h)) throw new Error(h);
  if (h.indexOf(g === "Lean" ? "Lean Over" : g + " Over") < 0) throw new Error(h);
}
console.log("ok");
""" % json.dumps(str(ROOT / "web" / "sports" / "labels.js")), encoding="utf-8")
    r = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr
