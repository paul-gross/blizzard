"""The generic hub command node executor — THE primitive (#65).

A hub command node (``executor: hub`` + a ``run:`` list) is the engine's one
structurally agentless primitive: the hub itself executes a declared list of shell
commands, serialized fleet-wide, and maps the outcome to an authored edge — the same
fused choice/edge shape a worker node's judgement uses. No agent, no LLM, ever
(``bzh:deterministic-shell``): the run-list is declared, never generated, and the
env this module builds carries no model credential (see :func:`_build_env`).

This module is a **pure step function** of ``(store, clock, seams)``
(``bzh:steppable-loop``): it owns the *policy* — walk ``run:``, skip a step whose
``produces:`` marker already exists, map the exit outcome to a choice, route via
:func:`~blizzard.hub.domain.graph.Graph.edge_for_choice` — behind two owned Protocol
seams for the *mechanism* (``bzh:dependency-inversion``,
``bzh:pluggable-seams``): :class:`~blizzard.hub.delivery.command_runner.IHubCommandRunner`
(subprocess) and :class:`~blizzard.hub.delivery.workdir.IHubWorkdir` (the per-chunk
temp folder). No ``subprocess``/``pathlib``/``httpx`` import lives in this file
(``bzh:domain-core``).

Fleet-wide serialization is a FACT (``hub_exec_slot``, ``bzh:facts-not-status``): one
chunk's hub node runs at a time. :meth:`HubNodeExecutor.run` returns ``None`` — never
raises, never blocks — when the slot is held elsewhere; the caller simply tries again
on a later tick.

The crash contract narrows to **at-least-once per step** (not per script): the only
re-run window is between a step's side effect and its marker record — so the rule a
graph author owns is "each step is safe to re-run" (re-pushing a pushed merge is a
no-op). This module's crash points bracket exactly those windows (see near the top of
this file); its module name is added to ``crash._INSTRUMENTED_MODULES``
(``bzh:crash-point-registry``).

**Pending (#66):** a ``run:`` step signals it by printing the reserved literal
``pending`` on its last stdout line with exit code 0 (a nonzero exit is always a
failure, never pending — there is no separate designated exit code). On pending, the
executor records a poll-attempt fact (never a transition), releases the fleet-wide
slot immediately, and the node is re-run — skipping any step whose ``produces:``
marker already exists, so a graph author's earlier steps must mark themselves done to
avoid re-running on every poll — once ``poll_interval`` has elapsed since the last
attempt. Exceeding ``poll_timeout`` (measured from the *first* recorded pending
attempt for this node visit) stops polling and routes the node's ``failure`` edge
through the same kick-back accounting #64's conflict path uses (a bounce fact, capped
by ``bounce_cap``, escalating past it) — pending itself consumes no retry and no
bounce budget; only the timeout crossing does.
"""

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
# What a hub node writes when its outcome has no authored edge (see `_route`). The
# artifact is both the operator-visible record in chunk detail and the
# once-per-(node, epoch) dedupe key gating the event_log row beside it.
_UNROUTABLE_ARTIFACT_NAME = "hub-unroutable-outcome"
_EVENT_UNROUTABLE_OUTCOME = "hub-node-unroutable-outcome"

# The staleness window a live hub-execution slot is reclaimed after (#65's ``TTL
# against the injected clock`` — never wall time, so tests under a
# :class:`~blizzard.foundation.clock.FixedClock` control it exactly). Generous: a
# genuinely long-running command must not be preempted by a second chunk mid-run;
# only a slot abandoned by a ``kill -9`` (no matching release ever comes) is stale.
DEFAULT_SLOT_STALE_AFTER = timedelta(minutes=30)

# The pending-poll cadence's own defaults (#66) — a node whose author omits
# ``poll_interval``/``poll_timeout`` gets these. Overridable per-node
# (``Node.poll_interval_seconds`` / ``poll_timeout_seconds``), mirroring
# ``DEFAULT_BOUNCE_CAP``'s per-node override shape.
DEFAULT_POLL_INTERVAL = timedelta(seconds=30)
DEFAULT_POLL_TIMEOUT = timedelta(minutes=30)

