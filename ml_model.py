#!/usr/bin/env python3
"""
ml_model.py - SHADOW moneyline (series-winner) model "ml-candidate-1".  NOT VALIDATED.

What it is
  The 2026-10-12 beat-the-market attempt (ML_EVAL.md, "Beat-the-market attempt") tested four model families on 1,790
  out-of-sample matches under a pre-registered rolling-origin protocol.  None passed.  This file freezes the best-scoring
  candidate so the FORWARD cohort (matches after the freeze) can test it honestly:

      p_model = sigmoid( w_L * logit(q) )        q = the frozen market baseline ml-baseline-1 for team A

  i.e. research config c01_slope (ml_models/calibration.py): the market's own log-odds stretched by one fitted slope
  (w_L > 1 = favourites a bit under-priced).  On the development blocks it scored +0.00064 log-loss vs the market,
  95% CI [-0.00010, +0.00147], 4 of 6 blocks positive - NOT significant, and picked as the best of 208 scored variants
  on those same blocks (a post-hoc choice).  Treat it as a hypothesis, not a result.

  The coefficient lives in ml_model_frozen.json with a sha256 of its payload; a file whose hash does not match is
  refused (the model then reports itself unavailable - it never falls back to anything else).

Member rules (PROMOTION_RULE in ml_eval.py, pre-registered 2026-10-12 and copied into the frozen file)
  - no stake, ever, until the forward cohort has >= 300 matches AND the log-loss gain vs ml-baseline-1 is > 0 with the
    match-grouped 95% CI excluding 0 AND the calibration slope is in [0.8, 1.2];
  - until then the probability may be shown only labelled "candidate - not validated".
  predict() therefore always returns stake = None and validated = False.

Shadow log
  moneyline.log_forecasts() writes one ml_forecasts row (model_version "ml-candidate-1") next to every ml-baseline-1
  row, with the SAME logged_ts, so the forecast scored for a match is the last one logged >= 30 min before its start
  (ml_eval.forward_rows picks it exactly like the baseline's).

HLTV context (optional, weight 0)
  context() returns the point-in-time HLTV features of ml_features.py for an upcoming match (live mode: rows dated
  before the cutoff's UTC date only).  No HLTV feature passed a walk-forward test, so they never move the probability -
  they are display context at weight 0.  The feature engine holds the whole HLTV history in memory (~180 MB, ~8 s to
  build, measured 2026-10-12), so it is OFF unless ML_HLTV_CONTEXT=1.  When on: one engine, rebuilt in the background at
  most every ENGINE_TTL from a pooled HLTV connection (hltv._connect), per-match answers cached CONTEXT_TTL (15 min),
  every good answer saved in hltv_store (an outage or a restart during one serves the last good copy), single flight,
  member calls never wait for a build and never raise.

Deployment: standard library only (no numpy / pandas).  The `freeze` CLI (research only) needs numpy + pandas and the
research dataset:  python ml_model.py freeze --dataset <dataset.pkl>
"""
import hashlib
import json
import math
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODEL_VERSION = "ml-candidate-1"
BASELINE_VERSION = "ml-baseline-1"
VALIDATED = False
LABEL = "candidate - not validated"
FROZEN_PATH = Path(__file__).resolve().with_name("ml_model_frozen.json")
CUTOFF_MIN = 30
P_MIN, P_MAX = 0.01, 0.99          # the research data never had a cutoff price outside this (resolved-looking) range
CONTEXT_TTL = 15 * 60              # per-match HLTV context cache
ENGINE_TTL = 2 * 3600              # the HLTV scraper writes every ~2 h
ENGINE_RETRY = 300                 # a failed engine build is retried after 5 min (the last good engine keeps serving)
CONTEXT_NOTE = ("HLTV context only - no HLTV feature passed the walk-forward tests, so these move nothing "
                "(weight 0)")
RULE_TEXT = ("ml-candidate-1 (NOT VALIDATED): p = sigmoid(w_L * logit(ml-baseline-1 probability of team A)), "
             "w_L frozen in ml_model_frozen.json; shadow only, never a stake")

_STATE = {"model": None, "error": None, "path": None, "mtime": None}
_STATE_LOCK = threading.Lock()


def _now():
    return time.time()


# ------------------------------------------------------------------------------------------------ frozen model
def canonical_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def payload_sha256(payload):
    return hashlib.sha256(canonical_json(payload).encode("ascii")).hexdigest()


