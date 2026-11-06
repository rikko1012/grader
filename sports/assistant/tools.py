"""Typed, READ-ONLY retrieval tools over the canonical sports store (owner brief 11).

Each tool has a JSON schema (additionalProperties false) that the assistant's arguments are validated against BEFORE it
runs; nothing here writes, places, submits or changes anything. A result is

  {"tool", "category", "as_of", "query", "window"?, "rows": [...], "summary"?, "notes": [...], "truncated"}

category keeps the three kinds of numbers apart (they are never mixed in one result):
  historical_stats  box-score observations (ESPN) and aggregates computed from exactly those rows
  market_quotes     offer snapshots: platform, line, sides, prices AS THE SOURCE GAVE THEM (no EV, no arbitrage)
  forecasts         stored Forecast records: id, versions, unconditional probabilities, projection
  reference         players, events, date ranges, context (injury status), factor coverage

Provenance rule (tested): every number sits in a dict that carries an id (record_id / agg_id / forecast_id) and an
as_of time. record_id = the canonical store's record id (snapshot id for offers); agg_id = a deterministic hash of the
aggregate's inputs, with source_record_ids listing them. as_of = the point in time the store was read at; known_at =
when that record became known to Scout. Strings that came from a provider (names, labels) are data: control characters
are stripped, length is capped and citation markers are neutralised so a page or feed cannot forge a citation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from datetime import datetime, timezone

from .. import capabilities, config as sconfig, markets, names, store
from ..contracts import now_utc, parse_utc
from ..identity import us_date
from . import dates

TS_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
LEAGUE_ENUM = ["nba", "nfl"]
MAX_ROWS = 50
CATEGORIES = ("historical_stats", "market_quotes", "forecasts", "reference")


class ToolError(ValueError):
    """Arguments that do not fit the tool's schema (returned to the model as an error result, never run)."""


# ------------------------------------------------------------------------------------------------------- utilities
_CTRL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")


def clean(s, n=120):
    """Provider / page text -> inert data: no control or bidi characters, capped, no citation markers."""
    if s is None:
        return None
    s = _CTRL.sub(" ", str(s))
    s = re.sub(r"\[\s*ref\s*:", "(ref:", s, flags=re.I)
    s = s.replace("<", "‹").replace(">", "›")
    return s[:n]


def _agg_id(tool, query, ids):
    h = hashlib.sha256(json.dumps([tool, query, sorted(ids)], sort_keys=True).encode()).hexdigest()
    return "agg-" + h[:16]


def _r(x, k=4):
    return None if x is None else round(float(x), k)


def _family(league):
    return sconfig.LEAGUES[league]["family"]


# --------------------------------------------------------------------------------------------- schema validation
def validate(schema: dict, args) -> dict:
    """Strict validation of a tool's arguments against its schema (types, enums, patterns, bounds, no extra keys)."""
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    props = schema.get("properties", {})
    extra = set(args) - set(props)
    if extra:
        raise ToolError(f"unknown argument(s): {sorted(extra)}")
    for r in schema.get("required", []):
        if args.get(r) is None:
            raise ToolError(f"missing required argument: {r}")
    out = {}
    for k, v in args.items():
        if v is None:
            continue
        p = props[k]
        t = p.get("type")
        if t == "string":
            if not isinstance(v, str):
                raise ToolError(f"{k} must be a string")
            if len(v) > p.get("maxLength", 200):
                raise ToolError(f"{k} is too long")
            if "pattern" in p and not re.match(p["pattern"], v):
                raise ToolError(f"{k} does not match {p['pattern']}")
        elif t == "integer":
            if isinstance(v, bool) or not isinstance(v, int):
                raise ToolError(f"{k} must be an integer")
        elif t == "number":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ToolError(f"{k} must be a finite number")
        elif t == "boolean":
            if not isinstance(v, bool):
                raise ToolError(f"{k} must be true or false")
        if "enum" in p and v not in p["enum"]:
            raise ToolError(f"{k} must be one of {p['enum']}")
        if "minimum" in p and v < p["minimum"]:
            raise ToolError(f"{k} must be >= {p['minimum']}")
        if "maximum" in p and v > p["maximum"]:
            raise ToolError(f"{k} must be <= {p['maximum']}")
        out[k] = v
    return out


