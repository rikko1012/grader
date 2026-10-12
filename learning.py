#!/usr/bin/env python3
"""
learning.py - continuous learning from settled matches (2026-10-12). SHADOW RESEARCH: nothing here changes a grade.

Owner rule: continuous learning = continuous MEASUREMENT and evidence-based improvement - NOT changing the live model
after each result. Every valid settled result teaches the research pipeline; only an improvement demonstrated on fixed,
pre-registered forward reviews - and then promoted by an explicit admin action - may change members' grades, and even
then only through a code release (grader.py / perf.py / server.py never read this module or its database).

One background thread (every CYCLE_S seconds), all state in CS2_DATA_DIR/.cache/learning.db:

1. Forecast copy. Every forecast in the pick log (picklog.forecasts, incl. its saved grader inputs) is copied ONCE into
   forecast_copy (INSERT OR IGNORE - never overwritten): line, side, probabilities, projection, scope, model version,
   recommendation, logged time, match start, and the inputs the projection was made from (perf fields, environment,
   roster / identity flags, history-through date).
2. Evaluation ledger (append-only). One EVALUATION UNIT per player x match x stat x scope (board lines from both books
   and member research lines are pooled; owner tests never), evaluated on the evaluation snapshot = the LATEST forecast
   logged >= 30 min before the start (several lines / repeated forecasts of the same total collapse into one unit, so
   they cannot dominate). Each settlement is written once per (unit, settlement version) with the evaluation forecast
   id; a corrected / changed result appends version n+1 that supersedes version n (evaluation reads the latest version
   only - nothing is double counted, nothing is deleted). Buckets: scored, void, push, incomplete, unresolved_identity,
   market_mismatch, settlement_conflict - only "scored" is ever scored.
3. Scorecard (recomputed whenever the ledger gained rows): projection error (MAE, bias), Over/Under hit vs predicted,
   log-loss, Brier, calibration bins and slope - by stat, scope, environment, history depth, model version, projection
   source and line source; every row with n units AND n distinct matches; every interval is a match-grouped bootstrap.
4. Error diagnosis per unit (aggregates only - never an automatic bonus / penalty): expected-rounds error, kill-rate
   error, headshot-rate error, an exact decomposition of the projection miss into those parts, roster / identity flags,
   market-matching flags, PIT and 50% / 80% interval coverage of the forecast's own distribution.
5. Challengers on a schedule: when >= N_NEW new distinct settled matches exist (and no challenger is in its forward
   test) a reproducible dataset snapshot is saved (cutoff, ledger row ids, SHA-256 of the rows) and a challenger of the
   performance model's parameters is fitted with time-ordered validation (see TRAIN_RULE / refit()). A challenger is
   FROZEN (config + data hash) before any forward forecast exists; then every new forecast gets its shadow probability
   beside the live one (members never see it). Forward results that later feed a new training run are recorded as that
   version's development data.
6. Reviews only at fixed windows (REVIEW_RULE: 300 / 600 / 1000 distinct forward matches). Passing makes a challenger
   "eligible for review" - never more. promote() / rollback() are explicit admin actions recorded in an append-only,
   versioned registry. Live grading is not wired to the registry.
7. Safety: data / settlement faults (identity-resolution spikes, HLTV projection outages, recorded corrections the pick
   log does not show, settlement audit differences) pause ledger updates, training and reviews; the dashboard says why.
8. Slip-builder batches are evaluated in their own table (slip_legs) from the saved batches - never mixed with lines.

Dashboard: /admin/learning (+ /admin/learning.json), admin token like /admin/diagnostics.
CLI (local copies):  python learning.py run --picks PATH --db PATH [--batches PATH] [--corrections PATH] [--n-new N]
"""
import collections
import hashlib
import json
import math
import os
import random
import sqlite3
import threading
import time
import traceback
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "learning-1"
DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent)
DB_PATH = DATA_DIR / ".cache" / "learning.db"
EVAL_LEAD_MIN = 30                  # evaluation snapshot = latest forecast at least this long before the start
CYCLE_S = 900                       # one learning cycle every 15 minutes
START_DELAY_S = 240                 # after boot: grading threads first
N_NEW = int(os.environ.get("LEARN_N_NEW") or 150)
PLAUSIBLE_MAX = {"kills": {"1": 50, "12": 90, "123": 130}, "hs": {"1": 35, "12": 60, "123": 90}}   # = server.PLAUSIBLE_MAX
BOOT_B = 300                        # bootstrap resamples (match-grouped) for scorecard intervals

TRAIN_RULE = {
    "family": "perf-refit-1",
    "trigger": "at least N_NEW new distinct settled matches since the last training run, and no challenger in its forward test",
    "n_new": N_NEW,
    "min_rows": 200, "min_matches": 60,
    "rows": "scored evaluation units (latest settlement version) with match start < cutoff and processed <= cutoff, whose "
            "logged perf-2 projection can be inverted exactly (checked against its logged perf-1 twin); history strictly "
            "before the match day",
    "objective": "mean log-loss of the evaluation line's Over/Under outcome (one row per unit)",
    "parameters": "prior strength k0, newcomer prior target prior_m, LAN/online blend ke, headshot shrink k_hs, kills "
                  "spread multiplier c_mult, headshot beta-binomial concentration kappa (coordinate search over fixed "
                  "grids, 2 sweeps, ties -> the live value); expected rounds per map base_r = training rounds shrunk "
                  "toward the live value with 400 maps of prior weight",
    "fixed": "half-life (needs the raw HLTV map history, which the logged inputs do not carry), opponent shrink, "
             "population averages",
    "validation": "time-ordered: fit on the first 60% / score the next 20%, fit on the first 80% / score the last 20% "
                  "(every transformation, incl. base_r, fitted on the training rows only); a challenger is frozen only "
                  "if BOTH validation log-loss gains are > 0, otherwise the run is recorded as 'no candidate'",
}
GRID = {"k0": (150.0, 200.0, 300.0, 450.0, 600.0), "prior_m": (0.88, 0.90, 0.93, 0.96, 1.0), "ke": (400.0, 800.0, 1600.0),
        "k_hs": (10.0, 25.0, 50.0, 100.0), "c_mult": (0.9, 0.95, 1.0, 1.05, 1.1, 1.2), "kappa": (50.0, 100.0, 200.0)}
BASE_R_PRIOR_MAPS = 400.0
REVIEW_RULE = {
    "windows": (300, 600, 1000),    # distinct forward matches - decisions happen ONLY here (no peeking-until-good)
    "cohort": "the first W distinct forward matches (by start) whose evaluation units are scored, frozen at decision time",
    "min_gain": 0.002,              # mean per-unit log-loss improvement vs the live model replayed on the same inputs
    "ci_level": round(1 - 0.05 / 3, 4),   # 95% family-wise over the 3 looks (Bonferroni) - stricter than a plain 95%
    "slope_band": (0.8, 1.2),       # calibration slope CI must overlap this band around 1 ...
    "slope_ci_max_width": 1.0,      # ... and be narrow enough to mean something (an interval like -7 to 13 overlaps anything)
    "slice_min_matches": 50, "slice_tol": 0.005,   # no stat x scope slice with >= 50 matches may lose more than this
    "replay_gap_max": 0.01,         # live replay must reproduce the logged live chance (mean abs gap) - else not comparable
    "futility": "a failed window retires the challenger when its gain point estimate is <= 0 or it was the last window",
    "automatic": False,
    "on_pass": "status 'eligible for review' only; promotion is an explicit admin action with a written reason",
}
FAULTS = {
    "identity_window_h": 24, "identity_min_n": 20, "identity_share": 0.25, "identity_vs_week": 2.0,
    "outage_share": 0.25,           # forecasts in the window whose performance model failed on an HLTV outage
    "audit_max": 0,                 # unacknowledged settlement-audit differences tolerated (0 = any one pauses)
}
# Owner option 3 (2026-10-12): the 10-day board diagnosis (Oct 3-12, 991 units / 132 matches; units in the research folder,
# data hash below) found that on Maps 1-2 the market line carried almost all the information for kills: pulling the
# projection toward the line, w fitted on EARLIER days only, improved next-day log-loss by +0.0149 [0.0039, 0.0264]
# (kills +0.0301 [0.0077, 0.0528]). Frozen here as a SHADOW challenger with the weights fitted on all 10 days - it never
# changes a grade; the fixed 300 / 600 / 1000-match reviews decide whether it is even eligible for the owner's review.
# EXACT METHOD (CHALLENGERS.md): a transform of the live PROBABILITY, not of the projection or the kill rate:
#   p_challenger = Phi(w x Phi^-1(p_live)),  p_live = the logged forecast's p_more = P(Over | no push), 3 decimals,
#   after every live adjustment (kills cap, safety margins, HS beta-binomial). w = 0 -> exactly 0.5; w = 1 -> p_live.
# It coincides with "projection' = line + w x (projection - line), same normal spread" ONLY for a normal predictive
# distribution with no cap - not in general. Push probability is not modelled (both are conditional on no push).
# kills w = 0.0 and HS w = 0.5 on Maps 1-2; other map scopes unchanged. FROZEN: any change = a new family/version.
LINE_BLEND = {"family": "line-blend-1", "w": {"kills": 0.0, "hs": 0.5}, "scopes": ["12"],
              "fit": "w per stat on board units Oct 3-12 (Maps 1-2), grid 0.00-1.20 step 0.05, minimum log-loss of the Over outcome",
              "data_hash": "49e7ac29547b86a9", "training_cutoff": "2026-10-12T15:00:00+00:00",
              "evidence": {"walk_forward_gain_all": [0.0149, 0.0039, 0.0264], "walk_forward_gain_kills12": [0.0301, 0.0077, 0.0528],
                           "walk_forward_gain_hs12": [0.0048, -0.0062, 0.0159], "test_units": 536, "test_matches": 87}}


# Display names (owner review 2026-10-12): lb-001 shrinks our own probability toward 50%; it does not use the book
# price and does not test projection accuracy against the line. The frozen family id stays as it is.
CHALLENGER_NAMES = {"line-blend-1": "probability-shrinkage challenger (toward 50%)", "perf-refit-1": "performance-model refit"}


def line_blend_p(p_live, prop, scope, cfg=LINE_BLEND):
    """The line-blend challenger's chance of Over from the live chance (same spread, projection pulled toward the line)."""
    if p_live is None or (scope or "12") not in cfg["scopes"] or prop not in cfg["w"]:
        return p_live
    q = min(max(float(p_live), 1e-6), 1 - 1e-6)
    from statistics import NormalDist
    nd = NormalDist()
    return nd.cdf(cfg["w"][prop] * nd.inv_cdf(q))


def _ensure_line_blend(con, now):
    """Freeze the line-blend challenger once (its forward test = forecasts logged after this moment)."""
    if con.execute("SELECT 1 FROM challengers WHERE family = ?", (LINE_BLEND["family"],)).fetchone():
        return
    h = _sha(LINE_BLEND)
    cid = f"lb-001-{h[:8]}"
    con.execute("INSERT INTO challengers (id, family, created_at, frozen_at, snapshot_id, data_hash, training_cutoff, config, "
                "config_hash, base_config, status, status_at, status_reason, revises, development) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, LINE_BLEND["family"], _iso(now), _iso(now), None, LINE_BLEND["data_hash"], LINE_BLEND["training_cutoff"],
                 json.dumps(LINE_BLEND, sort_keys=True), h, json.dumps({"live": "logged chance"}), "shadow", _iso(now),
                 "frozen (owner option 3, 2026-10-12); forward test = forecasts logged after the freeze", None, "[]"))
    con.execute("INSERT INTO events (at, kind, detail) VALUES (?,?,?)", (_iso(now), "challenger", json.dumps({"id": cid, "status": "shadow"})))


BUCKETS = ("scored", "void", "push", "incomplete", "unresolved_identity", "market_mismatch", "settlement_conflict")

# perf.py parameters each logged projection version was made with (copied from perf.py 2026-10-12). Every row is checked
# against its logged perf-1 twin (g7) - a row made with other parameters fails the check and is never refitted.
RECOVERY = {"perf-2": {"k0": 300.0, "ke": 800.0, "k_hs": 25.0, "pop": 0.66442, "pop_hs": 0.5106, "prior_m": 0.93},
            "perf-1": {"k0": 300.0, "ke": 800.0, "k_hs": 150.0, "pop": 0.66442, "pop_hs": 0.5106, "prior_m": 1.0}}
LIVE_CFG = {"k0": 300.0, "prior_m": 0.93, "ke": 800.0, "k_hs": 25.0, "base_r": None, "c_mult": 1.0, "kappa": 100.0,
            "cap_kills": 0.78}     # base_r None = each row's own logged rounds per map (the live value)

_RUN_LOCK = threading.Lock()
STATE = {"dashboard": None, "running": False, "last_error": None, "last_cycle": None, "disabled": False}


# ------------------------------------------------------------------------------------------------ small helpers
def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _iso(d):
    return d.astimezone(timezone.utc).isoformat(timespec="seconds") if d else None


def _niso(s):
    return _iso(_ts(s))


def _now(now=None):
    return now or datetime.now(timezone.utc)


def _r(x, n=4):
    return None if x is None else round(x, n)


