"""``GardenDeliveryRecorder`` retries a lost finding-state guard against a fresh bucket and resolves each commit once."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import pytest

from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.garden.delivery.materialize import (
    DeliveryOutcome,
    GardenDelivery,
)
from blizzard.hub.domain.garden.delivery.service import GardenDeliveryRecorder
from blizzard.hub.domain.garden.delivery.validation import GardenDeliveryRejected, ValidatedDelivery
from blizzard.hub.domain.garden.findings.bucket import FindingBucket
from blizzard.hub.domain.garden.findings.model import (
    GUARD_ATTEMPTS,
)
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.graph.model import Node

_PROJECT = "proj_test"

_RUN = RunContext(project_id=_PROJECT, routine_name="nightly", scope_slug="blizzard", mode="full")


class _Artifact:
    def __init__(self, name: str) -> None:
        self.data = "{}"
        self.artifact_id = f"art_{name}"


class _Artifacts:
    def latest_artifacts(self, chunk_id: str, names: Sequence[str]) -> dict[str, _Artifact]:
        return {name: _Artifact(name) for name in names}


class _Buckets:
    def __init__(self) -> None:
        self.reads = 0
        self.events: list[str] | None = None

    def for_run(self, run: RunContext) -> FindingBucket:
        self.reads += 1
        if self.events is not None:
            self.events.append("validate")
        return FindingBucket(citable=[])


class _Formats:
    def finding_delta(self, name: str, raw: str) -> Any:
        from blizzard.hub.domain.garden.formats import DeliveredDelta

        return DeliveredDelta(scope="blizzard", revisions={"blizzard": "a" * 40}, findings=[], measurement=None)

    def proposal_candidates(self, name: str, raw: str) -> list[Any]:
        return []


class _Materialize:
    def __init__(self, outcomes: list[DeliveryOutcome]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.delivered: list[tuple[ValidatedDelivery, dict[str, object]]] = []

    def already_delivered(self, *, chunk: Chunk, node: Node, epoch: int) -> bool:
        return False

    def deliver(self, validated: ValidatedDelivery, **kwargs: object) -> DeliveryOutcome:
        self.calls += 1
        self.delivered.append((validated, kwargs))
        return self.outcomes.pop(0)


def _recorder(
    outcomes: list[DeliveryOutcome],
    resolved: list[tuple[str, str]],
    events: list[str] | None = None,
    *,
    projects: list[str] | None = None,
    with_resolver: bool = True,
) -> tuple[GardenDeliveryRecorder, Any, Any]:
    from blizzard.hub.domain.garden.delivery.validation import CommitResolution

    def resolve(repo: str, sha: str) -> CommitResolution:
        resolved.append((repo, sha))
        if events is not None:
            events.append("resolve")
        return CommitResolution(exists=True)

    def resolver_for(project_id: str) -> Any:
        if projects is not None:
            projects.append(project_id)
        return resolve

    buckets, materialize = _Buckets(), _Materialize(outcomes)
    buckets.events = events
    recorder = GardenDeliveryRecorder(
        artifacts=cast(Any, _Artifacts()),
        buckets=cast(Any, buckets),
        materialize=cast(GardenDelivery, materialize),
        formats=cast(Any, _Formats()),
        resolve_commit_for=resolver_for if with_resolver else None,
    )
    return recorder, buckets, materialize


def _record(recorder: GardenDeliveryRecorder) -> DeliveryOutcome:
    return recorder.record(
        chunk=cast(Any, type("C", (), {"chunk_id": "ch_1"})()),
        node=cast(Any, type("N", (), {"node_id": "nd_1"})()),
        epoch=1,
        run=_RUN,
        delta_names=["d"],
        proposal_names=[],
    )


def test_recorder_retries_a_lost_guard_against_a_fresh_bucket_and_resolves_each_commit_once() -> None:
    resolved: list[tuple[str, str]] = []
    recorder, buckets, materialize = _recorder([DeliveryOutcome.GUARD_LOST, DeliveryOutcome.RECORDED], resolved)

    assert _record(recorder) is DeliveryOutcome.RECORDED
    assert buckets.reads == 2
    assert materialize.calls == 2
    assert resolved == [("blizzard", "a" * 40)]


def test_recorder_resolves_every_commit_before_validation_starts() -> None:
    events: list[str] = []
    recorder, _, _ = _recorder([DeliveryOutcome.GUARD_LOST, DeliveryOutcome.RECORDED], [], events)

    _record(recorder)

    assert events == ["resolve", "validate", "validate"]


def test_recorder_refuses_once_the_guard_attempts_run_out() -> None:
    recorder, buckets, materialize = _recorder([DeliveryOutcome.GUARD_LOST] * GUARD_ATTEMPTS, [])

    with pytest.raises(GardenDeliveryRejected) as exc:
        _record(recorder)

    assert materialize.calls == GUARD_ATTEMPTS
    assert buckets.reads == GUARD_ATTEMPTS
    assert str(exc.value) == (
        "findings this delivery names kept changing while it was being recorded — nothing was written; "
        "re-run the delivery"
    )


def test_recorder_asks_the_resolver_factory_for_the_runs_project() -> None:
    projects: list[str] = []
    recorder, _, _ = _recorder([DeliveryOutcome.RECORDED], [], projects=projects)

    _record(recorder)

    assert projects == [_PROJECT]


def test_recorder_with_no_resolver_factory_resolves_nothing_and_still_delivers() -> None:
    resolved: list[tuple[str, str]] = []
    recorder, _, materialize = _recorder([DeliveryOutcome.RECORDED], resolved, with_resolver=False)

    assert _record(recorder) is DeliveryOutcome.RECORDED
    assert resolved == []
    assert materialize.calls == 1


def test_recorder_hands_the_node_step_and_the_delta_artifact_ids_to_the_write() -> None:
    recorder, _, materialize = _recorder([DeliveryOutcome.RECORDED], [])

    _record(recorder)

    [(validated, kwargs)] = materialize.delivered
    assert validated.run == _RUN
    assert (kwargs["epoch"], kwargs["delta_artifact_ids"], kwargs["proposal_artifact_ids"]) == (1, ["art_d"], [])
    assert (kwargs["chunk"].chunk_id, kwargs["node"].node_id) == ("ch_1", "nd_1")
