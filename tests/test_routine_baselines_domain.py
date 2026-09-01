"""``RoutineBaselineService`` (unit tier, blizzard#392 D5): the per-scope delta
baseline a routine has swept, composed over doubled seams."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.ids import Id
from blizzard.hub.domain.chunks.delivery import IReadChunkDeliveryRepository
from blizzard.hub.domain.findings import FindingSet, IReadFindingSetRepository
from blizzard.hub.domain.routine_baselines import MalformedFindingSetIdError, RepoLandings, RoutineBaselineService

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _finding_set_id(at: datetime) -> str:
    return Id.mint_at("fins", at).value


@dataclass
class _FakeFindingSets:
    by_routine: dict[str, list[FindingSet]] = field(default_factory=dict)

    def newest_by_scope_for_routine(self, routine_name: str) -> list[FindingSet]:
        return self.by_routine.get(routine_name, [])

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


@dataclass
class _FakeDelivery:
    counts: dict[tuple[str, datetime], int] = field(default_factory=dict)
    calls: list[tuple[str, datetime]] = field(default_factory=list)

    def count_landed_since(self, repo: str, since: datetime) -> int:
        self.calls.append((repo, since))
        return self.counts.get((repo, since), 0)

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _service(
    *, finding_sets: _FakeFindingSets | None = None, delivery: _FakeDelivery | None = None
) -> tuple[RoutineBaselineService, _FakeFindingSets, _FakeDelivery]:
    finding_sets = finding_sets or _FakeFindingSets()
    delivery = delivery or _FakeDelivery()
    service = RoutineBaselineService(
        finding_sets=cast(IReadFindingSetRepository, finding_sets),
        delivery=cast(IReadChunkDeliveryRepository, delivery),
    )
    return service, finding_sets, delivery


def test_a_swept_pair_yields_its_baseline_with_the_instant_decoded_from_the_id() -> None:
    finding_set_id = _finding_set_id(_T0)
    finding_set = FindingSet(
        finding_set_id=finding_set_id,
        artifact_id="art_1",
        chunk_id="ch_1",
        scope_slug="blizzard",
        routine_name="gardening",
        revisions={"blizzard": "a1b2c3d"},
        measurement=None,
    )
    delivery = _FakeDelivery(counts={("blizzard", _T0): 7})
    service, *_ = _service(finding_sets=_FakeFindingSets(by_routine={"gardening": [finding_set]}), delivery=delivery)

    baselines = service.baselines_for("gardening")

    assert len(baselines) == 1
    baseline = baselines[0]
    assert baseline.scope_slug == "blizzard"
    assert baseline.finding_set_id == finding_set_id
    assert baseline.recorded_at == _T0
    assert baseline.repos == [RepoLandings(repo="blizzard", revision="a1b2c3d", landed_since=7)]


def test_an_unswept_pair_yields_no_entry_at_all() -> None:
    service, *_ = _service()

    assert service.baselines_for("gardening") == []


def test_landed_since_is_queried_per_repo_against_the_recorded_instant() -> None:
    finding_set_id = _finding_set_id(_T0)
    finding_set = FindingSet(
        finding_set_id=finding_set_id,
        artifact_id="art_1",
        chunk_id="ch_1",
        scope_slug="blizzard",
        routine_name="gardening",
        revisions={"blizzard": "a1", "web": "b2"},
        measurement=None,
    )
    service, _fs, delivery = _service(finding_sets=_FakeFindingSets(by_routine={"gardening": [finding_set]}))

    service.baselines_for("gardening")

    assert set(delivery.calls) == {("blizzard", _T0), ("web", _T0)}


def test_baselines_are_ordered_newest_swept_first() -> None:
    older = FindingSet(
        finding_set_id=_finding_set_id(datetime(2026, 1, 1, tzinfo=UTC)),
        artifact_id="art_1",
        chunk_id="ch_1",
        scope_slug="blizzard",
        routine_name="gardening",
        revisions={},
        measurement=None,
    )
    newer = FindingSet(
        finding_set_id=_finding_set_id(datetime(2026, 2, 1, tzinfo=UTC)),
        artifact_id="art_2",
        chunk_id="ch_1",
        scope_slug="web",
        routine_name="gardening",
        revisions={},
        measurement=None,
    )
    service, *_ = _service(finding_sets=_FakeFindingSets(by_routine={"gardening": [older, newer]}))

    baselines = service.baselines_for("gardening")

    assert [b.scope_slug for b in baselines] == ["web", "blizzard"]


def test_a_malformed_finding_set_id_raises() -> None:
    finding_set = FindingSet(
        finding_set_id="fins_not-a-ulid",
        artifact_id="art_1",
        chunk_id="ch_1",
        scope_slug="blizzard",
        routine_name="gardening",
        revisions={},
        measurement=None,
    )
    service, *_ = _service(finding_sets=_FakeFindingSets(by_routine={"gardening": [finding_set]}))

    with pytest.raises(MalformedFindingSetIdError):
        service.baselines_for("gardening")
