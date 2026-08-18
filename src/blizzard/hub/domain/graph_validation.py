"""Mint-time graph validation.

The rules ``POST /api/graphs`` runs before minting a graph immutable: errors reject the
definition, warnings mint it flagged. Pure domain logic over an already-parsed
:class:`GraphDoc` (``bzh:domain-core``) — no filesystem, no framework. Reachability is
a warning, not an error: cycles are intentional and retries escape to escalation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from blizzard.hub.domain.artifacts import is_valid_graph_artifact_name
from blizzard.hub.domain.graph import (
    RESERVED_TERMINAL,
    SESSION_LEGAL_FORMS,
    ChoiceTarget,
    Executor,
    GraphDoc,
    JudgedBy,
    NodeDoc,
    RetriesExhausted,
    SessionDecl,
    SessionMode,
)

# A filename's trailing extension. Bounded at 8 characters so a long dotted token — a
# version string, a sentence-ending abbreviation — is not read as one.
_FILE_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}$")


def _is_uninlined_file_reference(content: str) -> bool:
    """An ``artifacts:`` value that still reads as a bare path — one whitespace-free token, no
    URL scheme, carrying either a ``/`` separator or a filename extension. Baked content is prose
    carrying whitespace, so no real artifact fits the shape; the shape survives only when nothing
    inlined it. One residual hole, deliberate: an extension-less lone token (``notes``) is
    indistinguishable from one-word content, so it passes."""
    if content.split() != [content] or "://" in content:
        return False
    return "/" in content or bool(_FILE_EXTENSION.search(content))


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of validating a graph definition."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no error rejects the definition (warnings still mint)."""
        return not self.errors


@dataclass(frozen=True)
class Validator:
    """A parsed graph run against its structural rules.

    :meth:`of` does the running; the instance it returns carries what was found."""

    doc: GraphDoc
    node_names: set[str]
    session_names: set[str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, doc: GraphDoc) -> Validator:
        validator = cls(doc, {n.name for n in doc.nodes}, set(doc.sessions))
        validator._check()
        return validator

    @property
    def result(self) -> ValidationResult:
        return ValidationResult(errors=self.errors, warnings=self.warnings)

    def _check(self) -> None:
        self._check_entry()
        self._check_sessions()
        self._check_artifacts()
        for node in self.doc.nodes:
            check = NodeCheck.of(node, node_names=self.node_names, session_names=self.session_names)
            self.errors.extend(check.errors)
        self.warnings.extend(Reachability.of(self.doc).warnings)

    def _check_entry(self) -> None:
        if self.doc.entry not in self.node_names:
            self.errors.append(f"entry `{self.doc.entry}` does not name an existing node")

    def _check_sessions(self) -> None:
        """A session name and a node name share one reference namespace (issue #144), so a
        collision would make a node's own lineage silently unreachable."""
        for name, decl in self.doc.sessions.items():
            if name in self.node_names:
                self.errors.append(
                    f"session `{name}`: a session name may not collide with a node name — "
                    f"`resume:{name}` would resolve to the session and never the node"
                )
            self._check_decl(name, decl)

    def _check_decl(self, name: str, decl: SessionDecl) -> None:
        for entry in decl.model:
            if not entry:
                self.errors.append(f"session `{name}`: `model` entries must be non-empty strings")
        # Non-empty only: the `effort`/`compaction_window` vocabulary is the adapter's to
        # recognize (``bzh:one-owner``), same as `effort` (blizzard#343).
        if decl.effort is not None and not decl.effort.strip():
            self.errors.append(f"session `{name}`: `effort` must be a non-empty string")
        if decl.compaction_window is not None and not decl.compaction_window.strip():
            self.errors.append(f"session `{name}`: `compaction_window` must be a non-empty string")
        rotate = decl.rotate
        if rotate is None:
            return
        for field_name, value in (
            ("max_context_tokens", rotate.max_context_tokens),
            ("max_transcript_bytes", rotate.max_transcript_bytes),
            ("max_invocations", rotate.max_invocations),
        ):
            if value is not None and value <= 0:
                self.errors.append(f"session `{name}`: `rotate.{field_name}` must be a positive number")

    def _check_artifacts(self) -> None:
        """Every graph-scoped `artifacts:` name is legal, collides with no node's `produces:`
        name — both scopes are retrieved through the one artifact CLI, so a shared name would be
        genuinely ambiguous, not a legal shadow — and carries content rather than an unresolved
        file path."""
        produces_names = {spec.name for node in self.doc.nodes for spec in node.produces}
        for name, content in self.doc.artifacts.items():
            if not is_valid_graph_artifact_name(name):
                self.errors.append(
                    f"artifact `{name}`: name must be alphanumerics with internal `-`, `_`, `.` only "
                    f"— non-empty, no leading/trailing separator, no two separators in a row, no `/`"
                )
            if name in produces_names:
                self.errors.append(f"artifact `{name}`: collides with a node's `produces:` name")
            # A definition arriving with no directory to resolve against would otherwise bake the
            # path itself as the content and serve it to workers as the artifact.
            if _is_uninlined_file_reference(content):
                self.errors.append(
                    f"artifact `{name}`: value `{content}` is a file path, not content — a definition "
                    f"posted without a directory to resolve against must carry the file's text inline"
                )


