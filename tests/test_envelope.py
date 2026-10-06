"""Node-envelope assembly (unit tier) — latest-by-epoch, elicitation tail, addendum."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.api.chunk_views import ChunkView
from blizzard.hub.api.node_steps import node_envelope
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, RestartFact, TransitionFact, WorkRef
from blizzard.hub.domain.execution.envelope import Arrival, Envelope, LatestArtifacts
from blizzard.hub.domain.graph.model import (
    Choice,
    Edge,
    Graph,
    GraphArtifact,
    Node,
    ProducesSpec,
    RotatePolicy,
    SessionDecl,
)
from blizzard.wire.envelope import EnvelopeArtifact

pytestmark = pytest.mark.unit


def _row(name: str, epoch: int, *, node_name: str = "build", node_id: str = "nd_build") -> StoredArtifact:
    return StoredArtifact(
        kind=ArtifactKind.ASSET,
        name=name,
        data=f"v{epoch}",
        repo=None,
        forge=None,
        artifact_id=f"art_{name}{epoch}",
        chunk_id="ch_1",
        node_id=node_id,
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
        judgement_prompt="render your verdict",
        choices=[Choice("cho_1", "pass", "it works"), Choice("cho_2", "fail", "it does not")],
    )


def _graph(*sessions: SessionDecl, artifacts: list[GraphArtifact] | None = None) -> Graph:
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
        artifacts=artifacts or [],
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


def test_latest_artifacts_series_continues_across_node_ids_for_one_node_name() -> None:
    # A republication or migration re-mints the node under a new node id but the same name, so
    # the series keys on node name: a key on node id would resolve one row per id instead.
    rows = [
        _row("findings", 1, node_id="nd_build_a"),
        _row("findings", 3, node_id="nd_build_c"),
        _row("findings", 2, node_id="nd_build_b"),
    ]

    latest = LatestArtifacts.of(rows).rows
    assert [(r.node_id, r.epoch) for r in latest] == [("nd_build_c", 3)]

    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=rows, epoch=4))
    assert [(a.node_name, a.name, a.epoch, a.content) for a in env.artifacts] == [("build", "findings", 3, "v3")]

    # The superseded rows are history, not dropped: the chunk read still lists every one.
    view = ChunkView(
        services=SimpleNamespace(work_sources={}),  # type: ignore[arg-type]
        chunk=_chunk(),
        facts=ChunkFacts(minted=True),
        names=None,  # type: ignore[arg-type]
    )
    assert [(a.key, a.node_id) for a in view._artifacts(rows)] == [
        ("build.findings.1", "nd_build_a"),
        ("build.findings.2", "nd_build_b"),
        ("build.findings.3", "nd_build_c"),
    ]


def test_envelope_carries_authored_judgement_prose_and_choice_set() -> None:
    # The envelope carries the judgement prompt verbatim and the choice set — never a
    # baked-in elicitation tail; that's the runner's to render.
    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[_row("f", 1)], epoch=1))
    assert env.epoch == 1
    assert env.node.node_name == "build"
    assert env.node.checks == ["mise run test"]
    assert {c.name for c in env.node.choices} == {"pass", "fail"}
    assert env.prompt == "do the work"
    assert env.judgement_prompt == "render your verdict"
    assert "<Choice>" not in (env.judgement_prompt or "")  # the tail is the runner's to render
    assert env.work_refs == [{"source": "default", "ref": "1"}]
    assert [a.name for a in env.artifacts] == ["f"]


def test_envelope_carries_graph_artifacts_in_authored_order() -> None:
    # A non-alphabetical name set: an `order_by(name)` regression would still pass an
    # alphabetically-sorted fixture, so this pins the ordinal, not the name.
    artifacts = [
        GraphArtifact(name="zebra", content="z content", ordinal=0),
        GraphArtifact(name="apple", content="a content", ordinal=1),
    ]
    env = node_envelope(
        Envelope(chunk=_chunk(), graph=_graph(artifacts=artifacts), node=_node(), artifacts=[], epoch=1)
    )
    assert [(a.name, a.content) for a in env.graph_artifacts] == [("zebra", "z content"), ("apple", "a content")]
    assert all(a.kind is ArtifactKind.ASSET for a in env.graph_artifacts)


def test_envelope_graph_artifacts_empty_for_a_graph_declaring_none() -> None:
    # `node_envelope` sets the field, so a vanished population site reds here too — an `== []` alone
    # passes on the model's own default whether `node_envelope` populated it or not.
    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=1))
    assert env.graph_artifacts == []
    assert "graph_artifacts" in env.model_fields_set


def test_envelope_artifact_still_requires_node_name_and_epoch() -> None:
    # `EnvelopeArtifact` (node-scoped) is untouched by the new graph-scoped wire model. One
    # field omitted per case: omitting both still raises with either one relaxed to optional.
    with pytest.raises(ValidationError):
        EnvelopeArtifact(name="f", kind=ArtifactKind.ASSET, epoch=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        EnvelopeArtifact(name="f", kind=ArtifactKind.ASSET, node_name="build")  # type: ignore[call-arg]


def test_envelope_carries_session_source() -> None:
    # Mirrors target_graph beside the raw `to`: session_source is derived once at
    # parse and carried verbatim onto the envelope's NodeConfig.
    node = replace(_node(), session_source="build")
    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=node, artifacts=[], epoch=1))
    assert env.node.session == SessionMode.RESUME
    assert env.node.session_source == "build"


def test_envelope_session_source_defaults_to_none() -> None:
    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=1))
    assert env.node.session_source is None


def test_arrival_addendum_appends_to_the_pre_prompt() -> None:
    env = node_envelope(
        Envelope(
            chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=2, arrival_addendum="the review found X"
        )
    )
    assert env.prompt == "do the work\n\nthe review found X"


def test_required_artifacts_table_renders_name_and_kind_and_is_harness_inert() -> None:
    """The procedurally-generated required-artifacts table: one
    `#`-prefixed line per `produces:` entry, naming its kind and the fleet-protocol
    declaration verb — inert to the mock harness's prompt-is-program `exec`."""
    node = replace(
        _node(),
        produces=[
            ProducesSpec(name="review-findings", kind=ArtifactKind.ASSET),
            ProducesSpec(name="commit", kind=ArtifactKind.GIT_COMMIT),
        ],
    )
    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=node, artifacts=[], epoch=1))

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
    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=1))
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
        judgement_prompt=None,
        choices=[],
    )
    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=hub_node, artifacts=[], epoch=1))
    assert env.judgement_prompt is None
    assert env.node.choices == []


