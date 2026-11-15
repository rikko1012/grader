"""
The LoL data-center worker: ONE separate, low-priority process (never inside the web process - the 700-concurrent rule).
server.py only spawns it (python -m lol.center.worker) and restarts it with backoff; it never imports lol (the lol
package's isolation rule). The admin pages read the files this worker writes and the archive database read-only.

Inside the worker:
- markets thread: polls Underdog and PrizePicks LoL every LOL_ARCHIVE_EVERY s (default 120) into the quote archive
- riot thread: LoL Esports (no login) - next 7 days' schedule and last 3 days' results every 30 min, 12-month
  backfill in between (riot.py)
- ingest thread: Leaguepedia units one at a time through the shared pacer
- build thread: when any source changed, every 30 min: the canonical build and the coverage report; the offer ->
  series / player links every 10 min
- main thread: writes status.json / coverage.json / quarantine.json / links.json in DATA_DIR/lol/center every 60 s
  (atomic); exits when the parent server is gone
- malloc-trim thread: hands freed memory back to the OS every 60 s (glibc arenas capped at 2 before any thread starts);
  the worker is first in line for the kernel OOM killer (oom_score_adj 900), never the web process

- once per UTC day (from 06:00, right after the settle step): the A16 1 lc-1 decision as a child process
  (`python -m lol.cal autofit`; log <LOL_DATA_DIR>/model/lol_cal_log.jsonl), skipped when nothing new settled

Switches (environment): LOL_CAL_AUTOFIT=0 no daily lc-1 run; LOL_ARCHIVE=0 no market polling; LOL_LP=0 no Leaguepedia; LOL_LE=0 no LoL Esports. (LOL_DATA=0, read by server.py,
starts nothing.) No member page reads any of this; member LoL pages are not released.
"""
import json
import os
import random
import sys
import threading
import time

from . import store

OUT = store.db_path().parent
BUILD_EVERY_S = 1800
LINK_EVERY_S = 600
HANDOFF_BATCH = int(os.environ.get("LOL_HANDOFF_BATCH", "5000"))   # inbox records imported per loop (about once a minute)
STATE = {"started_at": None, "markets": {}, "ingest": {}, "riot": {}, "build": None, "links": None, "forecasts": None,
         "settled": None, "errors": []}


# 2026-10-18 memory: the worker reached ~445 MB before any build and 532 MB during one, while the container stood at 2,100 of
# 2,147 MB. Which step? A sampler reads this process's resident memory every 0.5 s and credits it to every step running at
# that moment; a peak while no step runs is "unattributed" (e.g. the LoL Esports pass). Measurement only.
MEM = {"stages": {}, "active": {}, "unattributed": {"peak_mb": 0.0}}



# glibc malloc (Linux / Render): each of this worker's threads gets its own malloc arena, and the memory a heavy step frees
# stays in those arenas instead of going back to the OS, so the worker's resident memory ratchets up between restarts
# (2026-10-29/30, no deploy for 17 h: 134 -> ~430 MB). The web process has handled this since 2026-10-18
# (membudget.tune_allocator), but the worker is a separate process and inherits neither setting. Same treatment here:
# cap the arenas before any thread starts, hand freed memory back every minute. Changes no data and no computation.
ALLOC = {"arena_max": None, "trims": 0, "last_trim_freed_mb": None, "platform": None}


def _trim_once(libc, collect=False):
    """One trim: optionally a full gc pass (reference cycles), then malloc_trim(0); records what it gave back."""
    import gc
    before = _rss()
    if collect:
        ALLOC["gc_freed"] = gc.collect()
    libc.malloc_trim(0)
    after = _rss()
    ALLOC["trims"] += 1
    if before is not None and after is not None:
        ALLOC["last_trim_freed_mb"] = round(before - after, 1)


