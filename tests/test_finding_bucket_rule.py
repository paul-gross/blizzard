"""``FindingBucket.of`` (unit tier, by value): which findings a run may cite, in what
order, and which are carried only as exited ids."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.garden.findings.bucket import FindingBucket
from blizzard.hub.domain.garden.findings.model import Finding

pytestmark = pytest.mark.unit

_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _finding(finding_id: str, *, scope: str = "runner", state: str = "live") -> Finding:
    return Finding(
        finding_id=finding_id,
        routine_name="nightly",
        scope_slug=scope,
        class_="c",
        locus="a.py:1",
        summary="s",
        introduced=None,
        introduced_at=None,
        first_observed_at=_AT,
        live=state == "live",
        state=state,
        note=None,
        last_seen_at=_AT,
        observed_count=0,
    )


def test_unexited_findings_are_citable_own_scope_first_then_by_id() -> None:
    bucket = FindingBucket.of(
        [_finding("fin_3", scope="hub"), _finding("fin_2"), _finding("fin_1", scope="hub"), _finding("fin_4")],
        own_scope="runner",
    )

    assert [f.finding_id for f in bucket.citable] == ["fin_2", "fin_4", "fin_1", "fin_3"]


@pytest.mark.parametrize("state", ["gone", "delivered"])
def test_a_gone_or_delivered_finding_stays_citable(state: str) -> None:
    bucket = FindingBucket.of([_finding("fin_1", state=state)], own_scope="runner")

    assert [f.finding_id for f in bucket.citable] == ["fin_1"]
    assert bucket.exited_ids == frozenset()


def test_an_exited_finding_is_only_an_id() -> None:
    bucket = FindingBucket.of([_finding("fin_1", state="wont-fix"), _finding("fin_2")], own_scope="runner")

    assert [f.finding_id for f in bucket.citable] == ["fin_2"]
    assert bucket.exited_ids == frozenset({"fin_1"})


def test_a_finding_read_twice_appears_once() -> None:
    bucket = FindingBucket.of([_finding("fin_1"), _finding("fin_1")], own_scope="runner")

    assert [f.finding_id for f in bucket.citable] == ["fin_1"]
