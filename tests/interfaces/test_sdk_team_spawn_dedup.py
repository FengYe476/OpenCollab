"""Public team delegation uses the declared role's casefold identity."""

from __future__ import annotations

import asyncio
import json

import pytest

from opencollab import OpenCollab
from opencollab.adapters.storage import SessionStore
from tests.support.provider_sdk_http import completion_http_response, http_response, install_sdk_transport


@pytest.mark.parametrize(
    ("roles", "tasks", "contexts", "expected_children"),
    [
        (("coder", "coder"), ("inspect", "inspect"), ("module=a", "module=a"), 1),
        (("coder", "Coder"), ("inspect", "inspect"), ("module=a", "module=a"), 1),
        (("coder", "Coder", "CODER", "coDer"), ("inspect",) * 4, ("module=a",) * 4, 1),
        (("coder", "Coder"), ("inspect", "test"), ("module=a", "module=a"), 2),
        (("coder", "Coder"), ("inspect", "inspect"), ("module=a", "module=b"), 2),
    ],
    ids=["exact-role", "mixed-case", "four-spellings", "distinct-task", "distinct-context"],
)
async def test_public_team_deduplicates_live_role_aliases(
    tmp_path, monkeypatch, roles, tasks, contexts, expected_children,
):
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "off")
    config = tmp_path / "team.yaml"
    config.write_text(
        """entry: lead
roles:
  lead:
    prompt: ROLE_LEAD
    tools: [spawn_agent]
  coder:
    prompt: ROLE_CODER
    tools: []
topology:
  lead: [coder]
""", encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    requests = {"lead": [], "coder": []}

    async def wait_for_batch():
        # Keep every child live until the parent has handled the whole batch.
        while True:
            snapshots = list(artifacts.glob("agent_0_*.json"))
            if snapshots:
                snapshot = SessionStore().load_snapshot(str(snapshots[0]), "ROLE_LEAD")
                if snapshot["session_state"]["phase"] == "awaiting_events":
                    return
            await asyncio.sleep(0.005)

    async def handler(request):
        body = json.loads(request.content)
        role = "lead" if "ROLE_LEAD" in str(body["messages"][0]["content"]) else "coder"
        requests[role].append(body)
        if role == "coder":
            await asyncio.wait_for(wait_for_batch(), 2)
            return completion_http_response(request, text="Source analyzed.")
        if len(requests["lead"]) > 1:
            return completion_http_response(
                request,
                text="\n".join(message["content"] for message in body["messages"] if message["role"] == "tool"),
            )
        calls = [
            {"id": f"spawn-{index}", "type": "function", "function": {
                "name": "spawn_agent",
                "arguments": json.dumps({"role": name, "task": task, "context": context}),
            }}
            for index, (name, task, context) in enumerate(zip(roles, tasks, contexts))
        ]
        return http_response(request, 200, json={
            "id": "chat-delegation", "object": "chat.completion", "created": 1, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": calls,
            }}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        })

    install_sdk_transport(monkeypatch, "openai", handler)
    result = await OpenCollab(
        workspace, model="fixture-model", provider="openai",
        api_key="controlled-test",  # pragma: allowlist secret
        base_url="https://controlled.invalid/v1", config={"wire_protocol": "chat_completions"},
    ).team(
        "Delegate the source analysis.", config=config, use_worktrees=False,
        artifacts=artifacts, budget=100_000, timeout=5,
    )

    assert result.status == "completed", result.reason
    assert result.metrics["sessions"] == expected_children + 1
    assert len(requests["coder"]) == expected_children
    results = [message["content"] for message in requests["lead"][-1]["messages"] if message["role"] == "tool"]
    assert results.count("Source analyzed.") == expected_children
    assert sum("already being handled" in content for content in results) == len(roles) - expected_children
