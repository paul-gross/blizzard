"""Node-envelope assembly (unit tier) — latest-by-epoch, elicitation tail, addendum."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.envelope import Envelope, LatestArtifacts
from blizzard.hub.domain.graph import (
    Choice,
    Executor,
    Graph,
    JudgedBy,
    Node,
    ProducesSpec,
    RotatePolicy,
    SessionDecl,
    SessionMode,
)
from blizzard.hub.domain.work import Chunk, WorkRef

pytestmark = pytest.mark.unit


def _row(name: str, epoch: int, *, node_name: str = "build") -> ArtifactRow:
    return ArtifactRow(
        kind=ArtifactKind.ASSET,
        name=name,
        data=f"v{epoch}",
        repo=None,
        forge=None,
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


def _graph(*sessions: SessionDecl) -> Graph:
    """The node's own graph — required since #144, since the node's effective session
    declaration is resolved against its ``sessions:`` map."""
    return Graph(
        graph_id="gr_1",
        name="t",
        entry_node_id="nd_build",
        nodes=[_node()],
        edges=[],
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
        sessions=list(sessions),
    )


def _chunk() -> Chunk:
    return Chunk(
        chunk_id="ch_1",
        graph_id="gr_1",
        work_refs=[WorkRef(source="default", ref="1")],
        minted_at=datetime(2026, 7, 13, tzinfo=UTC),
    )


def test_latest_artifacts_keeps_the_highest_epoch() -> None:
    rows = [_row("findings", 1), _row("findings", 3), _row("findings", 2), _row("other", 1)]
    latest = {(r.node_name, r.name): r.epoch for r in LatestArtifacts.of(rows).rows}
    assert latest == {("build", "findings"): 3, ("build", "other"): 1}


def test_envelope_carries_authored_judgement_prose_and_choice_set() -> None:
    # The envelope carries the judgement prompt verbatim and the choice set — never a
    # baked-in elicitation tail; that's the runner's to render.
    env = Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[_row("f", 1)], epoch=1).wire
    assert env.epoch == 1
    assert env.node.node_name == "build"
    assert env.node.checks == ["mise run test"]
    assert {c.name for c in env.node.choices} == {"pass", "fail"}
    assert env.prompt == "do the work"
    assert env.judgement_prompt == "render your verdict"
    assert "<Choice>" not in (env.judgement_prompt or "")  # the tail is the runner's to render
    assert env.work_refs == [{"source": "default", "ref": "1"}]
    assert [a.name for a in env.artifacts] == ["f"]


def test_envelope_carries_session_source() -> None:
    # Mirrors target_graph beside the raw `to`: session_source is derived once at
    # parse and carried verbatim onto the envelope's NodeConfig (issue #115).
    node = replace(_node(), session_source="build")
    env = Envelope(chunk=_chunk(), graph=_graph(), node=node, artifacts=[], epoch=1).wire
    assert env.node.session == SessionMode.RESUME
    assert env.node.session_source == "build"


def test_envelope_session_source_defaults_to_none() -> None:
    env = Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=1).wire
    assert env.node.session_source is None


def test_arrival_addendum_appends_to_the_pre_prompt() -> None:
    env = Envelope(
        chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=2, arrival_addendum="the review found X"
    ).wire
    assert env.prompt == "do the work\n\nthe review found X"


def test_required_artifacts_table_renders_name_and_kind_and_is_harness_inert() -> None:
    """The procedurally-generated required-artifacts table (issue #143, Phase 5): one
    `#`-prefixed line per `produces:` entry, naming its kind and the fleet-protocol
    declaration verb — inert to the mock harness's prompt-is-program `exec`."""
    node = replace(
        _node(),
        produces=[
            ProducesSpec(name="review-findings", kind=ArtifactKind.ASSET),
            ProducesSpec(name="commit", kind=ArtifactKind.GIT_COMMIT),
        ],
    )
    env = Envelope(chunk=_chunk(), graph=_graph(), node=node, artifacts=[], epoch=1).wire

    assert env.prompt is not None
    assert env.prompt.startswith("do the work\n\n")
    table = env.prompt[len("do the work\n\n") :]
    # Every rendered line is a `#`-prefixed comment — a mock's `exec` of the prompt sees
    # only legal no-op comment lines, never bare prose.
    for line in table.splitlines():
        if line:
            assert line.startswith("#"), f"non-inert line in the required-artifacts table: {line!r}"
    assert "artifact create --name review-findings" in table
    assert "(asset)" in table
    assert "artifact commit --repo <repo> --branch <branch> --commit <sha>" in table
    assert "--forge defaults to this repo's own `origin`" in table
    assert "(git_commit)" in table


def test_required_artifacts_table_is_empty_when_node_produces_nothing() -> None:
    # Mirrors `_node()`'s own `produces=[]`; this test names the reason explicitly.
    env = Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=1).wire
    assert env.prompt == "do the work"


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
    env = Envelope(chunk=_chunk(), graph=_graph(), node=hub_node, artifacts=[], epoch=1).wire
    assert env.judgement_prompt is None
    assert env.node.choices == []