def _s(desc, **kw):
    return {"type": "string", "description": desc, **kw}


TS = {"type": "string", "pattern": TS_PATTERN, "maxLength": 20}
STAT_KEYS = sorted(set(markets.MARKETS["basketball"]) | set(markets.MARKETS["football"]))

SPECS = {
    "resolve_dates": {
        "category": "reference",
        "description": "Turn a date phrase ('last night', 'this season', 'last 10 days', '2026-10-22', 'Oct 22') into an "
                       "explicit UTC range [start_utc, end_utc) over US Eastern game days. Always call this (or use the "
                       "ranges given with the question) before any tool that takes dates.",
        "schema": {"type": "object", "additionalProperties": False, "required": ["phrase", "league"],
                   "properties": {"phrase": _s("the date phrase", maxLength=60),
                                  "league": {"type": "string", "enum": LEAGUE_ENUM}}}},
    "find_players": {
        "category": "reference",
        "description": "Exact (normalised) name lookup -> canonical player ids. Never fuzzy: if no exact match exists "
                       "it says so; several matches are returned as candidates to disambiguate by team.",
        "schema": {"type": "object", "additionalProperties": False, "required": ["name", "league"],
                   "properties": {"name": _s("player name as written", maxLength=80),
                                  "league": {"type": "string", "enum": LEAGUE_ENUM}}}},
    "list_events": {
        "category": "reference",
        "description": "Games in a UTC range (from resolve_dates): event ids, teams, start times, status.",
        "schema": {"type": "object", "additionalProperties": False, "required": ["league", "start_utc", "end_utc"],
                   "properties": {"league": {"type": "string", "enum": LEAGUE_ENUM}, "start_utc": TS,
                                  "end_utc": TS}}},
    "player_game_log": {
        "category": "historical_stats",
        "description": "A player's per-game box-score values for one stat (ESPN), newest first, with sample size, "
                       "date window, mean / median / spread, and - when a line is given - strict Over / Under counts "
                       "with pushes separately. Optional home/away splits. Games not played are listed, not counted.",
        "schema": {"type": "object", "additionalProperties": False, "required": ["player_id", "league", "stat"],
                   "properties": {"player_id": _s("canonical player id from find_players", maxLength=60),
                                  "league": {"type": "string", "enum": LEAGUE_ENUM},
                                  "stat": {"type": "string", "enum": STAT_KEYS},
                                  "start_utc": TS, "end_utc": TS,
                                  "last_n": {"type": "integer", "minimum": 1, "maximum": 82},
                                  "line": {"type": "number", "minimum": -50, "maximum": 1000},
                                  "splits": {"type": "boolean"}}}},
    "market_quotes": {
        "category": "market_quotes",
        "description": "Current offer snapshots (latest revision known now): platform, line, scope, variant, which "
                       "sides are offered and their prices exactly as the source gave them, source update time vs our "
                       "observation time. compatible_group is equal only for identical markets (same event, player, "
                       "stat, scope, ordinary line) - compare prices only within one group. No EV / arbitrage values.",
        "schema": {"type": "object", "additionalProperties": False, "required": ["league"],
                   "properties": {"league": {"type": "string", "enum": LEAGUE_ENUM},
                                  "player_id": _s("canonical player id", maxLength=60),
                                  "event_id": _s("canonical event id", maxLength=60),
                                  "stat": {"type": "string", "enum": STAT_KEYS},
                                  "platform": {"type": "string", "enum": ["prizepicks", "underdog", "kalshi"]},
                                  "line_history": {"type": "boolean"},
                                  "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS}}}},
    "forecasts": {
        "category": "forecasts",
        "description": "Stored Scout forecasts: forecast id, forecast time, model / artifact / data / calibration "
                       "versions, projection, unconditional p_over / p_under / p_push, offered sides, and whether the "
                       "version is research-only (not validated for members).",
        "schema": {"type": "object", "additionalProperties": False, "required": ["league"],
                   "properties": {"league": {"type": "string", "enum": LEAGUE_ENUM},
                                  "player_id": _s("canonical player id", maxLength=60),
                                  "event_id": _s("canonical event id", maxLength=60),
                                  "model_version": _s("e.g. sharp-2", maxLength=40),
                                  "limit": {"type": "integer", "minimum": 1, "maximum": 20}}}},
    "player_context": {
        "category": "reference",
        "description": "Context facts held for a player (injury report status with its date). No record = unknown, "
                       "never 'healthy'.",
        "schema": {"type": "object", "additionalProperties": False, "required": ["player_id", "league"],
                   "properties": {"player_id": _s("canonical player id", maxLength=60),
                                  "league": {"type": "string", "enum": LEAGUE_ENUM}}}},
    "factor_coverage": {
        "category": "reference",
        "description": "Which prop factors Scout holds data for (and the tool / field that has it) and which are "
                       "UNAVAILABLE. Call it before discussing matchups, usage, pace, coverage schemes etc.; never "
                       "describe an unavailable factor as if it were known.",
        "schema": {"type": "object", "additionalProperties": False, "required": ["league"],
                   "properties": {"league": {"type": "string", "enum": LEAGUE_ENUM}}}},
}


def anthropic_tools():
    """Tool definitions for the Messages API (stable order = stable prompt cache prefix). eager_input_streaming is on
    (streamed requests with client tools); inputs are validated here before anything runs."""
    return [{"name": n, "description": s["description"], "input_schema": s["schema"], "eager_input_streaming": True}
            for n, s in SPECS.items()]


# --------------------------------------------------------------------------------------------- the factor table
# Owner factor list 2026-10-23 (scratchpad OWNER_FACTORS.md) mapped to what the store actually holds. "available" names
# the tool + field; anything else is "unavailable" with the reason. Candidates, not weights.
_AV, _NA, _PART = "available", "unavailable", "partial"
FACTORS = {
    "nba": [
        ("Minutes (actual, per game)", _NA, "box-score minutes are stored but not exposed as a tool stat yet", None),
        ("Projected minutes", _NA, "no projected-minutes source", None),
        ("Usage rate (USG%), shot attempts per game / per 36", _NA, "field-goal attempts and usage are not stored", None),
        ("Assist rate / potential assists", _NA, "only assists made are stored (player_game_log stat assists)", None),
        ("Recent form windows L5 / L10 vs season", _AV, "player_game_log with last_n and a season range", None),
        ("Home / away", _AV, "player_game_log splits=true (venue from team membership at game time)", None),
        ("Rest days / back-to-back", _AV, "player_game_log rows carry rest_days (days since the previous game)", None),
        ("Teammate injuries / injury status", _PART, "player_context: ESPN injury status + date only; no redistribution "
         "estimate", None),
        ("Starter / bench role change", _NA, "starter flags are not stored yet", None),
        ("Team pace, opponent pace, projected pace, game total", _NA, "no pace or game-total source", None),
        ("Defensive matchup / defender, DvP, DRtg, contest rate", _NA, "no matchup or defensive data", None),
        ("Shot profile (rim / mid / 3PT rate, TS%, eFG%, FT rate)", _NA, "not stored", None),
        ("Blowout risk (spread)", _NA, "spread markets are refused (capabilities)", None),
        ("On/off, rebound chances, AST/TO, ORtg / net rating", _NA, "not stored", None),
        ("Market: line vs history distribution", _AV, "market_quotes line + player_game_log line counts", None),
        ("Market: prices across books, Kalshi bid / ask", _AV, "market_quotes sides (compare within compatible_group)", None),
        ("Market: line movement", _AV, "market_quotes line_history=true (stored offer revisions)", None),
        ("Market: de-vigged implied probability", _NA, "not computed by the assistant tools", None),
        ("Scout projection / probabilities", _AV, "forecasts (research-only versions are labelled)", None),
    ],
    "nfl": [
        ("Targets, receptions, carries, pass attempts per game", _AV, "player_game_log stats targets / rec / rush_att / "
         "pass_att", None),
        ("Snap share, route participation, TPRR, routes run", _NA, "no snap or route data", None),
        ("Target share / air-yards share, red-zone usage", _NA, "team totals, air yards and red-zone splits not stored", None),
        ("Alignment / role (X, Z, slot, TE, RB)", _NA, "not stored", None),
        ("Coverage scheme (man / zone / shells), blitz, pressure", _NA, "no scheme data", None),
        ("Individual matchups (WR vs DB, OL vs DL), PFF / NGS", _NA, "no matchup or tracking data", None),
        ("Run scheme (zone / gap)", _NA, "not stored", None),
        ("QB throw distribution, aDOT, splits vs pressure", _NA, "not stored", None),
        ("Yards per game (pass / rush / receiving)", _AV, "player_game_log stats pass_yds / rush_yds / rec_yds", None),
        ("Game script: spread, implied team total, pace, plays per game", _NA, "spread / total markets refused; no "
         "play-by-play", None),
        ("Weather", _NA, "no weather source yet", None),
        ("Home / away", _AV, "player_game_log splits=true", None),
        ("Rest / short week", _AV, "player_game_log rows carry rest_days", None),
        ("Injury status", _PART, "player_context: ESPN injury status + date only", None),
        ("EPA, DVOA, success rate, explosive rate", _NA, "not stored", None),
        ("Recent form windows L3 / L5 vs season", _AV, "player_game_log last_n + season range", None),
        ("Market: line vs distribution, prices across books, Kalshi bid / ask", _AV, "market_quotes + player_game_log",
         None),
        ("Market: line movement", _AV, "market_quotes line_history=true", None),
        ("Market: de-vigged implied probability", _NA, "not computed by the assistant tools", None),
        ("Scout projection / probabilities", _AV, "forecasts", None),
    ],
}


# ---------------------------------------------------------------------------------------------------- the context
class ToolContext:
    """One question's read-only view: a mode=ro store connection and the point in time every read uses."""

    def __init__(self, con, now: str | None = None):
        self.con = con
        self.now = now or now_utc()
        self._players = {}
        self._teams = {}

    def _all_players(self, league):
        if league not in self._players:
            rows = self.con.execute("SELECT r.record_id, r.known_at, r.payload FROM records r JOIN (SELECT source_key, "
                                    "MAX(revision) rv FROM records WHERE late = 0 AND kind='player' AND league=? AND known_at <= ? "
                                    "GROUP BY source_key) z ON r.source_key = z.source_key AND r.revision = z.rv "
                                    "WHERE r.kind='player'", (league, self.now)).fetchall()
            self._players[league] = [(r["record_id"], r["known_at"], store._unpack(r["payload"])) for r in rows]
        return self._players[league]

    def team(self, team_id):
        if team_id is None:
            return None
        if team_id not in self._teams:
            t = store.latest(self.con, "team", "canonical", team_id, as_of=self.now)
            self._teams[team_id] = t.abbr if t else None
        return self._teams[team_id]

    def memberships(self, player_id):
        rows = self.con.execute("SELECT payload FROM records WHERE kind='membership' AND player_id=? AND known_at <= ?",
                                (player_id, self.now)).fetchall()
        return [store._unpack(r["payload"]) for r in rows]


def _team_at(mems, t):
    best = None
    for m in mems:
        if m["valid_from"] <= t and (not m.get("valid_to") or m["valid_to"] > t):
            if best is None or m["valid_from"] > best["valid_from"]:
                best = m
    return best["team_id"] if best else None


def _result(ctx, tool, query, rows, **kw):
    return {"tool": tool, "category": SPECS[tool]["category"], "as_of": ctx.now, "query": query, "rows": rows,
            "notes": kw.pop("notes", []), "truncated": kw.pop("truncated", False), **kw}


# ---------------------------------------------------------------------------------------------------- the tools
def t_resolve_dates(ctx, a):
    r = dates.resolve(a["phrase"], a["league"], parse_utc(ctx.now))
    if r is None:
        return _result(ctx, "resolve_dates", a, [], notes=["phrase not understood - ask the member for an explicit "
                                                           "date or range"])
    return _result(ctx, "resolve_dates", a, [], window=r)


def t_find_players(ctx, a):
    want = names.key(a["name"])
    hits = []
    for rid, known, p in ctx._all_players(a["league"]):
        if names.key(p["name"]) == want:
            team = _team_at(ctx.memberships(p["player_id"]), ctx.now)
            hits.append({"record_id": rid, "as_of": ctx.now, "known_at": known, "player_id": p["player_id"],
                         "name": clean(p["name"], 80), "position": clean(p.get("position"), 10),
                         "roster_status": p.get("status"), "team_id": team, "team": ctx.team(team),
                         "resolved": p.get("resolved", True)})
    notes = [] if hits else [f"no exact name match for '{clean(a['name'], 80)}' in {a['league'].upper()} - names are "
                             "never matched loosely; ask for the exact spelling"]
    if len(hits) > 1:
        notes.append("several players share this name - confirm the team before using any of them")
    return _result(ctx, "find_players", a, hits[:10], notes=notes, truncated=len(hits) > 10)


def t_list_events(ctx, a):
    if a["end_utc"] <= a["start_utc"]:
        raise ToolError("end_utc must be after start_utc")
    rows = []
    for rid, ev in store.events(ctx.con, league=a["league"], as_of=ctx.now):
        if ev.start_utc and a["start_utc"] <= ev.start_utc < a["end_utc"]:
            rows.append({"record_id": rid, "as_of": ctx.now, "known_at": ev.known_at, "event_id": ev.event_id,
                         "start_utc": ev.start_utc, "us_date": us_date(ev.start_utc), "status": ev.status,
                         "home_team_id": ev.home_team_id, "home": ctx.team(ev.home_team_id),
                         "away_team_id": ev.away_team_id, "away": ctx.team(ev.away_team_id),
                         "rescheduled": bool(ev.reschedules)})
    rows.sort(key=lambda r: r["start_utc"])
    return _result(ctx, "list_events", a, rows[:MAX_ROWS], truncated=len(rows) > MAX_ROWS,
                   window={"start_utc": a["start_utc"], "end_utc": a["end_utc"]})


def _summary(vals, line, ids, query, tool, label, window):
    played = [v for v in vals if v is not None]
    s = {"agg_id": _agg_id(tool, [query, label], ids), "as_of": window["as_of"], "label": label,
         "source_record_ids": ids, "n_games": len(played), "window": {k: window[k] for k in ("start_utc", "end_utc")}}
    if played:
        s.update(mean=_r(statistics.fmean(played), 2), median=_r(statistics.median(played), 2),
                 sd=_r(statistics.stdev(played), 2) if len(played) > 1 else None, min=min(played), max=max(played))
    if line is not None and played:
        s.update(line=line, over=sum(v > line for v in played), under=sum(v < line for v in played),
                 push=sum(v == line for v in played), denominator=len(played))
    if len(played) < 5:
        s["small_sample"] = True
    return s


def t_player_game_log(ctx, a):
    lg, pid, stat = a["league"], a["player_id"], a["stat"]
    mk = markets.market(_family(lg), stat)
    if mk is None:
        raise ToolError(f"stat {stat} is not a {lg.upper()} market")
    end = min(a.get("end_utc") or ctx.now, ctx.now)
    start = a.get("start_utc")
    if start and start >= end:
        raise ToolError("start_utc must be before end_utc")
    by_event = {}
    for part in mk["parts"]:
        for rid, obs, ev_start in store.history(ctx.con, pid, part, before=ctx.now, as_of=ctx.now, limit=400):
            if ev_start >= end or (start and ev_start < start):
                continue
            e = by_event.setdefault(obs.event_id, {"start": ev_start, "parts": {}, "ids": [], "known": [],
                                                   "participation": set()})
            e["parts"][part] = obs.value
            e["ids"].append(rid)
            e["known"].append(obs.known_at)
            e["participation"].add(obs.participation)
    mems = ctx.memberships(pid)
    order = sorted(by_event.items(), key=lambda kv: kv[1]["start"], reverse=True)
    rows = []
    for i, (eid, e) in enumerate(order):
        played = e["participation"] == {"played"} and len(e["parts"]) == len(mk["parts"]) and \
            all(v is not None for v in e["parts"].values())
        ev = store.latest(ctx.con, "event", "espn", eid, as_of=ctx.now)
        team = _team_at(mems, e["start"])
        venue, opp = "unknown", None
        if ev and team:
            if team == ev.home_team_id:
                venue, opp = "home", ev.away_team_id
            elif team == ev.away_team_id:
                venue, opp = "away", ev.home_team_id
        prev = order[i + 1][1]["start"] if i + 1 < len(order) else None
        rest = None
        if prev:
            rest = (parse_utc(e["start"]).date() - parse_utc(prev).date()).days
        rows.append({"record_id": e["ids"][0], "source_record_ids": e["ids"], "as_of": ctx.now,
                     "known_at": max(e["known"]), "event_id": eid, "start_utc": e["start"],
                     "us_date": us_date(e["start"]), "value": _r(sum(e["parts"].values()), 2) if played else None,
                     "participation": "played" if played else "/".join(sorted(e["participation"])),
                     "venue": venue, "opponent": ctx.team(opp), "opponent_team_id": opp, "rest_days": rest})
    if a.get("last_n"):
        rows = [r for r in rows if r["value"] is not None][:a["last_n"]]
    window = {"start_utc": start or (rows[-1]["start_utc"] if rows else None), "end_utc": end, "as_of": ctx.now}
    ids = [r["record_id"] for r in rows]
    summary = _summary([r["value"] for r in rows], a.get("line"), ids, a, "player_game_log", "all games", window)
    out = {"summary": summary}
    if a.get("splits"):
        sp = {}
        for v in ("home", "away"):
            rr = [r for r in rows if r["venue"] == v]
            sp[v] = _summary([r["value"] for r in rr], a.get("line"), [r["record_id"] for r in rr], a,
                             "player_game_log", v, window)
        out["splits"] = sp
    notes = [f"stat {mk['label']} = " + " + ".join(mk["parts"]) + " (ESPN box score, full game incl. overtime)"]
    if not rows:
        notes.append("no stored games for this player and stat in the window")
    elif summary.get("small_sample"):
        notes.append("small sample (fewer than 5 games played) - describe it as such")
    return _result(ctx, "player_game_log", a, rows[:MAX_ROWS], truncated=len(rows) > MAX_ROWS,
                   window={k: window[k] for k in ("start_utc", "end_utc")}, notes=notes, **out)


def _group(o):
    if not capabilities.is_ordinary(o) or o.promotional or not o.event_id or not o.player_id \
            or not o.stat:
        return None
    return "cg-" + hashlib.sha256(f"{o.league}|{o.event_id}|{o.player_id}|{o.stat}|{o.scope}".encode()).hexdigest()[:12]


def t_market_quotes(ctx, a):
    if not (a.get("player_id") or a.get("event_id")):
        raise ToolError("give player_id or event_id")
    lim = a.get("limit") or 20
    rows = []
    offers = store.offers(ctx.con, league=a["league"], event_id=a.get("event_id"), player_id=a.get("player_id"),
                          stat=a.get("stat"), platform=a.get("platform"), as_of=ctx.now, limit=400)
    now = parse_utc(ctx.now)
    for rid, o in offers:
        ok, why = capabilities.gate(o)
        row = {"record_id": rid, "as_of": ctx.now, "known_at": o.known_at, "platform": o.platform,
               "offer_id": clean(o.offer_id, 80), "event_id": o.event_id, "player_id": o.player_id,
               "player": clean(o.player_name, 80), "stat": o.stat, "source_stat": clean(o.source_stat, 60),
               "scope": o.scope, "line": o.line, "variant": o.variant, "promotional": o.promotional,
               "availability": o.availability,
               "sides": [{"side": s.side, "offered": s.offered, "price_format": s.price_format, "price": s.price,
                          "bid": s.bid, "ask": s.ask, "payout_multiplier": s.multiplier} for s in o.sides],
               "listed_start_utc": o.listed_start_utc, "listed_time_kind": o.listed_time_kind,
               "source_updated_at": o.source_updated_at, "source_time_unknown": o.source_time_unknown,
               "observed_at": o.observed_at,
               "observed_age_minutes": _r((now - parse_utc(o.observed_at)).total_seconds() / 60, 1),
               "compatible_group": _group(o), "gradable": ok, "not_gradable_because": list(why)}
        if a.get("line_history"):
            row["line_history"] = [{"record_id": m["record_id"], "as_of": ctx.now, "known_at": m["known_at"],
                                    "revision": m["revision"], "line": r.line}
                                   for m, r in store.revisions(ctx.con, "offer", o.platform, o.offer_id)
                                   if m["known_at"] <= ctx.now][-10:]
        rows.append(row)
    rows.sort(key=lambda r: (r["stat"] or "", r["platform"], r["line"]))
    notes = ["prices are as the source lists them; observed_age_minutes = since Scout last read it (not the source's "
             "own freshness when source_time_unknown is true)",
             "compare prices only between rows with the same non-null compatible_group"]
    return _result(ctx, "market_quotes", a, rows[:lim], truncated=len(rows) > lim, notes=notes)


def t_forecasts(ctx, a):
    if not (a.get("player_id") or a.get("event_id")):
        raise ToolError("give player_id or event_id")
    lim = a.get("limit") or 10
    rows = []
    for fid, f in store.forecasts(ctx.con, league=a["league"], event_id=a.get("event_id"),
                                  player_id=a.get("player_id"), model_version=a.get("model_version"), limit=200):
        if f.forecast_at > ctx.now:
            continue
        rows.append({"forecast_id": f.forecast_id, "record_id": fid, "as_of": ctx.now, "forecast_at": f.forecast_at,
                     "event_id": f.event_id, "player_id": f.player_id, "stat": f.stat, "scope": f.scope,
                     "line": f.line, "model_version": f.model_version, "artifact_version": f.artifact_version,
                     "data_version": f.data_version, "calibration_version": f.calibration_version,
                     "grade_policy_version": f.grade_policy_version, "projection_mean": _r(f.projection_mean, 3),
                     "projection_median": _r(f.projection_median, 3),
                     "projection_minus_line": _r(f.projection_mean - f.line, 3) if f.projection_mean is not None else None,
                     "p_over": _r(f.p_over, 4), "p_under": _r(f.p_under, 4), "p_push": _r(f.p_push, 4),
                     "p_over_given_no_push": _r(f.p_over_given_no_push, 4),
                     "p_under_given_no_push": _r(f.p_under_given_no_push, 4),
                     "sides_available": list(f.sides_available), "chosen_side": f.chosen_side, "grade": f.grade,
                     "research_only": f.research_only, "offer_snapshot_ids": list(f.offer_snapshot_ids)[:5]})
        if len(rows) >= lim:
            break
    notes = ["p_over / p_under / p_push are unconditional (sum to 1); *_given_no_push are conditional and named so",
             "research_only = true means the version has not passed its validation gate - say so"]
    return _result(ctx, "forecasts", a, rows, notes=notes, truncated=len(rows) >= lim)


def t_player_context(ctx, a):
    rows = []
    q = ("SELECT r.record_id, r.known_at, r.payload FROM records r JOIN (SELECT source, source_key, MAX(revision) rv "
         "FROM records WHERE late = 0 AND kind='context' AND player_id=? AND known_at <= ? GROUP BY source, source_key) z ON "
         "r.source = z.source AND r.source_key = z.source_key AND r.revision = z.rv WHERE r.kind='context'")
    for r in ctx.con.execute(q, (a["player_id"], ctx.now)).fetchall():
        c = store._unpack(r["payload"])
        rows.append({"record_id": r["record_id"], "as_of": ctx.now, "known_at": r["known_at"], "kind": c["kind"],
                     "status": c["status"], "present": c["present"], "detail": clean(c.get("detail"), 60),
                     "source": c["source"], "source_as_of": c.get("as_of"),
                     "source_time_unknown": c.get("source_time_unknown")})
    notes = [] if rows else ["no context record for this player: status UNKNOWN (not healthy, not injured)"]
    return _result(ctx, "player_context", a, rows[:MAX_ROWS], notes=notes)


def t_factor_coverage(ctx, a):
    rows = [{"factor": f, "status": s, "where_or_why": w} for f, s, w, _ in FACTORS[a["league"]]]
    return _result(ctx, "factor_coverage", a, rows,
                   notes=["unavailable factors must be named as unavailable, never estimated"])


RUN = {"resolve_dates": t_resolve_dates, "find_players": t_find_players, "list_events": t_list_events,
       "player_game_log": t_player_game_log, "market_quotes": t_market_quotes, "forecasts": t_forecasts,
       "player_context": t_player_context, "factor_coverage": t_factor_coverage}
assert set(RUN) == set(SPECS)


def run(ctx, name, args) -> dict:
    """Validate then run one tool. Unknown tool / bad arguments -> ToolError (the caller returns it as an error)."""
    if name not in SPECS:
        raise ToolError(f"unknown tool '{clean(name, 40)}' - only read-only Scout tools exist")
    return RUN[name](ctx, validate(SPECS[name]["schema"], args))
