"""Read-only, paged audit of every slip Scout generated, by size (owner 2026-11-16: "go over every 4 man ever generated through
scout"). Admin only (server.py /admin/slip_audit.json). Nothing here writes: SQLite stores open read-only (mode=ro), the JSONL logs
are streamed line by line, and each page settles its own legs from the pick log (picklog.rows_for_matches - indexed, bounded)
with the same rule slip history uses (slip_batches.leg_result_from_picks).

Sources (every place a generated slip is kept):
  studio       Slip Studio (slip_builder, slip_studio.db): every slip a member's Build press made - immutable rows, + copy / load
               / void events
  hub          Auto-build in the new board (workspace.db member_state.studio): ONLY each member's latest saved batch is stored
  tracked      slips members tracked (workspace.db slips + receipts + settlements: the member's own settled results)
  recommended  the old on-page builder's slips (slips_recommended.jsonl, first appearance of each)
  loaded       slips members loaded from the old on-page builder (slips_loaded.jsonl)
  batches      the shadow batch builder (slip_batches.jsonl) - never shown to members

page(source, n, offset, limit, role) -> {"source", "n", "offset", "limit", "slips": [...], "next": offset | None}
Each slip: {"source", "id", "at", "book", "entry_type", "member" (12-char hash or None), "used" (copy / load counts where kept),
"legs": [{"sport", "nick", "team", "prop", "scope", "line", "side", "p", "grade", "match", "result", "actual", "awp", "role_src"}],
"result": "won" | "lost" | "refund" | "pending"}. awp / role_src = the player's AWP reading NOW (role(nick, team)); the logs do not
keep the role at build time except the batches source.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

import picklog
import slip_batches

SOURCES = ("studio", "hub", "tracked", "recommended", "loaded", "batches")
LIMIT_MAX = 200
_PERIOD_SCOPE = {"map1": "1", "maps12": "12", "maps13": "123"}


def _ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def _member(m):
    return hashlib.sha256(str(m).encode()).hexdigest()[:12] if m else None


def _side(s):
    s = str(s or "")
    return "More" if s.lower() in ("more", "over", "higher") else "Less" if s.lower() in ("less", "under", "lower") else s


def _leg(sport, nick, team, prop, scope, line, side, p=None, grade=None, match=None, extra=None):
    d = {"sport": sport, "nick": nick, "team": team, "prop": prop, "scope": str(scope or "12"), "line": line, "side": _side(side),
         "p": p, "grade": grade, "match": match}
    if extra:
        d.update(extra)
    return d


def _jsonl(path, n, offset, limit, slips_of):
    """Stream a JSONL log; slips_of(rec) -> [(id, at, book, entry_type, member, used, legs)] of that record. Memory = one page."""
    out, seen, more = [], 0, False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                for s in slips_of(rec):
                    if len(s[6]) != n:
                        continue
                    if seen < offset:
                        seen += 1
                        continue
                    if len(out) >= limit:
                        more = True
                        break
                    seen += 1
                    out.append(s)
                if more:
                    break
    except OSError:
        pass
    return out, more


def _old_legs(legs):
    return [_leg("cs2", l.get("nick") or l.get("player"), l.get("team"), l.get("prop"), l.get("scope"), l.get("line"), l.get("side"),
                 l.get("p"), l.get("grade"), l.get("match_slug"),
                 {k: l[k] for k in ("awp", "awp_conf") if k in l}) for l in legs or []]


def _studio(path, n, offset, limit):
    out = []
    try:
        con = _ro(path)
    except sqlite3.Error:
        return [], False
    try:
        rows = con.execute("SELECT slip_id, batch_id, created_at, record FROM slips WHERE n = ? ORDER BY created_at, slip_id LIMIT ? OFFSET ?",
                           (n, limit + 1, offset)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        ids = [r[0] for r in rows]
        ev = {}
        for i in range(0, len(ids), 200):
            part = ids[i:i + 200]
            for sid, kind, c in con.execute(f"SELECT slip_id, kind, COUNT(*) FROM events WHERE slip_id IN ({','.join('?' * len(part))}) "
                                            "GROUP BY slip_id, kind", part):
                ev.setdefault(sid, {})[kind] = c
        owners = dict(con.execute(f"SELECT batch_id, owner FROM batches WHERE batch_id IN ({','.join('?' * len(rows))})",
                                  [r[1] for r in rows]).fetchall()) if rows else {}
        for sid, bid, at, rec in rows:
            s = json.loads(rec)
            out.append((sid, at, s.get("book"), s.get("entry_type"), _member(owners.get(bid)), ev.get(sid, {}),
                        [_leg("cs2", l.get("nick") or l.get("player"), l.get("team"), l.get("prop"), l.get("scope"), l.get("line"),
                              l.get("side"), l.get("p"), l.get("grade"), l.get("match_slug"),
                              {"match_id": l.get("match_id"), "book": l.get("book")}) for l in s.get("legs") or []]))
        return out, more
    finally:
        con.close()


def _hub(path, n, offset, limit):
    out, seen, more = [], 0, False
    try:
        con = _ro(path)
    except sqlite3.Error:
        return [], False
    try:
        for member, studio in con.execute("SELECT member, studio FROM member_state WHERE studio IS NOT NULL AND studio <> '' ORDER BY member"):
            try:
                b = json.loads(studio)
            except ValueError:
                continue
            for i, s in enumerate((b or {}).get("slips") or []):
                legs = s.get("legs") or []
                if len(legs) != n:
                    continue
                if seen < offset:
                    seen += 1
                    continue
                if len(out) >= limit:
                    more = True
                    break
                seen += 1
                out.append((f"{_member(member)}/{b.get('created_at')}/{i}", b.get("created_at"), s.get("platform"), s.get("entry_type"), _member(member),
                            {}, [_leg(l.get("sport"), l.get("player"), l.get("team"), l.get("stat"),
                                      _PERIOD_SCOPE.get(l.get("period"), l.get("period")), l.get("line"), l.get("side"), l.get("shown_p"), None,
                                      l.get("event_id"), {"event_label": l.get("event_label"), "start": l.get("start_utc")}) for l in legs]))
            if more:
                break
        return out, more
    finally:
        con.close()


def _tracked(path, n, offset, limit):
    out = []
    try:
        con = _ro(path)
    except sqlite3.Error:
        return [], False
    try:
        rows = con.execute("SELECT slip_id, member, tracked_at, platform, entry_type FROM slips WHERE legs = ? AND scratch = 0 "
                           "ORDER BY tracked_at, slip_id LIMIT ? OFFSET ?", (n, limit + 1, offset)).fetchall()
        more = len(rows) > limit
        for sid, member, at, platform, et in rows[:limit]:
            legs = []
            for r in con.execute("SELECT r.sport, r.nick, r.prop, r.scope, r.line, r.side, r.match_slug, r.payload, s.result, s.actual FROM receipts r "
                                 "LEFT JOIN settlements s ON s.receipt_id = r.receipt_id AND s.revision = (SELECT MAX(x.revision) FROM settlements x "
                                 "WHERE x.receipt_id = r.receipt_id) WHERE r.slip_id = ? ORDER BY r.leg_no", (sid,)):
                try:
                    pl = json.loads(r[7] or "{}")
                except ValueError:
                    pl = {}
                legs.append(_leg(r[0], r[1], pl.get("team"), r[2], r[3], r[4], r[5], pl.get("shown_p"), None, r[6],
                                 {"result": None if r[8] == "reopened" else r[8], "actual": r[9], "settled_by": "member receipt"}))
            out.append((sid, at, platform, et, _member(member), {}, legs))
        return out, more
    finally:
        con.close()


def _settle(slips, role):
    """Each CS2 leg's result from the pick log (exact match slug; an hltv:<id> event id also tries hltv-<id>), and the slip's."""
    cs2 = [l for s in slips for l in s["legs"] if l["sport"] == "cs2" and "result" not in l]
    slugs = set()
    for l in cs2:
        m = str(l.get("match") or "")
        l["_slugs"] = [m] + ([m.replace("hltv:", "hltv-", 1)] if m.startswith("hltv:") else [])
        slugs.update(x for x in l["_slugs"] if x)
    rows = picklog.rows_for_matches(slugs, cap=None) if slugs else []
    res, act = slip_batches.leg_result_from_picks(rows), slip_batches.leg_actual_from_picks(rows)
    for l in cs2:
        r = a = None
        for slug in l.pop("_slugs"):
            q = {"nick": l["nick"], "prop": l["prop"], "scope": l["scope"], "match_slug": slug, "line": l["line"], "side": l["side"]}
            try:
                r = res(q)
                a = act(q) if r else None
            except (KeyError, TypeError):
                r = None
            if r:
                break
        l["result"], l["actual"] = r, a
    memo = {}
    for s in slips:
        for l in s["legs"]:
            l.setdefault("result", None)
            if l["sport"] == "cs2" and role:
                k = ((l["nick"] or "").lower(), l.get("team") or "")
                if k not in memo:
                    try:
                        memo[k] = role(l["nick"] or "", l.get("team"))
                    except Exception:                       # a role lookup never costs the page
                        memo[k] = (None, None)
                l["awp_now"], l["role_src"] = memo[k]
        rs = [l["result"] for l in s["legs"]]
        s["result"] = ("lost" if "miss" in rs else "pending" if any(x is None for x in rs)
                       else "refund" if sum(x == "hit" for x in rs) < 2 else "won")
    return slips


def page(source, n=4, offset=0, limit=LIMIT_MAX, role=None, paths=None):
    """One page of `source`'s n-pick slips, settled. paths: {"studio", "workspace", "recommended", "loaded", "batches"} (server.py)."""
    if source not in SOURCES:
        raise ValueError("source must be one of " + ", ".join(SOURCES))
    n, offset, limit = int(n), max(0, int(offset)), max(1, min(LIMIT_MAX, int(limit)))
    p = paths or {}
    if source == "studio":
        got, more = _studio(p["studio"], n, offset, limit)
    elif source == "hub":
        got, more = _hub(p["workspace"], n, offset, limit)
    elif source == "tracked":
        got, more = _tracked(p["workspace"], n, offset, limit)
    elif source == "recommended":
        got, more = _jsonl(p["recommended"], n, offset, limit, lambda r: [(r.get("key"), r.get("first_seen"), None, None, None, {},
                                                                            _old_legs(r.get("legs")))])
    elif source == "loaded":
        got, more = _jsonl(p["loaded"], n, offset, limit, lambda r: [(r.get("cid"), r.get("logged_at"), (r.get("legs") or [{}])[0].get("book"),
                                                                       r.get("mode"), None, {"load": 1}, _old_legs(r.get("legs")))])
    else:
        got, more = _jsonl(p["batches"], n, offset, limit, lambda r: [(f"{r.get('batch_id')}/{i}", r.get("created_at"), "underdog", s.get("kind"),
                                                                        None, {}, _old_legs(s.get("legs")))
                                                                       for i, s in enumerate(r.get("slips") or [])])
    slips = [{"source": source, "id": i, "at": at, "book": bk, "entry_type": et, "member": m, "used": used, "legs": legs}
             for i, at, bk, et, m, used, legs in got]
    return {"source": source, "n": n, "offset": offset, "limit": limit, "slips": _settle(slips, role), "next": offset + len(slips) if more else None}