# Crash points (``bzh:crash-point-registry``) — the generic hub command node's
# per-step windows. Named for the boundary family the reaching scenario opens, per
# convention: a kill inside ``hubnode.after-step.before-marker`` re-runs the just-run
# step on the next hub-advance (the step's own side effect must be safe to redo); a
# kill inside ``hubnode.after-marker.before-next`` leaves that step's marker durable,
# so only the *unmarked* remainder re-runs.
_CP_HUBNODE_AFTER_STEP_BEFORE_MARKER = crashpoint(
    "hubnode.after-step.before-marker", "a run: step exited 0; its produces: marker is not yet durable"
)
_CP_HUBNODE_AFTER_MARKER_BEFORE_NEXT = crashpoint(
    "hubnode.after-marker.before-next", "a run: step's marker is durable; the next step has not started"
)
# The pending-poll window (#66): a kill here leaves the poll-attempt fact durable but
# the fleet-wide slot still live (its release, in :meth:`HubNodeExecutor.run`'s
# ``finally``, never ran) — the same shape a kill inside a ``run:`` step's own command
# would leave, so it resolves the same way: the slot's own staleness TTL
# (``DEFAULT_SLOT_STALE_AFTER``) reclaims it once abandoned, and pending-ness itself is
# derived from the durable poll fact (:func:`~blizzard.hub.domain.work.hub_node_pending`),
# never in-memory — so recovery is "keep polling", not a special recovery path.
_CP_HUBNODE_AFTER_POLL_BEFORE_SLOT_RELEASE = crashpoint(
    "hubnode.after-poll.before-slot-release",
    "the poll-attempt fact is durable; the fleet-wide slot is not yet released",
)


@dataclass(frozen=True)
class HubRunResult:
    """The outcome of one :meth:`HubNodeExecutor.run` call that actually ran.

    ``transition_id`` (issue #213) is the freshly-recorded ``transitions.transition_id``
    when ``wrote_transition`` is true, and ``None`` otherwise (a pending poll, a bounce,
    a bounce-cap escalation) — there is no fresh transition row to key an activity-feed
    frame on."""

    outcome_choice: str
    to_node_name: str
    wrote_transition: bool
    detail: str = ""
    transition_id: str | None = None


