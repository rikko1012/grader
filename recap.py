"""Self-reconciling pick recap (2026-10-12).

Why: a chat recap quoted "staked board picks hit ~55%" (one day, deduplicated across platforms) next to
"Overs 56% / Unders 62%" taken from a different, non-deduplicated cohort. This module builds every number of a
recap from ONE pass over the pick log, keeps the cohorts apart, and refuses to return anything that does not add up.

recap(picks, ...) -> nested dict; render_markdown(dict) -> text. Pure: no database, no network, no clock.
load_snapshot(dir) is the only I/O helper (applies the fixed Railway/Render combination rule of a frozen snapshot).

Cohorts (never mixed - a summary given rows from two cohorts raises MixedCohortError):
  board   test = 2             every board grade (Underdog / PrizePicks lines graded by the server, any stake)
  staked  test = 2, units > 0  the board grades that carried a stake (a subset of board, reported on its own)
  member  test = 0             lines members typed into the card (research lookups)
  test = 1 (owner load tests) is excluded and counted.

Outcome of each record (every status accounted for: W + L + P + V + pending = total):
  win      status settled, hit = 1
  loss     status settled, hit = 0
  push     status push (whole-number line landed exactly - no action)
  void     status void (forfeit, did not play, never found finished)
  pending  status open (not settled at the snapshot)

Chance used for expected wins: p_pick of the stored pick row. picklog keeps that row as the FIRST published
forecast (immutable since 2026-10-07; later pre-match regrades go to the forecasts table only and are ignored here).
Exception: rows with first_logged_at set were overwritten by a later grade on 2026-10-07 ~21:06-21:10 UTC; their
first forecast is not in the picks table, so the stored (overwritten) p_pick is used and the rows are counted.
p_pick = verdict["p"] = the grader's p_more or p_less, which is P(Over) / (P(Over) + P(Under)): conditional on no
push (grader.p_more / fix_v2 pmore, after the kills cap). So expected wins are summed over DECIDED records only
(wins + losses) and compared with the decided hit rate, which also excludes pushes and voids.

Deduplication (accuracy only - records are never deleted): records of one cohort with the same player, match, stat,
map scope, line, side and line type (settlement rule) are the same proposition graded on two platforms; they count
once when their outcomes are identical. The representative is the earliest-published record (its p_pick and grade).
Different line, scope or side always stay separate; same proposition with different outcomes stays separate and is
counted as a conflict. Stake accounting is shown on the platform-record basis only.

Stakes: units won = sum of units on wins, units lost = sum of units on losses. This is stake accounting on the
model's suggested units, NOT platform returns (no payouts, multipliers or slips are assumed or included).
"""
import json
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

OUTCOMES = ("win", "loss", "push", "void", "pending")
COHORTS = ("board", "staked", "member")
COHORT_LABEL = {"board": "All board grades (test=2)",
                "staked": "Staked board picks (test=2, units > 0)",
                "member": "Member-entered research lines (test=0)"}
DIMENSIONS = ("side", "prop", "scope", "grade", "platform", "day", "source")
DIM_LABEL = {"side": "Over vs Under", "prop": "Kills vs HS", "scope": "Map scope", "grade": "Grade",
             "platform": "Platform", "day": "Match day",
             "source": "Snapshot source file"}
SCOPE_LABEL = {"12": "Maps 1-2 (12)", "1": "Map 1 (1)", "123": "Maps 1-3 (123)"}
GRADE_ORDER = ("A+", "A", "A-", "B+", "B", "B-", "C", "LEAN")
UD_INFERRED = "underdog (book not recorded; board was Underdog-only before 2026-10-10)"
NO_PLATFORM = "none (member lookup, no board line)"
# same as diagnostics.PLAUSIBLE_MAX - informational count only (nothing is excluded for it)
PLAUSIBLE_MAX = {"kills": {"1": 50, "12": 90, "123": 130}, "hs": {"1": 35, "12": 60, "123": 90}}
EPS = 1e-9


class RecapError(Exception):
    """A subtotal failed to reconcile - the recap is not returned."""


