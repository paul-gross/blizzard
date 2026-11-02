"""Completion escalation decisions are rechecked on the chunk's write transaction."""

from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.execution.apply import ApplyService
from blizzard.hub.store import schema as s
from tests.support import build_hub
from tests.test_escalate_choice import _Chunk

pytestmark = pytest.mark.component


@pytest.mark.parametrize(
    "winner,loser,expected",
    [
        ("pass", "blocked", "done"),
        ("unfulfillable", "unfulfillable", "parked_at_gate"),
        ("unfulfillable", "blocked", "failure"),
    ],
)
def test_escalation_rechecks_after_another_completion_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winner: str, loser: str, expected: str
) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    original = ApplyService._record_completion_escalation
    fired = False

    def interleave(self: ApplyService, *args: Any, **kwargs: Any) -> Any:
        nonlocal fired
        if not fired:
            fired = True
            chunk.complete(winner)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ApplyService, "_record_completion_escalation", interleave)
    outcome = chunk.complete(loser)

    assert outcome["outcome"] == expected
    assert chunk.rows(s.escalations) == (0 if winner == "pass" else 1)
    assert chunk.rows(s.transitions) == (1 if winner == "pass" else 0)
