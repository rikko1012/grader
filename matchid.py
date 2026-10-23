"""
matchid.py - one canonical identity per CS2 match, shared by props, moneylines, slips and the projection's odds
link (Thunderpick), and - since the 2026-10-13 audit - settlement and evaluation (see the last paragraph).

Every platform spells teams and start times its own way (Underdog "Team Vitality", Polymarket "Overtake", Kalshi "6666",
BO3.gg "Vitality", HLTV "Overtake Sector"). This module turns (team A, team B, start) into ONE id that is the same on
every platform and in either team order, and records which platform market belongs to which match.

Team identity - team_key(name):
  "team:<HLTV team id>"  the name resolves EXACTLY to one row of HLTV's teams table after normalisation (case, accents,
                         punctuation, a leading "Team", a trailing Esports / E-sports / Esport / Gaming / Club / Clan / Team),
                         or through a maintained alias (team_aliases.json, each entry with its proof);
  "name:<normalised>"    a team HLTV only lists on its schedule (upcoming_matches) and not yet in its teams table;
  None                   anything else. Never fuzzy, never substring: an Academy / Junior / Youngsters / female ("fe") /
                         NXT / "2" squad is a different team from its main roster, and a name that hits two HLTV teams
                         after stripping affixes is unresolved rather than guessed.
  same_team(a, b): two platform spellings of one team by the same rule (the fallback odds: Polymarket, OddsPapi).

Match identity - canonical_match(team_a, team_b, start_iso, bo=None, event=None, scope="series"):
  "hltv:<HLTV match id>" exactly one HLTV match (schedule or finished) of exactly these two teams within WINDOW_H hours
                         (finished matches HLTV only dates: same UTC date as the start +/- WINDOW_H);
  "cs2:<12 hex>"         no HLTV match: a hash of (sorted team keys, start rounded to the hour UTC). The best-of is NOT
                         hashed: Kalshi markets carry no BO, so on 2026-10-12 a hashed BO made Polymarket's and Kalshi's
                         STATE vs THE UNIT two different ids. Two teams never play two series in one hour, so the BO adds
                         no separation; it is still used to choose between two HLTV candidates. The hour rounding alone
                         would fork one match whose platforms state 07:25 and 07:35 (or a Kalshi original start next to a
                         moved Polymarket start), so an id already issued for the same two teams within WINDOW_H is
                         reused (anchored_hash_id; anchors kept in memory and in match_links.db). A lookup with
                         mint=False (Thunderpick's timezone scan) never issues one;
  None                   a team is unresolved, two HLTV matches fit, or HLTV's schedule has never been readable (an id
                         made without it could fork from the hltv:<id> the same match gets later).
  Dated renames (team_aliases.json "renames", 2026-10-23): HLTV sometimes moves a roster to a NEW team id under a new name
  (LP 13304 -> BARCA 14107, same five players, from 2026-10-21) while a platform keeps the old name. A rename entry
  {from_team_id, to_team_id, valid_from} applies only inside canonical_match: when a side resolves to from_team_id, the
  start is on/after valid_from (an ISO UTC time, or a date = 00:00 UTC), from_team_id has NO HLTV match of its own in
  the window (against anyone - match-rules-4), and the pair with to_team_id has exactly one, that match is linked
  ("renamed" in the result). Settlement additionally requires the new team's lineup in that match to contain at least
  RENAME_MIN_PLAYERS of the entry's HLTV player ids (picklog._rename_roster_ok). Never a name alias: team_key('LP') stays
  team:13304, so LP's history, same_team and an LP line before the date (or with no BARCA match) are unchanged.
  Rematches (the same teams beyond the window) get different ids. scope is NOT part of the match: a series winner and a
  map 1 winner of the same match share match_id and differ in market_key(match_id, scope, platform, market_id).

HLTV data: the teams table (hourly), the schedule (5 min) and finished matches (15 min) are read by ONE background
refresh, each source on its own (one failing never blocks the others), kept in memory, in hltv_store (an HLTV outage or
a restart during one uses the last good copy) and - for the schedule - in match_links.db, so a match that has started
(HLTV drops it from upcoming_matches) keeps its exact start time. Member requests never touch the database.

Platform links: link(platform, event_id, market_id, match_id, outcome_team_map) in CS2_DATA_DIR/.cache/match_links.db
(SQLite, WAL, one transaction per write, idempotent upsert on (platform, market_id)). When a market first linked by
hash is later linked to its HLTV match, the old id is recorded as an alias (current_id() follows it).

What it decides for grading (corrected 2026-10-13 - this used to say "nothing here feeds or changes a grade"): since the
Thunderpick-first odds policy, canonical_match() decides whether a Thunderpick quote is linked to the match a line is
graded for (thunderpick.projection_odds via market_odds.select), so an alias or a rule here can change which market
feeds a grade. That state is versioned: mapping.revision() hashes team_aliases.json, comparison.VERIFIED_TEAM_ALIASES
and MATCHING_RULES_VERSION below; the revision is part of the shared-context key (cs2_scout) and is recorded with
every forecast (picklog mapping_rev, full content in picks.db mapping_revisions). Settlement (2026-10-13 audit) uses the
SAME identity: an hltv-<id> pick settles from that HLTV match, a BO3.gg-slug pick only from the one HLTV match
canonical_match(team, opponent, listed start) names (no date window; a hashed / ambiguous id is not settled from HLTV), and
evaluation (identity.assign rule 3) groups a slug under that settled HLTV match.
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import hltv
import hltv_store

DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent)
DB_PATH = DATA_DIR / ".cache" / "match_links.db"
ALIASES_PATH = Path(__file__).resolve().parent / "team_aliases.json"

# The matching rules' own version (2026-10-13): bump it whenever a rule here changes how a name or a match resolves
# (normalisation, affixes, WINDOW_H, candidate tie-breaks, alias loading) - it is hashed into mapping.revision(), so every
# forecast records which rules linked its odds. Alias FILE edits need no bump (the file itself is hashed).
# match-rules-2 (2026-10-13 audit): same_team - the fallback odds (Polymarket / OddsPapi) link teams by this identity
# (was substring / one-word containment there); the timezone scan's lookups never mint hashed ids (mint=False)
# match-rules-3 (2026-10-23, LP -> BARCA): dated team renames (team_aliases.json "renames", keyed on HLTV team ids) - a
# name that resolves to the old HLTV team, for a start on/after the rename's valid_from, links to the NEW team's HLTV match
# when (and only when) the old team has no HLTV match there and the new one has exactly one with the same opponent within
# WINDOW_H. Team identity (team_key / same_team / history reads) is unchanged: 'LP' stays HLTV 13304.
# match-rules-4 (2026-10-23 closeout, LP -> BARCA evidence): valid_from may be an exact UTC time (LP's own account
# announced "A LyP agora e BARCA Esports" 2026-10-20 18:00 UTC); the old team must have NO HLTV match of its own (any
# opponent) within WINDOW_H / that UTC date, not just none against this opponent; entries carry the HLTV player ids
# settlement checks the new team's lineup against.
MATCHING_RULES_VERSION = "match-rules-4"
RENAME_MIN_PLAYERS = 4        # settlement: the new team's lineup in the linked match must hold >= 4 of the entry's players
WINDOW_H = 3                  # an HLTV match of the same two teams starting within this many hours is the same match
TEAMS_TTL = 3600
SCHEDULE_TTL = 5 * 60
FINISHED_TTL = 15 * 60
FAIL_TTL = 30                 # a failed source is retried after this long (the previous good copy is used meanwhile)
SCHEDULE_MAX_AGE_H = hltv.SCHEDULE_MAX_AGE_H   # an HLTV schedule snapshot older than this is not trusted (same rule as hltv.py)
FINISHED_DAYS = 21            # finished HLTV matches kept for date-only linking
KEEP_DAYS = 60                # persisted schedule rows older than this are pruned
ROW_CACHE_TTL = 60            # match_id_for_row: one answer per (teams, start, bo) per minute
BACKGROUND = True             # tests set False: refreshes run inline

_AFFIX = re.compile(r"^(team)\s+|\s+(esports|e-sports|esport|e-sport|gaming|club|clan|team)$", re.I)
_LOCK = threading.Lock()      # guards _S
_INIT = threading.Lock()      # first load (single flight)
_DBW = threading.Lock()       # SQLite writes
_local = threading.local()
_S = {"teams": None, "teams_at": 0.0, "teams_ok": False,
      "schedule": None, "schedule_at": 0.0, "schedule_ok": False,
      "finished": None, "finished_at": 0.0, "finished_ok": False,
      "index": None, "pairs": {}, "busy": False, "loaded": False, "errors": {}}
_ROWS = {}                    # match_id_for_row cache: key -> (at, match_id)


# ---------------------------------------------------------------- names
def norm(s):
    """Lower-case letters and digits only, accents folded ('QUINTESSÊNCIA' -> 'quintessencia')."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def name_variants(name):
    """Exact normalised name first, then with one org affix removed at a time ('Team Falcons' -> 'falcons',
    'Bread Eaters Esports' -> 'breadeaters'). Squad markers (Academy, Junior, Youngsters, fe, NXT, 2) are never removed."""
    out, cur = [], " ".join(str(name or "").split())
    while cur:
        n = norm(cur)
        if len(n) >= 2 and n not in out:
            out.append(n)
        nxt = _AFFIX.sub("", cur).strip()
        if nxt == cur:
            break
        cur = nxt
    return out


