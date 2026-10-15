"""Exported conversation snapshots can resume their original autosave target."""

from __future__ import annotations

import pytest

from opencollab.adapters.storage import SessionStore
from opencollab.bootstrap import build_session, load_session
from opencollab.domain.agent import Agent
from tests.support.session_run_test_support import FakeLLM, llm_response


def model(turns=1):
    return FakeLLM([
        llm_response(content=f"Completed request {index}.", total_tokens=7, input_tokens=5)
        for index in range(turns)
    ])


async def converse(session, turns):
    for index in range(turns):
        await session.add_user_message(f"Remember requirement {index}.")
        await session.run_loop()


def transcript(snapshot):
    # Timestamps are restored into Session's bookkeeping beside its messages.
    return [{key: value for key, value in row.items() if key != "timestamp"}
            for row in snapshot["messages"]]


@pytest.mark.parametrize("turns", [1, 4])
@pytest.mark.parametrize("entry", ["restore", "load_session"])
@pytest.mark.parametrize("save_twice", [False, True])
async def test_manual_export_resumes_original_autosave_and_continues(
    tmp_path, turns, entry, save_twice,
):
    automatic = str(tmp_path / "automatic.json")
    exported = str(tmp_path / "exported.json")
    agent = Agent(name="reviewer", system_prompt="Complete the user's requests.")
    original = build_session(agent=agent, llm=model(turns), auto_save_path=automatic)
    try:
        if save_twice:
            original.save(exported)
        await converse(original, turns)
        original.save(exported)
        messages = original.messages.copy()
        tokens = original.used_tokens
    finally:
        await original.aclose()

    continuation = model()
    restored = None
    try:
        if entry == "load_session":
            restored = load_session(
                exported, agent=agent, llm=continuation, auto_save_path=automatic,
            )
        else:
            restored = build_session(
                agent=agent, llm=continuation, auto_save_path=automatic,
            )
            restored.restore(exported)
        assert restored.messages == messages
        assert restored.used_tokens == tokens
        await restored.add_user_message("Continue using the remembered requirements.")
        await restored.run_loop()
        assert continuation.calls[0]["messages"][:len(messages)] == messages
        saved = SessionStore().load_snapshot(automatic, agent.system_prompt)
        assert transcript(saved) == restored.messages
        assert saved["session_state"]["used_tokens"] == tokens + 7
        assert restored.persistence_errors == ()
    finally:
        if restored is not None:
            await restored.aclose()


async def test_export_keeps_original_autosave_bytes_unchanged(tmp_path):
    automatic = tmp_path / "automatic.json"
    exported = tmp_path / "exported.json"
    agent = Agent(name="reviewer", system_prompt="Remember the requests.")
    original = build_session(agent=agent, llm=model(4), auto_save_path=str(automatic))
    try:
        await converse(original, 4)
        source_paths = [automatic, tmp_path / "automatic.json.journal"]
        before = {path: path.read_bytes() for path in source_paths}
        original.save(str(exported))
        assert {path: path.read_bytes() for path in source_paths} == before
        store = SessionStore()
        source = store.load_snapshot(str(automatic), agent.system_prompt)
        saved = store.load_snapshot(str(exported), agent.system_prompt)
        assert saved["messages"] == source["messages"]
        assert saved["session_state"] == source["session_state"]
    finally:
        await original.aclose()


async def test_older_export_still_cannot_replace_a_newer_autosave(tmp_path):
    automatic = tmp_path / "automatic.json"
    exported = tmp_path / "older.json"
    agent = Agent(name="reviewer", system_prompt="Remember the requests.")
    original = build_session(agent=agent, llm=model(5), auto_save_path=str(automatic))
    try:
        await converse(original, 1)
        original.save(str(exported))
        await converse(original, 4)
    finally:
        await original.aclose()
    source_paths = [automatic, tmp_path / "automatic.json.journal"]
    before = {path: path.read_bytes() for path in source_paths}
    restored = build_session(agent=agent, llm=model(), auto_save_path=str(automatic))
    try:
        with pytest.raises(ValueError, match="refusing stale checkpoint"):
            restored.restore(str(exported))
        assert {path: path.read_bytes() for path in source_paths} == before
    finally:
        await restored.aclose()


async def test_restored_export_can_be_exported_again_before_resuming(tmp_path):
    automatic = str(tmp_path / "automatic.json")
    first = str(tmp_path / "first.json")
    second = str(tmp_path / "second.json")
    agent = Agent(name="reviewer", system_prompt="Remember the requests.")
    original = build_session(agent=agent, llm=model(4), auto_save_path=automatic)
    try:
        await converse(original, 4)
        original.save(first)
        messages = original.messages.copy()
    finally:
        await original.aclose()
    reader = load_session(first, agent=agent, llm=model())
    try:
        reader.save(second)
    finally:
        await reader.aclose()
    restored = build_session(agent=agent, llm=model(), auto_save_path=automatic)
    try:
        restored.restore(second)
        assert restored.messages == messages
    finally:
        await restored.aclose()


@pytest.mark.parametrize("target_kind", ["same-file", "new-target"])
async def test_export_without_original_autosave_can_start_autosaving(tmp_path, target_kind):
    exported = str(tmp_path / "exported.json")
    target = exported if target_kind == "same-file" else str(tmp_path / "new.json")
    agent = Agent(name="reviewer", system_prompt="Remember the requests.")
    original = build_session(agent=agent, llm=model(2))
    try:
        await converse(original, 2)
        original.save(exported)
        messages = original.messages.copy()
    finally:
        await original.aclose()
    restored = load_session(exported, agent=agent, llm=model(), auto_save_path=target)
    try:
        assert restored.messages == messages
        await restored.add_user_message("Continue.")
        await restored.run_loop()
        assert transcript(SessionStore().load_snapshot(target, agent.system_prompt)) == restored.messages
    finally:
        await restored.aclose()
