"""The sports worker: a separate process for ingestion, settlement and (later) training. Never the web process, never
at import time. Run it as its own service / cron:

  python -m sports.worker migrate                       create / upgrade <SPORTS_DATA_DIR>/canonical.db (additive)
  python -m sports.worker ingest nba nfl [--sources espn,pp,ud,kalshi,injuries,oddsapi]   one cycle   (SPORTS_INGEST)
  python -m sports.worker loop nba nfl --every 10       ingest every N minutes + settle hourly       (SPORTS_INGEST)
  python -m sports.worker settle [nba nfl]              settle stored offers of final games           (SPORTS_INGEST)
  python -m sports.worker verify nba --date YYYY-MM-DD  Kalshi settled values vs ESPN box scores      (SPORTS_INGEST)
  python -m sports.worker forecast [nba nfl]            sharp-2 market-only research forecasts         (SPORTS_INGEST)
  python -m sports.worker coverage [--json]             coverage report from the store (read-only, no flag)
  python -m sports.worker jobs                          the job ledger
  python -m sports.worker train ...                     refused unless SPORTS_TRAIN=1 (model agents register jobs)

Bounds (environment, with defaults): SPORTS_WORKER_CONCURRENCY=2 parallel jobs; SPORTS_JOB_TIMEOUT_S=300 per job;
SPORTS_JOB_ATTEMPTS=3 with exponential backoff + jitter (net.backoff); SPORTS_WORKER_MAX_MB=768 address-space cap
(POSIX RLIMIT_AS; reported "unsupported" on Windows) plus an RSS check between jobs; SPORTS_STORE_MAX_MB=1500 - ingestion
stops writing when the canonical store reaches it. Per-host request pacing is net.py's (ESPN 2/s, Kalshi 1/1.5 s,
PrizePicks 1/2 s, Underdog 1/s).

Jobs are idempotent (job_key includes the source and a time bucket; writes are content-hashed) and resumable (a job
that died 'running' is retried from its saved cursor).
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

from . import adapters, cache, config, flags, identity, kalshi, lines, net, oddsapi, settlement, store
from .contracts import now_utc, parse_utc

CONCURRENCY = int(os.environ.get("SPORTS_WORKER_CONCURRENCY", "2"))
JOB_TIMEOUT_S = int(os.environ.get("SPORTS_JOB_TIMEOUT_S", "300"))
JOB_ATTEMPTS = int(os.environ.get("SPORTS_JOB_ATTEMPTS", "3"))
MAX_MB = int(os.environ.get("SPORTS_WORKER_MAX_MB", "768"))
STORE_MAX_MB = float(os.environ.get("SPORTS_STORE_MAX_MB", "1500"))
SETTLE_LOOKBACK_DAYS = 4                 # box scores re-read for corrections this long after a game
WINDOW_DAYS = {"nba": 2, "wnba": 2, "cbb": 2, "nfl": 7, "cfb": 7}
ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports"


class BudgetExceeded(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------------ resources
def apply_memory_cap(mb=MAX_MB):
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

    def _row(self, key):
        r = self.con.execute("SELECT * FROM jobs WHERE job_key = ?", (key,)).fetchone()
        return dict(r) if r else None

    def _set(self, key, **kw):
        with self.lock:
            cols = ", ".join(f"{k} = ?" for k in kw)
            self.con.execute(f"UPDATE jobs SET {cols} WHERE job_key = ?", list(kw.values()) + [key])
            self.con.commit()

    def cursor(self, key):
        r = self._row(key)
        return json.loads(r["cursor"]) if r and r.get("cursor") else None

    def save_cursor(self, key, cur):
        self._set(key, cursor=json.dumps(cur), heartbeat_at=now_utc())

    def run(self, key, job, fn):
        """Run fn(ctx) once for job_key. done -> skipped (idempotent). failed / dead 'running' -> retried (resumable:
        ctx.cursor holds the saved progress). -> {"state", "result"|"error", "attempts"}."""
        with self.lock:
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


def _check_budget():
    mb = store.size_mb()
    if mb > STORE_MAX_MB:
        raise BudgetExceeded(f"sports store {mb:.0f} MB > SPORTS_STORE_MAX_MB {STORE_MAX_MB:.0f}: ingestion paused")


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
    rows = con.execute("SELECT r.* FROM records r JOIN (SELECT source, source_key, MAX(revision) rv FROM records "
                       "WHERE kind='membership' AND player_id LIKE ? GROUP BY source, source_key) z ON "
                       "r.source = z.source AND r.source_key = z.source_key AND r.revision = z.rv "
                       "WHERE r.kind='membership'", (f"{league}:%",)).fetchall()
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
        for _, o in store.offers(con, league=league, as_of=None, limit=200000):
            if not o.platform.startswith(platform_prefix) or o.offer_id in seen or o.availability == "removed":
                continue
            if o.listed_start_utc and o.listed_time_kind == "start" and o.listed_start_utc <= observed_at:
                continue                                 # started: it left the board because the game began
            stale.append(replace(o, availability="removed", observed_at=observed_at, known_at=observed_at))
        removed = store.put_many(con, stale)["revision"]
    return {k: res[k] for k in ("inserted", "duplicate", "revision", "invalid")} | {
        "quarantined": len(batch.quarantine), "removed": removed,
        "quarantine_reasons": {f"{s}:{r}": n for (s, r), n in batch.reason_counts().items()}}


def ingest_league(con, league, sources=("espn", "pp", "ud", "kalshi", "injuries", "oddsapi")):
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
    if "oddsapi" in sources:
        out["oddsapi"] = {"ok": False, "error": "ODDS_API_KEY not set (optional)"} if not config.odds_api_key() \
            else _ingest_oddsapi(con, lg, observed_at)
    return out


def _ingest_oddsapi(con, lg, observed_at):

    try:
        sport = lg.get("odds_api")
        key = config.odds_api_key()
        events = net.get_json(f"{oddsapi.BASE}/sports/{sport}/events", params={"apiKey": key})
        b = adapters.Batch()
        for ev in sorted(events, key=lambda e: e.get("commence_time") or "")[:16]:
            body = net.get_json(f"{oddsapi.BASE}/sports/{sport}/events/{ev['id']}/odds",
                                params={"apiKey": key, "regions": "us", "oddsFormat": "decimal",
                                        "markets": ",".join(oddsapi.CORE.get(lg["family"], []))})
            b.extend(adapters.oddsapi_offers(body, lg, observed_at))
        return _store_offers(con, "oddsapi", lg["key"], b, observed_at, True)
    except (net.HttpError, ValueError, KeyError) as e:
        return {"ok": False, "error": str(e).replace(config.odds_api_key() or "\0", "***")[:200]}


# ------------------------------------------------------------------------------------------------------ settlement
def settle_league(con, league, ctx=None, now=None):
    from . import espn
    lg = config.league(league)
    now = now or now_utc()
    done = set((ctx.cursor or {}).get("done", [])) if ctx else set()
    since = (parse_utc(now) - timedelta(days=SETTLE_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    evs = [e for e in _stored_events(con, league).values()
           if e.start_utc and since <= e.start_utc < now and e.event_id not in done]
    out = {"events": 0, "observations": {}, "settlements": {"inserted": 0, "unchanged": 0, "revision": 0}}
    for e in sorted(evs, key=lambda x: x.start_utc):
        espn_id = e.event_id.split(":")[-1]
        try:                                    # re-read within the lookback: ESPN corrects final box scores
            summ = espn.summary(lg, espn_id, refresh=e.status == "final")   # (14 of 2,513 NBA values, AUDIT.md)
        except net.HttpError:
            continue
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
            sts = []
            for _, o in store.offers(con, event_id=e.event_id, limit=100000):
                if o.player_id is None or o.platform.startswith("oddsapi"):
                    continue
                sts += settlement.settle_offer(o, obs.get(o.player_id, {}), e, observed_at)
            c = settlement.record(con, sts)
            for k, v in c.items():
                out["settlements"][k] += v
            done.add(e.event_id)
            if ctx:
                ctx.checkpoint({"done": sorted(done)})
        out["events"] += 1
    return out


# ------------------------------------------------------------------------------------------------------------- CLI
def _print(x):
    print(json.dumps(x, indent=1, default=str), flush=True)


def cmd_ingest(a, con):
    flags.require("SPORTS_INGEST")
    jobs = Jobs(con)
    srcs = tuple(s.strip() for s in a.sources.split(",")) if a.sources else \
        ("espn", "pp", "ud", "kalshi", "injuries", "oddsapi")
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, CONCURRENCY)) as ex:
        futs = {lg: ex.submit(jobs.run, f"ingest:{lg}:{','.join(srcs)}:{bucket(a.bucket_min)}", "ingest",
                              lambda ctx, lg=lg: ingest_league(con, lg, srcs)) for lg in a.leagues}
        for lg, f in futs.items():
            results[lg] = f.result()
    results["rss_mb"] = rss_mb()
    results["store_mb"] = round(store.size_mb(), 1)
    _print(results)
    return results


def cmd_settle(a, con):
    flags.require("SPORTS_INGEST")
    jobs = Jobs(con)
    out = {lg: jobs.run(f"settle:{lg}:{bucket(60)}", "settle", lambda ctx, lg=lg: settle_league(con, lg, ctx))
           for lg in (a.leagues or ["nba", "nfl"])}
    _print(out)
    return out


def cmd_loop(a, con):
    """ingest every --every minutes; Kalshi only every --kalshi-every minutes (its quotes are the store's largest
    writer: ~2,700 price revisions per league per read on 2026-10-23); settle hourly."""
    flags.require("SPORTS_INGEST")
    last_settle, last_kalshi = 0.0, 0.0
    base = tuple(s.strip() for s in a.sources.split(",")) if a.sources else         ("espn", "pp", "ud", "kalshi", "injuries", "oddsapi")
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
            cmd_ingest(argparse.Namespace(leagues=a.leagues, sources=",".join(srcs), bucket_min=a.bucket_min), con)
            if time.time() - last_settle > 3600:
                cmd_settle(argparse.Namespace(leagues=a.leagues), con)
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
    s = sub.add_parser("settle")
    s.add_argument("leagues", nargs="*")
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
    cap = apply_memory_cap() if a.cmd in ("ingest", "loop", "settle", "train") else None
    if a.cmd == "train":
        flags.require("SPORTS_TRAIN")
        from .model import jobs as model_jobs           # model agent's registered training jobs (sports/model/jobs.py)
        return model_jobs.main(a.rest)
    with store.opened() as con:
        if a.cmd == "migrate":
            _print({"schema_level": store.migrate(con), "path": str(store.db_path())})
            return
        if cap:
            print(f"memory cap: {cap}", file=sys.stderr)
        return {"ingest": cmd_ingest, "loop": cmd_loop, "settle": cmd_settle, "coverage": cmd_coverage,
                "verify": cmd_verify, "forecast": cmd_forecast, "jobs": lambda a, con: _print(Jobs(con).list())}[a.cmd](a, con)


if __name__ == "__main__":
    try:
        main()
    except flags.FlagOff as e:
        raise SystemExit(str(e))
