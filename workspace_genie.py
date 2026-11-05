"""SCOUT_GENIE: payload-grounded questions about one research line, behind its own flag (default off, owner switches it on).

The drawer's "Ask the Genie" tab sends one question about the line it shows. The answer is composed only from that line's /line
payload (the same shared, single-flight copy the drawer reads): saved projection, saved chance, grade and stake reason, L10 tally,
head-to-head, other books, market prices, line move, saved stats and start time. No model call, no new prediction, nothing written
to any forecast or grade. Every number in an answer is then checked against the numbers in the payload; a sentence carrying a
number the payload does not hold is removed before the answer leaves the server (`removed_numbers` counts them).

Daily cap: the sports assistant's quota pattern (sports/api_assist.py + sports/assistant/runs.py _reserve), copied here because
CS2 never imports sports: one SQLite row per (member, UTC day), reserved with one conditional UPDATE so two presses cannot both take
the last question. Only an answered question counts; an unmatched question or a refused request costs nothing.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

QUESTION_MAX = 300
DAILY_DEFAULT = 25
PER_MIN = 10
KEEP_DAYS = 7

_NUM_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
_DB = {"path": None, "con": None, "pruned": None}
_DB_LOCK = threading.Lock()


def enabled():
    return os.environ.get("SCOUT_GENIE") == "1"


def daily_limit():
    try:
        v = int(os.environ.get("SCOUT_GENIE_DAILY") or DAILY_DEFAULT)
    except ValueError:
        v = DAILY_DEFAULT
    return max(1, min(v, 500))


def utc_day(now=None):
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------------------------------------ quota

def _con(data_dir):
    p = str(Path(data_dir) / "genie.db")
    if _DB["con"] is None or _DB["path"] != p:
        if _DB["con"] is not None:
            _DB["con"].close()
        con = sqlite3.connect(p, timeout=2.0, check_same_thread=False, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS quota (member TEXT NOT NULL, day TEXT NOT NULL, questions INTEGER NOT NULL DEFAULT 0, "
                    "PRIMARY KEY (member, day))")
        _DB.update(path=p, con=con, pruned=None)
    return _DB["con"]


def reset():
    with _DB_LOCK:
        if _DB["con"] is not None:
            _DB["con"].close()
        _DB.update(path=None, con=None, pruned=None)


def _doc(used, limit, day):
    return {"day": day, "questions_used": used, "questions_limit": limit, "remaining_questions": max(0, limit - used)}


def quota(data_dir, member, day=None):
    day, lim = day or utc_day(), daily_limit()
    with _DB_LOCK:
        r = _con(data_dir).execute("SELECT questions FROM quota WHERE member=? AND day=?", (member, day)).fetchone()
    return _doc(r[0] if r else 0, lim, day)


def reserve(data_dir, member, day=None):
    """(True, quota) when one question was taken from today's allowance, (False, quota) when none is left."""
    day, lim = day or utc_day(), daily_limit()
    with _DB_LOCK:
        con = _con(data_dir)
        if _DB["pruned"] != day:                     # old days are not needed: one bounded delete a day
            cut = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
            con.execute("DELETE FROM quota WHERE day < ?", (cut,))
            _DB["pruned"] = day
        con.execute("INSERT OR IGNORE INTO quota (member, day) VALUES (?, ?)", (member, day))
        ok = con.execute("UPDATE quota SET questions = questions + 1 WHERE member=? AND day=? AND questions < ?",
                         (member, day, lim)).rowcount == 1
        used = con.execute("SELECT questions FROM quota WHERE member=? AND day=?", (member, day)).fetchone()[0]
    return ok, _doc(used, lim, day)


# --------------------------------------------------------------------------------------------------------- number check

def _canon(v):
    return round(float(v), 4)


def allowed_numbers(doc):
    """Every number the payload holds, in the forms the drawer prints: as saved, to one decimal, and a chance as a percent."""
    out, stack = set(), [doc]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, (list, tuple)):
            stack.extend(x)
        elif isinstance(x, bool) or x is None:
            continue
        elif isinstance(x, (int, float)):
            out.add(_canon(x))
            out.add(_canon(round(x, 1)))
            if 0 <= x <= 1:
                out.add(_canon(round(x * 100, 1)))
        elif isinstance(x, str):
            out.update(_canon(t) for t in _NUM_RE.findall(x))
    return out


