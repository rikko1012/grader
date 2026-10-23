"""Dispatcher for `python -m sports.worker train <job>` model jobs (study / fit / forecast / learn / snapshot / books)
and the read-only API snapshot writer. Worker process only."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .. import config, flags, store
from ..contracts import now_utc

API_DIR_NAME = "api"
PMF_TRIM = 1e-9


def api_dir():
    p = config.DATA_DIR / "model" / API_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _atomic_write(path, obj):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"), default=str)
    os.replace(tmp, path)


def run(a):
    from . import datasets, forward, learning, study
    if a.job == "study":
        hcon = datasets.connect(read_only=True)
        out = {}
        for sport in ([a.sport] if a.sport else ["nba", "nfl"]):
            phases = ["tune", "test"] if a.phase == "all" else [a.phase]
            for ph in phases:
                res = study.run(sport, hcon, ph)
                out[f"{sport}:{ph}"] = {m: r.get("gate_H", r.get("family")) for m, r in res["markets"].items()}
        return out
    if a.job == "fit":
        hcon = datasets.connect(read_only=True)
        return {s: forward.fit(s, hcon)["sha"] for s in ("nba", "nfl")}
    if a.job == "forecast":
        flags.require("SPORTS_INGEST")                    # writes the canonical store
        hcon = datasets.connect(read_only=True)
        with store.opened() as scon:
            out = {lg: forward.forecast_league(scon, hcon, lg) for lg in (a.leagues or ["nba", "nfl"])}
        out["snapshot"] = write_snapshot()
        return out
    if a.job == "learn":
        with store.opened() as scon:
            out = learning.on_settled(scon)
        hcon = datasets.connect(read_only=True)
        for sport, r in out["retrain"].items():
            if r.get("due"):
                out.setdefault("challengers", {})[sport] = learning.retrain(sport, hcon)
        out["snapshot"] = write_snapshot()
        return out
    if a.job == "snapshot":
        return write_snapshot()
    if a.job == "books":
        flags.require("SPORTS_INGEST")
        from ..books import sources
        with store.opened() as scon:
            return {lg: sources.ingest(scon, lg) for lg in (a.leagues or ["nba", "nfl"])}
    raise SystemExit(f"unknown model job {a.job}")


def write_snapshot(now=None):
    """Precomputed answers for sports/api_model.py: forecasts of not-started events (latest per offer x version) with
    their grade-policy output, PMFs of sm-1 / mi-1 (trimmed tails < 1e-9), registry, study summaries, scorecard."""
    from .. import features
    from . import forward, learning
    now = now or now_utc()
    out = {"written_at": now, "leagues": {}}
    icon = forward.inputs_db()
    pol = {r[0]: (json.loads(r[1]) if r[1] else None, r[2], json.loads(r[3]) if r[3] else None)
           for r in icon.execute("SELECT forecast_id, policy_json, pmf_lo, pmf_json FROM inputs")}
    icon.close()
    try:
        scon = store.connect(read_only=True)
    except FileNotFoundError:
        scon = None
    for lg in ("nba", "nfl"):
        views, pmfs = {}, {}
        if scon is not None:
            events = {e.event_id: e for _, e in store.events(scon, lg)}
            for rid, fc in store.forecasts(scon, league=lg, limit=200000):
                ev = events.get(fc.event_id)
                if ev is None or not ev.start_utc or ev.start_utc <= now:
                    continue
                key = (fc.offer_snapshot_ids[0] if fc.offer_snapshot_ids else rid, fc.model_version)
                if key in views and views[key]["forecast_at"] >= fc.forecast_at:
                    continue
                p = pol.get(fc.forecast_id)
                views[key] = view(fc, p[0] if p else None, ev)
                if p and p[2]:
                    lo, arr = p[1], p[2]
                    a0 = next((i for i, x in enumerate(arr) if x > PMF_TRIM), 0)
                    a1 = len(arr) - next((i for i, x in enumerate(reversed(arr)) if x > PMF_TRIM), 0)
                    pmfs[fc.forecast_id] = {"lo": lo + a0, "p": arr[a0:a1]}
        out["leagues"][lg] = {"forecasts": len(views)}
        _atomic_write(api_dir() / f"forecasts_{lg}.json", {"written_at": now, "league": lg,
                                                           "forecasts": list(views.values())})
        _atomic_write(api_dir() / f"pmf_{lg}.json", {"written_at": now, "pmf": pmfs})
    if scon is not None:
        scon.close()
    res_dir = Path(__file__).resolve().parents[1] / "research" / "results"
    studies = {}
    for p in sorted(res_dir.glob("model_study_*.json")):
        try:
            studies[p.stem] = summarize_study(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    try:
        sc, gates, diag = learning.scorecard(), learning.forward_gate(), learning.diagnostics(200)
    except Exception as e:                                           # noqa: BLE001 - snapshot stays writable
        sc, gates, diag = [], [], [{"error": str(e)[:200]}]
    _atomic_write(api_dir() / "model_meta.json", {
        "written_at": now, "registry": forward.registry(), "artifacts": {s: _art_meta(forward.load_artifact(s))
                                                                         for s in ("nba", "nfl")},
        "features": features.with_effects(), "studies": studies})
    _atomic_write(api_dir() / "admin_learning.json", {"written_at": now, "scorecard": sc, "forward_gate": gates,
                                                      "diagnostics": diag})
    return out


def _art_meta(a):
    if not a:
        return None
    return {"sha": a["sha"], "fitted_at": a["fitted_at"], "data_as_of": a["data_as_of"],
            "markets": {m: {"family": v["family"], "features": v["features"], "half_lives": v["half_lives"],
                            "cal": v["cal"], "fit_rows": v.get("fit_rows"), "fit_games": v.get("fit_games")}
                        for m, v in a["markets"].items()}}


def view(fc, policy, ev):
    return {"forecast_id": fc.forecast_id, "model_version": fc.model_version, "forecast_at": fc.forecast_at,
            "league": fc.league, "event_id": fc.event_id, "event_start_utc": ev.start_utc if ev else None,
            "player_id": fc.player_id, "player_key": fc.player_key, "stat": fc.stat, "scope": fc.scope,
            "line": fc.line, "offer_record_id": fc.offer_snapshot_ids[0] if fc.offer_snapshot_ids else None,
            "input_record_ids": list(fc.input_snapshot_ids), "artifact_version": fc.artifact_version,
            "data_version": fc.data_version, "calibration_version": fc.calibration_version,
            "projection_mean": fc.projection_mean, "projection_median": fc.projection_median,
            "p_over": fc.p_over, "p_under": fc.p_under, "p_push": fc.p_push,
            "p_over_given_no_push": fc.p_over_given_no_push, "p_under_given_no_push": fc.p_under_given_no_push,
            "sides_available": list(fc.sides_available), "chosen_side": fc.chosen_side, "grade": fc.grade,
            "stake_units": fc.stake_units, "reason_codes": list(fc.reason_codes), "research_only": fc.research_only,
            "policy": policy}


def summarize_study(res):
    out = {"sport": res.get("sport"), "phase": res.get("phase"), "generated_at": res.get("generated_at"),
           "markets": {}}
    for mk, r in (res.get("markets") or {}).items():
        if res.get("phase") == "test":
            t = r["test"]
            out["markets"][mk] = {
                "rows": t["rows"], "games": t["games"], "gate_H": r["gate_H"],
                "crps": {k: v["crps"]["mean"] for k, v in t["candidates"].items()},
                "crps_diff_vs_b1": t["paired_vs_b1"]["crps"], "crps_diff_vs_b0": t["paired_vs_b0"]["crps"],
                "mae": {k: v["ae"]["mean"] for k, v in t["candidates"].items()},
                "cov80": t["candidates"]["sm1"]["cov80"]["mean"],
                "calibration": {k: t["candidates"]["sm1"]["calibration"][k] for k in ("ece", "in_the_large")}}
        else:
            out["markets"][mk] = {"family": r.get("family"), "half_lives": r.get("half_lives"), "cal": r.get("cal"),
                                  "kept": (r.get("joint") or {}).get("features") if (r.get("joint") or {}).get("used")
                                  else [], "rows": r.get("rows")}
    return out
