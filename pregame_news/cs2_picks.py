"""pgn-1 CS2 roster news, next to every forecast WITHOUT touching the frozen forecast writer (picklog.py is guarded by
tests/test_workspace_api.py::test_model_hash_files_are_untouched).

A background thread reads the pick log READ-ONLY (sqlite mode=ro, query_only) every SCAN_S seconds: each forecast row
appended since the last pass (cursor = forecasts.id, kept in the news database) has its saved grader inputs (zlib JSON)
read for the roster facts the grade saw - roster confirmed, stand-in, stand-in for, on the listed team, the lineup check,
scout - and they are archived change-only as kind "roster" (sport cs2, subject = BO3.gg player id, event = match slug),
observed at the forecast's own logged_at. Only forecasts logged before the match start; owner load tests (test = 1) are
skipped. At most MAX_PER_PASS forecasts per pass, in pages of PAGE rows. Nothing on a request path; never raises.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

from . import connect, enabled, observe

KEYS = ("roster_confirmed", "is_standin", "standin_for", "on_listed_team", "scout", "lineup_check", "playing_for",
        "team_changed", "regional_extension")
SCAN_S = float(os.environ.get("PREGAME_NEWS_CS2_SCAN_S", "600"))
PAGE = 500
MAX_PER_PASS = 5000
STATE = {"last": None, "rows": 0, "error": None}


def _t(s):
    try:
        t = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _z(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def roster_facts(inputs_blob, factors_json=None):
    """The roster facts in one forecast's saved grader inputs (fallback: the pick's first factors JSON)."""
    d = {}
    if inputs_blob:
        try:
            gi = json.loads(zlib.decompress(inputs_blob))
            if isinstance(gi, dict):
                d = {k: gi[k] for k in KEYS if k in gi}
        except Exception:       # noqa: BLE001
            d = {}
    if not d and factors_json:
        try:
            f = json.loads(factors_json)
            if isinstance(f, dict):
                d = {k: f[k] for k in KEYS if k in f}
        except Exception:       # noqa: BLE001
            pass
    return d


def scan(picks_db, news_db, max_rows=MAX_PER_PASS) -> dict:
    out = {"read": 0, "archived": 0, "skipped": 0}
    if not enabled() or not Path(picks_db).exists():
        return out
    news = connect(news_db)
    src = sqlite3.connect(Path(picks_db).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        src.row_factory = sqlite3.Row
        src.execute("PRAGMA query_only = ON")
        r = news.execute("SELECT v FROM meta WHERE k = 'cs2_forecast_cursor'").fetchone()
        cursor = int(r[0]) if r else 0
        while out["read"] < max_rows:
            rows = src.execute(
                "SELECT f.id, f.logged_at, f.match_start, f.inputs, p.player_id, p.slug, p.team, p.opponent, "
                "p.match_slug, p.test, p.factors FROM forecasts f JOIN picks p ON p.id = f.pick_id "
                "WHERE f.id > ? ORDER BY f.id LIMIT ?", (cursor, PAGE)).fetchall()
            if not rows:
                break
            for x in rows:
                out["read"] += 1
                cursor = x["id"]
                at, st = _t(x["logged_at"]), _t(x["match_start"])
                if x["test"] == 1 or x["player_id"] is None or at is None or (st is not None and at >= st):
                    out["skipped"] += 1
                    continue
                facts = roster_facts(x["inputs"], x["factors"])
                if not facts:
                    out["skipped"] += 1
                    continue
                payload = {"player": x["slug"], "team": x["team"], "opponent": x["opponent"], **facts}
                res = observe(news, "cs2", "cs2", "roster", "match", {(str(x["player_id"]), x["match_slug"] or ""): payload},
                              observed_at=_z(at), source="pick log grader inputs (bo3.gg roster page + latest lineup)",
                              start_at=_z(st), touch=False)
                out["archived"] += res.get("new", 0) + res.get("changed", 0)
            with news:
                news.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('cs2_forecast_cursor', ?)", (str(cursor),))
            if len(rows) < PAGE:
                break
        out["cursor"] = cursor
        return out
    finally:
        src.close()
        news.close()


def _loop(picks_db, news_db):
    time.sleep(120)                         # after the start-up rush
    while True:
        try:
            r = scan(picks_db, news_db)
            STATE.update(last=datetime.now(timezone.utc).isoformat(timespec="seconds"), rows=r.get("archived", 0),
                         error=None)
        except Exception as e:      # noqa: BLE001
            STATE.update(error=f"{type(e).__name__}: {str(e)[:160]}")
        time.sleep(SCAN_S)


def start(data_dir):
    """One daemon thread (server.py start-up). PREGAME_NEWS=0 starts nothing."""
    if not enabled():
        return None
    d = Path(data_dir)
    t = threading.Thread(target=_loop, args=(d / "picks.db", Path(os.environ.get("PREGAME_NEWS_CS2_DB") or d / "pregame_news.db")),
                         daemon=True, name="pregame-news-cs2")
    t.start()
    return t
