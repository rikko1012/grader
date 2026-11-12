"""The hub card's "Behind the grade" block (owner 2026-11-11: "make sure player cards have all information for grades").

ONE server-built block, the same shape for every sport, on the card's /line doc as doc["grade_inputs"]. DISPLAY ONLY: nothing
here is a grade input and nothing here computes a projection, a chance, a side, a letter or a stake. Every headline number is
the board record's own (record.scout / record.offer), copied; the factor values are the ones the saved forecast holds (CS2: the
forecast row's grader inputs; LoL: the worker's saved forecast inputs; NBA / NFL: the forecast's saved input list from Sports).
A fact that is not saved anywhere is left out (the key is omitted) - never a placeholder, never a hedge word (no-unsure-labels).

Shape (every key optional except v / sport):
  v, sport
  model        {"name": live model name ("perf-cm2", "hs-cm2", "perf-2", "lpf-1", "lol-1f", "lol-1", "sm-1"), "version", "calibration",
                "grade_policy"}
  projection, line, gap (projection minus line), chance (the side's chance, full precision), side ("over" | "under")
  letter       the record's letter; "lean": "Lean Over" / "Lean Under" under the lowest letter
  letter_rule  {"label", "lower", "upper"}: the chance band of that letter, read from the live grade scale (CS2 grader.grade_from_p,
                LoL lol/center/forecast.py BANDS, NBA / NFL sports/model/grade_policy.py BANDS); "bands": the whole scale
  chance_rule  a rule that bounded the chance (the CS2 kills cap), when it did
  stake        {"units", "rule": the grader's / board's own reason text, "band_units": what the chance band stakes before rules}
  factors      [{"id", "label", "value", "effect", "effect_unit", "direction": "up" | "down" | "none", "live": bool}]
                live = the term carries weight in the live model for this prop; live False = shown for context (weight 0)
  effect_note  how to read "effect" for this model
  sample       {"n", "unit", "first", "last", "window_days"}
  matchup      [{"id", "label", "value"}]
  hold         {"reasons": [...], "rule": version tag of the hold}

The one piece of arithmetic: a perf-cm2 term's effect is the model's own addend for that term (cm2.FIT: coef x (feature - mean) /
std on the log scale, shown as a percentage) from the features SAVED with the forecast - the same line of cm2.projection, never a
second projection. LoL effects are the worker's saved effect_pct; a multiplier term's effect is the multiplier itself.
"""
import ast
import importlib.util
import math
import re
from datetime import date
from pathlib import Path

V = 1
HERE = Path(__file__).resolve().parent
HEDGE = re.compile(r"\b(unconfirmed|may|likely|pending|unknown|questionable|doubtful|probable|day[-_ ]to[-_ ]day|uncertain|not validated|no bet|not available|yet)\b", re.I)
SIDE_WORD = {"over": "Over", "under": "Under"}
MAX_CONTEXT = 14
MAX_MATCHUP = 10
DEFINITE_OUT = ("out", "injured_reserve", "suspended")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _put(d, k, v):
    if v is not None and v != "" and v != [] and v != {}:
        d[k] = v


def _direction(effect):
    return "up" if _num(effect) and effect > 0 else "down" if _num(effect) and effect < 0 else "none"


def _factor(fid, label, value, live, effect=None, unit=None):
    """One factor row, or None when it has no value to show."""
    if value is None or value == "" or not label:
        return None
    out = {"id": str(fid), "label": str(label), "value": str(value), "direction": _direction(effect), "live": bool(live)}
    if _num(effect):
        out["effect"] = round(float(effect), 2)
        out["effect_unit"] = unit or "pct"
    return out


def _row(rid, label, value):
    return {"id": rid, "label": label, "value": str(value)} if value not in (None, "", []) else None


def _scrub(o):
    """No hedge / status wording reaches the card: a string carrying one is dropped (and the row it labels with it)."""
    if isinstance(o, str):
        return None if HEDGE.search(o) else o
    if isinstance(o, list):
        return [y for y in (_scrub(x) for x in o) if y is not None]
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            y = _scrub(v)
            if y is None and isinstance(v, str) and k in ("label", "value"):
                return None                      # a factor / matchup row without its label or value is no row
            if y is not None and y != [] and y != {}:
                out[k] = y
        return out
    return o


