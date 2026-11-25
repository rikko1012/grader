"""Same-game injury report reconciliation (remaining brief 13, SPORTS_CONTEXT_RECONCILE, default off, owner on).

A projection is made at its forecast_at with the injury report Grader held then; the board shows the report Grader holds
now. When a status on either team changed in between (audit case: a player listed Questionable when his projection was
made and Out on the board), the page must say so, with both times: the forecast's forecast_at and the ESPN source time /
Grader read time of the new status.

Built in the WORKER inside the snapshot build (sports/snapshots.build) - never in the web process. Bounded: one store
read per distinct forecast time (at most MAX_TIMES per board, newest first) plus one for now; the comparison itself is
dict lookups over the two teams' rosters. Display only: no grade, chance, stake, forecast or settlement reads it, and
the forecast log is never written.

Rules
- The status at time T for a player = the injury record revision Grader held at T (known_at <= T, store CURRENT rule).
  Same record identity as the board (source_key "injury|<subject>|<event>").
- Listed = present "yes". A record with present "no" (ESPN "Active") and no record at all both read as not listed:
  ESPN's report has no removal entry, so a player who drops off the report without an "Active" entry stays as last
  listed - that is the board's rule too, never invented here.
- A change = the listed status differs between forecast_at and now (a detail-only change, e.g. body part, is not one).
- Every change carries the ESPN entry date (source_as_of, None when ESPN gave none) and when Grader first held the new
  status (read_at = its known_at).
"""
from __future__ import annotations

import os

from . import store

FLAG = "SPORTS_CONTEXT_RECONCILE"
VERSION = "ctx-rec-1"
MAX_TIMES = 6                 # distinct forecast times checked per board build (newest first)
PER_EVENT_TIMES = 3           # forecast times kept per game / player file
READ_LIMIT = 20000            # same bound as the forward job's injury read (sports/model/forward.injury_absences)
ROLE_ORDER = {"player": 0, "teammate": 1, "opponent": 2}


def on(env=None):
    return ((os.environ if env is None else env).get(FLAG) or "").strip().lower() in ("1", "true", "yes", "on")


def statuses_at(con, league, at):
    """{player id: {status, present, detail, as_of, read_at, last_seen_at, source}} - the injury record revision Grader
    held at `at` for every player with one."""
    out = {}
    for r in store._latest_rows(con, "context", "league = ?", [league], at, READ_LIMIT):
        if (r["source_key"] or "").split("|", 1)[0] != "injury":
            continue
        c = store._rec(r)
        try:
            seen = r["last_seen_at"]
        except (KeyError, IndexError):
            seen = None
        out[c.subject_id] = {"status": c.status, "present": c.present, "detail": c.detail, "as_of": c.as_of,
                             "read_at": r["known_at"], "source": c.source, "last_seen_at": seen or r["known_at"]}
    return out


def listed(x):
    """The listed status, or None (not on the report / listed Active)."""
    return x["status"] if x and x.get("present") == "yes" else None


def changes(before, after, pids):
    """[{player_id, before, after, source_as_of, read_at, source}] for every pid whose listed status differs."""
    out = []
    for pid in pids:
        b, a = before.get(pid), after.get(pid)
        sb, sa = listed(b), listed(a)
        if sb == sa:
            continue
        out.append({"player_id": pid, "before": sb, "after": sa,
                    "source_as_of": (a or {}).get("as_of") if a else None,
                    "read_at": (a or {}).get("read_at") if a else None,
                    "source": (a or b or {}).get("source")})
    return out


class Checker:
    """One per board build. forecast_times: every forecast_at on a game still on the board."""

    def __init__(self, con, league, now, forecast_times):
        self.con, self.league, self.now = con, league, now
        self.after = statuses_at(con, league, now)
        self.read_at = max((x["last_seen_at"] for x in self.after.values() if x.get("last_seen_at")), default=None)
        self.times = sorted({t for t in forecast_times if t and t <= now}, reverse=True)[:MAX_TIMES]
        self._before = {}
        self._events = {}

    def _at(self, t):
        if t not in self._before:
            self._before[t] = statuses_at(self.con, self.league, t)
        return self._before[t]

    def event_block(self, event_id, team_pids, times, name_of, team_of, abbr_of=None):
        """team_pids: {team id: [player ids]} for the two teams; times: the forecast_at values of this game's
        forecasts. -> {version, event_id, checked_at, report_read_at, by_forecast_at: {T: {state, listed_then,
        changes: [{..., player, team_id}]}}} or None when no checked time applies."""
        key = (event_id, tuple(sorted(times)))
        if key in self._events:
            return self._events[key]
        use = [t for t in sorted(set(times), reverse=True) if t in self.times][:PER_EVENT_TIMES]
        if not use:
            self._events[key] = None
            return None
        pids = sorted({p for xs in team_pids.values() for p in xs})
        by = {}
        for t in use:
            before = self._at(t)
            ch = changes(before, self.after, pids)
            for c in ch:
                c["player"] = name_of(c["player_id"])
                c["team_id"] = team_of.get(c["player_id"])
                c["team"] = abbr_of(c["team_id"]) if abbr_of and c["team_id"] else None
            by[t] = {"state": "changed" if ch else "unchanged",
                     "listed_then": sum(1 for p in pids if listed(before.get(p))),
                     "listed_now": sum(1 for p in pids if listed(self.after.get(p))), "changes": ch}
        blk = {"version": VERSION, "event_id": event_id, "checked_at": self.now, "report_read_at": self.read_at,
               "source": "ESPN injury report", "by_forecast_at": by}
        self._events[key] = blk
        return blk

    @staticmethod
    def player_block(blk, pid, team_id):
        """The game block seen from one player: each change gets a role (player / teammate / opponent), the player's
        own change first, and `player_change` names it."""
        if not blk:
            return None
        by = {}
        for t, x in blk["by_forecast_at"].items():
            ch = []
            for c in x["changes"]:
                role = "player" if c["player_id"] == pid else ("teammate" if team_id and c.get("team_id") == team_id
                                                                else "opponent")
                ch.append(dict(c, role=role))
            ch.sort(key=lambda c: (ROLE_ORDER[c["role"]], c.get("player") or c["player_id"]))
            by[t] = dict(x, changes=ch, player_change=next((c for c in ch if c["role"] == "player"), None))
        return dict(blk, player_id=pid, by_forecast_at=by)

    def summary(self):
        return {"version": VERSION, "times_checked": len(self._before), "times": self.times,
                "report_read_at": self.read_at,
                "games": sum(1 for v in self._events.values() if v),
                "games_changed": sum(1 for v in self._events.values() if v and any(
                    x["state"] == "changed" for x in v["by_forecast_at"].values()))}
