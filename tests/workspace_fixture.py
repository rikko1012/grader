"""Fixture data for the research workspace: a graded board on both platforms, their feeds, and the pick log rows behind them.
Used by the API tests, the browser test and scripts/load_audit700.py. No network and no production data."""
import json
import sqlite3
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODEL = "TESTMODEL"
LETTER = ((0.74, "A"), (0.68, "A-"), (0.63, "B+"), (0.57, "B"), (0.55, "B-"))


def iso(t):
    return t.isoformat(timespec="seconds")


def row(rid, book, nick, team, opp, line, side, p, match, start, prop="kills", scope="12", **kw):
    grade = next((g for lo, g in LETTER if p >= lo), "LEAN")
    r = {"id": rid, "book": book, "player": nick, "nick": nick, "player_id": sum(map(ord, nick)), "team": team, "team_name": team, "opponent": opp, "opp_name": opp,
         "match_slug": f"slug-{match}", "settle_slug": f"slug-{match}", "match_id": f"hltv:{match}", "start": start, "match_start": start, "lock_at": start,
         "prop": prop, "scope": scope, "line": line, "graded_line": line, "graded_model": MODEL, "side": side, "p": p, "grade": grade, "model_grade": grade,
         "units": 0.5 if grade != "LEAN" else 0.0, "stake_reason": None, "cap_reason": "", "state": "GRADE", "graded_at": iso(datetime.now(timezone.utc) - timedelta(hours=1)),
         "proj": line + (1.5 if side == "More" else -1.5), "model_outcomes": {"over": p, "under": round(1 - p, 4), "push": 0.0}, "event": "Meridian Cup", "bo": 3,
         "awp": False, "awp_conf": "confirmed", "igl": False, "alt": False, "over_available": True, "under_available": True, "unavailable": None, "hltv_retry": False,
         "l10_over": 6, "l10_n": 10, "l10_push": 0, "l10_avg": line + 1, "updated_at": iso(datetime.now(timezone.utc) - timedelta(minutes=20))}
    if book == "underdog":
        r.update(higher=1.0, lower=1.0, price_higher=1.92, price_lower=1.88, ud_player_id=f"u{nick}")
    else:
        r.update(higher=None, lower=None, mod_higher=1.0, mod_lower=1.0, odds_type="standard", price_higher=None, price_lower=None)
    r.update(kw)
    return r


def default_rows(start=None):
    s = start or iso(datetime.now(timezone.utc) + timedelta(hours=3))
    late = iso(datetime.fromisoformat(s) + timedelta(hours=2))
    return [
        row("11111111-0000-4000-8000-000000000001", "underdog", "vargo", "Halcyon", "Norrland", 38.5, "More", 0.612, 900, s),
        row("pp:7001", "prizepicks", "vargo", "Halcyon", "Norrland", 38.5, "More", 0.608, 900, s),
        row("11111111-0000-4000-8000-000000000002", "underdog", "brandr", "Norrland", "Halcyon", 34.5, "Less", 0.586, 900, s),
        row("pp:7002", "prizepicks", "brandr", "Norrland", "Halcyon", 34.5, "Less", 0.581, 900, s),
        row("11111111-0000-4000-8000-000000000003", "underdog", "orlo", "Vanta", "Kestrel Five", 33.5, "More", 0.598, 901, late),
        row("pp:7003", "prizepicks", "orlo", "Vanta", "Kestrel Five", 34.5, "More", 0.571, 901, late),
        row("11111111-0000-4000-8000-000000000004", "underdog", "raiko", "Kestrel Five", "Vanta", 36.5, "Less", 0.552, 901, late),
        row("pp:7004", "prizepicks", "mika7", "Kestrel Five", "Vanta", 36.0, "More", 0.566, 901, late),
        row("pp:7005", "prizepicks", "sable", "Vanta", "Kestrel Five", 7.5, "Less", 0.541, 901, late, prop="hs", scope="1"),
    ]


