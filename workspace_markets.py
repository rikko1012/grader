"""SCOUT_MARKET_TOOLS (default off): the Tools route's +EV feed and arbitrage sheet over prices this server already holds.

Inputs (no provider request is made here): the NBA / NFL snapshots' book rows (FanDuel, BetRivers, The Odds API books,
Kalshi, Polymarket, collected by the sports service) and every enabled direct pick'em reader whose price is a documented payout
(book_providers: Sleeper per-pick multipliers). Dabble's per-side number is undocumented and not what an entry pays
("price_comparable": false), so it never enters this math. Pick'em prices are never a standalone bet: flagged, kept out of the
consensus and out of the member view. All math is gradecore.market_tools / gradecore.devig.

+EV feed. EV = p_cal x decimal_odds - 1, push-adjusted (gradecore.market_tools.ev_push_adjusted); never a probability gap.
  p_cal, in this order:
    1. Scout's chance, only where gradecore containment's state FILE releases that sport and stat (calibration checked).
       An owner env switch is not a calibration check and does not count;
    2. otherwise the no-vig consensus of the OTHER sportsbooks at the identical line: each book's worst case over the four
       devig methods (gradecore.devig.worst_case), averaged over at least two books whose own two-sided market is fresh and
       not wide. The priced book never prices itself; prediction markets and pick'em multipliers never enter the consensus.
  A whole-number line needs a push chance: priced only with a released Scout chance (which carries p_push), otherwise left out.
  Market-width check: every row carries its own book's overround and width in cents; a wide market is flagged.
Arbitrage sheet. Best Over and best Under from two different books at the identical line.
  stake share A = impA / (impA + impB); margin % = (1 / (impA + impB) - 1) x 100 (gradecore.market_tools.arb_two_way).
  A leg that is stale (older than ARB_MAX_AGE_S), from a prediction market (depth and fees not verified), one-sided or a
  pick'em pick (not a standalone bet) is flagged and the pair is left out of the sheet; flagged pairs are listed with reasons.

Member view (owner 2026-11-05, "Price check"): every row also carries plain context the page shows as is - the chance the price
needs to break even ((1 - push chance) / odds, so a whole-number line priced with Scout's chance never reads "fair below needed"
next to a positive result), the American price, the average result per $100, each leg's stake per $100 and the either-way return
per $100. Members see sportsbook and prediction-market (Kalshi, Polymarket) price rows, as before; pick'em multiplier rows,
flagged pairs (stale / prediction-market / pick'em legs), exclusion counts and the formulas stay in the owner's view. Exclusions are also counted per line (excluded_lines), so they never exceed
markets_checked, and sections_possible says whether a sport's prices can produce each section at all.

For 700 members: one build every TTL s for everyone (srv.shared); each member's answer is that build cut to the sports they see."""
import heapq
import json
import time

import book_providers as bp
import workspace_books as wb

TTL = 30
PREDICTION = ("kalshi", "polymarket")
BOARD_PICKEM = ("underdog", "prizepicks")


def direct_pickem():
    """Direct pick'em readers in the config (Sleeper, Dabble, ...): their quotes are pick'em prices, never sportsbook prices."""
    return tuple(pid for pid, p in bp.providers().items() if p.get("kind") == "pickem" and pid in bp.POLLERS)


PICKEM = BOARD_PICKEM + ("sleeper", "dabble")
CONSENSUS_MAX_AGE_S = 10 * 60
MIN_BOOKS = 2
MAX_ROWS = 300
CLOSEST_ROWS = 25        # owner audit 2026-11-06 (T6): with nothing above its fair chance, the sheet lists the closest prices
FRESH_FILTERS = [[120, "2 min"], [600, "10 min"], [1800, "30 min"]]


def american(d):
    """Decimal price -> American price text (+125 / -111)."""
    try:
        d = float(d)
    except (TypeError, ValueError):
        return None
    if not d > 1:
        return None
    return f"+{round((d - 1) * 100)}" if d >= 2 else f"-{round(100 / (d - 1))}"


def kind_of(platform):
    if platform in PREDICTION:
        return "prediction"
    if platform in PICKEM or platform in direct_pickem():
        return "pickem"
    return "sportsbook"


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 1 else None


