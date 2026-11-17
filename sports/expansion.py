"""Soccer / singles tennis feed inspection. No scheduled jobs or grade promotion.

Read-only default: python -m sports.expansion --competition atp
Optional development storage: --data-root PATH (separate sport/competition databases).
Never infer match format from ESPN format.regulation.periods: the ATP feed can
report 5 for a best-of-three tournament. Missing player stats remain missing.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
import re
from pathlib import Path
import sqlite3

from .contracts import Event, Player, SourceId, StatObservation, Team, now_utc, utc

COMPETITIONS = {
    "eng.1": ("soccer", "soccer"), "esp.1": ("soccer", "soccer"),
    "ger.1": ("soccer", "soccer"), "ita.1": ("soccer", "soccer"),
    "fra.1": ("soccer", "soccer"), "usa.1": ("soccer", "soccer"),
    "uefa.champions": ("soccer", "soccer"),
    "atp": ("tennis", "atp"), "wta": ("tennis", "wta"),
}


@dataclass
class Feed:
    records: list = field(default_factory=list)
    matches: list = field(default_factory=list)
    rejected: list = field(default_factory=list)


def _number(v):
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
        return x if math.isfinite(x) and x >= 0 else None
    except (ValueError, TypeError):
        return None


def parse_scoreboard(body, competition, observed_at):
    """Pure parser. known_at is collection time, never backdated to the match.

Soccer competition namespaces prevent accidental cross-league event joins.
Tennis identities include tournament and match IDs; doubles are excluded.
Retired / walkover / suspended matches never produce completed-match stats.
"""
    sport, league = COMPETITIONS[competition]
    observed_at = utc(observed_at)
    if not observed_at:
        raise ValueError("observed_at required")
    out = Feed()
    seen = set()
    for parent in body.get("events") or []:
        comps = list(parent.get("competitions") or [])
        if sport == "tennis":
            comps += [c for g in parent.get("groupings") or [] for c in g.get("competitions") or []]
        for c in comps:
            cid = str(c.get("id") or "")
            pid = str(parent.get("id") or "")
            if not cid or not pid:
                out.rejected.append("missing event identity")
                continue
            eid = f"{league}:espn:{competition}:{pid}:{cid}"
            if eid in seen:
                continue
            seen.add(eid)
            sides = c.get("competitors") or []
            expected = "athlete" if sport == "tennis" else "team"
            if len(sides) != 2 or any(s.get("type") != expected or not s.get("id") for s in sides):
                out.rejected.append(f"{eid}: not a two-participant {expected} match")
                continue
            if sides[0]["id"] == sides[1]["id"]:
                out.rejected.append(f"{eid}: duplicate participant")
                continue
            try:
                start = utc(c.get("startDate") or c.get("date"))
            except ValueError:
                start = None
            typ = (c.get("status") or {}).get("type") or {}
            name = str(typ.get("name") or "").upper()
            abnormal = any(x in name for x in ("RETIR", "WALK", "SUSPEND", "CANCEL", "POSTPON", "ABANDON", "DEFAULT"))
            final_names = {"STATUS_FINAL", "STATUS_FULL_TIME"} if sport == "soccer" else {"STATUS_FINAL"}
            complete = typ.get("completed") is True and name in final_names and not abnormal
            state = "final" if complete else {"pre": "scheduled", "in": "in_progress"}.get(typ.get("state"), "unknown")
            if abnormal:
                state = "unknown"
            participants, home, away = [], None, None
            records = []
            for s in sides:
                source_id = str(s["id"])
                item = s.get(expected) or {}
                label = item.get("displayName") or item.get("name")
                ident = f"{league}:{'player' if sport == 'tennis' else 'team'}:espn:{source_id}"
                participants.append({"id": ident, "name": label})
                if sport == "tennis":
                    records.append(Player(ident, league, label or "", source_ids=(SourceId("espn", source_id),),
                                          observed_at=observed_at, known_at=observed_at))
                else:
                    records.append(Team(ident, league, item.get("abbreviation") or "", label or "",
                                        observed_at=observed_at, known_at=observed_at))
                    if s.get("homeAway") == "home":
                        home = ident
                    elif s.get("homeAway") == "away":
                        away = ident
            event = Event(eid, league, (parent.get("season") or {}).get("year"), "unknown", home, away,
                          start, "espn", status=state, source_ids=(SourceId("espn", cid),),
                          observed_at=observed_at, known_at=observed_at)
            records.append(event)
            if any(r.validate() for r in records):
                out.rejected.append(f"{eid}: invalid participant/event record")
                continue
            out.records.extend(records)
            out.matches.append({"event_id": eid, "competition": competition, "name": parent.get("name"),
                                "start": start, "status": state, "participants": participants,
                                "best_of": None, "surface": None, "source_status": name})
            if sport == "tennis" and complete:
                sets = [s.get("linescores") or [] for s in sides]
                values = [[_number(x.get("value")) for x in rows] for rows in sets]
                # Only complete ordinary sets; match tiebreaks / missing scores are not games.
                valid = len(values[0]) == len(values[1]) and 2 <= len(values[0]) <= 5
                valid = valid and all(v is not None and v.is_integer() for row in values for v in row)
                if valid:
                    valid = all((max(a,b) == 6 and min(a,b) <= 4) or
                                (max(a,b) == 7 and min(a,b) in (5,6))
                                for a,b in zip(*values))
                if not valid:
                    out.rejected.append(f"{eid}: incomplete or nonstandard set scores")
                    continue
                for i, participant in enumerate(participants):
                    for j, value in enumerate(values[i]):
                        out.records.append(StatObservation(eid, league, participant["id"], "player", f"set{j+1}",
                            "no", "games_won", "count", value, "played", "espn", cid, True, observed_at, observed_at))
                    out.records.append(StatObservation(eid, league, participant["id"], "player", "full_game",
                        "no", "games_won", "count", sum(values[i]), "played", "espn", cid, True, observed_at, observed_at))
    return out


def isolated_database(root, competition):
    """Never write new sports into the existing NBA/NFL canonical database.

