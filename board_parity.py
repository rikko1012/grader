"""Competitor spec 4.16 board parity fixes: one flag per fix, every one OFF by default (not in server.DEFAULT_ON).

  SCOUT_PARITY_EDGE         Edge vs break-even column = real leg EV (p / be - 1) for PrizePicks and Underdog, sortable from its header
  SCOUT_PARITY_HIT_SAMPLES  hit cells read "6/10" with the % in the tooltip
  SCOUT_PARITY_HIT_SORT     "Hit rate vs break-even" sort: L10 hit rate - break-even, rows below break-even sorted last
  SCOUT_PARITY_PLAYS        plays-count chips per app in the platform toggle
  SCOUT_PARITY_REPORT       "Report a problem" on the player card -> the workspace store's data_issues table
  SCOUT_PARITY_EV_BAND      EV band chip (Skip / Marginal / Strong / Premium) beside the edge
  SCOUT_PARITY_PREFS        member preferences: odds format (American / Decimal) and bet sizing (1/4 Kelly, 1/2 Kelly, flat 1U,
                            or $ from an entered bankroll). Display only: grader units never change.

All numbers come from gradecore.parity. With every flag off nothing here runs: records, /me and routes are byte-identical.
"""
import os

FLAGS = {"edge": "SCOUT_PARITY_EDGE", "hit_samples": "SCOUT_PARITY_HIT_SAMPLES", "hit_sort": "SCOUT_PARITY_HIT_SORT",
         "plays": "SCOUT_PARITY_PLAYS", "report": "SCOUT_PARITY_REPORT", "ev_band": "SCOUT_PARITY_EV_BAND", "prefs": "SCOUT_PARITY_PREFS"}
ROW_FLAGS = ("edge", "hit_sort", "ev_band", "prefs")         # the ones that need per-row numbers on the board
REASONS = {"line": "Wrong line or price", "player": "Wrong player or team", "stats": "Wrong past results", "time": "Wrong start time",
           "other": "Something else"}
NOTE_MAX = 300
REPORT_PER_MIN = 6


def on(name):
    return os.environ.get(FLAGS[name], "").strip() == "1"


def rows_on():
    return any(on(k) for k in ROW_FLAGS)


def features():
    """The /me features entry: {} when every flag is off (no key at all), else {"parity": {flag: True..., "break_even": table}}."""
    got = {k: True for k in FLAGS if on(k)}
    if not got:
        return {}
    if any(k in got for k in ROW_FLAGS):
        from gradecore import parity
        got["break_even"] = parity.table()
    if "prefs" in got:
        from gradecore import slip
        got["unit_share"] = slip.UNIT_SHARE          # 1U = this share of an entered bankroll (gradecore.slip.stake)
    if "report" in got:
        got["reasons"] = [list(x) for x in REASONS.items()]
    return {"parity": got}


def attach(rec):
    """Add rec["parity"] (gradecore.parity.of_record) when a row flag is on; a row with nothing to rate gets no key. Returns rec."""
    if rows_on():
        from gradecore import parity
        try:
            v = parity.of_record(rec)
        except (TypeError, ValueError):
            v = None
        if v is not None:
            rec["parity"] = v
    return rec


DDL = """CREATE TABLE IF NOT EXISTS data_issues(
  id INTEGER PRIMARY KEY AUTOINCREMENT, reported_at TEXT NOT NULL, member TEXT NOT NULL, scratch INTEGER NOT NULL DEFAULT 0,
  sport TEXT NOT NULL, platform TEXT NOT NULL, offer_id TEXT NOT NULL, revision TEXT, reason TEXT NOT NULL, note TEXT,
  player TEXT, stat TEXT, line REAL, start_utc TEXT, status TEXT NOT NULL DEFAULT 'open');
CREATE INDEX IF NOT EXISTS data_issues_open ON data_issues(status, reported_at DESC);"""


def clean_report(d):
    """(fields, None) or (None, error) from a member's POST body."""
    if not isinstance(d, dict):
        return None, "body must be a JSON object"
    sport, book, rid, reason = d.get("sport"), d.get("book"), d.get("id"), d.get("reason")
    if sport not in ("cs2", "lol", "nba", "nfl"):
        return None, "bad sport"
    if not isinstance(book, str) or not 0 < len(book) <= 40 or not isinstance(rid, str) or not 0 < len(rid) <= 200:
        return None, "bad line"
    if reason not in REASONS:
        return None, "choose what is wrong"
    note = d.get("note")
    if note is not None and not isinstance(note, str):
        return None, "bad note"
    rev = d.get("revision")
    return {"sport": sport, "platform": book, "offer_id": rid, "reason": reason, "note": (note or "").strip()[:NOTE_MAX] or None,
            "revision": rev if isinstance(rev, str) and len(rev) <= 100 else None}, None


def save_report(store, member, scratch, f, rec, now):
    """One row in data_issues (created on first use, so a flag-off store never gets the table). The same member reporting the
    same line revision for the same reason again is one row. -> {"id", "duplicate"}."""
    m, o, ev = (rec or {}).get("market") or {}, (rec or {}).get("offer") or {}, (rec or {}).get("event") or {}

    def op(con):
        for stmt in DDL.split(";"):            # execute, not executescript: executescript would commit the writer's open batch
            if stmt.strip():
                con.execute(stmt)
        dup = con.execute("SELECT id FROM data_issues WHERE member = ? AND sport = ? AND platform = ? AND offer_id = ? AND reason = ? "
                          "AND COALESCE(revision, '') = COALESCE(?, '')",
                          (member, f["sport"], f["platform"], f["offer_id"], f["reason"], f["revision"])).fetchone()
        if dup:
            return {"id": dup[0], "duplicate": True}
        cur = con.execute("INSERT INTO data_issues(reported_at, member, scratch, sport, platform, offer_id, revision, reason, note, player, stat, line, start_utc) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (now, member, 1 if scratch else 0, f["sport"], f["platform"], f["offer_id"], f["revision"], f["reason"], f["note"],
                           ((rec or {}).get("player") or {}).get("name"), m.get("stat"), o.get("line"), ev.get("start_utc")))
        return {"id": cur.lastrowid, "duplicate": False}
    return store.submit(op)
