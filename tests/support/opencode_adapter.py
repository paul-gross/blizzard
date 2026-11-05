"""The OpenCode adapter — command composition (unit) and a real subprocess (component).

Unit tests drive command building and model/effort/compaction resolution against a
monkeypatched ``subprocess.Popen``; component tests launch a fake ``opencode`` binary
(``tests/support/opencode_binary.py``)."""

from __future__ import annotations

from concurrent.futures import Executor
from typing import Any

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.opencode.adapter import (
    OpenCodeAdapter,
)
from blizzard.runner.process.child_env import AllowlistedEnv
from tests.support.child_launch import CHILDREN, version_probe
from tests.support.runner_fakes import FakeProbe


def adapter(spawn_executor: Executor, **kwargs: Any) -> OpenCodeAdapter:
    process = kwargs.setdefault("process", FakeProbe())
    kwargs.setdefault("launcher", ProcessLauncher(process, executor=spawn_executor, children=CHILDREN))
    kwargs.setdefault("worker_env", AllowlistedEnv.of(()))
    return OpenCodeAdapter(**kwargs, versions=version_probe())


def preamble(workdir: str, *, stdout_path: str = "", stderr_path: str = "") -> WorkerPreamble:
    return WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=workdir)],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