@dataclass(frozen=True)
class NodeCheck:
    """One node run against the rules its executor, judgement, checks, and session imply."""

    node: NodeDoc
    node_names: set[str]
    session_names: set[str]
    errors: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, node: NodeDoc, *, node_names: set[str], session_names: set[str]) -> NodeCheck:
        check = cls(node, node_names, session_names)
        check._check()
        return check

    def _check(self) -> None:
        self._check_run()
        self._check_poll()
        self._check_judgement_kind()
        self._check_checks_gating()
        self._check_choice_targets()
        self._check_session_ref()
        self._check_retries()

    def _check_run(self) -> None:
        # `run:` is legal ONLY on a hub command node (#65): a worker node's step is an agent
        # turn, not a declared command list.
        if self.node.run and self.node.executor is not Executor.HUB:
            self.errors.append(f"node `{self.node.name}`: `run:` is only legal on a hub node (`executor: hub`)")

    def _check_poll(self) -> None:
        """The pending-poll cadence (#66) is legal only on a hub command node — a node with
        no ``run:`` step never reports ``pending``."""
        node = self.node
        interval, timeout = node.poll_interval_seconds, node.poll_timeout_seconds
        if (interval is not None or timeout is not None) and not node.is_hub_command_node:
            self.errors.append(
                f"node `{node.name}`: `poll_interval`/`poll_timeout` are only legal on a "
                f"hub command node (`executor: hub` with `run:`)"
            )
        if interval is not None and interval <= 0:
            self.errors.append(f"node `{node.name}`: `poll_interval` must be a positive number of seconds")
        if timeout is not None and timeout <= 0:
            self.errors.append(f"node `{node.name}`: `poll_timeout` must be a positive number of seconds")
        if interval is not None and timeout is not None and timeout < interval:
            self.errors.append(f"node `{node.name}`: `poll_timeout` must be >= `poll_interval`")

    def _check_judgement_kind(self) -> None:
        node, judgement = self.node, self.node.judgement
        if node.executor is Executor.RUNNER:
            if judgement is None:
                self.errors.append(f"node `{node.name}`: a runner node must declare a judgement")
            elif judgement.by is JudgedBy.WORKER and not judgement.prompt:
                self.errors.append(f"node `{node.name}`: a worker-judged node must declare `judgement.prompt`")
            elif judgement.by is JudgedBy.HUMAN and judgement.prompt:
                self.errors.append(
                    f"node `{node.name}`: a human-judged (gate) node must not declare `judgement.prompt`"
                )
        elif node.executor is Executor.HUB:
            # The generic hub command node (#65, #67) — structurally agentless: no prompt, no
            # worker judgement prose, no in-session checks.
            if node.prompt is not None:
                self.errors.append(f"hub node `{node.name}`: a hub command node must not declare `prompt`")
            if node.checks:
                self.errors.append(f"hub node `{node.name}`: a hub command node must not declare `checks`")
            if judgement is not None and judgement.prompt:
                self.errors.append(f"hub node `{node.name}`: a hub command node must not declare `judgement.prompt`")
            if judgement is None:
                self.errors.append(
                    f"hub node `{node.name}`: a hub command node must declare a judgement "
                    f"(its outcome choices — at least the edges its commands route)"
                )

    def _check_checks_gating(self) -> None:
        """``checks_cwd``, ``checks_timeout``, and ``requires_checks`` are all meaningless
        without a ``checks:`` list to run (issue #114)."""
        node = self.node
        if not node.checks:
            if node.checks_cwd is not None:
                self.errors.append(f"node `{node.name}`: `checks_cwd` is only legal on a node that declares `checks:`")
            if node.checks_timeout is not None:
                self.errors.append(
                    f"node `{node.name}`: `checks_timeout` is only legal on a node that declares `checks:`"
                )
        if node.checks_timeout is not None and node.checks_timeout <= 0:
            self.errors.append(f"node `{node.name}`: `checks_timeout` must be a positive number of seconds")
        if node.judgement is None:
            return
        for choice in node.judgement.choices:
            if not choice.requires_checks:
                continue
            if not node.checks:
                self.errors.append(
                    f"node `{node.name}` choice `{choice.name}`: `requires_checks` is only legal on a "
                    f"choice whose node declares `checks:`"
                )
            # A gate node's exit is a person's signoff — the checks gate never runs there,
            # so `requires_checks` would be inert and misleading.
            if node.judgement.by is JudgedBy.HUMAN:
                self.errors.append(
                    f"node `{node.name}` choice `{choice.name}`: `requires_checks` is not legal on a "
                    f"human-judged (gate) node"
                )

    def _check_choice_targets(self) -> None:
        """Every choice needs a description and a ``to`` that resolves: a same-graph node
        name, the reserved terminal, or a well-formed ``graph:<name>`` target (issue #90)."""
        node = self.node
        if node.judgement is None:
            return
        for choice in node.judgement.choices:
            if not choice.description:
                self.errors.append(f"node `{node.name}` choice `{choice.name}`: missing `description`")
            if choice.to is None:
                self.errors.append(f"node `{node.name}` choice `{choice.name}`: missing `to`")
                continue
            target = ChoiceTarget.of(choice.to)
            if target.malformed:
                self.errors.append(
                    f"node `{node.name}` choice `{choice.name}`: malformed cross-graph target "
                    f"`to: {choice.to}` — expected `graph:<name>`"
                )
            elif target.node is not None and target.node != RESERVED_TERMINAL and target.node not in self.node_names:
                self.errors.append(
                    f"node `{node.name}` choice `{choice.name}`: `to: {choice.to}` resolves to no node "
                    f"(and is not the reserved terminal `{RESERVED_TERMINAL}`)"
                )

    def _check_session_ref(self) -> None:
        """``resume:<name>`` resolves declared-session-first, node-second; ``fresh:<name>``
        resolves against sessions only (D1, issues #115, #144)."""
        node = self.node
        if node.session_malformed:
            self.errors.append(f"node `{node.name}`: malformed session value — expected {SESSION_LEGAL_FORMS}")
        elif node.session_source is None:
            pass
        elif node.session is SessionMode.FRESH:
            if node.session_source not in self.session_names:
                self.errors.append(
                    f"node `{node.name}`: session `fresh:{node.session_source}` names no declared session "
                    f"(`fresh:<name>` must name a `sessions:` entry, never a node)"
                )
        elif node.session_source not in self.session_names and node.session_source not in self.node_names:
            self.errors.append(
                f"node `{node.name}`: session `resume:{node.session_source}` names neither a declared "
                f"session nor a node"
            )

    def _check_retries(self) -> None:
        # The retry escape hatch, when present, is the only legal exhaustion target.
        node = self.node
        if node.retries_exhausted is not None and node.retries_exhausted != RetriesExhausted.ESCALATE.value:
            self.errors.append(
                f"node `{node.name}`: retries.exhausted must be `{RetriesExhausted.ESCALATE.value}`, "
                f"got `{node.retries_exhausted}`"
            )


