"""Usage ledger pricing through the real OpenAI SDK HTTP transport."""

from __future__ import annotations

import json

import pytest

from opencollab.adapters.llm.client import LLMClient
from tests.support.provider_sdk_http import http_response, install_sdk_transport
from tests.support.responses_provider_test_support import message_item


@pytest.mark.parametrize(
    "protocol, creation_count",
    [("responses", None), ("responses", 0), ("responses", 500), ("responses", 976), ("chat_completions", 0)],
)
async def test_equal_input_and_creation_rates_price_sdk_usage(monkeypatch, tmp_path, protocol, creation_count):
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", str(log_path))
    monkeypatch.setenv("OPENCOLLAB_INPUT_USD_PER_MTOK", "2.5")
    monkeypatch.setenv("OPENCOLLAB_CACHED_INPUT_USD_PER_MTOK", "1.25")
    monkeypatch.setenv("OPENCOLLAB_OUTPUT_USD_PER_MTOK", "10")
    monkeypatch.delenv("OPENCOLLAB_CACHE_CREATION_USD_PER_MTOK", raising=False)
    monkeypatch.delenv("OPENCOLLAB_REQUIRE_INSTRUCTIONS_ECHO", raising=False)
    monkeypatch.delenv("OPENCOLLAB_EXTERNAL_PROVIDER_ISOLATION", raising=False)
    requests = []
    details = {"cached_tokens": 1024}
    if creation_count is not None:
        details["cache_write_tokens"] = creation_count
    usage = {
        "input_tokens": 2000, "output_tokens": 50, "total_tokens": 2050,
        "input_tokens_details": details, "output_tokens_details": {"reasoning_tokens": 0},
    }

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == f"/v1/{'responses' if protocol == 'responses' else 'chat/completions'}"
        if protocol == "chat_completions":
            return http_response(request, 200, json={
                "id": "chatcmpl_test", "object": "chat.completion", "created": 1, "model": body["model"],
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 2000, "completion_tokens": 50, "total_tokens": 2050,
                    "prompt_tokens_details": {"cached_tokens": 1024},
                },
            })
        item = message_item("ok")
        response = {
            "id": "resp_test", "object": "response", "created_at": 1, "model": body["model"],
            "status": "completed", "error": None, "incomplete_details": None, "output": [item], "usage": usage,
        }
        events = [
            {"type": "response.output_item.done", "output_index": 0, "sequence_number": 1, "item": item},
            {"type": "response.completed", "sequence_number": 2, "response": response},
        ]
        wire = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        return http_response(request, 200, headers={"content-type": "text/event-stream"}, text=wire)

    install_sdk_transport(monkeypatch, "openai", handle)
    async with LLMClient(
        model="gpt-4o", api_key="controlled-test",  # pragma: allowlist secret
        base_url="https://example.invalid/v1", wire_protocol=protocol, max_retries=0,
    ) as client:
        response = await client.complete([{"role": "user", "content": "Reply briefly"}])

    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(requests) == 1
    assert len(records) == 1
    assert response.content == "ok"
    assert response.usage.cache_creation_tokens == creation_count
    recorded = records[0]["usage"]
    assert recorded["cost_usd"] == pytest.approx(0.00422)
    assert recorded["input_tokens"] == 2000
    assert recorded["cached_input_tokens"] == 1024
    assert recorded["cache_creation_tokens"] == creation_count
    assert recorded["uncached_input_tokens"] == (None if creation_count is None else 976 - creation_count)
    if protocol == "responses":
        assert response.usage.raw_usage == usage
        assert recorded["raw_usage"] == usage
