"""Head-to-head for the research card: a player's saved past results against tonight's opponent, at tonight's line.

Display only (owner 2026-11-04: the all-sports cards lacked the H2H block of the classic CS2 card). It reads the same newest-first series
the card's history chart uses; nothing is recomputed or graded. Team names match exactly after case and punctuation are dropped, never
loosely: an academy team is not its main team (memory: exact team-name variants, never fuzzy)."""
import os
import re
from datetime import datetime, timezone

LIMIT = 10


def team_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _meetings(series, opponent, mids=None, oids=None, core=None):
    """[(series row, core meeting or None)] newest first: the exact-opponent meetings (team name, as before), plus - CS2 with an HLTV
    roster core (h2h_core, hc-1) - every series in which 3+ of tonight's opponent's current players were on the side he faced, under
    any team name (org switches, his own earlier teams). mids / oids run parallel to series: the HLTV match id of each and the HLTV
    team id he played against."""
    want = team_key(opponent)
    tags = None
    if core:
        import h2h_core
        tags = h2h_core.tag(mids, oids, core)
    out = []
    for i, s in enumerate(series or []):
        if not isinstance(s.get("value"), (int, float)):
            continue
        t = tags[i] if tags and i < len(tags) else None
        if t or (want and team_key(s.get("opponent")) == want):
            out.append((s, t))
    return out


def h2h(series, opponent, line, limit=LIMIT, mids=None, oids=None, core=None):
    """{opponent, n, over, under, push, avg, series} over the newest `limit` meetings, or None without an opponent or a line.
    With a roster core (CS2, h2h_core): roster meetings count too, and each meeting says how it counts: "via" "roster" (3+ of
    tonight's players faced, "faced" names them, "opponent" is the team name then) or "org" (the same team, fewer of tonight's
    players); "core" lists tonight's five."""
    if not team_key(opponent) or not isinstance(line, (int, float)):
        return None
    rows = _meetings(series, opponent, mids, oids, core)[:limit]
    vals = [s["value"] for s, _ in rows]
    out = {"opponent": opponent, "n": len(rows), "over": sum(v > line for v in vals), "under": sum(v < line for v in vals),
           "push": sum(v == line for v in vals), "avg": round(sum(vals) / len(vals), 1) if vals else None,
           "series": [{"date": s.get("date"), "opponent": s.get("opponent"), "value": s["value"]} for s, _ in rows]}
    if core:
        import h2h_core
        for row, (s, t) in zip(out["series"], rows):
            row.update(via="roster" if t else "org", maps=list(s.get("maps") or []), values=list(s.get("values") or []))
            if t:
                row["faced"] = t["faced"]
        out["core"] = h2h_core.public_core(core)
    return out


# ---- SCOUT_BOARD_H2H (default off, owner on): the board's H2H and Season columns ----
# Display only, from the same newest-first series as L5/L10/L20. Counts at the displayed line; nothing graded or recomputed.
def board_enabled():
    return os.environ.get("SCOUT_BOARD_H2H") == "1"


def _count(vals, line):
    if not vals:
        return None
    return {"over": sum(v > line for v in vals), "under": sum(v < line for v in vals), "push": sum(v == line for v in vals), "n": len(vals)}


def board_h2h(series, opponent, line, limit=LIMIT, mids=None, oids=None, core=None):
    """{over, under, push, n, opponent} of the newest `limit` meetings with tonight's opponent (the card's rule: with a CS2 roster
    core, roster meetings count too), or None."""
    if not team_key(opponent) or not isinstance(line, (int, float)):
        return None
    vals = [s["value"] for s, _ in _meetings(series, opponent, mids, oids, core)][:limit]
    t = _count(vals, line)
    return dict(t, opponent=opponent) if t else None


def board_season(series, line, year=None):
    """{over, under, push, n, season} over this calendar year's results (CS2 and LoL seasons run by calendar year), or None."""
    if not isinstance(line, (int, float)):
        return None
    year = str(year or datetime.now(timezone.utc).year)
    vals = [s["value"] for s in series or [] if str(s.get("date") or "")[:4] == year and isinstance(s.get("value"), (int, float))]
    t = _count(vals, line)
    return dict(t, season=year) if t else None
