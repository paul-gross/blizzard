"""The cross-daemon invariant binding the transcript lane's two per-record caps
(blizzard#247 D4/D5). Neither side can see the other's constant, so nothing but this
file keeps them ordered — and the ordering is what decides whether an oversized record
loses some of its content or all of it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from blizzard.hub.domain.transcripts import RECORD_MAX_BYTES
from blizzard.runner.loop.transcript_pump import TRANSCRIPT_RECORD_MAX_BYTES

pytestmark = pytest.mark.unit

_DEPLOYMENT_DOC = Path(__file__).resolve().parents[1] / "docs" / "deployment.md"


def test_runner_record_cap_stays_below_the_hub_backstop() -> None:
    """The runner's cap shrinks fields and keeps every turn; the hub's REJECTS the record
    and stores its turns as ``[]``. Inverting them turns every partial loss into a total
    one, silently — the runner would keep shipping records the hub then throws away."""
    assert TRANSCRIPT_RECORD_MAX_BYTES < RECORD_MAX_BYTES


def test_hub_backstop_leaves_headroom_for_the_runner_cap() -> None:
    """Ordering alone is not enough: the hub counts only ``turns_json`` while the runner
    counts the whole serialized record, so the two measure different things and a razor-thin
    gap could invert under a heavy envelope. Demand real margin, not one byte of it."""
    assert RECORD_MAX_BYTES - TRANSCRIPT_RECORD_MAX_BYTES >= 1024 * 1024


@pytest.mark.parametrize(
    ("pattern", "expected_mb", "what"),
    [
        (r"its own (\d+) MB per-record cap", TRANSCRIPT_RECORD_MAX_BYTES // (1024 * 1024), "runner"),
        (r"rogue case — (\d+) MB/record", RECORD_MAX_BYTES // (1024 * 1024), "hub"),
    ],
)
def test_the_operator_doc_states_the_cap_magnitude_the_code_enforces(pattern: str, expected_mb: int, what: str) -> None:
    """`bzh:one-prose-home` sanctions the operator doc restating these — its reader has no
    source tree — which makes the restatement's currency an obligation. Both went stale the
    first time the caps moved, and the sweep cannot see it: it matches phrases, not values."""
    found = re.findall(pattern, _DEPLOYMENT_DOC.read_text(encoding="utf-8"))

    assert found, f"docs/deployment.md no longer states the {what} per-record cap as /{pattern}/"
    assert [int(mb) for mb in found] == [expected_mb] * len(found), (
        f"docs/deployment.md states {found} MB for the {what} per-record cap; the code enforces {expected_mb} MB"
    )