class Fixture:
    """The board, the feeds and the pick log for one test. tests move a row by editing self.rows in place."""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.rows = default_rows()
        self.boards = 0
        self.grades = 0
        self.feed_ok = {"underdog": True, "prizepicks": True}
        self.board_ok = True
        self.updated = time.time()              # a feed's read time only changes on its own refresh

    def payload(self, book="underdog"):
        self.boards += 1
        if not self.board_ok:
            raise RuntimeError("board down")
        rows = [dict(r) for r in self.rows if r["book"] == book]
        out = {"book": book, "updated": self.updated, "error": None, "rows": rows, "total": len(rows), "version": MODEL}
        if book == "prizepicks":
            out["feed"] = {"state": "ok", "at": self.updated, "error": None}
        return out

    def _feed(self, book):
        keys = ("id", "book", "player", "prop", "scope", "line", "team", "opponent", "start", "alt", "higher", "lower", "over_available", "under_available",
                "odds_type", "ud_player_id", "mod_higher", "mod_lower")
        return [{k: r.get(k) for k in keys} for r in self.rows if r["book"] == book]

    def ud_feed(self, wait=False):
        ok = self.feed_ok["underdog"]
        return {"at": time.time() if ok else 0.0, "error": None if ok else "Underdog did not answer", "rows": self._feed("underdog")}

    def pp_feed(self, now=None, wait=False):
        ok = self.feed_ok["prizepicks"]
        return {"state": "ok" if ok else "failed", "at": time.time(), "rows": self._feed("prizepicks"), "error": None if ok else "PrizePicks did not answer",
                "ingested_at": time.time(), "checked_at": time.time(), "lines": len(self.rows), "source": None}

    def fake_grade(self, *a, **k):
        self.grades += 1
        raise AssertionError("the workspace must never grade")

    def install(self, set_):
        """set_ is monkeypatch.setattr, or setattr in the load harness."""
        import cs2_scout
        import picklog
        import prizepicks
        import server
        import underdog
        set_(picklog, "DB", self.tmp / "picks.db")
        set_(picklog, "_SCHEMA_OK", [False])
        set_(server, "board_payload", self.payload)
        set_(underdog, "board", self.ud_feed)
        set_(prizepicks, "board", self.pp_feed)
        set_(cs2_scout, "grade", self.fake_grade)
        set_(server, "_GB", {})
        set_(server, "_SLIP_IDX", {})
        set_(server, "_p1_export", self.export)
        self.seed()

    def export(self):
        rows = [{"match": f"m{i // 2}", "p": 0.6, "over": 1 if i % 5 < 3 else 0, "prop": "kills", "scope": "12", "version": MODEL, "start": "2026-10-20T10:00:00+00:00",
                 "logged": "2026-10-20T09:00:00+00:00", "units": 0.5, "forecast_id": i} for i in range(60)]
        return {"at": iso(datetime.now(timezone.utc)), "rows": rows, "pushes": [{"match": "m0", "p": 0.6, "scope": "12", "units": 0.5, "forecast_id": 999}],
                "excluded": {"push on the board line": 1}}

    def seed(self, rows=None):
        import picklog
        picklog._db().close()
        con = sqlite3.connect(picklog.DB, timeout=10)
        for r in rows if rows is not None else self.rows:
            self.seed_row(con, r)
        con.commit()
        con.close()

    def seed_row(self, con, r, p_saved=None, slug=None, logged_at=None, model=None, inputs=None):
        lt = r.get("odds_type") if r.get("odds_type") in ("goblin", "demon") else "standard"
        p = r["p"] if p_saved is None else p_saved
        book = r["book"] if r["book"] != "underdog" else "underdog"
        cur = con.execute("INSERT INTO picks(key, logged_at, player, slug, player_id, team, opponent, match_slug, match_start, prop, line, line_type, pick, p_pick, grade, units, "
                          "status, test, scope, model_version, book, market_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',2,?,?,?,?)",
                          (f"{r['id']}|{slug or r['settle_slug']}", logged_at or r["graded_at"], r["nick"], r["nick"], r["player_id"], r["team"], r["opponent"], slug or r["settle_slug"],
                           r["start"], r["prop"], r["line"], lt, r["side"], p, r["grade"], r["units"], r["scope"], model or MODEL, book, str(r["id"])))
        blob = zlib.compress(json.dumps(inputs or {"maps_played": 41, "rating": 1.12, "kpr": 0.74, "adr": 81.2, "kast": 73.1, "recent_avg": 40.1, "secret": 3}).encode())
        con.execute("INSERT INTO forecasts(pick_id, kind, logged_at, match_start, line, pick, p_pick, p_more, grade, units, lambda, model_version, inputs) "
                    "VALUES(?,'first',?,?,?,?,?,?,?,?,?,?,?)", (cur.lastrowid, logged_at or r["graded_at"], r["start"], r["line"], r["side"], p,
                                                                 p if r["side"] == "More" else 1 - p, r["grade"], r["units"], r.get("proj"), model or MODEL, blob))
        return cur.lastrowid

    def relog(self, r):
        """The pick log after a regrade at this row's current line."""
        import picklog
        con = sqlite3.connect(picklog.DB, timeout=10)
        pid = con.execute("SELECT id FROM picks WHERE market_id = ?", (str(r["id"]),)).fetchone()[0]
        con.execute("UPDATE picks SET line = ? WHERE id = ?", (r["line"], pid))
        con.execute("UPDATE forecasts SET line = ? WHERE pick_id = ?", (r["line"], pid))
        con.commit()
        con.close()

    def flip(self, r, p=0.58):
        """Grader changes side at the same line: a new forecast row, the old one kept. The caller clears the 5 s board copies."""
        import picklog
        r.update(side="Less" if r["side"] == "More" else "More", p=p, graded_at=iso(datetime.now(timezone.utc)))
        con = sqlite3.connect(picklog.DB, timeout=10)
        con.execute("INSERT INTO forecasts(pick_id, kind, logged_at, match_start, line, pick, p_pick, p_more, grade, units, lambda, model_version, inputs) "
                    "SELECT pick_id, 'update', ?, match_start, line, ?, ?, ?, grade, units, lambda, model_version, inputs FROM forecasts WHERE pick_id = ? ORDER BY id DESC LIMIT 1",
                    (r["graded_at"], r["side"], p, p if r["side"] == "More" else 1 - p, self.pick_id(r)))
        con.commit()
        con.close()

    def pick_id(self, r):
        import picklog
        con = sqlite3.connect(picklog.DB, timeout=10)
        try:
            return con.execute("SELECT id FROM picks WHERE market_id = ?", (str(r["id"]),)).fetchone()[0]
        finally:
            con.close()

    def settle(self, r, status="settled", actual=None, source="hltv", review=False):
        """What picklog does to a row after the match: nothing else in the workspace writes here."""
        import picklog
        con = sqlite3.connect(picklog.DB, timeout=10)
        res = json.dumps({"source": source, **({"review": True} if review else {})})
        con.execute("UPDATE picks SET status = ?, actual = ?, actual_maps = ?, settled_at = ?, result = ? WHERE market_id = ?",
                    (status, actual, "[1,2]", iso(datetime.now(timezone.utc)), res, str(r["id"])))
        con.commit()
        con.close()

    def by(self, nick, book="underdog"):
        return next(r for r in self.rows if r["nick"] == nick and r["book"] == book)


def leg_of(record, side=None, shown=True):
    """The leg the page sends for one /board record."""
    side = side or record["scout"]["side"]
    return {"id": f"{record['key']}|{record['offer']['platform']}|{side}", "sport": "cs2", "book": record["offer"]["platform"], "row_id": record["offer"]["offer_id"],
            "side": side, "line": record["offer"]["line"], "rev": record["offer"]["revision"], "read_rev": record["scout"]["read_rev"],
            "shown_p": record["scout"]["p_side"] if shown and side == record["scout"]["side"] else None}
