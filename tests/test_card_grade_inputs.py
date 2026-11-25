"""Owner 2026-11-11 ("make sure player cards have all information for grades"): the hub card's /line doc carries ONE block,
grade_inputs (card_grade_inputs.py), the same shape for every sport. Over real HTTP with server.Handler and fixture data (no network):
- every live term of the sport's live model is listed with live = true (CS2 kills: cm2_model.json feature_names; CS2 headshots: the
  same + headshots per kill; LoL: lol/factor_fit.json's fitted terms; NBA / NFL: the forecast's saved input list, which holds the
  sm-1 base inputs of sports/forecast_detail.py BASE_FIELDS) and everything shown for context has live = false;
- projection, chance, side, letter and units are the board record's own numbers;
- the letter's band is the live grade scale's (grader.grade_from_p, lol/center/forecast.py BANDS, sports/model/grade_policy.py BANDS);
- no hedge / status word appears in any string of the block;
- the board payload does not grow (no grade_inputs on a board record) and the block is display only.
The model files of lol/ and sports/ are read as text here: a CS2 test never imports those packages."""
import ast
import json
import math
import re
import shutil
import sqlite3
import subprocess
import zlib
from datetime import date
from pathlib import Path

import pytest

import card_grade_inputs as gi_mod
import cm2
import grader
import picklog
from tests.test_workspace_all_sports import cs2, env, fresh, get, recs  # noqa: F401


@pytest.fixture(autouse=True)
def clean_card_store():
    """cs2_card_ctx keeps card panels by row id for the process: another test's rows must not reach these cards."""
    import cs2_card_ctx
    cs2_card_ctx.reset()
    yield
    cs2_card_ctx.reset()
from tests.workspace_fixture import row
from tests.workspace_other_fixture import player

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r"\b(unconfirmed|may|likely|pending|unknown|questionable|doubtful|probable|uncertain|no bet|more|less|higher|lower)\b", re.I)
SHAPE = {"v", "sport", "model", "projection", "line", "gap", "chance", "side", "letter", "lean", "letter_rule", "chance_rule", "stake", "factors", "effect_note",
         "sample", "matchup", "hold"}


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


def common(g, rec, sport):
    """What every sport's block must satisfy against its board record."""
    sc = rec["scout"]
    assert g["v"] == 1 and g["sport"] == sport and set(g) <= SHAPE, set(g) - SHAPE
    assert g["projection"] == sc["projection"] and g["line"] == rec["offer"]["line"]
    assert g["gap"] == round(sc["projection"] - rec["offer"]["line"], 4)
    assert g["side"] == sc["side"] and g["chance"] == sc["p_side"]             # full precision: the page prints 0.1%
    assert g["stake"]["units"] == sc["units"]
    if sc.get("stake_reason"):
        assert g["stake"]["rule"] == sc["stake_reason"]
    bad = [s for s in strings(g) if FORBIDDEN.search(s)]
    assert not bad, bad
    for f in g.get("factors") or []:
        assert set(f) <= {"id", "label", "value", "effect", "effect_unit", "direction", "live"} and isinstance(f["value"], str) and isinstance(f["live"], bool)
        assert f["direction"] == ("up" if f.get("effect", 0) > 0 else "down" if f.get("effect", 0) < 0 else "none")
    assert all(v is not None and v != "" for v in strings(g))
    assert len(json.dumps(g, separators=(",", ":"))) < 8000                       # a few KB at most (LoL, 40 terms, is the largest)
    return {f["id"]: f for f in g.get("factors") or []}


def band(sport, letter):
    return next(b for b in gi_mod.scale(sport) if b["letter"] == letter)