def _read_frozen(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    pay, sha = doc.get("payload"), doc.get("sha256")
    if not isinstance(pay, dict) or not sha:
        raise ValueError("frozen file has no payload / sha256")
    got = payload_sha256(pay)
    if got != sha:
        raise ValueError(f"sha256 mismatch (file says {sha[:12]}..., payload is {got[:12]}...)")
    if pay.get("model_version") != MODEL_VERSION:
        raise ValueError(f"frozen model_version {pay.get('model_version')!r} is not {MODEL_VERSION!r}")
    w = pay.get("w")
    if pay.get("terms") != ["L"] or not isinstance(w, list) or len(w) != 1 or not math.isfinite(float(w[0])):
        raise ValueError("frozen model is not the one-term slope model this code evaluates")
    if not 0.5 <= float(w[0]) <= 2.0:
        raise ValueError(f"frozen slope {w[0]} outside the sane range [0.5, 2]")
    return {"payload": pay, "sha256": sha, "w_L": float(w[0])}


def load_frozen(path=None):
    """The verified frozen model {"payload", "sha256", "w_L"} or None (the reason is in status()). Re-read when the file
    changes. Never raises."""
    p = Path(path) if path else FROZEN_PATH
    try:
        mt = p.stat().st_mtime
    except OSError:
        mt = None
    with _STATE_LOCK:
        if _STATE["path"] == str(p) and _STATE["mtime"] == mt and (_STATE["model"] or _STATE["error"]):
            return _STATE["model"]
        try:
            if mt is None:
                raise FileNotFoundError(f"{p.name} not found")
            m, err = _read_frozen(p), None
        except Exception as e:                                   # noqa: BLE001 - a bad file disables the model
            m, err = None, f"{type(e).__name__}: {e}"
        _STATE.update(model=m, error=err, path=str(p), mtime=mt)
        return m


def status():
    m = load_frozen()
    return {"model_version": MODEL_VERSION, "validated": VALIDATED, "label": LABEL, "loaded": m is not None,
            "error": _STATE["error"], "sha256": m["sha256"] if m else None, "w_L": m["w_L"] if m else None,
            "frozen_at": (m["payload"].get("frozen_at") if m else None),
            "context": context_status()}


# ------------------------------------------------------------------------------------------------ prediction
def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)                              # the research code's clip
    return math.log(p / (1 - p))


def _sigmoid(z):
    z = min(max(z, -35.0), 35.0)
    return 1.0 / (1.0 + math.exp(-z))


def _market_prob(mf):
    for k in ("market_prob_a", "baseline_prob_a", "mkt_p", "prob_a"):
        if isinstance(mf, dict) and mf.get(k) is not None:
            return mf.get(k)
    return None


def _out(mf, prob=None, q=None, reason="", model=None):
    return {"model_version": MODEL_VERSION, "validated": VALIDATED, "label": LABEL,
            "prob_a": prob, "available": prob is not None, "market_prob_a": q, "reason": reason,
            "stake": None, "sha256": model["sha256"] if model else None, "w_L": model["w_L"] if model else None,
            "match_id": mf.get("match_id") if isinstance(mf, dict) else None}


def predict(match_features, with_context=False):
    """Probability that team A wins for one upcoming match. match_features: {"market_prob_a": q (the ml-baseline-1
    probability of team A, 0.01 < q < 0.99), optional match_id, team_a_key / team_b_key ("team:<HLTV id>"), start (ISO),
    bo, lan, event}. Returns {"prob_a", "available", "market_prob_a", "reason", "stake": None, "validated": False,
    "label": "candidate - not validated", ...}; with_context=True adds "context" (HLTV features, weight 0, never
    blocking). Never raises, never returns a 50% default: no usable market price -> prob_a None."""
    try:
        mf = match_features if isinstance(match_features, dict) else {}
        model = load_frozen()
        if model is None:
            out = _out(mf, reason=f"model unavailable ({_STATE['error']})")
        else:
            q = _market_prob(mf)
            try:
                q = float(q)
            except (TypeError, ValueError):
                q = None
            if q is None or not math.isfinite(q):
                out = _out(mf, reason="no market baseline - the candidate is a correction of the market price", model=model)
            elif not P_MIN < q < P_MAX:
                out = _out(mf, q=q, reason=f"market price {q:.3f} outside {P_MIN:.2f}-{P_MAX:.2f}: model not defined",
                           model=model)
            else:
                p = _sigmoid(model["w_L"] * _logit(q))
                out = _out(mf, prob=p, q=q, reason="market log-odds x frozen slope", model=model)
        if with_context:
            out["context"] = context(mf.get("team_a_key"), mf.get("team_b_key"), mf.get("start"), bo=mf.get("bo"),
                                     lan=mf.get("lan"), event=mf.get("event"))
        return out
    except Exception as e:                                       # noqa: BLE001 - never raise into a caller
        return _out({}, reason=f"model error ({type(e).__name__})")


