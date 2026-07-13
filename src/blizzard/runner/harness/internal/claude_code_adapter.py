"""The Claude Code adapter binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.harness.adapter.IHarnessAdapter` against the
``claude`` non-interactive CLI (design/harness-adapters.md):

* **spawn** — ``<binary> -p --output-format json --session-id <sid> --settings
  <worker-settings> <prompt>`` launched headless (fire-and-forget). Claude honors
  the pre-assigned ``--session-id``, so the returned session id is the hint; the pid
  and its start time are stamped from the parent right after launch (D-092).
* **judge** — ``<binary> -p --output-format json --resume <sid> <prompt>`` run
  synchronously, returning the raw reply for :meth:`parse_verdict` (the two-phase
  judgement elicitation, D-038). Kill-then-resume: never run against a live process.
* **resume_with_message** — the fire-and-forget resume (answer delivery / CI, P7).
* **resume_command** — the literal interactive takeover command for the escalation
  record.
* **parse_verdict** — extract the ``<Choice>{name}</Choice>`` from the harness-native
  output; missing/unparseable → ``None`` (a failure to the core, D-009).

In verification ``binary`` points at the ``blizzard-mock`` ``mock-claude-code``
façade (the prompt is a behavior script it ``exec``s), so the seam is exercised
against a realistic CLI with no tokens. The identity variables ride the spawn
environment (``BLIZZARD_LEASE_ID`` / ``BLIZZARD_SESSION_ID`` / ``BLIZZARD_ENV_IDS``);
the mock fence variables are supplied by the test scaffolding, not by this adapter.
Confined to ``internal/`` (``bzh:dependency-inversion``).

The first positional of ``judge`` / ``resume_with_message`` / ``resume_command`` is
the **working directory** — the provider-returned workdir the runner resolves from
the chunk→env binding (design/runner/environments.md) and supplies for the op.
"""

from __future__ import annotations

import json
import os
import subprocess

from blizzard.foundation.logging import get_logger
from blizzard.foundation.process import read_process_start_time
from blizzard.runner.harness.adapter import (
    IHarnessAdapter,
    WorkerHandle,
    WorkerPreamble,
)
from blizzard.wire.envelope import NodeEnvelope

_log = get_logger("blizzard.runner.harness")

_CHOICE_OPEN = "<Choice>"
_CHOICE_CLOSE = "</Choice>"


class HarnessSpawnError(RuntimeError):
    """The harness binary could not be launched (missing binary, bad workdir)."""


class ClaudeCodeAdapter:
    """The Claude Code binding. Dumb: translates the CLI surface, never decides."""

    def __init__(self, binary: str = "claude", *, settings_path: str | None = None) -> None:
        self._binary = binary
        self._settings_path = settings_path

    def spawn(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_hint: str | None) -> WorkerHandle:
        if not preamble.environments:
            raise HarnessSpawnError("spawn requires at least one acquired environment")
        session_id = session_hint or ""
        workdir = preamble.environments[0].workdir
        cmd = [self._binary, "-p", "--output-format", "json"]
        if session_id:
            cmd += ["--session-id", session_id]
        if self._settings_path:
            cmd += ["--settings", self._settings_path]
        cmd.append(envelope.prompt or "")

        env = self._spawn_env(envelope, preamble, session_id)
        try:
            proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            _log.error("harness spawn failed", binary=self._binary, cwd=workdir, detail=str(exc))
            raise HarnessSpawnError(f"failed to spawn {self._binary} in {workdir}: {exc}") from exc

        start_time = read_process_start_time(proc.pid) or ""
        _log.info("spawned worker", binary=self._binary, pid=proc.pid, session_id=session_id, cwd=workdir)
        return WorkerHandle(session_id=session_id, pid=proc.pid, process_start_time=start_time)

    def judge(self, environment_id: str, session_id: str, judgement_prompt: str) -> str:
        cmd = [self._binary, "-p", "--output-format", "json", "--resume", session_id, judgement_prompt]
        result = subprocess.run(cmd, cwd=environment_id, capture_output=True, text=True, env=os.environ.copy())
        _log.info("judgement resume", pid_returncode=result.returncode, session_id=session_id, cwd=environment_id)
        return result.stdout

    def resume_with_message(self, environment_id: str, session_id: str, message: str) -> int:
        cmd = [self._binary, "-p", "--resume", session_id, message]
        proc = subprocess.Popen(cmd, cwd=environment_id, env=os.environ.copy())
        return proc.pid

    def resume_command(self, environment_id: str, session_id: str) -> str:
        return f"cd {environment_id} && {self._binary} --resume {session_id}"

    def parse_verdict(self, output: str) -> str | None:
        text = self._result_text(output)
        start = text.find(_CHOICE_OPEN)
        if start == -1:
            return None
        end = text.find(_CHOICE_CLOSE, start)
        if end == -1:
            return None
        name = text[start + len(_CHOICE_OPEN) : end].strip()
        return name or None

    def parse_assessment(self, output: str) -> str:
        """The reply text following ``</Choice>`` — the worker's prose assessment (D-077)."""
        text = self._result_text(output)
        close = text.find(_CHOICE_CLOSE)
        if close == -1:
            return ""
        return text[close + len(_CHOICE_CLOSE) :].strip()

    # --- plumbing -----------------------------------------------------------

    @staticmethod
    def _result_text(output: str) -> str:
        """The assistant's final message: the ``result`` field of the JSON envelope, else raw."""
        for line in reversed(output.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(envelope, dict) and "result" in envelope:
                return str(envelope["result"])
        return output

    def _spawn_env(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_id: str) -> dict[str, str]:
        env = os.environ.copy()
        env["BLIZZARD_ENV_IDS"] = ",".join(e.environment_id for e in preamble.environments)
        env["BLIZZARD_ENV_WORKDIRS"] = ",".join(e.workdir for e in preamble.environments)
        env["BLIZZARD_SESSION_ID"] = session_id
        env["BLIZZARD_CHUNK_ID"] = envelope.chunk_id
        return env


def _conforms_harness_adapter(x: ClaudeCodeAdapter) -> IHarnessAdapter:
    return x