class MixedCohortError(RecapError):
    """Rows from more than one cohort were given to one summary."""


# ---------------------------------------------------------------- record normalisation

def _held(r):
    """Held for review (picklog SOURCE_RULE): BO3.gg quick and full scoreboards disagree and the line sits between them."""
    try:
        return bool(json.loads(r.get("result") or "{}").get("review"))
    except (TypeError, ValueError):
        return False


def outcome(r):
    st = r.get("status")
    if st in ("settled", "push") and _held(r):
        return "pending"                           # unresolved until HLTV / review settles it - never a win or loss
    if st == "settled":
        if r.get("hit") in (1, True, "1"):
            return "win"
        if r.get("hit") in (0, False, "0"):
            return "loss"
        raise RecapError(f"settled record without hit: id={r.get('id')} key={r.get('key')}")
    if st == "push":
        return "push"
    if st == "void":
        return "void"
    if st == "open":
        return "pending"
    raise RecapError(f"unknown status {st!r}: id={r.get('id')} key={r.get('key')}")


def _ts(s):
    if not s:
        return None
    t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def match_day(r, tz_offset_h=-5):
    t = _ts(r.get("match_start"))
    return (t + timedelta(hours=tz_offset_h)).date().isoformat() if t else "unknown"


def board_line(r):
    """A board grade (an Underdog / PrizePicks line the server graded) - THE cohort rule, shared by recap, diagnostics and
    learning (2026-10-13 audit D2: those two read `test` alone, so a board line a member opened counted as a member
    line). test 2, or test 0 with board provenance: on_board (picklog, 2026-10-12) 1 = recorded, 2 = inferred for older
    rows from the platform column; a snapshot from before the column: the same inference (test 0 with a platform).
    Owner load tests (test 1) never."""
    t = int(r.get("test") or 0)
    ob = r.get("on_board")
    if ob is None:
        ob = 2 if t == 0 and r.get("book") else 0
    return t == 2 or (t == 0 and int(ob or 0) in (1, 2))


def cohorts_of(r):
    """The cohorts this record belongs to (board records with a stake are in board AND staked)."""
    t = int(r.get("test") or 0)
    # on_board (picklog, 2026-10-12): a board line a member opened used to be rewritten to test 0 - it is still a board
    # grade, never a member-entered line (board_line)
    if board_line(r):
        return ("board", "staked") if float(r.get("units") or 0) > 0 else ("board",)
    if t == 0:
        return ("member",)
    if t == 1:
        return ()
    raise RecapError(f"unknown test value {t!r}: id={r.get('id')}")


def platform(r):
    b = r.get("book")
    if b:
        return str(b)
    return UD_INFERRED if int(r.get("test") or 0) == 2 else NO_PLATFORM


def first_published(r):
    return r.get("first_logged_at") or r.get("logged_at") or ""


def entry(r, cohort, tz_offset_h=-5):
    """One countable unit: a platform record of one cohort."""
    p = r.get("p_pick")
    return {"cohort": cohort, "outcome": outcome(r), "p": None if p is None else float(p),
            "units": float(r.get("units") or 0.0),
            "side": {"More": "Over", "Less": "Under"}.get(r.get("pick"), str(r.get("pick"))),
            "prop": "HS" if r.get("prop") == "hs" else "Kills" if r.get("prop") == "kills" else str(r.get("prop")),
            "scope": SCOPE_LABEL.get(str(r.get("scope") or "12"), str(r.get("scope"))),
            "grade": str(r.get("grade")), "platform": platform(r), "day": match_day(r, tz_offset_h),
            "source": str(r.get("_source") or "unspecified"),
            "model_version": r.get("model_version") or "untagged",
            "actual": r.get("actual"), "n_records": 1, "ids": [r.get("id")],
            "first_published": first_published(r), "overwritten": bool(r.get("first_logged_at")),
            "dedup_key": dedup_key(r)}


