"""Claude Code's default transcript root follows the worker's Claude config home, the one the ambient check reads."""

from __future__ import annotations

from concurrent.futures import Executor
from dataclasses import replace
from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock, SystemMonotonicClock
from blizzard.runner.harness.claude_code.ambient import user_config_dir
from blizzard.runner.harness.claude_code.declaration import CLAUDE_CODE_DECLARATION
from blizzard.runner.harness.claude_code.transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.wiring import shared_inputs
from blizzard.runner.process.child_env import AllowlistedEnv
from tests.support.child_launch import CHILDREN
from tests.support.config_inputs import init_host
from tests.support.harness_sections import claude_code
from tests.support.runner_fakes import NO_TRACING, FakeProbe


def _projects_root(tmp_path: Path, spawn_executor: Executor, *, transcripts_root: str) -> Path:
    config = init_host(tmp_path / "runtime").runner_config()
    shared = replace(
        shared_inputs(config.harness_settings(NO_TRACING)),
        worker_env=AllowlistedEnv.of(["CLAUDE_CONFIG_DIR", "HOME"]),
        transcripts_root=transcripts_root,
    )
    probe = FakeProbe()
    source = CLAUDE_CODE_DECLARATION.assemble(
        claude_code(config),
        shared,
        process=probe,
        launcher=ProcessLauncher(probe, executor=spawn_executor, children=CHILDREN),
        children=CHILDREN,
        clock=SystemClock(),
        monotonic_clock=SystemMonotonicClock(),
        health_roots=("",),
        corpus=CommittedCorpus(),
    ).binding.transcript_source
    assert isinstance(source, ClaudeCodeTranscriptSource)
    return source._projects_root  # pyright: ignore[reportPrivateUsage]


def test_the_transcript_root_shares_the_ambient_checks_config_home(
    tmp_path: Path, spawn_executor: Executor, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_home))
    monkeypatch.setenv("HOME", str(tmp_path / "daemon-home"))
    root = _projects_root(tmp_path, spawn_executor, transcripts_root="")
    assert root == config_home / "projects"
    assert user_config_dir({"CLAUDE_CONFIG_DIR": str(config_home)}) == root.parent


def test_the_transcript_root_defaults_under_the_workers_home(
    tmp_path: Path, spawn_executor: Executor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "worker-home"))
    assert _projects_root(tmp_path, spawn_executor, transcripts_root="") == tmp_path / "worker-home/.claude/projects"


def test_an_explicit_transcripts_root_wins(
    tmp_path: Path, spawn_executor: Executor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    explicit = tmp_path / "explicit"
    assert _projects_root(tmp_path, spawn_executor, transcripts_root=str(explicit)) == explicit
