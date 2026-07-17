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

Adapters stay dumb (``bzh:deterministic-shell``): ``parse_verdict`` returns the
choice *name*, not a graph decision — resolving it to an edge is the core's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.wire.envelope import NodeEnvelope


@dataclass(frozen=True)
class WorkerPreamble:
    """The runner's machine-local preamble prepended to the envelope (D-063, issue #17).

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
    """

    environments: list[AcquiredEnvironment]
    lease_id: str
    local_api_url: str
    workspace_root: str = ""
    prompt_prefix: str = ""


@dataclass(frozen=True)
class WorkerHandle:
    """What ``spawn`` returns — the facts recorded at spawn-return."""

    session_id: str  # harness-assigned where it self-assigns, else the honored hint
    pid: int
    process_start_time: str  # stable across pid reuse — REAP keys on (pid, start_time)


class IHarnessAdapter(Protocol):
    """The coding-harness seam. Dumb: translates, never decides."""

    def spawn(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_hint: str | None) -> WorkerHandle:
        """Start a headless worker; return its session id, pid, and start time."""
        ...

    def resume_with_message(self, environment_id: str, session_id: str, message: str) -> int:
        """Headless resume-with-message; returns the new pid. Kill first.

        The fire-and-forget resume behind answer delivery and the CI feedback loop
        (P7). The two-phase judgement elicitation — which needs the reply captured
        synchronously for :meth:`parse_verdict` — is :meth:`judge`.
        """
        ...

    def judge(self, environment_id: str, session_id: str, judgement_prompt: str) -> str:
        """Deliver the judgement prompt into the session and return the raw reply.

        The synchronous half of the two-phase node judgement: resumes the session
        headlessly with the engine-composed judgement prompt (base prose + the
        ``<Choice>`` elicitation tail) and returns the harness-native output the
        loop hands to :meth:`parse_verdict`. Separated from
        :meth:`resume_with_message` because the verdict elicitation must capture the
        reply, where async message delivery only needs the new pid.
        """
        ...

    def resume_command(self, environment_id: str, session_id: str) -> str:
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
