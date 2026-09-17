"""The OpenCode adapter binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.harness.adapter.IHarnessAdapter` against the ``opencode``
CLI. Reuses only the production event/record parsers (``opencode_shapes``) — the diagnostic
PROCESS/scratch machinery the compatibility proof owns (``opencode_process.py`` and its
siblings) is never called into here (execution spec, D5): every worker launches through
Phase 1's :class:`~blizzard.runner.loop.process_launch.ProcessLauncher`, exactly as the Claude
Code binding does.

Output/usage parsing below implements the execution spec's "Output and usage" section: root
assistant text concatenation with tool/child exclusion, explicit session-error surfacing, and
step-usage dedup across a completed turn's own captured stdout."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.adapter import (
    HarnessSpawnError,
    IHarnessAdapter,
    PendingWorkerHandle,
    WorkerHandle,
    WorkerIdentityError,
    WorkerPreamble,
)
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.internal.opencode_command import OpenCodeCommand, OpenCodeInvocationKind
from blizzard.runner.harness.internal.opencode_shapes import (
    OpenCodeMessage,
    OpenCodePart,
    OpenCodeRunEvent,
    OpenCodeShapeError,
    parse_model_reference,
    parse_run_event,
    parse_run_jsonl,
)
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NullTranscriptSource
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.loop.process_launch import IProcessLauncher, ProcessLauncher
from blizzard.wire.envelope import NodeEnvelope

_log = get_logger("blizzard.runner.harness")

_CHOICE_OPEN = "<Choice>"
_CHOICE_CLOSE = "</Choice>"

# The namespaced tier-alias prefix (issue #144, shared with Claude Code): an entry carrying
# it is a *role*, resolved through the runner's own table; one without it is a native name.
_TIER_PREFIX = "blizzard:"

# The well-known effort ordinal (issue #144); OpenCode's own `--variant` vocabulary is
# provider-defined, so an entry outside it needs an explicit `[opencode.effort.aliases]`
# rather than a built-in guess — the same "name the variant directly" contract the
# execution spec's "Models, effort, permissions, and compaction" section describes.
_EFFORT_ORDINAL = frozenset({"low", "medium", "high", "max"})

# Bounds `observe_version`'s probe so a wedged binary costs one skipped read, not a hang.
_VERSION_PROBE_TIMEOUT_SECONDS = 5

# How often a fresh mint's pending handle re-reads the stdout capture while awaiting identity.
_IDENTITY_POLL_INTERVAL_SECONDS = 0.05


@dataclass(frozen=True)
class _PendingOpenCodeIdentity:
    """Phase one's OpenCode-specific pending handle (D1): the launch is real, but identity
    has not arrived yet — it is read from the worker's own stdout, the exact stream this
    binding already parses for output and usage (execution spec, "Fresh-session handshake").
    Never constructed for a resume, which already knows its session id and returns a plain
    :class:`WorkerHandle` instead, whose ``await_identity`` is its own trivial phase two."""

    pid: int
    pgid: int | None
    process_start_time: str
    stdout_path: str
    process: IProcessProbe

    def await_identity(self, timeout: float) -> WorkerHandle:
        deadline = time.monotonic() + timeout
        while True:
            event = self._first_event()
            if event is not None:
                if not event.session_id:
                    raise WorkerIdentityError("the first OpenCode record carried an empty session id")
                return WorkerHandle(
                    session_id=event.session_id,
                    pid=self.pid,
                    process_start_time=self.process_start_time,
                    pgid=self.pgid,
                )
            if not self.process.is_alive(self.pid, self.process_start_time):
                raise WorkerIdentityError("the OpenCode worker exited before its first record")
            if time.monotonic() >= deadline:
                raise WorkerIdentityError(f"no identity within {timeout}s")
            time.sleep(_IDENTITY_POLL_INTERVAL_SECONDS)

    def _first_event(self) -> OpenCodeRunEvent | None:
        """The first complete JSONL line captured so far, parsed — ``None`` while the line
        is not yet fully written. A malformed or non-JSON first line is never "not yet",
        it is a spawn failure (D2): no empty or hinted id is ever recorded as success."""
        try:
            with open(self.stdout_path, "rb") as f:
                content = f.read()
        except OSError:
            return None
        first_line, newline, _rest = content.partition(b"\n")
        if not newline or not first_line.strip():
            return None
        try:
            text = first_line.decode("utf-8")
            decoded = json.loads(text)
            return parse_run_event(decoded)
        except (UnicodeDecodeError, ValueError, OpenCodeShapeError) as exc:
            raise WorkerIdentityError(f"malformed identity in the first OpenCode record: {exc}") from exc


class OpenCodeAdapter:
    """The OpenCode binding. Dumb: translates the CLI surface, never decides.

    Self-mints its own session id (``honors_session_hint`` is ``False``): a fresh spawn
    returns a :class:`_PendingOpenCodeIdentity`, never an already-identified handle."""

    def __init__(
        self,
        binary: str = "opencode",
        *,
        model: str = "",
        env_passthrough: Sequence[str] = (),
        model_aliases: Sequence[tuple[str, str]] = (),
        effort_aliases: Sequence[tuple[str, str]] = (),
        worker_config_path: str | None = None,
        transcript_source: IHarnessTranscriptSource | None = None,
        process: IProcessProbe,
        launcher: IProcessLauncher | None = None,
    ) -> None:
        self._binary = binary
        self._command = OpenCodeCommand(binary)
        # Empty is a legitimate default (unlike Claude Code's pinned `DEFAULT_WORKER_MODEL`):
        # OpenCode ships no built-in tier mapping, so an operator who configures none simply
        # lets OpenCode resolve its own configured default rather than Blizzard inventing one.
        self._model = model
        self._model_aliases = dict(model_aliases)
        self._effort_aliases = dict(effort_aliases)
        self._unrecognized_efforts: set[str] = set()
        self._unrecognized_compaction_windows: set[str] = set()
        self._env_passthrough = tuple(env_passthrough)
        # The runner-owned permission/plugin document (D7); `None` when this runtime
        # predates the OpenCode binding, or a deployment chose not to scaffold one.
        self._worker_config_path = worker_config_path
        self._transcript_source: IHarnessTranscriptSource = transcript_source or NullTranscriptSource()
        self._process: IProcessProbe = process
        self._launcher: IProcessLauncher = launcher if launcher is not None else ProcessLauncher(process)

    def observe_version(self) -> str | None:
        try:
            result = subprocess.run(
                [self._binary, "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=_VERSION_PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log.warning("harness version probe failed", binary=self._binary, detail=str(exc))
            return None
        return result.stdout.strip() or result.stderr.strip() or None

    def resolve_model(self, preferences: Sequence[str]) -> str:
        resolved = self.resolve_model_strict(preferences)
        if resolved is not None:
            return resolved
        if preferences:
            _log.info(
                "no model preference resolved; falling back to the adapter default",
                skipped=list(preferences),
                fallback=self._model or "(opencode's own configured default)",
            )
        return self._model

    def resolve_model_strict(self, preferences: Sequence[str]) -> str | None:
        skipped: list[str] = []
        for entry in preferences:
            resolved = self._resolve_one_model(entry)
            if resolved is not None:
                if skipped:
                    _log.info("skipped unresolvable model preferences", skipped=skipped, resolved=resolved)
                return resolved
            skipped.append(entry)
        return None

    def _resolve_one_model(self, entry: str) -> str | None:
        """One preference entry to a native ``provider/model`` pair, or ``None`` if this
        adapter cannot resolve it — including a syntactically-valid pair belonging to
        another harness's own tier vocabulary, skipped rather than handed to a CLI that
        would reject it."""
        if entry.startswith(_TIER_PREFIX):
            # OpenCode ships no built-in tier mapping (unlike Claude Code's three
            # defaults): an unmapped tier is a deliberately unavailable capability, which
            # is what lets a multi-harness selection skip this binding instead of
            # spawning under a model it cannot provide (harness-selection spec).
            return self._model_aliases.get(entry)
        if entry in self._model_aliases:
            return self._model_aliases[entry]
        # A non-namespaced entry is accepted only when it is a valid `provider/model`
        # OpenCode reference (execution spec) — never a bare native short name, since
        # OpenCode has no fixed short-name vocabulary the way Claude Code does.
        try:
            parse_model_reference(entry)
        except OpenCodeShapeError:
            return None
        return entry

    def resolve_effort(self, value: str | None) -> str | None:
        if value is None:
            return None
        aliased = self._effort_aliases.get(value)
        if aliased is not None:
            return aliased
        if value in _EFFORT_ORDINAL:
            return value
        if value not in self._unrecognized_efforts:
            self._unrecognized_efforts.add(value)
            _log.info("unrecognized effort value; ignoring", effort=value, known=sorted(_EFFORT_ORDINAL))
        return None

    def resolve_compaction_window(self, value: str | None) -> str | None:
        """Always unsupported (D8): OpenCode's compaction reserve and automatic-compaction
        switch do not represent Claude Code's numeric threshold, so no value here is ever
        translated — only ever dropped and logged once."""
        if value is None:
            return None
        if value not in self._unrecognized_compaction_windows:
            self._unrecognized_compaction_windows.add(value)
            _log.info("compaction window is unsupported by the OpenCode binding; ignoring", compaction_window=value)
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
        if not preamble.stdout_path:
            # OpenCode self-mints its session id and announces it only on its own stdout
            # (execution spec, "Fresh-session handshake"); with nowhere durable to read
            # from, a fresh mint could never learn who it spawned. Claude Code has no such
            # need — its stdout may legitimately discard to DEVNULL — so this requirement
            # is OpenCode-specific, not a widened seam contract.
            raise HarnessSpawnError("OpenCode spawn requires an injected stdout path to learn its session id")
        workdir = SpawnCwd(preamble.workspace_root, preamble.environments[0].workdir).path
        prompt = "\n\n".join(part for part in (preamble.prompt_prefix, envelope.prompt or "") if part)
        kind = OpenCodeInvocationKind.RESUME if resume_from else OpenCodeInvocationKind.FRESH
        cmd = self._command.build(
            kind,
            prompt=prompt,
            session_id=resume_from,
            model=model or self._model,
            variant=effort,
            auto=True,
        )
        env = self._spawn_env(envelope, preamble, resume_from or "")
        with open(preamble.stdout_path, "ab") as stdout_file:
            try:
                launched = self._launcher.launch(
                    cmd, cwd=workdir, env=env, stdout=stdout_file, stderr=subprocess.DEVNULL
                )
            except OSError as exc:
                _log.error("harness spawn failed", binary=self._binary, cwd=workdir, detail=str(exc))
                raise HarnessSpawnError(f"failed to spawn {self._binary} in {workdir}: {exc}") from exc
        _log.info(
            "spawned worker", binary=self._binary, pid=launched.pid, session_id=resume_from or "(pending)", cwd=workdir
        )
        if resume_from:
            # Resume never performs the handshake (execution spec): the stored session
            # reference is already authoritative.
            return WorkerHandle(
                session_id=resume_from,
                pid=launched.pid,
                process_start_time=launched.process_start_time,
                pgid=launched.pgid,
            )
        return _PendingOpenCodeIdentity(
            pid=launched.pid,
            pgid=launched.pgid,
            process_start_time=launched.process_start_time,
            stdout_path=preamble.stdout_path,
            process=self._process,
        )

    def honors_session_hint(self) -> bool:
        return False

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
        cmd = self._command.build(
            OpenCodeInvocationKind.JUDGE,
            prompt=judgement_prompt,
            session_id=session_id,
            variant=effort,
            auto=True,
        )
        env = (
            self.identity_env(preamble, chunk_id, session_id, elicitation=True)
            if preamble is not None
            else AllowlistedEnv.of(self._env_passthrough).variables
        )
        try:
            with open(output_path, "wb") as stdout_file:
                launched = self._launcher.launch(
                    cmd, cwd=workdir, env=env, stdout=stdout_file, stderr=subprocess.DEVNULL
                )
        except OSError as exc:
            _log.error("elicitation launch failed", binary=self._binary, cwd=workdir, detail=str(exc))
            raise HarnessSpawnError(f"failed to launch {self._binary} in {workdir}: {exc}") from exc
        _log.info("elicitation launched", binary=self._binary, pid=launched.pid, session_id=session_id, cwd=workdir)
        return WorkerHandle(
            session_id=session_id,
            pid=launched.pid,
            process_start_time=launched.process_start_time,
            pgid=launched.pgid,
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
    ) -> int:
        cmd = self._command.build(
            OpenCodeInvocationKind.NUDGE,
            prompt=message,
            session_id=session_id,
            variant=effort,
            auto=True,
        )
        env = (
            self.identity_env(preamble, chunk_id, session_id)
            if preamble is not None
            else AllowlistedEnv.of(self._env_passthrough).variables
        )
        if stdout_path:
            with open(stdout_path, "ab") as stdout_file:
                launched = self._launcher.launch(cmd, cwd=workdir, env=env, stdout=stdout_file, stderr=None)
        else:
            launched = self._launcher.launch(cmd, cwd=workdir, env=env, stdout=None, stderr=None)
        return launched.pid

    def resume_command(
        self,
        workdir: str,
        session_id: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        attended: bool = False,
    ) -> str:
        # `attended` names no distinct OpenCode composition (unlike Claude Code's
        # `--permission-mode`): an interactive TUI session always asks live, so the
        # paste string and the exec'd form are the same argv either way (execution spec,
        # "Worker process" — no `--format json`, no `--auto`, for either).
        del attended
        argv = self._command.takeover_argv(session_id=session_id, model=model, variant=effort)
        return f"cd {workdir} && {' '.join(argv)}"

    def identity_env(
        self, preamble: WorkerPreamble, chunk_id: str, session_id: str, *, elicitation: bool = False
    ) -> dict[str, str]:
        env = AllowlistedEnv.of(self._env_passthrough).variables
        env["BLIZZARD_ENV_IDS"] = ",".join(e.environment_id for e in preamble.environments)
        env["BLIZZARD_ENV_WORKDIRS"] = ",".join(e.workdir for e in preamble.environments)
        env["BLIZZARD_SESSION_ID"] = session_id
        env["BLIZZARD_CHUNK_ID"] = chunk_id
        env["BLIZZARD_LEASE_ID"] = preamble.lease_id
        env["BLIZZARD_RUNNER_URL"] = preamble.local_api_url
        env["BLIZZARD_LEASE_TOKEN"] = preamble.lease_token
        env.setdefault("BLIZZARD_RUNNER_ASK_CMD", "blizzard runner ask")
        if elicitation:
            env["BLIZZARD_ELICITATION"] = "1"
        if self._worker_config_path:
            # The runner-owned permission/plugin document (D7) — supplied both as a path
            # and as its own serialized content, exactly as the compatibility proof's
            # `configuration_isolation` probe already established OpenCode honors
            # (`docs/deployment/opencode-compatibility.md`).
            env["OPENCODE_CONFIG"] = self._worker_config_path
            try:
                with open(self._worker_config_path, encoding="utf-8") as f:
                    env["OPENCODE_CONFIG_CONTENT"] = f.read()
            except OSError:
                pass  # best-effort — a missing file just leaves OpenCode's own discovery
        return env

    def _spawn_env(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_id: str) -> dict[str, str]:
        return self.identity_env(preamble, envelope.chunk_id, session_id)

    # --- output/usage (execution spec, "Output and usage") ------------------

    @staticmethod
    def _parse_events(output: str) -> tuple[OpenCodeRunEvent, ...]:
        """Every event this invocation's own captured stdout carries, in emission order.

        Malformed JSONL parses to no events rather than raising: a process killed mid-write
        can leave a truncated or non-JSON capture, and that reads as "nothing usable" here
        — the same tolerance :class:`~.claude_code_adapter.ResultEnvelope` gives a killed
        Claude Code worker's partial stdout, just with nothing to reverse-scan for."""
        try:
            return parse_run_jsonl(output)
        except OpenCodeShapeError:
            return ()

    @staticmethod
    def _root_session_id(events: Sequence[OpenCodeRunEvent]) -> str | None:
        return events[0].session_id if events else None

    @classmethod
    def _root_text(cls, events: Sequence[OpenCodeRunEvent]) -> str:
        """Completed root assistant text, concatenated in emission order. Every record
        ``opencode run --format json`` emits already carries the root session id
        (execution spec, "Fresh-session handshake"), so the session check below is a
        defensive belt, not the mechanism doing the excluding: tool output and reasoning
        are excluded structurally, by never being a ``text`` part in the first place."""
        root = cls._root_session_id(events)
        texts = [
            event.part.text
            for event in events
            if event.type == "text" and event.part is not None and event.part.text and event.part.session_id == root
        ]
        return "\n".join(texts)

    @staticmethod
    def _session_error(events: Sequence[OpenCodeRunEvent]) -> str | None:
        """The first explicit session error, formatted for a human reader (execution spec:
        "surfaces explicit session errors"), or ``None`` when the turn carried none."""
        for event in events:
            if event.type == "error" and event.error is not None:
                detail = f" (status {event.error.status_code})" if event.error.status_code is not None else ""
                return f"{event.error.name}: {event.error.message}{detail}"
        return None

    @classmethod
    def _root_step_finishes(cls, events: Sequence[OpenCodeRunEvent]) -> list[OpenCodePart]:
        root = cls._root_session_id(events)
        return [
            event.part
            for event in events
            if event.type == "step_finish" and event.part is not None and event.session_id == root
        ]

    @staticmethod
    def _sum_tokens(parts: Iterable[OpenCodePart]) -> tuple[int, int, int, int]:
        """(input, output, cache-read, cache-write) summed across every distinct completed
        step's tokens. A step-finish part's ``tokens`` sub-fields are all required by the
        schema (never optional), so no field is ever defaulted to zero here — a genuinely
        tokenless message (a ``user`` turn) simply never becomes a step-finish part in the
        first place. OpenCode's own ``reasoning`` count folds into Blizzard's single
        ``output_tokens`` column: the shared usage shape carries no separate column for it,
        and a reasoning token is never simply dropped."""
        input_tokens = output_tokens = cache_read_tokens = cache_create_tokens = 0
        for part in parts:
            tokens = part.tokens
            if tokens is None:
                continue
            input_tokens += tokens.input_tokens
            output_tokens += tokens.output_tokens + tokens.reasoning_tokens
            cache_read_tokens += tokens.cache_read_tokens
            cache_create_tokens += tokens.cache_write_tokens
        return input_tokens, output_tokens, cache_read_tokens, cache_create_tokens

    @staticmethod
    def _known_cost(parts: Iterable[OpenCodePart]) -> float | None:
        """The summed dollar cost, or ``None`` when every step's cost reads as unknown.

        A subscription-authenticated step reports its cost as a literal ``0`` rather than
        omitting the field (execution spec), and a genuinely billed step never costs
        exactly nothing — so a zero is treated as "not reported", never as "free", and
        excluded from the sum. A nonzero figure survives verbatim, never estimated."""
        known = [part.cost for part in parts if part.cost]
        if not known:
            return None
        return sum(known)

    def parse_verdict(self, output: str) -> str | None:
        text = self._root_text(self._parse_events(output))
        start = text.find(_CHOICE_OPEN)
        if start == -1:
            return None
        end = text.find(_CHOICE_CLOSE, start)
        if end == -1:
            return None
        name = text[start + len(_CHOICE_OPEN) : end].strip()
        return name or None

    def has_usable_output(self, output: str) -> bool:
        """True once the root turn produced at least one completed model step — the
        OpenCode-native equivalent of Claude Code's result envelope. A process killed
        before its first ``step_finish`` (an OOM, a ``kill -9``) reads as "lost", exactly
        like a truncated envelope, independent of whether a verdict was ever named."""
        return bool(self._root_step_finishes(self._parse_events(output)))

    def parse_assessment(self, output: str) -> str:
        events = self._parse_events(output)
        text = self._root_text(events)
        close = text.find(_CHOICE_CLOSE)
        if close != -1:
            return text[close + len(_CHOICE_CLOSE) :].strip()
        # No choice was ever made — an explicit session error is the one thing worth
        # surfacing in its place; anything else is legitimately empty.
        return self._session_error(events) or ""

    def parse_usage(self, output: str, kind: UsageKind, *, model: str | None = None) -> UsageSample | None:
        finishes = self._root_step_finishes(self._parse_events(output))
        # Deduplicated by part identity: the same completed step is never counted twice
        # even were it to appear more than once on this one capture.
        by_id = {part.id: part for part in finishes if part.tokens is not None}
        if not by_id:
            return None
        parts = list(by_id.values())
        input_tokens, output_tokens, cache_read_tokens, cache_create_tokens = self._sum_tokens(parts)
        return UsageSample(
            kind=kind,
            model=model or self._model or "opencode",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            cost_usd=self._known_cost(parts),
        )

    @staticmethod
    def _finish_parts_from_line(line: str) -> list[OpenCodePart]:
        """One transcript line's completed-step parts, tolerating either shape a raw
        OpenCode transcript can carry: a run event (the process's own stdout shape) or an
        exported message (the session-export shape) — the same completed step can be
        described by both, which is exactly what the caller's identity-keyed dedup
        collapses back to one (execution spec). An unparseable or irrelevant line
        contributes nothing; this is a best-effort transcript fallback, never a raise."""
        stripped = line.strip()
        if not stripped:
            return []
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        if not isinstance(decoded, dict):
            return []
        try:
            event = parse_run_event(decoded)
        except OpenCodeShapeError:
            pass
        else:
            return [event.part] if event.type == "step_finish" and event.part is not None else []
        try:
            message = OpenCodeMessage.parse(decoded)
        except OpenCodeShapeError:
            return []
        return [part for part in message.parts if part.type == "step-finish"]

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind, *, model: str | None = None) -> UsageSample:
        by_id: dict[str, OpenCodePart] = {}
        for line in lines:
            for part in self._finish_parts_from_line(line):
                if part.tokens is not None:
                    by_id[part.id] = part
        input_tokens, output_tokens, cache_read_tokens, cache_create_tokens = self._sum_tokens(by_id.values())
        return UsageSample(
            kind=kind,
            model=model or self._model or "opencode",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            # A transcript carries no dollar figure (the shared seam contract,
            # `IHarnessUsageAccounting.sum_transcript_usage`) — token counts stay
            # authoritative regardless.
            cost_usd=None,
        )

    def transcript_source(self) -> IHarnessTranscriptSource:
        return self._transcript_source


def _conforms_harness_adapter(x: OpenCodeAdapter) -> IHarnessAdapter:
    return x
