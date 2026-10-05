"""Node-envelope assembly over already-loaded domain objects (``bzh:domain-core``, ``bzh:domain-takes-objects``).

The **pre-prompt** is the node's base prompt, the inlined arrival addendum of the edge the chunk took to
reach the node, and a generated required-artifacts table. The **judgement prompt** is the
node's authored prose only. Node-scope artifacts resolve **latest-by-epoch per
``{node_name}.{name}``**; the graph mint's baked-in declarations ride alongside as authored."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.node_steps import SessionMode
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb, MovementKind, TransitionFact, WorkRefLabel
from blizzard.hub.domain.graph.model import Edge, Graph, Node, RotatePolicy


@domain_model
@dataclass(frozen=True)
class CarriedArtifact:
    """One node-scope artifact as an envelope carries it: a ``git_commit`` decoded to its repo,
    branch, and commit, an ``asset`` to its content."""

    name: str
    kind: ArtifactKind
    node_name: str
    epoch: int
    repo: str | None = None
    branch_name: str | None = None
    commit_hash: str | None = None
    content: str | None = None

    @classmethod
    def of(cls, row: StoredArtifact) -> CarriedArtifact:
        if row.kind is ArtifactKind.GIT_COMMIT:
            branch_name, _, commit_hash = row.data.partition(":")
            return cls(
                name=row.name,
                kind=row.kind,
                node_name=row.node_name,
                epoch=row.epoch,
                repo=row.repo,
                branch_name=branch_name,
                commit_hash=commit_hash,
            )
        return cls(name=row.name, kind=row.kind, node_name=row.node_name, epoch=row.epoch, content=row.data)


@domain_model
@dataclass(frozen=True)
class LatestArtifacts:
    """Artifact rows resolved to one per ``{node_name}.{name}``, newest epoch wins."""

    rows: list[StoredArtifact]

    @classmethod
    def of(cls, rows: list[StoredArtifact]) -> LatestArtifacts:
        latest: dict[tuple[str, str], StoredArtifact] = {}
        for row in rows:
            key = (row.node_name, row.name)
            current = latest.get(key)
            if current is None or row.epoch > current.epoch:
                latest[key] = row
        return cls(list(latest.values()))

    @property
    def carried(self) -> list[CarriedArtifact]:
        return [CarriedArtifact.of(row) for row in self.rows]


@domain_model
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
    def of_facts(cls, graph: Graph, facts: ChunkFacts | None) -> Arrival:
        """The arrival of the chunk's *latest movement*: its addendum only when that movement is a
        transition into the current node — a later restart or migration re-enters the node without
        the edge that once led there, so it carries none."""
        movement = facts.latest_movement() if facts is not None else None
        if facts is None or movement is None or movement.kind is not MovementKind.TRANSITION:
            return cls(None)
        return cls.of_transition(graph, facts.newest_transition())

    @classmethod
    def of_choice(cls, graph: Graph, from_node: Node, choice: str) -> Arrival:
        return cls(graph.edge_for_choice(from_node.node_id, choice))

    @property
    def addendum(self) -> str | None:  # ast-grep-ignore: bzh:property-delegates
        return self.edge.prompt_addendum if self.edge is not None else None


@domain_model
@dataclass(frozen=True)
class EffectiveSession:
    """A node's session facets resolved **declaration > chunk default**, merged *field by field*
    (pinned by
    tests/test_envelope.py::test_a_declaration_outranks_the_chunk_default_field_by_field)."""

    name: str | None
    model: list[str]
    effort: str | None
    rotate: RotatePolicy | None
    compaction_window: str | None
    harnesses: list[str]

    @classmethod
    def of(cls, chunk: Chunk, graph: Graph, node: Node) -> EffectiveSession:
        declaration = graph.session_by_name(node.session_source) if node.session_source else None
        if declaration is None:
            return cls(None, list(chunk.default_model), chunk.default_effort, None, None, list(chunk.default_harnesses))
        return cls(
            declaration.name,
            list(declaration.model) if declaration.model else list(chunk.default_model),
            declaration.effort if declaration.effort is not None else chunk.default_effort,
            declaration.rotate,
            declaration.compaction_window,
            list(declaration.harnesses) if declaration.harnesses else list(chunk.default_harnesses),
        )


class NoCurrentNode(Exception):
    """The chunk has no node-step to run: it has ended, or its current node id names no node."""

    def __init__(self, chunk_id: str) -> None:
        super().__init__("chunk has no current runner node (terminal)")
        self.chunk_id = chunk_id


@domain_model
@dataclass(frozen=True)
class Envelope:
    """The envelope ``node`` is worked from. ``graph`` carries no default, so omitting it is a
    ``TypeError`` (pinned by
    tests/test_pin_hub_domain.py::test_envelope_requires_graph_explicitly)."""

    chunk: Chunk
    graph: Graph
    node: Node
    artifacts: list[StoredArtifact]
    epoch: int
    arrival_addendum: str | None = None
    # This visit was forced by an operator restart, which overrides the node's
    # declared session mode below — derived from the durable fact, so a re-read still says so.
    entered_by_restart: bool = False
    # Renders a work ref's source-native token; omitted, no ref carries a label.
    label: WorkRefLabel | None = None

    @classmethod
    def current(
        cls,
        chunk: Chunk,
        graph: Graph,
        facts: ChunkFacts,
        artifacts: list[StoredArtifact],
        *,
        label: WorkRefLabel | None = None,
    ) -> Envelope:
        """The envelope of the node-step the chunk stands at now — its current node (else the
        entry) at the epoch floor. Raises :class:`NoCurrentNode` for every ended chunk alike, and
        for a current node id that names no node."""
        node = facts.current_node(graph) if facts.admits(ChunkVerb.READ_ENVELOPE) else None
        if node is None:
            raise NoCurrentNode(chunk.chunk_id)
        return cls(
            chunk=chunk,
            graph=graph,
            node=node,
            artifacts=artifacts,
            epoch=facts.epoch_floor(),
            arrival_addendum=Arrival.of_facts(graph, facts).addendum,
            entered_by_restart=facts.entered_by_restart(),
            label=label,
        )

    @property
    def work_refs(self) -> list[dict[str, str]]:  # ast-grep-ignore: bzh:property-delegates
        refs: list[dict[str, str]] = []
        for p in self.chunk.work_refs:
            entry = {"source": p.source, "ref": p.ref}
            label = self.label(p) if self.label is not None else None
            if label is not None:
                entry["label"] = label
            refs.append(entry)
        return refs

    @property
    def prompt(self) -> str | None:  # ast-grep-ignore: bzh:property-delegates
        prompt = self.node.prompt
        if self.arrival_addendum:
            prompt = f"{prompt}\n\n{self.arrival_addendum}" if prompt else self.arrival_addendum
        required_artifacts = self.required_artifacts
        if required_artifacts:
            prompt = f"{prompt}{required_artifacts}" if prompt else required_artifacts.lstrip("\n")
        return prompt

    @property
    def required_artifacts(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """The generated table appended to the pre-prompt: one line per ``produces:``
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
    def judgement_prompt(self) -> str | None:  # ast-grep-ignore: bzh:property-delegates
        """The node's **authored** judgement prose only — the elicitation tail naming the choice set
        is generated at delivery, not baked in here. ``None`` at a node with no choices."""
        if not self.node.choices:
            return None
        return self.node.judgement_prompt

    @property
    def session(self) -> EffectiveSession:
        return EffectiveSession.of(self.chunk, self.graph, self.node)

    @property
    def session_mode(self) -> SessionMode:
        """The node's declared session mode — ``fresh`` whenever an operator restart forced this visit."""
        return self._session_mode()

    def _session_mode(self) -> SessionMode:
        return SessionMode.FRESH if self.entered_by_restart else self.node.session

    @property
    def carried_artifacts(self) -> list[CarriedArtifact]:
        """The node-scope artifacts this step carries, latest-by-epoch per ``{node_name}.{name}``."""
        return LatestArtifacts.of(self.artifacts).carried
