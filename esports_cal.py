"""ec-1 on CS2: the printed chance = the rate such chances have hit (gradecore/chance_cal.py, owner decision 2026-11-05).

DISPLAY LAYER ONLY, applied after the pick log: the CS2 projection model (tag 3007c59b74), the grader, the board's stored
grades (BOARD), the pick log and every logged forecast keep the model's own chance, letter and stake exactly as computed. What
members see is rebuilt here from those saved values:

    p            the calibrated chance of the model's side (chance_cal.side_chance; the side is never flipped)
    model_grade  the existing letter thresholds on that chance (grader.grade_from_p); under 55% = Lean
    units        min(the model's own stake, grader.units_for_p(calibrated chance)) - never raised; 0 under 55%
    grade        the letter when it carries a stake, else LEAN (the verdict's existing rule)
    reason       the chance in its words is the calibrated one
and the raw values ride along (p_raw, grade_raw, units_raw, model_grade_raw) with cal_v = "ec-1:<artifact sha>".

Surfaces: server.board_payload (board, slips, Studio, compare, posts, alerts, EV, workspace - they all read board rows) and
the player card (server.grade: verdict, the Over / Under block, the 'Nearby lines' chips) right after its pick log.
The public record's stated chance going forward: forward_export() maps the P1 rows logged after the layer went live (the
per-tag start times saved by mark_live) through the tables that were live then; rows logged before keep their logged chance.

Off (SCOUT_CAL_CHANCE unset / 0, or no valid artifact): every function returns its input unchanged."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from gradecore import chance_cal as cc

LETTERS = ("A+", "A", "A-", "B+", "B", "B-")
SIDE = {"More": "over", "Less": "under"}
LIVE_FILE = "chance_cal_live.json"
_live = {"path": None, "doc": None, "mt": None}


def _obj():
    return cc.load() if cc.enabled() else None


def on():
    return _obj() is not None


def _letter(p):
    from grader import grade_from_p
    g = grade_from_p(p)
    return g if g in LETTERS else None


def _units(p):
    from grader import units_for_p
    return units_for_p(p)


def _pct(x):
    return f"{x:.1%}"


def _reword(text, raw, cal):
    """The chance inside a verdict's reason, in the calibrated words (the side's chance and, where the reason names it, the
    other side's)."""
    if not isinstance(text, str) or raw is None:
        return text
    a, b = _pct(raw), _pct(1 - raw)
    if a == b:
        return text.replace(a, _pct(cal))
    return text.replace(a, "\x00A").replace(b, "\x00B").replace("\x00A", _pct(cal)).replace("\x00B", _pct(1 - cal))


def _confidence(p):
    return "high" if p >= 0.68 else "medium" if p >= 0.63 else "small" if p >= 0.57 else "low"


def calibrate_verdict(x, prop, obj=None, tag=None):
    """One verdict-shaped dict (board row, card verdict, ladder chip: side More / Less, p, grade, units, reason, ...) -> a
    NEW dict with the calibrated chance, letter and stake, or x itself when the layer does not apply."""
    obj = obj if obj is not None else _obj()
    if not obj or not isinstance(x, dict) or x.get("cal_v") or x.get("shadow") or x.get("p") is None:
        return x
    side = SIDE.get(x.get("side"))
    if side is None:
        return x
    raw = float(x["p"])
    c = cc.side_chance("cs2", prop, side, raw, obj)
    if c is None:
        return x
    out = dict(x, p=round(c, 3), p_raw=x["p"], cal_v=tag or cc.tag(obj))
    if "reason" in x:
        out["reason"] = _reword(x.get("reason"), raw, c)
    if "confidence" in x:
        out["confidence"] = _confidence(c)
    if x.get("state") == "SCOUT" or x.get("grade") == "SCOUT":
        return out                                   # scouting view: no letter, no stake - only the chance is printed
    letter = _letter(c)
    units_raw = float(x.get("units") or 0.0)
    units = min(units_raw, _units(c)) if letter else 0.0
    out.update(grade_raw=x.get("grade"), units_raw=x.get("units"), units=units, grade=letter if (letter and units > 0) else "LEAN")
    if "model_grade" in x:
        out["model_grade_raw"] = x.get("model_grade")
        out["model_grade"] = letter or "LEAN"
    if "exact_grade" in x:
        out["exact_grade"] = letter or "LEAN"
    if "stake_reason" in x or "model_grade" in x:
        if letter and not units and x.get("available", True) is not False:
            out["stake_reason"] = x.get("stake_reason") or "the model's own stake on this line is 0 units (calibration never raises one)"
        elif not letter:
            out["stake_reason"] = None
    if units_raw > 0 and not units and "reason" in x:
        out["reason"] = (f"{out.get('reason') or ''} · calibrated to settled results: {_pct(c)} "
                         f"(model {_pct(raw)}) - Lean, 0 units").lstrip(" ·")
    return out


def cs2_row(x, obj=None, tag=None):
    """A board row (server.board_payload) as members see it."""
    if not isinstance(x, dict):
        return x
    return calibrate_verdict(x, x.get("prop"), obj, tag)