def cm2_inputs(lam, hs=False):
    """Grader inputs shaped as cs2_scout saves them for a perf-cm2 forecast (cs2_scout._cm2_live): fourteen features near the fit's means."""
    fit = cm2.FIT
    feats = [m + (0.4 if i % 2 else -0.3) * s for i, (m, s) in enumerate(zip(fit["mean"], fit["std"]))]
    feats[6] = 1.0
    live = {"model": "cm-2", "lambda": 40.0, "sd": 9.3, "features": feats, "tables_sha": "t" * 12, "artifact_sha": cm2.ARTIFACT_SHA,
            "inputs": {"maps": 71, "lan": True, "elo_own": 1580.0, "elo_opp": 1510.0}}
    pf = {"version": "perf-cm2", "lambda": lam, "lambda_kills": 40.0, "sd": 9.3, "rate": 0.74, "opp_factor": 1.02, "rounds_per_map": 21.7, "maps": 2, "player_maps": 80,
          "newest_map_day": 740000, "data_quality": "ok", "perf2": {"version": "perf-2", "lambda": lam - 0.8}, "cm2_live": live}
    if hs:
        live.update(hs_per_kill=0.47, spread_c_kills=fit["spread_c"])
        pf.update(hs_per_kill=0.47)
    return {"prop": "hs" if hs else "kills", "line": 38.5, "scope": "12", "bo": 3, "maps_played": 41, "rating": 1.12, "kpr": 0.74, "adr": 81.2, "kast": 73.1, "recent_avg": 40.1,
            "trade_pr": 0.121, "days_since_last_map": 4, "opp_rank": 21, "p_team_market": 0.612, "perf": pf,
            "environment": {"lan": {"maps": 40, "kpr": 0.77}, "online": {"maps": 21, "kpr": 0.69}, "tonight": "lan"},
            "rank_band": {"opp_rank": 21, "band": 10, "maps": 14, "kpr": 0.812, "kpr_all": 0.74, "ratio": 1.097}}


def save_inputs(fx, r, inputs):
    con = sqlite3.connect(picklog.DB, timeout=10)
    con.execute("UPDATE forecasts SET inputs = ? WHERE pick_id = ?", (zlib.compress(json.dumps(inputs).encode()), fx.pick_id(r)))
    con.commit()
    con.close()


def test_cs2_kills_every_perf_cm2_term_is_on_the_card(env):
    r = env.by("vargo")
    save_inputs(env, r, cm2_inputs(r["proj"]))
    rec = cs2(env)[("vargo", "underdog")]
    assert "grade_inputs" not in rec                                             # the board payload does not grow
    st, doc = get(env, f"line?book=underdog&id={r['id']}")
    g = doc["grade_inputs"]
    assert st == 200 and doc["record"]["scout"] == rec["scout"]                  # display only: the record is untouched
    by = common(g, rec, "cs2")
    assert g["model"] == {"name": "perf-cm2", "version": rec["scout"]["model_version"]}
    names = cm2.ARTIFACT["feature_names"]                                        # the live model's own term list
    assert [f["id"] for f in g["factors"]][:len(names)] == names and all(by[n]["live"] for n in names)
    feats = cm2_inputs(r["proj"])["perf"]["cm2_live"]["features"]
    for n, x, b, m, s in zip(names, feats, cm2.FIT["coef"], cm2.FIT["mean"], cm2.FIT["std"]):
        assert by[n]["effect"] == round((math.exp(b * (x - m) / s) - 1) * 100, 2) and by[n]["effect_unit"] == "pct"    # the model's own addend
    assert by["lan_indicator"]["value"] == "LAN" and by["own_elo_over_400"]["value"] == f"{feats[7] * 400:.0f}"
    ctx = {k: f for k, f in by.items() if k not in names}
    assert {"perf2_projection", "recent_avg", "rating", "trade_pr", "rank_band", "p_team_market", "days_since_last_map"} <= set(ctx)
    assert not any(f["live"] for f in ctx.values())                              # shown for context: weight 0
    # the letter and its band come from the live grader
    assert g["letter"] == rec["scout"]["grade"] == grader.grade_from_p(r["p"]) == "B"
    b = band("cs2", "B")
    assert g["letter_rule"]["label"] == "B" and g["letter_rule"]["lower"] == b["lower"] and g["letter_rule"]["upper"] == b["upper"]
    assert grader.grade_from_p(b["lower"]) == "B" and grader.grade_from_p(b["lower"] - 1e-6) != "B" and grader.grade_from_p(b["upper"]) != "B"
    assert g["stake"]["band_units"] == grader.units_for_p(b["lower"])
    assert g["sample"] == {"n": 71, "unit": "maps", "last": date.fromordinal(740000).isoformat(), "window_days": cm2.ARTIFACT["feature_constants"]["window_days"]}
    # the note says where each effect comes from, as a plain fact (coef x (input - mean) / spread, the model's own numbers)
    assert g["effect_note"].startswith("Each effect is from the model's coefficients:") and not FORBIDDEN.search(g["effect_note"])
    assert not re.search(r"(may|might|likely|approximately|approx|estimated|roughly|about)", g["effect_note"], re.I)
    m = {x["id"]: x["value"] for x in g["matchup"]}
    assert m["opponent"] == "Norrland (VRS #21)" and m["setting"].startswith("LAN") and m["format"] == "Best of 3" and "rank_band" in m


