"""The OpenCode adapter binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.harness.adapter.IHarnessAdapter` against the ``opencode``
CLI. Reuses only the production event/record parsers (``opencode_shapes``) — never the
diagnostic PROCESS/scratch machinery the compatibility proof owns (D5): every worker launches
through :class:`~blizzard.runner.harness.process_launch.ProcessLauncher`, as Claude Code does."""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.adapter import (
    HarnessSpawnError,
    IHarnessAdapter,
    PendingWorkerHandle,
    ResumeHandle,
    WorkerHandle,
    WorkerIdentityError,
    WorkerPreamble,
)
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.internal import harness_shared
from blizzard.runner.harness.internal.opencode_command import OpenCodeCommand, OpenCodeInvocationKind
from blizzard.runner.harness.internal.opencode_price_cache import (
    IOpenCodePriceCatalog,
    OpenCodeModelPrice,
    OpenCodeStepTokens,
)
from blizzard.runner.harness.internal.opencode_shapes import (
    OpenCodeMessage,
    OpenCodePart,
    OpenCodeRunEvent,
    OpenCodeShapeError,
    parse_model_reference,
    parse_run_event,
)
from blizzard.runner.harness.overload import ProviderOverload
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NullTranscriptSource
from blizzard.runner.harness.usage import UsageKind, UsageLimit, UsageSample
from blizzard.runner.loop.process import IProcessProbe
from blizzard.wire.envelope import TIER_PREFIX, NodeEnvelope

_log = get_logger("blizzard.runner.harness")

# The well-known effort ordinal (issue #144); outside it needs an explicit `[opencode.effort.aliases]`, never a guess.
_EFFORT_ORDINAL = frozenset({"low", "medium", "high", "max"})

# How often a fresh mint's pending handle re-reads the stdout capture while awaiting identity.
_IDENTITY_POLL_INTERVAL_SECONDS = 0.05

# Leading non-identity lines the handshake tolerates before giving up as a spawn failure.
_MAX_IDENTITY_PREAMBLE_LINES = 20

# Bound on the diagnostic stderr tail a failed handshake's error carries (enough for one traceback line).
_STDERR_TAIL_BYTES = 2000

# The status a usage-limit refusal reports (blizzard#594) — distinct from an ordinary
# rate-limit's transient 429s (out of scope, issue #595) by its own message phrasing below.
_USAGE_LIMIT_STATUS_CODE = 429
_USAGE_LIMIT_MESSAGE_RE = re.compile(r"usage limit", re.IGNORECASE)

# The captured shape's own relative-reset phrasing (blizzard#594 D6): "reset in 2 hours",
# "reset in 1 day 4 hours" — a duration, never a clock time (unlike Claude Code's).
_RESET_DURATION_RE = re.compile(
    r"reset\w*\s+in\s+(?:(\d+)\s*day[s]?\s*)?(?:(\d+)\s*hour[s]?\s*)?(?:(\d+)\s*minute[s]?\s*)?", re.IGNORECASE
)

# blizzard#595: `_PROVIDER_REFUSAL_STATUSES` (opencode_facts.py) excludes 529, so it never
# collides with the usage-limit/refusal statuses above. The name match is a secondary
# guard only — the status check above is the one known-shape signal.
_OVERLOAD_STATUS_CODE = 529
_OVERLOAD_NAME_RE = re.compile(r"overloaded", re.IGNORECASE)


