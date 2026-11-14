"""Hosted transcript and takeover dispatch follows invocation ownership across replacement."""

from pathlib import Path
from unittest.mock import Mock

from blizzard.foundation.clock import FixedClock
from blizzard.runner.app import _harnesses_for_lease  # pyright: ignore[reportPrivateUsage]
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.harness_set import HarnessAssemblyKey, HarnessSet, HarnessSets, WorkerInvocation
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.lifecycle.takeover import TakeoverService
from blizzard.runner.transcripts.internal.harness_transcript_repositories import HarnessTranscriptRepositories
from blizzard.runner.transcripts.service import TranscriptService
from tests.component.runner.lifecycle.test_resume_judgement_takeover_harness_isolation import (
    _NOW,  # pyright: ignore[reportPrivateUsage]
    _SHARED_SESSION_ID,  # pyright: ignore[reportPrivateUsage]
    _open_scope,  # pyright: ignore[reportPrivateUsage]
    _seed_lease,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.runner_fakes import (
    FakeArchivedTranscriptRepository,
    FakeHarness,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    make_store,
    single_workspace,
)


def test_hosted_services_follow_retained_then_current_set(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    for suffix in ("a", "b"):
        _seed_lease(
            store, lease_id=f"lease_{suffix}", chunk_id=f"ch_{suffix}", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100
        )
        store.record_park(
            lease_id=f"lease_{suffix}", chunk_id=f"ch_{suffix}", question_id=f"qn_{suffix}", parked_at=_NOW
        )
    handle = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    old_source, new_source = FakeTranscriptSource(), FakeTranscriptSource()
    old_harness, new_harness = FakeHarness(handle=handle, verdict=None), FakeHarness(handle=handle, verdict=None)
    old_registry = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=old_harness, transcript_source=old_source)}
    )
    new_registry = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=new_harness, transcript_source=new_source)}
    )
    sets = HarnessSets(
        Mock(side_effect=[Mock(spec=HarnessSet, registry=old_registry), Mock(spec=HarnessSet, registry=new_registry)])
    )
    sets.select(HarnessAssemblyKey((1,)))
    sets.retain(WorkerInvocation("lease_a", 1))
    graph = Mock(harness_sets=sets)

    def resolve(lease_id: str) -> HarnessRegistry:
        return _harnesses_for_lease(graph, lease_id)

    workspaces = single_workspace(FakeProvider({}))
    transcripts = TranscriptService(
        store,
        store,
        store,
        HarnessTranscriptRepositories(old_registry),
        FakeArchivedTranscriptRepository(),
        workspaces,
        transcripts_for_lease=lambda lease_id: HarnessTranscriptRepositories(resolve(lease_id)),
    )
    takeover = TakeoverService(
        FixedClock(_NOW),
        FakeProbe(),
        takeover=store,
        elicitations=store,
        local_api_url="http://localhost",
        harnesses=old_registry,
        harnesses_for_lease=resolve,
        workspaces=workspaces,
    )
    sets.select(HarnessAssemblyKey((2,)))
    for suffix in ("a", "b"):
        transcripts.for_lease(f"lease_{suffix}")
        [segment] = store.transcript_segments_for_chunk(f"ch_{suffix}")
        transcripts.segment_content(f"ch_{suffix}", segment.segment_id)
        takeover.open(_open_scope(store, f"ch_{suffix}"), force=False)
    assert len(old_source.turns_since_calls) == 2
    assert len(new_source.turns_since_calls) == 2
    assert len(old_harness.resume_command_config) == 1
    assert len(new_harness.resume_command_config) == 1
    sets.release_lease("lease_a")
    transcripts.for_lease("lease_a")
    assert len(new_source.turns_since_calls) == 3
