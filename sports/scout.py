"""sports_scout command line (python sports_scout.py <command> ... or python -m sports <command> ...).

  leagues                         the leagues and where each one's data comes from
  slate   LEAGUE [--hours 36]     games coming up (ESPN)
  lines   LEAGUE                  the PrizePicks + Underdog board  [--platform pp|ud] [--player NAME] [--market KEY]
  sharp   LEAGUE                  Kalshi + Polymarket prop prices (+ Pinnacle where it serves this location, + The Odds
                                  API consensus when ODDS_API_KEY is set)
  edges   LEAGUE                  every pick'em line beside the sharp chance, best first
                                  [--platform pp|ud] [--min 0.55] [--limit 40] [--player NAME] [--all] [--record]
  player  LEAGUE NAME             his game log, recent averages, his lines and the chance of each side
  record  LEAGUE [LEAGUE ...]     save pre-game forecasts for every matched line  [--baseline]
  settle  [LEAGUE]                grade saved forecasts from ESPN box scores
  report  [LEAGUE]                how the saved forecasts did (hit rate, Brier, log loss, game-clustered interval)
  watch   LEAGUE [LEAGUE ...]     record every --every minutes (default 20) and settle each hour, until stopped

Results print to the screen and save to <SPORTS_DATA_DIR>/output/<league>_<command>_<date>.json.
Nothing here places a bet or changes any CS2 / LoL grade.
"""
import argparse
import json
import sys
import time
from contextlib import closing
from datetime import datetime, timezone

from . import config, edges, espn, kalshi, lines, markets, names, oddsapi, pinnacle, polymarket, projection, record

PLATFORM = {"pp": "prizepicks", "prizepicks": "prizepicks", "ud": "underdog", "underdog": "underdog"}


def _pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def _when(s):
    if not s:
        return "?"
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t.strftime("%a %H:%M") + "Z"


