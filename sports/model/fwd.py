"""Forward forecasts, INFERENCE ONLY (owner 2026-10-24: "generate forward forecasts without enabling unnecessary
training"). Flag SPORTS_FORECAST=1 - separate from SPORTS_TRAIN, which stays 0 on scout-sports-api.

  SPORTS_FORECAST=1 python -m sports.model.fwd run [nba nfl]   verify the frozen artifacts, forecast the current board,
                                                              write the bounded model snapshot + the run record
  python -m sports.model.fwd verify                           hash-check the frozen artifact package (no flag needed)
  python -m sports.model.fwd build-history <history.db>       (workstation only) rebuild the packaged history copy

What runs: forward.forecast_league (sm-1 + mi-1 + mc-1 + single-source market forecasts) with the COMMITTED, frozen
artifacts and pipeline.write_snapshot, after the DATA refreshes of the installed history (refresh_nba: finished NBA
games; refresh_nfl: the current NFL season's nflverse files, at most every 12 h). What never runs here: forward.fit, learning.retrain, study, dataset crawls,
the lab - nothing in this module imports or calls them. Settlement + scorecard keep running in the settle job
(worker settle -> learning.on_settled, which never retrains).

Frozen artifact package (sports/model/artifacts/MANIFEST.json, every file sha256-verified before each run (JSON as LF text); a mismatch
refuses the run and writes nothing):
  sm1_nba.json  sm-1 NBA artifact, internal sha 5ec8adf73a6dad18     sm1_nfl.json  sm-1 NFL artifact, cc56aeb8b88c31d8
  market_mc1.json  mc-1 / mi-1 frozen market config (8119ce6b61539999)  model_config_*.json, registry.json
  history_ds1_2026-10-23.db.gz  the ds-1 history the artifacts were fit on (the training copy minus the raw ESPN
      summary cache and the per-snap staging table, which no feature reads), installed read-only to
      <SPORTS_DATA_DIR>/model/fwd/history.db (its own path - never the training history.db) and verified by sha256
      of the decompressed file. Features are built from it point-in-time, exactly as the model agent's forward run.

Memory: one feature frame at a time (forward._ordered), the board read is the store's current window, the snapshot
reads only forecasts of not-started events for CURRENT offer revisions (pipeline.write_snapshot), so the peak does
not grow with stored-forecast history. Scheduler job `model-forward` (exclusive, cap sports/scheduler.py).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from .. import config, flags

FLAG = "SPORTS_FORECAST"
ART_DIR = Path(__file__).resolve().parent / "artifacts"
MANIFEST = ART_DIR / "MANIFEST.json"
_ON = {"1", "true", "yes", "on"}
CRLF, LF = bytes((13, 10)), bytes((10,))
# tables of the training history.db that the packaged copy leaves EMPTY: raw = zlib ESPN summaries kept only to re-parse
# without refetching; nfl_snaps = staging table already merged into nfl_pg.offense_snaps / offense_pct at build
PACKAGE_EMPTIED = ("raw", "nfl_snaps")


class ArtifactError(RuntimeError):
    """A frozen artifact is missing or does not match its registered sha256 - the run is refused."""


def on():
    return os.environ.get(FLAG, "").strip().lower() in _ON


def require():
    if not on():
        raise flags.FlagOff(f"{FLAG} is off (set {FLAG}=1 to run forward forecasts)")


def _file_sha(p, chunk=1 << 20):
    """sha256 of a packaged file. JSON artifacts are hashed as LF text (git stores them with LF; a Windows checkout
    with core.autocrlf has CRLF in the working tree - same content, same hash); binaries byte for byte."""
    p = Path(p)
    if p.suffix == ".json":
        return hashlib.sha256(p.read_bytes().replace(CRLF, LF)).hexdigest()
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


def manifest():
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ArtifactError(f"artifact manifest unreadable: {e}") from e


def verify_artifacts(art_dir=None, man=None):
    """sha256 of every packaged file + the internal content shas of the sm-1 / mc-1 artifacts. -> summary dict."""
    from . import forward
    art_dir = Path(art_dir) if art_dir else ART_DIR
    man = man or manifest()
    bad = []
    for name, meta in sorted(man["files"].items()):
        p = art_dir / name
        if not p.exists():
            bad.append(f"{name}: missing")
            continue
        got = _file_sha(p)
        if got != meta["sha256"]:
            bad.append(f"{name}: sha256 {got[:16]} != registered {meta['sha256'][:16]}")
    if not bad:
        for sport, want in man["artifact_shas"].items():
            if sport == "mc-1":
                got = json.loads((art_dir / "market_mc1.json").read_text(encoding="utf-8")).get("sha")
            else:
                a = json.loads((art_dir / f"sm1_{sport}.json").read_text(encoding="utf-8"))
                got = forward._sha(a["markets"])[:16]
                if a.get("sha") != got:
                    bad.append(f"sm1_{sport}.json: stored sha {a.get('sha')} != content sha {got}")
            if got != want:
                bad.append(f"{sport}: artifact sha {got} != registered {want}")
    if bad:
        raise ArtifactError("frozen artifacts do not verify: " + "; ".join(bad))
    return {"manifest": man["version"], "files": len(man["files"]), "artifact_shas": dict(man["artifact_shas"])}


# ------------------------------------------------------------------------------------------------------- history
def history_path():
    return config.DATA_DIR / "model" / "fwd" / "history.db"


def install_history(man=None, art_dir=None, dest=None):
    """Install the packaged history copy once (streamed gunzip -> temp -> sha256 check -> atomic rename). An installed
    copy is re-checked by its marker (package sha); a copy whose marker does not match is replaced. -> state."""
    man = man or manifest()
    h = man["history"]
    src = (Path(art_dir) if art_dir else ART_DIR) / h["file"]
    dest = Path(dest) if dest else history_path()
    marker = dest.with_name(dest.name + ".package.json")
    try:
        mk = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        mk = None
    if dest.exists() and mk and mk.get("db_sha256") == h["db_sha256"]:
        # installed from this package (later NBA refreshes only APPEND finished games - refresh_nba)
        return {"state": "present", "path": str(dest), "db_sha256": h["db_sha256"]}
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
    hh = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out, gzip.open(src, "rb") as g:
            while True:
                b = g.read(1 << 20)
                if not b:
                    break
                hh.update(b)
                out.write(b)
        if hh.hexdigest() != h["db_sha256"]:
            raise ArtifactError(f"history package decompresses to sha256 {hh.hexdigest()[:16]} != registered "
                                f"{h['db_sha256'][:16]}")
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(str(dest) + suffix)
        except OSError:
            pass
    marker.write_text(json.dumps({"db_sha256": h["db_sha256"], "size": dest.stat().st_size,
                                  "package": h["file"]}), encoding="utf-8")
    return {"state": "installed", "path": str(dest), "db_sha256": h["db_sha256"]}


def open_history(path=None):
    """Read-only connection to the installed copy (the forecast never writes it)."""
    p = Path(path) if path else history_path()
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


REFRESH_LAG_H = 32          # an ESPN day is crawled once its last game is surely final (late ET tips end ~05:00Z)


def refresh_days(now_iso):
    """Days the NBA refresh may queue: every day of the current season up to (now - REFRESH_LAG_H). A day is crawled
    ONCE (crawl table), so it must never be read while its games are still in progress."""
    from datetime import date, datetime, timedelta
    t = datetime.fromisoformat(now_iso.replace("Z", "+00:00")) - timedelta(hours=REFRESH_LAG_H)
    last = t.date()
    out = []
    for season, (a, b) in datasets_seasons().items():
        d = date.fromisoformat(a)
        end = min(last, date.fromisoformat(b)) if b else last
        while d <= end:
            out.append(f"day:{d:%Y%m%d}")
            d += timedelta(days=1)
    return out


def datasets_seasons():
    from . import datasets
    return {s: v for s, v in datasets.NBA_SEASONS.items() if v[1] is None}     # the season in progress only


def _trim():
    """Hand freed heap back to the OS between phases (glibc malloc_trim; no-op elsewhere) so the refresh's parsed
    ESPN bodies and the previous league's frames do not stack on the next phase's peak."""
    import gc
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def refresh_nba(now_iso, budget_s=None, path=None):
    """DATA refresh, not training: append finished NBA games (ESPN box scores, the ds-1 crawl the history was built
    with; polite pacing in sports/net.py) to the installed copy so features include games after the package date.
    Bounded by a time budget (SPORTS_FORECAST_REFRESH_S, default 120 s); any failure keeps the existing data (the
    forecast still runs - a transient source failure never blocks or changes forecasts). Off: SPORTS_FORECAST_REFRESH=0.
    NFL: refresh_nfl."""
    if os.environ.get("SPORTS_FORECAST_REFRESH", "1").strip().lower() not in _ON:
        return {"state": "off"}
    from . import datasets
    budget_s = float(os.environ.get("SPORTS_FORECAST_REFRESH_S", "120")) if budget_s is None else budget_s
    p = Path(path) if path else history_path()
    try:
        con = datasets.connect(p)
        try:
            from ..contracts import now_utc
            n = 0
            for item in refresh_days(now_iso):
                n += con.execute("INSERT OR IGNORE INTO crawl (league, item, state, updated_at) VALUES ('nba', ?, "
                                 "'pending', ?)", (item, now_utc())).rowcount
            con.commit()
            res = datasets.nba_crawl(con, budget_s=budget_s)
            last = con.execute("SELECT MAX(start_utc) FROM games WHERE league='nba' AND status = 'final'").fetchone()
            res.update(state="ok", days_queued=n, last_nba_game=last[0] if last else None)
            return res
        finally:
            con.close()
    except Exception as e:                              # noqa: BLE001 - never blocks the forecast
        return {"state": "failed", "error": f"{type(e).__name__}: {str(e)[:200]}"}


NFL_REFRESH_EVERY_H = 12   # nflverse rebuilds its release files nightly; reloading more often re-reads the same files


def nfl_season(now_iso):
    """The NFL season in progress (or just finished): a season runs September - February."""
    from datetime import datetime
    t = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    return t.year if t.month >= 3 else t.year - 1


def refresh_nfl(now_iso, path=None, every_h=None, files=None):
    """DATA refresh, not training: reload the CURRENT NFL season from the nflverse release files the history was built
    from (schedule, player-week stats, snap counts, play-by-play: datasets.nfl_build for that one season) into the
    installed copy, so features include games after the package date. Earlier seasons are untouched. At most every
    NFL_REFRESH_EVERY_H (SPORTS_FORECAST_NFL_REFRESH_H); files are cached ~20 h and streamed from disk (measured 104 MB
    peak for five seasons on Linux). Every file is fetched BEFORE any table changes (datasets.nfl_prefetch), so a source
    failure keeps the existing data; the forecast still runs either way. Off: SPORTS_FORECAST_REFRESH=0.
    `files` (name -> text): tests."""
    if os.environ.get("SPORTS_FORECAST_REFRESH", "1").strip().lower() not in _ON:
        return {"state": "off"}
    from datetime import datetime
    from . import datasets
    every_h = float(os.environ.get("SPORTS_FORECAST_NFL_REFRESH_H", NFL_REFRESH_EVERY_H)) if every_h is None else every_h
    season = nfl_season(now_iso)
    if season not in datasets.NFL_SEASONS:
        return {"state": "skipped", "reason": f"season {season} is not a ds-1 NFL season"}
    p = Path(path) if path else history_path()
    try:
        con = datasets.connect(p)
        try:
            row = con.execute("SELECT v FROM meta WHERE k = 'nfl_refresh_at'").fetchone()
            if row:
                age_h = (datetime.fromisoformat(now_iso.replace("Z", "+00:00")) -
                         datetime.fromisoformat(row[0].replace("Z", "+00:00"))).total_seconds() / 3600
                if 0 <= age_h < every_h:
                    return {"state": "fresh", "season": season, "refreshed_at": row[0]}
            pre = datasets.nfl_prefetch(con, season) if files is None else "test files"
            res = datasets.nfl_build(con, seasons=(season,), files=files)
            res["files"] = pre
            con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('nfl_refresh_at', ?)", (now_iso,))
            con.commit()
            last = con.execute("SELECT MAX(start_utc) FROM games WHERE league='nfl' AND status = 'final'").fetchone()
            return dict(res, state="ok", season=season, last_nfl_game=last[0] if last else None)
        finally:
            con.close()
    except Exception as e:                              # noqa: BLE001 - never blocks the forecast
        return {"state": "failed", "season": season, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def build_history(src, out_gz):
    """Workstation only: the packaged copy = the training history.db with PACKAGE_EMPTIED tables emptied, VACUUMed,
    gzip level 9 with mtime 0 (same input + same SQLite -> same bytes). -> {db_sha256, gz_sha256, sizes}."""
    out_gz = Path(out_gz)
    tmpd = Path(tempfile.mkdtemp())
    try:
        full = tmpd / "full.db"
        con = sqlite3.connect(f"file:{Path(src).as_posix()}?mode=ro", uri=True)
        con.execute("VACUUM INTO ?", (str(full),))
        con.close()
        c2 = sqlite3.connect(full)
        for t in PACKAGE_EMPTIED:
            c2.execute(f"DELETE FROM {t}")
        c2.commit()
        c2.execute("PRAGMA journal_mode=DELETE")
        c2.execute("VACUUM")
        c2.close()
        with open(full, "rb") as f, open(out_gz, "wb") as raw, \
                gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as g:
            shutil.copyfileobj(f, g)
        return {"db_sha256": _file_sha(full), "db_bytes": full.stat().st_size, "gz_sha256": _file_sha(out_gz),
                "gz_bytes": out_gz.stat().st_size}
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


# ----------------------------------------------------------------------------------------------------------- run
def vm_cap_mb(env=None):
    """Address-space backstop (RLIMIT_AS): explicit SPORTS_FORECAST_MAX_VM_MB wins; default follows the plan. NOT the
    collector's SPORTS_WORKER_MAX_VM_MB (320 on Starter, sized for ingest): numpy + the feature frame map more address
    space than they touch (ingest: VmPeak ~60-70 MB above RSS). Address space is not memory; the real guard is the
    scheduler's RSS cap (SPORTS_JOB_MODEL_FORWARD_CAP_MB, 340), which kills the child before the container fills."""
    env = os.environ if env is None else env
    from .. import scheduler
    memory = int(env.get("SPORTS_MEMORY_MB") or scheduler.memory_mb())
    return int(env.get("SPORTS_FORECAST_MAX_VM_MB") or (1280 if memory >= 2048 else 640))


