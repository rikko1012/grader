"""Capture-only inventory for the UNVERIFIED market sources (thunderpick, oddspapi) - so a person can review what they
actually return for NBA / NFL and flip VERIFIED in sports/books/__init__.py SOURCES. Worker / operator only.

Nothing here parses an offer. A capture stores, per answer:
  - the raw answer, gzipped, under <SPORTS_DATA_DIR>/books/inventory/<source>/<league>/ (last KEEP per path kept)
  - its sha256 and byte size
  - a STRUCTURAL inventory: every JSON key path with its count and value types, the distinct values of name-like
    fields (market / selection / outcome names, <= 60 each), and numeric line-like fields (handicap / line / total /
    points) with their distinct values - read from the answer, never assumed
The latest summary per source + league is <...>/inventory/<source>_<league>.json; the board manifest's `sources`
block (sports/snapshots.py) carries its time, status and counts.

  python -m sports.books.cli inventory thunderpick nba     # capture now (needs THUNDERPICK_API_KEY), then print
  python -m sports.books.cli inventory oddspapi nba        # unmetered /v4/account only (shared CS2 account)
  python -m sports.books.cli inventory-show thunderpick nba  # print the stored summary, no request

What proves a PLAYER-PROP format (the review checklist, also printed): for one known NBA / NFL game, a market whose
name names a player stat (e.g. points / rebounds / passing yards), whose selections carry (a) the player's name,
(b) Over and Under as separate selections, (c) one numeric line shared by the pair, (d) a decimal (or American)
price per selection, and (e) the event's teams / start so it resolves to the ESPN game. All five must be visible in
the stored inventory before VERIFIED is flipped; the parser is then written against the stored raw answer.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
from pathlib import Path

from .. import config

KEEP = 3
NAME_KEYS = re.compile(r"(^|_)(name|label|title|market|selection|outcome|type|stat|period|participant)s?$", re.I)
LINE_KEYS = re.compile(r"(^|_)(handicap|line|total|points|value|hdp|spread)$", re.I)
CHECKLIST = ("market name names a player stat", "selection carries the player's name", "Over and Under are separate "
             "selections", "one numeric line shared by the pair", "a price per selection",
             "event teams / start resolve to the ESPN game")


def root():
    return Path(config.DATA_DIR) / "books" / "inventory"


def structure(body, max_values=60, max_depth=8):
    """JSON -> {paths: {path: {n, types}}, names: {path: [distinct values]}, lines: {path: [distinct values]}}."""
    paths, names, lines = {}, {}, {}

    def walk(x, path, d):
        t = type(x).__name__
        p = paths.setdefault(path or "$", {"n": 0, "types": []})
        p["n"] += 1
        if t not in p["types"]:
            p["types"].append(t)
        if d >= max_depth:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                key = str(k)
                sub = f"{path}.{key}" if path else key
                if isinstance(v, str) and NAME_KEYS.search(key):
                    s = names.setdefault(sub, [])
                    if v[:80] not in s and len(s) < max_values:
                        s.append(v[:80])
                if isinstance(v, (int, float)) and not isinstance(v, bool) and LINE_KEYS.search(key):
                    s = lines.setdefault(sub, [])
                    if v not in s and len(s) < max_values:
                        s.append(v)
                walk(v, sub, d + 1)
        elif isinstance(x, list):
            for v in x:
                walk(v, f"{path}[]", d + 1)
    walk(body, "", 0)
    return {"paths": paths, "names": names, "lines": lines}


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "root"


def save_answer(source, league, path, content: bytes, at=None):
    """Store one raw answer (gz) + prune to KEEP per path. -> (file, sha256)."""
    at = at or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    d = root() / source / league
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{at}_{_slug(path)}.json.gz"
    with gzip.open(f, "wb") as fh:
        fh.write(content)
    old = sorted(d.glob(f"*_{_slug(path)}.json.gz"))
    for x in old[:-KEEP]:
        x.unlink(missing_ok=True)
    return f, hashlib.sha256(content).hexdigest()


def summary_path(source, league):
    return root() / f"{source}_{league}.json"


def write_summary(source, league, summary):
    p = summary_path(source, league)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, p)
    return p


def read_summary(source, league):
    try:
        return json.loads(summary_path(source, league).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def capture(source, league, getter=None, key=None):
    """One capture of an unverified source. getter(path) -> (status, bytes) is injectable for tests; the default is
    sources._get through the ledger (Thunderpick) or the guarded unmetered account read (OddsPapi)."""
    from . import SOURCES
    from . import sources as srcs
    if source not in SOURCES:                       # Thunderpick removed from Sports (owner 2026-10-24)
        return {"source": source, "league": league, "status": "not a Sports source"}
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    env = SOURCES[source]["key_env"]
    key = key if key is not None else srcs.source_key(source)      # OddsPapi: its odds_papi_api alias too (#38)
    out = {"source": source, "league": league, "captured_at": at, "verified": SOURCES[source]["verified"],
           "checklist": list(CHECKLIST), "answers": []}
    if not key and getter is None:
        out["status"] = f"{env} not set in this environment"
        write_summary(source, league, out)
        return out
    if source == "thunderpick":
        def default_get(p):
            r = srcs._get("thunderpick", f"{srcs.TP_BASE}/{p}", headers={"api-key": key, "Accept": "application/json"})
            return r.status_code, r.content
        get = getter or default_get
        for p in srcs.TP_PATHS[league]:
            try:
                status, content = get(p)
            except Exception as e:                      # noqa: BLE001 - recorded per path, key redacted
                out["answers"].append({"path": p, "error": str(e).replace(key or "\0", "***")[:160]})
                continue
            f, sha = save_answer(source, league, p, content, at.replace("-", "").replace(":", ""))
            ans = {"path": p, "http_status": status, "bytes": len(content), "sha256": sha, "raw_file": str(f)}
            try:
                ans["structure"] = structure(json.loads(content))
            except ValueError:
                ans["structure"] = None
                ans["not_json"] = content[:200].decode("utf-8", "replace")
            out["answers"].append(ans)
        out["status"] = "captured (format not verified)"
    else:
        inv = srcs.capture("oddspapi", league)
        out["status"] = inv.get("status")
        out["answers"] = inv.get("answers", [])
        out["note"] = ("the account read is unmetered; player-prop market names need the catalog (<= 3 requests, "
                       "guarded): SPORTS_ODDSPAPI=1 SPORTS_ODDSPAPI_MONTHLY_REQUESTS=<n> python sports_scout.py "
                       "books-oddspapi catalog")
    write_summary(source, league, out)
    return out


def coverage_entry(source, league):
    """Small block for the snapshot manifest."""
    s = read_summary(source, league)
    if not s:
        return {"captured_at": None, "status": "no capture stored yet"}
    return {"captured_at": s.get("captured_at"), "status": s.get("status"),
            "answers": [{k: a.get(k) for k in ("path", "http_status", "bytes", "sha256", "error")}
                        for a in s.get("answers", [])][:8]}


def render(s, out=print):
    """Readable review text of a stored summary."""
    if not s:
        out("  no capture stored")
        return
    out(f"  {s['source']} {s['league']}  captured {s.get('captured_at')}  status: {s.get('status')}  "
        f"verified={s.get('verified')}")
    for a in s.get("answers", []):
        if "error" in a:
            out(f"  - {a.get('path')}: ERROR {a['error']}")
            continue
        out(f"  - {a.get('path')}: HTTP {a.get('http_status')} {a.get('bytes')} bytes sha256 {str(a.get('sha256'))[:16]}"
            f"  raw {a.get('raw_file')}")
        st = a.get("structure")
        if not st:
            if a.get("not_json"):
                out(f"      not JSON: {a['not_json']!r}")
            for k, v in a.items():                      # oddspapi account answer: flat facts
                if k not in ("path", "structure"):
                    out(f"      {k}: {v}")
            continue
        out("      key paths (count, types):")
        for p, v in sorted(st["paths"].items(), key=lambda x: (x[0].count("."), x[0]))[:120]:
            out(f"        {p}  n={v['n']}  {'/'.join(v['types'])}")
        out("      name-like values:")
        for p, v in st["names"].items():
            out(f"        {p}: {v[:25]}")
        out("      line-like numbers:")
        for p, v in st["lines"].items():
            out(f"        {p}: {v[:25]}")
    out("  prop format proven only if ALL hold for one known game: " + "; ".join(s.get("checklist", CHECKLIST)))
