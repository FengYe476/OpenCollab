"""Preserve native thinking across append-only requests and context edits."""

from __future__ import annotations

import copy
from typing import Any

from opencollab.domain.session import SessionState

_THINKING_TYPES = frozenset({"thinking", "redacted_thinking"})


def _strip_anthropic_thinking(messages: list[dict[str, Any]], start: int = 0) -> list[dict[str, Any]]:
    """Remove a suffix of native thinking while retaining text and tool blocks."""
    result = list(messages)
    for index in range(start, len(result)):
        message = result[index]
        state = message.get("provider_state")
        blocks = state.get("anthropic_content") if isinstance(state, dict) else None
        if not isinstance(blocks, list):
            continue
        kept = [block for block in blocks if not isinstance(block, dict) or block.get("type") not in _THINKING_TYPES]
        if len(kept) != len(blocks):
            result[index] = {**message, "provider_state": {**state, "anthropic_content": kept}}
    return result


def _edited_anthropic_history(before: list[dict], after: list[dict]) -> list[dict]:
    """Drop thinking bound to a changed prefix, keeping the valid older run."""
    if [message for message in before if message.get("role") == "system" and not message.get("compacted")] != [
        message for message in after if message.get("role") == "system" and not message.get("compacted")
    ]:
        return _strip_anthropic_thinking(after)
    for index, (old, new) in enumerate(zip(before, after)):
        if old != new:
            return _strip_anthropic_thinking(after, index)
    return after


def _anthropic_history_input(state: SessionState) -> list[dict]:
    """Append fresh transcript messages to the last native provider view."""
    history = state.anthropic_history
    if history is None:
        return state.messages
    return copy.deepcopy([*history["messages"], *state.messages[history["source_count"]:]])


def _remember_anthropic_history(state: SessionState, messages: list[dict], tools: list[dict] | None) -> list[dict]:
    """Persist edits so removed blocks and compacted content stay removed."""
    history = state.anthropic_history
    messages = _edited_anthropic_history(_anthropic_history_input(state), messages)
    if history is not None:
        messages = _edited_anthropic_history(history["messages"], messages)
        if history["tools"] != tools:
            messages = _strip_anthropic_thinking(messages)
    state.anthropic_history = copy.deepcopy({
        "messages": messages, "source_count": len(state.messages), "tools": tools,
    })
    return messages
