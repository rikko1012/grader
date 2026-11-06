"""Extra books in the board's book toggle (Phase 4, one flag per provider in book_providers.json, all default off).

Selecting an extra book shows that book's lines for players already on the board. Nothing is recomputed:
  - same exact line as a PrizePicks / Underdog row -> that row's saved side, chance, projection and letter, unchanged; no stake
    (stakes are set for the pick'em entry the forecast was graded for);
  - any other line -> the line and price only, with no side and no chance (a different line is a different pick).
Matching is exact: normalised player name + stat + period; two board players with that name need the exact team too, otherwise
the line is left out and counted as ambiguous. Never fuzzy.
Rows are display-only research lines: they cannot be added to a Scout slip (one book per slip; slips are PrizePicks / Underdog).

For 700 members: one build per (sport, book) every TTL s through srv.shared; members never trigger a provider request
(book_providers' own poller, or the sports service, collects every price). This module imports no lol or sports package."""
import copy
import hashlib
import json
import statistics
import time
from datetime import datetime, timezone

import book_providers as bp

TTL = 30
PICKEM = ("underdog", "prizepicks")
PREF = ("underdog", "prizepicks")
GAME_PERIODS = ("full_game", "game")
_PARSED = {}


def _per(period):
    return "game" if period in GAME_PERIODS else period


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds") if isinstance(t, (int, float)) else (t or None)


def _ts(s):
    if isinstance(s, (int, float)):
        return float(s)
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def _sha(*parts):
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()


def snap(srv, sport):
    import workspace_api as api
    if sport == "cs2":
        return api.snapshot(srv)
    if sport == "lol":
        return api._adapter("lol").snapshot(srv)
    return api._adapter(sport).snapshot(srv, sport)


def records(srv, sport):
    """(records, snapshot) of one sport's shared board, parsed once per board build."""
    s = snap(srv, sport)
    if not s or s.get("body") is None:
        return None, None
    body = s["body"]
    c = _PARSED.get(sport)
    if c and c[0] is body:
        return c[1], s
    recs = json.loads(body).get("records") or []
    _PARSED[sport] = (body, recs)
    return recs, s


def reset():
    _PARSED.clear()
    _LINES.clear()


def subjects(recs):
    """(normalised name, stat, period) -> {(event id, team): [records]}"""
    out = {}
    for r in recs:
        try:
            k = (bp.norm(r["player"]["name"]), r["market"]["stat"], _per(r["market"]["period"]))
            sk = (r["event"]["id"], bp.norm(r["player"].get("team")))
        except (KeyError, TypeError):
            continue
        out.setdefault(k, {}).setdefault(sk, []).append(r)
    return out


def _same_event(recs, o, tol):
    """The offer's fixture is the board record's match: the same two teams, or the same start within tol seconds."""
    teams = set(o.get("event_teams") or ())
    start = o.get("event_start")
    for r in recs:
        e = r.get("event") or {}
        board = {bp.norm(t) for t in e.get("teams") or () if bp.norm(t)}
        if len(teams) == 2 and board == teams:
            return True
        t = _ts(e.get("start_utc"))
        if start is not None and t is not None and abs(t - start) <= tol:
            return True
    return False


def match(subj, o, rule=None):
    """-> (records, None) or (None, reason). Exact name; the team decides only between two players of the same name.
    With a provider match rule (book_providers.json "match", Dabble), the offer's team must be the board player's team and its
    fixture the board's match, even when only one board player has the name: a namesake or a later fixture never copies a read."""
    found = subj.get((o["player_norm"], o["stat"], _per(o["period"])))
    if not found:
        return None, "player not on the board"
    if rule:
        if not o.get("team_norm"):
            return None, "team missing"
        same_team = {k: v for k, v in found.items() if k[1] and k[1] == o["team_norm"]}
        if not same_team:
            return None, "team does not match"
        tol = rule.get("start_tolerance_s", 5400)
        same = [v for v in same_team.values() if _same_event(v, o, tol)]
        if not same:
            return None, "another match"
        if len(same) > 1:
            return None, "two board players share this name"
        return same[0], None
    if len(found) == 1:
        return next(iter(found.values())), None
    same_team = [v for (eid, team), v in found.items() if team and team == o.get("team_norm")]
    if len(same_team) == 1:
        return same_team[0], None
    return None, "two board players share this name"


