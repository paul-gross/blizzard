"""Harness identity and exact-registry contracts (unit)."""

from __future__ import annotations

import pytest

from blizzard.runner.app import create_app_for_export
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import (
    HarnessBinding,
    HarnessRegistry,
    UnavailableHarnessError,
    UnknownHarnessError,
)
from tests.runner_fakes import FakeHarness, FakeTranscriptSource


def _harness() -> FakeHarness:
    return FakeHarness(handle=WorkerHandle(session_id="session", pid=1, process_start_time="start", pgid=1), verdict=None)


@pytest.mark.unit
def test_session_reference_requires_both_owner_and_raw_session_id() -> None:
    assert SessionReference(CLAUDE_CODE_HARNESS_ID, "session") == SessionReference("claude_code", "session")

    with pytest.raises(ValueError, match="harness id"):
        SessionReference("", "session")
    with pytest.raises(ValueError, match="session id"):
        SessionReference(CLAUDE_CODE_HARNESS_ID, "")


@pytest.mark.unit
def test_registry_resolves_only_the_exact_requested_owner() -> None:
    adapter = _harness()
    source = FakeTranscriptSource()
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter, transcript_source=source)})

    assert registry.adapter(CLAUDE_CODE_HARNESS_ID) is adapter
    assert registry.transcript_source(CLAUDE_CODE_HARNESS_ID) is source
    with pytest.raises(UnknownHarnessError) as raised:
        registry.adapter("other")
    assert raised.value.known == (CLAUDE_CODE_HARNESS_ID,)


@pytest.mark.unit
def test_registry_distinguishes_a_known_unavailable_capability() -> None:
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=_harness())})

    with pytest.raises(UnavailableHarnessError) as raised:
        registry.transcript_source(CLAUDE_CODE_HARNESS_ID)
    assert raised.value.capability == "transcript source"


@pytest.mark.unit
def test_export_app_has_an_empty_hermetic_harness_registry() -> None:
    app = create_app_for_export()

    assert app.state.harnesses.known_harnesses == ()
