"""Input limits, refused intents and secret redaction for the assistant.

- The assistant is READ-ONLY research: it never places, submits or copies a wager, and this release has no EV /
  arbitrage advice feature (owner brief 7 + 11). Such requests get a fixed answer without a model call.
- Page context sent by the client is DATA: it is passed to the model inside a <page_context> block the system prompt
  marks as untrusted; it can never change settings, tools or limits (there is no tool that could).
- redact() runs on everything the assistant emits and on every logged error: Anthropic keys (sk-ant-...), the
  configured key value itself, and bearer tokens never leave the process.
"""
from __future__ import annotations

import os
import re

from . import config

WAGER = re.compile(r"\b(place|put|make|submit|enter|lock in|book|fire|execute|buy|sell)\b[^.?!]{0,40}\b(bet|bets|wager|"
                   r"wagers|entry|entries|slip|slips|parlay|order|trade|contract|contracts)\b|\bbet (?:it|this|that) "
                   r"for me\b|\bauto[- ]?bet\b", re.I)
EV_ARB = re.compile(r"(?<![\w])(\+?ev|expected value|arbitrage|arb|arbs|sure bet|surebet|risk[- ]free|middle the line|"
                    r"kelly)(?![\w])", re.I)
REFUSAL_WAGER = ("Scout's research assistant is read-only: it can't place, submit or enter bets, slips or trades. It can "
                 "show history, current quotes and Scout forecasts for a prop.")
REFUSAL_EV = ("This release doesn't offer expected-value or arbitrage advice. The assistant can show history, current "
              "quotes and Scout's stored probabilities, each with its source.")
_SECRET = re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}|(?i:bearer)\s+[A-Za-z0-9._\-]{12,}|(?i:x-api-key)\s*[:=]\s*\S+")


def screen(question: str):
    """-> None (fine) or {"code", "answer"} for a refused intent."""
    if WAGER.search(question or ""):
        return {"code": "wager_execution_refused", "answer": REFUSAL_WAGER}
    if EV_ARB.search(question or ""):
        return {"code": "ev_arbitrage_refused", "answer": REFUSAL_EV}
    return None


def check_limits(question, page_context=None):
    lim = config.limits()
    q = (question or "").strip()
    if not q:
        return "empty_question"
    if len(q) > lim["max_question_chars"]:
        return f"question_too_long (max {lim['max_question_chars']} characters)"
    if page_context is not None and len(page_context) > lim["max_context_chars"]:
        return f"page_context_too_long (max {lim['max_context_chars']} characters)"
    return None


def redact(s):
    if s is None:
        return s
    s = str(s)
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if len(key) >= 8:
        s = s.replace(key, "[redacted]")
    return _SECRET.sub("[redacted]", s)