def _load_aliases():
    """team_aliases.json (maintained, each entry with proof) + comparison.VERIFIED_TEAM_ALIASES.
    {normalised alias: HLTV name or "team:<id>"}."""
    out = {}
    try:
        data = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
        for e in data.get("aliases", []):
            target = f"team:{int(e['team_id'])}" if e.get("team_id") is not None else e.get("hltv_name")
            if e.get("name") and target:
                out[norm(e["name"])] = target
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"matchid: team_aliases.json unreadable ({type(e).__name__}) - no maintained aliases", file=sys.stderr, flush=True)
    try:
        import comparison
        for alias, name, _proof in comparison.VERIFIED_TEAM_ALIASES:
            out.setdefault(norm(alias), name)
    except Exception:
        pass
    return out


def _build_index(teams, schedule):
    """Lookup tables from HLTV's teams [(id, name)] and schedule rows."""
    exact, variant = {}, {}
    for tid, name in teams or ():
        vs = name_variants(name)
        if not vs:
            continue
        exact.setdefault(vs[0], set()).add(int(tid))
        for v in vs:
            variant.setdefault(v, set()).add(int(tid))
    known = set(variant)
    sched = {}                                    # names HLTV only has on its schedule (not in the teams table yet)
    for r in schedule or ():
        for nm in (r["team1"], r["team2"]):
            vs = name_variants(nm)
            if not vs or vs[0] == "tbd" or any(v in known for v in vs):
                continue
            for v in vs:
                sched.setdefault(v, set()).add(vs[0])
    return {"exact": exact, "variant": variant, "sched": sched, "aliases": _load_aliases(), "renames": _load_renames()}