def test_cs2_headshots_every_hs_cm2_term_is_on_the_card(env):
    r = row("11111111-0000-4000-8000-0000000000aa", "underdog", "sable", "Vanta", "Kestrel Five", 17.5, "Less", 0.541, 901, env.rows[-1]["start"], prop="hs", scope="12",
            stake_reason=None)
    env.rows.append(r)
    env.seed([r])
    save_inputs(env, r, cm2_inputs(r["proj"], hs=True))
    rec = cs2(env)[("sable", "underdog")]
    st, doc = get(env, f"line?book=underdog&id={r['id']}")
    g = doc["grade_inputs"]
    by = common(g, rec, "cs2")
    assert st == 200 and g["model"]["name"] == "hs-cm2"
    live = [f["id"] for f in g["factors"] if f["live"]]
    assert live == cm2.ARTIFACT["feature_names"] + ["kills_projection", "hs_per_kill"]       # cm-2 kills x headshots per kill
    assert by["hs_per_kill"]["value"] == "47.0%" and by["kills_projection"]["value"] == "40.0"
    assert not by["perf2_projection"]["live"] and not by["recent_avg"]["live"]
    # under the lowest letter the card reads "Lean Under" with the band it sits below - never "No bet"
    assert rec["scout"]["grade"] is None and "letter" not in g and g["lean"] == "Lean Under"
    assert g["letter_rule"]["label"] == "Lean Under" and g["letter_rule"]["upper"] == gi_mod.scale("cs2")[-1]["lower"] and "lower" not in g["letter_rule"]
    assert grader.grade_from_p(g["letter_rule"]["upper"]) == "B-"


def test_cs2_block_without_a_saved_forecast_says_only_what_is_known(env):
    r = env.by("orlo")                                  # the fixture's saved inputs hold no performance-model record
    rec = cs2(env)[("orlo", "underdog")]
    st, doc = get(env, f"line?book=underdog&id={r['id']}")
    g = doc["grade_inputs"]
    assert st == 200 and "model" not in g and "factors" not in g and "sample" not in g       # omitted, never a placeholder
    assert g["chance"] == rec["scout"]["p_side"] and g["letter"] == rec["scout"]["grade"] and g["matchup"][0]["value"] == "Kestrel Five"


def lol_fit():
    return json.loads((ROOT / "lol" / "factor_fit.json").read_text(encoding="utf-8"))


def lol_bands():
    tree = ast.parse((ROOT / "lol" / "center" / "forecast.py").read_text(encoding="utf-8"))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "BANDS")