# ---------------------------------------------------------------------------------------------------------------- grade scales

_SCALE = {}


def _bands_from_cuts(cuts):
    """[(label, lower)] highest first -> [{"letter", "lower", "upper"}] (the top band has no upper bound)."""
    out, upper = [], None
    for label, lo in cuts:
        b = {"letter": label, "lower": round(float(lo), 4)}
        if upper is not None:
            b["upper"] = upper
        out.append(b)
        upper = b["lower"]
    return out


def _cs2_scale():
    """The CS2 letter bands, read off the live grader.grade_from_p itself (bisection on its own answers - no number is retyped
    here), with the units grader.units_for_p stakes at each band's lower bound."""
    from grader import grade_from_p, units_for_p
    cuts, p, step = [], 0.5, 0.0025
    prev = grade_from_p(p)
    while p < 1.0 - 1e-12:
        q = min(1.0, p + step)
        g = grade_from_p(q)
        if g != prev:
            lo, hi = p, q
            for _ in range(50):
                mid = (lo + hi) / 2
                lo, hi = (lo, mid) if grade_from_p(mid) == g else (mid, hi)
            cuts.append((g, round(hi, 6)))
            prev = g
        p = q
    bands = _bands_from_cuts(list(reversed(cuts)))
    for b in bands:
        b["units"] = units_for_p(b["lower"])
    return bands


