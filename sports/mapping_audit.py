"""Player - team - game mapping audit (worker / admin only). Checks the canonical store's current offers against the
source records they were resolved with - exact ids only, never fuzzy - and lists every mismatch with counts.

Checks (each: count, by platform, up to 20 examples with record ids):
  player_team_not_in_event   the offer's player's current ESPN team (open membership) is neither team of its event
  team_label_mismatch        the book's own team label (offer.team) names the OTHER team of the event (exact abbr /
                             name match against the event's two ESPN teams only)
  player_unresolved          offer with no canonical player (the book's name did not resolve exactly)
  event_unresolved           offer with no canonical event
  multiple_open_teams        a player on the board with more than one open ESPN membership (a trade not closed)
  start_mismatch             the book's listed start differs from the ESPN start by more than 3 h (listed starts only)

  python -m sports.mapping_audit nba nfl        -> prints and writes <SPORTS_DATA_DIR>/snapshots/<lg>/mapping_audit.json
The admin route GET /api/sports/admin/books serves the stored file (no store query in the request).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict

from . import flags, store
from .contracts import now_utc, parse_utc

VERSION = "map-audit-1"
START_TOL_H = 3.0
TARGETS = ("prizepicks", "underdog")


def _open_offers(con, league, events, now):
    """(record id, offer) for the CURRENT revision of every open offer whose game has not started or that has no game
    - the former league-wide _iter_latest read the table row of every hot offer revision of the league (2026-11-18:
    240 s timeouts on every run since at least 2026-11-16 while the archive was stalled). Same rule, index reads only:
    candidate identities through records_event (rows of an upcoming game / rows with no game), each identity's newest
    non-late revision through records_current (snapshots._slate_offers), THEN the event / availability filter - so an
    identity whose newest revision moved away is never resurrected."""
    from .snapshots import _decode_current_rows, _slate_offers
    upcoming = {eid for eid, e in events.items() if (e.start_utc or "") > now}
    for rid, o, _m in _slate_offers(con, league, sorted(upcoming), now):
        if o.availability == "open" and o.event_id in upcoming:
            yield rid, o
    pairs = sorted({(r[0], r[1]) for r in con.execute(
        "SELECT DISTINCT source, source_key FROM records INDEXED BY records_event "
        "WHERE kind = 'offer' AND event_id IS NULL AND league = ?", (league,))})
    for i in range(0, len(pairs), 500):
        rows = []
        for source, key in pairs[i:i + 500]:
            row = con.execute(
                "SELECT record_id, source, source_key, revision, known_at, last_seen_at, last_source_updated_at "
                "FROM records INDEXED BY records_current WHERE kind = 'offer' AND source = ? AND source_key = ? "
                f"AND {store.CURRENT_SQL} AND league = ? AND known_at <= ? ORDER BY revision DESC LIMIT 1",
                (source, key, league, now)).fetchone()
            if row is not None:
                rows.append(row)
        for rid, o, _m in _decode_current_rows(con, rows):
            if o.availability == "open" and o.event_id is None:
                yield rid, o


def run(con, league, now=None):
    from .snapshots import _latest, snap_root
    now = now or now_utc()
    teams = {t.team_id: t for _, t, _ in _latest(con, "team", "league = ?", [league], now)}
    events = {e.event_id: e for _, e, _ in _latest(con, "event", "league = ?", [league], now)}
    open_teams = defaultdict(set)
    for _, m, _ in _latest(con, "membership", "player_id LIKE ?", [f"{league}:%"], now):
        if m.valid_to is None and m.source == "espn":
            open_teams[m.player_id].add(m.team_id)
    offers = _open_offers(con, league, events, now)
    checks = {k: {"count": 0, "by_platform": Counter(), "examples": []} for k in (
        "player_team_not_in_event", "team_label_mismatch", "player_unresolved", "event_unresolved",
        "multiple_open_teams", "start_mismatch")}

    def hit(k, o, rid, **extra):
        c = checks[k]
        c["count"] += 1
        c["by_platform"][o.platform.split(":")[0]] += 1
        if len(c["examples"]) < 20:
            c["examples"].append({"record_id": rid, "platform": o.platform, "player": o.player_name,
                                  "player_id": o.player_id, "event_id": o.event_id, "team": o.team, **extra})

    def names(tid):
        t = teams.get(tid)
        return {x.lower() for x in (t.abbr, t.name) if x} if t else set()

    seen_multi = set()
    offers_checked = 0
    for rid, o in offers:
        offers_checked += 1
        if o.event_id is None:
            hit("event_unresolved", o, rid)
            continue
        ev = events[o.event_id]
        if o.player_id is None:
            hit("player_unresolved", o, rid)
            continue
        cur = open_teams.get(o.player_id, set())
        if len(cur) > 1 and o.player_id not in seen_multi:
            seen_multi.add(o.player_id)
            hit("multiple_open_teams", o, rid, teams=sorted(cur))
        if cur and not (cur & {ev.home_team_id, ev.away_team_id}):
            hit("player_team_not_in_event", o, rid, player_teams=sorted(cur),
                event_teams=[ev.home_team_id, ev.away_team_id])
        if o.team and cur:
            lab = o.team.strip().lower()
            mine = set().union(*(names(t) for t in cur))
            other = names(ev.home_team_id) | names(ev.away_team_id)
            if lab in other and lab not in mine:
                hit("team_label_mismatch", o, rid, player_teams=sorted(cur))
        if o.listed_start_utc and o.listed_time_kind == "start" and ev.start_utc:
            gap = abs((parse_utc(o.listed_start_utc) - parse_utc(ev.start_utc)).total_seconds()) / 3600
            if gap > START_TOL_H:
                hit("start_mismatch", o, rid, listed=o.listed_start_utc, espn=ev.start_utc, gap_h=round(gap, 1))
    for c in checks.values():
        c["by_platform"] = dict(c["by_platform"])
    out = {"version": VERSION, "league": league, "as_of": now, "offers_checked": offers_checked, "checks": checks,
           "total_mismatches": sum(c["count"] for c in checks.values())}
    p = snap_root() / league / "mapping_audit.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m sports.mapping_audit")
    ap.add_argument("leagues", nargs="*", default=["nba", "nfl"])
    ap.add_argument("--now", default=None)
    a = ap.parse_args(argv)
    flags.require("SPORTS_INGEST")
    with store.opened(read_only=True) as con:
        res = {lg: run(con, lg, a.now) for lg in a.leagues}
    for lg, r in res.items():
        print(f"{lg}: {r['offers_checked']} open offers checked, {r['total_mismatches']} mismatches")
        for k, c in r["checks"].items():
            print(f"  {k:26s} {c['count']:6d}  {c['by_platform']}")
    return res


if __name__ == "__main__":
    main()