@dataclass(frozen=True)
class HubEnvInputs:
    """The already-loaded domain inputs :func:`build_hub_env` assembles into an env.

    Kept as a small named bundle (rather than a long parameter list) so the env
    contract's fields are visible in one place — see :func:`build_hub_env`'s docstring
    for what each key means."""

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
    set apart from a failed one. A code graph promises a commit at ``build``, so reaching
    ``deliver`` with none means something upstream lost it. A non-code graph — a review,
    a spike — promises none and still routes through ``deliver`` as the uniform terminal
    (MVP criterion 10), so an empty landing there is the correct answer, not a defect."""
    return any(spec.kind is ArtifactKind.GIT_COMMIT for node in graph.nodes for spec in node.produces)


def _delivery_repo(row: ArtifactRow) -> str | None:
    """How delivery addresses one repo: ``owner/name`` read from its origin (see
    :mod:`~blizzard.hub.delivery.repo_ref`), else the bare name for the script's
    configured-owner fallback to qualify."""
    ref = parse_repo_ref(row.forge) if row.forge else None
    return ref.qualified if ref else row.repo


class UnconvergedDeliveryError(RuntimeError):
    """Delivery was handed several distinct branches for one repo.

    Raised rather than tie-broken: N branches within one repo share a base, so merging the
    first invalidates the mergeability every other was checked against. Converging belongs
    upstream at `pre-push`; a hub script can only bounce (pinned by
    ``tests/test_hub_command_node.py::test_two_branches_for_one_repo_at_one_epoch_refuse_to_deliver``)."""


def _latest_commit_per_repo(rows: list[ArtifactRow]) -> list[ArtifactRow]:
    """Every ``git_commit`` artifact resolved to one row per **repo**, newest epoch wins.

    Deliberately NOT :func:`~blizzard.hub.domain.envelope.latest_artifacts_by_name`, whose
    ``(node_name, name)`` key is right for the envelope and wrong here: delivery's identity
    for a git pointer is the **repo** alone, so a branch a later node rewrote (a ``pre-push``
    rebase, a ``resolve``) supersedes the orphaned one instead of being delivered alongside
    it (pinned by ``tests/test_hub_command_node.py``'s
    ``test_a_rebased_tip_supersedes_the_pre_rebase_commit_for_the_same_repo``).

    A tie at one epoch resolves to the later row — a re-declaration within a step is a
    correction — unless the two name different branches, which raises
    :class:`UnconvergedDeliveryError` instead.
    """
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

    **Never a model credential** (``bzh:deterministic-shell`` — a hub node is
    structurally agentless): this function injects only the chunk/workdir/node
    identity, the per-repo git pointers the chunk's submitted work carries, the
    forge credential the coordinator already holds today, the mid-run marker
    callback and its authorizing token (:data:`ENV_MARKER_TOKEN` — a **delivery**
    credential scoped to this one node visit's marker writes, not a model
    credential), and (when resolved) the chunk's prose feature title. There is no
    field here, and must never be one, naming an LLM/agent API key.
    """
    commits = [
        {
            # The forge coordinate the declaring repo's own origin encodes, when it
            # encodes one (see `_delivery_repo`) — so a chunk spanning two owners
            # addresses each correctly.
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
        """The mid-run marker callback's write (#65) — a ``run:`` step's own dynamic-loop
        marker, recorded ahead of that step's own exit. Delegates to the same
        idempotent-per-``(chunk, node, name, epoch)`` store method the executor's own
        ``produces:`` marker uses; the controller (``POST /chunks/{id}/hub-markers``)
        calls this rather than touching the write repository directly
        (``bzh:controller-read-only``)."""
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

        Deferred means one of two things, and either way the caller simply tries again
        on a later poll — not an error, not a retry-consuming failure: (a) the fleet-wide slot is held by a different
        chunk right now, or (b) this exact ``(node, epoch)`` visit already recorded a
        ``pending`` outcome and ``poll_interval`` has not yet elapsed since the last
        attempt (#66). Case (b) is checked BEFORE acquiring the slot — a chunk not yet
        due to poll never contends for it, which is the property that makes pending
        never block another chunk's hub node.
        """
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
            # The bound is elapsed since the FIRST recorded pending attempt for this
            # visit — stop polling and kick back via #64 (below), never running the
            # `run:` list again this call.
            return self._route_pending_timeout(chunk, graph, node, epoch=epoch)
        workdir = self._workdir.ensure(chunk.chunk_id)
        artifacts = self._chunks.load_artifacts(chunk.chunk_id)
        # Minted before the env is built, revoked once this call is done with it —
        # scoped to exactly the (chunk, node, epoch) visit whose `run:` steps are about
        # to execute (issue #230). A land script presents this back on its own mid-run
        # marker-write callback (see `blizzard.hub.delivery.marker_auth`).
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
                # A defect in what reached this node, not a fault in running it — route the
                # node's `failure` edge exactly as a non-zero step exit would, rather than
                # letting it escape and crash-loop the tick; the detail lands in an artifact
                # an operator can read (pinned by tests/test_pin_hub_delivery.py::
                # test_an_unconverged_delivery_routes_the_failure_edge_instead_of_escaping_the_tick).
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
                    # Pending (#66): NOT a step success — no marker, no transition, no edge
                    # lookup. Record the poll attempt and hand back to `run()`'s `finally`,
                    # which releases the slot immediately.
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
        """Record one pending-poll-attempt fact (#66) — no transition, slot released
        by the caller's ``finally`` immediately after this returns.

        Consumes no retry and no bounce budget: pending is the node's normal operation
        while it waits on external state, not contention or failure. The crash point
        brackets exactly the window a ``kill -9`` here leaves open — the fact is
        durable, the slot is not yet released — which the slot's own staleness TTL
        reclaims exactly as any other mid-run crash does (no special recovery path;
        pending-ness itself is derived from this fact, so the next poll just resumes).
        """
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
        Pending itself consumed no retry and no bounce budget; only the timeout
        crossing does.
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
            # No authored edge for this outcome — a graph-authoring gap, not a crash;
            # nothing is routed, so this re-polls the identical outcome forever, moving no
            # retry, bounce or escalation budget. Left unannounced it is a silent loop, so
            # announce it once per (node, epoch): the artifact is the operator-visible
            # record in chunk detail AND the dedupe key gating one error-severity event_log
            # row, rather than a row per poll (pinned by
            # tests/test_hub_command_node.py::test_an_unroutable_outcome_is_announced_once_per_epoch).
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

        # A delivery kick-back (#64): this run's outcome is routing to a NON-terminal node
        # while at least one of the repos this node's commits named has not landed a
        # ``merged/<repo>`` marker. Detected by the domain fact
        # (:func:`~blizzard.hub.domain.work.landed_repos_from_markers`), never by choice
        # name — no outcome name is privileged (#67), so a fully-landed continuation into a
        # post-merge node (#63) is forward progress, not contention (pinned by
        # tests/test_hub_command_node.py::test_a_fully_landed_non_terminal_route_records_no_bounce
        # and ::test_a_non_terminal_route_with_nothing_landed_records_a_bounce). Mirrors
        # :meth:`_route_pending_timeout`'s bounce-then-route shape and cap-escalation check.
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
        ``work_ref``'s work item title, best-effort. Never lets a forge-read failure
        (:class:`~blizzard.hub.work_sources.source.WorkSourceError` or otherwise) or a missing
        registry/pointer/title break delivery: any of those degrades to ``None``, which
        :func:`build_hub_env` simply omits — a graph author's ``run:`` script falls
        back to its own ``blizzard: land ...`` default."""
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