def _valid_from(v):
    """A rename's valid_from as an aware UTC datetime: an ISO time ('2026-10-20T18:00:00Z') or a date (00:00 UTC)."""
    v = str(v).strip()
    if len(v) == 10:
        return datetime.combine(date.fromisoformat(v), datetime.min.time(), timezone.utc)
    t = _utc(v)
    if t is None:
        raise ValueError(v)
    return t


def _load_renames():
    """team_aliases.json "renames" (maintained, each with proof): {"team:<from id>": [(valid_from UTC datetime,
    "team:<to id>")]}, keyed on HLTV team ids, never on a name. An unreadable / incomplete entry is skipped (no rename =
    previous behaviour)."""
    out = {}
    try:
        data = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return out
    for e in data.get("renames") or []:
        try:
            a, b, d = int(e["from_team_id"]), int(e["to_team_id"]), _valid_from(e["valid_from"])
        except Exception:
            continue
        if a != b:
            out.setdefault(f"team:{a}", []).append((d, f"team:{b}"))
    return out


def successor_key(index, key, start):
    """The team key a dated rename gives `key` for a match starting at `start` (aware datetime or ISO), else None.
    index: matchid's lookup tables (None = the current ones). Two different successors valid at once = None."""
    try:
        index = index if index is not None else _index()
        st = start if isinstance(start, datetime) else _utc(start)
        if not index or not key or st is None:
            return None
        hits = {to for d, to in (index.get("renames") or {}).get(key, ()) if st >= d}
        return next(iter(hits)) if len(hits) == 1 else None
    except Exception:
        return None


def rename_players(from_id, to_id):
    """The HLTV player ids a maintained rename entry names (team_aliases.json "players"), as a frozenset; empty when
    the entry has none (settlement then never confirms the rename)."""
    try:
        data = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
        for e in data.get("renames") or []:
            if int(e.get("from_team_id", -1)) == int(from_id) and int(e.get("to_team_id", -1)) == int(to_id):
                return frozenset(int(p) for p in e.get("players") or ())
    except Exception:
        pass
    return frozenset()


def renamed_team_id(name, start):
    """The HLTV team id a dated rename gives this team name for a match at `start` (the name must resolve to the rename's
    old team), else None. Memory only. Settlement uses it to recognise the new team's rows in an hltv-<id> pick's own
    match (the opponent listed under the old name)."""
    try:
        if not name or not ready():
            return None
        index = _index()
        s = successor_key(index, _resolve(index, name)[0], start)
        return int(s[5:]) if s and s.startswith("team:") else None
    except Exception:
        return None


def _resolve(index, name, _depth=0):
    if not index or not name:
        return None, "no name" if not name else "HLTV teams unavailable"
    vs = name_variants(name)
    if not vs:
        return None, "empty name"
    hit = index["exact"].get(vs[0])
    if hit and len(hit) == 1:
        return f"team:{next(iter(hit))}", "exact"
    if hit:
        return None, f"'{name}' is {len(hit)} HLTV teams"
    alias = index["aliases"].get(vs[0])
    if alias and _depth == 0:
        if alias.startswith("team:"):
            return alias, "alias"
        k, why = _resolve(index, alias, 1)
        return (k, "alias") if k else (None, f"alias target '{alias}' not on HLTV ({why})")
    for v in vs:
        hit = index["variant"].get(v)
        if hit and len(hit) == 1:
            return f"team:{next(iter(hit))}", "variant"
        if hit:
            return None, f"'{name}' matches {len(hit)} HLTV teams after removing affixes"
    for v in vs:
        hit = index["sched"].get(v)
        if hit and len(hit) == 1:
            return f"name:{next(iter(hit))}", "HLTV schedule only"
        if hit:
            return None, f"'{name}' matches {len(hit)} HLTV schedule names"
    return None, f"'{name}' not on HLTV"


def team_key(name):
    """Canonical team key ('team:<HLTV id>' / 'name:<normalised>') or None. Exact after normalisation only."""
    return _resolve(_index(), name)[0]


def team_key_why(name):
    """(key or None, how it resolved / why not)."""
    return _resolve(_index(), name)


_IDENT = {}                   # same_team: name -> (team key or None, name tokens); cleared with the index / alias files
_ALIAS_T = {"sig": None, "fwd": {}, "rev": {}, "checked": 0.0}


