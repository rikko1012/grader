"""The edges board: every pick'em line beside the sharp chance of each side it offers.

Matching (never by substring): same player key (names.key), same market, and the same game - starts within
START_TOLERANCE, or for Kalshi (no reliable start time) the same US game date. One source naming the player in two games
(two players with one name) = ambiguous for that source, left out with the reason.
Quote choice: the sharp line equal to the offer's line first, else the nearest; on a tie Pinnacle, then the 2+ book
consensus, then Kalshi.

Each side's chance is "given no push" (both platforms void a tied pick: PrizePicks dnps-reboots-and-ties, Underdog
help article 10982402); the push chance is shown beside it.

Underdog: eff = chance x the side's payout modifier, compared with UD_REF = (1 / 3.5) ** 0.5 = 53.45%, the per-pick chance
at which a 2-pick standard entry (published base 3.5x, lol/center/slips.py UD_BASE) breaks even. Larger entries need
more per pick (3-pick 6.5x: 53.6%, 5-pick 20x: 54.9%).
PrizePicks: its payout table is in-app only (platform_rules.py), so no break-even is computed - the chance is shown alone.
Goblin / demon lines pay less / more than a standard line by amounts PrizePicks does not publish: shown, never ranked
with standard lines unless asked (--all).
"""
from datetime import datetime, timezone

from . import fair, markets

START_TOLERANCE_S = 3 * 3600
UD_REF = (1 / 3.5) ** 0.5