def test_envelope_carries_checks_gating_fields() -> None:
    """``checks_cwd``/``checks_timeout`` and a choice's ``requires_checks``
    ride the node envelope so the runner can execute + gate on them."""
    node = replace(
        _node(),
        checks_cwd="blizzard",
        checks_timeout=300,
        choices=[Choice("cho_1", "pass", "it works", requires_checks=True), Choice("cho_2", "fail", "it does not")],
    )
    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=node, artifacts=[], epoch=1))
    assert env.node.checks_cwd == "blizzard"
    assert env.node.checks_timeout == 300
    by_name = {c.name: c for c in env.node.choices}
    assert by_name["pass"].requires_checks is True
    assert by_name["fail"].requires_checks is False


def test_envelope_checks_gating_fields_default_off() -> None:
    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=1))
    assert env.node.checks_cwd is None
    assert env.node.checks_timeout is None
    assert all(not c.requires_checks for c in env.node.choices)


# --- The effective session declaration — precedence resolved hub-side ---


def _chunk_with_defaults(model: list[str], effort: str | None, harnesses: list[str] | None = None) -> Chunk:
    return replace(_chunk(), default_model=model, default_effort=effort, default_harnesses=harnesses or [])


def test_a_declaration_only_node_carries_the_declaration() -> None:
    node = replace(_node(), session=SessionMode.FRESH, session_source="code")
    decl = SessionDecl(name="code", model=["blizzard:basic"], effort="medium", rotate=RotatePolicy(max_invocations=30))

    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(decl), node=node, artifacts=[], epoch=1))

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

    env = node_envelope(Envelope(chunk=chunk, graph=_graph(), node=_node(), artifacts=[], epoch=1))

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

    env = node_envelope(Envelope(chunk=chunk, graph=_graph(decl), node=node, artifacts=[], epoch=1))

    assert env.node.session_model == ["blizzard:basic"]  # the declaration wins
    assert env.node.session_effort == "high"  # the chunk default fills the gap


