"""Extra book providers (Phase 4): a sport-neutral registry, ONE background poller for direct feeds, and a health record per source.

Config, rules and payouts: book_providers.json (each rule with its source; what no source states stays unknown).
Flags: one per provider (SCOUT_BOOK_SLEEPER, SCOUT_BOOK_DABBLE, SCOUT_BOOK_FANDUEL, SCOUT_BOOK_BETRIVERS, SCOUT_BOOK_ODDSAPI); the running
server turns on the ones that need no key (server.DEFAULT_ON), Render "0" turns one off. Apps that refuse this server, need a login or
a location check, or have no board that answers without an account (Betr, ParlayPlay, Boom) are listed under "blocked" in the config
with the evidence: no flag, no poller, a "blocked" row on the admin coverage page.
Off, nothing here runs: no thread, no request, no file.

Direct feeds (Sleeper, Dabble) are polled by one daemon thread, never by a member request, so 700 members at once cost nothing
upstream. Each poll:
  - rate limit: no poll before poll_s since the last attempt;
  - conditional GET (If-None-Match): an unchanged answer is a 304 and is not parsed again;
  - retries: up to `tries` attempts inside one poll, only for network errors and 5xx, with a short growing gap;
  - backoff: after a failed poll the next one waits backoff_base_s x 2^(failures-1) (capped at backoff_max_s, +-20% jitter);
    a Retry-After header wins when it is longer. 429 / 403 are never retried inside the poll; a 403 (the site refuses this
    server) waits refused_backoff_s and shows "refused" on the admin page: a refusal is never worked around;
  - freshness: offers carry observed_at (our last good read) and source_at (the provider's own update time). After
    stale_after_s without a good read the provider is "stale" and its lines are hidden, never shown as current
    (transient-failure rule: a blip hides a line, it never changes one).
Sports-service providers (FanDuel, BetRivers, The Odds API) are not polled here: the sports service collects them and their rows
arrive in the NBA / NFL snapshots this server already reads (workspace_sports). Their health is measured from those rows.

This module imports no CS2, LoL or sports code."""
from __future__ import annotations

import gzip
import json
import os
import random
import re
import statistics
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("book_providers.json")
UA = "1TimeScout/1.0 (+https://cs2-scout.onrender.com; partner line reader)"
_CFG = {"doc": None}
_LOCK = threading.Lock()
_THREAD = {"t": None, "dir": None}
_STATE = {}                         # provider id -> dict (see _blank)
SLEEPER_SPORT = {"cs": "cs2", "lol": "lol", "nba": "nba", "nfl": "nfl"}
SLEEPER_CODE = {v: k for k, v in SLEEPER_SPORT.items()}


def config():
    if _CFG["doc"] is None:
        _CFG["doc"] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return _CFG["doc"]


def providers():
    return config()["providers"]


def enabled(pid):
    p = providers().get(pid)
    return bool(p) and os.environ.get(p["flag"]) == "1"


def enabled_ids():
    return [pid for pid in providers() if enabled(pid)]


def public(pid):
    """What a member page may know about a provider: label, kind, sports and rules with sources. Never a feed URL or a key."""
    p = providers()[pid]
    return {"id": pid, "label": p["label"], "short": p.get("short"), "kind": p["kind"], "sports": list(p["sports"]),
            "rules": p.get("rules") or {}}


def features():
    """/me: the enabled providers, or None (flag-off pages carry no key at all)."""
    ids = enabled_ids()
    return [public(pid) for pid in ids] or None


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


# ------------------------------------------------------------------------------------------------------------- state

def _blank(pid):
    return {"id": pid, "offers": [], "at": None, "attempt_at": None, "next_at": 0.0, "failures": 0, "backoff_until": 0.0,
            "last_error": None, "last_status": None, "etag": None, "requests": 0, "not_modified": 0, "parsed_at": None,
            "skipped": {}, "players": {}, "lookups": 0}


