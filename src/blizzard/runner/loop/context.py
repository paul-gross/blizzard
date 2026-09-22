"""The loop context — the ``(stores, clock, seam clients)`` a step is a function of.

``bzh:steppable-loop`` requires each phase to be a pure function of its parameters,
reading the clock and every seam from them rather than a module global. This bundle is
that parameter object, plus the loop's static config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.adapter import IHarnessLifecycleAndVerdict
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import IHarnessRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.runner.loop.capability_snapshot import (
    HarnessHealthCache,
    HarnessVersionCache,
    TickCapabilities,
    capability_snapshot,
)
from blizzard.runner.loop.checks import ICheckRunner
from blizzard.runner.loop.chunk_status_cache import IChunkViews
from blizzard.runner.loop.elicitation_files import ElicitationFiles
from blizzard.runner.loop.env_release import EnvironmentRelease
from blizzard.runner.loop.hub import IHubClient
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.loop.session import HarnessSelector, SessionResolver
from blizzard.runner.loop.usage import UsageRecorder
from blizzard.runner.loop.worker_stdout import WorkerStdoutFiles
from blizzard.runner.loop.worktree import IWorktreeGit
from blizzard.runner.stores import RunnerStores
from blizzard.runner.subscriptions.credential_renewer import ICredentialRenewer
from blizzard.runner.subscriptions.subscription_sampler import ISubscriptionSampler
from blizzard.wire.runner import RunnerCapability

_log = get_logger("blizzard.runner.loop")

#: The retry budget a node with no ``retries.max`` falls back to — a chosen constant:
#: two execution attempts before escalation to needs-human.
DEFAULT_RETRIES_MAX = 2


@dataclass(frozen=True)
class LoopConfig:
    """The reconciliation loop's static configuration."""

    runner_id: str
    workspace_id: str
    max_agents: int = 1
    base_branch: str = "main"
    #: The runner's configured environment-pool size (issue #69); ``None`` unreported.
    env_capacity: int | None = None
    #: This runner's own browser-reachable base URL (issue #95); empty registers no
    #: federation identity.
    public_url: str = ""
    #: The redirect URI(s) this runner presents to the hub's IdP authorize endpoint (#95).
    redirect_uris: tuple[str, ...] = ()
    default_retries_max: int = DEFAULT_RETRIES_MAX
    #: The runner's own local-API base URL, handed to a spawned worker as
    #: ``BLIZZARD_RUNNER_URL`` so its heartbeat hook posts back.
    local_api_url: str = "http://127.0.0.1:8431"
    #: The winter workspace root — the spawn cwd for every worker (issue #17), so it loads
    #: the workspace's shared context instead of starting below it in an env subdir.
    workspace_root: str = ""
    #: The static workspace prompt from config (issue #17), resolved once at ``host``
    #: startup — the fallback under the store's runtime override.
    workspace_prompt: str = ""
    #: The operator's override of the baked-in blizzard preamble (issue #103), resolved
    #: once at ``host`` startup. Empty means unset; there is no runtime override.
    runner_prompt: str = ""
    #: Node NAMES this runner imposes a human gate on — matched across all graphs and read
    #: at context build, so a config edit needs a restart, not just a new tick.
    gates: tuple[str, ...] = ()
    #: The directory the per-lease harness-stdout files live in (issue #58); empty means
    #: no redirect. A worker's envelope survives the process there for later read-back.
    worker_stdout_dir: str = ""
    #: How long (days) a worker's captured stdout/stderr survive after being written, before
    #: the periodic `Retention` sweep prunes them (issue #58) — unrelated to release, which
    #: leaves them in place.
    worker_stdout_retention_days: int = 14
    #: The per-chunk spend cap (issue #61a); ``None`` means no cap.
    chunk_cap_usd: float | None = None
    #: The runner-wide spend ceiling (issue #61b); ``None`` means no ceiling.
    runner_ceiling_usd: float | None = None
    #: The runner ceiling's rolling window in hours; unused while the ceiling is ``None``.
    runner_ceiling_window_hours: float = 24.0
    #: The session-context warn line; ``None`` disables the lane, reading no transcript at all.
    context_warn_tokens: int | None = None
    #: The context sample step's per-lease cadence in seconds; unused while the lane is off.
    context_sample_interval_seconds: int = 60
    #: This runner's runtime directory (``RunnerConfig.root``), absolute; empty means
    #: unresolved, and readers compose nothing from it rather than guessing (issue #251).
    runner_dir: str = ""
    #: The directory a detached judgement elicitation's reply file lands in (blizzard#443,
    #: D4). Load-bearing, unlike ``worker_stdout_dir`` — always resolved to a real path by
    #: composition, never the empty-disables convention.
    elicitation_output_dir: str = ""
    #: The transcript outbound lane's own switch (``[transcripts] ship``, issue #246); off
    #: by default (D5) — the pump enqueues no delta while this is ``False``.
    transcripts_ship: bool = False
    #: The lane's byte-ceiling overrides (``[transcripts]``, blizzard#338); ``None`` keeps
    #: `blizzard.runner.transcripts.caps`'s own defaults, which own the values.
    transcript_record_max_bytes: int | None = None
    transcript_chunk_max_bytes: int | None = None
    #: This runner's selection policy over the peeked ready queue (``[queue] strict``,
    #: blizzard#459); off by default reaches past a marked head for the first unmarked
    #: entry, ``True`` holds at a marked head and yields no entry instead.
    queue_strict: bool = False