def _alias_tables():
    """The maintained + verified aliases without HLTV's tables: {alias: 'team:<id>' | target name} and the reverse
    {target HLTV name: 'team:<id>'} - so a renamed / respelled team keeps its identity even before HLTV's teams load.
    Re-read when team_aliases.json changes (checked at most every 5 s)."""
    now = time.time()
    if _ALIAS_T["sig"] is not None and _ALIAS_T["sig"][0] == str(ALIASES_PATH) and now - _ALIAS_T["checked"] < 5:
        return _ALIAS_T["fwd"], _ALIAS_T["rev"]
    _ALIAS_T["checked"] = now
    try:
        sig = (str(ALIASES_PATH), ALIASES_PATH.stat().st_mtime_ns)
    except OSError:
        sig = (str(ALIASES_PATH), None)
    if _ALIAS_T["sig"] != sig:
        _IDENT.clear()
        rev = {}
        try:
            for e in json.loads(ALIASES_PATH.read_text(encoding="utf-8")).get("aliases", []):
                if e.get("team_id") is not None and e.get("hltv_name"):
                    rev[norm(e["hltv_name"])] = f"team:{int(e['team_id'])}"
        except Exception:
            pass
        _ALIAS_T.update(sig=sig, fwd=_load_aliases(), rev=rev)
    return _ALIAS_T["fwd"], _ALIAS_T["rev"]


def _ident(name):
    """(strong key, name tokens) of one spelling: the strong key is matchid's team identity (team_key: exact names,
    org affixes, maintained + verified aliases; without HLTV's tables the alias files alone); the tokens are its exact
    name variants plus an alias's target name."""
    k = " ".join(str(name or "").split())
    fwd, rev = _alias_tables()
    hit = _IDENT.get(k)
    if hit is None:
        if len(_IDENT) > 20000:
            _IDENT.clear()
        strong, toks = (team_key(k) if k else None), set(name_variants(k))
        tgt = fwd.get(norm(k)) if k else None
        if tgt and not tgt.startswith("team:"):
            toks |= set(name_variants(tgt))
        if not strong and k:
            strong = (tgt if tgt and tgt.startswith("team:") else None) or rev.get(norm(k)) or \
                (rev.get(norm(tgt)) if tgt else None)
        hit = _IDENT[k] = (strong, frozenset(toks))
    return hit


def same_team(a, b):
    """Two platform spellings of ONE team (the fallback odds' team match, 2026-10-13 audit C13): both have a team identity
    (HLTV id via exact names / org affixes / maintained + verified aliases) -> the same identity; otherwise the same exact
    name after normalisation and org affixes, or an alias's target name. Never fuzzy / substring: Academy / Junior /
    Young / fe / NXT / Force / Talent / 2 squads are other teams. Never raises."""
    try:
        (ka, va), (kb, vb) = _ident(a), _ident(b)
        if not va or not vb:
            return False
        if ka and kb:
            return ka == kb
        return bool(va & vb)
    except Exception:                             # identity never breaks a caller
        return False


# ---------------------------------------------------------------- HLTV sources (background, cached, stored)
def _db_teams():
    if not hltv.dsn():
        raise hltv.Unavailable("HLTV database not configured")
    con = hltv._connect()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT teamid, name FROM teams")
            return [(int(t), n) for t, n in cur.fetchall() if n]
    finally:
        con.close()


def _db_schedule():
    """HLTV's current schedule [{matchid, start (aware UTC), team1, team2, event, format}]. A stale snapshot is a failure."""
    if not hltv.dsn():
        raise hltv.Unavailable("HLTV database not configured")
    con = hltv._connect()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT max(scraped_at) >= now() - make_interval(hours => %s) FROM upcoming_matches", (SCHEDULE_MAX_AGE_H,))
            if not cur.fetchone()[0]:             # scraper behind: keep the previous copy rather than an empty schedule
                raise hltv.Unavailable(f"HLTV schedule snapshot older than {SCHEDULE_MAX_AGE_H} h")
            cur.execute("SELECT matchid, time, team1, team2, event, format FROM upcoming_matches WHERE matchid IS NOT NULL AND time IS NOT NULL")
            return [{"matchid": int(m), "start": _utc(t), "team1": a, "team2": b, "event": e or "", "format": f or ""}
                    for m, t, a, b, e, f in cur.fetchall()]
    finally:
        con.close()


