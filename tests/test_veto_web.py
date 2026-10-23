"""veto-1 on the member pages (display only): the panel's wording per state (pending / in progress / confirmed - no hedge
label, a predicted map is never called confirmed, slot chances are said not to be one series, the combinations carry the
probability outside the shortlist, the forecast time is shown), the player card's Match section, the Matchroom slot, and
the server side: /api/veto is a stored answer (members only, bad ids refused), the card attaches the stored panel."""
import collections
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

import server
import veto_serve as vs
import whop_auth
from tests.test_audit13_site_ui import needs_node, run_page

PENDING = {"state": "pending", "format": "bo3", "at": "2026-10-23T18:00:00+00:00", "version": "veto-1-a140ca69ec",
           "record": "abc", "teams": ["Vitality", "MOUZ"], "match_id": "hltv:1", "confirmed": [], "bans": [],
           "slots": [{"slot": "Map 1", "top": [["nuke", "Nuke", 0.231], ["dust2", "Dust 2", 0.202]], "other": 0.567},
                     {"slot": "Map 2", "top": [["mirage", "Mirage", 0.4]], "other": 0.6}],
           "combos": [[["Nuke", "Dust 2", "Mirage"], 0.081], [["Mirage", "Dust 2", "Nuke"], 0.054]], "combos_other": 0.865,
           "facts": ["Vitality removed Ancient first in 7 of its last 10 vetoes"]}
PARTIAL = dict(PENDING, state="partial", confirmed=[{"slot": "Map 1", "key": "map1", "map": "nuke", "name": "Nuke", "by": None}],
               slots=[{"slot": "Map 2", "top": [["mirage", "Mirage", 0.4]], "other": 0.6}])
CONFIRMED = dict(PENDING, state="confirmed", slots=[], combos=[], combos_other=None, facts=[],
                 confirmed=[{"slot": "Map 1", "key": "map1", "name": "Nuke", "by": "Vitality"},
                        {"slot": "Map 2", "key": "map2", "name": "Mirage", "by": "MOUZ"},
                        {"slot": "Decider", "key": "decider", "name": "Ancient", "by": None}],
                 bans=[{"step": 1, "name": "Train", "by": "MOUZ"}])


@needs_node
def test_panel_wording_per_state(tmp_path):
    out = run_page(tmp_path, "(() => [vetoPanel(INPUT.p), vetoPanel(INPUT.q), vetoPanel(INPUT.c), vetoPanel(null)])()",
                   {"p": PENDING, "q": PARTIAL, "c": CONFIRMED})
    p, q, c, none = out
    assert "Veto pending" in p and "Forecast" in p and "Map 1 · Predicted" in p and "Map 2 · Predicted" in p
    assert "Confirmed" not in p                                          # nothing predicted is called confirmed
    assert "do not add up to one series" in p and "Other maps <b>56.7%</b>" in p
    assert "Most likely full series (Map 1 · Map 2 · Decider)" in p and "Other combinations: 86.5%" in p
    assert "Nuke · Dust 2 · Mirage<b>8.1%</b>" in p and "7 of its last 10 vetoes" in p
    assert "Veto in progress" in q and "Map 1 <b>Nuke</b>" in q and "Map 1 · Predicted" not in q and "Map 2 · Predicted" in q
    assert "Veto confirmed" in c and "Predicted" not in c and "(Vitality pick)" in c and "Removed: Train (MOUZ)" in c
    assert "Updated" in c and none == ""
    for h in (p, q, c):
        assert not any(w in h.lower() for w in ("unconfirmed", "uncertain", "provisional", "estimate only", "inferred"))


@needs_node
def test_player_card_match_section_shows_the_panel(tmp_path):
    card = {"grade": {"line": 30.5, "prop": "kills", "scope": "12", "line_type": "standard", "p_more": .55, "p_less": .45},
            "verdict": {"side": "More", "grade": "B-", "p": .55, "units": .5},
            "player_block": {"player": {"nick": "ZywOo", "id": 1}, "current_team": "Vitality",
                             "map_prediction": {"map1": {"de_ancient": .9}, "map2": {"de_train": .9}}},
            "next_match": {"opponent": "MOUZ", "start": "2099-01-01T12:00:00Z", "slug": "x", "bo": 3, "maps": []},
            "veto": PENDING}
    html = run_page(tmp_path, """(() => {try {render(INPUT);} catch(e) {if(!/nextSibling/.test(e.message))throw e;}
                                 return $('#out').innerHTML;})()""", card)
    assert "Map veto" in html and "Map 1 · Predicted" in html and "Other combinations" in html
    assert "Likely Map" not in html and "Ancient 90" not in html        # the old simulator's lines are gone from the page


@needs_node
def test_matchroom_has_a_veto_slot(tmp_path):
    from tests.test_audit13_site_ui import HTML
    assert 'id="ot-veto"' in HTML and "/api/veto?match=" in HTML and "otDeskVeto(otDeskRows)" in HTML


# ---------------------------------------------------------------- server
@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setattr(whop_auth, "enabled", lambda: False)
    for v in ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "REQUIRE_WHOP"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "_INFLIGHT", {})
    monkeypatch.setattr(server, "_ENC_LOCKS", {})
    monkeypatch.setattr(server, "_RES_BYTES", [0, 0.0])
    monkeypatch.setattr(vs, "_STORE", {"hltv:2399849": {"payload": PENDING, "record_id": "abc", "state": "pending"}})
    monkeypatch.setattr(vs, "_WANT", collections.OrderedDict())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _get(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, body


def test_api_veto_serves_the_stored_answer(srv):
    st, h, body = _get(srv, "/api/veto?match=hltv:2399849")
    assert st == 200 and json.loads(body)["veto"]["record"] == "abc" and "etag" in h
    st, h, body = _get(srv, "/api/veto?match=hltv:5")
    assert st == 200 and json.loads(body)["veto"] is None
    for bad in ("/api/veto?match=cs2:abc", "/api/veto?match=hltv:1;drop", "/api/veto"):
        assert _get(srv, bad)[0] == 400


def test_api_veto_is_members_only(srv, monkeypatch):
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    st, _, body = _get(srv, "/api/veto?match=hltv:2399849")
    assert st == 401 and json.loads(body)["error"] == "members_only"


def test_admin_veto_needs_the_token(srv, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_TOKEN", "tok")
    assert _get(srv, "/admin/veto.json")[0] in (401, 403, 404)
    st, _, body = _get(srv, "/admin/veto.json?action=status", {"X-Admin-Token": "tok"})
    assert st == 200 and json.loads(body)["version"] == "veto-1-a140ca69ec"


def test_card_attaches_the_stored_panel_without_computing(srv):
    res = {"player_block": {"current_team": "K27"}, "next_match": {"slug": "hltv-2399849", "opponent": "Virtus.pro"}}
    assert server.card_veto(res) == PENDING
    res2 = {"player_block": {"current_team": "A"}, "next_match": {"slug": "hltv-42", "opponent": "B", "start": "2099-01-01T00:00:00Z"}}
    assert server.card_veto(res2) is None and "hltv:42" in vs._WANT     # queued for the background, nothing computed
