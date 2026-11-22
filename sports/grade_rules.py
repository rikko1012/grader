"""Board grade rules from the 2026-11-21 grading audit (audit_1121, round 2 track nba-core). Pure python; every rule is
applied ONCE, in the worker's snapshot build (snapshots.guard_row / build), and every other surface (board, cards, sheets,
workspace hub, slip pool, tracked picks) reads the result from the snapshot - nothing here runs per member request.

Rules and their switches (each one line to roll back: set the variable and rebuild the snapshot). Round-2 review
(fixer, 2026-11-21): only the verified display bug fix defaults ON; every untested rule is shadow (default OFF).

  SPORTS_PP_SIDE_HOLD (default ON; =0 rolls back) - os-2 side hold, a DISPLAY BUG FIX under the owner's 2026-11-06
      one-sided rule (cap-2): a PrizePicks quote whose Scout side PrizePicks does not list gets no side / letter (hold
      "side", text "Scout's side is not offered on this line"). Before: the board lettered the UNLISTED side (live
      2026-11-21: De'Aaron Fox 6.0 Under B, Stephon Castle 7.0 Under B, Derrick White 5.0 Under C on Over-only lines).
      It never touches the side PrizePicks DOES list (Cam Spencer A Over / Darius Acuff Jr. B Over on Over-only lines
      keep their letters), never reclassifies the line, never stops a forecast and never changes the record.

  SPORTS_PP_ONESIDED (default OFF; =1 turns it on - owner call, untested on settled results) - os-2 reclassification:
      a PrizePicks "standard" line that lists ONE side becomes variant "one_sided" (capabilities.variant_of): shown
      "Over only" / "Under only", never forecast, graded, staked or slipped - which also removes the letter on the side
      that IS offered. Its public-record / ledger drop of past PrizePicks one-sided forecasts applies only to forecasts
      saved at or after SPORTS_PP_ONESIDED_FROM (ISO UTC; unset / 'never' / invalid = no record drop at all), so the
      record never rewrites what members were shown. The Underdog cap-2 drop is unchanged (main's rule).

  NBA_MVLEAN1 (default OFF; =1 turns it on - owner board preview first, it removes ~6% of NBA letters) + NBA_MVLEAN1_FROM
      (ISO UTC; unset / 'never' / invalid = the rule never applies, on the board or in the record) - mvlean-1, the
      CONFIRMED audit rule F03_mvlean1 (frozen logs/audit_1121/frozen/nba/F03_mvlean1.py, judged holdout/nba, verified
      verify/nba). For an NBA pick'em quote whose Scout side is known: mv = (+1 Over / -1 Under) x (line - open), where
      open = the line of the FIRST sm-1 forecast saved for the same (event, player, stat, platform). If mv > 0.75 the
      side is kept and shown as "Lean <side>" (letter Lean, stake 0; the grade tooltip says why). One activation test
      (mvlean_active: flag on, FROM set, forecast time >= FROM) decides the board, the record's Grades table and its
      recent list alike, so the board and the Public Record show the same label. Holdout graded 2113-1824-47 (53.67%)
      vs 2218-1961-47 (53.07%), +0.60 pt, game CI [+0.15, +1.07].

  SPORTS_GP_SIDE_FIX (default OFF; =1 turns it on - not walk-forward passed: (d) holdout 1-2, (b) no NBA holdout rows)
      - sports-gp-1 side choice (model/grade_policy.apply): (b) an offer-forced side the model rates under 50% gets no
      side / letter; (d) an exact 50/50 resolves by the projection mean. Off = main's max().

  SPORTS_LATEST_LINE (default OFF; =1 turns it on for NBA, NFL also needs SPORTS_LATEST_LINE_NFL=1) - the public record
      uses the book's LATEST offered line per (game, player, stat, platform). Not proven at the record's own pick rule
      (+0.19 train game CI [-0.45, 0.82] / +0.48 holdout [-0.46, 1.39]; NFL flat). Shadow.

  NBA_BEST_PROP (default OFF; =1 turns it on - display only, never rendered in a browser, ~85-90% Unders: owner call)
      - bp-1: for each NBA player and game, the one lettered pick'em quote with the highest shown chance is marked
      featured (q.x.bp = 1). No grade, chance or stake changes.
"""
from __future__ import annotations

