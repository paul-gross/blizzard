"""The coding-harness adapter seam.

Four operations cover every headless-run + persisted-session + resume harness:

* ``spawn`` starts a headless worker pointed at the chunk's environments, primed
  with the node envelope plus the runner's machine-local preamble, and returns the
  **actual** session id with the pid and process start time, recorded as facts at
  spawn-return.
* ``resume_with_message`` delivers a message into an existing session headlessly
  and returns the new pid. Never run against a live process — kill first.
* ``resume_command`` returns the literal shell command a human runs to resume the
  session interactively (the escalation record's takeover command).
* ``parse_verdict`` parses the judgement-resume reply into the selected choice name
   — a missing or unparseable ``<Choice>`` is ``None``, which the core
  treats as a failure.

Two more (epic #57) translate harness output into cost/token telemetry, never
recording anything themselves: ``parse_usage`` reads a result envelope's own
``usage`` + ``total_cost_usd``; ``sum_transcript_usage`` is the envelope-less
fallback — see :mod:`blizzard.runner.harness.usage`.

``sample_external_subscription_usage`` (issue #218) samples the harness's own
**subscription** rate-limit utilization, distinct from the cost/token telemetry above
— see :mod:`blizzard.runner.harness.external_usage`.

``transcript_source`` (blizzard#245) exposes the harness's **transcript source** — see
:mod:`blizzard.runner.harness.transcript` for the shape.

Adapters stay dumb (``bzh:deterministic-shell``): ``parse_verdict`` returns the
choice *name*, not a graph decision — resolving it to an edge is the core's job.
"""

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

    The held environments with their workdirs, the minted lease id, and the runner's
    local-API URL — never sent to the hub; all machine-local execution truth.
    ``BLIZZARD_ENV_IDS`` rides the spawn environment from ``environments``;
    ``BLIZZARD_LEASE_ID`` and ``BLIZZARD_RUNNER_URL`` ride it from ``lease_id`` and
    ``local_api_url``.

    ``workspace_root`` is the spawn **cwd** (issue #17): the worker is launched at the
    winter workspace root — not an env subdir — so it loads the workspace's shared
    context the way an interactive agent there does; empty falls back to the first
    environment's workdir. ``prompt_prefix`` is the runner-composed workspace prompt +
    info table the adapter prepends to the envelope prompt (rendered by
    :func:`blizzard.runner.harness.preamble.render_worker_preamble`); empty prepends
    nothing.

    ``stdout_path`` (epic #57) is the per-lease file the spawned worker's stdout is
    redirected to, so a killed/reaped worker's result envelope survives the process
    for :meth:`IHarnessAdapter.parse_usage` to read back later — the path is
    **injected**, never computed inside the adapter (``bzh:dependency-injection``;
    issue #58). Empty discards stdout.

    ``lease_token`` (issue #113) is the lease's minted capability-token plaintext,
    ridden into the spawn env as ``BLIZZARD_LEASE_TOKEN`` — a per-spawn identity var
    scoped to this worker's own lease, never a daemon secret
    (``bzh:worker-env-allowlist``).

    ``stderr_path`` (issue #125, change L(iii)) is the sibling per-lease file the spawned
    worker's **stderr** is redirected to, so a worker that launched then crashed to stderr
    leaves a readable tail. Injected exactly like ``stdout_path``; empty discards stderr.
    """

    environments: list[AcquiredEnvironment]
    lease_id: str
    local_api_url: str
    workspace_root: str = ""
    prompt_prefix: str = ""
    stdout_path: str = ""
    stderr_path: str = ""
    lease_token: str = ""


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
    ) -> WorkerHandle:
        """Start a headless worker; return its session id, pid, and start time.

        ``model``/``effort`` (issue #144) are the already-resolved native values — the
        caller ran them through :meth:`resolve_model`/:meth:`resolve_effort` first, so the
        adapter applies rather than resolves. Both ``None`` keeps the adapter's own
        default.

        **Application contract** (issue #144). ``model`` is applied at **mint only** — a
        spawn with ``resume_from`` set passes no model flag and leans on the harness
        restoring the session's own — while ``effort`` is applied on **every** invocation,
        because effort is not session-sticky the way model is (pinned by
        ``tests/test_runner_harness_adapter.py::test_spawn_on_a_resume_carries_the_effort_but_never_the_model``).

        A harness's own configuration can defeat that model stickiness, so mint-only is a
        deployment requirement rather than a preference; each adapter states its own trap
        (:mod:`blizzard.runner.harness.internal.claude_code_adapter` for Claude Code).

        ``resume_from`` (issue #115) is the prior session id a node-entry resume
        continues; ``None`` is a fresh spawn (``session_hint`` mints/honors a brand-new
        id). The returned :class:`WorkerHandle`'s ``session_id`` is the
        **authoritative continuation id** the runner records — whichever id the
        harness actually continued under, fork or in-place.
        """
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
    ) -> int:
        """Headless resume-with-message; returns the new pid. Kill first.

        The fire-and-forget resume; :meth:`judge` is the elicitation that needs the
        reply captured synchronously.

        ``stdout_path`` (epic #57) is the injected per-lease file the resumed
        worker's stdout is redirected to, mirroring :attr:`WorkerPreamble.stdout_path`
        — this operation has no preamble to carry it on, so it rides as a direct
        param instead. Empty inherits stdout.

        ``preamble`` re-supplies the per-lease worker identity (lease id, runner URL,
        held envs, and a freshly re-minted capability token), because ``--resume``
        inherits none of the spawn env. ``chunk_id`` names the lease's chunk for
        ``BLIZZARD_CHUNK_ID``. Both omitted keeps the identity-less allowlist env.
        """
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
    ) -> str:
        """Deliver the judgement prompt into the session and return the raw reply.

        The synchronous half of the two-phase node judgement: resumes the session
        headlessly with the caller-composed judgement prompt and returns the
        harness-native output for :meth:`parse_verdict`. Separated from
        :meth:`resume_with_message` because the verdict elicitation must capture the
        reply, where async message delivery only needs the new pid.

        ``effort`` (issue #144) is reasserted here for the same reason it is on every
        other path — it is not session-sticky. ``model`` is taken only to attribute this
        invocation's usage; it is never passed to the harness, which restores the
        session's own.

        ``preamble``/``chunk_id`` re-supply the per-lease worker identity exactly as on
        :meth:`resume_with_message`. Both omitted keeps the identity-less allowlist env.
        """
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

        ``attended=True`` composes the takeover door's exec'd command (issue #258): it
        reasserts the configured permission mode, because that exec also carries the
        lease's identity env. The default composes the **advertised paste string** —
        the escalation record and ``runner status`` output — which a human runs in a
        bare terminal with no identity env, so it stays at the harness's interactive
        permission default rather than compounding the missing identity with a
        permission bypass.

        ``model``/``effort`` (issue #144) are the session's **stamped** values — what it
        actually ran under, read back rather than re-resolved — and are appended to the
        command when given: a deliberate exception to the mint-only model contract above,
        because an operator's interactive takeover is neither cache-sensitive nor implicit
        (pinned by ``tests/test_runner_harness_adapter.py::
        test_resume_command_appends_the_sessions_stamped_model_and_effort``).

        Both ``None`` — a session predating the stamps, so *unknown* — renders today's
        bare command rather than guessing at a default and presenting it as fact.
        """
        ...

    def identity_env(self, preamble: WorkerPreamble, chunk_id: str, session_id: str) -> dict[str, str]:
        """The per-lease worker-identity child env spawn/judge/resume are built from.

        Exposed on the seam (issue #258) so a takeover can hand an operator's exec'd
        interactive session the lease identity — ``--resume`` inherits no spawn env.
        The env travels in the takeover API response and the CLI's exec, never in the
        printable ``resume_command`` string: the lease token stays off display surfaces.
        """
        ...

    def resolve_model(self, preferences: Sequence[str]) -> str:
        """Resolve a prioritized model preference list to a native model name (issue #144).

        The seam that keeps the hub and graph YAML harness-agnostic: the loop hands an
        ordered list of **opaque preference strings** — namespaced ``blizzard:`` tier
        aliases (``frontier``/``advanced``/``basic`` are the standard three) mixed freely
        with harness-native names — and receives back one name *this* harness understands.

        Left-to-right, first entry that resolves wins. An entry this adapter cannot
        resolve — an alias its config and built-ins both miss, or a native name belonging
        to another harness — is **skipped**, never a spawn failure: a preference list is
        an author's stated preference order, not a contract, and a codex runner is
        expected to skip ``opus``. An empty list, or one whose every entry is
        unresolvable, falls back to the adapter's own default with one log naming what
        was skipped.

        The aliases are deliberately **unordered roles, not an ordered scale**: nothing
        substitutes downward when a tier is unmapped, so every degradation is
        author-written (pinned by
        ``tests/test_pin_runner_harness.py::test_an_unmapped_tier_alias_never_substitutes_downward``).
        """
        ...

    def resolve_effort(self, value: str | None) -> str | None:
        """Resolve an authored effort value to this harness's native tier (issue #144).

        Model's twin, as a single value rather than a list: every adapter can map an
        ordinal *somewhere*, so there is no "unrecognized, try the next one" case.
        ``low|medium|high|max`` is the well-known vocabulary, extended by the runner's
        own ``[effort.aliases]``.

        ``None`` in (no preference expressed) returns ``None`` — the adapter passes no
        effort flag and the harness's own default stands. ``None`` out *also* covers a
        harness with no effort knob at all, which logs the value's arrival once and
        ignores it thereafter rather than failing a spawn over a knob it does not have.
        """
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

        ``kind`` names which invocation produced ``output`` (the caller knows —
        spawn, resume, or judge — the adapter never infers it). Returns ``None``
        when ``output`` carries no result envelope at all (e.g. a killed worker
        whose process never reached completion) — the caller's cue to fall back to
        :meth:`sum_transcript_usage`. Dumb translation only (``bzh:deterministic-
        shell``): never a model call, never a cost estimate — cost rides verbatim
        off the harness's own ``total_cost_usd``.

        ``model`` (issue #144) is the model this invocation actually ran under, used
        **only** when the harness reports none of its own — supplied by the caller,
        which knows what it resolved or what the session's own stamp says. ``None``
        keeps the adapter default.
        """
        ...

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind, *, model: str | None = None) -> UsageSample:
        """Sum per-message ``usage`` across a session transcript's raw JSONL lines.

        The envelope-less fallback: when a worker is killed/reaped before it ever
        produces a result envelope, its transcript still carries a ``usage`` object
        on every assistant message — summed here into token counts with
        ``cost_usd=None`` (a transcript carries no dollar figure). Takes
        already-read lines, mirroring :meth:`IHarnessTranscriptSource.read_raw_lines`,
        so the file locate/read step is never duplicated here.

        ``model`` is the same attribution fallback :meth:`parse_usage` takes, applying
        when no transcript line names a model either.
        """
        ...

    def sample_external_subscription_usage(self) -> ExternalSubscriptionUsageSnapshot | None:
        """Sample this harness's own subscription rate-limit utilization (issue #218).

        Mirrors :meth:`resolve_effort`'s ``None`` contract: ``None`` means this harness
        has no subscription concept at all (an adapter with no such notion, or a
        deployment not authenticated against one), **or** this particular attempt
        produced nothing — an unreadable/expired credential, an unreachable or
        non-2xx usage endpoint, an unparseable response, anything. Never a raise: a
        diagnostic sample is by nature best-effort, so every failure path is the
        caller's cue to log and move on, exactly like a harness with no knob to ask.
        """
        ...

    def transcript_source(self) -> IHarnessTranscriptSource:
        """This harness's transcript source (blizzard#245).

        An accessor, not three methods folded onto this Protocol directly: the
        transcript source is a cohesive sub-seam with its own configuration
        (a projects root) and its own lifetime. A harness with no on-disk transcript
        binds :class:`~blizzard.runner.harness.transcript.NullTranscriptSource`, so a
        caller never needs a null check of its own.
        """
        ...