def dedup_key(r):
    """Same proposition = same player, match, stat, scope, line, side and settlement rule (line type)."""
    # the match: identity.assign's canonical id (_mid, set in recap()) - one match logged under a BO3.gg slug and
    # hltv-<id> is one match (2026-10-12 audit D1); the slug when unlinked
    return (str(r.get("slug") or r.get("player") or "").lower(), r.get("_mid") or r.get("match_slug"), r.get("prop"),
            str(r.get("scope") or "12"), float(r["line"]), r.get("pick"), r.get("line_type") or "standard")


def deduplicate(entries):
    """Collapse identical propositions with identical outcomes into one entry. Returns (entries, info)."""
    groups = OrderedDict()
    for e in entries:
        groups.setdefault(e["dedup_key"], []).append(e)
    out, merged, conflicts = [], 0, 0
    for es in groups.values():
        if len(es) == 1:
            out.append(es[0])
            continue
        if len({(e["outcome"], e["actual"]) for e in es}) > 1:      # not identical outcomes: never merged
            conflicts += 1
            out.extend(es)
            continue
        if len({e["cohort"] for e in es}) > 1:
            raise MixedCohortError("deduplication across cohorts")
        rep = min(es, key=lambda e: (e["first_published"], e["ids"][0] if e["ids"][0] is not None else 0))
        d = dict(rep)
        d["platform"] = " + ".join(sorted({e["platform"] for e in es}))
        d["source"] = " + ".join(sorted({e["source"] for e in es}))
        d["n_records"] = sum(e["n_records"] for e in es)
        d["ids"] = [i for e in es for i in e["ids"]]
        d["units"] = None                                            # stakes: platform-record basis only
        out.append(d)
        merged += len(es) - 1
    return out, {"groups_merged": sum(1 for es in groups.values() if len(es) > 1 and
                                      len({(e["outcome"], e["actual"]) for e in es}) == 1),
                 "records_removed_by_dedup": merged, "conflicting_groups_kept_separate": conflicts}


# ---------------------------------------------------------------- summaries

def _blank(stakes):
    d = {"n": 0, **{o: 0 for o in OUTCOMES}, "expected_wins": 0.0, "p_missing": 0}
    if stakes:
        d.update(units_on_wins=0.0, units_on_losses=0.0, units_on_push=0.0, units_on_void=0.0, units_on_pending=0.0)
    return d


def _add(t, e, stakes):
    t["n"] += 1
    t[e["outcome"]] += 1
    if e["outcome"] in ("win", "loss"):
        if e["p"] is None:
            t["p_missing"] += 1
        else:
            t["expected_wins"] += e["p"]
    if stakes:
        t[{"win": "units_on_wins", "loss": "units_on_losses", "push": "units_on_push", "void": "units_on_void",
           "pending": "units_on_pending"}[e["outcome"]]] += e["units"]


def _finish(t, stakes):
    dec = t["win"] + t["loss"]
    t["decided"] = dec
    t["records"] = t["n"]
    t["hit_rate"] = t["win"] / dec if dec else None
    t["expected_rate"] = t["expected_wins"] / (dec - t["p_missing"]) if dec - t["p_missing"] > 0 else None
    t["wins_minus_expected"] = t["win"] - t["expected_wins"] if not t["p_missing"] else None
    if stakes:
        t["units_staked_decided"] = t["units_on_wins"] + t["units_on_losses"]
        t["net_units"] = t["units_on_wins"] - t["units_on_losses"]
    return t


def summarize(entries, cohort, stakes=True):
    """Totals + every breakdown for ONE cohort. Raises MixedCohortError on mixed rows, RecapError if anything fails
    to reconcile."""
    bad = {e["cohort"] for e in entries} - {cohort}
    if bad:
        raise MixedCohortError(f"summary for {cohort!r} got rows from {sorted(bad)}")
    tot = _blank(stakes)
    br = {d: {} for d in DIMENSIONS}
    for e in entries:
        _add(tot, e, stakes)
        for d in DIMENSIONS:
            _add(br[d].setdefault(e[d], _blank(stakes)), e, stakes)
    _finish(tot, stakes)
    for d in DIMENSIONS:
        for v in br[d].values():
            _finish(v, stakes)
        br[d] = OrderedDict(sorted(br[d].items(), key=lambda kv: _sort_key(d, kv[0])))
    out = {"cohort": cohort, "totals": tot, "breakdowns": br,
           "model_versions": dict(Counter(e["model_version"] for e in entries).most_common()),
           "platform_records": sum(e["n_records"] for e in entries)}
    reconcile(out)
    return out


