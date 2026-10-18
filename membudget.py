"""Memory-bounded caches for the web process (2026-10-18: Render killed the service three times for using over 2 GB -
the in-memory BO3.gg caches were bounded by entry COUNT, and one cached BO3.gg response can take ~170 KB as Python
objects, so 20,000-40,000 entries could exceed the whole container). Everything held here also lives on disk (the
packed cache) or is recomputed on a miss, so evicting an entry only costs a re-read - never data."""
import json
import os
import threading
import time
from collections import OrderedDict, deque

# measured 2026-10-18 on the production cache: Python objects take ~3.2-4.4x the JSON text (27.9 KB vs 6.4 KB for a map
# scoreboard, 172.6 KB vs 54.2 KB for a BO3.gg list)
PY_OVER_JSON = 4.0


def json_cost(data):
    """Approximate bytes `data` occupies as Python objects (from its JSON length)."""
    try:
        return int(len(json.dumps(data, separators=(",", ":"), default=str)) * PY_OVER_JSON) + 200
    except (TypeError, ValueError):
        return 50_000


class BudgetLRU:
    """A dict-like LRU cache bounded by total approximate bytes (and by entry count). Thread-safe."""

    def __init__(self, name, max_mb, max_items=None, cost=json_cost):
        self.name, self.max_bytes, self.max_items, self.cost = name, int(max_mb * 1e6), max_items, cost
        self._d = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self.evictions = 0

    def get(self, key, default=None):
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                return default
            self._d.move_to_end(key)
            return hit[0]

    def __contains__(self, key):
        with self._lock:
            return key in self._d

    def __getitem__(self, key):
        v = self.get(key, _MISSING)
        if v is _MISSING:
            raise KeyError(key)
        return v

    def __setitem__(self, key, value):
        self.put(key, value)

    def put(self, key, value, cost=None):
        c = self.cost(value) if cost is None else cost
        if c > self.max_bytes:
            return                                   # bigger than the whole budget: not cached (read from disk each time)
        with self._lock:
            old = self._d.pop(key, None)
            if old is not None:
                self._bytes -= old[1]
            self._d[key] = (value, c)
            self._bytes += c
            while self._d and (self._bytes > self.max_bytes or (self.max_items and len(self._d) > self.max_items)):
                _, (_, oc) = self._d.popitem(last=False)
                self._bytes -= oc
                self.evictions += 1

    def pop(self, key, default=None):
        with self._lock:
            old = self._d.pop(key, None)
            if old is None:
                return default
            self._bytes -= old[1]
            return old[0]

    def clear(self):
        with self._lock:
            self._d.clear()
            self._bytes = 0

    def __len__(self):
        return len(self._d)

    def items(self):
        with self._lock:
            return [(k, v[0]) for k, v in self._d.items()]

    def keys(self):
        with self._lock:
            return list(self._d.keys())

    def stats(self):
        return {"entries": len(self._d), "mb": round(self._bytes / 1e6, 1), "budget_mb": round(self.max_bytes / 1e6),
                "evictions": self.evictions}


_MISSING = object()


