"""Offline checks for the IO2Code example: no model, no Docker, no benchmark checkout."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from io2code_oc.tasks import Task, jsonable, kind_of, outputs_match
from io2code_oc.tools import CheckCandidateTool, DockerCandidateRunner, InProcessCandidateRunner, QueryLog
from io2code_oc.workflow import CANDIDATE_FILE

from opencollab.builtin_workflows import ReconstructionTask, reconstruct

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
CORRECT_PROGRAM = "def solve(xs):\n    return sum(abs(x) for x in xs)\n"


def _load_main():
    spec = importlib.util.spec_from_file_location("io2code_oc_main", EXAMPLE_DIR / "main.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeAdapter:
    """Stands in for IO2Code's TaskAdapter: a deterministic stream of fresh list inputs."""

    def __init__(self) -> None:
        self._next = 100

    def get_input_output_examples(self, n: int) -> list[tuple[Any, Any]]:
        out = []
        for _ in range(n):
            self._next += 1
            inp = [self._next, -self._next, 0]
            out.append((inp, sum(abs(x) for x in inp)))
        return out


def fake_task() -> Task:
    return Task(
        name="Sum_Abs",
        function_name="solve",
        input_type="list of integers",
        input_constraints="Valid input is a list of integers.",
        oracle=lambda xs: sum(abs(x) for x in xs),
        held_out=[([1, -2], 3), ([], 0)],
        visible=[([1], 1)],
        kind="list",
        lo=-1000,
        hi=1000,
        is_float=False,
        adapter=FakeAdapter(),
    )


class ScriptedContext:
    """Plays the agent: writes a correct candidate, proposes fresh inputs, spends no tokens."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self._next = 1

    async def phase(self, name: str) -> None:
        pass

    def tokens_spent(self) -> int:
        return 0

    def tokens_remaining(self) -> float:
        return 10**9

    async def agent(self, prompt: str, *, label: str, tools: list[Any], schema: dict | None = None, **_: Any):
        if schema is not None:
            n = int(prompt.split("exactly ")[1].split()[0])
            batch = []
            for _ in range(n):
                self._next += 1
                batch.append([self._next, -3])
            return {"inputs": batch}
        (self.workspace / CANDIDATE_FILE).write_text(CORRECT_PROGRAM, encoding="utf-8")
        return "done"


def test_outputs_match_follows_io2code_rules() -> None:
    assert outputs_match(3, 3) and not outputs_match(3, 4)
    assert outputs_match(0.1 + 0.2, 0.3) and not outputs_match(0.1, 0.2)
    assert outputs_match([1, [2, 3]], [1, [2, 3]]) and not outputs_match([1, 2], [1, 2, 3])
    assert outputs_match(jsonable((1, 2)), [1, 2])


def test_kind_mapping_covers_io2code_types() -> None:
    assert kind_of("int", None) == "list" and kind_of("2d_bit", None) == "two_lists"
    assert kind_of(None, "pair") == "pair" and kind_of(None, "points_list") == "points"
    assert kind_of(None, "coin_change") == "other" and kind_of("mystery", None) == "other"


def test_binding_runs_the_builtin_workflow_and_records_examples(tmp_path: Path) -> None:
    main = _load_main()
    task = fake_task()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    log = QueryLog()
    runner = InProcessCandidateRunner()
    binding = main.make_reconstruction_task(task, workspace, runner, [], log)
    assert isinstance(binding, ReconstructionTask) and binding.kind == "list"

    out = asyncio.run(reconstruct(ScriptedContext(workspace), {"task": binding, "random_share": 0.5}))
    assert out["stop_reason"] == "early_stop" and out["known_examples"] == 30 == log.count
    assert out["random_inputs"] == 15 and {e["source"] for e in log.entries} == {"agent", "random"}

    score = asyncio.run(main.score_held_out(task, workspace / CANDIDATE_FILE, runner))
    assert score == {"passed": 2, "total": 2, "solved": True, "failures": []}


def test_check_candidate_reports_mismatches(tmp_path: Path) -> None:
    task = fake_task()
    log = QueryLog(entries=[{"input": [1, -2], "output": 3}, {"input": [4], "output": 4}])
    (tmp_path / CANDIDATE_FILE).write_text("def solve(xs):\n    return sum(xs)\n", encoding="utf-8")
    tool = CheckCandidateTool(task, log, tmp_path, InProcessCandidateRunner())
    text = asyncio.run(tool.execute_with_runtime({"path": CANDIDATE_FILE}, runtime=None))
    assert text.startswith("1/2 recorded oracle cases match.") and "expected 3, got -1" in text
    assert "not a file inside the workspace" in asyncio.run(
        tool.execute_with_runtime({"path": "../x.py"}, runtime=None)
    )


def test_crashed_and_skipped_tasks_are_persisted_but_not_treated_as_finished(tmp_path: Path) -> None:
    main = _load_main()
    crashed = main.placeholder_record("Abs_Current", "crashed", "RuntimeError: boom")
    path = main.persist_record(tmp_path, crashed)

    assert path == tmp_path / "Abs_Current" / "result.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["status"] == "crashed" and stored["solved"] is False
    # Both appear in a summary rebuilt from result.json files, but --resume retries them.
    assert main.is_finished(stored) is False
    assert main.is_finished(main.placeholder_record("X", "skipped", "aborted")) is False
    assert main.is_finished({"task": "X", "status": "completed", "solved": True}) is True


def test_readmes_document_the_prerequisites() -> None:
    for name in ("README.md", "README.zh-CN.md"):
        text = (EXAMPLE_DIR / name).read_text(encoding="utf-8")
        assert "IO2CODE_ROOT" in text and "--docker" in text and "main.py" in text and "reconstruct" in text


class ShellEnvironment:
    """Execute trusted fixtures through the container runner's real subprocess command."""

    async def exec_cmd(self, command: str, timeout: int) -> SimpleNamespace:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        return SimpleNamespace(
            returncode=result.returncode, stdout=result.stdout, stderr=result.stderr, timed_out=False
        )


