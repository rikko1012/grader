"""Sports switches - all OFF unless the environment says otherwise (owner brief 2).

  SPORTS_UI      the member page /sports
  SPORTS_API     the JSON API /api/sports/*  (the page needs it too)
  SPORTS_INGEST  the worker's ingestion / settlement jobs (python -m sports.worker ...)
  SPORTS_TRAIN   the worker's training / fitting jobs

On = "1", "true", "yes" or "on" (any case). Anything else, or unset, is off. server.py reads SPORTS_UI / SPORTS_API with
its own two-line check BEFORE importing anything from sports, so with the flags off the web process never imports this
package. Rollback = set the flags off (no database restore, no esports rollback).
"""
import os

FLAGS = ("SPORTS_UI", "SPORTS_API", "SPORTS_INGEST", "SPORTS_TRAIN")
_ON = {"1", "true", "yes", "on"}


def on(name: str) -> bool:
    if name not in FLAGS:
        raise KeyError(name)
    return os.environ.get(name, "").strip().lower() in _ON


def state() -> dict:
    return {f: on(f) for f in FLAGS}


class FlagOff(RuntimeError):
    pass


def require(name: str):
    if not on(name):
        raise FlagOff(f"{name} is off (set {name}=1 to run this)")
