"""The coding-harness adapter seam.

Four operations cover every headless-run + persisted-session + resume harness: ``spawn``,
``resume_with_message``, ``resume_command``, and ``parse_verdict``, plus usage translation
and the transcript source. Provider subscription sampling is a separate, provider-selected
seam (blizzard#436). Adapters stay dumb (``bzh:deterministic-shell``): they never decide."""

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
    rather than an internal adapter (issue #125)."""


class WorkerIdentityError(RuntimeError):
    """``PendingWorkerHandle.await_identity`` could not confirm the launch's session id.
    Distinct from :class:`HarnessSpawnError`: a real process already exists, so its
    caller (:class:`~blizzard.runner.loop.spawn.Spawner`) must kill the group it
    already durably recorded, never treat it as "nothing started"."""


@dataclass(frozen=True)
class WorkerPreamble:
    """The runner's machine-local preamble prepended to the envelope (issue #17): held
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


@dataclass(frozen=True)
class WorkerHandle:
    """The facts a launch is authoritative on once identified (D1/D2) — this IS a
    :class:`PendingWorkerHandle` for any harness that already knows its session id at
    launch (every binding today): ``await_identity`` is trivially itself. A harness
    whose identity only arrives later returns a distinct pending type instead."""

    session_id: str  # harness-assigned where it self-assigns, else the honored hint
    pid: int
    process_start_time: str  # stable across pid reuse — REAP keys on (pid, start_time)
    pgid: int  # the owned process group (D3) — every launch gets one; never absent in memory
    confirm_durable: Callable[[], None] = field(
        default=lambda: None, compare=False
    )  # F1's disarm signal; no-op default

    def await_identity(self, timeout: float) -> WorkerHandle:
        """Already identified at launch — this handle is its own phase two."""
        return self


@dataclass(frozen=True)
class ResumeHandle:
    """The OS facts a resume launch is authoritative on (D3): its pid and the REAL
    process group the launcher recorded for it — never inferred as ``pid`` at the call
    site. Mirrors :class:`WorkerHandle`'s shape exactly: a resume launches deferred (D4)
    just like a fresh spawn or a judge, so it carries the same disarm signal and start time."""

    pid: int
    pgid: int
    process_start_time: str  # stable across pid reuse — `_wake` records it straight through
    confirm_durable: Callable[[], None] = field(
        default=lambda: None, compare=False
    )  # F1's disarm signal; no-op default


class PendingWorkerHandle(Protocol):
    """``spawn``'s own phase-one return (D1): the launched process's OS facts — pid, start
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
        """F1's disarm signal: call once — and only once the caller's own durable record
        naming this launch's pid/pgid has actually landed. Before that, the daemon's own
        death (crash or graceful, indistinguishable to the OS) must still kill this launch
        outright (the execution spec's narrow handshake window); after, neither should, so
        the recorded generation can be re-adopted rather than orphaned. Idempotent."""
        ...


class IHarnessWorkerLifecycle(Protocol):
    """Spawning, resuming, and judging a worker process (``bzh:seam-size-ceiling``) — one of the six slices
    ``IHarnessAdapter`` composes, the narrower seam for a consumer driving only worker lifecycle. Every
    ``session_cwd`` is the session's spawn cwd (``SpawnCwd.of_session``), whatever it resolved to: a harness
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
        """Start a headless worker; return its pending handle (D1) — pid, start time, and
        process group, before identity is confirmed. ``model``/``effort``/
        ``compaction_window`` (issue #144, blizzard#343) arrive already resolved; ``model``
        applies at **mint only**, the other two on **every** invocation. ``resume_from``
        (#115) continues a session; ``await_identity``'s result is authoritative."""
        ...

    def honors_session_hint(self) -> bool:
        """True iff a fresh spawn's identified session id always equals ``session_hint``.
        Claude Code declares ``True`` (preassigned ``--session-id``); a self-assigning
        harness declares ``False`` — the selftest's spawn gate
        (``blizzard.runner.selftest.checks.Spawn``) only demands hint-equality where ``True``."""
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
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> ResumeHandle:
        """Headless resume-with-message; returns the new launch's pid and its REAL,
        launcher-recorded process group (D3), never a caller-inferred ``pgid=pid``. Kill
        first. ``stdout_path`` is the injected stdout capture; empty inherits stdout.
        ``preamble``/``chunk_id`` re-supply the per-lease identity ``--resume`` inherits
        none of. ``compaction_window`` reasserts like ``effort``."""
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
        detached half of the launch/collect elicitation (blizzard#443).

        Mirrors ``spawn``: the reply lands in ``output_path`` (never empty — an unwritable
        target raises ``HarnessSpawnError`` rather than proceeding uncollectable, D4) and the
        caller reads it back once the returned handle's process has exited. ``model`` only
        attributes usage, never passed on. ``preamble``/``chunk_id`` re-supply worker
        identity. ``compaction_window`` reasserts like ``effort``."""
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

        ``attended=True`` composes the exec'd command (#258), reasserting the configured permission mode; the default
        composes the advertised paste string. Carries the stamped ``model``/``effort``, deliberately no
        ``compaction_window`` (blizzard#343 — not a fleet-driven turn)."""
        ...

    def identity_env(self, preamble: WorkerPreamble, chunk_id: str, session_id: str) -> dict[str, str]:
        """The per-lease worker-identity child env spawn/judge/resume are built from.

        Exposed on the seam (issue #258) because ``--resume`` inherits no spawn env. It
        is never rendered into the printable ``resume_command``: the lease token stays
        off display surfaces."""
        ...


class IHarnessModelResolution(Protocol):
    """Resolving an authored model/effort/compaction-window preference to this harness's
    own vocabulary (``bzh:seam-size-ceiling``) — never failing a spawn over an
    unresolvable one."""

    def resolve_model(self, preferences: Sequence[str]) -> str:
        """Resolve a preference list to a native model name (issue #144): left-to-right,
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
        """Resolve an authored effort value to this harness's native tier (issue #144).

        A single value rather than a list: every adapter can map an ordinal *somewhere*.
        ``low|medium|high|max`` is the well-known vocabulary. ``None`` in returns ``None``,
        as does a harness with no effort knob at all, which never fails a spawn over one."""
        ...

    def resolve_compaction_window(self, value: str | None) -> str | None:
        """Resolve an authored compaction-window value to this harness's own vocabulary
        (blizzard#343) — the same never-fails-a-spawn contract as ``resolve_effort``:
        unrecognized, unsupported, and ``None`` all return ``None``."""
        ...

    def resolvable_tier_ids(self) -> tuple[str, ...]:
        """The tier ids this adapter can resolve (blizzard#433) — built-ins and any
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

    def parse_usage(self, output: str, kind: UsageKind, *, model: str | None = None) -> UsageSample | None:
        """Translate a result envelope's ``usage`` + its cost figure into a sample.

        ``kind`` names which invocation produced ``output`` — never inferred; ``model`` attributes it only when the
        harness reports none. ``None`` when no envelope. Cost rides verbatim, its scope on ``cost_scope_tokens`` —
        unresolved, and never folded into the sample's separate ``estimated_cost_usd``."""
        ...

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind, *, model: str | None = None) -> UsageSample:
        """Sum per-message ``usage`` across a session transcript's raw JSONL lines.

        The envelope-less fallback for a worker killed before its result envelope: token counts
        and ``cost_usd=None`` (a transcript carries no billed figure), maybe an estimate.
        ``model`` is the same attribution fallback :meth:`parse_usage` takes."""
        ...

    def observed_model(self, lines: Sequence[str]) -> str | None:
        """The model a transcript range names as having actually run, or ``None`` when the
        range carries none — never a fallback literal, so a caller can tell "observed
        nothing" apart from "observed the configured default". Claude Code reads the
        per-record ``message.model`` :meth:`sum_transcript_usage` already reads; OpenCode
        reads an exported message's ``message.info`` provider and model, formatted as
        ``provider/model`` — a run event alone (no export line in ``lines``) carries neither."""
        ...


class IHarnessUsageLimits(Protocol):
    """Classifying an invocation's own output as a subscription-usage-limit exit
    (``bzh:seam-size-ceiling``, blizzard#594) — a slice of its own rather than joining
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
    (``bzh:seam-size-ceiling``, blizzard#595) — its own slice for the same reason
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
    and its declared degradations. Narrow and separate from IHarnessWorkerLifecycle: the
    evaluator (runner/harness/health.py) is this seam's only consumer."""

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
        match how the range reads in docs (blizzard#604)."""
        ...

    def normalize_version(self, raw: str | None) -> str | None:
        """This binding's own raw ``observe_version`` output, reduced to the bare version
        :meth:`supported_version`'s membership check and any corpus lookup compare against —
        each binding owns its own raw shape, never a normalizer shared across bindings by
        default (blizzard#606)."""
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
    """The selftest canary's own composed slice (``bzh:seam-size-ceiling``, blizzard#438):
    :class:`IHarnessLifecycleAndVerdict`'s pair plus usage accounting, its widened roster's
    ``UsageParsing``/``TranscriptReadability`` checks needing ``parse_usage`` and
    ``transcript_source`` too — still narrower than the full adapter, which it takes no
    model-resolution slice from at all."""

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
    ``transcript_source``, unsliced since no consumer needs it alone. Dumb: translates, never
    decides. A genuine pass-through — threading the adapter on rather than calling it —
    takes this alias; a caller takes the narrowest slice its job needs."""

    def transcript_source(self) -> IHarnessTranscriptSource:
        """This harness's transcript source (blizzard#245).

        An accessor rather than three methods folded onto this Protocol: the source is a
        cohesive sub-seam with its own configuration and lifetime. A harness with no
        on-disk transcript binds a null source, so no caller needs a null check."""
        ...
