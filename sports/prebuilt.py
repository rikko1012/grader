"""Prebuilt answer snapshots: heavy shared answers are built by the WORKER side (a scheduled child process, never a
member request) and read by the service through web.cached - so the first member after a restart or a TTL expiry
gets the answer in microseconds instead of running a multi-second build (slips pool: ~2 s cold, measured by the slips
agent 2026-10-23).

Contract (sports/web.py SNAPSHOTS table, one line per snapshot):
    "<name>": ("<module>:<function>", refresh_s, max_age_s)
  function(con) -> {cache_key_tuple: value}; cache keys are exactly the keys the route handler passes to web.cached,
  and key[0] == <name>. web.cached(key, fn) returns the prebuilt value when the snapshot holds that key and is at most
  max_age_s old; otherwise it falls back to its normal single-flight load (counted as a miss), so a stopped worker
  degrades to the old behaviour, never to an error.

Files: <SPORTS_DATA_DIR>/snapshots/<name>.snap = HMAC-SHA256 tag + pickle, written atomically (tmp + os.replace).
Pickle keeps the handler's exact types (tuples, sets); the tag key derives from SPORTS_TOKEN_SECRET (environment, never
on disk), so a file not written by our own worker is never unpickled. No secret -> snapshots off (fallback only).

    python -m sports.service snapshots [name ...]     # build now (the service's scheduler runs it every refresh_s)
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import os
import pickle
import threading
import time
from pathlib import Path

from . import config

MISS = object()
STATS = {"hits": 0, "misses": 0, "stale": 0, "bad_tag": 0, "read_errors": 0}
_MEM = {}                    # name -> (mtime_ns, size, obj)
_LOCK = threading.Lock()
TAG = 32


def snap_dir() -> Path:
    return config.DATA_DIR / "snapshots"


def _key():
    s = os.environ.get("SPORTS_TOKEN_SECRET", "")
    return hashlib.sha256(b"scout-sports-prebuilt|" + s.encode()).digest() if len(s) >= 32 else None


def _table():
    from . import web
    return web.SNAPSHOTS


def write(name, items, built_ms=None):
    key = _key()
    if key is None:
        raise RuntimeError("SPORTS_TOKEN_SECRET not set: snapshots are off")
    data = pickle.dumps({"name": name, "built_at": time.time(), "built_ms": built_ms, "items": items}, protocol=5)
    d = snap_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{name}.{os.getpid()}.tmp"
    tmp.write_bytes(hmac.new(key, data, hashlib.sha256).digest() + data)
    os.replace(tmp, d / f"{name}.snap")
    return len(data)


def read(name):
    """-> snapshot dict or None (missing / bad tag / unreadable). Memory-cached by file mtime + size."""
    key = _key()
    if key is None:
        return None
    f = snap_dir() / f"{name}.snap"
    try:
        st = f.stat()
    except OSError:
        return None
    with _LOCK:
        hit = _MEM.get(name)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
    try:
        raw = f.read_bytes()
    except OSError:
        with _LOCK:
            STATS["read_errors"] += 1
        return None
    if len(raw) <= TAG or not hmac.compare_digest(raw[:TAG], hmac.new(key, raw[TAG:], hashlib.sha256).digest()):
        with _LOCK:
            STATS["bad_tag"] += 1
        return None
    obj = pickle.loads(raw[TAG:])                       # noqa: S301 - tag verified with a key that is not on disk
    with _LOCK:
        _MEM[name] = (st.st_mtime_ns, st.st_size, obj)
    return obj


def lookup(key):
    """web.cached's first step: the prebuilt value for this cache key, or MISS."""
    if not isinstance(key, tuple) or not key:
        return MISS
    entry = _table().get(key[0])
    if entry is None:
        return MISS
    snap = read(key[0])
    if snap is None or key not in snap["items"]:
        with _LOCK:
            STATS["misses"] += 1
        return MISS
    if time.time() - snap["built_at"] > entry[2]:
        with _LOCK:
            STATS["stale"] += 1
        return MISS
    with _LOCK:
        STATS["hits"] += 1
    return snap["items"][key]


def state():
    out = {"enabled": _key() is not None, "stats": dict(STATS), "snapshots": {}}
    for name, (target, refresh_s, max_age_s) in _table().items():
        s = read(name)
        out["snapshots"][name] = ({"age_s": round(time.time() - s["built_at"], 1), "keys": len(s["items"]),
                                   "built_ms": s.get("built_ms"), "refresh_s": refresh_s, "max_age_s": max_age_s}
                                  if s else {"age_s": None, "refresh_s": refresh_s, "max_age_s": max_age_s})
    return out


def build(names=None):
    """Worker side: build each snapshot from a read-only store connection and write it. -> report."""
    from . import store
    rep = {}
    for name, (target, refresh_s, max_age_s) in _table().items():
        if names and name not in names:
            continue
        t0 = time.time()
        try:
            mod, _, fn = target.partition(":")
            with store.opened(read_only=True) as con:
                items = getattr(importlib.import_module(mod), fn)(con)
            ms = round((time.time() - t0) * 1000)
            size = write(name, items, ms)
            rep[name] = {"ok": True, "keys": len(items), "ms": ms, "bytes": size}
        except FileNotFoundError:
            rep[name] = {"ok": False, "error": "sports_store_not_ready"}
        except Exception as e:                          # noqa: BLE001 - one snapshot's failure is its own
            rep[name] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    return rep


# --------------------------------------------------------------------------- snapshot builders (adapters)
def slips_pool(con):
    """The slips agent's member leg pool for every (league, platform), under the exact cache keys api_slips._pool
    uses: ("slips_pool", league, platform, "member"). Calls the slips module's public pool.build; admin "research"
    pools stay on-demand."""
    from .slips import pool, rules
    return {("slips_pool", lg, plat, "member"): pool.build(con, lg, plat, policy="member")
            for lg in ("nba", "nfl") for plat in rules.PLATFORMS}
