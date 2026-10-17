"""LoL pick'em lines: PrizePicks and Underdog (kills / assists / deaths on Map 1, Maps 1-2, Maps 1-3).

PrizePicks - partner-api.prizepicks.com/projections?league_id=121 (LoL), the same partner API the CS2 receiver reads
             (it answers cloud servers; the app host api.prizepicks.com shows a CAPTCHA and is never used).
Underdog   - api.underdogfantasy.com/v1/over_under_lines?sport_id=LOL (public, no login).
Seen 2026-10-17 (Worlds play-ins, BO5): PrizePicks "MAPS 1-3 Kills" (standard / goblin / demon) and "MAPS 1-3 Kills
(Combo)"; Underdog "Kills on Maps 1+2+3", "Assists on Maps 1+2+3", "Fantasy Points on Maps 1+2+3". In BO3 weeks the
same books post Maps 1-2.

The WHOLE stat name is matched exactly (the CS2 audit C14 rule): "<maps> Kills|Assists|Deaths" only - combos, fantasy
points, "Kills + Assists" and any other stat are not graded. A book's line only sets the threshold a projection is
compared with; it never changes the projection.
"""
import json
import re
import threading
import time
from datetime import datetime, timezone

from . import cache, config, net, teams

PP_URL = "https://partner-api.prizepicks.com/projections"
PP_LEAGUE = 121
PP_HEADERS = {"Origin": "https://app.prizepicks.com", "Referer": "https://app.prizepicks.com/",
              "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/140.0.0.0 Safari/537.36"}
UD_URL = "https://api.underdogfantasy.com/v1/over_under_lines"
UD_HEADERS = {"Origin": "https://underdogfantasy.com", "Referer": "https://underdogfantasy.com/",
              "User-Agent": PP_HEADERS["User-Agent"]}
REFRESH = 2 * 60
PROPS = {"kills": "kills", "kill": "kills", "assists": "assists", "assist": "assists", "deaths": "deaths",
         "death": "deaths"}
SCOPES = {"1": "1", "12": "12", "123": "123"}
_PP_STAT = re.compile(r"^maps?\s+(\d(?:\s*[-+&]\s*\d)*)\s+([a-z]+)$", re.I)
_UD_CODE = re.compile(r"^([a-z]+)_on_maps?_(\d(?:_\d)*)$")
_BOARD = {"at": 0.0, "rows": [], "status": {}}
_LOCK = threading.Lock()


def _scope(maps):
    """'1-3' / '1+2+3' / '1_2_3' -> '123'; '1-2' -> '12'; '1' -> '1'. Anything else (e.g. 'Map 3') -> None."""
    nums = re.findall(r"\d", maps or "")
    if not nums or nums[0] != "1":
        return None
    if len(nums) == 2 and re.search(r"-", maps or ""):            # a range: 1-2, 1-3
        nums = [str(i) for i in range(int(nums[0]), int(nums[1]) + 1)]
    s = "".join(nums)
    return SCOPES.get(s) if s == "".join(str(i) for i in range(1, len(nums) + 1)) else None


def pp_market(stat):
    """'MAPS 1-2 Kills' -> ('kills', '12'); combos / other stats -> (None, None)."""
    m = _PP_STAT.match(" ".join(str(stat or "").split()))
    if not m:
        return None, None
    prop, scope = PROPS.get(m.group(2).lower()), _scope(m.group(1))
    return (prop, scope) if prop and scope else (None, None)


def pp_parse(body):
    inc = {(i.get("type"), i.get("id")): i.get("attributes") or {} for i in body.get("included") or []}
    rows = []
    for p in body.get("data") or []:
        a = p.get("attributes") or {}
        if a.get("status") not in (None, "pre_game") or a.get("is_live"):
            continue
        stat = a.get("stat_display_name") or a.get("stat_type")
        prop, scope = pp_market(stat)
        rel = p.get("relationships") or {}
        pl = inc.get(("new_player", ((rel.get("new_player") or {}).get("data") or {}).get("id")), {})
        if not prop or pl.get("combo") or a.get("line_score") is None:
            continue
        ot = a.get("odds_type") or "standard"
        allowed = a.get("allowed_wager_types")
        if isinstance(allowed, str):
            allowed = [allowed]
        under = ("under" in allowed) if isinstance(allowed, list) else ot in ("standard", "promo")
        over = ("over" in allowed) if isinstance(allowed, list) else True
        opp = re.sub(r"\s+maps?\s*\d(?:\s*[-+&]\s*\d)*\s*$", "", str(a.get("description") or ""), flags=re.I).strip()
        rows.append({"id": f"pp:{p.get('id')}", "book": "prizepicks", "player": pl.get("display_name") or pl.get("name"),
                     "team": pl.get("team"), "opponent": opp or None, "position": pl.get("position"),
                     "start": a.get("start_time"), "prop": prop, "scope": scope, "line": float(a["line_score"]),
                     "odds_type": ot, "over_available": over, "under_available": under,
                     "updated_at": a.get("updated_at"), "stat_label": stat})
    return rows


