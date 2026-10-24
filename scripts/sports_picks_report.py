"""Owner / admin report of tracked member picks (sports/picks.py pk-1) from the live sports service. Read-only.

usage (secrets from the environment only, never printed):
  SPORTS_TOKEN_SECRET=... python scripts/sports_picks_report.py [--api https://scout-sports-api.onrender.com] [--json]

Mints a short-lived ADMIN st1 token locally with sports/authtoken.py (the same secret as both services), then reads
GET /api/sports/admin/picks: counts, W-L-P-void, hit rate, Brier / log-loss of the displayed chances with 95%
intervals over games, by league / platform / validation state / surface. Stakes are 0; no returns are computed.
Exit 0 = report read, 2 = could not read it.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sports import authtoken  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="https://scout-sports-api.onrender.com")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        tok = authtoken.mint("owner-report", "admin", ttl=120)["token"]
    except authtoken.TokenError as e:
        print(f"cannot mint a token: {e.reason} (set SPORTS_TOKEN_SECRET)")
        return 2
    req = urllib.request.Request(a.api.rstrip("/") + "/api/sports/admin/picks", headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            rep = json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"could not read the report: {type(e).__name__}: {str(e)[:160]}")
        return 2
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0
    g = rep.get("all")
    if not g:
        print(rep.get("note") or "no picks recorded yet")
        return 0
    print(f"{rep['version']} built {rep['built_at']} - {rep['rule']}")

    def line(name, x):
        ci = x.get("hit_rate_ci95_games")
        print(f"  {name:<22} picks {x['picks']:>5}  members {x['members']:>3}  games {x['games']:>4}  "
              f"W-L-P {x['wins']}-{x['losses']}-{x['pushes']}  void {x['voids']}  pending {x['pending']}  "
              f"hit {x['hit_rate']} {ci}  Brier {x['brier']} {x.get('brier_ci95_games')}  "
              f"logloss {x['log_loss']}  mean shown {x['mean_displayed_chance']} (n={x['with_displayed_chance']})")
    line("all", g)
    for dim, vals in rep["by"].items():
        for k, x in vals.items():
            line(f"{dim}={k}", x)
    print(f"  duplicates {rep['duplicate_picks']}  corrected results {rep['corrected_results']}")
    for n in rep.get("notes", []):
        print("  - " + n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
