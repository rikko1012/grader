#!/usr/bin/env python3
"""
veto_serve.py - serving glue for the frozen map-veto model veto-1 (veto_model.py, veto-1-a140ca69ec). DISPLAY + LOGGING
ONLY: nothing here reaches a grade. grader / cs2_scout / hltv are untouched, picklog.MODEL_VERSION does not hash this file,
and the old hltv.predict_maps output that the grader inputs carry (map_probs, switched off by use_predicted_maps=False) is
left exactly as it was. veto-1 replaces hltv.predict_maps on the member pages because it passed its registered final-period
test (research/map_veto/RESULTS.md); map-informed grading stays off (a later vm-1 shadow may read the records logged here).

1. The history table (~1 MB file) is built OUTSIDE the web process: `python veto_serve.py build` is a child process that
   reads the HLTV Postgres in bounded 31-day chunks (two streaming passes: first the event / period pools, then the
   records day by day, so no 400-day row set is ever held), writes the table atomically and exits. Measured peak and
   the data revision it saw go to a meta file next to it. The web process only loads the finished file. A rebuild runs
   when the UTC day changes (the table holds every match dated before today) and when the stored vetoes of the window
   change (a late scrape / backfill; checked every SOURCE_CHECK_S); a failed build keeps the last good table and is
   retried after BUILD_RETRY_S (a failure is never cached as a result).
2. One background thread ("veto-serve", every LOOP_S) predicts each HLTV-schedule match starting within HORIZON_H (and
   matches a card asked about) ONCE per input change - table, pool, confirmed actions, roster - and keeps the member
   payload in memory. Members never compute: for_match() is a dict lookup.
3. Confirmed actions: BO3.gg's match page (status upcoming / current) names the maps once the veto is out (game number ->
   Map 1 / Map 2 / decider), and its veto list (bans and picks with the team) when present; the HLTV database stores
   vetoes only for finished matches. The prediction is conditioned on them exactly (veto_model known_actions); state =
   pending / partial / confirmed.
4. Map pool: the event's own pool (a complete veto at the same HLTV event - today's from the database, else the latest
   in the table), else the most common pool of the last POOL_RECENT_D days in the table. The source is kept in the record
   (internal); members see no pool label.
5. Logging: every changed prediction is saved (before the match starts: pre_start=1) in CS2_DATA_DIR/veto_log.db -
   model version, table cutoff + sha, team ids, lineup ids, pool, known actions, every slot marginal, map sets, Map 1/2
   pairs, the top sequences with the remaining mass, the facts - bounded (MAX_ROWS, MAX_PER_MATCH). The table file of
   every cutoff used is archived (KEEP_TABLES) so a record replays exactly (replay()). Each logged grade forecast carries
   a reference to the match's current veto record (picklog _factors shadow["veto"]). The frozen simulator's Map 1 / Map 2
   for the same match is saved alongside (veto_baselines, BO3 only) so /admin/veto.json?action=score compares the two
   prospectively on settled matches.
"""
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import zlib
from pathlib import Path

import veto_model as vm

SERVE_VERSION = "vserve-1"
HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or HERE)
VDIR = DATA_DIR / ".cache" / "veto"
TABLE_FILE = VDIR / "veto_table.json.gz"
META_FILE = VDIR / "veto_table.meta.json"
ARCHIVE = VDIR / "tables"
LOG_DB = DATA_DIR / "veto_log.db"

TABLE_DAYS = 400              # as research/map_veto/build_table.py (the tested serving window)
CHUNK_DAYS = 31
KEEP_TABLES = 14              # archived table files (one per cutoff, ~1 MB each) - records replay exactly
LOOP_S = 120                  # background cycle (BO3.gg's confirmed maps re-read at most this often)
SOURCE_CHECK_S = 2 * 3600     # how often the window's stored-veto revision is compared with the table's
SOURCE_WINDOW_D = 21          # ... over the last 21 days of the table (late scrapes / backfills land there)
BUILD_RETRY_S = 1800          # a failed build is retried after 30 minutes (the last good table stays loaded)
BUILD_TIMEOUT_S = 900
POOLS_EVERY_S = 900           # today's finished vetoes (event pools) re-read every 15 minutes
HORIZON_H = 36                # predict matches starting within the next 36 h ...
LIVE_KEEP_H = 8               # ... and keep / update those that started up to 8 h ago (live veto updates)
POOL_RECENT_D = 7
WANT_MAX = 300                # card-requested matches not on the schedule window (bounded)
STORE_MAX = 600
MAX_ROWS = 60000              # veto_log.db bounds
MAX_PER_MATCH = 60
KEEP_DAYS = 400
SEQ_KEEP = 20                 # sequences saved per record (the rest = remaining mass)
SHOW_SLOTS = 5                # maps per slot shown to members
SHOW_COMBOS = 5               # combinations shown to members
KNOWN_TTL_H = 0.03            # BO3.gg match list cache (~2 min)
KNOWN_KEEP_S = 1800           # a failed BO3.gg read keeps the last good confirmed maps this long
YIELD_S = 0.01                # pause between predictions (request threads get the interpreter)

_LOCK = threading.Lock()      # guards STATE / _STORE / _WANT (never held during I/O or model work)
STATE = {"table": None, "meta": None, "loaded_at": None, "table_sha": None, "error": None, "build": None,
         "build_running": False, "next_build_at": 0.0, "source_checked_at": 0.0, "source_rev": None, "force": False,
         "pools_today": {}, "pools_at": 0.0, "pools_error": None, "known": {}, "known_at": 0.0, "known_error": None,
         "cycle": None, "thread": None, "table_pools": None, "builds": [], "maps": None}
_STORE = {}                   # match_id -> {"key", "payload", "record_id", "state", "start", "computed_at", "version"}
_WANT = collections.OrderedDict()


def enabled():
    return os.environ.get("VETO_SERVE", "1") != "0"


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(t):
    return t.isoformat(timespec="seconds") if isinstance(t, dt.datetime) else t


def _utc(t):
    if t is None or t == "":
        return None
    if isinstance(t, dt.datetime):
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    try:
        x = dt.datetime.fromisoformat(str(t).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)


def _sha(obj, n=16):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:n]


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 16), b""):
            h.update(blk)
    return h.hexdigest()[:16]


def map_name(m):
    return vm.MAPS_DISPLAY.get(m, str(m).title())


# ====================================================================== 1. table builder (child process)
def _windows(cut, days, chunk_days):
    d = cut - days
    while d < cut:
        yield dt.date.fromordinal(d).isoformat(), dt.date.fromordinal(min(d + chunk_days, cut)).isoformat()
        d += chunk_days


_RNG = "JOIN match_overview mo USING (matchid) WHERE mo.date >= %s AND mo.date < %s"