def _ll(p, y):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -math.log(p if y else 1 - p)


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def connect(db=None):
    p = Path(db or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS forecast_copy (forecast_id INTEGER PRIMARY KEY, pick_id INTEGER, kind TEXT, logged_at TEXT,
        match_start TEXT, line REAL, side TEXT, p_pick REAL, p_more REAL, grade TEXT, units REAL, lambda REAL,
        model_version TEXT, unit_key TEXT, match_slug TEXT, player TEXT, prop TEXT, scope TEXT, source TEXT, book TEXT,
        line_type TEXT, test INTEGER, inputs_sha1 TEXT, features TEXT, copied_at TEXT);
    CREATE INDEX IF NOT EXISTS fc_unit ON forecast_copy (unit_key);
    CREATE INDEX IF NOT EXISTS fc_logged ON forecast_copy (logged_at);
    CREATE TABLE IF NOT EXISTS ledger (rid INTEGER PRIMARY KEY AUTOINCREMENT, unit_key TEXT NOT NULL, forecast_id INTEGER,
        settlement_version INTEGER NOT NULL, fingerprint TEXT, bucket TEXT, reason TEXT, match_slug TEXT, match_start TEXT,
        forecast_logged_at TEXT, prop TEXT, scope TEXT, line REAL, actual REAL, over_hit INTEGER, p_over REAL, lam REAL,
        dims TEXT, diag TEXT, pick_ids TEXT, settled_at TEXT, processed_at TEXT, supersedes INTEGER, change TEXT,
        UNIQUE (unit_key, settlement_version));
    CREATE INDEX IF NOT EXISTS ledger_fc ON ledger (forecast_id, settlement_version);
    CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, created_at TEXT, cutoff TEXT, data_hash TEXT, n_rows INTEGER,
        n_matches INTEGER, ledger_rows TEXT, rows BLOB);
    CREATE TABLE IF NOT EXISTS trainings (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, cutoff TEXT, snapshot_id TEXT,
        outcome TEXT, challenger_id TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS challengers (id TEXT PRIMARY KEY, family TEXT, created_at TEXT, frozen_at TEXT,
        snapshot_id TEXT, data_hash TEXT, training_cutoff TEXT, config TEXT, config_hash TEXT, base_config TEXT,
        status TEXT, status_at TEXT, status_reason TEXT, revises TEXT, development TEXT);
    CREATE TABLE IF NOT EXISTS shadow (challenger_id TEXT, forecast_id INTEGER, p_over_ch REAL, p_over_base REAL,
        p_over_logged REAL, lam_ch REAL, lam_base REAL, changed INTEGER, computed_at TEXT, note TEXT,
        PRIMARY KEY (challenger_id, forecast_id));
    CREATE TABLE IF NOT EXISTS reviews (challenger_id TEXT, win INTEGER, decided_at TEXT, passed INTEGER, result TEXT,
        PRIMARY KEY (challenger_id, win));
    CREATE TABLE IF NOT EXISTS registry (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, action TEXT, version_label TEXT,
        challenger_id TEXT, config TEXT, training_cutoff TEXT, eval_from TEXT, eval_to TEXT, eval_matches INTEGER,
        activation_at TEXT, reason TEXT, actor TEXT, previous_id INTEGER, restores_id INTEGER);
    CREATE TABLE IF NOT EXISTS slip_legs (batch_id TEXT, slip_idx INTEGER, leg_idx INTEGER, kind TEXT, n INTEGER,
        batch_created TEXT, match_slug TEXT, p REAL, side TEXT, line REAL, result TEXT, recorded_at TEXT,
        PRIMARY KEY (batch_id, slip_idx, leg_idx));
    CREATE TABLE IF NOT EXISTS acks (item TEXT PRIMARY KEY, at TEXT, reason TEXT);
    CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT, detail TEXT);
    """)
    for tbl, col in (("ledger", "lines"), ("forecast_copy", "probs_raw"), ("forecast_copy", "data_rev")):
        try:                              # 2026-10-12 owner review: every distinct line kept; raw chances + dataset revision
            con.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    return con


def _meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return json.loads(r["v"]) if r else default


def _set_meta(con, k, v):
    con.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, json.dumps(v, default=str)))


# ------------------------------------------------------------------------------------------------ reading the pick log
PICK_COLS = ("id", "test", "status", "actual", "hit", "line", "pick", "prop", "scope", "match_slug", "match_start",
             "logged_at", "player", "slug", "team", "opponent", "book", "line_type", "market_id", "result", "settled_at",
             "model_version", "p_more", "p_pick", "lambda")


def _picklog_path(picks_path=None):
    if picks_path:
        return Path(picks_path)
    import picklog
    return picklog.DB


def read_picklog(picks_path=None, after_forecast_id=0, limit=3000):
    """(picks, forecasts with id > after_forecast_id, up to `limit`) from ONE read transaction (consistent snapshot).
    Read-only; the large `factors` column is never read; forecast inputs are decoded here."""
    p = _picklog_path(picks_path)
    if not p.exists():
        raise FileNotFoundError(f"pick log not found at {p}")
    con = sqlite3.connect(str(p), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        fcols = {r[1] for r in con.execute("PRAGMA table_info(forecasts)")}
        opt = "".join(f", {c}" for c in ("probs_raw", "data_rev") if c in fcols)
        fr = [dict(r) for r in con.execute(
            "SELECT id, pick_id, kind, logged_at, match_start, line, pick, p_pick, p_more, grade, units, lambda, "
            f"model_version, inputs{opt} FROM forecasts WHERE id > ? ORDER BY id LIMIT ?", (after_forecast_id, limit))]
        cols = {r[1] for r in con.execute("PRAGMA table_info(picks)")}
        picks = [dict(r) for r in con.execute(f"SELECT {', '.join(c for c in PICK_COLS if c in cols)} FROM picks")]
        con.execute("COMMIT")
    finally:
        con.close()
    for f in fr:
        blob = f.pop("inputs", None)
        f["inputs_sha1"] = hashlib.sha1(blob).hexdigest() if blob else None
        try:
            f["inputs"] = json.loads(zlib.decompress(blob)) if blob else None
        except Exception:
            f["inputs"] = None
    return picks, fr


def read_corrections(path=None):
    if path is None:
        try:
            import picklog
            path = picklog.CORRECTIONS
        except Exception:
            path = DATA_DIR / "settlement_corrections.jsonl"
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def read_batches(path=None):
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []
    try:
        import slip_batches
        return slip_batches.read()
    except Exception:
        return []


# ------------------------------------------------------------------------------------------------ units + features
def source_of(p):
    t = p.get("test") or 0
    return ("board_pp" if p.get("book") == "prizepicks" else "board") if t == 2 else "member" if t == 0 else "owner test"


def unit_key(p):
    who = str(p.get("slug") or p.get("player") or "").lower()
    return f"{who}|{match_of(p)}|{p.get('prop')}|{p.get('scope') or '12'}"


def match_of(p):
    """The canonical match identity (identity.assign, tagged on each pick as _mid by tag_matches) - one match logged under a
    BO3.gg slug and under hltv-<id> is one match (2026-10-12 audit, D1). Falls back to the slug."""
    return p.get("_mid") or p.get("match_slug")


UNIT_IDENTITY = 2                   # 1 = slug units (until 2026-10-12), 2 = identity.assign canonical matches


def tag_matches(picks):
    import identity
    m = identity.assign([p for p in picks if p.get("id") is not None])
    for p in picks:
        if p.get("id") in m:
            p["_mid"] = m[p["id"]]
    return picks


def _migrate_unit_identity(con, picks, now):
    """Once per identity version: re-key the forecast copies and rebuild the ledger from the original forecasts. The old
    ledger is kept as ledger_v<n> (never deleted); the rebuild is recorded as an event."""
    have = _meta(con, "unit_identity", 1)
    if have == UNIT_IDENTITY:
        return False
    by = {p["id"]: p for p in picks}
    for fid, pid in con.execute("SELECT forecast_id, pick_id FROM forecast_copy").fetchall():
        p = by.get(pid)
        if p:
            con.execute("UPDATE forecast_copy SET unit_key = ?, match_slug = ? WHERE forecast_id = ?", (unit_key(p), match_of(p), fid))
    n = con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    old = f"ledger_v{have}_{now.strftime('%Y%m%d%H%M%S')}"
    con.execute(f"ALTER TABLE ledger RENAME TO {old}")
    con.execute("DROP INDEX IF EXISTS ledger_fc")
    con.execute("""CREATE TABLE ledger (rid INTEGER PRIMARY KEY AUTOINCREMENT, unit_key TEXT NOT NULL, forecast_id INTEGER,
        settlement_version INTEGER NOT NULL, fingerprint TEXT, bucket TEXT, reason TEXT, match_slug TEXT, match_start TEXT,
        forecast_logged_at TEXT, prop TEXT, scope TEXT, line REAL, actual REAL, over_hit INTEGER, p_over REAL, lam REAL,
        dims TEXT, diag TEXT, pick_ids TEXT, settled_at TEXT, processed_at TEXT, supersedes INTEGER, change TEXT, lines TEXT,
        UNIQUE (unit_key, settlement_version))""")
    con.execute("CREATE INDEX IF NOT EXISTS ledger_fc ON ledger (forecast_id, settlement_version)")
    _set_meta(con, "unit_identity", UNIT_IDENTITY)
    con.execute("INSERT INTO events (at, kind, detail) VALUES (?,?,?)", (_iso(now), "unit_identity", json.dumps(
        {"from": have, "to": UNIT_IDENTITY, "old_ledger": old, "old_rows": n,
         "why": "one match logged under a BO3.gg slug and hltv-<id> counted twice (audit D1); originals untouched"})))
    con.commit()
    return True


_PERF_KEEP = ("version", "lambda", "lambda_kills", "rate", "rate_all_envs", "opp_factor", "hs_per_kill", "rounds_per_map",
              "maps", "sd", "spread_c", "spread_c_kills", "history_rounds_weighted", "env_rounds_weighted", "player_maps",
              "newest_map_day", "lan_known", "data_quality", "error", "hltv_error")


def features(gi):
    """The parts of a forecast's saved grader inputs the learning pipeline uses (small, JSON)."""
    gi = gi if isinstance(gi, dict) else {}
    pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else None
    env = gi.get("environment")
    f = {"env": (env.get("tonight") if isinstance(env, dict) else None), "maps_played": gi.get("maps_played"),
         "sample_maps": gi.get("sample_maps"), "roster_confirmed": gi.get("roster_confirmed"),
         "is_standin": gi.get("is_standin"), "on_listed_team": gi.get("on_listed_team"), "has_inputs": bool(gi)}
    if pf is not None:
        f["perf"] = {k: pf.get(k) for k in _PERF_KEEP if pf.get(k) is not None}
        g7 = pf.get("g7") if isinstance(pf.get("g7"), dict) else None
        if g7:
            f["perf"]["g7"] = {k: g7.get(k) for k in ("version", "rate", "hs_per_kill", "lambda", "lambda_kills") if g7.get(k) is not None}
        if pf.get("newest_map_day"):
            try:
                f["history_through"] = datetime.fromordinal(int(pf["newest_map_day"])).date().isoformat()
            except (TypeError, ValueError, OverflowError):
                pass
    err = " ".join(str((pf or {}).get(k) or "") for k in ("error", "hltv_error")).lower()
    f["identity"] = "unresolved" if "not found" in err else "ok"
    f["outage"] = "unreachable" in err
    return f


def projection_source(feat, lam):
    """Which projection the forecast actually used: the performance model (HLTV or the BO3.gg fallback) when its lambda is
    the forecast's lambda, otherwise the older pipeline."""
    pf = (feat or {}).get("perf") or {}
    v = str(pf.get("version") or "")
    if v and pf.get("lambda") is not None and lam is not None and abs(float(lam) - float(pf["lambda"])) <= 0.35:
        return v
    if pf.get("error") and "unreachable" in str(pf["error"]).lower():
        return "older pipeline (HLTV outage)"
    return "older pipeline"


def history_band(feat):
    pf = (feat or {}).get("perf") or {}
    n = pf.get("player_maps") if pf.get("player_maps") is not None else (feat or {}).get("maps_played")
    if n is None:
        return "unknown"
    n = int(n)
    return "<10 maps" if n < 10 else "10-29 maps" if n < 30 else "30-99 maps" if n < 100 else "100+ maps"


def p_over_of(f):
    if f.get("p_more") is not None:
        return float(f["p_more"])
    if f.get("p_pick") is None:
        return None
    return float(f["p_pick"]) if f.get("side") == "More" else 1 - float(f["p_pick"])


# ------------------------------------------------------------------------------------------------ distributions
def kills_pmf(lam, c):
    """== perf.kills_pmf: discretised normal (sd c*sqrt(lam)) conditioned on kills >= 0, as a list by kill total."""
    sd = c * math.sqrt(max(lam, 1.0))
    raw = [max(0.0, _phi((k + 0.5 - lam) / sd) - _phi((k - 0.5 - lam) / sd)) for k in range(0, int(lam + 8 * sd) + 2)]
    tot = sum(raw)
    return [x / tot for x in raw]


def hs_pmf(lam_k, hsr, kappa, c):
    """== perf.hs_pmf (HS | kills beta-binomial averaged over kills) with a multiplicative recurrence instead of one
    lgamma per term (~10x faster; equal to 1e-9 - see tests). List by headshot total."""
    a, b = hsr * kappa, (1 - hsr) * kappa
    lg_ab = math.lgamma(a + b) - math.lgamma(b)
    out = [0.0]
    for k, pk in enumerate(kills_pmf(lam_k, c)):
        if pk <= 0:
            continue
        if len(out) < k + 1:
            out.extend([0.0] * (k + 1 - len(out)))
        t = math.exp(math.lgamma(k + b) + lg_ab - math.lgamma(k + a + b))      # P(HS = 0 | k)
        if t <= 0.0:                                                           # underflow: exact slow path for this k
            base = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) - math.lgamma(k + a + b)
            terms = [math.exp(math.lgamma(k + 1) - math.lgamma(y + 1) - math.lgamma(k - y + 1) + math.lgamma(y + a)
                              + math.lgamma(k - y + b) + base) for y in range(k + 1)]
        else:
            terms = []
            for y in range(k + 1):
                terms.append(t)
                if y < k:
                    t *= (k - y) / (y + 1) * (y + a) / (k - y - 1 + b)
        tk = sum(terms)
        for y, v in enumerate(terms):
            out[y] += pk * v / tk
    return out


