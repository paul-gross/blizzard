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

# The model every fleet worker runs on. Pinned so a spawn never inherits the
# operator's ambient ``claude`` default (which can resolve to a lightweight model
# unfit for the build/review work). Opus is the fleet's standing choice; override
# per-adapter via the ``model`` constructor argument.
DEFAULT_WORKER_MODEL = "claude-opus-4-8"


class HarnessSpawnError(RuntimeError):
    """The harness binary could not be launched (missing binary, bad workdir)."""


class ClaudeCodeAdapter:
    """The Claude Code binding. Dumb: translates the CLI surface, never decides."""

    def __init__(
        self,
        binary: str = "claude",
        *,
        settings_path: str | None = None,
        permission_mode: str | None = None,
        model: str = DEFAULT_WORKER_MODEL,
    ) -> None:
        self._binary = binary
        self._settings_path = settings_path
        # The model passed to every ``claude`` spawn (D-092). Pinned so a worker never
        # falls through to the operator's ambient default; defaults to Opus.
        self._model = model
        # The headless permission mode passed to ``claude -p`` (D-092). A non-interactive
        # worker has no one to approve tool use, so ``default`` mode blocks every edit and
        # non-trivial bash — the worker can inspect but never build. A workspace-isolated
        # runner sets ``bypassPermissions`` so the sandboxed worktree worker can edit,
        # run git/checks, commit, and push unattended. ``None`` omits the flag (the
        # ``mock-claude-code`` façade takes no such flag).
        self._permission_mode = permission_mode

    def spawn(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_hint: str | None) -> WorkerHandle:
        if not preamble.environments:
            raise HarnessSpawnError("spawn requires at least one acquired environment")
        session_id = session_hint or ""
        # Spawn cwd is the winter workspace root (issue #17) so the worker loads the
        # workspace's shared context (CLAUDE.md/AGENTS.md, .winter/, every repo and env)
        # like an interactive agent there; the held env(s) are named in the preamble
        # prompt instead. Falls back to the first env's workdir when no root is supplied.
        workdir = preamble.workspace_root or preamble.environments[0].workdir
        cmd = [self._binary, "-p", "--output-format", "json", "--model", self._model]
        if session_id:
            cmd += ["--session-id", session_id]
        if self._settings_path:
            cmd += ["--settings", self._settings_path]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        # The runner's workspace prompt + machine-local info table, prepended to the hub's
        # node prompt (issue #17). The preamble is composed in the core; the adapter only
        # concatenates it ahead of the envelope prompt (``bzh:deterministic-shell``).
        cmd.append("\n\n".join(part for part in (preamble.prompt_prefix, envelope.prompt or "") if part))

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
        cmd = [self._binary, "-p", "--resume", session_id]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        cmd.append(message)
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
        # Runner-minted identity the PostToolUse heartbeat hook inherits (per process
        # tree, so a sibling worker cannot misattribute a beat — design/harness-adapters.md).
        env["BLIZZARD_LEASE_ID"] = preamble.lease_id
        env["BLIZZARD_RUNNER_URL"] = preamble.local_api_url
        # The ask channel ([ask-answer.md]): the worker records an undecidable choice by
        # running ``blizzard runner ask`` against the local API above, then exits. Real
        # Claude Code invokes it per the node-prompt convention; the blizzard-mock façade
        # shells out to whatever ``BLIZZARD_RUNNER_ASK_CMD`` names, so wiring the real
        # command here is what lets the mock exercise the true ask path (verified e2e).
        env.setdefault("BLIZZARD_RUNNER_ASK_CMD", "blizzard runner ask")
        return env


def _conforms_harness_adapter(x: ClaudeCodeAdapter) -> IHarnessAdapter:
    return x