def model_released(sport, stat):
    """True only when gradecore containment's state file releases this sport and stat."""
    try:
        if sport in ("cs2", "lol"):
            import letter_containment as lc       # lc-1: a held letter is never a calibrated chance
            if lc.contained(sport, stat):
                return False
        from gradecore import containment
        lg = sport if sport in ("nba", "nfl") else None
        st = containment.state(sport, lg)
        return st.get("source") == "file" and not st.get("contained") and stat not in (st.get("stats") or [])
    except Exception:                              # noqa: BLE001 - unknown = not released
        return False


def exchange_prices(v):
    """A prediction market's Over contract as the sports board shows it ("bid / ask", or [bid, ask]) -> decimal prices
    (buy Yes at the ask = Over; buy No at 1 - bid = Under). Fees and depth are not known, so these legs are always flagged."""
    try:
        bid, ask = ([float(x) for x in v.split("/")] if isinstance(v, str) else [float(x) for x in v])[:2]
    except (TypeError, ValueError, AttributeError):
        return None, None
    over = 1 / ask if 0 < ask < 1 else None
    under = 1 / (1 - bid) if 0 < bid < 1 else None
    return over, under


def _label(p):
    import workspace_sports
    if isinstance(p, str) and p.startswith("oddsapi:"):
        return workspace_sports.book_label(p.split(":", 1)[1]) + " · The Odds API"
    if p in bp.providers() and p in bp.POLLERS:
        return bp.providers()[p]["label"]
    return workspace_sports.book_label(p)


def _quote(platform, over, under, seen, source, mult=False):
    return {"book": platform, "book_label": _label(platform), "kind": kind_of(platform), "over": over, "under": under,
            "seen": seen, "source": source, "multiplier": mult}


def groups(srv, sport, now):
    """{group key: {"rec": a board record of this market, "line", "quotes": [...]}} for one sport."""
    out = {}
    recs, s = wb.records(srv, sport)
    if recs is None:
        return None
    if sport in ("nba", "nfl"):
        quotes, props = wb.book_quotes(s)
        for (plat, e, pkey, st, scope, line), b, x in quotes:
            g = out.get((e, pkey, st, wb._per(scope), line))
            if g is None:
                got = wb._recs_of(s, props, x)
                if not got:
                    continue
                g = out[(e, pkey, st, wb._per(scope), line)] = {"recs": got, "line": line, "quotes": []}
            if plat in PREDICTION:
                over, under = exchange_prices(b.get("over"))
                q = _quote(plat, over, under, wb._ts(b.get("seen_at")), "sports service · " + _label(plat))
                q["exchange"] = True
                g["quotes"].append(q)
                continue
            g["quotes"].append(_quote(plat, _num(b.get("over")), _num(b.get("under")), wb._ts(b.get("seen_at")), "sports service · " + _label(plat)))
    subj = None
    for pid in direct_pickem():
        if not bp.enabled(pid) or bp.providers()[pid].get("price_comparable") is False:
            # Dabble: its per-side number is not what an entry pays (book_providers.json rules.pick_price). Priced as decimal odds
            # it makes every near-fair side look +EV, so it stays out of the +EV feed and the arbitrage sheet entirely.
            continue
        if subj is None:
            subj = wb.subjects(recs)
        label = bp.providers()[pid]["label"]
        for o in bp.offers(pid, sport, now):
            got, _ = wb.match(subj, o, bp.providers()[pid].get("match"))
            if not got:
                continue
            r0 = got[0]
            k = (r0["event"]["id"], r0["player"]["key"], r0["market"]["stat"], wb._per(r0["market"]["period"]), o["line"])
            g = out.setdefault(k, {"recs": got, "line": o["line"], "quotes": []})
            # only a documented per-pick payout (Sleeper's multiplier) is a pick'em price here
            g["quotes"].append(_quote(pid, o["over"].get("payout"), o["under"].get("payout"), o["observed_at"], label + " feed", mult=True))
    return out


def _consensus(quotes, exclude, side, now, mt, devig):
    """Mean of each other fresh, non-wide, two-sided sportsbook's worst-case fair chance for `side` -> (p, books, width) or None."""
    ps, books, widths = [], [], []
    for q in quotes:
        if q["book"] == exclude or q["kind"] != "sportsbook" or not (q["over"] and q["under"]):
            continue
        if q["seen"] is None or now - q["seen"] > CONSENSUS_MAX_AGE_S:
            continue
        w = mt.market_width(q["over"], q["under"])
        if w["wide"]:
            continue
        wc = devig.worst_case([q["over"], q["under"]], 0 if side == "over" else 1)
        if wc is None:
            continue
        ps.append(wc[0]); books.append(q["book_label"]); widths.append(w["overround"])
    if len(ps) < MIN_BOOKS:
        return None
    return sum(ps) / len(ps), books, sum(widths) / len(widths)


