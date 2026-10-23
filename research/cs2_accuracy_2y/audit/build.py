"""AU-1 (registered in REGISTER.md before running): one audit record per P1 unit - identity, settlement, duplicates, freshness
and leakage facts, joined from read-only local copies. Nothing is fitted; no production contact.

Inputs (scratch dir S = scratchpad/cs2acc):
  S/under/p1_units.json   stored P1 units export (learning p1_units, at 2026-10-23T04:29:55Z; UT-1 pull, paged)
  S/under/p1_fc.json      each unit's forecast row with decoded grader inputs (forecasts_page inputs=1; UT-1 pull, cutoff 79170)
  S/audit/fc_slim.json    every forecast row 1..80675 without inputs (slim_forecasts.py over the inventory pull)
  S/data/hltv_window.sqlite   read-only copy of the production HLTV DB (inventory, 2026-10-23T04:38Z)
  S/data/hltv_props.sqlite    the scraper's prop_grades / prop_lines (book lines; inventory, 04:48Z)
usage: python build.py <S>   ->  S/audit/units.json
"""
import collections
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

S = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\cs2acc")
SCOPE_MAPS = {"1": (1,), "12": (1, 2), "123": (1, 2, 3)}
# BO3.gg nick -> HLTV nick, found in this audit (evaluation join only - never used by grading): the live lookup misses them
EVAL_ALIASES = {"dev1ce": "device", "techno4k": "Techno"}
# BO3.gg match slugs whose HLTV match this audit identified by hand (team alias 'Sokerorg' = HLTV 'LFO 10'; FORZE Reload played
# twice that day - 14:00 vs BET-M 2399077, 17:00 vs Sokerorg 2399301). Evaluation join only.
EVAL_SLUGS = {"forze-reload-vs-sokerorg-14-10-2026": 2399301}


def ts(s):
    if not s:
        return None
    s = str(s).replace("Z", "+00:00").replace(" ", "T")
    if re.search(r"[+-]\d\d$", s):
        s += ":00"
    d = datetime.fromisoformat(s)
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def load_hltv(path, d0="2026-10-05", d1="2026-10-25"):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    q = lambda sql, a=(): c.execute(sql, a).fetchall()
    mo = {m: {"date": d, "event": e, "forfeited": f, "lan": l} for m, e, d, f, l in
          q("SELECT matchid, event, date, forfeited_maps, lan FROM match_overview WHERE date BETWEEN ? AND ?", (d0, d1))}
    ids = tuple(mo)
    ph = ",".join("?" * len(ids))
    teams = dict(q("SELECT teamid, name FROM teams"))
    players = dict(q("SELECT playerid, name FROM players"))
    by_nick = collections.defaultdict(set)
    for pid, n in players.items():
        by_nick[norm(n)].add(pid)
    maps = collections.defaultdict(dict)                     # match -> number -> {mapid, mapstatsid}
    for m, num, mapid, msid in q(f"SELECT matchid, number, mapid, mapstatsid FROM match_maps WHERE matchid IN ({ph})", ids):
        maps[m][num] = {"mapid": mapid, "msid": msid}
    score = collections.defaultdict(dict)                    # (match, mapid) -> team -> score
    for m, t, mapid, sc in q(f"SELECT matchid, teamid, mapid, score FROM matches WHERE sideid = 0 AND matchid IN ({ph})", ids):
        score[(m, mapid)][t] = sc
    ps = collections.defaultdict(dict)                       # (match, mapid) -> player -> (team, k)   [production settle path]
    for m, p, t, mapid, k in q(f"SELECT matchid, playerid, teamid, mapid, k FROM player_stats WHERE sideid = 0 AND matchid IN ({ph})", ids):
        ps[(m, mapid)][p] = (t, k)
    hs = {}
    for msid, p, k, h in q(f"SELECT mapstatsid, playerid, k, hs FROM map_headshots WHERE matchid IN ({ph})", ids):
        hs[(msid, p)] = (k, h)
    mps = {}
    for msid, p, t, k, h in q(f"SELECT mapstatsid, playerid, teamid, k, hs FROM map_player_stats WHERE sideid = 0 AND matchid IN ({ph})", ids):
        mps[(msid, p)] = (t, k, h)
    sq = {m: {"team1": a, "team2": b, "s1": s1, "s2": s2, "format": f, "status": st, "found_at": fa} for m, a, s1, b, s2, f, st, fa in
          q(f"SELECT matchid, team1, score1, team2, score2, format, status, found_at FROM scrape_queue WHERE matchid IN ({ph})", ids)}
    issues = collections.defaultdict(list)
    for m, sev, code, msg in q(f"SELECT matchid, severity, code, message FROM data_issues WHERE matchid IN ({ph})", ids):
        issues[m].append((code, msg))
    rounds = collections.defaultdict(int)
    for msid, n in q(f"SELECT mapstatsid, count(*) FROM map_rounds WHERE matchid IN ({ph}) GROUP BY mapstatsid", ids):
        rounds[msid] = n
    # every player's matches in the window (for BO3.gg-slug units and namesake / roster checks)
    pmatch = collections.defaultdict(set)
    for (m, mapid), pl in ps.items():
        for p, (t, _k) in pl.items():
            pmatch[p].add((m, t))
    mteams = collections.defaultdict(set)
    for (m, mapid), sc in score.items():
        mteams[m] |= set(sc)
    c.close()
    return dict(mteams=mteams, mo=mo, teams=teams, players=players, by_nick=by_nick, maps=maps, score=score, ps=ps, hs=hs, mps=mps, sq=sq,
                issues=issues, rounds=rounds, pmatch=pmatch)


