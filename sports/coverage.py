"""Data coverage: what each source gives, under what terms, how it fails - plus live counts from the canonical store
and a verification of a sample against ESPN results (owner brief 4).

  SOURCES                  static documentation per source (fields, leagues/seasons, depth, cadence, usage terms,
                           failure behaviour, rate limits) - every claim dated; "unverified" where we could not read it
  report(con)              counts from <SPORTS_DATA_DIR>/canonical.db: offers by platform/league/stat/capability/variant,
                           side availability, resolution rates, quarantine reasons, events, observations, settlements
  verify_kalshi_vs_espn()  Kalshi's settled value (expiration_value) for each settled player market on a date vs the
                           same player's stat in ESPN's box score -> agreement counts and every disagreement
Admin JSON: GET /api/sports/admin/coverage (SPORTS_API on + admin token) returns report(); CLI:
python -m sports.worker coverage [--json].
"""
from __future__ import annotations

from collections import Counter, defaultdict

from . import capabilities, markets, names, net, store

CHECKED = "2026-10-23"
SOURCES = {
    "espn": {
        "module": "sports/espn.py (+ adapters.espn_events / espn_box / espn_roster / espn_injuries)",
        "gives": "schedule + start times + status (scoreboard), box scores incl. overtime (summary), player game logs "
                 "(current + earlier seasons via ?season=), rosters with ESPN athlete ids and roster status, injury "
                 "report status/date (no editorial text stored)",
        "leagues": "NBA, NFL (+ WNBA, NCAA in the league table)",
        "depth": "game logs: this season and earlier seasons per athlete; scoreboard: any single date",
        "cadence": "scoreboard live; box score final minutes after the game; injuries ~hourly (observed); rosters daily",
        "access": "public JSON, no key, no login",
        "usage_terms": "UNOFFICIAL, undocumented endpoints (documented by pseudo-r/Public-ESPN-API, MIT). No licence is "
                       "granted for third-party product use; ESPN/Disney terms of use govern site use. Box-score facts "
                       "are used as settlement/stat inputs; ESPN news text is not stored. Legal review needed before "
                       "member display of ESPN-derived data (blocker, not resolved here).",
        "failure": "2026-10-23: a date RANGE (dates=A-B) answers HTTP 400 for NBA and NFL - read day by day "
                   "(espn.scoreboard_raw). Errors leave settlement pending; a failed read never settles or voids.",
        "rate_limit": "none published; paced 2 requests/s per host (net.py), cached (final box scores forever)",
    },
    "prizepicks": {
        "module": "sports/lines.py fetch + adapters.pp_offers",
        "gives": "board lines: player, stat, line, odds_type standard/goblin/demon, allowed sides, promo/flash-sale "
                 "flags, start time, projection updated_at",
        "leagues": "NBA (league 7), NFL (9), WNBA, CBB, CFB", "depth": "current board only (no history)",
        "cadence": "board refresh read every 3-10 min; lines move intra-day",
        "access": "partner-api.prizepicks.com public JSON (the same reader the CS2/LoL boards use)",
        "usage_terms": "no published API terms for third parties; same posture as the live esports readers. Payout "
                       "tables are in-app only: every PrizePicks payout / break-even is UNVERIFIED.",
        "failure": "a failed read keeps the previous offers (no 'removed' revisions written); 429/5xx retried",
        "rate_limit": "none published; paced 1 request / 2 s",
    },
    "underdog": {
        "module": "sports/lines.py fetch + adapters.ud_offers",
        "gives": "over_under_lines: player, stat, line, line_type, per-side status + payout modifier (documented) + "
                 "per-side updated_at; undocumented decimal prices kept only as labelled extras",
        "leagues": "NBA, NFL, WNBA, CBB, CFB", "depth": "current board only",
        "cadence": "read every 3-10 min",
        "access": "api.underdogfantasy.com public JSON",
        "usage_terms": "no published API terms for third parties; payout tables published (help 13780101, read "
                       "2026-10-23); how modifiers combine is not published (product rule assumed, labelled)",
        "failure": "as PrizePicks", "rate_limit": "none published; paced 1 request/s",
    },
    "kalshi": {
        "module": "sports/kalshi.py fetch + adapters.kalshi_offers",
        "gives": "player-prop ladders 'N+' per stat: YES/NO bid & ask with sizes, last price, volume, 24 h volume, "
                 "open interest, rules text, settlement sources, settled value (expiration_value) after the game",
        "leagues": "NBA 10 series, WNBA 4, NFL 10 (no college)",
        "depth": "open markets + settled markets (historical endpoints for older ones)",
        "cadence": "quotes move continuously; markets stay ACTIVE during the game (in-play) until early close",
        "access": "api.elections.kalshi.com/trade-api/v2 public market data, no key",
        "usage_terms": "Kalshi developer agreement / data terms NOT read (kalshi.com answered 429): UNVERIFIED for "
                       "display in a paid product. Fees: series fee_type 'quadratic', fee_multiplier 1.",
        "failure": "per-series failure recorded; offers of a failed read are not marked removed",
        "rate_limit": "token-bucket limits documented for authenticated keys; public reads paced 1 request / 1.5 s",
        "times": "market updated_time does not move with quotes (source time UNKNOWN); occurrence / expected "
                 "expiration = start + ~3 h, never the start",
        "settlement": "NBA: 'the Governing League' (nba.com) then ESPN; NFL: the governing league only. DNP / never "
                      "enters: fair-price settlement (not void, not No). Postponed > 48 h: fair price.",
    },
    "oddsapi": {
        "module": "sports/oddsapi.py + adapters.oddsapi_offers",
        "gives": "US sportsbook player-prop prices (decimal) per book with last_update",
        "leagues": "NBA, NFL, WNBA, NCAA", "depth": "current (historical is a paid add-on)",
        "cadence": "per request; 1 credit per market per region per event",
        "access": "ODDS_API_KEY required - NOT SET here, so no counts",
        "usage_terms": "the-odds-api.com terms (updated 2026-08-31): display in a commercial app allowed when the "
                       "data is not the primary product; no resale / redistribution as a data product; model "
                       "training allowed; storage allowed",
        "failure": "off without a key; errors reported with the key masked", "rate_limit": "credit budget per plan",
    },
    "pinnacle": {
        "module": "sports/pinnacle.py (data branch)", "gives": "props with limits - from non-US locations only",
        "access": "guest API refuses US locations (403 'Access from United States is prohibited'): never worked "
                  "around; not wrapped by an adapter",
        "usage_terms": "unofficial guest API; no terms for third-party use - not used",
    },
    "nflverse": {
        "module": "sports/fit_dispersion.py input (offline research only)",
        "gives": "weekly player stats CSV (2025 season used for dispersion.json)",
        "usage_terms": "nflverse-data repository licence CC-BY-4.0 (attribution required)",
    },
    "sportsdataverse": {
        "module": "sports/fit_dispersion.py input (offline research only)",
        "gives": "ESPN NBA player box scores CSV (2025-26) - ESPN-derived data; ESPN terms apply to the data",
    },
}