def _kills_three(lam, sd, line):
    up, dn = (math.floor(line) + 0.5,) * 2 if line != int(line) else (line + 0.5, line - 0.5)   # == perf.probs / grader._cuts
    po, pu = 1 - _phi((up - lam) / sd), _phi((dn - lam) / sd)
    return po, pu


# ------------------------------------------------------------------------------------------------ challenger family
def _rec_params(v):
    v = str(v or "")
    return RECOVERY.get(v[:-4] if v.endswith("-bo3") else v)


def recover(feat):
    """Exact inversion of a logged perf-2 projection into the weighted sums it was made from (kills K over rounds r,
    tonight's-environment kills KE over rounds re, headshots h over HS-eligible kills x). Two logged twins (perf-2 and its
    perf-1 'g7' comparison, different k_hs) give two equations for the headshot sums. Every row is CHECKED: the perf-1
    rate rebuilt from the recovered sums must equal the logged g7 rate, and the logged kill projection must be
    reproduced - a row made with other parameters fails and is never used. None = not invertible."""
    pf = (feat or {}).get("perf") or {}
    g7 = pf.get("g7") or {}
    P, P1 = _rec_params(pf.get("version")), _rec_params(g7.get("version"))
    need = ("rate", "rate_all_envs", "history_rounds_weighted", "env_rounds_weighted", "opp_factor", "hs_per_kill",
            "rounds_per_map", "maps", "lambda_kills", "lambda")
    if P is None or P1 is None or P is P1 or any(pf.get(k) is None for k in need) or g7.get("rate") is None \
            or g7.get("hs_per_kill") is None or not (pf.get("spread_c_kills") or pf.get("spread_c")):
        return None
    r, ra, re_, rate = (float(pf[k]) for k in ("history_rounds_weighted", "rate_all_envs", "env_rounds_weighted", "rate"))
    K = ra * (r + P["k0"]) - P["k0"] * P["pop"] * P["prior_m"]
    blend = re_ > 1e-9
    KE = rate * (re_ + P["ke"]) - P["ke"] * ra if blend else 0.0
    ra1 = (K + P1["k0"] * P1["pop"] * P1["prior_m"]) / (r + P1["k0"])
    rate1 = (KE + P1["ke"] * ra1) / (re_ + P1["ke"]) if blend else ra1
    if abs(rate1 - float(g7["rate"])) > 1e-6 * max(1.0, abs(float(g7["rate"]))) or K < -1e-6 or KE < -1e-6:
        return None
    A, B, ph = float(pf["hs_per_kill"]), float(g7["hs_per_kill"]), P["pop_hs"]
    kh, kh1 = P["k_hs"], P1["k_hs"]
    if abs(A - B) < 1e-12:
        x, h = (0.0, 0.0) if abs(A - ph) < 1e-9 else (None, None)
    else:
        x = (kh1 * (B - ph) - kh * (A - ph)) / (A - B)
        h = A * (x + kh) - kh * ph
        if x < -1e-6 or h < -1e-6 or h > x + 1e-6:
            return None
    lam_k = rate * float(pf["opp_factor"]) * float(pf["rounds_per_map"]) * float(pf["maps"])
    if abs(lam_k - float(pf["lambda_kills"])) > 1e-6 * max(1.0, lam_k):
        return None
    return {"K": K, "KE": KE, "r": r, "re": re_, "blend": blend, "x": x, "h": h, "A": A, "pop": P["pop"], "pop_hs": ph,
            "opp": float(pf["opp_factor"]), "maps": float(pf["maps"]), "base_r_live": float(pf["rounds_per_map"]),
            "c_kills": float(pf.get("spread_c_kills") or pf.get("spread_c"))}


def apply_cfg(rec, cfg, prop, line):
    """(projection, chance of Over with pushes removed) for one recovered row under a parameter set - the same path the
    live grader takes for a performance-model forecast (kills: discretised normal, capped; HS: beta-binomial)."""
    ra = (rec["K"] + cfg["k0"] * rec["pop"] * cfg["prior_m"]) / (rec["r"] + cfg["k0"])
    rate = (rec["KE"] + cfg["ke"] * ra) / (rec["re"] + cfg["ke"]) if rec["blend"] else ra
    base_r = cfg["base_r"] if cfg.get("base_r") is not None else rec["base_r_live"]
    lam_k = rate * rec["opp"] * base_r * rec["maps"]
    c = rec["c_kills"] * cfg["c_mult"]
    if prop == "hs":
        hsr = (rec["h"] + cfg["k_hs"] * rec["pop_hs"]) / (rec["x"] + cfg["k_hs"]) if rec["x"] is not None else rec["A"]
        pmf = hs_pmf(lam_k, hsr, cfg["kappa"], c)
        o = sum(v for y, v in enumerate(pmf) if y > line)
        u = sum(v for y, v in enumerate(pmf) if y < line)
        lam = sum(y * v for y, v in enumerate(pmf))
        return lam, (o / (o + u) if o + u > 0 else 0.5)
    o, u = _kills_three(lam_k, c * math.sqrt(max(lam_k, 1.0)), line)
    p = o / (o + u) if o + u > 0 else 0.5
    cap = cfg.get("cap_kills") or 0.78
    return lam_k, min(max(p, 1 - cap), cap)


def _loss(rows, cfg):
    tot = 0.0
    for i, r in enumerate(rows):
        tot += _ll(apply_cfg(r["rec"], cfg, r["prop"], r["line"])[1], r["over"])
        if i % 200 == 199:
            time.sleep(0)                      # let request threads run
    return tot / len(rows) if rows else None


def _estimate_base_r(rows, live):
    per = {}
    for r in rows:
        if r.get("rounds") and r.get("maps_n") and r["match"] not in per:
            per[r["match"]] = (r["rounds"], r["maps_n"])
    s = sum(a for a, _ in per.values()); n = sum(b for _, b in per.values())
    return (s + BASE_R_PRIOR_MAPS * live) / (n + BASE_R_PRIOR_MAPS)


def _live_base_r(rows):
    vals = [r["rec"]["base_r_live"] for r in rows]
    return sum(vals) / len(vals) if vals else 21.74132969862184


def fit(rows, base_cfg=None):
    """Deterministic coordinate search (TRAIN_RULE) on `rows` only. Returns (cfg, trace)."""
    base = dict(base_cfg or LIVE_CFG)
    cfg = dict(base, base_r=round(_estimate_base_r(rows, _live_base_r(rows)), 4))
    cur = _loss(rows, cfg)
    trace = [{"step": "base_r from training rounds", "base_r": cfg["base_r"], "loss": _r(cur, 6)}]
    for sweep in range(2):
        for k, vals in GRID.items():
            best_v, best_l = cfg[k], cur
            for v in vals:
                if v == cfg[k]:
                    continue
                l = _loss(rows, dict(cfg, **{k: v}))
                if l < best_l - 1e-6 or (abs(l - best_l) <= 1e-6 and abs(v - base[k]) < abs(best_v - base[k])):
                    best_v, best_l = v, l
            if best_v != cfg[k]:
                cfg[k], cur = best_v, best_l
                trace.append({"step": f"sweep {sweep + 1}: {k}", k: best_v, "loss": _r(cur, 6)})
    return cfg, trace


def _paired(rows, cfg_a, cfg_b, level=0.95, B=BOOT_B):
    """Mean per-unit log-loss gain of cfg_b over cfg_a with a match-grouped interval."""
    by = collections.defaultdict(lambda: [0.0, 0])
    for r in rows:
        g = _ll(apply_cfg(r["rec"], cfg_a, r["prop"], r["line"])[1], r["over"]) - _ll(apply_cfg(r["rec"], cfg_b, r["prop"], r["line"])[1], r["over"])
        by[r["match"]][0] += g; by[r["match"]][1] += 1
    n = sum(c for _, c in by.values())
    return {"units": n, "matches": len(by), "gain": _r(sum(s for s, _ in by.values()) / n, 5) if n else None,
            "gain_ci": _ci_ratio(list(by.values()), B=B, level=level)}


def refit(rows, base_cfg=None):
    """TRAIN_RULE: time-ordered validation, then the final fit on all rows. Returns a dict with 'candidate' True/False."""
    base = dict(base_cfg or LIVE_CFG)
    rows = sorted(rows, key=lambda r: (r["start"], r["lid"]))
    ms, seen = [], set()
    for r in rows:                                     # matches in time order (all rows of a match stay on one side)
        if r["match"] not in seen:
            seen.add(r["match"]); ms.append(r["match"])
    folds = []
    for tr, va in ((0.6, 0.8), (0.8, 1.0)):
        m_tr, m_va = set(ms[:int(len(ms) * tr)]), set(ms[int(len(ms) * tr):int(len(ms) * va)])
        rt, rv = [r for r in rows if r["match"] in m_tr], [r for r in rows if r["match"] in m_va]
        if not rt or not rv:
            folds.append({"train": len(rt), "validate": len(rv), "gain": None})
            continue
        cfg_f, _ = fit(rt, base)
        live_f = dict(base)
        folds.append(dict(_paired(rv, live_f, cfg_f), train_units=len(rt), train_matches=len(m_tr), config=cfg_f))
    cfg, trace = fit(rows, base)
    ok = all(f.get("gain") is not None and f["gain"] > 0 for f in folds)
    return {"candidate": ok, "config": cfg, "trace": trace, "folds": folds,
            "train_loss_live": _r(_loss(rows, base), 6), "train_loss_fit": _r(_loss(rows, cfg), 6)}


# ------------------------------------------------------------------------------------------------ statistics
def _ci_ratio(groups, B=BOOT_B, level=0.95, seed=7):
    """groups: [(sum, count)] per MATCH -> percentile interval of sum/count when matches are resampled."""
    g = [tuple(x) for x in groups if x[1]]
    n = len(g)
    if n < 2:
        return None
    rnd = random.Random(seed)
    vals = []
    for _ in range(B):
        s = c = 0.0
        for _ in range(n):
            a, b = g[rnd.randrange(n)]
            s += a; c += b
        vals.append(s / c)
    vals.sort()
    lo = int(math.floor((1 - level) / 2 * B))
    hi = min(B - 1, int(math.ceil((1 + level) / 2 * B)) - 1)
    return [round(vals[lo], 4), round(vals[hi], 4)]


def _groups(units, fn):
    by = collections.defaultdict(lambda: [0.0, 0])
    for u in units:
        v = fn(u)
        if v is None:
            continue
        by[u["match"]][0] += v; by[u["match"]][1] += 1
    return list(by.values())


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def logistic_slope(xs, ys):
    """Calibration slope: logistic regression of the outcome on logit(p) (Newton). Returns (intercept, slope) or None."""
    if len(xs) < 10 or len(set(ys)) < 2:
        return None
    a, b = 0.0, 1.0
    for _ in range(30):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, ys):
            z = max(min(a + b * x, 30), -30)
            p = 1 / (1 + math.exp(-z)); w = p * (1 - p)
            g0 += y - p; g1 += (y - p) * x; h00 += w; h01 += w * x; h11 += w * x * x
        det = h00 * h11 - h01 * h01
        if det <= 1e-12:
            return None
        da, db = (h11 * g0 - h01 * g1) / det, (-h01 * g0 + h00 * g1) / det
        a += da; b += db
        if abs(da) + abs(db) < 1e-9:
            break
    return a, b


def slope_ci(units, pkey="p", ykey="over", B=200, level=0.95, seed=5):
    fitp = logistic_slope([_logit(u[pkey]) for u in units], [u[ykey] for u in units])
    if fitp is None:
        return None, None
    by = collections.defaultdict(list)
    for u in units:
        by[u["match"]].append(u)
    ms = list(by.values())
    if len(ms) < 5:
        return round(fitp[1], 3), None
    rnd = random.Random(seed)
    vals = []
    for _ in range(B):
        us = [u for _ in ms for u in ms[rnd.randrange(len(ms))]]
        f = logistic_slope([_logit(u[pkey]) for u in us], [u[ykey] for u in us])
        if f is not None:
            vals.append(f[1])
    if len(vals) < B // 2:
        return round(fitp[1], 3), None
    vals.sort()
    return round(fitp[1], 3), [round(vals[int((1 - level) / 2 * len(vals))], 3), round(vals[int((1 + level) / 2 * len(vals)) - 1], 3)]


def metrics(units, B=BOOT_B, slope=False):
    """Scorecard row for scored units: {match, p, over, lam, actual}. n units AND n distinct matches; match-grouped CIs."""
    if not units:
        return {"units": 0, "matches": 0}
    errs = [u for u in units if u.get("lam") is not None]
    out = {"units": len(units), "matches": len({u["match"] for u in units}),
           "mae": _r(sum(abs(u["lam"] - u["actual"]) for u in errs) / len(errs), 3) if errs else None,
           "bias": _r(sum(u["lam"] - u["actual"] for u in errs) / len(errs), 3) if errs else None,
           "bias_ci": _ci_ratio(_groups(errs, lambda u: u["lam"] - u["actual"]), B) if errs else None,
           "pred_over": _r(sum(u["p"] for u in units) / len(units)),
           "actual_over": _r(sum(u["over"] for u in units) / len(units)),
           "over_gap_ci": _ci_ratio(_groups(units, lambda u: u["over"] - u["p"]), B),
           "log_loss": _r(sum(_ll(u["p"], u["over"]) for u in units) / len(units)),
           "log_loss_ci": _ci_ratio(_groups(units, lambda u: _ll(u["p"], u["over"])), B),
           "brier": _r(sum((u["p"] - u["over"]) ** 2 for u in units) / len(units))}
    out["over_gap"] = _r(out["actual_over"] - out["pred_over"])
    if slope:
        out["calibration_slope"], out["calibration_slope_ci"] = slope_ci(units)
    return out