import os

MVLEAN_VERSION = "mvlean-1"
MV_THRESHOLD = 0.75                       # frozen F03_mvlean1 THRESHOLD
SIDE_VERSION = "os-2"
BEST_VERSION = "bp-1"
PICKEM = ("prizepicks", "underdog")
SIDE_TEXT = {"over": "Over only line - Scout grades lines that offer both sides",
             "under": "Under only line - Scout grades lines that offer both sides",
             None: "Scout's side is not offered on this line"}
HOLD_TEXT = SIDE_TEXT[None]               # SPORTS_PP_SIDE_HOLD alone: the line's listed side keeps its letter
MV_WHY = "Shown as Lean: this line already moved more than 0.75 toward Scout's side since it opened"


def _on(name, default="1"):
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


def _utc(v):
    """An ISO time -> 'YYYY-MM-DDTHH:MM:SSZ' (UTC) or None."""
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from(name):
    """A start time from the environment (ISO UTC), normalised, or None: unset, 'never' or unparsable = never applies."""
    v = os.environ.get(name, "").strip()
    if not v or v.lower() in ("never", "0", "off", "none", "false", "no"):
        return None
    return _utc(v)


def _at_or_after(at, start):
    """True when the forecast time `at` is at or after `start` (a _from value; None = never)."""
    if start is None or not at:
        return False
    a = _utc(at)
    return a is not None and a >= start


def mvlean_on(league=None):
    """NBA_MVLEAN1 (default OFF). The rule acts only where mvlean_active() also holds (NBA_MVLEAN1_FROM set)."""
    return (league is None or league == "nba") and _on("NBA_MVLEAN1", "0")


def mvlean_active(league, forecast_at):
    """The ONE activation test of mvlean-1 for the board, the record's Grades table and its recent list: flag on,
    NBA_MVLEAN1_FROM set and the forecast saved at or after it."""
    return mvlean_on(league) and _at_or_after(forecast_at, mvlean_from())


def pp_side_hold_on():
    return _on("SPORTS_PP_SIDE_HOLD", "1")


def pp_onesided_on():
    return _on("SPORTS_PP_ONESIDED", "0")


def pp_onesided_record_drop(forecast_at):
    """os-2 record / ledger drop of a PrizePicks one-sided forecast: only with SPORTS_PP_ONESIDED on AND the forecast
    saved at or after SPORTS_PP_ONESIDED_FROM (unset = never: the record keeps what members were shown)."""
    return pp_onesided_on() and _at_or_after(forecast_at, _from("SPORTS_PP_ONESIDED_FROM"))


def side_fix_on():
    return _on("SPORTS_GP_SIDE_FIX", "0")


def latest_line_on(league=None):
    """SPORTS_LATEST_LINE (default off - not proven at the record's own pick rule; see the module doc). NFL also needs
    SPORTS_LATEST_LINE_NFL=1 (default off: no NFL gain, train 56.16% -> 55.53%, holdout 48.72% -> 49.21%)."""
    if not _on("SPORTS_LATEST_LINE", "0"):
        return False
    return league in (None, "nba") or _on("SPORTS_LATEST_LINE_NFL", "0")


def best_prop_on(league=None):
    return (league is None or league == "nba") and _on("NBA_BEST_PROP", "0")


# ------------------------------------------------------------------------------------------------------- mvlean-1
def platform_of_record(rid):
    """'offer:prizepicks:pp:123:r2:ab12' -> 'prizepicks' (store record ids are kind:source:source_key:rN:hash)."""
    parts = str(rid or "").split(":", 2)
    return parts[1] if len(parts) >= 3 and parts[0] == "offer" else None


def move_toward(side, line, open_line):
    """(+1 Over / -1 Under) x (line - open): how far the line has already moved toward Scout's side, or None."""
    if side not in ("over", "under") or line is None or open_line is None:
        return None
    try:
        return (1.0 if side == "over" else -1.0) * (float(line) - float(open_line))
    except (TypeError, ValueError):
        return None


