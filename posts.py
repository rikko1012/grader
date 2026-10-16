"""Publication drafts read ONLY from saved board forecasts and the line the platform offers now (owner 2026-10-16).

The ten posts of 2026-10-16 carried the right grades but their supporting numbers came from elsewhere ("Over in 15 of
his last 20" - windows picked because they sounded strong). A draft here is built from one saved forecast and states that
forecast's own numbers: platform, line, side, the model's estimate (a percentage, described as a model estimate - never
a hit rate), the grade and the forecast id / time / model version.
The only context is one fixed rule: the grader's own last-10 record at this line (Maps 1-2 totals, the series the grade
used, both sides, pushes out) saved WITH the forecast - shown for Maps 1-2 lines only (a Map 1 line's saved record counts
Maps 1-2 totals, so it is not shown). Historical hit rates are context, never a substitute for the forecast's chance.

Not drafted (the reason is listed): no saved forecast, the platform no longer offers this exact line or side, the board's
current grade differs from the latest saved forecast (wait for it to be logged), a saved grade that is not the letter its
own estimate earns (grader.grade_from_p), a 0-unit / Lean line, a started match. No outside record (e.g. the Pikkit
figures of the picks business) is ever attached to the grader.
"""
import re
import sqlite3
from datetime import datetime, timezone

import grader

