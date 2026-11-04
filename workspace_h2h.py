"""Head-to-head for the research card: a player's saved past results against tonight's opponent, at tonight's line.

Display only (owner 2026-11-04: the all-sports cards lacked the H2H block of the classic CS2 card). It reads the same newest-first series
the card's history chart uses; nothing is recomputed or graded. Team names match exactly after case and punctuation are dropped, never
loosely: an academy team is not its main team (memory: exact team-name variants, never fuzzy)."""
import re

LIMIT = 10


def team_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def h2h(series, opponent, line, limit=LIMIT):
    """{opponent, n, over, under, push, avg, series} over the newest `limit` meetings, or None without an opponent or a line."""
    want = team_key(opponent)
    if not want or not isinstance(line, (int, float)):
        return None
    rows = [s for s in series or [] if team_key(s.get("opponent")) == want and isinstance(s.get("value"), (int, float))][:limit]
    vals = [s["value"] for s in rows]
    return {"opponent": opponent, "n": len(rows), "over": sum(v > line for v in vals), "under": sum(v < line for v in vals),
            "push": sum(v == line for v in vals), "avg": round(sum(vals) / len(vals), 1) if vals else None,
            "series": [{"date": s.get("date"), "opponent": s.get("opponent"), "value": s["value"]} for s in rows]}