def verify(sentences, allowed):
    """(kept sentences, removed count): a sentence goes when any number in it is not in the payload."""
    kept, removed = [], 0
    for s in sentences:
        if all(_canon(t) in allowed for t in _NUM_RE.findall(s)):
            kept.append(s)
        else:
            removed += 1
    return kept, removed


# -------------------------------------------------------------------------------------------------------------- answers

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _pct(p):
    return f"{p * 100:.1f}%"


def _tenth(v):
    r = round(v, 1)
    return str(int(r)) if r == int(r) else str(r)


def _cap(s):
    return s[:1].upper() + s[1:] if s else s


def _verdict(rec, side):
    """upgrade.js verdictText, word for word, so the Genie and the drawer name a pick the same way."""
    s = rec.get("scout") or {}
    sd = side or s.get("side")
    if not sd:
        return "No lean"
    if s.get("side") and sd != s.get("side"):
        return f"Scout side is {_cap(s['side'])}"
    if s.get("hold"):
        return f"Research estimate {_cap(sd)}"
    if s.get("grade"):
        return f"{s['grade']} {_cap(sd)}" + (" · Research grade" if rec.get("sport") in ("nba", "nfl") else "")
    return f"Lean {_cap(sd)}"


def _read(rec, side, where=""):
    """One sentence naming Scout's read. Asked about the other side: Scout's own side, said plainly."""
    s = rec.get("scout") or {}
    if s.get("side") and side and side != s.get("side"):
        return f"Scout's side{where} is {_cap(s['side'])}, not {_cap(side)}."
    return f"Scout's read{where} is {_verdict(rec, side)}."


def _chance(rec, side):
    s = rec.get("scout") or {}
    return s.get("p_under") if side == "under" else s.get("p_over") if side == "over" else None


TOPICS = (
    ("pick", ("should", "take", "pick", "side", "lean", "play", "which way", "over or under")),
    ("projection", ("project", "expect", "predict", "forecast", "how many")),
    ("chance", ("chance", "probab", "likely", "percent", "%")),
    ("grade", ("grade", "stake", "unit", "why", "reason", "rating of the pick")),
    ("history", ("hit", "last 10", "l10", "history", "recent", "average", "avg", "past", "results", "form")),
    ("h2h", ("h2h", "head to head", "head-to-head", "against", "opponent", "meet", "matchup")),
    ("books", ("book", "elsewhere", "better line", "other line", "compare", "underdog", "prizepicks", "platform", "sources")),
    ("market", ("market", "price", "implied", "odds", "vig", "margin")),
    ("move", ("move", "moved", "open", "bump", "changed")),
    ("stats", ("kpr", "rating", "adr", "kast", "headshot rate", "stat", "per round", "per map", "maps played", "context", "input")),
    ("start", ("when", "start", "time", "kick", "begin", "lock")),
)
HELP = ("I answer from this line's saved data only. Ask about the projection, the saved chance, the grade, the last 10 results, "
        "head-to-head, other books, market prices, a line move, saved stats or the start time.")
SUGGESTIONS = ("What is the projection?", "How often did this line hit?", "What is the head-to-head?", "Is there a better line elsewhere?")


def topics_of(question):
    q = " ".join(question.lower().replace("?", " ").split())
    found = [name for name, words in TOPICS if any(w in q for w in words)]
    return found[:3]