def test_lol_every_fitted_term_is_on_the_card(env):
    fit = lol_fit()
    terms = list(fit["stats"]["kills"]["beta"])                                 # lol-1f's fitted terms for kills (lol/factor_fit.json)
    assert fit["stats"]["deaths"] is None                                       # deaths: the A7 test did not pass - weight 0
    panel = [{"key": k, "factor": k.replace("_", " "), "value": 0.1 * (i % 5) - 0.2, "effect_pct": round(0.4 * (i % 7) - 1.2, 2)} for i, k in enumerate(terms)]
    for r in env.lol.rows:
        f = r.get("forecast")
        if not f:
            continue
        dead = r["player"] == "Chika"
        f["model"] = "lol-1 (share, fit f59d290ec261)" if dead else "lpf-1 (share, fit f59d290ec261)"
        f.setdefault("inputs", {}).update(
            player_rate=3.26, team_factor=0.87, opp_factor=0.93, strength_factor=1.04, p_win=0.65, history_games=98, history_since="2025-10-01T00:00:00Z",
            history_last_game="2026-11-08T12:00:00Z", form="share", team_win={"elo": 0.65, "market": {"found": True, "p": 0.61}},
            factors={"model": "lol-1" if dead else "lol-1f", "multiplier": 1.0 if dead else 1.02, "not_pre_match": ["draft"],
                     "factors": [dict(p, effect_pct=None) for p in panel] if dead else panel})
        if not dead:
            f["inputs"]["lpf1"] = {"version": "lpf-1", "artifact": "ab12cd34ef56ab12cd34", "cutoff": "2026-11-09", "lambda_lol1f": round(f["projection"] - 0.4, 4),
                                   "p_win_series_model": 0.66, "p_decider": 0.41, "patch_assumed": "26.21"}
    env.lol.write()
    board = recs(env, "lol")
    rec = board[("Harky", "underdog")]
    assert "grade_inputs" not in rec
    st, doc = get(env, f"line?sport=lol&book=underdog&id={rec['offer']['offer_id']}")
    g = doc["grade_inputs"]
    by = common(g, rec, "lol")
    assert st == 200 and g["model"] == {"name": "lpf-1", "version": "ab12cd34ef56ab12"} and rec["scout"]["model_version"] == "lpf-1"
    assert all(by[k]["live"] and by[k]["effect"] == p["effect_pct"] for k, p in zip(terms, panel) if p["effect_pct"])
    assert set(terms) <= set(by) and all(by[k]["live"] for k in terms)
    assert {"lol1f_projection", "lpf1_correction", "p_win_series", "p_decider", "player_rate", "team_factor", "opp_factor", "strength_factor", "p_win"} <= {k for k, f in by.items() if f["live"]}
    assert by["lpf1_correction"]["effect"] == 0.4 and by["lpf1_correction"]["effect_unit"] == "stat" and by["lpf1_correction"]["direction"] == "up"
    assert by["opp_factor"]["effect"] == -7.0 and by["opp_factor"]["direction"] == "down"
    assert not by["p_win_market"]["live"] and not by["recent_per_game"]["live"]            # saved and shown, weight 0
    assert g["letter"] == rec["scout"]["grade"] == "A-"
    cut = {letter: (lo, units) for lo, letter, units in lol_bands()}                      # the worker's own published bands
    assert g["letter_rule"]["lower"] == cut["A-"][0] and g["letter_rule"]["upper"] == cut["A"][0]
    assert [(b["letter"], b["lower"]) for b in g["letter_rule"]["bands"]] == [(letter, lo) for lo, letter, _u in lol_bands()]
    assert g["stake"]["units"] == 0.0 and g["stake"]["rule"].startswith("0U")
    assert g["sample"] == {"n": 98, "unit": "games", "first": "2025-10-01", "last": "2026-11-08"}
    m = {x["id"]: x["value"] for x in g["matchup"]}
    assert m["opponent"] == rec["player"]["opponent"] and m["format"] == "Best of 5" and m["period"] == "games 1, 2 and 3 added"
    # a stat whose factor test did not pass (lol-1): every A7 term is listed, none of them live
    dead = board[("Chika", "underdog")]
    st, doc = get(env, f"line?sport=lol&book=underdog&id={dead['offer']['offer_id']}")
    by = common(doc["grade_inputs"], dead, "lol")
    assert doc["grade_inputs"]["model"]["name"] == "lol-1" and set(terms) <= set(by) and not any(by[k]["live"] or "effect" in by[k] for k in terms)
    assert by["player_rate"]["live"] and "lpf1_correction" not in by
    # a line that offers one side only has no chance, side, letter or stake - the block says why and nothing else about a grade
    why = "Over only: Grader grades lines that offer both sides."
    g = gi_mod.lol({"member": {"scope_games": "game 1 only"}, "gi": {"player_rate": 3.2}},
                   {"scout": {"side": None, "p_side": None, "projection": None, "grade": None, "units": None, "hold": {"label": "Over only", "reason": why}},
                    "offer": {"line": 7.5}, "player": {"opponent": "MVK Esports"}, "event": {"best_of": 3}})
    assert not ({"chance", "side", "letter", "lean", "stake", "factors", "model", "projection"} & set(g)) and g["hold"] == {"reasons": [why]}


