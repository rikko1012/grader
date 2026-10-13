#!/usr/bin/env python3
"""
mapping.py - the alias / matching revision (2026-10-13).

Which real team a platform's spelling is - and so which match, and which Thunderpick quote, a line is linked to - is decided
by three things that are NOT part of picklog.MODEL_VERSION: the maintained alias file (team_aliases.json, each entry with its
proof), comparison.VERIFIED_TEAM_ALIASES and the matching rules themselves (matchid.MATCHING_RULES_VERSION). Commit dd75d0a
changed only the alias file and left the model tag unchanged, so a forecast could not say which mapping linked its odds.

revision()      -> "map-<sha256[:12]>" over the canonical JSON of the three (alias entries sorted, keys sorted: reordering the
                   file is not a new revision; editing an entry, its proof included, is). Cached by the alias file's mtime and
                   size (one os.stat per call - it is part of every grade's shared-context key, cs2_scout._context_key).
record(con)     -> appends the current revision and its full content to mapping_revisions(rev PRIMARY KEY, first_seen,
                   content) in picks.db (INSERT OR IGNORE; UPDATE / DELETE abort - append-only), so any mapping state a
                   forecast recorded can be replayed later. Called by the pick-log writer the first time a revision is used.
content(rev)    -> that saved content (dict) or None.

Read-only with respect to grading: nothing here changes a link - it only names the state the links were made under.
"""
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_CACHE = {"key": None, "rev": None, "content": None}

# one statement each (never executescript: it would COMMIT the pick-log writer's open transaction half-way)
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS mapping_revisions (rev TEXT PRIMARY KEY, first_seen TEXT, content TEXT)",
    "CREATE TRIGGER IF NOT EXISTS mapping_revisions_no_update BEFORE UPDATE ON mapping_revisions "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS mapping_revisions_no_delete BEFORE DELETE ON mapping_revisions "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
)


def ensure_schema(con):
    for q in _SCHEMA:
        con.execute(q)


def _aliases_path():
    try:
        import matchid
        return Path(matchid.ALIASES_PATH)
    except Exception:                  # noqa: BLE001 - matchid unavailable: the same file next to this module
        return Path(__file__).resolve().parent / "team_aliases.json"


def _rules_version():
    try:
        import matchid
        return getattr(matchid, "MATCHING_RULES_VERSION", None)
    except Exception:                  # noqa: BLE001
        return None


def _verified():
    try:
        import comparison
        return [list(x) for x in comparison.VERIFIED_TEAM_ALIASES]
    except Exception:                  # noqa: BLE001
        return []


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _build(path):
    """The canonical content of the mapping state (what the revision hashes)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("aliases") or []
        status = "ok"
    except FileNotFoundError:
        entries, status = [], "missing"
    except Exception as e:             # noqa: BLE001 - an unreadable file is a state of its own (matchid then has no aliases)
        entries, status = [], f"unreadable ({type(e).__name__})"
    entries = sorted((e for e in entries if isinstance(e, dict)), key=_canon)
    return {"team_aliases": {"file": path.name, "status": status, "entries": entries},
            "verified_team_aliases": sorted(_verified(), key=_canon),
            "matching_rules": _rules_version()}


def _state():
    path = _aliases_path()
    try:
        st = os.stat(path)
        fkey = (st.st_mtime_ns, st.st_size)
    except OSError:
        fkey = None
    # the code constants are part of the key too (they only change with a deploy - or a test)
    key = (str(path), fkey, _rules_version(), _canon(_verified()))
    c = _CACHE
    if c["key"] == key and c["rev"]:
        return c["rev"], c["content"]
    with _LOCK:
        if _CACHE["key"] == key and _CACHE["rev"]:
            return _CACHE["rev"], _CACHE["content"]
        content = _build(path)
        rev = "map-" + hashlib.sha256(_canon(content).encode("utf-8")).hexdigest()[:12]
        _CACHE.update(key=key, rev=rev, content=content)
        return rev, content


def revision():
    """'map-<12 hex>' - the alias / matching revision every link is made under right now."""
    return _state()[0]


def current_content():
    """(revision, content) - the state revision() names."""
    rev, content = _state()
    return rev, json.loads(_canon(content))


def record(con):
    """Append the current revision + its content to mapping_revisions (idempotent; append-only). Returns the revision.
    The caller commits (the pick-log writer records it in the same transaction as the forecast that first uses it)."""
    rev, content = _state()
    ensure_schema(con)
    con.execute("INSERT OR IGNORE INTO mapping_revisions (rev, first_seen, content) VALUES (?, ?, ?)",
                (rev, datetime.now(timezone.utc).isoformat(timespec="seconds"), _canon(content)))
    return rev


def content(rev, con=None):
    """The saved content of one revision (dict) or None - replay of an old mapping state. con: an open picks.db
    connection (default: the pick log's database)."""
    import sqlite3
    own = con is None
    if own:
        import picklog
        if not Path(picklog.DB).exists():
            return None
        con = sqlite3.connect(str(picklog.DB), timeout=30)
    try:
        try:
            r = con.execute("SELECT content FROM mapping_revisions WHERE rev = ?", (rev,)).fetchone()
        except sqlite3.OperationalError:
            return None                # no revision recorded yet
        return json.loads(r[0]) if r else None
    finally:
        if own:
            con.close()
