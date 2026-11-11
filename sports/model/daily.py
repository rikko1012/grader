"""grades-daily (REGISTER.md amendment A9a 2-4): the daily cal-mi fit, the daily grade check and the containment
decision, ON the sports service (scheduler job `grades-daily`, a child process; flag SPORTS_INGEST - settlement writes
the scorecard it reads).

    python -m sports.model.daily run [--force]     what the scheduler runs (exits 0 when not due)
    python -m sports.model.daily status            last run, next due check, switches

When it runs: at most ONCE per game-day (UTC date of now - 10 h, the A8 4 boundary), not before
SPORTS_DAILY_AFTER_UTC_H (10) UTC = after the overnight settlement, and only when learning.db holds a settlement newer
than the last run. Otherwise it exits at once ("not due: ...") without importing numpy.

Steps (one child, in order):
  1. cal-mi fit (SPORTS_CAL_AUTOFIT=0 skips): the exact sports.model.cal fit path (rows_from_learning -> walk -> report
     (A8 6 + A8a) -> weights_obj) -> model/cal_mi.json, with hysteresis: ON the first day the rule passes; an ON market
     goes OFF only after OFF_AFTER (2) consecutive daily fails. One line per market in model/cal_mi_log.jsonl.
  2. gradecheck (SPORTS_GRADECHECK_DAILY=0 skips 2-3): gradecheck.run (gc-1) on every league, reading the weights step 1
     wrote -> model/gradecheck.json (compact) + model/gradecheck/gradecheck_<game-day>.json (newest KEEP kept).
  3. containment: sports/containment.apply_verdicts (A9 5 verdict per league) -> model/containment.json (+ its log).
  4. headline -> admin_learning.json "grades" (GET /api/sports/admin/model/learning) and model/daily_state.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "ga-1"
REGISTER = "sports/research/REGISTER.md A9a"
OFF_AFTER = 2
KEEP = 30
DAY_SHIFT_H = 10
MAX_LINES = 400
_OFF = {"0", "false", "no", "off"}


def _on(name):
    return os.environ.get(name, "1").strip().lower() not in _OFF


def autofit_on():
    return _on("SPORTS_CAL_AUTOFIT")


def gradecheck_on():
    return _on("SPORTS_GRADECHECK_DAILY")


def after_hour():
    try:
        return max(0, min(23, int(os.environ.get("SPORTS_DAILY_AFTER_UTC_H", "10"))))
    except ValueError:
        return 10


def model_dir():
    from .. import config
    return config.DATA_DIR / "model"


def paths():
    from .. import config
    base, m = config.DATA_DIR, model_dir()
    return {"learning": m / "learning.db", "inputs": m / "forecasts.db", "store": base / "canonical.db",
            "history": m / "history.db", "games": base / "games.db", "state": m / "daily_state.json",
            "cal_log": m / "cal_mi_log.jsonl", "gradecheck": m / "gradecheck.json", "dated": m / "gradecheck"}


def game_day(now):
    return (now - timedelta(hours=DAY_SHIFT_H)).date().isoformat()


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _atomic(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def latest_settled(learning):
    """Newest settled_at of a current scorecard row (read-only), or None."""
    p = Path(learning)
    if not p.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            row = con.execute("SELECT MAX(settled_at) FROM scorecard WHERE superseded = 0").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def due(now, state, latest):
    """-> (True, None) or (False, why). Cheap: no numpy, one MAX() query."""
    day = game_day(now)
    if now.hour < after_hour():
        return False, f"before {after_hour():02d}:00 UTC (overnight settlement)"
    if (state or {}).get("last_day") == day:
        return False, f"already ran for game-day {day}"
    if latest is None:
        return False, "no settled props in learning.db"
    if (state or {}).get("data_as_of") and latest <= state["data_as_of"]:
        return False, f"nothing new settled since {state['data_as_of']}"
    return True, None


# ================================================================================================ step 1: cal-mi fit
def hysteresis(rep, prev_params, fails):
    """A9a 2. rep: cal.report() per market; prev_params: the weights in use; fails: {market: consecutive fails while
    ON}. -> {market: {"on", "fails", "passed", "before"}}."""
    out = {}
    for k, r in sorted(rep.items()):
        passed = bool(r.get("on"))
        before = bool((prev_params.get(k) or {}).get("on"))
        if passed:
            on, n = True, 0
        elif before:
            n = int(fails.get(k, 0)) + 1
            on = n < OFF_AFTER
            n = n if on else 0
        else:
            on, n = False, 0
        out[k] = {"on": on, "fails": n, "passed": passed, "before": before}
    return out


def autofit(p, day, state, log=print):
    from . import cal
    lp, ip = p["learning"], p["inputs"]
    lcon = sqlite3.connect(f"file:{Path(lp).as_posix()}?mode=ro", uri=True, timeout=30)
    icon = sqlite3.connect(f"file:{Path(ip).as_posix()}?mode=ro", uri=True, timeout=30) if Path(ip).exists() else None
    try:
        rows = cal.rows_from_learning(lcon, icon)
    finally:
        lcon.close()
        if icon is not None:
            icon.close()
    if not rows:
        return {"skipped": "no cal-mi rows"}
    rep = cal.run_eval(rows)
    prev, prev_src = cal.load(force=True)
    pp = (prev or {}).get("params") or {}
    # a fail streak continues only on weights this job wrote; a manual `fit` (no "autofit" mark) starts it at 0
    fails = {k: v for k, v in ((state or {}).get("cal_fails") or {}).items() if "autofit" in (pp.get(k) or {})}
    dec = hysteresis(rep, pp, fails)
    rep2 = {k: dict(r, on=dec[k]["on"]) for k, r in rep.items()}
    obj = cal.weights_obj(rows, rep2, "daily autofit walk-forward (REGISTER A9a 2)",
                          max((r["settled_at"] or "" for r in rows), default=None))
    for k, d in dec.items():                            # record why an ON market is still ON after a fail
        if k in obj["params"]:
            obj["params"][k]["autofit"] = {"passed": d["passed"], "fails_in_a_row": d["fails"]}
    obj["sha"] = cal._sha(obj["params"])
    obj = cal.validate(obj)
    _atomic(cal.weights_path(), json.dumps(obj, indent=1))
    cal.reset_cache()
    at = _iso(datetime.now(timezone.utc))
    lines = []
    for k, d in dec.items():
        r = rep[k]
        lines.append({"date": day, "at": at, "market": k, "n": r.get("scored"), "props": r.get("props"),
                      "games": r.get("games"), "days": r.get("days"), "verdict": r.get("verdict"), "why": r.get("why"),
                      "passed": d["passed"], "on_before": d["before"], "on_after": d["on"],
                      "fails_in_a_row": d["fails"], "before_source": prev_src, "sha": obj["sha"]})
    p["cal_log"].parent.mkdir(parents=True, exist_ok=True)
    with open(p["cal_log"], "a", encoding="utf-8") as f:
        for x in lines:
            f.write(json.dumps(x, separators=(",", ":"), default=float) + "\n")
    return {"sha": obj["sha"], "rows": len(rows), "on": sorted(k for k, d in dec.items() if d["on"]),
            "switched_on": sorted(k for k, d in dec.items() if d["on"] and not d["before"]),
            "switched_off": sorted(k for k, d in dec.items() if d["before"] and not d["on"]),
            "held_on_after_fail": sorted(k for k, d in dec.items() if d["on"] and not d["passed"]),
            "fails": {k: d["fails"] for k, d in dec.items() if d["fails"]}}


# ============================================================================================ step 2-4: grade check
def headline(res):
    """Per league: the A9 5 verdict + the letter table (stated vs hit, n, CI) - what the admin page shows."""
    out = {}
    for lg, rep in sorted(((res or {}).get("leagues") or {}).items()):
        v = rep.get("verdict") or {}
        lt = rep.get("letter") or {}
        out[lg] = {"verdict": v.get("verdict"), "why": v.get("why"), "n": v.get("n"), "games": v.get("games"),
                   "game_days": v.get("game_days"), "hit": v.get("hit"), "stated": v.get("stated"), "ci": v.get("ci"),
                   "letters": {g: {k: (lt.get(g) or {}).get(k) for k in ("n", "stated", "hit", "ci")}
                               for g in ("A", "B", "C", "Lean", "AB") if g in lt},
                   "first_day": rep.get("first_day"), "last_day": rep.get("last_day"),
                   "cal_mi_on": ((rep.get("cal_mi") or {}).get("on") or []),
                   "chance": "cal-mi walk-forward on markets ON, else raw sm-1 (A9 2)"}
    return out


READABLE = ("verdict", "letter", "letter_all_leans", "cal_mi", "overall", "counts", "stat")


def compact(obj, depth=0, key=None):
    """The stored gradecheck JSON, under MAX_LINES at production shape (both leagues, every stat): one line per league
    section, except the sections the owner reads (READABLE), which keep one line per slice. Same content as the CLI's
    gradecheck.dumps - only the line breaks differ."""
    pad = " " * depth
    brk = depth < 3 or (depth == 3 and key in READABLE)  # top -> leagues -> league -> section -> slice
    if isinstance(obj, dict) and brk and obj:
        parts = [f"{pad} {json.dumps(str(k))}: {compact(v, depth + 1, k)}" for k, v in obj.items()]
        return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
    return json.dumps(obj, default=float, separators=(", ", ": "))


def write_gradecheck(p, res, day):
    text = compact(res)
    n = text.count("\n") + 1
    _atomic(p["gradecheck"], text)
    d = Path(p["dated"])
    d.mkdir(parents=True, exist_ok=True)
    _atomic(d / f"gradecheck_{day}.json", text)
    old = sorted(d.glob("gradecheck_*.json"))
    for x in old[:-KEEP]:
        try:
            x.unlink()
        except OSError:
            pass
    return {"lines": n, "bytes": len(text.encode("utf-8")), "kept": min(len(old), KEEP)}


def publish(summary):
    """Put the headline into admin_learning.json now (pipeline.write_snapshot carries it on every rewrite)."""
    try:
        from . import pipeline
        ap = pipeline.api_dir() / "admin_learning.json"
        d = _read_json(ap) or {"scorecard": [], "forward_gate": [], "diagnostics": []}
        d["grades"] = summary
        pipeline._atomic_write(ap, d)
    except Exception as e:                              # noqa: BLE001 - the admin copy never fails the job
        return f"{type(e).__name__}: {str(e)[:120]}"
    return None


def admin_view():
    """{"headline", "containment", "daily", "cal_mi_log"} for the admin route / admin_learning.json (pure json)."""
    from .. import containment
    p = paths()
    st = _read_json(p["state"]) or {}
    tail = []
    try:
        with open(p["cal_log"], encoding="utf-8") as f:
            tail = [json.loads(x) for x in f.readlines()[-40:]]
    except (OSError, ValueError):
        pass
    obj, _ = containment.load()
    return {"version": VERSION, "register": REGISTER, "headline": st.get("headline"),
            "generated_at": st.get("gradecheck_at"), "game_day": st.get("last_day"),
            "containment": containment.snapshot(), "containment_log": ((obj or {}).get("log") or [])[-20:],
            "cal_mi_log": tail, "daily": {k: st.get(k) for k in ("last_day", "last_run_at", "data_as_of", "last_skip",
                                                                 "cal", "cal_fails", "containment", "gradecheck_file",
                                                                 "attempt_day", "cal_attempt", "last_error")},
            "switches": {"SPORTS_CAL_AUTOFIT": autofit_on(), "SPORTS_GRADECHECK_DAILY": gradecheck_on(),
                         "SPORTS_DAILY_AFTER_UTC_H": after_hour()}}


def run(now=None, force=False, log=print):
    now = now or datetime.now(timezone.utc)
    p = paths()
    state = _read_json(p["state"]) or {}
    latest = latest_settled(p["learning"])
    ok, why = (True, None) if force else due(now, state, latest)
    day = game_day(now)
    if not ok:
        state["last_skip"] = {"at": _iso(now), "why": why}
        if state.get("publish_pending"):
            err = publish(admin_view())
            state["publish_pending"] = bool(err)
            _atomic(p["state"], json.dumps(state, indent=1))
            return dict(ran=False, why=why, **({"publish_error": err} if err else {}))
        _atomic(p["state"], json.dumps(state, indent=1))
        return {"ran": False, "why": why}
    out = {"ran": True, "game_day": day, "data_as_of": latest}
    t0 = time.time()
    # Resume a failed read/report stage without counting another calibration decision
    # on the same day. Persist BEFORE entering the weight writer: a killed/partially
    # completed fit is ambiguous and must never silently advance hysteresis twice.
    attempt = state.get("cal_attempt") if state.get("attempt_day") == day else None
    if attempt is not None and not force:
        out["cal"] = attempt.get("result") or {"error": "previous calibration attempt interrupted; retained for review"}
    elif autofit_on():
        state.update(attempt_day=day, cal_attempt={"at": _iso(now), "status": "running"})
        _atomic(p["state"], json.dumps(state, indent=1, default=float))
        try:
            out["cal"] = autofit(p, day, state, log)
            state["cal_fails"] = (out["cal"] or {}).get("fails") or {}
        except Exception as e:                          # noqa: BLE001 - a failed fit keeps yesterday's weights
            out["cal"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    else:
        out["cal"] = {"skipped": "SPORTS_CAL_AUTOFIT=0"}
    state.update(attempt_day=day, cal_attempt={"at": _iso(now), "status": "error" if out["cal"].get("error") else "done",
                                             "result": out["cal"]})
    _atomic(p["state"], json.dumps(state, indent=1, default=float))
    if out["cal"].get("error"):
        out["gradecheck"] = {"skipped": "calibration attempt incomplete; no new verdict applied"}
    elif gradecheck_on():
        from . import gradecheck
        from .. import containment
        try:
            res = gradecheck.run(p["learning"], p["inputs"], p["store"], p["history"], p["games"])
        except Exception as e:  # report reads retry through scheduler backoff; no second calibration decision
            res = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        if res.get("error"):
            out["gradecheck"] = {"error": res["error"]}
        else:
            out["gradecheck"] = write_gradecheck(p, res, day)
            out["headline"] = headline(res)
            out["containment"] = containment.apply_verdicts(res, day, f"daily gradecheck {day} (A9a 4)")
            state["headline"] = out["headline"]
            state["gradecheck_at"] = res.get("generated_at")
            state["gradecheck_file"] = out["gradecheck"]
            state["containment"] = out["containment"]
    else:
        out["gradecheck"] = {"skipped": "SPORTS_GRADECHECK_DAILY=0"}
    out["run_s"] = round(time.time() - t0, 1)
    errors = {name: value["error"] for name in ("cal", "gradecheck")
              if isinstance((value := out.get(name)), dict) and value.get("error")}
    state.update(last_run_at=_iso(now), cal=out.get("cal"), last_skip=None,
                 last_error={"at": _iso(now), "stages": errors} if errors else None)
    if errors:
        out["error"] = errors
    else:
        state.update(last_day=day, data_as_of=latest)
    state["publish_pending"] = True
    _atomic(p["state"], json.dumps(state, indent=1, default=float))
    err = publish(admin_view())
    if err:
        out["publish_error"] = err
    else:
        state["publish_pending"] = False
        _atomic(p["state"], json.dumps(state, indent=1, default=float))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m sports.model.daily")
    ap.add_argument("cmd", choices=["run", "status"])
    ap.add_argument("--force", action="store_true", help="run now even when not due (owner Shell)")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(admin_view(), indent=1, default=float))
        return 0
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    res = run(force=a.force)
    print(json.dumps(res, indent=1, default=float), flush=True)
    return 1 if res.get("error") or res.get("publish_error") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
