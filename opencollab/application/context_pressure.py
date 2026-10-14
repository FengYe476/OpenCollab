"""Additive history estimates calibrated by completed provider requests."""

from __future__ import annotations

import json
import math
from typing import Any, Callable


class ContextPressure:
    """Size the current view without treating encrypted bytes as token counts.

    Provider input usage measures a whole request, not individual messages.
    Distribute that measurement over the sent messages in proportion to their
    previous estimates. This preserves additivity for the history shapers.
    Unchanged messages (including copies) reuse those weights; edited or new
    messages use the provider-aware fallback. The next response recalibrates
    the view. We retain only the last request and its new assistant output.

    The weights are estimates, not per-message billing attribution. Tool schema
    and request overhead remain in the measured total as conservative headroom.
    Missing usage and restored sessions use the existing fallback until a
    successful request provides a measurement. Native replay data is untouched.
    """

    def __init__(self, estimate_message: Callable[[dict[str, Any]], int]):
        self._estimate_message = estimate_message
        self._weights: dict[str, int] = {}

    @staticmethod
    def _key(message: dict[str, Any]) -> str:
        # Exact content comparison also survives shapers that copy dictionaries.
        return json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    def clear(self) -> None:
        self._weights.clear()

    def __call__(self, messages: list[dict[str, Any]]) -> int:
        return sum(self._weight(message) for message in messages)

    def _weight(self, message: dict[str, Any]) -> int:
        known = self._weights.get(self._key(message))
        return known if known is not None else max(1, self._estimate_message(message))

    def observe_request(self, messages: list[dict[str, Any]], usage: Any) -> None:
        """Use final-attempt input usage; summary calls must not call this."""
        tokens = getattr(usage, "context_tokens", None)
        if tokens is None:
            tokens = getattr(usage, "input_tokens", None)
        if not self._measured(tokens, usage) or not messages:
            return
        weighted = [(self._key(message), self._weight(message)) for message in messages]
        total = sum(weight for _key, weight in weighted)
        self._weights = {
            key: math.ceil(weight * tokens / total) for key, weight in weighted
        }

    def observe_output(self, message: dict[str, Any], usage: Any) -> None:
        """Bound the new assistant replay by its reported generation size.

        Output usage already includes reasoning. Counting it again by encrypted
        string length would discard the benefit of the measured input prefix.
        A provider may reuse fewer reasoning tokens on the next call; that call's
        input usage will calibrate the combined view. Retry accounting exposes
        the final attempt separately from the cumulative generation cost.
        """
        tokens = getattr(usage, "context_output_tokens", None)
        if tokens is None:
            tokens = getattr(usage, "output_tokens", None)
        if self._measured(tokens, usage):
            # A Chat policy can omit reasoning that was part of the output.
            # Keep that smaller outbound projection when it is already known.
            self._weights[self._key(message)] = min(tokens + 64, max(1, self._estimate_message(message)))

    @staticmethod
    def _measured(tokens: Any, usage: Any) -> bool:
        estimated = getattr(usage, "context_tokens_estimated", None)
        if estimated is None:
            estimated = getattr(usage, "estimated", True)
        return (
            estimated is False
            and isinstance(tokens, int)
            and not isinstance(tokens, bool)
            and tokens > 0
        )