def _sort_key(dim, v):
    if dim == "grade":
        return (GRADE_ORDER.index(v) if v in GRADE_ORDER else 99, v)
    if dim == "scope":
        return (["Maps 1-2 (12)", "Map 1 (1)", "Maps 1-3 (123)"].index(v) if v.startswith(("Maps", "Map")) and
                v in ("Maps 1-2 (12)", "Map 1 (1)", "Maps 1-3 (123)") else 9, v)
    return (0, v)


def reconcile(s):
    """Self-check: statuses sum to the total; every breakdown's rows sum exactly to the cohort total."""
    t = s["totals"]
    if sum(t[o] for o in OUTCOMES) != t["n"]:
        raise RecapError(f"{s['cohort']}: W+L+P+V+pending {sum(t[o] for o in OUTCOMES)} != total {t['n']}")
    if t["decided"] != t["win"] + t["loss"]:
        raise RecapError(f"{s['cohort']}: decided != wins + losses")
    ints = ("n",) + OUTCOMES + ("decided", "p_missing")
    floats = [k for k in t if k.startswith("units_on_") or k in ("expected_wins",)]
    for d, rows in s["breakdowns"].items():
        for k in ints:
            got = sum(v[k] for v in rows.values())
            if got != t[k]:
                raise RecapError(f"{s['cohort']}: breakdown {d!r} {k} sums to {got}, total is {t[k]}")
        for k in floats:
            got = sum(v[k] for v in rows.values())
            if abs(got - t[k]) > EPS * max(1.0, abs(t[k])):
                raise RecapError(f"{s['cohort']}: breakdown {d!r} {k} sums to {got}, total is {t[k]}")
        for name, v in rows.items():
            if sum(v[o] for o in OUTCOMES) != v["n"]:
                raise RecapError(f"{s['cohort']}: {d}={name} statuses do not sum")
    if sum(s["model_versions"].values()) != t["n"]:
        raise RecapError(f"{s['cohort']}: model versions do not sum to the total")


# ---------------------------------------------------------------- the recap

