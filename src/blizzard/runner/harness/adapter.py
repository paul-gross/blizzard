"""The coding-harness adapter seam.

Four operations cover every headless-run + persisted-session + resume harness: ``spawn``,
``resume_with_message``, ``resume_command``, and ``parse_verdict``. Usage translation,
external-subscription sampling, and the transcript source ride alongside them. Adapters
stay dumb (``bzh:deterministic-shell``): they translate, they never decide."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.external_usage import ExternalSubscriptionUsageSnapshot
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.wire.envelope import NodeEnvelope


class HarnessSpawnError(RuntimeError):
    """The harness binary could not be launched (missing binary, bad workdir).

    Part of the adapter contract (``spawn`` raises it), so it lives on the public seam
    rather than an internal adapter (issue #125, change L(iii))."""


@dataclass(frozen=True)
class WorkerPreamble:
    """The runner's machine-local preamble prepended to the envelope (issue #17).

    Machine-local execution truth — held environments, lease identity and token, the
    local-API URL, the spawn cwd, injected capture paths. Never sent to the hub."""

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
    """What ``spawn`` returns — the facts recorded at spawn-return."""

    session_id: str  # harness-assigned where it self-assigns, else the honored hint
    pid: int
    process_start_time: str  # stable across pid reuse — REAP keys on (pid, start_time)


class IHarnessAdapter(Protocol):
    """The coding-harness seam. Dumb: translates, never decides."""

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
    ) -> WorkerHandle:
        """Start a headless worker; return its session id, pid, and start time.

        ``model``/``effort``/``compaction_window`` (issue #144, blizzard#343) arrive already
        resolved; ``model`` applies at **mint only**, the other two on **every** invocation.
        ``resume_from`` (#115) continues a session; the returned id is authoritative."""
        ...

    def resume_with_message(
        self,
        workdir: str,
        session_id: str,
        message: str,
        stdout_path: str = "",
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> int:
        """Headless resume-with-message; returns the new pid. Kill first.

        The fire-and-forget resume. ``stdout_path`` is the injected stdout capture; empty
        inherits stdout. ``preamble``/``chunk_id`` re-supply the per-lease identity
        ``--resume`` inherits none of. ``compaction_window`` reasserts like ``effort``."""
        ...

    def judge(
        self,
        workdir: str,
        session_id: str,
        judgement_prompt: str,
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        model: str | None = None,
        compaction_window: str | None = None,
    ) -> str:
        """Deliver the judgement prompt into the session and return the raw reply.

        The synchronous half of the two-phase node judgement — the reply is captured, not
        just the new pid. ``model`` only attributes usage, never passed on. ``preamble``/
        ``chunk_id`` re-supply worker identity. ``compaction_window`` reasserts like ``effort``."""
        ...

    def resume_command(
        self,
        workdir: str,
        session_id: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        attended: bool = False,
    ) -> str:
        """The literal interactive-takeover shell command for the escalation record.

        ``attended=True`` composes the exec'd command (issue #258), which carries identity
        env and so reasserts the configured permission mode; the default composes the
        advertised paste string. ``model``/``effort`` are the session's stamped values."""
        ...

    def identity_env(self, preamble: WorkerPreamble, chunk_id: str, session_id: str) -> dict[str, str]:
        """The per-lease worker-identity child env spawn/judge/resume are built from.

        Exposed on the seam (issue #258) because ``--resume`` inherits no spawn env. It
        is never rendered into the printable ``resume_command``: the lease token stays
        off display surfaces."""
        ...

    def resolve_model(self, preferences: Sequence[str]) -> str:
        """Resolve a preference list to a native model name (issue #144): left-to-right,
        first resolvable entry wins; an unresolvable entry is skipped, never an error; an
        empty or fully-unresolvable list falls back to the adapter default. Tier aliases
        are unordered roles, never a scale — nothing substitutes downward (pinned by
        ``tests/test_pin_runner_harness.py``)."""
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

    def parse_verdict(self, output: str) -> str | None:
        """Parse the ``<Choice>{name}</Choice>`` reply into a choice name, else ``None``."""
        ...

    def parse_assessment(self, output: str) -> str:
        """Parse the judgement reply's free-text assessment — the payload after the Choice.

        The verdict reply is ``<Choice>{name}</Choice>`` plus the worker's prose
        assessment of the node's checks. Empty string when the reply carries no
        assessment."""
        ...

    def parse_usage(self, output: str, kind: UsageKind, *, model: str | None = None) -> UsageSample | None:
        """Translate a result envelope's ``usage`` + ``total_cost_usd`` into a sample.

        ``kind`` names which invocation produced ``output``; the adapter never infers it.
        ``model`` attributes the sample only when the harness reports none of its own.
        ``None`` when there is no result envelope at all. Cost rides verbatim, never estimated."""
        ...

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind, *, model: str | None = None) -> UsageSample:
        """Sum per-message ``usage`` across a session transcript's raw JSONL lines.

        The envelope-less fallback, for a worker killed before it produced a result
        envelope: token counts with ``cost_usd=None``, since a transcript carries no dollar
        figure. ``model`` is the same attribution fallback :meth:`parse_usage` takes."""
        ...

    def sample_external_subscription_usage(self) -> ExternalSubscriptionUsageSnapshot | None:
        """Sample this harness's own subscription rate-limit utilization (issue #218).

        ``None`` means this harness has no subscription concept at all, **or** that this
        attempt produced nothing — a bad credential, an unreachable endpoint, an
        unparseable response, anything. Never a raise: the sample is best-effort."""
        ...

    def transcript_source(self) -> IHarnessTranscriptSource:
        """This harness's transcript source (blizzard#245).

        An accessor rather than three methods folded onto this Protocol: the source is a
        cohesive sub-seam with its own configuration and lifetime. A harness with no
        on-disk transcript binds a null source, so no caller needs a null check."""
        ...
