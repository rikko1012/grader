"""Shared fixture data for the hub card factor panels (tests/test_card_factors*.py): a graded CS2 result shaped as cs2_scout.grade returns it
and a stored map veto. No network and no production data."""


def classic_res(nick, team, opp):
    """A graded result shaped as cs2_scout.grade returns it (the fields the classic card prints)."""
    return {"player_block": {"player": {"nick": nick}, "overall": {"window": "last_6_months", "maps_played": 41, "rating": 1.12, "dpr": 0.64, "adr": 81.2, "kpr": 0.74,
                                                                 "form_windows": {"3m": {"kpr": 0.761, "hs_pct": 48.2, "maps": 18}, "6m": {"kpr": 0.742, "hs_pct": 47.0, "maps": 33},
                                                                                  "12m": {"kpr": 0.731, "hs_pct": 46.1, "maps": 61}}, "flags": ["questionable sample"]},
                             "kast": 73.1, "recent_form": {"kpr_recent": 0.78, "trend_pct_vs_window": 4, "env": "lan"},
                             "per_map": [{"map": "de_mirage", "kpr": 0.79, "maps_played": 12}, {"map": "de_inferno", "kpr": 0.70, "maps_played": 9}],
                             "teammate_ratings": {"alpha": 1.05, "bravo": 0.98, "benchy": 1.30, "delta": 0.91}, "benched": ["benchy"], "team_kill_share": 0.231,
                             "roster_confirmed": True, "awp_note": "Rifler (HLTV weapon stats)", "trade_pr": 0.121, "opening": {"kills_per_round": 0.102, "duel_win": 0.54},
                             "days_since_last_map": 4,
                             "_rank_band": {"opp_rank": 21, "band": 10, "maps": 14, "kpr": 0.812, "kpr_all": 0.74, "ratio": 1.097, "teams": ["Kestrel Five", "Vanta"], "source": "HLTV 12 months"}},
            "hltv": {"found": True, "window": "3 months", "maps": 22, "rating": 1.15, "ct_rating": 1.18, "t_rating": 1.11, "round_swing": 2.4, "adr": 82.0, "kast": 74.0, "kd": 1.12,
                     "newest_map": "2026-11-05"},
            "hltv_context": {"found": True, "eco": {"ekd": 1.10, "eadr": 80.1, "ekast": 73.5}, "ranking": {"rank": 14, "change": 3}, "opp_ranking": {"rank": 21, "change": -2},
                             "environment": {"lan": {"maps": 40, "kpr": 0.77}, "online": {"maps": 21, "kpr": 0.69}, "tonight": "lan", "event": "Meridian Cup", "ratio": 1.03},
                             "prop_maps": [{"map": "Mirage", "maps": 8, "kpr": 0.80, "rating": 1.2, "adr": 84.0}],
                             "h2h": {"wins": 2, "losses": 1, "map_win_rates": [{"map": "Mirage", "team": {"played": 3, "won": 2}, "opponent": {"played": 3, "won": 1}}]},
                             "last5": [{"date": "2026-11-05", "map": "Mirage", "number": 1, "opponent": opp, "score": "13-9", "kd": "21-14", "rating": 1.31}]},
            "faceit_identity": {"show_to_members": True}, "faceit": {"nickname": nick, "level": 10, "elo": 3100, "recent_n": 20, "avg_kills": 21.0, "kpr": 0.81, "avg_hs_pct": 51},
            "csapi": {"found": True, "window": "3 months", "maps": 20, "team": team, "vrs_rank": 14, "on_roster": True, "kills_per_map": 18.1, "rating": 1.1, "swing": 1.2,
                      "adr": 80, "kast": 72, "ct": {"k": 9.2}, "t": {"k": 8.9}, "newest_match": "2026-11-05"},
            "next_match": {"event": "Meridian Cup", "bo": 3, "maps": ["de_mirage"], "opponent": opp},
            "grade": {"projection_source": "perf-cm2", "market_quality": "HARD", "detail": {"p_win": 0.612}}}


VETO = {"state": "partial", "at": "2026-11-09T10:00:00Z", "confirmed": [{"slot": "Map 1", "name": "Mirage", "by": "Halcyon"}],
        "slots": [{"slot": "Map 2", "top": [[0, "Inferno", 0.41], [0, "Nuke", 0.33]], "other": 0.26}], "facts": ["Halcyon picked Mirage in 4 of their last 5 BO3s"]}


def rich_player(base, stat, fids, line):
    """A sports player file with what the classic player page reads (forecasts with their ladder, every book quote, Kalshi rungs, the
    matchup engine by stat) - the size of a real one, for the load harness."""
    p = dict(base)
    p["forecasts"] = {fid: {"line": line, "projection_mean": line - 0.9, "forecast_at": "2026-11-09T08:00:00Z", "model_version": "sm-2", "reason_codes": ["statistical_only"],
                            "contributions": [], "detail": {"interval": [line - 7, line + 6], "median": line - 1,
                                                            "factors": [{"label": f"Input {k}", "value": 30 + k, "definition": "x" * 40} for k in range(8)]}} for fid in fids}
    p["forecast_detail"] = {fid: {"pmf": {"bins": [[int(line) - 15 + i, 1, 0.03] for i in range(30)]},
                                  "ladder": [{"line": line + d, "p_over": 0.5, "p_under": 0.5, "p_push": 0.0} for d in range(-5, 6)]} for fid in fids}
    p["markets"] = [{"stat": stat, "scope": "full_game", "quotes": [
        {"platform": b, "line": line + (k % 3), "variant": "standard", "last_seen_at": "2026-11-09T09:58:00Z",
         "sides": [{"side": "over", "offered": True, "price": 1.87, "price_format": "decimal"}, {"side": "under", "offered": True, "price": 1.95, "price_format": "decimal"}]}
        for k, b in enumerate(("draftkings", "fanduel", "betmgm", "caesars", "pinnacle", "bet365"))],
        "alternates": [], "kalshi": [{"line": line + d, "sides": [{"bid": 0.4, "ask": 0.45}], "liquidity": {"volume": 1000, "open_interest": 300}, "last_seen_at": "2026-11-09T09:58:00Z"} for d in range(-3, 4)]}]
    f = dict(p.get("factors") or {"stats": [], "context": [], "not_held": []})
    f["engine"] = {"label": "Matchup engine", "as_of": "2026-11-09T08:00:00Z", "rows": [],
                   "by_stat": {stat: [{"key": f"k{k}", "group": "Matchup", "label": f"Value {k}", "value": 1.0 + k / 100, "n": 12, "source": "ESPN"} for k in range(30)]}}
    p["factors"] = f
    return p


def rich_history(stat, n=82):
    from datetime import datetime, timedelta, timezone
    t = datetime.now(timezone.utc) - timedelta(days=2)
    return {"version": "ui-snap-1", "league": "nba", "player_id": "x", "family": "basketball",
            "games": [{"event_id": f"e{i}", "start_utc": (t - timedelta(days=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "season": 2027, "season_type": "regular",
                       "team": {"abbr": "NY"}, "opponent": {"team_id": "t", "abbr": "BOS"}, "home": bool(i % 2), "participation": "played", "parts": {"min": 30 + i % 6},
                       "values": {stat: float(15 + i % 11)}, "source": ["espn"]} for i in range(n)]}