def report(con):
    offers = [o for _, o in store.offers(con, limit=500000)]
    by = defaultdict(Counter)
    for o in offers:
        k = f"{o.platform.split(':')[0]}:{o.league}"
        cap = capabilities.classify(o.league, o.stat, o.source_stat)
        ok, why = capabilities.gate(o)
        by[k]["offers"] += 1
        by[k][f"capability:{cap.status}"] += 1
        by[k]["gradable_now"] += int(ok)
        by[k][f"variant:{o.variant}"] += 1
        by[k][f"scope:{o.scope}"] += 1
        by[k]["promotional"] += int(o.promotional)
        by[k][f"availability:{o.availability}"] += 1
        by[k]["event_resolved"] += int(o.event_id is not None)
        by[k]["player_resolved"] += int(o.player_id is not None)
        sides = o.offered_sides()
        by[k]["sides:" + ("both" if len(sides) == 2 else (sides[0] + "_only" if sides else "none"))] += 1
        by[k]["source_time_unknown"] += int(o.source_time_unknown)
        if o.stat is None:
            by[k][f"unknown_stat:{o.source_stat}"] += 1
        else:
            by[k][f"stat:{o.stat}"] += 1
    kinds = Counter()
    for r in con.execute("SELECT kind, COUNT(*) n, COUNT(DISTINCT source || '|' || source_key) ids FROM records "
                         "GROUP BY kind").fetchall():
        kinds[r["kind"]] = {"rows": r["n"], "identities": r["ids"]}
    settled = Counter(s.status for _, s in store.settlements(con))
    return {"checked": CHECKED, "store": {"path": str(store.db_path()), "mb": round(store.size_mb(), 2),
                                          "records": dict(kinds)},
            "offers": {k: dict(sorted(v.items())) for k, v in sorted(by.items())},
            "quarantine": store.quarantine_counts(con), "settlements": dict(settled),
            "capabilities": capabilities.table(), "sources": SOURCES}


