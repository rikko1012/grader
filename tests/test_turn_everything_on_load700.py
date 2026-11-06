"""700 members at once with the running server's DEFAULT_ON applied (owner 2026-11-05 "Turn everything on": parity board fixes,
Kelly % prefs, line history, +EV board, slip header v2) and letter containment left OFF: each member opens /me, the CS2 board, the
+EV board, a card with its line history (line?book=...), Slip Studio's generate (PrizePicks and Underdog, CS2 and LoL), the slip
adder and a PrizePicks slip recheck. No 5xx, one shared board build per window, one shared generate build per option set, PrizePicks
payouts on the verified table, CS2 letters exactly as the grader wrote them. Fixture upstreams only.

A refused connect (Windows WinError 10061 when the accept backlog is momentarily full) is retried a few times: it is the client OS
refusing before the server sees the request, never a server answer. Every answer the server does send is counted."""
import gzip
import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import server
import whop_auth
import workspace_api
import workspace_ev as we
import workspace_books as wb
import workspace_linehist as lh
from tests.test_workspace_api import env, API, records, pair, idem  # noqa: F401
from tests.workspace_fixture import leg_of

GEN = [
    "generate?platform=prizepicks&type=power&picks=2&slips=8&sports=cs2",
    "generate?platform=underdog&type=standard&picks=3&slips=8&sports=cs2",
    "generate?platform=prizepicks&type=flex&picks=3&slips=4&sports=cs2,lol",
    "generate?platform=underdog&type=flex&picks=3&slips=4&sports=cs2",     # (a LoL-only ask is refused for fixture members without LoL access)
]


@pytest.fixture
def big_queue(monkeypatch):
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


@pytest.fixture
def default_on(monkeypatch):
    env = {}
    server.apply_default_flags(env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SCOUT_LETTER_CONTAINMENT", raising=False)
    workspace_api.reset(); wb.reset(); we._FAIR.clear(); we._LEGS.clear(); lh.reset()
    yield


def _send(port, method, path, headers, body=None, tries=6):
    """One request on a fresh connection; a refused connect is retried (client-side refusal, the server never saw it)."""
    for k in range(tries):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        try:
            c.request(method, path, body=body, headers=headers)
            r = c.getresponse()
            raw = r.read()
            if r.getheader("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return r.status, raw
        except ConnectionRefusedError:
            if k == tries - 1:
                raise
            time.sleep(0.2 * (k + 1))
        finally:
            c.close()


def test_700_members_at_once_with_everything_on(big_queue, env, default_on, monkeypatch):
    assert server.os.environ.get("CS2_MAP_WEIGHTED", "0") in ("", "0")                  # map-weighted projection stays shadow-only
    recs = records(env)
    v = recs[("vargo", "underdog")]
    assert "parity" in v and v["scout"]["grade"] == env.by("vargo")["grade"]          # letters unchanged (containment off)
    assert not any(x["scout"].get("letter_held") for x in recs.values())
    pp_pair = pair(env, "prizepicks")
    by = {(r["player"]["name"], r["offer"]["platform"]): r for r in recs.values()}
    adv_legs = [dict(leg_of(r), stat=r["market"]["stat"], period=r["market"]["period"], player_key=r["player"]["key"],
                     player=r["player"]["name"], team=r["player"]["team"], event=r["event"]["id"])
                for r in (by[("vargo", "prizepicks")], by[("brandr", "prizepicks")])]
    cards = [r["id"] for r in env.rows if r["book"] == "underdog"][:4]
    builds, gens = [0], {}
    lock = threading.Lock()
    real, real_gen = workspace_api._build, workspace_api._generate

    def counted(srv):
        with lock:
            builds[0] += 1
        time.sleep(0.05)
        return real(srv)

    def counted_gen(srv, o):
        k = (o["platform"], o["entry_type"], o["size"], o["count"], o["sports"])
        with lock:
            gens[k] = gens.get(k, 0) + 1
        time.sleep(0.05)
        return real_gen(srv, o)
    monkeypatch.setattr(workspace_api, "_build", counted)
    monkeypatch.setattr(workspace_api, "_generate", counted_gen)

    def member(i):
        out, ev, gen = [], None, None
        cookie = {"Cookie": f"{whop_auth.COOKIE}=good-m{i}", "Accept-Encoding": "gzip"}
        js = dict(cookie, **{"Content-Type": "application/json"})
        for p in ("me", "board", "pickem-ev", f"line?book=underdog&id={cards[i % len(cards)]}"):
            st, _ = _send(env.port, "GET", API + p, cookie)
            out.append(st)
        st, raw = _send(env.port, "GET", API + GEN[i % len(GEN)], cookie)
        out.append(st)
        if st == 200 and i < len(GEN):
            gen = (i, json.loads(raw))
        st, _ = _send(env.port, "POST", API + "slip-advice",
                      js, json.dumps({"legs": adv_legs, "entry_types": {"prizepicks": "power"}}).encode())
        out.append(st)
        st, raw = _send(env.port, "POST", API + "recheck", js,
                        json.dumps({"idem": f"load-{i}-{idem()}", "legs": pp_pair, "entry_types": {}}).encode())
        out.append(st)
        if st == 200 and i % 50 == 0:
            ev = json.loads(raw)["entries"]["prizepicks"]["ev"]
        return out, ev, gen
    t0 = time.time()
    codes, evs, docs = [], [], {}
    with ThreadPoolExecutor(max_workers=700) as ex:
        for r, ev, gen in ex.map(member, range(700)):
            codes.extend(r)
            if ev is not None:
                evs.append(ev)
            if gen:
                docs[gen[0]] = gen[1]
    took = time.time() - t0
    assert codes.count(200) == len(codes) == 700 * 7, {c: codes.count(c) for c in set(codes)}
    assert builds[0] <= 2, builds
    assert len(gens) == len(GEN) and all(n == 1 for n in gens.values()), gens                # one shared generate build per option set
    assert evs and all((e.get("payout") or {}).get("multiple") == 3.0 for e in evs), evs[:2]     # verified PrizePicks table
    pp2 = docs[0]
    assert pp2["slips"] and all(s["payout"]["multiple"] == 3.0 for s in pp2["slips"])
    for d in docs.values():                                                               # containment off: lettered legs only
        assert all(not l.get("chance") and not str(l["record"]).startswith("Lean") for s in d["slips"] for l in s["legs"])
    assert took < 150, took
