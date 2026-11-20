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
    "bra.1": ("soccer", "soccer"), "arg.1": ("soccer", "soccer"),
    "mex.1": ("soccer", "soccer"), "ned.1": ("soccer", "soccer"),
    "por.1": ("soccer", "soccer"), "sco.1": ("soccer", "soccer"),
    "bel.1": ("soccer", "soccer"), "tur.1": ("soccer", "soccer"),
    "uefa.champions": ("soccer", "soccer"),
    "fifa.friendly": ("soccer", "soccer"), "uefa.nations": ("soccer", "soccer"),
    "fifa.worldq.uefa": ("soccer", "soccer"), "fifa.worldq.conmebol": ("soccer", "soccer"),
    "fifa.worldq.concacaf": ("soccer", "soccer"), "fifa.worldq.afc": ("soccer", "soccer"),
    "fifa.worldq.caf": ("soccer", "soccer"), "fifa.world": ("soccer", "soccer"),
    "concacaf.nations.league": ("soccer", "soccer"),
    # 2026-11-18: 77 more ESPN soccer slugs, each verified by its own scoreboard league header (id + slug + name,
    # logs/sources_1118/probe/espn_slug_ids.json). Research only: no slug here is a forecast input (FORECAST_INPUTS).
    "eng.2": ("soccer", "soccer"), "eng.3": ("soccer", "soccer"), "eng.4": ("soccer", "soccer"),
    "eng.5": ("soccer", "soccer"), "ger.2": ("soccer", "soccer"), "ita.2": ("soccer", "soccer"),
    "esp.2": ("soccer", "soccer"), "fra.2": ("soccer", "soccer"), "ned.2": ("soccer", "soccer"),
    "sco.2": ("soccer", "soccer"), "aut.1": ("soccer", "soccer"), "sui.1": ("soccer", "soccer"),
    "den.1": ("soccer", "soccer"), "nor.1": ("soccer", "soccer"), "swe.1": ("soccer", "soccer"),
    "gre.1": ("soccer", "soccer"), "rus.1": ("soccer", "soccer"), "jpn.1": ("soccer", "soccer"),
    "aus.1": ("soccer", "soccer"), "ksa.1": ("soccer", "soccer"), "chn.1": ("soccer", "soccer"),
    "usa.usl.1": ("soccer", "soccer"), "usa.nwsl": ("soccer", "soccer"), "uefa.europa": ("soccer", "soccer"),
    "uefa.europa.conf": ("soccer", "soccer"), "eng.fa": ("soccer", "soccer"), "eng.league_cup": ("soccer", "soccer"),
    "esp.copa_del_rey": ("soccer", "soccer"), "ger.dfb_pokal": ("soccer", "soccer"),
    "ita.coppa_italia": ("soccer", "soccer"), "fra.coupe_de_france": ("soccer", "soccer"),
    "arg.2": ("soccer", "soccer"), "mex.2": ("soccer", "soccer"), "bra.2": ("soccer", "soccer"),
    "col.1": ("soccer", "soccer"), "chi.1": ("soccer", "soccer"), "per.1": ("soccer", "soccer"),
    "uru.1": ("soccer", "soccer"), "ecu.1": ("soccer", "soccer"), "par.1": ("soccer", "soccer"),
    "bol.1": ("soccer", "soccer"), "ven.1": ("soccer", "soccer"), "eng.w.1": ("soccer", "soccer"),
    "esp.w.1": ("soccer", "soccer"), "ned.w.1": ("soccer", "soccer"), "fra.w.1": ("soccer", "soccer"),
    "usa.w.usl.1": ("soccer", "soccer"), "club.friendly": ("soccer", "soccer"),
    "uefa.wchampions": ("soccer", "soccer"), "esp.copa_de_la_reina": ("soccer", "soccer"),
    "eng.w.league_cup": ("soccer", "soccer"), "mex.w.1": ("soccer", "soccer"), "ind.1": ("soccer", "soccer"),
    "ind.2": ("soccer", "soccer"), "bol.copa": ("soccer", "soccer"), "concacaf.champions": ("soccer", "soccer"),
    "conmebol.libertadores": ("soccer", "soccer"), "conmebol.sudamericana": ("soccer", "soccer"),
    "afc.champions": ("soccer", "soccer"), "caf.champions": ("soccer", "soccer"), "irl.1": ("soccer", "soccer"),
    "wal.1": ("soccer", "soccer"), "isr.1": ("soccer", "soccer"), "cyp.1": ("soccer", "soccer"),
    "cze.1": ("soccer", "soccer"), "rou.1": ("soccer", "soccer"), "rsa.1": ("soccer", "soccer"),
    "ger.super_cup": ("soccer", "soccer"), "idn.1": ("soccer", "soccer"), "tha.1": ("soccer", "soccer"),
    "mys.1": ("soccer", "soccer"), "crc.1": ("soccer", "soccer"), "hon.1": ("soccer", "soccer"),
    "gua.1": ("soccer", "soccer"), "slv.1": ("soccer", "soccer"), "jam.1": ("soccer", "soccer"),
    "aus.w.1": ("soccer", "soccer"),
    "atp": ("tennis", "atp"), "wta": ("tennis", "wta"),
    "atp_challenger": ("tennis", "atp"), "wta125": ("tennis", "wta"),
}

