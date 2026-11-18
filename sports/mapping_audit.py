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
import time
from collections import Counter, defaultdict

from . import flags, store
from .contracts import now_utc, parse_utc

VERSION = "map-audit-1"
START_TOL_H = 3.0
TARGETS = ("prizepicks", "underdog")


# The league's CURRENT offer revisions: the grouping reads index entries only (records_current_league), then ONE table
# row per identity, streamed. The scheduled audit used the point-in-time reader, which reads the table row of every
# stored revision and decodes every identity - cost grew with the hot offer backlog until it hit its 240 s timeout.
_CURRENT_OFFERS = (f"SELECT r.record_id, r.event_id, r.payload FROM (SELECT rid, MAX(revision) AS rv FROM records "
                   f"WHERE kind = 'offer' AND {store.CURRENT_SQL} AND league = ? GROUP BY source, source_key) z "
                   f"CROSS JOIN records r WHERE r.rid = z.rid")


def _upcoming(events, event_id, now):
    return event_id is None or bool(events.get(event_id) and (events[event_id].start_utc or "") > now)


def _current_offers(con, league, events, now, state):
    """(record id, offer) of the open offers of games not yet started; a played game's payload is never decoded.
    Stops (state["truncated"]) when state["deadline"] on state["clock"] passes."""
    from .contracts import from_dict
    for n, r in enumerate(con.execute(_CURRENT_OFFERS, (league,))):
        if state.get("deadline") is not None and n % 200 == 0 and state["clock"]() > state["deadline"]:
            state["truncated"] = True
            return
        if not _upcoming(events, r["event_id"], now):
            continue
        o = from_dict(store._unpack(r["payload"]))
        if o.availability == "open":
            yield r["record_id"], o


def run(con, league, now=None, budget_s=None, clock=time.monotonic):
    """now given (tests, replays): the audit as of that time. Without it (the scheduled job): the current offers,
    bounded by budget_s - on a stop the file is still written, marked "truncated" with the offers checked so far."""
    from .snapshots import _latest, _iter_latest, snap_root
    state = {"clock": clock, "deadline": None if budget_s is None else clock() + budget_s, "truncated": False}
    replay = now is not None
    now = now or now_utc()
    teams = {t.team_id: t for _, t, _ in _latest(con, "team", "league = ?", [league], now)}
    events = {e.event_id: e for _, e, _ in _latest(con, "event", "league = ?", [league], now)}
    open_teams = defaultdict(set)
    for _, m, _ in _latest(con, "membership", "player_id LIKE ?", [f"{league}:%"], now):
        if m.valid_to is None and m.source == "espn":
            open_teams[m.player_id].add(m.team_id)
    offers = ((rid, o) for rid, o, _ in _iter_latest(con, "offer", "league = ?", [league], now)
              if o.availability == "open" and _upcoming(events, o.event_id, now)) if replay else \
        _current_offers(con, league, events, now, state)
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
           "total_mismatches": sum(c["count"] for c in checks.values()), "truncated": state["truncated"]}
    p = snap_root() / league / "mapping_audit.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m sports.mapping_audit")
    ap.add_argument("leagues", nargs="*", default=["nba", "nfl"])
    ap.add_argument("--now", default=None)
    ap.add_argument("--time-budget-s", type=float, default=180, help="wall budget shared by the leagues (job timeout 240)")
    a = ap.parse_args(argv)
    flags.require("SPORTS_INGEST")
    with store.opened() as con:
        res, end = {}, time.monotonic() + a.time_budget_s
        for i, lg in enumerate(a.leagues):              # an even share of what is left, so the first cannot starve the rest
            share = max(0.0, end - time.monotonic()) / (len(a.leagues) - i)
            res[lg] = run(con, lg, a.now, budget_s=None if a.now else share)
    for lg, r in res.items():
        print(f"{lg}: {r['offers_checked']} open offers checked, {r['total_mismatches']} mismatches"
              + (" (stopped at the time budget)" if r.get("truncated") else ""))
        for k, c in r["checks"].items():
            print(f"  {k:26s} {c['count']:6d}  {c['by_platform']}")
    return res


if __name__ == "__main__":
    main()
