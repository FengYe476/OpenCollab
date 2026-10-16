"""A file-type filter retains the search tool's existing directory exclusions."""

from __future__ import annotations

import shutil

import pytest

from opencollab.adapters.env import LocalEnvironment
from opencollab.application.tool_execution import ToolRuntime
from opencollab.tools import builtin_tools


@pytest.mark.parametrize("glob", ["*.py", "**/*.py", "src/**/*.py"])
@pytest.mark.parametrize("excluded", [".git", ".venv", ".opencollab"])
async def test_type_glob_keeps_root_and_nested_directories_excluded(tmp_path, glob, excluded):
    if shutil.which("rg") is None:
        pytest.skip("ripgrep is unavailable")
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("NEEDLE source\n", encoding="utf-8")
    for directory in [tmp_path / excluded, tmp_path / "src" / excluded]:
        directory.mkdir()
        (directory / "generated.py").write_text("NEEDLE excluded\n", encoding="utf-8")

    environment = LocalEnvironment(str(tmp_path))
    tool, = builtin_tools("grep")
    try:
        output = await tool.execute_with_runtime(
            {"pattern": "NEEDLE", "glob": glob},
            ToolRuntime(environment, None, None),
        )
    finally:
        await environment.cleanup()

    assert "main.py:1:NEEDLE source" in output
    assert "excluded" not in output
    assert "generated.py" not in output
