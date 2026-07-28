"""Node-envelope assembly — the pure builder behind every envelope.

The runner works a node-step from a :class:`~blizzard.wire.envelope.NodeEnvelope`;
this module builds one from already-loaded domain objects — the pinned graph, the
target node, the chunk, its artifacts, and the executing epoch (``bzh:domain-core``,
``bzh:domain-takes-objects``). It is a pure function: the same inputs always
produce the same envelope, so it unit-tests with zero store.

Three engine rules live here:

* the **pre-prompt** is the node's base prompt plus the inlined arrival addendum of
  the edge the chunk took to reach the node (the ``fail -> build`` addendum carries
  the review findings back), plus a generated required-artifacts table naming every
  ``produces:`` entry and the fleet-protocol verb that declares it (issue #143, Phase
  5 — :func:`_required_artifacts_block`), rendered ``#``-prefixed for the same
  harness-inertness reason as the judgement tail below;
* the **judgement prompt** is the node's authored judgement prose *only*; the
  generated elicitation tail naming the choice set — ``select exactly one and output
  <Choice>{name}</Choice>`` — is appended by the runner from the envelope's carried
  choice set when it delivers the judgement into the session, rendered harness-inert
  so a mock behavior script still ``exec``s (runner ``steps._elicitation_tail``).

Artifacts are resolved **latest-by-epoch per name**: a node re-run under a
higher epoch supersedes its own earlier output, and the envelope carries one entry
per ``{node_name}.{artifact-name}``.
"""

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
    """The procedurally-generated required-artifacts table appended to the pre-prompt
    (issue #143, Phase 5): one line per ``produces:`` entry naming its kind and the
    fleet-protocol verb that declares it. ``""`` when the node declares nothing (a hub
    node, or a worker node with no ``produces:``), so the empty case leaves ``prompt``
    untouched exactly as :func:`build_node_envelope` already does for a missing
    ``arrival_addendum``.

    Generated straight off ``node.produces`` — never authored app/toolchain knowledge,
    only blizzard's own fleet-protocol verbs (``bzh:app-agnostic-graphs``). Rendered as
    ``#``-prefixed lines for the same reason the runner's own generated tails are
    (``runner.loop.steps._elicitation_tail``, ``_nudge_message``): a real coding harness
    reads them as an ordinary prose comment block, while the mock harness's
    prompt-is-program ``exec`` sees a legal no-op rather than a ``SyntaxError``.
    """
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
    """The node's **authored** judgement prose; ``None`` at a node with no verdict.

    The author writes only the prose; the engine-generated
    elicitation tail (``select exactly one and output <Choice>{name}</Choice>``) is
    appended by the runner from ``node.choices`` (carried on the envelope config) when
    it delivers the judgement into the session — the runner renders it harness-inert
    (``#``-prefixed) so a mock behavior *script* still ``exec``s cleanly (runner
    ``steps._elicitation_tail``). Baking a prose tail here too would both
    duplicate it and break the mock's ``exec``. ``None`` at a node with no worker
    judgement (a hub node or a human gate carries no verdict elicitation).
    """
    if not node.choices:
        return None
    return node.judgement_prompt


def addendum_for_transition(graph: Graph, transition: TransitionFact | None) -> str | None:
    """The inlined arrival addendum of the edge ``transition`` took, or ``None``.

    Mirrors ``apply.py``'s own resolution of the just-taken edge's ``prompt_addendum``,
    but keyed off an already-recorded :class:`~blizzard.hub.domain.work.TransitionFact`
    rather than a live completion submission — the shape a re-fetched envelope needs
    (``GET /chunks/{id}/envelope``, the lost-apply re-read and the held-chunk-advance
    poll, ``runner.loop.steps._spawn_into_held_node``), where no submission is in hand.
    ``None`` when the chunk has not yet transitioned, or the edge authored no addendum
    (the review-fail loop's findings addendum, and #64's kick-back addendum, both ride
    this same resolution)."""
    if transition is None or transition.from_node_id is None or transition.choice_name is None:
        return None
    edge = graph.edge_for_choice(transition.from_node_id, transition.choice_name)
    return edge.prompt_addendum if edge is not None else None


def _effective_session(
    chunk: Chunk, graph: Graph, node: Node
) -> tuple[str | None, list[str], str | None, RotatePolicyView | None]:
    """Resolve the node's effective session declaration (issue #144).

    Precedence is **session declaration > chunk default**, merged *field by field* rather
    than whole-record: a declaration that names a ``model`` but no ``effort`` takes the
    chunk's effort, not nothing. The runner's own default is the last resort and is
    applied there, so this never invents one.

    Resolved hub-side because the hub owns both halves — the graph and the chunk. Two
    tiers of "no declaration" reach here and both are legitimate:

    * a node whose ``session:`` names a **node** (``resume:<node>``, issue #115) or is
      bare — no pool, so ``session_name`` is ``None``, but the chunk's defaults still
      apply. That is the precedence rule's intended reach and the one behavior change
      this phase makes to a pre-#144 graph.
    * a node whose ``session:`` names a declared session — the declaration wins per field.

    A ``session_source`` that resolves to no declaration cannot be a dangling reference:
    the validator rejected that at mint. It is the node-name form.
    """
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
    """Assemble the envelope a runner works ``node`` from.

    ``graph`` is required and carries no default (issue #144): the node's session
    declaration is resolved against it, and a caller that forgot the argument silently
    getting the pre-#144 "no declaration, no chunk default" envelope back — with no type
    error — is exactly the drift this would otherwise invite. Every call site already
    holds it.
    """
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
