"""``src/blizzard/runner/transcripts/internal/projected_transcript_repository.py`` — the panel's read model.

How a scripted batch's truncation flags reach the panel's ``truncated`` flag.
"""

from __future__ import annotations

from blizzard.runner.harness.transcript import TranscriptBatch
from blizzard.runner.transcripts.internal.projected_transcript_repository import ProjectedTranscriptRepository
from tests.support.runner_fakes import FakeTranscriptSource


def _scripted_batch(*, truncated: bool, sidechain_truncated: bool) -> TranscriptBatch:
    return TranscriptBatch(
        session_id="sess-1",
        available=True,
        reason=None,
        turns=[],
        unlinked_sidechains=[],
        next_position=None,
        complete=True,
        truncated=truncated,
        sidechain_truncated=sidechain_truncated,
        normalizer_version="fake/1",
        harness_version=None,
    )


def test_a_sidechain_only_truncation_now_reaches_the_panels_truncated_flag() -> None:
    """A sidecar-only read-budget truncation sets the transcript's ``truncated`` flag."""
    source = FakeTranscriptSource({"sess-1": _scripted_batch(truncated=False, sidechain_truncated=True)})

    transcript = ProjectedTranscriptRepository(source).read_turns("sess-1", spawn_cwd=None)

    assert transcript.truncated is True


def test_a_main_file_truncation_does_reach_the_panels_truncated_flag() -> None:
    """A main-file tail-cap truncation sets the transcript's ``truncated`` flag."""
    source = FakeTranscriptSource({"sess-1": _scripted_batch(truncated=True, sidechain_truncated=False)})

    transcript = ProjectedTranscriptRepository(source).read_turns("sess-1", spawn_cwd=None)

    assert transcript.truncated is True
