"""Offer settlement from typed stat observations, per platform rule version, with revisions (owner brief 3 / 12).

settle_offer(offer, obs, event, settled_at) -> [Settlement] (one per OFFERED side)
  - event not final / postponed / unknown          -> nothing (stays unsettled; never guessed)
  - no observation for the player in a final game  -> unresolved ("player not in the box score")
  - participation unknown                          -> unresolved
  - did not play:  PrizePicks / Underdog -> void (both void a DNP: PrizePicks dnps-reboots-and-ties, Underdog
                   10982402); Kalshi -> void with reason "fair-price settlement" (contract rule: an inactive player or
                   one who never enters settles to a fair price, not to No)
  - played: value vs line -> over / under / push (a whole-number tie; both pick'em platforms void a tied pick and
    reduce the entry - the entry effect belongs to the slip settlement, not to this leg record)
  - combos / unsupported markets are not settled here (capabilities.gate refuses them upstream)

record(con, settlements) stores them; a later different result for the same (platform, offer, side) is a NEW revision
that names the one it supersedes (provider corrections), the earlier one is kept.
"""
from __future__ import annotations

from dataclasses import replace

from . import markets, store
from .contracts import Settlement

SETTLE_RULES = {"prizepicks": "pp-dnp-void/tie-void (help 2026-10-19)",
                "underdog": "ud-dnp-void/tie-void (help 10982402)",
                "kalshi": "kalshi-dnp-fair-price; postponed >48h fair price"}
FAMILY = {"nba": "basketball", "wnba": "basketball", "cbb": "basketball", "nfl": "football", "cfb": "football"}


def _sid(platform, offer_id, side, outcome, value, rule):
    import hashlib
    raw = f"{platform}|{offer_id}|{side}|{outcome}|{value}|{rule}"
    return "st-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def settle_offer(offer, obs_by_stat, event, settled_at, forecast_ids=()):
    """obs_by_stat: {part: StatObservation} for this player in this event (latest revisions)."""
    if event is None or event.status != "final":
        return []
    fam = FAMILY.get(offer.league)
    rule = SETTLE_RULES.get(offer.platform.split(":")[0], "book house rules (unverified)")
    spec = markets.market(fam, offer.stat) if offer.stat else None
    out = []
    for side in offer.offered_sides():
        status, outcome, value, reason, ref = "unresolved", None, None, None, None
        if spec is None:
            reason = "market not settled here"
        elif not obs_by_stat:
            reason = "player not in the final box score"
        else:
            parts = {}
            participation = {o.participation for o in obs_by_stat.values()}
            ref = ",".join(sorted(f"{o.source_ref}:{o.stat}" for o in obs_by_stat.values()))[:400]
            if "did_not_play" in participation and "played" not in participation:
                status = "void"
                reason = ("did not play: fair-price settlement (Kalshi contract rule)" if offer.platform == "kalshi"
                          else "did not play: void")
            elif participation == {"played"}:
                for p in spec["parts"]:
                    o = obs_by_stat.get(p)
                    parts[p] = o.value if o is not None else (0.0 if fam == "football" else None)
                value = markets.value(fam, offer.stat, parts)
                if value is None:
                    reason = "stat missing from the box score"
                else:
                    outcome = "over" if value > offer.line else ("under" if value < offer.line else "push")
                    status = "push" if outcome == "push" else ("win" if outcome == side else "loss")
            else:
                reason = "participation unknown"
        out.append(Settlement(
            settlement_id=_sid(offer.platform, offer.offer_id, side, outcome, value, rule), platform=offer.platform,
            offer_id=offer.offer_id, side=side, line=offer.line, event_id=offer.event_id, player_id=offer.player_id,
            stat=offer.stat or "", status=status, outcome=outcome, source_value=value, source="espn", source_ref=ref,
            rule_version=rule, affected_forecast_ids=tuple(forecast_ids), reason=reason, settled_at=settled_at,
            observed_at=settled_at, known_at=settled_at))
    return out


def at_line(s, line, side=None):
    """(status, outcome) of `side` at `line` from a leg settlement recorded at s.line (LINE_CORRECTNESS.md C10).
    A settlement is keyed by (platform, offer_id, side) and records the offer's CURRENT line at settlement; a forecast
    or slip leg made on an earlier revision at another line is scored at ITS OWN line from the recorded box-score
    value. void (did not play) and unresolved do not depend on the line. -> (None, None) without a settlement."""
    if s is None:
        return None, None
    side = side or s.side
    if line is None or (abs(float(line) - float(s.line)) < 1e-9 and side == s.side):
        if side == s.side:
            return s.status, s.outcome
    if s.status in ("void", "unresolved"):
        return s.status, None
    if s.source_value is None:
        return "unresolved", None
    v, ln = float(s.source_value), float(line if line is not None else s.line)
    outcome = "over" if v > ln else ("under" if v < ln else "push")
    return ("push" if outcome == "push" else ("win" if outcome == side else "loss")), outcome


def record(con, settlements):
    """Store with revision chaining. -> counts."""
    c = {"inserted": 0, "unchanged": 0, "revision": 0}
    for s in settlements:
        prev = store.latest(con, "settlement", s.platform, f"{s.offer_id}|{s.side}")
        if prev is not None:
            if (prev.status, prev.outcome, prev.source_value, prev.rule_version) == \
                    (s.status, s.outcome, s.source_value, s.rule_version):
                c["unchanged"] += 1
                continue
            s = replace(s, revision=prev.revision + 1, supersedes=prev.settlement_id)
        res = store.put(con, s)
        c["revision" if prev is not None else "inserted"] += 1 if res["status"] != "duplicate" else 0
    return c
