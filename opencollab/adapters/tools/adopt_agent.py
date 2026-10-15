"""Adopt one completed coding child's captured changes."""

from __future__ import annotations

from typing import Any

from opencollab.adapters.tools.base import Tool
from opencollab.application.ports import SchedulerPort
from opencollab.application.tool_execution import ToolRuntime


class AdoptAgentTool(Tool):
    name = "adopt_agent"
    description = (
        "Apply the complete changes returned by your successfully completed "
        "direct coder child to the team's delivery workspace. Supply its aid "
        "from the completion result. Then spawn a fresh tester to verify the "
        "adopted implementation. Conflicts leave the delivery files unchanged."
    )
    parameters = {
        "type": "object",
        "properties": {"aid": {"type": "integer", "minimum": 1}},
        "required": ["aid"],
        "additionalProperties": False,
    }

    def __init__(self, scheduler: SchedulerPort) -> None:
        self._scheduler = scheduler

    async def execute_with_runtime(self, params: dict[str, Any], runtime: ToolRuntime) -> str:
        if set(params) != {"aid"}:
            raise ValueError("adopt_agent accepts only a completed coder aid")
        return await self._scheduler.adopt_agent(runtime.aid, params["aid"])