def _row_base(g, sport):
    r = g["recs"][0]
    return {"sport": sport, "event": r["event"].get("label"), "event_id": r["event"]["id"], "start_utc": r["event"].get("start_utc"),
            "player": r["player"].get("name"), "team": r["player"].get("team"), "stat": r["market"].get("stat"),
            "stat_label": r["market"].get("stat_label"), "period_label": r["market"].get("period_label"), "line": g["line"]}


def _released_p(g, sport, side):
    """Scout's saved unconditional chance and push chance at this exact line, when containment's file releases it."""
    for r in g["recs"]:
        if r["offer"].get("line") != g["line"]:
            continue
        s = r.get("scout") or {}
        p, pp = s.get("p_" + side), s.get("p_push") or 0.0
        if isinstance(p, (int, float)) and model_released(sport, r["market"].get("stat")):
            return float(p), float(pp), s.get("model_version")
    return None


def sheet(srv, sport, now):
    from gradecore import devig, market_tools as mt
    gs = groups(srv, sport, now)
    if gs is None:
        return {"state": "unavailable", "error": "Board not loaded yet", "ev": [], "arbitrage": [], "flagged": [], "excluded": {}}
    ev, arb, flagged, excluded, newest, n_quotes, near = [], [], [], {}, None, 0, []
    excluded_lines, possible = {}, {"ev": False, "arb": False}
    here = {}

    def ex(why, n=1):
        excluded[why] = excluded.get(why, 0) + n
        here[why] = here.get(why, 0) + n
    for g in gs.values():
        quotes = g["quotes"]
        here.clear()
        n_ev = len(ev)
        books = {q["book"] for q in quotes if q["kind"] == "sportsbook" and not q.get("exchange") and not q["multiplier"] and (q["over"] or q["under"])}
        two_sided = {q["book"] for q in quotes if q["kind"] == "sportsbook" and not q.get("exchange") and q["over"] and q["under"]}
        if len(books) >= 2:
            possible["arb"] = True                          # two sportsbooks at one line: a both-sides pair can exist
        if len(two_sided) > MIN_BOOKS:
            possible["ev"] = True                           # a priced book plus MIN_BOOKS others: a fair chance can exist
        whole = float(g["line"]).is_integer()
        for q in quotes:
            n_quotes += 1
            if q["seen"] is not None and (newest is None or q["seen"] > newest):
                newest = q["seen"]
        # ---- +EV
        for q in quotes:
            if q.get("exchange"):
                ex("Prediction market: depth and fees not verified"); continue
            age = None if q["seen"] is None else now - q["seen"]
            if age is None or age > mt.EV_STALE_S:
                ex("Quote older than 30 minutes"); continue
            own = mt.market_width(q["over"], q["under"]) if q["over"] and q["under"] and not q["multiplier"] else None
            for side in ("over", "under"):
                price = q[side]
                if not price:
                    continue
                rel = _released_p(g, sport, side)
                if rel:
                    possible["ev"] = True
                    p, pp, mv = rel
                    p_source, books, cwidth = f"Scout calibrated chance ({mv})", [], None
                    chance_from = "Scout"
                else:
                    if whole:
                        ex("Whole-number line: push chance not priced"); continue
                    c = _consensus(quotes, q["book"], side, now, mt, devig)
                    if c is None:
                        ex("No calibrated chance and fewer than two other books at this line"); continue
                    p, books, cwidth = c
                    pp = 0.0
                    p_source = "No-vig consensus · " + ", ".join(books) + " (worst case of 4 devig methods)"
                    chance_from = "Other books (" + ", ".join(books) + ")"
                try:
                    value = mt.ev_push_adjusted(p, price, pp)
                except ValueError:
                    ex("Invalid price or chance"); continue
                flags = []
                if own and own["wide"]:
                    flags.append("Wide market")
                if q["multiplier"]:
                    flags.append("Pick'em multiplier: needs 2+ picks")
                (ev if value > 0 else near).append(dict(_row_base(g, sport), side=side, book=q["book"], book_label=q["book_label"], kind=q["kind"],
                               odds=round(price, 3), p_cal=round(p, 4), p_push=round(pp, 4), p_source=p_source, ev=round(value, 4),
                               width=own, consensus_width=round(cwidth, 4) if cwidth is not None else None,
                               age_s=round(age, 1), seen_at=wb._iso(q["seen"]), source=q["source"], flags=flags,
                               needs=round(mt.implied(price) * (1 - pp), 4), american=american(price), per_100=round(value * 100, 2),
                               chance_from=chance_from))
        if len(ev) == n_ev and here:                        # one reason per line, so the line counts add up to markets_checked
            why = max(here.items(), key=lambda kv: kv[1])[0]
            excluded_lines[why] = excluded_lines.get(why, 0) + 1
        # ---- arbitrage: best Over and best Under on two different books
        legs = []
        for q in quotes:
            for side in ("over", "under"):
                if q[side] or q.get("exchange"):
                    legs.append((side, q))
        if len({q["book"] for _, q in legs}) < 2:
            continue

        def leg_flags(q, side):
            f = []
            age = None if q["seen"] is None else now - q["seen"]
            if age is None or age > mt.ARB_MAX_AGE_S:
                f.append("Stale leg")
            if q.get("exchange") or q["kind"] == "prediction":
                f.append("Prediction market: depth and fees not verified")
            if q["multiplier"]:
                f.append("Pick'em pick: not a standalone bet")
            if not q.get("exchange") and not (q["over"] and q["under"]) and not q["multiplier"]:
                f.append("One-sided market")
            return f

        def best(side, clean, exclude=None):
            c = [q for s2, q in legs if s2 == side and q[side] and q["book"] != exclude and (not clean or not leg_flags(q, side))]
            return max(c, key=lambda q: q[side]) if c else None
        for clean in (True, False):
            a = best("over", clean)
            b = best("under", clean, a["book"] if a else None) if a else None
            b2 = best("under", clean)
            a2 = best("over", clean, b2["book"]) if b2 else None
            cands = [(x, y) for x, y in ((a, b), (a2, b2)) if x and y]
            if not cands:
                continue
            x, y = max(cands, key=lambda xy: -(1 / xy[0]["over"] + 1 / xy[1]["under"]))
            r = mt.arb_two_way(x["over"], y["under"])
            if not r["arbitrage"]:
                continue
            fl = leg_flags(x, "over") + leg_flags(y, "under")
            ages = [None if z["seen"] is None else round(now - z["seen"], 1) for z in (x, y)]
            row = dict(_row_base(g, sport), margin_pct=round(r["margin_pct"], 3), total_implied=round(r["total_implied"], 5),
                       return_per_100=round(r["margin_pct"], 2), oldest_leg_s=None if None in ages else max(ages),
                       legs=[{"side": "over", "book": x["book"], "book_label": x["book_label"], "kind": x["kind"], "odds": round(x["over"], 3),
                              "implied": round(r["implied"][0], 5), "stake_share": round(r["stake_share"][0], 5),
                              "stake_per_100": round(r["stake_share"][0] * 100, 2), "american": american(x["over"]),
                              "age_s": None if x["seen"] is None else round(now - x["seen"], 1), "seen_at": wb._iso(x["seen"]), "source": x["source"],
                              "flags": leg_flags(x, "over")},
                             {"side": "under", "book": y["book"], "book_label": y["book_label"], "kind": y["kind"], "odds": round(y["under"], 3),
                              "implied": round(r["implied"][1], 5), "stake_share": round(r["stake_share"][1], 5),
                              "stake_per_100": round(r["stake_share"][1] * 100, 2), "american": american(y["under"]),
                              "age_s": None if y["seen"] is None else round(now - y["seen"], 1), "seen_at": wb._iso(y["seen"]), "source": y["source"],
                              "flags": leg_flags(y, "under")}])
            if clean and not fl:
                arb.append(row)
                break
            if not clean and fl:
                row["flags"] = sorted(set(fl))
                flagged.append(row)
    ev.sort(key=lambda x: -x["ev"])
    arb.sort(key=lambda x: -x["margin_pct"])
    flagged.sort(key=lambda x: -x["margin_pct"])
    # T6: the priced single bets that fall short of their fair chance, closest first (members: sportsbook / prediction rows only,
    # cut in for_member). Same row shape as "ev"; their ev / per_100 are 0 or below and are never counted as worth acting on.
    closest = heapq.nlargest(CLOSEST_ROWS, (x for x in near if x["kind"] in ("sportsbook", "prediction")), key=lambda x: (x["ev"], -x["age_s"]))
    return {"state": "ready" if n_quotes else "empty", "as_of": wb._iso(now), "newest_quote_at": wb._iso(newest),
            "quotes_checked": n_quotes, "markets_checked": len(gs), "ev": ev[:MAX_ROWS], "closest": closest, "arbitrage": arb[:MAX_ROWS],
            "flagged": flagged[:MAX_ROWS], "excluded": excluded, "excluded_lines": excluded_lines, "sections_possible": possible}