def forecast_row(m, logged_iso):
    """The ml_forecasts row (moneyline._SCHEMA column order) of ml-candidate-1 for one board match whose baseline row is
    being logged at logged_iso, or None. Never raises."""
    try:
        b = m.get("baseline") or {}
        r = predict({"market_prob_a": b.get("prob_a"), "match_id": m.get("match_id")})
        why = {"market_prob_a": r["market_prob_a"], "baseline_platform": b.get("platform"), "w_L": r["w_L"],
               "sha256": (r["sha256"] or "")[:16] or None, "label": LABEL, "note": r["reason"]}
        return (m.get("match_id"), MODEL_VERSION, logged_iso, b.get("platform"), r["prob_a"],
                (m.get("teams") or {}).get("a", {}).get("name"), (m.get("teams") or {}).get("b", {}).get("name"),
                m.get("start"), int(r["prob_a"] is not None), int(bool(m.get("disagreement"))),
                json.dumps(why, sort_keys=True), RULE_TEXT)
    except Exception:                                            # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------------------ HLTV context
_CTX = {"engine": None, "built_at": 0.0, "source": None, "building": False, "last_try": 0.0, "error": None,
        "builds": 0}
_CTX_LOCK = threading.Lock()          # guards _CTX and _CACHE
_FEAT_LOCK = threading.Lock()         # one feature computation at a time (the engine's caches are plain dicts)
_CACHE = {}                           # key -> (computed_at, answer)
SNAPSHOT_KEY = ("ml_ctx_snapshot", 1)


def context_enabled():
    return os.environ.get("ML_HLTV_CONTEXT", "0") == "1"


def context_status():
    with _CTX_LOCK:
        return {"enabled": context_enabled(), "engine": _CTX["engine"] is not None, "source": _CTX["source"],
                "built_at": _CTX["built_at"] or None, "building": _CTX["building"], "error": _CTX["error"],
                "builds": _CTX["builds"], "cached": len(_CACHE)}


def _team_id(key):
    if key is None:
        return None
    s = str(key)
    if s.startswith("team:"):
        s = s[5:]
    return int(s) if s.isdigit() else None