def calibration(units, B=BOOT_B):
    out = []
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        b = [u for u in units if lo <= u["p"] < hi or (i == 9 and u["p"] == 1.0)]
        if b:
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "units": len(b), "matches": len({u['match'] for u in b}),
                        "predicted": _r(sum(u["p"] for u in b) / len(b)), "actual": _r(sum(u["over"] for u in b) / len(b)),
                        "actual_ci": _ci_ratio(_groups(b, lambda u: u["over"]), B)})
    return out


def directional(units, B=BOOT_B):
    """Over and Under calls separately: the side the model favoured, its average chance and how often it happened."""
    out = []
    for name, sel, pr, hit in (("Over (chance of Over > 50%)", lambda u: u["p"] > 0.5, lambda u: u["p"], lambda u: u["over"]),
                               ("Under (chance of Over < 50%)", lambda u: u["p"] < 0.5, lambda u: 1 - u["p"], lambda u: 1 - u["over"])):
        us = [u for u in units if sel(u)]
        if us:
            out.append({"side": name, "units": len(us), "matches": len({u['match'] for u in us}),
                        "predicted": _r(sum(pr(u) for u in us) / len(us)), "actual": _r(sum(hit(u) for u in us) / len(us)),
                        "gap_ci": _ci_ratio(_groups(us, lambda u: hit(u) - pr(u)), B)})
    return out


# ------------------------------------------------------------------------------------------------ settlement checks
def _scope_total(per_map, won, scope, prop):
    from scope import scope_total
    maps = [{"number": i + 1, "kills": km[0], "headshots": km[1], **({"won": won[i]} if won and i < len(won) else {})}
            for i, km in enumerate(per_map or []) if isinstance(km, (list, tuple)) and len(km) >= 2]
    t, _ = scope_total(maps, scope or "12", "hs" if prop == "hs" else "kills") if maps else (None, None)
    return t


def audit(picks):
    """No-network settlement audit of the pick log (board + member lines): status / hit / total / stored map detail must
    agree. Returns [{pick_id, unit_key, issue}]."""
    out = []
    for p in picks:
        if (p.get("test") or 0) not in (0, 2) or p.get("status") not in ("settled", "push"):
            continue
        a, line = p.get("actual"), p.get("line")
        iss = None
        if a is None:
            iss = "settled without a total"
        elif p["status"] == "push" and a != line:
            iss = f"push but total {a} is not the line {line}"
        elif p["status"] == "settled" and a == line:
            iss = f"total {a} equals the line: should be a push"
        elif p["status"] == "settled" and p.get("hit") is not None and int(p["hit"]) != int((a > line) == (p.get("pick") == "More")):
            iss = "hit flag disagrees with the total"
        else:
            try:
                res = json.loads(p.get("result") or "{}") or {}
            except ValueError:
                res = {}
            if res.get("per_map"):
                try:
                    t = _scope_total(res["per_map"], res.get("won"), p.get("scope") or "12", p.get("prop"))
                except Exception:
                    t = None
                if t is not None and t != a:
                    iss = f"stored map detail gives {t}, the settled total is {a}"
        if iss:
            out.append({"pick_id": p["id"], "unit_key": unit_key(p), "issue": iss})
    return out


def pending_corrections(picks, corrections):
    """Corrections recorded in settlement_corrections.jsonl that the pick log does not show (last entry per pick)."""
    last = {}
    for c in corrections:
        if c.get("id") is not None and isinstance(c.get("new"), dict):
            last[c["id"]] = c
    by = {p["id"]: p for p in picks}
    out = []
    for pid, c in last.items():
        p = by.get(pid)
        if p is None:
            continue
        n = c["new"]
        if (n.get("status") and n["status"] != p.get("status")) or ("actual" in n and n["actual"] != p.get("actual")):
            out.append({"pick_id": pid, "unit_key": unit_key(p), "issue": f"correction says {n.get('status')} {n.get('actual')}, "
                        f"the pick log shows {p.get('status')} {p.get('actual')}"})
    return out


def faults(con, picks, corrections, now):
    """Reasons to pause learning updates (empty list = run). Acknowledged audit items do not pause."""
    acks = {r["item"] for r in con.execute("SELECT item FROM acks")}
    out = []
    pend = pending_corrections(picks, corrections)
    if pend:
        out.append({"code": "corrections_pending", "message": f"{len(pend)} recorded settlement correction(s) are not reflected in "
                    "the pick log - apply them (or re-run the settlement audit) before learning continues", "items": pend[:50]})
    aud = [a for a in audit(picks) if f"pick:{a['pick_id']}" not in acks and f"unit:{a['unit_key']}" not in acks]
    if len(aud) > FAULTS["audit_max"]:
        out.append({"code": "settlement_audit", "message": f"{len(aud)} settled line(s) fail the settlement audit (status, hit, "
                    "total and stored map detail must agree) - fix them or acknowledge each one", "items": aud[:50]})
    w = FAULTS["identity_window_h"]
    t0, t1 = _iso(now - timedelta(hours=w)), _iso(now - timedelta(days=7))
    rec = con.execute("SELECT features FROM forecast_copy WHERE logged_at > ? AND COALESCE(test, 0) != 1", (t0,)).fetchall()
    wk = con.execute("SELECT features FROM forecast_copy WHERE logged_at > ? AND logged_at <= ? AND COALESCE(test, 0) != 1",
                     (t1, t0)).fetchall()
    fr = [json.loads(r["features"] or "{}") for r in rec]
    fw = [json.loads(r["features"] or "{}") for r in wk]
    if len(fr) >= FAULTS["identity_min_n"]:
        sh = sum(f.get("identity") == "unresolved" for f in fr) / len(fr)
        shw = sum(f.get("identity") == "unresolved" for f in fw) / len(fw) if fw else 0.0
        if sh >= max(FAULTS["identity_share"], FAULTS["identity_vs_week"] * shw):
            out.append({"code": "identity_spike", "message": f"{sh:.0%} of the last {w} h of forecasts ({len(fr)}) could not find "
                        f"the player in HLTV (previous week {shw:.0%}) - an identity-resolution fault, not a model signal"})
        so = sum(bool(f.get("outage")) for f in fr) / len(fr)
        if so >= FAULTS["outage_share"]:
            out.append({"code": "projection_outage", "message": f"{so:.0%} of the last {w} h of forecasts were made while the HLTV "
                        "database was unreachable"})
    return out


# ------------------------------------------------------------------------------------------------ the cycle
def _copy_forecasts(con, picks, forecasts, now):
    by = {p["id"]: p for p in picks}
    n = 0
    last = _meta(con, "last_forecast_id", 0)
    for f in forecasts:
        p = by.get(f["pick_id"])
        if p is None and (_ts(f.get("logged_at")) or now) > now - timedelta(days=1):
            _set_meta(con, "last_forecast_id", last)
            return n, True                             # its pick row is not visible yet: next cycle
        p = p or {}
        feat = features(f.get("inputs"))
        con.execute("INSERT OR IGNORE INTO forecast_copy (forecast_id, pick_id, kind, logged_at, match_start, line, side, p_pick, "
                    "p_more, grade, units, lambda, model_version, unit_key, match_slug, player, prop, scope, source, book, "
                    "line_type, test, inputs_sha1, features, copied_at, probs_raw, data_rev) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f["id"], f["pick_id"], f.get("kind"), _niso(f.get("logged_at")), _niso(f.get("match_start") or p.get("match_start")),
                     f.get("line"), f.get("pick"), f.get("p_pick"), f.get("p_more"), f.get("grade"), f.get("units"), f.get("lambda"),
                     f.get("model_version"), unit_key(p) if p else None, match_of(p) if p else None, p.get("player"), p.get("prop"),
                     p.get("scope") or "12", source_of(p) if p else None, p.get("book"), p.get("line_type"), p.get("test"),
                     f.get("inputs_sha1"), json.dumps(feat, separators=(",", ":"), default=str), _iso(now),
                     f.get("probs_raw"), f.get("data_rev")))
        last = max(last, f["id"])
        n += 1
    _set_meta(con, "last_forecast_id", last)
    return n, False


def _diag(feat, p_res, prop, scope, lam, actual, line, unit_rows):
    """Per-unit error diagnosis (stored with the ledger row; only ever aggregated)."""
    d = {"flags": []}
    pf = (feat or {}).get("perf") or {}
    src = projection_source(feat, lam)
    if feat.get("is_standin"):
        d["flags"].append("stand-in")
    if feat.get("roster_confirmed") is False:
        d["flags"].append("roster not confirmed")
    if src.endswith("-bo3"):
        d["flags"].append("BO3.gg history (HLTV identity not found)")
    elif src.startswith("older"):
        d["flags"].append("older pipeline projection")
    if "thin" in str(pf.get("data_quality") or "") or "no history" in str(pf.get("data_quality") or ""):
        d["flags"].append("thin history")
    lines = sorted({r.get("line") for r in unit_rows if r.get("line") is not None})
    if len(lines) > 1 and lines[-1] - lines[0] >= 1:
        d["market"] = f"lines differ by {lines[-1] - lines[0]:g} across books / requests"
    if any((r.get("test") == 2 and r.get("book") and not r.get("market_id")) for r in unit_rows):
        d["market_no_id"] = True
    d["settled_from"] = (p_res or {}).get("source") or "unknown"
    res = p_res or {}
    per_map, rounds = res.get("per_map") or [], res.get("rounds") or []
    used = res.get("maps_used") or {"1": [1], "12": [1, 2], "123": [1, 2, 3]}.get(scope or "12", [1, 2])
    try:
        if per_map and all(0 < i <= len(per_map) for i in used):
            kills = sum(per_map[i - 1][0] for i in used)
            hs = sum((per_map[i - 1][1] or 0) for i in used)
            d["kills_act"], d["hs_act"] = kills, hs
            if rounds and all(0 < i <= len(rounds) and rounds[i - 1] for i in used):
                d["rounds_act"] = sum(rounds[i - 1] for i in used)
                d["maps_n"] = len(used)
    except (TypeError, IndexError):
        pass
    perf_on = src.startswith("perf")
    if perf_on and pf.get("rate") is not None:
        R_pred = float(pf["rounds_per_map"]) * float(pf.get("maps") or len(used))
        rate_pred = float(pf["rate"]) * float(pf.get("opp_factor") or 1.0)
        d["rounds_pred"], d["rate_pred"], d["hsk_pred"] = round(R_pred, 2), round(rate_pred, 4), _r(pf.get("hs_per_kill"))
        lam_perf_k = rate_pred * R_pred
        if d.get("rounds_act") and d.get("kills_act") is not None and lam is not None:
            R, K = d["rounds_act"], d["kills_act"]
            d["rate_act"] = round(K / R, 4)
            c_rounds, c_rate = rate_pred * (R_pred - R), R * (rate_pred - K / R)
            if prop == "hs":
                hsk = float(pf.get("hs_per_kill") or 0)
                d["hsk_act"] = round(d["hs_act"] / K, 4) if K else None
                if K:
                    lam_perf = lam_perf_k * hsk
                    d["parts"] = {"rounds": round(hsk * c_rounds, 3), "kill_rate": round(hsk * c_rate, 3),
                                  "hs_rate": round(K * (hsk - d["hs_act"] / K), 3), "other": round(lam - lam_perf, 3)}
            else:
                d["parts"] = {"rounds": round(c_rounds, 3), "kill_rate": round(c_rate, 3), "other": round(lam - lam_perf_k, 3)}
        try:                                           # PIT of the actual under the forecast's own distribution
            a = int(round(actual))
            if prop == "hs" and pf.get("lambda_kills") and pf.get("hs_per_kill"):
                pmf = hs_pmf(float(pf["lambda_kills"]), float(pf["hs_per_kill"]), 100.0, float(pf.get("spread_c_kills") or pf.get("spread_c")))
                below = sum(pmf[:a]) if a > 0 else 0.0
                at = pmf[a] if a < len(pmf) else 0.0
            else:
                sd = float(pf.get("sd") or 0)
                F = lambda x: _phi((x + 0.5 - float(pf["lambda"])) / sd)
                below, at = F(a - 1), F(a) - F(a - 1)
            pit = below + at / 2
            d["pit"] = round(pit, 4)
            d["in50"], d["in80"] = int(0.25 <= pit <= 0.75), int(0.10 <= pit <= 0.90)
        except Exception:
            pass
    return d