def _db_finished():
    """Finished HLTV matches of the last FINISHED_DAYS: [{matchid, date, team_ids: [a, b]}] (HLTV dates them in UTC)."""
    if not hltv.dsn():
        raise hltv.Unavailable("HLTV database not configured")
    con = hltv._connect()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT mo.matchid, mo.date, array_agg(DISTINCT m.teamid)
                           FROM match_overview mo JOIN matches m ON m.matchid = mo.matchid
                           WHERE mo.date >= current_date - %s GROUP BY mo.matchid, mo.date""", (FINISHED_DAYS,))
            return [{"matchid": int(mid), "date": d, "team_ids": sorted(int(t) for t in ids if t is not None)}
                    for mid, d, ids in cur.fetchall()]
    finally:
        con.close()


_SOURCES = {"teams": (lambda: _db_teams(), TEAMS_TTL),
            "schedule": (lambda: _db_schedule(), SCHEDULE_TTL),
            "finished": (lambda: _db_finished(), FINISHED_TTL)}


def _refresh_source(name, force=False):
    """Refresh one source; never raises. A failure keeps the previous copy (memory, else hltv_store) and retries in FAIL_TTL."""
    loader, ttl = _SOURCES[name]
    with _LOCK:
        if not force and _S[name] is not None and time.time() - _S[name + "_at"] < ttl:
            return False
    try:
        val = loader()
        ok = True
    except Exception as e:
        val, ok = None, False
        _S["errors"][name] = hltv.err_text(e)
    if ok:
        with _LOCK:
            _S[name], _S[name + "_at"], _S[name + "_ok"] = val, time.time(), True
            _S["errors"].pop(name, None)
        hltv_store.save(("matchid", name), val)
        if name == "schedule" and val:
            _persist_schedule(val)
        return True
    with _LOCK:
        have = _S[name] is not None
    if not have:
        stored = hltv_store.load(("matchid", name))
        if stored is not None:
            with _LOCK:
                _S[name], _S[name + "_ok"] = stored[1], True
    with _LOCK:
        _S[name + "_at"] = time.time() - ttl + FAIL_TTL
    return have or _S[name] is not None


def refresh(force=False):
    """Refresh every stale source (each independently) and rebuild the lookup tables. Never raises."""
    try:
        changed = False
        for name in ("teams", "schedule", "finished"):
            try:
                changed = _refresh_source(name, force) or changed
            except Exception as e:                  # one source failing never blocks another
                _S["errors"][name] = f"{type(e).__name__}"
        if changed or _S["index"] is None:
            _rebuild()
    finally:
        with _LOCK:
            _S["busy"] = False


def _rebuild():
    with _LOCK:
        teams, schedule, finished = _S["teams"], _S["schedule"], _S["finished"]
    index = _build_index(teams, schedule)
    timed = {}
    for r in _stored_schedule():                  # everything HLTV has scheduled (incl. matches since dropped as started)
        timed[r["matchid"]] = r
    for r in schedule or ():                      # the live snapshot wins (rescheduled times)
        timed[r["matchid"]] = r
    pairs = {}
    for r in timed.values():
        ka, kb = _resolve(index, r["team1"])[0], _resolve(index, r["team2"])[0]
        if ka and kb and ka != kb:
            pairs.setdefault(tuple(sorted((ka, kb))), []).append(
                {"matchid": r["matchid"], "start": r["start"], "date": None, "format": r.get("format") or "", "event": r.get("event") or ""})
    for r in finished or ():
        ids = r.get("team_ids") or []
        if len(ids) != 2 or r["matchid"] in timed:
            continue                                  # known start time: matched by time only
        d = r["date"] if isinstance(r["date"], date) else date.fromisoformat(str(r["date"])[:10])
        pairs.setdefault(tuple(sorted(f"team:{i}" for i in ids)), []).append(
            {"matchid": r["matchid"], "start": None, "date": d, "format": "", "event": ""})
    with _LOCK:
        _S["index"], _S["pairs"] = index, pairs
        _ROWS.clear()
        _IDENT.clear()


def _kick():
    """Start the shared background refresh when something is stale (at most one at a time)."""
    now = time.time()
    with _LOCK:
        stale = any(_S[n] is None or now - _S[n + "_at"] >= ttl for n, (_, ttl) in _SOURCES.items())
        if not stale or _S["busy"]:
            return
        _S["busy"] = True
    if BACKGROUND:
        threading.Thread(target=refresh, name="matchid-refresh", daemon=True).start()
    else:
        refresh()


def _ensure():
    if not _S["loaded"]:
        with _INIT:                               # first use: the stored copy (fast), else one inline read for everyone
            if not _S["loaded"]:
                for name in _SOURCES:
                    stored = hltv_store.load(("matchid", name))
                    if stored is not None:
                        _S[name], _S[name + "_ok"] = stored[1], True
                if _S["teams"] is None:
                    with _LOCK:
                        _S["busy"] = True
                    refresh()
                else:
                    _rebuild()
                _S["loaded"] = True
    _kick()


def _index():
    _ensure()
    return _S["index"]


def _reset():
    """Forget everything in memory (tests; the stored copies on disk stay)."""
    with _LOCK:
        for n in _SOURCES:
            _S[n], _S[n + "_at"], _S[n + "_ok"] = None, 0.0, False
        _S.update(index=None, pairs={}, busy=False, loaded=False, errors={})
        _ROWS.clear()
        _IDENT.clear()
    with _HLOCK:
        _HASHED.clear()
        _HASHED_PATH[0] = None


def status():
    """Admin view: source ages / errors, how many teams and HLTV matches are known."""
    now = time.time()
    with _LOCK:
        return {n: {"ok": _S[n + "_ok"], "rows": len(_S[n] or ()), "age_s": round(now - _S[n + "_at"]) if _S[n + "_at"] else None,
                    "error": _S["errors"].get(n)} for n in _SOURCES} | {"pairs": len(_S["pairs"])}


def ready():
    """True once the lookup tables are loaded. Member paths (server.board_payload) skip linking until then rather than
    wait for the first HLTV read."""
    return bool(_S["loaded"]) and _S["index"] is not None


def warm():
    """Load the lookup tables now (server boot thread). Never raises; returns ready()."""
    try:
        _index()
    except Exception:
        pass
    return ready()


def link_stats():
    """Admin: what match_links.db holds (links per platform, distinct match ids, cs2 -> hltv aliases, kept schedule rows)."""
    try:
        con = _con()
        by = {p: n for p, n in con.execute("SELECT platform, COUNT(*) FROM links GROUP BY platform")}
        return {"links_by_platform": by,
                "linked_match_ids": con.execute("SELECT COUNT(DISTINCT match_id) FROM links").fetchone()[0],
                "id_aliases": con.execute("SELECT COUNT(*) FROM id_aliases").fetchone()[0],
                "persisted_schedule_rows": con.execute("SELECT COUNT(*) FROM hltv_matches").fetchone()[0]}
    except sqlite3.Error as e:
        return {"error": type(e).__name__}


# ---------------------------------------------------------------- matches
def _utc(t):
    if t is None or t == "":
        return None
    if isinstance(t, datetime):
        return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t / 1000 if t > 1e11 else t, timezone.utc)
    s = str(t).strip().replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if re.search(r"[+-]\d{2}$", s):               # Polymarket gameStartTime: '2026-10-12 01:00:00+00'
        s += ":00"
    try:
        return _utc(datetime.fromisoformat(s))
    except ValueError:
        return None


def _bo(bo):
    m = re.search(r"\d+", str(bo or ""))
    return int(m.group()) if m and int(m.group()) in (1, 2, 3, 5, 7) else None


def hash_id(keys, start):
    """'cs2:' + 12 hex of (sorted team keys, start rounded to the nearest hour UTC). No best-of (see the module doc).
    canonical_match never mints one blindly: it first reuses an id already issued for the same teams within WINDOW_H
    (anchored_hash_id) - the rounding alone would fork 07:25 / 07:35 or a rescheduled start into two ids."""
    st = _utc(start)
    hour = (st + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
    raw = "|".join([*sorted(keys), hour.strftime("%Y-%m-%dT%H")])
    return "cs2:" + hashlib.sha1(raw.encode()).hexdigest()[:12]


_HASHED = {}                  # tuple(sorted team keys) -> [(start, "cs2:..")] issued hashed ids (anchors)
_HASHED_PATH = [None]         # the match_links.db the anchors were loaded from
_HLOCK = threading.Lock()


def _load_hashed():
    """Anchors persisted in match_links.db (a restart keeps the id the first platform got). Caller holds _HLOCK."""
    if _HASHED_PATH[0] == DB_PATH:
        return
    _HASHED.clear()
    try:
        cut = time.time() - KEEP_DAYS * 86400
        for keys, start, mid in _con().execute("SELECT team_keys, start, match_id FROM hashed_ids WHERE at >= ?", (cut,)):
            st = _utc(start)
            if st is not None:
                _HASHED.setdefault(tuple(json.loads(keys)), []).append((st, mid))
    except (sqlite3.Error, ValueError):
        pass
    _HASHED_PATH[0] = DB_PATH


def _persist_hashed(keys, st, mid):
    try:
        with _DBW:
            con = _con()
            con.execute("BEGIN IMMEDIATE")
            try:
                now = time.time()
                con.execute("INSERT OR IGNORE INTO hashed_ids (team_keys, start, match_id, at) VALUES (?, ?, ?, ?)",
                            (json.dumps(list(keys)), st.isoformat(), mid, now))
                con.execute("DELETE FROM hashed_ids WHERE at < ?", (now - KEEP_DAYS * 86400,))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
    except Exception as e:                        # a locked / full disk only means the anchor isn't kept over a restart
        _S["errors"]["hashed_ids"] = type(e).__name__


def anchored_hash_id(keys, start):
    """The hashed id for these teams at this start: an id already issued for the same sorted team keys with a start
    within WINDOW_H (the nearest one) is reused - the first platform seen sets the anchor and every later platform, a
    rescheduled start or a prop row joins it - otherwise a new hash_id is issued and remembered (memory + match_links.db)."""
    st = _utc(start)
    k = tuple(sorted(keys))
    win = timedelta(hours=WINDOW_H)
    with _HLOCK:
        _load_hashed()
        near = sorted((abs(s - st), s, mid) for s, mid in _HASHED.get(k, ()) if abs(s - st) <= win)
        if near:
            return near[0][2]
        mid = hash_id(k, st)
        _HASHED.setdefault(k, []).append((st, mid))
    _persist_hashed(k, st, mid)
    return mid


def _fit(keys, st, bo=None, event=None):
    """The HLTV matches of this sorted key pair that fit a start (within WINDOW_H; finished matches by UTC date), after
    the best-of and event tie-breaks."""
    with _LOCK:
        cands = list(_S["pairs"].get(tuple(keys), ()))
    win = timedelta(hours=WINDOW_H)
    days = {(st - win).date(), st.date(), (st + win).date()}
    fit = [c for c in cands if (c["start"] is not None and abs(c["start"] - st) <= win)
           or (c["start"] is None and c["date"] in days and st <= datetime.now(timezone.utc) + win)]
    if len(fit) > 1 and _bo(bo):
        same_bo = [c for c in fit if _bo(c["format"]) == _bo(bo)]
        fit = same_bo if len(same_bo) == 1 else fit
    if len(fit) > 1 and event:
        same_ev = [c for c in fit if c["event"] and norm(c["event"]) == norm(event)]
        fit = same_ev if len(same_ev) == 1 else fit
    return fit


def _plays(key, st):
    """True when the team `key` has any HLTV match (any opponent) within WINDOW_H of st, or a finished one dated within
    the window's UTC dates - the same fit as _fit. A renamed team that still plays its own matches is not renamed."""
    with _LOCK:
        cands = [c for k, v in _S["pairs"].items() if key in k for c in v]
    win = timedelta(hours=WINDOW_H)
    days = {(st - win).date(), st.date(), (st + win).date()}
    return any((c["start"] is not None and abs(c["start"] - st) <= win) or (c["start"] is None and c["date"] in days)
               for c in cands)


