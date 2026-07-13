"""Node-envelope assembly (unit tier) — latest-by-epoch, elicitation tail, addendum."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.envelope import build_node_envelope, latest_artifacts_by_name
from blizzard.hub.domain.graph import Choice, Executor, JudgedBy, Node, SessionMode
from blizzard.hub.domain.work import Chunk, PmPointer

pytestmark = pytest.mark.unit


def _row(name: str, epoch: int, *, node_name: str = "build") -> ArtifactRow:
    return ArtifactRow(
        kind=ArtifactKind.ASSET,
        name=name,
        data=f"v{epoch}",
        repo=None,
        artifact_id=f"art_{name}{epoch}",
        chunk_id="ch_1",
        node_id="nd_build",
        node_name=node_name,
        epoch=epoch,
    )


def _node() -> Node:
    return Node(
        node_id="nd_build",
        graph_id="gr_1",
        name="build",
        executor=Executor.RUNNER,
        prompt="do the work",
        checks=["mise run test"],
        produces=[],
        session=SessionMode.RESUME,
        judged_by=JudgedBy.WORKER,
        retries_max=2,
        retries_exhausted="escalate",
        mode=None,
        judgement_prompt="render your verdict",
        choices=[Choice("cho_1", "pass", "it works"), Choice("cho_2", "fail", "it does not")],
    )


def _chunk() -> Chunk:
    return Chunk(
        chunk_id="ch_1",
        graph_id="gr_1",
        pm_pointers=[PmPointer(provider="github", url="http://f/issues/1")],
        minted_at=datetime(2026, 7, 13, tzinfo=UTC),
    )


def test_latest_artifacts_by_name_keeps_the_highest_epoch() -> None:
    rows = [_row("findings", 1), _row("findings", 3), _row("findings", 2), _row("other", 1)]
    latest = {(r.node_name, r.name): r.epoch for r in latest_artifacts_by_name(rows)}
    assert latest == {("build", "findings"): 3, ("build", "other"): 1}


def test_envelope_carries_config_pointers_and_elicitation_tail() -> None:
    env = build_node_envelope(chunk=_chunk(), node=_node(), artifacts=[_row("f", 1)], epoch=1)
    assert env.epoch == 1
    assert env.node.node_name == "build"
    assert env.node.checks == ["mise run test"]
    assert {c.name for c in env.node.choices} == {"pass", "fail"}
    assert env.prompt == "do the work"
    judgement = env.judgement_prompt
    assert judgement is not None
    assert "render your verdict" in judgement
    assert "<Choice>{name}</Choice>" in judgement
    assert "`pass`: it works" in judgement
    assert env.pm_pointers == [{"provider": "github", "url": "http://f/issues/1"}]
    assert [a.name for a in env.artifacts] == ["f"]


def test_arrival_addendum_appends_to_the_pre_prompt() -> None:
    env = build_node_envelope(
        chunk=_chunk(), node=_node(), artifacts=[], epoch=2, arrival_addendum="the review found X"
    )
    assert env.prompt == "do the work\n\nthe review found X"


def test_hub_node_has_no_judgement_prompt() -> None:
    hub_node = Node(
        node_id="nd_deliver",
        graph_id="gr_1",
        name="deliver",
        executor=Executor.HUB,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.RESUME,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        mode="merge-to-main",
        judgement_prompt=None,
        choices=[],
    )
    env = build_node_envelope(chunk=_chunk(), node=hub_node, artifacts=[], epoch=1)
    assert env.judgement_prompt is None
    assert env.node.choices == []