def env_mb(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


# memory.stat fields reported beside anon / file (cgroup v2 names, v1 names where they differ) - what part of the page cache
# can be dropped at once (inactive, clean) and what cannot (dirty / under writeback, shared memory, kernel memory)
_DETAIL = (("active_file", "active_file"), ("inactive_file", "inactive_file"), ("file_dirty", "dirty"), ("dirty", "dirty"),
           ("file_writeback", "writeback"), ("writeback", "writeback"), ("shmem", "shmem"), ("slab_reclaimable", "slab_reclaimable"),
           ("slab_unreclaimable", "slab_unreclaimable"), ("sock", "sock"), ("kernel", "kernel"))


def _cgroup_extra(base, v2, out):
    """The container's true peak, how often it reached its limit, and memory-pressure stall time (2026-10-18: a 2,100 MB
    total of a 2,147 MB limit - is the page cache being reclaimed at the limit, and does that stall the processes?)."""
    try:
        out["container_peak_mb"] = round(int(open(os.path.join(base, "memory.peak" if v2 else "memory.max_usage_in_bytes")).read()) / 1e6, 1)
    except (OSError, ValueError):
        pass
    try:
        if v2:      # low / high / max = times usage hit that boundary (max: the kernel had to reclaim at the limit); oom_kill
            out["container_events"] = {k: int(v) for k, v in (l.split() for l in open(os.path.join(base, "memory.events")) if len(l.split()) == 2)}
        else:
            out["container_events"] = {"failcnt": int(open(os.path.join(base, "memory.failcnt")).read())}
    except (OSError, ValueError):
        pass
    try:            # pressure stall information: share of time some / all tasks waited on memory (avg over 10 s / 60 s / 300 s)
        ps = {}
        for line in open(os.path.join(base, "memory.pressure")):
            kind, *kv = line.split()
            for x in kv:
                k, v = x.split("=")
                if k in ("avg10", "avg60", "avg300"):
                    ps[f"{kind}_{k}"] = float(v)
        out["container_pressure"] = ps
    except (OSError, ValueError):
        pass


def process_memory():
    """{"server_rss_mb", "container_mb", "container_limit_mb", "container_anon_mb", "container_file_cache_mb",
    "container_detail_mb", "container_peak_mb", "container_events", "container_pressure"} - the whole container (cgroup) as
    Render counts it, not only this process. The page cache is not guaranteed headroom: dirty pages, pages under writeback
    and shared memory cannot be dropped at once."""
    out = {}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    out["server_rss_mb"] = round(int(line.split()[1]) / 1024, 1)
    except OSError:
        try:
            import psutil
            out["server_rss_mb"] = round(psutil.Process().memory_info().rss / 1e6, 1)
        except Exception:
            pass
    for cur, lim in (("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
                     ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            out["container_mb"] = round(int(open(cur).read().strip()) / 1e6, 1)
            v = open(lim).read().strip()
            out["container_limit_mb"] = None if v == "max" else round(int(v) / 1e6, 1)
            try:                                      # anon = process memory; file = page cache (only partly reclaimable)
                st = dict(l.split() for l in open(os.path.join(os.path.dirname(cur), "memory.stat")) if len(l.split()) == 2)
                for k, name in (("anon", "container_anon_mb"), ("file", "container_file_cache_mb"), ("rss", "container_anon_mb"),
                                ("cache", "container_file_cache_mb")):
                    if k in st and name not in out:
                        out[name] = round(int(st[k]) / 1e6, 1)
                det = {}
                for k, name in _DETAIL:
                    if k in st and name not in det:
                        det[name] = round(int(st[k]) / 1e6, 1)
                out["container_detail_mb"] = det
            except (OSError, ValueError):
                pass
            _cgroup_extra(os.path.dirname(cur), cur.endswith("memory.current"), out)
            break
        except (OSError, ValueError):
            continue
    return out


def rss_of(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        return None


def processes():
    """Resident memory of every process in the container by role: web (this process), lol (the LoL worker), learning (the
    learning cycle's child), other. Linux /proc only; {} elsewhere."""
    out, me = {}, os.getpid()
    try:
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return out
    for d in pids:
        try:
            cmd = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        rss = rss_of(int(d))
        if not rss:
            continue
        role = ("web" if int(d) == me else "lol" if "lol.center.worker" in cmd else "learning" if "learning.py" in cmd
                else "other")
        out[role] = round(out.get(role, 0) + rss, 1)
    return out


# One compact sample a minute for the last 24 h, kept in the server itself (2026-10-18: the observation of a rebuild's
# memory must not depend on someone polling /health at the right minute) + the highest total / process memory seen.
HISTORY = deque(maxlen=1440)
PEAK = {}


def sample():
    m = process_memory()
    s = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "total": m.get("container_mb"), "anon": m.get("container_anon_mb"),
         "file": m.get("container_file_cache_mb"), "dirty": (m.get("container_detail_mb") or {}).get("dirty"),
         "inactive_file": (m.get("container_detail_mb") or {}).get("inactive_file"), "peak": m.get("container_peak_mb"),
         "max_events": (m.get("container_events") or {}).get("max"), "full_avg60": (m.get("container_pressure") or {}).get("full_avg60")}
    s.update({f"rss_{k}": v for k, v in processes().items()})
    HISTORY.append(s)
    for k in ("total", "anon"):
        if s.get(k) is not None and s[k] > (PEAK.get(k) or {}).get("mb", -1.0):
            PEAK[k] = {"mb": s[k], "at": s["t"]}
    return s


ALLOC = {"arena_max": None, "trims": 0, "last_trim_freed_mb": None, "platform": None}


def tune_allocator(arenas=2, trim_every=60):
    """glibc malloc on Linux (Render): many threads each get their own malloc arena, and memory freed by the bounded
    caches stays in those arenas instead of returning to the OS - the process's resident memory kept climbing while
    every cache stayed at its budget (2026-10-18 measurements). Cap the arenas (before the server's threads start) and
    hand freed memory back every `trim_every` seconds. No-op off Linux/glibc. Changes no data and no computation."""
    import ctypes
    try:
        libc = ctypes.CDLL("libc.so.6")
    except OSError:
        ALLOC["platform"] = "no glibc - not tuned"
        return False
    M_ARENA_MAX = -8
    ALLOC["arena_max"] = arenas if libc.mallopt(M_ARENA_MAX, arenas) == 1 else None
    ALLOC["platform"] = "glibc"

    def trim():
        import gc
        n = 0
        while True:
            time.sleep(trim_every)
            n += 1
            try:
                sample()
            except Exception:                              # noqa: BLE001 - a measurement never stops the trims
                pass
            before = process_memory().get("server_rss_mb")
            if n % 5 == 0:                                 # full collection every 5 min: reference cycles (failed futures,
                ALLOC["gc_freed"] = gc.collect()           # exceptions with tracebacks) otherwise wait for a rare gen-2 pass
            libc.malloc_trim(0)
            after = process_memory().get("server_rss_mb")
            ALLOC["trims"] += 1
            if before is not None and after is not None:
                ALLOC["last_trim_freed_mb"] = round(before - after, 1)
    threading.Thread(target=trim, daemon=True, name="malloc-trim").start()
    return True
