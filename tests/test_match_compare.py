"""Match player projection comparison (server.match_compare): both teams, our projections, lines only where offered,
same match and maps only, honest team totals."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server

START = "2026-10-10T13:45:00.000+00:00"


def _row(nick, team, prop, line, side="More", p=0.6, scope="12", start="2026-10-10T13:45:00Z"):
    return {"nick": nick, "player": nick, "team": team, "team_name": team, "opponent": "x", "prop": prop, "scope": scope, "line": line,
            "side": side, "p": p, "grade": "B", "units": 0.5, "proj": line + 1, "start": start, "graded_at": "2026-10-09T19:00:00+00:00",
            "dstat": ["ok", ""], "source": "HLTV", "state": "GRADE", "reason": "why " + nick}


def test_match_compare(monkeypatch):
    rows = [_row("Wicadia", "Aurora", "hs", 15.5), _row("Wicadia", "Aurora", "kills", 29.5, side="Less"),
            _row("ZywOo", "Vitality", "kills", 34.5),
            _row("ZywOo", "Vitality", "hs", 14.5, scope="1"),                       # other maps: never mixed in
            _row("donk", "Spirit", "kills", 30.5),                                   # other match
            _row("ropz", "Vitality", "kills", 25.5, start="2026-10-12T13:45:00Z")]   # other date
    monkeypatch.setattr(server, "board_payload", lambda *a, **k: {"rows": rows})

    class B:
        def team(self, slug):
            return {"aurora": {"players": [{"nickname": n} for n in ("Wicadia", "XANTARES", "woxic", "jottAAA", "MAJ3R")]},
                    "vitality": {"players": [{"nickname": n} for n in ("ZywOo", "apEX", "flameZ", "mezii")] + [{"nickname": "XTQZZZ", "is_coach": True}]}}[slug]
        def close(self): pass
    monkeypatch.setattr(server, "BO3", B)
    import perf_live
    monkeypatch.setattr(perf_live, "projection", lambda nick, team, opp, lan, scope, prop, day=None: {"lambda": 20.0 if prop == "kills" else 10.0, "data_quality": "ok"}
                        if nick != "MAJ3R" else {"error": "player not found in HLTV"})
    out = server.match_compare("aurora", "vitality", "Aurora", "Vitality", START, "12")
    teams = {t["team"]: t for t in out["teams"]}
    assert set(teams) == {"Aurora", "Vitality"}
    au = {p["nick"]: p for p in teams["Aurora"]["players"]}; vi = {p["nick"]: p for p in teams["Vitality"]["players"]}
    assert au["Wicadia"]["hs"]["reason"] == "why Wicadia" and au["Wicadia"]["hs"]["line"] == 15.5 and au["Wicadia"]["kills"]["side"] == "Less"            # offered lines with picks
    assert au["XANTARES"]["kills"] == {"proj": 20.0, "line": None, "no_line": True, "pending": False, "source": "HLTV", "dq": "ok"}   # projection, no pick
    assert "side" not in au["XANTARES"]["hs"]                                                          # no invented pick
    assert au["MAJ3R"]["kills"]["proj"] is None and au["MAJ3R"]["kills"]["no_line"]
    assert vi["ZywOo"]["kills"]["line"] == 34.5 and vi["ZywOo"]["hs"]["line"] is None                 # Map 1 line kept out
    assert "donk" not in au and "donk" not in vi and "ropz" not in vi and "XTQZZZ" not in vi          # other match / coach
    assert teams["Aurora"]["summary"]["complete"] is False and teams["Aurora"]["summary"]["kills"] is None   # MAJ3R has no projection
    assert teams["Vitality"]["summary"]["roster"] == 4 and teams["Vitality"]["summary"]["basis"] is None       # 4 listed: lineup unclear


def test_team_total_uses_the_five_who_play(monkeypatch):
    lineup = ["a1", "a2", "a3", "a4", "a5"]
    rows = [_row(n, "Aurora", "kills", 20.5) for n in lineup]
    monkeypatch.setattr(server, "board_payload", lambda *a, **k: {"rows": rows})
    class B:
        def team(self, slug):
            return {"players": [{"nickname": n} for n in lineup + ["sub6"]]} if slug == "aurora" else {"players": []}
        def close(self): pass
    monkeypatch.setattr(server, "BO3", B)
    import perf_live
    monkeypatch.setattr(perf_live, "projection", lambda *a: {"lambda": 10.0, "data_quality": "ok"})
    t = next(x for x in server.match_compare("aurora", "vit", "Aurora", "Vitality", START, "12")["teams"] if x["team"] == "Aurora")
    assert t["summary"]["basis"] == "Underdog lineup" and t["summary"]["lineup"] == lineup and t["summary"]["complete"]
    assert t["summary"]["kills"] == 5 * 21.5                                         # the 6-man roster page's sub is not added



def test_benched_players_are_left_out(monkeypatch):
    monkeypatch.setattr(server, "board_payload", lambda *a, **k: {"rows": []})
    class B:
        def team(self, slug):
            if slug != "aurora-gaming":
                return {"players": []}
            return {"players": [{"nickname": n, "status": 1} for n in ("XANTARES", "woxic", "Jimpphat", "kyxsan", "Wicadia")]
                    + [{"nickname": "MAJ3R", "status": 2}, {"nickname": "ashhh", "status": 1, "is_coach": True}]}
        def close(self): pass
    monkeypatch.setattr(server, "BO3", B)
    import perf_live
    monkeypatch.setattr(perf_live, "projection", lambda *a: {"lambda": 10.0, "data_quality": "ok"})
    t = next(x for x in server.match_compare("aurora-gaming", "vitality", "Aurora", "Vitality", START, "12")["teams"] if x["team"] == "Aurora")
    assert sorted(p["nick"] for p in t["players"]) == ["Jimpphat", "Wicadia", "XANTARES", "kyxsan", "woxic"]   # MAJ3R (benched) and the coach out
    assert t["summary"]["basis"] == "roster page" and t["summary"]["complete"]                               # 5 active -> a team total


def test_prizepicks_table_uses_its_standard_lines(monkeypatch):
    ud = [_row("Wicadia", "Aurora", "kills", 29.5)]
    pp = [dict(_row("Wicadia", "Aurora", "kills", 28.5), book="prizepicks", odds_type="standard"),
          dict(_row("Wicadia", "Aurora", "hs", 12.5), book="prizepicks", odds_type="goblin", line_type="goblin")]
    seen = []
    monkeypatch.setattr(server, "board_payload", lambda book="underdog": seen.append(book) or {"rows": pp if book == "prizepicks" else ud})
    class B:
        def team(self, slug): return {"players": [{"nickname": "Wicadia"}]} if slug == "aurora" else {"players": []}
        def close(self): pass
    monkeypatch.setattr(server, "BO3", B)
    import perf_live
    monkeypatch.setattr(perf_live, "projection", lambda *a, **k: {"lambda": 10.0, "data_quality": "ok"})
    t = next(x for x in server.match_compare("aurora", "vit", "Aurora", "Vitality", START, "12", "prizepicks")["teams"] if x["team"] == "Aurora")
    w = next(p for p in t["players"] if p["nick"] == "Wicadia")
    # the table's lines come from its own platform's board (both boards are read afterwards only for BO3.gg projections)
    assert seen[0] == "prizepicks" and w["kills"]["line"] == 28.5            # PrizePicks' line, not Underdog's 29.5
    assert w["hs"]["line"] is None and w["hs"]["no_line"]                     # the goblin market stays on the board