def _chunk_records(cur, a, b, mname, full):
    """make_record dicts for every match dated in [a, b), sorted by (date, id). full=False reads only what the veto
    status needs (no lineups / map winners) - pass 1."""
    cur.execute("SELECT matchid, event, date, lan FROM match_overview WHERE date >= %s AND date < %s", (a, b))
    ov = {r[0]: r[1:] for r in cur.fetchall()}
    acts = collections.defaultdict(list)
    cur.execute(f"SELECT matchid, step, teamid, action, mapid FROM map_vetoes {_RNG}", (a, b))
    for mid, st, tid, act, mp in cur.fetchall():
        acts[mid].append((st, tid, act, mname.get(mp, str(mp))))
    teams = collections.defaultdict(set)
    series, mw = collections.defaultdict(dict), collections.defaultdict(lambda: collections.defaultdict(dict))
    lu = collections.defaultdict(lambda: collections.defaultdict(set))
    if full:
        cur.execute(f"SELECT matchid, teamid, mapid, score FROM matches {_RNG} AND sideid = 0", (a, b))
        for mid, tid, mp, sc in cur.fetchall():
            teams[mid].add(tid)
            if mp == 0:
                series[mid][tid] = sc
            else:
                mw[mid][mname.get(mp, str(mp))][tid] = sc
        cur.execute(f"SELECT DISTINCT matchid, teamid, playerid FROM player_stats {_RNG} AND mapid = 0", (a, b))
        for mid, tid, pid in cur.fetchall():
            lu[mid][tid].add(pid)
    else:
        cur.execute(f"SELECT DISTINCT matchid, teamid FROM matches {_RNG} AND sideid = 0", (a, b))
        for mid, tid in cur.fetchall():
            teams[mid].add(tid)
    recs = []
    for mid, (event, date, lan) in ov.items():
        tl = sorted(teams.get(mid, ()))
        if len(tl) != 2:
            continue
        if full:
            recs.append(vm.make_record(mid, date, event, lan, tl, None, acts.get(mid, []),
                                       {t: lu[mid].get(t, ()) for t in tl},
                                       {m: vm._winner(sc) for m, sc in mw.get(mid, {}).items()}, vm._winner(series.get(mid, {}))))
        else:
            recs.append(vm.make_record(mid, date, event, lan, tl, None, acts.get(mid, [])))
    recs.sort(key=lambda r: (r["date"], r["mid"]))
    return recs


def _assign_pool(r, ev_pool, day_pool, window_days=15):
    """veto_model.assign_pools for ONE record, from pools counted over the whole window beforehand (same rule, same
    tie-breaks: event pool by (-count, pool), else the dominant pool within +-window_days)."""
    if r["status"] == "complete":
        r["pool"], r["pool_source"] = sorted(a[3] for a in r["actions"]), "own veto"
        return r
    seen = {a[3] for a in r["actions"]}
    cand = [p for p, _ in sorted(ev_pool.get(r["event"], {}).items(), key=lambda x: (-x[1], x[0])) if seen <= set(p)]
    src = "event veto"
    if not cand:
        d0 = vm._day(r["date"])
        near = collections.Counter()
        for d in range(d0 - window_days, d0 + window_days + 1):
            if d in day_pool:
                near.update(day_pool[d])
        cand = [p for p, _ in sorted(near.items(), key=lambda x: (-x[1], x[0])) if seen <= set(p)]
        src = "period pool"
    r["pool"], r["pool_source"] = (sorted(cand[0]), src) if cand else (None, None)
    if r["status"] == "inferred":
        rest = sorted(set(r["pool"] or ()) - seen)
        if r["pool"] and len(rest) == 1:
            r["actions"].append([7, None, "leftover", rest[0]])
            r["inferred_leftover"] = True
        else:
            r["status"] = "partial"
    return r


def build_lean(conn, cutoff, days=TABLE_DAYS, chunk_days=CHUNK_DAYS, progress=None):
    """The same VetoTable as veto_model.build_table_from_db(conn, cutoff, days) (tested equal: tests + the production
    check in MODEL_VERSIONS), streamed: pass 1 counts the complete vetoes' pools per event and day, pass 2 builds each
    chunk's records, assigns pools and adds them day by day - only one chunk of rows is ever in memory."""
    cut = vm._day(cutoff)
    cur = conn.cursor()
    cur.execute("SET statement_timeout = 60000")
    cur.execute("SELECT mapid, name FROM maps")
    mname = {r[0]: vm.map_key(r[1]) for r in cur.fetchall()}
    ev_pool = collections.defaultdict(collections.Counter)
    day_pool = collections.defaultdict(collections.Counter)
    for a, b in _windows(cut, days, chunk_days):
        for r in _chunk_records(cur, a, b, mname, False):
            if r["status"] == "complete":
                p = tuple(sorted(x[3] for x in r["actions"]))
                ev_pool[r["event"]][p] += 1
                day_pool[vm._day(r["date"])][p] += 1
        if progress:
            progress("pass1", a)
    table = vm.VetoTable()
    n = 0
    for a, b in _windows(cut, days, chunk_days):
        by = collections.defaultdict(list)
        for r in _chunk_records(cur, a, b, mname, True):
            by[r["date"]].append(_assign_pool(r, ev_pool, day_pool))
            n += 1
        for d in sorted(by):
            table.add_day(by[d], d)
        by.clear()
        if progress:
            progress("pass2", a)
    cur.close()
    return vm.finish_cutoff(table, cut), {"records": n}


def source_rev(cur, cutoff, window_d=SOURCE_WINDOW_D):
    """Revision of the stored vetoes the table's last window_d days were built from: 'n=<rows>;max=<match id>'."""
    c = vm._day(cutoff)
    cur.execute(f"SELECT count(*), max(v.matchid) FROM map_vetoes v {_RNG}",
                (dt.date.fromordinal(c - window_d).isoformat(), dt.date.fromordinal(c).isoformat()))
    n, mx = cur.fetchone()
    return f"n={n};max={mx}"


