"""The assistant runner - a SEPARATE process (never the esports web process, never at import):

  python -m sports.assistant.runner            loop: take queued runs, answer them, write events
  python -m sports.assistant.runner --once     answer what is queued now, then exit
  python -m sports.assistant.runner --status   print config.status() (no key material) and exit

Refuses to start unless config.status()["enabled"] (SPORTS_ASSISTANT on, ANTHROPIC_API_KEY set, anthropic installed).
Bounded: SPORTS_ASSIST_RUNNER_CONCURRENCY parallel runs; a run older than SPORTS_ASSIST_RUN_TIMEOUT_S is stopped and
failed (question refunded); runs whose runner died are failed by fail_stale() with a final event; old runs purged.
Every run reads the canonical store through a read-only (mode=ro) connection.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import store
from ..contracts import now_utc
from . import config, orchestrator, policy, runs
from .tools import ToolContext

CACHED_TYPES = ("dates", "tool_call", "tool_result", "delta", "final")


def process(run, llm, runs_path=None, store_path=None, now=None):
    """Answer one claimed run end to end. Safe to call from tests with a ScriptedLLM."""
    con = runs.connect(runs_path)
    t0 = time.time()
    timeout = config.limits()["run_timeout_s"]
    timed_out = {"v": False}

    def emit(typ, data):
        runs.append(con, run["run_id"], typ, data)

    def should_cancel():
        if time.time() - t0 > timeout:
            timed_out["v"] = True
            return True
        return runs.cancel_requested(con, run["run_id"])

    try:
        try:
            scon = store.connect(store_path, read_only=True)
        except FileNotFoundError:
            emit("error", {"code": "sports_store_not_ready", "message": "Grader's sports data is not available yet."})
            runs.finish(con, run["run_id"], "failed", error="sports_store_not_ready", refund=True)
            return "failed"
        try:
            out = orchestrator.answer(run, llm, ToolContext(scon, now or now_utc()), emit, should_cancel)
        finally:
            scon.close()
        if timed_out["v"]:
            emit("error", {"code": "run_timeout", "message": "The answer took too long and was stopped."})
            runs.finish(con, run["run_id"], "failed", out.get("usage"), "run_timeout", llm.model, refund=True)
            return "failed"
        ended = runs.finish(con, run["run_id"], out["state"], out.get("usage"), out.get("error"), llm.model,
                            refund=out["state"] == "failed")
        if not ended:                                   # fail_stale ended it meanwhile: its state stands (#35)
            return runs.get_run(con, run["run_id"])["state"]
        if out["state"] == "done" and out.get("cacheable") and run.get("cache_key"):
            evs = [{"type": e["type"], "data": e["data"]} for e in runs.events_after(con, run["run_id"], 0, 1000)
                   if e["type"] in CACHED_TYPES]
            runs.put_cache(con, run["cache_key"], evs)
        return out["state"]
    except Exception as e:                              # noqa: BLE001 - a run failure never kills the runner
        msg = policy.redact(f"{type(e).__name__}: {e}")[:300]
        sys.stderr.write(f"assistant run {run['run_id']} failed: {msg}\n")
        try:
            emit("error", {"code": "internal_error", "message": "The assistant hit an error. Try again."})
            runs.finish(con, run["run_id"], "failed", error="internal_error", refund=True)
        except Exception:                               # noqa: BLE001
            pass
        return "failed"
    finally:
        con.close()


def drain(llm_factory, once=False, poll_s=0.5, stop=None):
    lim = config.limits()
    stop = stop or threading.Event()
    with ThreadPoolExecutor(max_workers=lim["runner_concurrency"], thread_name_prefix="assist") as pool:
        active = set()
        last_maint = 0.0
        while not stop.is_set():
            con = runs.connect()
            try:
                runs.runner_beat(con, config.status())
                if time.time() - last_maint > 60:
                    runs.fail_stale(con, lim["run_timeout_s"] * 2)
                    runs.purge(con)
                    last_maint = time.time()
                active = {f for f in active if not f.done()}
                claimed = None
                if len(active) < lim["runner_concurrency"]:
                    claimed = runs.claim(con)
            finally:
                con.close()
            if claimed:
                active.add(pool.submit(process, claimed, llm_factory()))
                continue
            if once and not active:
                break
            time.sleep(poll_s)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m sports.assistant.runner")
    p.add_argument("--once", action="store_true")
    p.add_argument("--status", action="store_true")
    a = p.parse_args(argv)
    st = config.status()
    if a.status:
        print(json.dumps(st, indent=2))
        return
    if not st["enabled"]:
        raise SystemExit("assistant disabled: " + "; ".join(st["disabled_because"]))
    from .llm import AnthropicLLM
    drain(AnthropicLLM, once=a.once)


if __name__ == "__main__":
    main()
