"""Arithmetic tests price fixture books; which REAL books settle like the app is gradecore.book_rules' own table and is tested in
tests/test_ev_two_books.py. This helper states a fixture's assumption out loud: these books are independent of each other and
void a sit-out, as the app does."""
from gradecore import book_rules

VOID = {"dnp": "void", "overtime": "included", "push": "refund", "source": "test fixture", "read_at": "-"}


def like_the_app(monkeypatch, books=("fanduel", "betrivers", "draftkings", "bovada")):
    monkeypatch.setattr(book_rules, "SETTLEMENT", {**book_rules.SETTLEMENT, **{(b, s): VOID for b in books for s in ("nba", "nfl")}})
    # a fixture book is its own family unless the real map names a shared supplier (Unibet AU -> Kambi); a book the real map
    # leaves unlisted (DraftKings, PointsBet AU) is stated independent here, as the fixture's own assumption
    fam = lambda b: b if book_rules.FAMILY.get(b, book_rules.UNLISTED) == book_rules.UNLISTED else book_rules.FAMILY[b]
    monkeypatch.setattr(book_rules, "FAMILY", {**book_rules.FAMILY, **{b: fam(b) for b in books}})
