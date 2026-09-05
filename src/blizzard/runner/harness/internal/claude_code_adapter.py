"""The Claude Code adapter binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.harness.adapter.IHarnessAdapter` against the ``claude``
non-interactive CLI. ``--permission-mode`` and ``--settings`` are per-invocation, not
session-sticky, so each is reasserted on every resume. Every child env comes from
:class:`AllowlistedEnv`."""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Any

from blizzard.foundation.logging import get_logger
from blizzard.foundation.process import ProcStat
from blizzard.runner.harness.adapter import (
    HarnessSpawnError,
    IHarnessAdapter,
    WorkerHandle,
    WorkerPreamble,
)
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NullTranscriptSource
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.wire.envelope import NodeEnvelope

_log = get_logger("blizzard.runner.harness")

_CHOICE_OPEN = "<Choice>"
_CHOICE_CLOSE = "</Choice>"

# The model a worker runs on when nothing expressed a preference, pinned so a spawn never
# inherits the operator's ambient default.
DEFAULT_WORKER_MODEL = "claude-opus-5"

# The namespaced tier-alias prefix (issue #144): an entry carrying it is a *role*, resolved
# through the table below; one without it is a harness-native name.
_TIER_PREFIX = "blizzard:"

# Built-in tier mappings, so a zero-config runner resolves the standard tiers; overridden
# entry-by-entry by the runner's own table. Unordered roles, not a scale.
_BUILTIN_TIERS = {
    "blizzard:frontier": "fable",
    "blizzard:advanced": "opus",
    "blizzard:basic": "sonnet",
}

# The native names recognized without a tier alias — which is what lets an unrecognized
# one be **skipped** as another harness's rather than handed to a CLI that rejects it.
_NATIVE_SHORT_NAMES = frozenset({"fable", "opus", "sonnet", "haiku"})
_NATIVE_PREFIX = "claude-"

# The well-known effort ordinal, extended by the runner's ``[effort.aliases]`` — which is
# also how a deployment reaches a native tier outside the ordinal.
_EFFORT_ORDINAL = frozenset({"low", "medium", "high", "max"})

# `--autocompact`'s own vocabulary shape (blizzard#343): a recognition check, not the
# CLI's own 100k-1M range (enforced CLI-side, never re-implemented here).
_COMPACTION_WINDOW_RE = re.compile(r"auto|[0-9]+[kK]?")


@dataclass(frozen=True)
class ResultEnvelope:
    """A worker invocation's final ``--output-format json`` envelope.

    A killed worker's stdout can carry partial or non-JSON lines ahead of (or instead of) the
    final envelope, so :meth:`of` scans in reverse and skips anything that fails to parse."""

    fields: Mapping[str, Any]

    @classmethod
    def of(cls, output: str) -> ResultEnvelope | None:
        for raw_line in reversed(output.splitlines()):
            line = raw_line.strip()
            if not line.startswith("{"):
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict) and "result" in decoded:
                return cls(decoded)
        return None

    @property
    def result(self) -> str:
        return str(self.fields["result"])

    @property
    def usage(self) -> Mapping[str, Any] | None:
        usage = self.fields.get("usage")
        return usage if isinstance(usage, dict) else None

    @property
    def model(self) -> str | None:
        model = self.fields.get("model")
        return model if isinstance(model, str) and model else None

    @property
    def cost_usd(self) -> float | None:
        cost = self.fields.get("total_cost_usd")
        return float(cost) if isinstance(cost, int | float) else None


