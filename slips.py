"""Auto slip builder: the server copy of the board's slip builder (web/index.html, buildSlips) plus its two logs.

- slips_recommended.jsonl: every distinct slip the builder recommends ("Highest hit chance", 2-4 picks), logged
  once, the first time it appears. A fixed, deduplicated set - this is what scores the builder itself.
- slips_loaded.jsonl: slips members loaded. Retries are dropped by the client's load id; many members loading
  the same slip share one `key`, so it counts once when scoring.
Both live in DATA_DIR (the Railway volume), are appended under a lock and fsynced, so they survive restarts.
"""
import json
import os
import threading
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

from bo3 import DATA_DIR

BUILDER_VERSION = "slips-6"          # keep in step with BUILDER_V in web/index.html
GRADES = {"A+", "A", "A-", "B+", "B", "B-"}
RECOMMENDED = Path(DATA_DIR) / "slips_recommended.jsonl"
LOADED = Path(DATA_DIR) / "slips_loaded.jsonl"
_LOCK = threading.Lock()
_SEEN = {"rec": None, "load": None}


def slip_key(legs):
    """One slip = its picks (board id, line, side), order ignored."""
    return "|".join(sorted(f"{l['id']}:{l['line']}:{l['side']}" for l in legs))


def _legs(rows, now_iso):
    """Bettable picks: B- or better with units, not started; best 2 per match, top 14 matches."""
    by = {}
    for r in rows:
        if (not r.get("side") or r.get("p") is None or r.get("state") == "SCOUT" or r.get("grade") not in GRADES
                or (r.get("units") or 0) <= 0 or (r.get("start") and r["start"] <= now_iso)
                # role-sensitive props need a confirmed role: HS only for confirmed non-AWPers, kills only for confirmed non-IGLs
                or (r.get("prop") == "hs" and r.get("awp") is not False) or (r.get("prop") == "kills" and r.get("igl") is not False)):
            continue
        m = (r.get("matchup") or f"{r.get('team')}|{r.get('opponent')}") + "|" + (r.get("start") or "")
        by.setdefault(m, []).append(dict(r, _m=m, _nick=(r.get("nick") or r.get("player") or "").lower()))
    groups = [sorted(g, key=lambda x: -x["p"])[:2] for g in by.values()]
    return sorted(groups, key=lambda g: -g[0]["p"])[:16]


def build_all(rows):
    """The page's default view for each size, built independently: Mixed sides, up to 3 slips, no match
    in two slips of that size (too few matches = fewer slips). AWPers' headshot lines and IGLs' kill
    lines (and unknown roles) are left out."""
    return {n: build(rows, n, set()) for n in (2, 3, 4)}


def build(rows, n, used=None):
    """Up to 3 n-pick slips ranked by estimated all-hit chance; one pick per match and per player;
    no match in `used` (updated with the matches taken)."""
    used = set() if used is None else used
    now_iso = datetime.now(timezone.utc).isoformat()
    groups, out = _legs(rows, now_iso), []
    for gs in combinations(groups, n):
        def rec(i, cur):
            if i == n:
                if len({x["_nick"] for x in cur}) == n:
                    p = 1.0
                    for x in cur:
                        p *= x["p"]
                    out.append((p, list(cur)))
                return
            for x in gs[i]:
                rec(i + 1, cur + [x])
        rec(0, [])
    out.sort(key=lambda s: -s[0])
    picked = []
    for p, legs in out:
        if len(picked) >= 3:
            break
        ms = {x["_m"] for x in legs}
        if not ms & used:
            picked.append((p, legs))
            used |= ms
    return picked


def _append(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _seen(kind, path, field):
    if _SEEN[kind] is None:
        s = set()
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        s.add(json.loads(line)[field])
                    except (ValueError, KeyError, TypeError):
                        pass
        except OSError:
            pass
        _SEEN[kind] = s
    return _SEEN[kind]


def _leg(r):
    return {k: r.get(k) for k in ("id", "nick", "player", "prop", "scope", "line", "side", "p", "grade",
                                  "matchup", "team", "opponent", "start")}


def log_recommended(rows, grader_version):
    """Log every new recommended slip (first appearance only). Returns how many were new."""
    new = 0
    with _LOCK:
        seen = _seen("rec", RECOMMENDED, "key")
        for n, built in build_all(rows).items():
            for rank, (p, legs) in enumerate(built, 1):
                ls = [_leg(x) for x in legs]
                key = slip_key(ls)
                if key in seen:
                    continue
                seen.add(key)
                _append(RECOMMENDED, {"key": key, "n": n, "rank": rank, "all": p, "builder": BUILDER_VERSION,
                                      "grader": grader_version, "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "legs": ls})
                new += 1
    return new


def log_loaded(d):
    """A member loaded a slip. Returns False for a retry (same load id)."""
    cid = str(d.get("cid") or "")[:64]
    if not cid:
        raise ValueError("no load id")
    legs = d["legs"]
    rec = {"cid": cid, "key": slip_key(legs), "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           **{k: d.get(k) for k in ("builder", "grader", "board_at", "mode", "n", "all", "pay", "pay_source")},
           "legs": [{k: l.get(k) for k in ("id", "nick", "prop", "scope", "line", "side", "p", "grade", "matchup", "start", "mult")}
                    for l in legs]}
    with _LOCK:
        seen = _seen("load", LOADED, "cid")
        if cid in seen:
            return False
        seen.add(cid)
        _append(LOADED, rec)
    return True


def read(path, last=5000):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-last:]
    except OSError:
        return []
    return [json.loads(x) for x in lines if x.strip()]