LETTERS = ("A+", "A", "A-", "B+", "B", "B-")
SCOPE_WORDS = {"12": "Maps 1-2", "1": "Map 1", "123": "Maps 1-3"}
FOOTER = "Full slate in 1Time 👉 whop.com/1timeesports\nResearch tool, not betting advice. 21+"
X_LIMIT = 280
_URL = re.compile(r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?", re.I)


def x_len(t):
    """X's weighted length: a link counts 23, characters past U+10FF (emoji, CJK) count 2, the rest 1."""
    t = _URL.sub("x" * 23, t)
    return sum(1 if ord(ch) <= 0x10FF else 2 for ch in t)


def _central(start):
    """'Fri 12 PM CT' for a UTC start (America/Chicago)."""
    try:
        from zoneinfo import ZoneInfo
        t = datetime.fromisoformat(str(start).replace("Z", "+00:00")).astimezone(ZoneInfo("America/Chicago"))
    except Exception:          # noqa: BLE001
        return ""
    h = t.hour % 12 or 12
    return f"{t.strftime('%a')} {h}{'' if t.minute == 0 else ':%02d' % t.minute} {'AM' if t.hour < 12 else 'PM'} CT"


def _record(rec_hit):
    """(overs, unders, decided) from the saved 'o/n' last-10 record at this line (pushes out) - None when absent."""
    try:
        o, n = (int(x) for x in str(rec_hit).split("/"))
    except (TypeError, ValueError):
        return None
    return (o, n - o, n) if n > 0 else None


def text(fc, row):
    """The post for one verified saved forecast (fc: forecasts row + its pick; row: the board's offered line)."""
    side = "OVER" if fc["pick"] == "More" else "UNDER"
    stat = "headshots" if fc["prop"] == "hs" else "kills"
    scope = SCOPE_WORDS.get(fc.get("scope") or "12", "Maps 1-2")
    maps = "" if (fc.get("scope") or "12") == "12" else f" ({scope})"
    head = f"{fc['nick']} {side} {fc['line']:g} {stat}{maps} · Grade {fc['grade']} · {fc['p_pick'] * 100:.1f}% model estimate 🧞"
    rec = _record(fc.get("rec_hit"))
    ctx = (f"Grader's last-{rec[2]} record at this line (Maps 1-2): {rec[0]} over, {rec[1]} under. "
           if rec and (fc.get("scope") or "12") == "12" else "")
    who = " vs ".join(x for x in (row.get("team_name") or fc.get("team"), row.get("opp_name") or fc.get("opponent")) if x)
    when = _central(fc.get("match_start") or row.get("start"))
    book = "PrizePicks" if fc["book"] == "prizepicks" else "Underdog"
    return f"{head}\n{ctx}{', '.join(x for x in (who, when) if x)} ({book}).\n{FOOTER}"


def drafts(board_rows, book, db_path, now=None, min_grade="B-"):
    """{"drafts": [...], "skipped": [...]} for one platform's current board rows. Read-only on the pick log."""
    now = now or datetime.now(timezone.utc)
    allowed = LETTERS[:LETTERS.index(min_grade) + 1] if min_grade in LETTERS else LETTERS
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    out, skipped = [], []
    try:
        for r in board_rows or []:
            if (r.get("book") or "underdog") != book or not r.get("side"):
                continue
            label = f"{r.get('nick') or r.get('player')} {r.get('side')} {r.get('line')} {r.get('prop')}"
            if r.get("grade") not in allowed or not (r.get("units") or 0) > 0:
                continue                                            # Lean / 0U / below the asked grade: not a post
            st = r.get("start")
            if st and datetime.fromisoformat(str(st).replace("Z", "+00:00")) <= now:
                skipped.append({"line": label, "why": "match started"})
                continue
            if r.get("unavailable"):
                skipped.append({"line": label, "why": "the platform no longer offers this side"})
                continue
            p = con.execute("SELECT * FROM picks WHERE player_id = ? AND match_slug = ? AND prop = ? AND line = ? "
                            "AND COALESCE(scope, '12') = ? AND book = ? AND COALESCE(line_type, 'standard') = 'standard' "
                            "AND status = 'open' ORDER BY id DESC LIMIT 1",
                            (r.get("player_id"), r.get("match_slug"), r.get("prop"), r.get("line"), r.get("scope") or "12",
                             book)).fetchone()
            f = con.execute("SELECT * FROM forecasts WHERE pick_id = ? ORDER BY id DESC LIMIT 1", (p["id"],)).fetchone() if p else None
            if f is None:
                skipped.append({"line": label, "why": "no saved forecast of this exact line"})
                continue
            if float(f["line"]) != float(r["line"]) or f["pick"] != r["side"]:
                skipped.append({"line": label, "why": "the offered line or side differs from the saved forecast"})
                continue
            if f["grade"] != r.get("grade"):
                skipped.append({"line": label, "why": f"the board shows {r.get('grade')} but the latest saved forecast says "
                                                     f"{f['grade']} - wait for the regrade to be logged"})
                continue
            if grader.grade_from_p(f["p_pick"] or 0) != f["grade"]:
                skipped.append({"line": label, "why": f"the saved grade {f['grade']} is not the letter its {f['p_pick']:.1%} "
                                                     f"model estimate earns ({grader.grade_from_p(f['p_pick'] or 0)})"})
                continue
            fc = dict(f, nick=p["player"], prop=p["prop"], scope=p["scope"], book=p["book"], team=p["team"], opponent=p["opponent"])
            t = text(fc, r)
            out.append({"forecast_id": f["id"], "pick_id": p["id"], "logged_at": f["logged_at"], "model_version": f["model_version"],
                        "platform": book, "line": f["line"], "side": f["pick"], "chance": f["p_pick"], "grade": f["grade"],
                        "stake_units_saved": f["units"], "stake_units_board": r.get("units"), "projection": f["lambda"],
                        "match_start": p["match_start"], "last10_saved": f["rec_hit"], "text": t, "chars": x_len(t),
                        "fits_x": x_len(t) <= X_LIMIT, "verified": True})
    finally:
        con.close()
    out.sort(key=lambda d: (LETTERS.index(d["grade"]), -(d["chance"] or 0)))
    return {"platform": book, "drafts": out, "skipped": skipped,
            "rule": "built only from the latest saved forecast of a line the platform offers now at that number and side; "
                    "context = the grader's last-10 record at this line saved with the forecast (fixed window, both sides, "
                    "Maps 1-2 lines only)"}