def base_fields(lg):
    tree = ast.parse((ROOT / "sports" / "forecast_detail.py").read_text(encoding="utf-8"))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "BASE_FIELDS")[lg]


def sports_bands():
    src = (ROOT / "sports" / "model" / "grade_policy.py").read_text(encoding="utf-8")
    ns = {}
    exec(compile(src, "grade_policy", "exec"), ns)       # pure python, no imports of its own
    return dict(ns["BANDS"])


def sports_player(lg, fid, line, registered):
    """A player file as Sports serves it: the forecast's saved input list = the sm-1 base inputs + the artifact's registered features."""
    p = player("G")
    inputs = [{"id": k, "label": lab, "value": 2.5 if dg else 12} for k, lab, dg in base_fields(lg)] + registered
    p["forecasts"] = {fid: {"line": line, "projection_mean": line + 0.7, "forecast_at": "2026-11-10T08:00:00Z", "model_version": "sm-1", "artifact_version": "sm-1-art-7f3a",
                            "grade_policy_version": "sports-gp-1", "reason_codes": ["statistical_only"], "contributions": None,
                            "detail": {"interval": [3, 11], "median": 7, "factors": inputs}}}
    p["factors"]["engine"] = {"label": "Matchup engine", "as_of": "2026-11-10T08:00:00Z", "rows": [], "by_stat": {
        "assists": [{"key": "dvp", "group": "Defensive matchup", "label": "Assists allowed per game to G", "value": 8.4, "n": 12},
                    {"key": "pace", "group": "Pace", "label": "Opponent pace", "value": 101.2},
                    {"key": "usage", "group": "Role", "label": "Usage rate, last 10", "value": 0.24, "n": 10},
                    {"key": "status", "group": "Role", "label": "Status", "value": "Questionable"}],
        "pass_yds": [{"key": "dvp", "group": "Defensive matchup", "label": "Passing yards allowed per game", "value": 231.5, "n": 9},
                     {"key": "snap_trend", "group": "Role", "label": "Snap share trend, last 3", "value": 0.03, "n": 3}]}}
    return p


