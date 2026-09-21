"""The Claude Code adapter binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.harness.adapter.IHarnessAdapter` against the ``claude``
non-interactive CLI. ``--permission-mode`` and ``--settings`` are per-invocation, not
session-sticky, so each is reasserted on every resume. Every child env comes from
:class:`AllowlistedEnv`."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.adapter import (
    HarnessSpawnError,
    IHarnessAdapter,
    PendingWorkerHandle,
    ResumeHandle,
    WorkerHandle,
    WorkerPreamble,
)
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.internal import harness_shared
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NullTranscriptSource
from blizzard.runner.harness.usage import UsageKind, UsageLimit, UsageSample
from blizzard.runner.loop.process import IProcessProbe
from blizzard.wire.envelope import TIER_PREFIX, NodeEnvelope

_log = get_logger("blizzard.runner.harness")

# The model a worker runs on when nothing expressed a preference, pinned so a spawn never
# inherits the operator's ambient default.
DEFAULT_WORKER_MODEL = "claude-opus-5"

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

# The synthetic record's own reset-time phrasing (blizzard#594, the 2026-09-05 shape):
# "resets 5:40pm (America/Chicago)" — a clock time in an IANA zone, never a duration.
_RATE_LIMIT_RESET_RE = re.compile(r"resets\s+(\d{1,2}):(\d{2})\s*([ap]m)\s*\(([^)]+)\)", re.IGNORECASE)


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

    @property
    def cost_scope_tokens(self) -> int | None:
        """The tokens ``total_cost_usd`` was charged for, summed across every model the
        envelope's ``modelUsage`` breaks out — sub-models the top-level ``usage`` omits
        included, since the cost figure covers them too. ``None`` when the envelope
        carries no breakdown, which says the figure is this invocation's alone."""
        breakdown = self.fields.get("modelUsage")
        if not isinstance(breakdown, dict):
            return None
        fields = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
        total = 0
        for entry in breakdown.values():
            if not isinstance(entry, dict):
                continue
            total += sum(int(entry.get(field) or 0) for field in fields)
        return total


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
        process: IProcessProbe,
        launcher: IProcessLauncher,
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
        # The pid-liveness seam (`bzh:pluggable-seams`); the Linux `/proc` reference binding
        # is the only production substitute, always injected (`bzh:dependency-injection`).
        self._process: IProcessProbe = process
        # Injected, never self-constructed (`bzh:dependency-injection`): ONE launcher, both bindings (D4).
        self._launcher: IProcessLauncher = launcher

    def observe_version(self) -> str | None:
        """The configured executable's version, observed right now — bounded and
        non-raising: a timeout, a missing binary, or empty output all read as ``None``,
        logged rather than propagated, since a caller reads this BEFORE the worker
        launches and must never let a wedged or absent binary delay that launch.
        Uncached, so a self-updated binary is reflected on the very next call."""
        return harness_shared.observe_version(self._binary)

    def resolve_model(self, preferences: Sequence[str]) -> str:
        """Left-to-right; first entry that resolves wins; an empty or fully-unresolvable list
        falls back to the adapter default. Shared with OpenCode
        (``harness_shared.resolve_model``); ``_resolve_one_model`` is this adapter's own
        native-name resolution."""
        return harness_shared.resolve_model(self._resolve_one_model, self._model, preferences)

    def resolve_model_strict(self, preferences: Sequence[str]) -> str | None:
        """Left-to-right; first entry that resolves wins; unresolvable entries skipped;
        ``None`` when nothing in ``preferences`` resolved — no adapter-default fallback,
        the distinction a multi-harness selection needs. Shared with OpenCode
        (``harness_shared.resolve_model_strict``)."""
        return harness_shared.resolve_model_strict(self._resolve_one_model, preferences)

    def _resolve_one_model(self, entry: str) -> str | None:
        """One preference entry to a native name, or ``None`` if this adapter cannot."""
        if entry.startswith(TIER_PREFIX):
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

    def resolvable_tier_ids(self) -> tuple[str, ...]:
        """Every tier id this adapter can resolve (blizzard#433): the built-ins merged
        with the runner's own ``[models.aliases]`` table, an overridden id appearing
        once — the same override-by-key precedence :meth:`_resolve_one_model` applies.
        Shared with OpenCode (``harness_shared.resolvable_tier_ids``)."""
        return harness_shared.resolvable_tier_ids(_BUILTIN_TIERS, self._model_aliases)

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
    ) -> PendingWorkerHandle:
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
        # Injected per-lease files surviving the process; both go through
        # `harness_shared.stdout_target`, empty meaning DEVNULL.
        with (
            harness_shared.stdout_target(preamble.stdout_path) as stdout_file,
            harness_shared.stdout_target(preamble.stderr_path) as stderr_file,
        ):
            try:
                # F1: deferred — the caller's own `confirm_durable()` (right after ITS durable
                # provisional record lands) is what disarms this launch's parent-death signal.
                launched = self._launcher.launch(
                    cmd,
                    cwd=workdir,
                    env=env,
                    stdout=stdout_file if stdout_file is not None else subprocess.DEVNULL,
                    stderr=stderr_file if stderr_file is not None else subprocess.DEVNULL,
                    defer_disarm=True,
                )
            except OSError as exc:
                _log.error("harness spawn failed", binary=self._binary, cwd=workdir, detail=str(exc))
                raise HarnessSpawnError(f"failed to spawn {self._binary} in {workdir}: {exc}") from exc

        _log.info("spawned worker", binary=self._binary, pid=launched.pid, session_id=session_id, cwd=workdir)
        # Already identified (D1) — phase two is instant. Left armed (F1): `Spawner.spawn`
        # calls `confirm_durable()` right after its own durable provisional record lands.
        return WorkerHandle(
            session_id=session_id,
            pid=launched.pid,
            process_start_time=launched.process_start_time,
            pgid=launched.pgid,
            confirm_durable=launched.confirm_durable,
        )

    def honors_session_hint(self) -> bool:
        return True

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
                # F1: deferred — the caller's own `confirm_durable()` (right after ITS durable
                # `record_elicitation_started`/`record_elicitation_relaunch` lands) disarms it.
                launched = self._launcher.launch(
                    cmd, cwd=workdir, env=env, stdout=stdout_file, stderr=subprocess.DEVNULL, defer_disarm=True
                )
        except OSError as exc:
            _log.error("elicitation launch failed", binary=self._binary, cwd=workdir, detail=str(exc))
            raise HarnessSpawnError(f"failed to launch {self._binary} in {workdir}: {exc}") from exc
        _log.info("elicitation launched", binary=self._binary, pid=launched.pid, session_id=session_id, cwd=workdir)
        # F1: left armed — `Judgement._elicit`/`_relaunch` call `confirm_durable()` right after
        # THEIR OWN durable `record_elicitation_started`/`record_elicitation_relaunch` lands.
        return WorkerHandle(
            session_id=session_id,
            pid=launched.pid,
            process_start_time=launched.process_start_time,
            pgid=launched.pgid,
            confirm_durable=launched.confirm_durable,
        )

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
    ) -> ResumeHandle:
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
        # Injected per-lease file (epic #57); unset (``None``) inherits the runner's own.
        # Deferred (F1, D4) like spawn/judge — `dormant.py::_wake` confirms after `record_spawn` lands.
        with harness_shared.stdout_target(stdout_path) as stdout_file:
            launched = self._launcher.launch(
                cmd, cwd=workdir, env=env, stdout=stdout_file, stderr=None, defer_disarm=True
            )
        # `launched.pgid` is the launcher's own recorded group (D3) — carried to the
        # caller rather than left for it to assume `pgid == pid`.
        return ResumeHandle(
            pid=launched.pid,
            pgid=launched.pgid,
            process_start_time=launched.process_start_time,
            confirm_durable=launched.confirm_durable,
        )

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
        return harness_shared.find_choice_verdict(self._result_text(output))

    def has_usable_output(self, output: str) -> bool:
        return ResultEnvelope.of(output) is not None

    def parse_assessment(self, output: str) -> str:
        """The reply text following ``</Choice>`` — the worker's prose assessment."""
        return harness_shared.text_after_choice_close(self._result_text(output)) or ""

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
            cost_scope_tokens=envelope.cost_scope_tokens,
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

    def classify_usage_limit(self, output: str, lines: Sequence[str], now: datetime) -> UsageLimit | None:
        # The signal is the synthetic transcript record (blizzard#594), never `output`:
        # a limited invocation's own stdout carries no result envelope to read `is_error`
        # off in the first place, so corroborating against it would only narrow, never help.
        del output
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
            if record.get("isApiErrorMessage") is not True or record.get("error") != "rate_limit":
                continue
            text = self._rate_limit_text(record)
            return UsageLimit(resets_at=self._parse_rate_limit_reset(text, now), detail=text or "rate_limit")
        return None

    @staticmethod
    def _rate_limit_text(record: Mapping[str, Any]) -> str:
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                return block["text"]
        return ""

    @staticmethod
    def _parse_rate_limit_reset(text: str, now: datetime) -> datetime | None:
        """The next occurrence of the reported clock time in its own IANA zone, after
        ``now`` — never a raise: an unrecognized zone or an unparseable message both
        return ``None``, which the caller falls back on rather than guesses past."""
        match = _RATE_LIMIT_RESET_RE.search(text)
        if match is None:
            return None
        hour_str, minute_str, meridiem, zone_name = match.groups()
        try:
            zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError):
            return None
        hour = int(hour_str) % 12
        if meridiem.lower() == "pm":
            hour += 12
        local_now = now.astimezone(zone)
        candidate = local_now.replace(hour=hour, minute=int(minute_str), second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        # `now` is always UTC (``bzh:injected-clock``); the reply matches its own tzinfo.
        return candidate.astimezone(now.tzinfo)

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
        return harness_shared.build_identity_env(
            preamble, chunk_id, session_id, self._env_passthrough, elicitation=elicitation
        )

    def _spawn_env(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_id: str) -> dict[str, str]:
        return self.identity_env(preamble, envelope.chunk_id, session_id)


def _conforms_harness_adapter(x: ClaudeCodeAdapter) -> IHarnessAdapter:
    return x
