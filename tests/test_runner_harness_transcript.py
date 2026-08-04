"""The harness transcript seam's values and null source (blizzard#245).

Covers :class:`TranscriptPosition`'s opaque round-trip, :class:`NullTranscriptSource`'s
absent-but-healthy shape, and that :class:`ClaudeCodeAdapter` binds it by default —
the value shapes and Protocol this issue adds. The Claude Code source's own
file-location and normalization mechanics are pinned separately, in
``test_runner_harness_claude_code_transcript.py`` and
``test_runner_harness_claude_code_normalizer.py``.
"""

from __future__ import annotations

import pytest

from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.transcript import (
    NullTranscriptSource,
    TranscriptPosition,
)


@pytest.mark.unit
def test_transcript_position_round_trips_its_opaque_token() -> None:
    position = TranscriptPosition(token='{"main.jsonl": 1024}')
    assert position.token == '{"main.jsonl": 1024}'
    # Equal tokens compare equal — a frozen dataclass, not an identity-only value.
    assert position == TranscriptPosition(token='{"main.jsonl": 1024}')
    assert position != TranscriptPosition(token='{"main.jsonl": 2048}')


@pytest.mark.unit
def test_null_transcript_source_turns_since_matches_absent_but_healthy_shape() -> None:
    batch = NullTranscriptSource().turns_since("sid-1", spawn_cwd=None, since=None)
    assert batch.session_id == "sid-1"
    assert batch.available is False
    assert batch.reason == "not_found"
    assert batch.turns == []
    assert batch.unlinked_sidechains == []
    assert batch.next_position is None
    assert batch.complete is True
    assert batch.truncated is False
    assert batch.harness_version is None


@pytest.mark.unit
def test_null_transcript_source_read_raw_lines_is_empty() -> None:
    assert NullTranscriptSource().read_raw_lines("sid-1", spawn_cwd=None) == []


@pytest.mark.unit
def test_null_transcript_source_size_bytes_is_unknown_not_zero() -> None:
    # `None` is the unknown that leaves a rotation head standing — never `0`, which
    # would read as "well under bound" and make the threshold inert.
    assert NullTranscriptSource().size_bytes("sid-1", spawn_cwd=None) is None


@pytest.mark.unit
def test_claude_code_adapter_defaults_to_the_null_transcript_source() -> None:
    source = ClaudeCodeAdapter().transcript_source()
    assert isinstance(source, NullTranscriptSource)


@pytest.mark.unit
def test_claude_code_adapter_returns_the_injected_transcript_source() -> None:
    injected = NullTranscriptSource()
    adapter = ClaudeCodeAdapter(transcript_source=injected)
    assert adapter.transcript_source() is injected
