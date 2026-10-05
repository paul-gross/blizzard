"""The loop context — the ``(stores, clock, seam clients)`` a step is a function of.

``bzh:steppable-loop`` requires each phase to be a pure function of its parameters,
reading the clock and every seam from them rather than a module global. This bundle is
that parameter object, plus the loop's static config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer, NoopPlatformTracer
from blizzard.foundation.roles import domain_model
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.environments.worktree import IWorktreeGit
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.capability_snapshot import (
    HarnessVersionCache,
    TickCapabilities,
    capability_snapshot,
)
from blizzard.runner.harness.health_cache import HarnessHealthCache
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import IHarnessRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.runner.hub.chunk_status_cache import IChunkViews
from blizzard.runner.hub.client import IHubClient
from blizzard.runner.leases.worker_stdout import WorkerStdoutFiles
from blizzard.runner.lifecycle.env_release import EnvironmentRelease
from blizzard.runner.lifecycle.judgement.check_runner import ICheckRunner
from blizzard.runner.lifecycle.judgement.elicitation_files import ElicitationFiles
from blizzard.runner.lifecycle.session import HarnessSelector, SessionResolver
from blizzard.runner.loop.retention_floor import RetentionPasses
from blizzard.runner.process.probe import IProcessProbe
from blizzard.runner.process.worker_scratch import WorkerScratchDirs
from blizzard.runner.stores import RunnerStores
from blizzard.runner.subscriptions.credential_renewer import ICredentialRenewer
from blizzard.runner.subscriptions.subscription_sampler import ISubscriptionSampler
from blizzard.runner.usage.recorder import UsageRecorder
from blizzard.wire.runner import RunnerCapability

_log = get_logger("blizzard.runner.loop")

#: The retry budget a node with no ``retries.max`` falls back to — a chosen constant:
#: two execution attempts before escalation to needs-human.
DEFAULT_RETRIES_MAX = 2


@domain_model
@dataclass(frozen=True)
class LoopConfig:
    """The reconciliation loop's static configuration."""

    runner_id: str
    workspace_id: str
    max_agents: int = 1
    base_branch: str = "main"
    #: The runner's configured environment-pool size; ``None`` unreported.
    env_capacity: int | None = None
    #: This runner's own browser-reachable base URL; empty registers no
    #: federation identity.
    public_url: str = ""
    #: The redirect URI(s) this runner presents to the hub's IdP authorize endpoint (#95).
    redirect_uris: tuple[str, ...] = ()
    default_retries_max: int = DEFAULT_RETRIES_MAX
    #: The runner's own local-API base URL, handed to a spawned worker as
    #: ``BLIZZARD_RUNNER_URL`` so its heartbeat hook posts back.
    local_api_url: str = "http://127.0.0.1:8431"
    #: The winter workspace root — the spawn cwd for every worker, so it loads
    #: the workspace's shared context instead of starting below it in an env subdir.
    workspace_root: str = ""
    #: The static workspace prompt from config, resolved once at ``host``
    #: startup — the fallback under the store's runtime override.
    workspace_prompt: str = ""
    #: The operator's override of the baked-in blizzard preamble, resolved
    #: once at ``host`` startup. Empty means unset; there is no runtime override.
    runner_prompt: str = ""
    #: Node NAMES this runner imposes a human gate on — matched across all graphs and read
    #: at context build, so a config edit needs a restart, not just a new tick.
    gates: tuple[str, ...] = ()
    #: The directory the per-lease harness-stdout files live in; empty means
    #: no redirect. A worker's envelope survives the process there for later read-back.
    worker_stdout_dir: str = ""
    #: How long (days) a worker's captured stdout/stderr survive after being written, before
    #: the periodic `Retention` sweep prunes them — unrelated to release, which
    #: leaves them in place.
    worker_stdout_retention_days: int = 14
    #: The per-chunk spend cap; ``None`` means no cap.
    chunk_cap_usd: float | None = None
    #: The runner-wide spend ceiling; ``None`` means no ceiling.
    runner_ceiling_usd: float | None = None
    #: The runner ceiling's rolling window in hours; unused while the ceiling is ``None``.
    runner_ceiling_window_hours: float = 24.0
    #: The session-context warn line; ``None`` disables the lane, reading no transcript at all.
    context_warn_tokens: int | None = None
    #: The context sample step's per-lease cadence in seconds; unused while the lane is off.
    context_sample_interval_seconds: int = 60
    #: This runner's runtime directory (``RunnerConfig.root``), absolute; empty means
    #: unresolved, and readers compose nothing from it rather than guessing.
    runner_dir: str = ""
    #: The directory a detached judgement elicitation's reply file lands in.
    #: Load-bearing, unlike ``worker_stdout_dir`` — always resolved to a real path by
    #: composition, never the empty-disables convention.
    elicitation_output_dir: str = ""
    #: The transcript outbound lane's own switch (``[transcripts] ship``); off
    #: by default — the pump enqueues no delta while this is ``False``.
    transcripts_ship: bool = False
    #: The lane's byte-ceiling overrides (``[transcripts]``); ``None`` keeps
    #: `blizzard.runner.transcripts.caps`'s own defaults, which own the values.
    transcript_record_max_bytes: int | None = None
    transcript_chunk_max_bytes: int | None = None
    #: This runner's selection policy over the peeked ready queue (``[queue] strict``);
    #: off by default reaches past a marked head for the first unmarked
    #: entry, ``True`` holds at a marked head and yields no entry instead.
    queue_strict: bool = False
    #: Platform tracing runs on this runner, so every worker invocation carries its step's trace context.
    platform_tracing: bool = False
    #: ``[tracing] worker_programs`` is on too, so every worker invocation is pointed at the runner's receiver.
    worker_program_tracing: bool = False