def render_text(rep):
    lines = [f"sports coverage (store {rep['store']['path']}, {rep['store']['mb']} MB)", ""]
    for k, v in rep["offers"].items():
        n = v.get("offers", 0)
        lines.append(f"{k:22} offers {n:6}  gradable {v.get('gradable_now', 0):6}  event resolved "
                     f"{v.get('event_resolved', 0):6}  player resolved {v.get('player_resolved', 0):6}  "
                     f"both sides {v.get('sides:both', 0):6}")
    lines.append("")
    for q in rep["quarantine"][:30]:
        lines.append(f"quarantine {q['kind']:8} {q['source']:10} {q['n']:6}  {q['reason']}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------- verification
def _kalshi_settled(series, day_code):
    from . import kalshi
    out, cursor = [], None
    for _ in range(20):
        params = {"series_ticker": series, "status": "settled", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        body = net.get_json(f"{kalshi.API}/markets", params=params)
        out += [m for m in body.get("markets") or [] if f"-{day_code}" in (m.get("event_ticker") or "")]
        cursor = body.get("cursor")
        if not cursor:
            break
    return out


def verify_kalshi_vs_espn(lg, date_iso, series_filter=None):
    """Settled Kalshi player markets on one US date vs ESPN box scores for the same games and players."""
    from datetime import date
    from . import espn, identity, kalshi
    d = date.fromisoformat(date_iso)
    day_code = f"{d:%y}{d.strftime('%b').upper()}{d:%d}"
    fam = lg["family"]
    events = espn.scoreboard(lg, d, d)
    summaries = {}
    for e in events:
        if e["completed"]:
            summaries[e["id"]] = (e, espn.summary(lg, e["id"]))
    teams = identity.Teams(lg["key"], [x for e in events for x in (
        {"id": (e["home"] or {}).get("id"), "abbr": (e["home"] or {}).get("abbr")},
        {"id": (e["away"] or {}).get("id"), "abbr": (e["away"] or {}).get("abbr")})])
    by_pair = {}
    for eid, (e, s) in summaries.items():
        by_pair[frozenset((teams.resolve((e["home"] or {}).get("abbr")),
                           teams.resolve((e["away"] or {}).get("abbr"))))] = s
    res = Counter()
    diffs, scalar = [], []
    for series, market in (kalshi.SERIES.get(lg["key"]) or {}).items():
        if series_filter and series not in series_filter:
            continue
        spec = markets.market(fam, market)
        for m in _kalshi_settled(series, day_code):
            res["markets"] += 1
            title = (m.get("yes_sub_title") or "")
            name = title.split(":")[0].strip()
            ev_part = m["event_ticker"].split("-")[1][7:]       # 'CHIMIL'
            pair = None
            for k in range(2, 5):
                a, h = ev_part[:k], ev_part[k:]
                ta, th = teams.resolve(a), teams.resolve(h)
                if ta and th:
                    pair = frozenset((ta, th))
                    break
            s = by_pair.get(pair)
            if s is None:
                res["no_espn_final_game"] += 1
                continue
            rows = s["players"].get(names.key(name)) or []
            if len(rows) != 1:
                res["player_not_in_box" if not rows else "ambiguous_name"] += 1
                continue
            row = rows[0]
            if m.get("result") == "scalar":
                scalar.append({"ticker": m["ticker"], "espn_played": row["played"]})
                res["kalshi_fair_price"] += 1
                continue
            ev = markets.value(fam, market, row["parts"]) if spec and row["played"] else None
            if ev is None or m.get("result") not in ("yes", "no"):
                res["espn_value_missing" if ev is None else "kalshi_result_missing"] += 1
                continue
            res["compared"] += 1
            strike = float(m.get("floor_strike"))
            k_yes = m.get("result") == "yes"
            if (ev > strike) == k_yes:
                res["result_agree"] += 1
            else:
                res["result_disagree"] += 1
                diffs.append({"ticker": m["ticker"], "kalshi_result": m.get("result"), "espn": ev, "strike": strike})
            # expiration_value: NBA gives the number (at EARLY CLOSE for a Yes - 'closes early if the event occurs' -
            # so only a No ran to the final whistle); NFL gives the stat NAME. Compare numbers only for No results.
            try:
                kv = float(m.get("expiration_value"))
            except (TypeError, ValueError):
                kv = None
            if kv is not None and not k_yes:
                res["final_value_compared"] += 1
                if abs(kv - ev) < 1e-9:
                    res["final_value_agree"] += 1
                else:
                    diffs.append({"ticker": m["ticker"], "kalshi_value": kv, "espn": ev})
    return {"date": date_iso, "league": lg["key"], "counts": dict(res), "value_disagreements": diffs[:50],
            "kalshi_fair_price_markets": scalar[:50]}
