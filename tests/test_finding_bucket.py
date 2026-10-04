"""``FindingBucketReader`` (unit tier) — a run's bucket over an in-memory finding store:
the routine's own non-exited findings across every scope, review findings on the run's
scope only, exited ids carried but never shown, own scope ordered first."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import Finding
from blizzard.hub.domain.garden.run_context import RunContext

pytestmark = pytest.mark.unit
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_RUN = RunContext(routine_name="nightly", scope_slug="runner", mode="full")


def _finding(finding_id: str, *, scope: str, state: str = "live", routine: str = "nightly") -> Finding:
    return Finding(
        finding_id=finding_id,
        routine_name=routine,
        scope_slug=scope,
        class_="c",
        locus="a.py:1",
        summary="s",
        introduced=None,
        introduced_at=None,
        first_observed_at=_T0,
        live=state == "live",
        state=state,
        note=None,
        last_seen_at=_T0,
        observed_count=1,
        actor=None,
    )


class _Repo:
    def __init__(self, routine: list[Finding], review: list[Finding]) -> None:
        self._routine = routine
        self._review = review

    def list_for_routine(self, routine_name: str, *, include_gone: bool = False) -> list[Finding]:
        assert include_gone
        return [f for f in self._routine if f.routine_name == routine_name]

    def list_by_source(self, *, scope_slug: str, source: str, include_gone: bool = False) -> list[Finding]:
        assert include_gone and source == "review"
        return [f for f in self._review if f.scope_slug == scope_slug]


def _reader(routine: list[Finding], review: list[Finding]) -> FindingBucketReader:
    return FindingBucketReader(cast(Any, _Repo(routine, review)))


def test_bucket_spans_scopes_and_orders_own_scope_first() -> None:
    routine = [
        _finding("fin_c", scope="other"),
        _finding("fin_b", scope="runner"),
        _finding("fin_a", scope="other"),
        _finding("fin_d", scope="runner"),
    ]
    bucket = _reader(routine, []).for_run(_RUN)
    assert [f.finding_id for f in bucket.citable] == ["fin_b", "fin_d", "fin_a", "fin_c"]


def test_gone_is_shown_and_exited_is_only_carried_by_id() -> None:
    routine = [
        _finding("fin_a", scope="other", state="gone"),
        _finding("fin_b", scope="runner", state="delivered"),
        _finding("fin_c", scope="other", state="resolved"),
    ]
    bucket = _reader(routine, []).for_run(_RUN)
    assert {f.finding_id for f in bucket.citable} == {"fin_a", "fin_b"}
    assert bucket.exited_ids == {"fin_c"}


def test_review_findings_only_on_the_runs_own_scope() -> None:
    review = [
        _finding("fin_r1", scope="runner", routine="review"),
        _finding("fin_r2", scope="other", routine="review"),
    ]
    bucket = _reader([], review).for_run(_RUN)
    assert [f.finding_id for f in bucket.citable] == ["fin_r1"]
