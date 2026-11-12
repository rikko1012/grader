"""Extra books in the board's book toggle (Phase 4, one flag per provider in book_providers.json, all default off).

Selecting an extra book shows that book's lines for players already on the board. Nothing is recomputed:
  - same exact line as a PrizePicks / Underdog row -> that row's saved side, chance, projection and letter, unchanged; no stake
    (stakes are set for the pick'em entry the forecast was graded for);
  - any other line -> Scout's chance at THAT line, read from the same saved projection distribution of that player / stat / maps
    (line_chance: CS2 grader dist, LoL saved NB), labelled as Scout's chance at that line, with no letter and no stake; when the
    saved distribution does not reproduce the board's own chance, the line and price only.
  - every line -> L5 / L10 / L20 (and H2H / Season with SCOUT_BOARD_H2H) counted from the player's game log at the line shown
    (audit 2026-11-05 A2/B4), and the board player's research doc (history, stats, H2H, Ask) through line_doc().
Matching is exact: normalised player name + stat + period; two board players with that name need the exact team too, otherwise
the line is left out and counted as ambiguous. Never fuzzy.
Rows are display-only research lines: they cannot be added to a Scout slip (one book per slip; slips are PrizePicks / Underdog).

For 700 members: one build per (sport, book) every TTL s through srv.shared; members never trigger a provider request
(book_providers' own poller, or the sports service, collects every price). This module imports no lol or sports package."""
import copy
import hashlib
import json
import statistics
import threading
import time
from datetime import datetime, timezone

import book_providers as bp

TTL = 30
PICKEM = ("underdog", "prizepicks")
PREF = ("underdog", "prizepicks")
GAME_PERIODS = ("full_game", "game")
ONE_SIDED_LABELS = ("Over only", "Under only")     # a pick'em line offering one side only (LoL os-1, sports cap-2)
# cap-3 (owner 2026-11-08: "these are only A's cause they're probbaly -1000 odd props") on an extra book whose payout follows
# the price (kind != "pickem": FanDuel / BetRivers / The Odds API books). The rule is sports/board_view.price_cap, mirrored here
# because this module imports no sports package (tests/test_sports_cap3_hub.py keeps the constants and the answers equal): a
# side and letter only when the line's no-vig chance of that side is <= CAP_MAX_SIDE_MARKET - its own two decimal prices
# first, else the board's market chance at that same line, else held. Held = no side, no letter, no stake, no p_side (so the
# hub's Scout-chance sort puts it last); chances and projection stay; the hub board's own held book row says the same.
CAP_VERSION = "cap-3"
CAP_MAX_SIDE_MARKET = 0.65
CAP_TEXT = "Scout grades lines priced near even."
CAP_HOLD_LABEL = "Research estimate"               # = workspace_sports.HOLD_LABEL: the hub board's label for a held book row
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
    _BY_ID.clear()
    _BY_PROP.clear()


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


def _pref(r):
    return PREF.index(r["offer"]["platform"]) if r["offer"]["platform"] in PREF else len(PREF)


def _pick_base(recs, line, nearest=True):
    """(board record, same line?): the same standard line on PrizePicks / Underdog, else (esports) the nearest standard line of that
    player, whose saved distribution the chance at this line is read from; NBA / NFL keep the board's first record (unchanged)."""
    std = [r for r in recs if (r["market"].get("line_type") or "standard") == "standard"]
    same = sorted((r for r in std if r["offer"].get("line") == line), key=_pref)
    if same:
        return same[0], True
    if not nearest:
        return recs[0], False
    try:
        near = sorted(std, key=lambda r: (abs(float(r["offer"].get("line")) - float(line)), _pref(r)))
    except (TypeError, ValueError):
        near = []
    return (near[0] if near else recs[0]), False


def _windows():
    """The hit-rate windows a board row shows: L5 / L10 / L20, and H2H / Season with SCOUT_BOARD_H2H."""
    import workspace_h2h
    return ("l5", "l10", "l20") + (("h2h", "season") if workspace_h2h.board_enabled() else ())


def own_po(over, under):
    """P(Over) from a book line's own two decimal prices, de-vigged (= sports/board_view.own_chance for a book), or None."""
    a = (over or {}).get("price") if (over or {}).get("offered") else None
    b = (under or {}).get("price") if (under or {}).get("offered") else None
    if not (isinstance(a, (int, float)) and isinstance(b, (int, float)) and a > 1 and b > 1):
        return None
    io, iu = 1.0 / a, 1.0 / b
    return io / (io + iu)