def _d(x):
    if x is None or isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def recap(picks, tz_offset_h=-5, date_from=None, date_to=None, cutoff=None, meta=None):
    """Recap of the pick log `picks` (list of picklog rows) for match days date_from..date_to (inclusive, in the fixed
    UTC offset tz_offset_h). cutoff (ISO time): rows logged after it are left out and counted. meta: free-form
    provenance (snapshot time, sources) copied into the header."""
    d0, d1 = _d(date_from), _d(date_to)
    cut = _ts(cutoff) if cutoff else None
    excl = Counter()
    rows_in = 0
    by_cohort = {c: [] for c in COHORTS}
    first_last = {c: [None, None] for c in COHORTS}
    overwritten = Counter()
    implausible = Counter()
    import identity
    picks = [dict(r) for r in picks]
    for src in {r.get("_source") for r in picks}:                 # ids are unique within one source (pick log) only
        grp = [r for r in picks if r.get("_source") == src and r.get("id") is not None]
        m = identity.assign(grp)
        for r in grp:
            r["_mid"] = m.get(r["id"])
    for r in picks:
        rows_in += 1
        cs = cohorts_of(r)
        if not cs:
            excl["owner load tests (test=1)"] += 1
            continue
        day = match_day(r, tz_offset_h)
        if day == "unknown":
            dd = None
        else:
            dd = date.fromisoformat(day)
        if (d0 and (dd is None or dd < d0)) or (d1 and (dd is None or dd > d1)):
            excl["match day outside the range"] += 1
            continue
        if cut and _ts(r.get("logged_at")) and _ts(r.get("logged_at")) > cut:
            excl["logged after the cutoff"] += 1
            continue
        for c in cs:
            by_cohort[c].append(entry(r, c, tz_offset_h))
            fl = first_last[c]
            fl[0] = day if fl[0] is None or day < fl[0] else fl[0]
            fl[1] = day if fl[1] is None or day > fl[1] else fl[1]
            if r.get("first_logged_at"):
                overwritten[c] += 1
            mx = PLAUSIBLE_MAX.get(r.get("prop"), {}).get(str(r.get("scope") or "12"))
            if mx is not None and not (0 < float(r["line"]) <= mx):
                implausible[c] += 1
    # every input row is accounted for exactly once (staked rows are a subset of board, not extra rows)
    if len(by_cohort["board"]) + len(by_cohort["member"]) + sum(excl.values()) != rows_in:
        raise RecapError("input rows not fully accounted for")
    out = {"header": {"date_from": d0.isoformat() if d0 else None, "date_to": d1.isoformat() if d1 else None,
                      "timezone": f"America/Chicago as fixed UTC{tz_offset_h:+d} (match day of match_start)",
                      "cutoff": cut.isoformat() if cut else None, "meta": dict(meta or {}),
                      "rows_in": rows_in, "excluded": dict(excl),
                      "member_rows_with_board_platform": sum(1 for e in by_cohort["member"] if e["platform"] != NO_PLATFORM)},
           "cohorts": {}}
    for c in COHORTS:
        ents = by_cohort[c]
        rec = summarize(ents, c, stakes=True)
        ded_entries, info = deduplicate(ents)
        ded = summarize(ded_entries, c, stakes=False)
        if ded["platform_records"] != rec["totals"]["n"]:
            raise RecapError(f"{c}: deduplicated view covers {ded['platform_records']} records, cohort has {rec['totals']['n']}")
        if ded["totals"]["n"] + info["records_removed_by_dedup"] != rec["totals"]["n"]:
            raise RecapError(f"{c}: dedup count does not reconcile")
        out["cohorts"][c] = {"label": COHORT_LABEL[c], "first_day": first_last[c][0], "last_day": first_last[c][1],
                             "records": rec, "deduplicated": ded, "dedup": info,
                             "overwritten_first_forecast": overwritten[c], "implausible_line_typo": implausible[c]}
    # staked is a subset of board: never more of anything
    b, s = out["cohorts"]["board"]["records"]["totals"], out["cohorts"]["staked"]["records"]["totals"]
    for k in ("n",) + OUTCOMES:
        if s[k] > b[k]:
            raise RecapError(f"staked {k} {s[k]} exceeds board {b[k]}")
    return out


# ---------------------------------------------------------------- markdown

def _pct(x):
    return "-" if x is None else f"{x * 100:.1f}%"


def _u(x):
    return f"{x:.2f}U"


def _row(name, t, stakes):
    cells = [name, str(t["n"]), str(t["win"]), str(t["loss"]), str(t["push"]), str(t["void"]), str(t["pending"]),
             _pct(t["hit_rate"]), f"{t['expected_wins']:.2f}", _pct(t["expected_rate"]),
             "-" if t["wins_minus_expected"] is None else f"{t['wins_minus_expected']:+.2f}"]
    if stakes:
        cells += [_u(t["units_on_wins"]), _u(t["units_on_losses"]), f"{t['net_units']:+.2f}U"]
    return "| " + " | ".join(cells) + " |"


def _head(first, stakes):
    h = [first, "Total", "W", "L", "Push", "Void", "Pending", "Hit rate (W/(W+L))", "Expected W", "Expected rate",
         "W - expected"]
    if stakes:
        h += ["Units won", "Units lost", "Net units"]
    return "| " + " | ".join(h) + " |\n|" + "---|" * len(h)