def hltv_total(H, m, pid, scope, prop):
    """The production settlement read (hltv._SERIES_SQL: player_stats kills, map_headshots hs, scores incl. OT), per map,
    plus the map_player_stats cross-check. Returns a dict of facts."""
    mm = H["maps"].get(m, {})
    out = {"maps_played": sorted(mm), "per_map": {}, "issue": None}
    for num, mp in sorted(mm.items()):
        row = H["ps"].get((m, mp["mapid"]), {}).get(pid)
        sc = H["score"].get((m, mp["mapid"]), {})
        team = row[0] if row else None
        own = sc.get(team) if team is not None else None
        opp = next((v for t, v in sc.items() if t != team), None) if team is not None else None
        h = H["hs"].get((mp["msid"], pid))
        alt = H["mps"].get((mp["msid"], pid))
        n_players = len(H["ps"].get((m, mp["mapid"]), {}))
        out["per_map"][num] = {"k": row[1] if row else None, "hs": h[1] if h else None, "team": team,
                               "rounds": (own or 0) + (opp or 0) if own is not None and opp is not None else None,
                               "won": (own > opp) if own is not None and opp is not None else None,
                               "alt_k": alt[1] if alt else None, "alt_hs": alt[2] if alt else None, "n_players": n_players,
                               "round_rows": H["rounds"].get(mp["msid"], 0)}
    use = SCOPE_MAPS[scope]
    key = "hs" if prop == "hs" else "k"
    vals = [out["per_map"].get(n, {}).get(key) for n in use]
    alts = [out["per_map"].get(n, {}).get("alt_" + key) for n in use]
    out["total"] = sum(vals) if all(v is not None for v in vals) else None
    out["alt_total"] = sum(alts) if all(v is not None for v in alts) else None
    out["ot_maps"] = [n for n in use if (out["per_map"].get(n, {}).get("rounds") or 0) > 24]
    out["rounds_scope"] = sum(out["per_map"].get(n, {}).get("rounds") or 0 for n in use)
    return out