@dataclass(frozen=True)
class ResolvedSubscription:
    """One declared subscription, paired with its resolved sampler and renewer bindings
    (blizzard#436, blizzard#504) — the loop step's own view, carrying only what it reads
    (``slug``/``name``/``sample_interval_seconds``); ``sampler`` is ``None`` for a
    provider with no binding, declared but unsampled. ``renewer`` is ``None`` for
    Anthropic or any unknown provider (D2), which keeps today's read-only behaviour
    exactly."""

    slug: str
    name: str
    sample_interval_seconds: int
    sampler: ISubscriptionSampler | None
    renewer: ICredentialRenewer | None


class ICloseableUsageHttpClient(Protocol):
    """Owns the shared, lazily-built HTTP client every declared subscription's sampler draws
    from (blizzard#436, hub:95); whoever owns this ``LoopContext``'s lifetime closes it
    exactly once, whether or not a client was ever actually built."""

    def close(self) -> None: ...


class _NoUsageHttpClient:
    """The no-op default for a context built without composing the subscription seam
    (most direct test constructions) — closing it does nothing."""

    def close(self) -> None:
        return None


@dataclass(frozen=True)
class LoopContext:
    """Everything a step function reads — passed in, never module-global."""

    stores: RunnerStores
    clock: IClock
    hub: IHubClient
    #: This tick's (or, standalone, this step's own) chunk-status read seam (blizzard#521) —
    #: see :mod:`blizzard.runner.loop.chunk_status_cache`.
    chunk_views: IChunkViews
    provider: IWorkspaceProvider
    process: IProcessProbe
    worktree_git: IWorktreeGit
    config: LoopConfig
    worker_files: WorkerStdoutFiles
    elicitation_files: ElicitationFiles
    usage: UsageRecorder
    sessions: SessionResolver
    #: The fresh-mint owner selector over the acceptable harness set; a resume never reaches it.
    harness_selector: HarnessSelector
    env_release: EnvironmentRelease
    #: Required; every recorded session's owner resolves through it, with no single-harness fallback.
    harnesses: IHarnessRegistry
    #: The check-runner seam (issue #114) — ``None`` when not wired, so a node with no
    #: ``checks:`` still ticks; a node that declares ``checks:`` needs it.
    check_runner: ICheckRunner | None = None
    #: Coarse "any transcript source wired" flag; a read still resolves per-owner via ``transcript_source_for``.
    transcripts_wired: bool = False
    #: The SSE publish seam (D2, blizzard#317), typed against the Protocol
    #: (``bzh:dependency-inversion``); ``None`` on ``blizzard runner tick``, a no-op there.
    events: IRunnerEventPublisher | None = None
    #: Every declared provider subscription, resolved (blizzard#436) — each sampled when
    #: due against its own ``sample_interval_seconds`` and ``slug``-keyed anchor.
    subscriptions: tuple[ResolvedSubscription, ...] = ()
    #: The shared subscription-sampling HTTP client's owner (blizzard#436, hub:95) — see
    #: :class:`ICloseableUsageHttpClient`.
    usage_http_client: ICloseableUsageHttpClient = field(default_factory=_NoUsageHttpClient)
    #: This tick's capability memo (``tick()`` wires it); ``None`` rebuilds it per read.
    capabilities: TickCapabilities | None = None
    #: The loop's own cross-tick harness-version cache — unlike ``capabilities`` above, never rebound per tick.
    harness_versions: HarnessVersionCache | None = None
    #: The loop's own cross-tick harness-health cache (blizzard#438) — mirrors ``harness_versions`` above.
    harness_health: HarnessHealthCache | None = None

    def capability_snapshot(self) -> tuple[RunnerCapability, ...]:
        """This runner's capabilities as the registration push and the matched fleet peek
        both carry them — served from this tick's memo when ``tick()`` wired one, since
        building one probes every bound harness binary."""
        if self.capabilities is not None:
            return self.capabilities.get()
        return capability_snapshot(self.harnesses, self.harness_versions, self.harness_health)

    def adapter_for(self, session: SessionReference) -> IHarnessLifecycleAndVerdict:
        """Resolve an existing session's adapter from its recorded owner — may raise
        ``UnknownHarnessError``/``UnavailableHarnessError``."""
        return self.harnesses.adapter(session.harness_id)

    def transcript_source_for(self, session: SessionReference) -> IHarnessTranscriptSource:
        """Resolve an existing session's transcript source from its recorded owner — same
        raise/guard contract as :meth:`adapter_for`."""
        return self.harnesses.transcript_source(session.harness_id)

    def resolve_boundary_start(self, session: SessionReference, workdir: str | None) -> tuple[str | None, bool]:
        """``(start_position, start_unreadable)`` for a boundary about to open on ``session`` —
        the current transcript tail, or ``(None, True)`` when the source is unresolvable or the
        read fails; never conflate that with ``(None, False)``, a fresh session's own beginning
        sentinel (blizzard#437 D6). Shared by every resume/judge/nudge boundary opener, so a
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