def render_markdown(rc, title="Pick recap"):
    h = rc["header"]
    L = [f"# {title}", ""]
    rng = f"{h['date_from'] or 'first record'} to {h['date_to'] or 'last record'}"
    L += [f"- **Match days:** {rng} ({h['timezone']})",
          f"- **Snapshot cutoff:** {h['cutoff'] or 'none'}"]
    for k, v in (h.get("meta") or {}).items():
        L.append(f"- **{k}:** {v}")
    L.append(f"- **Rows read:** {h['rows_in']}; excluded: " +
             (", ".join(f"{k} {v}" for k, v in h["excluded"].items()) or "none"))
    L.append("- **Model versions included** (records per cohort; 'untagged' = logged before versions were stamped "
             "on 2026-10-07):")
    for c in COHORTS:
        mv = rc["cohorts"][c]["records"]["model_versions"]
        L.append(f"  - {COHORT_LABEL[c]}: " + (", ".join(f"`{k}` {v}" for k, v in mv.items()) or "none"))
    L += ["", "## How to read this", "",
          "- Three cohorts, never pooled: board grades (test=2), the staked subset of the board (units > 0), "
          "member research lines (test=0). Owner load tests (test=1) are excluded. Every percentage below names "
          "its cohort and its basis.",
          "- Every record has exactly one outcome: W (settled hit), L (settled miss), Push, Void, Pending (open at the "
          "snapshot). W + L + Push + Void + Pending = Total in every row, and every breakdown table sums to its "
          "cohort total (checked in code; the report is not produced otherwise).",
          "- Hit rate = W / (W + L): pushes, voids and pending are excluded.",
          "- Expected W = sum of the ORIGINAL saved chance (p_pick of the stored pick row = the first published "
          "forecast; later pre-match regrades are ignored) over the decided records (W + L). The chance is the "
          "grader's P(Over)/(P(Over)+P(Under)), i.e. conditional on no push on whole-number lines, which is why it "
          "is compared with the push-excluded hit rate. Rows overwritten on 2026-10-07 (first_logged_at set) keep "
          "their stored chance and are counted below.",
          "- Platform records = one row per platform line. Deduplicated = the same proposition (player, match, stat, "
          "map scope, line, side, line type) on both platforms with the same outcome counted once (earliest-published "
          "record's chance and grade). Different line, scope or side are never merged. No record is deleted.",
          "- Units won / lost = the model's suggested units on wins / losses: stake accounting, NOT platform returns "
          "(no payouts, multipliers or slips are included).", ""]
    L += ["## Headline (all cohorts, both bases)", "", _head("Cohort / basis", False)]
    for c in COHORTS:
        co = rc["cohorts"][c]
        L.append(_row(f"{COHORT_LABEL[c]} - platform records", co["records"]["totals"], False))
        L.append(_row(f"{COHORT_LABEL[c]} - deduplicated", co["deduplicated"]["totals"], False))
    L.append("")
    for c in COHORTS:
        co = rc["cohorts"][c]
        rt, dd = co["records"]["totals"], co["dedup"]
        L += [f"## {COHORT_LABEL[c]}", "",
              f"- Records: {rt['n']} platform records (match days {co['first_day'] or '-'} to {co['last_day'] or '-'}); "
              f"deduplicated: {co['deduplicated']['totals']['n']} "
              f"({dd['groups_merged']} cross-platform groups merged, {dd['records_removed_by_dedup']} records collapsed, "
              f"{dd['conflicting_groups_kept_separate']} same-proposition groups with different outcomes kept separate).",
              f"- Stake accounting (platform records, NOT platform returns): units on wins {_u(rt['units_on_wins'])}, "
              f"units on losses {_u(rt['units_on_losses'])}, net {rt['net_units']:+.2f}U over "
              f"{_u(rt['units_staked_decided'])} staked on decided records; on pushes {_u(rt['units_on_push'])}, "
              f"voids {_u(rt['units_on_void'])}, pending {_u(rt['units_on_pending'])}.",
              f"- Stored pick rows overwritten by a later grade on 2026-10-07 (first forecast not recoverable): "
              f"{co['overwritten_first_forecast']}. Lines above the plausible maximum (likely typos, still counted): "
              f"{co['implausible_line_typo']}."]
        if c == "member":
            L.append(f"- Member rows that carry a board platform: {rc['header']['member_rows_with_board_platform']} "
                     "(a board line a member later looked up; picklog stores the merged row as a member line).")
        L.append("")
        for basis, key, stakes in (("platform records", "records", True), ("deduplicated", "deduplicated", False)):
            s = co[key]
            for d in DIMENSIONS:
                L += [f"### {COHORT_LABEL[c]} - {DIM_LABEL[d]} ({basis})", "", _head(DIM_LABEL[d], stakes)]
                for name, t in s["breakdowns"][d].items():
                    L.append(_row(name, t, stakes))
                L.append(_row(f"**Total ({rt['n'] if key == 'records' else s['totals']['n']})**", s["totals"], stakes))
                L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- frozen snapshot loader (the only I/O)