def cap_held(kind, side, po=None, market=None):
    """cap-3 (= sports/board_view.price_cap): True when this extra-book line's side is not priced near even. A pick'em book or
    a line with no side is never held. market = {"p_over", "p_under"} at this same line, used only without own prices."""
    if kind == "pickem" or not side:
        return False
    if po is not None:
        m = po if side == "over" else 1.0 - po
    elif market and isinstance(market.get("p_over"), (int, float)) and isinstance(market.get("p_under"), (int, float)):
        m = market["p_over"] if side == "over" else market["p_under"]
    else:
        m = None
    return m is None or m > CAP_MAX_SIDE_MARKET + 1e-12


def _cap3(r, kind, over, under, market):
    """Apply cap-3 to one derived record's Scout block in place (display only: the board's saved chances never change)."""
    s = r["scout"]
    if not cap_held(kind, s.get("side"), own_po(over, under), market):
        return
    s.pop("letter_held", None)
    s.update(side=None, p_side=None, grade=None, units=None, stake_reason=None,
             hold={"label": CAP_HOLD_LABEL, "reason": CAP_TEXT},
             validation={"kind": "research", "label": CAP_HOLD_LABEL, "detail": CAP_TEXT},
             read_rev="x-" + _sha(s.get("read_rev"), CAP_VERSION)[:16])


def derive(pid, label, kind, sport, recs, line, over, under, source_at, observed_at, offer_key, read=None):
    """One display record for an extra book's line, built from the board's own records of that player and market.
    read(base record, line) -> (chance at this line or None, history at this line or None): line_chance + the game log."""
    base, is_same = _pick_base(recs, line, nearest=sport in ("cs2", "lol"))
    if is_same and ((base.get("scout") or {}).get("hold") or {}).get("label") in ONE_SIDED_LABELS:
        is_same = False             # os-1 / cap-2: an "Over only" app line's label is that app's, never copied to another book
    same = [base] if is_same else []
    chance, hist = None, None
    # NBA / NFL: a same-line row keeps the board's own counts (snapshot job) and reads the game log only for a window the board
    # row is missing; another line reads it for every window (card-why 2026-11-06)
    sports_same = sport not in ("cs2", "lol") and is_same
    gaps = [w for w in _windows() if not ((base.get("history") or {}).get(w) or {}).get("n")] if sports_same else []
    if read is not None and (not sports_same or gaps):
        try:
            chance, hist = read(base, line)
        except Exception:                          # noqa: BLE001 - a failed read shows the line without them, never a wrong number
            chance, hist = None, None
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
        if hist and sports_same:                   # NBA / NFL: the board's own counts stay; only its empty windows are filled
            bh = dict(base.get("history") or {})
            r["history"] = dict(bh, **{w: hist.get(w) for w in gaps if hist.get(w)})
        elif hist:                                 # the saved L10 at this exact line stays; L5 / L20 / H2H / Season from the game log
            bh = base.get("history") or {}
            r["history"] = dict(hist, l10=bh.get("l10") or hist.get("l10"), avg=bh["avg"] if bh.get("avg") is not None else hist.get("avg"))
    else:
        c = chance or {}
        r["scout"] = {"side": c.get("side"), "read_rev": ("x-" + _sha(line, c.get("side"), c.get("p_over"), c.get("p_under"))[:16]) if chance else "x-none",
                      "p_side": c.get("p_side"), "p_over": c.get("p_over"), "p_under": c.get("p_under"), "p_push": c.get("p_push"),
                      "projection": base["scout"].get("projection"), "range80": base["scout"].get("range80"), "grade": None, "units": None,
                      "stake_reason": None, "hold": None, "model_version": base["scout"].get("model_version"),
                      "forecast_at": base["scout"].get("forecast_at") if chance else None, "validation": base["scout"].get("validation"),
                      **({"line_chance": True} if chance else {})}
        r["history"] = hist or {"l5": None, "l10": None, "l20": None, "avg": None, "recent": None}
        r["market_chance"] = None
    if sport in ("cs2", "lol") and r["scout"].get("grade"):
        import letter_containment as lc               # lc-1: a held letter is never shown on an extra book's line either
        if lc.contained(sport, m.get("stat")):
            r["scout"].update(grade=None, letter_held=True, units=None)
    # cap-3: a sportsbook line priced far from even shows no side or letter (its own prices first; the board's market chance
    # only at the same line it was read at)
    _cap3(r, kind, over, under, base.get("market_chance") if same else None)
    r["state"] = {"kind": "ok", "reason": None}
    pc = bp.providers().get(pid) or {}
    r["extra"] = {"provider": pid, "kind": kind, "same_line_as": base["offer"]["platform_label"] if same else None,
                  # the board line whose player research doc this line opens (line_doc), and whether its chance is read at this line
                  "base": {"platform": base["offer"]["platform"], "offer_id": base["offer"].get("offer_id"), "line": base["offer"].get("line")},
                  "line_chance": bool(chance) and not same,
                  # Dabble: its per-side number is its own unit, never decimal odds (book_providers.json price_note)
                  "price_comparable": pc.get("price_comparable", True), "price_label": pc.get("price_label")}
    return r


