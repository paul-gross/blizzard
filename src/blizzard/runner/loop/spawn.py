"""Putting a worker process behind a lease — the fresh spawn, and what a resume re-supplies."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.ids import LEASE_PREFIX, Id
from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.artifacts import GraphArtifactRecord
from blizzard.runner.domain.invocation_boundaries import InvocationBoundaryKind
from blizzard.runner.domain.lease_auth import LeaseToken
from blizzard.runner.domain.leases import (
    LeaseRecord,
    NewLease,
)
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.environments.repository import EnvBindingRecord
from blizzard.runner.harness.adapter import (
    DEFAULT_IDENTITY_AWAIT_TIMEOUT_SECONDS,
    HarnessSpawnError,
    IHarnessLifecycleAndVerdict,
    WorkerIdentityError,
    WorkerPreamble,
)
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.preamble import Preamble
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.loop.capability_snapshot import default_harness_id
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.loop.session import ResumedSession, SkippedHarness
from blizzard.wire.envelope import NodeEnvelope

_log = get_logger("blizzard.runner.loop")

# The lease-mint -> spawn -> record window is the orphan-lease window REAP must absorb.
_CP_AFTER_MINT = crashpoint("spawn.after-lease-mint.before-spawn", "lease minted; worker not spawned")
# The transcript invocation boundary (blizzard#437 D6): a genuinely new pre-launch write.
_CP_AFTER_BOUNDARY = crashpoint(
    "spawn.after-boundary-record.before-spawn", "spawn invocation boundary durable; worker not yet launched"
)
# The two-phase spawn's three windows (D1/D2), each bracketing a durable write.
_CP_AFTER_LAUNCH = crashpoint(
    "spawn.after-launch.before-provisional-record", "worker process launched; provisional ownership not yet durable"
)
_CP_AFTER_PROVISIONAL = crashpoint(
    "spawn.after-provisional-record.before-identity", "provisional ownership durable; identity not yet known"
)
_CP_AFTER_IDENTITY = crashpoint(
    "spawn.after-identity.before-session-record", "identity known; authoritative session not yet durable"
)
_CP_AFTER_SPAWN = crashpoint("spawn.after-spawn", "worker spawned; pid recorded")


@dataclass(frozen=True)
class Environments:
    """A chunk's held env bindings, as the spawn primitives want them."""

    bindings: list[EnvBindingRecord]

    @property
    def acquired(self) -> list[AcquiredEnvironment]:
        return [AcquiredEnvironment(environment_id=b.environment_id, workdir=b.workdir) for b in self.bindings]


@dataclass(frozen=True)
class MintedLease:
    """A lease recorded and announced to the hub, with no worker behind it yet."""

    lease_id: str
    epoch: int
    token: str
    model: str | None
    effort: str | None
    compaction_window: str | None