def mvlean_from():
    """The first forecast time mvlean-1 applies (NBA_MVLEAN1_FROM, ISO UTC) normalised, or None (unset / 'never' /
    invalid: the rule never relabels, on the board or in the record). Set it to the deploy time."""
    return _from("NBA_MVLEAN1_FROM")


def mvlean_demotes(side, line, open_line):
    """True when the frozen F03_mvlean1 rule shows this side as Lean (mv > 0.75)."""
    mv = move_toward(side, line, open_line)
    return mv is not None and mv > MV_THRESHOLD


def shown_letter(league, r, letter):
    """The letter the board showed for a settled record row r (lean, line, forecast_at, mv_open, model): 'Lean' when
    mvlean-1 demoted it, else `letter`. The record's Grades table AND its recent list use it, so both match the board."""
    if letter in ("A", "B", "C") and league == "nba" and (r.get("model") or "sm-1") == "sm-1" \
            and mvlean_active(league, r.get("forecast_at")) \
            and mvlean_demotes(r.get("lean"), r.get("line"), r.get("mv_open")):
        return "Lean"
    return letter


class OpenLines:
    """The first sm-1 forecast line per (event, player, stat, platform), built from the forecasts the snapshot build
    already streams (snapshots._iter_forecasts: every forecast ever saved for the slate's games)."""

    def __init__(self):
        self._first = {}

    def add(self, fc):
        if getattr(fc, "model_version", None) != "sm-1" or (getattr(fc, "scope", "full_game") or "full_game") != "full_game":
            return
        plats = {platform_of_record(r) for r in (getattr(fc, "offer_snapshot_ids", None) or ())} - {None}
        for p in plats:
            if p not in PICKEM:
                continue
            k = (fc.event_id, fc.player_id, fc.stat, p)
            cur = self._first.get(k)
            if cur is None or (fc.forecast_at, fc.forecast_id) < (cur[1], cur[2]):
                self._first[k] = (float(fc.line), fc.forecast_at, fc.forecast_id)

    def get(self, event_id, player_id, stat, platform):
        """(open line, first forecast_at) or None."""
        v = self._first.get((event_id, player_id, stat, platform))
        return (v[0], v[1]) if v else None

    def __len__(self):
        return len(self._first)


# ---------------------------------------------------------------------------------------------- per-quote marking
def offered_sides(q):
    """{'over', 'under'} offered on a snapshot quote view (its 'sides' list) or a compact quote ('o' = [over, under])."""
    if q.get("sides") is not None:
        return {s.get("side") for s in q.get("sides") or () if s.get("offered")}
    o = q.get("o") or ()
    return {s for s, f in zip(("over", "under"), o) if f}


def side_hold(q, x):
    """os-2: the hold for a PrizePicks quote whose Scout side is not offered on it, or None. PrizePicks only: it has no
    per-side suspension (its listed sides are the line's type), while an Underdog / sportsbook side that is suspended right
    now never changes a stored two-sided line's grading (cap-2)."""
    if not pp_side_hold_on() or not x or not x.get("ln") or q.get("platform") != "prizepicks":
        return None
    on = offered_sides(q)
    if not on or x["ln"] in on:
        return None                              # the side PrizePicks lists keeps its letter (never removed here)
    only = next(iter(on)) if len(on) == 1 else None
    # SPORTS_PP_ONESIDED off (default): the line is still graded on its listed side, so never say it is not graded
    t = SIDE_TEXT.get(only, HOLD_TEXT) if pp_onesided_on() else HOLD_TEXT
    return {"b": "side", "t": t, "v": SIDE_VERSION}


