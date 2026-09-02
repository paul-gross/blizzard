"""``GardenRunService`` (unit tier) — the run list and one run's own delta, composed
over fake `IReadGardenRunRepository`/`IReadChunkRecordRepository`/
`IReadChunkFactsRepository` collaborators. No store: proves the outcome derivation, the
escalation carry, and the artifact-delta fold (added/observed/gone, positional add-id
matching) as pure composition over already-loaded facts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.garden_run import (
    DeliveredSet,
    DeliveredSetRaw,
    GardenRunService,
    RunIdentity,
    RunRecord,
)
from blizzard.hub.domain.work import Chunk, ChunkFacts, EscalationFact, MigrationFact, TransitionFact

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_SINCE = datetime(2025, 12, 1, tzinfo=UTC)
_UNTIL = datetime(2026, 2, 1, tzinfo=UTC)


@dataclass
class _FakeRepo:
    records: list[RunRecord] = field(default_factory=list)
    identities: dict[str, RunIdentity] = field(default_factory=dict)
    delivered: dict[str, list[DeliveredSetRaw]] = field(default_factory=dict)

    def runs_in_window(self, *, since: datetime, until: datetime) -> list[RunRecord]:
        return [r for r in self.records if since <= r.identity.minted_at < until]

    def run_identity(self, chunk_id: str) -> RunIdentity | None:
        return self.identities.get(chunk_id)

    def delivered_sets(self, chunk_id: str) -> list[DeliveredSetRaw]:
        return self.delivered.get(chunk_id, [])


@dataclass
class _FakeChunkRecords:
    chunks: dict[str, Chunk] = field(default_factory=dict)

    def get(self, chunk_id: str) -> Chunk | None:
        return self.chunks.get(chunk_id)

    def list_ready(self) -> list[Chunk]:
        return []

    def list_not_ready(self) -> list[Chunk]:
        return []

    def list_all(self) -> list[Chunk]:
        return list(self.chunks.values())


@dataclass
class _FakeChunkFacts:
    facts: dict[str, ChunkFacts] = field(default_factory=dict)

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.facts.get(chunk_id)

    def load_all_facts(self) -> dict[str, ChunkFacts]:
        return dict(self.facts)


def _chunk(chunk_id: str, *, graph_id: str = "gr_1") -> Chunk:
    return Chunk(chunk_id=chunk_id, graph_id=graph_id, work_refs=[], minted_at=_T0)


def _identity(
    chunk_id: str, *, routine_name: str = "nightly", scope_slug: str = "blizzard", mode: str = "full"
) -> RunIdentity:
    return RunIdentity(chunk_id=chunk_id, routine_name=routine_name, scope_slug=scope_slug, mode=mode, minted_at=_T0)


def _service(
    repo: _FakeRepo | None = None,
    chunk_records: _FakeChunkRecords | None = None,
    chunk_facts: _FakeChunkFacts | None = None,
) -> GardenRunService:
    return GardenRunService(
        repo=repo or _FakeRepo(),
        chunk_records=chunk_records or _FakeChunkRecords(),
        chunk_facts=chunk_facts or _FakeChunkFacts(),
    )


def _delta_artifact(findings: list[dict[str, object]], *, measurement: str | None = None) -> str:
    return json.dumps(
        {"scope": "blizzard", "revisions": {"blizzard": "aaa"}, "measurement": measurement, "findings": findings}
    )


# --------------------------------------------------------------------------- #
# list_runs


def test_a_delivered_run_reports_its_outcome_and_finding_sets() -> None:
    identity = _identity("ch_1")
    delivered = [DeliveredSet(finding_set_id="fins_1", revisions={"blizzard": "aaa"}, measurement="score: 4")]
    repo = _FakeRepo(records=[RunRecord(identity=identity, delivered=delivered)])
    chunk_records = _FakeChunkRecords(chunks={"ch_1": _chunk("ch_1")})
    chunk_facts = _FakeChunkFacts(facts={"ch_1": ChunkFacts(minted=True, promoted=True)})

    (row,) = _service(repo, chunk_records, chunk_facts).list_runs(since=_SINCE, until=_UNTIL)

    assert row.chunk_id == "ch_1"
    assert row.routine_name == "nightly"
    assert row.outcome == ChunkStatus.READY
    assert row.escalation is None
    assert row.delivered == delivered


def test_an_escalated_run_carries_its_node_and_takeover_command() -> None:
    identity = _identity("ch_1")
    repo = _FakeRepo(records=[RunRecord(identity=identity, delivered=[])])
    chunk_records = _FakeChunkRecords(chunks={"ch_1": _chunk("ch_1", graph_id="gr_9")})
    facts = ChunkFacts(
        minted=True,
        promoted=True,
        transitions=[
            TransitionFact(
                to_node_id="nd_build", to_node_executor=Executor.RUNNER, epoch=1, recorded_at=_T0, from_node_id=None
            )
        ],
        escalations=[
            EscalationFact(epoch=1, recorded_at=_T0, takeover_command="resume", wrapped_takeover_command="wrapped")
        ],
    )
    chunk_facts = _FakeChunkFacts(facts={"ch_1": facts})

    (row,) = _service(repo, chunk_records, chunk_facts).list_runs(since=_SINCE, until=_UNTIL)

    assert row.outcome == ChunkStatus.NEEDS_HUMAN
    assert row.escalation is not None
    assert row.escalation.graph_id == "gr_9"
    assert row.escalation.node_id == "nd_build"
    assert row.escalation.takeover_command == "resume"
    assert row.escalation.wrapped_takeover_command == "wrapped"


def test_an_escalation_reports_the_node_it_opened_on_not_a_later_migration() -> None:
    """A migration never supersedes `ChunkFacts.open_escalation`, so one recorded
    after the escalation can re-pin the chunk elsewhere while it stays open — the row
    must still name the node the escalation was actually raised from."""
    identity = _identity("ch_1")
    repo = _FakeRepo(records=[RunRecord(identity=identity, delivered=[])])
    chunk_records = _FakeChunkRecords(chunks={"ch_1": _chunk("ch_1", graph_id="gr_new")})
    facts = ChunkFacts(
        minted=True,
        promoted=True,
        transitions=[
            TransitionFact(
                to_node_id="nd_build",
                to_node_executor=Executor.RUNNER,
                epoch=1,
                recorded_at=_T0,
                from_node_id=None,
                graph_id="gr_old",
            )
        ],
        escalations=[
            EscalationFact(epoch=1, recorded_at=_T0, takeover_command="resume", wrapped_takeover_command="wrapped")
        ],
        migrations=[
            MigrationFact(
                from_node_id="nd_build",
                from_graph_id="gr_old",
                to_graph_id="gr_new",
                landed_node_id="nd_entry",
                choice_name=None,
                model=None,
                epoch=2,
                recorded_at=_T0.replace(year=_T0.year + 1),
            )
        ],
    )
    chunk_facts = _FakeChunkFacts(facts={"ch_1": facts})

    (row,) = _service(repo, chunk_records, chunk_facts).list_runs(since=_SINCE, until=_UNTIL)

    assert row.escalation is not None
    assert row.escalation.graph_id == "gr_old"
    assert row.escalation.node_id == "nd_build"


def test_a_run_that_delivered_nothing_still_lists_with_an_empty_delivered_list() -> None:
    """A run that delivered an empty finding list still leaves a `finding_sets` row —
    it is reported, not confused with an escalated run that left none at all."""
    identity = _identity("ch_1")
    delivered = [DeliveredSet(finding_set_id="fins_empty", revisions={}, measurement=None)]
    repo = _FakeRepo(records=[RunRecord(identity=identity, delivered=delivered)])
    chunk_records = _FakeChunkRecords(chunks={"ch_1": _chunk("ch_1")})
    chunk_facts = _FakeChunkFacts(facts={"ch_1": ChunkFacts(minted=True, promoted=True)})

    (row,) = _service(repo, chunk_records, chunk_facts).list_runs(since=_SINCE, until=_UNTIL)

    assert row.delivered == delivered


def test_several_delivered_sets_from_one_run_stay_separate_never_merged() -> None:
    identity = _identity("ch_1")
    delivered = [
        DeliveredSet(finding_set_id="fins_1", revisions={"a": "1"}, measurement=None),
        DeliveredSet(finding_set_id="fins_2", revisions={"b": "2"}, measurement="score: 9"),
    ]
    repo = _FakeRepo(records=[RunRecord(identity=identity, delivered=delivered)])
    chunk_records = _FakeChunkRecords(chunks={"ch_1": _chunk("ch_1")})
    chunk_facts = _FakeChunkFacts(facts={"ch_1": ChunkFacts(minted=True, promoted=True)})

    (row,) = _service(repo, chunk_records, chunk_facts).list_runs(since=_SINCE, until=_UNTIL)

    assert [d.finding_set_id for d in row.delivered] == ["fins_1", "fins_2"]


def test_a_run_whose_chunk_is_ephemeral_is_absent_from_the_list() -> None:
    identity = _identity("ch_gone")
    repo = _FakeRepo(records=[RunRecord(identity=identity, delivered=[])])
    service = _service(repo, _FakeChunkRecords(chunks={}), _FakeChunkFacts(facts={}))

    assert service.list_runs(since=_SINCE, until=_UNTIL) == []


# --------------------------------------------------------------------------- #
# run_delta


def test_run_delta_is_none_for_a_chunk_with_no_run_identity() -> None:
    service = _service(_FakeRepo())

    assert service.run_delta(_chunk("ch_ghost")) is None


def test_run_delta_splits_add_observed_and_gone_into_three_groups() -> None:
    artifact = _delta_artifact(
        [
            {"op": "add", "class": "stale-docstring", "locus": "a.py:1", "summary": "s", "introduced": None},
            {"op": "observed", "id": "fin_seen"},
            {"op": "gone", "id": "fin_missing", "note": "not found this pass"},
        ]
    )
    raw = DeliveredSetRaw(
        finding_set_id="fins_1",
        revisions={"blizzard": "aaa"},
        measurement=None,
        artifact_data=artifact,
        add_finding_ids=["fin_new"],
    )
    repo = _FakeRepo(identities={"ch_1": _identity("ch_1")}, delivered={"ch_1": [raw]})
    chunk_records = _FakeChunkRecords(chunks={"ch_1": _chunk("ch_1")})
    chunk_facts = _FakeChunkFacts(facts={"ch_1": ChunkFacts(minted=True, promoted=True)})

    delta = _service(repo, chunk_records, chunk_facts).run_delta(_chunk("ch_1"))

    assert delta is not None
    (set_delta,) = delta.sets
    assert [a.finding_id for a in set_delta.added] == ["fin_new"]
    assert set_delta.added[0].class_ == "stale-docstring"
    assert set_delta.observed == ["fin_seen"]
    assert [(g.finding_id, g.note) for g in set_delta.gone] == [("fin_missing", "not found this pass")]


def test_an_add_op_with_no_matching_fact_degrades_to_an_unmatched_finding_id() -> None:
    """A set delivered before `finding_facts.finding_set_id` existed (Phase 1) carries
    no add ids at all — the add still renders from the artifact, linked to nothing."""
    artifact = _delta_artifact(
        [{"op": "add", "class": "stale-docstring", "locus": "a.py:1", "summary": "s", "introduced": None}]
    )
    raw = DeliveredSetRaw(
        finding_set_id="fins_old", revisions={}, measurement=None, artifact_data=artifact, add_finding_ids=[]
    )
    repo = _FakeRepo(identities={"ch_1": _identity("ch_1")}, delivered={"ch_1": [raw]})
    chunk_records = _FakeChunkRecords(chunks={"ch_1": _chunk("ch_1")})
    chunk_facts = _FakeChunkFacts(facts={"ch_1": ChunkFacts(minted=True, promoted=True)})

    delta = _service(repo, chunk_records, chunk_facts).run_delta(_chunk("ch_1"))

    assert delta is not None
    (set_delta,) = delta.sets
    assert set_delta.added[0].finding_id is None


def test_add_ids_are_matched_to_add_ops_positionally() -> None:
    artifact = _delta_artifact(
        [
            {"op": "add", "class": "a", "locus": "a.py:1", "summary": "first", "introduced": None},
            {"op": "observed", "id": "fin_untouched"},
            {"op": "add", "class": "b", "locus": "b.py:2", "summary": "second", "introduced": None},
        ]
    )
    raw = DeliveredSetRaw(
        finding_set_id="fins_1",
        revisions={},
        measurement=None,
        artifact_data=artifact,
        add_finding_ids=["fin_first", "fin_second"],
    )
    repo = _FakeRepo(identities={"ch_1": _identity("ch_1")}, delivered={"ch_1": [raw]})
    chunk_records = _FakeChunkRecords(chunks={"ch_1": _chunk("ch_1")})
    chunk_facts = _FakeChunkFacts(facts={"ch_1": ChunkFacts(minted=True, promoted=True)})

    delta = _service(repo, chunk_records, chunk_facts).run_delta(_chunk("ch_1"))

    assert delta is not None
    (set_delta,) = delta.sets
    assert [a.finding_id for a in set_delta.added] == ["fin_first", "fin_second"]


def test_a_finding_never_named_in_the_artifact_appears_in_no_group() -> None:
    artifact = _delta_artifact([{"op": "observed", "id": "fin_seen"}])
    raw = DeliveredSetRaw(
        finding_set_id="fins_1", revisions={}, measurement=None, artifact_data=artifact, add_finding_ids=[]
    )
    repo = _FakeRepo(identities={"ch_1": _identity("ch_1")}, delivered={"ch_1": [raw]})
    chunk_records = _FakeChunkRecords(chunks={"ch_1": _chunk("ch_1")})
    chunk_facts = _FakeChunkFacts(facts={"ch_1": ChunkFacts(minted=True, promoted=True)})

    delta = _service(repo, chunk_records, chunk_facts).run_delta(_chunk("ch_1"))

    assert delta is not None
    (set_delta,) = delta.sets
    assert set_delta.added == []
    assert set_delta.observed == ["fin_seen"]
    assert set_delta.gone == []


def test_run_delta_keeps_several_sets_from_one_run_separately_grouped() -> None:
    first = _delta_artifact([{"op": "add", "class": "a", "locus": "a.py:1", "summary": "s1", "introduced": None}])
    second = _delta_artifact([{"op": "add", "class": "b", "locus": "b.py:2", "summary": "s2", "introduced": None}])
    raws = [
        DeliveredSetRaw(
            finding_set_id="fins_1", revisions={}, measurement=None, artifact_data=first, add_finding_ids=["fin_1"]
        ),
        DeliveredSetRaw(
            finding_set_id="fins_2", revisions={}, measurement=None, artifact_data=second, add_finding_ids=["fin_2"]
        ),
    ]
    repo = _FakeRepo(identities={"ch_1": _identity("ch_1")}, delivered={"ch_1": raws})
    chunk_records = _FakeChunkRecords(chunks={"ch_1": _chunk("ch_1")})
    chunk_facts = _FakeChunkFacts(facts={"ch_1": ChunkFacts(minted=True, promoted=True)})

    delta = _service(repo, chunk_records, chunk_facts).run_delta(_chunk("ch_1"))

    assert delta is not None
    assert [s.finding_set_id for s in delta.sets] == ["fins_1", "fins_2"]
    assert [s.added[0].finding_id for s in delta.sets] == ["fin_1", "fin_2"]