class _PeakSampler(threading.Thread):
    """Peak resident memory of THIS process, sampled every 0.1 s (Linux: VmHWM is exact and starts at exec)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak, self.stop = 0, False

    @staticmethod
    def rss():
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) * 1024
        except OSError:
            pass
        try:
            import psutil
            return psutil.Process().memory_info().rss
        except Exception:
            return 0

    @staticmethod
    def hwm():
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmHWM:"):
                        return int(line.split()[1]) * 1024
        except OSError:
            pass
        try:
            import psutil
            return getattr(psutil.Process().memory_info(), "peak_wset", 0)
        except Exception:
            return 0

    def run(self):
        while not self.stop:
            self.peak = max(self.peak, self.rss())
            time.sleep(0.1)


def _dsn():
    return (os.environ.get("HLTV_DB_DSN") or os.environ.get("HLTV_DATABASE_URL") or "").strip()


def build_cli(out, cutoff=None, days=TABLE_DAYS):
    """Child-process entry: build, save atomically, write the meta file, print the meta as JSON, exit."""
    if hasattr(os, "nice"):
        try:
            os.nice(10)                       # members first
        except OSError:
            pass
    smp = _PeakSampler()
    smp.start()
    base = smp.rss()
    t0 = time.time()
    cutoff = cutoff or _now().date().isoformat()
    import psycopg2
    con = psycopg2.connect(_dsn(), connect_timeout=20, application_name="cs2-scout-veto-build")
    try:
        con.set_session(readonly=True, autocommit=True)
        table, info = build_lean(con, cutoff, days)
        with con.cursor() as cur:
            cur.execute("SET statement_timeout = 60000")
            rev = source_rev(cur, cutoff)
    finally:
        con.close()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    save_lines(trim_table(table), tmp)
    os.replace(tmp, out)
    smp.stop = True
    meta = {"version": vm.version(), "serve": SERVE_VERSION, "cutoff": cutoff, "days": days, "source_rev": rev,
            "built_at": _iso(_now()), "seconds": round(time.time() - t0, 1), "teams": len(table.team),
            "team_records": sum(len(v) for v in table.team.values()), "match_records": info["records"],
            "file_kb": round(out.stat().st_size / 1024), "sha": _file_sha(out),
            "peak_rss_mb": round(max(smp.peak, smp.hwm()) / 2 ** 20, 1), "start_rss_mb": round(base / 2 ** 20, 1),
            "pid": os.getpid()}
    mt = out.with_name(out.name + ".meta.tmp")
    mt.write_text(json.dumps(meta), encoding="utf-8")
    os.replace(mt, out.with_name(out.name.replace(".json.gz", "") + ".meta.json"))
    print(json.dumps(meta), flush=True)
    return meta


# ---------------------------------------------------------------- serving file: trimmed + line-by-line (low load peak)
def trim_table(table, keep=None):
    """Keep each team's newest `keep` vetoes (veto_model.PARAMS["max_records"] = 20): predictions are IDENTICAL, because
    VetoTable.history reads only the newest max_records of a team (its own and an identity-linked team's), and a team is
    identity-linked only through a player of one of those records (tests + the production check). ~3x less memory."""
    keep = keep or vm.PARAMS["max_records"]
    for tid in list(table.team):
        table.team[tid] = table.team[tid][-keep:]
    table.keep = keep
    return table


NL = chr(10)


def save_lines(table, path):
    """gzip JSON lines: a header (everything but the team lists), then one line per team - so the web process loads it
    line by line (peak = the table + one line, not a whole parsed JSON document next to it)."""
    d = table.to_json()
    teams = d.pop("team")
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps(dict(d, format="veto-lines-1")) + NL)
        for tid in sorted(teams, key=int):
            f.write(json.dumps([tid, teams[tid]]) + NL)


def load_lines(path):
    """VetoTable from save_lines() - the same object VetoTable.from_json gives for the same content."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        head = json.loads(f.readline())
        if head.get("format") != "veto-lines-1":
            raise ValueError("not a veto-lines-1 file")
        t = vm.VetoTable.from_json(dict(head, team={}))
        for line in f:
            tid, lst = json.loads(line)
            t.team[int(tid)] = [vm.TeamRec(r[0], r[1], r[2], r[3], tuple(r[4]),
                                           t._acts((a[0], a[1], tuple(a[2])) for a in r[5]),
                                           tuple((x[0], x[1]) for x in r[6]), r[7]) for r in lst]
            for r in t.team[int(tid)]:
                for p in r.lineup:
                    t.player_teams[p][int(tid)] = max(t.player_teams[p].get(int(tid), 0), r.day)
    t.stamp = 1
    return t


# ====================================================================== 2. table management (web process, background)
def _meta_path(table_path):
    p = Path(table_path)
    return p.with_name(p.name.replace(".json.gz", "") + ".meta.json")


def load_table(path=None):
    """Load a finished table file (+ its meta) and swap it in. Returns True when loaded. A broken file is refused and the
    current table stays."""
    path = Path(path or TABLE_FILE)
    try:
        meta = json.loads(_meta_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    try:
        sha = _file_sha(path)
        if meta.get("sha") and meta["sha"] != sha:
            raise ValueError("table file does not match its meta (partial copy)")
        t = load_lines(path)
    except Exception as e:                    # noqa: BLE001 - never replace a good table with a broken one
        with _LOCK:
            STATE["error"] = f"load failed: {type(e).__name__}: {str(e)[:160]}"
        return False
    meta = dict(meta, sha=sha, cutoff=meta.get("cutoff") or dt.date.fromordinal(t.cutoff).isoformat())
    install_table(t, meta)
    _archive(path, meta)
    return True


def install_table(t, meta):
    """Swap a loaded table in (the old one is dropped once no prediction holds it)."""
    pools = table_pools(t)
    with _LOCK:
        STATE.update(table=t, meta=meta, table_sha=meta.get("sha"), loaded_at=_iso(_now()), error=None, table_pools=pools)
        for e in _STORE.values():
            e["key"] = None                   # every match re-predicted on the new table (next cycle)


def _archive(path, meta):
    """Keep a copy of every table a record was made on (by cutoff + sha), newest KEEP_TABLES."""
    try:
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        dst = ARCHIVE / f"veto_table_{meta.get('cutoff')}_{meta.get('sha')}.json.gz"
        if not dst.exists():
            tmp = dst.with_name(dst.name + ".tmp")
            tmp.write_bytes(Path(path).read_bytes())
            os.replace(tmp, dst)
            _meta_path(dst).write_text(json.dumps(meta), encoding="utf-8")
        files = sorted(ARCHIVE.glob("veto_table_*.json.gz"), key=lambda p: p.stat().st_mtime)
        for old in files[:-KEEP_TABLES]:
            old.unlink(missing_ok=True)
            _meta_path(old).unlink(missing_ok=True)
    except OSError:
        pass


def archived_table(cutoff, sha):
    """The archived table a record was made on (None when it aged out)."""
    p = ARCHIVE / f"veto_table_{cutoff}_{sha}.json.gz"
    return load_lines(p) if p.exists() else None


def run_build(cutoff=None, timeout=BUILD_TIMEOUT_S):
    """Build the table in a child process that exits (the web process never holds the build's rows) and load it.
    Returns the child's meta, or raises. Never more than one at a time."""
    with _LOCK:
        if STATE["build_running"]:
            raise RuntimeError("a build is already running")
        STATE["build_running"] = True
    t0 = time.time()
    try:
        VDIR.mkdir(parents=True, exist_ok=True)
        out = VDIR / "veto_table.next.json.gz"
        cmd = [sys.executable, str(HERE / "veto_serve.py"), "build", "--out", str(out)]
        if cutoff:
            cmd += ["--cutoff", str(cutoff)]
        p = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise RuntimeError(f"builder exit {p.returncode}: {(p.stderr or '').strip()[-300:]}")
        meta = json.loads((p.stdout or "").strip().splitlines()[-1])
        os.replace(out, TABLE_FILE)
        os.replace(_meta_path(out), META_FILE)
        if not load_table(TABLE_FILE):
            raise RuntimeError(STATE.get("error") or "load failed")
        meta["wall_s"] = round(time.time() - t0, 1)
        with _LOCK:
            STATE["build"] = {"ok": True, "at": _iso(_now()), **meta}
            STATE["builds"] = (STATE["builds"] + [STATE["build"]])[-10:]
            STATE["source_rev"] = meta.get("source_rev")
        return meta
    except Exception as e:
        with _LOCK:
            STATE["build"] = {"ok": False, "at": _iso(_now()), "error": f"{type(e).__name__}: {str(e)[:300]}",
                              "wall_s": round(time.time() - t0, 1)}
            STATE["builds"] = (STATE["builds"] + [STATE["build"]])[-10:]
        raise
    finally:
        with _LOCK:
            STATE["build_running"] = False


def _hltv_conn():
    import hltv
    if not hltv.dsn():
        raise RuntimeError("HLTV database not configured")
    return hltv._connect()


def _maps_table(cur):
    with _LOCK:
        if STATE["maps"]:
            return STATE["maps"]
    cur.execute("SELECT mapid, name FROM maps")
    m = {r[0]: vm.map_key(r[1]) for r in cur.fetchall()}
    with _LOCK:
        STATE["maps"] = m
    return m


def _needs_build(now):
    """(reason or None): no table, the UTC day moved past its cutoff, the window's stored vetoes changed, forced."""
    with _LOCK:
        meta, nxt, force = STATE["meta"], STATE["next_build_at"], STATE["force"]
        checked = STATE["source_checked_at"]
    if time.time() < nxt and not force:
        return None
    if force:
        return "forced"
    if not meta:
        return "no table"
    if str(meta.get("cutoff")) < now.date().isoformat():
        return "new day"
    if time.time() - checked >= SOURCE_CHECK_S:
        with _LOCK:
            STATE["source_checked_at"] = time.time()
        try:
            con = _hltv_conn()
            try:
                with con.cursor() as cur:
                    rev = source_rev(cur, meta["cutoff"])
            finally:
                con.close()
        except Exception:
            return None                       # can't tell: keep the table (checked again later)
        if meta.get("source_rev") and rev != meta.get("source_rev"):
            return f"stored vetoes changed ({meta.get('source_rev')} -> {rev})"
    return None


def ensure_table(now=None):
    now = now or _now()
    if STATE["table"] is None and TABLE_FILE.exists():
        load_table(TABLE_FILE)
    why = _needs_build(now)
    if not why or not _dsn():
        return None
    with _LOCK:
        STATE["force"] = False
    try:
        meta = run_build()
        with _LOCK:
            STATE["next_build_at"] = 0.0
        return dict(meta, reason=why)
    except Exception:
        with _LOCK:
            STATE["next_build_at"] = time.time() + BUILD_RETRY_S
        traceback.print_exc()
        return None


def table_pools(table):
    """From the loaded table: event -> latest complete 7-map pool seen there, and the most common pool of the last
    POOL_RECENT_D days (one count per match)."""
    ev, by_match = {}, {}
    last = 0
    for lst in table.team.values():
        for r in lst:
            for _, _, avail in r.acts:
                if len(avail) == 7:
                    p = tuple(sorted(avail))
                    by_match[r.mid] = (r.day, p)
                    if r.event is not None and (r.event not in ev or (r.day, r.mid) > ev[r.event][0]):
                        ev[r.event] = ((r.day, r.mid), p)
                    last = max(last, r.day)
                    break
    recent = collections.Counter(p for d, p in by_match.values() if d > last - POOL_RECENT_D)
    rp = sorted(recent.items(), key=lambda x: (-x[1], x[0]))
    return {"event": {k: v[1] for k, v in ev.items()}, "recent": list(rp[0][0]) if rp else None,
            "recent_n": rp[0][1] if rp else 0}


def refresh_today_pools(now=None):
    """Event pools from vetoes HLTV stored for matches dated on/after the table cutoff (today's finished ones): bounded
    query, every POOLS_EVERY_S; a failure keeps the last good pools."""
    with _LOCK:
        meta = STATE["meta"]
        if time.time() - STATE["pools_at"] < POOLS_EVERY_S or not meta:
            return
    try:
        con = _hltv_conn()
        try:
            with con.cursor() as cur:
                mname = _maps_table(cur)
                cur.execute("SELECT mo.event, v.matchid, v.step, v.action, v.mapid FROM map_vetoes v JOIN match_overview mo "
                            "USING (matchid) WHERE mo.date >= %s ORDER BY v.matchid LIMIT 20000", (meta["cutoff"],))
                rows = cur.fetchall()
        finally:
            con.close()
    except Exception as e:
        with _LOCK:
            STATE["pools_error"] = f"{type(e).__name__}"
            STATE["pools_at"] = time.time() - POOLS_EVERY_S + 120
        return
    with _LOCK:
        STATE["pools_today"] = pools_from_rows(rows, mname)
        STATE["pools_at"], STATE["pools_error"] = time.time(), None


def pools_from_rows(rows, mname):
    """{event: pool} from complete stored vetoes (7 consecutive steps, 7 distinct maps); the highest match id wins."""
    by = collections.defaultdict(list)
    ev = {}
    for e, mid, st, act, mp in rows:
        by[mid].append((st, act, mname.get(mp, str(mp))))
        ev[mid] = e
    out = {}
    for mid in sorted(by):
        v = sorted(by[mid])
        if [s for s, _, _ in v] == list(range(1, 8)) and len({m for _, _, m in v}) == 7 and v[-1][1] == "leftover":
            out[ev[mid]] = sorted(m for _, _, m in v)
    return out


# ====================================================================== 3. confirmed actions (BO3.gg)
SLOT_STEPS = {"bo1": {1: 7}, "bo3": {1: 3, 2: 4, 3: 7}, "bo5": {1: 3, 2: 4, 3: 5, 4: 6, 5: 7}}


def _fmt(x):
    s = str(x or "").lower().replace(" ", "")
    if s in ("1", "bo1"):
        return "bo1"
    if s in ("3", "bo3"):
        return "bo3"
    if s in ("5", "bo5"):
        return "bo5"
    return None


def known_from_bo3(m, team_key_of=None):
    """Confirmed veto steps from one BO3.gg match (dict with games / match_maps): ({step: action}, problem or None).
    games (number -> map name) give the picks and the decider (team unknown); match_maps (order, team_id, choice_type
    1 = pick, 2 = removal, other = the leftover) give the whole order with the team."""
    fmt = _fmt(m.get("bo_type"))
    if not fmt:
        return {}, "unknown format"
    out = {}
    tid = {m.get("team1_id"): "team1", m.get("team2_id"): "team2"}
    for v in sorted(m.get("match_maps") or [], key=lambda v: v.get("order") or 0):
        name = v.get("map_name") or (v.get("maps") or {}).get("map_name")
        st = v.get("order")
        if not name or not isinstance(st, int):
            continue
        ct = v.get("choice_type")
        act = "picked" if ct == 1 else "removed" if ct == 2 else "leftover"
        out[st] = {"step": st, "team": tid.get(v.get("team_id")) if act != "leftover" else None, "action": act,
                   "map": vm.map_key(name), "source": "BO3.gg veto"}
    for g in m.get("games") or []:
        n, name = g.get("number"), g.get("map_name")
        st = SLOT_STEPS[fmt].get(n)
        if not name or st is None:
            continue
        mk = vm.map_key(name)
        cur = out.get(st)
        if cur and cur["map"] != mk:
            return out, f"BO3.gg veto and game {n} disagree ({cur['map']} vs {mk})"
        if not cur:
            out[st] = {"step": st, "team": None, "action": "leftover" if st == 7 else "picked", "map": mk,
                       "source": "BO3.gg maps"}
    return out, None


def refresh_known(now=None):
    """BO3.gg matches around now (upcoming / current, started up to LIVE_KEEP_H ago, starting within 4 h): confirmed
    steps per canonical HLTV match id. A failed read keeps the last good copy for KNOWN_KEEP_S."""
    now = now or _now()
    try:
        import bo3
        import matchid
        b = bo3.BO3(background=True)
        try:
            params = {"scope": "widget-matches", "page[offset]": 0, "page[limit]": 100, "sort": "start_date",
                      "filter[matches.status][in]": "upcoming,current",
                      "filter[matches.start_date][gt]": (now - dt.timedelta(hours=LIVE_KEEP_H)).strftime("%Y-%m-%d %H:00"),
                      "filter[matches.start_date][lt]": (now + dt.timedelta(hours=4)).strftime("%Y-%m-%d %H:00"),
                      "filter[matches.discipline_id][eq]": 1, "with": "teams,tournament,games"}
            rows = b._run(b.cs2._api._make_request("/matches", params), ttl=KNOWN_TTL_H).get("results") or []
        finally:
            b.close()
        known = {}
        for m in rows:
            t1, t2 = (m.get("team1") or {}).get("name"), (m.get("team2") or {}).get("name")
            if not t1 or not t2:
                continue
            cm = matchid.canonical_match(t1, t2, m.get("start_date"), m.get("bo_type"),
                                         (m.get("tournament") or {}).get("name"), mint=False)
            mid = cm.get("match_id")
            if cm.get("status") != "linked" or not str(mid or "").startswith("hltv:"):
                continue
            steps, problem = known_from_bo3(m)
            if not steps and not problem:
                continue
            ks = cm.get("team_keys_by_side") or [None, None]
            known[mid] = {"steps": steps, "problem": problem, "side_ids": {"team1": _team_id(ks[0]), "team2": _team_id(ks[1])},
                          "names": {"team1": t1, "team2": t2}, "slug": m.get("slug"),
                          "status": m.get("status"), "at": _iso(now)}
        with _LOCK:
            STATE["known"], STATE["known_at"], STATE["known_error"] = known, time.time(), None
    except Exception as e:                    # noqa: BLE001
        with _LOCK:
            STATE["known_error"] = f"{type(e).__name__}"
            if time.time() - STATE["known_at"] > KNOWN_KEEP_S:
                STATE["known"] = {}          # too old to stand for the live veto: no confirmed maps rather than stale ones


# ====================================================================== 4. prediction per match
def _team_id(key):
    return int(key.split(":", 1)[1]) if isinstance(key, str) and key.startswith("team:") else None


def schedule_matches(now=None):
    """HLTV schedule matches in the window, from matchid's in-memory copy (no database read here)."""
    now = now or _now()
    try:
        import matchid
        matchid._ensure()
        with matchid._LOCK:
            sched = list(matchid._S.get("schedule") or [])
    except Exception:
        sched = []
    out = []
    for r in sched:
        st = _utc(r.get("start"))
        if st is None or not (now - dt.timedelta(hours=LIVE_KEEP_H) <= st <= now + dt.timedelta(hours=HORIZON_H)):
            continue
        out.append({"match_id": f"hltv:{r['matchid']}", "hltv_match": int(r["matchid"]), "team1": r.get("team1"),
                    "team2": r.get("team2"), "start": st, "format": r.get("format"), "event": r.get("event") or None})
    return out


def want(match_id, team_a, team_b, start, bo=None, event=None):
    """A card asked about a match: predict it in the background if it is not already known (bounded, no work here)."""
    if not match_id or not str(match_id).startswith("hltv:") or not team_a or not team_b:
        return
    with _LOCK:
        if match_id in _STORE or match_id in _WANT:
            return
        _WANT[match_id] = {"match_id": match_id, "hltv_match": int(str(match_id).split(":", 1)[1]), "team1": team_a,
                           "team2": team_b, "start": _utc(start), "format": bo, "event": event}
        while len(_WANT) > WANT_MAX:
            _WANT.popitem(last=False)


def _resolve_teams(m):
    try:
        import matchid
        ka, kb = matchid.team_key(m["team1"]), matchid.team_key(m["team2"])
    except Exception:
        ka = kb = None
    return _team_id(ka), _team_id(kb)


def pool_candidates(event, pools_today, tpools):
    out = []
    if event and event in (pools_today or {}):
        out.append((list(pools_today[event]), "event veto today"))
    if event and event in ((tpools or {}).get("event") or {}):
        out.append((list(tpools["event"][event]), "event veto"))
    if (tpools or {}).get("recent"):
        out.append((list(tpools["recent"]), f"recent pool ({POOL_RECENT_D} d)"))
    seen, uniq = set(), []
    for p, s in out:
        if len(p) == 7 and tuple(p) not in seen:
            seen.add(tuple(p))
            uniq.append((sorted(p), s))
    return uniq


def match_inputs(m, table, pools_today, tpools, known_entry, now):
    """Every input of one prediction (what the record saves and replay() re-runs). Returns (inputs, problem)."""
    fmt = _fmt(m.get("format"))
    if not fmt:
        return None, "format not BO1 / BO3 / BO5"
    ta, tb = m.get("ta"), m.get("tb")
    if ta is None or tb is None:
        ta, tb = _resolve_teams(m)
    if ta is None or tb is None or ta == tb:
        return None, "teams not resolved to HLTV ids"
    steps, kproblem, ksrc = {}, None, None
    if known_entry:
        if known_entry.get("problem"):
            kproblem = known_entry["problem"]
        side_id = known_entry.get("side_ids") or {}
        for st, k in (known_entry.get("steps") or {}).items():
            t = side_id.get(k.get("team")) if k.get("team") else None
            if k.get("team") and t not in (ta, tb):
                kproblem = kproblem or "BO3.gg team not in this HLTV match"
            steps[int(st)] = dict(k, team=t if t in (ta, tb) else None)
        ksrc = sorted({k.get("source") for k in steps.values() if k.get("source")}) or None
    known = [{"step": s, "team": k["team"], "action": k["action"], "map": k["map"]} for s, k in sorted(steps.items())]
    kmaps = {k["map"] for k in known}
    cands = pool_candidates(m.get("event"), pools_today, tpools)
    pool = next(((p, s) for p, s in cands if kmaps <= set(p)), None)
    lin_a, lin_b = table.last_lineup.get(ta), table.last_lineup.get(tb)
    before = max(now.date(), dt.date.fromordinal(table.cutoff)) if table.cutoff else now.date()
    inp = {"match_id": m["match_id"], "hltv_match": m.get("hltv_match"), "match_start": _iso(m.get("start")),
           "format": fmt, "event": m.get("event"), "before": before.isoformat(),
           "teams": {"a": {"id": ta, "name": m.get("team1"), "lineup": sorted(lin_a[1]) if lin_a else [],
                           "lineup_from": dt.date.fromordinal(lin_a[0]).isoformat() if lin_a else None},
                     "b": {"id": tb, "name": m.get("team2"), "lineup": sorted(lin_b[1]) if lin_b else [],
                           "lineup_from": dt.date.fromordinal(lin_b[0]).isoformat() if lin_b else None}},
           "listed_first": "a", "pool": pool[0] if pool else None, "pool_source": pool[1] if pool else None,
           "known_actions": known, "known_source": ksrc, "known_problem": kproblem}
    if kproblem:
        return inp, f"confirmed maps contradict each other: {kproblem}"
    if not pool:
        return inp, "no map pool holds the confirmed maps" if kmaps else "no map pool known"
    return inp, None


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def veto_state(fmt, known):
    n = vm.N_MAPS[fmt]
    slots = set(SLOT_STEPS[fmt].values())
    have = {k["step"] for k in known}
    if len(have) == 7 or slots <= have:
        return "confirmed"
    return "partial" if have else "pending"


def predict_inputs(inp, table):
    """Run veto-1 on saved inputs (uncached: the full sequence distribution is needed for pairs / sets). Raises
    veto_model.IllegalVeto for an impossible veto."""
    a, b = inp["teams"]["a"], inp["teams"]["b"]
    return vm.predict(a["id"], b["id"], inp["format"], inp["pool"], before=inp["before"],
                      known_actions=inp["known_actions"], event=inp["event"], lineup_a=a["lineup"] or None,
                      lineup_b=b["lineup"] or None, listed_first=inp["listed_first"], table=table, top=SEQ_KEEP,
                      names={a["id"]: a["name"] or f"team {a['id']}", b["id"]: b["name"] or f"team {b['id']}"},
                      return_dist=True)


def _pairs(dist):
    acc = collections.defaultdict(float)
    for seq, p in dist.items():
        if len(seq) >= 2:
            acc[seq[:2]] += p
    return [[list(k), round(v, 6)] for k, v in sorted(acc.items(), key=lambda x: (-x[1], x[0]))]


def make_record(inp, out, table_meta, state, now):
    dist = out.pop("_dist", {})
    output = {k: out.get(k) for k in ("maps", "inclusion", "sets", "sequences", "sequences_remaining_mass",
                                      "first_actor", "templates", "history")}
    output["pairs"] = _pairs(dist) if inp["format"] != "bo1" else None
    rec = {"kind": "veto-forecast", "version": out.get("version"), "params_sha": out.get("params_sha"),
           "serve": SERVE_VERSION, "input_cutoff": out.get("input_cutoff"),
           "table": {k: (table_meta or {}).get(k) for k in ("cutoff", "sha", "built_at", "source_rev")},
           "inputs": inp, "state": state, "output": output, "facts": out.get("facts")}
    rec["id"] = _sha(rec)
    st = _utc(inp.get("match_start"))
    rec["logged_at"] = _iso(now)
    rec["pre_start"] = bool(st and now < st)
    return rec


SLOTS = {"bo1": [("Map", "map1")], "bo3": [("Map 1", "map1"), ("Map 2", "map2"), ("Decider", "decider")],
         "bo5": [("Map 1", "map1"), ("Map 2", "map2"), ("Map 3", "map3"), ("Map 4", "map4"), ("Decider", "decider")]}
SHOWN_SLOTS = {"bo1": ("map1",), "bo3": ("map1", "map2"), "bo5": ("map1", "map2")}   # predicted slots on the page


def member_payload(inp, rec, state, now):
    """What a member sees (no hedge labels: Predicted / Confirmed / timestamp; no pool source). Each predicted slot is
    that slot's own chance; whole combinations are listed separately with the probability left outside the shortlist."""
    fmt = inp["format"]
    slots = SLOTS[fmt]
    a, b = inp["teams"]["a"], inp["teams"]["b"]
    who = {a["id"]: a["name"], b["id"]: b["name"]}
    step_slot = {s: slots[i] for i, s in enumerate(sorted(SLOT_STEPS[fmt].values()))}
    known = inp["known_actions"]
    conf = [{"slot": step_slot[k["step"]][0], "key": step_slot[k["step"]][1], "map": k["map"], "name": map_name(k["map"]),
             "by": who.get(k["team"])} for k in known if k["step"] in step_slot]
    order = [k for _, k in slots]
    conf.sort(key=lambda c: order.index(c["key"]))
    bans = [{"step": k["step"], "map": k["map"], "name": map_name(k["map"]), "by": who.get(k["team"])}
            for k in known if k["action"] == "removed"]
    out = {"state": state, "format": fmt, "at": rec["logged_at"] if rec else _iso(now), "version": rec["version"] if rec else None,
           "record": rec["id"] if rec else None, "teams": [a["name"], b["name"]], "match_id": inp["match_id"],
           "confirmed": conf, "bans": bans, "slots": [], "combos": [], "combos_other": None, "facts": []}
    if not rec or state == "confirmed":
        return out
    o = rec["output"]
    done = {c["key"] for c in conf}
    for lab, key in slots:
        if key in done or key not in SHOWN_SLOTS[fmt]:
            continue
        d = (o.get("maps") or {}).get(key) or {}
        top = list(d.items())[:SHOW_SLOTS]
        if top:
            out["slots"].append({"slot": lab, "top": [[m, map_name(m), p] for m, p in top],
                                 "other": round(max(0.0, 1 - sum(p for _, p in top)), 4)})
    if fmt in ("bo3", "bo5"):
        seqs = o["sequences"][:SHOW_COMBOS]
        out["combos"] = [[[map_name(m) for m in s], p] for s, p in seqs]
        out["combos_other"] = round(max(0.0, 1 - sum(p for _, p in seqs)), 4)
    out["facts"] = (rec.get("facts") or [])[:4]
    return out


def compute_match(m, table, table_meta, pools_today, tpools, known_entry, now):
    """(cache key, member payload or None, record or None, problem)."""
    inp, problem = match_inputs(m, table, pools_today, tpools, known_entry, now)
    if inp is None:
        return None, None, None, problem
    key = _sha({"inp": {k: v for k, v in inp.items() if k != "match_start"}, "table": (table_meta or {}).get("sha"),
                "v": vm.version(), "s": SERVE_VERSION})
    state = veto_state(inp["format"], inp["known_actions"]) if not inp.get("known_problem") else "pending"
    if problem:
        # contradictory confirmed data / no pool: nothing shown (no guessed panel); the attempt is still recorded
        return key, None, None, problem
    try:
        out = predict_inputs(inp, table)
    except vm.IllegalVeto as e:
        return key, None, None, f"illegal veto: {e}"
    rec = make_record(inp, out, table_meta, state, now)
    return key, member_payload(inp, rec, state, now), rec, None


def cycle(now=None, matches=None, log=True):
    """One background pass: predict every window match whose inputs changed. Returns a summary."""
    now = now or _now()
    with _LOCK:
        table, meta, ptoday, tpools = STATE["table"], STATE["meta"], dict(STATE["pools_today"]), STATE["table_pools"]
        known = dict(STATE["known"])
        wanted = list(_WANT.values())
    if table is None:
        return {"skipped": "no table"}
    ms = {m["match_id"]: m for m in (matches if matches is not None else schedule_matches(now))}
    for w in wanted:
        ms.setdefault(w["match_id"], w)
    t0 = time.time()
    n = {"matches": len(ms), "computed": 0, "unchanged": 0, "logged": 0, "problems": 0}
    probs = collections.Counter()
    for mid, m in ms.items():
        try:
            key, payload, rec, problem = compute_match(m, table, meta, ptoday, tpools, known.get(mid), now)
        except Exception as e:                # noqa: BLE001 - one match never stops the pass
            key, payload, rec, problem = None, None, None, f"error: {type(e).__name__}: {str(e)[:120]}"
        with _LOCK:
            cur = _STORE.get(mid)
        if key is not None and cur and cur.get("key") == key:
            n["unchanged"] += 1
            continue
        if problem:
            n["problems"] += 1
            probs[problem.split(":")[0]] += 1
        n["computed"] += 1
        if rec is not None and log:
            try:
                n["logged"] += bool(log_record(rec))
            except Exception:
                traceback.print_exc()
        ent = {"key": key, "payload": payload, "record_id": rec and rec["id"], "state": rec and rec["state"],
               "start": m.get("start"), "computed_at": _iso(now), "version": vm.version(), "problem": problem,
               "cutoff": (meta or {}).get("cutoff"), "teams": [m.get("team1"), m.get("team2")]}
        with _LOCK:
            _STORE[mid] = ent
            _WANT.pop(mid, None)
        if YIELD_S:
            time.sleep(YIELD_S)
    with _LOCK:
        for mid in [k for k, e in _STORE.items() if e.get("start") and e["start"] < now - dt.timedelta(hours=LIVE_KEEP_H)]:
            _STORE.pop(mid, None)
        while len(_STORE) > STORE_MAX:
            _STORE.pop(min(_STORE, key=lambda k: _STORE[k].get("computed_at") or ""), None)
        STATE["cycle"] = dict(n, at=_iso(now), seconds=round(time.time() - t0, 2), problems_by=dict(probs))
    return STATE["cycle"]


# ====================================================================== member reads (memory only)
def for_match(match_id):
    """The member payload for a canonical match id, or None. A dict lookup - never computes."""
    with _LOCK:
        e = _STORE.get(match_id)
    return e and e.get("payload")


def forecast_ref(match_id):
    """What a logged grade forecast stores about the veto (picklog shadow['veto']): the record it can be replayed from."""
    with _LOCK:
        e = _STORE.get(match_id) if match_id else None
    if not e:
        return {"id": None, "version": vm.version(), "match_id": match_id, "reason": "no veto forecast for this match"}
    return {"id": e.get("record_id"), "version": e.get("version"), "state": e.get("state"), "cutoff": e.get("cutoff"),
            "match_id": match_id, "at": e.get("computed_at"), **({"reason": e["problem"]} if e.get("problem") else {})}


def match_id_of(res):
    """The canonical match id of a card result (same rule as server._card_match_id; memory only)."""
    pb, nm = res.get("player_block") or {}, res.get("next_match") or {}
    mm = re.fullmatch(r"hltv-(\d+)", str(nm.get("slug") or ""))
    if mm:
        return f"hltv:{mm.group(1)}"
    if nm.get("hltv_match"):
        return f"hltv:{nm['hltv_match']}"
    try:
        import matchid
        if not matchid.ready():
            return None
        return matchid.match_id_for_row({"team_name": pb.get("playing_for") or pb.get("current_team"),
                                         "opp_name": nm.get("opponent"), "start": nm.get("start"), "bo": nm.get("bo"),
                                         "event": nm.get("event")})
    except Exception:
        return None


def for_card(res):
    """The card's veto panel (or None) + a background request when the match is not predicted yet. No compute here."""
    mid = match_id_of(res)
    if not mid:
        return None
    p = for_match(mid)
    if p is None:
        pb, nm = res.get("player_block") or {}, res.get("next_match") or {}
        want(mid, pb.get("playing_for") or pb.get("current_team"), nm.get("opponent"), nm.get("start"), nm.get("bo"),
             nm.get("event"))
    return p


# ====================================================================== 5. logging (bounded SQLite)
_DB_INIT = [False]
_DB_LOCK = threading.Lock()


def _db():
    con = sqlite3.connect(LOG_DB, timeout=30)
    con.row_factory = sqlite3.Row
    if not _DB_INIT[0]:
        LOG_DB.parent.mkdir(parents=True, exist_ok=True)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS veto_records (id TEXT PRIMARY KEY, match_id TEXT, hltv_match INTEGER,
                       logged_at TEXT, match_start TEXT, state TEXT, pre_start INTEGER, version TEXT, fmt TEXT, body BLOB)""")
        con.execute("CREATE INDEX IF NOT EXISTS veto_rec_match ON veto_records (hltv_match, logged_at)")
        con.execute("CREATE INDEX IF NOT EXISTS veto_rec_time ON veto_records (logged_at)")
        con.execute("""CREATE TABLE IF NOT EXISTS veto_baselines (hltv_match INTEGER, day TEXT, pool TEXT, logged_at TEXT,
                       match_start TEXT, body TEXT, PRIMARY KEY (hltv_match, day, pool))""")
        con.commit()
        _DB_INIT[0] = True
    return con


def log_record(rec, con=None):
    """Save one record (idempotent on its content id). Bounded: MAX_PER_MATCH per match, MAX_ROWS in all (oldest go)."""
    own = con is None
    con = con or _db()
    try:
        with _DB_LOCK:
            if con.execute("SELECT 1 FROM veto_records WHERE id = ?", (rec["id"],)).fetchone():
                return False
            inp = rec["inputs"]
            if con.execute("SELECT count(*) FROM veto_records WHERE hltv_match = ?", (inp.get("hltv_match"),)).fetchone()[0] >= MAX_PER_MATCH:
                return False
            con.execute("INSERT INTO veto_records (id, match_id, hltv_match, logged_at, match_start, state, pre_start, version, "
                        "fmt, body) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (rec["id"], inp["match_id"], inp.get("hltv_match"), rec["logged_at"], inp.get("match_start"),
                         rec["state"], int(rec["pre_start"]), rec["version"], inp["format"],
                         zlib.compress(json.dumps(rec, separators=(",", ":"), default=str).encode(), 6)))
            con.commit()
        return True
    finally:
        if own:
            con.close()


def prune_log(now=None, con=None):
    now = now or _now()
    own = con is None
    con = con or _db()
    try:
        with _DB_LOCK:
            cut = _iso(now - dt.timedelta(days=KEEP_DAYS))
            con.execute("DELETE FROM veto_records WHERE logged_at < ?", (cut,))
            con.execute("DELETE FROM veto_baselines WHERE logged_at < ?", (cut,))
            n = con.execute("SELECT count(*) FROM veto_records").fetchone()[0]
            if n > MAX_ROWS:
                con.execute("DELETE FROM veto_records WHERE id IN (SELECT id FROM veto_records ORDER BY logged_at LIMIT ?)",
                            (n - MAX_ROWS,))
            con.commit()
    finally:
        if own:
            con.close()


def records(after=None, limit=100, hltv_match=None, con=None):
    """Logged records, oldest first after `after` (logged_at|id cursor), at most 200."""
    limit = max(1, min(int(limit or 100), 200))
    own = con is None
    con = con or _db()
    try:
        q, args = "SELECT logged_at, id, body FROM veto_records", []
        cond = []
        if hltv_match:
            cond.append("hltv_match = ?")
            args.append(int(hltv_match))
        if after:
            la, _, rid = str(after).partition("|")
            cond.append("(logged_at > ? OR (logged_at = ? AND id > ?))")
            args += [la, la, rid]
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY logged_at, id LIMIT ?"
        rows = con.execute(q, args + [limit]).fetchall()
        out = [json.loads(zlib.decompress(r["body"])) for r in rows]
        nxt = f"{rows[-1]['logged_at']}|{rows[-1]['id']}" if len(rows) == limit else None
        return {"records": out, "next": nxt, "count": len(out)}
    finally:
        if own:
            con.close()


def replay(rec, table=None):
    """Re-run a logged record on the table it was made on (archived by cutoff + sha). Returns (same, re-run record)."""
    table = table or archived_table(rec["table"]["cutoff"], rec["table"]["sha"])
    if table is None:
        raise LookupError("the table this record was made on is no longer archived")
    out = predict_inputs(rec["inputs"], table)
    again = make_record(rec["inputs"], out, rec["table"], rec["state"], _utc(rec["logged_at"]))
    return again["id"] == rec["id"], again


# ---------------------------------------------------------------- frozen-simulator baseline (BO3, saved before the match)
BASELINE_EVERY_S = 6 * 3600


def save_baselines(now=None, limit=40):
    """hltv.predict_maps (the frozen 50/50-blended simulator the display used until now) for upcoming BO3 matches with a
    veto-1 forecast, once per match + pool + day, so the two are compared prospectively. Background only, bounded."""
    now = now or _now()
    try:
        import hltv
    except Exception:
        return 0
    with _LOCK:
        items = [(mid, e) for mid, e in _STORE.items() if e.get("record_id") and e.get("state") == "pending"
                 and e.get("start") and now < e["start"] <= now + dt.timedelta(hours=24)]
    con = _db()
    n = 0
    try:
        for mid, e in items[:limit]:
            hid = int(mid.split(":", 1)[1])
            try:
                rec = records(hltv_match=hid, limit=200, con=con)["records"]
            except Exception:
                continue
            last = next((r for r in reversed(rec) if r["id"] == e["record_id"]), None)
            if not last or last["inputs"]["format"] != "bo3":
                continue
            pool = last["inputs"]["pool"]
            day = now.date().isoformat()
            if con.execute("SELECT 1 FROM veto_baselines WHERE hltv_match = ? AND day = ? AND pool = ?",
                           (hid, day, ",".join(pool))).fetchone():
                continue
            a, b = last["inputs"]["teams"]["a"]["name"], last["inputs"]["teams"]["b"]["name"]
            try:
                pr = hltv.predict_maps(a, b, day, pool_keys=pool)
            except Exception:
                continue                       # transient: tried again next pass
            body = {"version": "hltv.predict_maps (frozen 50/50 blend)", "map1": (pr or {}).get("map1"),
                    "map2": (pr or {}).get("map2"), "history": (pr or {}).get("history"), "covered": bool(pr)}
            with _DB_LOCK:
                con.execute("INSERT OR IGNORE INTO veto_baselines VALUES (?,?,?,?,?,?)",
                            (hid, day, ",".join(pool), _iso(now), last["inputs"]["match_start"], json.dumps(body)))
                con.commit()
            n += 1
            time.sleep(YIELD_S)
    finally:
        con.close()
    return n


# ---------------------------------------------------------------- prospective score (admin)
FLOOR = 1e-9
_SCORE = {"at": 0.0, "body": None, "busy": False}
SCORE_TTL = 600


def _ll(d, m):
    return -math.log(max((d or {}).get(m, 0.0), FLOOR))


def _frozen_pair(p1, p2, pool):
    pair = {}
    for a in pool:
        for b in pool:
            if a != b:
                pair[(a, b)] = p1.get(a, 0) * p2.get(b, 0) / max(1 - p2.get(a, 0), 1e-12)
    z = sum(pair.values()) or 1.0
    return {k: v / z for k, v in pair.items()}


def _boot(diffs, B=2000, seed=20261023):
    if not diffs:
        return None
    rnd = random.Random(seed)
    n = len(diffs)
    ms = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(B))
    return [round(ms[int(0.025 * B)], 4), round(ms[int(0.975 * B) - 1], 4)]


def score_report(actual_fn=None, now=None, days=60, max_matches=3000):
    """Prospective veto-1 accuracy on settled matches: for each match the LAST pre-start record logged while the veto
    was pending, against HLTV's stored veto; the frozen simulator (veto_baselines, the last pre-start one) on the same
    matches. Log-loss per target, mean and match-level bootstrap 95% CI of (frozen - veto-1) on the common matches."""
    now = now or _now()
    con = _db()
    try:
        rows = con.execute("SELECT hltv_match, logged_at, body FROM veto_records WHERE pre_start = 1 AND state = 'pending' "
                           "AND logged_at >= ? ORDER BY logged_at", (_iso(now - dt.timedelta(days=days)),)).fetchall()
        last = {}
        for r in rows:
            last[r["hltv_match"]] = r
        base = {}
        for r in con.execute("SELECT hltv_match, logged_at, match_start, body FROM veto_baselines ORDER BY logged_at"):
            if r["match_start"] and r["logged_at"] < r["match_start"]:
                base[r["hltv_match"]] = json.loads(r["body"])
    finally:
        con.close()
    ids = sorted(last)[-max_matches:]
    act = (actual_fn or actual_vetoes)(ids)
    per = collections.defaultdict(list)
    cmp_ = collections.defaultdict(list)
    n_fmt = collections.Counter()
    for hid in ids:
        a = act.get(hid)
        if not a:
            continue
        rec = json.loads(zlib.decompress(last[hid]["body"]))
        fmt = rec["inputs"]["format"]
        if a["fmt"] != fmt or len(a["seq"]) != vm.N_MAPS[fmt]:
            continue
        n_fmt[fmt] += 1
        o = rec["output"]
        labs = {"bo1": ["map1"], "bo3": ["map1", "map2", "decider"], "bo5": ["map1", "map2", "map3", "map4", "decider"]}[fmt]
        for i, lab in enumerate(labs):
            per[(fmt, lab)].append(_ll(o["maps"].get(lab), a["seq"][i]))
        sets = {tuple(s): p for s, p in o["sets"]}
        per[(fmt, "map set")].append(_ll(sets, tuple(sorted(a["seq"]))))
        if o.get("pairs"):
            pairs = {tuple(s): p for s, p in o["pairs"]}
            per[(fmt, "map1+map2")].append(_ll(pairs, tuple(a["seq"][:2])))
        bl = base.get(hid)
        if fmt == "bo3" and bl and bl.get("covered"):
            pool = rec["inputs"]["pool"]
            p1, p2 = bl["map1"] or {}, bl["map2"] or {}
            for lab, mine, theirs in (("map1", _ll(o["maps"]["map1"], a["seq"][0]), _ll(p1, a["seq"][0])),
                                      ("map2", _ll(o["maps"]["map2"], a["seq"][1]), _ll(p2, a["seq"][1])),
                                      ("map1+map2", _ll({tuple(s): p for s, p in o["pairs"]}, tuple(a["seq"][:2])),
                                       _ll(_frozen_pair(p1, p2, pool), tuple(a["seq"][:2])))):
                cmp_[lab].append((mine, theirs))
    mean = lambda v: round(sum(v) / len(v), 4) if v else None
    out = {"version": vm.version(), "serve": SERVE_VERSION, "at": _iso(now), "window_days": days,
           "settled_matches": dict(n_fmt), "forecast_matches": len(ids),
           "rule": "last pre-start record while the veto was pending, per HLTV match; log-loss, lower is better",
           "veto1": {f"{f} {t}": {"n": len(v), "logloss": mean(v)} for (f, t), v in sorted(per.items())},
           "vs_frozen_bo3": {}}
    for lab, pairs in cmp_.items():
        d = [t - m for m, t in pairs]
        out["vs_frozen_bo3"][lab] = {"n": len(pairs), "veto1": mean([m for m, _ in pairs]), "frozen": mean([t for _, t in pairs]),
                                     "frozen_minus_veto1": mean(d), "ci95": _boot(d)}
    return out


def actual_vetoes(ids):
    """{hltv match id: {"fmt", "seq": played order (picks then leftover)}} for complete stored vetoes (bounded query)."""
    if not ids:
        return {}
    out = {}
    con = _hltv_conn()
    try:
        with con.cursor() as cur:
            mname = _maps_table(cur)
            for i in range(0, len(ids), 500):
                cur.execute("SELECT matchid, step, action, mapid FROM map_vetoes WHERE matchid = ANY(%s)", (list(ids[i:i + 500]),))
                by = collections.defaultdict(list)
                for mid, st, act, mp in cur.fetchall():
                    by[mid].append((st, act, mname.get(mp, str(mp))))
                for mid, v in by.items():
                    v.sort()
                    kinds = "".join({"removed": "r", "picked": "p", "leftover": "L"}.get(a, "?") for _, a, _ in v)
                    fmt = vm.fmt_of(kinds, [s for s, _, _ in v])
                    if fmt and len(v) == 7 and len({m for _, _, m in v}) == 7:
                        out[mid] = {"fmt": fmt, "seq": [m for _, a, m in v if a == "picked"] + [v[-1][2]]}
    finally:
        con.close()
    return out


def cached_score(force=False):
    if not force and _SCORE["body"] and time.time() - _SCORE["at"] < SCORE_TTL:
        return _SCORE["body"]
    with _LOCK:
        if _SCORE["busy"]:
            return _SCORE["body"] or {"busy": True}
        _SCORE["busy"] = True
    try:
        body = score_report()
        _SCORE.update(at=time.time(), body=body)
        return body
    except Exception as e:                    # noqa: BLE001 - a failed report is not cached
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        _SCORE["busy"] = False


# ====================================================================== status / admin / thread
def status():
    with _LOCK:
        t = STATE["table"]
        s = {"enabled": enabled(), "version": vm.version(), "serve": SERVE_VERSION, "table_loaded": t is not None,
             "meta": STATE["meta"], "loaded_at": STATE["loaded_at"], "error": STATE["error"], "build": STATE["build"],
             "builds": STATE["builds"], "build_running": STATE["build_running"],
             "next_build_retry_in_s": max(0, round(STATE["next_build_at"] - time.time())) if STATE["next_build_at"] else 0,
             "cycle": STATE["cycle"], "store": len(_STORE), "wanted": len(_WANT),
             "states": dict(collections.Counter(e.get("state") or ("problem" if e.get("problem") else None) for e in _STORE.values())),
             "pools_today": len(STATE["pools_today"]), "pools_error": STATE["pools_error"],
             "known_matches": len(STATE["known"]), "known_error": STATE["known_error"],
             "table_teams": len(t.team) if t else 0, "recent_pool": (STATE["table_pools"] or {}).get("recent")}
    return s


def admin(q):
    act = (q.get("action") or "status").lower()
    if act == "status":
        return status()
    if act == "records":
        return records(q.get("after"), q.get("limit") or 100, q.get("match") or None)
    if act == "score":
        return cached_score(force=q.get("refresh") == "1")
    if act == "store":
        with _LOCK:
            items = sorted(_STORE.items(), key=lambda x: str(x[1].get("start") or ""))[:200]
        return {"matches": [{"match_id": k, **{kk: (str(v) if kk == "start" else v) for kk, v in e.items() if kk != "payload"}}
                            for k, e in items]}
    if act == "rebuild":
        with _LOCK:
            STATE["force"], STATE["next_build_at"] = True, 0.0
        return {"queued": True}
    return {"error": "action = status | records | score | store | rebuild"}


def loop_once(now=None):
    now = now or _now()
    ensure_table(now)
    refresh_today_pools(now)
    refresh_known(now)
    c = cycle(now)
    try:
        save_baselines(now)
    except Exception:
        traceback.print_exc()
    return c


def forever():
    time.sleep(30)                            # after the boot work
    last_prune = 0.0
    while True:
        try:
            loop_once()
            if time.time() - last_prune > 86400:
                prune_log()
                last_prune = time.time()
        except Exception:
            traceback.print_exc()
        time.sleep(LOOP_S)


def start():
    """Start the background thread once (VETO_SERVE=0 turns it off)."""
    if not enabled():
        return False
    with _LOCK:
        if STATE["thread"] is not None and STATE["thread"].is_alive():
            return False
        th = threading.Thread(target=forever, daemon=True, name="veto-serve")
        STATE["thread"] = th
    th.start()
    return True


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        args = sys.argv[2:]
        kw = {}
        for i in range(0, len(args) - 1, 2):
            kw[args[i].lstrip("-")] = args[i + 1]
        build_cli(kw.get("out") or str(TABLE_FILE), kw.get("cutoff"), int(kw.get("days") or TABLE_DAYS))
    else:
        print("usage: python veto_serve.py build [--out PATH] [--cutoff YYYY-MM-DD] [--days N]")
