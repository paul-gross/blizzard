"""The OpenCode 2 adapter binding (``bzh:pluggable-seams``): every spawn, resume, judgement, and nudge launches one
``blizzard runner opencode drive`` worker against the operator's managed service, and every verdict, usage figure,
and limit is read from that driver's own result, never OpenCode's output."""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id
from blizzard.foundation.logging import get_logger
from blizzard.foundation.model_support import model_supported
from blizzard.foundation.node_steps import TIER_PREFIX
from blizzard.runner.harness import harness_shared
from blizzard.runner.harness.adapter import (
    HarnessSpawnError,
    HarnessVersionChangedError,
    IHarnessAdapter,
    ResumeHandle,
    WorkerHandle,
    WorkerPreamble,
)
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.internal.stdout_target import stdout_target
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.json_value import decode_json
from blizzard.runner.harness.opencode_v2.attend import DEFAULT_OPENCODE_BINARY, model_option
from blizzard.runner.harness.opencode_v2.client import ModelReference
from blizzard.runner.harness.opencode_v2.errors import ErrorKind
from blizzard.runner.harness.opencode_v2.orphan import ISessionOrphaner, SessionOrphaner
from blizzard.runner.harness.opencode_v2.registration import ServiceRegistration, read_registration, registration_path
from blizzard.runner.harness.opencode_v2.request import DriveKind, DriveRequest, write_request
from blizzard.runner.harness.opencode_v2.result import DriverResult, parse_result
from blizzard.runner.harness.opencode_v2.service import IServiceKeeper, ServiceUnavailableError
from blizzard.runner.harness.opencode_v2.shapes import STEP_TYPES, Message, OpenCodeTwoShapeError, parse_message
from blizzard.runner.harness.opencode_v2.transcript.source import OpenCodeTwoTranscriptSource
from blizzard.runner.harness.opencode_v2.transport import UrllibServiceTransport
from blizzard.runner.harness.opencode_v2.version import ADMITTED_OPENCODE_V2_RANGE_TEXT, normalize_opencode_v2_version
from blizzard.runner.harness.opencode_v2.version_hold import VersionHold
from blizzard.runner.harness.overload import ProviderOverload
from blizzard.runner.harness.process_launch import IProcessLauncher, LaunchedProcess
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, TranscriptErrorFactory
from blizzard.runner.harness.usage import UsageKind, UsageLimit, UsageSample
from blizzard.runner.node_steps.envelope import Envelope
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.child_processes import Stream

_log = get_logger("blizzard.runner.harness")

SESSION_ID_PREFIX = "ses"
DEFAULT_SUPPORTED_MODELS: tuple[str, ...] = ("*/*",)
_EFFORT_ORDINAL = frozenset({"low", "medium", "high", "max"})
_WINDOW = re.compile(r"(?P<count>[0-9]+)(?P<thousands>[kK]?)")
_MODEL = re.compile(r"(?P<provider>[^/\s]+)/(?P<model>\S+)")


def window_tokens(window: str | None) -> int | None:
    """A resolved compaction window in tokens; ``None`` for ``auto`` or none, which leave the operator's own."""
    match = _WINDOW.fullmatch(window or "")
    if match is None:
        return None
    return int(match["count"]) * (1000 if match["thousands"] else 1)