Separate soccer competition and tennis tour databases also prevent accidentally
pooling ATP/WTA history or different soccer competitions during future fitting.
"""
    sport, league = COMPETITIONS[competition]
    path = Path(root).resolve() / sport / competition / "canonical.db"
    if path.exists():
        # Inspect before store.connect could apply migrations to an existing file.
        con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'").fetchone():
                foreign = con.execute("SELECT DISTINCT league FROM records WHERE league IS NOT NULL AND league != ?",
                                      (league,)).fetchall()
                if foreign:
                    raise ValueError("refusing a database containing another sport or tour")
                prefix = f"{league}:espn:{competition}:"
                if con.execute("SELECT 1 FROM records WHERE event_id IS NOT NULL AND event_id NOT LIKE ? LIMIT 1",
                               (prefix + "%",)).fetchone():
                    raise ValueError("refusing a database containing another competition")
        finally:
            con.close()
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--competition", choices=COMPETITIONS, required=True)
    parser.add_argument("--date", help="One date, YYYYMMDD")
    parser.add_argument("--data-root", help="Development root; each sport/competition gets its own database. Omitted = no writes")
    args = parser.parse_args()
    if args.date and not re.fullmatch(r"\d{8}", args.date):
        parser.error("date must be YYYYMMDD")
    from . import net
    sport, _ = COMPETITIONS[args.competition]
    body = net.get_json(f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{args.competition}/scoreboard",
                        params={"dates": args.date} if args.date else {})
    feed = parse_scoreboard(body, args.competition, now_utc())
    result = {"competition": args.competition, "matches": len(feed.matches), "records": len(feed.records),
              "stats": sum(isinstance(r, StatObservation) for r in feed.records), "rejected": feed.rejected,
              "write": bool(args.data_root)}
    if args.data_root:
        from . import store
        con = store.connect(isolated_database(args.data_root, args.competition))
        try:
            result["stored"] = {k:v for k,v in store.put_many(con, feed.records).items() if k != "record_ids"}
        finally:
            con.close()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
