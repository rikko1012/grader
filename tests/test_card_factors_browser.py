"""Owner 2026-11-09: "include all the factors in cards" + "all odds on player props". In a real browser at 1440 and 390, real Handler
and fixture data (no network): every sport's hub card shows the classic card's restored panels on its Stats tab (CS2: every factor,
vs similar-ranked opponents, LAN vs online, BO3.gg stats, map pool, match and veto, HLTV, teammates (benched left out), checks,
second source, FACEIT; LoL: every factor, team win chance, the model, times; NBA / NFL: every factor, Scout projection with its
distribution and ladder, history and splits, matchup, minutes / role, matchup engine) and the full odds table on its Lines tab (every
book, alternates, best per side, seen time). No hedge words; nothing runs past the screen; the slip column is never covered.
CARD_SHOTS=<folder> keeps a screenshot of each card tab at both sizes."""
import os
from pathlib import Path

import pytest

import cs2_card_ctx
from tests.card_factors_fixture import classic_res, VETO
from tests.test_upgrade_browser import browser, env, ctx, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

FLAGS = ("SCOUT_WORKSPACE_V2", "SCOUT_BOARD_H2H", "SCOUT_GENIE", "SCOUT_CLOSING_LINES", "SCOUT_LINE_HISTORY")
SHOTS = Path(os.environ.get("CARD_SHOTS") or "")
HEDGE = ("questionable", "day-to-day", "day_to_day", "unconfirmed", "uncertain", "not validated", "undefined", "nan%", "null")


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    for k in FLAGS:
        monkeypatch.setenv(k, "1")


def seed(env, monkeypatch):
    cs2_card_ctx.reset()
    for r in env.rows:
        cs2_card_ctx.put(r["id"], cs2_card_ctx.build(classic_res(r["nick"], r["team"], r["opponent"])))
    import veto_serve
    monkeypatch.setattr(veto_serve, "for_match", lambda mid: VETO)
    # LoL: the worker's forecast inputs carry every factor and the team win chance two ways (as lol/center writes them)
    for r in env.lol.rows:
        if r.get("forecast"):
            r["forecast"].setdefault("inputs", {}).update(
                factors={"model": "lol-1f", "multiplier": 1.034, "not_pre_match": ["draft", "sides"],
                         "factors": [{"key": "kp_share", "factor": "Kill participation share", "value": 0.12, "effect_pct": 2.1},
                                     {"key": "team_pace", "factor": "Team kills per game", "value": -0.08, "effect_pct": -1.2},
                                     {"key": "league_tempo", "factor": "League tempo", "value": 0.05, "effect_pct": None}]},
                team_win={"elo": 0.652, "market": {"found": True, "p": 0.61, "liquidity": 12000}})
    env.lol.write()
    # NBA / NFL: the player files the classic player page reads (forecast detail, every book quote, Kalshi, matchup engine)
    sp = env.sports
    for pid, stat, fids, line in (("nba:player:espn:301", "points", ("fc-aaaaaaaa00000001", "fc-aaaaaaaa00000002"), 20.5),
                                  ("nfl:player:espn:401", "pass_yds", ("fc-bbbbbbbb00000001", "fc-bbbbbbbb00000002"), 264.5)):
        p = sp.players.get(pid) or dict(sp.players["nba:player:espn:301"], player="Sam Field", position="QB")
        p = dict(p)
        p["forecasts"] = {fid: {"line": line, "projection_mean": line - 0.9, "forecast_at": "2026-11-09T08:00:00Z", "model_version": "sm-2", "reason_codes": ["statistical_only", "gp:unvalidated"],
                                "contributions": [], "detail": {"interval": [line - 7, line + 6], "median": line - 1, "factors": [{"label": "Minutes, last 10", "value": 33.4, "definition": "Average minutes"}]}} for fid in fids}
        p["forecast_detail"] = {fid: {"pmf": {"bins": [[int(line) - 4 + i, 1, b] for i, b in enumerate((0.05, 0.1, 0.15, 0.2, 0.18, 0.14, 0.1, 0.08))]},
                                      "ladder": [{"line": line - 1, "p_over": 0.52, "p_under": 0.48, "p_push": 0.0}, {"line": line, "p_over": 0.44, "p_under": 0.56, "p_push": 0.0, "stored": True}]} for fid in fids}
        p["markets"] = [{"stat": stat, "scope": "full_game", "quotes": [
            {"platform": "draftkings", "line": line, "variant": "standard", "last_seen_at": "2026-11-09T09:58:00Z", "sides": [{"side": "over", "offered": True, "price": 1.87, "price_format": "decimal"}, {"side": "under", "offered": True, "price": 1.95, "price_format": "decimal"}]},
            {"platform": "underdog", "line": line + 2, "variant": "alt", "last_seen_at": "2026-11-09T09:58:00Z", "sides": [{"side": "over", "offered": True, "multiplier": 1.4}, {"side": "under", "offered": False}]}],
            "alternates": [], "kalshi": [{"line": line, "sides": [{"bid": 0.41, "ask": 0.45}], "liquidity": {"volume": 1200, "open_interest": 400}, "last_seen_at": "2026-11-09T09:58:00Z"}]}]
        p["factors"] = dict(p["factors"], engine={"label": "Matchup engine", "as_of": "2026-11-09T08:00:00Z", "rows": p["factors"]["engine"]["rows"],
                                                  "by_stat": {stat: [{"key": "opp_pos_allowed", "group": "Defensive matchup", "label": "Allowed to the position", "value": 1.07, "n": 12, "source": "ESPN box scores"},
                                                                     {"key": "pace", "group": "Pace", "label": "Opponent pace", "value": 99.1, "n": 12, "source": "ESPN"}]}})
        sp.players[pid] = p
    h = sp.history["nba:player:espn:301"]
    for i, g in enumerate(h["games"]):
        g.update(season_type="regular", parts={"min": 30 + i % 6})
    if "nfl:player:espn:401" not in sp.history:
        from tests.workspace_other_fixture import games
        sp.history["nfl:player:espn:401"] = games("pass_yds", [250, 280, 241, 301, 266, 270, 255, 290], opp="BUF")
        sp.history["nfl:player:espn:401"]["family"] = "football"


