"""Node-envelope assembly over already-loaded domain objects (``bzh:domain-core``, ``bzh:domain-takes-objects``).

The **pre-prompt** is the node's base prompt, the inlined arrival addendum of the edge the chunk took to
reach the node, and a generated required-artifacts table (issue #143). The **judgement prompt** is the
node's authored prose only. Node-scope artifacts resolve **latest-by-epoch per
``{node_name}.{name}``**; the graph mint's baked-in declarations ride alongside as authored."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.node_steps import SessionMode
from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.graph import Edge, Graph, Node
from blizzard.hub.domain.work import Chunk, TransitionFact
from blizzard.wire.envelope import (
    EnvelopeArtifact,
    EnvelopeChoice,
    GraphArtifact,
    NodeConfig,
    NodeEnvelope,
    RotatePolicyView,
)
from blizzard.wire.graph import ProducesEntry


@dataclass(frozen=True)
class LatestArtifacts:
    """Artifact rows resolved to one per ``{node_name}.{name}``, newest epoch wins."""

    rows: list[ArtifactRow]

    @classmethod
    def of(cls, rows: list[ArtifactRow]) -> LatestArtifacts:
        latest: dict[tuple[str, str], ArtifactRow] = {}
        for row in rows:
            key = (row.node_name, row.name)
            current = latest.get(key)
            if current is None or row.epoch > current.epoch:
                latest[key] = row
        return cls(list(latest.values()))

    @property
    def wire(self) -> list[EnvelopeArtifact]:
        return [self._projected(row) for row in self.rows]

    @staticmethod
    def _projected(row: ArtifactRow) -> EnvelopeArtifact:
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
        return EnvelopeArtifact(
            name=row.name, kind=row.kind, node_name=row.node_name, epoch=row.epoch, content=row.data
        )


@dataclass(frozen=True)
class Arrival:
    """The edge a chunk took into its current node, and the addendum that edge inlines."""

    edge: Edge | None

    @classmethod
    def of_transition(cls, graph: Graph, transition: TransitionFact | None) -> Arrival:
        """Keyed off an already-recorded transition rather than a live completion submission — the
        shape a re-fetched envelope needs, where no submission is in hand."""
        if transition is None or transition.from_node_id is None or transition.choice_name is None:
            return cls(None)
        return cls(graph.edge_for_choice(transition.from_node_id, transition.choice_name))

    @classmethod
    def of_choice(cls, graph: Graph, from_node: Node, choice: str) -> Arrival:
        return cls(graph.edge_for_choice(from_node.node_id, choice))

    @property
    def addendum(self) -> str | None:
        return self.edge.prompt_addendum if self.edge is not None else None


@dataclass(frozen=True)
class EffectiveSession:
    """A node's session facets resolved **declaration > chunk default**, merged *field by field*
    (issue #144; pinned by
    tests/test_envelope.py::test_a_declaration_outranks_the_chunk_default_field_by_field)."""

    name: str | None
    model: list[str]
    effort: str | None
    rotate: RotatePolicyView | None
    compaction_window: str | None

    @classmethod
    def of(cls, chunk: Chunk, graph: Graph, node: Node) -> EffectiveSession:
        declaration = graph.session_by_name(node.session_source) if node.session_source else None
        if declaration is None:
            return cls(None, list(chunk.default_model), chunk.default_effort, None, None)
        rotate = declaration.rotate
        return cls(
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
            declaration.compaction_window,
        )


@dataclass(frozen=True)
class Envelope:
    """The envelope ``node`` is worked from. ``graph`` carries no default, so omitting it is a
    ``TypeError`` (issue #144; pinned by
    tests/test_pin_hub_domain.py::test_envelope_requires_graph_explicitly)."""

    chunk: Chunk
    graph: Graph
    node: Node
    artifacts: list[ArtifactRow]
    epoch: int
    arrival_addendum: str | None = None
    # This visit was forced by an operator restart (issue #370), which overrides the node's
    # declared session mode below — derived from the durable fact, so a re-read still says so.
    entered_by_restart: bool = False

    @property
    def prompt(self) -> str | None:
        prompt = self.node.prompt
        if self.arrival_addendum:
            prompt = f"{prompt}\n\n{self.arrival_addendum}" if prompt else self.arrival_addendum
        required_artifacts = self.required_artifacts
        if required_artifacts:
            prompt = f"{prompt}{required_artifacts}" if prompt else required_artifacts.lstrip("\n")
        return prompt

    @property
    def required_artifacts(self) -> str:
        """The generated table appended to the pre-prompt (issue #143): one line per ``produces:``
        entry naming its kind and the fleet-protocol verb that declares it, or ``""``. Never
        authored app or toolchain knowledge (``bzh:app-agnostic-graphs``); ``#``-prefixed so a
        harness reading the prompt as a program sees a legal no-op."""
        if not self.node.produces:
            return ""
        lines = ["", "", "# Required artifacts for this node-step:"]
        for spec in self.node.produces:
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

    @property
    def judgement_prompt(self) -> str | None:
        """The node's **authored** judgement prose only — the elicitation tail naming the choice set
        is generated at delivery, not baked in here. ``None`` at a node with no choices."""
        if not self.node.choices:
            return None
        return self.node.judgement_prompt

    @property
    def config(self) -> NodeConfig:
        session = EffectiveSession.of(self.chunk, self.graph, self.node)
        node = self.node
        return NodeConfig(
            node_id=node.node_id,
            node_name=node.name,
            executor=node.executor,
            session=SessionMode.FRESH if self.entered_by_restart else node.session,
            session_source=node.session_source,
            session_name=session.name,
            session_model=session.model,
            session_effort=session.effort,
            session_rotate=session.rotate,
            session_compaction_window=session.compaction_window,
            judged_by=node.judged_by,
            checks=list(node.checks),
            checks_cwd=node.checks_cwd,
            checks_timeout=node.checks_timeout,
            produces=[ProducesEntry(name=p.name, kind=p.kind) for p in node.produces],
            proposes_work_items=node.proposes_work_items,
            retries_max=node.retries_max,
            mode=node.mode,
            choices=[
                EnvelopeChoice(name=c.name, description=c.description, requires_checks=c.requires_checks)
                for c in node.choices
            ],
        )

    @property
    def wire(self) -> NodeEnvelope:
        return NodeEnvelope(
            chunk_id=self.chunk.chunk_id,
            graph_id=self.chunk.graph_id,
            epoch=self.epoch,
            node=self.config,
            prompt=self.prompt,
            judgement_prompt=self.judgement_prompt,
            work_refs=[{"source": p.source, "ref": p.ref} for p in self.chunk.work_refs],
            artifacts=LatestArtifacts.of(self.artifacts).wire,
            graph_artifacts=[
                GraphArtifact(name=a.name, kind=ArtifactKind.ASSET, content=a.content) for a in self.graph.artifacts
            ],
        )
