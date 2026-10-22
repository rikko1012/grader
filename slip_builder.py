"""Slip studio: the SERVER-side diversified slip builder (builder_version "studio-1").

Why: the on-page builder (slips-8, web/index.html buildSlips) built each size on its own from the same top picks and
rebuilt on every refresh, so a handful of props carried most of the risk (frontales Over 30.5 sat in 19 of 24 losing
member slips, 2026-10-07 recap). This builder keeps a whole BATCH diversified and remembers it.

build(rows, settings, batch=None, key=None, model=None, now=None)
    rows      graded board rows (server.board_payload(book)["rows"]; both platforms may be passed together)
    settings  {"book": "underdog"|"prizepicks", "side": "More"|"Less"|"either", "sizes": [2, 3, 4],
               "count": 3 | "counts": {4: 3, 3: 3, 2: 3}, "overlap": "as_needed" (default)|"none"|"limited",
               "max_uses": {"player": 2, "match": 2} (limited only), "stat": "kills"|"hs"|"both",
               "scope": "12"|"1"|"123"|"all", "market": "standard"|"goblin"|"demon"|"moneyline",
               "fill": "large" (default: the largest size planned first) | "small" (the smallest first)}
    batch     a batch_id from current_batch() / new_batch() (exposure is stored), or None (nothing stored)
    key       idempotency key (the client's request id): the same key in the same batch returns the stored result
    model     the current grader tag (board_payload()["version"]); default picklog.MODEL_VERSION
    -> {"batch_id", "build_id", "builder_version", "generated_at", "settings", "slips": {2: [...], 3: [...], 4: [...]},
        "plan", "shortage", "exposure", "excluded", "search"}

Eligibility (one rule, the same exclusions as the current builders): graded B or better with units > 0 (B- / Lean
out), graded on the EXACT line now shown by the CURRENT model, not started, role confirmed (HS only for confirmed /
measured non-AWPers, kills only for confirmed non-IGLs; inferred roles are not confirmation), no Underdog alternate
lines, Underdog side must be offered, PrizePicks feed not stale, PrizePicks promo lines left out (their payout
differs), goblin / demon are their own entry type (a side only when the feed offers it: prizepicks.directions), moneylines are their own entry type (never with props).
One platform and one entry type per slip, by construction.

Rules across the WHOLE batch (every size, every Build press until new_batch()). studio-2 default overlap="as_needed"
(owner 2026-10-16: "I press 5 slips and only get 2"): the count asked for is built. Every slip is filled with FRESH picks
first (no repeated prop, player - his kills and headshots are one exposure - or match); only when no fresh slip is left
is a player / match used again, the least-used first (REUSE_PENALTY), and never the same slip twice. overlap="none":
the strict studio-1 rule - nothing repeats, the count may fall short (said why). overlap="limited": a player / match in
up to max_uses slips (caps 1-3). Always: never both sides of one player's stat in a batch; inside a slip one pick per
match, per player and per team.

Selection: slip SIZES are planned before picks - the largest size first (4, then 3, then 2), so 2-pick suggestions
can't consume the legs a 4-pick needs. Each slip is then filled by a branch-and-bound search maximising
sum(log chance) - REUSE_PENALTY x (earlier uses of its players and matches in the batch); the search is exact unless
it visits more than NODE_LIMIT nodes for one slip (reported in "search"). Quality = log of the all-hit chance (the
model's chances, assumed independent) - payouts are NOT assumed (no verified entry payouts). A "shortage" entry says why
when the count can't be built: under "as_needed" only when the eligible picks make no further different slip (fewer
eligible matches than the slip size, or every combination already used).

Records (SQLite DATA_DIR/.cache/slip_studio.db, WAL, BEGIN IMMEDIATE transactions): every recommended slip is an
immutable row (triggers refuse UPDATE / DELETE) with batch, settings, rules, version, time and each pick's identity
and grade; copy / load events are separate rows (record_event). Session ids are stored only as a SHA-256 prefix.

verify_before_copy(slip, boards) re-checks every leg against what the platform shows now (comparison.line_check) and
the current grade; any change -> {"ok": False, "changes": [...], "action": "rebuild"} - the old chance is never
attached to a new line (a stale or failing Underdog feed is a change too: it can't confirm the line). The page then
calls build(..., replace=<slip_id>): the slip is recorded 'void' and ONE slip of its size is built. A replaced, changed
('verify_changed') or dismissed slip keeps its immutable record and stays visible in get_batch (status), but no longer
counts toward the batch's usage or exposure, so its unchanged legs are eligible again. The batch's overlap mode, caps
and rules come from every build's stored settings, never from the last slip alone. copy_text(slip) spells out every
leg's full identity.
"""
import hashlib
import json
import math
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import comparison

VERSION = "studio-4"                           # studio-4: "build around" pinned picks (settings.must_include)
GRADES = ("A+", "A", "A-", "B+", "B")          # B- left out (no evidence it belongs in slips - batch-3)
SIZES = (4, 3, 2)                              # planning order: largest first
MAX_COUNT = 10                                 # slips per size per Build press
DEFAULT_COUNT = 3
DEFAULT_CAPS = {"player": 2, "match": 2}       # overlap="limited" defaults
MAX_CAP = 3
NODE_LIMIT = 20000                             # search nodes per slip (bound reported when reached)
REUSE_PENALTY = 10.0                           # > any log-chance spread: a fresh pick always beats a reused one
OPEN_CAPS = {"player": 10 ** 6, "match": 10 ** 6}  # "as_needed" second pass: any reuse (least-used first), same in-slip rules
OVERLAPS = ("as_needed", "none", "limited")
MONEYLINE = ("moneyline", "ml", "team_ml")
MAX_PINS = 2                                   # settings.must_include: up to 2 board row ids every slip is built around
DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent)
DB_PATH = DATA_DIR / ".cache" / "slip_studio.db"
_LOCK = threading.Lock()                       # the connection pool + first-time schema init (held briefly, never in a txn)
_WRITE_LOCK = threading.Lock()                 # one writer per process (SQLite BEGIN IMMEDIATE covers processes)
_INIT = set()

RULES_NONE = ("no repeated prop, player or match across the whole batch (all sizes, every Build until a new batch)",
              "a player's kills and headshots are one exposure", "never both sides of one player's stat",
              "one pick per match, player and team inside a slip", "one platform and one entry type per slip")
RULES_AS_NEEDED = ("fresh picks first: a player, prop or match is used again only when no fresh slip is left for the "
                   "count you asked for, the least-used first", "a player's kills and headshots are one exposure",
                   "never both sides of one player's stat", "one pick per match, player and team inside a slip",
                   "never the same slip twice", "one platform and one entry type per slip")


