"""Shared fixtures for the transcript route tests (``tests/unit/runner/api/test_transcripts.py``, ``tests/component/runner/api/test_transcripts.py``)."""

from __future__ import annotations

from pathlib import Path

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.transcripts.internal.harness_transcript_repositories import HarnessTranscriptRepositories
from blizzard.runner.transcripts.repository import Transcript
from blizzard.runner.transcripts.service import TranscriptService
from tests.config_inputs import create_runner_app
from tests.runner_fakes import (
    FakeArchivedTranscriptRepository,
    FakeProvider,
    StaticTranscriptRepositoryResolver,
    make_host_stores,
    make_store,
    make_stores,
    single_workspace,
)


class FakeTranscriptRepository:
    """An in-process ``IReadTranscriptRepository`` — one canned ``Transcript`` per session id.

    ``spawn_cwd`` is recorded but not consulted; this fake exists to control what the
    route sees, not to re-derive the disambiguation hint."""

    def __init__(self, by_session_id: dict[str, Transcript] | None = None) -> None:
        self._by_session_id = by_session_id or {}
        self.calls: list[tuple[str, str | None]] = []

    def read_turns(self, session_id: str, *, spawn_cwd: str | None, since: str | None = None) -> Transcript:
        self.calls.append((session_id, spawn_cwd))
        if session_id in self._by_session_id:
            return self._by_session_id[session_id]
        return Transcript(session_id=session_id, available=False, reason="not_found", turns=[], truncated=False)


def app_with_transcripts(
    tmp_path: Path,
    *,
    repo: FakeTranscriptRepository | None = None,
    archived: FakeArchivedTranscriptRepository | None = None,
    workspace_root: str = "",
    harnesses: HarnessRegistry | None = None,
):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    repo = repo or FakeTranscriptRepository()
    archived = archived or FakeArchivedTranscriptRepository()
    transcripts = (
        HarnessTranscriptRepositories(harnesses) if harnesses is not None else StaticTranscriptRepositoryResolver(repo)
    )
    service = TranscriptService(
        leases=store,
        transcript_ledger=store,
        environments=store,
        transcripts=transcripts,
        archived=archived,
        workspaces=single_workspace(FakeProvider({}), spawn_root=workspace_root),
    )
    return (
        create_runner_app(
            config, runner_stores=make_stores(store), host_stores=make_host_stores(store), transcripts=service
        ),
        store,
        repo,
    )
