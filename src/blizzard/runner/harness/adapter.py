"""The coding-harness adapter seam.

Four operations cover every headless-run + persisted-session + resume harness: ``spawn``,
``resume_with_message``, ``resume_command``, and ``parse_verdict``, plus usage translation
and the transcript source. Provider subscription sampling is a separate, provider-selected
seam. Adapters stay dumb (``bzh:deterministic-shell``): they never decide."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from packaging.specifiers import SpecifierSet

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.health import DeclaredDegradation
from blizzard.runner.harness.overload import ProviderOverload
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.runner.harness.usage import UsageKind, UsageLimit, UsageSample
from blizzard.wire.envelope import NodeEnvelope

#: Sole-declared default bound on :meth:`PendingWorkerHandle.await_identity`; spawn and selftest both import it.
DEFAULT_IDENTITY_AWAIT_TIMEOUT_SECONDS = 10.0


class HarnessSpawnError(RuntimeError):
    """The harness binary could not be launched (missing binary, bad workdir).

    Part of the adapter contract (``spawn`` raises it), so it lives on the public seam
    rather than an internal adapter."""


class WorkerIdentityError(RuntimeError):
    """``PendingWorkerHandle.await_identity`` could not confirm the launch's session id.
    Distinct from :class:`HarnessSpawnError`: a real process already exists, so the
    caller must kill its group, never treat it as "nothing started"."""


@dataclass(frozen=True)
class WorkerPreamble:
    """The runner's machine-local preamble prepended to the envelope: held
    environments, lease identity and token, the local-API URL, the spawn cwd, and
    injected capture paths. Never sent to the hub."""

    environments: list[AcquiredEnvironment]
    lease_id: str
    local_api_url: str
    workspace_root: str = ""  # the spawn cwd; empty falls back to the first env's workdir
    prompt_prefix: str = ""  # prepended to the envelope prompt; empty prepends nothing
    stdout_path: str = ""  # per-lease stdout capture, outliving the process; empty discards
    stderr_path: str = ""  # per-lease stderr capture; empty discards
    lease_token: str = ""  # a per-spawn identity var, never a daemon secret
    tmpdir: str = ""  # per-lease scratch directory (BLIZZARD_TMPDIR); empty disables it
    worker_programs: bool = False  # point the worker's OpenTelemetry exporters at the runner's receiver
    traceparent: str = ""  # the step root's W3C traceparent; empty (tracing off) sets no trace variable


@dataclass(frozen=True)
class WorkerHandle:
    """The facts a launch is authoritative on once identified — this IS a
    :class:`PendingWorkerHandle` for any harness that already knows its session id at
    launch (every binding today): ``await_identity`` is trivially itself. A harness
    whose identity only arrives later returns a distinct pending type instead."""

    session_id: str  # harness-assigned where it self-assigns, else the honored hint
    pid: int
    process_start_time: str  # stable across pid reuse — REAP keys on (pid, start_time)
    pgid: int  # the owned process group — every launch gets one; never absent in memory
    confirm_durable: Callable[[], None] = field(
        default=lambda: None, compare=False
    )  # disarms the daemon-death kill signal once durably recorded; no-op default

    def await_identity(self, timeout: float) -> WorkerHandle:
        """Already identified at launch — this handle is its own phase two."""
        return self


@dataclass(frozen=True)
class ResumeHandle:
    """The OS facts a resume launch is authoritative on: its pid and the REAL
    process group the launcher recorded for it — never inferred as ``pid`` at the call
    site. Mirrors :class:`WorkerHandle`'s shape exactly: a resume launches deferred
    just like a fresh spawn or a judge, so it carries the same disarm signal and start time."""

    pid: int
    pgid: int
    process_start_time: str  # stable across pid reuse — `_wake` records it straight through
    confirm_durable: Callable[[], None] = field(
        default=lambda: None, compare=False
    )  # disarms the daemon-death kill signal once durably recorded; no-op default


class PendingWorkerHandle(Protocol):
    """``spawn``'s own phase-one return: the launched process's OS facts — pid, start
    time, and owned group — durable-worthy before any identity is known. A Protocol, not a
    dataclass, since ``await_identity`` may block or read a stream rather than merely
    return data already in hand."""

    @property
    def pid(self) -> int: ...

    @property
    def process_start_time(self) -> str: ...

    @property
    def pgid(self) -> int: ...

    def await_identity(self, timeout: float) -> WorkerHandle:
        """Block up to ``timeout`` seconds for this launch's authoritative session id.

        Raises :class:`WorkerIdentityError` on a timeout, a malformed reply, or the
        process exiting before identity arrived — never returns an empty session id."""
        ...

    def confirm_durable(self) -> None:
        """Call once — and only once the caller's own durable record
        naming this launch's pid/pgid has actually landed. Before that, the daemon's own
        death (crash or graceful, indistinguishable to the OS) must still kill this launch
        outright (the execution spec's narrow handshake window); after, neither should, so
        the recorded generation can be re-adopted rather than orphaned. Idempotent."""
        ...


class IHarnessWorkerLifecycle(Protocol):
    """Spawning, resuming, and judging a worker process (``bzh:seam-size-ceiling``). Every
    ``session_cwd`` is the session's spawn cwd, whatever it resolved to: a harness
    scoping sessions to their launch directory never completes a turn run from anywhere else."""

    def spawn(
        self,
        envelope: NodeEnvelope,
        preamble: WorkerPreamble,
        session_hint: str | None,
        resume_from: str | None = None,
        *,
        model: str | None = None,
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> PendingWorkerHandle:
        """Start a headless worker; return its pending handle — pid, start time, and
        process group, before identity is confirmed. ``model``/``effort``/
        ``compaction_window`` arrive already resolved. Each
        binding applies the knobs its harness needs per invocation; ``resume_from`` (#115)
        continues a session. ``await_identity``'s result is authoritative."""
        ...

    def honors_session_hint(self) -> bool:
        """True iff a fresh spawn's identified session id always equals ``session_hint``.
        A harness that preassigns the id declares ``True``; a self-assigning one declares
        ``False``."""
        ...

    def observe_version(self) -> str | None:
        """The configured harness binding's version, observed right now, or ``None`` when
        it could not be — bounded and non-raising (never a property: this may run a
        subprocess). Uncached, so a self-updated binary is reflected on the next call."""
        ...

    def resume_with_message(
        self,
        session_cwd: str,
        session_id: str,
        message: str,
        stdout_path: str = "",
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        model: str | None = None,
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> ResumeHandle:
        """Headless resume-with-message; returns the new launch's pid and its REAL,
        launcher-recorded process group, never a caller-inferred ``pgid=pid``. Kill
        first. ``stdout_path`` is the injected stdout capture; empty inherits stdout.
        ``preamble``/``chunk_id`` re-supply the per-lease identity ``--resume`` inherits
        none of. The caller supplies the session's resolved ``model``/``effort``/
        ``compaction_window``; the binding applies its supported resume parameters."""
        ...

    def judge(
        self,
        session_cwd: str,
        session_id: str,
        judgement_prompt: str,
        output_path: str,
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        model: str | None = None,
        compaction_window: str | None = None,
    ) -> WorkerHandle:
        """Launch the judgement prompt into the session and return immediately — the
        detached half of the launch/collect elicitation.

        Mirrors ``spawn``: the reply lands in ``output_path`` (never empty — an unwritable
        target raises ``HarnessSpawnError`` rather than proceeding uncollectable) and the
        caller reads it back once the returned handle's process has exited. ``model``
        supplies the session stamp to bindings that reassert it. ``preamble``/``chunk_id``
        re-supply worker identity. Supported bindings reassert ``compaction_window`` and
        ``effort`` as needed."""
        ...

    def resume_command(
        self,
        session_cwd: str,
        session_id: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        attended: bool = False,
    ) -> str:
        """The literal interactive-takeover shell command for the escalation record.

        ``attended=True`` composes the exec'd command (#258), reasserting the harness's configured permission
        posture; the default composes the advertised paste string. Carries the stamped ``model``/``effort``,
        deliberately no ``compaction_window`` (not a fleet-driven turn)."""
        ...

    def identity_env(self, preamble: WorkerPreamble, chunk_id: str, session_id: str) -> dict[str, str]:
        """The per-lease worker-identity child env spawn/judge/resume are built from.

        Exposed on the seam because ``--resume`` inherits no spawn env. It
        is never rendered into the printable ``resume_command``: the lease token stays
        off display surfaces."""
        ...


class IHarnessModelResolution(Protocol):
    """Resolving an authored model/effort/compaction-window preference to this harness's
    own vocabulary (``bzh:seam-size-ceiling``) — never failing a spawn over an
    unresolvable one."""

    def resolve_model(self, preferences: Sequence[str]) -> str:
        """Resolve a preference list to a native model name: left-to-right,
        first resolvable entry wins; an unresolvable entry is skipped, never an error; an
        empty or fully-unresolvable list falls back to the adapter default. Tier aliases
        are unordered roles, never a scale — nothing substitutes downward (pinned by
        ``tests/test_pin_runner_harness.py``). Expressed in terms of :meth:`resolve_model_strict`."""
        ...

    def resolve_model_strict(self, preferences: Sequence[str]) -> str | None:
        """:meth:`resolve_model`'s own strict half: the same left-to-right walk, but ``None``
        rather than the adapter default when nothing in ``preferences`` resolves — the
        "nothing authored resolved" a multi-harness selection reads, which
        :meth:`resolve_model`'s always-a-model contract cannot itself express."""
        ...

    def resolve_effort(self, value: str | None) -> str | None:
        """Resolve an authored effort value to this harness's native tier.

        A single value rather than a list: every adapter can map an ordinal *somewhere*.
        ``low|medium|high|max`` is the well-known vocabulary. ``None`` in returns ``None``,
        as does a harness with no effort knob at all, which never fails a spawn over one."""
        ...

    def resolve_compaction_window(self, value: str | None) -> str | None:
        """Resolve an authored compaction-window value to this harness's own vocabulary —
        the same never-fails-a-spawn contract as ``resolve_effort``:
        unrecognized, unsupported, and ``None`` all return ``None``."""
        ...

    def resolvable_tier_ids(self) -> tuple[str, ...]:
        """The tier ids this adapter can resolve — built-ins and any
        operator-declared alias alike, an overridden id appearing once. The capability
        snapshot's own source; never itself a spawn-time resolution."""
        ...


class IHarnessVerdictParsing(Protocol):
    """Parsing a worker's raw output into a verdict, a usability check, and its free-text
    assessment (``bzh:seam-size-ceiling``)."""

    def parse_verdict(self, output: str) -> str | None:
        """Parse the ``<Choice>{name}</Choice>`` reply into a choice name, else ``None``."""
        ...

    def has_usable_output(self, output: str) -> bool:
        """True when ``output`` carries a well-formed result envelope — independent of
        whether it names a verdict, which a legitimate ask-instead-of-a-choice reply also
        lacks. A process killed mid-write (an OOM, a ``kill -9``) can leave a non-empty but
        truncated/malformed ``output``, which this reports as unusable — indistinguishable
        from no output at all."""
        ...

    def parse_assessment(self, output: str) -> str:
        """Parse the judgement reply's free-text assessment — the payload after the Choice.

        The verdict reply is ``<Choice>{name}</Choice>`` plus the worker's prose
        assessment of the node's checks. Empty string when the reply carries no
        assessment."""
        ...


class IHarnessUsageAccounting(Protocol):
    """Turning a worker's raw output or transcript into a usage sample
    (``bzh:seam-size-ceiling``) — the result-envelope path and the envelope-less
    transcript-sum fallback."""

    def needs_usage_transcript(self, output: str, *, model: str | None = None) -> bool:
        """Whether this invocation needs transcript evidence to price against the right model.

        True either because the envelope itself omits one (Claude Code, independent of
        ``model``) or because ``model`` is unresolved and this binding has no configured
        default of its own to fall back on (OpenCode) — never true merely because ``model``
        is unresolved: a binding with its own default prices off that without paying for
        a read."""
        ...

    def parse_usage(
        self, output: str, kind: UsageKind, *, model: str | None = None, transcript_lines: Sequence[str] = ()
    ) -> UsageSample | None:
        """Translate a result envelope's ``usage`` + its cost figure into a sample.

        ``kind`` names which invocation produced ``output`` — never inferred; ``model`` is the expected session
        model for comparison or a fallback where the binding supports it. ``transcript_lines`` are this
        invocation's own assistant records for bindings whose envelope omits the observed model. ``None``
        when no envelope.
        Cost rides verbatim, its scope on ``cost_scope_tokens`` — unresolved, and never
        folded into the sample's separate ``estimated_cost_usd``."""
        ...

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind, *, model: str | None = None) -> UsageSample:
        """Sum per-message ``usage`` across a session transcript's raw JSONL lines.

        The envelope-less fallback for a worker killed before its result envelope: token counts
        and ``cost_usd=None`` (a transcript carries no billed figure), maybe an estimate.
        ``model`` is the expected session model for comparison or a binding-specific fallback."""
        ...

    def observed_model(self, lines: Sequence[str]) -> str | None:
        """The model a transcript range names as having actually run, or ``None`` when it names
        none — never a fallback literal, so "observed nothing" reads apart from "observed the
        default"."""
        ...


class IHarnessUsageLimits(Protocol):
    """Classifying an invocation's own output as a subscription-usage-limit exit
    (``bzh:seam-size-ceiling``) — a slice of its own rather than joining
    ``IHarnessUsageAccounting``: that slice's mandate is producing a ``UsageSample``, this
    one a fact the loop decides what to do with (``bzh:deterministic-shell``)."""

    def classify_usage_limit(self, output: str, lines: Sequence[str], now: datetime) -> UsageLimit | None:
        """``None`` when this invocation was not usage-limited. ``output`` is the
        invocation's own captured stdout; ``lines`` is its transcript range (generation
        boundary to judge boundary or tail) — which one carries the harness's own signal
        is this adapter's to know. Never raises: an unparseable reset time returns a
        ``UsageLimit`` with ``resets_at=None``, not a guess."""
        ...


class IHarnessProviderOverload(Protocol):
    """Classifying an invocation's own exit as a provider-overloaded (529) one
    (``bzh:seam-size-ceiling``) — its own slice for the same reason
    ``IHarnessUsageLimits`` is: a fact the loop decides what to do with
    (``bzh:deterministic-shell``), not a ``UsageSample``."""

    def classify_provider_overload(self, output: str, lines: Sequence[str]) -> ProviderOverload | None:
        """``None`` when this invocation did not exit on a provider overload. ``output`` is
        the invocation's own captured stdout; ``lines`` is its transcript range (generation
        boundary to judge boundary or tail) — which one carries the harness's own signal is
        this adapter's to know. Never raises."""
        ...


class IHarnessHealthProbe(Protocol):
    """The evidence a harness-health evaluation needs that no other adapter seam supplies —
    binary discovery, provider authentication, the binding's supported-version declaration,
    and its declared degradations."""

    def binary_present(self) -> bool:
        """Whether the configured binary resolves right now — bounded and non-raising,
        the standalone half of :meth:`IHarnessWorkerLifecycle.observe_version`'s own
        presence check, for a caller that needs presence without paying for a version probe."""
        ...

    def probe_authentication(self) -> bool:
        """Whether this binding's provider credentials are present and usable, observed
        right now — bounded and non-raising, never a network round trip that could delay
        a health check indefinitely."""
        ...

    def config_conflicts(self) -> tuple[str, ...]:
        """Ambient settings this binding's worker would load that defeat the runner's required
        wiring, each rendered as its file and key (never content); empty when none or when the
        binding has no such check. Bounded and non-raising."""
        ...

    def supported_version(self) -> SpecifierSet | None:
        """This binding's declared admitted-version range as a semver ``SpecifierSet``, or
        ``None`` when it declares no range at all. Checked through
        :func:`~blizzard.runner.harness.internal.harness_shared.version_admitted`, never
        equality against one literal; ``None`` is the only "no constraint" value — an
        *empty* ``SpecifierSet`` would instead admit every version."""
        ...

    def supported_version_display(self) -> str | None:
        """:meth:`supported_version`'s own declared literal display string, or ``None``
        alongside its ``None`` — never ``str(SpecifierSet)``, whose clause order does not
        match how the range reads in docs."""
        ...

    def normalize_version(self, raw: str | None) -> str | None:
        """This binding's own raw ``observe_version`` output, reduced to the bare version
        :meth:`supported_version`'s membership check and any corpus lookup compare against —
        each binding owns its own raw shape, never a normalizer shared across bindings by
        default."""
        ...

    def classifies_offline(self) -> bool:
        """Whether this binding backs its admitted range with a committed offline
        compatibility corpus at all — declared here rather than read from the filesystem
        (``bzh:pluggable-seams``), so a corpus going missing degrades that one binding's own
        health rather than silently downgrading it to membership-only admission. ``False``
        means an admitted version is never run through ``classify_offline`` at all."""
        ...

    def declared_degradations(self) -> tuple[DeclaredDegradation, ...]:
        """Every known, non-blocking compatibility gap this binding declares about
        itself — reported diagnostics only, never a spawn-time decision."""
        ...


class IHarnessLifecycleAndVerdict(IHarnessWorkerLifecycle, IHarnessVerdictParsing, Protocol):
    """Worker lifecycle plus verdict parsing, combined into the one slice a consumer
    needing both takes rather than the full adapter (``bzh:seam-size-ceiling``)."""


class IHarnessSelfTestSeam(IHarnessWorkerLifecycle, IHarnessVerdictParsing, IHarnessUsageAccounting, Protocol):
    """Worker lifecycle, verdict parsing, and usage accounting plus ``transcript_source``
    (``bzh:seam-size-ceiling``) — no model-resolution slice."""

    def transcript_source(self) -> IHarnessTranscriptSource: ...


class IHarnessAdapter(
    IHarnessWorkerLifecycle,
    IHarnessModelResolution,
    IHarnessVerdictParsing,
    IHarnessUsageAccounting,
    IHarnessUsageLimits,
    IHarnessProviderOverload,
    Protocol,
):
    """The coding-harness seam: its six narrower slices (``bzh:seam-size-ceiling``) plus
    ``transcript_source``. Dumb: translates, never decides."""

    def transcript_source(self) -> IHarnessTranscriptSource:
        """This harness's transcript source.

        An accessor rather than three methods folded onto this Protocol: the source is a
        cohesive sub-seam with its own configuration and lifetime. A harness with no
        on-disk transcript binds a null source, so no caller needs a null check."""
        ...
