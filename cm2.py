"""cm-2 - the CORRECTED complete-model shadow for Maps 1-2 KILLS only (accuracy handoff 2026-10-18,
claude-handoffs/accuracy-2026-10-18). A separately registered experiment beside cm-1, never a live input: no projection,
chance, letter, stake or member page reads it.

The artifact (cm2_model.json = the research's candidate_artifact.json, byte for byte): the 14 cm-1 features, refitted once with
fixed regularization (fit through 2026-09-30). No fitting, search or tuning happens here.

What differs from cm-1 (cm-1 itself is unchanged - its artifact, records, registration and replay stay as they are):
- the tables use only complete scoreboards: 10 rows, ten unique players, five per team, k / scores present, one consistent
  positive round count, nonnegative kills (the research rule, model_study.build) - in every table, ratings included. Source rows
  are not deleted: the excluded maps are counted by reason in the tables' meta;
- an unknown historical environment stays unknown (NaN): it counts in all-environment history, never in the LAN-only or
  online-only history (cm-1's '1.0 if lan else 0.0' made it online, and its database read turned NULL into False first);
- a forecast is ineligible, with its reason saved, for: another stat or scope, an unknown target environment, a player / team /
  opponent not identified in HLTV, a line that is not a whole or half number, no tables yet, or no player maps in the window.
  It never borrows a legacy or cm-1 value under its own name.

The probability (candidate_runtime.forecast, the research's exact inference reference): mean = exp(intercept + sum of
standardised features x weights); sd = spread_c x sqrt(max(mean, 1)); whole and half lines have separate thresholds. Saved
separately: raw Over, Under and push; Over given no push (uncapped); p_more = that conditional chance inside the old kills cap
22-78% - a conditional chance, not an outright win probability. No Over margin or legacy adjustment is applied.

Tables are built in the learning child process (refresh_tables, every REFRESH_S) and saved as plain .npy files that the server
memory-maps read-only (pages the OS can drop - no second resident copy of the history in the server)."""
import hashlib
import json
import math
import os
import shutil
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import cm1

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or HERE)
CACHE = DATA_DIR / ".cache"
POINTER = CACHE / "cm2_tables.json"
REFRESH_S = 2 * 3600
ARTIFACT_PATH = HERE / "cm2_model.json"
ARTIFACT = json.loads(ARTIFACT_PATH.read_bytes())
# identity = the canonical JSON (sorted keys, no spaces), independent of line endings: git stores the file with LF, the
# supplied research file has CRLF (its raw-byte sha256 3997cfce...11b1 is provenance only, CM2 in learning.py)
ARTIFACT_CANONICAL_SHA = hashlib.sha256(json.dumps(ARTIFACT, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
ARTIFACT_SHA = ARTIFACT_CANONICAL_SHA[:16]
# Records written before 2026-10-18 22:30 UTC carry this canonical hash under the misleading name "artifact_file_sha"
# (it never was a raw-file-byte hash); later records use "artifact_canonical_sha". replay accepts either name and checks
# the value against the canonical hash only.
ARTIFACT_FILE_SHA = ARTIFACT_CANONICAL_SHA
CONSTANTS_SHA = hashlib.sha256(json.dumps({"fit": ARTIFACT["fit"], "feature_names": ARTIFACT["feature_names"],
                                           "feature_constants": ARTIFACT["feature_constants"]},
                                          sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
VERSION = "cm-2"
FIT = ARTIFACT["fit"]
CAP = (0.22, 0.78)
HISTORY_RULE = ("HLTV database maps since 2025-09-01, complete scoreboards only (ten unique players, five per team, present "
                "nonnegative kills, one positive round count); unknown environment = overall history only; rows strictly before "
                "the match day; the latest weekly opponent refit on or before it; Elo at the start of that day")

# the features are cm-1's (cm1.inputs / cm1.features) - only valid if the frozen constants are the same numbers
if any(ARTIFACT["feature_constants"][k] != cm1.K.get(k) for k in ARTIFACT["feature_constants"]):
    raise RuntimeError("cm-2 feature constants differ from cm-1's - the weights would apply to different features")
if len(ARTIFACT["feature_names"]) != 14 or any(len(FIT[k]) != 14 for k in ("coef", "mean", "std")) or any(s <= 0 for s in FIT["std"]):
    raise RuntimeError("cm-2 artifact shape is invalid")


def env(v):
    """The corrected environment rule: unknown stays unknown (NaN never equals the LAN or the online flag)."""
    return float("nan") if v is None else (1.0 if v else 0.0)


# ------------------------------------------------------------------------------------------------ tables
def clean_rows(rows):
    """Rows of complete scoreboards only (research rule, checked in this order) and the excluded maps by reason. Map order
    and row order are kept, so the tables are built exactly as in the research."""
    maps = {}
    for r in rows:
        maps.setdefault((r[0], r[3]), []).append(r)
    bad, clean = Counter(), []
    for rs in maps.values():
        if len(rs) != 10:
            bad["not_ten_rows"] += 1
        elif len({r[4] for r in rs}) != 10:
            bad["duplicate_players"] += 1
        elif sorted(Counter(r[5] for r in rs).values()) != [5, 5]:
            bad["not_five_per_team"] += 1
        elif any(r[7] is None or r[9] is None or r[10] is None for r in rs):
            bad["missing_stats"] += 1
        elif len({r[9] + r[10] for r in rs}) != 1:
            bad["inconsistent_rounds"] += 1
        elif any(r[7] < 0 or r[9] + r[10] <= 0 for r in rs):
            bad["invalid_stats"] += 1
        else:
            clean.extend(rs)
    return clean, bad


def build_tables(rows, built_at=None, through=None):
    clean, bad = clean_rows(rows)
    t = cm1.build_tables(clean, built_at=built_at, through=through, env=env)
    t["meta"].update(version=VERSION, artifact_sha=ARTIFACT_SHA, rows_raw=len(rows), rows=len(clean), excluded_maps=dict(bad),
                     unknown_env_rows=sum(1 for r in clean if r[2] is None), history_rule=HISTORY_RULE)
    return t


def fetch_rows(today):
    """cm-1's dump query, month by month, keeping NULL environment as None (cm-1's read turns it into False)."""
    import hltv
    import psycopg2
    con = psycopg2.connect(hltv.dsn(), connect_timeout=20)
    con.set_session(readonly=True, autocommit=True)
    rows = []
    try:
        cur = con.cursor()
        cur.execute("SET statement_timeout = 300000")
        d0 = cm1.DATA_START
        while d0 <= today:
            d1 = min(date(d0.year + (d0.month == 12), d0.month % 12 + 1, 1) - timedelta(days=1), today)
            cur.execute(cm1.SQL, (d0, d1))
            rows += [(int(a), str(b), None if c is None else bool(c), int(d) if d is not None else None, int(e), int(f), int(g), h_, i_, j_, k_)
                     for a, b, c, d, e, f, g, h_, i_, j_, k_ in cur.fetchall()]
            d0 = d1 + timedelta(days=1)
    finally:
        con.close()
    return rows


def save_tables(t, cache=None):
    """A new directory of .npy files, then the pointer file replaced atomically; older directories (but the previous one) go."""
    cache = Path(cache or CACHE)
    cache.mkdir(parents=True, exist_ok=True)
    name = f"cm2_tables_{t['meta']['tables_sha']}_{int(time.time() * 1000)}"
    tmp = cache / (name + ".tmp")
    tmp.mkdir()
    try:
        for k, a in t["arrays"].items():
            np.save(tmp / f"{k}.npy", np.ascontiguousarray(a))
        os.replace(tmp, cache / name)
    except BaseException:                      # a failed / partial save: the pointer still names the last good tables
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    ptr = cache / POINTER.name
    prev = None
    try:
        prev = json.loads(ptr.read_text()).get("dir")
    except (OSError, ValueError):
        pass
    tp = ptr.with_suffix(".tmp")
    tp.write_text(json.dumps(dict(t["meta"], dir=name)))
    os.replace(tp, ptr)
    for d in cache.glob("cm2_tables_*"):
        if d.is_dir() and d.name not in (name, prev):
            shutil.rmtree(d, ignore_errors=True)
    return name


def refresh_tables(now=None, force=False, cache=None):
    """Learning child process: rebuild from the HLTV database when older than REFRESH_S. Never raises."""
    import hltv
    cache = Path(cache or CACHE)
    ptr = cache / POINTER.name
    try:
        if not hltv.dsn():
            return {"skipped": "HLTV database not configured"}
        if not force and ptr.exists() and time.time() - ptr.stat().st_mtime < REFRESH_S:
            return {"skipped": "fresh"}
        today = (now or datetime.now(timezone.utc)).date()
        t = build_tables(fetch_rows(today), through=today)
        save_tables(t, cache)
        return {"built": t["meta"]}
    except Exception as e:      # noqa: BLE001 - shadow only: the cycle never fails on it
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


_T = {"tables": None, "mtime": 0.0, "checked": 0.0, "meta": None}
_LOCK = threading.Lock()


def _load(d):
    out = {}
    for f in d.glob("*.npy"):
        try:
            out[f.stem] = np.load(f, mmap_mode="r")
        except ValueError:                       # an empty array cannot be memory-mapped
            out[f.stem] = np.load(f)
    return out


def current(cache=None):
    """The latest tables (memory-mapped, read-only; the pointer is checked at most once a minute). None when absent."""
    cache = Path(cache or CACHE)
    ptr = cache / POINTER.name
    now = time.time()
    with _LOCK:
        if now - _T["checked"] < 60 and _T["tables"] is not None:
            return _T["tables"]
        _T["checked"] = now
        try:
            mt = ptr.stat().st_mtime
        except OSError:
            return _T["tables"]
        if _T["tables"] is not None and mt == _T["mtime"]:
            return _T["tables"]
    meta = json.loads(ptr.read_text())
    t = {"arrays": _load(cache / meta["dir"]), "meta": meta}
    with _LOCK:
        _T.update(tables=t, mtime=mt, meta=meta)
    return t


# ------------------------------------------------------------------------------------------------ one forecast (pure)
def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def projection(t, player_ids, team_id, opp_id, day, lan):
    """cm-2's Maps 1-2 kills projection for LIVE grading (owner 2026-10-20: promoted for kills Maps 1-2 ahead of the
    registered review). The same eligibility, inputs, features and arithmetic as the shadow record; {"ineligible": why}
    when cm-2 cannot forecast (the grade then stays on perf-2 and says why)."""
    if t is None:
        return {"ineligible": "cm-2 tables not built yet"}
    if lan is None:
        return {"ineligible": "target environment (LAN / online) unknown"}
    if not player_ids:
        return {"ineligible": "player not identified in HLTV"}
    if not team_id or not opp_id:
        return {"ineligible": "own team or opponent not identified in HLTV"}
    s = cm1.inputs(t, player_ids, team_id, opp_id, day, lan)
    if s.get("ineligible"):
        return s
    if not s["maps"]:
        return {"ineligible": "no player maps in the 420 days before the match day"}
    x = cm1.features(s)
    if not all(math.isfinite(v) for v in x):
        return {"ineligible": "a feature is not finite"}
    eta = FIT["intercept"] + sum(b * (v - m) / sd for b, v, m, sd in zip(FIT["coef"], x, FIT["mean"], FIT["std"]))
    lam = math.exp(eta)
    return {"lambda": lam, "sd": FIT["spread_c"] * math.sqrt(max(lam, 1.0)), "features": x, "inputs": s,
            "tables_sha": (t.get("meta") or {}).get("tables_sha"), "artifact_sha": ARTIFACT_SHA, "version": VERSION}


def forecast(features, line):
    """candidate_runtime.forecast (the research's inference reference), the same arithmetic in the same order."""
    if not math.isfinite(line) or line < 0 or line * 2 != int(line * 2):
        raise ValueError("Expected a nonnegative integer or half-point line")
    if len(features) != 14 or not all(math.isfinite(x) for x in features):
        raise ValueError("Expected fourteen finite, correctly ordered features")
    eta = FIT["intercept"] + sum(b * (x - m) / s for b, x, m, s in zip(FIT["coef"], features, FIT["mean"], FIT["std"]))
    lam = math.exp(eta)
    sd = FIT["spread_c"] * math.sqrt(max(lam, 1.0))
    whole = line == int(line)
    upper = line + 0.5 if whole else math.floor(line) + 0.5
    lower = line - 0.5 if whole else upper
    over = _phi((lam - upper) / sd)
    under = _phi((lower - lam) / sd)
    push = max(0.0, 1.0 - over - under) if whole else 0.0
    conditional = over / (over + under) if over + under else 0.5
    return {"lambda": lam, "sd": sd, "p_over_raw": over, "p_under_raw": under, "p_push_raw": push,
            "p_over_given_no_push": conditional, "p_more": min(CAP[1], max(CAP[0], conditional))}


OUTPUTS = ("lambda", "sd", "p_over_raw", "p_under_raw", "p_push_raw", "p_over_given_no_push", "p_more")
SEMANTICS = ("p_more = chance of Over given no push, inside the kills cap 22-78% (scored like the live p_more, on decided "
             "props); p_over_raw / p_under_raw / p_push_raw are the outright chances; p_over_given_no_push is uncapped")


def _line_ok(line):
    try:
        x = float(line)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x >= 0 and x * 2 == int(x * 2) else None


def for_forecast(t, player_ids, team_id, opp_id, day, lan, line, prop, scope, quote=None, identity=None):
    """The cm-2 record of one forecast (saved inside the pre-match fh-1 record, in the same write), or one with
    "ineligible": why. Pure apart from the clock."""
    base = {"version": VERSION, "artifact_sha": ARTIFACT_SHA, "artifact_canonical_sha": ARTIFACT_CANONICAL_SHA, "constants_sha": CONSTANTS_SHA,
            "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "prop": prop, "scope": scope or "12"}
    if prop != "kills" or (scope or "12") != "12":
        return dict(base, ineligible="cm-2 covers Maps 1-2 kills only")
    if t is None:
        return dict(base, ineligible="cm-2 tables not built yet")
    m = t["meta"]
    base["tables"] = {k: m.get(k) for k in ("built_at", "tables_sha", "rows", "rows_raw", "excluded_maps", "unknown_env_rows")}
    base["history"] = {"rule": HISTORY_RULE, "tables_sha": m.get("tables_sha"), "built_at": m.get("built_at"),
                       "note": "built from the database as stored at built_at; a match's date does not prove when its rows arrived"}
    if lan is None:
        return dict(base, ineligible="target environment (LAN / online) unknown")
    if not player_ids:
        return dict(base, ineligible="player not identified in HLTV")
    if not team_id or not opp_id:
        return dict(base, ineligible="own team or opponent not identified in HLTV")
    ln = _line_ok(line)
    if ln is None:
        return dict(base, ineligible="line is not a whole or half number")
    s = cm1.inputs(t, player_ids, team_id, opp_id, day, lan)
    if s.get("ineligible"):
        return dict(base, **s)
    if not s["maps"]:
        return dict(base, ineligible="no player maps in the 420 days before the match day")
    x = cm1.features(s)
    if not all(math.isfinite(v) for v in x):
        return dict(base, ineligible="a feature is not finite")
    out = dict(base, inputs=s, features=x, feature_names=ARTIFACT["feature_names"], line=ln, player_ids=[int(p) for p in player_ids],
               team_id=int(team_id), opp_id=int(opp_id), day=int(day), lan=bool(lan), semantics=SEMANTICS)
    out.update(forecast(x, ln))
    q, idn = quote or {}, identity or {}
    tq = ((q.get("anchors") or {}).get("target")) or {}
    out["offer"] = {"book": idn.get("book") or tq.get("book"), "source_id": tq.get("source_id"), "market_id": idn.get("market_id") or tq.get("market_id"),
                    "match": idn.get("match"), "start": idn.get("start"), "line": tq.get("line", ln),
                    "market_type": tq.get("market_type") or q.get("feed_odds_type"),
                    "over_offered": tq.get("over_offered", q.get("over_available")), "under_offered": tq.get("under_offered", q.get("under_available")),
                    "source_updated_at": tq.get("source_updated_at") or q.get("updated_at"),
                    "observed_at": tq.get("observed_at") or q.get("board_at")}
    return out


# replay tolerance (the handoff's reference: means 1e-10, probabilities 1e-12). On the platform that wrote the record the
# replay is bit-identical (production: 0 mismatches); another platform's libm can differ in the last bit of erf / exp
# (~1e-16 seen on Windows), which must not read as a changed forecast - while any real change (>= 1e-9) is caught.
TOL = {"lambda": 1e-10, "sd": 1e-10, "p_over_raw": 1e-12, "p_under_raw": 1e-12, "p_push_raw": 1e-12, "p_over_given_no_push": 1e-12,
       "p_more": 1e-12}
TOL_FEATURE = 1e-12


def _num(v):
    """A finite real number - never a bool, None, string, NaN or infinity."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _identity(rec):
    bad = []
    if rec.get("version") != VERSION:
        bad.append(f"version {rec.get('version')!r} != {VERSION!r}")
    if rec.get("artifact_sha") != ARTIFACT_SHA:
        bad.append(f"artifact {rec.get('artifact_sha')!r} != {ARTIFACT_SHA}")
    canon = rec.get("artifact_canonical_sha", rec.get("artifact_file_sha"))
    if canon != ARTIFACT_CANONICAL_SHA:
        bad.append(f"artifact canonical sha {canon!r} != {ARTIFACT_CANONICAL_SHA}")
    if rec.get("constants_sha") != CONSTANTS_SHA:
        bad.append(f"constants {rec.get('constants_sha')!r} != {CONSTANTS_SHA}")
    return bad


def replay_status(rec):
    """(status, detail) for one saved cm-2 record, from its own saved values only (no tables, no board):
    "replayed" - a computed forecast, recomputed within tolerance; "ineligible" - explicitly skipped with its reason (NOT a
    replayed forecast); "missing" - no cm-2 part; "error" - the part records an error; "mismatch" - anything malformed,
    non-finite, of the wrong identity, or recomputed differently."""
    if not rec:
        return "missing", ["no cm-2 part in the record"]
    if not isinstance(rec, dict):
        return "mismatch", ["cm-2 part is not an object"]
    if rec.get("error"):
        return "error", [str(rec["error"])[:160]]
    bad = _identity(rec)
    if rec.get("ineligible"):
        if not isinstance(rec["ineligible"], str):
            bad.append("ineligible reason is not text")
        if any(k in rec for k in OUTPUTS) or "features" in rec:
            bad.append("an ineligible record carries forecast values")
        return ("mismatch", bad) if bad else ("ineligible", [rec["ineligible"]])
    if rec.get("prop") != "kills" or rec.get("scope") != "12":
        bad.append(f"stat / scope {rec.get('prop')!r} / {rec.get('scope')!r} is not Maps 1-2 kills")
    ln, s, fs = rec.get("line"), rec.get("inputs"), rec.get("features")
    if not _num(ln) or _line_ok(ln) != ln:
        bad.append(f"line {ln!r} is not a whole or half number")
    if not isinstance(s, dict):
        bad.append("inputs missing")
    if not isinstance(fs, list) or len(fs) != 14 or not all(_num(v) for v in fs):
        bad.append("features missing, malformed or not finite")
    miss = [k for k in OUTPUTS if not _num(rec.get(k))]
    if miss:
        bad.append("outputs missing or not finite: " + ", ".join(miss))
    if bad:
        return "mismatch", bad
    try:
        x = cm1.features(s)
        if not all(_num(v) for v in x):
            return "mismatch", ["reconstructed features are not finite"]
        r = forecast(x, ln)
    except Exception as e:      # noqa: BLE001 - a replay that cannot run is a failed replay
        return "mismatch", [f"{type(e).__name__}: {str(e)[:120]}"]
    if not all(_num(r[k]) for k in OUTPUTS):
        return "mismatch", ["reconstructed outputs are not finite"]
    if any(abs(a - b) > TOL_FEATURE for a, b in zip(fs, x)):
        bad.append("features differ")
    bad += [f"{k}: stored {rec[k]!r} replay {r[k]!r}" for k in OUTPUTS if abs(rec[k] - r[k]) > TOL[k]]
    return ("mismatch", bad) if bad else ("replayed", [])


def replay(rec):
    """(ok, detail) - the check_record interface: ok for a replayed forecast or an explicitly ineligible record (nothing to
    reconstruct; counted apart by replay_status), never for a missing / malformed / mismatched one with a cm-2 part."""
    st, why = replay_status(rec)
    if st == "missing":
        return True, []
    return st in ("replayed", "ineligible", "error"), ([] if st in ("replayed", "ineligible", "error") else why)


STATS = {"computed": 0, "ineligible": 0, "error": 0}


def status():
    m = _T.get("meta")
    if m is None and POINTER.exists():
        try:
            m = json.loads(POINTER.read_text())
        except (OSError, ValueError):
            m = None
    return {"version": VERSION, "artifact_sha": ARTIFACT_SHA, "artifact_canonical_sha": ARTIFACT_CANONICAL_SHA, "constants_sha": CONSTANTS_SHA,
            "tables": m, "counts": dict(STATS)}
