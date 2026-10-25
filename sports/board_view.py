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

import json
import math
from pathlib import Path

from .model.grade_policy import STATES as _gp_states

VERSION = "bv-1"
WINDOWS = (10, 5)

# ------------------------------------------------------------------------------------------------ vg-1 / sg-1 guards
# vg-1 (owner re-audit 2026-10-24 N1/N2): a market whose Scout model has NOT passed its registered history gate
# (sports/model/artifacts/registry.json, sm-1 "validation" per stat; NFL: all five sm-1 markets failed gate H, see
# MODEL_CARD_NFL.md) never produces a lean, an edge, a confidence or an edge ranking anywhere. The projection and its
# distribution are still shown, labelled GATE_TEXT. mi-1 is built on the sm-1 distribution, so it inherits the gate.
# A stat with no registry entry has passed no gate and is treated the same way.
GATE_VERSION = "vg-1"
GATE_PASSED = _gp_states[1:]                                   # history or forward validated (grade_policy.STATES)
GATE_TEXT = "{LG} model not validated - projection only"
SCOUT_MODELS = ("sm-1", "mi-1")
_REGISTRY = Path(__file__).resolve().parent / "model" / "artifacts" / "registry.json"
_reg_cache = {}


def _registry():
    if "r" not in _reg_cache:
        try:
            _reg_cache["r"] = json.loads(_REGISTRY.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _reg_cache["r"] = {}
    return _reg_cache["r"]


def gate_state(stat):
    """sm-1's registered validation state for this stat ("unvalidated" when the stat is not registered)."""
    v = ((_registry().get("versions") or {}).get("sm-1") or {}).get("validation") or {}
    return v.get(stat, "unvalidated")


def gate_reason(league, stat):
    """None when the market's Scout model passed its registered gate, else the member text (projection only)."""
    if gate_state(stat) in GATE_PASSED:
        return None
    return GATE_TEXT.format(LG=(league or "").upper() or "Scout")


# sg-1 hard sanity guard (all markets). For ONE quote with a Scout view (own forecast or line read) at line L with
# projected mean mu and its lean (lean_of below), the lean / edge / confidence of that row are suppressed when ANY of:
#   ratio_low   L >= RATIO_MIN_LINE and mu < 0.25 x L    (the projection's opportunity mass collapsed; e.g. 0.1 vs 11.5)
#   ratio_high  L >= RATIO_MIN_LINE and mu > 4 x L
#   l10_contra  the strict L10 history at L (board_view hit_counts: o over, u under, n played games with the stat)
#               has n >= L10_MIN_N and the side OPPOSITE the lean won at least L10_CONTRA_SHARE of them
#               (lean Under with >= 6 of 10 Overs; lean Over with >= 6 of 10 Unders; 3 of 5 when only 5 played)
# Lines below RATIO_MIN_LINE (0.5 / 1.5 counts) are discrete enough that a mean far below the line is legitimate (a
# centre's 0.1 threes at 0.5); the L10 rule still applies to them. Each suppressed row is logged (sanity_log).
SANITY_VERSION = "sg-1"
RATIO_LOW, RATIO_HIGH, RATIO_MIN_LINE = 0.25, 4.0, 2.0
L10_MIN_N, L10_CONTRA_SHARE = 5, 0.6
SANITY_TEXT = {"ratio_low": "Held: projection is under 25% of the line",
               "ratio_high": "Held: projection is over 4x the line",
               "l10_contra": "Held: last-10 results contradict the lean"}


def lean_of(po, pu, mu, line):
    """THE lean rule (= web/sports/table.js leanOf): Over when P(Over) > 50% and the mean is above the line (Under
    mirrored), else None."""
    if po is None or pu is None:
        return None
    if po > 0.5 and (mu is None or mu > line):
        return "over"
    if pu > 0.5 and (mu is None or mu < line):
        return "under"
    return None


def sanity_check(mu, line, lean, l10=None):
    """-> None or a reason code (SANITY_TEXT). l10 = [over, under, push, n] strict counts at this line, or None."""
    line = float(line)
    if mu is not None and line >= RATIO_MIN_LINE:
        if mu < RATIO_LOW * line:
            return "ratio_low"
        if mu > RATIO_HIGH * line:
            return "ratio_high"
    if lean and l10 and l10[3] >= L10_MIN_N:
        opp = l10[0] if lean == "under" else l10[1]
        if opp >= L10_CONTRA_SHARE * l10[3] - 1e-9:
            return "l10_contra"
    return None


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


# ------------------------------------------------------------------------------------------- cf-2 row signal (N3/N4)
# THE board row signal, computed server-side in the snapshot (sports/snapshots.guard_row) so every surface renders the
# same numbers: web/sports/table.js propRow reads q.x (ln lean, e edge, c / cw confidence, s edge sort key, b hold).
#   Scout chance  the quote's sm-1 / mi-1 view at its line (own forecast or line read) - snapshots.scout_view
#   market chance market_view mv-1 at THIS offer (mk.at[record]) or the headline when it is at the same line
#   lean          lean_of (chance > 50% AND the mean on the same side of the line) AND, when a market chance exists,
#                 a POSITIVE edge for that side (owner re-audit N4: never a lean the market prices higher); else none
#   edge          100 x (Scout chance - market chance) of the lean side, probability points (not expected value)
#   gap           |Scout Over - market Over| in points; flag at >= GAP_PTS
#   confidence cf-2 = cf-1 (1 + steps: P(lean) >= 55%, >= 60%, edge >= 3, edge >= 6; caps: edge < 0 -> 2, thin L10
#                 (< 10 games) -> 2, wide 80% range (> 1.5x the line) -> 3, gap >= 10 -> 3) PLUS (N3):
#                 market estimate from ONE source -> max 2; |gap| > 15 pts -> max 2. The time-dependent stale-input
#                 cap (forecast > 24 h, line not re-confirmed in 15 min) is applied by the page at render time.
#   edge sort key s = edge for a row whose market estimate has >= 2 sources and |gap| <= 15; edge - SORT_PENALTY
#                 otherwise, so a single-source / large-gap row never ranks above a multi-source row (edges are
#                 within +-100). Held rows (vg-1 / sg-1) and rows without an edge have no key (listed last).
CONF_VERSION = "cf-2"
GAP_PTS, GAP_CAP_PTS, THIN_N, WIDE_REL, SORT_PENALTY = 10.0, 15.0, 10, 1.5, 200.0


def market_at(mk, record_id, line):
    """= table.js marketAt: the market chance at this offer's line, or None."""
    if not mk:
        return None
    at = mk.get("at") or {}
    if record_id in at:
        c = at[record_id]
    elif mk.get("c") and mk.get("l") is not None and float(mk["l"]) == float(line):
        c = mk["c"]
    else:
        c = None
    if c and (c.get("n") or 0) > 0 and c.get("po") is not None and c.get("pu") is not None:
        return c
    return None


def signal(po, pu, mu, line, mkc, l10=None, iv=None):
    """One quote's row signal -> {"ln", "pl", "e", "g", "c", "cw", "s", "n", "v"} (see above)."""
    line = float(line)
    lean = lean_of(po, pu, mu, line)
    edge = gap = None
    if mkc:
        gap = 100.0 * (po - mkc["po"])
        if lean:
            edge = 100.0 * ((po if lean == "over" else pu) - (mkc["po"] if lean == "over" else mkc["pu"]))
            if edge <= 0:                                   # N4: the market prices this side at least as high
                lean, edge = None, None
    out = {"ln": lean, "v": CONF_VERSION, "n": (mkc or {}).get("n") or 0}
    if gap is not None:
        out["g"] = round(gap, 2)
    if not lean:
        out.update(c=1, cw=["No lean"])
        return out
    pl = po if lean == "over" else pu
    out["pl"] = round(pl, 4)
    s, why = 1, []
    if pl >= 0.55 - 1e-12:
        s += 1
        why.append("Scout chance >= 55%")
    if pl >= 0.60 - 1e-12:
        s += 1
        why.append("Scout chance >= 60%")
    if edge is not None and edge >= 3 - 1e-9:
        s += 1
        why.append("edge >= 3 pts")
    if edge is not None and edge >= 6 - 1e-9:
        s += 1
        why.append("edge >= 6 pts")
    if edge is None:
        why.append("no market chance at this line")

    def cap(n, txt):
        nonlocal s
        s = min(s, n)
        why.append(f"{txt} (max {n})")
    if not l10 or l10[3] < THIN_N:
        cap(2, "thin history")
    if iv and iv[0] is not None and iv[1] is not None and (iv[1] - iv[0]) > WIDE_REL * max(abs(line), 1):
        cap(3, "wide distribution")
    if gap is not None and abs(gap) >= GAP_PTS - 1e-9:
        cap(3, "model vs market gap")
    single = mkc is not None and (mkc.get("n") or 0) < 2
    big = gap is not None and abs(gap) > GAP_CAP_PTS + 1e-9
    if single:
        cap(2, "market estimate from one source")
    if big:
        cap(2, "model vs market gap over 15 pts")
    out.update(c=s, cw=why)
    if edge is not None:
        out["e"] = round(edge, 3)
        out["s"] = round(edge - (SORT_PENALTY if (single or big) else 0.0), 3)
    return out


def quote_x(league, stat, po, pu, mu, line, mkc, l10=None, iv=None):
    """The q.x a snapshot / sheet quote carries: a hold {"b", "t", "v"} (vg-1 gate, then sg-1 sanity), else the cf-2
    signal. -> (x, sanity code or None)."""
    gate = gate_reason(league, stat)
    if gate:
        return {"b": "gate", "t": gate, "v": GATE_VERSION}, None
    sig = signal(po, pu, mu, line, mkc, l10, iv)
    code = sanity_check(mu, line, lean_of(po, pu, mu, float(line)), l10)
    if code:
        return {"b": code, "t": SANITY_TEXT[code], "v": SANITY_VERSION}, code
    return sig, None
