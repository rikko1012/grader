"""Append-only log of every slip Auto-build generated (owner 2026-11-16: "go over every 4 man ever generated" found that only each
member's latest batch was kept, so Auto-build could not be reviewed). One JSON line per DISTINCT slip, written once per shared build
(workspace_api._generate runs once per option set per GEN_TTL, however many members ask), never per request.

What a line holds: when, the options asked for, and each leg's identity as generated (sport, platform, row id, player, stat, period,
line, side, line type, printed chance, ranked chance, letter, start, the match keys the pick log settles under). No member id.
Read back only through slip_audit (admin, paged). Never read on a member path; a failed write never costs a member their slips.

AUTOBUILD_LOG=0 turns it off (no deploy). The file stops growing at MAX_BYTES (said once in the server log)."""
import json
import os
import threading
from collections import OrderedDict

VERSION = "abl-1"
NAME = "autobuild_log.jsonl"
MAX_BYTES = 150 * 1024 * 1024
SEEN_MAX = 50000
_LOCK = threading.Lock()
_SEEN = OrderedDict()                   # slip signatures already written by this process (a restart may repeat a slip: readers dedupe)
_FULL = [False]


def enabled():
    return os.environ.get("AUTOBUILD_LOG", "1").strip() != "0"


def _leg(l, slug=None):
    d = {"sport": l.get("sport"), "book": l.get("book"), "row_id": l.get("row_id"), "side": l.get("side"), "line": l.get("line"),
         "line_type": l.get("line_type"), "player": l.get("player"), "team": l.get("team"), "stat": l.get("stat"), "period": l.get("period"),
         "event": l.get("event"), "start": l.get("start"), "p": l.get("p"), "rank_p": round(l["rank_p"], 4) if isinstance(l.get("rank_p"), float) else l.get("rank_p"),
         "grade": l.get("grade"), "model": l.get("model_version")}
    if l.get("awper_hs"):
        d["awper_hs"] = True
    if slug:
        d["match_slug"] = slug
    return d


def record(path, at, options, slips, slug_of=None, source="generate"):
    """Append the slips not written before. slips: slip_quality.build's own slips ({"platform", "entry_type", "legs", "all_hit", ...}).
    slug_of(leg) -> the pick log's match slug of a CS2 leg (or None). -> how many lines were written."""
    if not enabled() or not slips:
        return 0
    lines = []
    with _LOCK:
        for s in slips:
            legs = s.get("legs") or []
            sig = "|".join([str(s.get("platform")), str(s.get("entry_type"))] + sorted(str(l.get("key")) for l in legs))
            if sig in _SEEN:
                _SEEN.move_to_end(sig)
                continue
            _SEEN[sig] = 1
            if len(_SEEN) > SEEN_MAX:
                _SEEN.popitem(last=False)
            rec = {"v": VERSION, "at": at, "source": source, "platform": s.get("platform"), "entry_type": s.get("entry_type"), "n": len(legs),
                   "same_match": bool(s.get("same_match")), "all_hit": round(s["all_hit"], 4) if isinstance(s.get("all_hit"), float) else s.get("all_hit"),
                   "options": options,
                   "legs": [_leg(l, slug_of(l) if slug_of and l.get("sport") == "cs2" else None) for l in legs]}
            lines.append(json.dumps(rec, default=str, separators=(",", ":")))
        if not lines:
            return 0
        try:
            if os.path.exists(path) and os.path.getsize(path) >= MAX_BYTES:
                if not _FULL[0]:
                    _FULL[0] = True
                    print(f"autobuild_log: {path} reached {MAX_BYTES} bytes - no further lines are written", flush=True)
                return 0
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            return 0
    return len(lines)