def ud_parse(body):
    apps = {x["id"]: x for x in body.get("appearances") or []}
    players = {x["id"]: x for x in body.get("players") or []}
    games = {x["id"]: x for x in body.get("games") or []}
    rows = []
    for ln in body.get("over_under_lines") or []:
        if ln.get("status") not in (None, "active"):
            continue
        st = (ln.get("over_under") or {}).get("appearance_stat") or {}
        m = _UD_CODE.match(str(st.get("stat") or "").lower())
        label = str(st.get("display_stat") or "").lower().split()
        prop = PROPS.get(m.group(1)) if m else None
        scope = SCOPES.get(m.group(2).replace("_", "")) if m else None
        if not prop or not scope or not label or PROPS.get(label[0]) != prop:
            continue
        app = apps.get(st.get("appearance_id")) or {}
        pl = players.get(app.get("player_id")) or {}
        g = games.get(app.get("match_id")) or {}
        try:
            value = float(ln.get("stat_value"))
        except (TypeError, ValueError):
            continue
        title = g.get("full_team_names_title") or g.get("title") or ""
        parts = re.split(r"\s+(?:@|vs\.?)\s+", title, maxsplit=1)
        away, home = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", "")
        team = home if app.get("team_id") == g.get("home_team_id") else away if app.get("team_id") == g.get("away_team_id") else ""
        opp = away if team == home and team else home if team == away and team else ""
        opts = {o.get("choice"): o for o in ln.get("options") or []}

        def _f(o, k):
            try:
                return float((o or {}).get(k))
            except (TypeError, ValueError):
                return None
        rows.append({"id": f"ud:{ln.get('id')}", "book": "underdog",
                     "player": " ".join(x for x in (pl.get("first_name"), pl.get("last_name")) if x).strip(),
                     "team": team or None, "opponent": opp or None, "start": g.get("scheduled_at"), "prop": prop,
                     "scope": scope, "line": value, "odds_type": ln.get("line_type") or "balanced",
                     "over_available": "higher" in opts, "under_available": "lower" in opts,
                     "mult_over": _f(opts.get("higher"), "payout_multiplier"),
                     "mult_under": _f(opts.get("lower"), "payout_multiplier"),
                     "price_over": _f(opts.get("higher"), "decimal_price"),
                     "price_under": _f(opts.get("lower"), "decimal_price"),
                     "updated_at": ln.get("updated_at") or (opts.get("higher") or {}).get("updated_at"),
                     "stat_label": st.get("display_stat")})
    return rows


def _read(book):
    if book == "prizepicks":
        r = net.get(PP_URL, params={"league_id": PP_LEAGUE, "per_page": 1000}, headers=PP_HEADERS)
        return pp_parse(r.json())
    r = net.get(UD_URL, params={"sport_id": "LOL"}, headers=UD_HEADERS)
    return ud_parse(r.json())


def board(refresh=False):
    """Both books' current LoL lines: {"at", "rows", "status": {book: {ok, lines, error}}}. Re-read every REFRESH s;
    a book that fails keeps its last rows (status says so). Every read is archived (archive())."""
    with _LOCK:
        if not refresh and _BOARD["rows"] and time.time() - _BOARD["at"] < REFRESH:
            return dict(_BOARD)
        rows, status = [], {}
        for book in ("prizepicks", "underdog"):
            try:
                got = _read(book)
                status[book] = {"ok": True, "lines": len(got), "error": None}
                archive(book, got)
            except Exception as e:          # noqa: BLE001 - one book down never hides the other
                got = [r for r in _BOARD["rows"] if r["book"] == book]
                status[book] = {"ok": False, "lines": len(got), "error": f"{type(e).__name__}: {e}"[:200]}
            rows += got
        _BOARD.update(at=time.time(), rows=rows, status=status)
        return dict(_BOARD)


def archive(book, rows, now=None):
    """Keep every line ever observed: a new line, a moved line (or changed type / sides) and a removed line each add a
    row to <LOL_DATA_DIR>/lines_archive.jsonl - so the model can later be checked against the lines that were really
    offered (the CS2 real-line studies needed exactly this). Never fatal."""
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    last = cache.get("lines_last", book) or {}
    cur = {r["id"]: [r["line"], r["odds_type"], r["over_available"], r["under_available"], r.get("mult_over"),
                     r.get("mult_under"), r.get("price_over"), r.get("price_under")] for r in rows}   # a payout move too
    events = []
    for r in rows:
        if last.get(r["id"]) != cur[r["id"]]:
            events.append(dict(r, event="new" if r["id"] not in last else "moved", seen_at=now,
                               prev_line=(last.get(r["id"]) or [None])[0]))
    events += [{"id": i, "book": book, "event": "removed", "seen_at": now, "prev_line": v[0]}
               for i, v in last.items() if i not in cur]
    if not events:
        return 0
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.DATA_DIR / "lines_archive.jsonl", "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, separators=(",", ":")) + "\n")
        cache.put("lines_last", book, cur)
    except OSError:
        return 0
    return len(events)


def lines_for(player, team=None, code=None, rows=None):
    """Every line on the board for this player (exact name; when both sides name a team it must be his team - by name,
    alias or his team's code: PrizePicks lists abbreviations)."""
    rows = rows if rows is not None else board()["rows"]
    key = teams.norm(player)
    return [r for r in rows if teams.norm(r.get("player")) == key
            and (not team or not r.get("team") or teams.matches(r["team"], team, code))]