@dataclass(frozen=True)
class ResolvedSubscription:
    """One declared subscription with its resolved sampler and renewer bindings — the loop
    step's own view. ``sampler``/``renewer`` are ``None`` for an
    unbound or unknown provider; ``provider`` rides along because the registration push
    reads it, though no view reaches it yet."""

    slug: str
    name: str
    provider: str
    sample_interval_seconds: int
    sampler: ISubscriptionSampler | None
    renewer: ICredentialRenewer | None


class ICloseableUsageHttpClient(Protocol):
    """Owns the shared, lazily-built HTTP client every declared subscription's sampler draws
    from; whoever owns this ``LoopContext``'s lifetime closes it
    exactly once, whether or not a client was ever actually built."""

    def close(self) -> None: ...


class _NoUsageHttpClient:
    """The no-op default for a context built without composing the subscription seam
    (most direct test constructions) — closing it does nothing."""

    def close(self) -> None:
        return None


@dataclass(frozen=True)
class LoopContext:
    """The loop driver's bundle — every step's narrow context is satisfied by it, never module-global."""

    stores: RunnerStores
    clock: IClock
    hub: IHubClient
    #: This tick's (or, standalone, this step's own) chunk-status read seam —
    #: see :mod:`blizzard.runner.hub.chunk_status_cache`.
    chunk_views: IChunkViews
    provider: IWorkspaceProvider
    process: IProcessProbe
    worktree_git: IWorktreeGit
    config: LoopConfig
    worker_files: WorkerStdoutFiles
    elicitation_files: ElicitationFiles
    #: The per-lease scratch directory (`BLIZZARD_TMPDIR`), removed at lease closure.
    worker_scratch: WorkerScratchDirs
    usage: UsageRecorder
    sessions: SessionResolver
    #: The fresh-mint owner selector over the acceptable harness set; a resume never reaches it.
    harness_selector: HarnessSelector
    env_release: EnvironmentRelease
    #: Required; every recorded session's owner resolves through it, with no single-harness fallback.
    harnesses: IHarnessRegistry
    #: The check-runner seam — ``None`` when not wired, so a node with no
    #: ``checks:`` still ticks; a node that declares ``checks:`` needs it.
    check_runner: ICheckRunner | None = None
    #: Coarse "any transcript source wired" flag; a read still resolves per-owner via ``transcript_source_for``.
    transcripts_wired: bool = False
    #: The SSE publish seam, typed against the Protocol
    #: (``bzh:dependency-inversion``); ``None`` on ``blizzard runner tick``, a no-op there.
    events: IRunnerEventPublisher | None = None
    #: Every declared provider subscription, resolved — each sampled when
    #: due against its own ``sample_interval_seconds`` and ``slug``-keyed anchor.
    subscriptions: tuple[ResolvedSubscription, ...] = ()
    #: The shared subscription-sampling HTTP client's owner — see
    #: :class:`ICloseableUsageHttpClient`.
    usage_http_client: ICloseableUsageHttpClient = field(default_factory=_NoUsageHttpClient)
    #: Opens the tick's root and step spans — the no-op on a standalone ``blizzard runner tick``.
    tracer: IPlatformTracer = field(default_factory=NoopPlatformTracer)
    #: This tick's capability memo (``tick()`` wires it); ``None`` rebuilds it per read.
    capabilities: TickCapabilities | None = None
    #: The loop's own cross-tick harness-version cache — unlike ``capabilities`` above, never rebound per tick.
    harness_versions: HarnessVersionCache | None = None
    #: The loop's own cross-tick harness-health cache — mirrors ``harness_versions`` above.
    harness_health: HarnessHealthCache | None = None
    #: The loop's own cross-tick retention-pass floor — mirrors ``harness_versions`` above.
    #: ``None`` (a one-shot tick) has no memory to gate on, so every pass runs.
    retention_passes: RetentionPasses | None = None

    def capability_snapshot(self) -> tuple[RunnerCapability, ...]:
        """This runner's capabilities as the registration push and the matched fleet peek
        both carry them — served from this tick's memo when ``tick()`` wired one, since
        building one probes every bound harness binary."""
        if self.capabilities is not None:
            return self.capabilities.get()
        return capability_snapshot(self.harnesses, self.harness_versions, self.harness_health)

    def transcript_source_for(self, session: SessionReference) -> IHarnessTranscriptSource:
        """Resolve an existing session's transcript source from its recorded owner — same
        raise/guard contract as the registry's role accessors."""
        return self.harnesses.transcript_source(session.harness_id)

    def resolve_boundary_start(self, session: SessionReference, workdir: str | None) -> tuple[str | None, bool]:
        """``(start_position, start_unreadable)`` for a boundary about to open on ``session`` —
        the current transcript tail, or ``(None, True)`` when the source is unresolvable or the
        read fails; never conflate that with ``(None, False)``, a fresh session's own beginning
        sentinel. Shared by every resume/judge/nudge boundary opener, so a
        transient read failure on any of them is durably distinguishable from a fresh spawn."""
        spawn_cwd = SpawnCwd(self.config.workspace_root, workdir).path
        try:
            source = self.transcript_source_for(session)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            _log.info(
                "invocation boundary tail read blocked by unavailable harness transcript source",
                harness_id=session.harness_id,
                detail=str(exc),
            )
            return None, True
        position = source.tail_position(session.session_id, spawn_cwd=spawn_cwd)
        if position is None:
            return None, True
        return position.token, False