@dataclass(frozen=True)
class _PendingOpenCodeIdentity:
    """Phase one's OpenCode-specific pending handle (D1): the launch is real, but identity
    is read from the worker's own stdout (execution spec, "Fresh-session handshake"). Never
    constructed for a resume, which already knows its session id and returns a plain
    :class:`WorkerHandle` instead, whose ``await_identity`` is its own trivial phase two."""

    pid: int
    pgid: int  # every launch gets one (D3) — see `ProcessLauncher.launch`
    process_start_time: str
    stdout_path: str
    stderr_path: str
    process: IProcessProbe
    confirm_durable: Callable[[], None] = field(
        default=lambda: None, compare=False
    )  # F1's disarm signal; no-op default

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
        transcript_source: IHarnessTranscriptSource | None = None,
        price_catalog: IOpenCodePriceCatalog | None = None,
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
        # The runner-owned permission/plugin document (D7); `None` when this runtime
        # predates the OpenCode binding, or a deployment chose not to scaffold one.
        self._worker_config_path = worker_config_path
        self._transcript_source: IHarnessTranscriptSource = transcript_source or NullTranscriptSource()
        # Injected, optional: with no catalog, a zero-cost step never gets an estimate.
        self._price_catalog = price_catalog
        self._process: IProcessProbe = process
        # Injected, never self-constructed (`bzh:dependency-injection`): ONE launcher, both bindings (D4).
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
        """Every tier id this adapter can resolve (blizzard#433): OpenCode ships no
        built-in tier mapping, so only the runner's own ``[opencode.models.aliases]``
        table is resolvable here. Shared with Claude Code (``harness_shared.resolvable_tier_ids``)."""
        return harness_shared.resolvable_tier_ids({}, self._model_aliases)

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
            auto=True,
        )
        env = self._spawn_env(envelope, preamble, resume_from or "")
        # Both go through `harness_shared.stdout_target`, empty meaning DEVNULL — the same
        # idiom Claude Code's `spawn` honors `preamble.stderr_path` with.
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
            # reference is already authoritative; `Spawner.spawn` still disarms it (F1).
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
            auto=True,
        )
        env = (
            self.identity_env(preamble, chunk_id, session_id, elicitation=True)
            if preamble is not None
            else self._worker_env.variables
        )
        try:
            with harness_shared.stdout_target(output_path, mode="wb") as stdout_file:
                # F1: deferred — the caller's own `confirm_durable()` (right after ITS durable
                # `record_elicitation_started`/`record_elicitation_relaunch` lands) disarms it.
                launched = self._launcher.launch(
                    cmd, cwd=session_cwd, env=env, stdout=stdout_file, stderr=subprocess.DEVNULL, defer_disarm=True
                )
        except OSError as exc:
            _log.error("elicitation launch failed", binary=self._binary, cwd=session_cwd, detail=str(exc))
            raise HarnessSpawnError(f"failed to launch {self._binary} in {session_cwd}: {exc}") from exc
        _log.info("elicitation launched", binary=self._binary, pid=launched.pid, session_id=session_id, cwd=session_cwd)
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
        session_cwd: str,
        session_id: str,
        message: str,
        stdout_path: str = "",
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> ResumeHandle:
        cmd = self._command.build(
            OpenCodeInvocationKind.NUDGE,
            prompt=message,
            session_id=session_id,
            variant=effort,
            auto=True,
        )
        env = self.identity_env(preamble, chunk_id, session_id) if preamble is not None else self._worker_env.variables
        # Deferred (F1, D4): a resume gets the same ownership spawn/judge get — `dormant.py::_wake`
        # calls `confirm_durable()` right after its own durable `record_spawn` lands.
        with harness_shared.stdout_target(stdout_path) as stdout_file:
            launched = self._launcher.launch(
                cmd, cwd=session_cwd, env=env, stdout=stdout_file, stderr=None, defer_disarm=True
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
        if self._worker_config_path:
            # The runner-owned permission/plugin document (D7) — supplied both as a path and
            # its serialized content, as the compatibility proof's `configuration_isolation` probe established.
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
        """Every event this invocation's own captured stdout carries, in emission order,
        skipping any line that fails to parse rather than discarding the whole capture over
        it — OpenCode has no transcript fallback to re-derive a lost verdict from (unlike
        Claude Code's ``ResultEnvelope``). Parses line by line, unlike
        :func:`~.opencode_shapes.parse_run_jsonl`, which raises on the first bad line."""
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

    def parse_usage(self, output: str, kind: UsageKind, *, model: str | None = None) -> UsageSample | None:
        finishes = self._root_step_finishes(self._parse_events(output))
        # Deduplicated by part identity: the same completed step is never counted twice
        # even were it to appear more than once on this one capture.
        by_id = {part.id: part for part in finishes if part.tokens is not None}
        if not by_id:
            return None
        parts = list(by_id.values())
        input_tokens, output_tokens, cache_read_tokens, cache_create_tokens = self._sum_tokens(parts)
        # `output` is run events, never an export, and a run event never carries a step's
        # provider/model: nothing here to observe, so the fallback chain is the only source.
        provider, resolved_model = self._invocation_model_reference(model)
        estimated: list[float] = []
        prices: dict[tuple[str, str], OpenCodeModelPrice | None] = {}
        # Every step here prices against this one (provider, resolved_model) pair, so the
        # lookup can never diverge step to step — unlike the per-step pairs below.
        for part in parts:
            if part.cost:
                continue
            amount = self._estimate_step(part, provider, resolved_model, prices)
            if amount is not None:
                estimated.append(amount)
        return UsageSample(
            kind=kind,
            model=model or self._model or "opencode",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            cost_usd=self._known_cost(parts),
            estimated_cost_usd=sum(estimated) if estimated else None,
        )

    @staticmethod
    def _finish_parts_from_line(line: str) -> list[tuple[OpenCodePart, str | None, str | None]]:
        """One transcript line's completed-step parts with the ``(provider, model)`` its shape
        carries — none for a run event, ``message.info``'s for an exported message. The
        caller's identity-keyed dedup collapses a step both shapes describe (execution spec).
        Unparseable lines contribute nothing; never a raise."""
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
            return [(event.part, None, None)] if event.type == "step_finish" and event.part is not None else []
        try:
            message = OpenCodeMessage.parse(decoded)
        except OpenCodeShapeError:
            return []
        return [
            (part, message.info.provider_id, message.info.model_id)
            for part in message.parts
            if part.type == "step-finish"
        ]

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind, *, model: str | None = None) -> UsageSample:
        by_id: dict[str, tuple[OpenCodePart, str | None, str | None]] = {}
        for line in lines:
            for part, provider, part_model in self._finish_parts_from_line(line):
                if part.tokens is None:
                    continue
                if provider is None or part_model is None:
                    # A run-event copy of a step an export line already named keeps that
                    # export's own provider/model rather than erasing it.
                    _, provider, part_model = by_id.get(part.id, (part, None, None))
                by_id[part.id] = (part, provider, part_model)
        parts = [entry[0] for entry in by_id.values()]
        input_tokens, output_tokens, cache_read_tokens, cache_create_tokens = self._sum_tokens(parts)
        estimated_cost_usd: float | None = None
        # A non-zero cost here is a billed figure this fallback drops; no estimate
        # may then mask it.
        if not any(part.cost for part in parts):
            invocation_provider, invocation_model = self._invocation_model_reference(model)
            amounts: list[float | None] = []
            prices: dict[tuple[str, str], OpenCodeModelPrice | None] = {}
            for part, provider, part_model in by_id.values():
                # The step's own (provider, model) pair when its shape carried both, else the
                # invocation's pair — never one half of each.
                if provider is None or part_model is None:
                    provider, part_model = invocation_provider, invocation_model
                amounts.append(self._estimate_step(part, provider, part_model, prices))
            # All-or-nothing: one unpriced step leaves the whole estimate unknown rather
            # than silently understated.
            complete = bool(amounts) and all(a is not None for a in amounts)
            estimated_cost_usd = sum(a for a in amounts if a is not None) if complete else None
        return UsageSample(
            kind=kind,
            # The export's own provider/model over a passed-in or configured one (blizzard#629):
            # an export line names the model that actually ran, which `model` only approximates.
            model=self.observed_model(lines) or model or self._model or "opencode",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            # A transcript carries no dollar figure (`IHarnessUsageAccounting.sum_transcript_usage`);
            # token counts stay authoritative regardless. It may still carry an estimate.
            cost_usd=None,
            estimated_cost_usd=estimated_cost_usd,
        )

    @staticmethod
    def _export_provider_model(line: str) -> tuple[str, str] | None:
        """One transcript line's ``(provider, model)`` when it is an exported message
        naming both — ``None`` for a run event (which names neither) or an export whose
        ``message.info`` leaves either field unset."""
        stripped = line.strip()
        if not stripped:
            return None
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if not isinstance(decoded, dict):
            return None
        try:
            message = OpenCodeMessage.parse(decoded)
        except OpenCodeShapeError:
            return None
        if message.info.provider_id and message.info.model_id:
            return message.info.provider_id, message.info.model_id
        return None

    def observed_model(self, lines: Sequence[str]) -> str | None:
        observed: tuple[str, str] | None = None
        for line in lines:
            found = self._export_provider_model(line)
            if found is not None:
                observed = found
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
        # Output only, like `classify_usage_limit` (blizzard#595): a root `error` event
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
