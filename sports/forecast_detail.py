"""What a displayed forecast is, read from the forecast pipeline's own saved records (worker only; sports/snapshots.py).

For every displayed forecast id this adds, from <SPORTS_DATA_DIR>/model/forecasts.db (table `inputs`, written by
sports/model/forward.py at forecast time - the replay record):
  kind        "scout" (sm-1: player and game data), "informed" (mi-1: market-informed), "market" (mc-1 / mkt-* /
              sharp-*: read from prices)
  median, interval   the median and the central 80% range (10th-90th percentile) of the forecast's own saved PMF
  validation  the grade policy's validation_state + label saved with the forecast (grades / stakes stay withheld)
  factors     the values of the inputs the model ACTUALLY used for that row: the sm-1 base inputs present in the saved
              row (expected minutes, earlier games, teammates out ...) and the artifact's registered features for that
              market (sports/features.py compute() on the saved row) - values only, never weights or contributions
  pmf         [lo, [p...]] for re-reading the SAME distribution at another line (line_probs) - a line change needs
              no new projection unless the inputs changed
Nothing is estimated here; a forecast without a saved input row gets only its kind.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import config

MARKET_MODELS = ("mc-1", "sharp-1", "sharp-2")
BASE_FIELDS = {
    "nba": [("ew_min", "Expected minutes (recent weighted)", 1), ("prior_n", "Earlier games in sample", 0),
            ("absent_share", "Teammates out (share of team minutes)", 2), ("starter_lag", "Started previous game", 0),
            ("rest_days", "Rest days", 1)],
    "nfl": [("prior_n", "Earlier games in sample", 0), ("ew_offense_pct", "Snap share (recent weighted)", 2),
            ("ew_target_share", "Target share (recent weighted)", 2), ("ew_targets", "Targets per game (weighted)", 1),
            ("ew_carries", "Carries per game (weighted)", 1), ("rest_days", "Rest days", 1)],
}
STAT_MARKET = {"nba": {"points": "points", "rebounds": "rebounds", "assists": "assists", "threes": "threes"}}


def kind(model_version):
    if not model_version:
        return None
    if model_version in MARKET_MODELS or model_version.startswith("mkt-"):
        return "market"
    if model_version == "mi-1":
        return "informed"
    return "scout"


def _db():
    p = Path(config.DATA_DIR) / "model" / "forecasts.db"
    if not p.exists():
        return None
    try:
        return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return None


def quantiles(lo, pmf, qs=(0.1, 0.5, 0.9)):
    out, c, i = [], 0.0, 0
    for q in qs:
        while i < len(pmf) and c + pmf[i] < q - 1e-12:
            c += pmf[i]
            i += 1
        out.append(lo + min(i, len(pmf) - 1))
    return out


def line_probs(lo, pmf, line):
    """Unconditional (over, under, push) at `line` from a PMF on lo, lo+1, ... (same rule as sports/model/dists)."""
    o = u = p = 0.0
    for k, x in enumerate(pmf):
        v = lo + k
        if v > line + 1e-9:
            o += x
        elif v < line - 1e-9:
            u += x
        else:
            p += x
    t = o + u + p
    return (o / t, u / t, p / t) if t > 0 else (None, None, None)


def _artifact_features(league):
    try:
        from .model.forward import load_artifact
        art = load_artifact(league)
        return {mk: list(m.get("features") or []) for mk, m in (art or {}).get("markets", {}).items()}
    except Exception:                                   # noqa: BLE001 - factors are optional detail
        return {}


def _feature_values(league, market, feat_ids, row):
    if not feat_ids:
        return []
    try:
        import numpy as np
        from . import features
        from .model.frame import Frame
        reg = {r["id"]: r for r in features.REGISTRY}
        one = Frame({k: np.array([np.nan if v is None else v], dtype=float) if isinstance(v, (int, float)) or v is None
                     else np.array([v], dtype=object) for k, v in row.items()})
        out = []
        for fid in feat_ids:
            r = reg.get(fid)
            if not r or not r.get("column"):
                continue
            try:
                v = float(features.compute(one, league, market, r["column"])[0])
            except Exception:                           # noqa: BLE001 - a column this row lacks = not shown
                continue
            out.append({"id": fid, "label": r["factor"], "value": None if v != v else round(v, 3),
                        "definition": r["definition"]})
        return out
    except Exception:                                   # noqa: BLE001
        return []


def enrich(forecasts, league, with_pmf_for=()):
    """forecasts: {fid: forecast_view dict} (mutated: adds 'kind' and, where saved inputs exist, 'detail')."""
    for f in forecasts.values():
        f["kind"] = kind(f.get("model_version"))
    con = _db()
    if con is None:
        return {"inputs_db": "absent"}
    feats = None
    want = [fid for fid, f in forecasts.items() if f["kind"] in ("scout", "informed")]
    n = 0
    try:
        for i in range(0, len(want), 500):
            chunk = want[i:i + 500]
            rows = con.execute("SELECT forecast_id, market, pmf_lo, pmf_json, row_json, policy_json FROM inputs WHERE "
                               f"forecast_id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
            for fid, market, lo, pmf_json, row_json, policy_json in rows:
                f = forecasts[fid]
                d = {}
                if pmf_json and lo is not None:
                    pmf = json.loads(pmf_json)
                    q10, q50, q90 = quantiles(int(lo), pmf)
                    d.update(median=q50, interval=[q10, q90])
                    if fid in with_pmf_for:
                        d["pmf"] = [int(lo), [round(x, 6) for x in pmf]]
                pol = json.loads(policy_json) if policy_json else {}
                d["validation"] = {"state": pol.get("validation_state"), "label": pol.get("label"),
                                   "stake": pol.get("stake_units"), "stake_reason": pol.get("stake_reason")}
                if with_pmf_for and fid in with_pmf_for and row_json:
                    row = json.loads(row_json)
                    base = [{"id": k, "label": lab, "value": round(float(row[k]), dg) if dg else int(row[k])}
                            for k, lab, dg in BASE_FIELDS.get(league, []) if isinstance(row.get(k), (int, float))]
                    if feats is None:
                        feats = _artifact_features(league)
                    d["factors"] = base + _feature_values(league, market, feats.get(market, []), row)
                f["detail"] = d
                n += 1
    finally:
        con.close()
    return {"inputs_db": "present", "enriched": n}