def test_container_runner_uses_current_source_with_existing_bytecode(tmp_path: Path) -> None:
    program = tmp_path / CANDIDATE_FILE
    prefix = "import math, sys\nassert sys.flags.isolated\nassert __spec__.origin == __file__\n"
    stale = prefix + "def solve(xs):\n    return math.prod(xs)\n"
    current = prefix + "def solve(xs):\n    return math.fsum(xs)\n"
    assert len(stale) == len(current)
    program.write_text(stale, encoding="utf-8")
    timestamp = program.stat().st_mtime_ns
    py_compile.compile(str(program), doraise=True)
    program.write_text(current, encoding="utf-8")
    os.utime(program, ns=(timestamp, timestamp))
    runner = DockerCandidateRunner(ShellEnvironment(), tmp_path, str(tmp_path), sys.executable)

    assert asyncio.run(runner.run(program, "solve", [[2, 3]])) == [{"ok": True, "output": 5.0}]
    assert len(list(tmp_path.glob("__pycache__/*.pyc"))) == 1


def test_final_selection_scores_the_selected_source_after_same_second_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    main = _load_main()
    task = fake_task()
    runner = DockerCandidateRunner(ShellEnvironment(), tmp_path, str(tmp_path), sys.executable)
    program = tmp_path / CANDIDATE_FILE
    timestamp = 1_700_000_000_000_000_000

    class RewritingContext(ScriptedContext):
        code_turn = 0

        async def agent(self, prompt: str, *, schema: dict | None = None, **kwargs: Any):
            if schema is not None:
                return await super().agent(prompt, schema=schema, **kwargs)
            self.code_turn += 1
            operator = "sum" if self.code_turn == 1 else "max"
            program.write_text(f"def solve(xs):\n    return {operator}(abs(x) for x in xs)\n", encoding="utf-8")
            written = timestamp - 2_000_000_000 if self.code_turn == 1 else timestamp
            os.utime(program, ns=(written, written))
            return "done"

    copy = shutil.copy

    def copy_in_final_second(source: Any, destination: Any, *args: Any, **kwargs: Any):
        result = copy(source, destination, *args, **kwargs)
        if Path(destination) == program:
            os.utime(program, ns=(timestamp, timestamp))
        return result

    monkeypatch.setattr(shutil, "copy", copy_in_final_second)
    binding = main.make_reconstruction_task(task, tmp_path, runner, [], QueryLog())
    out = asyncio.run(reconstruct(RewritingContext(tmp_path), {"task": binding, "max_iterations": 2}))
    score = asyncio.run(main.score_held_out(task, program, runner))

    assert out["final_program"] == "iter_001.py"
    assert program.read_text(encoding="utf-8") == CORRECT_PROGRAM
    assert score == {"passed": 2, "total": 2, "solved": True, "failures": []}