def derive(pid, label, kind, sport, recs, line, over, under, source_at, observed_at, offer_key):
    """One display record for an extra book's line, built from the board's own records of that player and market."""
    same = sorted((r for r in recs if r["offer"].get("line") == line and (r["market"].get("line_type") or "standard") == "standard"),
                  key=lambda r: PREF.index(r["offer"]["platform"]) if r["offer"]["platform"] in PREF else len(PREF))
    base = same[0] if same else recs[0]
    r = copy.deepcopy(base)
    m = r["market"]
    m["line_type"] = "standard"
    r["key"] = ".".join(str(x) for x in (sport, r["event"]["id"], r["player"]["key"], m["stat"], m["period"], line, pid))
    r["offer"] = {"platform": pid, "platform_label": label, "offer_id": "x-" + _sha(pid, offer_key)[:24],
                  "revision": "x-" + _sha(line, over.get("payout"), under.get("payout"), over.get("price"), under.get("price"),
                                           over.get("offered"), under.get("offered"))[:16],
                  "revision_kind": "exact", "line": line, "seen_at": _iso(observed_at), "source_at": _iso(source_at),
                  "over": {k: over.get(k) for k in ("offered", "payout", "price")}, "under": {k: under.get(k) for k in ("offered", "payout", "price")},
                  "moved_from": None, "moved_at": None, "slip": False}
    if same:
        s = r["scout"]
        s["units"] = None
        s["stake_reason"] = f"Same line and saved chance as {base['offer']['platform_label']}; stakes are set for that entry only."
    else:
        r["scout"] = {"side": None, "read_rev": "x-none", "p_side": None, "p_over": None, "p_under": None, "p_push": None,
                      "projection": base["scout"].get("projection"), "range80": None, "grade": None, "units": None, "stake_reason": None,
                      "hold": None, "model_version": base["scout"].get("model_version"), "forecast_at": None, "validation": base["scout"].get("validation")}
        r["history"] = {"l5": None, "l10": None, "l20": None, "avg": None, "recent": None}
        r["market_chance"] = None
    if sport in ("cs2", "lol") and r["scout"].get("grade"):
        import letter_containment as lc               # lc-1: a held letter is never shown on an extra book's line either
        if lc.contained(sport, m.get("stat")):
            r["scout"].update(grade=None, letter_held=True, units=None)
    r["state"] = {"kind": "ok", "reason": None}
    pc = bp.providers().get(pid) or {}
    r["extra"] = {"provider": pid, "kind": kind, "same_line_as": base["offer"]["platform_label"] if same else None,
                  # Dabble: its per-side number is its own unit, never decimal odds (book_providers.json price_note)
                  "price_comparable": pc.get("price_comparable", True), "price_label": pc.get("price_label")}
    return r


# ------------------------------------------------------------------------------------------------- sports-service books

def quote_stale_after(feed, recs, now):
    """Seconds after which a sports-service book quote is stale. With a "ladder" in the provider's feed config (the
    sports books worker's own fetch schedule, sports/books/schedule.py LADDERS["full"]): 2 x the fetch interval for a
    game that far from its start, at least stale_min_s - the same rule the sports service applies, so a quote is never
    hidden between two scheduled fetches. Without a ladder or a known start: the fixed stale_after_s."""
    fixed = feed["stale_after_s"]
    ladder = feed.get("ladder")
    starts = [_ts((r.get("event") or {}).get("start_utc")) for r in recs or ()]
    starts = [x for x in starts if x is not None]
    if not ladder or not starts:
        return fixed
    hours = (min(starts) - now) / 3600.0
    if hours <= 0:
        return fixed
    for max_h, every_min in ladder:
        if hours <= max_h:
            return max(feed.get("stale_min_s", 1200), 2 * 60 * every_min)
    return fixed


