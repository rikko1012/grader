import book_providers as bp


def test_bovada_follows_existing_sportsbook_access_with_independent_off_switch(monkeypatch):
    monkeypatch.delenv("SCOUT_BOOK_BOVADA", raising=False)
    monkeypatch.delenv("SCOUT_BOOK_FANDUEL", raising=False)
    assert not bp.enabled("bovada")
    monkeypatch.setenv("SCOUT_BOOK_FANDUEL", "1")
    assert bp.enabled("bovada")
    assert bp.public("bovada")["label"] == "Bovada"
    monkeypatch.setenv("SCOUT_BOOK_BOVADA", "0")
    assert not bp.enabled("bovada") and bp.enabled("fanduel")