def test_nba_every_sm1_input_is_on_the_card(env):
    sp = env.sports
    reg = [{"id": "nba.usage_l10", "label": "Usage rate (recent weighted)", "value": 0.241, "definition": "x"}]
    sp.players["nba:player:espn:304"] = sports_player("nba", "fc-aaaaaaaa00000007", 6.5, reg)
    rec = recs(env, "nba")[("Reed Lean", "underdog")]
    assert "grade_inputs" not in rec
    st, doc = get(env, f"line?sport=nba&book=underdog&id={rec['offer']['offer_id']}")
    g = doc["grade_inputs"]
    by = common(g, rec, "nba")
    assert st == 200 and doc["record"]["scout"] == rec["scout"]
    assert g["model"] == {"name": "sm-1", "version": "sm-1-art-7f3a", "grade_policy": "sports-gp-1"}
    terms = [k for k, _l, _d in base_fields("nba")] + ["nba.usage_l10"]         # every input the forecast was computed from
    assert [f["id"] for f in g["factors"] if f["live"]] == terms and by["rest_days"]["value"] == "2.50" and by["prior_n"]["value"] == "12"
    ctx = [f for f in g["factors"] if not f["live"]]
    assert [f["id"] for f in ctx] == ["usage"]                                  # the matchup engine's row: weight 0; a status word is never shown
    assert g["letter"] == rec["scout"]["grade"] == "B"
    cut = sports_bands()
    assert g["letter_rule"]["lower"] == cut["B"] and g["letter_rule"]["upper"] == cut["A"]
    assert g["stake"] == {"units": 0, "rule": "Stakes remain 0."} and g["sample"] == {"n": 12, "unit": "games"}
    m = {x["id"]: x["value"] for x in g["matchup"]}
    assert m["opponent"] == rec["player"]["opponent"] and m["venue"] == "Away" and m["rest_days"] == "2" and m["teammates_out"] == "1: Mitch Out"
    assert m["dvp"] == "8.40" and m["pace"] == "101.2"                            # opponent defence and pace
    assert "hold" not in g


def test_nfl_every_sm1_input_is_on_the_card_and_a_hold_says_why(env):
    sp = env.sports
    doc_, _view = sp.boards["nfl"]
    mk = doc_["markets"][0]
    mk.pop("nv")
    mk["q"][0]["x"] = {"v": "cf-2", "ln": "over", "pl": 0.5614, "gr": "B", "gp": "sports-gp-1", "c": 2, "cw": []}
    mk["q"][0]["ca"] = {"po": 0.5614, "pu": 0.4386, "pp": 0.0, "raw": [0.58, 0.42, 0.0], "v": "cal-mi-1:9c1d"}
    mk["q"][1]["x"] = {"b": "av_out", "t": "Ruled out (ESPN injury report)", "v": "av-1"}
    reg = [{"id": "nfl.pass_att_l5", "label": "Pass attempts (recent weighted)", "value": 34.2, "definition": "x"}]
    sp.players["nfl:player:espn:401"] = sports_player("nfl", "fc-bbbbbbbb00000001", 264.5, reg)
    sp.players["nfl:player:espn:401"]["forecasts"]["fc-bbbbbbbb00000002"] = sp.players["nfl:player:espn:401"]["forecasts"]["fc-bbbbbbbb00000001"]
    board = recs(env, "nfl")
    rec = board[("Sam Field", "underdog")]
    st, doc = get(env, f"line?sport=nfl&book=underdog&id={rec['offer']['offer_id']}")
    g = doc["grade_inputs"]
    by = common(g, rec, "nfl")
    assert st == 200 and g["model"] == {"name": "sm-1", "version": "sm-1-art-7f3a", "calibration": "cal-mi-1:9c1d", "grade_policy": "sports-gp-1"}
    terms = [k for k, _l, _d in base_fields("nfl")] + ["nfl.pass_att_l5"]
    assert [f["id"] for f in g["factors"] if f["live"]] == terms
    assert [f["id"] for f in g["factors"] if not f["live"]] == ["snap_trend"]   # the snap trend is shown for context only (sn-1 is a shadow)
    assert g["letter"] == rec["scout"]["grade"] == "B" and g["letter_rule"]["lower"] == sports_bands()["B"]
    assert g["stake"]["units"] == 0 and by["prior_n"]["live"] and {x["id"] for x in g["matchup"]} >= {"opponent", "dvp"}
    # the same player's PrizePicks line is held by av-1: no side, chance, letter or stake - the hold's own text and rule
    held = board[("Sam Field", "prizepicks")]
    st, doc = get(env, f"line?sport=nfl&book=prizepicks&id={held['offer']['offer_id']}")
    g = doc["grade_inputs"]
    assert held["scout"]["side"] is None and not ({"side", "chance", "letter", "lean", "stake"} & set(g))
    assert g["hold"] == {"reasons": ["Ruled out (ESPN injury report)"], "rule": "av-1"} and g["projection"] == held["scout"]["projection"]
    assert not [s for s in strings(g) if FORBIDDEN.search(s)]