def main():
    P = json.load(open(S / "under" / "p1_units.json"))
    F = {r["id"]: r for r in json.load(open(S / "under" / "p1_fc.json"))["rows"]}
    slim = json.load(open(S / "audit" / "fc_slim.json"))
    by_pick = collections.defaultdict(list)
    by_src = collections.defaultdict(list)
    for r in slim:
        by_pick[r["pick_id"]].append(r)
        if r.get("q") and r["q"].get("src"):
            by_src[(r["q"].get("book"), r["q"]["src"])].append(r)
    H = load_hltv(S / "data" / "hltv_window.sqlite")
    pc = sqlite3.connect(f"file:{S / 'data' / 'hltv_props.sqlite'}?mode=ro", uri=True)
    pg = {}
    for row in pc.execute("SELECT book, line_id, start_time, player, team, opponent, market, maps, line, odds_type, status, actual, "
                          "map_values, matchid, note, updated_at FROM prop_grades"):
        pg[(row[0], row[1])] = dict(zip(("book", "line_id", "start", "player", "team", "opponent", "market", "maps", "line", "odds_type",
                                         "status", "actual", "map_values", "matchid", "note", "updated_at"), row))
    pg_by = collections.defaultdict(list)                    # (book, nick, start, market, maps) -> rows (name join fallback)
    for g in pg.values():
        pg_by[(g["book"], norm(g["player"]), str(g["start"])[:16], g["market"], g["maps"])].append(g)

    units = []
    for i, u in enumerate(P["rows"]):
        f = F.get(u["forecast_id"])
        gi = (f or {}).get("inputs") if isinstance((f or {}).get("inputs"), dict) else {}
        pf = gi.get("perf") or {}
        rs = pf.get("round_share") or {}
        op = gi.get("odds_provenance") or {}
        quote = f.get("quote") if f else None
        try:
            quote = json.loads(quote) if isinstance(quote, str) else (quote or {})
        except ValueError:
            quote = {}
        tgt = ((quote.get("anchors") or {}).get("target") or {})
        start, logged = ts(u["start"]), ts(u["logged"])
        rec = {k: u[k] for k in ("match", "p", "over", "lam", "actual", "line", "prop", "scope", "version", "revision", "units",
                                 "forecast_id", "logged", "start", "book")}
        rec["i"] = i
        rec["pick_id"] = f.get("pick_id") if f else None
        rec["perf_version"] = pf.get("version")
        pids = pf.get("hltv_player_ids") or []
        rec["pid"] = pids[0] if len(pids) == 1 else None
        rec["n_pids"] = len(pids)
        rec["team_id"] = rs.get("team_id")
        rec["opp_id"] = pf.get("hltv_opp_team") if pf.get("hltv_opp_team") is not None else rs.get("opp_id")
        rec["q_team"], rec["q_opp"] = (op.get("query") or {}).get("team"), (op.get("query") or {}).get("opponent")
        rec["hltv_error"] = pf.get("hltv_error")
        rec["flags"] = {k: gi.get(k) for k in ("is_standin", "standin_for", "on_listed_team", "roster_confirmed", "scout")}
        # ---------- identity: the HLTV match ----------
        m = int(u["match"].split(":")[1]) if u["match"].startswith("hltv:") else None
        rec["key_kind"] = "hltv" if m else "bo3gg_slug"
        if m is None and rec["pid"]:
            sday = start.date()
            cands = sorted({mm for (mm, t) in H["pmatch"].get(rec["pid"], set())
                            if mm in H["mo"] and abs((date.fromisoformat(H["mo"][mm]["date"]) - sday).days) <= 1})
            if rec["opp_id"]:
                cands = [mm for mm in cands if rec["opp_id"] in H["mteams"].get(mm, ())]
            rec["slug_candidates"] = cands
            m = cands[0] if len(cands) == 1 else EVAL_SLUGS.get(u["match"]) if EVAL_SLUGS.get(u["match"]) in cands else None
        rec["hltv_match"] = m
        mo = H["mo"].get(m) if m else None
        rec["hltv_date"] = mo["date"] if mo else None
        rec["forfeited"] = mo["forfeited"] if mo else None
        sq = H["sq"].get(m, {}) if m else {}
        rec["format"] = sq.get("format")
        rec["hltv_teams"] = sorted(H["mteams"].get(m, ())) if m else []
        rec["hltv_team_names"] = [H["teams"].get(t) for t in rec["hltv_teams"]]
        rec["data_issues"] = H["issues"].get(m, []) if m else []
        # player present? which team? (production settle reads player_stats sideid 0 by player id)
        played_team = None
        if m and rec["pid"]:
            for num, mp in H["maps"].get(m, {}).items():
                r_ = H["ps"].get((m, mp["mapid"]), {}).get(rec["pid"])
                if r_:
                    played_team = r_[0]
                    break
        rec["played"] = played_team is not None
        rec["played_team"] = played_team
        rec["played_team_name"] = H["teams"].get(played_team)
        rec["opp_in_match"] = (rec["opp_id"] in rec["hltv_teams"]) if (m and rec["opp_id"]) else None
        rec["team_in_match"] = (rec["team_id"] in rec["hltv_teams"]) if (m and rec["team_id"]) else None
        nick = H["players"].get(rec["pid"]) if rec["pid"] else None
        rec["nick"] = nick
        rec["namesakes"] = len(H["by_nick"].get(norm(nick), ())) if nick else None
        # the teammates who did play for that team (stand-in / roster check)
        if m and played_team is None and rec["team_id"]:
            mp1 = H["maps"].get(m, {}).get(1)
            rec["team_lineup"] = sorted(H["players"].get(p, p) for p, (t, k) in H["ps"].get((m, mp1["mapid"]), {}).items()
                                        if t == rec["team_id"]) if mp1 else []
        if rec["hltv_error"]:                                # BO3.gg-only player: is the nick on the HLTV scoreboard after all?
            mnick = re.search(r"player '([^']+)'", rec["hltv_error"])
            rec["bo3_nick"] = mnick.group(1) if mnick else None
            if m and rec["bo3_nick"]:
                on = [p for num, mp in H["maps"].get(m, {}).items() for p in H["ps"].get((m, mp["mapid"]), {}) if norm(H["players"].get(p)) == norm(rec["bo3_nick"])]
                rec["bo3_nick_in_hltv_match"] = sorted(set(on))
        # ---------- the HLTV player who really played (tp): the forecast's id when he is on the scoreboard, else the nick
        # (exact normalised, then prefix - diagnosis only, e.g. BO3.gg 'brain' = HLTV 'Brain47') among the match's players
        tp, tp_how = (rec["pid"], "forecast id") if rec["played"] else (None, None)
        if m and tp is None:
            names = [x for x in (nick, rec.get("bo3_nick"), EVAL_ALIASES.get(norm(rec.get("bo3_nick")))) if x]
            plist = {p for num, mp in H["maps"].get(m, {}).items() for p in H["ps"].get((m, mp["mapid"]), {})}
            for nm_ in names:
                ex = [p for p in plist if norm(H["players"].get(p)) == norm(nm_)]
                if len(ex) == 1:
                    tp, tp_how = ex[0], "nick exact" if nm_ in (nick, rec.get("bo3_nick")) else "eval alias"
                    break
            if tp is None:
                for nm_ in names:
                    pre = [p for p in plist if norm(nm_) and norm(H["players"].get(p)).startswith(norm(nm_))]
                    if len(pre) == 1:
                        tp, tp_how = pre[0], "nick prefix"
                        break
        rec["tp"], rec["tp_how"] = tp, tp_how
        rec["tp_nick"] = H["players"].get(tp) if tp else None
        # ---------- settlement ----------
        if m and tp:
            ht = hltv_total(H, m, tp, u["scope"], u["prop"])
            rec["hltv"] = {k: ht[k] for k in ("total", "alt_total", "maps_played", "ot_maps", "rounds_scope")}
            rec["hltv_per_map"] = ht["per_map"]
        # ---------- book line (scraper prop_grades) ----------
        g = pg.get((tgt.get("book") or u["book"], tgt.get("source_id"))) if tgt.get("source_id") else None
        how = "source_id" if g else None
        if g is None and nick:
            mk = {"kills": "kills", "hs": "headshots"}[u["prop"]]
            mp_ = {"1": "1", "12": "1-2", "123": "1-3"}[u["scope"]]
            cands = [x for x in pg_by.get((u["book"], norm(nick), start.strftime("%Y-%m-%d %H:%M"), mk, mp_), [])
                     if x["odds_type"] in (None, "standard")]
            if len(cands) == 1:
                g, how = cands[0], "name"
        rec["pg"] = {k: g[k] for k in ("player", "team", "opponent", "line", "status", "actual", "matchid", "note", "odds_type", "maps",
                                       "market", "updated_at")} if g else None
        rec["pg_join"] = how
        # ---------- freshness ----------
        rec["lead_min"] = (start - logged).total_seconds() / 60
        rec["quote"] = {"board_at": quote.get("board_at"), "updated_at": quote.get("updated_at"), "feed_state": tgt.get("feed_state"),
                        "src": tgt.get("source_id"), "odds_type": quote.get("feed_odds_type"),
                        "other_line": ((quote.get("anchors") or {}).get("other") or {}).get("line"),
                        "other_book": ((quote.get("anchors") or {}).get("other") or {}).get("book")} if quote else None
        hd = pf.get("hltv_data") or {}
        rec["hltv_age_min"] = max([(x or {}).get("age_min") or 0 for x in (hd.get("player_maps"), hd.get("opp_maps"))]) if hd else None
        rec["hltv_series"] = gi.get("hltv_series")
        rec["odds"] = {k: op.get(k) for k in ("retrieved_at", "quote_age_s", "selected_at", "selected", "availability", "start_utc_from_source",
                                              "canonical_start", "blip")} if op else None
        # later forecasts of the same pick and of the same book line id (line moves after the P1 forecast)
        cut = start - timedelta(minutes=30)
        same_pick = sorted(by_pick.get(rec["pick_id"], []), key=lambda r: r["id"])
        rec["pick_forecasts"] = len(same_pick)
        rec["pick_later"] = [(r["id"], r["logged_at"], r["line"], r["p_more"]) for r in same_pick if r["id"] > u["forecast_id"]]
        if tgt.get("source_id"):
            sib = sorted(by_src.get((tgt.get("book") or u["book"], tgt["source_id"]), []), key=lambda r: r["id"])
            rec["src_lines"] = [(r["id"], r["logged_at"], r["line"], r["p_more"], r["pick_id"]) for r in sib]
        # ---------- leakage ----------
        mday = start.date().toordinal()
        rec["newest_map_day"] = pf.get("newest_map_day")
        cm = pf.get("cm2_live") or {}
        ci = cm.get("inputs") or {}
        rec["cm2_day"], rec["cm2_refit"] = ci.get("day"), ci.get("refit")
        rec["match_day"] = mday
        rec["per_map_nonempty"] = bool(gi.get("per_map"))
        rec["hltv_series_as_of"] = (gi.get("hltv_series") or {}).get("as_of")
        rec["hd_as_of"] = [(x or {}).get("as_of") for x in (hd.get("player_maps"), hd.get("opp_maps"))] if hd else None
        rec["data_rev"] = f.get("data_rev") if f else None
        units.append(rec)
    out = S / "audit" / "units.json"
    json.dump({"p1_at": P.get("at"), "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "units": units},
              open(out, "w"), default=str)
    print("units", len(units), "->", out)


if __name__ == "__main__":
    main()
