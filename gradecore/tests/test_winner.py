from gradecore import winner


def test_market_margin_and_orientation():
    x = winner.book_signal([-150, 130])
    assert 0 < x < .2 and winner.book_signal([130, -150]) == -x
    assert winner.book_signal([None, -110]) is None


def test_matchup_can_flip_a_close_market_without_inventing_a_probability():
    pick = winner.decide(["A","B"], {"book": .02, "exchange": -.01}, {"form": -.4, "rating": -.2})
    assert pick["team"] == "B" and pick["basis"] == "Market and matchup"
    assert [f["team"] for f in pick["factors"]] == ["A","B","B","B"]
    assert abs(sum(f["weight"] for f in pick["factors"])-1) < .001
    assert "prob" not in pick


def test_neutral_and_missing_evidence_are_order_independent_explicit_tiebreaks():
    for teams in (["A","B"], ["B","A"]):
        for signals in ({}, {"midpoint": 0}, {"bad": float("nan")}):
            result = winner.decide(teams, signals, {})
            assert result["team"] == "A" and "tie-break" in result["basis"]


def test_partial_book_and_spread():
    game = {"teams": ["Away","Home"], "quotes":[{"source":"Book","kind":"american","prices":[140,None]}]}
    assert winner.game_pick(game)["team"] == "Home"
    game["quotes"][0]["prices"] = [-110,None]
    result = winner.game_pick(game, spreads={"Book": -.2})
    assert result["team"] == "Home" and result["factors"][0]["label"] == "Book spread"


def test_cs2_context_and_expired_book():
    m = {"teams":{"a":"Alpha","b":"Beta"}, "thunderpick":{"eligible":True, "p_a":.8,"p_b":.2},
         "platforms":{"kalshi":{"usable":True,"prob_a":.47}},
         "side":{"composite":{"signals":{"form":-.5,"rating":None}}}}
    read = lambda b,a,c: b["prob_a"]
    assert winner.cs2_pick(m,read)["team"] == "Alpha"
    assert winner.cs2_pick(m,read,False)["team"] == "Beta"