def canonical_match(team_a, team_b, start_iso, bo=None, event=None, scope="series", mint=True):
    """{"match_id", "team_keys": [sorted], "flipped", "status": linked|ambiguous|unlinked, "reason", "source", "hltv_match",
    "scope"}. Never raises. match_id is None unless status is linked. mint=False: a lookup only - no HLTV match ->
    unlinked, never a new hashed id (2026-10-13 audit: Thunderpick's timezone scan tried 27 starts per event and left an
    anchor per window, so two platforms a few minutes apart could get two ids for one match)."""
    out = {"match_id": None, "team_keys": None, "flipped": False, "status": "unlinked", "reason": "", "source": None,
           "hltv_match": None, "scope": scope}
    try:
        index = _index()
        (ka, wa), (kb, wb) = _resolve(index, team_a), _resolve(index, team_b)
        if not ka or not kb:
            out["reason"] = "; ".join(w for k, w in ((ka, wa), (kb, wb)) if not k)
            return out
        if ka == kb:
            out["reason"] = f"'{team_a}' and '{team_b}' are the same HLTV team"
            return out
        keys = sorted((ka, kb))
        out.update(team_keys=keys, flipped=[ka, kb] != keys, team_keys_by_side=[ka, kb])
        st = _utc(start_iso)
        if st is None:
            out["reason"] = "no start time"
            return out
        with _LOCK:
            sched_ok = _S["schedule_ok"]
        fit = _fit(keys, st, bo, event)
        if not fit and not any(_plays(k, st) for k in (ka, kb) if successor_key(index, k, st)):
            # dated rename (match-rules-3): the old team id has no HLTV match here; its successor team may (LP -> BARCA).
            # Linked only when the successor pair has exactly one HLTV match - else the previous result stands
            sa, sb = successor_key(index, ka, st), successor_key(index, kb, st)
            for na, nb in ((sa or ka, kb), (ka, sb or kb), (sa or ka, sb or kb)):
                if (na, nb) == (ka, kb) or na == nb:
                    continue
                alt = _fit(sorted((na, nb)), st, bo, event)
                if len(alt) == 1:
                    keys = sorted((na, nb))
                    out.update(team_keys=keys, flipped=[na, nb] != keys, team_keys_by_side=[na, nb],
                               renamed={"from": [ka, kb], "to": [na, nb]})
                    fit = alt
                    break
        if len(fit) > 1:
            out.update(status="ambiguous", reason=f"{len(fit)} HLTV matches of these teams within {WINDOW_H} h: "
                                                  + ", ".join(str(c["matchid"]) for c in fit))
            return out
        if fit:
            c = fit[0]
            out.update(match_id=f"hltv:{c['matchid']}", status="linked", source="hltv", hltv_match=c["matchid"],
                       hltv_start=c["start"].isoformat() if c["start"] is not None else None, hltv_bo=_bo(c.get("format")),
                       reason="HLTV match " + ("on the schedule" if c["start"] is not None else "finished, same UTC date")
                       + (" (dated team rename)" if out.get("renamed") else ""))
            return out
        if not sched_ok:
            out["reason"] = "HLTV schedule unavailable - id withheld (it could fork from the HLTV id)"
            return out
        if not mint:
            out["reason"] = f"no HLTV match of these teams within {WINDOW_H} h (lookup only - no hashed id)"
            return out
        out.update(match_id=anchored_hash_id(keys, st), status="linked", source="hash",
                   reason=f"no HLTV match of these teams within {WINDOW_H} h - hashed id")
        return out
    except Exception as e:                        # identity never breaks a caller
        out["reason"] = f"error: {type(e).__name__}"
        return out