def state(pid):
    with _LOCK:
        return _STATE.setdefault(pid, _blank(pid))


def reset():
    """Tests: forget every provider state and the cached config."""
    with _LOCK:
        _STATE.clear()
    _CFG["doc"] = None


# ------------------------------------------------------------------------------------------------------------ http

class FetchError(Exception):
    def __init__(self, msg, status=None, retry_after=None):
        super().__init__(msg)
        self.status, self.retry_after = status, retry_after


def _retry_after(v):
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return None


def http_get(url, etag=None, timeout=12.0, tries=2, opener=None, sleep=time.sleep):
    """-> (status, body bytes or None for 304, etag). Retries network errors and 5xx only; raises FetchError otherwise."""
    last = None
    for attempt in range(max(1, tries)):
        headers = {"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "gzip"}
        if etag:
            headers["If-None-Match"] = etag
        req = urllib.request.Request(url, headers=headers)
        try:
            with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
                raw = r.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024:
                    raise FetchError("answer too large", r.status)
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return r.status, raw, r.headers.get("ETag")
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return 304, None, etag
            ra = _retry_after(e.headers.get("Retry-After") if e.headers else None)
            last = FetchError(f"HTTP {e.code}", e.code, ra)
            if e.code < 500:
                raise last
        except FetchError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            last = FetchError(type(e).__name__)
        if attempt + 1 < tries:
            sleep(0.5 * (attempt + 1))
    raise last


# ------------------------------------------------------------------------------------------------------- scheduling

def due(st, feed, now):
    return now >= st["next_at"] and now >= st["backoff_until"]


def on_success(st, feed, now):
    st.update(at=now, failures=0, backoff_until=0.0, last_error=None, next_at=now + feed["poll_s"])


def on_failure(st, feed, now, err, rand=random.random):
    st["failures"] += 1
    wait = min(feed["backoff_max_s"], feed["backoff_base_s"] * 2 ** (st["failures"] - 1)) * (0.8 + 0.4 * rand())
    if getattr(err, "retry_after", None):
        wait = max(wait, min(err.retry_after, 6 * 3600))
    if getattr(err, "status", None) == 403:
        wait = max(wait, feed.get("refused_backoff_s", feed["backoff_max_s"]))
    st.update(backoff_until=now + wait, next_at=now + max(wait, feed["poll_s"]), last_error=str(err)[:80],
              last_status=getattr(err, "status", None))


def freshness(st, feed, now):
    """ok / stale / starting: lines are shown only while ok."""
    if not st["at"]:
        return "starting" if not st["failures"] else "failing"
    if now - st["at"] > feed["stale_after_s"]:
        return "stale"
    return "ok"


# ---------------------------------------------------------------------------------------------------------- Sleeper

def _player_cache_path(data_dir):
    return Path(data_dir) / "book_providers_players.json"


def load_players(data_dir):
    try:
        return json.loads(_player_cache_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_players(data_dir, players):
    p = _player_cache_path(data_dir)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(players, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)


def player_name(doc, sport):
    """Display name a board uses: the gamer tag for esports, first + last for NBA / NFL."""
    if sport in ("cs2", "lol"):
        return (doc.get("metadata") or {}).get("username") or doc.get("username") or ""
    return " ".join(x for x in (doc.get("first_name"), doc.get("last_name")) if x)


def parse_sleeper(body, now, players, stats):
    """Raw /lines/available answer -> (offers, skipped counts, wanted player ids). Pure: no network, no clock."""
    offers, skipped, wanted = [], {}, set()

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1
    for r in body if isinstance(body, list) else []:
        try:
            sport = SLEEPER_SPORT.get(r.get("sport"))
            if sport is None:
                continue
            if r.get("status") != "active" or r.get("game_status") != "pre_game":
                skip("not open before the game"); continue
            if r.get("subject_type") != "player" or r.get("outcome_type") != "over_under":
                skip("not a player over / under"); continue
            if r.get("line_type") != "normal":
                skip("alternate or promo line"); continue
            mapped = (stats.get(sport) or {}).get(r.get("wager_type"))
            if not mapped:
                skip(f"unmapped stat: {sport} {r.get('wager_type')}"[:60]); continue
            sides = {o.get("outcome"): o for o in r.get("options") or [] if o.get("status") == "active"}
            lines = {o.get("outcome_value") for o in sides.values()}
            if len(lines) != 1 or not sides:
                skip("sides disagree on the line"); continue
            line = float(next(iter(lines)))

            def side(name):
                o = sides.get(name)
                try:
                    m = float(o["payout_multiplier"]) if o else None
                except (TypeError, ValueError, KeyError):
                    m = None
                return {"offered": m is not None and m > 1, "payout": m if m and m > 1 else None, "price": None,
                        "line_id": str(o.get("line_id")) if o else None}
            sid = str(r.get("subject_id"))
            pdoc = (players.get(sport) or {}).get(sid)
            if not pdoc:
                wanted.add((sport, sid))
                skip("player name not read yet"); continue
            team = next((o.get("subject_team") for o in sides.values() if o.get("subject_team")), None) or pdoc.get("team")
            upd = r.get("updated_at")
            offers.append({"provider": "sleeper", "book": "sleeper", "sport": sport, "raw_stat": r.get("wager_type"), "stat": mapped[0],
                           "period": mapped[1], "subject_id": sid, "player": pdoc.get("name") or "", "player_norm": norm(pdoc.get("name")),
                           "team": team or "", "team_norm": norm(team), "game_id": str(r.get("game_id") or ""), "line": line,
                           "over": side("over"), "under": side("under"),
                           "source_at": float(upd) / 1000.0 if isinstance(upd, (int, float)) else None, "observed_at": now})
        except (TypeError, ValueError, KeyError, AttributeError):
            skip("unreadable line")
    return offers, skipped, wanted


def poll_sleeper(data_dir, now=None, fetch=http_get, sleep=time.sleep, clock=time.time):
    """One scheduled Sleeper poll. Returns the provider state. Never raises."""
    pid = "sleeper"
    p = providers()[pid]
    feed, st = p["feed"], state(pid)
    now = clock() if now is None else now
    if not due(st, feed, now):
        return st
    st["attempt_at"] = now
    if not st["players"]:
        st["players"] = load_players(data_dir)
    try:
        st["requests"] += 1
        status, raw, etag = fetch(feed["lines_url"], etag=st["etag"], timeout=feed["timeout_s"], tries=feed["tries"])
        st["last_status"] = status
        if status == 304:
            if st.get("_body") is None:
                st["etag"] = None
                raise FetchError("not modified, but no copy is held")
            st["not_modified"] += 1
            body = st["_body"]
        else:
            body = json.loads(raw)
            st["etag"] = etag
            # keep only our sports' rows without the history arrays (a few hundred KB), never the whole answer
            drop = ("recent_performance", "pick_stats")
            st["_body"] = [{k: v for k, v in r.items() if k not in drop} for r in body
                           if isinstance(r, dict) and r.get("sport") in SLEEPER_SPORT] if isinstance(body, list) else []
            del raw
            body = st["_body"]
    except FetchError as e:
        on_failure(st, feed, now, e)
        return st
    except ValueError:
        on_failure(st, feed, now, FetchError("unreadable answer"))
        return st
    stats = p["stats"]
    offers, skipped, wanted = parse_sleeper(body, now, _names(st["players"]), stats)
    # names for players not seen before: a bounded number per poll, spaced out, cached on disk for player_cache_days
    looked, changed = 0, False
    for sport, sid in sorted(wanted):
        if looked >= feed["player_lookups_per_poll"]:
            break
        looked += 1
        try:
            st["requests"] += 1
            _, raw, _ = fetch(feed["player_url"].format(sport=SLEEPER_CODE[sport], id=sid), timeout=feed["timeout_s"], tries=1)
            doc = json.loads(raw) if raw else None
        except (FetchError, ValueError):
            doc = None
        if isinstance(doc, dict) and doc.get("player_id"):
            st["players"].setdefault(sport, {})[sid] = {"name": player_name(doc, sport), "team": doc.get("team"), "at": now}
            changed = True
        sleep(feed["player_lookup_gap_s"])
    st["lookups"] += looked
    if changed:
        try:
            save_players(data_dir, st["players"])
        except OSError:
            pass
        offers, skipped, _ = parse_sleeper(body, now, _names(st["players"]), stats)
    keep_s = feed["player_cache_days"] * 86400
    for sport in list(st["players"]):
        st["players"][sport] = {k: v for k, v in st["players"][sport].items() if now - (v.get("at") or 0) <= keep_s}
    st.update(offers=offers, skipped=skipped, parsed_at=now)
    on_success(st, feed, now)
    return st


def _names(players):
    return {s: {k: {"name": v.get("name"), "team": v.get("team")} for k, v in d.items()} for s, d in players.items()}


# ----------------------------------------------------------------------------------------------------------- Dabble
# api.dabble.com, read without credentials (2026-11-05). Discovery: /competitions/active/ (a competition by its exact name) ->
# /competitions/{id}/sport-fixtures (fixture ids and start times only, re-read every fixtures_refresh_s; NFL is 4 MB) ->
# /sportfixtures/details/{id} per fixture starting within horizon_h (conditional on its ETag). Lines come only from the structured
# playerProps rows (one per side: player, team, stats keys, value, lineType) joined to prices on (marketId, selectionId); market and
# selection name strings are never parsed. A side without a price row is a stale selection and is not offered.
# Review 2026-11-05: each offer carries its fixture's teams and start (workspace_books.match checks both against the board's match);
# one poll stays inside poll_budget_s and stops after max_failures_in_a_row network failures (the poller thread is shared with
# Sleeper); the fixture cap is per sport and counted; one sport's list failing keeps its previous ids; discovery that finds none
# of the configured competitions is a failure, never a green 0. Dabble's price never enters +EV / arbitrage math (price_comparable).

def _iso_ts(v):
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def dabble_stat_key(stats):
    """playerProps.stats -> the config key: one stat as is, a combo as its keys joined with '+' in sorted order."""
    if not isinstance(stats, list) or not stats or not all(isinstance(x, str) and x for x in stats):
        return None
    return "+".join(sorted(stats))


def parse_dabble_competitions(doc, wanted):
    """/competitions/active/ -> {sport: competition id}. Exact name first; the configured id only when that name is absent."""
    rows = ((doc or {}).get("data") or {}).get("activeCompetitions") or []
    rows = [c for c in rows if isinstance(c, dict)]
    out = {}
    for sport, want in wanted.items():
        by_name = [c.get("id") for c in rows if c.get("name") == want["name"]]
        if len(by_name) == 1 and by_name[0]:
            out[sport] = by_name[0]
        elif any(c.get("id") == want["id"] for c in rows):
            out[sport] = want["id"]
    return out


def parse_dabble_fixtures(doc, now, horizon_s):
    """/competitions/{id}/sport-fixtures -> [(fixture id, start epoch)] open and starting within horizon_s. Only ids and times kept."""
    out = []
    for f in (doc or {}).get("data") or []:
        if not isinstance(f, dict) or f.get("status") != "Open" or not f.get("id"):
            continue
        t = _iso_ts(f.get("advertisedStart"))
        if t is not None and now < t <= now + horizon_s:
            out.append((str(f["id"]), t))
    return out


# Dabble names NBA / NFL teams in full ("Orlando Magic"); the board uses ESPN codes ("ORL"). An exact full-name table, never fuzzy:
# a name not in it stays as it is and the line is counted "team does not match".
DABBLE_TEAM_CODES = {
    "nba": {"Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET", "Golden State Warriors": "GS", "Houston Rockets": "HOU", "Indiana Pacers": "IND", "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NO", "New York Knicks": "NY", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SA", "Toronto Raptors": "TOR", "Utah Jazz": "UTAH", "Washington Wizards": "WSH"},
    "nfl": {"Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB", "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG", "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WSH"},
}


def _dabble_team(sport, name):
    return (DABBLE_TEAM_CODES.get(sport) or {}).get(name, name)


def parse_dabble(doc, now, sport, stats):
    """One /sportfixtures/details/{id} answer -> (offers, skipped counts). Pure: no network, no clock. Counts are per market."""
    offers, skipped = [], {}

    def skip(why, n=1):
        skipped[why] = skipped.get(why, 0) + n
    d = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(d, dict):
        skip("unreadable fixture")
        return offers, skipped
    props = [r for r in d.get("playerProps") or [] if isinstance(r, dict)]
    start = _iso_ts(d.get("advertisedStart"))
    if d.get("status") != "Open" or start is None or start <= now:
        skip("not open before the game", max(1, len({r.get("marketId") for r in props})))
        return offers, skipped
    markets = {m.get("id"): m for m in d.get("markets") or [] if isinstance(m, dict)}
    prices = {(x.get("marketId"), x.get("selectionId")): x.get("price") for x in d.get("prices") or [] if isinstance(x, dict)}
    groups = {}
    for r in props:
        groups.setdefault((r.get("marketId"), r.get("value")), []).append(r)
    smap = stats.get(sport) or {}
    cand, ids = {}, {}
    fid = str(d.get("id") or "")
    # the fixture's own teams and start: the board match checks the line belongs to the board's match, not a namesake's or a later one
    event_teams = sorted({norm(_dabble_team(sport, t.get("name") or "")) for t in d.get("teams") or [] if isinstance(t, dict) and norm(t.get("name") or "")})
    for (mid, value), rows in groups.items():
        try:
            m = markets.get(mid) or {}
            if m.get("status") != "open" or m.get("isDfsAllowed") is not True or m.get("isDisplayed") is False:
                skip("market not open for pick'em"); continue
            sides, bad = {}, False
            for r in rows:
                if r.get("isDisplayed") is False:
                    continue
                lt = r.get("lineType")
                bad = bad or lt in sides or lt not in ("over", "under")
                sides[lt] = r
            if bad or not sides:
                skip("sides unreadable"); continue
            if any(r.get("isOptimal") is False for r in sides.values()):
                skip("alternate line (not the main line)"); continue
            r0 = next(iter(sides.values()))
            key = dabble_stat_key(r0.get("stats"))
            if any(dabble_stat_key(r.get("stats")) != key or r.get("playerId") != r0.get("playerId") for r in sides.values()):
                skip("sides disagree"); continue
            mapped = smap.get(key)
            if not mapped:
                skip(f"unmapped stat: {sport} {key}"[:60]); continue
            name = r0.get("playerName")
            if not isinstance(name, str) or not norm(name):
                skip("no player name"); continue
            line = float(value)

            def side(lt):
                r = sides.get(lt)
                try:
                    x = float(prices.get((mid, r.get("selectionId")))) if r else None
                except (TypeError, ValueError):
                    x = None
                ok = x is not None and x > 1
                return {"offered": ok, "payout": None, "price": x if ok else None}
            over, under = side("over"), side("under")
            if not (over["offered"] or under["offered"]):
                skip("no price on either side"); continue
            team = _dabble_team(sport, r0.get("teamName") or "")
            o = {"provider": "dabble", "book": "dabble", "sport": sport, "raw_stat": key, "stat": mapped[0], "period": mapped[1],
                 "subject_id": str(r0.get("playerId") or ""), "player": name, "player_norm": norm(name), "team": team,
                 "team_norm": norm(team), "game_id": fid, "line": line, "over": over, "under": under, "source_at": None, "observed_at": now,
                 "event_teams": event_teams, "event_start": start}
            cand.setdefault((o["player_norm"], o["stat"], o["period"]), []).append(o)
            ids.setdefault(o["player_norm"], set()).add(o["subject_id"])
        except (TypeError, ValueError, KeyError, AttributeError):
            skip("unreadable line")
    for (pn, _, _), lst in cand.items():
        if len(ids.get(pn, ())) > 1:
            skip("two players share this name", len(lst)); continue
        if len({o["line"] for o in lst}) > 1:
            skip("two main lines for one market", len(lst)); continue
        offers.append(lst[0])
        if len(lst) > 1:
            skip("same line listed twice", len(lst) - 1)
    return offers, skipped


def _json(raw):
    if raw is None:
        raise FetchError("not modified, but no copy is held")
    return json.loads(raw)


def poll_budget_s(feed):
    """Wall-clock seconds one poll may spend. All direct feeds share the one poller thread, so a slow or timing-out app must
    hand the thread back long before another app's lines go stale (Sleeper stale_after_s 900): at most a third of the
    shortest stale_after_s of any direct feed, and never more than this feed's poll_s."""
    other = [p["feed"]["stale_after_s"] for p in providers().values() if p.get("source") == "direct" and p.get("feed")]
    cap = min([feed["poll_s"], feed["stale_after_s"] / 3] + [s / 3 for s in other])
    return min(feed.get("poll_budget_s") or cap, cap)


def _dabble_list_due(st, feed, now):
    """The fixture lists are re-read every fixtures_refresh_s, and sooner (not before fixtures_min_refresh_s) when a cached start
    has passed: the fixture may have moved later, and a new fixture may have been posted."""
    at = st.get("fixtures_at")
    if at is None or now >= st.get("fixtures_next_at", at + feed["fixtures_refresh_s"]):
        return True
    passed = any(start <= now for _, start in (st.get("fixtures") or {}).values())
    return passed and now - at >= feed["fixtures_min_refresh_s"]


def _dabble_read_lists(st, p, feed, now, fetch, kw, over_budget):
    """Competitions, then one fixture list per sport. Fails the poll only when the competitions read fails or names none of the
    configured competitions (a changed or stripped answer is never a green 'ok' with 0 lines). One sport's list failing keeps
    that sport's previous fixture ids and is counted; the others are read as usual."""
    base = feed["base_url"]
    st["requests"] += 1
    _, raw, _ = fetch(base + feed["competitions_path"], **kw)
    comps = parse_dabble_competitions(_json(raw), feed["competitions"])
    del raw
    if not comps:
        raise FetchError("no configured competition found")
    old, found, sk = st.get("fixtures") or {}, {}, {}
    for sport in p["sports"]:
        if sport not in comps:
            continue
        try:
            if over_budget():
                raise FetchError("time budget")
            st["requests"] += 1
            _, raw, _ = fetch(base + feed["fixtures_path"].format(id=comps[sport]), **kw)
            rows = parse_dabble_fixtures(_json(raw), now, feed["horizon_h"] * 3600)
            del raw
        except FetchError as e:
            if e.status in (403, 429):
                raise
            sk[f"fixture list failed: {sport}"] = 1
            found.update({f: v for f, v in old.items() if v[0] == sport})
            continue
        except ValueError:
            sk[f"fixture list failed: {sport}"] = 1
            found.update({f: v for f, v in old.items() if v[0] == sport})
            continue
        for fid, start in rows:
            found[fid] = (sport, start)
    nxt = now + (feed["fixtures_min_refresh_s"] if sk else feed["fixtures_refresh_s"])     # a failed list is retried sooner
    st.update(fixtures=found, fixtures_at=now, fixtures_next_at=nxt, competitions=sorted(comps), list_skipped=sk)


def poll_dabble(data_dir, now=None, fetch=http_get, sleep=time.sleep, clock=time.time):
    """One scheduled Dabble poll. Returns the provider state. Never raises. Holds only the parsed offers per fixture (no bodies)."""
    pid = "dabble"
    p = providers()[pid]
    feed, st = p["feed"], state(pid)
    now = clock() if now is None else now
    if not due(st, feed, now):
        return st
    st["attempt_at"] = now
    base, kw = feed["base_url"], {"timeout": feed["timeout_s"], "tries": feed["tries"]}
    cache = st.setdefault("by_fixture", {})          # fixture id -> {"etag", "offers", "skipped", "at"}
    t0, budget = clock(), poll_budget_s(feed)
    over_budget = lambda: clock() - t0 > budget      # noqa: E731 - wall clock of this poll, never the injected `now`
    try:
        if _dabble_list_due(st, feed, now):
            _dabble_read_lists(st, p, feed, now, fetch, kw, over_budget)
        live = sorted((start, fid, sport) for fid, (sport, start) in (st.get("fixtures") or {}).items() if start > now)
        offers, skipped, ok, failed, in_a_row = [], dict(st.get("list_skipped") or {}), 0, 0, 0
        per, kept = {}, []
        for x in live:                                             # the cap is per sport: a heavy NBA / NFL day never drops CS2 / LoL
            per[x[2]] = per.get(x[2], 0) + 1
            if per[x[2]] <= feed["max_fixtures_per_sport"]:
                kept.append(x)
            else:
                skipped[f"over the fixture cap: {x[2]}"] = skipped.get(f"over the fixture cap: {x[2]}", 0) + 1
        live = kept
        # least recently read first, so a poll cut short by its time budget reads the others next time
        order = sorted(live, key=lambda x: ((cache.get(x[1]) or {}).get("at") or 0, x[0]))
        stopped = 0
        for i, (start, fid, sport) in enumerate(order):
            c = cache.get(fid)
            if i and (stopped or over_budget()):
                stopped += 1                                       # not read this poll: its last read stays until stale
            else:
                if i:
                    sleep(feed["detail_gap_s"])
                try:
                    st["requests"] += 1
                    status, raw, etag = fetch(base + feed["details_path"].format(id=fid), etag=(c or {}).get("etag"), **kw)
                    if status == 304 and c:
                        st["not_modified"] += 1
                        c["at"] = now
                        for o in c["offers"]:
                            o["observed_at"] = now
                    else:
                        doc = _json(raw)
                        del raw
                        o, sk = parse_dabble(doc, now, sport, p["stats"])
                        moved = _iso_ts((doc.get("data") or {}).get("advertisedStart")) if isinstance(doc, dict) else None
                        if moved is not None and moved != start:   # the fixture's own start wins over the cached list
                            st["fixtures"][fid] = (sport, moved)
                        c = cache[fid] = {"etag": etag, "offers": o, "skipped": sk, "at": now}
                    ok += 1
                    in_a_row = 0
                except FetchError as e:
                    if e.status in (403, 429):
                        raise
                    failed += 1
                    in_a_row += 1
                    if in_a_row >= feed["max_failures_in_a_row"]:
                        raise FetchError(f"{in_a_row} fixture reads failed in a row ({str(e)[:30]})")
                except ValueError:
                    failed += 1
            if c and now - c["at"] <= feed["stale_after_s"]:      # a fixture not read this time keeps its last read until stale
                offers.extend(c["offers"])
                for k, v in c["skipped"].items():
                    skipped[k] = skipped.get(k, 0) + v
        for fid in [f for f in cache if f not in {x[1] for x in live}]:
            del cache[fid]                                         # started, gone or over the cap: forget it
        if failed:
            skipped["fixture read failed"] = failed
        if stopped:
            skipped["not read this poll (time budget)"] = stopped
        if live and not ok:
            raise FetchError("every fixture read failed")
    except FetchError as e:
        on_failure(st, feed, now, e)
        return st
    except ValueError:
        on_failure(st, feed, now, FetchError("unreadable answer"))
        return st
    if len(offers) > feed["max_offers"]:
        skipped["over the row cap"] = len(offers) - feed["max_offers"]
        offers = offers[:feed["max_offers"]]
    st.update(offers=offers, skipped=skipped, parsed_at=now, fixtures_read=len(live))
    on_success(st, feed, now)
    return st


POLLERS = {"sleeper": poll_sleeper, "dabble": poll_dabble}


def offers(pid, sport=None, now=None):
    """Current offers of a direct provider, [] when it is off or not fresh (a stale feed hides its lines)."""
    if not enabled(pid) or pid not in POLLERS:
        return []
    st, feed = state(pid), providers()[pid]["feed"]
    if freshness(st, feed, time.time() if now is None else now) != "ok":
        return []
    return [o for o in st["offers"] if sport is None or o["sport"] == sport]


def start(data_dir):
    """Start the one poller thread when any direct provider is on. Idempotent; a no-op with every flag off."""
    if not any(enabled(pid) for pid in POLLERS):
        return False
    with _LOCK:
        t = _THREAD["t"]
        if t is not None and t.is_alive():
            return True
        _THREAD["dir"] = data_dir

        def run():
            while any(enabled(pid) for pid in POLLERS):
                for pid, fn in POLLERS.items():
                    if enabled(pid):
                        try:
                            fn(_THREAD["dir"])
                        except Exception as e:          # noqa: BLE001 - the poller never dies on one bad answer
                            state(pid)["last_error"] = type(e).__name__
                time.sleep(5)
        _THREAD["t"] = threading.Thread(target=run, name="book-providers", daemon=True)
        _THREAD["t"].start()
    return True


# ----------------------------------------------------------------------------------------------------------- health

def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def health(now=None):
    """One row per configured provider for the admin coverage page. Direct providers report their own poller; sports-service
    providers are filled in by the caller from the NBA / NFL snapshots."""
    now = time.time() if now is None else now
    out = []
    for pid, p in providers().items():
        row = {"id": pid, "label": p["label"], "kind": p["kind"], "flag": p["flag"], "enabled": enabled(pid), "source": p["source"],
               "sports": list(p["sports"])}
        if pid in POLLERS:
            st, feed = state(pid), p["feed"]
            row.update(state="off" if not row["enabled"] else freshness(st, feed, now), last_ok=st["at"], attempt_at=st["attempt_at"],
                       failures=st["failures"], backoff_until=st["backoff_until"] if st["backoff_until"] > now else None,
                       next_poll_in_s=round(max(0.0, st["next_at"] - now), 1) if row["enabled"] else None, last_error=st["last_error"],
                       last_status=st["last_status"], requests=st["requests"], not_modified=st["not_modified"],
                       feed_age_s=round(now - st["at"], 1) if st["at"] else None, offers=len(st["offers"]),
                       by_sport={s: sum(o["sport"] == s for o in st["offers"]) for s in p["sports"]},
                       median_staleness_s=_median([now - o["source_at"] for o in st["offers"] if o.get("source_at")]),
                       skipped=dict(sorted(st["skipped"].items(), key=lambda kv: -kv[1])[:12]),
                       poll_s=feed["poll_s"], stale_after_s=feed["stale_after_s"])
            if row["enabled"] and st["backoff_until"] > now:
                row["state"] = "refused" if st["last_status"] == 403 else "backoff"
        else:
            row.update(state="off" if not row["enabled"] else "via sports service", stale_after_s=p["feed"]["stale_after_s"],
                       note=p["feed"].get("note"))
        out.append(row)
    for pid, b in (config().get("blocked") or {}).items():
        out.append({"id": pid, "label": b["label"], "kind": b.get("kind"), "flag": None, "enabled": False, "source": "none",
                    "sports": [], "state": "blocked", "note": b.get("reason"), "checked_at": b.get("checked_at"), "evidence": b.get("evidence")})
    return out