def _parse_ts(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _load_snapshot():
    """Snapshot of the HLTV database through the server's connection pool (raises on an outage)."""
    import hltv
    import ml_features
    if not hltv.dsn():
        raise RuntimeError("HLTV database not configured")
    con = hltv._connect()
    try:
        return ml_features.Snapshot.from_db(con)
    finally:
        con.close()


def _build_engine():
    """Background: a fresh engine from the database; on failure the last good one stays, and with none in memory the
    snapshot saved in hltv_store is used. Never raises."""
    import hltv_store
    import ml_features
    eng, src, err = None, None, None
    try:
        snap = _load_snapshot()
        eng, src = ml_features.FeatureEngine(snap), "live"
        hltv_store.save(SNAPSHOT_KEY, snap)
    except Exception as e:                                       # noqa: BLE001
        err = f"{type(e).__name__}: HLTV database unreachable" if _transient(e) else f"{type(e).__name__}: build failed"
        with _CTX_LOCK:
            have = _CTX["engine"] is not None
        if not have:
            try:
                stored = hltv_store.load(SNAPSHOT_KEY)
                if stored is not None:
                    eng, src = ml_features.FeatureEngine(stored[1]), "stored"
            except Exception:                                    # noqa: BLE001
                pass
    with _CTX_LOCK:
        if eng is not None:
            _CTX.update(engine=eng, built_at=_now(), source=src, builds=_CTX["builds"] + 1)
            _CACHE.clear()
        _CTX.update(building=False, error=err)


def _transient(e):
    try:
        import hltv
        return hltv.is_transient(e)
    except Exception:                                            # noqa: BLE001
        return True


def _kick_build(now):
    """Start one background build when none runs and the engine is missing or older than ENGINE_TTL (single flight)."""
    with _CTX_LOCK:
        if _CTX["building"]:
            return
        fresh = _CTX["engine"] is not None and now - _CTX["built_at"] < ENGINE_TTL
        if fresh or now - _CTX["last_try"] < (ENGINE_RETRY if _CTX["error"] else 0):
            return
        _CTX.update(building=True, last_try=now)
    threading.Thread(target=_build_engine, daemon=True, name="ml-model-context").start()


def _ctx_answer(feats, source, day, computed_at):
    return {"available": True, "weight": 0, "note": CONTEXT_NOTE, "source": source, "feature_day": day.isoformat(),
            "computed_at": datetime.fromtimestamp(computed_at, timezone.utc).isoformat(timespec="seconds"),
            "features": feats}


def context(team_a_key, team_b_key, start, bo=None, lan=None, event=None, now=None):
    """HLTV point-in-time features for an upcoming match (weight 0). Uses rows dated before the UTC date of
    min(start - 30 min, now) only. Never blocks on a database build, never raises."""
    try:
        if not context_enabled():
            return {"available": False, "reason": "HLTV context is off (ML_HLTV_CONTEXT=1 turns it on)"}
        ta, tb = _team_id(team_a_key), _team_id(team_b_key)
        st = _parse_ts(start)
        if ta is None or tb is None or ta == tb:
            return {"available": False, "reason": "a team is not an HLTV team"}
        if st is None:
            return {"available": False, "reason": "no start time"}
        now = _now() if now is None else now
        import ml_features
        cutoff = min(st - CUTOFF_MIN * 60, now)
        day = ml_features.feature_day(cutoff)
        key = (ta, tb, day.toordinal(), bo, None if lan is None else bool(lan), event)
        with _CTX_LOCK:
            hit = _CACHE.get(key)
            eng = _CTX["engine"]
            src = _CTX["source"]
        if hit and now - hit[0] < CONTEXT_TTL:
            return hit[1]
        _kick_build(now)
        import hltv_store
        skey = ("ml_ctx", ta, tb, day.toordinal(), bo, None if lan is None else bool(lan), str(event or ""))
        if eng is not None:
            with _FEAT_LOCK:                                     # single flight: a waiting caller re-checks the cache
                with _CTX_LOCK:
                    hit = _CACHE.get(key)
                if hit and now - hit[0] < CONTEXT_TTL:
                    return hit[1]
                f = ml_features.live_features(eng, ta, tb, st, bo=bo, lan=lan, event=event, now_ts=now)
                ans = _ctx_answer(f, src, day, now)
                with _CTX_LOCK:
                    if len(_CACHE) > 2000:
                        _CACHE.clear()
                    _CACHE[key] = (now, ans)
            if src == "live":
                hltv_store.save(skey, ans)
            return ans
        stored = hltv_store.load(skey)                           # exact day only (ordinal keys are never 'loosened')
        if stored is not None and stored[1].get("feature_day") == day.isoformat():
            return dict(stored[1], source="stored")
        return {"available": False, "reason": "HLTV database unreachable and no stored copy" if _CTX["error"] else
                ("HLTV context is being prepared" if _CTX["building"] else "HLTV context not ready")}
    except Exception as e:                                       # noqa: BLE001
        return {"available": False, "reason": f"HLTV context error ({type(e).__name__})"}


# ------------------------------------------------------------------------------------------------ freeze (research CLI)
def _file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def freeze(dataset, out=None, fixture=None, results=None):
    """Fit c01_slope on EVERY primary-view row of the research dataset (all data up to the freeze) with the research
    code itself (lambda chosen by its inner CV exactly as in each evaluation window), write ml_model_frozen.json with a
    sha256, and the 20-row reproduction fixture. Research only (numpy + pandas)."""
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ml_models import calibration as C
    import ml_eval
    df, used = C.load_dataset(dataset)
    prim = C.primary_rows(df).sort_values(["cutoff_ts", "match_id"]).reset_index(drop=True)
    assert prim.match_id.is_unique and prim.y.notna().all()
    raw = C.raw_from_rows(prim)
    y = prim.y.to_numpy().astype(float)
    cut = prim.cutoff_ts.to_numpy().astype(float)
    vi, zs = C.inner_cv(raw, y, cut, ["c01_slope"])
    yv = y[vi]
    inner = {lam: float(C.logloss(C.sigmoid(zs[("c01_slope", lam, None)]), yv).mean()) for lam in C.LAMS}
    lam = min(C.LAMS, key=lambda l: inner[l])                 # same rule as the scored single-config model
    m = C.fit(raw, y, "c01_slope", lam)
    ll_mkt_in = float(C.logloss(C.sigmoid(zs[C.MARKET]), yv).mean())
    dev = {}
    if results and os.path.exists(results):
        r = json.load(open(results, encoding="utf-8"))
        for run in ("primary", "polymarket", "kalshi_pooled_train"):
            mm = (((r.get("runs") or {}).get(run) or {}).get("models") or {}).get("cfg_c01_slope")
            if mm:
                dev[run] = {k: mm.get(k) for k in ("n", "ll_market", "ll_model", "gain", "gain_ci", "blocks_positive",
                                                   "blocks_total", "brier_gain", "brier_gain_ci")}
                dev[run]["calibration_slope"] = (mm.get("calibration") or {}).get("slope")
                dev[run]["calibration_slope_ci"] = (mm.get("calibration") or {}).get("slope_ci")
    ts = lambda s: datetime.fromtimestamp(float(s), timezone.utc).isoformat(timespec="seconds")   # noqa: E731
    payload = {
        "model_version": MODEL_VERSION, "validated": False, "status": LABEL,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "family": "market calibration, research config c01_slope (ml_models/calibration.py)",
        "formula": "p = sigmoid(w_L * logit(clip(q, 1e-6, 1 - 1e-6))); q = ml-baseline-1 probability of team A, "
                   "defined for 0.01 < q < 0.99",
        "terms": ["L"], "w": [float(m["w"][0])], "lam": float(lam), "lam_grid": list(C.LAMS),
        "prior": "L2 penalty lam * (w_L - 1)^2 / 2 toward the market (w_L = 1)",
        "lambda_selection": "4-chunk expanding-window inner CV over the last 50% of the training rows by cutoff "
                            "(each inner fit only on rows with cutoff < chunk start - 1 day); lowest inner log-loss",
        "inner_cv": {"n_rows": int(len(vi)), "ll_market": round(ll_mkt_in, 6), "ll_model": round(inner[lam], 6),
                     "ll_by_lambda": {str(k): round(v, 6) for k, v in inner.items()}},
        "training": {"rows": int(len(prim)), "matches": int(prim.match_id.nunique()),
                     "platforms": {k: int(v) for k, v in prim.platform.value_counts().items()},
                     "view": "primary: one row per match, the platform with the smaller spread at the cutoff "
                             "(Polymarket spread not recorded -> one tick, so Polymarket when listed, else Kalshi)",
                     "cutoff_first": ts(cut.min()), "cutoff_last": ts(cut.max()),
                     "dataset": os.path.basename(str(used).split(" ")[0]), "dataset_sha256": _file_sha(str(used).split(" ")[0])},
        "development_evidence": dict(dev, note="rolling-origin out-of-sample results of the same config on the "
                                     "development blocks 2026-06..2026-10; best of 208 variants scored on those blocks, "
                                     "so selected post hoc: NOT a validation"),
        "research_code_sha256": _file_sha(str(Path(__file__).resolve().parent / "ml_models" / "calibration.py")),
        "promotion_rule": ml_eval.PROMOTION_RULE,
    }
    doc = {"payload": payload, "sha256": payload_sha256(payload)}
    out = Path(out) if out else FROZEN_PATH
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    # 20 stored rows spread over time and both platforms: research-code predictions with the frozen weight
    pick = sorted(set(np.linspace(0, len(prim) - 1, 17).astype(int).tolist()
                      + prim.index[prim.platform == "kalshi"].tolist()[:3]))
    pick = (pick + [i for i in range(len(prim)) if i not in pick])[:20]
    sub = prim.iloc[sorted(pick)]
    p_research = C.predict_rows({k: m[k] for k in ("config", "terms", "w", "stats", "lam")}, sub)
    rows = [{"match_id": str(r.match_id), "platform": r.platform, "cutoff_ts": int(r.cutoff_ts), "mkt_p": float(r.mkt_p),
             "p_research": float(p)} for r, p in zip(sub.itertuples(), p_research)]
    fx = Path(fixture) if fixture else Path(__file__).resolve().parent / "tests" / "fixtures" / "ml_model_rows.json"
    with open(fx, "w", encoding="utf-8") as fh:
        json.dump({"model_sha256": doc["sha256"], "source": "ml_models/calibration.py predict_rows with the frozen fit",
                   "rows": rows}, fh, indent=1)
        fh.write("\n")
    return doc


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="ml-candidate-1 (shadow, not validated)")
    sub = ap.add_subparsers(dest="cmd")
    f = sub.add_parser("freeze", help="research only: refit on the dataset and write ml_model_frozen.json")
    f.add_argument("--dataset", required=True)
    f.add_argument("--results", default=None, help="results_calibration.json (development evidence, optional)")
    f.add_argument("--out", default=None)
    f.add_argument("--fixture", default=None)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "freeze":
        doc = freeze(a.dataset, a.out, a.fixture, a.results)
        print(json.dumps({"sha256": doc["sha256"], "w": doc["payload"]["w"], "lam": doc["payload"]["lam"],
                          "training": doc["payload"]["training"]}, indent=1))
    else:
        print(json.dumps(status(), indent=1, default=str))


if __name__ == "__main__":
    main()