METHOD = {"ev": "EV = p_cal x decimal odds - 1, push-adjusted (a push returns the stake)",
          "p_cal": "Scout's chance only where calibration has released the sport and stat; otherwise the no-vig consensus of at least "
                   "two other sportsbooks at the same line (worst case of four devig methods). The priced book never prices itself.",
          "arbitrage": "stake share A = impA / (impA + impB); margin % = (1 / (impA + impB) - 1) x 100",
          "wide_overround": None, "arb_max_age_s": None, "ev_stale_s": None, "fresh_filters": FRESH_FILTERS}


def build(srv, now=None):
    from gradecore import market_tools as mt
    now = time.time() if now is None else now
    sports = {}
    for sport in ("cs2", "lol", "nba", "nfl"):
        try:
            sports[sport] = sheet(srv, sport, now)
        except Exception as e:                     # noqa: BLE001 - one sport failing never empties the others
            sports[sport] = {"state": "unavailable", "error": type(e).__name__, "ev": [], "arbitrage": [], "flagged": [], "excluded": {}}
    books = _books(sports.values())
    method = dict(METHOD, wide_overround=mt.WIDE_OVERROUND, arb_max_age_s=mt.ARB_MAX_AGE_S, ev_stale_s=mt.EV_STALE_S)
    return {"as_of": wb._iso(now), "sports": sports, "books": books, "method": method}


