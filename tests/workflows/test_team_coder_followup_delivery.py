"""A coder can deliver successive completions in one public team turn."""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys

import pytest

from opencollab.bootstrap import build_runtime_context, build_scheduler
from tests.support.provider_sdk_http import completion_http_response, http_response, install_sdk_transport


@pytest.mark.parametrize("mode", ["git", "plain", "shared"])
async def test_public_turn_adopts_followup_from_the_same_coder(tmp_path, monkeypatch, mode):
    source = tmp_path / "project"
    source.mkdir()
    (source / "answer.py").write_text("def answer():\n    return 1\n")
    if mode != "plain":
        for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Tests", "-c",
                     "user.email=tests@example.invalid", "-c", "commit.gpgsign=false",
                     "commit", "-qm", "\u521d\u59cb\u5316")):
            subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
    config = tmp_path / "team.yaml"
    config.write_text("""entry: lead
roles:
  lead:
    model: fixture-lead
    prompt: Delegate and adopt the completed coder.
    tools: [adopt_agent, message_agent, team_status, bash]
  coder:
    model: fixture-coder
    prompt: Implement and verify the requested change.
    tools: [file_write, bash]
topology:
  lead: [coder]
""")
    calls = {"fixture-lead": 0, "fixture-coder": 0}
    events = []
    completions = []

    def check(value):
        return "bash", {"command": shlex.join((
            sys.executable, "-B", "-c",
            f"from answer import answer; assert answer() == {value}; print('DELIVERY_PASS')",
        ))}

    async def respond(request):
        body = json.loads(request.content)
        model = body["model"]
        calls[model] += 1
        step = calls[model]
        events.append({"model": model, "step": step})
        latest = next((str(m["content"]) for m in reversed(body["messages"]) if m["role"] == "tool"), "")
        if model == "fixture-lead":
            if step == 1:
                name, args = "message_agent", {
                    "to_role": "coder", "summary": "Implement", "content": "Implement answer 2 and verify it.",
                }
            elif step in (3, 6):
                assert "coder" in latest and "idle" in latest
                name, args = "adopt_agent", {"aid": 1}
            elif step == 4:
                assert "return 2" in (source / "answer.py").read_text()
                name, args = "message_agent", {
                    "to_role": "coder", "summary": "Continue", "content": "Implement answer 3 and verify it.",
                }
            elif step in (2, 5):
                assert "Message queued" in latest, latest
                # Hold the active lead turn until the real follow-up driver has
                # captured DONE, without relying on a wall-clock delay.
                driver = scheduler._tasks.get(1)
                if driver is not None:
                    await asyncio.shield(driver)
                completed = {
                    "step_count": scheduler.agent_step_count(1),
                    "parent_turn": scheduler._turn_started_at[0],
                    "captured_parent_turn": scheduler._completed_coder_diffs[1][0],
                }
                assert completed["captured_parent_turn"] == completed["parent_turn"]
                if step == 5:
                    assert completed["step_count"] > completions[0]["step_count"]
                    assert completed["parent_turn"] == completions[0]["parent_turn"]
                assert scheduler.table.get(1).state.phase.value == "done"
                value = 2 if step == 2 else 3
                assert f"return {value}" in await scheduler._sessions[1].env.read_file("answer.py")
                completions.append(completed)
                events.append({"event": "coder_done_before_adopt", "value": value, **completed})
                name, args = "team_status", {}
            elif step == 7:
                assert "return 3" in (source / "answer.py").read_text()
                name, args = check(3)
            else:
                assert step == 8 and "DELIVERY_PASS" in latest
                return completion_http_response(request, text="Delivered and verified both completions.")
        else:
            value = 2 if step <= 3 else 3
            if step in (1, 4):
                name, args = "file_write", {
                    "path": "answer.py", "mode": "create", "content": f"def answer():\n    return {value}\n",
                }
            elif step in (2, 5):
                name, args = check(value)
            else:
                assert step in (3, 6) and "DELIVERY_PASS" in latest
                return completion_http_response(request, text=f"Completed and verified answer {value}.")
        return http_response(request, 200, json={
            "id": f"{model}-{step}", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": f"{model}-{step}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }],
            }}], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        })

    provider_errors = []

    async def handler(request):
        try:
            return await respond(request)
        except Exception as error:
            provider_errors.append(error)
            raise

    install_sdk_transport(monkeypatch, "openai", handler)
    context = build_runtime_context(str(source), {
        "model": "fixture-lead", "provider": "openai", "api_key": "local-fixture",  # pragma: allowlist secret
        "base_url": "https://controlled.invalid/v1", "budget": 100_000, "llm_max_retries": 0,
    }, trace=False)
    scheduler = build_scheduler(
        context, team_config_path=config, use_worktrees=mode != "shared", interactive=False,
        allow_unisolated_shell=True, allow_unisolated_child_shell=True, serialize_turns=False,
        save_dir=tmp_path / "artifacts", max_steps=15,
        prebuild_team=True,
    )
    try:
        output = await asyncio.wait_for(scheduler.run_turn(0, "Implement 2, adopt it, then ask the coder for 3."), 30)
        assert output == "Delivered and verified both completions."
        assert calls == {"fixture-lead": 8, "fixture-coder": 6}
        executed = subprocess.run(
            [sys.executable, "-B", "-c", "from answer import answer; assert answer() == 3"],
            cwd=source, check=True, capture_output=True,
        )
        assert executed.returncode == 0
    except Exception:
        if provider_errors:
            raise provider_errors[0]
        raise
    finally:
        (tmp_path / "provider-events.json").write_text(json.dumps(events, indent=2))
        await scheduler.cleanup()