def platform_of(pid, platform):
    p = bp.providers()[pid]
    if p.get("platform_prefix"):
        return isinstance(platform, str) and platform.startswith(p["platform_prefix"])
    return platform in (p.get("platforms") or ())


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 1 else None


def book_quotes(s, pid=None):
    """Every sportsbook / exchange quote attached to an NBA / NFL snapshot: (records of that market, book row, x).
    One per (platform, event, player, stat, period, line)."""
    seen, out = set(), []
    index, props = s.get("index") or {}, s.get("props") or {}
    for (p, rid), x in index.items():
        if p not in PICKEM:
            continue
        same, other = x.get("books") or ([], [])
        for b in list(same) + list(other):
            plat = b.get("platform")
            if pid is not None and not platform_of(pid, plat):
                continue
            k = (plat, x.get("e"), x.get("pkey"), x.get("st"), x.get("scope"), b.get("line"))
            if k in seen or b.get("line") is None:
                continue
            seen.add(k)
            out.append((k, b, x))
    return out, props


def _recs_of(s, props, x):
    recs = []
    for key in props.get((x.get("e"), x.get("pkey"), x.get("st"), x.get("scope"))) or []:
        y = (s.get("index") or {}).get(key)
        if y and y.get("rec"):
            recs.append(json.loads(y["rec"]))
    return recs


# ---------------------------------------------------------------------------------------------------------------- build

def build(srv, sport, pid, now=None):
    """-> (status, doc). The display records of one extra book on one sport, with coverage and feed freshness."""
    now = time.time() if now is None else now
    p = bp.providers()[pid]
    label, kind = p["label"], p["kind"]
    recs, s = records(srv, sport)
    if recs is None:
        return 503, {"error": "board_unavailable"}
    out, cov = [], {"offers": 0, "matched": 0, "same_line": 0, "stale": 0, "reasons": {}}
    ages = []

    def miss(why):
        cov["reasons"][why] = cov["reasons"].get(why, 0) + 1
    feed_state, feed_at, stale_after = "ok", None, p["feed"]["stale_after_s"]
    if p["source"] == "direct":
        st = bp.state(pid)
        feed_state = bp.freshness(st, p["feed"], now)
        feed_at = st["at"]
        subj = subjects(recs)
        for o in bp.offers(pid, sport, now):
            cov["offers"] += 1
            got, why = match(subj, o, p.get("match"))
            if got is None:
                miss(why)
                continue
            cov["matched"] += 1
            r = derive(pid, label, kind, sport, got, o["line"], o["over"], o["under"], o.get("source_at"), o["observed_at"],
                       (o["subject_id"], o["raw_stat"], o["line"], o["game_id"]))
            cov["same_line"] += r["extra"]["same_line_as"] is not None
            if o.get("source_at"):
                ages.append(now - o["source_at"])
            out.append(r)
    elif sport in ("nba", "nfl"):
        quotes, props = book_quotes(s, pid)
        newest = None
        for k, b, x in quotes:
            cov["offers"] += 1
            t = _ts(b.get("seen_at"))
            got = _recs_of(s, props, x)
            if t is None or now - t > quote_stale_after(p["feed"], got, now):
                cov["stale"] += 1
                continue
            newest = t if newest is None or t > newest else newest
            if not got:
                miss("market not on the board")
                continue
            cov["matched"] += 1
            over = {"offered": _num(b.get("over")) is not None, "payout": _num(b.get("over")), "price": _num(b.get("over"))}
            under = {"offered": _num(b.get("under")) is not None, "payout": _num(b.get("under")), "price": _num(b.get("under"))}
            r = derive(pid, label, kind, sport, got, b.get("line"), over, under, None, t, k)
            plat = b.get("platform")
            if plat != pid:                        # The Odds API: each book keeps its own name
                r["offer"]["platform_label"] = (b.get("platform_label") or plat) + " · The Odds API"
            cov["same_line"] += r["extra"]["same_line_as"] is not None
            ages.append(now - t)
            out.append(r)
        feed_at = newest
        if not quotes:
            feed_state = "empty"
        elif newest is None:
            feed_state = "stale"
    else:
        feed_state = "not_offered"
    board_subjects = len({(k, sk) for k, v in subjects(recs).items() for sk in v})
    covered = len({(r["event"]["id"], r["player"]["key"], r["market"]["stat"], _per(r["market"]["period"])) for r in out})
    cov.update(board_subjects=board_subjects, covered_subjects=covered,
               coverage_pct=round(100.0 * covered / board_subjects, 1) if board_subjects else None,
               median_staleness_s=round(statistics.median(ages), 1) if ages else None)
    doc = {"sport": sport, "book": pid, "label": label, "kind": kind, "rules": p.get("rules") or {},
           "feed": {"state": feed_state, "at": _iso(feed_at), "stale_after_s": stale_after},
           "platforms": {pid: {"label": label, "short": p.get("short"), "slip": False, "kind": kind,
                                 "price_comparable": p.get("price_comparable", True), "price_label": p.get("price_label")}},
           "records": out if feed_state in ("ok",) or p["source"] != "direct" else [], "coverage": cov}
    return 200, doc