def mark_quote(league, row, q, x, opens, forecast_at=None):
    """Apply mvlean-1 to one quote's signal x in place (letter -> Lean, side kept). -> mv when demoted, else None.
    forecast_at = the quote's forecast time (mvlean_active: the same test the record uses). Only A-C letters are
    marked (a quote already Lean is not counted as demoted)."""
    if opens is None or not x or not x.get("ln") or x.get("gr") not in ("A", "B", "C") \
            or q.get("platform") not in PICKEM or not mvlean_active(league, forecast_at):
        return None
    op = opens.get(row.get("event_id"), row.get("player_id"), row.get("stat"), q.get("platform"))
    if op is None:
        return None
    mv = move_toward(x["ln"], q.get("line"), op[0])
    if mv is None or mv <= MV_THRESHOLD:
        return None
    x["gr0"] = x["gr"]                       # the letter before the rule (admin / audit only; members see Lean)
    x["gr"] = "Lean"
    x["mv"] = round(mv, 3)
    x["mo"] = op[0]
    x["mr"] = MVLEAN_VERSION
    x["mw"] = MV_WHY                         # the grade tooltip's reason (a Lean shown with a chance in a letter band)
    return mv


_LETTER_RANK = {"A": 0, "B": 1, "C": 2}


def feature_best(league, rows):
    """bp-1 (display only): mark q.x.bp = 1 on one quote per (event, player). -> number of featured quotes."""
    if not best_prop_on(league):
        return 0
    best = {}
    for r in rows:
        for q in r.get("quotes") or ():
            x = q.get("x") or {}
            if x.get("b") or not x.get("ln") or x.get("gr") not in _LETTER_RANK or q.get("platform") not in PICKEM:
                continue
            if x["ln"] not in offered_sides(q):
                continue
            pl = x.get("pl")
            if not isinstance(pl, (int, float)):
                continue
            key = (-float(pl), 0 if q.get("platform") == "underdog" else 1, _LETTER_RANK[x["gr"]],
                   str(q.get("record_id")))
            k = (r.get("event_id"), r.get("player_id"))
            if k not in best or key < best[k][0]:
                best[k] = (key, q)
    for _k, (_key, q) in best.items():
        q["x"]["bp"] = 1
        q["x"]["bv"] = BEST_VERSION
    return len(best)


def marks_of(rows):
    """{record_id: {"gr": letter shown, "bp": 0/1}} for every pick'em quote a rule touched - the file the slip pool and
    tracked picks read (snapshots.write_grade_marks), so they show the board's own letter."""
    out = {}
    for r in rows:
        for q in r.get("quotes") or ():
            x = q.get("x") or {}
            if q.get("record_id") and (x.get("mr") or x.get("bp") or x.get("b") == "side"):
                out[q["record_id"]] = {k: x[k] for k in ("gr", "bp", "mr", "b", "ln") if k in x}
    return out


# ------------------------------------------------------------------------------------- readers (slip pool, tracked picks)
MARKS_CHECK_S = 10.0
_MARKS = {}


def marks(league):
    """{record_id: mark} from the league's last snapshot grade_marks.json (write_grade_marks). Cached per league; the
    file's mtime is re-checked every MARKS_CHECK_S seconds. A missing / unreadable file -> {} (nothing changes)."""
    import json
    import time
    now = time.time()
    c = _MARKS.get(league)
    if c is not None and now - c[0] <= MARKS_CHECK_S:
        return c[2]
    try:
        from .snapshots import snap_root
        p = snap_root() / league / "grade_marks.json"
        mt = p.stat().st_mtime_ns
    except (OSError, ValueError):
        _MARKS[league] = (now, None, {})
        return {}
    if c is not None and c[1] == mt:
        _MARKS[league] = (now, mt, c[2])
        return c[2]
    try:
        d = json.loads(p.read_text(encoding="utf-8")).get("marks") or {}
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError, AttributeError):
        d = {}
    _MARKS[league] = (now, mt, d)
    return d


def letter_for(league, rid, side, letter):
    """The board's own letter for this record id and side: None when the board held the side (os-2), 'Lean' when
    mvlean-1 demoted it, else the letter unchanged."""
    if letter is None or not rid:
        return letter
    m = marks(league).get(rid)
    if not m or m.get("ln") not in (None, side):
        return letter
    if m.get("b") == "side":
        return None
    if m.get("mr") and letter in _LETTER_RANK:
        return "Lean"
    return letter


def featured(league, rid, side):
    """bp-1: True when the board featured this record id on this side."""
    m = marks(league).get(rid) if rid else None
    return bool(m and m.get("bp") and m.get("ln") == side)
