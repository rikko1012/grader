"""Why did the sports service restart? Small durable records so the next restart names itself (2026-11-18: the
service restarted at ~16:09 UTC with no deploy and nothing on disk said why).

  <DATA_DIR>/service_life/last_exit.json   written (fsync) by the memory watchdog just before os._exit(3)
  <DATA_DIR>/service_life/restarts.jsonl   one line per boot: commit, the previous exit record or "unclean" (no record:
                                           OOM kill, crash, host restart), cgroup memory.events / memory.peak, and the
                                           last service RSS samples before the restart; last 50 kept
  <DATA_DIR>/service_life/service_rss.json per-minute ring (2 h) of service RSS + cgroup memory.current, saved every 5 min

Every function swallows its own errors: a full disk or a missing /sys file never stops the watchdog reaching os._exit.
"""
from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from collections import deque

RING_MINUTES = 120
SAVE_EVERY_S = 300
KEEP_BOOTS = 50
_RING = deque(maxlen=RING_MINUTES)
_LOCK = threading.Lock()
_LAST = {"minute": None, "saved": 0.0}
CGROUP = "/sys/fs/cgroup"


def _dir():
    from . import config
    d = config.DATA_DIR / "service_life"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path, obj):
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _utc(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t or time.time()))


def cgroup():
    """cgroup v2 memory counters of this container (oom / oom_kill / max events, peak, current) in MB; {} off Linux."""
    out = {}
    try:
        with open(f"{CGROUP}/memory.events") as f:
            out["events"] = {k: int(v) for k, v in (line.split() for line in f if line.strip())}
    except (OSError, ValueError):
        pass
    for name in ("memory.peak", "memory.current", "memory.max"):
        try:
            with open(f"{CGROUP}/{name}") as f:
                v = f.read().strip()
            out[name.split(".")[1] + "_mb"] = None if v == "max" else round(int(v) / 1e6, 1)
        except (OSError, ValueError):
            pass
    # sg-stability 2026-11-20: anon vs page cache and pressure, so "at the limit, mostly page cache" is measured, not
    # assumed (anon is what an OOM kill follows; file cache is reclaimed)
    try:
        with open(f"{CGROUP}/memory.stat") as f:
            stat = dict(line.split()[:2] for line in f if line.strip())
        out["stat_mb"] = {k: round(int(stat[k]) / 1e6, 1) for k in STAT_KEYS if k in stat}
    except (OSError, ValueError):
        pass
    try:
        with open(f"{CGROUP}/memory.pressure") as f:
            out["pressure"] = {line.split()[0]: {k: float(v) for k, v in (x.split("=") for x in line.split()[1:])
                                                 if k != "total"} for line in f if line.strip()}
    except (OSError, ValueError, IndexError):
        pass
    return out


STAT_KEYS = ("anon", "file", "kernel", "shmem", "file_dirty", "file_writeback", "active_file", "inactive_file",
             "slab_reclaimable")


def trim():
    """Give freed heap pages back to the OS (glibc malloc_trim; no-op elsewhere). -> True when called."""
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
        return True
    except (OSError, AttributeError):
        return False


def sample(rss_mb, now=None):
    """Called every watchdog tick: keep one sample per minute, save the ring every SAVE_EVERY_S."""
    now = now or time.time()
    minute = int(now // 60)
    try:
        with _LOCK:
            if minute != _LAST["minute"]:
                _LAST["minute"] = minute
                cur = cgroup().get("current_mb")
                _RING.append({"at": _utc(now), "rss_mb": round(rss_mb or 0, 1), "cgroup_mb": cur})
            due = now - _LAST["saved"] >= SAVE_EVERY_S
            if due:
                _LAST["saved"] = now
                ring = list(_RING)
        if due:
            _write(_dir() / "service_rss.json", {"saved_at": _utc(now), "samples": ring})
    except Exception:                                   # noqa: BLE001 - never break the watchdog
        pass


def exit_record(reason, **detail):
    """The watchdog's last act before os._exit: what was running and how big things were."""
    try:
        with _LOCK:
            ring = list(_RING)[-15:]
        _write(_dir() / "last_exit.json", {"at": _utc(), "pid": os.getpid(), "reason": reason, **detail,
                                           "cgroup": cgroup(), "rss_tail": ring})
    except Exception:                                   # noqa: BLE001
        pass


def boot(version=None):
    """Append this boot's record (with the previous exit, or 'unclean' when the previous run left none)."""
    try:
        d = _dir()
        prev = None
        try:
            prev = json.loads((d / "last_exit.json").read_text(encoding="utf-8"))
            (d / "last_exit.json").unlink()
        except (OSError, ValueError):
            prev = None
        ring = None
        try:
            ring = json.loads((d / "service_rss.json").read_text(encoding="utf-8")).get("samples", [])[-15:]
        except (OSError, ValueError, AttributeError):
            pass
        rec = {"at": _utc(), "pid": os.getpid(), "version": version,
               "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:12] or None,
               "previous_exit": prev or "unclean", "previous_rss_tail": None if prev else ring, "cgroup": cgroup()}
        path = d / "restarts.jsonl"
        lines = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-(KEEP_BOOTS - 1):]
        except OSError:
            pass
        lines.append(json.dumps(rec, separators=(",", ":"), default=str))
        tmp = d / f".restarts.{os.getpid()}.tmp"
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return rec
    except Exception:                                   # noqa: BLE001
        return None


KEEP_HIGH = 20


def high_water(detail, now=None):
    """sweep 2026-11-23 SO-5: one record when service RSS passes the trim threshold (the watchdog rate-limits it), so a memory
    climb that ends in an rss_over_max restart names what was being served at its start, not only at the exit. Last 20 kept."""
    try:
        with _LOCK:
            ring = list(_RING)[-5:]
        rec = {"at": _utc(now), "pid": os.getpid(), **detail, "rss_tail": ring}
        path = _dir() / "rss_high.jsonl"
        lines = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-(KEEP_HIGH - 1):]
        except OSError:
            pass
        lines.append(json.dumps(rec, separators=(",", ":"), default=str))
        tmp = path.parent / f".rss_high.{os.getpid()}.tmp"
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return rec
    except Exception:                                   # noqa: BLE001 - never break the watchdog
        return None


def high_recent(n=5):
    """The last n high-water records, newest last (ops 'rss_high')."""
    try:
        lines = (_dir() / "rss_high.jsonl").read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]
    except (OSError, ValueError):
        return []


def recent(n=10):
    """The last n boot records, newest last (ops 'restarts')."""
    try:
        lines = (_dir() / "restarts.jsonl").read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]
    except (OSError, ValueError):
        return []