class OpenCodeTwoAdapter:
    """The OpenCode 2 binding. Dumb: composes driver launches and reads driver results, never decides. It mints each
    session's id itself, so a fresh spawn is identified at launch and ignores the runner's hint."""

    def __init__(
        self,
        *,
        runner_id: str,
        worker_env: AllowlistedEnv,
        launcher: IProcessLauncher,
        keeper: IServiceKeeper,
        versions: VersionProbe,
        hold: VersionHold,
        request_dir: Path,
        clock: IClock,
        binary: str = DEFAULT_OPENCODE_BINARY,
        driver_command: Sequence[str] = ("blizzard",),
        model: str = "",
        agent: str | None = None,
        model_aliases: Sequence[tuple[str, str]] = (),
        effort_aliases: Sequence[tuple[str, str]] = (),
        supported_models: Sequence[str] | None = None,
        autonomy: Autonomy = Autonomy.Dangerous,
        transcript_source: IHarnessTranscriptSource | None = None,
        orphaner: ISessionOrphaner | None = None,
    ) -> None:
        self._runner_id = runner_id
        self._worker_env = worker_env
        self._launcher = launcher
        self._keeper = keeper
        self._versions = versions
        self._hold = hold
        self._request_dir = request_dir
        self._clock = clock
        self._binary = binary
        self._driver_command = tuple(driver_command)
        self._model = model
        self._agent = agent
        self._model_aliases = dict(model_aliases)
        self._effort_aliases = dict(effort_aliases)
        self._supported_models = DEFAULT_SUPPORTED_MODELS if supported_models is None else tuple(supported_models)
        self._autonomy = autonomy
        self._transcript_source: IHarnessTranscriptSource = transcript_source or OpenCodeTwoTranscriptSource(
            self._registration,
            UrllibServiceTransport(),
            TranscriptErrorFactory(get_logger("blizzard.runner.harness.transcript")),
            runner_id=runner_id,
        )
        self._orphaner: ISessionOrphaner = orphaner or SessionOrphaner(self._registration, UrllibServiceTransport())
        # The version launches are held to: the one last observed, which is the one the runner advertises.
        self._advertised: str | None = None

    @property
    def autonomy(self) -> Autonomy:
        return self._autonomy

    def observe_version(self) -> str | None:
        """The answering service's version, which the runner advertises and records; with none answering, the
        binary's, whose service the next launch starts."""
        identity = self._keeper.answering()
        raw = identity.version if identity is not None else self._versions.observe_version(self._binary)
        version = normalize_opencode_v2_version(raw) or raw
        self._hold.observe(version)
        self._advertised = version
        return version

    # -- model resolution -------------------------------------------------------------------------------------------

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
        if entry.startswith(TIER_PREFIX):
            return self._model_aliases.get(entry)
        if entry in self._model_aliases:
            return self._model_aliases[entry]
        if not model_supported(entry, self._supported_models) or _MODEL.fullmatch(entry) is None:
            return None
        return entry

    def resolve_effort(self, value: str | None) -> str | None:
        if value is None:
            return None
        aliased = self._effort_aliases.get(value)
        if aliased is not None:
            return aliased
        return value if value in _EFFORT_ORDINAL else None

    def resolve_compaction_window(self, value: str | None) -> str | None:
        if value is None or not (value == "auto" or _WINDOW.fullmatch(value)):
            return None
        return value

    def resolvable_tier_ids(self) -> tuple[str, ...]:
        return harness_shared.resolvable_tier_ids({}, self._model_aliases)

    def supported_models(self) -> tuple[str, ...]:
        return self._supported_models

    def _reference(self, model: str | None, effort: str | None) -> ModelReference | None:
        match = _MODEL.fullmatch(model or self._model or "")
        if match is None:
            return None
        return ModelReference(match["provider"], match["model"], effort)

    # -- launching --------------------------------------------------------------------------------------------------

    def honors_session_hint(self) -> bool:
        return False

    def identity_env(
        self, preamble: WorkerPreamble, chunk_id: str, session_id: str, *, elicitation: bool = False
    ) -> dict[str, str]:
        return harness_shared.build_identity_env(
            preamble, chunk_id, session_id, self._worker_env, elicitation=elicitation
        )

    def _launch(
        self, request: DriveRequest, env: dict[str, str], stdout_path: str, stderr_path: str, *, stdout_mode: str = "ab"
    ) -> LaunchedProcess:
        """Write ``request`` and launch its driver, deferred until the caller's durable record lands. Every failure
        before the driver exists is the seam's :class:`HarnessSpawnError`."""
        try:
            identity = self._keeper.ensure()
        except ServiceUnavailableError as exc:
            raise HarnessSpawnError(f"no OpenCode service to drive the turn on: {exc}") from exc
        self._hold_to_advertised(normalize_opencode_v2_version(identity.version) or identity.version)
        try:
            path = write_request(self._request_dir, request)
        except OSError as exc:
            raise HarnessSpawnError(f"cannot write the OpenCode 2 drive request: {exc}") from exc
        argv = [*self._driver_command, "runner", "opencode", "drive", "--request", str(path)]
        try:
            with (
                stdout_target(stdout_path, mode=stdout_mode) as stdout_file,
                stdout_target(stderr_path) as stderr_file,
            ):
                launched = self._launcher.launch(
                    argv,
                    cwd=request.directory,
                    env=self._worker_env.worker(env),
                    stdout=stdout_file if stdout_file is not None else Stream.INHERIT,
                    stderr=stderr_file if stderr_file is not None else Stream.DEVNULL,
                    defer_disarm=True,
                )
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise HarnessSpawnError(f"failed to launch the OpenCode 2 driver in {request.directory}: {exc}") from exc
        _log.info("launched opencode driver", kind=request.kind.value, session_id=request.session_id, pid=launched.pid)
        return launched

    def _hold_to_advertised(self, version: str) -> None:
        """Refuse a launch on a service whose version is not the one the runner advertised, which the next
        observation advertises afresh; any launch once a version outside the range was seen is refused for good."""
        self._hold.observe(version)
        if self._hold.foreign is not None:
            raise HarnessVersionChangedError(
                f"the OpenCode service reported version {self._hold.foreign}, outside"
                f" {ADMITTED_OPENCODE_V2_RANGE_TEXT}; restart the runner to take it up"
            )
        if self._advertised is not None and self._advertised != version:
            raise HarnessVersionChangedError(
                f"the OpenCode service now reports version {version}, not {self._advertised}"
            )

    def _request(
        self,
        kind: DriveKind,
        session_id: str,
        directory: str,
        prompt: str,
        lease_id: str,
        model: str | None,
        effort: str | None,
        compaction_window: str | None,
    ) -> DriveRequest:
        return DriveRequest(
            kind=kind,
            session_id=session_id,
            directory=directory,
            prompt=prompt,
            runner_id=self._runner_id,
            lease_id=lease_id,
            autonomy=self._autonomy,
            model=self._reference(model, effort),
            agent=self._agent,
            compaction_tokens=window_tokens(compaction_window),
        )

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
    ) -> WorkerHandle:
        del session_hint  # the binding mints its own ids
        if not preamble.environments:
            raise HarnessSpawnError("spawn requires at least one acquired environment")
        directory = SpawnCwd(preamble.workspace_root, preamble.environments[0].workdir).path
        if not directory:
            raise HarnessSpawnError("spawn has no working directory")
        session_id = resume_from or Id.mint(SESSION_ID_PREFIX, self._clock).value
        prompt = "\n\n".join(part for part in (preamble.prompt_prefix, envelope.prompt or "") if part)
        kind = DriveKind.RESUME if resume_from else DriveKind.SPAWN
        request = self._request(
            kind, session_id, directory, prompt, preamble.lease_id, model, effort, compaction_window
        )
        env = self.identity_env(preamble, envelope.chunk_id, session_id)
        launched = self._launch(request, env, preamble.stdout_path, preamble.stderr_path)
        return WorkerHandle(
            session_id=session_id,
            pid=launched.pid,
            process_start_time=launched.process_start_time,
            pgid=launched.pgid,
            confirm_durable=launched.confirm_durable,
        )

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
        lease_id = preamble.lease_id if preamble is not None else ""
        request = self._request(
            DriveKind.JUDGE, session_id, session_cwd, judgement_prompt, lease_id, model, effort, compaction_window
        )
        env = (
            self.identity_env(preamble, chunk_id, session_id, elicitation=True)
            if preamble is not None
            else self._worker_env.variables
        )
        launched = self._launch(request, env, output_path, "", stdout_mode="wb")
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
        lease_id = preamble.lease_id if preamble is not None else ""
        request = self._request(
            DriveKind.NUDGE, session_id, session_cwd, message, lease_id, model, effort, compaction_window
        )
        env = self.identity_env(preamble, chunk_id, session_id) if preamble is not None else self._worker_env.variables
        launched = self._launch(request, env, stdout_path, "")
        return ResumeHandle(
            pid=launched.pid,
            pgid=launched.pgid,
            process_start_time=launched.process_start_time,
            confirm_durable=launched.confirm_durable,
        )

    def _registration(self) -> ServiceRegistration | None:
        path = registration_path(self._worker_env.tool().variables)
        return read_registration(path) if path is not None else None

    def orphan(self, session_id: str) -> None:
        """Interrupt the session tree a killed driver left running; best effort, bounded, and never starting a
        service."""
        self._orphaner.orphan(session_id)

    def reconcile(self, session_ids: Sequence[str]) -> int:
        """Interrupt each session tree still running on the service after its driver died with the daemon, and return
        how many ran; best effort, each bounded, and never starting a service."""
        return sum(self._orphaner.orphan(session_id) for session_id in session_ids)

    def resume_command(
        self,
        session_cwd: str,
        session_id: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        attended: bool = False,
    ) -> str:
        # Pasted or exec'd, takeover goes through `attend`: the tree is stopped, the rules swapped, and the stamped
        # model reasserted first. The TUI switches the agent to its own; the next driven resume restores the lease's.
        del attended
        argv = [*self._driver_command, "runner", "opencode", "attend", session_id]
        reference = self._reference(model, effort)
        if reference is not None:
            argv += ["--model", model_option(reference)]
        if self._binary != DEFAULT_OPENCODE_BINARY:
            argv += ["--opencode", self._binary]
        return f"cd {shlex.quote(session_cwd)} && {shlex.join(argv)}"

    # -- reading the driver's result --------------------------------------------------------------------------------

    @staticmethod
    def _result(output: str) -> DriverResult | None:
        return parse_result(output)

    def parse_verdict(self, output: str) -> str | None:
        result = self._result(output)
        return None if result is None else harness_shared.find_choice_verdict(result.text)

    def has_usable_output(self, output: str) -> bool:
        return self._result(output) is not None

    def parse_assessment(self, output: str) -> str:
        result = self._result(output)
        if result is None:
            return ""
        after_close = harness_shared.text_after_choice_close(result.text)
        if after_close is not None:
            return after_close
        return result.error.message if result.error is not None else ""

    def needs_usage_transcript(self, output: str, *, model: str | None = None) -> bool:
        del output, model  # every step in the driver's result names the model that ran it
        return False

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
        """The root's and every descendant's steps this invocation ran, summed; reasoning folds into output, and a
        zero cost reads as unreported, as a subscription reports it."""
        del transcript_lines, invocation_start, invocation_end
        result = self._result(output)
        if result is None or not result.steps:
            return None
        steps = result.steps
        costs = [step.cost for step in steps if step.cost]
        return UsageSample(
            kind=kind,
            model=result.root_model() or model or self._model or "unknown",
            input_tokens=sum(step.tokens.input for step in steps),
            output_tokens=sum(step.tokens.output + step.tokens.reasoning for step in steps),
            cache_read_tokens=sum(step.tokens.cache_read for step in steps),
            cache_create_tokens=sum(step.tokens.cache_write for step in steps),
            cost_usd=sum(costs) if costs else None,
        )

    def sum_transcript_usage(
        self,
        lines: Sequence[str],
        kind: UsageKind,
        *,
        model: str | None = None,
        observed: str | None = None,
        invocation_start: datetime | None = None,
        invocation_end: datetime | None = None,
    ) -> UsageSample:
        """Every step the transcript's records carry, the root's and each descendant's, summed as the driver's
        result is; what a killed driver never reported is recovered from them."""
        del invocation_start, invocation_end
        steps = [step for _, step in _transcript_steps(lines)]
        costs = [step.cost for step in steps if step.cost]
        return UsageSample(
            kind=kind,
            model=observed or self.observed_model(lines) or model or self._model or "unknown",
            input_tokens=sum(step.tokens.input for step in steps if step.tokens),
            output_tokens=sum(step.tokens.output + step.tokens.reasoning for step in steps if step.tokens),
            cache_read_tokens=sum(step.tokens.cache_read for step in steps if step.tokens),
            cache_create_tokens=sum(step.tokens.cache_write for step in steps if step.tokens),
            cost_usd=sum(costs) if costs else None,
        )

    def observed_model(self, lines: Sequence[str]) -> str | None:
        """The model the newest root step in the records ran."""
        for root, step in reversed(_transcript_steps(lines)):
            if root and step.model is not None:
                return f"{step.model.provider_id}/{step.model.id}"
        return None

    def transcript_source(self) -> IHarnessTranscriptSource:
        return self._transcript_source

    def classify_usage_limit(self, output: str, lines: Sequence[str], now: datetime) -> UsageLimit | None:
        del lines
        result = self._result(output)
        error = None if result is None else result.error
        if error is None or error.kind is not ErrorKind.USAGE_LIMIT:
            return None
        resets_at = None if error.retry_after_ms is None else now + timedelta(milliseconds=error.retry_after_ms)
        return UsageLimit(resets_at=resets_at, detail=error.message)

    def classify_provider_overload(self, output: str, lines: Sequence[str]) -> ProviderOverload | None:
        del lines
        result = self._result(output)
        error = None if result is None else result.error
        if error is None or error.kind is not ErrorKind.OVERLOAD:
            return None
        return ProviderOverload(detail=error.message)


def _transcript_steps(lines: Sequence[str]) -> list[tuple[bool, Message]]:
    """Each step a raw transcript record carries, and whether it is the root's; unreadable records skip."""
    steps: list[tuple[bool, Message]] = []
    for line in lines:
        try:
            record = decode_json(line)
            if not isinstance(record, dict) or not isinstance(raw := record.get("message"), dict):
                continue
            if raw.get("type") not in STEP_TYPES:
                continue
            message = parse_message(raw)
        except (ValueError, OpenCodeTwoShapeError):
            continue
        if message.tokens is not None:
            steps.append((record.get("parent") is None, message))
    return steps


_harness_adapter_conforms: type[IHarnessAdapter] = OpenCodeTwoAdapter

__all__ = ["SESSION_ID_PREFIX", "OpenCodeTwoAdapter", "window_tokens"]
