"""A synthetic season in the canonical store (no network) for the sports-jobs equality / memory tests (2026-11-08).

What production holds and the results / settle-post code paths depend on:
  * offers on pick'em apps (PrizePicks / Underdog), a sportsbook with alternate lines and Kalshi ladder rungs, each with
    several stored REVISIONS (odds moves; some move the line);
  * forecasts of several models (sm-1, mi-1, mc-1, mkt-<book>) on each offer revision, so one line holds many forecast
    revisions; one-sided Underdog lines (cap-2) and first-half (S1) forecasts that the record leaves out;
  * settlements per (offer, side) at the offer's current line: decided, void (did not play), unresolved, and a
    correction (revision 2) of some; plus settled pick'em sides no forecast was made for.
build(con, ...) writes it; the defaults are test-sized, the benchmark passes a season-sized scale."""
from __future__ import annotations

import random

from sports import store
from sports.contracts import Event, OfferSnapshot, Settlement, SideQuote
from sports.tests import helpers as h

STATS = {"nba": ("points", "rebounds", "assists", "threes"), "nfl": ("pass_yds", "rush_yds", "rec_yds", "rec")}


def _ts(day, hour, minute=0):
    return f"2026-10-{day:02d}T{hour:02d}:{minute:02d}:00Z"


def _offer(lg, eid, pid, stat, platform, oid, line, rev, start, sides=None, variant="standard", scope="full_game"):
    obs = _ts(int(start[8:10]), 12 + rev, (rev * 7) % 60)
    if sides is None:
        sides = (SideQuote("over", True, "american", price=-110 - rev), SideQuote("under", True, "american",
                                                                                     price=-110 + rev))
    return h.offer(platform=platform, oid=oid, line=line, stat=stat, sides=sides, variant=variant, scope=scope,
                   event_id=eid, player_id=pid, league=lg, observed_at=obs, listed_start_utc=start,
                   player_key=pid.rsplit(":", 1)[-1], player_name=pid.rsplit(":", 1)[-1])


def _fc(o, orid, model, rng, rev, sides=("over", "under")):
    mean = o.line + rng.uniform(-3, 3)
    po = round(min(0.9, max(0.1, 0.5 + (mean - o.line) / 10 + rng.uniform(-0.05, 0.05))), 6)
    push = 0.0 if abs(o.line - round(o.line)) > 1e-9 else 0.04
    pu = round(1 - po - push, 6)
    side = "over" if po >= pu else "under"
    if side not in sides:
        side = sides[0]
    return h.forecast(forecast_at=_ts(int(o.listed_start_utc[8:10]), 12 + rev, 30 + (rev * 3) % 30), league=o.league,
                      event_id=o.event_id, player_id=o.player_id, player_key=o.player_key, stat=o.stat, scope=o.scope,
                      line=o.line, offer_snapshot_ids=(orid,), input_snapshot_ids=(orid,), model_version=model,
                      projection_mean=round(mean, 3), p_over=po, p_under=pu, p_push=push,
                      p_over_given_no_push=po / (1 - push), p_under_given_no_push=pu / (1 - push),
                      sides_available=tuple(sides), chosen_side=side, research_only=model != "sm-1")


def _settle(o, value, status_kind, settled_at, rev=1, prev=None):
    out = []
    for side in o.offered_sides():
        if status_kind == "void":
            status, outcome, v = "void", None, None
        elif status_kind == "unresolved":
            status, outcome, v = "unresolved", None, None
        else:
            v = value
            outcome = "over" if v > o.line else ("under" if v < o.line else "push")
            status = "push" if outcome == "push" else ("win" if outcome == side else "loss")
        sid = f"st-{o.platform}-{o.offer_id}-{side}-{rev}"
        out.append(Settlement(settlement_id=sid, platform=o.platform, offer_id=o.offer_id, side=side, line=o.line,
                              event_id=o.event_id, player_id=o.player_id, stat=o.stat, status=status, outcome=outcome,
                              source_value=v, source="espn", source_ref=None, rule_version="test",
                              revision=rev, supersedes=(f"st-{o.platform}-{o.offer_id}-{side}-{rev - 1}"
                                                        if rev > 1 else None),
                              settled_at=settled_at, observed_at=settled_at, known_at=settled_at))
    return out


