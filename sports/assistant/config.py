"""Assistant settings, read from the environment at call time (tests and operators can change them without a restart
of anything but the runner).

  SPORTS_ASSISTANT                 on/off switch for the assistant (default OFF; also needs SPORTS_API for the routes)
  ANTHROPIC_API_KEY                read ONLY by the runner process when it builds the client; never logged, printed,
                                   stored or sent to a page. Absent -> the assistant is disabled with a clear status.
  SPORTS_ASSIST_MODEL              Claude model id (default claude-opus-5-5)
  SPORTS_ASSIST_EFFORT             low / medium / high (default medium; set explicitly - Opus 5.5's default is medium)
  SPORTS_ASSIST_FALLBACKS          1 = server-side refusal fallbacks ("default" routing), 0 = off (default 1)
  SPORTS_ASSIST_MAX_OUTPUT_TOKENS  per model turn (default 8000, includes thinking)
  SPORTS_ASSIST_MAX_ANSWER_CHARS   visible answer cap (default 4000)
  SPORTS_ASSIST_MAX_QUESTION_CHARS question cap (default 1000)
  SPORTS_ASSIST_MAX_CONTEXT_CHARS  page-context cap (default 4000)
  SPORTS_ASSIST_MAX_TOOL_ROUNDS    tool round trips per answer (default 6)
  SPORTS_ASSIST_DAILY_QUESTIONS    per member per UTC day (default 30; cached answers do not count)
  SPORTS_ASSIST_DAILY_TOKENS       per member per UTC day, model input+output tokens (default 400000)
  SPORTS_ASSIST_CACHE_S            identical-question cache life (default 600)
  SPORTS_ASSIST_RUN_TIMEOUT_S      a run older than this is failed by the runner (default 180)
  SPORTS_ASSIST_RUNNER_CONCURRENCY parallel runs in the runner (default 2)
"""
from __future__ import annotations

import os

DEFAULT_MODEL = "claude-opus-5-5"
PROMPT_VERSION = "sa-1"
_ON = {"1", "true", "yes", "on"}


def _int(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def enabled_flag() -> bool:
    return os.environ.get("SPORTS_ASSISTANT", "").strip().lower() in _ON


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def sdk_installed() -> bool:
    import importlib.util
    return importlib.util.find_spec("anthropic") is not None


def model_id() -> str:
    return os.environ.get("SPORTS_ASSIST_MODEL", "").strip() or DEFAULT_MODEL


def effort() -> str:
    e = os.environ.get("SPORTS_ASSIST_EFFORT", "medium").strip().lower()
    return e if e in ("low", "medium", "high") else "medium"


def fallbacks_on() -> bool:
    return os.environ.get("SPORTS_ASSIST_FALLBACKS", "1").strip().lower() in _ON


def limits() -> dict:
    return {"max_output_tokens": _int("SPORTS_ASSIST_MAX_OUTPUT_TOKENS", 8000),
            "max_answer_chars": _int("SPORTS_ASSIST_MAX_ANSWER_CHARS", 4000),
            "max_question_chars": _int("SPORTS_ASSIST_MAX_QUESTION_CHARS", 1000),
            "max_context_chars": _int("SPORTS_ASSIST_MAX_CONTEXT_CHARS", 4000),
            "max_tool_rounds": _int("SPORTS_ASSIST_MAX_TOOL_ROUNDS", 6),
            "daily_questions": _int("SPORTS_ASSIST_DAILY_QUESTIONS", 30),
            "daily_tokens": _int("SPORTS_ASSIST_DAILY_TOKENS", 400000),
            "cache_s": _int("SPORTS_ASSIST_CACHE_S", 600),
            "run_timeout_s": _int("SPORTS_ASSIST_RUN_TIMEOUT_S", 180),
            "runner_concurrency": _int("SPORTS_ASSIST_RUNNER_CONCURRENCY", 2)}


def status() -> dict:
    """What an admin sees. Says WHY the assistant is off; never contains the key or any part of it."""
    reasons = []
    if not enabled_flag():
        reasons.append("SPORTS_ASSISTANT is off")
    if not api_key_present():
        reasons.append("ANTHROPIC_API_KEY is not set in the runner environment")
    if not sdk_installed():
        reasons.append("the anthropic Python package is not installed")
    return {"enabled": not reasons, "disabled_because": reasons, "model": model_id(), "effort": effort(),
            "fallbacks": fallbacks_on(), "api_key_present": api_key_present(), "sdk_installed": sdk_installed(),
            "prompt_version": PROMPT_VERSION, "limits": limits()}
