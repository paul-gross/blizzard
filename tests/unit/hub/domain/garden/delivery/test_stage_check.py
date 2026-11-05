"""The stage-time check of a garden artifact (unit tier): which formats the garden-routine graph owes each
name, and that the refusal is the very message delivery raises for the same content."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.api.garden_formats import GardenFormats
from blizzard.hub.domain.garden.delivery.stage_check import GardenStageCheck
from blizzard.hub.domain.garden.delivery.validation import GardenDeliveryRejected
from blizzard.hub.domain.graph.actions import SHIPPED_CATALOGUE, ArtifactFormat
from blizzard.hub.domain.graph.model import Graph, Node
from blizzard.hub.graphs import PACKAGED

_FORMATS = GardenFormats()
_CHECK = GardenStageCheck(formats=_FORMATS, catalogue=SHIPPED_CATALOGUE)


def _graph() -> Graph:
    """The packaged garden-routine graph's nodes, each carrying the action and wiring the doc authors."""
    doc = PACKAGED.named("garden-routine").doc
    nodes = [
        Node(
            node_id=f"nd_{n.name}",
            graph_id="gr_garden",
            name=n.name,
            executor=n.executor,
            prompt=None,
            checks=[],
            produces=[],
            session=SessionMode.FRESH,
            judged_by=JudgedBy.WORKER,
            retries_max=None,
            retries_exhausted=None,
            action=n.action,
            action_args=n.action_args,
        )
        for n in doc.nodes
    ]
    assert any(n.executor is Executor.HUB for n in nodes)
    return Graph(
        graph_id="gr_garden",
        name="garden-routine",
        entry_node_id=nodes[0].node_id,
        nodes=nodes,
        edges=[],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_the_garden_routine_graph_owes_delta_and_docket_their_formats_and_survey_none() -> None:
    graph = _graph()
    assert graph.artifact_formats("delta", SHIPPED_CATALOGUE) == {ArtifactFormat.FINDING_DELTA}
    assert graph.artifact_formats("docket", SHIPPED_CATALOGUE) == {ArtifactFormat.GARDEN_PROPOSAL_CANDIDATES}
    assert graph.artifact_formats("survey", SHIPPED_CATALOGUE) == frozenset()


def _delivery_message(name: str, content: str, *, delta: bool) -> str:
    with pytest.raises(GardenDeliveryRejected) as raised:
        if delta:
            _FORMATS.finding_delta(name, content)
        else:
            _FORMATS.proposal_candidates(name, content)
    return str(raised.value)


def test_an_object_typed_measurement_is_refused_with_the_delivery_message() -> None:
    content = json.dumps({"scope": "s", "revisions": {}, "measurement": {"a": 1}, "findings": []})
    assert _CHECK.refusal(_graph(), "delta", content) == _delivery_message("delta", content, delta=True)


def test_non_json_docket_text_is_refused_with_the_delivery_message() -> None:
    content = "[{not json"
    refusal = _CHECK.refusal(_graph(), "docket", content)
    assert refusal == _delivery_message("docket", content, delta=False)
    assert refusal is not None and "garden-proposal-candidate shape" in refusal


def test_valid_content_and_an_unvalidated_name_pass() -> None:
    valid = json.dumps({"scope": "s", "revisions": {}, "measurement": None, "findings": []})
    assert _CHECK.refusal(_graph(), "delta", valid) is None
    assert _CHECK.refusal(_graph(), "docket", "[]") is None
    assert _CHECK.refusal(_graph(), "survey", "anything at all") is None
