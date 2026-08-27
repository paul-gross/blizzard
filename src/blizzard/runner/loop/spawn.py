"""Putting a worker process behind a lease — the fresh spawn, and what a resume re-supplies."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.ids import LEASE_PREFIX, Id
from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.lease_auth import LeaseToken
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import HarnessSpawnError, WorkerPreamble
from blizzard.runner.harness.preamble import Preamble
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.store.repository import EnvBindingRecord, GraphArtifactRecord, LeaseRecord, NewLease
from blizzard.wire.envelope import NodeEnvelope

_log = get_logger("blizzard.runner.loop")

# The lease-mint -> spawn -> record window is the orphan-lease window REAP must absorb.
_CP_AFTER_MINT = crashpoint("spawn.after-lease-mint.before-spawn", "lease minted; worker not spawned")
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
        if not self.ctx.store.local_paused(self.ctx.config.runner_id):
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
        resume_from: str | None = None,
    ) -> None:
        """Mint a fresh-epoch lease and spawn a headless worker for a node-step.

        Always its caller's final statement, with no post-spawn logic after it — that is what
        lets the brake stay a silent ``None`` return no caller can misread as "spawn failed".
        The sole funnel into ``ctx.harness.spawn``, so a re-spawn joins its pool."""
        if self.suppressed(via=via, chunk_id=chunk_id):
            return
        now = self.ctx.clock.now()
        lease = self._mint(chunk_id, envelope, resume_from=resume_from, at=now)
        _CP_AFTER_MINT.reached()
        rendered = self._render(
            chunk_id, lease.lease_id, environments, node_name=envelope.node.node_name, resume_from=resume_from
        )
        try:
            handle = self.ctx.harness.spawn(
                envelope,
                self._worker_preamble(lease, environments, rendered),
                session_hint=str(uuid.uuid4()),
                resume_from=resume_from,
                model=lease.model,
                effort=lease.effort,
                compaction_window=lease.compaction_window,
            )
        except HarnessSpawnError as exc:
            # Surface the launch-time failure (issue #125) then RE-RAISE: no worker started, so
            # the attempt was never recorded and the chunk simply retries next tick.
            OutboundFacts(self.ctx).command_failed(
                chunk_id=chunk_id,
                lease_id=lease.lease_id,
                node_name=envelope.node.node_name,
                command="spawn harness worker",
                stderr_tail=str(exc),
            )
            raise
        self.ctx.store.record_spawn(
            lease.lease_id,
            pid=handle.pid,
            process_start_time=handle.process_start_time,
            session_id=handle.session_id,
            spawned_at=now,
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
        self.ctx.store.record_session_preamble(handle.session_id, fingerprint=rendered.fingerprint, at=now)
        _CP_AFTER_SPAWN.reached()

    def enter_node(
        self, chunk_id: str, envelope: NodeEnvelope, environments: list[AcquiredEnvironment], *, via: str
    ) -> None:
        """Spawn into this node, continuing whatever session its pool resolves to (issue #115)."""
        resume_from = self.ctx.sessions.resume_target(
            chunk_id,
            envelope.node,
            SpawnCwd(self.ctx.config.workspace_root, environments[0].workdir if environments else None).path,
        )
        self.spawn(chunk_id, envelope, environments, via=via, resume_from=resume_from)

    def generation(self, lease_id: str) -> int:
        """The spawn generation this lease's next start is about to mint — one past the
        durably-recorded count, read *before* that start's own ``record_spawn`` lands."""
        return self.ctx.store.lease_generation(lease_id) + 1

    def stdout_path(self, lease_id: str) -> str:
        return self.ctx.worker_files.stdout_path(lease_id, self.generation(lease_id))

    def preamble(self, lease: LeaseRecord, bindings: list[EnvBindingRecord]) -> WorkerPreamble:
        """The per-lease identity a resumed worker needs to reach the runner for its lease.

        A resume inherits none of the spawn env, so the identity is re-supplied. Only the
        token's hash is ever persisted, so the token itself is **re-minted** here."""
        lease_token, token_hash = LeaseToken.mint()
        self.ctx.store.record_lease_token(lease.lease_id, token_hash, self.ctx.clock.now())
        return WorkerPreamble(
            environments=Environments(bindings).acquired,
            lease_id=lease.lease_id,
            local_api_url=self.ctx.config.local_api_url,
            lease_token=lease_token,
        )

    def _mint(self, chunk_id: str, envelope: NodeEnvelope, *, resume_from: str | None, at: datetime) -> MintedLease:
        """Pin the mint's graph artifacts, record the lease, stash its capability-token
        hash, and buffer the hub's fact."""
        # Mint above the max of both floors (bzh:epoch-fencing, #112): the local fence alone is 0
        # for a chunk this runner never drove, so a migrated chunk would mint below hub truth.
        epoch = max(self.ctx.store.latest_epoch(chunk_id), envelope.epoch) + 1
        lease_id = Id.mint(LEASE_PREFIX, self.ctx.clock).value
        node = envelope.node
        retries_max = node.retries_max if node.retries_max is not None else self.ctx.config.default_retries_max
        model, effort, compaction_window = self.ctx.sessions.session_stamps(node, resume_from)
        # Before `record_lease`: a crash here leaves only an orphan row a retry
        # writes again identically — never a lease whose mint's declarations are absent.
        self.ctx.store.record_graph_artifacts(
            graph_id=envelope.graph_id,
            artifacts=[
                GraphArtifactRecord(name=a.name, ordinal=i, kind=a.kind, content=a.content)
                for i, a in enumerate(envelope.graph_artifacts)
            ],
            recorded_at=at,
        )
        self.ctx.store.record_lease(
            NewLease(
                lease_id=lease_id,
                chunk_id=chunk_id,
                graph_id=envelope.graph_id,
                node_id=node.node_id,
                node_name=node.node_name,
                epoch=epoch,
                runner_id=self.ctx.config.runner_id,
                retries_max=retries_max,
                session_name=node.session_name,
                resolved_model=model,
                resolved_effort=effort,
                resolved_compaction_window=compaction_window,
                created_at=at,
            )
        )
        if self.ctx.events is not None:
            self.ctx.events.publish_lease_changed(lease_id, chunk_id, cause="created")
        # A per-lease capability token (issue #113): only its hash is stashed durably, the
        # plaintext carried forward to the spawn preamble alone and never persisted.
        token, token_hash = LeaseToken.mint()
        self.ctx.store.record_lease_token(lease_id, token_hash, at)
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
        resume_from: str | None,
    ) -> Preamble:
        # The store's runtime override when set, else the static config prompt — read here so a
        # replace applies to the next spawn with no restart.
        override = self.ctx.store.workspace_prompt_override(self.ctx.config.workspace_id)
        # The previous turn's node is read from recorded lease rows (blizzard#340): the minted
        # lease has no session row yet, so the newest lease that ran `resume_from` is the prior turn's.
        prior_lease = self.ctx.store.lease_for_session(resume_from) if resume_from else None
        # `prior` is read ONLY when this spawn resumes a session (issue #149), so a fresh one can
        # never elide prose it has never seen; nothing recorded reads `None` and renders in full.
        return Preamble.of(
            runner_prompt=self.ctx.config.runner_prompt,
            workspace_prompt=override if override is not None else self.ctx.config.workspace_prompt,
            environments=environments,
            lease_id=lease_id,
            runner_id=self.ctx.config.runner_id,
            chunk_id=chunk_id,
            prior=self.ctx.store.session_preamble_fingerprint(resume_from) if resume_from else None,
            node=node_name,
            prior_node=prior_lease.node_name if prior_lease else None,
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
