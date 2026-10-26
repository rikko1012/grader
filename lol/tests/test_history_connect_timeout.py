"""Audit 2026-10-26: the LoL history Postgres connect has a bounded timeout (no network, no database server)."""
import sys

import pytest


def test_lol_history_postgres_connect_has_a_timeout(monkeypatch):
    import types
    seen = {}

    class Stop(Exception):
        pass

    def fake_connect(dsn, **kw):
        seen.update(kw)
        raise Stop()
    monkeypatch.setitem(sys.modules, "psycopg2", types.SimpleNamespace(connect=fake_connect))
    from lol import history
    with pytest.raises(Stop):
        history.DB(dsn="postgresql://example.invalid/db")
    assert seen.get("connect_timeout") and seen["connect_timeout"] <= 30
