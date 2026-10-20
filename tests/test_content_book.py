"""Content book (owner 2026-10-19): 1,000 distinct ideas + the product guide; the live section states only numbers from a
verified saved forecast (posts.drafts) and facts the board carries; the page is gated (admin token or CONTENT_BOOK_KEY).
Every player, line and number here is a TEST FIXTURE."""
import re

import pytest

import content_book as cb
import posts
import server
from tests.test_api_moneyline_slips import TOKEN, call, env  # noqa: F401 - pytest fixture


def test_the_library_has_1000_distinct_ideas_rules_and_the_product_guide():
    rows = cb.ideas()
    assert len(rows) == 1000 and len({r[2] for r in rows}) == 1000
    assert all(r[1] in cb.FORMATS for r in rows)
    md = cb.markdown()
    assert len(re.findall(r"(?m)^\d+\. \*\*", md)) == 1000
    assert "1-800-GAMBLER" not in md and "21+." not in md and "Research tool, not betting advice." in md and "Know the product" in md and "model estimate" in md
    for head in ("What 1Time is", "How a CS2 grade is made", "What members see and use", "What the grader is NOT"):
        assert f"### {head}" in md
    assert "A+ 80%+, A 74%+, A- 68%+, B+ 63%+, B 57%+, B- 55%+" in md and "A+ 3U, A 2U, A- 1.5U, B+ 1U, B and B- 0.5U" in md
    ideas_text = " ".join(" ".join(r[2:]) for r in rows).lower()
    for banned in ("guarantee", "can't lose", "free money", "sure thing"):
        assert banned not in ideas_text, banned                     # the ideas never promise; the rules forbid these words


def _draft(**kw):
    d = {"forecast_id": 7, "pick_id": 3, "platform": "prizepicks", "line": 24.5, "side": "More", "chance": 0.634, "grade": "B+",
         "projection": 27.1, "match_start": "2099-10-20T12:00:00Z", "last10_saved": "6/9", "text": "verified caption",
         "player": "tester", "prop": "kills", "scope": "12", "player_id": 11, "match_slug": "a-vs-b"}
    d.update(kw)
    return d


def test_live_scripts_use_only_the_saved_forecast_and_the_boards_facts():
    rows = [{"book": "prizepicks", "player_id": 11, "nick": "tester", "match_slug": "a-vs-b", "prop": "kills", "scope": "12",
             "line": 24.5, "open_line": 23.5},
            {"book": "underdog", "player_id": 11, "nick": "tester", "match_slug": "a-vs-b", "prop": "kills", "scope": "12", "line": 25.5}]
    x, = cb.live_scripts([_draft()], rows)
    assert x["line"] == "tester OVER 24.5 kills" and x["chance"] == "63.4%" and x["grade"] == "B+"
    assert x["facts"] == ["Underdog lists 25.5 for the same prop", "PrizePicks opened this line at 23.5 (bumped up)"]
    text = " ".join(s for _, s in x["scripts"])
    assert "63.4% model estimate" in text and "27.1" in text and "6 over, 3 under" in text
    nums = set(re.findall(r"\d+(?:\.\d+)?", text))
    assert nums <= {"24.5", "63.4", "27.1", "25.5", "23.5", "6", "3", "9", "10", "1", "2"}, nums      # nothing invented
    y, = cb.live_scripts([_draft(scope="1", last10_saved="6/9")], [])
    assert not any(t == "Grader's own record" for t, _ in y["scripts"]) and y["facts"] == []      # Map 1: no record line


def test_page_is_gated_and_renders_the_live_lines(env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CONTENT_BOOK_KEY", "creator-key-test")
    monkeypatch.setattr(posts, "drafts", lambda rows, bk, db, min_grade="B-": {"drafts": [_draft(platform=bk)] if bk == "prizepicks" else [],
                                                                               "skipped": []})
    monkeypatch.setattr(server, "results_payload", lambda: {"picks": {"picks": 10, "matches": 4, "printed": 0.61, "observed": 0.5}, "as_of": "x"})
    assert call(env, "GET", "/content-book")[0] == 404
    assert call(env, "GET", "/content-book?key=wrong")[0] == 404
    st, body = call(env, "GET", "/content-book?key=creator-key-test")
    page = body.decode()
    assert st == 200 and "tester OVER 24.5 kills" in page and "63.4%" in page and "verified caption" in page
    assert "printed 61.0% average chance, won 50.0%" in page and "The 1,000 ideas" in page
    st, md = call(env, "GET", "/content-book.md", admin=True)
    assert st == 200 and md.decode().startswith("# 1Time grader - content book")