def market_key(match_id, scope, platform, market_id):
    """One market: the match + its scope ('series', 'map1', 'map2', ...) + platform + the platform's market id.
    None when the match is unlinked. Series and map markets of one match never share a key."""
    if not match_id:
        return None
    if not platform or market_id in (None, ""):
        raise ValueError("platform and market_id are required")
    sc = re.sub(r"\s+", "", str(scope or "series").lower())
    sc = {"match": "series", "serieswinner": "series", "moneyline": "series"}.get(sc, sc)
    sc = re.sub(r"^(?:map|m)?(\d)(?:winner)?$", r"map\1", sc)
    return f"{match_id}|{sc}|{str(platform).lower()}|{market_id}"


def match_id_for_row(row):
    """match_id for a prop-board row (team_name / opp_name, else the platform's team / opponent; start, else match_start;
    bo; event). None when the match can't be identified. Cached a minute per (teams, start, bo)."""
    a = row.get("team_name") or row.get("team")
    b = row.get("opp_name") or row.get("opponent")
    start = row.get("start") or row.get("match_start")
    k = (norm(a), norm(b), str(start or ""), _bo(row.get("bo")))
    now = time.time()
    hit = _ROWS.get(k)
    if hit and now - hit[0] < ROW_CACHE_TTL:
        return hit[1]
    mid = canonical_match(a, b, start, row.get("bo"), row.get("event"))["match_id"]
    if len(_ROWS) > 20000:
        _ROWS.clear()
    _ROWS[k] = (now, mid)
    return mid