def run_record_path():
    return config.DATA_DIR / "model" / "api" / "forecast_run.json"


def last_run():
    try:
        return json.loads(run_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _peak_rss_mb():
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(r / 1024.0 if sys.platform != "darwin" else r / 1e6, 1)
    except ImportError:
        pass
    try:
        import psutil
        mi = psutil.Process().memory_info()
        return round(getattr(mi, "peak_wset", mi.rss) / 1e6, 1)
    except Exception:                                   # noqa: BLE001
        return None


def run(leagues=None, forecast_at=None, log=print):
    """Inference-only forward run. Refused without SPORTS_FORECAST or when an artifact does not verify."""
    require()
    from .. import store
    from ..contracts import now_utc
    from . import forward, pipeline
    t0 = time.time()
    started = now_utc()
    leagues = list(leagues or ["nba", "nfl"])
    ver = verify_artifacts()
    hist = install_history()
    try:                                                # sm-2 shadow inputs (REGISTER A5); never blocks sm-1
        from . import shadow
        if shadow.enabled():
            hist["sm2_extras"] = shadow.install_extras(history_path())
    except Exception as e:                              # noqa: BLE001
        hist["sm2_extras"] = {"state": f"failed: {type(e).__name__}"}
    if "nba" in leagues:
        hist["nba_refresh"] = refresh_nba(forecast_at or started)
        _trim()
    if "nfl" in leagues:
        hist["nfl_refresh"] = refresh_nfl(forecast_at or started)
        _trim()
    hcon = open_history()
    out = {"started_at": started, "forecast_at": forecast_at, "flag": FLAG, "artifacts": ver, "history": hist,
           "leagues": {}, "training": "not run (inference only: no fit, no learn, no retrain)"}
    try:
        with store.opened() as scon:
            before = scon.execute("SELECT COUNT(*) FROM forecast_keys").fetchone()[0]
            for lg in leagues:
                r = forward.forecast_league(scon, hcon, lg, forecast_at=forecast_at, log=log)
                out["leagues"][lg] = r
                _trim()
            after = scon.execute("SELECT COUNT(*) FROM forecast_keys").fetchone()[0]
        try:                                            # sm-2 locked test L2 (REGISTER A5 8): count, score once
            from . import shadow
            out["sm2_l2"] = {lg: shadow.l2_check(lg, hcon, forecast_at or started) for lg in leagues}
        except Exception as e:                          # noqa: BLE001 - never blocks sm-1
            out["sm2_l2"] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        if "nfl" in leagues:
            try:                                        # sm-nfl-2 held-out H2 (REGISTER A6 3): count only, bounded
                from . import nflv2
                out["nflv2_h2"] = nflv2.h2_check(hcon, forecast_at or started)
            except Exception as e:                      # noqa: BLE001 - never blocks sm-1
                out["nflv2_h2"] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
            try:                                        # sn-1 held-out (REGISTER A11 3): counts only, no interim look
                from . import sn1
                out["sn1_heldout"] = sn1.progress(hcon) if sn1.load() is not None else {"state": "no sn-1 artifact"}
            except Exception as e:                      # noqa: BLE001 - never blocks sm-1
                out["sn1_heldout"] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
    finally:
        hcon.close()
    out["forecasts_added"] = after - before
    graded = [lg for lg, r in out["leagues"].items() if r.get("graded_or_staked")]
    out["withheld_check"] = "ok: no grade letter, stake 0" if not graded else f"VIOLATION in {graded}"
    out["snapshot"] = pipeline.write_snapshot(now=forecast_at)
    out["finished_at"] = now_utc()
    out["ran_s"] = round(time.time() - t0, 1)
    out["peak_rss_mb"] = _peak_rss_mb()
    pipeline._atomic_write(run_record_path(), _summary(out))
    pipeline.publish_run(_summary(out))
    return out


def _summary(out):
    """The admin view of one run (also inside admin_learning.json: GET /api/sports/admin/model/learning)."""
    lg = {}
    for k, r in out["leagues"].items():
        lg[k] = {x: r.get(x) for x in ("forecast_at", "artifact", "upcoming_rows", "written", "unchanged", "skipped",
                                       "no_forecast", "injury_report", "error")}
        lg[k]["why_none"] = dict(list((r.get("why_none") or {}).items())[:20000])   # per offer key (UI reasons)
    return {"started_at": out["started_at"], "finished_at": out.get("finished_at"), "ran_s": out.get("ran_s"),
            "flag": FLAG, "artifacts": out["artifacts"], "history": out["history"],
            "forecasts_added": out.get("forecasts_added"), "leagues": lg, "withheld_check": out.get("withheld_check"),
            "snapshot": out.get("snapshot"), "peak_rss_mb": out.get("peak_rss_mb"), "training": out["training"],
            "sm2_shadow": {k: r.get("shadow") for k, r in out["leagues"].items()}, "sm2_l2": out.get("sm2_l2"),
            "nflv2_shadow": (out["leagues"].get("nfl") or {}).get("shadow_nflv2"), "nflv2_h2": out.get("nflv2_h2"),
            "sn1_shadow": (out["leagues"].get("nfl") or {}).get("shadow_sn1"), "sn1_heldout": out.get("sn1_heldout")}


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m sports.model.fwd")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run")
    s.add_argument("leagues", nargs="*")
    s.add_argument("--at", help="forecast time (UTC ISO) - measurement / replay on a store copy only")
    sub.add_parser("verify")
    s = sub.add_parser("build-history")
    s.add_argument("src")
    s.add_argument("--out", default=str(ART_DIR / "history_ds1_2026-10-23.db.gz"))
    a = p.parse_args(argv)
    if a.cmd == "verify":
        res = verify_artifacts()
    elif a.cmd == "build-history":
        res = build_history(a.src, a.out)
    else:
        require()                                       # refused before any memory cap / import of the model
        from ..worker import apply_memory_cap
        print(f"memory cap: {apply_memory_cap(vm_cap_mb())}", file=sys.stderr)
        res = _summary(run(a.leagues, forecast_at=a.at, log=lambda x: print(json.dumps(x, default=str), flush=True)))
    print(json.dumps(res, indent=1, default=str), flush=True)
    return res


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except flags.FlagOff as e:
        raise SystemExit(str(e))
    except ArtifactError as e:
        raise SystemExit(f"refused: {e}")
