"""GitHub -> Scout source-record inbox. Only Scout writes the serving history.

The isolated history job publishes source records, never local canonical ids or a
replacement of lol_games. Scout imports bounded batches before its normal build.
Older observations cannot overwrite newer local corrections. Nothing promotes a fit.
"""
import json
import os

from . import store

SCHEMA = """CREATE TABLE IF NOT EXISTS lol_source_inbox (
    id BIGSERIAL PRIMARY KEY, tbl TEXT NOT NULL, skey TEXT NOT NULL,
    sha TEXT NOT NULL, data TEXT NOT NULL, event_time TEXT, seen_at TEXT NOT NULL,
    gone_at TEXT, producer TEXT NOT NULL, observation_key TEXT NOT NULL UNIQUE)"""
FIELDS = ("id", "tbl", "skey", "sha", "data", "event_time", "seen_at", "gone_at")


def _connect(dsn):
    import psycopg2
    return psycopg2.connect(dsn, connect_timeout=10, options="-c statement_timeout=30000")


def publish(con, dsn=None):
    """Publish this collector's latest source observations; retries are idempotent."""
    dsn = dsn or os.environ.get("LOL_HANDOFF_DSN")
    if not dsn:
        raise RuntimeError("LOL_HANDOFF_DSN is required: refusing an artifact-only history run")
    rows = con.execute("SELECT c.tbl, c.skey, c.sha, r.data, c.event_time, c.last_seen, c.gone_at "
                       "FROM lp_current c JOIN lp_rows r ON r.tbl=c.tbl AND r.skey=c.skey AND r.rev=c.rev")
    pg = _connect(dsn)
    count = 0
    try:
        from psycopg2.extras import execute_values
        with pg:
            with pg.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('1time-lol-source-handoff'))")
                cur.execute(SCHEMA)
                while batch := rows.fetchmany(500):
                    execute_values(cur, "INSERT INTO lol_source_inbox "
                                   "(tbl,skey,sha,data,event_time,seen_at,gone_at,producer,observation_key) VALUES %s "
                                   "ON CONFLICT (observation_key) DO NOTHING",
                                   [tuple(r) + ("github-history", store.sha([r[0], r[1], r[2], r[5], r[6]]))
                                    for r in batch], page_size=500)
                    count += len(batch)
        return {"published_records": count, "destination": "Scout source inbox"}
    finally:
        pg.close()


def apply_records(con, records):
    """Import observations and advance the cursor in the same local transaction."""
    from . import ingest
    allowed = set(ingest.WINDOWED) | set(ingest.REFERENCE) | {"LE:Match", "LE:Game", "LE:PlayerGame"}
    result = {"imported": 0, "older": 0, "unchanged": 0, "rejected": 0}
    cursor = int(store.get_meta(con, "handoff_cursor", 0))
    with con:
        for r in records:
            if r["id"] <= cursor:
                continue
            cursor = r["id"]
            try:
                data = json.loads(r["data"])
            except (TypeError, ValueError):
                data = None
            if r["tbl"] not in allowed or not r["skey"] or not isinstance(data, dict) or store.sha(data) != r["sha"]:
                store.quarantine(con, "handoff", str(r["id"]), "invalid_source_record", {"tbl": r["tbl"]})
                result["rejected"] += 1
                continue
            current = con.execute("SELECT sha,last_seen,gone_at FROM lp_current WHERE tbl=? AND skey=?",
                                  (r["tbl"], r["skey"])).fetchone()
            incoming_time = max(r["seen_at"], r["gone_at"] or "")
            local_time = max(current["last_seen"], current["gone_at"] or "") if current else ""
            if current and incoming_time <= local_time:
                result["older"] += 1
                continue
            same = current and current["sha"] == r["sha"] and current["gone_at"] == r["gone_at"]
            store.put_rows(con, r["tbl"], [data], lambda _: r["skey"], lambda _: r["event_time"], None, r["seen_at"])
            if r["gone_at"]:
                con.execute("UPDATE lp_current SET gone_at=? WHERE tbl=? AND skey=?", (r["gone_at"], r["tbl"], r["skey"]))
            result["unchanged" if same else "imported"] += 1
        store.set_meta(con, "handoff_cursor", cursor)
        if result["imported"]:
            store.set_meta(con, "dirty", store.now_iso())
        store.set_meta(con, "last_handoff", dict(result, cursor=cursor, at=store.now_iso()))
    return result


def consume(con, dsn=None, limit=500):
    dsn = dsn or os.environ.get("LOL_DB_DSN")
    if not dsn:
        return {"configured": False}
    pg = _connect(dsn)
    try:
        with pg, pg.cursor() as cur:
            # The producer creates the table. A not-yet-published inbox is normal.
            cur.execute("SELECT to_regclass('public.lol_source_inbox')")
            if cur.fetchone()[0] is None:
                return {"configured": True, "waiting_for_first_publish": True}
            cur.execute("SELECT id,tbl,skey,sha,data,event_time,seen_at,gone_at FROM lol_source_inbox "
                        "WHERE id > %s ORDER BY id LIMIT %s", (int(store.get_meta(con, "handoff_cursor", 0)), limit))
            records = [dict(zip(FIELDS, row)) for row in cur.fetchall()]
        return dict(apply_records(con, records), configured=True)
    finally:
        pg.close()


if __name__ == "__main__":
    con = store.connect()
    try:
        print(json.dumps(publish(con)))
    except Exception as exc:
        # Database errors can contain credentials: report only the type.
        raise SystemExit("history handoff failed: " + type(exc).__name__) from None
    finally:
        con.close()