def _ingest(con, picks, now, bad_units, corr_ids):
    """Append ledger rows for every unit whose evaluation changed (idempotent). Returns counts."""
    units = collections.defaultdict(list)
    for p in picks:
        if (p.get("test") or 0) in (0, 2) and p.get("match_slug"):
            units[unit_key(p)].append(p)
    fc = collections.defaultdict(list)
    for r in con.execute("SELECT forecast_id, pick_id, unit_key, logged_at, line_type, source, line, p_more, p_pick, side, book, "
                         "probs_raw FROM forecast_copy WHERE unit_key IS NOT NULL"):
        fc[r["unit_key"]].append(r)
    latest = {r["unit_key"]: r for r in con.execute(
        "SELECT l.unit_key, l.settlement_version, l.fingerprint, l.rid, l.processed_at FROM ledger l JOIN "
        "(SELECT unit_key, MAX(settlement_version) v FROM ledger GROUP BY unit_key) m ON m.unit_key = l.unit_key AND m.v = l.settlement_version")}
    src_rank = {"board": 2, "board_pp": 1, "member": 0}
    added = superseded = backfilled = 0
    for uk, rows in units.items():
        st = {r["status"] for r in rows}
        if not st & {"settled", "push", "void"}:
            continue
        acts = {r["actual"] for r in rows if r["status"] in ("settled", "push") and r.get("actual") is not None}
        if "open" in st and not acts:
            continue                                   # partly void, partly still open: wait
        p0 = rows[0]
        prop, scope = p0.get("prop"), p0.get("scope") or "12"
        starts = [s for s in (_ts(r.get("match_start")) for r in rows) if s]
        start = min(starts) if starts else None
        pids = {r["id"] for r in rows}
        snap = None
        if start:
            cut = _iso(start - timedelta(minutes=EVAL_LEAD_MIN))
            el = [f for f in fc.get(uk, []) if f["pick_id"] in pids and f["logged_at"] and f["logged_at"] <= cut]
            if el:
                snap = max(el, key=lambda f: (f["logged_at"], f["line_type"] in (None, "standard"), src_rank.get(f["source"], 0), -f["forecast_id"]))
        f = dict(con.execute("SELECT * FROM forecast_copy WHERE forecast_id = ?", (snap["forecast_id"],)).fetchone()) if snap else None
        feat = json.loads(f["features"] or "{}") if f else {}
        settled_rows = [r for r in rows if r["status"] in ("settled", "push") and r.get("actual") is not None]
        res_row = next((r for r in settled_rows if '"hltv"' in str(r.get("result") or "")), settled_rows[0] if settled_rows else None)
        try:
            res = json.loads(res_row.get("result") or "{}") if res_row else {}
        except ValueError:
            res = {}
        actual = next(iter(acts)) if len(acts) == 1 else None
        line = f["line"] if f else None
        p_over = p_over_of(f) if f else None
        bucket, reason = "scored", None
        if uk in bad_units:
            bucket, reason = "settlement_conflict", bad_units[uk]
        elif len(acts) > 1:
            bucket, reason = "settlement_conflict", f"lines in this unit settled with different totals {sorted(acts)}"
        elif not acts and st <= {"void"}:
            bucket, reason = "void", "void (forfeit / not played / never found)"
        elif not acts:
            bucket, reason = "incomplete", "settled without a total"
        elif f is None:
            bucket, reason = "incomplete", ("no forecast 30+ min before the start" if start else "no match start")
        elif not (0 < (line or 0) <= PLAUSIBLE_MAX.get(prop, {}).get(scope, 1e9)):
            bucket, reason = "market_mismatch", f"implausible line {line} (typo / wrong market)"
        elif feat.get("identity") == "unresolved" and projection_source(feat, f.get("lambda")).startswith("older"):
            bucket, reason = "unresolved_identity", "player not found in HLTV and no performance-model projection"
        elif actual == line:
            bucket, reason = "push", "the total landed on the evaluation line"
        elif p_over is None:
            bucket, reason = "incomplete", "forecast has no probability"
        lam = f.get("lambda") if f else None
        # UNIT OF OBSERVATION (owner review 2026-10-12): the unit (player x canonical match x stat x scope) is ONE row of the
        # primary scorecard (the evaluation forecast above). Every distinct line forecast in it is kept here with its own
        # outcome - nothing is discarded - and weighted 1/k (k = distinct lines of the unit) in the all-lines view, so a
        # unit with many lines cannot dominate; uncertainty is grouped by canonical match.
        lines = []
        if start and actual is not None:
            cut = _iso(start - timedelta(minutes=EVAL_LEAD_MIN))
            per = {}
            for fr_ in fc.get(uk, []):
                if fr_["pick_id"] in pids and fr_["logged_at"] and fr_["logged_at"] <= cut and fr_["line"] is not None:
                    k = (float(fr_["line"]), fr_["line_type"] or "standard")
                    if k not in per or fr_["logged_at"] > per[k]["logged_at"]:
                        per[k] = fr_
            for (ln, lt), fr_ in sorted(per.items()):
                po = p_over_of(dict(fr_))
                lines.append({"forecast_id": fr_["forecast_id"], "line": ln, "line_type": lt, "book": fr_["book"],
                              "p_over": po, "outcome": "push" if actual == ln else ("over" if actual > ln else "under"),
                              "logged_at": fr_["logged_at"]})
        fp = _sha([bucket, reason, f["forecast_id"] if f else None, actual, line, res.get("per_map"), res.get("rounds"), res.get("maps_used"),
                   [(x["forecast_id"], x["line"]) for x in lines]])
        prev = latest.get(uk)
        if prev and prev["fingerprint"] == fp:
            continue                                   # already processed: nothing changed (idempotent)
        fp_pre = _sha([bucket, reason, f["forecast_id"] if f else None, actual, line, res.get("per_map"), res.get("rounds"), res.get("maps_used")])
        if prev and prev["fingerprint"] == fp_pre:
            # the same settlement, recorded before the lines column existed (2026-10-12): fill the new column in place -
            # not a new settlement version, nothing else changes
            con.execute("UPDATE ledger SET lines = ?, fingerprint = ? WHERE rid = ?", (json.dumps(lines), fp, prev["rid"]))
            backfilled += 1
            continue
        over = int(actual > line) if bucket == "scored" else None
        diag = _diag(feat, res, prop, scope, lam, actual, line, rows) if bucket == "scored" else {}
        dims = {"stat": prop, "scope": scope, "env": feat.get("env") or "unknown", "history": history_band(feat),
                "version": (f or {}).get("model_version") or "untagged", "projection": projection_source(feat, lam),
                "source": (f or {}).get("source") or source_of(p0)} if f else {}
        ver = (prev["settlement_version"] + 1) if prev else 1
        change = None
        if prev:
            change = "correction (settlement_corrections.jsonl)" if pids & corr_ids else "settlement changed (status / total / detail)"
            superseded += 1
        settled_at = max((r.get("settled_at") or "" for r in rows), default="") or None
        con.execute("INSERT INTO ledger (unit_key, forecast_id, settlement_version, fingerprint, bucket, reason, match_slug, match_start, "
                    "forecast_logged_at, prop, scope, line, actual, over_hit, p_over, lam, dims, diag, pick_ids, settled_at, processed_at, "
                    "supersedes, change, lines) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uk, f["forecast_id"] if f else None, ver, fp, bucket, reason, match_of(p0), _iso(start),
                     f["logged_at"] if f else None, prop, scope, line, actual, over, p_over, lam, json.dumps(dims), json.dumps(diag),
                     json.dumps(sorted(pids)), _niso(settled_at), _iso(now), prev["rid"] if prev else None, change, json.dumps(lines)))
        added += 1
    return {"added": added, "superseded": superseded, "lines_backfilled": backfilled}


LATEST_SQL = ("SELECT l.* FROM ledger l JOIN (SELECT unit_key, MAX(settlement_version) v FROM ledger GROUP BY unit_key) m "
              "ON m.unit_key = l.unit_key AND m.v = l.settlement_version")


def latest_rows(con):
    return [dict(r) for r in con.execute(LATEST_SQL)]


def _unit(r):
    return {"match": r["match_slug"], "p": r["p_over"], "over": r["over_hit"], "lam": r["lam"], "actual": r["actual"],
            "dims": json.loads(r["dims"] or "{}"), "diag": json.loads(r["diag"] or "{}"), "start": r["match_start"],
            "forecast_id": r["forecast_id"], "logged": r["forecast_logged_at"], "prop": r["prop"], "scope": r["scope"],
            "rid": r["rid"], "unit": r["unit_key"], "line": r["line"]}


def scorecard(con):
    rows = latest_rows(con)
    sc = [_unit(r) for r in rows if r["bucket"] == "scored"]
    buckets = collections.Counter(r["bucket"] for r in rows)
    reasons = collections.Counter((r["bucket"], r["reason"]) for r in rows if r["bucket"] != "scored")
    out = {"overall": metrics(sc, slope=True), "calibration": calibration(sc), "directional": directional(sc), "by": {},
           "buckets": {b: buckets.get(b, 0) for b in BUCKETS},
           "bucket_matches": {b: len({r["match_slug"] for r in rows if r["bucket"] == b}) for b in BUCKETS},
           "bucket_reasons": [{"bucket": b, "reason": r, "units": n} for (b, r), n in reasons.most_common(30)],
           "ledger_rows": con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0], "units": len(rows),
           "superseded": con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] - len(rows)}
    for dim in ("stat", "scope", "env", "history", "version", "projection", "source"):
        g = collections.defaultdict(list)
        for u in sc:
            g[u["dims"].get(dim, "unknown")].append(u)
        out["by"][dim] = [dict(metrics(us), group=k) for k, us in sorted(g.items(), key=lambda kv: -len(kv[1]))]
        time.sleep(0)
    g = collections.defaultdict(list)
    for u in sc:
        g[(u["prop"], u["scope"])].append(u)
    out["by"]["stat x scope"] = [dict(metrics(us), group=f"{k[0]} · {k[1]}") for k, us in sorted(g.items(), key=lambda kv: -len(kv[1]))]
    out["diagnosis"] = diagnosis(sc)
    out["questions"] = two_questions(sc)
    out["all_lines"] = all_lines_view([r for r in rows if r["bucket"] == "scored"])
    return out


def two_questions(sc):
    """Owner review 2026-10-12 - two separate questions, never merged:
    Q1 do our probabilities beat a constant 50% (log-loss vs 0.5, conditional on no push)?
    Q2 do our projected totals predict the outcome better than the offered line (|projection - actual| vs |line - actual|)?
    Paired per unit, intervals resample canonical matches. A checkpoint does not guarantee either is resolved."""
    out = {}
    for name, us in (("all", sc), ("kills", [u for u in sc if u["prop"] == "kills"]), ("hs", [u for u in sc if u["prop"] == "hs"])):
        q1 = [u for u in us if u["p"] is not None and u["over"] in (0, 1)]
        g1 = lambda u: _ll(0.5, u["over"]) - _ll(u["p"], u["over"])
        q2 = [u for u in us if u["lam"] is not None and u["actual"] is not None and u.get("line") is not None]
        g2 = lambda u: abs(u["line"] - u["actual"]) - abs(u["lam"] - u["actual"])
        out[name] = {
            "q1_vs_50": {"units": len(q1), "matches": len({u["match"] for u in q1}),
                         "log_loss_gain_vs_50": _r(sum(g1(u) for u in q1) / len(q1), 5) if q1 else None,
                         "ci": _ci_ratio(_groups(q1, g1)) if q1 else None,
                         "note": "positive = our probabilities beat a constant 50%"},
            "q2_projection_vs_line": {"units": len(q2), "matches": len({u["match"] for u in q2}),
                                      "abs_error_gain_vs_line": _r(sum(g2(u) for u in q2) / len(q2), 3) if q2 else None,
                                      "ci": _ci_ratio(_groups(q2, g2)) if q2 else None,
                                      "note": "positive = our projection was closer to the actual total than the line"}}
    return out


def all_lines_view(rows):
    """Every distinct line forecast with its own outcome, each weighted 1/k within its unit (k = distinct lines), pushes
    out (conditional chances). Uncertainty grouped by canonical match."""
    items = []
    for r in rows:
        ls = [x for x in json.loads(r.get("lines") or "[]") if x.get("p_over") is not None and x["outcome"] != "push"]
        for x in ls:
            items.append({"match": r["match_slug"], "w": 1.0 / len(ls), "p": x["p_over"], "y": int(x["outcome"] == "over")})
    if not items:
        return {"lines": 0, "units": 0, "matches": 0}
    by = collections.defaultdict(lambda: [0.0, 0.0])
    for i in items:
        by[i["match"]][0] += i["w"] * _ll(i["p"], i["y"])
        by[i["match"]][1] += i["w"]
    W = sum(i["w"] for i in items)
    return {"lines": len(items), "units": round(W), "matches": len(by),
            "weighted_log_loss": _r(sum(i["w"] * _ll(i["p"], i["y"]) for i in items) / W),
            "weighted_log_loss_ci": _ci_ratio(list(by.values())),
            "weighted_brier": _r(sum(i["w"] * (i["p"] - i["y"]) ** 2 for i in items) / W),
            "policy": "primary scorecard = one evaluation forecast per unit; this view keeps every distinct line, weight 1/k per unit"}


