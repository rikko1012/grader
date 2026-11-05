"""The board's book toggle (workspace_books, CS2-side, imports no sports package) copies the books worker's fetch ladder
into book_providers.json; this keeps the two identical so a quote is never hidden between two scheduled fetches."""
import json
from pathlib import Path

from sports.books import schedule

ROOT = Path(__file__).resolve().parents[2]


def test_book_providers_ladder_matches_the_books_worker():
    cfg = json.loads((ROOT / "book_providers.json").read_text(encoding="utf-8"))
    for pid in ("fanduel", "betrivers"):
        feed = cfg["providers"][pid]["feed"]
        assert [tuple(x) for x in feed["ladder"]] == [tuple(x) for x in schedule.LADDERS["full"]]
        assert feed["stale_min_s"] == schedule.STALE_MIN_S
