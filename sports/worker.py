"""The sports worker: a separate process for ingestion, settlement and (later) training. Never the web process, never
at import time. Run it as its own service / cron:

  python -m sports.worker migrate                       create / upgrade <SPORTS_DATA_DIR>/canonical.db (additive)
  python -m sports.worker ingest nba nfl [--sources espn,pp,ud,kalshi,injuries,oddsapi,polymarket,books,games]
                                                        one cycle                                     (SPORTS_INGEST)
  python -m sports.worker loop nba nfl --every 10       ingest every N minutes + settle hourly       (SPORTS_INGEST)
  python -m sports.worker books nba nfl                 the books job alone, when a game is due      (SPORTS_INGEST)
  python -m sports.worker settle [nba nfl]              settle stored offers of final games           (SPORTS_INGEST)
  python -m sports.worker verify nba --date YYYY-MM-DD  Kalshi settled values vs ESPN box scores      (SPORTS_INGEST)
  python -m sports.worker forecast [nba nfl]            sharp-2 market-only research forecasts         (SPORTS_INGEST)
  python -m sports.worker coverage [--json]             coverage report from the store (read-only, no flag)
  python -m sports.worker jobs                          the job ledger
  python -m sports.worker train ...                     refused unless SPORTS_TRAIN=1 (model agents register jobs)

Bounds (environment, with defaults): SPORTS_WORKER_CONCURRENCY=2 parallel jobs; SPORTS_JOB_TIMEOUT_S=300 per job;
SPORTS_JOB_ATTEMPTS=3 with exponential backoff + jitter (net.backoff); SPORTS_WORKER_MAX_MB=768 RSS ceiling (checked
between loop cycles); SPORTS_WORKER_MAX_VM_MB (default: SPORTS_WORKER_MAX_MB) virtual address-space cap (POSIX RLIMIT_AS;
reported "unsupported" on Windows) - virtual size runs well above RSS (thread stacks, allocator arenas, mapped
libraries), so it is sized from VmPeak, never from the RSS budget; SPORTS_STORE_MAX_MB=1500 - ingestion
stops writing when everything under SPORTS_DATA_DIR (data_mb: canonical, books, games, cache, ...) reaches it. Per-host
request pacing is net.py's (ESPN 2/s, Kalshi 1/1.5 s, PrizePicks 1/2 s, Underdog 1/s).

Jobs are idempotent (job_key includes the source and a time bucket; writes are content-hashed) and resumable (a job
that died 'running' is retried from its saved cursor).

This is the ONE scheduled collector (the sports service's ingest-loop child). Each source is polled here once:
  espn / injuries / pp / ud / kalshi   ESPN events + rosters, the injury report, the pick'em boards, Kalshi props
  oddsapi      The Odds API through books/sources.oddsapi_ingest (credit ledger; events and players resolved)
  polymarket   books/sources.polymarket_ingest
  (Novig / Gemini display-only exchanges are NOT a source here: a step of the light books job, books/exchanges.run_due -
   inside ingest-<league> they doubled its cost on 2026-11-09)
  books        FanDuel / BetRivers own feeds (+ OddsPapi when its guard allows): books/worker.run_once, each game
               read stored as quote history AND as sc-1 offers (books/bridge.py) from the same fetch. Scheduled as
               its OWN light job (`books` command, scheduler "books", every minute on a game day; 2026-11-06) so the
               direct ladder (3 / 5 / 8 min inside 2 / 8 / 24 h) is kept; the ingest-<league> cycles leave it out
               unless SPORTS_BOOKS_JOB=0
  games        sports/games collect.tick: pre-game context (pickcenter, predictor, NWS weather), ESPN-supplied
               DraftKings prop lines, Kalshi game ladders, BPI / FPI, rosters / injuries through the same cached reads
               as above (no second request)
Settlement reads each final box score once and hands the same body to the game store (box score + play-by-play).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from . import adapters, cache, config, flags, identity, kalshi, lines, net, settlement, store
from .contracts import now_utc, parse_utc

CONCURRENCY = int(os.environ.get("SPORTS_WORKER_CONCURRENCY", "2"))
JOB_TIMEOUT_S = int(os.environ.get("SPORTS_JOB_TIMEOUT_S", "300"))
JOB_ATTEMPTS = int(os.environ.get("SPORTS_JOB_ATTEMPTS", "3"))
MAX_MB = int(os.environ.get("SPORTS_WORKER_MAX_MB", "768"))           # RSS ceiling (loop exits for a clean restart)


def vm_cap_mb(env=None):
    """Address-space cap (RLIMIT_AS) in MB: SPORTS_WORKER_MAX_VM_MB, else SPORTS_WORKER_MAX_MB (the former single knob,
    so an unset VM value changes nothing). Virtual size is not RSS (thread stacks, allocator arenas, mapped libraries):
    measured 2026-10-24 on real feeds under Starter-like limits (research/results/storage_2026-10-24/constrained.jsonl),
    the scheduler's ingest job peaks at RSS 178-194 MB but VmPeak 240-257 MB; at RLIMIT_AS 220 it died of MemoryError
    at RSS 131 MB without MALLOC_ARENA_MAX=2 and ran pinned at the cap (no headroom) with it."""
    env = os.environ if env is None else env
    return int(env.get("SPORTS_WORKER_MAX_VM_MB") or env.get("SPORTS_WORKER_MAX_MB") or 768)


MAX_VM_MB = vm_cap_mb()
STORE_MAX_MB = float(os.environ.get("SPORTS_STORE_MAX_MB", "1500"))
DISK_FREE_FLOOR_MB = float(os.environ.get("SPORTS_DISK_FREE_FLOOR_MB", "5000"))


def store_cap_mb(env_cap=None, base=None):
    """The data-directory cap ingestion pauses at. 2026-11-05: SPORTS_STORE_MAX_MB (4,000) was sized for the old 5 GB disk;
    on the 105 GB disk it stopped every NBA/NFL feed for ~14 h with 97 GB free. The cap is therefore at least half the
    disk the data lives on; the real guard is free space (DISK_FREE_FLOOR_MB). -> (cap_mb, free_mb or None)."""
    import shutil
    cap = STORE_MAX_MB if env_cap is None else env_cap
    try:
        du = shutil.disk_usage(str(base or config.DATA_DIR))
        return max(cap, 0.5 * du.total / 1e6), du.free / 1e6
    except OSError:
        return cap, None
SETTLE_LOOKBACK_DAYS = 4                 # box scores re-read for corrections this long after a game
SETTLE_FLOOR = os.environ.get("SPORTS_SETTLE_FLOOR", "2026-10-20T00:00:00Z")   # catch-up reaches back to here
SETTLE_CATCHUP_MAX = int(os.environ.get("SPORTS_SETTLE_CATCHUP_MAX", "40"))  # older unsettled games per run
WINDOW_DAYS = {"nba": 2, "wnba": 2, "cbb": 2, "nfl": 7, "cfb": 7}
SOURCES = ("espn", "pp", "ud", "kalshi", "injuries", "oddsapi", "polymarket", "books", "games")
# cmd_ingest runs leagues on parallel threads; the books job and the game tick share per-process leases and
# league-independent work (blocked-book probes, holds), so they run one league at a time, as one pass did before
_COLLECT_LOCK = threading.Lock()
ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports"


class BudgetExceeded(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------------ resources
def apply_memory_cap(mb=MAX_VM_MB):
    try:
        import resource
    except ImportError:
        return "unsupported (no resource module on this OS)"
    lim = int(mb) * 1024 * 1024
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (lim if hard == resource.RLIM_INFINITY else min(lim, hard), hard))
    return f"RLIMIT_AS {mb} MB"


def rss_mb():
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError, AttributeError):
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return None
        return pmc.WorkingSetSize / 1e6
    except Exception:                                   # noqa: BLE001 - measurement only
        return None


# ------------------------------------------------------------------------------------------------------ job ledger
class Jobs:
    """A resumable, idempotent job ledger in the canonical store (table jobs)."""

    def __init__(self, con, attempts=JOB_ATTEMPTS, timeout_s=JOB_TIMEOUT_S, sleep=time.sleep):
        self.con, self.attempts, self.timeout_s, self.sleep = con, attempts, timeout_s, sleep
        self.lock = threading.Lock()
        self._deferred = {}                              # key -> ledger fields a timed-out attempt could not write yet

    def _row(self, key):
        r = self.con.execute("SELECT * FROM jobs WHERE job_key = ?", (key,)).fetchone()
        return dict(r) if r else None

    def _set(self, key, **kw):
        # the store's write lock first (same order as write_tx callers): a ledger commit can never land inside another
        # thread's BEGIN IMMEDIATE batch on the shared connection (bug sweep 2026-11-06 #12)
        with store._WRITE_LOCK, self.lock:
            self._flush_deferred_locked()
            self._write_locked(key, kw)

    def _write_locked(self, key, kw):
        cols = ", ".join(f"{k} = ?" for k in kw)
        self.con.execute(f"UPDATE jobs SET {cols} WHERE job_key = ?", list(kw.values()) + [key])
        self.con.commit()

    def _flush_deferred_locked(self):
        while self._deferred:
            k, kw = self._deferred.popitem()
            self._write_locked(k, kw)

    LEDGER_WAIT_S = 2.0                                  # longest a timed-out run() waits for the write lock

    def _set_nowait(self, key, wait_s=None, **kw):
        """Record without blocking the caller for long (bug sweep #12 review): the timed-out job's thread may hold the
        store write lock inside a long write_tx. If the lock is not free within wait_s, the fields are kept in memory
        and written by the next ledger write (or flush()). -> True if written now."""
        if not store._WRITE_LOCK.acquire(timeout=self.LEDGER_WAIT_S if wait_s is None else wait_s):
            with self.lock:
                self._deferred[key] = dict(self._deferred.get(key, {}), **kw)
            return False
        try:
            with self.lock:
                self._flush_deferred_locked()
                self._write_locked(key, kw)
            return True
        finally:
            store._WRITE_LOCK.release()

    def flush(self, wait_s=0.0):
        """Write any deferred ledger rows if the store write lock is free within wait_s. -> rows still deferred."""
        if self._deferred and store._WRITE_LOCK.acquire(timeout=max(wait_s, 0.001)):
            try:
                with self.lock:
                    self._flush_deferred_locked()
            finally:
                store._WRITE_LOCK.release()
        return len(self._deferred)

    def cursor(self, key):
        r = self._row(key)
        return json.loads(r["cursor"]) if r and r.get("cursor") else None

    def save_cursor(self, key, cur):
        self._set(key, cursor=json.dumps(cur), heartbeat_at=now_utc())

    def run(self, key, job, fn):
        """Run fn(ctx) once for job_key. done -> skipped (idempotent). failed / dead 'running' -> retried (resumable:
        ctx.cursor holds the saved progress). -> {"state", "result"|"error", "attempts"}.
        A TIMED-OUT attempt is never retried in this process: its thread cannot be stopped and is still running on the
        shared connection, so a retry would run beside it (bug sweep 2026-11-06 #12). The job is recorded failed and a
        later run (a new process) resumes it from its cursor."""
        with store._WRITE_LOCK, self.lock:
            r = self._row(key)
            if r is None:
                self.con.execute("INSERT INTO jobs (job_key, job, state, attempts, created_at) VALUES (?,?,?,?,?)",
                                 (key, job, "pending", 0, now_utc()))
                self.con.commit()
                r = self._row(key)
        if r["state"] == "done":
            return {"state": "skipped", "result": json.loads(r["result"] or "null"), "attempts": r["attempts"]}
        attempts = r["attempts"]
        last_err = None
        while attempts < self.attempts:
            attempts += 1
            self._set(key, state="running", attempts=attempts, started_at=now_utc(), heartbeat_at=now_utc(), error=None)
            ctx = JobContext(self, key)
            ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"job-{job}")
            try:
                result = ex.submit(fn, ctx).result(timeout=self.timeout_s)
            except FutTimeout:
                last_err = f"timeout after {self.timeout_s}s"
                written = self._set_nowait(key, state="failed", error=last_err, finished_at=now_utc())
                out = {"state": "failed", "error": last_err, "attempts": attempts, "still_running": True}
                if not written:
                    out["ledger_deferred"] = True        # the job still holds the write lock; written on the next write
                return out
            except (BudgetExceeded, flags.FlagOff) as e:
                self._set(key, state="failed", error=str(e)[:500], finished_at=now_utc(), attempts=self.attempts)
                return {"state": "failed", "error": str(e), "attempts": attempts}
            except Exception as e:                      # noqa: BLE001 - recorded, retried with jitter
                last_err = f"{type(e).__name__}: {str(e)[:300]}"
            else:
                self._set(key, state="done", result=json.dumps(result, default=str)[:20000], finished_at=now_utc())
                return {"state": "done", "result": result, "attempts": attempts}
            finally:
                ex.shutdown(wait=False, cancel_futures=True)  # a hung source call never blocks the worker loop
            self._set(key, state="failed", error=last_err, finished_at=now_utc())
            if attempts < self.attempts:
                self.sleep(net.backoff(attempts - 1, base=2.0, cap=20.0))
        return {"state": "failed", "error": last_err, "attempts": attempts}

    def list(self, limit=50):
        return [dict(r) for r in self.con.execute("SELECT job_key, job, state, attempts, error, started_at, "
                                                  "finished_at FROM jobs ORDER BY created_at DESC LIMIT ?",
                                                  (limit,)).fetchall()]


class JobContext:
    def __init__(self, jobs, key):
        self.jobs, self.key = jobs, key
        self.cursor = jobs.cursor(key)

    def checkpoint(self, cur):
        self.cursor = cur
        self.jobs.save_cursor(self.key, cur)


def bucket(minutes, now=None):
    t = now or datetime.now(timezone.utc)
    b = int(t.timestamp() // (minutes * 60))
    return datetime.fromtimestamp(b * minutes * 60, tz=timezone.utc).strftime("%Y%m%dT%H%M")


def data_mb():
    """Every file under SPORTS_DATA_DIR - the disk that fills: canonical.db AND books.db / games.db (written by this
    loop since the books + games sources joined it), cache, forecasts, snapshots, spools. sports/RETENTION.md."""
    tot = 0
    for p in config.DATA_DIR.rglob("*"):
        try:
            if p.is_file():
                tot += p.stat().st_size
        except OSError:
            continue
    return tot / 1e6


def _check_budget():
    mb = data_mb()
    cap, free = store_cap_mb()
    if mb > cap:
        raise BudgetExceeded(f"sports data {mb:.0f} MB > cap {cap:.0f} MB: ingestion paused (canonical.db {store.size_mb():.0f} MB)")
    if free is not None and free < DISK_FREE_FLOOR_MB:
        raise BudgetExceeded(f"disk free {free:.0f} MB < floor {DISK_FREE_FLOOR_MB:.0f} MB: ingestion paused")


# --------------------------------------------------------------------------------------------------------- fetches
def _espn_teams(lg):
    sp, l2 = lg["espn"]
    body = cache.fetch("espn:teams", lg["key"], 86400, lambda: net.get_json(f"{ESPN_SITE}/{sp}/{l2}/teams"))
    out = []
    for t in ((body.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []:
        t = t.get("team") or {}
        out.append({"id": t.get("id"), "abbr": t.get("abbreviation"), "name": t.get("displayName")})
    return out


def _espn_roster(lg, espn_team_id):
    sp, l2 = lg["espn"]
    return cache.fetch("espn:roster", f"{lg['key']}:{espn_team_id}", 12 * 3600,
                       lambda: net.get_json(f"{ESPN_SITE}/{sp}/{l2}/teams/{espn_team_id}/roster"))


def _espn_injuries_raw(lg):
    sp, l2 = lg["espn"]
    return cache.fetch("espn:injuries_raw", lg["key"], 1800, lambda: net.get_json(f"{ESPN_SITE}/{sp}/{l2}/injuries"))


# ------------------------------------------------------------------------------------------------------- ingestion
def _stored_events(con, league):
    return {e.event_id: e for _, e in store.events(con, league)}


def build_resolver(con, lg, observed_at, out):
    """ESPN teams + events (window) + rosters -> Resolver. Stores events / teams / players / memberships."""
    from . import espn
    league = lg["key"]
    teams = identity.Teams(league, _espn_teams(lg))
    today = datetime.now(timezone.utc).date()
    prev = _stored_events(con, league)
    batch = adapters.Batch()
    for d in range(-1, WINDOW_DAYS.get(league, 2) + 1):
        body = espn.scoreboard_raw(lg, today + timedelta(days=d))
        batch.extend(adapters.espn_events(body, lg, observed_at, prev))
    res = store.put_many(con, batch.records)
    store.quarantine_many(con, batch.quarantine, league, observed_at)
    out["espn_events"] = {k: res[k] for k in ("inserted", "duplicate", "revision", "invalid")}
    events = [r for r in batch.records if r.KIND == "event"]
    rosters = identity.Rosters()
    open_m = {}
    for _, m in _latest_memberships(con, league):
        if m.valid_to is None:
            open_m[m.player_id] = m
    team_ids = sorted({t for e in events for t in (e.home_team_id, e.away_team_id)})
    rb = adapters.Batch()
    for tid in team_ids:
        espn_tid = tid.split(":")[-1]
        try:
            body = _espn_roster(lg, espn_tid)
        except net.HttpError as e:
            rb.q("player", "espn", tid, f"roster_fetch_failed: {e}")
            continue
        rb.extend(adapters.espn_roster(body, lg, tid, observed_at, rosters, open_m))
    res = store.put_many(con, rb.records)
    store.quarantine_many(con, rb.quarantine, league, observed_at)
    out["espn_rosters"] = {"teams": len(team_ids), **{k: res[k] for k in ("inserted", "duplicate", "revision")}}
    all_events = list(_stored_events(con, league).values())
    return adapters.Resolver(league, teams, all_events, rosters)


def _latest_memberships(con, league):
    # ESPN roster memberships only: the history job's dated one-game rows (source espn_gamelog, one per player per
    # game) are closed intervals the resolver never uses and were decoded every cycle (bug sweep 2026-11-06 #44)
    rows = con.execute("SELECT r.* FROM records r JOIN (SELECT source, source_key, MAX(revision) rv FROM records "
                       "WHERE late = 0 AND kind='membership' AND source = 'espn' AND player_id LIKE ? "
                       "GROUP BY source, source_key) z ON "
                       "r.source = z.source AND r.source_key = z.source_key AND r.revision = z.rv "
                       "WHERE r.kind='membership'", (f"{league}:%",)).fetchall()
    return [(r["record_id"], store._rec(r)) for r in rows]


# an offer identity not seen for this long is cold - the same boundary sports/archive.py uses to move it out of the hot
# table (SPORTS_ARCHIVE_AFTER_H) - and is no longer a withdrawal candidate of a board read
REMOVAL_WINDOW_H = float(os.environ.get("SPORTS_ARCHIVE_AFTER_H", "72"))


def _board_offers(con, league, platform_prefix, observed_at):
    """Current revision of every offer of this platform whose identity was SEEN (last_seen_at / observed_at /
    known_at) within REMOVAL_WINDOW_H - the offers a board read can still withdraw. Through store._latest_rows, so the
    store's own definition of "current" applies (and any later refinement of it). For every offer seen in the window
    it returns exactly the rows of the former league-wide scan, whatever their event (missing from the store,
    unresolved, started - the caller's per-offer checks are unchanged), so withdrawal semantics are kept; only offers
    unseen for longer (cold, as sports/archive.py defines it) are left out. The window applies to the whole identity
    (its latest sighting over all its rows), so the result does not depend on which revision a re-sighting touched.
    Filtered in SQL: no payload of another platform or of cold history is decoded (measured 2026-10-24: the
    league-wide scan decoded every offer of the league on every board read and drove the ingest child from ~120 to
    ~250 MB)."""
    since = (parse_utc(observed_at) - timedelta(hours=REMOVAL_WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = len(platform_prefix)
    rows = store._latest_rows(
        con, "offer", "league = ? AND substr(platform, 1, ?) = ? AND (source, source_key) IN (SELECT source, source_key "
        "FROM records WHERE kind = 'offer' AND league = ? AND substr(platform, 1, ?) = ? GROUP BY source, source_key "
        "HAVING MAX(COALESCE(last_seen_at, observed_at, known_at)) >= ?)",
        [league, n, platform_prefix, league, n, platform_prefix, since], None, 10_000_000)
    return [(r["record_id"], store._rec(r)) for r in rows]


def _store_offers(con, platform_prefix, league, batch, observed_at, read_ok):
    """Store a board read; offers seen before, still in the future and missing from a SUCCESSFUL read get a 'removed'
    revision (never on a failed read)."""
    res = store.put_many(con, batch.records)
    store.quarantine_many(con, batch.quarantine, league, observed_at)
    removed = 0
    if read_ok:
        seen = {r.offer_id for r in batch.records}
        stale = []
        for _, o in _board_offers(con, league, platform_prefix, observed_at):
            if not o.platform.startswith(platform_prefix) or o.offer_id in seen or o.availability == "removed":
                continue
            if o.listed_start_utc and o.listed_time_kind == "start" and o.listed_start_utc <= observed_at:
                continue                                 # started: it left the board because the game began
            stale.append(store.withdrawn(o, observed_at))
        removed = store.put_many(con, stale)["revision"]
    return {k: res[k] for k in ("inserted", "duplicate", "revision", "invalid")} | {
        "quarantined": len(batch.quarantine), "removed": removed,
        "quarantine_reasons": {f"{s}:{r}": n for (s, r), n in batch.reason_counts().items()}}


def ingest_league(con, league, sources=SOURCES):
    _check_budget()
    lg = config.league(league)
    observed_at = now_utc()
    out = {"league": league, "observed_at": observed_at}
    resolver = build_resolver(con, lg, observed_at, out)
    if "pp" in sources:
        try:
            body = cache.fetch("lines:prizepicks", league, lines.REFRESH_S, lambda: lines._fetch_pp(lg), refresh=True)
            out["prizepicks"] = _store_offers(con, "prizepicks", league, adapters.pp_offers(body, lg, observed_at,
                                                                                            resolver), observed_at, True)
        except (net.HttpError, ValueError) as e:
            out["prizepicks"] = {"ok": False, "error": str(e)[:200]}
    if "ud" in sources:
        try:
            body = cache.fetch("lines:underdog", league, lines.REFRESH_S, lambda: lines._fetch_ud(lg), refresh=True)
            out["underdog"] = _store_offers(con, "underdog", league, adapters.ud_offers(body, lg, observed_at, resolver),
                                            observed_at, True)
        except (net.HttpError, ValueError) as e:
            out["underdog"] = {"ok": False, "error": str(e)[:200]}
    if "kalshi" in sources and kalshi.SERIES.get(league):
        kb, failed = adapters.Batch(), []
        for series, market in kalshi.SERIES[league].items():
            try:
                evs = cache.fetch("kalshi:events", series, kalshi.REFRESH_S, lambda s=series: kalshi._events(s),
                                  refresh=True)
            except (net.HttpError, ValueError) as e:
                failed.append(f"{series}: {e}")
                continue
            kb.extend(adapters.kalshi_offers(evs, lg, observed_at, market, resolver))
        out["kalshi"] = _store_offers(con, "kalshi", league, kb, observed_at, not failed)
        if failed:
            out["kalshi"]["errors"] = failed[:5]
    if "injuries" in sources:
        try:
            ib = adapters.espn_injuries(_espn_injuries_raw(lg), lg, observed_at)
            res = store.put_many(con, ib.records)
            store.quarantine_many(con, ib.quarantine, league, observed_at)
            out["injuries"] = {k: res[k] for k in ("inserted", "duplicate", "revision", "invalid")}
        except (net.HttpError, ValueError) as e:
            out["injuries"] = {"ok": False, "error": str(e)[:200]}
    if "oddsapi" in sources:                        # the one Odds API path (ledger; events / players resolved)
        from .books import sources as book_sources
        out["oddsapi"] = book_sources.oddsapi_ingest(con, league, observed_at, resolver, _espn_teams(lg))
    if "polymarket" in sources:
        from .books import sources as book_sources
        out["polymarket"] = book_sources.polymarket_ingest(con, league, observed_at, resolver)
    if "books" in sources:
        out["books"] = _ingest_books(con, lg, resolver)
    if "games" in sources:
        out["games"] = _ingest_games(lg)
    return out


def _ingest_books(con, lg, resolver):
    """FanDuel / BetRivers / OddsPapi through the books worker; every successful game read also becomes sc-1 offers."""
    from .books import bridge, store as book_store, worker as book_worker
    with _COLLECT_LOCK:
        bdb = book_store.connect()
        try:
            sink = bridge.sink(con, lambda league: resolver)
            res = book_worker.run_once(bdb, (lg["key"],), sink=sink)
            res["sc1_offers"] = sink.totals
            return res
        except Exception as e:                          # noqa: BLE001 - one source never stops the cycle
            return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        finally:
            bdb.close()


def books_league(con, league, now=None):
    """The books job for one league (scheduler "books"): exits at once - no provider request, no ESPN read, no canonical
    write - unless books.worker.pending says a game list, a game on the direct ladder or a probe is due. Otherwise the
    same pass the ingest cycle made before (_ingest_books): the canonical resolver first (ESPN events / rosters through
    the shared caches), then books.worker.run_once with the sc-1 sink."""
    from .books import store as book_store, worker as book_worker
    with _COLLECT_LOCK:
        bdb = book_store.connect()
        try:
            due = book_worker.pending(bdb, (league,), now=now)
        finally:
            bdb.close()
    if not due:
        return {"league": league, "due": False}
    _check_budget()                                     # store cap + DISK_FREE_FLOOR_MB, as every ingest cycle
    lg = config.league(league)
    observed_at = now_utc()
    out = {"league": league, "due": True, "observed_at": observed_at}
    resolver = build_resolver(con, lg, observed_at, out)
    out["books"] = _ingest_books(con, lg, resolver)
    return out


def cmd_books(a, con):
    """python -m sports.worker books nba nfl: FanDuel / BetRivers (+ OddsPapi when its guard allows) on the direct
    ladder (books/schedule.py), the scheduler's light "books" job. Exit 3 when ingestion is paused (store cap / disk
    floor), so the scheduler records a failure instead of a silent success."""
    flags.require("SPORTS_INGEST")
    out = {}
    for lg in a.leagues:
        try:
            out[lg] = books_league(con, lg)
        except BudgetExceeded as e:
            out[lg] = {"state": "failed", "error": str(e)}
        except Exception as e:                          # noqa: BLE001 - one league never stops the other
            out[lg] = {"state": "failed", "error": f"{type(e).__name__}: {str(e)[:200]}"}
    out["exchanges"] = _exchanges_step(con, a.leagues)
    out["rss_mb"] = rss_mb()
    _print(out)
    if a.leagues and all("ingestion paused" in str((out.get(lg) or {}).get("error") or "") for lg in a.leagues):
        raise SystemExit(3)
    return out


EXCHANGES_HEADROOM_MB = 35          # measured 2026-11-09: one exchanges pass adds ~25 MB to the process (Novig NBA answer)


def _exchanges_step(con, leagues):
    """The display-only exchanges (Novig, Gemini; books/exchanges.run_due) inside the books job's process: every
    SPORTS_EXCHANGES_EVERY_S (600), one hard SPORTS_EXCHANGES_BUDGET_S (30 s) budget, skipped when the process could
    pass the books job's memory cap or ingestion is paused. Never raises: the books job's own result stands."""
    from . import scheduler
    from .books import exchanges
    try:
        cap = float(os.environ.get("SPORTS_JOB_BOOKS_CAP_MB") or scheduler.BOOKS_CAP_MB * scheduler.cap_scale())
        if rss_mb() + EXCHANGES_HEADROOM_MB > cap:
            return {"skipped": f"memory: {rss_mb():.0f} + {EXCHANGES_HEADROOM_MB} MB > books cap {cap:.0f} MB"}
        _check_budget()
        return exchanges.run_due(con, leagues)
    except BudgetExceeded as e:
        return {"skipped": str(e)[:200]}
    except Exception as e:                              # noqa: BLE001 - never fails the books job
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def cmd_exchanges(a, con):
    """python -m sports.worker exchanges nba nfl: one display-only exchanges pass now (Novig, Gemini;
    books/exchanges.run), one hard time budget (SPORTS_EXCHANGES_BUDGET_S, 30 s). Operator / manual: the scheduled pass
    is the books job's exchanges step (_exchanges_step). Exit 3 when ingestion is paused (store cap / disk floor)."""
    from .books import exchanges
    flags.require("SPORTS_INGEST")
    try:
        _check_budget()
    except BudgetExceeded as e:
        _print({"state": "failed", "error": str(e)})
        raise SystemExit(3)
    out = exchanges.run(con, a.leagues)
    out["rss_mb"] = rss_mb()
    _print(out)
    return out


def _ingest_games(lg):
    from .games import collect, store as game_store
    with _COLLECT_LOCK:
        gdb = game_store.connect()
        try:
            return collect.tick(gdb, (lg["key"],))
        except Exception as e:                          # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        finally:
            gdb.close()


# ------------------------------------------------------------------------------------------------------ settlement
def _settled_events(con, league):
    """event ids of this league that already hold at least one settlement record (one indexed scan)."""
    return {r[0] for r in con.execute("SELECT DISTINCT event_id FROM records WHERE kind='settlement' AND event_id LIKE ?",
                                      (f"{league}:%",))}


def _events_with_offers(con, league, event_ids):
    out = set()
    ids = sorted(event_ids)
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        out |= {r[0] for r in con.execute(f"SELECT DISTINCT event_id FROM records_meta_all WHERE kind='offer' AND "
                                          f"event_id IN ({','.join('?' * len(part))})", part)}
    return out


def settle_league(con, league, ctx=None, now=None, since=None, max_catchup=None):
    """Settle the stored offers of this league's started games. Every game started in the last SETTLE_LOOKBACK_DAYS is
    re-read (ESPN corrects box scores). CATCH-UP (fresh 2026-10-24): an older game since SETTLE_FLOOR (or `since`)
    that has offers but NO settlement yet - a missed / failed / killed cycle - is settled too, at most max_catchup per
    run (oldest first), so a gap heals itself without re-reading every old game every hour. Idempotent: settlement.record
    stores an unchanged result once."""
    from . import espn
    lg = config.league(league)
    now = now or now_utc()
    done = set((ctx.cursor or {}).get("done", [])) if ctx else set()
    recent = (parse_utc(now) - timedelta(days=SETTLE_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    floor = since or SETTLE_FLOOR
    stored = [e for e in _stored_events(con, league).values()
              if e.start_utc and e.start_utc < now and e.event_id not in done and e.status not in ("cancelled",)]
    evs = [e for e in stored if e.start_utc >= recent]
    old = [e for e in stored if floor <= e.start_utc < recent]
    if old:
        have = _settled_events(con, league)
        with_offers = _events_with_offers(con, league, {e.event_id for e in old} - have)
        old = sorted((e for e in old if e.event_id in with_offers), key=lambda x: x.start_utc)
        evs += old[:max_catchup if max_catchup is not None else SETTLE_CATCHUP_MAX]
    out = {"events": 0, "observations": {}, "settlements": {"inserted": 0, "unchanged": 0, "revision": 0, "kept": 0},
           "empty_box": 0}
    for e in sorted(evs, key=lambda x: x.start_utc):
        espn_id = e.event_id.split(":")[-1]
        try:                                    # re-read within the lookback: ESPN corrects final box scores
            summ, raw = espn.summary_and_raw(lg, espn_id, refresh=e.status == "final")   # (14 of 2,513, AUDIT.md)
        except net.HttpError:
            continue
        if raw is not None and summ.get("completed"):
            _game_store_final(lg, espn_id, raw)         # the game store's box score + play-by-play: same request
        observed_at = now_utc()
        if summ.get("completed") and e.status != "final":
            e = replace(e, status="final", observed_at=observed_at, known_at=observed_at)
            store.put(con, e)
        b = adapters.espn_box(summ, lg, e.event_id, observed_at)
        res = store.put_many(con, b.records)
        store.quarantine_many(con, b.quarantine, league, observed_at)
        for k in ("inserted", "duplicate", "revision"):
            out["observations"][k] = out["observations"].get(k, 0) + res[k]
        if e.status == "final":
            obs = {}
            for r in b.records:
                if r.KIND == "stat":
                    obs.setdefault(r.participant_id, {})[r.stat] = r
            if not obs:                                 # completed but no box score yet / a partial ESPN answer: a
                out["empty_box"] += 1                   # transient gap - settle on a later read, never 'unresolved'
                out["events"] += 1                      # for every player (and the game is not marked done)
                continue
            sts = []
            for _, o in store.offers(con, event_id=e.event_id, limit=100000):
                if o.player_id is None or o.platform.startswith(("oddsapi", "oddspapi")) or \
                        o.platform in ("fanduel", "betrivers"):
                    continue                            # book prices are market references, never settled offers
                sts += settlement.settle_offer(o, obs.get(o.player_id, {}), e, observed_at)
            c = settlement.record(con, sts)
            for k, v in c.items():
                out["settlements"][k] = out["settlements"].get(k, 0) + v
            done.add(e.event_id)
            if ctx:
                ctx.checkpoint({"done": sorted(done)})
        out["events"] += 1
    return out


def _game_store_final(lg, espn_id, raw):
    """Store a final game in sports/games from the settlement's own ESPN body (first read only; never breaks
    settlement). NFL play roles need one extra core-plays request per game - unique data, fetched once."""
    try:
        from .games import collect, store as game_store
        gdb = game_store.connect()
        try:
            if not game_store.has_game(gdb, lg["key"], espn_id):
                collect.ingest_body(gdb, lg, espn_id, raw)
        finally:
            gdb.close()
    except Exception:                                   # noqa: BLE001
        traceback.print_exc()


# ------------------------------------------------------------------------------------------------------------- CLI
def _print(x):
    print(json.dumps(x, indent=1, default=str), flush=True)


def cmd_ingest(a, con):
    flags.require("SPORTS_INGEST")
    jobs = Jobs(con)
    srcs = tuple(s.strip() for s in a.sources.split(",")) if a.sources else SOURCES
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, CONCURRENCY)) as ex:
        futs = {lg: ex.submit(jobs.run, f"ingest:{lg}:{','.join(srcs)}:{bucket(a.bucket_min)}", "ingest",
                              lambda ctx, lg=lg: ingest_league(con, lg, srcs)) for lg in a.leagues}
        for lg, f in futs.items():
            results[lg] = f.result()
    results["rss_mb"] = rss_mb()
    results["store_mb"] = round(store.size_mb(), 1)
    _print(results)
    # 2026-11-05: a paused ingest exited 0 in ~1 s, so the scheduler logged success while every feed went stale for ~14 h
    paused = [lg for lg in a.leagues if (results.get(lg) or {}).get("state") == "failed"
              and "ingestion paused" in str((results.get(lg) or {}).get("error") or "")]
    if paused and len(paused) == len(a.leagues):
        raise SystemExit(3)
    return results


def cmd_settle(a, con):
    flags.require("SPORTS_INGEST")
    jobs = Jobs(con)
    since = getattr(a, "since", None)
    if since and len(since) == 10:
        since += "T00:00:00Z"
    leagues = a.leagues or ["nba", "nfl"]
    t0, stage_s = time.time(), []
    out = {}
    for i, lg in enumerate(leagues):
        if since:                                       # explicit backfill: its own job key (never 'done' by an hourly run)
            out[lg] = jobs.run(f"settle-backfill:{lg}:{since}:{bucket(10)}", "settle",
                               lambda ctx, lg=lg: settle_league(con, lg, ctx, since=since, max_catchup=10 ** 6))
        else:
            out[lg] = jobs.run(f"settle:{lg}:{bucket(getattr(a, 'bucket_min', None) or 60)}", "settle",
                               lambda ctx, lg=lg: settle_league(con, lg, ctx))
        if out[lg].get("still_running"):
            # bug sweep #12 review: the timed-out settle's thread is still writing on this connection. Nothing else
            # (the next league, results, learning) runs beside it; the caller ends the process (main -> os._exit).
            for rest in leagues[i + 1:]:
                out[rest] = {"state": "not_run", "error": f"{lg} settle still running after its timeout"}
            out["still_running"] = True
            _print(out)
            return out
    stage_s.append(["settle", round(time.time() - t0, 1)])
    if getattr(a, "no_post", False):
        # 2026-11-08: the results ledger + model scorecard (~1,000 s and growing) ran in this process after settling,
        # so the job hit its 1,500 s kill every run (5 timeouts, 4 h backoff, settlement every ~4.5 h). They run as
        # the scheduler's "settle-post" job now (started right after this one); settlement itself is unchanged.
        _write_stages("settle_stages.json", stage_s)
        _print(out)
        return out
    post = _post_settle(leagues, con, stage_s, t0, "settle_stages.json")
    out.update(post)
    _print(out)
    return out


def _write_stages(name, stage_s, finished=True):
    """admin ops: seconds since start at the end of each stage (settle_stages.json / settle_post_stages.json)."""
    try:
        (config.DATA_DIR / name).write_text(json.dumps({"at": now_utc(), "stage_s": stage_s, "finished": finished}))
    except OSError:
        pass


def _post_settle(leagues, con, stage_s, t0, stages_name, budget_s=None):
    """The bookkeeping after settling: the model + shadow scorecards, then the results ledger per league. Never breaks
    settlement. The stages file is written after each stage, so a killed run still says how far it got.
    Scorecards FIRST (2026-11-08 review): they resume per event (learning.on_settled / shadow.settle marks), while the
    ledger is rebuilt in full each run - a ledger that is killed (cap / timeout) can no longer starve the scorecards.
    budget_s (settle-post, stall 2026-11-09): the scorecards stop between events once it is spent - the events done so
    far keep their marks, the next run resumes - and the ledger still runs, so one run never holds model-daily (and so
    settle) for long. None = no limit (manual runs, settle without --no-post)."""
    out = {}
    deadline = (t0 + budget_s) if budget_s else None
    try:                                                # model scorecard + diagnostic queue (sports/model/learning.py)
        from .model import learning                     # never breaks settlement
        out["model_learning"] = learning.on_settled(con, deadline=deadline)
    except Exception as e:                              # noqa: BLE001
        out["model_learning"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    stage_s.append(["model_learning", round(time.time() - t0, 1)])
    _write_stages(stages_name, stage_s, finished=False)
    from . import snapshots                             # Results ledger (never breaks settlement)
    out["results"] = {}
    for lg in leagues:
        if lg not in snapshots.LEAGUES:
            continue
        try:
            out["results"][lg] = snapshots.write_results(con, lg)
        except Exception as e:                          # noqa: BLE001
            out["results"][lg] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    stage_s.append(["results", round(time.time() - t0, 1)])
    _write_stages(stages_name, stage_s)
    return out


def cmd_settle_post(a, con):
    """python -m sports.worker settle-post nba nfl: the results ledger and the model / shadow scorecards from the
    settlements already stored (the scheduler starts it after each settle run). Writes only results.json,
    learning.db and shadow.db; never a settlement."""
    flags.require("SPORTS_INGEST")
    leagues = a.leagues or ["nba", "nfl"]
    out = _post_settle(leagues, con, [], time.time(), "settle_post_stages.json",
                       budget_s=getattr(a, "budget_s", None))
    _print(out)
    return out


def _still_running(res):
    """True when a job of this step timed out and its thread is still running on the shared connection."""
    if not isinstance(res, dict):
        return False
    return bool(res.get("still_running")) or any(isinstance(v, dict) and v.get("still_running") for v in res.values())


def cmd_loop(a, con):
    """ingest every --every minutes; Kalshi only every --kalshi-every minutes (its quotes are the store's largest
    writer: ~2,700 price revisions per league per read on 2026-10-23); settle hourly."""
    flags.require("SPORTS_INGEST")
    last_settle, last_kalshi = 0.0, 0.0
    base = tuple(s.strip() for s in a.sources.split(",")) if a.sources else SOURCES
    while True:
        rss = rss_mb()
        if rss and rss > MAX_MB:
            print(f"rss {rss:.0f} MB > {MAX_MB} MB: exiting for a clean restart", file=sys.stderr, flush=True)
            return 3
        try:
            srcs = base
            if "kalshi" in base and time.time() - last_kalshi < a.kalshi_every * 60:
                srcs = tuple(x for x in base if x != "kalshi")
            elif "kalshi" in base:
                last_kalshi = time.time()
            res = cmd_ingest(argparse.Namespace(leagues=a.leagues, sources=",".join(srcs), bucket_min=a.bucket_min), con)
            if _still_running(res):                     # bug sweep #12 review: never a second writer beside it
                return {"still_running": True, "step": "ingest"}
            if time.time() - last_settle > 3600:
                res = cmd_settle(argparse.Namespace(leagues=a.leagues), con)
                if _still_running(res):
                    return {"still_running": True, "step": "settle"}
                last_settle = time.time()
        except BudgetExceeded as e:
            print(str(e), file=sys.stderr, flush=True)
        except Exception:                               # noqa: BLE001 - one bad cycle never stops the loop
            traceback.print_exc()
        time.sleep(max(60, a.every * 60))


def cmd_forecast(a, con):
    """sharp-2 market-only research forecasts for every gradable stored pick'em offer (writes: SPORTS_INGEST)."""
    flags.require("SPORTS_INGEST")
    from . import sharp2
    out = {lg: sharp2.record_league(con, lg, now_utc()) for lg in (a.leagues or ["nba", "nfl"])}
    _print(out)
    return out


def cmd_coverage(a, con):
    from . import coverage
    rep = coverage.report(con)
    _print(rep) if a.json else print(coverage.render_text(rep))
    return rep


def cmd_verify(a, con):
    flags.require("SPORTS_INGEST")
    from . import coverage
    rep = coverage.verify_kalshi_vs_espn(config.league(a.league), a.date)
    _print(rep)
    return rep


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m sports.worker", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    for name in ("ingest", "loop"):
        s = sub.add_parser(name)
        s.add_argument("leagues", nargs="+")
        s.add_argument("--sources")
        s.add_argument("--bucket-min", type=int, default=5)
        if name == "loop":
            s.add_argument("--every", type=int, default=10)
            s.add_argument("--kalshi-every", type=int, default=30)
    s = sub.add_parser("books")
    s.add_argument("leagues", nargs="+")
    s = sub.add_parser("exchanges")
    s.add_argument("leagues", nargs="+")
    s = sub.add_parser("settle")
    s.add_argument("leagues", nargs="*")
    s.add_argument("--since", help="backfill: settle every stored game started since this date (YYYY-MM-DD)")
    s.add_argument("--bucket-min", type=int, default=60, help="idempotency bucket of the scheduled run")
    s.add_argument("--no-post", action="store_true",
                   help="settle only: the results ledger and scorecards run as `settle-post` (scheduler job)")
    s = sub.add_parser("settle-post")
    s.add_argument("leagues", nargs="*")
    s.add_argument("--budget-s", type=float, default=None,
                   help="stop the scorecards between events after this many seconds (resumed next run); the ledger "
                        "still runs")
    s = sub.add_parser("forecast")
    s.add_argument("leagues", nargs="*")
    s = sub.add_parser("coverage")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("verify")
    s.add_argument("league")
    s.add_argument("--date", required=True)
    sub.add_parser("jobs")
    s = sub.add_parser("train")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    if a.cmd == "train":
        flags.require("SPORTS_TRAIN")                   # a refused command sets no memory cap on its process
    cap = apply_memory_cap() if a.cmd in ("ingest", "loop", "books", "exchanges", "settle", "settle-post", "train") else None
    if a.cmd == "train":
        from .model import jobs as model_jobs           # model agent's registered training jobs (sports/model/jobs.py)
        return model_jobs.main(a.rest)
    with store.opened() as con:
        if a.cmd == "migrate":
            _print({"schema_level": store.migrate(con), "path": str(store.db_path())})
            return
        if cap:
            print(f"memory cap: {cap}", file=sys.stderr)
        res = {"ingest": cmd_ingest, "loop": cmd_loop, "books": cmd_books, "exchanges": cmd_exchanges, "settle": cmd_settle,
               "settle-post": cmd_settle_post,
               "coverage": cmd_coverage, "verify": cmd_verify, "forecast": cmd_forecast, "jobs": lambda a, con: _print(Jobs(con).list())}[a.cmd](a, con)
        if a.cmd in ("ingest", "loop", "settle") and _still_running(res):
            # bug sweep #12 review: a timed-out job's thread is still writing on this connection. End the process now
            # (no close of the connection under it, no wait on it); SQLite rolls back its open batch, and the job is
            # resumed from its cursor by the next run.
            print("a timed-out job is still running: exiting for a clean restart", file=sys.stderr, flush=True)
            sys.stdout.flush()
            os._exit(4)
        return res


if __name__ == "__main__":
    try:
        main()
    except flags.FlagOff as e:
        raise SystemExit(str(e))
