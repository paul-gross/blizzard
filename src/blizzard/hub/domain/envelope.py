"""Node-envelope assembly — the pure builder behind every envelope, over already-loaded domain objects
(``bzh:domain-core``, ``bzh:domain-takes-objects``).

The **pre-prompt** is the node's base prompt, the inlined arrival addendum of the edge the chunk took
to reach the node, and a generated required-artifacts table (issue #143). The **judgement prompt** is
the node's authored prose only. Artifacts resolve **latest-by-epoch per ``{node_name}.{name}``**."""

from __future__ import annotations

from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.graph import Graph, Node
from blizzard.hub.domain.work import Chunk, TransitionFact
from blizzard.wire.envelope import EnvelopeArtifact, EnvelopeChoice, NodeConfig, NodeEnvelope, RotatePolicyView
from blizzard.wire.graph import ProducesEntry


def latest_artifacts_by_name(rows: list[ArtifactRow]) -> list[ArtifactRow]:
    """Resolve an artifact list to one row per ``{node_name}.{name}``, newest epoch wins."""
    latest: dict[tuple[str, str], ArtifactRow] = {}
    for row in rows:
        key = (row.node_name, row.name)
        current = latest.get(key)
        if current is None or row.epoch > current.epoch:
            latest[key] = row
    return list(latest.values())


def _to_envelope_artifact(row: ArtifactRow) -> EnvelopeArtifact:
    if row.kind is ArtifactKind.GIT_COMMIT:
        branch_name, _, commit_hash = row.data.partition(":")
        return EnvelopeArtifact(
            name=row.name,
            kind=row.kind,
            node_name=row.node_name,
            epoch=row.epoch,
            repo=row.repo,
            branch_name=branch_name,
            commit_hash=commit_hash,
        )
    return EnvelopeArtifact(name=row.name, kind=row.kind, node_name=row.node_name, epoch=row.epoch, content=row.data)


def _required_artifacts_block(node: Node) -> str:
    """The generated required-artifacts table appended to the pre-prompt (issue #143): one line per
    ``produces:`` entry naming its kind and the fleet-protocol verb that declares it, or ``""`` when
    the node declares nothing. Generated straight off ``node.produces`` — never authored app or
    toolchain knowledge, only blizzard's own fleet-protocol verbs (``bzh:app-agnostic-graphs``).
    ``#``-prefixed so a harness reading the prompt as a program sees a legal no-op."""
    if not node.produces:
        return ""
    lines = ["", "", "# Required artifacts for this node-step:"]
    for spec in node.produces:
        if spec.kind is ArtifactKind.GIT_COMMIT:
            lines.append(
                f"#   - {spec.name} (git_commit): push your branch, then run "
                f"`blizzard runner artifact commit --repo <repo> --branch <branch> "
                f"--commit <sha>` — <repo> is that repo's own worktree DIRECTORY NAME "
                f"(not an `owner/name` slug or URL), <sha> is the FULL commit sha "
                f"(`git rev-parse HEAD`), not abbreviated (--forge defaults to this "
                f"repo's own `origin`; pass it only to override)"
            )
        else:
            lines.append(
                f"#   - {spec.name} (asset): run `blizzard runner artifact create "
                f"--name {spec.name}` (content on stdin)"
            )
    return "\n".join(lines)


def _judgement_prompt(node: Node) -> str | None:
    """The node's **authored** judgement prose only — the elicitation tail naming the choice set is
    generated at delivery, not baked in here. ``None`` at a node with no choices."""
    if not node.choices:
        return None
    return node.judgement_prompt


def addendum_for_transition(graph: Graph, transition: TransitionFact | None) -> str | None:
    """The inlined arrival addendum of the edge ``transition`` took, or ``None``.

    Keyed off an already-recorded :class:`~blizzard.hub.domain.work.TransitionFact` rather than a live
    completion submission — the shape a re-fetched envelope needs, where no submission is in hand.
    ``None`` when the chunk has not yet transitioned, or the edge authored no addendum."""
    if transition is None or transition.from_node_id is None or transition.choice_name is None:
        return None
    edge = graph.edge_for_choice(transition.from_node_id, transition.choice_name)
    return edge.prompt_addendum if edge is not None else None


def _effective_session(
    chunk: Chunk, graph: Graph, node: Node
) -> tuple[str | None, list[str], str | None, RotatePolicyView | None]:
    """Resolve the node's effective session declaration (issue #144): precedence is **declaration >
    chunk default**, merged *field by field* rather than whole-record (pinned by
    tests/test_envelope.py::test_a_declaration_outranks_the_chunk_default_field_by_field). No default
    is invented here. A ``session_source`` resolving to no declaration is the node-name form, never a
    dangling reference — the validator rejected that at mint."""
    declaration = graph.session_by_name(node.session_source) if node.session_source else None
    if declaration is None:
        return (None, list(chunk.default_model), chunk.default_effort, None)
    rotate = declaration.rotate
    return (
        declaration.name,
        list(declaration.model) if declaration.model else list(chunk.default_model),
        declaration.effort if declaration.effort is not None else chunk.default_effort,
        RotatePolicyView(
            max_context_tokens=rotate.max_context_tokens,
            max_transcript_bytes=rotate.max_transcript_bytes,
            max_invocations=rotate.max_invocations,
        )
        if rotate is not None
        else None,
    )


def build_node_envelope(
    *,
    chunk: Chunk,
    graph: Graph,
    node: Node,
    artifacts: list[ArtifactRow],
    epoch: int,
    arrival_addendum: str | None = None,
) -> NodeEnvelope:
    """Assemble the envelope ``node`` is worked from. ``graph`` is required and carries no default, so
    omitting it is a ``TypeError`` (issue #144; pinned by
    tests/test_pin_hub_domain.py::test_build_node_envelope_requires_graph_explicitly)."""
    prompt = node.prompt
    if arrival_addendum:
        prompt = f"{prompt}\n\n{arrival_addendum}" if prompt else arrival_addendum
    required_artifacts = _required_artifacts_block(node)
    if required_artifacts:
        prompt = f"{prompt}{required_artifacts}" if prompt else required_artifacts.lstrip("\n")

    session_name, session_model, session_effort, session_rotate = _effective_session(chunk, graph, node)
    config = NodeConfig(
        node_id=node.node_id,
        node_name=node.name,
        executor=node.executor,
        session=node.session,
        session_source=node.session_source,
        session_name=session_name,
        session_model=session_model,
        session_effort=session_effort,
        session_rotate=session_rotate,
        judged_by=node.judged_by,
        checks=list(node.checks),
        checks_cwd=node.checks_cwd,
        checks_timeout=node.checks_timeout,
        produces=[ProducesEntry(name=p.name, kind=p.kind) for p in node.produces],
        retries_max=node.retries_max,
        mode=node.mode,
        choices=[
            EnvelopeChoice(name=c.name, description=c.description, requires_checks=c.requires_checks)
            for c in node.choices
        ],
    )
    return NodeEnvelope(
        chunk_id=chunk.chunk_id,
        graph_id=chunk.graph_id,
        epoch=epoch,
        node=config,
        prompt=prompt,
        judgement_prompt=_judgement_prompt(node),
        work_refs=[{"source": p.source, "ref": p.ref} for p in chunk.work_refs],
        artifacts=[_to_envelope_artifact(r) for r in latest_artifacts_by_name(artifacts)],
    )