def diagnosis(sc):
    """Aggregate error diagnosis (reported, never applied as a bonus / penalty)."""
    out = {"note": "Aggregates only. A single miss is not a cause: a correctly estimated 60% line loses 40% of the time.",
           "components": [], "rates": [], "flags": [], "market": {}, "spread": {}}
    for stat in ("kills", "hs"):
        us = [u for u in sc if u["prop"] == stat and u["diag"].get("parts")]
        if us:
            row = {"stat": stat, "units": len(us), "matches": len({u['match'] for u in us}),
                   "total_bias": _r(sum(u["lam"] - u["actual"] for u in us) / len(us), 3)}
            for part in ("rounds", "kill_rate", "hs_rate", "other"):
                vals = [u["diag"]["parts"].get(part) for u in us if u["diag"]["parts"].get(part) is not None]
                if vals:
                    row[part] = _r(sum(vals) / len(vals), 3)
                    row[part + "_abs"] = _r(sum(abs(v) for v in vals) / len(vals), 3)
                    row[part + "_ci"] = _ci_ratio(_groups([u for u in us if u["diag"]["parts"].get(part) is not None],
                                                          lambda u, part=part: u["diag"]["parts"][part]))
            out["components"].append(row)
    for name, pk, ak in (("Expected rounds (per evaluation unit)", "rounds_pred", "rounds_act"),
                         ("Kills per round (incl. opponent effect)", "rate_pred", "rate_act"),
                         ("Headshots per kill", "hsk_pred", "hsk_act")):
        us = [u for u in sc if u["diag"].get(pk) is not None and u["diag"].get(ak) is not None]
        if us:
            out["rates"].append({"measure": name, "units": len(us), "matches": len({u['match'] for u in us}),
                                 "predicted": _r(sum(u["diag"][pk] for u in us) / len(us)), "actual": _r(sum(u["diag"][ak] for u in us) / len(us)),
                                 "bias": _r(sum(u["diag"][pk] - u["diag"][ak] for u in us) / len(us)),
                                 "bias_ci": _ci_ratio(_groups(us, lambda u: u["diag"][pk] - u["diag"][ak]))})
    fl = collections.defaultdict(list)
    for u in sc:
        for f in u["diag"].get("flags") or ["no flag"]:
            fl[f].append(u)
    for f, us in sorted(fl.items(), key=lambda kv: -len(kv[1])):
        errs = [u for u in us if u["lam"] is not None]
        out["flags"].append({"flag": f, "units": len(us), "matches": len({u['match'] for u in us}),
                             "mae": _r(sum(abs(u["lam"] - u["actual"]) for u in errs) / len(errs), 3) if errs else None,
                             "bias": _r(sum(u["lam"] - u["actual"] for u in errs) / len(errs), 3) if errs else None,
                             "log_loss": _r(sum(_ll(u["p"], u["over"]) for u in us) / len(us))})
    out["market"] = {"units_lines_differ_1plus": sum(bool(u["diag"].get("market")) for u in sc),
                     "units_board_line_without_market_id": sum(bool(u["diag"].get("market_no_id")) for u in sc),
                     "settled_from": dict(collections.Counter(u["diag"].get("settled_from") or "unknown" for u in sc))}
    pits = [u for u in sc if u["diag"].get("pit") is not None]
    if pits:
        hist = [0] * 10
        for u in pits:
            hist[min(int(u["diag"]["pit"] * 10), 9)] += 1
        m = sum(u["diag"]["pit"] for u in pits) / len(pits)
        out["spread"] = {"units": len(pits), "matches": len({u['match'] for u in pits}), "pit_hist": hist,
                         "pit_mean": _r(m), "pit_var": _r(sum((u["diag"]["pit"] - m) ** 2 for u in pits) / len(pits)),
                         "pit_var_ideal": round(1 / 12, 4),
                         "in50": _r(sum(u["diag"]["in50"] for u in pits) / len(pits)), "in50_ci": _ci_ratio(_groups(pits, lambda u: u["diag"]["in50"])),
                         "in80": _r(sum(u["diag"]["in80"] for u in pits) / len(pits)), "in80_ci": _ci_ratio(_groups(pits, lambda u: u["diag"]["in80"])),
                         "reading": "PIT mean > 0.5 = actuals above the forecast (projection low); variance > 1/12 = spread too narrow"}
    return out


# ------------------------------------------------------------------------------------------------ training + challengers
def _train_row(u, fc_row):
    feat = json.loads(fc_row["features"] or "{}")
    rec = recover(feat)
    if rec is None:
        return None
    pf = feat.get("perf") or {}
    try:                                                # leakage guard: history strictly before the match day
        if pf.get("newest_map_day") and _ts(u["start"]) and int(pf["newest_map_day"]) >= _ts(u["start"]).date().toordinal():
            return None
    except (TypeError, ValueError):
        return None
    d = u["diag"]
    return {"lid": u["rid"], "fid": u["forecast_id"], "match": u["match"], "start": u["start"], "prop": u["prop"],
            "scope": u["scope"], "line": fc_row["line"], "over": u["over"], "p_logged": u["p"], "rec": rec,
            "rounds": d.get("rounds_act"), "maps_n": d.get("maps_n")}


def build_snapshot(con, cutoff):
    """The reproducible training set at `cutoff`: scored units (latest settlement version) with match start < cutoff and
    processed <= cutoff, invertible perf-2 projections only. Saved with ledger row ids and the SHA-256 of the rows."""
    c = _iso(cutoff)
    rows = []
    for r in latest_rows(con):
        if r["bucket"] != "scored" or not r["match_start"] or r["match_start"] >= c or (r["processed_at"] or "") > c:
            continue
        if not r["forecast_logged_at"] or r["forecast_logged_at"] > _iso(_ts(r["match_start"]) - timedelta(minutes=EVAL_LEAD_MIN)):
            continue
        fc_row = con.execute("SELECT line, features FROM forecast_copy WHERE forecast_id = ?", (r["forecast_id"],)).fetchone()
        tr = _train_row(_unit(r), fc_row) if fc_row else None
        if tr:
            rows.append(tr)
    rows.sort(key=lambda x: (x["start"], x["lid"]))
    h = _sha(rows)
    sid = "snap-" + h[:12]
    con.execute("INSERT OR IGNORE INTO snapshots (id, created_at, cutoff, data_hash, n_rows, n_matches, ledger_rows, rows) VALUES (?,?,?,?,?,?,?,?)",
                (sid, c, c, h, len(rows), len({x["match"] for x in rows}), json.dumps([x["lid"] for x in rows]),
                 zlib.compress(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode(), 6)))
    return sid, h, rows


def load_snapshot(con, sid):
    r = con.execute("SELECT * FROM snapshots WHERE id = ?", (sid,)).fetchone()
    if not r:
        raise KeyError(sid)
    rows = json.loads(zlib.decompress(r["rows"]))
    if _sha(rows) != r["data_hash"]:
        raise ValueError(f"snapshot {sid}: rows do not match the saved hash")
    return dict(r), rows


def retrain_from_snapshot(sid, db=None):
    """Reproducibility check: the same snapshot gives the same challenger parameters (hash compared by the caller)."""
    con = connect(db)
    try:
        meta, rows = load_snapshot(con, sid)
        base = json.loads(con.execute("SELECT base_config FROM challengers WHERE snapshot_id = ?", (sid,)).fetchone()["base_config"]) \
            if con.execute("SELECT 1 FROM challengers WHERE snapshot_id = ?", (sid,)).fetchone() else dict(LIVE_CFG)
        res = refit(rows, base)
        return {"snapshot": sid, "data_hash": meta["data_hash"], "config": res["config"], "config_hash": _sha(res["config"]),
                "candidate": res["candidate"]}
    finally:
        con.close()


def _challenger_status(con, cid, status, reason, now):
    con.execute("UPDATE challengers SET status = ?, status_at = ?, status_reason = ? WHERE id = ?", (status, _iso(now), reason, cid))
    con.execute("INSERT INTO events (at, kind, detail) VALUES (?,?,?)", (_iso(now), "challenger", json.dumps({"id": cid, "status": status, "reason": reason})))


def _distinct_new_matches(con, since):
    return {r["match_slug"] for r in latest_rows(con) if r["bucket"] == "scored" and (since is None or (r["processed_at"] or "") > since)}


def training_step(con, now, force=False):
    """Train when the schedule says so (TRAIN_RULE). Returns what happened (or why not)."""
    if con.execute("SELECT 1 FROM challengers WHERE status = 'shadow' AND family = ?", (TRAIN_RULE["family"],)).fetchone() and not force:
        return {"ran": False, "why": "a challenger is in its forward test"}
    last = con.execute("SELECT * FROM trainings ORDER BY id DESC LIMIT 1").fetchone()
    since = last["cutoff"] if last else None
    new = _distinct_new_matches(con, since)
    if len(new) < N_NEW and not force:
        return {"ran": False, "why": f"{len(new)} of {N_NEW} new distinct settled matches since the last training"}
    sid, h, rows = build_snapshot(con, now)
    nm = len({r["match"] for r in rows})
    detail = {"rows": len(rows), "matches": nm, "new_matches": len(new)}
    # development data: forward results of earlier challengers that this training run now uses
    dev = []
    for ch in con.execute("SELECT id, frozen_at FROM challengers"):
        used = [r for r in rows if r["start"] > ch["frozen_at"]]
        if used:
            dev.append({"challenger": ch["id"], "units": len(used), "matches": len({r['match'] for r in used}),
                        "note": "its forward results are development data for this version"})
    detail["development_overlap"] = dev
    if len(rows) < TRAIN_RULE["min_rows"] or nm < TRAIN_RULE["min_matches"]:
        con.execute("INSERT INTO trainings (at, cutoff, snapshot_id, outcome, detail) VALUES (?,?,?,?,?)",
                    (_iso(now), _iso(now), sid, "insufficient data", json.dumps(detail)))
        return {"ran": True, "outcome": "insufficient data", **detail}
    base = dict(LIVE_CFG)
    res = refit(rows, base)
    detail.update(folds=res["folds"], trace=res["trace"], train_loss_live=res["train_loss_live"], train_loss_fit=res["train_loss_fit"])
    cid = None
    if res["candidate"]:
        n = con.execute("SELECT COUNT(*) FROM challengers").fetchone()[0] + 1
        ch_hash = _sha(res["config"])
        cid = f"ch-{n:03d}-{ch_hash[:8]}"
        prev = con.execute("SELECT id FROM challengers ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
        for old in con.execute("SELECT id FROM challengers WHERE status = 'shadow' AND family = ?", (TRAIN_RULE["family"],)).fetchall():   # only when forced
            _challenger_status(con, old["id"], "superseded", f"superseded by {cid}; its forward results so far are development data for {cid}", now)
        # FROZEN here (config + params + data hash), before any forward forecast exists: forward = logged after frozen_at
        con.execute("INSERT INTO challengers (id, family, created_at, frozen_at, snapshot_id, data_hash, training_cutoff, config, "
                    "config_hash, base_config, status, status_at, status_reason, revises, development) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, TRAIN_RULE["family"], _iso(now), _iso(now), sid, h, _iso(now), json.dumps(res["config"], sort_keys=True),
                     ch_hash, json.dumps(base, sort_keys=True), "shadow", _iso(now),
                     "frozen; forward test = forecasts logged after the freeze", prev["id"] if prev else None, json.dumps(dev)))
    con.execute("INSERT INTO trainings (at, cutoff, snapshot_id, outcome, challenger_id, detail) VALUES (?,?,?,?,?,?)",
                (_iso(now), _iso(now), sid, "challenger frozen" if cid else "no candidate (no time-ordered validation gain)", cid,
                 json.dumps(detail, default=str)))
    return {"ran": True, "outcome": "challenger frozen" if cid else "no candidate", "challenger": cid, **detail}


