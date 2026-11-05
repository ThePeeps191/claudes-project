"""Drive the real anthropic SDK through a mock HTTP transport.

This checks the exact JSON tinyco puts on the wire, including replaying the
SDK's own content-block objects on the following request, without network
access or an API key.
"""

from __future__ import annotations

import json

import anthropic
import httpx2 as httpx
import pytest

from tinyco.config import parse
from tinyco.llm import ClaudeModel, ModelError
from tinyco.tools import TOOLS


def _sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _tool_use_stream() -> bytes:
    return _sse(
        [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-5-5",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 1,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 40,
                    },
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Plan: save a note."},
            },
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig123"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Saving a note."}},
            {"type": "content_block_stop", "index": 1},
            {
                "type": "content_block_start",
                "index": 2,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "note", "input": {}},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '{"text": '},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '"use pytest"}'},
            },
            {"type": "content_block_stop", "index": 2},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 30},
            },
            {"type": "message_stop"},
        ]
    )


def _end_stream() -> bytes:
    return _sse(
        [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_2",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-5-5",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 5,
                        "output_tokens": 1,
                        "cache_read_input_tokens": 90,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Done."}},
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 3},
            },
            {"type": "message_stop"},
        ]
    )


def test_real_sdk_request_and_replay(tmp_path):
    bodies: list[dict] = []
    headers: list[httpx.Headers] = []
    streams = [_tool_use_stream(), _end_stream()]

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        headers.append(request.headers)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=streams[len(bodies) - 1])

    client = anthropic.Anthropic(
        api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0
    )
    model = ClaudeModel(client)
    agent = parse({"agents": {"ceo": {"effort": "high"}}}, tmp_path).agents["ceo"]
    messages: list = [{"role": "user", "content": "briefing"}]

    turn = model.respond(agent=agent, system="sys", messages=messages, tools=TOOLS)
    assert turn.stop_reason == "tool_use"
    assert turn.thinking == "Plan: save a note."
    assert turn.text == "Saving a note."
    assert [(c.name, c.input) for c in turn.tool_calls] == [("note", {"text": "use pytest"})]
    assert turn.usage == {"input": 50, "output": 30, "cache_read": 0, "cache_write": 40, "web_searches": 0}

    body = bodies[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "high"}
    assert body["fallbacks"] == "default"
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["stream"] is True
    assert all(t["eager_input_streaming"] is True for t in body["tools"])
    assert {t["name"] for t in body["tools"]} == {t["name"] for t in TOOLS}
    assert "server-side-fallback-2026-07-01" in headers[0].get("anthropic-beta", "")

    # Replay the SDK's own block objects, as the runner does.
    messages.append({"role": "assistant", "content": turn.content})
    messages.append(
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "Saved.", "is_error": False}],
        }
    )
    turn2 = model.respond(agent=agent, system="sys", messages=messages, tools=TOOLS)
    assert turn2.stop_reason == "end_turn" and turn2.text == "Done."

    replayed = bodies[1]["messages"][1]["content"]
    assert [b["type"] for b in replayed] == ["thinking", "text", "tool_use"]
    assert replayed[0]["signature"] == "sig123"  # thinking blocks go back unchanged
    assert replayed[2] == {"type": "tool_use", "id": "toolu_1", "name": "note", "input": {"text": "use pytest"}}
    # Request prefix is byte-stable across calls (good for prompt caching).
    assert bodies[1]["tools"] == bodies[0]["tools"] and bodies[1]["system"] == bodies[0]["system"]


def test_auth_error_is_fatal(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
        )

    client = anthropic.Anthropic(
        api_key="bad", http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0
    )
    agent = parse({"agents": {"ceo": {}}}, tmp_path).agents["ceo"]
    with pytest.raises(ModelError) as info:
        ClaudeModel(client).respond(agent=agent, system="s", messages=[{"role": "user", "content": "x"}], tools=[])
    assert info.value.fatal


def test_missing_credentials_is_fatal_not_a_crash(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no stored profile either

    def handler(request):  # pragma: no cover - never reached
        raise AssertionError("request should not be sent")

    client = anthropic.Anthropic(http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0)
    agent = parse({"agents": {"ceo": {}}}, tmp_path).agents["ceo"]
    with pytest.raises(ModelError) as info:
        ClaudeModel(client).respond(agent=agent, system="s", messages=[{"role": "user", "content": "x"}], tools=[])
    assert info.value.fatal and "ANTHROPIC_API_KEY" in str(info.value)