@dataclass(frozen=True)
class Reachability:
    """What the entry reaches over the adjacency every node's authored choices imply.

    No node name is privileged (#67); a cross-graph target (issue #90) is an exit *out*
    of this graph, so like the terminal it contributes no intra-graph adjacency."""

    entry: str
    node_names: set[str]
    edges: dict[str, set[str]]

    @classmethod
    def of(cls, doc: GraphDoc) -> Reachability:
        edges: dict[str, set[str]] = {n.name: set() for n in doc.nodes}
        for node in doc.nodes:
            if node.judgement is None:
                continue
            for choice in node.judgement.choices:
                if choice.to is not None and ChoiceTarget.of(choice.to).node is not None:
                    edges[node.name].add(choice.to)
        return cls(doc.entry, set(edges), edges)

    @property
    def warnings(self) -> list[str]:
        if self.entry not in self.node_names:
            return []  # an entry error already fired; reachability is meaningless
        warnings = [
            f"node `{name}` is unreachable from entry `{self.entry}`"
            for name in sorted(self.node_names - self.reached())
        ]
        if RESERVED_TERMINAL not in self.reached(include_terminal=True):
            warnings.append(f"no path from entry `{self.entry}` to the terminal `{RESERVED_TERMINAL}`")
        return warnings

    def reached(self, *, include_terminal: bool = False) -> set[str]:
        seen: set[str] = set()
        stack = [self.entry]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            for target in self.edges.get(current, set()):
                if target == RESERVED_TERMINAL and not include_terminal:
                    continue
                stack.append(target)
        return seen