@contextlib.contextmanager
def _stdout_target(path: str) -> Iterator[IO[bytes] | None]:
    """The injected per-lease stdout file, opened for append, else ``None`` (no redirect).

    A context manager so no caller leaks the descriptor across a failed ``Popen``; the
    path is always supplied, never computed here (``bzh:dependency-injection``)."""
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
        model_aliases: Sequence[tuple[str, str]] = (),
        effort_aliases: Sequence[tuple[str, str]] = (),
        transcript_source: IHarnessTranscriptSource | None = None,
    ) -> None:
        self._binary = binary
        self._settings_path = settings_path
        self._model = model
        # The runner's own tier tables (issue #144, ``[models.aliases]`` /
        # ``[effort.aliases]``), overriding this adapter's built-ins entry by entry.
        self._model_aliases = dict(model_aliases)
        self._effort_aliases = dict(effort_aliases)
        # Values already logged as unrecognized, so the notice fires once per value.
        self._unrecognized_efforts: set[str] = set()
        self._unrecognized_compaction_windows: set[str] = set()
        # A non-interactive worker has no one to approve tool use, so the default mode
        # lets it inspect but never build. ``None`` omits the flag.
        self._permission_mode = permission_mode
        # The declared extension to the spawn-environment allowlist (issue #88), forwarded
        # to every child alongside the fixed base allowlist.
        self._env_passthrough = tuple(env_passthrough)
        # Injected, never self-constructed (`bzh:dependency-injection`); the null source
        # serves the construction sites that need no real one.
        self._transcript_source: IHarnessTranscriptSource = transcript_source or NullTranscriptSource()

    def resolve_model(self, preferences: Sequence[str]) -> str:
        """Left-to-right; first entry that resolves wins; unresolvable entries skipped."""
        skipped: list[str] = []
        for entry in preferences:
            resolved = self._resolve_one_model(entry)
            if resolved is not None:
                if skipped:
                    _log.info("skipped unresolvable model preferences", skipped=skipped, resolved=resolved)
                return resolved
            skipped.append(entry)
        if skipped:
            # Never a spawn failure: an all-unresolvable list is what a mixed-harness
            # fleet produces, so fall back and say so.
            _log.info(
                "no model preference resolved; falling back to the adapter default",
                skipped=skipped,
                fallback=self._model,
            )
        return self._model

    def _resolve_one_model(self, entry: str) -> str | None:
        """One preference entry to a native name, or ``None`` if this adapter cannot."""
        if entry.startswith(_TIER_PREFIX):
            # Runner config first, adapter built-ins second — an operator's table
            # overrides the shipped tier defaults rather than merging with them.
            return self._model_aliases.get(entry) or _BUILTIN_TIERS.get(entry)
        # A non-namespaced entry is a harness-native name. It may still be aliased (an
        # operator naming their own shorthand), so the table is consulted first.
        if entry in self._model_aliases:
            return self._model_aliases[entry]
        if entry in _NATIVE_SHORT_NAMES or entry.startswith(_NATIVE_PREFIX):
            return entry
        return None

    def resolve_effort(self, value: str | None) -> str | None:
        """The authored effort to a native tier, or ``None`` when none was expressed."""
        if value is None:
            return None
        # Config first, so a deployment can both rename the ordinal and reach a native
        # tier outside it (Claude Code's own ``xhigh``).
        aliased = self._effort_aliases.get(value)
        if aliased is not None:
            return aliased
        if value in _EFFORT_ORDINAL:
            return value
        # The knob exists, so an unrecognized value is an authoring mistake rather than a
        # missing capability — logged once and dropped, never a spawn failure.
        if value not in self._unrecognized_efforts:
            self._unrecognized_efforts.add(value)
            _log.info("unrecognized effort value; ignoring", effort=value, known=sorted(_EFFORT_ORDINAL))
        return None

    def resolve_compaction_window(self, value: str | None) -> str | None:
        """``"auto"`` or a token-count spelling, else dropped and logged once (blizzard#343)."""
        if value is None:
            return None
        if _COMPACTION_WINDOW_RE.fullmatch(value):
            return value
        if value not in self._unrecognized_compaction_windows:
            self._unrecognized_compaction_windows.add(value)
            _log.info("unrecognized compaction window value; ignoring", compaction_window=value)
        return None

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
        if not preamble.environments:
            raise HarnessSpawnError("spawn requires at least one acquired environment")
        # A resume reuses the original session id in place — forking is opt-in and never
        # passed here — so `session_hint` is irrelevant on that path (issue #115).
        session_id = resume_from or session_hint or ""
        # The rule's one owner is `SpawnCwd` (issue #29). `environments` was checked
        # non-empty above, so the fallback is always a real workdir here.
        workdir = SpawnCwd(preamble.workspace_root, preamble.environments[0].workdir).path
        cmd = [self._binary, "-p", "--output-format", "json"]
        # `--model` at MINT ONLY (a resume restores it); `--effort`/`--autocompact` on EVERY
        # invocation — neither is sticky (issue #144, blizzard#343).
        if not resume_from:
            cmd += ["--model", model or self._model]
        if effort:
            cmd += ["--effort", effort]
        if compaction_window:
            cmd += ["--autocompact", compaction_window]
        if resume_from:
            cmd += ["--resume", resume_from]
        elif session_id:
            cmd += ["--session-id", session_id]
        if self._settings_path:
            cmd += ["--settings", self._settings_path]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        # The preamble is composed in the core; the adapter only concatenates it ahead of
        # the envelope prompt (``bzh:deterministic-shell``, issue #17).
        cmd.append("\n\n".join(part for part in (preamble.prompt_prefix, envelope.prompt or "") if part))

        env = self._spawn_env(envelope, preamble, session_id)
        # Injected per-lease files, so a killed worker's output survives the process; both
        # go through `_stdout_target` for its cleanup guarantee, and an empty path is DEVNULL.
        with _stdout_target(preamble.stdout_path) as stdout_file, _stdout_target(preamble.stderr_path) as stderr_file:
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=workdir,
                    env=env,
                    stdout=stdout_file if stdout_file is not None else subprocess.DEVNULL,
                    stderr=stderr_file if stderr_file is not None else subprocess.DEVNULL,
                )
            except OSError as exc:
                _log.error("harness spawn failed", binary=self._binary, cwd=workdir, detail=str(exc))
                raise HarnessSpawnError(f"failed to spawn {self._binary} in {workdir}: {exc}") from exc

        start_time = ProcStat.of(proc.pid).start_time or ""
        _log.info("spawned worker", binary=self._binary, pid=proc.pid, session_id=session_id, cwd=workdir)
        return WorkerHandle(session_id=session_id, pid=proc.pid, process_start_time=start_time)

    def judge(
        self,
        workdir: str,
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
        if not output_path:
            raise HarnessSpawnError("judge requires an output path — a detached verdict is unrecoverable without one")
        cmd = [self._binary, "-p", "--output-format", "json", "--resume", session_id]
        # No `--model` (sticky); `--effort`/`--autocompact` ARE reasserted. `model` is taken
        # only to attribute usage below, never to switch the session (issue #144, blizzard#343).
        if effort:
            cmd += ["--effort", effort]
        if compaction_window:
            cmd += ["--autocompact", compaction_window]
        # Prefix parity with `resume_with_message` — pinned by
        # `test_judge_prefix_matches_resume_with_messages_settings_and_effort`.
        if self._settings_path:
            cmd += ["--settings", self._settings_path]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        cmd.append(judgement_prompt)
        env = (
            self.identity_env(preamble, chunk_id, session_id, elicitation=True)
            if preamble is not None
            else AllowlistedEnv.of(self._env_passthrough).variables
        )
        # Detached (blizzard#443): the reply lands in `output_path`, never a pipe this
        # call waits on — the collect half reads it back once the process has exited.
        try:
            with open(output_path, "wb") as stdout_file:
                proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdout=stdout_file, stderr=subprocess.DEVNULL)
        except OSError as exc:
            _log.error("elicitation launch failed", binary=self._binary, cwd=workdir, detail=str(exc))
            raise HarnessSpawnError(f"failed to launch {self._binary} in {workdir}: {exc}") from exc
        start_time = ProcStat.of(proc.pid).start_time or ""
        _log.info("elicitation launched", binary=self._binary, pid=proc.pid, session_id=session_id, cwd=workdir)
        return WorkerHandle(session_id=session_id, pid=proc.pid, process_start_time=start_time)

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
        cmd = [self._binary, "-p", "--output-format", "json", "--resume", session_id]
        # As on `judge`: no `--model` (sticky), `--effort`/`--autocompact` reasserted (not sticky).
        if effort:
            cmd += ["--effort", effort]
        if compaction_window:
            cmd += ["--autocompact", compaction_window]
        # Re-attach the worker hooks: this re-enters a long-lived session that later exits
        # on its own, and a resume does not carry the original spawn's `--settings`.
        if self._settings_path:
            cmd += ["--settings", self._settings_path]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        cmd.append(message)
        # Re-supply the per-lease identity: a resume inherits none of the spawn env, and
        # the token plaintext is never persisted, so the caller re-mints it.
        env = (
            self.identity_env(preamble, chunk_id, session_id)
            if preamble is not None
            else AllowlistedEnv.of(self._env_passthrough).variables
        )
        # Injected per-lease file (epic #57), mirroring `spawn`'s `preamble.stdout_path`.
        with _stdout_target(stdout_path) as stdout_file:
            proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdout=stdout_file)
        return proc.pid

    def resume_command(
        self,
        workdir: str,
        session_id: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        attended: bool = False,
    ) -> str:
        # Asserted only for the ATTENDED composition (issue #258): the unattended string is
        # run in a bare terminal, so it stays at the interactive permission default.
        mode = self._permission_mode if attended else None
        parts = (("model", model), ("effort", effort), ("permission-mode", mode))
        flags = "".join(f" --{name} {value}" for name, value in parts if value)
        return f"cd {workdir} && {self._binary} --resume {session_id}{flags}"

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

    def has_usable_output(self, output: str) -> bool:
        return ResultEnvelope.of(output) is not None

    def parse_assessment(self, output: str) -> str:
        """The reply text following ``</Choice>`` — the worker's prose assessment."""
        text = self._result_text(output)
        close = text.find(_CHOICE_CLOSE)
        if close == -1:
            return ""
        return text[close + len(_CHOICE_CLOSE) :].strip()

    def parse_usage(self, output: str, kind: UsageKind, *, model: str | None = None) -> UsageSample | None:
        envelope = ResultEnvelope.of(output)
        if envelope is None or envelope.usage is None:
            return None
        usage = envelope.usage
        return UsageSample(
            kind=kind,
            model=envelope.model or model or self._model,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_create_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            cost_usd=envelope.cost_usd,
        )

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind, *, model: str | None = None) -> UsageSample:
        input_tokens = output_tokens = cache_read_tokens = cache_create_tokens = 0
        resolved = model or self._model
        # A reply carrying several content blocks is written as several records that each
        # repeat their message's ONE usage, so summing per record overcounts (measured 1.7x
        # against the billed figure on a long session). Every field here is per-message.
        counted: set[str] = set()
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
                resolved = record_model
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            message_id = message.get("id")
            if isinstance(message_id, str) and message_id:
                if message_id in counted:
                    continue
                counted.add(message_id)
            # An id-less record cannot be collapsed, so it is counted — an unidentifiable
            # message is more likely one message than a repeat of the last.
            input_tokens += int(usage.get("input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            cache_read_tokens += int(usage.get("cache_read_input_tokens") or 0)
            cache_create_tokens += int(usage.get("cache_creation_input_tokens") or 0)
        return UsageSample(
            kind=kind,
            model=resolved,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            cost_usd=None,
        )

    def transcript_source(self) -> IHarnessTranscriptSource:
        return self._transcript_source

    # --- plumbing -----------------------------------------------------------

    @staticmethod
    def _result_text(output: str) -> str:
        """The assistant's final message: the ``result`` field of the JSON envelope, else raw."""
        envelope = ResultEnvelope.of(output)
        return envelope.result if envelope is not None else output

    def identity_env(
        self, preamble: WorkerPreamble, chunk_id: str, session_id: str, *, elicitation: bool = False
    ) -> dict[str, str]:
        """The child env carrying this lease's worker identity: the allowlist plus the
        ``BLIZZARD_*`` vars a worker's CLI and its hooks read to reach the runner for
        this lease. ``spawn``, ``resume_with_message``, and a takeover (via the seam,
        issue #258) all build from this, so a daemon resume is as fully identified as a
        fresh one — ``--resume`` does not inherit the original spawn env."""
        env = AllowlistedEnv.of(self._env_passthrough).variables
        env["BLIZZARD_ENV_IDS"] = ",".join(e.environment_id for e in preamble.environments)
        env["BLIZZARD_ENV_WORKDIRS"] = ",".join(e.workdir for e in preamble.environments)
        env["BLIZZARD_SESSION_ID"] = session_id
        env["BLIZZARD_CHUNK_ID"] = chunk_id
        # Runner-minted identity, inherited per process tree, so a sibling worker cannot
        # misattribute a beat.
        env["BLIZZARD_LEASE_ID"] = preamble.lease_id
        env["BLIZZARD_RUNNER_URL"] = preamble.local_api_url
        env["BLIZZARD_LEASE_TOKEN"] = preamble.lease_token
        # The command a worker runs to record an undecidable choice; `setdefault`, so a
        # caller that already named one keeps it.
        env.setdefault("BLIZZARD_RUNNER_ASK_CMD", "blizzard runner ask")
        if elicitation:
            env["BLIZZARD_ELICITATION"] = "1"
        return env

    def _spawn_env(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_id: str) -> dict[str, str]:
        return self.identity_env(preamble, envelope.chunk_id, session_id)


def _conforms_harness_adapter(x: ClaudeCodeAdapter) -> IHarnessAdapter:
    return x
