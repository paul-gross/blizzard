"""The OpenCode adapter binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.harness.adapter.IHarnessAdapter` against the ``opencode``
CLI. Reuses only the production event/record parsers (``opencode.shapes``) — never the
diagnostic PROCESS/scratch machinery the compatibility proof owns: every worker launches
through :class:`~blizzard.runner.harness.process_launch.ProcessLauncher`, as Claude Code does."""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.foundation.node_steps import TIER_PREFIX
from blizzard.foundation.roles import adapter_model
from blizzard.runner.harness import harness_shared
from blizzard.runner.harness.adapter import (
    HarnessSpawnError,
    IHarnessAdapter,
    PendingWorkerHandle,
    ResumeHandle,
    WorkerHandle,
    WorkerIdentityError,
    WorkerPreamble,
)
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.opencode.bundle import check_ambient_plugins, content_with_snapshot_references
from blizzard.runner.harness.opencode.command import OpenCodeCommand, OpenCodeInvocationKind
from blizzard.runner.harness.opencode.permissions.permission_compose import (
    compose_ask_denials,
    merge_overrides,
    residual_asks,
)
from blizzard.runner.harness.opencode.permissions.permission_resolver import (
    IOpenCodePermissionResolver,
    OpenCodePermissionResolveError,
)
from blizzard.runner.harness.opencode.shapes import (
    OpenCodeMessage,
    OpenCodePart,
    OpenCodeRunEvent,
    OpenCodeShapeError,
    parse_model_reference,
    parse_run_event,
)
from blizzard.runner.harness.opencode.usage.descendant_usage import DescendantStep, OpenCodeDescendantUsage
from blizzard.runner.harness.opencode.usage.price_cache import (
    IOpenCodePriceCatalog,
    OpenCodeModelPrice,
    OpenCodeStepTokens,
)
from blizzard.runner.harness.overload import ProviderOverload
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NullTranscriptSource
from blizzard.runner.harness.usage import UsageKind, UsageLimit, UsageSample
from blizzard.runner.node_steps.envelope import Envelope
from blizzard.runner.process.probe import IProcessProbe

_log = get_logger("blizzard.runner.harness")

# The well-known effort ordinal; outside it needs an explicit `[opencode.effort.aliases]`, never a guess.
_EFFORT_ORDINAL = frozenset({"low", "medium", "high", "max"})

# How often a fresh mint's pending handle re-reads the stdout capture while awaiting identity.
_IDENTITY_POLL_INTERVAL_SECONDS = 0.05

# Leading non-identity lines the handshake tolerates before giving up as a spawn failure.
_MAX_IDENTITY_PREAMBLE_LINES = 20

# Bound on the diagnostic stderr tail a failed handshake's error carries (enough for one traceback line).
_STDERR_TAIL_BYTES = 2000

# The status a usage-limit refusal reports — distinct from an ordinary
# rate-limit's transient 429s (out of scope) by its own message phrasing below.
_USAGE_LIMIT_STATUS_CODE = 429
_USAGE_LIMIT_MESSAGE_RE = re.compile(r"usage limit", re.IGNORECASE)

# The captured shape's own relative-reset phrasing: "reset in 2 hours",
# "reset in 1 day 4 hours" — a duration, never a clock time (unlike Claude Code's).
_RESET_DURATION_RE = re.compile(
    r"reset\w*\s+in\s+(?:(\d+)\s*day[s]?\s*)?(?:(\d+)\s*hour[s]?\s*)?(?:(\d+)\s*minute[s]?\s*)?", re.IGNORECASE
)

# `_PROVIDER_REFUSAL_STATUSES` (opencode/compatibility/facts.py) excludes 529, so it never
# collides with the usage-limit/refusal statuses above. The name match is a secondary
# guard only — the status check above is the one known-shape signal.
_OVERLOAD_STATUS_CODE = 529
_OVERLOAD_NAME_RE = re.compile(r"overloaded", re.IGNORECASE)