# ------------------------------------------------------------------------------------------------- sports-service books

def quote_stale_after(feed, recs, now):
    """Seconds after which a sports-service book quote is stale. With a "ladder" in the provider's feed config (the
    sports books worker's own fetch schedule, sports/books/schedule.py LADDERS["direct"], the SPORTS_BOOKS_LADDER default): 2 x the fetch interval for a
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
    if pid == "draftkings":
        import workspace_draftkings
        return workspace_draftkings.build(srv, sport, now)
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
        read = reader(srv, sport, s)
        for o in bp.offers(pid, sport, now):
            cov["offers"] += 1
            got, why = match(subj, o, p.get("match"))
            if got is None:
                miss(why)
                continue
            cov["matched"] += 1
            r = derive(pid, label, kind, sport, got, o["line"], o["over"], o["under"], o.get("source_at"), o["observed_at"],
                       (o["subject_id"], o["raw_stat"], o["line"], o["game_id"]), read=read)
            cov["line_chance"] = cov.get("line_chance", 0) + bool(r["extra"].get("line_chance"))
            cov["with_history"] = cov.get("with_history", 0) + bool((r.get("history") or {}).get("l5"))
            cov["same_line"] += r["extra"]["same_line_as"] is not None
            if o.get("source_at"):
                ages.append(now - o["source_at"])
            out.append(r)
    elif sport in ("nba", "nfl"):
        quotes, props = book_quotes(s, pid)
        read = reader(srv, sport, s)
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
            r = derive(pid, label, kind, sport, got, b.get("line"), over, under, None, t, k, read=read)
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


# ------------------------------------------------------------------------------------- the game log and the saved distribution

HISTORY_BUDGET_S = 1.0                            # one build reads uncached CS2 game logs this long at most; the rest come from the cache


def _hist_of(ser, line, opponent):
    """L5 / L10 / L20 / avg (and H2H / Season with SCOUT_BOARD_H2H) at this line from one newest-first game log, or None."""
    import workspace_h2h
    if not ser or not isinstance(line, (int, float)):
        return None

    def tally(n):
        w = ser[:n]
        o = sum(1 for g in w if g["value"] > line)
        u = sum(1 for g in w if g["value"] < line)
        return {"over": o, "under": u, "push": len(w) - o - u, "n": len(w)}
    last = [g["value"] for g in ser[:10]]
    out = {"l5": tally(5), "l10": tally(10), "l20": tally(20), "avg": round(sum(last) / len(last), 1), "recent": None}
    if workspace_h2h.board_enabled():
        # NBA / NFL seasons cross the calendar year: the season of the newest played game, as the board's own SZN column counts it
        season = _sports_season(ser, line) if ser and "season" in ser[0] else workspace_h2h.board_season(ser, line)
        out.update(h2h=workspace_h2h.board_h2h(ser, opponent, line), season=season)
    return out


def series_of(srv, sport, s, base, budget=None):
    """(newest-first game log of this board record's player / stat / maps, the board's index row); (None, row) when not known now.
    CS2: the HLTV year workspace_api reads for /line (cached and shared); past `budget` only what is already cached. LoL: the
    snapshot's own series (no I/O)."""
    o = (base or {}).get("offer") or {}
    x = ((s or {}).get("index") or {}).get((o.get("platform"), str(o.get("offer_id"))))
    if x is None:
        return None, None
    if sport == "cs2":
        import workspace_api as api
        peek = budget is not None and time.monotonic() > budget
        try:
            doc = api._history(srv, x, full=True, peek=peek) or {}
        except Exception:                          # noqa: BLE001 - no game log now: the counts are left out, never zero
            doc = {}
        ser = [g for g in doc.get("ser") or [] if isinstance(g.get("value"), (int, float))]
        return (ser or None), x
    if sport == "lol":
        import workspace_lol
        return (workspace_lol.series_values(((s or {}).get("series") or {}).get(x.get("pid")), x.get("stat"), x.get("scope")) or None), x
    if sport in ("nba", "nfl"):
        return sports_series(srv, sport, x, budget), x
    return None, x