def _tune_allocator(arenas=2, trim_every=60):
    """Cap glibc's arenas and start the trim thread. False (and nothing changed) off Linux / glibc."""
    import ctypes
    try:
        libc = ctypes.CDLL("libc.so.6")
    except OSError:
        ALLOC["platform"] = "no glibc - not tuned"
        return False
    M_ARENA_MAX = -8
    ALLOC["arena_max"] = arenas if libc.mallopt(M_ARENA_MAX, arenas) == 1 else None
    ALLOC["platform"] = "glibc"

    def loop():
        n = 0
        while True:
            time.sleep(trim_every)
            n += 1
            try:
                _trim_once(libc, collect=n % 5 == 0)
            except Exception:                              # noqa: BLE001 - a trim never stops the worker
                pass
    threading.Thread(target=loop, daemon=True, name="lol-malloc-trim").start()
    return True


def _prefer_oom_kill(path="/proc/self/oom_score_adj", score=900):
    """First in line for the kernel's OOM killer, as the sports service's jobs are (sports/scheduler.py): if the
    container reaches its memory limit, the kernel ends this worker - server.py restarts it with backoff - instead of the
    web process members are on (2026-10-31 05:40 UTC: the container's anonymous memory peaked at 1,884 of 2,147 MB while
    the worker held 617 MB). An unprivileged process may raise its own score; children inherit it. No-op off Linux."""
    try:
        with open(path, "w") as f:
            f.write(str(score))
    except OSError:
        return False
    ALLOC["oom_score_adj"] = score
    return True


def _market_odds(team, opponent, start):
    """The match-winner price for a forecast record (lol/odds.py, Polymarket; cached 60 s per process)."""
    from .. import odds
    return odds.match_odds(team, opponent, start)

def _rss():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        return None
    return None


def _mem_sampler(every=0.5):
    while True:
        time.sleep(every)
        r = _rss()
        if r is None:
            return
        act = list(MEM["active"].values())
        for cur in act:
            cur["peak"] = max(cur["peak"], r)
        if not act and r > MEM["unattributed"]["peak_mb"]:
            MEM["unattributed"] = {"peak_mb": round(r, 1), "at": store.now_iso()}