def _say(topic, doc, side, q):
    rec = doc.get("record") or {}
    s, offer, mk, pl = rec.get("scout") or {}, rec.get("offer") or {}, rec.get("market") or {}, rec.get("player") or {}
    name, line, stat = pl.get("name") or "This player", offer.get("line"), f"{mk.get('period_label') or ''} {mk.get('stat_label') or ''}".strip()
    book = offer.get("platform_label") or offer.get("platform") or "this book"
    p = _chance(rec, side)
    if topic == "pick":
        out = [_read(rec, side, f" on {book} {line}")]
        if _num(p):
            out.append(f"The saved chance for {_cap(side)} {line} is {_pct(p)}.")
        return out
    if topic == "projection":
        if not _num(s.get("projection")):
            return [f"No projection was saved for {name} {stat}."]
        return [f"{name} has a saved projection of {_tenth(s['projection'])} {stat} against a line of {line}."]
    if topic == "chance":
        if not _num(p):
            return [f"No chance was saved for {_cap(side)} {line}."]
        out = [f"The saved chance for {_cap(side)} {line} is {_pct(p)}."]
        if _num(s.get("p_push")) and s["p_push"] > 0:
            out.append(f"The saved push chance is {_pct(s['p_push'])}; a push is not a win.")
        return out
    if topic == "grade":
        out = [_read(rec, side)]
        if _num(s.get("units")) and not s.get("hold"):
            out.append(f"The stake is {_tenth(s['units'])}U.")
        if s.get("stake_reason"):
            out.append(str(s["stake_reason"]).rstrip(".") + ".")
        fc = doc.get("forecast") or {}
        if fc.get("model_version") or s.get("model_version"):
            out.append(f"Model {fc.get('model_version') or s.get('model_version')}.")
        return out
    if topic == "history":
        t = (doc.get("history") or {}).get("tally") or (rec.get("history") or {}).get("l10")
        if not t or not t.get("n"):
            return ["Past results at this line are not available."]
        hits = t.get("under") if side == "under" else t.get("over")
        out = [f"{hits} of the last {t['n']} results went {_cap(side)} {line}; {t.get('push') or 0} pushed."]
        avg = (doc.get("history") or {}).get("avg")
        if _num(avg):
            out.append(f"The average over those results is {_tenth(avg)}.")
        out.append("This is a past count, not a forecast.")
        return out
    if topic == "h2h":
        hh = doc.get("h2h")
        if not hh or not hh.get("opponent"):
            return ["No head-to-head data is saved for this line."]
        if not hh.get("n"):
            return [f"No past meetings with {hh['opponent']} are in this player's saved results."]
        out = [f"Against {hh['opponent']}: {hh.get('over')} of {hh['n']} went Over {line} and {hh.get('under')} of {hh['n']} went Under."]
        if _num(hh.get("avg")):
            out.append(f"The average in those meetings is {_tenth(hh['avg'])}.")
        return out
    if topic == "books":
        mine = (offer.get("platform"), str(offer.get("offer_id")))
        others = [b for b in (doc.get("books") or []) if (b.get("platform"), str(b.get("row_id"))) != mine]
        mb = doc.get("market_books") or {}
        extra = [b for b in (mb.get("same") or []) + (mb.get("other") or []) if b.get("platform") != offer.get("platform")]
        rows = [f"{b.get('platform_label') or b.get('platform')} lists {b.get('line')}" + (f" ({b.get('line_type')})" if b.get("line_type") not in (None, "standard") else "")
                for b in (others + extra)[:6]]
        if not rows:
            return ["No other book lists this exact player, stat and period right now."]
        return ["; ".join(rows) + ".", "A different line or payout type is a different pick with its own forecast."]
    if topic == "market":
        m = doc.get("market") or {}
        if not m.get("available"):
            return [str(m.get("reason") or "No market prices are saved for this line.")]
        return [f"Market chance from {m.get('source')}: Over {_pct(m['p_over'])}, Under {_pct(m['p_under'])}."]
    if topic == "move":
        if offer.get("moved_from") is None:
            return [f"The {book} line has not moved from where it opened."]
        return [f"The {book} line moved from {offer['moved_from']} to {line}."]
    if topic == "stats":
        rows = [r for r in ((doc.get("context") or {}).get("rows") or []) if r.get("label") and r.get("value") is not None]
        if not rows:
            return ["No saved stats are on file for this line."]
        words = set(q.lower().split())
        pick = [r for r in rows if words & set(str(r["label"]).lower().split()) - {"per", "the", "of", "a", "last"}] or rows
        return ["; ".join(f"{r['label']}: {r['value']}" for r in pick[:6]) + "."]
    if topic == "start":
        ev = rec.get("event") or {}
        if not ev.get("start_utc"):
            return ["The start time is not saved for this line."]
        return [f"{ev.get('label') or 'The match'} starts at {ev['start_utc']} UTC."]
    return []


def answer(doc, question, side):
    """{"sentences", "topics", "matched", "removed_numbers"}: composed from `doc` only and checked against it."""
    topics = topics_of(question)
    if not topics:
        return {"sentences": [HELP], "topics": [], "matched": False, "removed_numbers": 0, "suggestions": list(SUGGESTIONS)}
    said = []
    for t in topics:
        said.extend(_say(t, doc, side, question))
    kept, removed = verify(said, allowed_numbers(doc))
    if not kept:                    # nothing survived the number check: the help text, free (no status line, no charge)
        return {"sentences": [HELP], "topics": topics, "matched": False, "removed_numbers": removed, "suggestions": list(SUGGESTIONS)}
    return {"sentences": kept, "topics": topics, "matched": True, "removed_numbers": removed}