def cs2_rows(rows):
    obj = _obj()
    if not obj:
        return rows
    tag = cc.tag(obj)
    return [cs2_row(x, obj, tag) for x in rows or []]


def _grade_block(g, side, obj, tag):
    """The card's Over / Under block (res["grade"]) with calibrated chances, letters and stakes; raw kept beside."""
    raw_more, raw_less = g.get("p_more"), g.get("p_less")
    if raw_more is None or raw_less is None:
        return g
    s = raw_more + raw_less
    q = raw_more / s if s > 0 else 0.5
    pr = cc.pair("cs2", g.get("prop"), q, side, obj)
    if pr is None:
        return g
    cm, cl = pr
    out = dict(g, p_more=round(cm, 3), p_less=round(cl, 3), p_more_raw=raw_more, p_less_raw=raw_less, cal_v=tag)
    for c, kg, ku, kf in ((cm, "grade", "units", "units_free"), (cl, "grade_less", "units_less", "units_free_less")):
        letter = _letter(c)
        if g.get(kg) in LETTERS + ("C",):
            out[kg + "_raw"] = g.get(kg)
            out[kg] = letter or "C"
        for k in (ku, kf):
            if isinstance(g.get(k), (int, float)) and not isinstance(g.get(k), bool):
                out[k + "_raw"] = g[k]
                out[k] = min(float(g[k]), _units(c)) if letter else 0.0
    return out


def cs2_card(res):
    """The player card (server.grade's res) right after its pick log: new dicts for the grade block, the verdict and the
    'Nearby lines' chips (the logged row was built before this and keeps the model's own values)."""
    obj = _obj()
    if not obj or not isinstance(res, dict) or res.get("cal_v"):
        return res
    tag = cc.tag(obj)
    out = dict(res, cal_v=tag)
    g = res.get("grade") if isinstance(res.get("grade"), dict) else None
    v = res.get("verdict") if isinstance(res.get("verdict"), dict) else None
    prop = (g or {}).get("prop")
    if g and "p_more" in g:
        side = SIDE.get((v or {}).get("side")) or ("over" if (g.get("p_more") or 0) >= (g.get("p_less") or 0) else "under")
        out["grade"] = _grade_block(g, side, obj, tag)
    if v:
        out["verdict"] = calibrate_verdict(v, prop, obj, tag)
    if isinstance(res.get("ladder"), list):
        out["ladder"] = [calibrate_verdict(x, prop, obj, tag) if isinstance(x, dict) else x for x in res["ladder"]]
    if isinstance(res.get("dist_view"), dict):
        out["dist_view"] = dict(res["dist_view"], note="The model's own distribution for this total, before calibration to settled results")
    return out


# ------------------------------------------------------------------------------------------------ going forward (record)
def _live_path(data_dir=None):
    if data_dir is not None:
        return Path(data_dir) / LIVE_FILE
    return _live["path"]


def configure(data_dir):
    _live.update(path=Path(data_dir) / LIVE_FILE, doc=None, mt=None)


def mark_live(data_dir, now=None):
    """Called once when the server starts with the layer on: records when THIS artifact first went live, with its tables, so
    the record maps exactly the forecasts logged under it. Existing entries are never changed."""
    configure(data_dir)
    obj = _obj()
    if not obj:
        return None
    p = _live_path()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {"version": cc.VERSION, "tags": []}
    tag = cc.tag(obj)
    if any(e.get("tag") == tag for e in doc.get("tags") or []):
        return doc
    at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    doc.setdefault("tags", []).append({"tag": tag, "from": at, "games": {g: {"tables": d.get("tables"), "letters": d.get("letters")}
                                                                         for g, d in obj["games"].items()}})
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    _live.update(doc=None, mt=None)
    return doc


def live_doc():
    p = _live_path()
    if p is None:
        return None
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return None
    if mt != _live["mt"]:
        try:
            _live.update(doc=json.loads(p.read_text(encoding="utf-8")), mt=mt)
        except (OSError, ValueError):
            return None
    return _live["doc"]


def forward_export(export, doc=None):
    """The stored P1 export with each row LOGGED after the layer went live printed at the calibrated chance that was shown
    (the tables live at its logging time); earlier rows keep their logged chance. p_raw keeps the model's own. A new dict."""
    doc = doc if doc is not None else (live_doc() if cc.enabled() else None)
    tags = sorted((doc or {}).get("tags") or [], key=lambda e: e.get("from") or "")
    if not tags or not isinstance(export, dict):
        return export

    def entry(logged):
        e = None
        for t in tags:
            if logged and str(logged) >= t["from"]:
                e = t
        return e

    def one(r):
        e = entry(r.get("logged"))
        if e is None or r.get("p") is None:
            return r
        pr = cc.pair("cs2", r.get("prop"), float(r["p"]), None, {"games": e["games"]})
        return r if pr is None else dict(r, p=round(pr[0], 4), p_raw=r["p"], cal_v=e["tag"])
    return dict(export, rows=[one(r) for r in export.get("rows") or []], pushes=[one(r) for r in export.get("pushes") or []])


def status():
    d = cc.status()
    d["live"] = [{"tag": e.get("tag"), "from": e.get("from")} for e in ((live_doc() or {}).get("tags") or [])]
    return d
