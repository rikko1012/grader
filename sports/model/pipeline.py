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
    if a.job == "fit":                                     # a CHALLENGER refit; the committed champion is untouched
        hcon = datasets.connect(read_only=True)
        return {s: learning.retrain(s, hcon) for s in ("nba", "nfl")}
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
    """Precomputed answers for sports/api_model.py: forecasts of not-started events (latest per CURRENT offer revision x
    version) with their grade-policy output, PMFs of sm-1 / mi-1 (trimmed tails < 1e-9), registry, study summaries,
    scorecard.

    Bounded (Starter, 2026-10-24): reads only the forecasts of not-started events (records_event index), keeps those
    made for the CURRENT offer revision (the one board / card / slip read - store.CURRENT_SQL), and reads saved
    inputs only for the forecasts it keeps. Memory follows the current board, never the stored-forecast history."""
    from .. import features
    from . import forward, learning
    now = now or now_utc()
    out = {"written_at": now, "leagues": {}}
    try:
        scon = store.connect(read_only=True)
    except FileNotFoundError:
        scon = None
    icon = forward.inputs_db()
    for lg in ("nba", "nfl"):
        views, pmfs, pol = {}, {}, {}
        if scon is not None:
            events = {e.event_id: e for _, e in store.events(scon, lg)}
            up = sorted(eid for eid, ev in events.items() if ev.start_utc and ev.start_utc > now)
            current = current_offer_ids(scon, lg, up)
            for part in _chunks(up, 400):
                for r in scon.execute(f"SELECT record_id, payload FROM records WHERE kind = 'forecast' AND event_id "
                                      f"IN ({','.join('?' * len(part))})", part):
                    fc = store._rec(r)
                    if fc.league != lg or not fc.offer_snapshot_ids or fc.offer_snapshot_ids[0] not in current:
                        continue
                    key = (fc.offer_snapshot_ids[0], fc.model_version)
                    if key in views and views[key][0].forecast_at >= fc.forecast_at:
                        continue
                    views[key] = (fc, events.get(fc.event_id))
            ids = [fc.forecast_id for fc, _ in views.values()]
            for part in _chunks(ids, 400):
                for r in icon.execute(f"SELECT forecast_id, policy_json, pmf_lo, pmf_json FROM inputs WHERE "
                                      f"forecast_id IN ({','.join('?' * len(part))})", part):
                    pol[r[0]] = (json.loads(r[1]) if r[1] else None, r[2], json.loads(r[3]) if r[3] else None)
            for fc, _ev in views.values():
                p = pol.get(fc.forecast_id)
                if p and p[2]:
                    lo, arr = p[1], p[2]
                    a0 = next((i for i, x in enumerate(arr) if x > PMF_TRIM), 0)
                    a1 = len(arr) - next((i for i, x in enumerate(reversed(arr)) if x > PMF_TRIM), 0)
                    pmfs[fc.forecast_id] = {"lo": lo + a0, "p": arr[a0:a1]}
        rows = [view(fc, (pol.get(fc.forecast_id) or (None,))[0], ev) for fc, ev in views.values()]
        out["leagues"][lg] = {"forecasts": len(rows)}
        _atomic_write(api_dir() / f"forecasts_{lg}.json", {"written_at": now, "league": lg, "forecasts": rows})
        _atomic_write(api_dir() / f"pmf_{lg}.json", {"written_at": now, "pmf": pmfs})
        del views, pmfs, pol, rows
    icon.close()
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
    try:
        bands = learning.grade_band_report()
    except Exception as e:                                           # noqa: BLE001
        bands = [{"error": str(e)[:200]}]
    try:
        from . import shadow
        shadow_state = shadow.status()
    except Exception as e:                                           # noqa: BLE001
        shadow_state = {"error": str(e)[:200]}
    try:
        from . import nflv2
        shadow_state = dict(shadow_state, nflv2=nflv2.status())     # sm-nfl-2 (REGISTER A6), admin only
    except Exception as e:                                           # noqa: BLE001
        shadow_state = dict(shadow_state, nflv2={"error": str(e)[:200]})
    _atomic_write(api_dir() / "model_meta.json", {
        "written_at": now, "registry": forward.registry(), "challengers": _runtime_challengers(), "artifacts": {s: _art_meta(forward.load_artifact(s))
                                                                         for s in ("nba", "nfl")},
        "features": features.with_effects(), "studies": studies})
    try:                                                             # daily grade check + containment (A9a)
        from . import daily
        grades = daily.admin_view()
    except Exception as e:                                           # noqa: BLE001
        grades = {"error": str(e)[:200]}
    _atomic_write(api_dir() / "admin_learning.json", {"written_at": now, "scorecard": sc, "forward_gate": gates,
                                                      "grade_bands": bands, "shadow": shadow_state,
                                                      "diagnostics": diag, "forecast_run": _last_forecast_run(),
                                                      "grades": grades})
    return out


def _chunks(xs, n):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def current_offer_ids(scon, league, event_ids):
    """Record ids of the CURRENT revision (store.CURRENT_SQL) of every offer of these events - the same revision
    store.offers gives the board, the player card, the slip pool and new forecasts. Ids only (no payloads)."""
    out = set()
    for part in _chunks(event_ids, 400):
        q = (f"SELECT r.record_id FROM records r JOIN (SELECT source, source_key, MAX(revision) rv FROM records WHERE "
             f"kind = 'offer' AND {store.CURRENT_SQL} AND league = ? AND event_id IN ({','.join('?' * len(part))}) "
             f"GROUP BY source, source_key) z ON r.source = z.source AND r.source_key = z.source_key AND "
             f"r.revision = z.rv WHERE r.kind = 'offer'")
        out.update(x[0] for x in scon.execute(q, [league] + list(part)))
    return out


def _last_forecast_run():
    p = api_dir() / "forecast_run.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def publish_run(summary):
    """Put the latest forward run record into admin_learning.json (GET /api/sports/admin/model/learning)."""
    p = api_dir() / "admin_learning.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {"written_at": now_utc(), "scorecard": [], "forward_gate": [], "diagnostics": []}
    d["forecast_run"] = summary
    _atomic_write(p, d)


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


def _runtime_challengers():
    p = config.DATA_DIR / "model" / "challengers" / "registry_runtime.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("challengers", [])
    except (OSError, ValueError):
        return []
