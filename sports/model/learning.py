"""Continuous-learning plumbing (worker only; REGISTER.md 7 + 9): settlement -> scorecard + diagnostic queue,
registered retrain trigger, champion / challenger registry. Nothing here promotes anything automatically.

on_settled(scon)       called by the worker after every settle cycle: every settled forecast of a model version gets a
                       scorecard row (once per forecast x settlement revision; a correction makes a new row and marks
                       the old one superseded) and a diagnostic-queue entry when it is a large miss (|PIT - 0.5| >=
                       0.45), a void, or a corrected settlement
scorecard(...)         per version x market x sport: n, distinct games, chosen-side hit rate vs mean probability,
                       log-loss / Brier at the offered line, pushes, voids - with game-clustered intervals
retrain_due(...)       cadence (7 days) or sample trigger (>= 2,000 NBA / >= 600 NFL new settled player-games since the
                       last fit) -> a CHALLENGER artifact (same frozen config, new data) in the registry
forward_gate(...)      registered gate F per sport x market (>= 300 settled props, >= 100 games, chosen-side calibration,
                       log-loss vs sharp-2 / B1) - evaluated only at the 300 / 600 / 1,000 windows; reports, never promotes
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import timedelta

import numpy as np

from .. import config, store
from ..contracts import now_utc, parse_utc
from . import evaluate, forward

REVIEW_WINDOWS = (300, 600, 1000)
RETRAIN_DAYS = 7
SAMPLE_TRIGGER = {"nba": 2000, "nfl": 600}


def db():
    p = config.DATA_DIR / "model" / "learning.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS scorecard (forecast_id TEXT, settlement_id TEXT, model_version TEXT,
        league TEXT, stat TEXT, event_id TEXT, platform TEXT, line REAL, side TEXT, p_side REAL, p_over REAL,
        p_under REAL, p_push REAL, status TEXT, outcome TEXT, value REAL, pit REAL, forecast_at TEXT, settled_at TEXT,
        superseded INTEGER DEFAULT 0, PRIMARY KEY (forecast_id, settlement_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS diagnostics (forecast_id TEXT, settlement_id TEXT, kind TEXT, detail TEXT,
        created_at TEXT, reviewed INTEGER DEFAULT 0, PRIMARY KEY (forecast_id, settlement_id, kind))""")
    con.execute("""CREATE TABLE IF NOT EXISTS fits (sport TEXT, artifact_sha TEXT, fitted_at TEXT, data_as_of TEXT,
        settled_rows INTEGER, role TEXT, PRIMARY KEY (sport, artifact_sha))""")
    return con


def _pit(fc, value, pmf_row=None, lo=None):
    if pmf_row is None or value is None:
        return None
    p = np.asarray(pmf_row, float)
    ks = np.arange(lo, lo + len(p))
    return float(p[ks < value].sum() + 0.5 * p[ks == value].sum())


