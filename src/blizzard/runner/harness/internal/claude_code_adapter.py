"""The Claude Code adapter binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.harness.adapter.IHarnessAdapter` against the
``claude`` non-interactive CLI:

* **spawn** — ``<binary> -p --output-format json --session-id <sid> --settings
  <worker-settings> <prompt>`` launched headless (fire-and-forget). Claude honors
  the pre-assigned ``--session-id``, so the returned session id is the hint; the pid
  and its start time are stamped from the parent right after launch.
* **judge** — ``<binary> -p --output-format json --resume <sid> [--permission-mode
  <mode>] <prompt>`` run synchronously, returning the raw reply for
  :meth:`parse_verdict` (the two-phase judgement elicitation). Kill-then-resume:
  never run against a live process. ``--permission-mode`` is reasserted on this
  resume exactly as ``spawn``/``resume_with_message`` do: the flag is per-invocation,
  not session-sticky, so a resume that omits it drops the session back to the
  settings-resolved default — silently denying the judgement turn's own
  ``blizzard runner attach`` (the ``retrospective`` a node's ``judgement_prompt``
  elicits) in a headless session that has no one to approve it.
* **resume_with_message** — the fire-and-forget resume (answer delivery / CI, P7).
* **resume_command** — the literal interactive takeover command for the escalation
  record.
* **parse_verdict** — extract the ``<Choice>{name}</Choice>`` from the harness-native
  output; missing/unparseable → ``None`` (a failure to the core).
* **parse_usage** — a result envelope's ``usage`` + ``total_cost_usd``, translated
  into a :class:`~blizzard.runner.harness.usage.UsageSample`; ``None`` when no
  envelope is present. **sum_transcript_usage** is the envelope-less fallback,
  summing per-message ``usage`` off the raw session transcript (``cost_usd`` always
  ``None`` there — a transcript carries no dollar figure). Both epic #57.

``spawn``/``resume_with_message`` redirect the worker's stdout to an **injected**
per-lease file (``preamble.stdout_path`` / the ``stdout_path`` param) rather than
discarding it, so a killed/reaped worker's result envelope survives the process for
``parse_usage`` to read back later; empty keeps the prior discard/inherit behavior.

In verification ``binary`` points at the ``blizzard-mock`` ``mock-claude-code``
façade (the prompt is a behavior script it ``exec``s), so the seam is exercised
against a realistic CLI with no tokens. The identity variables ride the spawn
environment (``BLIZZARD_LEASE_ID`` / ``BLIZZARD_SESSION_ID`` / ``BLIZZARD_ENV_IDS``);
the mock fence variable (``BLIZZARD_MOCK_HARNESS_FENCE``) is supplied by the test
scaffolding's declared ``worker_env_passthrough``, not by this adapter.
Confined to ``internal/`` (``bzh:dependency-inversion``).

Every child env — spawn, judge, resume — is built by :func:`_allowlisted_env`
(``bzh:worker-env-allowlist``): a fixed base allowlist plus the operator's declared
``env_passthrough``, never a full ``os.environ`` copy, so a daemon secret (foremost
``BZ_HUB_TOKEN``) is absent from a worker/judge/resume child by construction.

The first positional of ``judge`` / ``resume_with_message`` / ``resume_command`` is
the **working directory** — the provider-returned workdir the runner resolves from
the chunk→env binding and supplies for the op.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
from collections.abc import Iterator, Sequence
from typing import IO

from blizzard.foundation.logging import get_logger
from blizzard.foundation.process import read_process_start_time
from blizzard.runner.harness.adapter import (
    IHarnessAdapter,
    WorkerHandle,
    WorkerPreamble,
)
from blizzard.runner.harness.spawn_cwd import resolve_spawn_cwd
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.wire.envelope import NodeEnvelope

_log = get_logger("blizzard.runner.harness")

_CHOICE_OPEN = "<Choice>"
_CHOICE_CLOSE = "</Choice>"

# The worker spawn-environment allowlist's base (`bzh:worker-env-allowlist`): what a
# child process needs to locate/run its interpreter and behave predictably in a
# headless shell, determined empirically against the real `claude` harness on the
# dogfooding fleet. Deliberately conservative — an operator widens it via
# `[worker] env_passthrough` (`RunnerConfig.worker_env_passthrough`) rather than this
# list growing ad hoc.
_BASE_ALLOWLIST_VARS: tuple[str, ...] = ("PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR")
# `LC_*` locale vars are a family, not a fixed set of names, so they are matched by
# prefix rather than enumerated in `_BASE_ALLOWLIST_VARS`.
_LOCALE_PREFIX = "LC_"


def _allowlisted_env(passthrough: Sequence[str]) -> dict[str, str]:
    """The child env built from the base allowlist + `LC_*` + the operator's passthrough.

    Never a full `os.environ` copy (`bzh:worker-env-allowlist`): everything not named
    here — foremost a daemon credential like `BZ_HUB_TOKEN` — is absent from a
    worker/judge/resume child by construction. The one function all three subprocess
    env constructions build from.
    """
    names = set(_BASE_ALLOWLIST_VARS) | set(passthrough)
    env = {name: os.environ[name] for name in names if name in os.environ}
    env.update((k, v) for k, v in os.environ.items() if k.startswith(_LOCALE_PREFIX))
    return env


# The model every fleet worker runs on. Pinned so a spawn never inherits the
# operator's ambient ``claude`` default (which can resolve to a lightweight model
# unfit for the build/review work). Opus is the fleet's standing choice; override
# per-adapter via the ``model`` constructor argument.
DEFAULT_WORKER_MODEL = "claude-opus-4-8"


class HarnessSpawnError(RuntimeError):
    """The harness binary could not be launched (missing binary, bad workdir)."""


def _result_envelope(output: str) -> dict[str, object] | None:
    """The last JSON-object line carrying a ``result`` key, else ``None``.

    The one JSON-line-scanning walk shared by ``_result_text`` (``parse_verdict``/
    ``parse_assessment``'s plumbing) and ``parse_usage`` — a killed/verdict-less
    worker's stdout can carry partial or non-JSON lines ahead of (or instead of)
    the final envelope, so scanning in reverse and skipping anything that fails to
    parse as a ``result``-bearing object is the one tolerant rule both callers need.
    """
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(envelope, dict) and "result" in envelope:
            return envelope
    return None


@contextlib.contextmanager
def _stdout_target(path: str) -> Iterator[IO[bytes] | None]:
    """The injected per-lease stdout file, opened for append, else ``None`` (no redirect).

    A shared context manager so ``spawn``/``resume_with_message`` never leak the file
    descriptor across a failed ``Popen`` (``bzh:dependency-injection`` — the path is
    always supplied by the caller, never computed here).
    """
    if not path:
        yield None
        return
    with open(path, "ab") as f:
        yield f


class ClaudeCodeAdapter:
    """The Claude Code binding. Dumb: translates the CLI surface, never decides."""

    def __init__(
        self,
        binary: str = "claude",
        *,
        settings_path: str | None = None,
        permission_mode: str | None = None,
        model: str = DEFAULT_WORKER_MODEL,
        env_passthrough: Sequence[str] = (),
    ) -> None:
        self._binary = binary
        self._settings_path = settings_path
        # The model passed to every ``claude`` spawn. Pinned so a worker never
        # falls through to the operator's ambient default; defaults to Opus.
        self._model = model
        # The headless permission mode passed to ``claude -p``. A non-interactive
        # worker has no one to approve tool use, so ``default`` mode blocks every edit and
        # non-trivial bash — the worker can inspect but never build. A workspace-isolated
        # runner sets ``bypassPermissions`` so the sandboxed worktree worker can edit,
        # run git/checks, commit, and push unattended. ``None`` omits the flag (the
        # ``mock-claude-code`` façade takes no such flag).
        self._permission_mode = permission_mode
        # The operator's declared extension to the spawn-environment allowlist (issue
        # #88, `RunnerConfig.worker_env_passthrough`) — forwarded to every worker/judge/
        # resume child alongside the fixed base allowlist.
        self._env_passthrough = tuple(env_passthrough)

    def spawn(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_hint: str | None) -> WorkerHandle:
        if not preamble.environments:
            raise HarnessSpawnError("spawn requires at least one acquired environment")
        session_id = session_hint or ""
        # Spawn cwd is the winter workspace root (issue #17) so the worker loads the
        # workspace's shared context (CLAUDE.md/AGENTS.md, .winter/, every repo and env)
        # like an interactive agent there; the held env(s) are named in the preamble
        # prompt instead. Falls back to the first env's workdir when no root is supplied.
        # `resolve_spawn_cwd` is the rule's one owner (issue #29) — the transcript
        # locator is its second caller. `preamble.environments` was checked non-empty
        # above, so the fallback is always a real workdir here; `| None` on the return
        # type is for that second caller, whose fallback can legitimately be absent.
        workdir = resolve_spawn_cwd(preamble.workspace_root, preamble.environments[0].workdir)
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
        # Stdout rides to the injected per-lease file (epic #57) so the result
        # envelope survives the process for `parse_usage` — never computed here
        # (`bzh:dependency-injection`); empty keeps today's DEVNULL behavior.
        with _stdout_target(preamble.stdout_path) as stdout_file:
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=workdir,
                    env=env,
                    stdout=stdout_file if stdout_file is not None else subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                _log.error("harness spawn failed", binary=self._binary, cwd=workdir, detail=str(exc))
                raise HarnessSpawnError(f"failed to spawn {self._binary} in {workdir}: {exc}") from exc

        start_time = read_process_start_time(proc.pid) or ""
        _log.info("spawned worker", binary=self._binary, pid=proc.pid, session_id=session_id, cwd=workdir)
        return WorkerHandle(session_id=session_id, pid=proc.pid, process_start_time=start_time)

    def judge(self, environment_id: str, session_id: str, judgement_prompt: str) -> str:
        cmd = [self._binary, "-p", "--output-format", "json", "--resume", session_id]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        cmd.append(judgement_prompt)
        result = subprocess.run(
            cmd, cwd=environment_id, capture_output=True, text=True, env=_allowlisted_env(self._env_passthrough)
        )
        _log.info("judgement resume", pid_returncode=result.returncode, session_id=session_id, cwd=environment_id)
        return result.stdout

    def resume_with_message(self, environment_id: str, session_id: str, message: str, stdout_path: str = "") -> int:
        cmd = [self._binary, "-p", "--output-format", "json", "--resume", session_id]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        cmd.append(message)
        # Injected per-lease file (epic #57), mirroring `spawn`'s `preamble.stdout_path` —
        # this op has no preamble, so the path rides as a direct param; empty keeps
        # today's behavior (stdout inherited).
        with _stdout_target(stdout_path) as stdout_file:
            proc = subprocess.Popen(
                cmd, cwd=environment_id, env=_allowlisted_env(self._env_passthrough), stdout=stdout_file
            )
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
        """The reply text following ``</Choice>`` — the worker's prose assessment."""
        text = self._result_text(output)
        close = text.find(_CHOICE_CLOSE)
        if close == -1:
            return ""
        return text[close + len(_CHOICE_CLOSE) :].strip()

    def parse_usage(self, output: str, kind: UsageKind) -> UsageSample | None:
        envelope = _result_envelope(output)
        if envelope is None:
            return None
        usage = envelope.get("usage")
        if not isinstance(usage, dict):
            return None
        cost = envelope.get("total_cost_usd")
        model = envelope.get("model")
        return UsageSample(
            kind=kind,
            model=str(model) if isinstance(model, str) and model else self._model,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_create_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
        )

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind) -> UsageSample:
        input_tokens = output_tokens = cache_read_tokens = cache_create_tokens = 0
        model = self._model
        for raw_line in lines:
            line = raw_line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("type") != "assistant":
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            record_model = message.get("model")
            if isinstance(record_model, str) and record_model:
                model = record_model
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            input_tokens += int(usage.get("input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            cache_read_tokens += int(usage.get("cache_read_input_tokens") or 0)
            cache_create_tokens += int(usage.get("cache_creation_input_tokens") or 0)
        return UsageSample(
            kind=kind,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            cost_usd=None,
        )

    # --- plumbing -----------------------------------------------------------

    @staticmethod
    def _result_text(output: str) -> str:
        """The assistant's final message: the ``result`` field of the JSON envelope, else raw."""
        envelope = _result_envelope(output)
        return str(envelope["result"]) if envelope is not None else output

    def _spawn_env(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_id: str) -> dict[str, str]:
        env = _allowlisted_env(self._env_passthrough)
        env["BLIZZARD_ENV_IDS"] = ",".join(e.environment_id for e in preamble.environments)
        env["BLIZZARD_ENV_WORKDIRS"] = ",".join(e.workdir for e in preamble.environments)
        env["BLIZZARD_SESSION_ID"] = session_id
        env["BLIZZARD_CHUNK_ID"] = envelope.chunk_id
        # Runner-minted identity the PostToolUse heartbeat hook inherits (per process
        # tree, so a sibling worker cannot misattribute a beat).
        env["BLIZZARD_LEASE_ID"] = preamble.lease_id
        env["BLIZZARD_RUNNER_URL"] = preamble.local_api_url
        env["BLIZZARD_LEASE_TOKEN"] = preamble.lease_token
        # The ask channel: the worker records an undecidable choice by
        # running ``blizzard runner ask`` against the local API above, then exits. Real
        # Claude Code invokes it per the node-prompt convention; the blizzard-mock façade
        # shells out to whatever ``BLIZZARD_RUNNER_ASK_CMD`` names, so wiring the real
        # command here is what lets the mock exercise the true ask path (verified e2e).
        env.setdefault("BLIZZARD_RUNNER_ASK_CMD", "blizzard runner ask")
        return env


def _conforms_harness_adapter(x: ClaudeCodeAdapter) -> IHarnessAdapter:
    return x
