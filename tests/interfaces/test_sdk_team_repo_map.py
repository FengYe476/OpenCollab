"""Team repository maps describe the files its environment can read."""

from __future__ import annotations

import json

import pytest

from opencollab import OpenCollab
from opencollab.environments import local_environment
from tests.support.provider_sdk_http import completion_http_response, http_response, install_sdk_transport


@pytest.mark.parametrize("mode", ["different", "same", "default", "nonlocal"])
async def test_team_map_follows_environment_and_keeps_configuration_sources(tmp_path, monkeypatch, mode):
    configuration = tmp_path / "configuration"
    project = tmp_path / "project"
    configuration.mkdir()
    project.mkdir()
    (configuration / "launch_config.py").write_text("LAUNCHER_CONFIG = 10\n", encoding="utf-8")
    (configuration / "role.md").write_text("Configuration role prompt. Inspect the application.\n", encoding="utf-8")
    (project / "role.md").write_text("Project role prompt.\n", encoding="utf-8")
    (project / "application.py").write_text("def answer():\n    return 27\n", encoding="utf-8")
    for workspace, name in ((configuration, "host-guide"), (project, "project-guide")):
        skill = workspace / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Inspect the application\n---\n{name} instructions.\n",
            encoding="utf-8",
        )
    team = configuration / "team.yaml"
    team.write_text(
        "entry: lead\nroles:\n  lead:\n    prompt_file: role.md\n"
        "    tools: [use_skill, file_read, grep]\ntopology:\n  lead: []\n",
        encoding="utf-8",
    )
    workspace = configuration if mode in {"different", "nonlocal"} else project
    environment = None if mode == "default" else local_environment(project)
    if mode == "nonlocal":
        # Exercise the remote-filesystem declaration using local I/O as transport.
        monkeypatch.setattr(environment, "local_filesystem", False)
    observed = {"actions": [], "tool_results": []}

    def handler(request):
        body = json.loads(request.content)
        messages = body["messages"]
        system = "\n".join(message["content"] for message in messages if message["role"] == "system")
        observed.setdefault("system", system)
        results = [message["content"] for message in messages if message["role"] == "tool"]
        actions = observed["actions"]
        if not actions:
            name = "use_skill"
            arguments = {"name": "host-guide" if "host-guide" in system else "project-guide"}
        elif len(actions) == 1:
            mapping = system.partition("## Repository layout")[2]
            name = "file_read"
            arguments = {"path": "launch_config.py" if "launch_config.py" in mapping else "application.py"}
        elif actions[-1] == "file_read" and "file not found" in results[-1]:
            name = "grep"
            arguments = {"pattern": "def answer", "path": ".", "glob": "*.py"}
        elif actions[-1] == "grep":
            name = "file_read"
            arguments = {"path": results[-1].split(":", 1)[0]}
        else:
            observed["tool_results"] = results
            return completion_http_response(
                request, text="answer returns 27" if "return 27" in results[-1] else "source unavailable"
            )
        actions.append(name)
        return http_response(request, 200, json={
            "id": f"chat-map-{len(actions)}", "object": "chat.completion", "created": 1, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": f"call-{len(actions)}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }],
            }}], "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
        })

    install_sdk_transport(monkeypatch, "openai", handler)
    try:
        result = await OpenCollab(
            workspace, model="fixture-model", api_key="controlled-test",  # pragma: allowlist secret
            base_url="https://controlled.invalid/v1", environment=environment,
            config={"budget": 10_000, "llm_max_retries": 0, "wire_protocol": "chat_completions"},
        ).team(
            "Read the application's answer function.", config=team, use_worktrees=False,
            artifacts=tmp_path / "artifacts", trace=False, timeout=10, max_steps=8,
        )
    finally:
        if environment is not None:
            await environment.cleanup()

    assert result.ok, (result.reason, result.error)
    assert result.output == "answer returns 27"
    assert observed["actions"] == ["use_skill", "file_read"]
    system = observed["system"]
    assert "Configuration role prompt." in system
    assert "Project role prompt." not in system
    catalog, separator, mapping = system.partition("## Repository layout")
    expected_skill = "host-guide" if workspace == configuration else "project-guide"
    other_skill = "project-guide" if expected_skill == "host-guide" else "host-guide"
    assert expected_skill in catalog
    assert other_skill not in catalog
    assert any(f"{expected_skill} instructions." in content for content in observed["tool_results"])
    assert any("return 27" in content for content in observed["tool_results"])
    if mode == "nonlocal":
        assert not separator
    else:
        assert separator
        assert "application.py" in mapping
        assert "launch_config.py" not in mapping
