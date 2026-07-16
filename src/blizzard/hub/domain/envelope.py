"""Node-envelope assembly (D-038/D-089) — the pure builder behind every envelope.

The runner works a node-step from a :class:`~blizzard.wire.envelope.NodeEnvelope`;
this module builds one from already-loaded domain objects — the pinned graph, the
target node, the chunk, its artifacts, and the executing epoch (``bzh:domain-core``,
``bzh:domain-takes-objects``). It is a pure function: the same inputs always
produce the same envelope, so it unit-tests with zero store.

Two engine rules live here, both D-038:

* the **pre-prompt** is the node's base prompt plus the inlined arrival addendum of
  the edge the chunk took to reach the node (the ``fail -> build`` addendum carries
  the review findings back);
* the **judgement prompt** is the node's authored judgement prose *only* (D-042); the
  generated elicitation tail naming the choice set — ``select exactly one and output
  <Choice>{name}</Choice>`` — is appended by the runner from the envelope's carried
  choice set when it delivers the judgement into the session, rendered harness-inert
  so a mock behavior script still ``exec``s (runner ``steps._elicitation_tail``).

Artifacts are resolved **latest-by-epoch per name** (D-089): a node re-run under a
higher epoch supersedes its own earlier output, and the envelope carries one entry
per ``{node_name}.{artifact-name}``.
"""

from __future__ import annotations

from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.graph import Node
from blizzard.hub.domain.work import Chunk
from blizzard.wire.envelope import EnvelopeArtifact, EnvelopeChoice, NodeConfig, NodeEnvelope


def latest_artifacts_by_name(rows: list[ArtifactRow]) -> list[ArtifactRow]:
    """Resolve an artifact list to one row per ``{node_name}.{name}``, newest epoch wins (D-089)."""
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


def _judgement_prompt(node: Node) -> str | None:
    """The node's **authored** judgement prose (D-042); ``None`` at a node with no verdict.

    The author writes only the prose (design/workflow-engine.md); the engine-generated
    elicitation tail (``select exactly one and output <Choice>{name}</Choice>``) is
    appended by the runner from ``node.choices`` (carried on the envelope config) when
    it delivers the judgement into the session — the runner renders it harness-inert
    (``#``-prefixed) so a mock behavior *script* still ``exec``s cleanly (D-042,
    runner ``steps._elicitation_tail``). Baking a prose tail here too would both
    duplicate it and break the mock's ``exec``. ``None`` at a node with no worker
    judgement (a hub node or a human gate carries no verdict elicitation).
    """
    if not node.choices:
        return None
    return node.judgement_prompt


def build_node_envelope(
    *,
    chunk: Chunk,
    node: Node,
    artifacts: list[ArtifactRow],
    epoch: int,
    arrival_addendum: str | None = None,
) -> NodeEnvelope:
    """Assemble the envelope a runner works ``node`` from (D-089)."""
    prompt = node.prompt
    if arrival_addendum:
        prompt = f"{prompt}\n\n{arrival_addendum}" if prompt else arrival_addendum

    config = NodeConfig(
        node_id=node.node_id,
        node_name=node.name,
        executor=node.executor,
        session=node.session,
        judged_by=node.judged_by,
        checks=list(node.checks),
        produces=list(node.produces),
        retries_max=node.retries_max,
        mode=node.mode,
        choices=[EnvelopeChoice(name=c.name, description=c.description) for c in node.choices],
    )
    return NodeEnvelope(
        chunk_id=chunk.chunk_id,
        graph_id=chunk.graph_id,
        epoch=epoch,
        node=config,
        prompt=prompt,
        judgement_prompt=_judgement_prompt(node),
        pm_pointers=[{"source": p.source, "ref": p.ref} for p in chunk.pm_pointers],
        artifacts=[_to_envelope_artifact(r) for r in latest_artifacts_by_name(artifacts)],
    )
