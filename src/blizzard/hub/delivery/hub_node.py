"""The generic hub command node executor — THE primitive (#65).

Walk a node's declared ``run:`` list, skipping any step whose ``produces:`` marker is already
durable, and map the outcome to an authored edge. Structurally agentless
(``bzh:deterministic-shell``); a pure step function over injected seams (``bzh:steppable-loop``,
``bzh:domain-core``). Serialization is a fact: ``run`` returns ``None`` if the slot is held."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.ids import ARTIFACT_PREFIX, TRANSITION_PREFIX, mint
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.delivery.command_runner import IHubCommandRunner
from blizzard.hub.delivery.marker_auth import MarkerAuthority
from blizzard.hub.delivery.repo_ref import parse_repo_ref
from blizzard.hub.delivery.workdir import IHubWorkdir
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.graph import (
    DEFAULT_BOUNCE_CAP,
    HUB_DEFAULT_FAILURE_CHOICE,
    HUB_DEFAULT_SUCCESS_CHOICE,
    HUB_PENDING_CHOICE,
    RESERVED_TERMINAL,
    Graph,
    Node,
)
from blizzard.hub.domain.work import (
    Chunk,
    HubNodePollFact,
    IWriteChunkRepository,
    bounce_count,
    bounces_over_cap,
    hub_node_poll_history,
    landed_repos_from_markers,
)
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_HUB_RUNNER_ID = "hub"
# Written when an outcome has no authored edge (`_route`); also the once-per-(node,
# epoch) dedupe key gating the event_log row beside it.
_UNROUTABLE_ARTIFACT_NAME = "hub-unroutable-outcome"
_EVENT_UNROUTABLE_OUTCOME = "hub-node-unroutable-outcome"

# Measured against the injected clock, never wall time. Generous on purpose: only a slot
# abandoned by a `kill -9` — no matching release ever comes — should be reclaimed.
DEFAULT_SLOT_STALE_AFTER = timedelta(minutes=30)

# Pending-poll cadence defaults (#66), overridable per node.
DEFAULT_POLL_INTERVAL = timedelta(seconds=30)
DEFAULT_POLL_TIMEOUT = timedelta(minutes=30)

# Crash points (``bzh:crash-point-registry``) — the per-step re-run windows; recovery is
# the next hub-advance, which re-runs whatever the markers below do not cover.
_CP_HUBNODE_AFTER_STEP_BEFORE_MARKER = crashpoint(
    "hubnode.after-step.before-marker", "a run: step exited 0; its produces: marker is not yet durable"
)
_CP_HUBNODE_AFTER_MARKER_BEFORE_NEXT = crashpoint(
    "hubnode.after-marker.before-next", "a run: step's marker is durable; the next step has not started"
)
# A kill here leaves the slot live with no release coming; `DEFAULT_SLOT_STALE_AFTER`
# reclaims it, and pending-ness is re-derived from the durable poll fact.
_CP_HUBNODE_AFTER_POLL_BEFORE_SLOT_RELEASE = crashpoint(
    "hubnode.after-poll.before-slot-release",
    "the poll-attempt fact is durable; the fleet-wide slot is not yet released",
)


@dataclass(frozen=True)
class HubRunResult:
    """The outcome of one :meth:`HubNodeExecutor.run` call that actually ran.

    ``transition_id`` (issue #213) is set only when ``wrote_transition`` is true; a
    pending poll, a bounce, or an escalation records no transition row."""

    outcome_choice: str
    to_node_name: str
    wrote_transition: bool
    detail: str = ""
    transition_id: str | None = None


@dataclass(frozen=True)
class HubEnvInputs:
    """The already-loaded domain inputs :func:`build_hub_env` assembles into an env."""

    chunk: Chunk
    node: Node
    workdir: str
    epoch: int
    artifacts: list  # list[ArtifactRow] — untyped here to avoid a domain->storage import cycle
    base_branch: str
    marker_callback_url: str
    forge_url: str | None = None
    forge_token: str | None = None
    forge_owner: str | None = None
    feature_title: str | None = None
    expects_git_commits: bool = True
    marker_token: str = ""


# The env-injection contract — documented here as the single source of truth a graph
# author's `run:` script reads.
ENV_CHUNK_ID = "BZ_HUB_CHUNK_ID"
ENV_WORKDIR = "BZ_HUB_WORKDIR"
ENV_NODE_ID = "BZ_HUB_NODE_ID"
ENV_NODE_NAME = "BZ_HUB_NODE_NAME"
ENV_EPOCH = "BZ_HUB_EPOCH"
ENV_BASE_BRANCH = "BZ_HUB_BASE_BRANCH"
ENV_GIT_COMMITS = "BZ_HUB_GIT_COMMITS"  # JSON list of {repo, branch, commit}
ENV_ARTIFACT_NAMES = "BZ_HUB_ARTIFACT_NAMES"  # JSON list of already-recorded artifact names for this node
ENV_MARKER_CALLBACK_URL = "BZ_HUB_MARKER_CALLBACK_URL"  # POST {name, content} records a marker mid-run
ENV_MARKER_TOKEN = "BZ_HUB_MARKER_TOKEN"  # the capability token authorizing that POST (issue #230)
ENV_FORGE_URL = "BZ_FORGE_URL"
ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
ENV_FORGE_OWNER = "BZ_FORGE_OWNER"  # qualifies a bare (owner-less) repo, mirroring land_default.qualify_repo
# the prose PR/merge title resolved from the chunk's primary work item, absent when
# it can't be resolved
ENV_FEATURE_TITLE = "BZ_HUB_FEATURE_TITLE"
# "1" when some node in this chunk's graph declares a `git_commit`-kind `produces:`, "0"
# when none does — see `graph_declares_git_commit`.
ENV_EXPECT_GIT_COMMITS = "BZ_HUB_EXPECT_GIT_COMMITS"


def graph_declares_git_commit(graph: Graph) -> bool:
    """Whether any node in ``graph`` declares a ``git_commit``-kind ``produces:``.

    The graph's own statement of intent, and the only thing that tells an empty delivery
    set apart from a failed one."""
    return any(spec.kind is ArtifactKind.GIT_COMMIT for node in graph.nodes for spec in node.produces)


def _delivery_repo(row: ArtifactRow) -> str | None:
    """How delivery addresses one repo: ``owner/name`` read from its origin (see
    :mod:`~blizzard.hub.delivery.repo_ref`), else the bare name for the script's
    configured-owner fallback to qualify."""
    ref = parse_repo_ref(row.forge) if row.forge else None
    return ref.qualified if ref else row.repo


class UnconvergedDeliveryError(RuntimeError):
    """Delivery was handed several distinct branches for one repo.

    Raised rather than tie-broken: merging the first invalidates the mergeability every
    other was checked against."""


def _latest_commit_per_repo(rows: list[ArtifactRow]) -> list[ArtifactRow]:
    """Every ``git_commit`` artifact resolved to one row per **repo**, newest epoch wins.

    Delivery's identity for a git pointer is the repo alone, so a rewritten branch
    supersedes the orphaned one. A tie at one epoch resolves to the later row unless the
    two name different branches, which raises :class:`UnconvergedDeliveryError`."""
    latest: dict[str | None, ArtifactRow] = {}
    for row in rows:
        if row.kind is not ArtifactKind.GIT_COMMIT:
            continue
        current = latest.get(row.repo)
        if current is None or row.epoch > current.epoch:
            latest[row.repo] = row
        elif row.epoch == current.epoch and row.data != current.data:
            raise UnconvergedDeliveryError(
                f"repo {row.repo!r} has two different branches declared at epoch {row.epoch} "
                f"({current.data!r} and {row.data!r}) — the environments' work was never "
                f"rolled up into one branch per repo, so there is no single thing to deliver"
            )
        elif row.epoch == current.epoch:
            latest[row.repo] = row  # identical re-declaration: a correction, not a second unit
    return list(latest.values())


def build_hub_env(inputs: HubEnvInputs) -> dict[str, str]:
    """Assemble a hub command node's injected env — pure, no I/O.

    **Never a model credential** (``bzh:deterministic-shell``): no key injected here may
    grant access to an LLM or agent API. :data:`ENV_MARKER_TOKEN` is a delivery credential
    scoped to this one node visit's marker writes."""
    commits = [
        {
            # Owner-qualified when the declaring repo's origin encodes one, so a chunk
            # spanning two owners addresses each correctly.
            "repo": _delivery_repo(row),
            "branch": row.data.partition(":")[0],
            "commit": row.data.partition(":")[2],
        }
        for row in _latest_commit_per_repo(inputs.artifacts)
    ]
    names = sorted({row.name for row in inputs.artifacts if row.node_id == inputs.node.node_id})
    env = {
        ENV_CHUNK_ID: inputs.chunk.chunk_id,
        ENV_WORKDIR: inputs.workdir,
        ENV_NODE_ID: inputs.node.node_id,
        ENV_NODE_NAME: inputs.node.name,
        ENV_EPOCH: str(inputs.epoch),
        ENV_BASE_BRANCH: inputs.base_branch,
        ENV_GIT_COMMITS: json.dumps(commits),
        ENV_ARTIFACT_NAMES: json.dumps(names),
        ENV_MARKER_CALLBACK_URL: inputs.marker_callback_url,
        ENV_EXPECT_GIT_COMMITS: "1" if inputs.expects_git_commits else "0",
    }
    if inputs.forge_url:
        env[ENV_FORGE_URL] = inputs.forge_url
    if inputs.forge_token:
        env[ENV_FORGE_TOKEN] = inputs.forge_token
    if inputs.forge_owner:
        env[ENV_FORGE_OWNER] = inputs.forge_owner
    if inputs.feature_title:
        env[ENV_FEATURE_TITLE] = inputs.feature_title
    if inputs.marker_token:
        env[ENV_MARKER_TOKEN] = inputs.marker_token
    return env


def _log_name(index: int, step_name: str | None, produces: str | None) -> str:
    return f"hub-log.{step_name or produces or index}"


def poll_interval_for(node: Node) -> timedelta:
    """The cadence a hub command node's pending poll waits between attempts (#66) —
    the node's own override, else :data:`DEFAULT_POLL_INTERVAL`. Pure; exported so a
    caller surfacing "next poll at T" (the chunk-detail read) computes the same value
    the executor gates on."""
    if node.poll_interval_seconds is not None:
        return timedelta(seconds=node.poll_interval_seconds)
    return DEFAULT_POLL_INTERVAL


def poll_timeout_for(node: Node) -> timedelta:
    """The bound a hub command node's pending poll gives up at (#66) — the node's own
    override, else :data:`DEFAULT_POLL_TIMEOUT`. See :func:`poll_interval_for`."""
    if node.poll_timeout_seconds is not None:
        return timedelta(seconds=node.poll_timeout_seconds)
    return DEFAULT_POLL_TIMEOUT


def _printed_choice(stdout: str, known_names: frozenset[str]) -> str | None:
    """The choice a step explicitly selected — its last non-blank stdout line, iff it
    names one of the node's authored choices (#65's outcome-mapping vocabulary) or the
    machinery-reserved ``pending`` outcome (#66), recognized regardless of whether the
    node authors a matching choice — like ``success``/``failure``, it is never an
    authored edge."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    last = lines[-1]
    if last == HUB_PENDING_CHOICE or last in known_names:
        return last
    return None


@dataclass(frozen=True)
class _NoopStep:
    command: str = ""
    name: str | None = None
    produces: str | None = None


class HubNodeExecutor:
    """Runs a generic hub command node's ``run:`` list to completion, once."""

    def __init__(
        self,
        *,
        chunks: IWriteChunkRepository,
        runner: IHubCommandRunner,
        workdir: IHubWorkdir,
        clock: IClock,
        marker_authority: MarkerAuthority,
        base_branch: str = "main",
        marker_callback_base_url: str = "",
        forge_url: str | None = None,
        forge_token: str | None = None,
        forge_owner: str | None = None,
        work_sources: IWorkSourceRegistry | None = None,
        slot_stale_after: timedelta = DEFAULT_SLOT_STALE_AFTER,
    ) -> None:
        self._chunks = chunks
        self._runner = runner
        self._workdir = workdir
        self._clock = clock
        self._marker_authority = marker_authority
        self._base_branch = base_branch
        self._marker_callback_base_url = marker_callback_base_url
        self._forge_url = forge_url
        self._forge_token = forge_token
        self._forge_owner = forge_owner
        self._work_sources = work_sources
        self._slot_stale_after = slot_stale_after

    def record_marker(
        self, chunk_id: str, *, node_id: str, node_name: str, epoch: int, name: str, content: str
    ) -> bool:
        """The mid-run marker callback's write (#65) — a ``run:`` step's own marker,
        recorded ahead of that step's exit. Idempotent per
        ``(chunk, node, name, epoch)``, like the executor's own ``produces:`` write."""
        return self._chunks.record_hub_artifact(
            chunk_id,
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            name=name,
            content=content,
            at=self._clock.now(),
        )

    def run(self, chunk: Chunk, graph: Graph, node: Node, *, epoch: int) -> HubRunResult | None:
        """Execute ``node``'s ``run:`` list once, to completion; ``None`` if deferred.

        Deferred — the slot is held elsewhere, or this visit is pending and not yet due
        (#66) — is neither an error nor a retry-consuming failure. The due check runs
        BEFORE the slot is acquired, so a pending chunk never contends for it."""
        now = self._clock.now()
        facts = self._chunks.load_facts(chunk.chunk_id)
        poll_history = hub_node_poll_history(facts, node_id=node.node_id, epoch=epoch) if facts is not None else []
        if poll_history and now - poll_history[-1].polled_at < poll_interval_for(node):
            return None  # not yet due — never touches the fleet-wide slot
        slot_id = self._chunks.acquire_hub_exec_slot(
            chunk.chunk_id, node_id=node.node_id, at=now, stale_after=self._slot_stale_after
        )
        if slot_id is None:
            return None
        try:
            return self._run_locked(chunk, graph, node, epoch=epoch, poll_history=poll_history)
        finally:
            self._chunks.release_hub_exec_slot(chunk.chunk_id, at=self._clock.now())

    def _run_locked(
        self, chunk: Chunk, graph: Graph, node: Node, *, epoch: int, poll_history: list[HubNodePollFact]
    ) -> HubRunResult:
        if poll_history and self._clock.now() - poll_history[0].polled_at >= poll_timeout_for(node):
            # The bound is elapsed since the FIRST pending attempt of this visit: stop
            # polling, and never run the `run:` list again this call.
            return self._route_pending_timeout(chunk, graph, node, epoch=epoch)
        workdir = self._workdir.ensure(chunk.chunk_id)
        artifacts = self._chunks.load_artifacts(chunk.chunk_id)
        # Minted before the env is built and revoked once this call is done with it, so
        # it is live only for this (chunk, node, epoch) visit (issue #230).
        marker_token = self._marker_authority.issue(chunk.chunk_id, node_id=node.node_id, epoch=epoch)
        try:
            try:
                env = build_hub_env(
                    HubEnvInputs(
                        chunk=chunk,
                        node=node,
                        workdir=workdir,
                        epoch=epoch,
                        artifacts=artifacts,
                        base_branch=self._base_branch,
                        marker_callback_url=self._marker_callback_url(chunk.chunk_id, node.node_id, epoch),
                        forge_url=self._forge_url,
                        forge_token=self._forge_token,
                        forge_owner=self._forge_owner,
                        feature_title=self._resolve_feature_title(chunk),
                        expects_git_commits=graph_declares_git_commit(graph),
                        marker_token=marker_token,
                    )
                )
            except UnconvergedDeliveryError as exc:
                # Routed as a `failure` rather than allowed to escape, which would
                # crash-loop the tick (tests/test_pin_hub_delivery.py).
                self._chunks.record_hub_artifact(
                    chunk.chunk_id,
                    node_id=node.node_id,
                    node_name=node.name,
                    epoch=epoch,
                    name=_log_name(1, "unconverged-delivery", None),
                    content=f"[unconverged delivery]\n{exc}\n",
                    at=self._clock.now(),
                )
                return self._route(chunk, graph, node, epoch=epoch, choice=HUB_DEFAULT_FAILURE_CHOICE, commits=[])

            choice_names = frozenset(c.name for c in node.choices)
            chosen: str | None = None
            for index, step in enumerate(node.run or [_NoopStep()], start=1):
                if step.produces and self._chunks.has_hub_artifact(
                    chunk.chunk_id, node_id=node.node_id, epoch=epoch, name=step.produces
                ):
                    continue  # already done — the at-least-once-per-step skip (#65)

                result = self._runner.run(command=step.command, cwd=workdir, env=env)
                self._chunks.record_hub_artifact(
                    chunk.chunk_id,
                    node_id=node.node_id,
                    node_name=node.name,
                    epoch=epoch,
                    name=_log_name(index, step.name, step.produces),
                    content=f"$ {step.command}\n[exit {result.exit_code}]\n{result.stdout}{result.stderr}",
                    at=self._clock.now(),
                )
                if result.exit_code != 0:
                    chosen = _printed_choice(result.stdout, choice_names) or HUB_DEFAULT_FAILURE_CHOICE
                    break
                printed = _printed_choice(result.stdout, choice_names)
                if printed == HUB_PENDING_CHOICE:
                    # Pending (#66): NOT a step success — no marker, no transition, no
                    # edge lookup; the slot is released on the way out.
                    return self._record_pending(chunk, node, epoch=epoch)
                _CP_HUBNODE_AFTER_STEP_BEFORE_MARKER.reached()
                if step.produces:
                    self._chunks.record_hub_artifact(
                        chunk.chunk_id,
                        node_id=node.node_id,
                        node_name=node.name,
                        epoch=epoch,
                        name=step.produces,
                        content="done",
                        at=self._clock.now(),
                    )
                _CP_HUBNODE_AFTER_MARKER_BEFORE_NEXT.reached()
                if printed:
                    chosen = printed
                    break
            if chosen is None:
                chosen = HUB_DEFAULT_SUCCESS_CHOICE

            commits: list[dict[str, str]] = json.loads(env[ENV_GIT_COMMITS])
            return self._route(chunk, graph, node, epoch=epoch, choice=chosen, commits=commits)
        finally:
            self._marker_authority.revoke(chunk.chunk_id, node_id=node.node_id, epoch=epoch)

    def _record_pending(self, chunk: Chunk, node: Node, *, epoch: int) -> HubRunResult:
        """Record one pending-poll-attempt fact (#66) — no transition, and the slot is
        released immediately after this returns. Consumes no retry and no bounce budget:
        pending is the node waiting on external state, not contention or failure."""
        now = self._clock.now()
        self._chunks.record_hub_node_poll(chunk.chunk_id, node_id=node.node_id, epoch=epoch, at=now)
        _CP_HUBNODE_AFTER_POLL_BEFORE_SLOT_RELEASE.reached()
        next_poll_at = now + poll_interval_for(node)
        return HubRunResult(
            outcome_choice=HUB_PENDING_CHOICE,
            to_node_name=node.name,
            wrote_transition=False,
            detail=f"pending — next poll at {iso_utc(next_poll_at)}",
        )

    def _route_pending_timeout(self, chunk: Chunk, graph: Graph, node: Node, *, epoch: int) -> HubRunResult:
        """A pending node that exceeded its ``poll_timeout`` is a kick-back (#64), not a
        plain failure: record a bounce fact, escalate past the node's ``bounce_cap``,
        else route the ``failure`` edge with the kick-back envelope riding along.
        """
        hub_epoch = epoch + 1
        now = self._clock.now()
        cause = "poll-timeout"
        detail = f"hub node `{node.name}` exceeded its poll_timeout awaiting `{HUB_PENDING_CHOICE}`"
        envelope_payload = json.dumps({"cause": cause, "detail": detail})
        self._chunks.record_bounce(chunk.chunk_id, epoch=hub_epoch, cause=cause, envelope=envelope_payload, at=now)

        facts = self._chunks.load_facts(chunk.chunk_id)
        cap = node.bounce_cap if node.bounce_cap is not None else DEFAULT_BOUNCE_CAP
        if facts is not None and bounces_over_cap(facts, cap):
            # Hub-authored escalation, no runner runtime dir to compose a wrapped
            # takeover command from — leaves wrapped_takeover_command at its store default.
            self._chunks.record_bounce_escalation(
                chunk.chunk_id, epoch=hub_epoch, runner_id=_HUB_RUNNER_ID, takeover_command="", at=now
            )
            return HubRunResult(
                outcome_choice=HUB_DEFAULT_FAILURE_CHOICE,
                to_node_name="",
                wrote_transition=False,
                detail=(
                    f"poll_timeout exceeded — bounce cap ({cap}) crossed after {bounce_count(facts)} bounces, escalated"
                ),
            )
        artifact = ArtifactRow(
            kind=ArtifactKind.ASSET,
            name="bounce-envelope",
            data=envelope_payload,
            repo=None,
            forge=None,
            artifact_id=mint(ARTIFACT_PREFIX, self._clock),
            chunk_id=chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=hub_epoch,
        )
        return self._route(
            chunk, graph, node, epoch=epoch, choice=HUB_DEFAULT_FAILURE_CHOICE, extra_artifacts=[artifact]
        )

    def _route(
        self,
        chunk: Chunk,
        graph: Graph,
        node: Node,
        *,
        epoch: int,
        choice: str,
        extra_artifacts: list[ArtifactRow] | None = None,
        commits: list[dict[str, str]] | None = None,
    ) -> HubRunResult:
        edge = graph.edge_for_choice(node.node_id, choice)
        if edge is None:
            # An authoring gap, not a crash: nothing routes, so the same outcome re-polls
            # forever. Announced once per (node, epoch) rather than once per poll.
            now = self._clock.now()
            detail = f"no authored edge for choice `{choice}` on hub node `{node.name}`"
            authored = sorted(c.name for c in node.choices)
            announced = self._chunks.record_hub_artifact(
                chunk.chunk_id,
                node_id=node.node_id,
                node_name=node.name,
                epoch=epoch,
                name=_UNROUTABLE_ARTIFACT_NAME,
                content=(
                    f"{detail}\n\n"
                    f"authored choices: {', '.join(authored) or '<none>'}\n"
                    "This node will re-poll the same outcome until the graph authors an "
                    "edge for it — nothing else will move it.\n"
                ),
                at=now,
            )
            if announced:
                self._chunks.record_event(
                    severity="error",
                    kind=_EVENT_UNROUTABLE_OUTCOME,
                    runner_id=_HUB_RUNNER_ID,
                    chunk_id=chunk.chunk_id,
                    lease_id=None,
                    node_name=node.name,
                    message=detail,
                    detail={"choice": choice, "epoch": epoch, "authored_choices": authored},
                    at=now,
                )
            return HubRunResult(
                outcome_choice=choice,
                to_node_name="",
                wrote_transition=False,
                detail=detail,
            )
        to_node_id = RESERVED_TERMINAL if edge.to_node_name == RESERVED_TERMINAL else _resolve(graph, edge.to_node_name)
        if to_node_id is None:
            return HubRunResult(
                outcome_choice=choice,
                to_node_name=edge.to_node_name,
                wrote_transition=False,
                detail=f"choice `{choice}` routes to unknown node {edge.to_node_name}",
            )
        hub_epoch = epoch + 1

        # A delivery kick-back (#64), detected from the landed-marker fact and never from
        # the choice name — no outcome name is privileged (#67).
        if commits and to_node_id != RESERVED_TERMINAL:
            pending_repos = {c["repo"] for c in commits}
            landed_now = landed_repos_from_markers(self._chunks.load_artifacts(chunk.chunk_id))
            if not pending_repos.issubset(landed_now):
                now = self._clock.now()
                detail = f"hub node `{node.name}` routed `{choice}` to `{edge.to_node_name}` — delivery incomplete"
                envelope_payload = json.dumps({"cause": choice, "detail": detail})
                self._chunks.record_bounce(
                    chunk.chunk_id, epoch=hub_epoch, cause=choice, envelope=envelope_payload, at=now
                )
                facts = self._chunks.load_facts(chunk.chunk_id)
                cap = node.bounce_cap if node.bounce_cap is not None else DEFAULT_BOUNCE_CAP
                if facts is not None and bounces_over_cap(facts, cap):
                    # Hub-authored escalation, no runner runtime dir to compose a wrapped
                    # takeover command from — leaves wrapped_takeover_command at its store default.
                    self._chunks.record_bounce_escalation(
                        chunk.chunk_id, epoch=hub_epoch, runner_id=_HUB_RUNNER_ID, takeover_command="", at=now
                    )
                    return HubRunResult(
                        outcome_choice=choice,
                        to_node_name="",
                        wrote_transition=False,
                        detail=f"bounce cap ({cap}) crossed after {bounce_count(facts)} bounces, escalated",
                    )
                envelope_artifact = ArtifactRow(
                    kind=ArtifactKind.ASSET,
                    name="bounce-envelope",
                    data=envelope_payload,
                    repo=None,
                    forge=None,
                    artifact_id=mint(ARTIFACT_PREFIX, self._clock),
                    chunk_id=chunk.chunk_id,
                    node_id=node.node_id,
                    node_name=node.name,
                    epoch=hub_epoch,
                )
                extra_artifacts = [*(extra_artifacts or []), envelope_artifact]

        fresh_transition_id = mint(TRANSITION_PREFIX, self._clock)
        wrote = self._chunks.record_hub_step_transition(
            chunk.chunk_id,
            from_node_id=node.node_id,
            to_node_id=to_node_id,
            choice_name=choice,
            epoch=hub_epoch,
            runner_id=_HUB_RUNNER_ID,
            transition_id=fresh_transition_id,
            at=self._clock.now(),
            artifacts=extra_artifacts or [],
            release_route=to_node_id == RESERVED_TERMINAL,
        )
        return HubRunResult(
            outcome_choice=choice,
            to_node_name=edge.to_node_name,
            wrote_transition=wrote,
            transition_id=fresh_transition_id if wrote else None,
        )

    def _resolve_feature_title(self, chunk: Chunk) -> str | None:
        """The chunk's prose feature title (:data:`ENV_FEATURE_TITLE`) — the FIRST
        ``work_ref``'s work item title, best-effort. A read failure or a missing
        registry, pointer, or title degrades to ``None`` rather than breaking delivery."""
        if not chunk.work_refs or self._work_sources is None:
            return None
        pointer = chunk.work_refs[0]
        source = self._work_sources.get(pointer.source)
        if source is None:
            return None
        try:
            title = source.fetch(pointer).title
        except Exception:  # a forge read failing must never break delivery — degrade to no title
            return None
        return title or None

    def _marker_callback_url(self, chunk_id: str, node_id: str, epoch: int) -> str:
        if not self._marker_callback_base_url:
            return ""
        base = self._marker_callback_base_url.rstrip("/")
        return f"{base}/api/chunks/{chunk_id}/hub-markers?node_id={node_id}&epoch={epoch}"


def _resolve(graph: Graph, node_name: str) -> str | None:
    node = graph.node_by_name(node_name)
    return node.node_id if node is not None else None