def _t(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def _index(quotes):
    idx = {}
    for q in quotes:
        idx.setdefault((q["player_key"], q["market"]), []).append(q)
    return idx


def us_date(start):
    """The US calendar day a game belongs to: UTC start minus 6 h (a 02:00Z tip-off is the evening before; no league
    here starts a game between midnight and 6 am Eastern). No time-zone database needed (Windows has none built in)."""
    t = _t(start)
    return datetime.fromtimestamp(t - 6 * 3600, tz=timezone.utc).date().isoformat() if t is not None else None


def _same_slot(offer, q, t0, day):
    if q.get("start") and t0 is not None:
        return abs(_t(q["start"]) - t0) <= START_TOLERANCE_S
    if q.get("date") and day:
        return q["date"] == day                       # Kalshi: dated by its event ticker, never by time
    return True


def _pick(offer, cands):
    """-> (quote, reason). Candidates in other games are dropped; one source naming the player in two games is
    ambiguous for that source (its quotes are dropped)."""
    if not cands:
        return [], None
    t0, day = _t(offer.get("start")), us_date(offer.get("start"))
    near = [q for q in cands if _same_slot(offer, q, t0, day)]
    if not near:
        return None, "sharp price is for another game"
    keep, ambiguous = [], False
    for src in {q["source"] for q in near}:
        qs = [q for q in near if q["source"] == src]
        if len({q.get("game") for q in qs}) > 1 and t0 is not None and all(q.get("start") for q in qs):
            best = min({q["start"] for q in qs}, key=lambda s: abs(_t(s) - t0))
            qs = [q for q in qs if q["start"] == best]
        if len({q.get("game") for q in qs}) > 1:
            ambiguous = True
            continue
        keep += qs
    if not keep:
        return [], "ambiguous: one name in two sharp games" if ambiguous else "sharp price is for another game"
    rank = {"pinnacle": 0, "consensus": 1, "kalshi": 2}
    keep.sort(key=lambda q: (abs(q["line"] - offer["line"]), rank.get(q["source"], 9)))
    best = keep[0]
    if best["line"] == offer["line"]:
        return [best], None
    # a ladder (Kalshi 10+ / 15+ / 20+): the nearest rung on the OTHER side of the line from the same source
    other = [q for q in keep if q["source"] == best["source"] and q.get("game") == best.get("game")
             and (q["line"] - offer["line"]) * (best["line"] - offer["line"]) < 0]
    return ([best, other[0]] if other else [best]), None


def _chance(fam, market, shape, quotes, line):
    """One quote: read the fitted shape at the line. Two rungs either side of the line: read each and weight by
    closeness (1 / gap), so the shape is only ever read between two prices the market really gave."""
    fs = [fair.at_line(fam, market, shape, q["line"], q["p_over"], line) for q in quotes]
    if len(fs) == 1:
        return fs[0]
    w = [1 / abs(f["gap"]) for f in fs]
    mix = {k: sum(f[k] * x for f, x in zip(fs, w)) / sum(w) for k in ("over", "under", "push", "mean")}
    lo, hi = sorted(q["line"] for q in quotes)
    return {**mix, "mean": round(mix["mean"], 2), "gap": min((f["gap"] for f in fs), key=abs),
            "method": f"between rungs {lo:g} and {hi:g}", "far": all(f["far"] for f in fs)}


def build(lg, offers, quotes, injuries=None):
    fam = lg["family"]
    idx = _index(quotes)
    rows = []
    for o in offers:
        spec = markets.market(fam, o["market"])
        row = {k: o.get(k) for k in ("platform", "league", "offer_id", "player", "player_key", "team", "position",
                                     "game", "start", "market", "stat", "line", "odds_type")}
        row["sides_offered"] = [s for s in ("over", "under") if o["sides"][s]["offered"]]
        inj = (injuries or {}).get(o["player_key"])
        row["injury"] = f"{inj['status']}" + (f" ({inj['detail']})" if inj.get("detail") else "") if inj else None
        qs, why = _pick(o, idx.get((o["player_key"], o["market"]), [])) if spec else ([], "market not modelled")
        if not qs:
            row.update({"sharp": None, "reason": why or "no sharp price for this player and stat", "side": None,
                        "chance": None})
            rows.append(row)
            continue
        q = qs[0]
        f = _chance(fam, o["market"], spec["shape"], qs, o["line"])
        decided = 1 - f["push"]
        chance = {s: (f[s] / decided if decided > 0 else None) for s in ("over", "under")}
        sides = {}
        for s in row["sides_offered"]:
            mult = o["sides"][s].get("multiplier")
            eff = chance[s] * mult if (o["platform"] == "underdog" and mult and chance[s] is not None) else None
            sides[s] = {"chance": chance[s], "multiplier": mult, "eff": eff,
                        "value": (eff - UD_REF) if eff is not None else None}
        best = max(sides, key=lambda s: (sides[s]["value"] if sides[s]["value"] is not None else sides[s]["chance"] or 0))
        row.update({"sharp": {"source": q["source"], "line": q["line"], "p_over": q["p_over"], "books": q.get("books"),
                              "limit": q.get("limit"), "overround": q.get("overround"), "game": q.get("game"),
                              "id": q.get("id"), "mean": f["mean"], "gap": f["gap"], "far": f["far"],
                              "method": f["method"], "rungs": [[x["line"], x["p_over"]] for x in qs],
                              "spread": q.get("spread")},
                    "push": f["push"], "sides": sides, "side": best, "chance": sides[best]["chance"],
                    "multiplier": sides[best]["multiplier"], "eff": sides[best]["eff"],
                    "value": sides[best]["value"], "reason": None})
        rows.append(row)
    return rows


def rank(rows, platform=None, show_all=False, min_chance=0.0):
    """Matched rows, best first. Default: standard PrizePicks lines / Underdog lines only, no far line gaps."""
    out = []
    for r in rows:
        if r.get("chance") is None or (platform and r["platform"] != platform):
            continue
        if not show_all and (r["sharp"]["far"] or (r["platform"] == "prizepicks" and r["odds_type"] != "standard")):
            continue
        if r["chance"] < min_chance:
            continue
        out.append(r)
    return sorted(out, key=lambda r: -(r["value"] + UD_REF if r["value"] is not None else r["chance"]))
