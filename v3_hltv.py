"""HLTV only on the v3 CS2 card (owner 2026-11-20, emphatic: "no BO3.gg data anywhere on member-page stats - HLTV only").

scrub(doc) rewrites one CS2 /line research doc so nothing in it is a BO3.gg-derived number: what the v3 card's browser side does
before the hub paints (web/v3/player.js hltvOnly - keep the two in step), applied here to the Genie's payload on a v3 request, so an
answer can never state a BO3.gg number either. Display only: never a grade input; no grade, chance, stake or projection is read or
changed (the model's own outputs and the model's own HLTV inputs stay). The classic card (v3 off) never calls this.

Removed: cs2_card stats / per_map / teammates / me / team_kill_share / checks (trade and opening kills, sample flags, days since his
last map = the min of BO3.gg and HLTV), an AWP note that is not HLTV's weapon stats, a BO3.gg (or unknown-source) rank band, the CS
API and FACEIT panels (not HLTV); the "Saved with this forecast" context rows (grader inputs, BO3.gg / merged); the grade block's
context-only factors read from those inputs. The saved L10 tally and recent average (the grader's merged BO3.gg + HLTV last 10)
are replaced by counts of the HLTV series (the verified one, else history.hltv_series)."""
import copy

CTX_GI = frozenset(("maps_played", "rating", "kpr", "kpr_last5", "adr", "kast", "hs_pct", "days_since_last_map", "recent_avg",
                    "recent_median", "recent_n"))
GI_BO3 = frozenset(("recent_avg", "recent_median", "kpr_last5", "rating", "adr", "kast", "trade_pr", "days_since_last_map"))


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _bo3_band(rb):
    return not isinstance(rb, dict) or "bo3" in str(rb.get("source") or "").lower()


def _tally(ser, line):
    if not ser or not _num(line):
        return None
    t = {"n": len(ser), "over": 0, "under": 0, "push": 0}
    for g in ser:
        t["over" if g["value"] > line else "under" if g["value"] < line else "push"] += 1
    return t


def scrub(doc):
    """A new CS2 doc with no BO3.gg-derived number (other sports: returned as is)."""
    rec = (doc or {}).get("record") or {}
    if not isinstance(doc, dict) or rec.get("sport") != "cs2":
        return doc
    d = copy.deepcopy(doc)
    d["hltv_only"] = True
    c = d.get("cs2_card")
    band_bo3 = not isinstance(c, dict) or _bo3_band(c.get("rank_band"))
    if isinstance(c, dict):
        ck = c.get("checks") or {}
        days = ck.get("days_since_last_map")
        c.update(hltv_only=True, stats={}, per_map=[], teammates=[], me=None, team_kill_share=None, csapi=None, faceit=None)
        c["checks"] = {"roster_confirmed": bool(ck.get("roster_confirmed")), "standin_for": ck.get("standin_for"),
                       "awp_note": ck.get("awp_note") if "(hltv weapon stats" in str(ck.get("awp_note") or "").lower() else None,
                       "rest_rule": bool(_num(days) and days >= 30)}
        if band_bo3:
            c["rank_band"] = None
        c.pop("hltv_v3", None)
    ctx = d.get("context")
    if isinstance(ctx, dict) and isinstance(ctx.get("rows"), list):
        ctx["rows"] = [r for r in ctx["rows"] if isinstance(r, dict) and r.get("key") not in CTX_GI and r.get("group") != "Saved with this forecast"]
    g = d.get("grade_inputs")
    if isinstance(g, dict):
        if isinstance(g.get("factors"), list):
            g["factors"] = [f for f in g["factors"] if isinstance(f, dict) and not (f.get("live") is not True and f.get("id") in GI_BO3)
                            and not (f.get("id") == "rank_band" and band_bo3)]
        if isinstance(g.get("matchup"), list):
            g["matchup"] = [m for m in g["matchup"] if isinstance(m, dict) and not (m.get("id") == "rank_band" and band_bo3)]
    h = d.get("history") if isinstance(d.get("history"), dict) else {}
    d["history"] = h
    ser = h.get("series") or h.get("hltv_series") or []
    ser = [s for s in ser if isinstance(s, dict) and _num(s.get("value"))]
    h.pop("hltv_series", None)
    h["series"] = ser or None
    line = (rec.get("offer") or {}).get("line")
    ten = ser[:10]
    h["tally"] = _tally(ten, line)
    h["avg"] = round(sum(s["value"] for s in ten) / len(ten), 1) if ten else None
    rh = d["record"].setdefault("history", {})
    rh.update(l5=_tally(ser[:5], line), l10=h["tally"], l20=_tally(ser[:20], line) if len(ser) > 10 else None, avg=h["avg"])
    return d