def shadow_step(con, now, limit=4000):
    """Live and challenger chances side by side for every forecast logged after each active challenger's freeze."""
    n = 0
    for ch in con.execute("SELECT * FROM challengers WHERE status IN ('shadow', 'eligible_for_review')").fetchall():
        cfg, base = json.loads(ch["config"]), json.loads(ch["base_config"])
        blend = ch["family"] == LINE_BLEND["family"]
        todo = con.execute("SELECT f.* FROM forecast_copy f WHERE f.logged_at > ? AND NOT EXISTS (SELECT 1 FROM shadow s "
                           "WHERE s.challenger_id = ? AND s.forecast_id = f.forecast_id) ORDER BY f.forecast_id LIMIT ?",
                           (ch["frozen_at"], ch["id"], limit)).fetchall()
        for i, f in enumerate(todo):
            feat = json.loads(f["features"] or "{}")
            pl = p_over_of(dict(f))
            if blend:                                     # the live chance itself is the baseline (no replay needed)
                pc = line_blend_p(pl, f["prop"], f["scope"], cfg)
                vals = (pc, pl, pl, None, f["lambda"], int(pc is not None and pl is not None and abs(pc - pl) > 1e-9),
                        None if pc is not None else "no live chance")
                con.execute("INSERT OR IGNORE INTO shadow (challenger_id, forecast_id, p_over_ch, p_over_base, p_over_logged, lam_ch, lam_base, "
                            "changed, computed_at, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (ch["id"], f["forecast_id"], vals[0], vals[1], vals[2], vals[3], vals[4], vals[5], _iso(now), vals[6]))
                n += 1
                continue
            rec = recover(feat) if projection_source(feat, f["lambda"]).startswith("perf") else None
            if rec is None or f["line"] is None:
                vals = (pl, pl, pl, f["lambda"], f["lambda"], 0, "unchanged: not a refittable performance-model forecast")
            else:
                lb, pb = apply_cfg(rec, base, f["prop"], f["line"])
                lc, pc = apply_cfg(rec, cfg, f["prop"], f["line"])
                vals = (pc, pb, pl, lc, lb, int(abs(pc - pb) > 1e-9), None)
            con.execute("INSERT OR IGNORE INTO shadow (challenger_id, forecast_id, p_over_ch, p_over_base, p_over_logged, lam_ch, lam_base, "
                        "changed, computed_at, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (ch["id"], f["forecast_id"], vals[0], vals[1], vals[2], vals[3], vals[4], vals[5], _iso(now), vals[6]))
            n += 1
            if i % 100 == 99:
                time.sleep(0)
    return n


def forward_units(con, ch):
    out = []
    for r in latest_rows(con):
        if r["bucket"] != "scored" or not r["forecast_logged_at"] or r["forecast_logged_at"] <= ch["frozen_at"] \
                or not r["match_start"] or r["match_start"] <= ch["frozen_at"]:
            continue
        s = con.execute("SELECT * FROM shadow WHERE challenger_id = ? AND forecast_id = ?", (ch["id"], r["forecast_id"])).fetchone()
        if s is None or s["p_over_ch"] is None:
            continue
        u = _unit(r)
        u.update(p_ch=s["p_over_ch"], p_base=s["p_over_base"], p_logged=s["p_over_logged"], changed=s["changed"])
        out.append(u)
    out.sort(key=lambda u: (u["start"], u["match"]))
    return out


def paired_stats(units, level=0.95, B=BOOT_B, slope=True):
    """Challenger vs the live model replayed on the same inputs (and vs the logged live chance)."""
    if not units:
        return {"units": 0, "matches": 0}
    out = {"units": len(units), "matches": len({u['match'] for u in units}), "changed_units": sum(u["changed"] for u in units),
           "log_loss_logged": _r(sum(_ll(u["p_logged"], u["over"]) for u in units) / len(units)),
           "log_loss_live_replay": _r(sum(_ll(u["p_base"], u["over"]) for u in units) / len(units)),
           "log_loss_challenger": _r(sum(_ll(u["p_ch"], u["over"]) for u in units) / len(units)),
           "replay_gap": _r(sum(abs(u["p_base"] - u["p_logged"]) for u in units) / len(units)),
           "ci_level": level}
    gl = lambda u: _ll(u["p_base"], u["over"]) - _ll(u["p_ch"], u["over"])
    out["gain"] = _r(sum(gl(u) for u in units) / len(units), 5)
    out["gain_ci"] = _ci_ratio(_groups(units, gl), B=B, level=level)
    out["gain_vs_logged"] = _r(sum(_ll(u["p_logged"], u["over"]) - _ll(u["p_ch"], u["over"]) for u in units) / len(units), 5)
    if slope:
        out["slope"], out["slope_ci"] = slope_ci([dict(u, p=u["p_ch"]) for u in units])
    sl = collections.defaultdict(list)
    for u in units:
        sl[(u["prop"], u["scope"])].append(u)
    out["slices"] = [{"slice": f"{k[0]} · {k[1]}", "units": len(us), "matches": len({u['match'] for u in us}),
                      "gain": _r(sum(gl(u) for u in us) / len(us), 5)} for k, us in sorted(sl.items())]
    return out


def review_checks(st):
    R = REVIEW_RULE
    lo, hi = R["slope_band"]
    checks = {
        "log-loss gain >= minimum": st.get("gain") is not None and st["gain"] >= R["min_gain"],
        "gain interval excludes 0": bool(st.get("gain_ci")) and st["gain_ci"][0] > 0,
        "calibration slope interval overlaps 0.8-1.2": bool(st.get("slope_ci")) and st["slope_ci"][0] <= hi and st["slope_ci"][1] >= lo,
        "calibration slope interval narrow enough to judge": bool(st.get("slope_ci")) and st["slope_ci"][1] - st["slope_ci"][0] <= R["slope_ci_max_width"],
        "no major stat x scope slice worse than tolerance": all(s["gain"] >= -R["slice_tol"] for s in st.get("slices", [])
                                                                if s["matches"] >= R["slice_min_matches"]),
        "live replay reproduces the logged chances": st.get("replay_gap") is not None and st["replay_gap"] <= R["replay_gap_max"],
    }
    return checks, all(checks.values())


def review_step(con, now):
    """Decisions ONLY at the fixed windows; each decision is recorded once and never recomputed. Never promotes."""
    out = []
    for ch in con.execute("SELECT * FROM challengers WHERE status = 'shadow'").fetchall():
        done = {r["win"] for r in con.execute("SELECT win FROM reviews WHERE challenger_id = ?", (ch["id"],))}
        nxt = next((w for w in REVIEW_RULE["windows"] if w not in done), None)
        if nxt is None:
            continue
        fu = forward_units(con, ch)
        order = []
        for u in fu:
            if u["match"] not in order:
                order.append(u["match"])
        if len(order) < nxt:
            continue
        keep = set(order[:nxt])
        cohort = [u for u in fu if u["match"] in keep]
        st = paired_stats(cohort, level=REVIEW_RULE["ci_level"])
        checks, passed = review_checks(st)
        st.update(checks=checks, window=nxt, cohort_from=cohort[0]["start"], cohort_to=cohort[-1]["start"])
        con.execute("INSERT INTO reviews (challenger_id, win, decided_at, passed, result) VALUES (?,?,?,?,?)",
                    (ch["id"], nxt, _iso(now), int(passed), json.dumps(st, default=str)))
        if passed:
            _challenger_status(con, ch["id"], "eligible_for_review", f"passed the {nxt}-match review - an admin may promote it", now)
        elif nxt == REVIEW_RULE["windows"][-1] or (st.get("gain") is not None and st["gain"] <= 0):
            _challenger_status(con, ch["id"], "retired", f"did not pass the {nxt}-match review", now)
        out.append({"challenger": ch["id"], "window": nxt, "passed": passed})
    return out


# ------------------------------------------------------------------------------------------------ registry (admin only)
def _ensure_baseline(con, now):
    if con.execute("SELECT 1 FROM registry").fetchone():
        return
    try:
        import picklog
        mv = picklog.MODEL_VERSION
    except Exception:
        mv = "unknown"
    cfg = dict(LIVE_CFG)
    try:
        import grader
        cfg["cap_kills"] = grader.CONFIG.get("p_cap_kills", cfg["cap_kills"])
    except Exception:
        pass
    con.execute("INSERT INTO registry (at, action, version_label, config, activation_at, reason, actor) VALUES (?,?,?,?,?,?,?)",
                (_iso(now), "baseline", f"live perf-2 (grader {mv})", json.dumps(cfg, sort_keys=True), _iso(now),
                 "the configuration live when learning started", "system"))


def current_registry(con):
    r = con.execute("SELECT * FROM registry ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def promote(challenger_id, reason, actor="admin", now=None, db=None, confirm=False):
    """Explicit admin action. Only a challenger that PASSED a fixed review ('eligible_for_review') can be promoted. Records
    training cutoff, evaluation period, configuration, activation time and reason as a new registry version. Does NOT
    switch live grading (a code release does - see MODEL_VERSIONS.md). confirm=False = dry run."""
    now = _now(now)
    if not str(reason or "").strip():
        raise ValueError("a written reason is required")
    con = connect(db)
    try:
        _ensure_baseline(con, now)
        ch = con.execute("SELECT * FROM challengers WHERE id = ?", (challenger_id,)).fetchone()
        if ch is None:
            raise KeyError(f"no challenger {challenger_id}")
        if ch["status"] != "eligible_for_review":
            raise PermissionError(f"{challenger_id} is '{ch['status']}' - only a challenger that passed a fixed review can be promoted")
        rv = con.execute("SELECT * FROM reviews WHERE challenger_id = ? AND passed = 1 ORDER BY win DESC LIMIT 1", (challenger_id,)).fetchone()
        if rv is None:
            raise PermissionError(f"{challenger_id} has no passed review on record")
        res = json.loads(rv["result"])
        cur = current_registry(con)
        n = con.execute("SELECT COUNT(*) FROM registry WHERE action = 'promote'").fetchone()[0] + 1
        row = {"action": "promote", "version_label": f"research-v{n} ({challenger_id})", "challenger_id": challenger_id,
               "config": ch["config"], "training_cutoff": ch["training_cutoff"], "eval_from": res.get("cohort_from"),
               "eval_to": res.get("cohort_to"), "eval_matches": rv["win"], "activation_at": _iso(now), "reason": reason,
               "actor": actor, "previous_id": cur["id"] if cur else None}
        if not confirm:
            return {"dry_run": True, "would_record": row}
        con.execute("INSERT INTO registry (at, action, version_label, challenger_id, config, training_cutoff, eval_from, eval_to, "
                    "eval_matches, activation_at, reason, actor, previous_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_iso(now), row["action"], row["version_label"], challenger_id, row["config"], row["training_cutoff"],
                     row["eval_from"], row["eval_to"], row["eval_matches"], row["activation_at"], reason, actor, row["previous_id"]))
        _challenger_status(con, challenger_id, "promoted", f"promoted by {actor}: {reason}", now)
        con.commit()
        return {"dry_run": False, "recorded": row, "note": "registry updated; live grading is unchanged until a code release"}
    finally:
        con.close()


def rollback(reason, actor="admin", now=None, db=None, confirm=False):
    """One-click rollback: append a registry row restoring the version active before the current one."""
    now = _now(now)
    if not str(reason or "").strip():
        raise ValueError("a written reason is required")
    con = connect(db)
    try:
        _ensure_baseline(con, now)
        cur = current_registry(con)
        if not cur or cur["previous_id"] is None:
            raise PermissionError("nothing to roll back to (the baseline is active)")
        tgt = dict(con.execute("SELECT * FROM registry WHERE id = ?", (cur["previous_id"],)).fetchone())
        row = {"action": "rollback", "version_label": tgt["version_label"], "challenger_id": tgt["challenger_id"],
               "config": tgt["config"], "training_cutoff": tgt["training_cutoff"], "eval_from": tgt["eval_from"],
               "eval_to": tgt["eval_to"], "eval_matches": tgt["eval_matches"], "activation_at": _iso(now), "reason": reason,
               "actor": actor, "previous_id": cur["id"], "restores_id": tgt["id"]}
        if not confirm:
            return {"dry_run": True, "would_record": row}
        con.execute("INSERT INTO registry (at, action, version_label, challenger_id, config, training_cutoff, eval_from, eval_to, "
                    "eval_matches, activation_at, reason, actor, previous_id, restores_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_iso(now), "rollback", row["version_label"], row["challenger_id"], row["config"], row["training_cutoff"],
                     row["eval_from"], row["eval_to"], row["eval_matches"], row["activation_at"], reason, actor, cur["id"], tgt["id"]))
        if cur.get("challenger_id") and cur["action"] == "promote":
            _challenger_status(con, cur["challenger_id"], "rolled_back", f"rolled back by {actor}: {reason}", now)
        con.commit()
        return {"dry_run": False, "recorded": row}
    finally:
        con.close()


def acknowledge(item, reason, now=None, db=None):
    """Admin: accept one settlement-audit item ('pick:<id>' or 'unit:<key>') so it stops pausing learning; the unit is kept
    out of scoring (settlement_conflict bucket)."""
    if not str(reason or "").strip() or not (str(item).startswith("pick:") or str(item).startswith("unit:")):
        raise ValueError("item 'pick:<id>' or 'unit:<key>' and a reason are required")
    con = connect(db)
    try:
        con.execute("INSERT OR REPLACE INTO acks (item, at, reason) VALUES (?,?,?)", (item, _iso(_now(now)), reason))
        con.commit()
    finally:
        con.close()
    return {"acknowledged": item}


# ------------------------------------------------------------------------------------------------ slips
def slips_step(con, picks, batches, now):
    """Slip-builder batches, evaluated on their own table: each leg's result recorded once it is known."""
    try:
        import slip_batches
        lr = slip_batches.leg_result_from_picks(picks)
    except Exception:
        return 0
    n = 0
    for b in batches:
        for si, s in enumerate(b.get("slips") or []):
            for li, l in enumerate(s.get("legs") or []):
                try:
                    res = lr(l)
                except Exception:
                    res = None
                if res is None:
                    continue
                n += con.execute("INSERT OR IGNORE INTO slip_legs (batch_id, slip_idx, leg_idx, kind, n, batch_created, match_slug, p, side, "
                                 "line, result, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (b.get("batch_id"), si, li, s.get("kind"), s.get("n"), b.get("created_at"), l.get("match_slug"),
                                  l.get("p"), l.get("side"), l.get("line"), res, _iso(now))).rowcount
    return n


def slips_scorecard(con, batches):
    legs = [dict(r) for r in con.execute("SELECT * FROM slip_legs")]
    out = {"batches": len(batches), "kinds": []}
    by = collections.defaultdict(list)
    for l in legs:
        by[l["kind"]].append(l)
    for kind, ls in sorted(by.items()):
        dec = [l for l in ls if l["result"] in ("hit", "miss") and l["p"] is not None]
        units = [{"match": l["match_slug"], "p": l["p"], "over": int(l["result"] == "hit"), "lam": None, "actual": None} for l in dec]
        slips = collections.defaultdict(list)
        for l in ls:
            slips[(l["batch_id"], l["slip_idx"])].append(l)
        tot = {(b.get("batch_id"), i): len(s.get("legs") or []) for b in batches for i, s in enumerate(b.get("slips") or [])}
        settled = [v for k, v in slips.items() if tot.get(k) == len(v)]      # every leg has a result
        live = [[l for l in v if l["result"] in ("hit", "miss")] for v in settled]
        live = [v for v in live if len(v) >= 2]
        out["kinds"].append({"kind": kind, "legs_settled": len(dec), "matches": len({l['match_slug'] for l in dec}),
                             "leg_predicted": _r(sum(l["p"] for l in dec) / len(dec)) if dec else None,
                             "leg_hit_rate": _r(sum(l["result"] == "hit" for l in dec) / len(dec)) if dec else None,
                             "leg_hit_ci": _ci_ratio(_groups(units, lambda u: u["over"])) if dec else None,
                             "leg_log_loss": _r(sum(_ll(u["p"], u["over"]) for u in units) / len(units)) if units else None,
                             "slips_settled": len(live), "slips_all_hit": sum(all(l["result"] == "hit" for l in v) for v in live),
                             "slips_expected_all_hit": _r(sum(math.prod(l["p"] for l in v) for v in live), 2) if live else None})
    return out