def _table(rows, cols):
    if not rows:
        print("  (none)")
        return
    w = [max(len(c), *(len(str(r[i])) for r in rows)) for i, c in enumerate(cols)]
    print("  " + "  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    for r in rows:
        print("  " + "  ".join(str(v).ljust(w[i]) for i, v in enumerate(r)))


def _save(league, cmd, data):
    try:
        config.OUT_DIR.mkdir(parents=True, exist_ok=True)
        p = config.OUT_DIR / f"{league}_{cmd}_{datetime.now(timezone.utc):%Y-%m-%d}.json"
        p.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        return p
    except OSError:
        return None


def sharp_quotes(lg, refresh=False):
    q, st = pinnacle.load(lg, refresh=refresh)
    k, st3 = kalshi.load(lg, refresh=refresh)
    pm, st4 = polymarket.load(lg, refresh=refresh)
    c, st2 = oddsapi.load(lg, refresh=refresh)
    return q + k + pm + c, {"pinnacle": st, "kalshi": st3, "polymarket": st4, "odds_api": st2}


def board(lg, refresh=False, injuries=True):
    offers, st = lines.load(lg, refresh=refresh)
    quotes, st2 = sharp_quotes(lg, refresh=refresh)
    inj = {}
    if injuries:
        try:
            inj = espn.injuries(lg)
        except Exception as e:                      # injuries are context only; a failure never blocks the board
            st2["espn_injuries"] = {"ok": False, "error": str(e)[:120]}
    return edges.build(lg, offers, quotes, inj), {**st, **st2}


def _status_line(st):
    bits = []
    for k, v in st.items():
        if v.get("ok"):
            n = v.get("offers", v.get("quotes"))
            bits.append(f"{k}: {n}" if n is not None else f"{k}: ok")
        else:
            bits.append(f"{k}: {v.get('error')}")
    print("  sources - " + " | ".join(bits))


def cmd_leagues(a):
    rows = [(k, v["name"], f"{v['espn'][0]}/{v['espn'][1]}", v["pp"], v["ud"], v["pin"]) for k, v in config.LEAGUES.items()]
    _table(rows, ["key", "league", "ESPN", "PrizePicks", "Underdog", "Pinnacle"])
    print(f"\n  The Odds API consensus: {'ON' if config.odds_api_key() else 'off (set ODDS_API_KEY to add it)'}")


def cmd_slate(a):
    lg = config.league(a.league)
    evs = espn.slate(lg, hours=a.hours)
    _table([(_when(e["start"]), e["short"] or e["name"], e["status"]) for e in evs], ["start", "game", "status"])
    _save(lg["key"], "slate", evs)


def cmd_lines(a):
    lg = config.league(a.league)
    offers, st = lines.load(lg, refresh=a.refresh)
    _status_line(st)
    plat = PLATFORM.get(a.platform) if a.platform else None
    rows = [o for o in offers if (not plat or o["platform"] == plat)
            and (not a.player or names.key(a.player) in o["player_key"]) and (not a.market or o["market"] == a.market)]
    rows.sort(key=lambda o: (o["start"] or "", o["player"], o["market"], o["line"]))
    _table([(o["platform"][:2].upper(), _when(o["start"]), o["player"], o["team"] or "", o["market"], o["line"],
             o["odds_type"], "/".join(s for s in ("over", "under") if o["sides"][s]["offered"]),
             " ".join(f"{s[0]}{o['sides'][s]['multiplier']}" for s in ("over", "under")
                      if o["sides"][s]["multiplier"] not in (None, 1.0)))
            for o in rows[:a.limit]], ["bk", "start", "player", "team", "market", "line", "type", "sides", "mult"])
    print(f"  {len(rows)} lines" + (f" (first {a.limit} shown)" if len(rows) > a.limit else ""))
    _save(lg["key"], "lines", rows)


def cmd_sharp(a):
    lg = config.league(a.league)
    quotes, st = sharp_quotes(lg, refresh=a.refresh)
    _status_line(st)
    rows = [q for q in quotes if not a.player or names.key(a.player) in q["player_key"]]
    rows.sort(key=lambda q: (q["start"] or "", q["player"], q["market"]))
    _table([(q["source"][:4], _when(q["start"]), q["player"], q["market"], q["line"], _pct(q["p_over"]),
             q.get("books") or "", q.get("limit") or "") for q in rows[:a.limit]],
           ["src", "start", "player", "market", "line", "P(over)", "books", "limit"])
    print(f"  {len(rows)} sharp prices")
    _save(lg["key"], "sharp", rows)


def _print_edges(rows):
    out = []
    for r in rows:
        s = r["sharp"]
        mult = f"x{r['multiplier']:g}" if r.get("multiplier") not in (None, 1.0) else ""
        lines_ = sorted(x[0] for x in s.get("rungs") or [[s["line"]]])
        out.append((r["platform"][:2].upper(), _when(r["start"]), r["player"], r["market"], r["line"],
                    (r["odds_type"] or "")[:8], f"{r['side'].upper()}{mult}", _pct(r["chance"]),
                    _pct(r["eff"]) if r.get("eff") is not None else "",
                    f"{s['source'][:3]} " + "|".join(f"{x:g}" for x in lines_),
                    f"{s['gap']:+g}" if s["gap"] else "0", (r.get("injury") or "")[:22]))
    _table(out, ["bk", "start", "player", "market", "line", "type", "side", "chance", "UD eff", "sharp", "gap",
                 "injury"])


def cmd_edges(a):
    lg = config.league(a.league)
    rows, st = board(lg, refresh=a.refresh)
    _status_line(st)
    if a.player:
        rows = [r for r in rows if names.key(a.player) in r["player_key"]]
    ranked = edges.rank(rows, platform=PLATFORM.get(a.platform) if a.platform else None, show_all=a.all,
                        min_chance=a.min)
    matched = sum(1 for r in rows if r.get("chance") is not None)
    print(f"  {len(rows)} lines, {matched} with a sharp price, {len(ranked)} shown"
          f"{'' if a.all else ' (standard lines, line gaps within the fitted range; --all for every line)'}")
    print(f"  UD eff = chance x payout modifier; a 2-pick Underdog standard entry breaks even at {_pct(edges.UD_REF)} "
          "per pick. PrizePicks: payout table not public - chance only.\n")
    _print_edges(ranked[:a.limit])
    if a.record:
        with closing(record.connect()) as con:
            print(f"\n  recorded {record.save(con, rows)} new forecasts")
    _save(lg["key"], "edges", {"status": st, "rows": rows})


def _baselines(lg, rows):
    """{offer_id: baseline} for matched rows (one ESPN look-up per player, cached 6 h)."""
    out, logs = {}, {}
    for r in rows:
        if r.get("chance") is None:
            continue
        pk = r["player_key"]
        if pk not in logs:
            aths = [x for x in espn.search_athlete(lg, r["player"]) if names.key(x["name"]) == pk]
            try:
                logs[pk] = espn.gamelog(lg, aths[0]["id"]) if len(aths) == 1 else None
            except Exception:
                logs[pk] = None
        if logs[pk]:
            out[r["offer_id"]] = projection.baseline(logs[pk], lg["family"], r["market"], lg["half_life_games"],
                                                     r["line"])
    return out


def cmd_record(a):
    for key in a.leagues:
        lg = config.league(key)
        rows, st = board(lg, refresh=True)
        base = _baselines(lg, rows) if a.baseline else None
        with closing(record.connect()) as con:
            n = record.save(con, rows, base)
        print(f"  {lg['name']}: {n} new forecasts ({sum(1 for r in rows if r.get('chance') is not None)} matched lines)")
        _status_line(st)


def cmd_player(a):
    lg = config.league(a.league)
    aths = espn.search_athlete(lg, a.name)
    if not aths:
        raise SystemExit(f"no {lg['name']} player found for '{a.name}' on ESPN")
    ath = aths[0]
    games = espn.gamelog(lg, ath["id"], refresh=a.refresh)
    print(f"  {ath['name']} - {ath.get('team') or ''} (ESPN id {ath['id']}), {len(games)} games in the log\n")
    fam = lg["family"]
    shown = [g for g in projection.played_games(games, fam)][:10]
    cols = [m for m in markets.MARKETS[fam] if any(markets.value(fam, m, g["parts"]) for g in shown)]
    _table([(g["date"][:10] if g["date"] else "?", ("vs " if g["home"] else "@ ") + (g["opponent"] or "?"),
             *[f"{markets.value(fam, m, g['parts']):g}" for m in cols]) for g in shown], ["date", "opp", *cols])
    rows, st = board(lg, refresh=a.refresh)
    mine = [r for r in rows if r["player_key"] == names.key(ath["name"])]
    print(f"\n  his lines ({len(mine)}):")
    out = []
    for r in sorted(mine, key=lambda r: (r["market"], r["platform"], r["line"])):
        b = projection.baseline(games, fam, r["market"], lg["half_life_games"], r["line"]) or {}
        side = r.get("side") or "over"
        bc = b.get("p_over") if side == "over" else b.get("p_under")
        dec = 1 - (b.get("p_push") or 0)
        out.append((r["platform"][:2].upper(), r["market"], r["line"], r["odds_type"], side.upper(),
                    _pct(r.get("chance")), f"{r['sharp']['source'][:3]} {r['sharp']['line']:g}" if r.get("sharp") else
                    (r.get("reason") or "")[:30], b.get("mean", "-"),
                    _pct(bc / dec) if bc is not None and dec > 0 else "-",
                    f"{b.get('over_last10', '-')}/{b.get('n_last10', '-')}"))
    _table(out, ["bk", "market", "line", "type", "side", "sharp chance", "sharp", "recent avg", "history chance",
                 "over L10"])
    print("\n  history chance = his recency-weighted average with the measured spread; display only (not measured yet)."
          if mine else "")
    _save(lg["key"], f"player_{names.key(ath['name']).replace(' ', '_')}", {"athlete": ath, "games": games, "lines": mine})


def cmd_settle(a):
    with closing(record.connect()) as con:
        print(f"  {record.settle(con, a.league)}")


def cmd_report(a):
    with closing(record.connect()) as con:
        rep = record.report(con, a.league)
    al = rep["all"]
    print(f"  model {rep['model']}: {al['props']} settled props"
          + (f", {al.get('games')} games, hit rate {_pct(al.get('hit_rate'))} (95% game-clustered "
             f"{_pct((al.get('hit_rate_95') or (None, None))[0])}-{_pct((al.get('hit_rate_95') or (None, None))[1])}),"
             f" mean forecast {_pct(al.get('mean_chance'))}, Brier {al.get('brier', 0):.4f}, log loss "
             f"{al.get('log_loss', 0):.4f}" if al["props"] else "")
          + f"; excluded {al['excluded']}, unsettled {al['unsettled']}")
    for name in ("by_band", "by_platform", "by_league", "by_market"):
        rows = [(k, v["props"], _pct(v.get("hit_rate")), _pct(v.get("mean_chance")),
                 f"{v['brier']:.4f}" if v.get("brier") is not None else "-") for k, v in rep[name].items()]
        if rows:
            print(f"\n  {name[3:]}:")
            _table(rows, ["group", "props", "hit rate", "mean forecast", "Brier"])
    if al.get("same_rows_with_baseline"):
        s = al["same_rows_with_baseline"]
        print(f"\n  same {s['props']} props: sharp Brier {s['sharp']['brier']:.4f} vs history baseline "
              f"{s['baseline']['brier']:.4f}")
    _save(a.league or "all", "report", rep)


def cmd_watch(a):
    last_settle = 0.0
    while True:
        try:
            cmd_record(argparse.Namespace(leagues=a.leagues, baseline=a.baseline))
            if time.time() - last_settle > 3600:
                with closing(record.connect()) as con:
                    for key in a.leagues:
                        print(f"  settle {key}: {record.settle(con, config.league(key)['key'])}")
                last_settle = time.time()
        except Exception as e:                       # one bad cycle never stops the loop
            print(f"  cycle failed: {e}", file=sys.stderr)
        sys.stdout.flush()
        time.sleep(max(60, a.every * 60))


def main(argv=None):
    p = argparse.ArgumentParser(prog="sports_scout", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("leagues")
    s = sub.add_parser("slate")
    s.add_argument("league")
    s.add_argument("--hours", type=int, default=36)
    for name in ("lines", "sharp", "edges"):
        s = sub.add_parser(name)
        s.add_argument("league")
        s.add_argument("--player")
        s.add_argument("--limit", type=int, default=60)
        s.add_argument("--refresh", action="store_true")
        if name in ("lines", "edges"):
            s.add_argument("--platform", choices=sorted(PLATFORM))
        if name == "lines":
            s.add_argument("--market")
        if name == "edges":
            s.add_argument("--min", type=float, default=0.0)
            s.add_argument("--all", action="store_true")
            s.add_argument("--record", action="store_true")
    s = sub.add_parser("player")
    s.add_argument("league")
    s.add_argument("name")
    s.add_argument("--refresh", action="store_true")
    for name in ("record", "watch"):
        s = sub.add_parser(name)
        s.add_argument("leagues", nargs="+")
        s.add_argument("--baseline", action="store_true", help="also log the history baseline (ESPN game logs)")
        if name == "watch":
            s.add_argument("--every", type=int, default=20)
    for name in ("settle", "report"):
        s = sub.add_parser(name)
        s.add_argument("league", nargs="?")
    a = p.parse_args(argv)
    if getattr(a, "league", None) and a.cmd in ("settle", "report"):
        a.league = config.league(a.league)["key"]
    {"leagues": cmd_leagues, "slate": cmd_slate, "lines": cmd_lines, "sharp": cmd_sharp, "edges": cmd_edges,
     "player": cmd_player, "record": cmd_record, "settle": cmd_settle, "report": cmd_report,
     "watch": cmd_watch}[a.cmd](a)