def test_hedge_words_and_side_names_never_reach_the_block():
    rec = {"scout": {"side": "over", "p_side": 0.6, "projection": 21.0, "grade": "B", "units": 0.0, "stake_reason": "Less is not offered on this line", "hold": None},
           "offer": {"line": 20.5}, "player": {"opponent": "BOS"}, "event": {}, "market": {"stat": "points"}, "state": {"kind": "ok"}}
    p = {"forecasts": {"f": {"model": "sm-1", "inputs": [{"id": "a", "label": "Minutes", "value": 31.0}, {"id": "b", "label": "Status", "value": "Questionable"},
                                                          {"id": "c", "label": "Role (unconfirmed)", "value": 1}]}},
         "context": [{"key": "note", "label": "Availability", "value": "may play"}], "injury": {"status": "questionable", "detail": "Knee"}}
    g = gi_mod.sports({"fid": "f", "st": "points"}, rec, p, "nba")
    assert [f["id"] for f in g["factors"]] == ["a"] and [m["id"] for m in g["matchup"]] == ["opponent"]
    assert g["stake"]["rule"] == "Under is not offered on this line"
    assert not [s for s in strings(g) if FORBIDDEN.search(s)]
    assert gi_mod.sports({}, {"scout": {}, "offer": {}}, None, "nba") is None       # nothing known: no block


# ---------------------------------------------------------------------------------------------------------------- the panel

NODE = shutil.which("node")
RENDER = """
import {readFileSync} from 'node:fs';
import {panels} from %s;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const P = panels({esc, pct: v => (v * 100).toFixed(1) + '%%', when: x => x, sideWord: s => s === 'over' ? 'Over' : 'Under'});
const inp = JSON.parse(readFileSync(process.argv[2], 'utf8'));
console.log(JSON.stringify(inp.map(([r, doc]) => P.grade(r, doc, {}))));
"""