def _books(docs, flagged=False):
    """Books that have a row in these sport docs (EV and both-sides rows; flagged pairs only for the owner's view)."""
    docs = list(docs)
    out = {(r["book"], r["book_label"]) for d in docs for r in d.get("ev", [])}
    out |= {(l["book"], l["book_label"]) for d in docs for r in d.get("arbitrage", []) + (d.get("flagged", []) if flagged else []) for l in r["legs"]}
    return [{"id": b, "label": l} for b, l in sorted(out)]


def shared(srv):
    def compute():
        return 200, json.dumps(build(srv), default=str, separators=(",", ":")).encode()
    return srv.shared(("ws_market_sheet",), compute, ttl=TTL)


MEMBER_HIDDEN = ("flagged", "excluded", "excluded_lines")


def for_member(body, allowed, owner=False):
    """The shared build cut to the sports this member sees (a dict), with the book list cut to those sports.
    A member who is not the owner gets sportsbook and prediction-market price rows (a pick'em multiplier is not a single bet), clean
    both-sides pairs only, no flagged pair or its margin, no exclusion counts and no formula strings. The owner keeps everything."""
    doc = json.loads(body)
    doc["sports"] = {s: d for s, d in doc["sports"].items() if allowed.get(s)}
    doc["owner"] = bool(owner)
    if not owner:
        for d in doc["sports"].values():
            d["ev"] = [r for r in d.get("ev", []) if r.get("kind") in ("sportsbook", "prediction")]
            d["closest"] = [r for r in d.get("closest", []) if r.get("kind") in ("sportsbook", "prediction")]
            for r in d["ev"] + d["closest"]:
                r.pop("p_source", None)
            d["arbitrage"] = [r for r in d.get("arbitrage", []) if all(l.get("kind") == "sportsbook" and not l.get("flags") for l in r.get("legs", []))]
            for k in MEMBER_HIDDEN:
                d.pop(k, None)
        doc["method"] = {k: v for k, v in (doc.get("method") or {}).items() if k not in ("ev", "p_cal", "arbitrage")}
    doc["books"] = _books(doc["sports"].values(), flagged=owner)
    return doc
