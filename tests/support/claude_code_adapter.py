"""The Claude Code adapter — verdict parsing (unit) and a real subprocess (component).

``parse_verdict`` is exercised over the harness-native JSON envelope and its failure
modes; the component test drives spawn/resume against a real fake-harness binary. Also
covers usage parsing, the per-lease stdout redirect, and node-entry resume.
"""

from __future__ import annotations

from concurrent.futures import Executor
from typing import Any

from blizzard.runner.harness.claude_code.adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.process.child_env import AllowlistedEnv
from tests.support.child_launch import CHILDREN, version_probe
from tests.support.runner_fakes import FakeProbe


def adapter(spawn_executor: Executor, **kwargs: Any) -> ClaudeCodeAdapter:
    """A :class:`ClaudeCodeAdapter` helper defaulting ``process`` to a fresh :class:`FakeProbe`
    and ``launcher`` to a real one over it — most cases here don't care which they get."""
    process = kwargs.setdefault("process", FakeProbe())
    kwargs.setdefault("launcher", ProcessLauncher(process, executor=spawn_executor, children=CHILDREN))
    kwargs.setdefault("worker_env", AllowlistedEnv.of(()))
    kwargs.setdefault("versions", version_probe())
    return ClaudeCodeAdapter(**kwargs)


SENTINEL_UNLISTED_VAR = "MY_UNLISTED_SENTINEL_VAR"
