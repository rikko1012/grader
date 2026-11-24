"""Download existing Sports reports. GET-only; never trains, settles or changes flags.

ADMIN_TOKEN is read from the process environment. It stays in request headers to
the two fixed Scout origins. Output contains report data, never credentials.
"""
import argparse
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
import urllib.error
import urllib.request

API = "https://scout-sports-api.onrender.com"
SCOUT = "https://cs2-scout.onrender.com"
REPORTS = {
    "operations": "/api/sports/admin/ops",
    "gradecheck": "/api/sports/admin/model/gradecheck",
    "learning": "/api/sports/admin/model/learning",
    "model-comparisons": "/api/sports/admin/model/comparisons",
    "nba-results": "/api/sports/results?league=nba",
    "nfl-results": "/api/sports/results?league=nfl",
    "results-summary": "/api/sports/results/public",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials to a redirect destination.


def get_json(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
        raw = response.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError("report exceeds download limit")
        return json.loads(raw)


def collect(out, admin, fetch=get_json):
    if not admin:
        raise ValueError("Set ADMIN_TOKEN in the local process environment; do not paste it into chat.")
    session = fetch(SCOUT + "/api/sports/token", {"X-Admin-Token": admin, "X-Load-Test": admin})
    token = session.get("token") if isinstance(session, dict) else None
    if not isinstance(token, str) or not token.startswith("st1."):
        raise ValueError("Scout did not issue a Sports token; check existing admin access and service configuration.")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"retrieved_at": datetime.now(timezone.utc).isoformat(),
                "source": API, "mode": "read-only saved reports", "reports": {}}
    for name, path in REPORTS.items():
        try:
            payload = fetch(API + path, {"Authorization": "Bearer " + token})
            target = out / (name + ".json")
            temp = target.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temp.replace(target)
            manifest["reports"][name] = {"ok": True, "file": target.name}
        except (OSError, ValueError, urllib.error.HTTPError) as error:
            # No raw exception strings, URLs, request headers or response bodies.
            manifest["reports"][name] = {"ok": False, "error": type(error).__name__,
                                         "http_status": getattr(error, "code", None)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="output/sports-reports")
    args = parser.parse_args()
    try:
        manifest = collect(args.out, os.environ.get("ADMIN_TOKEN", ""))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    except OSError as error:
        print("Sports connection failed: " + type(error).__name__, file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2))
    return 0 if all(r["ok"] for r in manifest["reports"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