def render(tmp_path, pairs):
    js = tmp_path / "render.mjs"
    js.write_text(RENDER % json.dumps((ROOT / "web" / "upgrade-cards.js").as_uri()), encoding="utf-8")
    data = tmp_path / "pairs.json"
    data.write_text(json.dumps(pairs), encoding="utf-8")
    out = subprocess.run([NODE, str(js), str(data)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_panel_prints_the_block_with_the_record_numbers(env, tmp_path):
    r = env.by("vargo")
    save_inputs(env, r, dict(cm2_inputs(r["proj"]), rating=1.12))
    rec = cs2(env)[("vargo", "underdog")]
    doc = get(env, f"line?book=underdog&id={r['id']}")[1]
    evil = json.loads(json.dumps(doc))
    evil["grade_inputs"]["matchup"][0]["value"] = "<img src=x onerror=alert(1)>"
    moved = json.loads(json.dumps(rec))
    moved["scout"].update(p_side=0.7, grade="A-", units=1.5)                    # the board regraded after this doc was read
    html, none, stale, xss, extra = render(tmp_path, [[rec, doc], [rec, {"record": rec}], [moved, doc], [rec, evil], [dict(rec, extra={"base": {}}), doc]])
    assert none == "" and extra == ""                                            # no block: nothing printed
    assert html.startswith('<section class="h-gi"') and set(re.findall(r'class="([a-z0-9-]+)', html)) <= {c for c in re.findall(r'class="([a-z0-9-]+)', html) if c.startswith("h-gi")}
    order = [html.index(t) for t in ("Behind the grade", "Projection vs line", ">Chance<", ">Side<", ">Letter<", ">Stake<", ">Model<", ">Sample<",
                                     "What moved the projection", "Shown for context", "<caption>Matchup</caption>")]
    assert order == sorted(order)
    assert f"{rec['scout']['projection']:.1f} vs {rec['offer']['line']}" in html and f"{rec['scout']['p_side'] * 100:.1f}%" in html      # 0.1%
    assert ">Over</b>" in html and "<b>B</b>" in html and "Why this letter: chance 57.0% to below 63.0%" in html and "0.5U" in html and "perf-cm2" in html
    live, ctx = html.split("Shown for context")[0], html.split("Shown for context")[1]
    assert live.count('<th scope="row">') == 7 + len(cm2.ARTIFACT["feature_names"]) and "▲" in live and "▼" in live and ">up</span>" in live and ">down</span>" in live
    assert "Rating" in ctx and "Rating" not in live and "Weight 0" in ctx
    assert 'scope="col"' in html and "<th>" not in html                          # semantic tables
    assert not FORBIDDEN.search(re.sub(r"<[^>]+>", " ", html))
    # a doc read for an earlier grade prints the record's own numbers and none of the older grade's inputs
    assert "70.0%" in stale and "1.5U" in stale and "61.2%" not in stale and "What moved the projection" not in stale and "Why this letter" not in stale
    assert "<img" not in xss and "&lt;img src=x" in xss
    # the sample's unit agrees with its count: "1 map" / "1 game", never "1 maps" / "1 games"
    assert f'{doc["grade_inputs"]["sample"]["n"]}</span> maps' in html and doc["grade_inputs"]["effect_note"] in html.replace("&#39;", "'")
    units = []
    for n, unit in ((1, "maps"), (1, "games"), (2, "games"), (0, "maps")):
        d = json.loads(json.dumps(doc))
        d["grade_inputs"]["sample"].update(n=n, unit=unit)
        units.append([rec, d])
    one_map, one_game, two_games, no_maps = render(tmp_path, units)
    assert "1</span> map <" in one_map and "1</span> game <" in one_game and "2</span> games <" in two_games and "0</span> maps <" in no_maps
    css = (ROOT / "web" / "upgrade-next-grade.css").read_text(encoding="utf-8")
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", css))
    assert used <= {"--n-bg", "--n-s1", "--n-s2", "--n-line", "--n-line2", "--n-t1", "--n-t2", "--n-t3", "--n-acc", "--n-over", "--n-under", "--n-warn", "--n-font", "--n-mono"}
    plain = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = [m.strip() for m in re.findall(r"([^{}]+)\{", plain) if not m.strip().startswith("@")]
    assert rules and all(sel.strip().startswith("html[data-ui=next] #u-hub .h-gi") for rule in rules for sel in rule.split(","))
    assert not re.search(r"box-shadow|gradient|#[0-9a-fA-F]{3,8}\b|rgba?\(", plain) and set(re.findall(r"border-radius:\s*([^;]+);", css)) == {"2px"}
    for cls in set(re.findall(r'class="([^"]+)"', html)):
        assert all(c in css for c in cls.split()), cls


def test_lol_card_shows_the_so1_offset_row():
    """so-1 (LoL round 2): a lol-1f+so-1 forecast names its model and shows the map-count offset as its own row."""
    rec = {"scout": {"side": "over", "model_version": "lol-1f+so-1"}}
    out = gi_mod.lol({"member": {}, "gi": {"scope_offset": {"version": "so-1", "multiplier": 0.9048, "c": -0.1}}}, rec)
    rows = [r for r in (out.get("factors") or []) if r and r.get("id") == "scope_offset"]
    assert out["model"]["name"] == "lol-1f+so-1" and rows and rows[0]["label"] == "Map-count offset from settled lines"
    assert rows[0]["value"] == "x0.90" and rows[0]["effect"] == -9.52