def on_settled(scon, log=None, now=None, days=30):
    """Scorecard + diagnostic queue for every forecast (of the last `days`) whose offer has a (new) settlement.
    Idempotent: one row per forecast x current settlement; a corrected settlement supersedes the earlier row."""
    lcon = db()
    icon = forward.inputs_db()
    rows = scon.execute("SELECT record_id FROM records WHERE kind='forecast' AND known_at >= ?",
                        ((parse_utc(now or now_utc()) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),)
                        ).fetchall()
    n_new = n_diag = 0
    for (fid,) in rows:
        fc = store.get(scon, fid)
        if fc is None or not fc.offer_snapshot_ids or fc.chosen_side is None:
            continue
        offer = store.get(scon, fc.offer_snapshot_ids[0])
        if offer is None:
            continue
        sts = store.settlements(scon, offer_id=offer.offer_id, platform=offer.platform)
        sts = [s for _, s in sts if s.side == fc.chosen_side]
        if not sts:
            continue
        cur = max(sts, key=lambda s: s.revision)
        if lcon.execute("SELECT 1 FROM scorecard WHERE forecast_id=? AND settlement_id=?",
                        (fid, cur.settlement_id)).fetchone():
            continue
        r = icon.execute("SELECT pmf_lo, pmf_json FROM inputs WHERE forecast_id=?", (fid,)).fetchone()
        pit = _pit(fc, cur.source_value, json.loads(r[1]) if r and r[1] else None, r[0] if r else None)
        p_side = fc.p_over if fc.chosen_side == "over" else fc.p_under
        lcon.execute("UPDATE scorecard SET superseded=1 WHERE forecast_id=?", (fid,))
        lcon.execute("INSERT OR IGNORE INTO scorecard VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                     (fid, cur.settlement_id, fc.model_version, fc.league, fc.stat, fc.event_id, offer.platform,
                      fc.line, fc.chosen_side, p_side, fc.p_over, fc.p_under, fc.p_push, cur.status, cur.outcome,
                      cur.source_value, pit, fc.forecast_at, cur.settled_at))
        n_new += 1
        kinds = []
        if pit is not None and abs(pit - 0.5) >= 0.45:
            kinds.append(("large_miss", f"PIT {pit:.3f}, value {cur.source_value}, line {fc.line}"))
        if cur.status == "void":
            kinds.append(("void", cur.reason or ""))
        if cur.revision > 1:
            kinds.append(("settlement_corrected", f"revision {cur.revision} supersedes {cur.supersedes}"))
        for k, d in kinds:
            lcon.execute("INSERT OR IGNORE INTO diagnostics VALUES (?,?,?,?,?,0)", (fid, cur.settlement_id, k, d[:300],
                                                                                    now_utc()))
            n_diag += 1
    lcon.commit()
    out = {"scorecard_rows_added": n_new, "diagnostics_added": n_diag, "retrain": {}}
    for sport in ("nba", "nfl"):
        out["retrain"][sport] = retrain_due(sport, lcon)
    lcon.close()
    icon.close()
    if log:
        log(out)
    return out


def scorecard(lcon=None, model_version=None):
    own = lcon is None
    lcon = lcon or db()
    q = "SELECT * FROM scorecard WHERE superseded = 0"
    a = []
    if model_version:
        q += " AND model_version = ?"
        a.append(model_version)
    cur = lcon.execute(q, a)
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    if own:
        lcon.close()
    groups = {}
    for r in rows:
        groups.setdefault((r["model_version"], r["league"], r["stat"]), []).append(r)
    out = []
    for (mv, lg, st), rs in sorted(groups.items()):
        graded = [r for r in rs if r["status"] in ("win", "loss")]
        p = np.array([r["p_side"] / max(1e-9, 1 - (r["p_push"] or 0)) for r in graded], float)
        hit = np.array([1.0 if r["status"] == "win" else 0.0 for r in graded])
        games = np.array([r["event_id"] for r in graded], dtype=object)
        row = {"model_version": mv, "league": lg, "stat": st, "settled": len(rs), "graded": len(graded),
               "games": len(set(games)), "pushes": sum(r["status"] == "push" for r in rs),
               "voids": sum(r["status"] == "void" for r in rs)}
        if len(graded):
            pc = np.clip(p, 1e-6, 1 - 1e-6)
            row["mean_p_chosen_given_no_push"] = float(p.mean())
            row["hit_rate"] = float(hit.mean())
            row["hit_minus_p"] = evaluate.boot_mean(hit - p, games)
            row["logloss"] = float(np.mean(-(hit * np.log(pc) + (1 - hit) * np.log(1 - pc))))
            row["brier"] = float(np.mean((p - hit) ** 2))
        row["review_window_reached"] = max([w for w in REVIEW_WINDOWS if len(graded) >= w], default=None)
        out.append(row)
    return out