class stage:
    """with stage("export"): ... - records the step's start / peak / end resident memory and its duration."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        r = _rss()
        MEM["active"][self.name] = {"peak": r or 0.0, "start": r, "t0": time.time()}
        return self

    def __exit__(self, *exc):
        cur = MEM["active"].pop(self.name, None)
        if cur is not None:
            r = _rss()
            peak = max(cur["peak"], r or 0.0)
            st = MEM["stages"].setdefault(self.name, {"runs": 0, "peak_mb": 0.0})
            st["runs"] += 1
            st["last"] = {"at": store.now_iso(), "seconds": round(time.time() - cur["t0"], 1),
                          "start_mb": round(cur["start"], 1) if cur["start"] is not None else None,
                          "peak_mb": round(peak, 1) if r is not None else None, "end_mb": round(r, 1) if r is not None else None}
            if r is not None and peak > st["peak_mb"]:
                st["peak_mb"], st["peak_at"] = round(peak, 1), store.now_iso()
        return False


def _err(where, e):
    STATE["errors"] = (STATE["errors"] + [{"at": store.now_iso(), "where": where, "error": f"{type(e).__name__}: {str(e)[:300]}"}])[-20:]


def _rollback(con):
    try:
        con.rollback()
    except Exception:
        pass


def _step(con, name, fn):
    """One build-loop stage: its result, or None after recording the error (the loop goes on to the next stage)."""
    try:
        with stage(name):
            return fn()
    except Exception as e:
        _err(name, e)
        _rollback(con)
        return None


def _write(name, obj):
    tmp = OUT / (name + ".tmp")
    tmp.write_text(json.dumps(obj, default=str))
    os.replace(tmp, OUT / name)


AUTOFIT_TIMEOUT_S = 900
REPO = __import__("pathlib").Path(__file__).resolve().parents[2]


def lc1_autofit(con, now=None, run=None):
    """A16 1: once per UTC day (first cycle at or after cal.AUTOFIT_HOUR), right after settlement, the A14 lc-1 decision.
    Nothing new settled -> one skip line, no child. Otherwise `python -m lol.cal autofit` runs as a child process (numpy and the
    fit never enter this worker's memory), single-threaded, bounded by AUTOFIT_TIMEOUT_S. One attempt per day, success or not.
    -> summary dict, or None when not due / disabled."""
    import subprocess
    from datetime import datetime, timezone
    from .. import cal
    if not cal.autofit_enabled():
        return None
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    if now.hour < cal.AUTOFIT_HOUR or store.get_meta(con, "lc1_autofit_day") == today:
        return None
    store.set_meta(con, "lc1_autofit_day", today)
    con.commit()
    sig = cal.settle_signature(con)
    if cal.skip_if_unchanged(sig, today):
        return {"at": store.now_iso(), "skipped": True, "signature": sig}
    env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    run = run or subprocess.run
    t0 = time.time()
    r = run([sys.executable, "-m", "lol.cal", "autofit", "--db", str(store.db_path())], cwd=str(REPO), env=env,
            capture_output=True, text=True, timeout=AUTOFIT_TIMEOUT_S)
    out = {"at": store.now_iso(), "seconds": round(time.time() - t0, 1), "code": r.returncode}
    if r.returncode != 0:
        out["error"] = (r.stderr or "")[-400:]
        cal.append_log(cal.log_path(), [{"date": today, "at": out["at"], "market": "*", "action": "error",
                                         "why": f"autofit exit {r.returncode}: {(r.stderr or '').strip()[-200:]}"}])
        return out
    try:
        res = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return dict(out, error="no JSON from the child")
    out.update({k: res.get(k) for k in ("skipped", "rows", "sha", "on", "peak_rss_mb")})
    return out


def moneylines_loop():
    from .. import odds
    from . import winner_form
    while True:
        try:
            events, observed = odds.upcoming_events(with_time=True)
            doc = odds.series_winners(events, observed)
            if doc["games"]:
                try:
                    con = store.connect()
                    try:
                        winner_form.attach(con, doc)
                    finally:
                        con.close()
                except Exception as e:
                    _err("moneyline matchup history", e)
            _write("moneylines.json", doc)
        except Exception as e:
            _err("moneylines", e)
        time.sleep(300)


def markets_loop():
    from . import markets
    con = store.connect()
    while True:
        for platform in ("underdog", "prizepicks"):
            try:
                with stage(f"markets:{platform}"):
                    STATE["markets"][platform] = dict(markets.poll(con, platform), at=store.now_iso())
            except Exception as e:                 # a failure of one poll never stops the archive
                _err(f"markets:{platform}", e)
                try:
                    con.rollback()
                except Exception:
                    pass
        time.sleep(markets.EVERY_S + random.uniform(-10, 10))


def ingest_loop():
    """Leaguepedia units only (a rate-limited unit can wait many minutes - the build has its own thread)."""
    from . import ingest
    con = store.connect()
    while True:
        try:
            u = ingest.next_unit(con)
            if u is None:
                STATE["ingest"]["idle_since"] = STATE["ingest"].get("idle_since") or store.now_iso()
                time.sleep(60)
            else:
                STATE["ingest"].pop("idle_since", None)
                STATE["ingest"]["current"] = [u[0], u[1] if u[0] != "ref" else [u[1][0], len(u[1][1])]]
                with stage("ingest"):
                    ok, agg = ingest.run_unit(con, u)
                STATE["ingest"]["last"] = {"unit": STATE["ingest"].pop("current"), "ok": ok, "at": store.now_iso(),
                                           **{k: agg.get(k) for k in ("rows", "new", "changed", "gone", "error") if agg.get(k) is not None}}
                if not ok:
                    time.sleep(300)
        except Exception as e:
            _err("ingest", e)
            try:
                con.rollback()
            except Exception:
                pass
            time.sleep(300)


def _memory():
    """This worker's resident memory now and at its peak (MB), where the platform reports it."""
    out = {}
    try:
        import resource                                   # Linux (Render): ru_maxrss in KB
        out["peak_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:
        pass
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    out["rss_mb"] = round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return out


def code_fingerprint():
    """sha256 of the code that turns source rows into the canonical history (identity, build, export)."""
    import hashlib
    from pathlib import Path
    h = hashlib.sha256()
    for name in ("build.py", "export.py", "store.py", "names.py"):
        h.update((Path(__file__).with_name(name)).read_bytes())
    return h.hexdigest()[:16]


def mark_rebuild_on_code_change(con):
    """A deploy that changes the build / identity / export code rebuilds the canonical history from the stored source
    records at the next build window, even when no source row changed (repairs apply without waiting for new data)."""
    fp = code_fingerprint()
    if store.get_meta(con, "build_code") != fp:
        store.set_meta(con, "build_code", fp)
        store.set_meta(con, "dirty", store.now_iso())
        con.commit()
        return True
    return False


def build_loop():
    """When any source changed: the canonical build, the coverage report and the export to the one LoL history (at
    most every BUILD_EVERY_S, the first after 5 min). Every LINK_EVERY_S: offer -> series / player links, the pre-match
    forecasts, settlement of finished series, and the admin board."""
    from . import build, coverage, export, forecast, handoff, learning, match, slips
    con = store.connect()
    t_build, t_link = time.time() - BUILD_EVERY_S + 300, 0.0
    mark_rebuild_on_code_change(con)
    while True:
        try:
            try:
                with stage("handoff"):
                    STATE["handoff"] = handoff.consume(con, limit=HANDOFF_BATCH)
            except Exception as exc:
                con.rollback()
                # A remote inbox outage must not stop local builds or disclose a DSN.
                STATE["handoff"] = {"error": type(exc).__name__, "at": store.now_iso()}
            flag = store.db_path().parent / "rebuild.flag"       # admin request (/admin/lol/rebuild): build now
            if flag.exists():
                store.set_meta(con, "dirty", store.now_iso())
                con.commit()
                t_build = 0.0
                flag.unlink()
            if store.get_meta(con, "dirty") and time.time() - t_build > BUILD_EVERY_S:
                # audit 12: an attempt always advances the clock - a failing build is retried at the next build window,
                # never every minute - and the links / forecasts / board below still run on the last good build
                t_build, t_link = time.time(), 0.0
                try:
                    with stage("build"):
                        STATE["build"] = build.build(con)
                    with stage("coverage"):
                        _write("coverage.json", coverage.refresh(con))
                    with stage("export"):
                        STATE["export"] = export.export(con)
                except Exception as e:
                    _err("build", e)
                    _rollback(con)
            if time.time() - t_link > LINK_EVERY_S and store.get_meta(con, "built_at"):
                t_link = time.time()
                # each stage on its own: one failing stage never skips settlement, board.json, learning or slips
                _step(con, "links", lambda: STATE.__setitem__("links", {"at": store.now_iso(), "counts": match.link_open_markets(con)}))
                if store.get_meta(con, "last_export"):
                    _step(con, "forecasts", lambda: STATE.__setitem__(
                        "forecasts", {"at": store.now_iso(), "counts": forecast.run(con, market=_market_odds)}))
                    _step(con, "settle", lambda: STATE.__setitem__("settled", {"at": store.now_iso(), "counts": forecast.settle_all(con)}))
                    # A16 1: the daily lc-1 decision right after settlement (child process; skipped when nothing new settled)
                    lc1 = _step(con, "lc1_autofit", lambda: lc1_autofit(con))
                    if lc1 is not None:
                        STATE["lc1_autofit"] = lc1
                b = _step(con, "board", lambda: forecast.board(con))
                if b is not None:
                    try:
                        with stage("slips_member"):
                            b["slips_member"] = slips.member_summary(con)    # recommended slips for members, or why none
                    except Exception as e:                              # never blocks the board itself
                        _err("slips_member", e)
                        _rollback(con)
                    _step(con, "board_write", lambda: _write("board.json", b))
                _step(con, "learning", lambda: _write("learning.json", learning.dashboard(con)))
                _step(con, "slips_preview", lambda: _write("slips.json", {
                    "at": store.now_iso(), "note": "preview: no slip is recommended while stakes are held at 0U",
                    "previews": [slips.build(con, pl, size, 5, mode, no_overlap=True, preview=True)
                                 for pl in ("underdog", "prizepicks") for size in (2, 3) for mode in ("mixed",)]}))
        except Exception as e:
            _err("build", e)
            _rollback(con)
        time.sleep(60)


def refit_loop():
    """Weekly candidate refits in their own thread (never blocks builds): the first one a week after the start, and
    only once both backfills are complete. Recorded, never promoted automatically."""
    from . import learning, riot
    con = store.connect()
    if not store.get_meta(con, "last_refit_at"):
        store.set_meta(con, "last_refit_at", store.now_iso())         # first refit: a week from now
        con.commit()
    while True:
        time.sleep(3600)
        try:
            done_le = (STATE.get("riot") or {}).get("backfill", {}).get("done")
            from . import ingest
            prog = ingest.progress(con)["tables"]
            done_lp = all(v["windows_done"] == v["windows"] for k, v in prog.items() if k in ingest.CORE)
            if done_le and done_lp:
                with stage("refit"):
                    rec = learning.maybe_refit(con)
                if rec:
                    STATE["refit"] = rec
        except Exception as e:
            _err("refit", e)


def write_status():
    from . import cargo, ingest, markets
    con = store.connect()
    try:
        out = {"at": store.now_iso(), "pid": os.getpid(), "started_at": STATE["started_at"], "db": str(store.db_path()),
               "db_mb": round(store.db_path().stat().st_size / 1e6, 1) if store.db_path().exists() else None,
               "markets_live": STATE["markets"], "markets": markets.status(con), "ingest_live": STATE["ingest"], "riot": STATE["riot"],
               "ingest": ingest.progress(con), "pacer": cargo.PACER.view(),
               "leaguepedia_login": dict(cargo.LOGIN, configured=bool(cargo.credentials())),
               "last_build": store.get_meta(con, "last_build"), "links": STATE["links"],
               "last_export": store.get_meta(con, "last_export"), "forecasts": STATE.get("forecasts"), "settled": STATE.get("settled"),
               "lc1_autofit": STATE.get("lc1_autofit"),
               "history_handoff": STATE.get("handoff"), "last_history_import": store.get_meta(con, "last_handoff"),
               "quarantine": {r[0]: r[1] for r in con.execute(
                   "SELECT kind || ':' || reason, COUNT(*) FROM quarantine WHERE resolved_at IS NULL GROUP BY kind, reason")},
               "errors": STATE["errors"][-5:], "memory": dict(_memory(), stages=MEM["stages"], running=sorted(MEM["active"]),
                                                              unattributed=MEM["unattributed"], allocator=ALLOC)}
        quarantine = [dict(r) for r in con.execute("SELECT kind, skey, reason, detail, first_seen, last_seen FROM quarantine "
                                                   "WHERE resolved_at IS NULL ORDER BY last_seen DESC LIMIT 500")]
        links = [dict(r) for r in con.execute("SELECT l.*, e.player, e.team, e.opponent, e.start_time, e.stat, e.scope, e.line "
                                              "FROM market_links l JOIN market_state s ON s.platform = l.platform AND "
                                              "s.market_key = l.market_key JOIN market_events e ON e.id = s.last_event_id "
                                              "WHERE s.open = 1 ORDER BY e.start_time LIMIT 2000")]
    finally:
        con.close()
    _write("status.json", out)
    _write("quarantine.json", {"at": out["at"], "rows": quarantine})
    _write("links.json", {"at": out["at"], "rows": links})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    STATE["started_at"] = store.now_iso()
    parent = os.getppid()
    _tune_allocator()                                      # before any other thread: the arena cap applies to new arenas
    _prefer_oom_kill()
    threading.Thread(target=_mem_sampler, daemon=True, name="lol-mem").start()
    if os.environ.get("LOL_ARCHIVE", "1") != "0":
        threading.Thread(target=markets_loop, daemon=True, name="lol-markets").start()
        threading.Thread(target=moneylines_loop, daemon=True, name="lol-moneylines").start()
    if os.environ.get("LOL_LP", "1") != "0":
        threading.Thread(target=ingest_loop, daemon=True, name="lol-ingest").start()
    threading.Thread(target=build_loop, daemon=True, name="lol-build").start()
    threading.Thread(target=refit_loop, daemon=True, name="lol-refit").start()
    if os.environ.get("LOL_LE", "1") != "0":
        from . import riot
        threading.Thread(target=riot.loop, args=(STATE["riot"], stage), daemon=True, name="lol-riot").start()
    while True:
        try:
            write_status()
        except Exception as e:
            _err("status", e)
        time.sleep(60)
        if os.name == "posix" and os.getppid() != parent:     # the server is gone: stop too
            return 0


if __name__ == "__main__":
    sys.exit(main())