def test_a_declaration_with_neither_field_falls_all_the_way_to_the_chunk_default() -> None:
    node = replace(_node(), session=SessionMode.FRESH, session_source="gate")
    chunk = _chunk_with_defaults(["blizzard:advanced"], "high")

    env = node_envelope(Envelope(chunk=chunk, graph=_graph(SessionDecl(name="gate")), node=node, artifacts=[], epoch=1))

    assert env.node.session_name == "gate"  # still a pool member
    assert env.node.session_model == ["blizzard:advanced"]
    assert env.node.session_effort == "high"


def test_neither_a_declaration_nor_a_chunk_default_expresses_no_preference() -> None:
    # No declaration and no chunk default: the runner's own default applies.
    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=1))

    assert env.node.session_name is None
    assert env.node.session_model == []
    assert env.node.session_effort is None
    assert env.node.session_rotate is None


def test_a_node_name_session_target_carries_no_pool_but_still_the_chunk_default() -> None:
    # `resume:<node>` resolves against node names, not the `sessions:` map,
    # so it names no pool.
    node = replace(_node(), session_source="build")
    chunk = _chunk_with_defaults(["blizzard:advanced"], "high")

    env = node_envelope(Envelope(chunk=chunk, graph=_graph(SessionDecl(name="code")), node=node, artifacts=[], epoch=1))

    assert env.node.session_source == "build"
    assert env.node.session_name is None
    assert env.node.session_model == ["blizzard:advanced"]


# --- The effective harness set — the `model` precedence rule, cloned ---


def test_a_declared_harness_set_replaces_the_chunk_default_as_a_whole_list() -> None:
    # Whole-list replacement, not merged: a declared `harnesses` wins outright over the
    # chunk default, unlike `model`/`effort`'s field-by-field merge.
    node = replace(_node(), session_source="code")
    decl = SessionDecl(name="code", harnesses=["claude"])
    chunk = _chunk_with_defaults([], None, harnesses=["claude", "codex"])

    env = node_envelope(Envelope(chunk=chunk, graph=_graph(decl), node=node, artifacts=[], epoch=1))

    assert env.node.session_harnesses == ["claude"]


def test_a_declaration_without_harnesses_inherits_the_chunk_default() -> None:
    node = replace(_node(), session_source="code")
    decl = SessionDecl(name="code", model=["blizzard:basic"])
    chunk = _chunk_with_defaults([], None, harnesses=["claude", "codex"])

    env = node_envelope(Envelope(chunk=chunk, graph=_graph(decl), node=node, artifacts=[], epoch=1))

    assert env.node.session_harnesses == ["claude", "codex"]


def test_a_bare_fresh_node_takes_the_chunk_default_harnesses() -> None:
    chunk = _chunk_with_defaults([], None, harnesses=["claude", "codex"])

    env = node_envelope(Envelope(chunk=chunk, graph=_graph(), node=_node(), artifacts=[], epoch=1))

    assert env.node.session_harnesses == ["claude", "codex"]


def test_a_bare_resume_node_takes_the_chunk_default_harnesses() -> None:
    node = replace(_node(), session=SessionMode.RESUME)
    chunk = _chunk_with_defaults([], None, harnesses=["claude", "codex"])

    env = node_envelope(Envelope(chunk=chunk, graph=_graph(), node=node, artifacts=[], epoch=1))

    assert env.node.session_harnesses == ["claude", "codex"]