def shot(p, name):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"{name}-{p.viewport_size['width']}.png"), full_page=False)


def board(ctx, env, size):
    p = open_page(ctx, env, '/#all-sports', size)
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    if size == DESK:                               # a pick on the slip: the slip column stays open beside the card
        p.locator("#hub-rows tr[data-record] [data-hub=add]").first.click()
        p.wait_for_function("document.getElementById('hub-slip-count').textContent === '1'")
    return p


def open_card(p, sport, q):
    p.locator(f'#hub-sports [data-id={sport}]').click()
    p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].length && [...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('{sport}|'))")
    p.locator('#hub-q').fill(q)
    p.wait_for_timeout(300)
    p.locator('#hub-rows tr[data-record] .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-why', timeout=20000)
    p.wait_for_function("!document.querySelector('#hub-detail .h-detail-loading')", timeout=20000)


def tab(p, name):
    p.locator(f'#hub-detail [data-hub=card-tab][data-id={name}]').click()
    p.wait_for_function(f"document.querySelector('#hub-detail').dataset.tab === '{name}' && !document.querySelector('#hub-detail .h-detail-loading')", timeout=20000)
    p.wait_for_timeout(150)


def sections(p):
    return p.evaluate("[...document.querySelectorAll('#hub-card-body .h-fx')].map(d => d.dataset.fx)")


