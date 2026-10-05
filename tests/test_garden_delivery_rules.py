"""Garden-delivery rules (unit tier), pinned by value with no repository and no clock:
which named artifacts a delivery reads, the non-blank text a routine-run proposal must
carry, the plan a passing delivery mints, and the replay verb by marker state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import FINDING_PREFIX, FINDING_SET_PREFIX, GARDEN_PROPOSAL_PREFIX, Id
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import Chunk, WorkRef
from blizzard.hub.domain.garden.delivery.materialize import (
    DeliveryOutcome,
    NewFindingFact,
    build_delivery_plan,
    delivery_replay_outcome,
)
from blizzard.hub.domain.garden.delivery.validation import (
    GardenDeliveryRejected,
    SelectedArtifacts,
    ValidatedDelivery,
    check_proposal,
    select_delta_artifacts,
    select_proposal_artifacts,
)
from blizzard.hub.domain.garden.formats import DeliveredDelta, ProposalCandidate
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.graph.model import Node
from tests.garden_artifacts import candidate, delivered_delta

pytestmark = pytest.mark.unit

_AT = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
_RUN = RunContext(routine_name="nightly", scope_slug="blizzard", mode="full")
_CHUNK = Chunk(chunk_id="ch_1", graph_id="gr_1", work_refs=[WorkRef(source="default", ref="1")], minted_at=_AT)
_NODE = Node(
    node_id="nd_1",
    graph_id="gr_1",
    name="deliver",
    executor=Executor.HUB,
    prompt=None,
    checks=[],
    produces=[],
    session=SessionMode.FRESH,
    judged_by=JudgedBy.WORKER,
    retries_max=None,
    retries_exhausted=None,
)


def _artifact(name: str, data: str) -> StoredArtifact:
    return StoredArtifact(
        kind=ArtifactKind.ASSET,
        name=name,
        data=data,
        repo=None,
        forge=None,
        artifact_id=f"art_{name}",
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="survey",
        epoch=1,
    )


def _candidate(**overrides: object) -> ProposalCandidate:
    payload: dict[str, object] = {"ref": "p1", "class": "c", "title": "t", "body": "b", "findings": []}
    payload.update(overrides)
    return candidate(payload)


def _delta(findings: Sequence[Mapping[str, object]]) -> DeliveredDelta:
    return delivered_delta({"scope": "blizzard", "revisions": {"blizzard": "abc"}, "findings": findings})


# -- which artifacts a delivery reads ------------------------------------------------


def test_every_named_delta_resolving_is_selected_in_named_order() -> None:
    latest = {"b": _artifact("b", "B"), "a": _artifact("a", "A")}

    assert select_delta_artifacts(["a", "b"], latest) == SelectedArtifacts(
        contents={"a": "A", "b": "B"}, artifact_ids={"a": "art_a", "b": "art_b"}
    )


def test_one_missing_delta_refuses_naming_it() -> None:
    with pytest.raises(GardenDeliveryRejected, match="missing: ghost"):
        select_delta_artifacts(["a", "ghost"], {"a": _artifact("a", "A")})


def test_naming_no_delta_refuses() -> None:
    with pytest.raises(GardenDeliveryRejected, match="<none named>"):
        select_delta_artifacts([], {})


def test_a_missing_proposals_artifact_is_skipped() -> None:
    latest = {"docket": _artifact("docket", "[]")}

    assert select_proposal_artifacts(["docket", "ghost"], latest) == SelectedArtifacts(
        contents={"docket": "[]"}, artifact_ids={"docket": "art_docket"}
    )


# -- a routine-run proposal's text ---------------------------------------------------


@pytest.mark.parametrize("field", ["title", "class", "body"])
def test_a_blank_proposal_field_refuses(field: str) -> None:
    with pytest.raises(GardenDeliveryRejected) as exc:
        check_proposal(_candidate(**{field: " \t"}), run=_RUN, live_findings={})
    assert str(exc.value) == f"proposal 'p1' has a blank {field}"


def test_a_blank_title_is_refused_before_a_bad_citation() -> None:
    with pytest.raises(GardenDeliveryRejected, match="blank title"):
        check_proposal(_candidate(title="", findings=["unknown-ref"]), run=_RUN, live_findings={})


def test_a_proposal_with_text_and_no_citations_passes() -> None:
    check_proposal(_candidate(), run=_RUN, live_findings={})


# -- the plan a passing delivery mints -----------------------------------------------


def test_the_plan_carries_the_node_step_and_the_given_instant() -> None:
    plan = build_delivery_plan(
        ValidatedDelivery(run=_RUN, deltas=[_delta([])], proposals=[]),
        chunk=_CHUNK,
        node=_NODE,
        epoch=3,
        delta_artifact_ids=["art_d"],
        at=_AT,
    )

    assert (plan.chunk_id, plan.node_id, plan.node_name, plan.epoch, plan.at, plan.run) == (
        "ch_1",
        "nd_1",
        "deliver",
        3,
        _AT,
        _RUN,
    )
    assert plan.proposals == []


def test_an_empty_delta_still_mints_one_finding_set_and_nothing_else() -> None:
    plan = build_delivery_plan(
        ValidatedDelivery(run=_RUN, deltas=[_delta([])], proposals=[]),
        chunk=_CHUNK,
        node=_NODE,
        epoch=1,
        delta_artifact_ids=["art_d"],
        at=_AT,
    )

    [delta] = plan.deltas
    assert delta.finding_set.finding_set_id.startswith(f"{FINDING_SET_PREFIX}_")
    parsed = Id.parse(delta.finding_set.finding_set_id)
    assert parsed is not None and parsed.minted_at == _AT
    assert (delta.finding_set.artifact_id, delta.finding_set.scope_slug, delta.finding_set.revisions) == (
        "art_d",
        "blizzard",
        {"blizzard": "abc"},
    )
    assert (delta.new_findings, delta.facts) == ([], [])


def test_each_op_maps_to_its_fact_and_a_gone_on_a_delivered_finding_resolves() -> None:
    delivered, gone, observed = (Id.mint_at(FINDING_PREFIX, _AT).value for _ in range(3))
    validated = ValidatedDelivery(
        run=_RUN,
        deltas=[
            _delta(
                [
                    {"op": "add", "class": "c", "locus": "a.py:1", "summary": "s", "ref": "r1"},
                    {"op": "observed", "id": observed},
                    {"op": "gone", "id": gone, "note": "fixed"},
                    {"op": "gone", "id": delivered, "note": "landed"},
                ]
            )
        ],
        proposals=[],
        gone_settlements={delivered: ("resolved", "operator:pat")},
    )

    [delta] = build_delivery_plan(
        validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=["art_d"], at=_AT
    ).deltas

    [new] = delta.new_findings
    set_id = delta.finding_set.finding_set_id
    assert (new.routine_name, new.scope_slug, new.class_, new.locus, new.summary) == (
        "nightly",
        "blizzard",
        "c",
        "a.py:1",
        "s",
    )
    assert delta.facts == [
        NewFindingFact(finding_id=new.finding_id, kind="add", finding_set_id=set_id, ref="r1"),
        NewFindingFact(finding_id=observed, kind="observed", finding_set_id=set_id),
        NewFindingFact(finding_id=gone, kind="gone", finding_set_id=set_id, note="fixed"),
        NewFindingFact(
            finding_id=delivered, kind="resolved", finding_set_id=set_id, note="landed", actor="operator:pat"
        ),
    ]


def test_a_proposal_citing_a_ref_carries_the_id_its_add_minted() -> None:
    live = Id.mint_at(FINDING_PREFIX, _AT).value
    validated = ValidatedDelivery(
        run=_RUN,
        deltas=[_delta([{"op": "add", "class": "c", "locus": "a.py:1", "summary": "s", "ref": "r1"}])],
        proposals=[_candidate(findings=["r1", live])],
    )

    plan = build_delivery_plan(
        validated,
        chunk=_CHUNK,
        node=_NODE,
        epoch=1,
        delta_artifact_ids=["art_d"],
        proposal_artifact_ids=["art_docket"],
        at=_AT,
    )

    [proposal] = plan.proposals
    minted = plan.deltas[0].new_findings[0].finding_id
    assert proposal.proposal_id.startswith(f"{GARDEN_PROPOSAL_PREFIX}_")
    assert (proposal.routine_name, proposal.class_, proposal.title, proposal.body) == ("nightly", "c", "t", "b")
    assert (proposal.source_artifact_id, proposal.ref, proposal.finding_ids) == ("art_docket", "p1", [minted, live])


def test_an_introduced_instant_is_looked_up_only_for_a_single_repo_delta() -> None:
    introduced = datetime(2026, 7, 1, tzinfo=UTC)
    add = {"op": "add", "class": "c", "locus": "a.py:1", "summary": "s", "introduced": "abc1234"}
    single = _delta([add])
    multi = delivered_delta({"scope": "blizzard", "revisions": {"blizzard": "abc", "other": "def"}, "findings": [add]})
    lookups: dict[tuple[str, str], datetime | None] = {
        ("blizzard", "abc1234"): introduced,
        ("other", "abc1234"): introduced,
    }

    plan = build_delivery_plan(
        ValidatedDelivery(run=_RUN, deltas=[single, multi], proposals=[], introduced_at=lookups),
        chunk=_CHUNK,
        node=_NODE,
        epoch=1,
        delta_artifact_ids=["art_1", "art_2"],
        at=_AT,
    )

    assert [d.new_findings[0].introduced_at for d in plan.deltas] == [introduced, None]


# -- the replay verb -------------------------------------------------------------------


def test_a_recorded_node_step_replays_as_already_recorded() -> None:
    assert delivery_replay_outcome(recorded=True) is DeliveryOutcome.ALREADY_RECORDED


def test_an_unrecorded_node_step_proceeds_to_validation() -> None:
    assert delivery_replay_outcome(recorded=False) is None
