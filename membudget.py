"""Memory-bounded caches for the web process (2026-10-18: Render killed the service three times for using over 2 GB -
the in-memory BO3.gg caches were bounded by entry COUNT, and one cached BO3.gg response can take ~170 KB as Python
objects, so 20,000-40,000 entries could exceed the whole container). Everything held here also lives on disk (the
packed cache) or is recomputed on a miss, so evicting an entry only costs a re-read - never data."""
import json
import os
import threading
from collections import OrderedDict

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


def process_memory():
    """{"server_rss_mb", "children": {pid: rss_mb}, "container_mb", "container_limit_mb"} - the whole container (cgroup)
    as Render counts it, not only this process."""
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
            try:                                      # anon = what an out-of-memory kill counts; file = reclaimable cache
                st = dict(l.split() for l in open(os.path.join(os.path.dirname(cur), "memory.stat")) if len(l.split()) == 2)
                for k, name in (("anon", "container_anon_mb"), ("file", "container_file_cache_mb"), ("rss", "container_anon_mb"),
                                ("cache", "container_file_cache_mb")):
                    if k in st and name not in out:
                        out[name] = round(int(st[k]) / 1e6, 1)
            except (OSError, ValueError):
                pass
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
