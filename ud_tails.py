"""Owner tail links (PP_OPEN_LINK, 2026-10-29): the owner attaches the share link of an Underdog entry they made (Underdog's
own "share" link: play.underdogsports.com / underdogfantasy.onelink.me ...deep_link_value=entry_slip&deep_link_sub1=<id> /
app.underdogsports.com ...entrySlipShareId=<id>) to one Slip Studio slip; members then see "Tail on Underdog" on that slip.

Admin only (server /admin/ud_tail). Validated strictly by pplink.tail_share_id (three hosts, https, the entry slip's share
id is a uuid). Stored bounded (TAILS_MAX, oldest dropped) in one small JSON file beside the Studio database, each with the
slip's leg signature at the time it was attached: a slip whose lines changed since never shows the link (the server also
hides it once any leg's match has started or a line moved on the board). No network I/O, no Underdog account.
"""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pplink

TAILS_MAX = 50
_LOCK = threading.Lock()
_CACHE = {"path": None, "mtime": None, "data": None}


def _path():
    import slip_builder
    return Path(slip_builder.DB_PATH).with_name("ud_tails.json")


def leg_sig(rec):
    """The slip's lines as attached: (row id, line, side) of every leg, hashed."""
    legs = [[str(l.get("row_id") or ""), l.get("line"), l.get("side")] for l in (rec or {}).get("legs") or []]
    return hashlib.sha256(json.dumps(legs, default=str).encode()).hexdigest()[:24]


def _read():
    p = _path()
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return {}
    if _CACHE["path"] == str(p) and _CACHE["mtime"] == mt and _CACHE["data"] is not None:
        return _CACHE["data"]
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    _CACHE.update(path=str(p), mtime=mt, data=d)
    return d


def _write(d):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)
    _CACHE.update(path=None, mtime=None, data=None)


def all_tails():
    """{slip_id: {"url", "share_id", "sig", "book", "at"}} (a copy)."""
    with _LOCK:
        return {k: dict(v) for k, v in _read().items()}


def get(slip_id):
    with _LOCK:
        t = _read().get(str(slip_id))
    return dict(t) if t else None


def attach(rec, url, now=None):
    """Attach (or replace) the share link of one stored Underdog Studio slip -> (entry, None) or (None, reason)."""
    if not rec or not rec.get("slip_id"):
        return None, "Unknown slip"
    if (rec.get("book") or "underdog") != "underdog":
        return None, "Only Underdog slips take an Underdog share link"
    canon, sid = pplink.tail_share_id(url)
    if canon is None:
        return None, sid
    ent = {"url": canon, "share_id": sid, "sig": leg_sig(rec), "book": "underdog",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time()))}
    with _LOCK:
        d = dict(_read())
        d.pop(rec["slip_id"], None)
        d[rec["slip_id"]] = ent
        while len(d) > TAILS_MAX:                       # bounded: the oldest attached link goes first
            d.pop(min(d, key=lambda k: (d[k].get("at") or "", k)))
        _write(d)
    return dict(ent), None


def detach(slip_id):
    with _LOCK:
        d = dict(_read())
        if d.pop(str(slip_id), None) is None:
            return False
        _write(d)
    return True