# ---------------------------------------------------------------- settings
def normalize_settings(s):
    """Validated settings (ValueError on anything unknown)."""
    s = dict(s or {})
    book = str(s.get("book") or "underdog").lower()
    if book not in ("underdog", "prizepicks"):
        raise ValueError("book must be underdog or prizepicks")
    side = {"more": "More", "over": "More", "higher": "More", "less": "Less", "under": "Less", "lower": "Less",
            "either": "either", "mixed": "either", "any": "either", "both": "either"}.get(str(s.get("side") or "either").lower())
    if not side:
        raise ValueError("side must be More, Less or either")
    stat = str(s.get("stat") or "both").lower()
    if stat not in ("kills", "hs", "both"):
        raise ValueError("stat must be kills, hs or both")
    scope = str(s.get("scope") or "12")
    if scope not in ("12", "1", "123", "all"):
        raise ValueError("scope must be 12, 1, 123 or all")
    market = str(s.get("market") or "standard").lower()
    if market not in ("standard", "goblin", "demon", "moneyline"):
        raise ValueError("market must be standard, goblin, demon or moneyline")
    if market in ("goblin", "demon") and book != "prizepicks":
        raise ValueError("goblin / demon lines are PrizePicks entry types")
    sizes = s.get("sizes") or [2, 3, 4]
    try:
        sizes = sorted({int(n) for n in sizes}, reverse=True)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("sizes must be a subset of 2, 3, 4")
    if not sizes or any(n not in SIZES for n in sizes):
        raise ValueError("sizes must be a subset of 2, 3, 4")
    fill = str(s.get("fill") or "large").lower()
    if fill not in ("large", "small"):
        raise ValueError("fill must be large or small")
    if fill == "small":                                  # smaller slips planned (and filled) first
        sizes = sorted(sizes)
    raw = s.get("counts") or {}
    counts = {}
    for n in sizes:
        v = raw.get(n, raw.get(str(n))) if isinstance(raw, dict) else None
        v = s.get("count", DEFAULT_COUNT) if v is None else v
        try:
            counts[n] = max(1, min(MAX_COUNT, int(v)))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("count must be a whole number")
    overlap = str(s.get("overlap") or "as_needed").lower()
    if overlap not in OVERLAPS:
        raise ValueError("overlap must be as_needed, none or limited")
    if overlap in ("none", "as_needed"):
        caps = {"player": 1, "match": 1}                 # as_needed: the fresh-picks pass (reuse only in its second pass)
    else:
        mu = s.get("max_uses") or {}
        try:
            caps = {k: max(1, min(MAX_CAP, int(mu.get(k, DEFAULT_CAPS[k])))) for k in ("player", "match")}
        except (TypeError, ValueError, AttributeError, OverflowError):
            raise ValueError("max_uses must be whole numbers")
    out = {"book": book, "side": side, "stat": stat, "scope": scope, "market": market, "sizes": sizes,
           "counts": counts, "overlap": overlap, "max_uses": caps, "fill": fill}
    pins = s.get("must_include")
    if pins:                                             # only stored when used (older settings stay byte-identical)
        if not isinstance(pins, (list, tuple)) or len(pins) > MAX_PINS or not all(
                isinstance(x, str) and 0 < len(x) <= 128 for x in pins):
            raise ValueError(f"must_include must be a list of up to {MAX_PINS} board row ids")
        out["must_include"] = list(dict.fromkeys(pins))  # order kept, a repeated id once
    return out


# ---------------------------------------------------------------- row identity
def _norm(s):
    return comparison._norm(s)


