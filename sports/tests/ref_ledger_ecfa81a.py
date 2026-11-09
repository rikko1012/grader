"""REFERENCE ONLY (tests): the results ledger (sports/snapshots.py write_results / results_from_rows / _boot_mean_ci)
exactly as on origin/main ecfa81a, with head_to_head from the ecfa81a results_public copy. The streamed ledger must write
the same results.json (sports/tests/test_sports_jobs_1108.py)."""
import random
from collections import defaultdict

from sports.contracts import now_utc
from sports.snapshots import (RESULTS_EVENTS_PER_PASS, SNAP_VERSION, _event_settlements, _r, _write, player_names,
                              results_view, snap_root)
from sports.tests import ref_results_public_ecfa81a as rp_ref


def write_results(con, league, now=None, base=None):
    """The results ledger file (<league>/results.json) from the CURRENT settlements - called by the settle job right
    after it settles (sports/worker.py cmd_settle), so Results moves when games settle, not with the 3-minute board
    build (which no longer decodes every settlement). -> {"rows", "settled_by_model"}."""
    now = now or now_utc()
    root = snap_root(base) / league
    meta = {"version": SNAP_VERSION, "league": league, "built_at": now_utc(), "as_of": now}
    # STREAMED event by event (settle job cap 120 MB): only events holding a forecast are read, and only the current
    # settlements of those events are decoded (a game's pick'em board settles ~2,000 sides; most carry no forecast)
    fc_events = {r[0] for r in con.execute("SELECT DISTINCT event_id FROM records WHERE kind='forecast' AND league = ?",
                                           (league,))}
    st_events = sorted(fc_events & {r[0] for r in con.execute(
        "SELECT DISTINCT event_id FROM records WHERE kind='settlement' AND event_id LIKE ?", (f"{league}:%",))})
    rows = []
    for i in range(0, len(st_events), RESULTS_EVENTS_PER_PASS):
        part = st_events[i:i + RESULTS_EVENTS_PER_PASS]
        rows += results_view(con, league, _event_settlements(con, part), now, meta, rows_only=True)
    obj = results_from_rows(rows, meta)
    names = player_names(con, {r["player_id"] for r in obj["rows"] if r.get("player_id")}, now)
    for r in obj["rows"]:                              # N7: the player's record name, never the lowercase key
        r["player"] = names.get(r.get("player_id"))
    _write(root / "results.json", obj)
    return {"rows": obj["total_rows"], "settled_by_model": {k: v["settled"] for k, v in obj["summary"].items()}}


def _boot_mean_ci(groups, field, absolute=False, reps=400, seed=7):
    """Same whole-game weighted mean bootstrap without copying every sampled prop.

    Game totals and counts are sufficient for these five mean statistics. Keep
    group order, random draws, null exclusions and quantiles identical to _boot_ci.
    Temporary memory depends on games, not the number of historical forecasts.
    """
    if len(groups) < 2:
        return None
    totals = []
    for group in groups:
        total, count = 0.0, 0
        for row in group:
            value = row.get(field)
            if value is not None:
                total += abs(value) if absolute else value
                count += 1
        totals.append((total, count))
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        total, count = 0.0, 0
        for _ in groups:
            value, n = totals[rng.randrange(len(totals))]
            total += value
            count += n
        if count:
            vals.append(total / count)
    if not vals:
        return None
    vals.sort()
    return [_r(vals[int(0.025 * (len(vals) - 1))], 4), _r(vals[int(0.975 * (len(vals) - 1))], 4)]


def results_from_rows(rows, base_meta):
    """The ledger file body (summary per model + newest 2,000 rows) from ledger rows."""
    rows.sort(key=lambda r: (r["settled_at"] or "", r["forecast_id"]), reverse=True)
    summary = {}
    for mv in sorted({r["model_version"] for r in rows}):
        rr = [r for r in rows if r["model_version"] == mv and r["status"] == "settled"]
        groups = defaultdict(list)
        for r in rr:
            groups[r["event_id"]].append(r)
        g = list(groups.values())
        errs = [r for r in rr if r["error"] is not None]

        def mae(x):
            e = [abs(r["error"]) for r in x if r["error"] is not None]
            return sum(e) / len(e) if e else None

        def bias(x):
            e = [r["error"] for r in x if r["error"] is not None]
            return sum(e) / len(e) if e else None

        def mb(x):
            e = [r["brier"] for r in x if r["brier"] is not None]
            return sum(e) / len(e) if e else None

        def ml(x):
            e = [r["log_loss"] for r in x if r["log_loss"] is not None]
            return sum(e) / len(e) if e else None

        def mbb(x):
            e = [r.get("brier_binary") for r in x if r.get("brier_binary") is not None]
            return sum(e) / len(e) if e else None
        summary[mv] = {
            "settled": len(rr), "games": len(g),
            "void": sum(1 for r in rows if r["model_version"] == mv and r["status"] == "void"),
            "pushes": sum(1 for r in rr if r["outcome"] == "push"),
            "projection": {"n": len(errs), "mae": _r(mae(rr), 4), "mae_ci": _boot_mean_ci(g, "error", absolute=True),
                           "bias": _r(bias(rr), 4), "bias_ci": _boot_mean_ci(g, "error")},
            "probability": {"n": len(rr), "brier": _r(mb(rr), 5), "brier_ci": _boot_mean_ci(g, "brier"),
                            "log_loss": _r(ml(rr), 5), "log_loss_ci": _boot_mean_ci(g, "log_loss"),
                            "brier_binary": _r(mbb(rr), 5), "brier_binary_ci": _boot_mean_ci(g, "brier_binary"),
                            "brier_binary_n": sum(1 for r in rr if r.get("brier_binary") is not None)},
            "returns": {"recorded": sum(len(r["payouts"]) for r in rows if r["model_version"] == mv)}}
    rp = rp_ref
    hh = rp.head_to_head(rows)
    return {**base_meta, "rows": rows[:2000], "total_rows": len(rows), "summary": summary,
            "head_to_head": {"version": rp.HH_VERSION, "method": rp.HH_METHOD, "rows": hh},
            "method": {"brier": "three-outcome Brier (over / under / push) on the forecast's unconditional chances: "
                                "the sum over the three outcomes, range 0-2 - a 50/50 forecast with no push scores "
                                "0.50 on it (= 2 x the binary Brier 0.25), so ~0.5 means coin-flip skill, not a bug",
                       "brier_binary": "binary Brier on the chance of Over given no push vs the settled Over / Under "
                                       "(pushes left out): range 0-1, 0.25 = always 50%",
                       "log_loss": "-ln(chance the forecast gave the settled outcome)",
                       "error": "settled value minus the forecast's projection mean",
                       "ci": "95% bootstrap interval, whole games resampled (400 draws, seed 7)",
                       "returns": "only stakes and payouts a settlement record states; none are assumed"}}


