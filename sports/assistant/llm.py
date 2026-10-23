"""The model behind the assistant: one streamed Messages API turn at a time (the orchestrator owns the tool loop).

AnthropicLLM uses the official SDK (imported lazily, only in the runner process):
  - model id from SPORTS_ASSIST_MODEL (default claude-opus-5-5), adaptive thinking, explicit effort
  - streaming (client.beta.messages.stream when refusal fallbacks are on, else client.messages.stream)
  - the frozen system prompt + tool list are the cached prefix (cache_control on the system block)
  - server-side refusal fallbacks ("default" routing, beta server-side-fallback-2026-07-01) unless
    SPORTS_ASSIST_FALLBACKS=0
  - tools carry eager_input_streaming; inputs are validated by tools.validate before anything runs; a turn whose
    tool JSON could not be parsed at all (ValueError from the stream) is re-issued at most twice
  - the API key is resolved by the SDK from ANTHROPIC_API_KEY; this module never reads, logs or returns it
  - should_cancel() is polled on every stream event; a cancel leaves the stream (closing the HTTP response)

ScriptedLLM is the deterministic test double with the same interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .policy import redact


class LLMError(RuntimeError):
    def __init__(self, code, message=""):
        super().__init__(f"{code}: {redact(message)[:300]}")
        self.code = code


@dataclass
class TurnResult:
    content: list                       # what is appended back as the assistant message (SDK blocks or dicts)
    blocks: list                        # normalised: {"type": "text", "text"} / {"type": "tool_use", "id", "name", "input"}
    stop_reason: str
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    cancelled: bool = False


def _norm(b):
    t = getattr(b, "type", None) if not isinstance(b, dict) else b.get("type")
    get = (lambda k: b.get(k)) if isinstance(b, dict) else (lambda k: getattr(b, k, None))
    if t == "text":
        return {"type": "text", "text": get("text") or ""}
    if t == "tool_use":
        return {"type": "tool_use", "id": get("id"), "name": get("name"), "input": get("input")}
    return {"type": t}


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, client=None):
        if client is None:
            if not config.api_key_present():
                raise LLMError("no_api_key", "ANTHROPIC_API_KEY is not set")
            try:
                import anthropic
            except ImportError as e:
                raise LLMError("sdk_missing", "the anthropic package is not installed") from e
            client = anthropic.Anthropic(max_retries=2, timeout=120.0)
        self.client = client
        self.model = config.model_id()

    def turn(self, system, messages, tools, on_text, should_cancel) -> TurnResult:
        try:
            import anthropic
            errs = (anthropic.RateLimitError, anthropic.AuthenticationError, anthropic.APIStatusError,
                    anthropic.APIConnectionError)
        except ImportError:                             # tests drive a fake client without the SDK installed
            errs = None
        rate, auth, status, conn = errs or ((), (), (), ())
        lim = config.limits()
        kw = dict(model=self.model, max_tokens=lim["max_output_tokens"],
                  system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                  tools=tools, messages=messages, thinking={"type": "adaptive"},
                  output_config={"effort": config.effort()})
        json_retries = 0
        while True:
            try:
                if config.fallbacks_on():
                    ctx = self.client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"],
                                                           extra_body={"fallbacks": "default"}, **kw)
                else:
                    ctx = self.client.messages.stream(**kw)
                with ctx as stream:
                    for event in stream:
                        if should_cancel():
                            return TurnResult([], [], "cancelled", cancelled=True)
                        if getattr(event, "type", None) == "text":
                            on_text(event.text)
                    msg = stream.get_final_message()
                break
            except ValueError:                          # tool JSON the SDK could not parse: re-issue the turn
                json_retries += 1
                if json_retries > 2:
                    raise LLMError("invalid_tool_json", "the model produced unreadable tool input three times")
            except rate as e:
                raise LLMError("rate_limited", str(e)) from None
            except auth:
                raise LLMError("auth_failed", "the API key was rejected") from None
            except status as e:
                raise LLMError(f"api_error_{e.status_code}", str(getattr(e, "message", "")) or "") from None
            except conn:
                raise LLMError("connection_error", "could not reach the Claude API") from None
        u = getattr(msg, "usage", None)
        usage = {"input_tokens": int(getattr(u, "input_tokens", 0) or 0)
                 + int(getattr(u, "cache_read_input_tokens", 0) or 0)
                 + int(getattr(u, "cache_creation_input_tokens", 0) or 0),
                 "output_tokens": int(getattr(u, "output_tokens", 0) or 0)}
        return TurnResult(list(msg.content), [_norm(b) for b in msg.content], msg.stop_reason or "end_turn", usage)


class ScriptedLLM:
    """Test double. script = list of turns; a turn is a list of blocks, or a callable(messages) -> list of blocks.
    Text blocks are streamed in small chunks through on_text; calls are recorded (system, messages, tools)."""
    name = "scripted"
    model = "scripted-test"

    def __init__(self, script, chunk=7, stop_reasons=None, cancel_after_chunks=None):
        self.script = list(script)
        self.calls = []
        self.chunk = chunk
        self.stop_reasons = stop_reasons or {}
        self.cancel_after_chunks = cancel_after_chunks

    def turn(self, system, messages, tools, on_text, should_cancel) -> TurnResult:
        i = len(self.calls)
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        spec = self.script[i] if i < len(self.script) else [{"type": "text", "text": "No more script."}]
        blocks = spec(messages) if callable(spec) else spec
        n = 0
        for b in blocks:
            if b["type"] == "text":
                t = b["text"]
                for k in range(0, len(t), self.chunk):
                    if should_cancel():
                        return TurnResult([], [], "cancelled", cancelled=True)
                    on_text(t[k:k + self.chunk])
                    n += 1
                    if self.cancel_after_chunks is not None and n >= self.cancel_after_chunks:
                        self.cancel_after_chunks = None
                        self._cancel_hook()
        stop = self.stop_reasons.get(i) or ("tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn")
        return TurnResult(list(blocks), [_norm(b) for b in blocks], stop, {"input_tokens": 100, "output_tokens": 50})

    def _cancel_hook(self):                              # tests replace this to request a cancel mid-stream
        pass