# The competitions of 53dad0b (soccer 24 + tennis 4). Their history / summary behaviour is unchanged by the
# 2026-11-18 additions; the newer slugs get research-only collection with a smaller summary queue.
BASE_COMPETITIONS = frozenset((
    'eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'usa.1', 'bra.1', 'arg.1', 'mex.1', 'ned.1', 'por.1', 'sco.1', 'bel.1',
    'tur.1', 'uefa.champions', 'fifa.friendly', 'uefa.nations', 'fifa.worldq.uefa', 'fifa.worldq.conmebol',
    'fifa.worldq.concacaf', 'fifa.worldq.afc', 'fifa.worldq.caf', 'fifa.world', 'concacaf.nations.league',
    'atp', 'wta', 'atp_challenger', 'wta125'))


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
    if competition in ('atp_challenger', 'wta125'):
        raise ValueError('this competition uses the separate 365Scores parser')
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
            category = str((c.get('type') or {}).get('slug') or '')
            if sport == 'tennis' and category and category != ('mens-singles' if league == 'atp' else 'womens-singles'):
                out.rejected.append(f'{eid}: wrong tour or format')
                continue
            if len(sides) != 2 or any(s.get("type") != expected or not s.get("id") for s in sides):
                out.rejected.append(f"{eid}: not a two-participant {expected} match")
                continue
            if sides[0]["id"] == sides[1]["id"]:
                out.rejected.append(f"{eid}: duplicate participant")
                continue
            if any(str(s['id']).startswith('-') or
                   (s.get(expected) or {}).get('displayName', '').strip().lower() in ('', 'tbd', 'bye')
                   for s in sides):
                out.rejected.append(f"{eid}: unresolved participant")
                continue
            try:
                start = utc(c.get("startDate") or c.get("date"))
            except ValueError:
                start = None
            typ = (c.get("status") or {}).get("type") or {}
            name = str(typ.get("name") or "").upper()
            abnormal = any(x in name for x in ("RETIR", "WALK", "SUSPEND", "CANCEL", "POSTPON", "ABANDON", "DEFAULT"))
            detail = ' '.join([str(typ.get(k) or '') for k in ('description','detail','shortDetail')] +
                              [str(n.get('text') or '') for n in c.get('notes') or []])
            abnormal = abnormal or bool(re.search(r'\b(retired|retirement|walkover|abandoned|defaulted|w/o)\b', detail, re.I))
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
                participants.append({"id": ident, "name": label,
                                     "abbreviation": item.get("abbreviation"),
                                     "short_name": item.get("shortDisplayName")})
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
            if sport == 'soccer':
                # sg-scope (2026-11-20): the fixture's stage facts, as ESPN reports them (season.type id = one stage of
                # one season; leg = 1st / 2nd leg of a two-legged tie; period / clock at the end). expansion_scope
                # decides from these + stored finals whether the fixture can reach extra time. Never inferred.
                season = parent.get('season') or {}
                if season.get('type') is not None:
                    out.matches[-1]['stage_type'] = str(season['type'])
                if season.get('slug'):
                    out.matches[-1]['stage_slug'] = str(season['slug'])
                leg = (c.get('leg') or {}).get('value') if isinstance(c.get('leg'), dict) else None
                if isinstance(leg, int) and not isinstance(leg, bool):
                    out.matches[-1]['leg'] = leg
                status = c.get('status') or {}
                if typ.get('state') == 'post':
                    if isinstance(status.get('period'), int) and not isinstance(status.get('period'), bool):
                        out.matches[-1]['period'] = status['period']
                    if status.get('displayClock'):
                        out.matches[-1]['clock'] = str(status['displayClock'])
            elif isinstance(parent.get('major'), bool):
                # sg-scope: ESPN's Grand Slam flag; expansion_formats derives best-of from it for full-match lines only.
                out.matches[-1]['major'] = parent['major']
            if sport == 'soccer' and complete:
                scores = [_number(s.get('score', {}).get('value') if isinstance(s.get('score'), dict)
                                  else s.get('score')) for s in sides]
                if all(v is not None and v.is_integer() for v in scores):
                    out.matches[-1]['scores'] = {p['id']:scores[i] for i,p in enumerate(participants)}
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
                # Two completed sets must have one winner; 3 sets may be 2-1 or
                # 3-0. In a 4/5-set match a player must win exactly three sets.
                # Never count a set played after the match was already decided.
                wins = [sum(a > b for a,b in zip(values[i], values[1-i]))
                        for i in range(2)] if valid else []
                if valid:
                    target = 3 if len(values[0]) >= 4 or max(wins) == 3 else 2
                    valid = max(wins) == target and min(wins) < target
                    valid = valid and all(sum(a > b for a,b in zip(values[i][:-1], values[1-i][:-1])) < target
                                          for i in range(2))
                if not valid:
                    out.rejected.append(f"{eid}: incomplete or nonstandard set scores")
                    continue
                out.matches[-1]['set_scores'] = {p['id']: values[i] for i, p in enumerate(participants)}
                for i, participant in enumerate(participants):
                    for j, value in enumerate(values[i]):
                        out.records.append(StatObservation(eid, league, participant["id"], "player", f"set{j+1}",
                            "no", "games_won", "count", value, "played", "espn", cid, True, observed_at, observed_at))
                    out.records.append(StatObservation(eid, league, participant["id"], "player", "full_game",
                        "no", "games_won", "count", sum(values[i]), "played", "espn", cid, True, observed_at, observed_at))
                    # These are exact arithmetic from completed set scores, not
                    # inferred serve statistics or a guessed best-of format.
                    for stat, value in (("games_played", sum(map(sum, values))),
                                        ("sets_won", wins[i]), ("sets_played", len(values[i])),
                                        ("tie_breakers_played", sum(max(a,b)==7 and min(a,b)==6 for a,b in zip(*values)))):
                        out.records.append(StatObservation(eid, league, participant['id'], 'player', 'full_game',
                            'no', stat, 'count', value, 'played', 'espn', cid, True, observed_at, observed_at))
                    out.records.append(StatObservation(eid, league, participant['id'], 'player', 'set1',
                        'no', 'games_played', 'count', values[0][0]+values[1][0], 'played', 'espn', cid,
                        True, observed_at, observed_at))
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
                source = '365scores' if competition in ('atp_challenger','wta125') else 'espn'
                prefix = f"{league}:{source}:{competition}:"
                if con.execute("SELECT 1 FROM records WHERE event_id IS NOT NULL AND event_id NOT LIKE ? LIMIT 1",
                               (prefix + "%",)).fetchone():
                    raise ValueError("refusing a database containing another competition")
        finally:
            con.close()
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--competition", choices=[c for c in COMPETITIONS if c not in ('atp_challenger','wta125')], required=True)
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
