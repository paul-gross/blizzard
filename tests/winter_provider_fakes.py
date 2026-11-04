"""Helpers for the runner winter provider tests, shared with other test modules."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path


class FakeWinter:
    def __init__(self, *, service_bound: bool = False, fail_on: list[str] | None = None) -> None:
        self.runs: list[list[str]] = []
        self.captures: list[list[str]] = []
        self.ready = 0
        self.service_bound = service_bound
        self.fail_on = fail_on  # arg-list prefix that raises, simulating a failed winter step
        # What `winter ws worktrees --json` reports; the last two shapes exist to be
        # filtered out.
        self.worktrees: list[dict[str, object]] = []

    def ensure_ready(self, workspace_root: Path) -> None:
        self.ready += 1

    def run(self, workspace_root: Path, args: Sequence[str]) -> None:
        args = list(args)
        if self.fail_on is not None and args[: len(self.fail_on)] == self.fail_on:
            raise RuntimeError("scripted winter failure")
        self.runs.append(args)

    def capture(self, workspace_root: Path, args: Sequence[str]) -> str:
        self.captures.append(list(args))
        if list(args)[:2] == ["ws", "worktrees"]:
            return json.dumps(self.worktrees)
        bound = "winter-service-tmux" if self.service_bound else None
        return json.dumps([{"slot": "service", "bound": bound, "binding_kind": "explicit"}])


class FakeGit:
    def __init__(self, origins: dict[str, str] | None = None) -> None:
        self.cleans: list[str] = []
        self.origin_reads: list[str] = []
        self._origins = origins or {}

    def clean_environment(self, env_workdir: Path) -> None:
        self.cleans.append(str(env_workdir))

    def origin_url(self, repo_workdir: Path) -> str:
        self.origin_reads.append(str(repo_workdir))
        return self._origins.get(str(repo_workdir), f"file:///origins/{Path(repo_workdir).name}.git")