def test_envelope_carries_checks_gating_fields() -> None:
    """``checks_cwd``/``checks_timeout`` and a choice's ``requires_checks`` (issue #114)
    ride the node envelope so the runner can execute + gate on them."""
    node = replace(
        _node(),
        checks_cwd="blizzard",
        checks_timeout=300,
        choices=[Choice("cho_1", "pass", "it works", requires_checks=True), Choice("cho_2", "fail", "it does not")],
    )
    env = Envelope(chunk=_chunk(), graph=_graph(), node=node, artifacts=[], epoch=1).wire
    assert env.node.checks_cwd == "blizzard"
    assert env.node.checks_timeout == 300
    by_name = {c.name: c for c in env.node.choices}
    assert by_name["pass"].requires_checks is True
    assert by_name["fail"].requires_checks is False


def test_envelope_checks_gating_fields_default_off() -> None:
    env = Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=1).wire
    assert env.node.checks_cwd is None
    assert env.node.checks_timeout is None
    assert all(not c.requires_checks for c in env.node.choices)


# --- The effective session declaration (issue #144) — precedence resolved hub-side ---


def _chunk_with_defaults(model: list[str], effort: str | None) -> Chunk:
    return replace(_chunk(), default_model=model, default_effort=effort)


def test_a_declaration_only_node_carries_the_declaration() -> None:
    node = replace(_node(), session=SessionMode.FRESH, session_source="code")
    decl = SessionDecl(name="code", model=["blizzard:basic"], effort="medium", rotate=RotatePolicy(max_invocations=30))

    env = Envelope(chunk=_chunk(), graph=_graph(decl), node=node, artifacts=[], epoch=1).wire

    assert env.node.session_name == "code"
    assert env.node.session_model == ["blizzard:basic"]
    assert env.node.session_effort == "medium"
    assert env.node.session_rotate is not None
    assert env.node.session_rotate.max_invocations == 30
    assert env.node.session_rotate.max_context_tokens is None


def test_a_chunk_default_only_node_carries_the_chunk_default_and_no_pool() -> None:
    # A bare `resume`/`fresh` node references no declaration, so it belongs to no pool —
    # but the chunk's defaults still reach it: the precedence rule's intended reach.
    chunk = _chunk_with_defaults(["blizzard:advanced"], "high")

    env = Envelope(chunk=chunk, graph=_graph(), node=_node(), artifacts=[], epoch=1).wire

    assert env.node.session_name is None
    assert env.node.session_model == ["blizzard:advanced"]
    assert env.node.session_effort == "high"
    assert env.node.session_rotate is None


def test_a_declaration_outranks_the_chunk_default_field_by_field() -> None:
    # Merged per field, not whole-record: a declaration naming `model` but no `effort`
    # takes the chunk's effort rather than nothing.
    node = replace(_node(), session_source="code")
    decl = SessionDecl(name="code", model=["blizzard:basic"])
    chunk = _chunk_with_defaults(["blizzard:advanced"], "high")

    env = Envelope(chunk=chunk, graph=_graph(decl), node=node, artifacts=[], epoch=1).wire

    assert env.node.session_model == ["blizzard:basic"]  # the declaration wins
    assert env.node.session_effort == "high"  # the chunk default fills the gap


def test_a_declaration_with_neither_field_falls_all_the_way_to_the_chunk_default() -> None:
    node = replace(_node(), session=SessionMode.FRESH, session_source="gate")
    chunk = _chunk_with_defaults(["blizzard:advanced"], "high")

    env = Envelope(chunk=chunk, graph=_graph(SessionDecl(name="gate")), node=node, artifacts=[], epoch=1).wire

    assert env.node.session_name == "gate"  # still a pool member
    assert env.node.session_model == ["blizzard:advanced"]
    assert env.node.session_effort == "high"


def test_neither_a_declaration_nor_a_chunk_default_expresses_no_preference() -> None:
    # No declaration and no chunk default: the runner's own default applies.
    env = Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=1).wire

    assert env.node.session_name is None
    assert env.node.session_model == []
    assert env.node.session_effort is None
    assert env.node.session_rotate is None


def test_a_node_name_session_target_carries_no_pool_but_still_the_chunk_default() -> None:
    # `resume:<node>` (issue #115) resolves against node names, not the `sessions:` map,
    # so it names no pool.
    node = replace(_node(), session_source="build")
    chunk = _chunk_with_defaults(["blizzard:advanced"], "high")

    env = Envelope(chunk=chunk, graph=_graph(SessionDecl(name="code")), node=node, artifacts=[], epoch=1).wire

    assert env.node.session_source == "build"
    assert env.node.session_name is None
    assert env.node.session_model == ["blizzard:advanced"]
