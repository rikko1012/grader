"""Attribution audit of the ten published picks (owner 2026-10-16). For each: every saved board forecast of that player /
stat / line logged BEFORE the slate was copied (the user pasted it at 2026-10-16T02:01:12Z), the latest one per platform,
compared with the slate / post and the official settlement. No reconstruction from later data."""
import json, io, os, sys
from datetime import datetime, timezone
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
D = os.environ.get("RL_DATA", "data") + "/"     # picks.json + forecasts.json from the paged admin export
T_REF = datetime(2026, 10, 16, 2, 1, 12, tzinfo=timezone.utc)
SLATE = [("yiksrezo", "More", 26.5, "kills", "A-"), ("n1ssim", "Less", 15.5, "hs", "B+"), ("Jimpphat", "Less", 30.5, "kills", "B"),
         ("jackasmo", "More", 27.5, "kills", "B"), ("Qlocuu", "More", 27.5, "kills", "B"), ("leakz", "Less", 34.5, "kills", "B"),
         ("Djon8", "More", 29.5, "kills", "B"), ("NAF", "Less", 14.5, "hs", "B"), ("Staehr", "Less", 32.5, "kills", "B"),
         ("poiii", "Less", 31.5, "kills", "B-")]
P = json.load(open(D + "picks.json"))
F = json.load(open(D + "forecasts.json"))
ts = lambda s: datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc) if s else None
byp = {}
for f in F:
    byp.setdefault(f["pick_id"], []).append(f)
out = []
for nick, side, line, prop, grade in SLATE:
    cands = [p for p in P if (p.get("player") or "").lower() == nick.lower() and p.get("prop") == prop
             and (p.get("match_start") or "") >= "2026-10-15T20" and (p.get("match_start") or "") < "2026-10-17"]
    print(f"\n=== {nick} {side} {line} {prop} (slate grade {grade}) - {len(cands)} pick rows")
    rec = {"slate": [nick, side, line, prop, grade], "platforms": []}
    for p in sorted(cands, key=lambda p: (str(p.get("book")), p["line"])):
        fs = sorted((f for f in byp.get(p["id"], []) if ts(f["logged_at"]) <= T_REF), key=lambda f: f["id"])
        later = [f for f in byp.get(p["id"], []) if ts(f["logged_at"]) > T_REF and ts(f["logged_at"]) < ts(p["match_start"])]
        last = fs[-1] if fs else None
        tag = (f"pick {p['id']} {p.get('book') or 'member'} {p.get('line_type')} line {p['line']} scope {p.get('scope')} "
               f"test={p.get('test')} on_board={p.get('on_board')} start {p['match_start']} | settled {p.get('status')} actual {p.get('actual')} hit {p.get('hit')}")
        print(" ", tag)
        if last:
            print(f"    latest saved before 02:01Z: forecast {last['id']} {last['logged_at']} side {last['pick']} p_pick {last['p_pick']} "
                  f"p_more {last['p_more']} grade {last['grade']} units {last['units']} lambda {last['lambda']} model {last['model_version']}")
        else:
            print("    no saved forecast before 02:01Z")
        if later:
            l2 = later[-1]
            print(f"    later pre-match forecasts: {len(later)}; last {l2['logged_at']} side {l2['pick']} p {l2['p_pick']} grade {l2['grade']} units {l2['units']}")
        rec["platforms"].append({"pick": p, "before": last, "n_before": len(fs), "last_prematch": later[-1] if later else None})
    out.append(rec)
json.dump(out, open(D + "reconcile.json", "w"), default=str, indent=1)
