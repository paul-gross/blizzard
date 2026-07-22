"""The coding-harness adapter seam.

Four operations cover every headless-run + persisted-session + resume harness:

* ``spawn`` starts a headless worker pointed at the chunk's environments, primed
  with the node envelope plus the runner's machine-local preamble — the held env
  ids and their workdirs — and returns the **actual** session id with the
  pid and process start time, recorded as facts at spawn-return.
* ``resume_with_message`` delivers a message into an existing session headlessly
  and returns the new pid — the operation behind the judgement prompt,
  answer delivery, and the CI feedback loop. Never run against a live
  process — kill first.
* ``resume_command`` returns the literal shell command a human runs to resume the
  session interactively (the escalation record's takeover command).
* ``parse_verdict`` parses the judgement-resume reply into the selected choice name
   — a missing or unparseable ``<Choice>`` is ``None``, which the core
  treats as a failure.

Two more (epic #57) translate harness output into cost/token telemetry, never
recording anything themselves: ``parse_usage`` reads a result envelope's own
``usage`` + ``total_cost_usd``; ``sum_transcript_usage`` is the envelope-less
fallback, summing per-message ``usage`` off the raw session transcript. Cost always
comes from the harness — blizzard never maintains a pricing table.

Adapters stay dumb (``bzh:deterministic-shell``): ``parse_verdict`` returns the
choice *name*, not a graph decision — resolving it to an edge is the core's job.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.wire.envelope import NodeEnvelope


@dataclass(frozen=True)
class WorkerPreamble:
    """The runner's machine-local preamble prepended to the envelope (issue #17).

    The held environments with their workdirs, the minted lease id, and the runner's
    local-API URL — never sent to the hub; all machine-local execution truth.
    ``BLIZZARD_ENV_IDS`` rides the spawn environment from ``environments``;
    ``BLIZZARD_LEASE_ID`` and ``BLIZZARD_RUNNER_URL`` ride it from ``lease_id`` and
    ``local_api_url`` so the worker's ``PostToolUse`` heartbeat hook posts to the
    right lease with no arguments.

    ``workspace_root`` is the spawn **cwd** (issue #17): the worker is launched at the
    winter workspace root — not an env subdir — so it loads the workspace's shared
    context the way an interactive agent there does; empty falls back to the first
    environment's workdir (legacy behavior). ``prompt_prefix`` is the runner-composed
    workspace prompt + info table the adapter prepends to the envelope prompt (rendered
    by :func:`blizzard.runner.harness.preamble.render_worker_preamble`); empty prepends
    nothing.

    ``stdout_path`` (epic #57) is the per-lease file the spawned worker's stdout is
    redirected to, so a killed/reaped worker's result envelope survives the process
    for :meth:`IHarnessAdapter.parse_usage` to read back later — the path is
    **injected**, never computed inside the adapter (``bzh:dependency-injection`);
    the runner composition root resolves the concrete path (phase 2 of issue #58).
    Empty keeps today's behavior (stdout discarded).

    ``lease_token`` (issue #113) is the lease's minted capability-token plaintext,
    ridden into the spawn env as ``BLIZZARD_LEASE_TOKEN`` alongside ``BLIZZARD_
    LEASE_ID`` — a per-spawn identity var scoped to this worker's own lease, never
    a daemon secret (``bzh:worker-env-allowlist``). This phase mints and carries it
    only; no caller yet authorizes anything against it.
    """

    environments: list[AcquiredEnvironment]
    lease_id: str
    local_api_url: str
    workspace_root: str = ""
    prompt_prefix: str = ""
    stdout_path: str = ""
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
    ) -> WorkerHandle:
        """Start a headless worker; return its session id, pid, and start time.

        ``resume_from`` (issue #115) is the prior session id a node-entry resume
        continues; ``None`` is today's fresh spawn (``session_hint`` mints/honors a
        brand-new id). The returned :class:`WorkerHandle`'s ``session_id`` is the
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
    ) -> int:
        """Headless resume-with-message; returns the new pid. Kill first.

        The fire-and-forget resume behind answer delivery and the CI feedback loop
        (P7). The two-phase judgement elicitation — which needs the reply captured
        synchronously for :meth:`parse_verdict` — is :meth:`judge`.

        ``stdout_path`` (epic #57) is the injected per-lease file the resumed
        worker's stdout is redirected to, mirroring :attr:`WorkerPreamble.stdout_path`
        — this operation has no preamble to carry it on, so it rides as a direct
        param instead. Empty keeps today's behavior (stdout inherited).

        ``preamble`` re-supplies the per-lease worker identity (lease id, runner URL,
        held envs, and a freshly re-minted capability token) so the resumed worker can
        ``blizzard runner attach`` and its heartbeat/SessionEnd hooks can post —
        ``--resume`` inherits none of the spawn env. ``chunk_id`` names the lease's
        chunk for ``BLIZZARD_CHUNK_ID``. Both omitted (the selftest/CI resume, which
        speaks to no live lease) keeps the identity-less allowlist env.
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
    ) -> str:
        """Deliver the judgement prompt into the session and return the raw reply.

        The synchronous half of the two-phase node judgement: resumes the session
        headlessly with the engine-composed judgement prompt (base prose + the
        ``<Choice>`` elicitation tail) and returns the harness-native output the
        loop hands to :meth:`parse_verdict`. Separated from
        :meth:`resume_with_message` because the verdict elicitation must capture the
        reply, where async message delivery only needs the new pid.

        ``preamble`` re-supplies the per-lease worker identity exactly as it does on
        :meth:`resume_with_message` — the judgement turn runs its own
        ``blizzard runner attach`` (a node's ``judgement_prompt`` elicits the
        ``retrospective``), and ``--resume`` inherits none of the spawn env, so
        without it the attach cannot reach the runner. ``chunk_id`` names the
        lease's chunk for ``BLIZZARD_CHUNK_ID``. Both omitted (the selftest, which
        speaks to no live lease) keeps the identity-less allowlist env.
        """
        ...

    def resume_command(self, workdir: str, session_id: str) -> str:
        """The literal interactive-takeover shell command for the escalation record."""
        ...

    def parse_verdict(self, output: str) -> str | None:
        """Parse the ``<Choice>{name}</Choice>`` reply into a choice name, else ``None``."""
        ...

    def parse_assessment(self, output: str) -> str:
        """Parse the judgement reply's free-text assessment — the payload after the Choice.

        The verdict reply is ``<Choice>{name}</Choice>`` plus the worker's prose
        assessment of the node's checks. A node that
        ``produces`` an **asset** (the review node's findings) carries that assessment
        as the asset's content; the core harvests it into the completion. Empty string
        when the reply carries no assessment."""
        ...

    def parse_usage(self, output: str, kind: UsageKind) -> UsageSample | None:
        """Translate a result envelope's ``usage`` + ``total_cost_usd`` into a sample.

        ``kind`` names which invocation produced ``output`` (the caller knows —
        spawn, resume, or judge — the adapter never infers it). Returns ``None``
        when ``output`` carries no result envelope at all (e.g. a killed worker
        whose process never reached completion) — the caller's cue to fall back to
        :meth:`sum_transcript_usage`. Dumb translation only (``bzh:deterministic-
        shell``): never a model call, never a cost estimate — cost rides verbatim
        off the harness's own ``total_cost_usd``.
        """
        ...

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind) -> UsageSample:
        """Sum per-message ``usage`` across a session transcript's raw JSONL lines.

        The envelope-less fallback: when a worker is killed/reaped before it ever
        produces a result envelope, its transcript still carries a ``usage`` object
        on every assistant message — summed here into token counts with
        ``cost_usd=None`` (a transcript carries no dollar figure). Takes
        already-read lines, mirroring :func:`blizzard.runner.transcripts.parser.
        parse_turns`'s ``lines: list[str]`` shape, so the file locate/read step
        (:mod:`blizzard.runner.transcripts.internal.jsonl_transcript_repository`)
        is never duplicated here.
        """
        ...
