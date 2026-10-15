"""Strict team diff delivery over an existing candidate workspace lease."""

from __future__ import annotations

import shlex
from typing import Any

from opencollab.adapters._git_patch_bytes import _complete_patch
from opencollab.adapters.git_patch import guarded_staged_diff_command


class _TeamCandidateEnvironment:
    """Delegate execution while retaining the team's strict patch evidence."""

    def __init__(self, lease: Any, source_workspace: str) -> None:
        self._lease = lease
        self.source_workspace = source_workspace
        self._delivery_pending = False

    @property
    def _delegate(self) -> Any:
        if self._lease.environment is None:
            raise RuntimeError("candidate environment setup has not completed")
        return self._lease.environment

    @property
    def workspace(self) -> str:
        if self._lease.environment is None:
            return self._lease.candidate_workspace
        return self._delegate.workspace

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def diff_path_prefix(self) -> str:
        return self._lease.workspace_prefix.rstrip("/")

    @property
    def diff_base(self) -> str:
        return self._lease.base_revision

    def retain_changes(self) -> str:
        self._delivery_pending = True
        return self.workspace

    @property
    def recovery_location(self) -> str | None:
        return self.workspace if self._delivery_pending else None

    async def get_diff(self) -> str:
        self._delegate._ensure_active()
        self._delivery_pending = True
        root = self._lease.candidate_workspace
        result = await self._delegate.exec_cmd(
            f"cd -- {shlex.quote(root)} && "
            + guarded_staged_diff_command(base_revision=self._lease.base_revision),
        )
        patch = await _complete_patch(
            result, "worktree diff extraction", self._delegate, root,
            self._lease.base_revision, None, captured_blobs=True,
        )
        self._delivery_pending = False
        return patch

    async def cleanup(self) -> None:
        if self._delivery_pending:
            raise RuntimeError(f"worktree changes await complete delivery; retained at {self.workspace}")
        await self._lease.cleanup()

    async def abort(self) -> None:
        # Stop processes and files before releasing any workspace. A failed
        # export continues to retain the physical checkout for recovery.
        await self._delegate.abort()
        await self.cleanup()
