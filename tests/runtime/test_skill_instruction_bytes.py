"""Public skill loading preserves executable Unicode examples through real SDK requests."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys

import pytest

from opencollab import OpenCollab
from tests.support.provider_sdk_http import (
    completion_http_response,
    http_response,
    install_sdk_transport,
)


@pytest.mark.parametrize(
    "separator", ["plain", "\U0001f642", "\u2028", "\u2029", "\u0085"],
    ids=["plain", "emoji", "line-separator", "paragraph-separator", "next-line"],
)
async def test_skill_command_survives_public_load_and_execution(tmp_path, monkeypatch, separator):
    workspace = tmp_path / "workspace"
    skill = workspace / "skills" / "json-check"
    skill.mkdir(parents=True)
    command = (
        f"{shlex.quote(sys.executable)} - <<'PY'\n"
        "import json\n"
        f"sample = '''{{\"value\":\"before{separator}after\"}}'''\n"
        "assert 'value' in json.loads(sample)\n"
        "print('JSON_OK')\n"
        "PY"
    )
    body = "Run the complete JSON example.\n\n```bash\n" + command + "\n```"
    (skill / "SKILL.md").write_text(
        "---\nname: json-check\ndescription: Check the JSON example\n---\n" + body + "\n",
        encoding="utf-8",
    )
    config = tmp_path / "team.yaml"
    config.write_text(
        "entry: lead\nroles:\n  lead:\n    prompt: Load the skill and execute its sample.\n"
        "    tools: [use_skill, bash]\ntopology: {}\n",
        encoding="utf-8",
    )
    original = subprocess.run(
        ["/bin/bash", "-c", command], cwd=workspace, text=True, capture_output=True, check=True,
    )
    assert original.stdout == "JSON_OK\n"
    requests = []
    delivered = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            name, arguments, identity = "use_skill", {"name": "json-check"}, "load-skill"
        elif len(requests) == 2:
            content = next(message["content"] for message in payload["messages"]
                           if message.get("tool_call_id") == "load-skill")
            assert content == body
            loaded_command = re.search(r"```bash\n(.*?)\n```", content, re.S).group(1)
            delivered.append(loaded_command)
            name, arguments, identity = "bash", {"command": loaded_command}, "execute-skill"
        else:
            return completion_http_response(request, text="Complete.")
        return http_response(request, 200, json={
            "id": "controlled-skill", "object": "chat.completion", "created": 1,
            "model": payload["model"], "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": identity, "type": "function", "function": {
                        "name": name, "arguments": json.dumps(arguments),
                    },
                }],
            }}], "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        })

    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "off")
    install_sdk_transport(monkeypatch, "openai", handler)
    result = await OpenCollab(
        workspace, model="gpt-4o", api_key="controlled-test",  # pragma: allowlist secret
        base_url="https://controlled.invalid/v1", config={"llm_max_retries": 0},
    ).team(
        "Check the JSON example.", config=config, trace=False, artifacts=tmp_path / "artifacts",
        use_worktrees=False, allow_unisolated_shell=True, max_steps=5, budget=30_000,
    )
    output = next(message["content"] for message in requests[-1]["messages"]
                  if message.get("tool_call_id") == "execute-skill")

    assert result.status == "completed"
    assert delivered == [command]
    assert "Exit code: 0" in output and "JSON_OK" in output