@dataclass(frozen=True)
class Spawner:
    """Every path that puts a worker process behind a lease: the fresh, fresh-epoch spawn, and
    the per-lease identity a resume or a judgement re-supplies.

    The local-pause brake (issue #45) is checked here, before any mutation — so a suppressed
    start writes no fact, kills no pid and mints no lease."""

    ctx: LoopContext

    def suppressed(self, *, via: str, chunk_id: str, lease_id: str | None = None) -> bool:
        """True — and logged once — when the runner's own brake blocks this start.

        Reads ``local_paused`` only. Which call sites must consult it is held mechanically by
        ``tests/test_spawn_suppressed_registry.py``, not by an enumeration to recount by hand."""
        if not self.ctx.stores.pause.local_paused(self.ctx.config.runner_id):
            return False
        _log.info(
            "spawn suppressed — locally paused",
            runner_id=self.ctx.config.runner_id,
            via=via,
            chunk_id=chunk_id,
            lease_id=lease_id,
        )
        return True

    def spawn(
        self,
        chunk_id: str,
        envelope: NodeEnvelope,
        environments: list[AcquiredEnvironment],
        *,
        via: str,
        resume_from: SessionReference | None = None,
        harness_id: str | None = None,
    ) -> None:
        """Mint a fresh-epoch lease and spawn a headless worker for a node-step.

        Always its caller's final statement, with no post-spawn logic after it — that is what
        lets the brake stay a silent ``None`` return no caller can misread as "spawn failed".
        The sole funnel into the resolved owner's ``.spawn``, so a re-spawn joins its pool."""
        if self.suppressed(via=via, chunk_id=chunk_id):
            return
        # A pool rotation's replacement or a retry (`Attempt.requeue`) mints under its own prior
        # owner — neither a selection candidate; only fresh work reaches the selector or its no-set fallback.
        if resume_from is not None:
            owner = resume_from.harness_id
        elif harness_id is not None:
            owner = harness_id
        elif envelope.node.session_harnesses:
            selection = self.ctx.harness_selector.select(envelope.node)
            if selection.harness_id is None:
                self.escalate_no_acceptable_harness(
                    chunk_id,
                    envelope,
                    attempted=envelope.node.session_harnesses,
                    skipped=selection.skipped,
                    via=via,
                )
                return
            owner = selection.harness_id
        else:
            # Same registry-order default `capability_snapshot` advertises (blizzard#433);
            # `None` (a no-bindings registry, D10) resolves below like any unresolvable owner.
            owner = default_harness_id(self.ctx.harnesses) or ""
        # Resolve before minting: an owner this runner cannot serve must block its resume,
        # not leave a lease another harness could later adopt. Logged, not raised, so this blocks only this spawn.
        harness = self._resolve_harness(owner, via=via)
        if harness is None:
            return
        now = self.ctx.clock.now()
        resumed = self.ctx.sessions.resumption(resume_from)
        lease = self._mint(chunk_id, envelope, resume=resumed, harness_id=owner, at=now)
        _CP_AFTER_MINT.reached()
        rendered = self._render(
            chunk_id,
            lease.lease_id,
            environments,
            node_name=envelope.node.node_name,
            resume=resumed,
        )
        # The invocation boundary (D6/D11): a fresh spawn opens on the beginning sentinel; a
        # `resume_from` continuation of an EXISTING session is a `resume`, reading its tail (F1).
        kind: InvocationBoundaryKind
        if resume_from is not None:
            kind = "resume"
            workdir = environments[0].workdir if environments else None
            start_position, start_unreadable = self.ctx.resolve_boundary_start(resume_from, workdir)
        else:
            kind, start_position, start_unreadable = "spawn", None, False
        self.ctx.stores.invocation_boundaries.record_boundary_open(
            lease_id=lease.lease_id,
            chunk_id=chunk_id,
            node_id=envelope.node.node_id,
            epoch=lease.epoch,
            generation=self.generation(lease.lease_id),
            kind=kind,
            start_position=start_position,
            start_unreadable=start_unreadable,
            opened_at=now,
        )
        _CP_AFTER_BOUNDARY.reached()
        # Observed before the spawn: a hang or raise here costs only this generation's
        # `harness_version` observation, never runs after the worker is already live and unrecorded.
        version = harness.observe_version()
        try:
            pending = harness.spawn(
                envelope,
                self._worker_preamble(lease, environments, rendered),
                session_hint=str(uuid.uuid4()),
                resume_from=resume_from.session_id if resume_from is not None else None,
                model=lease.model,
                effort=lease.effort,
                compaction_window=lease.compaction_window,
            )
        except HarnessSpawnError as exc:
            # Surface the launch-time failure (issue #125) then RE-RAISE: nothing was ever
            # launched, but the lease minted above is durable — REAP reaps it (a retry), below.
            OutboundFacts(self.ctx).command_failed(
                chunk_id=chunk_id,
                lease_id=lease.lease_id,
                node_name=envelope.node.node_name,
                command="spawn harness worker",
                stderr_tail=str(exc),
            )
            raise
        _CP_AFTER_LAUNCH.reached()  # the process exists; nothing about it is durable yet
        try:
            # Phase one (D1/D2): durable BEFORE identity is awaited, so a crash anywhere past
            # this point leaves a real process's ownership recoverable rather than invisible.
            self.ctx.stores.liveness.record_provisional_spawn(
                lease.lease_id,
                pid=pending.pid,
                process_start_time=pending.process_start_time,
                pgid=pending.pgid,
                spawned_at=now,
                harness_id=owner,
            )
        except Exception:
            # F1: unlike an OS crash, a plain raise here never disarms the trampoline on its
            # own — kill it explicitly instead.
            self.ctx.process.kill_group(pending.pgid)
            raise
        _CP_AFTER_PROVISIONAL.reached()  # ownership durable; identity not yet known
        # F1: disarmed only now — a crash before this line still kills the worker outright.
        pending.confirm_durable()
        # F8: still blocks the tick pass on one lease — unlike `judge()`'s already-identified,
        # pollable-later wait, there is no durable record shape yet for this one; bound kept short instead.
        try:
            handle = pending.await_identity(DEFAULT_IDENTITY_AWAIT_TIMEOUT_SECONDS)
        except WorkerIdentityError as exc:
            # A real, durably-provisional process (D1/D2) — kill it and mark it unidentified;
            # the lease stays OPEN until REAP's sweep closes it via `Attempt.fail` (a retry).
            self.ctx.process.kill_group(pending.pgid)
            self.ctx.stores.liveness.record_identity_failed(lease.lease_id, at=self.ctx.clock.now())
            OutboundFacts(self.ctx).command_failed(
                chunk_id=chunk_id,
                lease_id=lease.lease_id,
                node_name=envelope.node.node_name,
                command="await worker identity",
                stderr_tail=str(exc),
            )
            raise HarnessSpawnError(str(exc)) from exc
        _CP_AFTER_IDENTITY.reached()  # identity known; not yet the authoritative record
        spawned_session = SessionReference(owner, handle.session_id)
        self.ctx.stores.liveness.record_identified_spawn(
            lease.lease_id,
            session=spawned_session,
            identified_at=self.ctx.clock.now(),
            harness_version=version,
        )
        if self.ctx.events is not None:
            # The 'created' mint alone leaves `spawning` -> `running` unannounced (D4).
            self.ctx.events.publish_lease_changed(
                lease.lease_id,
                chunk_id,
                cause="spawned",
            )
        # Keyed on the HANDLE's session id — the authoritative continuation id (issue #149).
        # Written after the spawn, so a durable fingerprint always implies the prose was sent.
        self.ctx.stores.session.record_session_preamble(spawned_session, fingerprint=rendered.fingerprint, at=now)
        _CP_AFTER_SPAWN.reached()

    def enter_node(
        self, chunk_id: str, envelope: NodeEnvelope, environments: list[AcquiredEnvironment], *, via: str
    ) -> None:
        """Spawn into this node, continuing whatever session it resolves to (issue #115) — a
        named pool's head, or a plain resume's own latest session. Either shape's owner failing
        to resolve right now escalates the chunk in place instead of spawning — an operation on
        an existing recorded session no other runner can resume, so nothing here loops a
        blocked mint forever or substitutes a different harness for it."""
        spawn_cwd = SpawnCwd(self.ctx.config.workspace_root, environments[0].workdir if environments else None).path
        resume = self.ctx.sessions.resolve_resume(chunk_id, envelope.node, spawn_cwd)
        if resume.owner_unresolvable is not None:
            session, exc = resume.owner_unresolvable
            self._escalate_unresolvable_resume_owner(chunk_id, envelope, session, exc, via=via)
            return
        self.spawn(chunk_id, envelope, environments, via=via, resume_from=resume.session, harness_id=resume.pool_owner)

    def _escalate_unresolvable_resume_owner(
        self,
        chunk_id: str,
        envelope: NodeEnvelope,
        session: SessionReference,
        exc: UnknownHarnessError | UnavailableHarnessError,
        *,
        via: str,
    ) -> None:
        """An existing session's owner — a named pool's head, or a plain resume's own latest
        session — cannot be dispatched to right now. Mints a zero-budget, never-spawned lease
        purely to give ``Attempt.escalate_owner_unresolvable`` an existing lease to close; see
        `blizzard-context:/architecture/crash-correctness/runner.md`."""
        if self.suppressed(via=via, chunk_id=chunk_id):
            return
        if self.ctx.stores.escalations.open_escalation_for_chunk(chunk_id) is not None:
            return  # already escalated — nothing here supersedes it
        now = self.ctx.clock.now()
        resumed = self.ctx.sessions.resumption(session)
        minted = self._mint(chunk_id, envelope, resume=resumed, harness_id=session.harness_id, at=now, retries_max=0)
        _CP_AFTER_MINT.reached()
        lease = self.ctx.stores.lease_record.active_lease(minted.lease_id)
        assert lease is not None  # just minted above, and nothing else has touched it yet
        # Deferred: `blizzard.runner.loop.attempt` imports `Spawner` at module scope, so
        # importing `Attempt` back at module scope here would cycle.
        from blizzard.runner.loop.attempt import Attempt

        Attempt(self.ctx, lease).escalate_owner_unresolvable(session=session, exc=exc, via="resume-owner")

    def escalate_no_acceptable_harness(
        self,
        chunk_id: str,
        envelope: NodeEnvelope,
        *,
        attempted: Sequence[str],
        skipped: Sequence[SkippedHarness],
        via: str,
    ) -> None:
        """No member of the envelope's acceptable harness set could serve this mint: escalate
        the chunk in place rather than mint under the runner default or loop blocked forever.
        Mints an owner-less, never-spawned lease purely to give
        ``Attempt.escalate_no_acceptable_harness`` an existing lease to close — reached alike
        from a fresh mint's exhausted selection and a retry whose recorded owner fell out of the set."""
        if self.suppressed(via=via, chunk_id=chunk_id):
            return
        if self.ctx.stores.escalations.open_escalation_for_chunk(chunk_id) is not None:
            return  # already escalated — nothing here supersedes it
        now = self.ctx.clock.now()
        minted = self._mint(chunk_id, envelope, resume=None, harness_id=None, at=now, retries_max=0)
        _CP_AFTER_MINT.reached()
        lease = self.ctx.stores.lease_record.active_lease(minted.lease_id)
        assert lease is not None  # just minted above, and nothing else has touched it yet
        # Deferred: `blizzard.runner.loop.attempt` imports `Spawner` at module scope, so
        # importing `Attempt` back at module scope here would cycle.
        from blizzard.runner.loop.attempt import Attempt

        Attempt(self.ctx, lease).escalate_no_acceptable_harness(attempted=attempted, skipped=skipped, via=via)

    def _resolve_harness(self, harness_id: str, *, via: str) -> IHarnessLifecycleAndVerdict | None:
        """Resolve ``harness_id``, logging and returning ``None`` — never raising — when it
        is unknown or unavailable. Every owner reaching here was already confirmed resolvable
        moments earlier this same call; what still fails here is only a same-tick race or a
        fresh mint's own default."""
        try:
            return self.ctx.harnesses.adapter(harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            _log.error(
                "spawn blocked by unavailable harness owner",
                via=via,
                harness_id=harness_id,
                detail=str(exc),
            )
            return None

    def generation(self, lease_id: str) -> int:
        """The spawn generation this lease's next start is about to mint — one past the
        durably-recorded count, read *before* that start's own ``record_spawn`` lands."""
        return self.ctx.stores.liveness.lease_generation(lease_id) + 1

    def stdout_path(self, lease_id: str) -> str:
        return self.ctx.worker_files.stdout_path(lease_id, self.generation(lease_id))

    def preamble(self, lease: LeaseRecord, bindings: list[EnvBindingRecord]) -> WorkerPreamble:
        """The per-lease identity a resumed worker needs to reach the runner for its lease.

        A resume inherits none of the spawn env, so the identity is re-supplied. Only the
        token's hash is ever persisted, so the token itself is **re-minted** here."""
        lease_token, token_hash = LeaseToken.mint()
        self.ctx.stores.tokens.record_lease_token(lease.lease_id, token_hash, self.ctx.clock.now())
        return WorkerPreamble(
            environments=Environments(bindings).acquired,
            lease_id=lease.lease_id,
            local_api_url=self.ctx.config.local_api_url,
            lease_token=lease_token,
        )

    def _mint(
        self,
        chunk_id: str,
        envelope: NodeEnvelope,
        *,
        resume: ResumedSession | None,
        harness_id: str | None,
        at: datetime,
        retries_max: int | None = None,
    ) -> MintedLease:
        """Pin the mint's graph artifacts, record the lease, stash its capability-token hash, and
        buffer the hub's fact. ``retries_max`` overrides the node's own declared budget when given
        — used only by the two zero-budget, never-spawned escalation mints; every ordinary caller
        leaves it unset. ``harness_id=None`` is the escalation-only mode: no member of the
        acceptable set resolved, so it resolves no stamps and records no mint owner."""
        # Mint above the max of both floors (bzh:epoch-fencing, #112): the local fence alone is 0
        # for a chunk this runner never drove, so a migrated chunk would mint below hub truth.
        epoch = max(self.ctx.stores.lease_record.latest_epoch(chunk_id), envelope.epoch) + 1
        lease_id = Id.mint(LEASE_PREFIX, self.ctx.clock).value
        node = envelope.node
        resolved_retries_max = (
            retries_max
            if retries_max is not None
            else (node.retries_max if node.retries_max is not None else self.ctx.config.default_retries_max)
        )
        model, effort, compaction_window = (
            self.ctx.sessions.session_stamps(node, resume, harness_id=harness_id)
            if harness_id is not None
            else (None, None, None)
        )
        # Before `record_lease`: a crash here leaves only an orphan row a retry
        # writes again identically — never a lease whose mint's declarations are absent.
        self.ctx.stores.graph_artifacts.record_graph_artifacts(
            graph_id=envelope.graph_id,
            artifacts=[
                GraphArtifactRecord(name=a.name, ordinal=i, kind=a.kind, content=a.content)
                for i, a in enumerate(envelope.graph_artifacts)
            ],
            recorded_at=at,
        )
        self.ctx.stores.lease_record.record_lease(
            NewLease(
                lease_id=lease_id,
                chunk_id=chunk_id,
                graph_id=envelope.graph_id,
                node_id=node.node_id,
                node_name=node.node_name,
                epoch=epoch,
                runner_id=self.ctx.config.runner_id,
                retries_max=resolved_retries_max,
                session_name=node.session_name,
                resolved_model=model,
                resolved_effort=effort,
                resolved_compaction_window=compaction_window,
                created_at=at,
            )
        )
        # Stamped before spawn ever runs: a crash or launch failure between here and spawn-return
        # still leaves the mint's owner durable for a retry — skipped for an escalation-only mint.
        if harness_id is not None:
            self.ctx.stores.session.record_mint_owner(lease_id, harness_id)
        if self.ctx.events is not None:
            self.ctx.events.publish_lease_changed(lease_id, chunk_id, cause="created")
        # A per-lease capability token (issue #113): only its hash is stashed durably, the
        # plaintext carried forward to the spawn preamble alone and never persisted.
        token, token_hash = LeaseToken.mint()
        self.ctx.stores.tokens.record_lease_token(lease_id, token_hash, at)
        OutboundFacts(self.ctx).lease_minted(chunk_id, lease_id, epoch=epoch, at=at)
        return MintedLease(
            lease_id=lease_id, epoch=epoch, token=token, model=model, effort=effort, compaction_window=compaction_window
        )

    def _render(
        self,
        chunk_id: str,
        lease_id: str,
        environments: list[AcquiredEnvironment],
        *,
        node_name: str,
        resume: ResumedSession | None,
    ) -> Preamble:
        # The store's runtime override when set, else the static config prompt — read here so a
        # replace applies to the next spawn with no restart.
        override = self.ctx.stores.workspace_prompt.workspace_prompt_override(self.ctx.config.workspace_id)
        # `prior` is read ONLY when this spawn resumes a session (issue #149), so a fresh one can
        # never elide prose it has never seen; nothing recorded reads `None` and renders in full.
        return Preamble.of(
            runner_prompt=self.ctx.config.runner_prompt,
            workspace_prompt=override if override is not None else self.ctx.config.workspace_prompt,
            environments=environments,
            lease_id=lease_id,
            runner_id=self.ctx.config.runner_id,
            chunk_id=chunk_id,
            prior=self.ctx.stores.session.session_preamble_fingerprint(resume.session) if resume else None,
            node=node_name,
            prior_node=resume.lease.node_name if resume and resume.lease else None,
        )

    def _worker_preamble(
        self, lease: MintedLease, environments: list[AcquiredEnvironment], rendered: Preamble
    ) -> WorkerPreamble:
        generation = self.generation(lease.lease_id)
        return WorkerPreamble(
            environments=environments,
            lease_id=lease.lease_id,
            local_api_url=self.ctx.config.local_api_url,
            workspace_root=self.ctx.config.workspace_root,
            prompt_prefix=rendered.text,
            stdout_path=self.ctx.worker_files.stdout_path(lease.lease_id, generation),
            stderr_path=self.ctx.worker_files.stderr_path(lease.lease_id, generation),
            lease_token=lease.token,
        )
