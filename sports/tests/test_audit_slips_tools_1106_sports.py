"""Owner site audit 2026-11-06 T8, the sports service's side: the NBA / NFL grading comparison
(/api/sports/admin/model/gradecore-comparison) answers only an st1 token of role admin, and the esports gateway's token route
mints that role for the owner's own read (workspace_sports.admin_token goes through it). The esports side of the same check is
tests/test_audit_slips_tools_1106.py. Every player and number is a TEST FIXTURE."""
import json
import sqlite3

import pytest

from sports import authtoken, config, gateway, web

SECRET = "t" * 40


def _shadow(path, sport, player, event):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS gradecore_shadow (id TEXT PRIMARY KEY, sport TEXT NOT NULL, forecast_id TEXT NOT NULL, "
               "forecast_at TEXT NOT NULL, captured_at TEXT NOT NULL, payload TEXT NOT NULL)")
    body = {"forecast_id": "f1", "forecast_at": "2026-11-06T20:00:00+00:00", "old_letter": "B", "old_p": 0.61, "artifact_hash": "x",
            "offer": {"event_id": event, "player": player, "stat": "kills", "scope": "maps12", "line": 7.5, "book": "prizepicks"},
            "candidate": {"sides": {"over": {"p": 0.6}, "under": {"p": 0.4}}, "policy": "shadow"}}
    db.execute("INSERT INTO gradecore_shadow VALUES (?,?,?,?,?,?)", ("k-" + sport + player, sport, "f1", body["forecast_at"],
                                                                     "2026-11-06T20:00:01+00:00", json.dumps(body)))
    db.commit()
    db.close()


def _admin_token():
    code, body, _, _ = gateway.handle_get("/api/sports/token", "", {}, member=lambda: "owner-grade-review",
                                          allowed=lambda: True, admin=lambda: True, headers={})
    assert code == 200
    return json.loads(body)["token"]


@pytest.mark.parametrize("league", ["nba", "nfl"])
def test_t8_comparison_route_answers_the_gateway_admin_token_and_refuses_a_member(tmp_path, monkeypatch, league):
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", SECRET)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _shadow(tmp_path / "canonical.db", league, "Fixture Guard", "e-1")
    path, query = "/api/sports/admin/model/gradecore-comparison", {"league": league}
    claims = authtoken.verify(_admin_token())
    assert claims["role"] == "admin"
    code, data, _ = web.dispatch(web.Request(path=path, query=query, method="GET", body=b"", member=claims["sub"], role="admin"))
    assert code == 200, data
    doc = json.loads(data)
    assert doc["sport"] == league and [r["player"] for r in doc["rows"]] == ["Fixture Guard"]
    member = authtoken.verify(authtoken.mint("someone", "member")["token"])
    code, _, _ = web.dispatch(web.Request(path=path, query=query, method="GET", body=b"", member=member["sub"], role="member"))
    assert code in (401, 403)
