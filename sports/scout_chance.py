"""THE one definition of a "Grader chance" (owner audit 2026-10-24 bug 5: the slate card said 75, the game page 49).

A Grader chance is a displayed sm-1 / mi-1 forecast attached to an ORDINARY pick'em / book quote (not Kalshi, not an
alternate / promotional line) that is offered (at least one side), on the offer's CURRENT revision line (the board
only attaches a forecast to the current revision, or to an earlier revision at the identical line / stat / scope /
sides), of a game that has NOT started. A line read (the same saved distribution read at another line) is not a
forecast and is counted separately.

Counts are of MARKETS (one player x stat x scope of one game) with at least one Grader chance - the unit the board and
the game page list - plus the number of quotes, so every surface can show the same number.

Hidden markets: a market row on the board with no ordinary quote is not listed by default; hidden_reason() names
why, in member language, for the "N markets hidden: ..." note.
"""
from __future__ import annotations

DEFINITION = "sc-1"
MODELS = ("sm-1", "mi-1")


def models():
    """MODELS, plus nba8-1 only while NBA_STATS8 is on (default off -> MODELS exactly)."""
    from . import capabilities
    return capabilities.nba8_models(MODELS)
TEXT = ("A Grader chance is a sm-1 / mi-1 forecast on an offered, current line of a game that has not started "
        "(ordinary pick'em / book lines; Kalshi rungs, alternate and promotional lines excluded).")


def is_scout_chance(q, forecasts, started):
    """q: an ordinary quote view of a board row (snapshots.offer_view + forecast_id)."""
    if started or not q.get("forecast_id"):
        return False
    f = forecasts.get(q["forecast_id"]) or {}
    if f.get("model_version") not in models():
        return False
    if q.get("availability", "open") != "open":
        return False
    return any(s.get("offered") for s in q.get("sides") or ())


def row_count(row, forecasts, started):
    """-> number of Prop Grader-chance quotes in one market row (row['quotes'] = its ordinary quotes)."""
    return sum(1 for q in row.get("quotes") or () if is_scout_chance(q, forecasts, started))


def hidden_reason(row):
    """Why a market row is not listed by default, or None when it is listed. row: the board row (quotes = ordinary
    quotes; kalshi_rungs / alternates / promotional = counts or lists)."""
    if row.get("quotes"):
        return None
    n = lambda k: row[k] if isinstance(row.get(k), int) else len(row.get(k) or ())   # noqa: E731
    if n("alternates"):
        return "alternate lines only"
    if n("kalshi_rungs") or row.get("kalshi"):
        return "Kalshi prices only"
    if n("promotional"):
        return "promotional lines only"
    return "no ordinary line"


def event_counts(rows, forecasts, started):
    """rows: one game's board rows. -> {"markets", "quotes", "line_reads", "listed", "hidden": {"total", "reasons"}}."""
    out = {"definition": DEFINITION, "markets": 0, "quotes": 0, "line_reads": 0, "listed": 0,
           "hidden": {"total": 0, "reasons": {}}}
    for r in rows:
        c = row_count(r, forecasts, started)
        out["quotes"] += c
        out["markets"] += 1 if c else 0
        out["line_reads"] += sum(1 for q in r.get("quotes") or () if q.get("line_read"))
        why = hidden_reason(r)
        if why is None:
            out["listed"] += 1
        else:
            out["hidden"]["total"] += 1
            out["hidden"]["reasons"][why] = out["hidden"]["reasons"].get(why, 0) + 1
    return out