def forward_gate(lcon=None):
    """Gate F as registered (REGISTER.md 7) - reported at review windows only."""
    sc = scorecard(lcon)
    by = {(r["model_version"], r["league"], r["stat"]): r for r in sc}
    out = []
    for (mv, lg, st), r in by.items():
        if mv not in ("sm-1", "mi-1", "mc-1"):
            continue
        res = {"model_version": mv, "league": lg, "stat": st, "graded": r["graded"], "games": r["games"]}
        if r["graded"] < 300 or r["games"] < 100:
            res["state"] = f"waiting ({r['graded']}/300 props, {r['games']}/100 games)"
        else:
            hm = r["hit_minus_p"]
            ref = by.get(("sharp-2", lg, st))
            res["calibration_ok"] = abs(hm[0]) <= 0.03 and hm[1] <= 0 <= hm[2]
            res["logloss_vs_sharp2"] = (r["logloss"] - ref["logloss"]) if ref and ref.get("logloss") else None
            res["state"] = "review window reached - decision by the registered review, never automatic"
        out.append(res)
    return out


def retrain_due(sport, lcon):
    last = lcon.execute("SELECT MAX(fitted_at), MAX(data_as_of) FROM fits WHERE sport=?", (sport,)).fetchone()
    art = forward.load_artifact(sport)
    fitted_at = last[0] or (art or {}).get("fitted_at")
    if not fitted_at:
        return {"due": True, "why": "no artifact yet"}
    days = (parse_utc(now_utc()) - parse_utc(fitted_at)).total_seconds() / 86400
    from . import datasets
    try:
        hcon = datasets.connect(read_only=True)
        tbl = "nba_pg" if sport == "nba" else "nfl_pg"
        new = hcon.execute(f"SELECT COUNT(*) FROM {tbl} p JOIN games g ON g.game_id = p.game_id AND g.league = ? "
                           "WHERE g.start_utc > ?", (sport, (art or {}).get("data_as_of") or fitted_at)).fetchone()[0]
        hcon.close()
    except sqlite3.Error:
        new = None
    due = days >= RETRAIN_DAYS or (new is not None and new >= SAMPLE_TRIGGER[sport])
    return {"due": bool(due), "days_since_fit": round(days, 2), "new_rows": new,
            "rule": f"{RETRAIN_DAYS} days or >= {SAMPLE_TRIGGER[sport]} new player-games"}


def retrain(sport, hcon):
    """Fit a CHALLENGER artifact (same frozen configuration, all data to now). The champion file is untouched."""
    path = config.DATA_DIR / "model" / "challengers" / f"sm1_{sport}_{now_utc().replace(':', '')}.json"
    art = forward.fit(sport, hcon, out_path=path)
    lcon = db()
    lcon.execute("INSERT OR REPLACE INTO fits VALUES (?,?,?,?,?,?)", (sport, art["sha"], art["fitted_at"],
                                                                       art["data_as_of"], None, "challenger"))
    lcon.commit()
    lcon.close()
    rp = config.DATA_DIR / "model" / "challengers" / "registry_runtime.json"
    reg = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {"challengers": []}
    reg["challengers"].append({"sport": sport, "sha": art["sha"], "path": str(path.name),
                               "fitted_at": art["fitted_at"], "promoted": False,
                               "note": "challenger only: the champion is the committed artifact; promotion needs the "
                                       "registered gate + the owner release process"})
    rp.write_text(json.dumps(reg, indent=1, sort_keys=True), encoding="utf-8")
    return {"challenger": art["sha"], "path": str(path)}


def diagnostics(limit=200):
    lcon = db()
    cur = lcon.execute("SELECT * FROM diagnostics WHERE reviewed = 0 ORDER BY created_at DESC LIMIT ?", (limit,))
    names = [d[0] for d in cur.description]
    out = [dict(zip(names, r)) for r in cur.fetchall()]
    lcon.close()
    return out


def _isfinite(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))
