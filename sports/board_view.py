"""Per-prop summary fields for the Prop board table (board agent, bv-1).

ONE definition of the history hit rate shown on a board row, shared by the board table (web/sports/table.js
SportsRows.propRow) and any other sheet (PrizePicks / Underdog / consistency sheets reuse `hit_counts` and the
board_view.json file instead of re-implementing it).

Hit rate at a line = the player's last N PLAYED games that have a value for the stat (newest first; DNP games and
games without the stat excluded - the same filter as web/sports/stats.js windowSummary), counted STRICTLY:
Over = value > line, Under = value < line, Push = value == line (kept separate, never folded into either side).

Written by sports/snapshots.py build() as <SPORTS_DATA_DIR>/snapshots/<league>/board_view.json:
    {"version": "bv-1", ..., "rows": {<market key>: {"lines": {"<line>": [o10, u10, p10, n10, o5, u5, p5, n5]},
                                                   "seasons10": [2026, 2027], "from10": iso, "to10": iso,
                                                   "played": n, "sd10": x}}}
Served by GET /api/sports/board?league=..&part=view (sports/api_board.py, snapshot bytes only)."""
from __future__ import annotations

import math

VERSION = "bv-1"
WINDOWS = (10, 5)


def played_values(games, stat):
    """games newest first -> [(value, season, start_utc)] of played games with a value for this stat."""
    out = []
    for g in games or ():
        if g.get("participation") != "played":
            continue
        v = (g.get("values") or {}).get(stat)
        if v is None:
            continue
        out.append((float(v), g.get("season"), g.get("start_utc")))
    return out


def hit_counts(values, line, n):
    """Strict counts over the first n values -> [over, under, push, n_used]."""
    w = values[:n]
    o = sum(1 for v in w if v > line)
    u = sum(1 for v in w if v < line)
    return [o, u, len(w) - o - u, len(w)]


def _sd(xs):
    if len(xs) < 2:
        return None
    mu = sum(xs) / len(xs)
    return round(math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)), 3)


def row_summary(stat, lines, games):
    """One board market (player + stat) -> its summary at every given line."""
    pv = played_values(games, stat)
    vals = [x[0] for x in pv]
    w10 = pv[:10]
    out = {"lines": {}, "played": len(pv), "seasons10": sorted({x[1] for x in w10 if x[1] is not None}),
           "from10": w10[-1][2] if w10 else None, "to10": w10[0][2] if w10 else None, "sd10": _sd(vals[:10])}
    for ln in sorted({float(x) for x in lines if x is not None}):
        out["lines"][line_key(ln)] = hit_counts(vals, ln, 10) + hit_counts(vals, ln, 5)
    return out


def line_key(ln):
    """18.5 -> "18.5", 4.0 -> "4" (same text as stats.js line())."""
    ln = float(ln)
    return str(int(round(ln))) if abs(ln - round(ln)) < 1e-9 else str(round(ln, 1))


def player_rows(rows, games):
    """Board rows of ONE player (snapshots markets_rows values) + his games -> {market key: summary}."""
    out = {}
    for r in rows:
        lines = [q.get("line") for q in r.get("quotes", []) + r.get("kalshi", [])]
        out[r["key"]] = row_summary(r["stat"], lines, games)
    return out


class StreamWriter:
    """Writes board_view.json row by row (no in-memory copy of all rows): tmp file in the target folder, then an atomic
    os.replace on close(); abort() (or garbage collection before close) removes the tmp file. Memory = one player's rows at a time."""

    def __init__(self, path, meta):
        import json as _json
        import os as _os
        import tempfile as _tf
        self._json, self._os, self.path = _json, _os, path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, self.tmp = _tf.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        self.f = _os.fdopen(fd, "w", encoding="utf-8")
        head = _json.dumps(meta, separators=(",", ":"), default=str, allow_nan=False)
        self.f.write(head[:-1] + ("," if len(meta) else "") + '"rows":{')
        self.n, self.done = 0, False

    def add(self, rows):
        for k, v in rows.items():
            self.f.write(("," if self.n else "") + self._json.dumps(k) + ":" +
                         self._json.dumps(v, separators=(",", ":"), allow_nan=False))
            self.n += 1

    def close(self):
        self.f.write("}}")
        self.f.close()
        size = self._os.path.getsize(self.tmp)
        self._os.replace(self.tmp, self.path)
        self.done = True
        return size

    def __del__(self):                                   # a failed build never leaves its tmp file behind
        if not getattr(self, "done", True):
            self.abort()

    def abort(self):
        self.done = True
        try:
            self.f.close()
            self._os.unlink(self.tmp)
        except OSError:
            pass