@dataclass(frozen=True)
class _PendingOpenCodeIdentity:
    """Phase one's OpenCode-specific pending handle: the launch is real, but identity
    is read from the worker's own stdout (execution spec, "Fresh-session handshake"). Never
    constructed for a resume, which already knows its session id and returns a plain
    :class:`WorkerHandle` instead, whose ``await_identity`` is its own trivial phase two."""

    pid: int
    pgid: int  # every launch gets one — see `ProcessLauncher.launch`
    process_start_time: str
    stdout_path: str
    stderr_path: str
    process: IProcessProbe
    confirm_durable: Callable[[], None] = field(
        default=lambda: None, compare=False
    )  # disarms the daemon-death kill signal once durably recorded; no-op default

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
                raise WorkerIdentityError(f"the OpenCode worker exited before its first record{self._stderr_tail()}")
            if time.monotonic() >= deadline:
                raise WorkerIdentityError(f"no identity within {timeout}s{self._stderr_tail()}")
            time.sleep(_IDENTITY_POLL_INTERVAL_SECONDS)

    def _stderr_tail(self) -> str:
        """The worker's own diagnosis of its death, appended to an identity failure — the
        only place that death's real cause survives; nothing else reads ``stderr_path``."""
        try:
            with open(self.stderr_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - _STDERR_TAIL_BYTES))
                tail = f.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return ""
        return f" — stderr: {tail}" if tail else ""

    def _first_event(self) -> OpenCodeRunEvent | None:
        """The first line, among those captured so far, that parses as a complete OpenCode
        identity record — tolerating up to :data:`_MAX_IDENTITY_PREAMBLE_LINES` leading
        non-JSON lines ahead of it. ``None`` while fewer complete lines than that bound
        have arrived (not yet written, not malformed); exceeding the bound with none valid
        raises, rather than waiting on ``await_identity``'s own timeout to notice."""
        try:
            with open(self.stdout_path, "rb") as f:
                content = f.read()
        except OSError:
            return None
        complete_lines = content.split(b"\n")[:-1]  # a trailing partial line is never complete
        for raw_line in complete_lines[:_MAX_IDENTITY_PREAMBLE_LINES]:
            line = raw_line.strip()
            if not line:
                continue
            try:
                decoded = json.loads(line.decode("utf-8"))
                return parse_run_event(decoded)
            except (UnicodeDecodeError, ValueError, OpenCodeShapeError):
                continue
        if len(complete_lines) >= _MAX_IDENTITY_PREAMBLE_LINES:
            raise WorkerIdentityError(
                f"no valid identity within the first {_MAX_IDENTITY_PREAMBLE_LINES} lines of OpenCode's output"
            )
        return None


@adapter_model
@dataclass(frozen=True)
class _UsageStep:
    """A completed step and the ``(provider, model)`` its message named, if any."""

    part: OpenCodePart
    provider_id: str | None
    model_id: str | None


def _instants_of(decoded: dict) -> list[int]:
    """Every epoch-ms instant one decoded line shows, for closing an unended task's window."""
    found: list[int] = []

    def take(container: object, *keys: str) -> None:
        if isinstance(container, dict):
            for key in keys:
                value = container.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    found.append(int(value))

    take(decoded, "timestamp")
    info = decoded.get("info")
    if isinstance(info, dict):
        take(info.get("time"), "created", "completed")
    parts = decoded.get("parts")
    for part in parts if isinstance(parts, list) else []:
        if isinstance(part, dict):
            take(part.get("time"), "start", "end")
            state = part.get("state")
            if isinstance(state, dict):
                take(state.get("time"), "start", "end")
    return found