# ------------------------------------------------------------------------------------------------ cycle + dashboard
def run_cycle(now=None, picks=None, forecasts=None, batches=None, corrections=None, db=None, picks_path=None,
              batches_path=None, corrections_path=None, train=True):
    """One learning cycle. Data may be injected (tests / local copies); by default it is read from the pick log."""
    now = _now(now)
    with _RUN_LOCK:
        STATE["running"] = True
        con = connect(db)
        try:
            _ensure_baseline(con, now)
            _ensure_line_blend(con, now)
            log = {"at": _iso(now)}
            # 1. copy new forecasts (immutable), in pages
            if picks is None:
                total = 0
                while True:
                    picks_, fr = read_picklog(picks_path, _meta(con, "last_forecast_id", 0))
                    tag_matches(picks_)
                    n, stalled = _copy_forecasts(con, picks_, fr, now)
                    total += n
                    con.commit()
                    if len(fr) < 3000 or stalled:
                        break
                picks = picks_
                log["forecasts_copied"] = total
            else:
                tag_matches(picks)
                last = _meta(con, "last_forecast_id", 0)
                log["forecasts_copied"] = _copy_forecasts(con, picks, sorted((f for f in forecasts or [] if f["id"] > last), key=lambda f: f["id"]), now)[0]
            if _migrate_unit_identity(con, picks, now):
                log["unit_identity_migrated"] = UNIT_IDENTITY
            corrections = read_corrections(corrections_path) if corrections is None else corrections
            batches = read_batches(batches_path) if batches is None else batches
            # 2. shadow forecasts (measurement only - continues while paused)
            log["shadow_added"] = shadow_step(con, now)
            con.commit()
            # 3. safety
            fl = faults(con, picks, corrections, now)
            was = _meta(con, "paused", None)
            if fl:
                if not was:
                    con.execute("INSERT INTO events (at, kind, detail) VALUES (?,?,?)", (_iso(now), "paused", json.dumps([f["code"] for f in fl])))
                _set_meta(con, "paused", {"since": (was or {}).get("since") or _iso(now), "faults": fl})
                log["paused"] = [f["code"] for f in fl]
            else:
                if was:
                    con.execute("INSERT INTO events (at, kind, detail) VALUES (?,?,?)", (_iso(now), "resumed", "{}"))
                _set_meta(con, "paused", None)
                acks = {r["item"]: r["reason"] for r in con.execute("SELECT item, reason FROM acks")}
                bad = {}
                for a in audit(picks):
                    if f"pick:{a['pick_id']}" in acks or f"unit:{a['unit_key']}" in acks:
                        bad[a["unit_key"]] = f"acknowledged audit difference: {a['issue']}"
                corr_ids = {c.get("id") for c in corrections if c.get("id") is not None}
                log["ingest"] = _ingest(con, picks, now, bad, corr_ids)
                con.commit()
                if log["ingest"]["added"] or _meta(con, "scorecard") is None:
                    _set_meta(con, "scorecard", scorecard(con))
                    _set_meta(con, "scorecard_at", _iso(now))
                    con.commit()
                if train:
                    log["training"] = training_step(con, now)
                    con.commit()
                    if log["training"].get("challenger"):
                        log["shadow_added"] += shadow_step(con, now)
                log["reviews"] = review_step(con, now)
                con.commit()
            log["slip_legs_added"] = slips_step(con, picks, batches, now)
            _set_meta(con, "slips", slips_scorecard(con, batches))
            _set_meta(con, "last_cycle", log)
            d = build_dashboard(con, now)
            _set_meta(con, "dashboard", d)
            con.commit()
            STATE.update(dashboard=d, dashboard_at=time.time(), last_cycle=log)
            return log
        finally:
            con.close()
            STATE["running"] = False


def build_dashboard(con, now=None):
    now = _now(now)
    try:
        import picklog
        live = picklog.MODEL_VERSION
    except Exception:
        live = "unknown"
    last = con.execute("SELECT * FROM ledger ORDER BY rid DESC LIMIT 1").fetchone()
    lt = con.execute("SELECT * FROM trainings ORDER BY id DESC LIMIT 1").fetchone()
    since = lt["cutoff"] if lt else None
    rows = latest_rows(con)
    scored = [r for r in rows if r["bucket"] == "scored"]
    new = {r["match_slug"] for r in scored if since is None or (r["processed_at"] or "") > since}
    active = con.execute("SELECT id FROM challengers WHERE status = 'shadow' AND family = ?", (TRAIN_RULE["family"],)).fetchone()
    chs = []
    for ch in con.execute("SELECT * FROM challengers ORDER BY created_at DESC LIMIT 20").fetchall():
        fu = forward_units(con, ch) if ch["status"] in ("shadow", "eligible_for_review") else []
        done = [dict(r, result=json.loads(r["result"])) for r in con.execute("SELECT * FROM reviews WHERE challenger_id = ? ORDER BY win", (ch["id"],))]
        nxt = next((w for w in REVIEW_RULE["windows"] if w not in {d["win"] for d in done}), None)
        nm = len({u["match"] for u in fu})
        early = [u for u in fu if (u["start"] or "") <= ch["frozen_at"] or (u["logged"] or "") <= ch["frozen_at"]]
        chs.append({"id": ch["id"], "family": ch["family"], "name": CHALLENGER_NAMES.get(ch["family"], ch["family"]),
                    "freeze_check": {"forward_units": len(fu), "units_started_or_logged_before_freeze": len(early),
                                     "earliest_forward_start": min((u["start"] for u in fu), default=None),
                                     "rule": "forward = forecast logged after the freeze AND match start after the freeze"},
                    "status": ch["status"], "status_reason": ch["status_reason"],
                    "frozen_at": ch["frozen_at"], "training_cutoff": ch["training_cutoff"], "snapshot": ch["snapshot_id"],
                    "data_hash": ch["data_hash"], "config": json.loads(ch["config"]), "base_config": json.loads(ch["base_config"]),
                    "revises": ch["revises"], "development": json.loads(ch["development"] or "[]"),
                    "shadow_forecasts": con.execute("SELECT COUNT(*) FROM shadow WHERE challenger_id = ?", (ch["id"],)).fetchone()[0],
                    "forward_interim": dict(paired_stats(fu, slope=False), label="interim - no decision is made between review windows") if fu else {"units": 0, "matches": 0},
                    "reviews": done,
                    "next_review": (f"at {nxt} distinct forward matches (now {nm})" if nxt and ch["status"] == "shadow" else None)})
    if active:
        nxt_train = f"after the forward test of {active['id']} ends, and {N_NEW} new distinct settled matches (now {len(new)})"
    else:
        nxt_train = f"when {N_NEW} new distinct settled matches exist since the last training (now {len(new)})"
    reg = [dict(r) for r in con.execute("SELECT * FROM registry ORDER BY id DESC LIMIT 30")]
    for r in reg:
        r["config"] = json.loads(r["config"] or "{}")
    paused = _meta(con, "paused", None)
    return {
        "generated": _iso(now), "version": VERSION, "live_model_version": live,
        "principle": "Every valid settled result is measured here; members' grades change only through a reviewed, explicitly "
                     "promoted improvement released in code. Nothing on this page changes a grade.",
        "status": {"paused": bool(paused), "since": (paused or {}).get("since"), "faults": (paused or {}).get("faults", []),
                   "last_cycle": _meta(con, "last_cycle"), "last_error": STATE.get("last_error")},
        "latest_settlement": ({"processed_at": last["processed_at"], "settled_at": last["settled_at"], "unit": last["unit_key"],
                               "match": last["match_slug"], "bucket": last["bucket"], "version": last["settlement_version"],
                               "change": last["change"]} if last else None),
        "matches": {"scored_distinct": len({r["match_slug"] for r in scored}), "scored_units": len(scored),
                    "new_since_training": len(new), "forecasts_copied": con.execute("SELECT COUNT(*) FROM forecast_copy").fetchone()[0]},
        "training": {"rule": TRAIN_RULE, "next": nxt_train,
                     "runs": [dict(dict(r), detail=json.loads(r["detail"] or "{}")) for r in con.execute("SELECT * FROM trainings ORDER BY id DESC LIMIT 10")]},
        "challengers": chs, "review_rule": REVIEW_RULE, "faults_rule": FAULTS,
        "registry": reg, "registry_note": "Registry of approved research configurations. Live grading runs the deployed code "
                                          f"(model version {live}); a promoted configuration reaches members only through a code release.",
        "scorecard": _meta(con, "scorecard"), "scorecard_at": _meta(con, "scorecard_at"),
        "slips": _meta(con, "slips"),
        "events": [dict(r) for r in con.execute("SELECT * FROM events ORDER BY id DESC LIMIT 20")],
    }


def refresh_dashboard(db=None):
    """Rebuild the stored dashboard now (after an admin action)."""
    con = connect(db)
    try:
        d = build_dashboard(con)
        _set_meta(con, "dashboard", d)
        con.commit()
    finally:
        con.close()
    STATE.update(dashboard=d, dashboard_at=time.time())
    return d


def dashboard(db=None, max_age=30):
    """The last stored dashboard (built by the learning cycle) - one small read, cached max_age s in memory."""
    if STATE.get("dashboard") is None or time.time() - (STATE.get("dashboard_at") or 0) > max_age:
        try:
            con = connect(db)
            try:
                d = _meta(con, "dashboard")
                if d is None:
                    d = build_dashboard(con)
            finally:
                con.close()
            STATE.update(dashboard=d, dashboard_at=time.time())
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
    out = dict(STATE["dashboard"])
    out["running"] = STATE.get("running")
    out["disabled"] = STATE.get("disabled")
    if STATE.get("last_error"):
        out["status"] = dict(out.get("status") or {}, last_error=STATE["last_error"])
    return out


CHILD_TIMEOUT_S = 1800


def spawn_cycle(timeout=CHILD_TIMEOUT_S):
    """One learning cycle in a SEPARATE, low-priority process: a refit is minutes of pure-Python CPU (about 220 s on
    4,000 units) and must never hold the server's GIL while members are grading (700-concurrent rule). The server thread
    only waits on the child (no CPU) and then reads the stored dashboard."""
    import shutil
    import subprocess
    import sys
    cmd = [sys.executable, str(Path(__file__).resolve()), "run", "--child"]
    if os.name == "posix" and shutil.which("nice"):
        cmd = ["nice", "-n", "15"] + cmd
    STATE["running"] = True
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(Path(__file__).resolve().parent))
        if p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout or "").strip()[-600:] or f"exit code {p.returncode}")
        STATE["last_error"] = None
    finally:
        STATE["running"] = False
        STATE["dashboard_at"] = 0                      # re-read the stored dashboard on the next request


def _cycle_safe():
    try:
        spawn_cycle()
    except Exception as e:
        STATE["last_error"] = f"{type(e).__name__}: {str(e)[:400]}"
        traceback.print_exc()


def forever(every=CYCLE_S, delay=START_DELAY_S):
    """Boot thread (server.py): spawns one learning cycle (child process) every `every` s. LEARNING=0 turns it off."""
    if os.environ.get("LEARNING", "1") == "0":
        STATE["disabled"] = True
        return
    time.sleep(delay)
    while True:
        _cycle_safe()
        time.sleep(every)


def http(path, q, web_dir):
    """Admin routes (server.py checks the admin token first). Returns (status, body, content type)."""
    if path == "/admin/learning":
        return 200, (Path(web_dir) / "learning.html").read_bytes(), "text/html; charset=utf-8"
    if path == "/admin/learning.json":
        if q.get("fresh") == "1" and not STATE.get("running"):
            threading.Thread(target=_cycle_safe, daemon=True, name="learning-now").start()
        return 200, dashboard(), "application/json"
    confirm = q.get("confirm") == "1"
    try:
        if path == "/admin/learning/promote":
            res = promote(q.get("challenger") or "", q.get("reason") or "", actor="admin", confirm=confirm)
        elif path == "/admin/learning/rollback":
            res = rollback(q.get("reason") or "", actor="admin", confirm=confirm)
        elif path == "/admin/learning/ack":
            res = acknowledge(q.get("item") or "", q.get("reason") or "")
        else:
            return 404, {"error": "not found"}, "application/json"
    except (ValueError, KeyError, PermissionError) as e:
        return 400, {"error": str(e)}, "application/json"
    if confirm or path.endswith("/ack"):
        try:
            refresh_dashboard()                       # the registry / acknowledgements show at once
        except Exception:
            STATE["dashboard_at"] = 0
    return 200, res, "application/json"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--picks"); r.add_argument("--db"); r.add_argument("--batches"); r.add_argument("--corrections")
    r.add_argument("--n-new", type=int)
    r.add_argument("--no-train", action="store_true")
    r.add_argument("--child", action="store_true", help="spawned by the server: default paths, one-line output")
    s = sub.add_parser("retrain"); s.add_argument("snapshot"); s.add_argument("--db")
    a = ap.parse_args(argv)
    global N_NEW
    if a.cmd == "run":
        if a.n_new:
            N_NEW = a.n_new
        log = run_cycle(db=a.db, picks_path=a.picks, batches_path=a.batches, corrections_path=a.corrections, train=not a.no_train)
        print(json.dumps(log, default=str) if a.child else json.dumps(log, indent=1, default=str))
    elif a.cmd == "retrain":
        print(json.dumps(retrain_from_snapshot(a.snapshot, a.db), indent=1, default=str))


if __name__ == "__main__":
    main()