if TYPE_CHECKING:
    from blizzard.runner.hub.outbound import OutboundContext
    from blizzard.runner.lifecycle.attempt import AttemptContext
    from blizzard.runner.lifecycle.claim import ClaimContext
    from blizzard.runner.lifecycle.dormant import DormantContext
    from blizzard.runner.lifecycle.drain import DrainContext
    from blizzard.runner.lifecycle.held_chunk import HeldChunkContext
    from blizzard.runner.lifecycle.judgement.git_commits import GitCommitsContext
    from blizzard.runner.lifecycle.judgement.judgement import JudgementContext
    from blizzard.runner.lifecycle.spawn import SpawnContext
    from blizzard.runner.lifecycle.usage_limit import UsageLimitContext
    from blizzard.runner.throttle.overload import OverloadContext
    from blizzard.runner.transcripts.transcript_backfill import TranscriptBackfillContext
    from blizzard.runner.transcripts.transcript_drain import TranscriptDrainContext
    from blizzard.runner.transcripts.transcript_pump import TranscriptPumpContext

    def _conforms_to_outbound(ctx: LoopContext) -> OutboundContext:
        return ctx

    def _conforms_to_transcript_pump(ctx: LoopContext) -> TranscriptPumpContext:
        return ctx

    def _conforms_to_transcript_drain(ctx: LoopContext) -> TranscriptDrainContext:
        return ctx

    def _conforms_to_transcript_backfill(ctx: LoopContext) -> TranscriptBackfillContext:
        return ctx

    def _conforms_to_git_commits(ctx: LoopContext) -> GitCommitsContext:
        return ctx

    def _conforms_to_overload(ctx: LoopContext) -> OverloadContext:
        return ctx

    def _conforms_to_spawn(ctx: LoopContext) -> SpawnContext:
        return ctx

    def _conforms_to_attempt(ctx: LoopContext) -> AttemptContext:
        return ctx

    def _conforms_to_held_chunk(ctx: LoopContext) -> HeldChunkContext:
        return ctx

    def _conforms_to_drain(ctx: LoopContext) -> DrainContext:
        return ctx

    def _conforms_to_claim(ctx: LoopContext) -> ClaimContext:
        return ctx

    def _conforms_to_usage_limit(ctx: LoopContext) -> UsageLimitContext:
        return ctx

    def _conforms_to_dormant(ctx: LoopContext) -> DormantContext:
        return ctx

    def _conforms_to_judgement(ctx: LoopContext) -> JudgementContext:
        return ctx