def not_under_slip(p):
    """the card never sits under the slip column (desktop) and fits the screen (phone)"""
    box = p.evaluate("""() => { const c = document.querySelector('#hub-card').getBoundingClientRect(); const s = document.querySelector('#u-hub .h-slip');
      const r = s && getComputedStyle(s).display !== 'none' && s.getBoundingClientRect().width > 0 ? s.getBoundingClientRect() : null; return {c: [c.left, c.right], s: r ? [r.left, r.right] : null, w: innerWidth}; }""")
    assert box['c'][1] <= box['w'] + 1, box
    if p.viewport_size['width'] >= 1100:
        assert box['s'], box
        assert box['c'][1] <= box['s'][0] + 1 or box['c'][0] >= box['s'][1] - 1, box


def body_text(p):
    return p.locator('#hub-card-body').inner_text().lower()


WANT = {
    "cs2": ("vargo", ["cs2-factors", "cs2-rank-band", "cs2-env", "cs2-stats", "cs2-maps", "cs2-match", "cs2-hltv", "cs2-mates", "cs2-checks", "cs2-csapi", "cs2-faceit"]),
    "lol": ("Guigo", ["lol-factors", "lol-win", "lol-model", "lol-times"]),
    "nba": ("Karl", ["sp-factors", "sp-forecast", "sp-splits", "sp-matchup", "sp-usage", "sp-engine"]),
    "nfl": ("Sam", ["sp-factors", "sp-forecast", "sp-splits", "sp-matchup", "sp-engine"]),
}


@pytest.mark.parametrize('size', [DESK, PHONE], ids=['1440', '390'])
@pytest.mark.parametrize('sp', ['cs2', 'lol', 'nba', 'nfl'])
def test_hub_card_shows_every_restored_panel_and_all_odds(ctx, env, monkeypatch, sp, size):
    seed(env, monkeypatch)
    p = board(ctx, env, size)
    q, want = WANT[sp]
    open_card(p, sp, q)
    tab(p, 'stats')
    got = sections(p)
    assert all(w in got for w in want), (sp, got)
    text = body_text(p)
    for w in HEDGE:
        assert w not in text, (w, sp)
    assert 'every factor' in text and ('weight 0' in text), text[:400]
    if sp == 'cs2':
        assert 'vs similar-ranked opponents' in text and 'lan vs online' in text and 'benchy' not in text
        assert '+10%' in text                                    # rank band ratio 1.097, as the classic card rounds it
        assert 'map veto' in text and 'mirage' in text
    if sp in ('nba', 'nfl'):
        assert 'splits' in text and 'home' in text and 'away' in text
        assert 'at another line' in text
    shot(p, f'cards-{sp}-stats')
    no_h_scroll(p)
    not_under_slip(p)
    tab(p, 'lines')
    odds = p.evaluate("""() => { const t = document.querySelector('#hub-card-body .h-odds-table'); if (!t) return null;
      return {rows: t.querySelectorAll('tbody tr').length, best: t.querySelectorAll('td.is-best').length, seen: [...t.querySelectorAll('time')].length,
              text: t.innerText}; }""")
    assert odds and odds['rows'] >= 2 and odds['best'] >= 1, (sp, odds)
    assert 'unavailable' not in odds['text'].lower() and 'not offered' not in odds['text'].lower()
    if sp in ('nba', 'nfl'):
        assert 'DraftKings'.lower() in odds['text'].lower() or 'draftkings' in odds['text'].lower()
        assert '−' in odds['text'] or '+' in odds['text']        # a sportsbook price in American odds beside the decimal
        assert p.locator('#hub-card-body .h-odds details').count() == 1   # the Kalshi ladder
    shot(p, f'cards-{sp}-lines')
    no_h_scroll(p)
    assert not p.errors, p.errors


def test_panels_collapse_and_reopen(ctx, env, monkeypatch):
    seed(env, monkeypatch)
    p = board(ctx, env, PHONE)
    open_card(p, 'cs2', 'vargo')
    tab(p, 'stats')
    d = p.locator('#hub-card-body .h-fx[data-fx=cs2-hltv]')
    assert d.get_attribute('open') is None
    d.locator('summary').click()
    assert d.get_attribute('open') is not None
    assert 'eco-adjusted' in d.inner_text().lower()
    assert not p.errors