def test_a_node_name_session_target_takes_the_chunk_default_harnesses() -> None:
    # `resume:<node>` names no pool, so it falls straight to the chunk
    # default, same as the bare forms.
    node = replace(_node(), session_source="build")
    chunk = _chunk_with_defaults([], None, harnesses=["claude", "codex"])

    env = node_envelope(Envelope(chunk=chunk, graph=_graph(SessionDecl(name="code")), node=node, artifacts=[], epoch=1))

    assert env.node.session_harnesses == ["claude", "codex"]


def test_neither_a_declaration_nor_a_chunk_default_yields_an_empty_effective_harness_set() -> None:
    env = node_envelope(Envelope(chunk=_chunk(), graph=_graph(), node=_node(), artifacts=[], epoch=1))

    assert env.node.session_harnesses == []


def test_declared_graph_artifacts_are_never_spliced_into_the_prompt() -> None:
    artifacts = [GraphArtifact(name="policy", content="POLICY-BODY-MARKER", ordinal=0)]
    env = node_envelope(
        Envelope(chunk=_chunk(), graph=_graph(artifacts=artifacts), node=_node(), artifacts=[], epoch=1)
    )
    assert env.prompt is not None
    assert "POLICY-BODY-MARKER" not in env.prompt
    assert "policy" not in env.prompt


_T0 = datetime(2026, 7, 13, 1, tzinfo=UTC)
_T1 = datetime(2026, 7, 13, 2, tzinfo=UTC)


def _addended_graph() -> Graph:
    review = replace(_node(), node_id="nd_review", name="review", choices=[Choice("cho_fail", "fail", "no")])
    edge = Edge(from_node_id="nd_review", choice_id="cho_fail", to_node_name="build", prompt_addendum="RE-ENTER")
    return replace(_graph(), nodes=[_node(), review], edges=[edge])


def _transition_into_build() -> TransitionFact:
    return TransitionFact(
        to_node_id="nd_build",
        to_node_executor=Executor.RUNNER,
        epoch=2,
        recorded_at=_T0,
        from_node_id="nd_review",
        choice_name="fail",
        graph_id="gr_1",
    )


def test_arrival_of_a_transition_into_the_current_node_is_the_edges_addendum() -> None:
    facts = ChunkFacts(minted=True, transitions=[_transition_into_build()])
    assert Arrival.of_facts(_addended_graph(), facts).addendum == "RE-ENTER"


def test_arrival_is_nothing_once_a_restart_supersedes_the_transition() -> None:
    restart = RestartFact(to_node_id="nd_build", from_node_id="nd_build", graph_id="gr_1", epoch=3, recorded_at=_T1)
    facts = ChunkFacts(minted=True, transitions=[_transition_into_build()], restarts=[restart])
    assert Arrival.of_facts(_addended_graph(), facts).addendum is None


def test_arrival_is_nothing_for_a_chunk_that_has_not_moved() -> None:
    assert Arrival.of_facts(_addended_graph(), None).addendum is None
    assert Arrival.of_facts(_addended_graph(), ChunkFacts(minted=True)).addendum is None


def test_envelope_carries_the_graph_name_and_labels_each_work_ref_its_source_renders() -> None:
    chunk = replace(
        _chunk(),
        work_refs=[WorkRef(source="gh", ref="42"), WorkRef(source="hub", ref="7"), WorkRef(source="gone", ref="1")],
    )
    labels = {"gh": "acme#42", "hub": "hub:7"}

    env = node_envelope(
        Envelope(
            chunk=chunk,
            graph=_graph(),
            node=_node(),
            artifacts=[],
            epoch=1,
            label=lambda ref: labels.get(ref.source),
        )
    )

    assert env.graph_name == "t"
    assert env.work_refs == [
        {"source": "gh", "ref": "42", "label": "acme#42"},
        {"source": "hub", "ref": "7", "label": "hub:7"},
        {"source": "gone", "ref": "1"},
    ]