def load_snapshot(snap_dir, split_day="2026-10-10", tz_offset_h=-5):
    """Combine a frozen snapshot: match days <= split_day from railway_picks.json, later days from render_picks.json.
    Returns (rows, provenance). Rows get `_source`. Also checks that each Render pick row's p_pick equals its first
    forecast in render_forecasts.json (the 'first published forecast' rule)."""
    snap = Path(snap_dir)
    man = json.loads((snap / "SNAPSHOT.json").read_text(encoding="utf-8"))
    rw = json.loads((snap / "railway_picks.json").read_text(encoding="utf-8"))
    rw = rw["picks"] if isinstance(rw, dict) else rw
    rd = json.loads((snap / "render_picks.json").read_text(encoding="utf-8"))
    rd = rd["picks"] if isinstance(rd, dict) else rd
    split = date.fromisoformat(split_day)
    rows, dropped = [], Counter()
    for src, rs, keep in (("railway", rw, lambda d: d <= split), ("render", rd, lambda d: d > split)):
        for r in rs:
            day = match_day(r, tz_offset_h)
            if day != "unknown" and keep(date.fromisoformat(day)):
                rows.append(dict(r, _source=src))
            else:
                dropped[src] += 1
    first_fc, check = {}, Counter()
    fpath = snap / "render_forecasts.json"
    if fpath.exists():
        for f in json.loads(fpath.read_text(encoding="utf-8")):
            if f.get("kind") == "first":
                first_fc.setdefault(f["pick_id"], f)
        for r in rd:
            f = first_fc.get(r["id"])
            if f is None:
                check["no first forecast"] += 1
            elif abs(float(f["p_pick"]) - float(r["p_pick"])) < 1e-9 and f["pick"] == r["pick"] and float(f["line"]) == float(r["line"]):
                check["pick row == first forecast"] += 1
            else:
                check["pick row differs from first forecast"] += 1
    prov = {"snapshot taken (UTC)": man.get("taken_at_utc"),
            "railway export": str(man.get("railway_export")) + " - Railway rows still open at that time are Pending here",
            "combination rule": man.get("rule"),
            "rows used": f"railway {sum(1 for r in rows if r['_source'] == 'railway')}, "
                         f"render {sum(1 for r in rows if r['_source'] == 'render')}; dropped by the rule: "
                         f"railway {dropped['railway']}, render {dropped['render']}",
            "first-forecast check (Render picks vs render_forecasts kind=first)": ", ".join(f"{k} {v}" for k, v in check.items()) or "n/a",
            "sha256 (manifest)": ", ".join(f"{k[7:]} {v}" for k, v in man.items() if k.startswith("sha256_"))}
    return rows, prov


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Self-reconciling pick recap from a frozen snapshot")
    ap.add_argument("snapshot")
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--out", required=True, help="output path without extension (.md and .json are written)")
    ap.add_argument("--title", default="Pick recap")
    a = ap.parse_args()
    rows, prov = load_snapshot(a.snapshot)
    man = json.loads((Path(a.snapshot) / "SNAPSHOT.json").read_text(encoding="utf-8"))
    rc = recap(rows, -5, a.date_from, a.date_to, cutoff=man.get("taken_at_utc"), meta=prov)
    Path(a.out + ".json").write_text(json.dumps(rc, indent=1, default=str), encoding="utf-8")
    Path(a.out + ".md").write_text(render_markdown(rc, a.title), encoding="utf-8")
    print(a.out + ".md")