# ---------------------------------------------------------------- persistence (match_links.db)
def _con():
    con = getattr(_local, "con", None)
    if con is None or getattr(_local, "path", None) != DB_PATH:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.executescript("""
            CREATE TABLE IF NOT EXISTS links (platform TEXT NOT NULL, market_id TEXT NOT NULL, event_id TEXT, match_id TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'series', outcome_team_map TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY (platform, market_id));
            CREATE INDEX IF NOT EXISTS links_match ON links (match_id);
            CREATE TABLE IF NOT EXISTS id_aliases (old_id TEXT PRIMARY KEY, new_id TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS hltv_matches (matchid INTEGER PRIMARY KEY, start TEXT NOT NULL, team1 TEXT, team2 TEXT,
                event TEXT, format TEXT, seen_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS hashed_ids (team_keys TEXT NOT NULL, start TEXT NOT NULL, match_id TEXT NOT NULL,
                at REAL NOT NULL, PRIMARY KEY (team_keys, match_id));
        """)
        _local.con, _local.path = con, DB_PATH
    return con


def link(platform, event_id, market_id, match_id, outcome_team_map, scope="series"):
    """Record that a platform market belongs to a match. outcome_team_map: which team each outcome pays on, e.g.
    Polymarket {"0": "team:1", "1": "team:2"} (outcome index -> team key), Kalshi {"yes": "team:1"} (each Kalshi market is
    its own YES/NO contract on one team). Idempotent upsert on (platform, market_id), one transaction.
    Returns {"changed": bool, "previous": old match_id or None}. Raises ValueError on a missing id."""
    if not platform or market_id in (None, "") or not match_id:
        raise ValueError("platform, market_id and match_id are required")
    p, mk = str(platform).lower(), str(market_id)
    otm = json.dumps(outcome_team_map or {}, sort_keys=True, separators=(",", ":"))
    ev = None if event_id is None else str(event_id)
    sc = str(scope or "series")
    now = time.time()
    with _DBW:
        con = _con()
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute("SELECT event_id, match_id, scope, outcome_team_map FROM links WHERE platform=? AND market_id=?",
                              (p, mk)).fetchone()
            if row and tuple(row) == (ev, match_id, sc, otm):
                con.execute("COMMIT")
                return {"changed": False, "previous": match_id}
            if row:
                con.execute("UPDATE links SET event_id=?, match_id=?, scope=?, outcome_team_map=?, updated_at=? WHERE platform=? AND market_id=?",
                            (ev, match_id, sc, otm, now, p, mk))
                if row[1] != match_id and str(row[1]).startswith("cs2:") and str(match_id).startswith("hltv:"):
                    con.execute("INSERT OR REPLACE INTO id_aliases (old_id, new_id, at) VALUES (?, ?, ?)", (row[1], match_id, now))
            else:
                con.execute("INSERT INTO links (platform, market_id, event_id, match_id, scope, outcome_team_map, created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (p, mk, ev, match_id, sc, otm, now, now))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return {"changed": True, "previous": row[1] if row else None}


def get_link(platform, market_id):
    row = _con().execute("SELECT platform, market_id, event_id, match_id, scope, outcome_team_map, created_at, updated_at "
                         "FROM links WHERE platform=? AND market_id=?", (str(platform).lower(), str(market_id))).fetchone()
    return _link_dict(row) if row else None


def links_for(match_id):
    """Every platform market linked to this match (or to a hashed id that now points to it)."""
    ids = [match_id] + [r[0] for r in _con().execute("SELECT old_id FROM id_aliases WHERE new_id=?", (match_id,))]
    rows = _con().execute(f"SELECT platform, market_id, event_id, match_id, scope, outcome_team_map, created_at, updated_at FROM links "
                          f"WHERE match_id IN ({','.join('?' * len(ids))}) ORDER BY platform, market_id", ids).fetchall()
    return [_link_dict(r) for r in rows]


def _link_dict(r):
    return {"platform": r[0], "market_id": r[1], "event_id": r[2], "match_id": r[3], "scope": r[4],
            "outcome_team_map": json.loads(r[5]), "created_at": r[6], "updated_at": r[7]}


def current_id(match_id):
    """A hashed id that was later linked to its HLTV match -> the HLTV id; anything else unchanged."""
    if not match_id:
        return match_id
    try:
        row = _con().execute("SELECT new_id FROM id_aliases WHERE old_id=?", (match_id,)).fetchone()
        return row[0] if row else match_id
    except sqlite3.Error:
        return match_id


def _persist_schedule(rows):
    """Keep every HLTV schedule row (matches HLTV drops from upcoming_matches once they start keep their exact time)."""
    now = time.time()
    try:
        with _DBW:
            con = _con()
            con.execute("BEGIN IMMEDIATE")
            try:
                con.executemany("INSERT INTO hltv_matches (matchid, start, team1, team2, event, format, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                                "ON CONFLICT(matchid) DO UPDATE SET start=excluded.start, team1=excluded.team1, team2=excluded.team2, "
                                "event=excluded.event, format=excluded.format, seen_at=excluded.seen_at",
                                [(r["matchid"], r["start"].isoformat(), r["team1"], r["team2"], r["event"], r["format"], now)
                                 for r in rows if r.get("start")])
                con.execute("DELETE FROM hltv_matches WHERE seen_at < ?", (now - KEEP_DAYS * 86400,))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
    except Exception as e:                        # a locked / full disk only means the copy isn't kept
        _S["errors"]["persist"] = type(e).__name__


def _stored_schedule():
    try:
        rows = _con().execute("SELECT matchid, start, team1, team2, event, format FROM hltv_matches").fetchall()
    except sqlite3.Error:
        return []
    return [{"matchid": m, "start": _utc(s), "team1": a, "team2": b, "event": e or "", "format": f or ""}
            for m, s, a, b, e, f in rows if _utc(s)]