def _ts(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def is_moneyline(r):
    return str(r.get("prop") or "").lower() in MONEYLINE or str(r.get("market_type") or "").lower() == "moneyline"


def market_type(r):
    """standard / goblin / demon / promo / alt / moneyline - the entry type a row belongs to."""
    if is_moneyline(r):
        return "moneyline"
    ot = str(r.get("odds_type") or r.get("line_type") or "standard").lower()
    if (r.get("book") or "underdog") == "prizepicks":
        return ot if ot in ("goblin", "demon", "promo") else "standard"
    return "alt" if r.get("alt") else "standard"


def _team(r):
    return r.get("team_name") or r.get("team") or ""


def _opp(r):
    return r.get("opp_name") or r.get("opponent") or ""


def player_keys(r):
    """Every identity of the player (BO3.gg id, nick + team): a shared key = the same player (shared exposure)."""
    if is_moneyline(r):
        return frozenset({"team:" + _norm(_team(r))})          # the team the moneyline backs
    ks = {"nm:" + _norm(r.get("nick") or r.get("player")) + "|" + _norm(_team(r))}
    if r.get("player_id") is not None:
        ks.add(f"id:{r['player_id']}")
    return frozenset(ks)


def match_keys(r):
    """Every identity of the match: the canonical id (matchid.py, on board rows as match_id - the same on both platforms),
    BO3.gg's slug, the sorted team names + date. A shared key = the same match."""
    ks = set()
    if r.get("match_id"):
        ks.add("mid:" + str(r["match_id"]))
    if r.get("match_slug"):
        ks.add("slug:" + str(r["match_slug"]))
    teams = sorted(x for x in (_norm(_team(r)), _norm(_opp(r))) if x)
    if teams:
        ks.add("tm:" + "|".join(teams) + "|" + str(r.get("start") or "")[:10])
    return frozenset(ks)


def _stat(r):
    return "moneyline" if is_moneyline(r) else f"{r.get('prop')}|{r.get('scope') or '12'}"


def _side_stat(r):
    """The market a SIDE belongs to for the both-sides rule: the player's stat on ANY maps scope (kills Over on Maps 1-2
    and kills Under on Map 1 are opposite sides of one player's kills)."""
    return "moneyline" if is_moneyline(r) else str(r.get("prop"))


def _mult(r):
    v = r.get("higher") if r.get("side") == "More" else r.get("lower")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def why_excluded(r, st, model, now):
    """None = eligible; otherwise the reason (one per row, first rule that applies)."""
    if (r.get("book") or "underdog") != st["book"]:
        return "other platform"
    ml = is_moneyline(r)
    if ml and st["market"] != "moneyline":
        return "moneyline - its own entry type, never mixed with props"
    if not ml and st["market"] == "moneyline":
        return "player prop - not in a moneyline entry"
    if not ml:
        if st["stat"] != "both" and r.get("prop") != st["stat"]:
            return "stat not selected"
        if st["scope"] != "all" and (r.get("scope") or "12") != st["scope"]:
            return "maps not selected"
    t = _ts(r.get("start"))
    if t is None:
        return "no start time"
    if t <= now:
        return "started / locked"
    if not r.get("side") or r.get("p") is None or r.get("state") == "SCOUT":
        return "not graded / scout"
    try:
        chance = float(r["p"])
    except (TypeError, ValueError):
        return "invalid probability"
    if r.get("side") not in ("More", "Less") or not math.isfinite(chance) or not 0 <= chance <= 1:
        return "invalid probability or side"
    if r.get("hltv_retry"):
        return "saved grade kept while live data is unavailable (stale - not a current forecast)"
    if r.get("unavailable"):
        return r["unavailable"]                       # the feed does not offer this side (server.side_unavailable)
    if r.get("grade") not in GRADES or (r.get("units") or 0) <= 0:
        return "below B / no units (Lean, B-)"
    try:
        same_line = float(r["graded_line"]) == float(r.get("line"))
    except (KeyError, TypeError, ValueError):
        same_line = False
    if not same_line:
        return "line changed since the grade"
    if model and r.get("graded_model") != model:
        return "grade from an older model"
    mt = market_type(r)
    if mt == "alt":
        return "Underdog alternate line"
    if mt == "promo":
        return "PrizePicks promo line (payout differs)"
    if mt != st["market"]:
        return f"other entry type ({mt})"
    if st["book"] == "prizepicks" and not ml:
        # the sides PrizePicks' FEED offers for this line (2026-10-14 owner: never a blanket goblin / demon 'More only'):
        # under_available / over_available from prizepicks.directions(); a row without them keeps the conservative
        # reading - Less not confirmed on goblin / demon lines, both sides on standard ones
        ok = r.get("under_available") if r.get("side") == "Less" else r.get("over_available")
        if ok is None:
            ok = not (r.get("side") == "Less" and mt in ("goblin", "demon"))
        if not ok:
            return (f"{'Less' if r.get('side') == 'Less' else 'More'} not offered or not confirmed by the PrizePicks feed "
                    f"for this line" + (f" ({r['direction_basis']})" if r.get("direction_basis") else ""))
    if st["book"] == "underdog" and not ml and _mult(r) is None:
        return "side not offered by Underdog"
    if st["book"] == "prizepicks" and r.get("stale"):
        return "PrizePicks feed stale"
    if not ml:
        role = r.get("awp") if r.get("prop") == "hs" else r.get("igl")
        if role is True:
            return "AWPer HS / IGL kills"
        if role is not False:
            return "role not confirmed"
        if r.get("prop") == "hs" and r.get("awp_conf") not in ("confirmed", "measured"):
            return "AWP role only inferred"
    if st["side"] != "either" and r.get("side") != st["side"]:
        return "side not selected"
    return None


def pick_record(r):
    """The immutable identity + grade of one leg."""
    mt = market_type(r)
    return {"row_id": r.get("id"), "player": r.get("player"), "nick": r.get("nick") or r.get("player"),
            "player_id": r.get("player_id"), "ud_player_id": r.get("ud_player_id"),
            "team": _team(r), "opponent": _opp(r), "book_team": r.get("team"), "book_opponent": r.get("opponent"),
            "match_id": r.get("match_id"), "ud_match_id": r.get("ud_match_id"),
            # the pick log's key for the match (an HLTV-only match: hltv-<id>, server.settle_slug) - without it the leg
            # never settles (2026-10-21 bug: History 'waiting' for ever, and the whole shadow batch with it)
            "match_slug": r.get("match_slug") or r.get("settle_slug"),
            "start": r.get("start"), "event": r.get("event"),
            "prop": "moneyline" if mt == "moneyline" else r.get("prop"), "scope": r.get("scope") or "12",
            "line": r.get("line"), "side": r.get("side"), "book": r.get("book") or "underdog", "market_type": mt,
            "grade": r.get("grade"), "p": r.get("p"), "model_outcomes": r.get("model_outcomes"),
            "units": r.get("units"), "graded_model": r.get("graded_model"),
            "graded_at": r.get("graded_at"), "graded_line": r.get("graded_line"),
            "mult": _mult(r) if (r.get("book") or "underdog") == "underdog" else None}


class _Leg:
    __slots__ = ("row", "rec", "pk", "mk", "tk", "stat", "side_stat", "side", "p", "logp", "score", "uid")

    def __init__(self, r):
        self.row, self.rec = r, pick_record(r)
        self.pk, self.mk = player_keys(r), match_keys(r)
        self.tk = frozenset(x for x in (_norm(_team(r)),) if x)
        self.stat, self.side_stat, self.side = _stat(r), _side_stat(r), r.get("side")
        self.p = float(r["p"])
        self.logp = math.log(max(self.p, 1e-6))
        self.uid = f"{r.get('book') or 'underdog'}:{r.get('id')}:{r.get('line')}:{r.get('side')}"
        self.score = self.logp


def _leg_from_rec(rec):
    """A used leg rebuilt from its stored record (exposure of earlier builds)."""
    r = dict(rec, id=rec.get("row_id"), team_name=rec.get("team"), opp_name=rec.get("opponent"))
    return _Leg(r)


# ---------------------------------------------------------------- exposure state
class _Usage:
    """Counts of players / matches / props / market sides used in the batch so far."""

    def __init__(self):
        self.player, self.match, self.prop, self.sides = {}, {}, {}, {}
        self.slip_sets = set()

    @staticmethod
    def _get(d, keys):
        return max((d.get(k, 0) for k in keys), default=0)

    def players(self, leg):
        return self._get(self.player, leg.pk)

    def matches(self, leg):
        return self._get(self.match, leg.mk)

    def props(self, leg):
        return self._get(self.prop, {f"{k}|{leg.stat}" for k in leg.pk})

    def opposing(self, leg):
        return any(self.sides.get(f"{k}|{leg.side_stat}", set()) - {leg.side} for k in leg.pk)

    def add(self, legs):
        for leg in legs:
            for k in leg.pk:
                self.player[k] = self.player.get(k, 0) + 1
                pk = f"{k}|{leg.stat}"
                self.prop[pk] = self.prop.get(pk, 0) + 1
                self.sides.setdefault(f"{k}|{leg.side_stat}", set()).add(leg.side)
            for k in leg.mk:
                self.match[k] = self.match.get(k, 0) + 1
        self.slip_sets.add(frozenset(l.uid for l in legs))

    def blocked(self, leg, caps):
        """Why this leg can't go into another slip of the batch (None = it can)."""
        if self.opposing(leg):
            return "other side of this player's stat already in this batch"
        if self.players(leg) >= caps["player"] or self.matches(leg) >= caps["match"] or self.props(leg) >= caps["player"]:
            return "already in this batch (no-repeat rule)" if caps["player"] == 1 and caps["match"] == 1 \
                else "at the batch's overlap cap"
        return None


def _conflict(a, chosen):
    """Inside one slip: one pick per match, per player and per team."""
    return any(a.mk & c.mk or a.pk & c.pk or a.tk & c.tk for c in chosen)


def _fill(n, pool, usage, caps, seed=()):
    """Best n-leg slip from pool under the batch caps: branch and bound on sum(score). Returns (legs | None, nodes, capped).
    seed = the pinned legs ("build around", settings.must_include): every slip starts from them (they are not subject to
    the batch's overlap caps - the member asked for them in every slip); the other legs never share their match, player or
    team, and the whole slip (seed included) is never a slip the batch already has."""
    seed = list(seed)
    sid = {l.uid for l in seed}
    usable = []
    for leg in pool:
        if leg.uid in sid or (seed and _conflict(leg, seed)) or usage.blocked(leg, caps):
            continue
        leg.score = leg.logp - REUSE_PENALTY * (usage.players(leg) + usage.matches(leg))
        usable.append(leg)
    usable.sort(key=lambda l: (-l.score, l.uid))
    sc = [l.score for l in usable]
    pre = [0.0]
    for x in sc:
        pre.append(pre[-1] + x)
    best = {"score": None, "legs": None}
    nodes = [0]

    def dfs(start, chosen, score):
        nodes[0] += 1
        if len(chosen) == n:
            if frozenset(l.uid for l in chosen) in usage.slip_sets:
                return                                   # the same slip twice in one batch: never
            if best["score"] is None or score > best["score"] + 1e-12:
                best["score"], best["legs"] = score, list(chosen)
            return
        need = n - len(chosen)
        for j in range(start, len(usable) - need + 1):
            if best["score"] is not None and score + pre[j + need] - pre[j] <= best["score"] + 1e-12:
                break                                    # sorted: nothing further on can beat the best found
            c = usable[j]
            if _conflict(c, chosen):
                continue
            chosen.append(c)
            dfs(j + 1, chosen, score + c.score)
            chosen.pop()
            if nodes[0] > NODE_LIMIT:
                return
    if len(seed) > n:
        return None, 0, False
    dfs(0, list(seed), 0.0)
    return best["legs"], nodes[0], nodes[0] > NODE_LIMIT


def _capacity(pool, usage, caps):
    """How many more legs the batch can take without any repeat (none mode): one per match and per player, greedy
    by chance - the leg budget the size plan is allocated from."""
    used_p, used_m, k = set(), set(), 0
    for leg in sorted(pool, key=lambda l: (-l.logp, l.uid)):
        if usage.blocked(leg, caps) or leg.pk & used_p or leg.mk & used_m:
            continue
        used_p |= leg.pk
        used_m |= leg.mk
        k += 1
    return k


def _plan(st, capacity, pinned=0):
    """Slips per size from the leg budget (none mode), in st["sizes"] order: largest first (fill "large", default) or
    smallest first (fill "small"); limited / as_needed = as requested. pinned = legs every slip already holds (they take
    nothing from the budget; a size that can't hold them gets none)."""
    if st["overlap"] != "none":
        return {n: (c if n >= pinned else 0) for n, c in st["counts"].items()}
    plan, left = {}, capacity
    for n in st["sizes"]:                                # 4, then 3, then 2 (fill "small": 2, then 3, then 4)
        per = n - pinned
        plan[n] = 0 if per < 0 else st["counts"][n] if per == 0 else min(st["counts"][n], left // per)
        left -= plan[n] * max(per, 0)
    return plan


def _select(pool, st, usage, had_prior=False, pins=()):
    caps = st["max_uses"]
    soft = st["overlap"] == "as_needed"
    pins = list(pins)
    k = len(pins)
    if pins:                                             # the other legs: never a pinned leg, nor its match / player / team
        pu = {l.uid for l in pins}
        pool = [l for l in pool if l.uid not in pu and not _conflict(l, pins)]
    cap0 = _capacity(pool, usage, caps)
    plan = _plan(st, cap0, k)
    out, shortage, search = {n: [] for n in SIZES}, {}, {"node_limit": NODE_LIMIT, "nodes": 0, "bound_hit": False, "reused_slips": 0}
    for n in st["sizes"]:
        want = st["counts"][n]
        if n < k:
            shortage[n] = (f"0 of {want} {n}-pick slips: a {n}-pick slip can't hold the {k} picks this build is built "
                           f"around. Ask for {k}-pick slips or larger, or build around one pick.")
            continue
        for _ in range(plan[n]):
            legs, nodes, capped = _fill(n, pool, usage, caps, pins)
            if not legs and soft:                        # no fresh slip left: reuse, least-used first
                legs, nodes2, capped2 = _fill(n, pool, usage, OPEN_CAPS, pins)
                nodes, capped = nodes + nodes2, capped or capped2
                search["reused_slips"] += bool(legs)
            search["nodes"] += nodes
            search["bound_hit"] |= capped
            if not legs:
                break
            usage.add(legs)
            out[n].append(legs)
        if len(out[n]) < want:
            left = [l for l in pool if not usage.blocked(l, OPEN_CAPS if soft else caps)]
            matches = len({min(l.mk) for l in left if l.mk})
            pin_note = ""
            if k:
                pin_note = (f" Every slip of this build holds the {k} pick{'s' if k != 1 else ''} it is built around"
                            + (" - a slip of just those picks is offered once (the same slip is never offered twice)."
                               if n == k else f", so the other {n - k} come from other matches." if n - k > 1
                               else ", so the other pick comes from another match."))
            if soft:
                shortage[n] = (f"{len(out[n])} of {want} {n}-pick slips: today's eligible picks make no other different "
                               f"{n}-pick slip ({matches} eligible match{'es' if matches != 1 else ''}; a slip needs {n} different "
                               f"matches and players). More slips appear as more lines are graded." + pin_note)
                continue
            if st["overlap"] == "none":
                why = (f"{len(out[n])} of {want} {n}-pick slips: {matches} unused eligible match{'es' if matches != 1 else ''} "
                       f"left in this batch{(' after the smaller slips' if st.get('fill') == 'small' else ' after the larger slips') if n != st['sizes'][0] else ''} ({n} distinct matches and players per slip)")
            else:
                why = (f"{len(out[n])} of {want} {n}-pick slips: {matches} eligible match{'es' if matches != 1 else ''} left "
                       f"under the overlap caps (a player in at most {caps['player']}, a match in at most {caps['match']} "
                       f"slips of this batch; one pick per match and player inside a slip)")
            if search["bound_hit"]:
                why += f"; search stopped at its {NODE_LIMIT}-node bound"
            total_matches = len({min(l.mk) for l in pool if l.mk})
            if total_matches < n - k:
                shortage[n] = (why + f". Rules are not relaxed. The filtered pool has {total_matches} distinct eligible matches"
                               f"{' besides the pinned picks' if k else ''}; a {n}-pick slip needs {n - k}{' more' if k else ''}. "
                               "Allowing repeats across slips or starting a new "
                               "batch cannot supply the missing matches. Broaden the filters or wait for more lines." + pin_note)
                continue
            tips = ["ask for fewer slips"] + (["allow limited overlap"] if st["overlap"] == "none" else [])
            tips += ["start a new batch"] if had_prior else []
            shortage[n] = (why + ". Rules are not relaxed to fill the count - "
                           + (", ".join(tips[:-1]) + " or " if len(tips) > 1 else "") + tips[-1] + "." + pin_note)
    return out, plan, shortage, search, cap0


# ---------------------------------------------------------------- storage
_POOL = {}                                     # path -> idle connections (requests run on short-lived threads)
POOL_MAX = 8


def _connect(path):
    con = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA synchronous=NORMAL")      # WAL: each commit stays atomic; no fsync per commit (checkpoints sync)
    if str(path) not in _INIT:
        with _LOCK:
            if str(path) not in _INIT:
                con.execute("PRAGMA journal_mode=WAL")
                con.executescript(_SCHEMA)
                _INIT.add(str(path))
    return con


@contextmanager
def _db(write=False):
    """A pooled connection; write=True runs inside one BEGIN IMMEDIATE transaction (all or nothing, one writer)."""
    path = Path(DB_PATH)
    key = str(path)
    with _LOCK:
        idle = _POOL.setdefault(key, [])
        con = idle.pop() if idle else None
    if con is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = _connect(path)
    ok = False
    try:
        if write:
            # writers queue on _WRITE_LOCK only (SQLite's BEGIN IMMEDIATE + busy_timeout serialise across processes);
            # _LOCK (the pool) is never held through a transaction, so reads - owns / get_batch / get_slip / the
            # idempotent pre-check - run beside a build instead of waiting for its compute
            with _WRITE_LOCK:
                con.execute("BEGIN IMMEDIATE")
                try:
                    yield con
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
        else:
            yield con
        ok = True
    finally:
        with _LOCK:
            idle = _POOL.setdefault(key, [])
            if ok and len(idle) < POOL_MAX and not con.in_transaction:
                idle.append(con)
                con = None
        if con is not None:
            con.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches(batch_id TEXT PRIMARY KEY, owner TEXT NOT NULL, seq INTEGER NOT NULL,
  idem_key TEXT, created_at TEXT NOT NULL, closed_at TEXT, UNIQUE(owner, seq), UNIQUE(owner, idem_key));
CREATE TABLE IF NOT EXISTS builds(batch_id TEXT NOT NULL, idem_key TEXT NOT NULL, build_id TEXT NOT NULL UNIQUE,
  seq INTEGER NOT NULL, created_at TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(batch_id, idem_key));
CREATE TABLE IF NOT EXISTS slips(slip_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, build_id TEXT NOT NULL,
  n INTEGER NOT NULL, created_at TEXT NOT NULL, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS slips_batch ON slips(batch_id);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, slip_id TEXT NOT NULL,
  kind TEXT NOT NULL, at TEXT NOT NULL, idem_key TEXT, detail TEXT, UNIQUE(batch_id, idem_key));
CREATE TRIGGER IF NOT EXISTS slips_immutable_u BEFORE UPDATE ON slips BEGIN SELECT RAISE(ABORT, 'recommendation records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS slips_immutable_d BEFORE DELETE ON slips BEGIN SELECT RAISE(ABORT, 'recommendation records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS builds_immutable_u BEFORE UPDATE ON builds BEGIN SELECT RAISE(ABORT, 'build records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS builds_immutable_d BEFORE DELETE ON builds BEGIN SELECT RAISE(ABORT, 'build records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS events_immutable_u BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
"""


def _owner(session_id):
    if not session_id:
        raise ValueError("no session")
    return hashlib.sha256(str(session_id).encode()).hexdigest()[:24]


def _now_iso(now=None):
    return (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def owns(session_id, batch_id):
    """True when batch_id belongs to this member - the endpoint takes batch ids from the session, never from the client."""
    with _db() as con:
        r = con.execute("SELECT owner FROM batches WHERE batch_id=?", (str(batch_id),)).fetchone()
    return bool(r) and r[0] == _owner(session_id)


def current_batch(session_id):
    """The member's open batch (created on first use)."""
    o = _owner(session_id)
    with _db() as con:
        row = con.execute("SELECT batch_id FROM batches WHERE owner=? AND closed_at IS NULL ORDER BY seq DESC LIMIT 1", (o,)).fetchone()
    if row:
        return row[0]
    with _db(write=True) as con:
        row = con.execute("SELECT batch_id FROM batches WHERE owner=? AND closed_at IS NULL ORDER BY seq DESC LIMIT 1", (o,)).fetchone()
        if row:
            return row[0]
        return _create(con, o, None)


def _create(con, owner, key):
    seq = (con.execute("SELECT MAX(seq) FROM batches WHERE owner=?", (owner,)).fetchone()[0] or 0) + 1
    bid = f"{owner[:12]}-{seq}"
    con.execute("INSERT INTO batches(batch_id, owner, seq, idem_key, created_at) VALUES (?,?,?,?,?)", (bid, owner, seq, key, _now_iso()))
    return bid


def new_batch(session_id, key=None):
    """Explicit new batch: closes the open one (its exposure stops counting). Same key again = the same new batch."""
    o = _owner(session_id)
    with _db(write=True) as con:
        if key:
            row = con.execute("SELECT batch_id FROM batches WHERE owner=? AND idem_key=?", (o, str(key)[:64])).fetchone()
            if row:
                return row[0]
        con.execute("UPDATE batches SET closed_at=? WHERE owner=? AND closed_at IS NULL", (_now_iso(), o))
        return _create(con, o, str(key)[:64] if key else None)


def _batch_slips(con, batch_id):
    return [json.loads(r[0]) for r in con.execute("SELECT record FROM slips WHERE batch_id=? ORDER BY rowid", (batch_id,))]


# A slip stops counting toward the batch's usage / exposure once it is replaced ('void', recorded by build(replace=..)),
# found changed before copying ('verify_changed') or dismissed by the member ('dismiss'). Its immutable record stays and
# the batch view still shows it (marked); a later verify_ok never brings it back.
DEAD_KINDS = ("void", "verify_changed", "dismiss")


def _dead(con, batch_id):
    """{slip_id: {"kind", "replaced_by"}} of the batch's slips that no longer count (void wins over the other kinds)."""
    out = {}
    q = f"SELECT slip_id, kind, detail FROM events WHERE batch_id=? AND kind IN ({','.join('?' * len(DEAD_KINDS))}) ORDER BY id"
    for sid, kind, detail in con.execute(q, (batch_id, *DEAD_KINDS)):
        if out.get(sid, {}).get("kind") == "void":
            continue
        d = json.loads(detail) if detail else {}
        out[sid] = {"kind": kind, "replaced_by": (d or {}).get("replaced_by") if kind == "void" else None}
    return out


def _usage_from(slips):
    u = _Usage()
    for s in slips:
        u.add([_leg_from_rec(l) for l in s["legs"]])
    return u


def _rules(overlap, caps):
    if overlap == "none":
        return list(RULES_NONE)
    if overlap == "as_needed":
        return list(RULES_AS_NEEDED)
    return [f"a player in at most {caps['player']} slips and a match in at most {caps['match']} slips of the batch",
            "fresh picks before any reuse", "never both sides of one player's stat (any maps)",
            "one pick per match, player and team inside a slip", "one platform and one entry type per slip"]


def _batch_mode(slips, st=None):
    """The batch's overlap mode from EVERY slip's stored settings (not the last one): limited if any build allowed it,
    caps = the largest cap any limited build used; mixed = more than one (mode, caps) in the batch."""
    per, seen = [], set()
    for s in slips:
        ss = s.get("settings") or {}
        ov = ss.get("overlap") or "none"
        caps = dict(ss.get("max_uses") or {"player": 1, "match": 1})
        k = (s.get("build_id"), ov, caps.get("player"), caps.get("match"))
        if k not in seen:
            seen.add(k)
            per.append({"build_id": s.get("build_id"), "overlap": ov, "caps": caps})
    if not per:
        st = st or normalize_settings({})
        return {"overlap": st["overlap"], "caps": dict(st["max_uses"]), "mixed": False, "builds": []}
    lim = [p for p in per if p["overlap"] == "limited"]
    asn = any(p["overlap"] == "as_needed" for p in per)
    caps = ({k: max(int(p["caps"].get(k) or 1) for p in lim) for k in ("player", "match")} if lim
            else {"player": 1, "match": 1})
    modes = {(p["overlap"], p["caps"].get("player"), p["caps"].get("match")) for p in per}
    return {"overlap": "as_needed" if asn else "limited" if lim else "none", "caps": caps, "mixed": len(modes) > 1, "builds": per}


def _exposure(slips, st=None):
    """Per player / match / prop: how many slips of the batch use it (repeats listed - visible caps). The mode, caps and
    rules describe the WHOLE batch (_batch_mode); st only matters for an empty batch."""
    pl, ma, pr = {}, {}, {}
    for s in slips:
        for l in s["legs"]:
            pkey = (l.get("player_id") if l.get("player_id") is not None else _norm(l.get("nick")) + "|" + _norm(l.get("team")))
            # the canonical match id first (two platform spellings of one match are one match - as in the build itself)
            mkey = l.get("match_id") or l.get("match_slug") or "|".join(sorted([_norm(l.get("team")), _norm(l.get("opponent"))])) + "|" + str(l.get("start") or "")[:10]
            for d, k, lab in ((pl, pkey, l.get("nick")), (ma, mkey, f"{l.get('team')} vs {l.get('opponent')}"),
                              (pr, (pkey, l.get("prop"), l.get("scope")), f"{l.get('nick')} {l.get('prop')} {l.get('scope')}")):
                e = d.setdefault(k, {"label": lab, "slips": []})
                e["slips"].append(s["slip_id"])
    rep = lambda d: [{"label": e["label"], "uses": len(e["slips"]), "slips": e["slips"]} for e in d.values() if len(e["slips"]) > 1]
    top = lambda d: max((len(e["slips"]) for e in d.values()), default=0)
    mode = _batch_mode(slips, st)
    rules = _rules(mode["overlap"], mode["caps"])
    if mode["mixed"]:
        rules = ["this batch mixes overlap settings across builds - the rules below are the most permissive one used: "
                 + "; ".join(f"{p['build_id']}: {'no repeats' if p['overlap'] == 'none' else 'repeats only when needed' if p['overlap'] == 'as_needed' else 'limited (player ' + str(p['caps'].get('player')) + ', match ' + str(p['caps'].get('match')) + ')'}"
                             for p in mode["builds"])] + rules
    mu = {"prop": top(pr), "player": top(pl), "match": top(ma)}
    return {"overlap": mode["overlap"], "caps": mode["caps"], "mixed": mode["mixed"], "builds": mode["builds"],
            "slips": len(slips), "legs": sum(len(s["legs"]) for s in slips),
            "distinct_props": len(pr), "distinct_players": len(pl), "distinct_matches": len(ma),
            "max_uses": mu, "no_repeats": max(mu.values()) <= 1,
            "repeated_players": rep(pl), "repeated_matches": rep(ma), "repeated_props": rep(pr), "rules": rules}


def _grouped(slips):
    g = {n: [] for n in SIZES}
    for s in slips:
        g.setdefault(s["n"], []).append(s)
    return {n: g[n] for n in sorted(g)}


def _intkeys(res):
    for k in ("slips", "plan", "shortage"):
        if isinstance(res.get(k), dict):
            res[k] = {(int(a) if str(a).isdigit() else a): b for a, b in res[k].items()}
    return res


def _default_key(rows, st):
    fp = sorted((str(r.get("book")), str(r.get("id")), str(r.get("line")), str(r.get("side")), str(r.get("p")),
                 str(r.get("graded_at"))) for r in rows)
    return "auto:" + hashlib.sha1(json.dumps([st, fp], sort_keys=True, default=str).encode()).hexdigest()[:24]


def _state(con, batch):
    """What a build depends on: (builds so far, slips so far, dead slip ids) - re-read inside the write transaction."""
    nb = con.execute("SELECT COUNT(*) FROM builds WHERE batch_id=?", (batch,)).fetchone()[0] or 0
    ns = con.execute("SELECT COUNT(*) FROM slips WHERE batch_id=?", (batch,)).fetchone()[0] or 0
    dead = _dead(con, batch)
    return (nb, ns, tuple(sorted((k, v["kind"]) for k, v in dead.items()))), dead


def _replace_settings(old, st):
    """One slip of the replaced slip's size, with its book / entry type / overlap settings."""
    n = int(old.get("n") or len(old.get("legs") or []) or 2)
    base = dict(old.get("settings") or st)
    base.update(sizes=[n], counts={n: 1}, count=1)
    return normalize_settings(base)


def build(rows, settings, batch=None, key=None, model=None, now=None, replace=None):
    """Build slips for one Build press (see the module docstring). ValueError on bad settings / unknown or closed batch.
    replace=<slip_id of this batch>: the slip is recorded void (it stops counting toward the batch's usage / exposure,
    so its still-valid legs are eligible again) and ONE slip of its size is built with its settings - never a full new
    set. The same key again returns the stored result (nothing written twice)."""
    st = normalize_settings(settings)
    now = now or datetime.now(timezone.utc)
    if model is None:
        try:
            import picklog
            model = picklog.MODEL_VERSION
        except Exception:
            model = None
    rows = list(rows or [])
    if batch is None:
        return _intkeys(json.loads(json.dumps(_compute(rows, st, model, now, None, None, []), default=str)))
    key = str(key)[:96] if key else _default_key(rows, st)

    def plan(con):
        state, dead = _state(con, batch)
        prior = _batch_slips(con, batch)
        stx = st
        if replace:
            old = next((s for s in prior if s["slip_id"] == replace), None)
            if old is None:
                raise ValueError("unknown slip for this batch")
            if dead.get(replace, {}).get("kind") == "void":
                raise ValueError("this slip was already replaced - verify its replacement or build again")
            stx = _replace_settings(old, st)
        live = [s for s in prior if s["slip_id"] not in dead and s["slip_id"] != replace]
        build_id = f"{batch}/b{state[0] + 1}"
        res = _compute(rows, stx, model, now, batch, build_id, live)
        if replace:
            res["replaced"] = replace
        return state, res

    with _db() as con:                                   # a retry is answered without the write lock
        done = con.execute("SELECT result FROM builds WHERE batch_id=? AND idem_key=?", (batch, key)).fetchone()
        if done:
            return _intkeys(json.loads(done[0]))
        row = con.execute("SELECT closed_at FROM batches WHERE batch_id=?", (batch,)).fetchone()
        if row is None:
            raise ValueError("unknown batch")
        if row[0]:
            raise ValueError("batch closed - start a new batch")
        # the search runs on a snapshot OUTSIDE the write transaction (the write lock is held for the inserts only)
        snap_state, res = plan(con)
    with _db(write=True) as con:
        row = con.execute("SELECT closed_at FROM batches WHERE batch_id=?", (batch,)).fetchone()
        if row is None:
            raise ValueError("unknown batch")
        done = con.execute("SELECT result FROM builds WHERE batch_id=? AND idem_key=?", (batch, key)).fetchone()
        if done:
            return _intkeys(json.loads(done[0]))         # a retry: the stored result, nothing written
        if row[0]:
            raise ValueError("batch closed - start a new batch")
        if _state(con, batch)[0] != snap_state:          # another build / event landed meanwhile: recompute on it
            snap_state, res = plan(con)
        build_id = res["build_id"]
        for n in SIZES:
            for s in res["slips"][n]:
                con.execute("INSERT INTO slips(slip_id, batch_id, build_id, n, created_at, record) VALUES (?,?,?,?,?,?)",
                            (s["slip_id"], batch, build_id, n, res["generated_at"], json.dumps(s, default=str)))
        if replace:
            con.execute("INSERT INTO events(batch_id, slip_id, kind, at, idem_key, detail) VALUES (?,?,?,?,?,?)",
                        (batch, replace, "void", _now_iso(), ("void:" + key)[:96],
                         json.dumps({"replaced_by": build_id, "reason": "a line changed before copying"})))
        body = json.dumps(res, default=str)
        con.execute("INSERT INTO builds(batch_id, idem_key, build_id, seq, created_at, result) VALUES (?,?,?,?,?,?)",
                    (batch, key, build_id, int(build_id.rsplit("/b", 1)[1]), res["generated_at"], body))
    return _intkeys(json.loads(body))


def _compute(rows, st, model, now, batch_id, build_id, prior):
    """prior = the batch's slips that still COUNT (replaced / changed / dismissed slips left out by the caller)."""
    gen = _now_iso(now)
    excluded, pool, seen = {}, [], set()

    def out(reason, r):
        e = excluded.setdefault(reason, {"reason": reason, "count": 0, "examples": []})
        e["count"] += 1
        if len(e["examples"]) < 3:
            e["examples"].append(f"{r.get('nick') or r.get('player')} {r.get('prop')} {r.get('line')} {r.get('side') or ''}".strip())
    for r in rows:
        why = why_excluded(r, st, model, now)
        if why:
            out(why, r); continue
        leg = _Leg(r)
        if leg.uid in seen:
            continue                                      # the same row passed twice counts once
        seen.add(leg.uid)
        pool.append(leg)
    usage = _usage_from(prior)
    pins, pinned_left_out = _pins(rows, st, model, now, usage)
    pin_uids = {l.uid for l in pins}
    fresh = []
    for leg in pool:
        if leg.uid in pin_uids:
            continue                                      # seeds every slip (never counted as left out / in the pool)
        b = usage.blocked(leg, OPEN_CAPS if st["overlap"] == "as_needed" else st["max_uses"])
        if b:
            out(b, leg.row)
        else:
            fresh.append(leg)
    eligible_n, eligible_matches = len(pool), len({min(l.mk) for l in pool if l.mk})
    chosen, plan, shortage, search, capacity = _select(fresh, st, usage, bool(prior), pins)
    if not pool:
        shortage["all"] = (f"No eligible picks on {'PrizePicks' if st['book'] == 'prizepicks' else 'Underdog'} for these settings"
                           + (" - most left out: " + "; ".join(f"{e['reason']} ({e['count']})" for e in
                                                                sorted(excluded.values(), key=lambda e: -e["count"])[:3]) if excluded else "") + ".")
    rules = _rules(st["overlap"], st["max_uses"])
    full_cap = _capacity(pool, _Usage(), {"player": 1, "match": 1})
    slips, k = {n: [] for n in SIZES}, 0
    for n in SIZES:
        for legs in chosen[n]:
            k += 1
            from slip_probability import summary as probability_summary
            probability = probability_summary([l.rec for l in legs])
            slips[n].append({"slip_id": f"{build_id or 'preview'}/s{k}", "batch_id": batch_id, "build_id": build_id,
                             "n": n, "book": st["book"], "entry_type": st["market"], "generated_at": gen,
                             "builder_version": VERSION, "settings": st, "rules": rules,
                             **probability,
                             "selection_log_score": round(sum(l.logp for l in legs), 6),
                             **({"built_around": [l.rec["row_id"] for l in pins]} if pins else {}),
                             "legs": [dict(l.rec) for l in legs]})
    new = [s for n in SIZES for s in slips[n]]
    allslips = prior + new
    exp = _exposure(allslips, st)
    reps = {}
    for e in exp["repeated_players"] + exp["repeated_matches"]:
        for sid in e["slips"]:
            reps.setdefault(sid, []).append(f"{e['label']} in {e['uses']} slips")
    for s in new:
        s["repeats"] = reps.get(s["slip_id"], [])        # visible reuse (as_needed / limited)
    return {"batch_id": batch_id, "build_id": build_id, "builder_version": VERSION, "generated_at": gen, "model": model,
            "settings": st, "slips": slips, "plan": plan, "shortage": shortage, "exposure": exp,
            "excluded": sorted(excluded.values(), key=lambda e: -e["count"]),
            "eligible": {"lines": eligible_n, "matches": eligible_matches, "unused_capacity": capacity,
                         # the most slips of each size the eligible picks support with no repeats (all of them / what this
                         # batch has left) - one pick per match and player, so 9 matches = 2 four-picks, 3 three-picks or 4 two-picks
                         "max_slips": max_slips(full_cap), "max_slips_left": max_slips(capacity)},
            "search": search,
            # "build around" (settings.must_include): the pinned picks every slip of this build holds, and the ids left
            # out with the builder's own reason (why_excluded / the batch's both-sides rule / clash with the other pin)
            "pinned": [{"id": l.rec["row_id"], "label": _leg_label(l.rec)} for l in pins],
            "pinned_left_out": pinned_left_out}


def _leg_label(rec):
    return f"{rec.get('nick') or rec.get('player')} {rec.get('prop')} {_side_word(rec.get('side'))} {rec.get('line')}"


def _pins(rows, st, model, now, usage):
    """settings.must_include -> (pinned _Legs in the order asked, [{id, why}] left out). Each id must be a board row the
    builder itself would use (why_excluded = None: this platform, entry type, filters, grade, line, role ...); the batch's
    both-sides rule still applies, and two pins never share a match, player or team (one slip holds both). The batch's
    no-repeat / overlap caps do not apply to a pin: the member asked for it in every slip of this build."""
    want = st.get("must_include") or []
    if not want:
        return [], []
    by_id = {}
    for r in rows:
        by_id.setdefault(str(r.get("id")), []).append(r)
    pins, left = [], []
    for pid in want:
        cands = by_id.get(pid) or []
        if not cands:
            left.append({"id": pid, "why": "not on the current board (the line may have been pulled or moved)"})
            continue
        leg, whys = None, []
        for r in sorted(cands, key=lambda r: (r.get("book") or "underdog") != st["book"]):   # this platform's row first
            why = why_excluded(r, st, model, now)
            if why is None:
                leg = _Leg(r)
                break
            whys.append(why)
        if leg is None:
            left.append({"id": pid, "why": whys[0]})
        elif usage.opposing(leg):
            left.append({"id": pid, "why": "other side of this player's stat already in this batch"})
        elif _conflict(leg, pins):
            left.append({"id": pid, "why": "same match, player or team as the other pick you built around (one pick per "
                                           "match, player and team inside a slip)"})
        else:
            pins.append(leg)
    return pins, left


def max_slips(legs):
    """{size: most slips of that size} from a no-repeat leg budget (each size on its own, not together)."""
    return {n: legs // n for n in sorted(SIZES)}


def capacity(rows, settings, model=None, now=None):
    """What the eligible picks can support for these settings, nothing stored and no batch: eligible lines and matches,
    the no-repeat leg budget (one pick per match and per player) and the most slips of each size it allows."""
    st = normalize_settings(settings)
    now = now or datetime.now(timezone.utc)
    pool, seen = [], set()
    for r in rows or []:
        if why_excluded(r, st, model, now):
            continue
        leg = _Leg(r)
        if leg.uid not in seen:
            seen.add(leg.uid)
            pool.append(leg)
    legs = _capacity(pool, _Usage(), {"player": 1, "match": 1})
    return {"builder_version": VERSION, "settings": {k: st[k] for k in ("book", "side", "stat", "scope", "market")},
            "lines": len(pool), "matches": len({min(l.mk) for l in pool if l.mk}), "legs": legs, "max_slips": max_slips(legs),
            "max_different_slips": {n: different_slips(pool, n) for n in sorted(SIZES)}}


def different_slips(pool, n, limit=MAX_COUNT, node_limit=NODE_LIMIT):
    """How many different n-pick slips the legs make (one pick per match, player and team inside a slip), counted up to
    `limit` - the most an "as_needed" build of that size can return (before the both-sides rule across slips)."""
    legs = sorted(pool, key=lambda l: (-l.logp, l.uid))
    found, nodes = [0], [0]

    def dfs(start, chosen):
        if found[0] >= limit or nodes[0] > node_limit:
            return
        nodes[0] += 1
        if len(chosen) == n:
            found[0] += 1
            return
        for j in range(start, len(legs) - (n - len(chosen)) + 1):
            if not _conflict(legs[j], chosen):
                chosen.append(legs[j])
                dfs(j + 1, chosen)
                chosen.pop()
            if found[0] >= limit:
                return
    dfs(0, [])
    return found[0]


_DEAD_LABEL = {"void": "replaced", "verify_changed": "changed", "dismiss": "dismissed"}


def get_batch(batch_id):
    """Everything recommended in a batch so far (all Build presses). Each slip carries status live / replaced (with
    replaced_by) / changed / dismissed; the exposure counts the live slips only."""
    with _db() as con:
        b = con.execute("SELECT created_at, closed_at FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if not b:
            return None
        slips = _batch_slips(con, batch_id)
        builds = con.execute("SELECT COUNT(*) FROM builds WHERE batch_id=?", (batch_id,)).fetchone()[0]
        dead = _dead(con, batch_id)
    for s in slips:
        d = dead.get(s["slip_id"])
        s["status"] = _DEAD_LABEL[d["kind"]] if d else "live"
        if d and d.get("replaced_by"):
            s["replaced_by"] = d["replaced_by"]
    live = [s for s in slips if s["status"] == "live"]
    return {"batch_id": batch_id, "created_at": b[0], "closed_at": b[1], "builds": builds, "builder_version": VERSION,
            "slips": _grouped(slips), "live_slips": len(live), "superseded": len(slips) - len(live),
            "exposure": _exposure(live)}


def member_batches(session_id, limit=6):
    """The member's batches, newest first: the open one and up to `limit` earlier ones - every record as stored (the
    Slips page shows them as Active / History; nothing is deleted or rewritten)."""
    o = _owner(session_id)
    with _db() as con:
        ids = [r[0] for r in con.execute("SELECT batch_id FROM batches WHERE owner=? ORDER BY seq DESC LIMIT ?", (o, int(limit) + 1))]
    return [b for b in (get_batch(i) for i in ids) if b]

def slip_status(batch_id, slip_id):
    """None while the slip counts; else {"status": replaced / changed / dismissed, "replaced_by"} (DEAD_KINDS)."""
    with _db() as con:
        d = _dead(con, batch_id).get(slip_id)
    return {"status": _DEAD_LABEL[d["kind"]], "replaced_by": d.get("replaced_by")} if d else None


def get_slip(slip_id):
    with _db() as con:
        r = con.execute("SELECT record FROM slips WHERE slip_id=?", (slip_id,)).fetchone()
    return json.loads(r[0]) if r else None


EVENT_KINDS = ("copy", "load", "verify_ok", "verify_changed", "dismiss")     # what a member may record ('void' is build's)


def record_event(batch_id, slip_id, kind, key=None, detail=None):
    """A member copied / loaded / verified a recommended slip (separate from the immutable record). Returns False for a
    retry (same key)."""
    if kind not in EVENT_KINDS:
        raise ValueError("unknown event kind")
    with _db(write=True) as con:
        if not con.execute("SELECT 1 FROM slips WHERE slip_id=? AND batch_id=?", (slip_id, batch_id)).fetchone():
            raise ValueError("unknown slip for this batch")
        if key and con.execute("SELECT 1 FROM events WHERE batch_id=? AND idem_key=?", (batch_id, str(key)[:96])).fetchone():
            return False
        con.execute("INSERT INTO events(batch_id, slip_id, kind, at, idem_key, detail) VALUES (?,?,?,?,?,?)",
                    (batch_id, slip_id, kind, _now_iso(), str(key)[:96] if key else None,
                     json.dumps(detail, default=str)[:4000] if detail is not None else None))
    return True


def events(batch_id):
    with _db() as con:
        return [{"slip_id": a, "kind": b, "at": c, "detail": json.loads(d) if d else None}
                for a, b, c, d in con.execute("SELECT slip_id, kind, at, detail FROM events WHERE batch_id=? ORDER BY id", (batch_id,))]


# ---------------------------------------------------------------- before copying
def _feed_board(b):
    """comparison.line_check's board ({"rows", "at", "error", "state"}) from a raw feed or a board_payload dict."""
    if b is None:
        return None
    if "at" in b:
        return b
    return {"rows": b.get("rows") or [], "at": b.get("updated"), "error": b.get("error"),
            "state": (b.get("feed") or {}).get("state")}


def _side_word(s):
    """Member wording (2026-10-13 audit: the change list said 'More' / 'Less'): Over / Under; More / Less stay internal."""
    return "Over" if s == "More" else "Under" if s == "Less" else s


def _grade_word(g):
    return "Lean" if g == "LEAN" else "No match scheduled" if g == "SCOUT" else g


def verify_before_copy(slip, boards, now=None, model=None, settings=None):
    """Every leg against what its platform shows NOW and the current grade. boards = {book: {"feed": <underdog.board() /
    prizepicks.board()>, "graded": <board_payload(book)>}} (a bare board_payload dict serves as both).
    -> {"ok", "action": "copy"|"rebuild", "legs": [{status, line_check, changes}], "changes": [...]}.
    Any change = rebuild: the old chance is never carried to a new line."""
    now = now or datetime.now(timezone.utc)
    st = normalize_settings(settings or slip.get("settings") or {"book": slip.get("book")})
    out, flat = [], []
    for i, l in enumerate(slip.get("legs") or []):
        lab = f"{l.get('nick')} {l.get('prop')} {l.get('line')} {_side_word(l.get('side'))}"
        ch = []
        b = (boards or {}).get(l.get("book") or "underdog") or {}
        if "rows" in b:                                   # a bare board_payload dict: graded rows and feed in one
            graded, feed = b, _feed_board(b)
        else:
            graded, feed = b.get("graded"), _feed_board(b.get("feed") or b.get("graded"))
        lc_status = None
        t = _ts(l.get("start"))
        if t is None or t <= now:
            ch.append(f"{lab}: match started / locked")
        if l.get("market_type") != "moneyline":
            gr = (graded or {}).get("rows") or []
            id_map = {}
            for r in gr:
                if r.get("ud_player_id") is not None and r.get("player_id") is not None:
                    id_map.setdefault(r["ud_player_id"], r["player_id"])
            lc = comparison.line_check(feed, l.get("player") or l.get("nick"), l.get("prop"), l.get("scope") or "12", l.get("line"),
                                       team=l.get("book_team") or l.get("team"), start=l.get("start"), now=now,
                                       pid=l.get("player_id"), id_map=id_map if l.get("book") == "underdog" else {},
                                       book=l.get("book") or "underdog",
                                       odds_type=l.get("market_type") if l.get("market_type") in ("goblin", "demon") else "standard",
                                       side=l.get("side"), opponent=l.get("book_opponent") or l.get("opponent"))
            lc_status = lc.get("status")
            if lc_status != "verified":
                ch.append(f"{lab}: {lc.get('note') or lc_status}")
            elif (l.get("book") or "underdog") == "underdog" and (lc.get("stale") or (feed or {}).get("error")):
                # line_check keeps "verified" for a stale Underdog feed (other callers rely on that); underdog.board()
                # keeps the last rows when a refresh fails - neither confirms the line is up NOW
                lc_status = "stale"
                ch.append(f"{lab}: Underdog feed not fresh - this line can't be confirmed right now")
        cur = None
        for r in (graded or {}).get("rows") or []:
            if r.get("id") == l.get("row_id") and (r.get("book") or "underdog") == (l.get("book") or "underdog"):
                cur = r
                break
        if cur is None:
            ch.append(f"{lab}: no current grade for this line")
        elif cur.get("hltv_retry"):
            # the board kept its last grade while live data is unavailable: stale, not a current forecast of this line
            ch.append(f"{lab}: the grade is a saved one kept while live data is unavailable - can't be confirmed right now")
        else:
            if cur.get("line") is None or l.get("line") is None or float(cur["line"]) != float(l["line"]):
                ch.append(f"{lab}: line moved {l.get('line')} -> {cur.get('line')}")
            if cur.get("side") != l.get("side"):
                ch.append(f"{lab}: model side now {_side_word(cur.get('side'))}")
            try:
                same = float(cur.get("graded_line")) == float(l.get("line"))
            except (TypeError, ValueError):
                same = False
            cur_model = model or (graded or {}).get("version")
            if not same:
                ch.append(f"{lab}: the forecast is not for this exact line any more")
            if cur.get("graded_model") != l.get("graded_model") or (cur_model and cur.get("graded_model") != cur_model):
                ch.append(f"{lab}: graded by another model version")
            if cur.get("grade") != l.get("grade"):
                ch.append(f"{lab}: grade {_grade_word(l.get('grade'))} -> {_grade_word(cur.get('grade'))}")
            if cur.get("p") is None or l.get("p") is None or abs(float(cur["p"]) - float(l["p"])) >= 0.0005:
                ch.append(f"{lab}: chance changed")
            if l.get("model_outcomes") is not None and cur.get("model_outcomes") != l.get("model_outcomes"):
                ch.append(f"{lab}: outcome probabilities changed")
            why = why_excluded(cur, st, cur_model, now)
            if why and why not in ("started / locked",):
                ch.append(f"{lab}: no longer eligible ({why})")
        out.append({"leg": i, "status": "ok" if not ch else "changed", "line_check": lc_status, "changes": ch})
        flat += ch
    return {"ok": not flat, "action": "copy" if not flat else "rebuild", "checked_at": _now_iso(now), "legs": out, "changes": flat}


# ---------------------------------------------------------------- copy text
_SCOPE = {"12": "Maps 1-2", "1": "Map 1", "123": "Maps 1-3"}
_BOOK = {"underdog": ("Underdog", {"More": "Higher", "Less": "Lower"}), "prizepicks": ("PrizePicks", {"More": "More", "Less": "Less"})}


def _when(s):
    t = _ts(s)
    return t.strftime("%a %b %d %H:%M UTC") if t else "start unknown"


def copy_text(slip):
    """The slip as text with every leg's full identity (player, team vs opponent, start, stat, maps, line, side,
    platform, market type)."""
    book, words = _BOOK.get(slip.get("book") or "underdog", _BOOK["underdog"])
    et = slip.get("entry_type") or "standard"
    # what members paste: the 1Time name, never internal ids or builder versions (those stay on the stored record)
    lines = [f"{book} - {slip.get('n')}-pick entry - {et} lines - 1Time grader"]
    for i, l in enumerate(slip.get("legs") or [], 1):
        mt = l.get("market_type") or "standard"
        if mt == "moneyline":
            lines.append(f"{i}. {l.get('team')} to win vs {l.get('opponent')} - {_when(l.get('start'))} - series moneyline - {book}")
            continue
        side = "Over" if l.get("side") == "More" else "Under"
        lines.append(f"{i}. {l.get('nick') or l.get('player')} ({l.get('team')}) vs {l.get('opponent')} - {_when(l.get('start'))} - "
                     f"{'Kills' if l.get('prop') == 'kills' else 'Headshots'}, {_SCOPE.get(str(l.get('scope') or '12'), l.get('scope'))} - "
                     f"{side} {l.get('line')} ({words.get(l.get('side'), l.get('side'))} on {book}) - {mt} line")
    lines.append("Check each line in the app before entering: lines move, and a changed line needs a new grade.")
    return "\n".join(lines)