_LINES = {}                                       # (sport, pid) -> (body bytes, feed state, [lock epoch per record]) beside each board build
HEALTHY = ("ok", "empty", "not_offered")          # feed states whose record count is a real answer (0 included)


def _lock_epoch(r):
    e = r.get("event") or {}
    if e.get("status") == "started":
        return 0.0
    t = _ts(e.get("lock_utc") or e.get("start_utc"))
    return t


def _note_lines(sport, pid, body, doc):
    _LINES[(sport, pid)] = (body, (doc.get("feed") or {}).get("state"), [_lock_epoch(r) for r in doc.get("records") or []])


def board(srv, sport, pid):
    """Shared single-flight copy: (status, body bytes). Each build also notes its feed state and record lock times, so open_lines
    counts without parsing the body again."""
    def compute():
        st, doc = build(srv, sport, pid)
        body = json.dumps(doc, default=str, separators=(",", ":")).encode()
        if st == 200:
            _note_lines(sport, pid, body, doc)
        return st, body
    return srv.shared(("ws_books", sport, pid), compute, ttl=TTL)


def open_lines(srv, sport, pid, now=None):
    """Lines of one extra book on one sport whose match has not started (the hub board's own started rule: status started, or lock /
    start time passed), or None when that is unknown right now: the board build failed (e.g. 503 while the sport's board snapshot is
    missing) or the feed is not healthy (starting, failing, stale). None is never the same as 0 (transient-failure rule)."""
    now = time.time() if now is None else now
    st, body = board(srv, sport, pid)
    if st != 200:
        return None
    c = _LINES.get((sport, pid))
    if not c or c[0] is not body:                  # a body built before this process noted it: parse once and note it
        _note_lines(sport, pid, body, json.loads(body))
        c = _LINES[(sport, pid)]
    if c[1] not in HEALTHY:
        return None
    return sum(1 for t in c[2] if t is None or t > now)


def coverage(srv, sports=("cs2", "lol", "nba", "nfl")):
    """Admin: coverage of every enabled extra book on every sport it lists (offers, matched, % of board markets, staleness)."""
    out = {}
    for pid in bp.enabled_ids():
        for sport in bp.providers()[pid]["sports"]:
            if sport not in sports:
                continue
            try:
                st, doc = build(srv, sport, pid)
            except Exception as e:                 # noqa: BLE001 - one sport failing never hides the others
                out.setdefault(pid, {})[sport] = {"error": type(e).__name__}
                continue
            out.setdefault(pid, {})[sport] = doc.get("coverage") if st == 200 else {"error": doc.get("error")}
    return out