def sports_series(srv, lg, x, budget=None):
    """NBA / NFL (card-why 2026-11-06): the player's newest-first played games for this stat (full game only), each with its
    season, from the SAME shared player file the research card reads (workspace_sports._player, one upstream read per player
    per PLAYER_TTL for every member). Past `budget` only what is already cached; None when not known now (no counts shown)."""
    import workspace_api as api
    import workspace_sports as ws
    pid, stat = x.get("pid"), x.get("st")
    if x.get("scope") != "full_game" or not stat or not ws._PID_RE.match(str(pid or "")):
        return None
    try:
        if budget is not None and time.monotonic() > budget:
            doc = api._peek_shared(srv, ("ws_sp_file", "/api/sports/history", lg, pid))
        else:
            doc = ws._player(srv, lg, pid, "/api/sports/history")
    except Exception:                              # noqa: BLE001 - no game log now: the counts are left out, never zero
        doc = None
    out = []
    for g in (doc or {}).get("games") or []:
        v = (g.get("values") or {}).get(stat)
        if g.get("played") and isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append({"date": str(g.get("start_utc") or "")[:10], "opponent": g.get("opponent") or "", "value": v, "season": g.get("season"),
                        "_n": len((doc or {}).get("games") or [])})
    return out or None


def _sports_season(ser, line):
    """{over, under, push, n, season} over the newest played game's season, or None when the saved file may not hold the whole
    season (the file keeps the newest RESEARCH_MAX_GAMES games: a full file of one season could be cut short)."""
    import workspace_sports as ws
    season = ser[0].get("season") if ser else None
    if season is None:
        return None
    vals = [g["value"] for g in ser if g.get("season") == season]
    full = (ser[0].get("_n") or 0) >= ws.RESEARCH_MAX_GAMES and all(g.get("season") == season for g in ser)
    if not vals or full:
        return None
    return {"over": sum(v > line for v in vals), "under": sum(v < line for v in vals), "push": sum(v == line for v in vals), "n": len(vals), "season": str(season)}


def chance_of(srv, sport, x, line):
    """Scout's chance at this line from the board row's saved distribution (line_chance), or None."""
    import line_chance
    if x is None:
        return None
    if sport == "cs2":
        g = (getattr(srv, "BOARD", None) or {}).get(x.get("id")) or {}
        if not g.get("dist"):
            return None
        return line_chance.cs2_at(dict(x, dist=g["dist"], perf_on=g.get("perf_on")), line)
    if sport == "lol":
        return line_chance.lol_at(x.get("lam"), x.get("nb"), x.get("fc_line") if x.get("fc_line") is not None else x.get("line"),
                                  x.get("po"), x.get("pu"), line)
    return None


def reader(srv, sport, s):
    """read(base record, line) for derive(): (chance at the line, history at the line). One read budget per build."""
    budget = time.monotonic() + HISTORY_BUDGET_S

    def read(base, line):
        try:
            ln = float(line)
        except (TypeError, ValueError):
            return None, None
        ser, x = series_of(srv, sport, s, base, budget)
        return chance_of(srv, sport, x, ln), _hist_of(ser, ln, (base.get("player") or {}).get("opponent"))
    return read


_BY_ID = {}                                       # (sport, pid) -> (body bytes, {offer id: record}) of the last book board parsed
_BY_PROP = {}                                     # (sport, pid) -> (body bytes, {prop key: [line rows]}) of the last book board parsed


def _prop_key(r):
    e, p, m = r.get("event") or {}, r.get("player") or {}, r.get("market") or {}
    return "|".join(str(x) for x in (e.get("id"), p.get("key"), m.get("stat"), m.get("period")))


def prop_lines(srv, sport, rec):
    """Every enabled direct extra book's (Sleeper, Dabble) current lines for this board record's event / player / stat / period:
    [{"platform", "platform_label", "line", "line_type"}]. Site audit 2026-11-06 N9: the Ask tab's "better line elsewhere" answer
    named only the board's own books. Read from each book's shared board build, indexed once per build (no request per question);
    a book whose feed is not current lists nothing (its lines are off the board too)."""
    key, out = _prop_key(rec), []
    for pid in bp.enabled_ids():
        p = bp.providers().get(pid) or {}
        if p.get("source") != "direct" or sport not in (p.get("sports") or ()):
            continue
        try:
            st, body = board(srv, sport, pid)
        except Exception:                          # noqa: BLE001 - one book failing never hides the others
            continue
        if st != 200:
            continue
        now = time.time()
        out.extend({k: v for k, v in x.items() if k != "lock"} for x in _props_of(sport, pid, body).get(key, ()) if x["lock"] is None or x["lock"] > now)
    return out


_PROP_LOCK = threading.Lock()