class OpenCodeAdapter:
    """The OpenCode binding. Dumb: translates the CLI surface, never decides.

    Self-mints its own session id (``honors_session_hint`` is ``False``): a fresh spawn
    returns a :class:`_PendingOpenCodeIdentity`, never an already-identified handle."""

    def __init__(
        self,
        binary: str = "opencode",
        *,
        model: str = "",
        worker_env: AllowlistedEnv,
        model_aliases: Sequence[tuple[str, str]] = (),
        effort_aliases: Sequence[tuple[str, str]] = (),
        worker_config_path: str | None = None,
        effective_config_dir: str | None = None,
        autonomy: Autonomy = Autonomy.Dangerous,
        transcript_source: IHarnessTranscriptSource | None = None,
        price_catalog: IOpenCodePriceCatalog | None = None,
        descendant_usage: OpenCodeDescendantUsage | None = None,
        permission_resolver: IOpenCodePermissionResolver | None = None,
        process: IProcessProbe,
        launcher: IProcessLauncher,
    ) -> None:
        self._binary = binary
        self._command = OpenCodeCommand(binary)
        # Empty is a legitimate default (unlike Claude Code's pinned `DEFAULT_WORKER_MODEL`):
        # OpenCode ships no built-in tier mapping, so it resolves its own configured default.
        self._model = model
        self._model_aliases = dict(model_aliases)
        self._effort_aliases = dict(effort_aliases)
        self._unrecognized_efforts: set[str] = set()
        self._unrecognized_compaction_windows: set[str] = set()
        # The one allowlisted env (``bzh:worker-env-allowlist``) every child this adapter
        # launches is built from — the declared passthrough plus any `PATH` prepend.
        self._worker_env = worker_env
        # The runner-owned permission/plugin document; `None` when this runtime
        # predates the OpenCode binding, or a deployment chose not to scaffold one.
        self._worker_config_path = worker_config_path
        self._effective_config_dir = effective_config_dir
        # `--auto` auto-approves what the permission map does not deny; `Normal` omits it.
        self._autonomy = autonomy
        self._transcript_source: IHarnessTranscriptSource = transcript_source or NullTranscriptSource()
        # Injected, optional: with no catalog, a zero-cost step never gets an estimate.
        self._price_catalog = price_catalog
        # Injected, optional: with none, an invocation's usage is its root session's steps alone.
        self._descendant_usage = descendant_usage
        self._process: IProcessProbe = process
        # Injected (`bzh:dependency-injection`): `Normal` asks OpenCode what a launch can `ask` before it starts,
        # and with none injected refuses to launch rather than launch unproven.
        self._permission_resolver = permission_resolver
        # Injected, never self-constructed (`bzh:dependency-injection`): ONE launcher, both bindings.
        self._launcher: IProcessLauncher = launcher

    def observe_version(self) -> str | None:
        """Shared verbatim with Claude Code (``harness_shared.observe_version``); only
        ``self._binary`` differs between the two."""
        return harness_shared.observe_version(self._binary)

    def resolve_model(self, preferences: Sequence[str]) -> str:
        return harness_shared.resolve_model(
            self._resolve_one_model,
            self._model,
            preferences,
            fallback_label=self._model or "(opencode's own configured default)",
        )

    def resolve_model_strict(self, preferences: Sequence[str]) -> str | None:
        return harness_shared.resolve_model_strict(self._resolve_one_model, preferences)

    def _resolve_one_model(self, entry: str) -> str | None:
        """One preference entry to a native ``provider/model`` pair, or ``None`` if this
        adapter cannot resolve it — including a syntactically-valid pair belonging to
        another harness's own tier vocabulary, skipped rather than handed to a CLI that
        would reject it."""
        if entry.startswith(TIER_PREFIX):
            # OpenCode ships no built-in tier mapping (unlike Claude Code's three defaults):
            # an unmapped tier lets a multi-harness selection skip this binding (harness-selection spec).
            return self._model_aliases.get(entry)
        if entry in self._model_aliases:
            return self._model_aliases[entry]
        # A non-namespaced entry is accepted only as a valid `provider/model` OpenCode
        # reference (execution spec) — OpenCode has no fixed short-name vocabulary.
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

    def resolvable_tier_ids(self) -> tuple[str, ...]:
        """Every tier id this adapter can resolve: OpenCode ships no
        built-in tier mapping, so only the runner's own ``[opencode.models.aliases]``
        table is resolvable here. Shared with Claude Code (``harness_shared.resolvable_tier_ids``)."""
        return harness_shared.resolvable_tier_ids({}, self._model_aliases)

    def resolve_compaction_window(self, value: str | None) -> str | None:
        """Always unsupported: OpenCode's compaction reserve and automatic-compaction
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
        envelope: Envelope,
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
            # (execution spec); with nowhere durable to read from it could never be learned.
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
            auto=self._autonomy is not Autonomy.Normal,
        )
        env = self._spawn_env(envelope, preamble, resume_from or "")
        if workdir:
            self._check_plugins(workdir, env)
        env = self._deny_unanswerable_asks(workdir, env)
        # Both go through `harness_shared.stdout_target`, empty meaning DEVNULL — the same
        # idiom Claude Code's `spawn` honors `preamble.stderr_path` with.
        with (
            harness_shared.stdout_target(preamble.stdout_path) as stdout_file,
            harness_shared.stdout_target(preamble.stderr_path) as stderr_file,
        ):
            try:
                # Deferred — the caller's own `confirm_durable()` (right after ITS durable
                # provisional record lands) is what disarms this launch's parent-death signal.
                launched = self._launcher.launch(
                    cmd,
                    cwd=workdir,
                    env=env,
                    stdout=stdout_file,
                    stderr=stderr_file if stderr_file is not None else subprocess.DEVNULL,
                    defer_disarm=True,
                )
            except OSError as exc:
                _log.error("harness spawn failed", binary=self._binary, cwd=workdir, detail=str(exc))
                raise HarnessSpawnError(f"failed to spawn {self._binary} in {workdir}: {exc}") from exc
        _log.info(
            "spawned worker", binary=self._binary, pid=launched.pid, session_id=resume_from or "(pending)", cwd=workdir
        )
        if resume_from:
            # Resume never performs the handshake (execution spec) — the stored session
            # reference is already authoritative; `Spawner.spawn` still disarms it.
            return WorkerHandle(
                session_id=resume_from,
                pid=launched.pid,
                process_start_time=launched.process_start_time,
                pgid=launched.pgid,
                confirm_durable=launched.confirm_durable,
            )
        return _PendingOpenCodeIdentity(
            pid=launched.pid,
            pgid=launched.pgid,
            process_start_time=launched.process_start_time,
            stdout_path=preamble.stdout_path,
            stderr_path=preamble.stderr_path,
            process=self._process,
            confirm_durable=launched.confirm_durable,
        )

    def honors_session_hint(self) -> bool:
        return False

    def judge(
        self,
        session_cwd: str,
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
            auto=self._autonomy is not Autonomy.Normal,
        )
        env = (
            self.identity_env(preamble, chunk_id, session_id, elicitation=True)
            if preamble is not None
            else self._config_env()
        )
        self._check_plugins(session_cwd, env)
        env = self._deny_unanswerable_asks(session_cwd, env)
        try:
            with harness_shared.stdout_target(output_path, mode="wb") as stdout_file:
                # Deferred — the caller's own `confirm_durable()` (right after ITS durable
                # `record_elicitation_started`/`record_elicitation_relaunch` lands) disarms it.
                launched = self._launcher.launch(
                    cmd, cwd=session_cwd, env=env, stdout=stdout_file, stderr=subprocess.DEVNULL, defer_disarm=True
                )
        except OSError as exc:
            _log.error("elicitation launch failed", binary=self._binary, cwd=session_cwd, detail=str(exc))
            raise HarnessSpawnError(f"failed to launch {self._binary} in {session_cwd}: {exc}") from exc
        _log.info("elicitation launched", binary=self._binary, pid=launched.pid, session_id=session_id, cwd=session_cwd)
        # Left armed — `Judgement._elicit`/`_relaunch` call `confirm_durable()` right after
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
        session_cwd: str,
        session_id: str,
        message: str,
        stdout_path: str = "",
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        model: str | None = None,
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> ResumeHandle:
        del model  # OpenCode's session selects its own model on resume.
        cmd = self._command.build(
            OpenCodeInvocationKind.NUDGE,
            prompt=message,
            session_id=session_id,
            variant=effort,
            auto=self._autonomy is not Autonomy.Normal,
        )
        env = self.identity_env(preamble, chunk_id, session_id) if preamble is not None else self._config_env()
        self._check_plugins(session_cwd, env)
        env = self._deny_unanswerable_asks(session_cwd, env)
        # Deferred: a resume gets the same ownership spawn/judge get — `lifecycle/dormant.py::_wake`
        # calls `confirm_durable()` right after its own durable `record_spawn` lands.
        with harness_shared.stdout_target(stdout_path) as stdout_file:
            launched = self._launcher.launch(
                cmd, cwd=session_cwd, env=env, stdout=stdout_file, stderr=None, defer_disarm=True
            )
        # `launched.pgid` is the launcher's own recorded group — carried to the
        # caller rather than left for it to assume `pgid == pid`.
        return ResumeHandle(
            pid=launched.pid,
            pgid=launched.pgid,
            process_start_time=launched.process_start_time,
            confirm_durable=launched.confirm_durable,
        )

    def resume_command(
        self,
        session_cwd: str,
        session_id: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        attended: bool = False,
    ) -> str:
        # `attended` names no distinct OpenCode composition (unlike Claude Code's
        # `--permission-mode`): the paste string and exec'd form share the same argv (execution spec).
        del attended
        argv = self._command.takeover_argv(session_id=session_id, model=model, variant=effort)
        return f"cd {session_cwd} && {' '.join(argv)}"

    def identity_env(
        self, preamble: WorkerPreamble, chunk_id: str, session_id: str, *, elicitation: bool = False
    ) -> dict[str, str]:
        """Shared base with Claude Code (``harness_shared.build_identity_env``), layering
        this binding's own runner-owned OpenCode config vars on top."""
        env = harness_shared.build_identity_env(
            preamble, chunk_id, session_id, self._worker_env, elicitation=elicitation
        )
        config_env = self._config_env()
        for key in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR"):
            if key in config_env:
                env[key] = config_env[key]
        return env

    def _config_env(self) -> dict[str, str]:
        env = dict(self._worker_env.variables)
        if self._worker_config_path:
            # The runner-owned permission/plugin document — supplied both as a path and
            # its serialized content, as the compatibility proof's `configuration_isolation` probe established.
            env["OPENCODE_CONFIG"] = self._worker_config_path
            with open(self._worker_config_path, encoding="utf-8") as f:
                content = f.read()
            env["OPENCODE_CONFIG_CONTENT"] = (
                content_with_snapshot_references(content, Path(self._effective_config_dir))
                if self._effective_config_dir
                else content
            )
            if self._effective_config_dir:
                env["OPENCODE_CONFIG_DIR"] = self._effective_config_dir
        return env

    def _check_plugins(self, cwd: str, env: dict[str, str]) -> None:
        if self._effective_config_dir:
            check_ambient_plugins(Path(self._effective_config_dir), Path(cwd), env)

    def _deny_unanswerable_asks(self, cwd: str | None, env: dict[str, str]) -> dict[str, str]:
        """``Normal``'s guarantee: no permission rule of an unattended launch can resolve to ``ask``.

        Every reachable ask is composed to ``deny`` in ``OPENCODE_CONFIG_CONTENT``, then OpenCode re-resolves the
        result; a launch it cannot prove clean fails here, before any process starts. Takeover never comes through."""
        if self._autonomy is not Autonomy.Normal:
            return env
        if not cwd:
            raise HarnessSpawnError("no launch cwd can be resolved; refusing an unproven `normal` launch")
        resolver = self._permission_resolver
        if resolver is None:
            raise HarnessSpawnError("no OpenCode permission resolver is wired; refusing an unproven `normal` launch")
        try:
            resolved = resolver.resolve(cwd=cwd, env=env)
            overrides = compose_ask_denials(resolved.merged_config, resolved.agent_rulesets)
            if not overrides:
                return env
            content = env.get("OPENCODE_CONFIG_CONTENT")
            document = merge_overrides(json.loads(content) if content else {}, overrides)
            composed = {**env, "OPENCODE_CONFIG_CONTENT": json.dumps(document, indent=2) + "\n"}
            remaining = residual_asks(resolver.resolve(cwd=cwd, env=composed).agent_rulesets)
        except (OpenCodePermissionResolveError, json.JSONDecodeError) as exc:
            raise HarnessSpawnError(f"cannot establish that an unattended OpenCode launch never asks: {exc}") from exc
        if remaining:
            raise HarnessSpawnError(
                "an unattended OpenCode launch in `normal` autonomy could still ask for permission, which stops the "
                f"agent loop; cannot deny {remaining[0].describe()}"
                + (f" (and {len(remaining) - 1} more)" if len(remaining) > 1 else "")
            )
        return composed

    def _spawn_env(self, envelope: Envelope, preamble: WorkerPreamble, session_id: str) -> dict[str, str]:
        return self.identity_env(preamble, envelope.chunk_id, session_id)

    # --- output/usage (execution spec, "Output and usage") ------------------

    @staticmethod
    def _parse_events(output: str) -> tuple[OpenCodeRunEvent, ...]:
        """Every event this invocation's own captured stdout carries, in emission order,
        skipping any line that fails to parse rather than discarding the whole capture over
        it — OpenCode has no transcript fallback to re-derive a lost verdict from (unlike
        Claude Code's ``ResultEnvelope``). Parses line by line, unlike
        :func:`~.opencode.shapes.parse_run_jsonl`, which raises on the first bad line."""
        events: list[OpenCodeRunEvent] = []
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                decoded = json.loads(stripped)
                events.append(parse_run_event(decoded))
            except (json.JSONDecodeError, OpenCodeShapeError):
                continue
        return tuple(events)

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
        step's tokens. A step-finish part's ``tokens`` sub-fields are all schema-required, so
        no field is ever defaulted to zero here. OpenCode's own ``reasoning`` count folds
        into Blizzard's single ``output_tokens`` column: no reasoning token is ever dropped."""
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
        A subscription-authenticated step reports cost as a literal ``0`` rather than
        omitting the field (execution spec); since a billed step never costs exactly
        nothing, a zero is treated as "not reported" rather than "free", and excluded."""
        known = [part.cost for part in parts if part.cost]
        if not known:
            return None
        return sum(known)

    def _invocation_model_reference(self, model: str | None) -> tuple[str | None, str | None]:
        """This invocation's own ``(provider, model)``, the fallback a zero-cost step's
        estimate uses when its own parsed shape carries no message info. A label
        this binding cannot parse — including OpenCode's own unresolved-default fallback —
        leaves the step unknown rather than guessed."""
        label = model or self._model
        if not label:
            return None, None
        try:
            reference = parse_model_reference(label)
        except OpenCodeShapeError:
            return None, None
        return reference.provider, reference.model

    @staticmethod
    def _step_tokens(part: OpenCodePart) -> OpenCodeStepTokens:
        """One step's own tokens, priced the way :class:`OpenCodeModelPrice` expects —
        reasoning kept apart from output, before :meth:`_sum_tokens` folds the two
        together. ``part.tokens`` is guaranteed non-``None`` by every caller."""
        tokens = part.tokens
        assert tokens is not None
        return OpenCodeStepTokens(
            input=tokens.input_tokens,
            output=tokens.output_tokens,
            reasoning=tokens.reasoning_tokens,
            cache_read=tokens.cache_read_tokens,
            cache_write=tokens.cache_write_tokens,
        )

    def _estimate_step(
        self,
        part: OpenCodePart,
        provider: str | None,
        model: str | None,
        prices: dict[tuple[str, str], OpenCodeModelPrice | None],
    ) -> float | None:
        """This zero-cost step's estimated dollar cost, or ``None`` when it stays unknown:
        no catalog injected, no resolvable provider/model, or the catalog has no priceable
        entry (a missing model, a missing or unreadable cache file). Never raises.
        ``prices`` memoizes lookups across one parse, so the cache is read once per model
        rather than once per step."""
        if self._price_catalog is None or provider is None or model is None:
            return None
        key = (provider, model)
        if key not in prices:
            prices[key] = self._price_catalog.price_for(provider, model)
        price = prices[key]
        if price is None:
            return None
        return price.estimate(self._step_tokens(part))

    def parse_verdict(self, output: str) -> str | None:
        return harness_shared.find_choice_verdict(self._root_text(self._parse_events(output)))

    def has_usable_output(self, output: str) -> bool:
        """True once the root turn produced at least one completed model step — the
        OpenCode-native equivalent of Claude Code's result envelope. A process killed
        before its first ``step_finish`` (an OOM, a ``kill -9``) reads as "lost", exactly
        like a truncated envelope, independent of whether a verdict was ever named."""
        return bool(self._root_step_finishes(self._parse_events(output)))

    def parse_assessment(self, output: str) -> str:
        events = self._parse_events(output)
        after_close = harness_shared.text_after_choice_close(self._root_text(events))
        if after_close is not None:
            return after_close
        # No choice was ever made — an explicit session error is the one thing worth
        # surfacing in its place; anything else is legitimately empty.
        return self._session_error(events) or ""

    def needs_usage_transcript(self, output: str, *, model: str | None = None) -> bool:
        # A run event never carries a step's provider/model — `output` alone never
        # resolves this binding's own gap. The only recovery is the export `model`'s
        # absence forces, and only when this binding has no configured
        # default of its own to price against instead (never pay for a read
        # a pinned or pre-configured invocation didn't need).
        del output
        return model is None and not self._model

    def parse_usage(
        self,
        output: str,
        kind: UsageKind,
        *,
        model: str | None = None,
        transcript_lines: Sequence[str] = (),
        invocation_start: datetime | None = None,
        invocation_end: datetime | None = None,
    ) -> UsageSample | None:
        del transcript_lines  # OpenCode's invocation stream carries its own step usage.
        events = self._parse_events(output)
        finishes = self._root_step_finishes(events)
        # Deduplicated by part identity: the same completed step is never counted twice
        # even were it to appear more than once on this one capture.
        by_id = {part.id: part for part in finishes if part.tokens is not None}
        if not by_id:
            return None
        # A run event carries no provider/model, so the invocation's pair prices a root step.
        steps = [_UsageStep(part, None, None) for part in by_id.values()]
        root = self._root_session_id(events)
        assert root is not None  # a root step finish implies a first event
        tasks = {part.id: part for part in self._task_parts_of_events(events, root)}
        if invocation_start is not None and invocation_end is not None and self._descendant_usage is not None:
            for part in self._descendant_usage.root_tasks(root):
                tasks.setdefault(part.id, part)
        start_ms = int(invocation_start.timestamp() * 1000) if invocation_start is not None else None
        end_ms = int(invocation_end.timestamp() * 1000) if invocation_end is not None else self._horizon_of(events)
        steps.extend(self._descendant_steps(root, self._bounded_tasks(tasks.values(), start_ms, end_ms), end_ms))
        input_tokens, output_tokens, cache_read_tokens, cache_create_tokens = self._sum_tokens(s.part for s in steps)
        return UsageSample(
            kind=kind,
            model=model or self._model or "opencode",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            cost_usd=self._known_cost(s.part for s in steps),
            estimated_cost_usd=self._estimate_unbilled(steps, model),
        )

    def _descendant_steps(
        self, root_session_id: str, task_parts: Sequence[OpenCodePart], horizon_ms: int | None
    ) -> list[_UsageStep]:
        if self._descendant_usage is None or not task_parts:
            return []
        collected: list[DescendantStep] = self._descendant_usage.collect(
            root_session_id=root_session_id, task_parts=task_parts, horizon_ms=horizon_ms
        )
        return [_UsageStep(step.part, step.provider_id, step.model_id) for step in collected]

    @staticmethod
    def _task_parts_of_events(events: Sequence[OpenCodeRunEvent], root: str) -> list[OpenCodePart]:
        return [
            event.part
            for event in events
            if event.type == "tool_use"
            and event.part is not None
            and event.part.tool == "task"
            and event.part.session_id == root
        ]

    @staticmethod
    def _bounded_tasks(parts: Iterable[OpenCodePart], start_ms: int | None, end_ms: int | None) -> list[OpenCodePart]:
        bounded = []
        for part in parts:
            started = part.started_at_ms()
            if start_ms is not None and (started is None or started < start_ms):
                continue
            if end_ms is not None and (started is None or started > end_ms):
                continue
            bounded.append(part)
        return bounded

    @staticmethod
    def _horizon_of(events: Sequence[OpenCodeRunEvent]) -> int | None:
        """The latest event timestamp, epoch ms."""
        stamps = [
            event.raw["timestamp"]
            for event in events
            if isinstance(event.raw.get("timestamp"), int) and not isinstance(event.raw.get("timestamp"), bool)
        ]
        return max(stamps) if stamps else None

    def _estimate_unbilled(self, steps: Sequence[_UsageStep], model: str | None) -> float | None:
        """The summed estimate of every zero-cost step at its own ``(provider, model)``, else the
        invocation's. All-or-nothing: one unpriceable step leaves it ``None``, as does no zero-cost step."""
        unbilled = [step for step in steps if not step.part.cost]
        if not unbilled:
            return None
        invocation_provider, invocation_model = self._invocation_model_reference(model)
        prices: dict[tuple[str, str], OpenCodeModelPrice | None] = {}
        total = 0.0
        for step in unbilled:
            provider, step_model = step.provider_id, step.model_id
            if provider is None or step_model is None:
                provider, step_model = invocation_provider, invocation_model
            amount = self._estimate_step(step.part, provider, step_model, prices)
            if amount is None:
                return None
            total += amount
        return total

    @staticmethod
    def _decode_line(line: str) -> dict | None:
        """One transcript line, JSON-decoded if it is a well-formed object — shared by every
        per-line reader below so one line is decoded at most once per pass over ``lines``."""
        stripped = line.strip()
        if not stripped:
            return None
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None

    @staticmethod
    def _parts_and_model_from_decoded(
        decoded: dict,
    ) -> tuple[list[tuple[OpenCodePart, str | None, str | None]], tuple[str, str] | None]:
        """One decoded transcript line's completed-step parts with the ``(provider, model)``
        its shape carries, and the line's own observed ``(provider, model)`` independent of
        whether it carries a completed step — a run event contributes neither. One shared
        shape-parse backs both: a caller wanting only one of the two still pays for a
        single parse rather than two. Unparseable lines contribute nothing; never a raise."""
        try:
            event = parse_run_event(decoded)
        except OpenCodeShapeError:
            pass
        else:
            run_event_parts: list[tuple[OpenCodePart, str | None, str | None]] = (
                [(event.part, None, None)] if event.type == "step_finish" and event.part is not None else []
            )
            return run_event_parts, None
        try:
            message = OpenCodeMessage.parse(decoded)
        except OpenCodeShapeError:
            return [], None
        parts = [
            (part, message.info.provider_id, message.info.model_id)
            for part in message.parts
            if part.type == "step-finish"
        ]
        observed = (
            (message.info.provider_id, message.info.model_id)
            if message.info.provider_id and message.info.model_id
            else None
        )
        return parts, observed

    def sum_transcript_usage(
        self,
        lines: Sequence[str],
        kind: UsageKind,
        *,
        model: str | None = None,
        invocation_start: datetime | None = None,
        invocation_end: datetime | None = None,
    ) -> UsageSample:
        by_id: dict[str, _UsageStep] = {}
        task_parts: dict[str, OpenCodePart] = {}
        root_session: str | None = None
        observed: tuple[str, str] | None = None
        # One pass over `lines`: the observed (provider, model) is picked up off the
        # same decode that yields each line's finish-parts, rather than a second full pass
        # through `observed_model` re-decoding lines `_worker_sample` may have already read.
        for line in lines:
            decoded = self._decode_line(line)
            if decoded is None:
                continue
            parts, line_observed = self._parts_and_model_from_decoded(decoded)
            if line_observed is not None:
                observed = line_observed
            for part, provider, part_model in parts:
                if part.tokens is None:
                    continue
                if provider is None or part_model is None:
                    # A run-event copy of a step an export line already named keeps that
                    # export's own provider/model rather than erasing it.
                    known = by_id.get(part.id)
                    provider, part_model = (known.provider_id, known.model_id) if known else (None, None)
                by_id[part.id] = _UsageStep(part, provider, part_model)
            for task_part in self._task_parts_of_decoded(decoded):
                task_parts[task_part.id] = task_part
                root_session = root_session or task_part.session_id
        steps = list(by_id.values())
        if root_session is not None:
            start_ms = int(invocation_start.timestamp() * 1000) if invocation_start is not None else None
            end_ms = (
                int(invocation_end.timestamp() * 1000) if invocation_end is not None else self._horizon_of_lines(lines)
            )
            steps.extend(
                self._descendant_steps(root_session, self._bounded_tasks(task_parts.values(), start_ms, end_ms), end_ms)
            )
        input_tokens, output_tokens, cache_read_tokens, cache_create_tokens = self._sum_tokens(s.part for s in steps)
        # A non-zero cost here is a billed figure this fallback drops; no estimate
        # may then mask it.
        estimated_cost_usd = None if any(s.part.cost for s in steps) else self._estimate_unbilled(steps, model)
        observed_model = f"{observed[0]}/{observed[1]}" if observed is not None else None
        return UsageSample(
            kind=kind,
            # The export's own provider/model over a passed-in or configured one:
            # an export line names the model that actually ran, which `model` only approximates.
            model=observed_model or model or self._model or "opencode",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            # A transcript carries no dollar figure (`IHarnessUsageAccounting.sum_transcript_usage`);
            # token counts stay authoritative regardless. It may still carry an estimate.
            cost_usd=None,
            estimated_cost_usd=estimated_cost_usd,
        )

    @classmethod
    def _task_parts_of_decoded(cls, decoded: dict) -> list[OpenCodePart]:
        """The ``task`` tool parts one decoded line carries; an unparseable line carries none."""
        try:
            event = parse_run_event(decoded)
        except OpenCodeShapeError:
            pass
        else:
            return cls._task_parts_of_events([event], event.session_id)
        try:
            message = OpenCodeMessage.parse(decoded)
        except OpenCodeShapeError:
            return []
        return [part for part in message.parts if part.type == "tool" and part.tool == "task"]

    def _horizon_of_lines(self, lines: Sequence[str]) -> int | None:
        """The latest instant the lines show, epoch ms."""
        stamps: list[int] = []
        for line in lines:
            decoded = self._decode_line(line)
            if decoded is None:
                continue
            stamps.extend(_instants_of(decoded))
        return max(stamps) if stamps else None

    def observed_model(self, lines: Sequence[str]) -> str | None:
        observed: tuple[str, str] | None = None
        for line in lines:
            decoded = self._decode_line(line)
            if decoded is None:
                continue
            _, line_observed = self._parts_and_model_from_decoded(decoded)
            if line_observed is not None:
                observed = line_observed
        return f"{observed[0]}/{observed[1]}" if observed is not None else None

    def transcript_source(self) -> IHarnessTranscriptSource:
        return self._transcript_source

    def classify_usage_limit(self, output: str, lines: Sequence[str], now: datetime) -> UsageLimit | None:
        # The invocation's own captured stdout carries every event this turn produced
        # (`_session_error` reads the same way); the transcript range is OpenCode's
        # session-export shape, which a usage-limit refusal never reaches — the turn
        # errors before a step ever finishes.
        del lines
        for event in self._parse_events(output):
            if event.type != "error" or event.error is None:
                continue
            if event.error.status_code != _USAGE_LIMIT_STATUS_CODE:
                continue
            if not _USAGE_LIMIT_MESSAGE_RE.search(event.error.message):
                continue
            return UsageLimit(
                resets_at=self._parse_reset_duration(event.error.message, now), detail=event.error.message
            )
        return None

    @staticmethod
    def _parse_reset_duration(text: str, now: datetime) -> datetime | None:
        """``now`` plus the reported duration, or ``None`` when nothing parsed — never a
        raise, and never a guess past an unrecognized phrasing."""
        match = _RESET_DURATION_RE.search(text)
        if match is None:
            return None
        days, hours, minutes = (int(group) if group else 0 for group in match.groups())
        if days == 0 and hours == 0 and minutes == 0:
            return None
        return now + timedelta(days=days, hours=hours, minutes=minutes)

    def classify_provider_overload(self, output: str, lines: Sequence[str]) -> ProviderOverload | None:
        # Output only, like `classify_usage_limit`: a root `error` event
        # carries the provider's own status, never OpenCode's session-export transcript.
        del lines
        for event in self._parse_events(output):
            if event.type != "error" or event.error is None:
                continue
            if event.error.status_code == _OVERLOAD_STATUS_CODE:
                return ProviderOverload(detail=event.error.message)
            # Secondary guard: only the status check above rests on a known parsed shape.
            if _OVERLOAD_NAME_RE.search(event.error.name):
                return ProviderOverload(detail=event.error.message)
        return None


def _conforms_harness_adapter(x: OpenCodeAdapter) -> IHarnessAdapter:
    return x
