"""Finish-fixes 2026-10-18 item 5 (slip chance + recheck), the brief's cases one by one. Every row is a TEST FIXTURE."""
from datetime import timedelta

import pytest

import slip_builder as sb
from slip_probability import summary
from tests.test_slip_builder import M, NOW, _boards, board, private_db, row  # noqa: F401 - private_db is an autouse fixture


def test_whole_number_push_is_not_an_all_win_chance():
    leg = lambda line: {"side": "More", "line": line, "p": .55 / .95, "model_outcomes": {"over": .55, "under": .40, "push": .05}}
    full = summary([leg(14), leg(13)])
    assert full["all_hit"] == pytest.approx(.3025) and full["all_hit_basis"] == "shown_chance"   # (.55/.95 x .95) x 2 (closeout slipsem)
    old = summary([{k: v for k, v in leg(14).items() if k != "model_outcomes"}] * 2)                   # an older record
    assert old["all_hit"] == pytest.approx(.335180, abs=1e-6) and old["all_hit_basis"] == "conditional_no_push"
    half = {"side": "Less", "line": 20.5, "p": .6, "model_outcomes": {"over": .4, "under": .6, "push": 0.0}}
    assert summary([half, half])["all_hit"] == pytest.approx(.36)                                      # half lines unchanged
    assert summary([half, leg(14)])["all_hit"] == pytest.approx(.6 * .55)                              # mixed
    bad = dict(leg(14), model_outcomes={"over": .55, "under": .40, "push": .2})                        # not a distribution
    assert summary([bad])["all_hit_basis"] == "conditional_no_push"                                    # never trusted


def _slip(rows, **cfg):
    res = sb.build(rows, dict({"sizes": [2], "count": 1}, **cfg), model=M, now=NOW)
    return res["slips"][2][0]


def _changed(rows, slip, edit):
    moved = [dict(r) for r in rows]
    tgt = next(r for r in moved if r["id"] == slip["legs"][0]["row_id"])
    edit(tgt)
    return sb.verify_before_copy(slip, _boards(moved, NOW.timestamp()), now=NOW, model=M)


@pytest.mark.parametrize("name,edit", [
    ("moved line", lambda r: r.update(line=31.5)),
    ("side withdrawn", lambda r: r.update(higher=None)),
    ("stat changed under the same id", lambda r: r.update(prop="hs")),
    ("maps changed under the same id", lambda r: r.update(scope="1")),
    ("market type changed", lambda r: r.update(alt=True)),
    ("match locked", lambda r: r.update(start=(NOW - timedelta(minutes=1)).isoformat())),
    ("prop removed", lambda r: r.update(id="gone")),
])
def test_recheck_rejects_every_change_and_never_substitutes(name, edit):
    rows = board(3, per_match=1, seed=31)
    slip = _slip(rows)
    assert sb.verify_before_copy(slip, _boards(rows, NOW.timestamp()), now=NOW, model=M)["action"] == "copy"
    v = _changed(rows, slip, edit)
    assert v["ok"] is False and v["action"] == "rebuild", name
    assert v["legs"][0]["status"] == "changed" and "p" not in v["legs"][0], name


def test_recheck_needs_the_slips_own_platform():
    rows = board(3, per_match=1, seed=32)
    slip = _slip(rows)
    pp_only = {"prizepicks": _boards(rows, NOW.timestamp())["underdog"]}                             # another platform's board
    assert sb.verify_before_copy(slip, pp_only, now=NOW, model=M)["action"] == "rebuild"


@pytest.mark.parametrize("side", ["More", "Less"])
def test_overs_only_and_unders_only_mean_what_they_say(side):
    rows = [dict(r, side="More" if i % 2 else "Less") for i, r in enumerate(board(8, per_match=2, seed=33))]
    res = sb.build(rows, {"sizes": [2, 3], "count": 3, "side": side}, model=M, now=NOW)
    legs = [l for n in (2, 3) for s in res["slips"][n] for l in s["legs"]]
    assert legs and all(l["side"] == side for l in legs)
