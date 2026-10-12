"""Search result limits count LF records and preserve inline Unicode."""

from __future__ import annotations

import pytest

from opencollab.adapters._env_base import ExecResult
from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.tools.fs import GrepTool
from opencollab.application.tool_execution import ToolRuntime


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", " ", "\\u2028"])
@pytest.mark.parametrize("backend", ["rg", "grep"])
@pytest.mark.parametrize("max_results", [1, 2])
async def test_search_preserves_complete_matching_records(tmp_path, separator, backend, max_results):
    class SearchEnvironment(LocalEnvironment):
        async def exec_cmd(self, cmd, timeout=120.0):
            if backend == "grep" and cmd.startswith("rg "):
                return ExecResult(127, "", "rg unavailable")
            return await super().exec_cmd(cmd, timeout)

    records = [f'{{"text":"before{separator}NEEDLE"}}', '{"text":"next NEEDLE"}']
    (tmp_path / "records.jsonl").write_text("\n".join(records) + "\n", encoding="utf-8")
    environment = SearchEnvironment(str(tmp_path))
    try:
        output = await GrepTool().execute_with_runtime(
            {"path": "records.jsonl", "pattern": "NEEDLE", "max_results": max_results},
            ToolRuntime(environment, None, None),
        )
    finally:
        await environment.cleanup()

    prefix = "records.jsonl:" if backend == "grep" else ""
    assert output.split("\n")[0] == f"{prefix}1:{records[0]}"
    if max_results == 1:
        assert "1 more matching lines not shown" in output
        assert f"{prefix}2:{records[1]}" not in output
    else:
        assert output == f"{prefix}1:{records[0]}\n{prefix}2:{records[1]}"