def build(con, leagues=("nba", "nfl"), events=3, players=4, stats=2, revisions=3, extra_settled=6, seed=11,
          first_day=21, settle=True):
    """-> {"forecasts": n, "settlements": n, "events": n}. Deterministic for a seed."""
    rng = random.Random(seed)
    n_fc = n_st = n_ev = 0
    for lg in leagues:
        for e in range(events):
            day = first_day + (e % 9)
            start = _ts(day, 23, 30)
            eid = f"{lg}:espn:{900 + e}"
            store.put(con, Event(event_id=eid, league=lg, season=2027, season_type="regular",
                                 home_team_id=f"{lg}:team:espn:1", away_team_id=f"{lg}:team:espn:2", start_utc=start,
                                 start_source="espn", status="final", observed_at=_ts(day, 10), known_at=_ts(day, 10)))
            n_ev += 1
            recs, setts = [], []
            for p in range(players):
                pid = f"{lg}:player:espn:{1000 + p}"
                dnp = rng.random() < 0.06
                for st in STATS[lg][:stats]:
                    base = round(rng.uniform(3, 30)) + 0.5
                    value = None if dnp else float(max(0, round(base + rng.gauss(0, 5))))
                    plan = [("prizepicks", f"pp:{eid}:{pid}:{st}", base, ("sm-1", "mi-1", "mc-1", "mkt-fanduel")),
                            ("underdog", f"ud:{eid}:{pid}:{st}", base, ("sm-1", "mi-1", "mc-1")),
                            ("fanduel", f"fd:{eid}:{pid}:{st}:a", base - 2, ("sm-1", "mc-1")),
                            ("fanduel", f"fd:{eid}:{pid}:{st}:b", base + 2, ("sm-1", "mc-1")),
                            ("kalshi", f"kalshi:{eid}:{pid}:{st}:1", base + 1, ("sm-1", "mkt-kalshi"))]
                    for platform, oid, line, models in plan:
                        cur = None
                        one_sided = platform == "underdog" and rng.random() < 0.1
                        for rev in range(revisions):
                            ln = line + (1.0 if (rev == revisions - 1 and rng.random() < 0.3) else 0.0)
                            sides = (SideQuote("over", True), SideQuote("under", False)) if one_sided else None
                            o = _offer(lg, eid, pid, st, platform, oid, ln, rev, start, sides=sides,
                                       variant="one_sided" if one_sided else "standard")
                            orid = store.put(con, o, commit=False)["record_id"]
                            cur = o
                            for m in models:
                                if rng.random() < 0.15 and rev:
                                    continue                     # not every model re-forecasts every revision
                                recs.append(_fc(o, orid, m, rng, rev, ("over",) if one_sided else ("over", "under")))
                        if platform == "prizepicks" and rng.random() < 0.08:   # an S1 line (never in the record)
                            o = _offer(lg, eid, pid, st, platform, oid + ":h1", line / 2, 0, start, scope="first_half")
                            orid = store.put(con, o, commit=False)["record_id"]
                            recs.append(_fc(o, orid, "sm-1", rng, 0))
                        if settle:
                            kind = "void" if dnp else ("unresolved" if rng.random() < 0.03 else "decided")
                            setts.append((cur, value, kind))
            # pick'em sides no forecast was made for (most of a game's settled board)
            for x in range(extra_settled):
                o = _offer(lg, eid, f"{lg}:player:espn:{5000 + x}", STATS[lg][0], "prizepicks",
                           f"pp:{eid}:x{x}", 10.5, 0, start)
                store.put(con, o, commit=False)
                if settle:
                    setts.append((o, 12.0, "decided"))
            con.commit()
            if recs:
                n_fc += store.put_many(con, recs)["inserted"]
            if settle:
                st_recs = []
                for o, value, kind in setts:
                    at = _ts(day + 1, 6)
                    st_recs += _settle(o, value, kind, at)
                    if kind == "decided" and rng.random() < 0.04:        # a provider correction: revision 2
                        st_recs += _settle(o, (value or 0) + 1, kind, _ts(day + 1, 9), rev=2)
                n_st += store.put_many(con, st_recs)["inserted"]
    return {"forecasts": n_fc, "settlements": n_st, "events": n_ev}


__all__ = ["build", "OfferSnapshot"]
