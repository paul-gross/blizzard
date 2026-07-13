"""The coding-harness adapter seam (D-038/D-050/D-056/D-092).

Four operations cover every headless-run + persisted-session + resume harness:

* ``spawn`` starts a headless worker pointed at the chunk's environments, primed
  with the node envelope plus the runner's machine-local preamble — the held env
  ids and their workdirs (D-063) — and returns the **actual** session id with the
  pid and process start time, recorded as facts at spawn-return (D-092).
* ``resume_with_message`` delivers a message into an existing session headlessly
  and returns the new pid (D-050) — the operation behind the judgement prompt
  (D-038), answer delivery, and the CI feedback loop. Never run against a live
  process — kill first.
* ``resume_command`` returns the literal shell command a human runs to resume the
  session interactively (the escalation record's takeover command).
* ``parse_verdict`` parses the judgement-resume reply into the selected choice name
  (D-042/D-056) — a missing or unparseable ``<Choice>`` is ``None``, which the core
  treats as a failure (D-009).

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
    """The runner's machine-local preamble prepended to the envelope (D-063).

    The held environments with their workdirs — never sent to the hub; it is
    machine-local execution truth. ``BLIZZARD_ENV_IDS`` rides the spawn environment
    from this (D-063).
    """

    environments: list[AcquiredEnvironment]


@dataclass(frozen=True)
class WorkerHandle:
    """What ``spawn`` returns — the facts recorded at spawn-return (D-092)."""

    session_id: str  # harness-assigned where it self-assigns, else the honored hint
    pid: int
    process_start_time: str  # stable across pid reuse — REAP keys on (pid, start_time)


class IHarnessAdapter(Protocol):
    """The coding-harness seam (D-092). Dumb: translates, never decides."""

    def spawn(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_hint: str | None) -> WorkerHandle:
        """Start a headless worker; return its session id, pid, and start time (D-092)."""
        ...

    def resume_with_message(self, environment_id: str, session_id: str, message: str) -> int:
        """Headless resume-with-message; returns the new pid (D-050). Kill first.

        The fire-and-forget resume behind answer delivery and the CI feedback loop
        (P7). The two-phase judgement elicitation — which needs the reply captured
        synchronously for :meth:`parse_verdict` — is :meth:`judge`.
        """
        ...

    def judge(self, environment_id: str, session_id: str, judgement_prompt: str) -> str:
        """Deliver the judgement prompt into the session and return the raw reply (D-038).

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
        """Parse the ``<Choice>{name}</Choice>`` reply into a choice name, else ``None`` (D-009)."""
        ...