def _lol_scale():
    """lol/center/forecast.py BANDS ((cut, letter, units), ...) read as a literal from the worker's source: the web process never
    imports lol (lol/tests/test_lol_isolation.py)."""
    tree = ast.parse((HERE / "lol" / "center" / "forecast.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "BANDS" for t in node.targets):
            rows = ast.literal_eval(node.value)
            bands = _bands_from_cuts([(g, cut) for cut, g, _u in rows])
            for b, (_c, _g, u) in zip(bands, rows):
                b["units"] = u
            return bands
    return None


def _sports_scale():
    """sports/model/grade_policy.py BANDS (sports-gp-1), loaded from its file (pure python, no imports): this module never imports
    the sports package (sports/tests/test_sports_isolation.py)."""
    spec = importlib.util.spec_from_file_location("_card_gi_sports_gp", HERE / "sports" / "model" / "grade_policy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return _bands_from_cuts(list(mod.BANDS)), getattr(mod, "VERSION", None)


def scale(sport):
    """The live letter bands of one sport, highest first (cached), or None when they cannot be read."""
    key = "sports" if sport in ("nba", "nfl") else sport
    if key not in _SCALE:
        try:
            _SCALE[key] = _cs2_scale() if key == "cs2" else _lol_scale() if key == "lol" else _sports_scale()[0]
        except Exception:
            _SCALE[key] = None
    return _SCALE[key]


def _letter_block(out, sport, sc, lean):
    """letter / lean / letter_rule from the record's own letter and side (the letter is never re-derived from the chance).
    lean: the row's own grade says it is under the lowest letter (the caller reads it off the row, never off the chance)."""
    side, letter = sc.get("side"), sc.get("grade")
    if not side or sc.get("letter_held"):
        return
    bands = scale(sport) or []
    known = {b["letter"] for b in bands}
    if letter in known:
        out["letter"] = letter
        b = next(x for x in bands if x["letter"] == letter)
        rule = {"label": letter, "lower": b["lower"]}
        _put(rule, "upper", b.get("upper"))
        if _num(b.get("units")):
            out["_band_units"] = b["units"]
    elif lean:
        out["lean"] = f"Lean {SIDE_WORD[side]}"
        rule = {"label": out["lean"]}
        if bands:
            rule["upper"] = bands[-1]["lower"]
    else:
        if letter:
            out["letter"] = letter
        return
    if bands:
        rule["bands"] = [{"letter": b["letter"], "lower": b["lower"]} for b in bands]
        out["letter_rule"] = rule


def _sides(text):
    """A grader reason names the sides More / Less; members read Over / Under (site-consistency rule)."""
    return re.sub(r"\bLess\b", "Under", re.sub(r"\bMore\b", "Over", str(text))) if text else None


def _head(sport, rec, lean=False):
    """The record's own numbers: projection, line, gap, chance, side, letter, stake."""
    sc, of = rec.get("scout") or {}, rec.get("offer") or {}
    out = {"v": V, "sport": sport}
    proj, line = sc.get("projection"), of.get("line")
    _put(out, "projection", proj if _num(proj) else None)
    _put(out, "line", line if _num(line) else None)
    if _num(proj) and _num(line):
        out["gap"] = round(proj - line, 4)
    if sc.get("side") in SIDE_WORD and _num(sc.get("p_side")):
        out["side"], out["chance"] = sc["side"], sc["p_side"]
        _letter_block(out, sport, sc, lean)
        stake = {}
        if _num(sc.get("units")):
            stake["units"] = sc["units"]
        _put(stake, "rule", _sides(sc.get("stake_reason")))
        bu = out.pop("_band_units", None)
        if _num(bu) and sport == "cs2":
            stake["band_units"] = bu
        _put(out, "stake", stake)
    hold = []
    h = sc.get("hold") or {}
    if h.get("reason"):
        hold.append(str(h["reason"]))
    st = rec.get("state") or {}
    if st.get("kind") == "held" and st.get("reason"):
        hold.append(str(st["reason"]))
    if hold:
        out["hold"] = {"reasons": hold}
    return out


def _finish(out):
    out = _scrub(out) or {}
    if not any(k in out for k in ("chance", "projection", "factors", "hold")):
        return None
    return out


# ---------------------------------------------------------------------------------------------------------------- CS2

def _pct(v, d=1):
    return f"{v * 100:.{d}f}%"


def _exp(x):
    return math.exp(x)


# cm-2's fourteen terms (cm2_model.json feature_names; cm1.features): how each saved feature reads on the card. A term the artifact
# gains later still appears (its own name, its raw value) - the list itself always comes from the artifact.
CM2_SHOW = {
    "log_all_rate": ("Kills per round, all maps", lambda x, s: f"{_exp(x):.3f}"),
    "log_environment_rate": (lambda s: f"Kills per round, {'LAN' if s.get('lan') else 'online'} maps", lambda x, s: f"{_exp(x):.3f}"),
    "log_opponent_allowance_ratio": ("Kills the opponent gives up, against the average team", lambda x, s: f"{(_exp(x) - 1) * 100:+.1f}%"),
    "log1p_weighted_rounds": ("Rounds in his sample, recent maps count most", lambda x, s: f"{math.expm1(x):.0f}"),
    "log1p_environment_weighted_rounds": (lambda s: f"Rounds in his {'LAN' if s.get('lan') else 'online'} sample, recent maps count most",
                                          lambda x, s: f"{math.expm1(x):.0f}"),
    "log1p_maps": ("Maps in his sample", lambda x, s: f"{math.expm1(x):.0f}"),
    "lan_indicator": ("Tonight's setting", lambda x, s: "LAN" if x else "Online"),
    "own_elo_over_400": ("Team Elo", lambda x, s: f"{x * 400:.0f}"),
    "opponent_elo_over_400": ("Opponent Elo", lambda x, s: f"{x * 400:.0f}"),
    "elo_difference_over_400": ("Elo gap, team minus opponent", lambda x, s: f"{x * 400:+.0f}"),
    "log_own_historical_rounds_per_map": ("Rounds per map, his team", lambda x, s: f"{_exp(x):.1f}"),
    "log_opponent_historical_rounds_per_map": ("Rounds per map, opponent", lambda x, s: f"{_exp(x):.1f}"),
    "log_opponent_adjusted_player_rate": ("Kills per round, adjusted for the opponents he faced", lambda x, s: f"{_exp(x):.3f}"),
    "log_current_opponent_factor": ("Tonight's opponent, kills given up in the opponent adjustment", lambda x, s: f"{(_exp(x) - 1) * 100:+.1f}%"),
}
CM2_NOTE = ("Effect = that term's own share of the projection in the fitted model, against the model's average input. "
            "The terms are fitted together, so one term's sign is read with the others.")
# perf-2 (perf.project): rate x opponent factor x rounds per map x maps (x headshots per kill)
PERF2_TERMS = (("rate", "Kills per round, tonight's setting", lambda v: f"{v:.3f}", None),
               ("rate_all_envs", "Kills per round, all maps", lambda v: f"{v:.3f}", None),
               ("opp_factor", "Kills the opponent gives up, against the average team", lambda v: f"x{v:.3f}", lambda v: (v - 1) * 100),
               ("rounds_per_map", "Rounds per map", lambda v: f"{v:.1f}", None),
               ("maps", "Maps on this line", lambda v: f"{v:g}", None))
PERF2_NOTE = "Projection = kills per round x opponent factor x rounds per map x maps. Effect = the opponent factor's own change."
# shown for context under a performance-model projection (weight 0: the performance model replaces the old pipeline's projection)
CS2_CONTEXT = (("recent_avg", "Last-10 average, Maps 1-2", lambda v: f"{v:.1f}"),
               ("recent_median", "Last-10 median, Maps 1-2", lambda v: f"{v:.1f}"),
               ("kpr_last5", "Kills per round, last 10 matches", lambda v: f"{v:.3f}"),
               ("rating", "Rating", lambda v: f"{v:.2f}"),
               ("adr", "Damage per round", lambda v: f"{v:.1f}"),
               ("kast", "KAST", lambda v: f"{v:.1f}%"),
               ("round_swing", "Round Swing", lambda v: f"{v:+.2f}%"),
               ("trade_pr", "Trade kills per round", lambda v: f"{v:.3f}"),
               ("p_team_market", "Team win chance, betting markets", lambda v: _pct(v)),
               ("days_since_last_map", "Days since his last map", lambda v: f"{v:g}"))


def _cm2_factors(rec_cm2, hs):
    """The live perf-cm2 terms from the features saved with the forecast, each with the model's own addend for it."""
    import cm2
    names, fit = list(cm2.ARTIFACT["feature_names"]), cm2.FIT
    feats, s = rec_cm2.get("features") or [], rec_cm2.get("inputs") or {}
    if len(feats) != len(names) or not all(_num(v) for v in feats):
        return []
    out = []
    for name, x, b, m, sd in zip(names, feats, fit["coef"], fit["mean"], fit["std"]):
        label, show = CM2_SHOW.get(name, (name.replace("_", " "), lambda v, _s: f"{v:.3f}"))
        if callable(label):
            label = label(s)
        try:
            value = show(x, s)
            eff = (math.exp(b * (x - m) / sd) - 1) * 100 if sd else None
        except (ValueError, OverflowError, ZeroDivisionError):
            continue
        out.append(_factor(name, label, value, True, eff, "pct"))
    if hs and _num(rec_cm2.get("hs_per_kill")):
        if _num(rec_cm2.get("lambda")):
            out.append(_factor("kills_projection", "Kills projection (perf-cm2)", f"{rec_cm2['lambda']:.1f}", True))
        out.append(_factor("hs_per_kill", "Headshots per kill", _pct(rec_cm2["hs_per_kill"]), True))
    return out


def _perf2_factors(pf, hs):
    out = []
    for key, label, show, eff in PERF2_TERMS:
        v = pf.get(key)
        if _num(v):
            out.append(_factor(key, label, show(v), True, eff(v) if eff else None, "pct"))
    if hs and _num(pf.get("hs_per_kill")):
        out.append(_factor("hs_per_kill", "Headshots per kill", _pct(pf["hs_per_kill"]), True))
    return out


def _band_text(rb):
    if not isinstance(rb, dict) or not rb.get("maps") or not _num(rb.get("kpr")):
        return None
    t = f"{rb['kpr']:.3f} kills per round over {rb['maps']} maps"
    if _num(rb.get("ratio")):
        t += f" ({(rb['ratio'] - 1) * 100:+.0f}% against his overall rate)"
    return t


def _band_label(rb):
    r, w = rb.get("opp_rank"), rb.get("band") or 10
    return f"Vs teams ranked #{max(1, r - w)}-{r + w}" if _num(r) else "Vs teams ranked like the opponent"


def cs2(x, rec, saved=None, card=None):
    """x: the board row; rec: its display record (workspace_api.record_of); saved: the saved forecast's parts (workspace_api._saved:
    {"gi": grader inputs, ...}); card: the cs2_card panels (cs2_card_ctx) the doc already carries. No read of its own."""
    out = _head("cs2", rec, lean=(x.get("model_grade") or x.get("grade")) in ("LEAN", "C"))
    sc = rec.get("scout") or {}
    gi = (saved or {}).get("gi") if isinstance((saved or {}).get("gi"), dict) else {}
    card = card if isinstance(card, dict) else {}
    pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
    hs = (rec.get("market") or {}).get("stat") == "hs"
    live_cm2 = pf.get("cm2_live") if isinstance(pf.get("cm2_live"), dict) and not pf["cm2_live"].get("ineligible") else None
    src = str(pf.get("version") or "") if pf and not pf.get("error") and _num(pf.get("lambda")) else str(card.get("source") or "")
    if sc.get("side") and src.startswith("perf") and not src.endswith("-bo3"):
        model = {"name": "hs-cm2" if (src == "perf-cm2" and hs) else src}
        _put(model, "version", sc.get("model_version"))
        out["model"] = model
        factors = []
        if pf and src == "perf-cm2" and live_cm2:
            try:
                factors = _cm2_factors(live_cm2, hs)
                if factors:
                    out["effect_note"] = CM2_NOTE
            except Exception:
                factors = []
        elif pf and src != "perf-cm2":
            factors = _perf2_factors(pf, hs)
            if factors:
                out["effect_note"] = PERF2_NOTE
        # context: the saved forecast's own inputs first, the card panel's copy for a line graded before its forecast was saved
        st, ck = card.get("stats") or {}, card.get("checks") or {}
        ctx = dict({"rating": st.get("rating"), "adr": st.get("adr"), "kast": st.get("kast"), "kpr_last5": st.get("kpr_recent"), "trade_pr": ck.get("trade_pr"),
                    "days_since_last_map": ck.get("days_since_last_map"), "p_team_market": card.get("p_win"),
                    "round_swing": (card.get("hltv") or {}).get("round_swing")}, **{k: v for k, v in gi.items() if _num(v) and k != "round_swing"})
        if src == "perf-cm2" and _num((pf.get("perf2") or {}).get("lambda")):
            factors.append(_factor("perf2_projection", "perf-2 projection", f"{pf['perf2']['lambda']:.1f}", False))
        for key, label, show in CS2_CONTEXT:
            if _num(ctx.get(key)):
                factors.append(_factor(key, label, show(ctx[key]), False))
        rb = gi.get("rank_band") if isinstance(gi.get("rank_band"), dict) else card.get("rank_band")
        if _band_text(rb or {}):
            factors.append(_factor("rank_band", _band_label(rb), _band_text(rb), False,
                                   (rb["ratio"] - 1) * 100 if _num(rb.get("ratio")) else None, "pct"))
        _put(out, "factors", [f for f in factors if f])
        # sample: the maps the projection read and his newest map before the match day
        sample = {}
        n = (live_cm2 or {}).get("inputs", {}).get("maps") if live_cm2 else pf.get("player_maps")
        if _num(n) and n > 0:
            sample.update(n=int(n), unit="maps")
        if _num(pf.get("newest_map_day")):
            try:
                sample["last"] = date.fromordinal(int(pf["newest_map_day"])).isoformat()
            except (ValueError, OverflowError):
                pass
        if live_cm2 and sample:
            try:
                import cm2
                _put(sample, "window_days", (cm2.ARTIFACT.get("feature_constants") or {}).get("window_days"))
            except Exception:
                pass
        _put(out, "sample", sample)
        # the kills cap bounds the chance (grader CONFIG p_cap_kills): said only when this chance sits on it
        try:
            from grader import CONFIG
            cap = CONFIG.get("p_cap_kills")
            if not hs and _num(cap) and _num(out.get("chance")) and out["chance"] >= cap - 5e-4:
                out["chance_rule"] = f"Kills chances are capped at {cap:.0%}."
        except Exception:
            pass
    # matchup: definite facts of this match
    pl, ev = rec.get("player") or {}, rec.get("event") or {}
    env = gi.get("environment") if isinstance(gi.get("environment"), dict) else card.get("environment") if isinstance(card.get("environment"), dict) else {}
    tonight = env.get("tonight") or x.get("tonight")
    opp_rank = gi.get("opp_rank") if _num(gi.get("opp_rank")) else ((card.get("context") or {}).get("opp_ranking") or {}).get("rank")
    rb = gi.get("rank_band") if isinstance(gi.get("rank_band"), dict) else card.get("rank_band") or {}
    setting = None
    if tonight in ("lan", "online"):
        e = env.get(tonight) if isinstance(env.get(tonight), dict) else {}
        setting = ("LAN" if tonight == "lan" else "Online") + (f" - his rate there {e['kpr']:.2f} kills per round over {e['maps']} maps"
                                                               if _num(e.get("kpr")) and e.get("maps") else "")
    rows = [_row("opponent", "Opponent", (pl.get("opponent") or "") + (f" (VRS #{int(opp_rank)})" if pl.get("opponent") and _num(opp_rank) else "")),
            _row("rank_band", _band_label(rb), _band_text(rb)) if rb else None,
            _row("setting", "Setting", setting),
            _row("format", "Series format", f"Best of {ev['best_of']}" if ev.get("best_of") else None),
            _row("period", "Maps counted", (rec.get("market") or {}).get("period_label")),
            _row("event", "Event", ev.get("competition"))]
    _put(out, "matchup", [r for r in rows if r][:MAX_MATCHUP])
    return _finish(out)


# ---------------------------------------------------------------------------------------------------------------- LoL

LOL_GI_KEYS = ("player_rate", "team_factor", "opp_factor", "strength_factor", "p_win", "history_games", "history_since", "history_last_game", "form",
               "lpf1", "calibration", "lambda_lol1")
# the worker's own labels for three indicator terms carry a status word; the card states the same fact plainly
LOL_LABEL = {"lane_missing": "No lane opponent on file", "patch_missing": "No patch on file", "no_history": "No earlier games"}
LOL_NOTE = "Effect = that term's own change to the per-game projection, as saved with the forecast."


def lol_inputs(forecast):
    """The few saved forecast inputs the block reads (workspace_lol keeps them beside the index entry, server side only)."""
    f = forecast if isinstance(forecast, dict) else {}
    inp = f.get("inputs") if isinstance(f.get("inputs"), dict) else {}
    out = {k: inp[k] for k in LOL_GI_KEYS if inp.get(k) is not None}
    m = re.search(r"fit ([0-9a-f]{6,})", str(f.get("model") or ""))
    if m:
        out["fit"] = m.group(1)
    return out


def _mult(fid, label, v, live=True):
    return _factor(fid, label, f"x{v:.2f}", live, (v - 1) * 100, "pct") if _num(v) else None


def lol(ix, rec):
    """ix: the workspace_lol index entry ("member": the member board row's research keys, "gi": lol_inputs of its forecast)."""
    m, gi = ix.get("member") or {}, ix.get("gi") or {}
    out = _head("lol", rec, lean=bool(ix.get("lean")))
    sc = rec.get("scout") or {}
    name = sc.get("model_version")
    panel = m.get("factors") if isinstance(m.get("factors"), dict) else {}
    l1 = gi.get("lpf1") if isinstance(gi.get("lpf1"), dict) else {}
    if sc.get("side") and name:
        model = {"name": str(name)}
        _put(model, "version", str(l1["artifact"])[:16] if name == "lpf-1" and l1.get("artifact") else gi.get("fit"))
        _put(model, "calibration", (gi.get("calibration") or {}).get("version") if isinstance(gi.get("calibration"), dict) else None)
        out["model"] = model
        factors = []
        if name == "lpf-1" and l1:
            base = l1.get("lambda_lol1f")
            if _num(base):
                factors.append(_factor("lol1f_projection", "Starting projection (lol-1f)", f"{base:.2f}", True))
                if _num(sc.get("projection")):
                    factors.append(_factor("lpf1_correction", "Projection after the full-history correction (lpf-1)", f"{sc['projection']:.2f}", True,
                                           sc["projection"] - base, "stat"))
            if _num(l1.get("p_win_series_model")):
                factors.append(_factor("p_win_series", "Series win chance, model", _pct(l1["p_win_series_model"]), True))
            if _num(l1.get("p_decider")):
                factors.append(_factor("p_decider", "Chance of a deciding game", _pct(l1["p_decider"]), True))
            if l1.get("patch_assumed"):
                factors.append(_factor("patch", "Patch", l1["patch_assumed"], True))
        if _num(gi.get("player_rate")):
            factors.append(_factor("player_rate", "Player share of team output" if gi.get("form") == "share" else "Player per-game rate", f"{gi['player_rate']:.2f}", True))
        factors += [_mult("team_factor", "Team pace factor", gi.get("team_factor")), _mult("opp_factor", "Opponent factor", gi.get("opp_factor")),
                    _mult("strength_factor", "Match-strength factor", gi.get("strength_factor"))]
        tw = m.get("team_win") if isinstance(m.get("team_win"), dict) else {}
        pw = gi.get("p_win") if _num(gi.get("p_win")) else tw.get("elo")
        if _num(pw):
            factors.append(_factor("p_win", "Team win chance, model (Elo)", _pct(pw), True))
        in_grade = panel.get("model") == "lol-1f"
        for f in panel.get("factors") or []:            # the worker's own A7 term list (lol/factors.py FEATS), every term
            if not isinstance(f, dict) or not f.get("key"):
                continue
            eff = f.get("effect_pct") if in_grade else None
            v = f.get("value")
            factors.append(_factor(f["key"], LOL_LABEL.get(f["key"]) or str(f.get("factor") or f["key"]).capitalize(),
                                   f"{v:.2f}" if _num(v) else None, in_grade and _num(eff), eff, "pct"))
        mk = tw.get("market") if isinstance(tw.get("market"), dict) else {}
        if mk.get("found") and _num(mk.get("p")):
            factors.append(_factor("p_win_market", "Team win chance, market", _pct(mk["p"]), False))
        if _num(m.get("recent_per_game")):
            factors.append(_factor("recent_per_game", f"Recent average a game, last {m.get('recent_games')} games" if m.get("recent_games") else "Recent average a game",
                                   f"{m['recent_per_game']:.2f}", False))
        if name == "lpf-1" and _num(gi.get("lambda_lol1")):
            factors.append(_factor("lol1_projection", "lol-1 projection", f"{gi['lambda_lol1']:.2f}", False))
        factors = [f for f in factors if f]
        if factors:
            out["factors"] = factors
            if any("effect" in f for f in factors):
                out["effect_note"] = LOL_NOTE
        sample = {}
        if _num(gi.get("history_games")) and gi["history_games"] > 0:
            sample.update(n=int(gi["history_games"]), unit="games")
            _put(sample, "first", str(gi["history_since"])[:10] if gi.get("history_since") else None)
            _put(sample, "last", str(gi["history_last_game"])[:10] if re.match(r"\d{4}-\d\d-\d\d", str(gi.get("history_last_game") or "")) else None)
        _put(out, "sample", sample)
    pl, ev = rec.get("player") or {}, rec.get("event") or {}
    rows = [_row("opponent", "Opponent", pl.get("opponent")), _row("format", "Series format", f"Best of {ev['best_of']}" if ev.get("best_of") else None),
            _row("period", "Games counted", m.get("scope_games")), _row("role", "Role", pl.get("role")), _row("event", "Event", ev.get("competition")),
            _row("line_type", "Line type", m.get("line_type_note"))]
    _put(out, "matchup", [r for r in rows if r][:MAX_MATCHUP])
    return _finish(out)


# ---------------------------------------------------------------------------------------------------------------- NBA / NFL

SPORTS_MATCHUP_GROUP = re.compile(r"matchup|defen|pace|opponent", re.I)
SPORTS_SAMPLE_ID = "prior_n"
SPORTS_HOLD_RULE = {"av-1": "av-1", "cap-3": "cap-3"}


def _val(v):
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if _num(v):
        return f"{v:.2f}" if abs(v) < 10 and v != int(v) else f"{v:.1f}" if v != int(v) else f"{int(v)}"
    return None if v in (None, "") else str(v)


def sports(x, rec, player, lg):
    """x: the workspace_sports index entry; player: the trimmed player file the card already read (forecast inputs, context, the
    matchup engine's rows). No read of its own."""
    out = _head(lg, rec, lean=(rec.get("scout") or {}).get("grade") == "Lean")
    sc = rec.get("scout") or {}
    p = player if isinstance(player, dict) else {}
    fid = x.get("fid") or x.get("lf")
    fc = (p.get("forecasts") or {}).get(fid) if fid else None
    fc = fc if isinstance(fc, dict) else {}
    name = sc.get("model_version") or fc.get("model")
    if name and _num(sc.get("projection")):
        model = {"name": str(name)}
        _put(model, "version", fc.get("artifact"))
        _put(model, "calibration", x.get("cal") or fc.get("calibration"))
        _put(model, "grade_policy", x.get("gp") or fc.get("grade_policy"))
        out["model"] = model
        factors, sample = [], {}
        for i in fc.get("inputs") or []:                # the forecast's own saved input list (sports/forecast_detail.py): every term the model read
            if not isinstance(i, dict) or i.get("value") is None:
                continue
            fid_ = i.get("id") or re.sub(r"[^a-z0-9]+", "_", str(i.get("label") or "").lower()).strip("_")
            factors.append(_factor(fid_, i.get("label"), _val(i["value"]), True))
            if i.get("id") == SPORTS_SAMPLE_ID and _num(i["value"]) and i["value"] > 0:
                sample.update(n=int(i["value"]), unit="games")
        for c in fc.get("contributions") or []:         # a model that publishes per-term contributions shows them as effects
            if isinstance(c, dict) and c.get("label") and _num(c.get("value")):
                factors.append(_factor("c_" + re.sub(r"[^a-z0-9]+", "_", str(c["label"]).lower()).strip("_"), c["label"], f"{c['value']:+.2f}", True, c["value"], "stat"))
        eng = ((p.get("engine") or {}).get("by_stat") or {}).get(x.get("st")) or []
        ctx = [r for r in eng if isinstance(r, dict) and r.get("label") and r.get("value") is not None and not SPORTS_MATCHUP_GROUP.search(str(r.get("group") or ""))]
        for r in ctx[:MAX_CONTEXT]:                     # the matchup engine's values: weight 0 (sports/player_context.py)
            factors.append(_factor(r.get("key") or r["label"], r["label"] + (f" (n={r['n']})" if r.get("n") else ""), _val(r["value"]), False))
        _put(out, "factors", [f for f in factors if f])
        _put(out, "sample", sample)
    pl = rec.get("player") or {}
    rows = [_row("opponent", "Opponent", pl.get("opponent")),
            _row("venue", "Venue", "Home" if p.get("next_home") is True else "Away" if p.get("next_home") is False else None)]
    for c in p.get("context") or []:
        if not isinstance(c, dict) or not c.get("label"):
            continue
        if c.get("key") == "teammates_out":
            outs = [str(i.get("player")) for i in c.get("items") or [] if isinstance(i, dict) and i.get("player") and str(i.get("status") or "").lower() in DEFINITE_OUT]
            if outs:
                rows.append(_row("teammates_out", "Teammates out", f"{len(outs)}: " + ", ".join(outs[:8])))
        elif c.get("key") == "venue":
            continue
        else:
            rows.append(_row(str(c.get("key") or c["label"]), c["label"], _val(c.get("value"))))
    inj = p.get("injury") if isinstance(p.get("injury"), dict) else {}
    if str(inj.get("status") or "").lower() in DEFINITE_OUT:
        rows.append(_row("injury", "Injury report", " - ".join(str(v).replace("_", " ").capitalize() for v in (inj.get("status"), inj.get("detail")) if v)))
    eng = ((p.get("engine") or {}).get("by_stat") or {}).get(x.get("st")) or (p.get("engine") or {}).get("rows") or []
    for r in [r for r in eng if isinstance(r, dict) and r.get("label") and r.get("value") is not None and SPORTS_MATCHUP_GROUP.search(str(r.get("group") or ""))][:6]:
        rows.append(_row(str(r.get("key") or r["label"]), r["label"] + (f" (n={r['n']})" if r.get("n") else ""), _val(r["value"])))
    _put(out, "matchup", [r for r in rows if r][:MAX_MATCHUP])
    if out.get("hold") and x.get("hv") in SPORTS_HOLD_RULE:
        out["hold"]["rule"] = SPORTS_HOLD_RULE[x["hv"]]
    return _finish(out)
