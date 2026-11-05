"""Model backends.

``ClaudeModel`` talks to the Claude API. Anything implementing ``Model`` can
stand in for it, which is how the test-suite runs whole companies offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import AgentSpec


@dataclass
class ToolCall:
    id: str
    name: str
    input: Any


@dataclass
class Turn:
    """One model response, normalized."""

    content: list  # raw content blocks, replayed verbatim on the next request
    stop_reason: str
    model: str
    usage: dict = field(default_factory=dict)
    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    refusal: str | None = None


class ModelError(RuntimeError):
    """A model call failed in a way the run cannot recover from."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal  # True: stop the whole company (e.g. bad credentials)


class Model(Protocol):
    def respond(self, *, agent: AgentSpec, system: str, messages: list, tools: list[dict]) -> Turn: ...


def _get(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def normalize(content: list, stop_reason: str, model: str, usage: dict, refusal: str | None = None) -> Turn:
    texts, thoughts, calls = [], [], []
    for b in content:
        t = _get(b, "type")
        if t == "text":
            texts.append(_get(b, "text", ""))
        elif t == "thinking":
            if _get(b, "thinking"):
                thoughts.append(_get(b, "thinking"))
        elif t == "tool_use":
            calls.append(ToolCall(_get(b, "id"), _get(b, "name"), _get(b, "input")))
    return Turn(
        content=content,
        stop_reason=stop_reason or "end_turn",
        model=model,
        usage=usage,
        text="\n".join(x for x in texts if x).strip(),
        thinking="\n".join(thoughts).strip(),
        tool_calls=calls,
        refusal=refusal,
    )


class ClaudeModel:
    """Claude API backend: streaming, adaptive thinking, prompt caching, refusal fallbacks."""

    FALLBACK_BETA = "server-side-fallback-2026-07-01"
    # Models that accept the server-side ``fallbacks: "default"`` parameter.
    FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")

    def __init__(self, client: Any = None, max_tokens: int = 64000):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(max_retries=4)
        self.client = client
        self.max_tokens = max_tokens

    def respond(self, *, agent: AgentSpec, system: str, messages: list, tools: list[dict]) -> Turn:
        import anthropic

        params: dict[str, Any] = dict(
            model=agent.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=[{**t, "eager_input_streaming": True} for t in tools],
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": agent.effort},
            cache_control={"type": "ephemeral"},
        )
        if agent.model in self.FALLBACK_MODELS:
            params["betas"] = [self.FALLBACK_BETA]
            params["fallbacks"] = "default"
        try:
            with self.client.beta.messages.stream(**params) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise ModelError(f"authentication failed: {e.message} (set ANTHROPIC_API_KEY)", fatal=True) from e
        except anthropic.PermissionDeniedError as e:
            raise ModelError(f"permission denied: {e.message}", fatal=True) from e
        except anthropic.NotFoundError as e:
            raise ModelError(f"model or endpoint not found ({agent.model}): {e.message}", fatal=True) from e
        except anthropic.BadRequestError as e:
            raise ModelError(f"bad request: {e.message}") from e
        except anthropic.RateLimitError as e:
            raise ModelError(f"rate limited after retries: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise ModelError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ModelError(f"could not reach the API: {e}") from e
        except TypeError as e:
            if "authentication" in str(e).lower():
                raise ModelError(
                    "no Claude API credentials found: set ANTHROPIC_API_KEY (or run `ant auth login`)", fatal=True
                ) from e
            raise

        u = msg.usage
        usage = {
            "input": u.input_tokens or 0,
            "output": u.output_tokens or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        refusal = None
        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            refusal = getattr(details, "explanation", None) or getattr(details, "category", None) or "declined"
        return normalize(list(msg.content), msg.stop_reason, msg.model or agent.model, usage, refusal)
