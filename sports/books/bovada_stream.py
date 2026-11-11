"""Finite public Bovada snapshots; no browser, credentials or challenge solving.

Protocol verified against the public NBA/NFL client on 2026-11-11. Catch-up
delimiters are mandatory: a timeout/partial snapshot must never withdraw quotes.
"""
import json
import time
import uuid

from .. import net

URL = "wss://services.bovada.lv/services/sports/subscription/"
COMPETITIONS = {"nba": "2958468", "nfl": "241"}
MAX_BYTES = 8 * 1024 * 1024


def snapshot(league, path=None):
    import websocket  # Sports worker dependency only; never imported by esports.

    if path is None:
        route = (f"/competitions/{COMPETITIONS[league]}?marketFilterId=def&wsOnly=true"
                 "&catchupDelimiter=true&eventsLimit=5000&preMatchOnly=true&delta=true")
        prefix = "competition"
    else:
        route = "/eventByLink/" + path.replace("/", "$") + "?catchupEventDelimiter=true&delta=true"
        prefix = "event"
    ws = None
    started = False
    groups = []
    total = 0
    deadline = time.monotonic() + 20
    try:
        net._wait("services.bovada.lv")
        ws = websocket.create_connection(URL + str(uuid.uuid4()) + "?language=en&lnGrp=2", timeout=8)
        ws.send("SUBSCRIBE|A|" + route)
        for _ in range(200):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Bovada snapshot deadline exceeded")
            ws.settimeout(min(8, remaining))
            raw = ws.recv()
            if not isinstance(raw, str) or not raw:
                raise ValueError("Bovada snapshot closed early")
            total += len(raw.encode("utf-8"))
            if total > MAX_BYTES:
                raise ValueError("Bovada snapshot exceeds memory budget")
            head, sep, payload = raw.partition("|")
            header = json.loads(head)
            body = json.loads(payload) if sep else None
            kind = header.get("type")
            if kind == "chg" or (kind == "chgEnablement" and body.get("enabled") is not False):
                raise net.HttpError(403, URL)
            if kind == "chgEnablement":
                continue
            if header.get("target") != "prematch":
                raise ValueError("unexpected Bovada snapshot target")
            if kind == prefix + "_catchup_start":
                if started:
                    raise ValueError("duplicate Bovada snapshot start")
                started = True
            elif kind == prefix + "_catchup_end":
                if not started:
                    raise ValueError("Bovada snapshot end without start")
                if path is not None and header.get("path") != path.replace("/", "$"):
                    raise ValueError("wrong Bovada snapshot delimiter")
                return groups
            elif kind == "competition" and prefix == "competition" and started:
                if not isinstance(body, dict) or not isinstance(body.get("events"), list):
                    raise ValueError("invalid Bovada competition")
                if not any(str(p.get("id")) == COMPETITIONS[league] for p in body.get("path", [])):
                    raise ValueError("wrong Bovada competition")
                groups.append(body)
            elif kind == "fullEvent" and prefix == "event" and started:
                if body.get("link") != path:
                    raise ValueError("wrong Bovada event")
                groups.append({"events": [body]})
            else:
                raise ValueError("unexpected Bovada snapshot frame")
        raise ValueError("Bovada snapshot frame limit exceeded")
    except websocket.WebSocketBadStatusException as exc:
        headers = {str(k).lower(): v for k, v in (exc.resp_headers or {}).items()}
        delay = net.retry_after_s(headers.get("retry-after"))
        until = time.time() + delay if delay is not None else None
        if until is not None:
            net.hold("services.bovada.lv", until)
        raise net.HttpError(exc.status_code, URL, not_before=until) from None
    except (websocket.WebSocketException, OSError) as exc:
        raise ValueError("Bovada subscription unavailable") from None
    finally:
        if ws is not None:
            ws.close(timeout=1)