def _note_props(sport, pid, body, doc):
    """The prop index of one book board build, made inside board()'s single-flight compute (review of N9: every Ask question
    arriving just after a rebuild parsed the whole book board again, one parse per concurrent request)."""
    idx = {}
    if (doc.get("feed") or {}).get("state") == "ok":
        for r in doc.get("records") or ():
            if (r.get("event") or {}).get("status") == "started":
                continue
            o = r.get("offer") or {}
            idx.setdefault(_prop_key(r), []).append({"platform": o.get("platform"), "platform_label": o.get("platform_label"),
                                                     "line": o.get("line"), "line_type": (r.get("market") or {}).get("line_type") or "standard",
                                                     "lock": _lock_epoch(r)})
    _BY_PROP[(sport, pid)] = (body, idx)


def _props_of(sport, pid, body):
    """The prop index of this body: the one noted at build; a body this process did not note (after reset) is parsed once, by one
    thread, while the others wait for that copy."""
    c = _BY_PROP.get((sport, pid))
    if c and c[0] is body:
        return c[1]
    with _PROP_LOCK:
        c = _BY_PROP.get((sport, pid))
        if not c or c[0] is not body:
            _note_props(sport, pid, body, json.loads(body))
            c = _BY_PROP[(sport, pid)]
        return c[1]


def record_by_id(srv, sport, pid, oid):
    """(status, record or None) of one extra-book line on the shared book board."""
    st, body = board(srv, sport, pid)
    if st != 200:
        return st, None
    c = _BY_ID.get((sport, pid))
    if not c or c[0] is not body:
        doc = json.loads(body)
        c = _BY_ID[(sport, pid)] = (body, {r["offer"]["offer_id"]: r for r in doc.get("records") or []})
    return 200, c[1].get(oid)


def line_doc(srv, sport, pid, oid):
    """(status, doc): an extra book's line opens the board player's research doc (history, stats, H2H, books, Ask) at its OWN line.
    The record is the extra line's own (its line and price, and Scout's chance at that line or none); the history series is the
    player's game log, so every count is at the line shown. No letter and no stake (unchanged rule)."""
    import workspace_api as api
    import workspace_h2h
    st, rec = record_by_id(srv, sport, pid, oid)
    if st != 200:
        return 503, {"error": "line_unavailable"}
    if rec is None:
        return 404, {"error": "not found"}
    base = (rec.get("extra") or {}).get("base") or {}
    got = api._line_for(srv, sport, base.get("platform"), str(base.get("offer_id")))
    if got is None or got[0] != 200:
        return 404, {"error": "not found"}
    doc = json.loads(got[1])
    recs, s = records(srv, sport)
    brec = next((r for r in recs or [] if r["offer"]["platform"] == base.get("platform") and str(r["offer"].get("offer_id")) == str(base.get("offer_id"))), None)
    ser = series_of(srv, sport, s, brec)[0] if brec else None
    line, same = rec["offer"]["line"], bool((rec.get("extra") or {}).get("same_line_as"))
    h = rec.get("history") or {}
    hist = dict(doc.get("history") or {}, tally=h.get("l10"))
    if h.get("avg") is not None:
        hist["avg"] = h["avg"]
    if ser:
        hist["series"] = [{k: v for k, v in g.items() if not k.startswith("_") and k != "season"} for g in ser[:20]]
    doc["history"] = hist
    if not same:
        doc["forecast"] = None                     # no forecast was saved at this line; a chance shown is read from the distribution
    old = doc.get("h2h")
    if ser:
        doc["h2h"] = workspace_h2h.h2h(ser, rec["player"].get("opponent"), line)
    elif old:
        vals = [g["value"] for g in old.get("series") or [] if isinstance(g.get("value"), (int, float))]
        doc["h2h"] = dict(old, over=sum(v > line for v in vals), under=sum(v < line for v in vals), push=sum(v == line for v in vals))
    o = rec["offer"]
    doc["books"] = list(doc.get("books") or []) + [{"platform": o["platform"], "platform_label": o["platform_label"], "row_id": o["offer_id"], "key": rec["key"],
                                                    "line": line, "line_type": "standard", "over": o["over"], "under": o["under"], "seen_at": o.get("seen_at"),
                                                    "scout_side": rec["scout"].get("side"), "p_side": rec["scout"].get("p_side"), "grade": None}]
    doc["market"] = {"available": False, "reason": f"{o['platform_label']} prices are not used for a market chance."}
    doc["record"] = rec
    doc.pop("grade_inputs", None)                  # "Behind the grade" is the board line's own block: this line has no letter or stake
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
            _note_props(sport, pid, body, doc)
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
