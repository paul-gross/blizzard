"""Concrete, process-owning probes for the pinned OpenCode compatibility proof.

The binding invokes the supplied binary with argument lists, gives children the allowlisted
environment and runner-owned config, parses Phase 1 shapes, and turns each probe into one
observation. Provider access follows the caller's explicit live-provider opt-in.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from blizzard.runner.harness.compatibility import (
    PROBE_ROSTER,
    CompatibilityProbe,
    EvidenceState,
    ProbeObservation,
)
from blizzard.runner.harness.internal.opencode_attach import IAttachProxyFactory
from blizzard.runner.harness.internal.opencode_compaction import (
    IOpenCodeCompactor,
    OpenCodeCompactionResult,
)
from blizzard.runner.harness.internal.opencode_cursor import CursorError
from blizzard.runner.harness.internal.opencode_facts import (
    has_exact_permission_denial,
    has_live_tool_state,
    has_requested_model_variant,
    has_requested_model_variant_for_message,
    provider_refusal,
    runner_config_denies,
    transcript_evidence,
)
from blizzard.runner.harness.internal.opencode_loopback import (
    ILoopbackTransport,
    LoopbackRequest,
    LoopbackTransportError,
    local_server_argv,
    wait_for_local_server,
)
from blizzard.runner.harness.internal.opencode_process import (
    IOpenCodeProcess,
    OpenCodeProcessError,
    OpenCodeProcessResult,
    OpenCodeStartedProcess,
    stop_started_process,
)
from blizzard.runner.harness.internal.opencode_proof_script import (
    CONFIG_PERMISSION_COMMAND,
    CONFIGURATION_PROMPT,
    FRESH_PROMPT,
    PERMISSION_AGENT,
    PERMISSION_COMMAND,
    PERMISSION_PROMPT,
    PERMISSION_TOOL,
    PROCESS_CONTROL_COMMAND,
    PROCESS_CONTROL_PROMPT,
    RESUME_PROMPT,
    SECURITY_DENIAL_COMMAND,
    TOOL_AGENT,
)
from blizzard.runner.harness.internal.opencode_scratch_config import (
    COMPACTION_TAIL_TURNS,
    ISOLATION_EVIDENCE,
    PROJECT_CONFIG_SENTINEL,
    RUNNER_CONFIG_USERNAME,
    USER_CONFIG_SENTINEL,
    IsolationRoots,
    child_env,
    prepare_isolation,
    provision_disposable_auth,
    write_runner_config,
)
from blizzard.runner.harness.internal.opencode_scratch_git import IOpenCodeScratchGit, OpenCodeScratchRepo
from blizzard.runner.harness.internal.opencode_shapes import (
    OpenCodeRunEvent,
    OpenCodeSessionExport,
    OpenCodeShapeError,
    parse_child_sessions,
    parse_model_reference,
    parse_run_jsonl,
    parse_session_export,
)
from blizzard.runner.harness.internal.opencode_takeover import OpenCodeTakeoverProbe
from blizzard.runner.harness.internal.opencode_transcript import (
    TranscriptExportSample,
    TranscriptProof,
    inspect_transcript,
)

PINNED_VERSION_PATTERN = re.compile(
    r"^\s*(?:opencode(?:\s+version)?\s+)?(?:v)?"
    r"(?P<version>\d+\.\d+\.\d+(?:(?:-[0-9A-Za-z][0-9A-Za-z.-]*)|(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)|(?:\.[0-9A-Za-z][0-9A-Za-z.-]*))?)"
    r"\s*$",
    re.IGNORECASE,
)
DEFAULT_COMMAND_TIMEOUT_SECONDS = 60.0
# The one version this binding is built to prove; the neutral contract only carries it as data.
PINNED_OPENCODE_VERSION = "1.18.25"
SHAPE_FAULT_SUMMARY = "OpenCode emitted an unsupported or malformed required shape"
INTERNAL_FAULT_SUMMARY = "the compatibility probe failed before it could observe OpenCode"
BOUNDARY_FAULT_SUMMARY = "the runner could not establish the fail-closed filesystem boundary"
TRANSCRIPT_POLL_SECONDS = 0.1
TRANSCRIPT_MAX_LIVE_EXPORTS = 32


@dataclass(frozen=True)
class _TurnResult:
    events: tuple[OpenCodeRunEvent, ...] | None
    export: OpenCodeSessionExport | None
    error: str | None
    transcript: TranscriptProof | None = None
    provider_refusal: str | None = None


class OpenCodeProbeError(RuntimeError):
    """The concrete OpenCode binding could not produce one of its observations."""


class LiveProviderOptInRequired(OpenCodeProbeError):
    """A provider-reaching probe was constructed without an explicit opt-in."""


class OpenCodeCompatibilityProbe:
    """Run the closed probe roster against one explicit OpenCode binary and scratch repo."""

    def __init__(
        self,
        *,
        binary: str,
        model: str,
        variant: str,
        scratch_git: IOpenCodeScratchGit,
        process: IOpenCodeProcess,
        compactor: IOpenCodeCompactor,
        transport: ILoopbackTransport,
        attach_proxy_factory: IAttachProxyFactory,
        allow_live_provider: bool = False,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        if allow_live_provider is not True:
            raise LiveProviderOptInRequired("pass the explicit live-provider opt-in before running OpenCode")
        if not isinstance(binary, str) or not binary.strip():
            raise OpenCodeProbeError("the OpenCode binary is empty")
        binary_path = Path(binary)
        if not binary_path.is_file() or not os.access(binary_path, os.X_OK):
            raise OpenCodeProbeError("the OpenCode binary must be an executable file")
        try:
            model_reference = parse_model_reference(model)
        except ValueError as exc:
            raise OpenCodeProbeError("the OpenCode model must use the exact non-empty provider/model form") from exc
        if (
            not isinstance(variant, str)
            or not variant.strip()
            or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in variant)
        ):
            raise OpenCodeProbeError("the OpenCode variant must be a non-empty single argument")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise OpenCodeProbeError("the OpenCode command timeout must be positive")
        self.binary = str(binary_path.resolve())
        self.model = model
        self.variant = variant
        self._model_reference = model_reference
        self._scratch_git = scratch_git
        self._process = process
        self._compactor = compactor
        self._transport = transport
        self._attach_proxy_factory = attach_proxy_factory
        self._timeout_seconds = timeout_seconds
        self.expected_version = PINNED_OPENCODE_VERSION
        self.observed_version = "unknown"
        self._evidence: dict[str, object] = {}
        self._config_snapshots: dict[Path, bytes] = {}
        self._security_markers: tuple[Path, Path] | None = None

    @property
    def evidence(self) -> Mapping[str, object]:
        """Raw process evidence for the outer sanitizer; never print this mapping directly."""

        return dict(self._evidence)

    @property
    def secret_values(self) -> tuple[str, ...]:
        """Caller-supplied secret values are intentionally empty for the live binding.
        The child environment is built by :class:`AllowlistedEnv`, and the one credential file the
        probe handles is copied byte-for-byte without being parsed, so no credential value is ever
        available to name here.  Shape/key and bearer sanitization protects retained output.
        """

        return ()

    def run(self) -> Sequence[ProbeObservation]:
        """Run all probes and return one observation for every closed-roster member."""

        self.observed_version = "unknown"
        self._evidence = {"operations": []}
        observations: dict[CompatibilityProbe, ProbeObservation] = {}
        try:
            with tempfile.TemporaryDirectory(prefix="blizzard-opencode-isolation-") as isolated:
                roots = prepare_isolation(Path(isolated))
                self._evidence["isolated_paths"] = roots.to_payload()
                self._evidence["xdg"] = dict(ISOLATION_EVIDENCE)
                preflight_cwd = roots.root / "preflight-cwd"
                preflight_cwd.mkdir(mode=0o700)
                preflight_env = child_env(None, roots)
                self._evidence["preflight"] = {
                    "cwd": str(preflight_cwd),
                    "environment_keys": sorted(preflight_env),
                }
                sandbox_preflight = getattr(self._process, "preflight", None)
                if not callable(sandbox_preflight):
                    self._evidence["sandbox"] = {"available": False, "reason": "missing process boundary"}
                    self._fill_failed(
                        observations,
                        PROBE_ROSTER,
                        "the OpenCode process binding did not provide a fail-closed filesystem boundary",
                    )
                    return self._ordered_observations(observations)
                try:
                    sandbox_preflight(cwd=preflight_cwd, env=preflight_env)
                except OpenCodeProcessError:
                    self._evidence["sandbox"] = {"available": False, "reason": "boundary preflight failed"}
                    self._fill_failed(observations, PROBE_ROSTER, BOUNDARY_FAULT_SUMMARY)
                    return self._ordered_observations(observations)
                self._evidence["sandbox"] = {
                    "available": True,
                    "filesystem": "landlock-abi-3-or-newer",
                    "tool_user": "same-user-landlock-layer",
                }
                self.observed_version = self._observe_version(preflight_cwd, preflight_env)
                if self.observed_version != PINNED_OPENCODE_VERSION:
                    self._evidence["preflight_blocked"] = "version-mismatch"
                    self._fill_failed(
                        observations,
                        PROBE_ROSTER,
                        f"OpenCode version {self.observed_version!r} does not match the pinned "
                        f"{PINNED_OPENCODE_VERSION}",
                    )
                    return self._ordered_observations(observations)

                if provision_disposable_auth(roots):
                    xdg = self._evidence["xdg"]
                    assert isinstance(xdg, dict)
                    xdg["auth_provisioned"] = True
                with self._scratch_git.new_scratch_repo() as raw_repo:
                    repo = OpenCodeScratchRepo(Path(raw_repo.workdir))
                    self._evidence["scratch_workdir"] = str(repo.workdir)
                    runner_config = write_runner_config(repo.workdir, roots, model=self.model, variant=self.variant)
                    config_path = runner_config.path
                    self._security_markers = runner_config.security_markers
                    self._config_snapshots = runner_config.snapshots
                    self._evidence["config"] = runner_config.evidence
                    env = child_env(config_path, roots)
                    self._evidence["environment_keys"] = sorted(env)
                    fresh = self._fresh_turn(repo.workdir, env)
                    if fresh.provider_refusal is not None:
                        self._evidence["provider_refusal"] = fresh.provider_refusal
                        self._fill_ambiguous(observations, PROBE_ROSTER, fresh.provider_refusal)
                        return self._ordered_observations(observations)
                    if fresh.export is not None:
                        # Usage is an export-level contract. Keep observing it even when the
                        # stronger live transcript proof fails independently.
                        observations[CompatibilityProbe.USAGE_COST] = self._usage_observation(fresh.export)
                    if fresh.error is None and fresh.events is not None and fresh.export is not None:
                        observations[CompatibilityProbe.FRESH_TURN] = ProbeObservation.observed(
                            CompatibilityProbe.FRESH_TURN,
                            f"fresh turn emitted {len(fresh.events)} parsed event(s), committed the requested "
                            "file, and exported",
                            "fresh/events",
                            "fresh/commit",
                            "fresh/export",
                        )
                        observations[CompatibilityProbe.TRANSCRIPT_READ] = self._transcript_observation(
                            CompatibilityProbe.TRANSCRIPT_READ, fresh.transcript
                        )
                        observations[CompatibilityProbe.TRANSCRIPT_CURSOR] = self._transcript_observation(
                            CompatibilityProbe.TRANSCRIPT_CURSOR, fresh.transcript
                        )
                    else:
                        self._fill_failed(
                            observations,
                            (
                                CompatibilityProbe.FRESH_TURN,
                                CompatibilityProbe.TRANSCRIPT_READ,
                                CompatibilityProbe.TRANSCRIPT_CURSOR,
                            ),
                            fresh.error or "fresh turn did not produce a parsed export",
                        )
                        if fresh.export is None:
                            self._fill_failed(
                                observations,
                                (CompatibilityProbe.USAGE_COST,),
                                fresh.error or "fresh turn did not produce a parsed export",
                            )

                    session_id = self._session_id(fresh.events)
                    resume: _TurnResult | None = None
                    if session_id is not None:
                        resume = self._resume_turn(repo.workdir, env, session_id)
                        if resume.error is None and resume.events is not None and resume.export is not None:
                            observations[CompatibilityProbe.RESUME] = ProbeObservation.observed(
                                CompatibilityProbe.RESUME,
                                "the existing session accepted a follow-up turn and exported it",
                                "resume/events",
                                "resume/export",
                            )
                            observations[CompatibilityProbe.JUDGEMENT] = self._judgement_observation(
                                resume.events, resume.export
                            )
                            if fresh.export is not None:
                                observations[CompatibilityProbe.MODEL_VARIANT] = self._model_variant_observation(
                                    fresh.export, resume.export
                                )
                        else:
                            self._fill_failed(
                                observations,
                                (
                                    CompatibilityProbe.RESUME,
                                    CompatibilityProbe.JUDGEMENT,
                                    CompatibilityProbe.MODEL_VARIANT,
                                ),
                                resume.error or "resume did not produce a parsed export",
                            )
                        takeover = self._takeover_probe().observe(repo.workdir, env, session_id)
                        self._evidence["takeover"] = takeover.evidence
                        observations[CompatibilityProbe.TAKEOVER] = takeover.observation
                        observations[CompatibilityProbe.CHILD_SESSIONS] = self._children_observation(
                            repo.workdir, env, session_id
                        )
                    else:
                        self._fill_failed(
                            observations,
                            (
                                CompatibilityProbe.RESUME,
                                CompatibilityProbe.JUDGEMENT,
                                CompatibilityProbe.MODEL_VARIANT,
                                CompatibilityProbe.TAKEOVER,
                                CompatibilityProbe.CHILD_SESSIONS,
                            ),
                            "fresh turn did not report a session identity",
                        )

                    observations[CompatibilityProbe.PERMISSION] = self._permission_observation(repo.workdir, env, roots)
                    observations[CompatibilityProbe.PROCESS_CONTROL] = self._process_control_observation(
                        repo.workdir, env
                    )
                    observations[CompatibilityProbe.ROOT_HOOK] = ProbeObservation.absent(
                        CompatibilityProbe.ROOT_HOOK,
                        "the runner-owned proof config has no portable hook lifecycle signal",
                        "config/root-hook-unsupported",
                    )
                    observations[CompatibilityProbe.CONFIGURATION_ISOLATION] = self._configuration_observation(
                        repo.workdir, config_path, env, roots
                    )
        except Exception as exc:
            message = self._unexpected_error(exc)
            self.observed_version = self.observed_version or "unknown"
            self._fill_failed(observations, PROBE_ROSTER, message)
        return self._ordered_observations(observations)

    @staticmethod
    def _ordered_observations(
        observations: Mapping[CompatibilityProbe, ProbeObservation],
    ) -> tuple[ProbeObservation, ...]:
        return tuple(
            observations.get(
                probe,
                ProbeObservation.failed(probe, "the probe did not return an observation", "runtime/missing"),
            )
            for probe in PROBE_ROSTER
        )

    def _observe_version(self, cwd: Path, env: Mapping[str, str]) -> str:
        result = self._invoke("version", [self.binary, "--version"], cwd=cwd, env=env)
        if result.returncode != 0:
            return "unknown"
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            return "unknown"
        match = PINNED_VERSION_PATTERN.fullmatch(lines[0])
        return match.group("version") if match else "unknown"

    def _fresh_turn(self, cwd: Path, env: Mapping[str, str]) -> _TurnResult:
        """Run the edit/commit turn and export it repeatedly while live and after it exits."""

        argv = self._run_args(FRESH_PROMPT, agent=TOOL_AGENT)
        self._record_operation("fresh", argv, cwd=cwd, result=None)
        try:
            started = self._process.start_capture(argv, cwd=cwd, env=env)
        except OpenCodeProcessError:
            return _TurnResult(None, None, "OpenCode process could not be started for the live transcript proof")

        outcome: _TurnResult | None = None
        try:
            outcome = self._collect_fresh_turn(started, cwd, env)
        finally:
            reaped = stop_started_process(started)
        if not reaped:
            return _TurnResult(
                outcome.events if outcome is not None else None,
                outcome.export if outcome is not None else None,
                "OpenCode fresh-turn process group could not be reaped",
                outcome.transcript if outcome is not None else None,
                outcome.provider_refusal if outcome is not None else None,
            )
        assert outcome is not None
        return outcome

    def _collect_fresh_turn(
        self,
        started: OpenCodeStartedProcess,
        cwd: Path,
        env: Mapping[str, str],
    ) -> _TurnResult:
        """Collect a fresh turn while the caller owns unconditional process cleanup."""

        live_samples: list[TranscriptExportSample] = []
        deadline = time.monotonic() + self._timeout_seconds
        session_id: str | None = None
        while time.monotonic() < deadline and started.poll() is None:
            line = started.read_line(TRANSCRIPT_POLL_SECONDS)
            if line:
                try:
                    line_events = parse_run_jsonl(line)
                except ValueError:
                    line_events = ()
                refusal = provider_refusal(line_events)
                if refusal is not None:
                    return _TurnResult(None, None, refusal, provider_refusal=refusal)
                line_session = self._session_id(line_events)
                if line_session is not None:
                    session_id = line_session
            if session_id is None or len(live_samples) >= TRANSCRIPT_MAX_LIVE_EXPORTS:
                continue
            export, error = self._export_session(
                cwd, env, session_id, operation=f"fresh_export_during_{len(live_samples) + 1}"
            )
            if error is None and export is not None and started.poll() is None:
                live_samples.append(TranscriptExportSample(f"during_{len(live_samples) + 1}", True, export))
                if has_live_tool_state(export) and len(live_samples) >= 2:
                    break

        remaining = max(0.1, deadline - time.monotonic())
        try:
            result = started.result(remaining)
        except Exception:
            return _TurnResult(None, None, "OpenCode live transcript process could not be collected")
        if result.timed_out:
            return _TurnResult(None, None, "OpenCode fresh turn timed out")
        if result.returncode != 0:
            return _TurnResult(None, None, f"OpenCode fresh turn exited with status {result.returncode}")
        try:
            events = parse_run_jsonl(result.stdout)
        except ValueError as exc:
            return _TurnResult(None, None, self._safe_error(exc))
        refusal = provider_refusal(events)
        if refusal is not None:
            return _TurnResult(events, None, refusal, provider_refusal=refusal)
        session_id = self._session_id(events)
        if session_id is None:
            return _TurnResult(events, None, "OpenCode output did not include a session identity")
        export, error = self._export_session(cwd, env, session_id, operation="fresh_export_after")
        if error is not None or export is None:
            return _TurnResult(events, None, error or "OpenCode post-exit export was empty")
        if export.info.parent_id is not None:
            return _TurnResult(events, export, "OpenCode fresh session was not a root session")
        after_repeat, repeat_error = self._export_session(cwd, env, session_id, operation="fresh_export_after_repeat")
        if repeat_error is None and after_repeat is not None:
            samples = [
                *live_samples,
                TranscriptExportSample("after", False, export),
                TranscriptExportSample("after_repeat", False, after_repeat),
            ]
        else:
            samples = [*live_samples, TranscriptExportSample("after", False, export)]
        seed = self._resume_turn(cwd, env, session_id)
        if seed.export is not None:
            samples.append(TranscriptExportSample("compaction_seed", False, seed.export))

        compaction = self._compact_session(cwd, env, session_id)
        samples.extend(compaction.samples)
        transcript = inspect_transcript(samples)
        self._evidence["transcript"] = transcript_evidence(samples, transcript)
        if compaction.error is not None:
            return _TurnResult(events, export, compaction.error, transcript)
        if seed.error is not None:
            return _TurnResult(events, export, "the compaction seed turn failed: " + seed.error, transcript)
        if not compaction.effective:
            return _TurnResult(
                events,
                export,
                "the summarize request did not produce an attributable compaction transition",
                transcript,
            )
        if not transcript.valid:
            return _TurnResult(events, export, "transcript proof: " + "; ".join(transcript.failures), transcript)
        verifier = getattr(self._scratch_git, "has_fresh_commit", None)
        if not callable(verifier) or not verifier(OpenCodeScratchRepo(cwd), "compatibility-proof.txt", "ok\n"):
            return _TurnResult(events, export, "the requested compatibility proof file was not committed", transcript)
        return _TurnResult(events, export, None, transcript)

    def _compact_session(self, cwd: Path, env: Mapping[str, str], session_id: str) -> OpenCodeCompactionResult:
        def capture(operation: str, live: bool) -> TranscriptExportSample | None:
            export, error = self._export_session(cwd, env, session_id, operation=operation)
            if error is not None or export is None:
                return None
            return TranscriptExportSample(operation, live, export, phase="compaction")

        result = self._compactor.compact(
            binary=self.binary,
            cwd=cwd,
            env=env,
            session_id=session_id,
            provider=self._model_reference.provider,
            model=self._model_reference.model,
            capture=capture,
            record_operation=lambda operation, argv: self._record_operation(operation, argv, cwd=cwd, result=None),
            record_http_operation=lambda operation, method, path, status: self._record_http_operation(
                operation,
                method,
                path,
                status,
                cwd=cwd,
                identifiers={"session_id": session_id},
            ),
        )
        self._evidence["compaction"] = {
            "request_succeeded": result.request_succeeded,
            "request_status": result.request_status,
            "transition_observed": result.transition_observed,
            "effective": result.effective,
        }
        return result

    def _resume_turn(self, cwd: Path, env: Mapping[str, str], session_id: str) -> _TurnResult:
        result = self._invoke(
            "resume",
            self._run_args(RESUME_PROMPT, session_id=session_id),
            cwd=cwd,
            env=env,
            identifiers={"session_id": session_id},
        )
        events, export, error = self._events_and_export(
            result, cwd=cwd, env=env, session_id=session_id, operation="resume_export"
        )
        if error is not None or events is None or export is None:
            return _TurnResult(events, export, error or "resume did not produce a parsed export")
        repeat, repeat_error = self._export_session(cwd, env, session_id, operation="resume_export_repeat")
        if repeat_error is None and repeat is not None:
            self._evidence["resume_export_repeated"] = True
        return _TurnResult(events, export, None)

    def _events_and_export(
        self,
        result: OpenCodeProcessResult,
        *,
        cwd: Path,
        env: Mapping[str, str],
        session_id: str | None,
        operation: str,
    ) -> tuple[tuple[OpenCodeRunEvent, ...] | None, OpenCodeSessionExport | None, str | None]:
        if result.timed_out:
            return None, None, "OpenCode command timed out"
        if result.returncode != 0:
            return None, None, self._exit_detail(result, "command")
        try:
            events = parse_run_jsonl(result.stdout)
            actual_session = self._session_id(events)
            if actual_session is None:
                return None, None, "OpenCode output did not include a session identity"
            if session_id is not None and actual_session != session_id:
                return None, None, "OpenCode resume changed the session identity"
            export, error = self._export_session(cwd, env, actual_session, operation=operation)
            return events, export, error
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return None, None, self._safe_error(exc)

    def _export_session(
        self,
        cwd: Path,
        env: Mapping[str, str],
        session_id: str,
        *,
        operation: str,
    ) -> tuple[OpenCodeSessionExport | None, str | None]:
        result = self._invoke(
            operation,
            [self.binary, "export", session_id],
            cwd=cwd,
            env=env,
            identifiers={"session_id": session_id},
        )
        if result.timed_out:
            return None, "OpenCode export timed out"
        if result.returncode != 0:
            return None, self._exit_detail(result, "export")
        try:
            export = parse_session_export(json.loads(result.stdout))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return None, self._safe_error(exc)
        if export.info.id != session_id:
            return None, "OpenCode export identity did not match the run identity"
        return export, None

    def _permission_observation(self, cwd: Path, env: Mapping[str, str], roots: IsolationRoots) -> ProbeObservation:
        primary = self._command_denial_observation(
            CompatibilityProbe.PERMISSION,
            "permission",
            PERMISSION_PROMPT,
            PERMISSION_COMMAND,
            cwd=cwd,
            env=env,
            summary="the runner-owned Bash rule produced one terminal denial for the requested command",
            evidence="permission/denial",
        )
        if primary.state is not EvidenceState.OBSERVED:
            return primary
        return self._security_boundary_observation(cwd, env, roots)

    def _security_boundary_observation(
        self, cwd: Path, env: Mapping[str, str], roots: IsolationRoots
    ) -> ProbeObservation:
        """Prove the configured model-tool shell cannot read auth or mutate a host path."""

        if self._security_markers is None:
            return ProbeObservation.failed(
                CompatibilityProbe.PERMISSION,
                "the security probe did not receive its marker paths",
                "security/markers",
            )
        auth_marker, outside_marker = self._security_markers
        denial = self._command_denial_observation(
            CompatibilityProbe.PERMISSION,
            "permission_security_denial",
            (
                "Compatibility security proof. Use the Bash tool exactly once with the exact command "
                f"`{SECURITY_DENIAL_COMMAND}`. The command must be denied; do not replace it with prose."
            ),
            SECURITY_DENIAL_COMMAND,
            cwd=cwd,
            env=env,
            summary="the runner-owned Bash rule produced an explicit security-probe denial",
            evidence="security/denial",
        )
        if denial.state is not EvidenceState.OBSERVED:
            return ProbeObservation.failed(
                CompatibilityProbe.PERMISSION,
                "a model security command was not explicitly denied",
                "security/denial",
            )
        auth_command = f"cat {shlex.quote(str(roots.auth_path))} > {shlex.quote(str(auth_marker))}"
        outside_command = f"printf outside > {shlex.quote(str(outside_marker))}"
        for index, command in enumerate((auth_command, outside_command), start=1):
            result = self._invoke(
                f"permission_boundary_{index}",
                [str(roots.model_tool_shell), "-c", command],
                cwd=cwd,
                env=env,
            )
            if result.timed_out:
                return ProbeObservation.failed(
                    CompatibilityProbe.PERMISSION,
                    "the model-tool filesystem boundary timed out",
                    f"security/{index}/timeout",
                )
            if result.returncode == 0:
                return ProbeObservation.failed(
                    CompatibilityProbe.PERMISSION,
                    "the model-tool filesystem boundary allowed a denied command",
                    f"security/{index}/allow",
                )
            if result.start_failed:
                return ProbeObservation.failed(
                    CompatibilityProbe.PERMISSION,
                    BOUNDARY_FAULT_SUMMARY,
                    f"security/{index}/boundary",
                )
            if result.returncode < 0:
                return ProbeObservation.failed(
                    CompatibilityProbe.PERMISSION,
                    "the model-tool filesystem boundary exited unsuccessfully",
                    f"security/{index}/exit",
                )

        try:
            auth_marker_contents = auth_marker.read_bytes() if auth_marker.exists() else b""
        except OSError:
            auth_marker_contents = b"unreadable"
        if auth_marker_contents:
            return ProbeObservation.failed(
                CompatibilityProbe.PERMISSION,
                "the model-tool filesystem boundary allowed an auth read",
                "security/auth-marker",
            )
        try:
            outside_unchanged = outside_marker.read_bytes() == b"unchanged\n"
        except OSError:
            outside_unchanged = False
        if not outside_unchanged:
            return ProbeObservation.failed(
                CompatibilityProbe.PERMISSION,
                "the model security command mutated an external marker",
                "security/outside-marker",
            )
        return ProbeObservation.observed(
            CompatibilityProbe.PERMISSION,
            "the denied model command was rejected, and the configured model-tool shell, exercised directly, "
            "could neither read disposable auth nor mutate an external path",
            "permission/denial",
            "security/denial",
            "security/auth-denial",
            "security/outside-denial",
            "security/non-mutation",
        )

    def _command_denial_observation(
        self,
        probe: CompatibilityProbe,
        operation: str,
        prompt: str,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str],
        summary: str,
        evidence: str,
    ) -> ProbeObservation:
        if not runner_config_denies(env, command):
            return ProbeObservation.failed(
                probe,
                "the runner-owned configuration does not deny the requested Bash command",
                "config/rule",
            )
        result = self._invoke(operation, self._run_args(prompt, agent=PERMISSION_AGENT), cwd=cwd, env=env)
        if result.timed_out:
            return ProbeObservation.failed(probe, "the permission turn timed out", f"{operation}/timeout")
        if result.returncode != 0:
            return ProbeObservation.failed(
                probe,
                INTERNAL_FAULT_SUMMARY if result.start_failed else "the permission turn exited unsuccessfully",
                f"{operation}/exit",
            )
        try:
            events = parse_run_jsonl(result.stdout)
        except (ValueError, TypeError, json.JSONDecodeError):
            return ProbeObservation.failed(
                probe,
                "the permission turn did not emit a parseable terminal tool result",
                f"{operation}/shape",
            )
        if not has_exact_permission_denial(events, command=command):
            return ProbeObservation.failed(
                probe,
                "the requested Bash command did not produce one terminal denial from the configured rule",
                f"{operation}/denial",
            )
        return ProbeObservation.observed(probe, summary, evidence)

    def _process_control_observation(self, cwd: Path, env: Mapping[str, str]) -> ProbeObservation:
        argv = self._run_args(PROCESS_CONTROL_PROMPT, agent=TOOL_AGENT)
        self._record_operation("process_control", argv, cwd=cwd, result=None)
        try:
            child = self._process.start_capture(argv, cwd=cwd, env=env)
        except (OpenCodeProcessError, AttributeError):
            return ProbeObservation.failed(
                CompatibilityProbe.PROCESS_CONTROL,
                "OpenCode process could not be started for live-state interruption",
                "process-control/start",
            )
        observation: ProbeObservation | None = None
        live_state = False
        try:
            live_state = self._wait_for_requested_live_tool(child, cwd, env, PROCESS_CONTROL_COMMAND)
            if not live_state:
                exited = child.poll() is not None
                detail = "the requested long-running Bash tool never reached a live state"
                if exited:
                    detail = "the process exited before the requested long-running Bash tool reached a live state"
                observation = ProbeObservation.failed(
                    CompatibilityProbe.PROCESS_CONTROL,
                    detail,
                    "process-control/live-state",
                )
            else:
                observation = ProbeObservation.observed(
                    CompatibilityProbe.PROCESS_CONTROL,
                    "the running OpenCode process was interrupted and reaped",
                    "process-control/terminated",
                )
        finally:
            reaped = stop_started_process(child)
        self._evidence["process_control"] = {
            "tool": PERMISSION_TOOL,
            "command": PROCESS_CONTROL_COMMAND,
            "live_state_observed": live_state,
            "reaped": reaped,
        }
        if not reaped:
            return ProbeObservation.failed(
                CompatibilityProbe.PROCESS_CONTROL,
                "the interrupted OpenCode process group remained alive",
                "process-control/alive",
            )
        assert observation is not None
        return observation

    def _wait_for_requested_live_tool(
        self,
        child: OpenCodeStartedProcess,
        cwd: Path,
        env: Mapping[str, str],
        command: str,
    ) -> bool:
        """Wait until an export, not the terminal-only run stream, shows the requested tool live."""

        deadline = time.monotonic() + self._timeout_seconds
        session_id: str | None = None
        export_index = 0
        while time.monotonic() < deadline and child.poll() is None:
            line = child.read_line(TRANSCRIPT_POLL_SECONDS)
            if line:
                try:
                    events = parse_run_jsonl(line)
                except (ValueError, TypeError, json.JSONDecodeError):
                    events = ()
                session_id = self._session_id(events) or session_id
            if session_id is None or child.poll() is not None:
                continue
            export_index += 1
            export, error = self._export_session(
                cwd,
                env,
                session_id,
                operation=f"process_control_export_{export_index}",
            )
            if error is None and export is not None and has_live_tool_state(export, command) and child.poll() is None:
                return True
        return False

    def _configuration_observation(
        self,
        cwd: Path,
        config_path: Path,
        env: Mapping[str, str],
        roots: IsolationRoots,
    ) -> ProbeObservation:
        try:
            outside_scratch = not config_path.is_relative_to(cwd)
        except AttributeError:
            outside_scratch = not str(config_path).startswith(f"{cwd}{os.sep}")
        if (
            not config_path.exists()
            or not outside_scratch
            or env.get("OPENCODE_CONFIG") != str(config_path)
            or env.get("OPENCODE_DISABLE_PROJECT_CONFIG") != "1"
        ):
            return ProbeObservation.failed(
                CompatibilityProbe.CONFIGURATION_ISOLATION,
                "OpenCode did not receive an existing runner-owned config outside the scratch repo "
                "with project config disabled",
                "config/isolation",
            )
        effective_result = self._invoke(
            "configuration_effective",
            [self.binary, "debug", "config", "--pure"],
            cwd=cwd,
            env=env,
        )
        if effective_result.timed_out or effective_result.returncode != 0:
            return ProbeObservation.failed(
                CompatibilityProbe.CONFIGURATION_ISOLATION,
                INTERNAL_FAULT_SUMMARY
                if effective_result.start_failed
                else "OpenCode could not report its effective runner configuration",
                "config/effective",
            )
        try:
            effective = json.loads(effective_result.stdout)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ProbeObservation.failed(
                CompatibilityProbe.CONFIGURATION_ISOLATION,
                "OpenCode's effective configuration was not a JSON object",
                "config/effective",
            )
        if not isinstance(effective, Mapping):
            return ProbeObservation.failed(
                CompatibilityProbe.CONFIGURATION_ISOLATION,
                "OpenCode's effective configuration was not a JSON object",
                "config/effective",
            )
        effective_text = json.dumps(effective, sort_keys=True)
        if (
            effective.get("username") != RUNNER_CONFIG_USERNAME
            or effective.get("model") != self.model
            or PROJECT_CONFIG_SENTINEL in effective_text
            or USER_CONFIG_SENTINEL in effective_text
        ):
            return ProbeObservation.failed(
                CompatibilityProbe.CONFIGURATION_ISOLATION,
                "OpenCode's effective configuration retained a competing project or user sentinel",
                "config/effective",
            )
        if effective.get("shell") != str(roots.model_tool_shell):
            return ProbeObservation.failed(
                CompatibilityProbe.CONFIGURATION_ISOLATION,
                "OpenCode did not resolve the runner-owned model-tool shell",
                "config/effective-shell",
            )
        # `debug config --pure` drops keys the schema does not know, so an echoed value is the
        # only proof that the compaction key the transcript proof depends on was understood.
        compaction = effective.get("compaction")
        if not isinstance(compaction, Mapping) or compaction.get("tail_turns") != COMPACTION_TAIL_TURNS:
            return ProbeObservation.failed(
                CompatibilityProbe.CONFIGURATION_ISOLATION,
                "OpenCode did not resolve the runner-owned compaction tail bound",
                "config/effective-compaction",
            )
        denial = self._command_denial_observation(
            CompatibilityProbe.CONFIGURATION_ISOLATION,
            "configuration_effect",
            CONFIGURATION_PROMPT,
            CONFIG_PERMISSION_COMMAND,
            cwd=cwd,
            env=env,
            summary="OpenCode enforced the runner-owned Bash denial while competing project and user "
            "config was isolated",
            evidence="config/effect",
        )
        if denial.state is not EvidenceState.OBSERVED:
            return denial
        unchanged = self._config_files_unchanged()
        if not unchanged:
            return ProbeObservation.failed(
                CompatibilityProbe.CONFIGURATION_ISOLATION,
                "the runner, project, or user configuration file changed during the proof",
                "config/nonmutation",
            )
        return ProbeObservation.observed(
            CompatibilityProbe.CONFIGURATION_ISOLATION,
            "OpenCode resolved the runner config and its model-tool shell, omitted both competing sentinels, "
            "and left all config files unchanged",
            "config/effective",
            "config/effective-shell",
            "config/effect",
            "config/nonmutation",
        )

    def _config_files_unchanged(self) -> bool:
        return all(
            path.is_file() and path.read_bytes() == expected for path, expected in self._config_snapshots.items()
        )

    def _children_observation(self, cwd: Path, env: Mapping[str, str], session_id: str) -> ProbeObservation:
        argv = local_server_argv(self.binary)
        self._record_operation("children_server", argv, cwd=cwd, result=None)
        try:
            server = self._process.start_capture(argv, cwd=cwd, env=env)
        except (OpenCodeProcessError, AttributeError):
            return ProbeObservation.failed(
                CompatibilityProbe.CHILD_SESSIONS,
                "the child-session server could not be started",
                "children/start",
            )
        result: OpenCodeProcessResult
        try:
            base_url = wait_for_local_server(server, self._timeout_seconds)
            if base_url is None:
                result = OpenCodeProcessResult(-1, "", "", timed_out=server.poll() is None)
            else:
                request = LoopbackRequest(
                    method="GET",
                    url=f"{base_url}/session/{session_id}/children",
                    headers={"X-OpenCode-Directory": str(cwd)},
                )
                status: int | None = None
                try:
                    with self._transport.request(request, timeout=self._timeout_seconds) as response:
                        status = response.status
                        body = response.read().decode("utf-8")
                        result = OpenCodeProcessResult(
                            0 if 200 <= status < 300 else -1,
                            body if 200 <= status < 300 else "",
                            "",
                        )
                except (LoopbackTransportError, TimeoutError, OSError, UnicodeDecodeError):
                    result = OpenCodeProcessResult(-1, "", "")
                self._record_http_operation(
                    "children",
                    "GET",
                    urlsplit(request.url).path,
                    status,
                    cwd=cwd,
                    identifiers={"session_id": session_id},
                )
        finally:
            reaped = stop_started_process(server)
        if not reaped:
            return ProbeObservation.failed(
                CompatibilityProbe.CHILD_SESSIONS,
                "the child-session server could not be reaped",
                "children/reap",
            )
        return self._children_result_observation(result, session_id)

    def _children_result_observation(self, result: OpenCodeProcessResult, session_id: str) -> ProbeObservation:
        if result.timed_out:
            return ProbeObservation.failed(
                CompatibilityProbe.CHILD_SESSIONS,
                "the child-session command timed out",
                "children/timeout",
            )
        if result.returncode != 0:
            return ProbeObservation.failed(
                CompatibilityProbe.CHILD_SESSIONS,
                "the child-session command exited unsuccessfully",
                "children/exit",
            )
        try:
            children = parse_child_sessions(json.loads(result.stdout))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return ProbeObservation.failed(CompatibilityProbe.CHILD_SESSIONS, self._safe_error(exc), "children/shape")
        if not children:
            return ProbeObservation.absent(
                CompatibilityProbe.CHILD_SESSIONS,
                "the session returned no child sessions",
                "children/empty",
            )
        if any(child.parent_id != session_id for child in children):
            return ProbeObservation.failed(
                CompatibilityProbe.CHILD_SESSIONS,
                "a child-session result did not identify the probed session as its parent",
                "children/parent",
            )
        return ProbeObservation.observed(
            CompatibilityProbe.CHILD_SESSIONS,
            f"the session returned {len(children)} parsed child session(s)",
            "children/response",
        )

    def _takeover_probe(self) -> OpenCodeTakeoverProbe:
        return OpenCodeTakeoverProbe(
            binary=self.binary,
            process=self._process,
            transport=self._transport,
            attach_proxy_factory=self._attach_proxy_factory,
            timeout_seconds=self._timeout_seconds,
            host=self,
        )

    def record_operation(
        self,
        operation: str,
        argv: Sequence[str] | None,
        *,
        cwd: Path,
        result: OpenCodeProcessResult | None,
        identifiers: Mapping[str, str] | None = None,
    ) -> None:
        self._record_operation(operation, argv, cwd=cwd, result=result, identifiers=identifiers)

    def record_http_operation(
        self,
        operation: str,
        method: str,
        path: str,
        status: int | None,
        *,
        cwd: Path,
        identifiers: Mapping[str, str] | None = None,
    ) -> None:
        self._record_http_operation(operation, method, path, status, cwd=cwd, identifiers=identifiers)

    def export_session(
        self, cwd: Path, env: Mapping[str, str], session_id: str, *, operation: str
    ) -> tuple[OpenCodeSessionExport | None, str | None]:
        return self._export_session(cwd, env, session_id, operation=operation)

    def _transcript_observation(
        self, probe: CompatibilityProbe, transcript: TranscriptProof | None
    ) -> ProbeObservation:
        if transcript is None:
            return ProbeObservation.failed(
                probe,
                "the live transcript proof did not run",
                "transcript/missing",
            )
        if probe is CompatibilityProbe.TRANSCRIPT_READ:
            failures = transcript.read_failures
            summary = "repeated exports were obtainable while the turn was live and after it exited"
            evidence = ("transcript/live-export", "transcript/post-exit-export")
        else:
            failures = transcript.cursor_failures
            summary = (
                "repeated exports preserved unique stable identities and ordering, and the cursor admitted only "
                "genuinely new records across a compaction that never returned a removed identity"
            )
            evidence = ("transcript/identities", "transcript/compaction")
        if failures:
            return ProbeObservation.failed(
                probe,
                "the live transcript proof was incomplete: " + "; ".join(failures),
                "transcript/proof",
            )
        return ProbeObservation.observed(probe, summary, *evidence)

    def _model_variant_observation(
        self, fresh_export: OpenCodeSessionExport, resume_export: OpenCodeSessionExport
    ) -> ProbeObservation:
        fresh_ok = has_requested_model_variant(
            fresh_export, self._model_reference.provider, self._model_reference.model, self.variant
        )
        resume_ok = has_requested_model_variant(
            resume_export, self._model_reference.provider, self._model_reference.model, self.variant
        )
        if fresh_ok and resume_ok:
            return ProbeObservation.observed(
                CompatibilityProbe.MODEL_VARIANT,
                "fresh and resumed assistant messages retained the requested model and variant",
                "fresh/export-model-variant",
                "resume/export-model-variant",
            )
        return ProbeObservation.failed(
            CompatibilityProbe.MODEL_VARIANT,
            "fresh and resumed assistant exports did not both retain the requested model and variant",
            "export/model-variant",
        )

    def _usage_observation(self, export: OpenCodeSessionExport) -> ProbeObservation:
        has_tokens = any(message.info.tokens is not None for message in export.messages) or any(
            part.tokens is not None for message in export.messages for part in message.parts
        )
        has_cost = any(message.info.cost is not None for message in export.messages) or any(
            part.cost is not None for message in export.messages for part in message.parts
        )
        if not has_tokens:
            return ProbeObservation.failed(
                CompatibilityProbe.USAGE_COST,
                "the export carried no token usage shape",
                "export/usage",
            )
        if not has_cost:
            return ProbeObservation.absent(
                CompatibilityProbe.USAGE_COST,
                "token usage was exported but no cost was reported",
                "export/usage",
            )
        return ProbeObservation.observed(
            CompatibilityProbe.USAGE_COST,
            "the export carried token usage and an explicit cost",
            "export/usage",
        )

    def _judgement_observation(
        self, events: Sequence[OpenCodeRunEvent], export: OpenCodeSessionExport
    ) -> ProbeObservation:
        choice_parts = [
            event.part
            for event in events
            if event.part is not None and event.part.text is not None and "<Choice>pass</Choice>" in event.part.text
        ]
        if choice_parts and any(
            has_requested_model_variant_for_message(
                export, part.message_id, self._model_reference.provider, self._model_reference.model, self.variant
            )
            for part in choice_parts
        ):
            return ProbeObservation.observed(
                CompatibilityProbe.JUDGEMENT,
                "the resumed judgement emitted the pass choice and its assistant export retained model and variant",
                "resume/judgement",
                "resume/judgement-model-variant",
            )
        if any(
            event.part is not None and event.part.text is not None and "<Choice>pass</Choice>" in event.part.text
            for event in events
        ):
            return ProbeObservation.failed(
                CompatibilityProbe.JUDGEMENT,
                "the resumed judgement choice was not tied to an assistant export with the requested model and variant",
                "resume/judgement-model-variant",
            )
        return ProbeObservation.failed(
            CompatibilityProbe.JUDGEMENT,
            "the resumed turn emitted no parseable pass choice",
            "resume/judgement",
        )

    @staticmethod
    def _session_id(events: Sequence[OpenCodeRunEvent] | None) -> str | None:
        if not events:
            return None
        session_ids = {event.session_id for event in events if event.session_id}
        return next(iter(session_ids)) if len(session_ids) == 1 else None

    def _run_args(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        agent: str | None = None,
        directory: Path | None = None,
    ) -> list[str]:
        args = [
            self.binary,
            "run",
            "--auto",
            "--format",
            "json",
            "--model",
            self.model,
            "--variant",
            self.variant,
        ]
        if session_id is not None:
            args.extend(("--session", session_id))
        if agent is not None:
            args.extend(("--agent", agent))
        if directory is not None:
            args.extend(("--dir", str(directory)))
        args.append(prompt)
        return args

    def _record_operation(
        self,
        operation: str,
        argv: Sequence[str] | None,
        *,
        cwd: Path,
        result: OpenCodeProcessResult | None,
        identifiers: Mapping[str, str] | None = None,
    ) -> None:
        operations = self._evidence.setdefault("operations", [])
        if not isinstance(operations, list):
            return
        operation_record: dict[str, object] = {
            "operation": operation,
            "cwd": str(cwd),
        }
        if argv is not None:
            operation_record["argv"] = list(argv)
        if identifiers:
            operation_record["observed_identifiers"] = dict(identifiers)
        if result is not None:
            operation_record.update(
                {
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                    "stdout_line_count": len(result.stdout.splitlines()),
                    "stderr_line_count": len(result.stderr.splitlines()),
                    "output_retained": False,
                    "output_truncated": result.output_truncated,
                    "process_group_reaped": result.process_group_reaped,
                }
            )
        operations.append(operation_record)

    def _record_http_operation(
        self,
        operation: str,
        method: str,
        path: str,
        status: int | None,
        *,
        cwd: Path,
        identifiers: Mapping[str, str] | None = None,
    ) -> None:
        operations = self._evidence.setdefault("operations", [])
        if not isinstance(operations, list):
            return
        operation_record: dict[str, object] = {
            "operation": operation,
            "cwd": str(cwd),
            "http": {"method": method, "path": path, "status": status},
        }
        if identifiers:
            operation_record["observed_identifiers"] = dict(identifiers)
        operations.append(operation_record)

    def _invoke(
        self,
        operation: str,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        identifiers: Mapping[str, str] | None = None,
    ) -> OpenCodeProcessResult:
        try:
            result = self._process.run(argv, cwd=cwd, env=env, timeout=self._timeout_seconds)
        except OpenCodeProcessError:
            result = OpenCodeProcessResult(-1, "", "", timed_out=False, start_failed=True)
        self._record_operation(operation, argv, cwd=cwd, result=result, identifiers=identifiers)
        return result

    @staticmethod
    def _fill_failed(
        observations: dict[CompatibilityProbe, ProbeObservation],
        probes: Sequence[CompatibilityProbe],
        detail: str,
    ) -> None:
        for probe in probes:
            observations.setdefault(
                probe,
                ProbeObservation.failed(probe, detail, f"{probe.value}/failure"),
            )

    @staticmethod
    def _exit_detail(result: OpenCodeProcessResult, what: str) -> str:
        """A command the runner never started says nothing about OpenCode's own exit behavior."""

        if result.start_failed:
            return INTERNAL_FAULT_SUMMARY
        return f"OpenCode {what} exited with status {result.returncode}"

    @staticmethod
    def _fill_ambiguous(
        observations: dict[CompatibilityProbe, ProbeObservation],
        probes: Sequence[CompatibilityProbe],
        detail: str,
    ) -> None:
        for probe in probes:
            observations.setdefault(
                probe,
                ProbeObservation.ambiguous(probe, detail, f"{probe.value}/provider-refusal"),
            )

    def _safe_error(self, error: Exception) -> str:
        """Every caller of this has already parsed OpenCode's own output, so the fault is its shape."""

        del error
        return SHAPE_FAULT_SUMMARY

    def _unexpected_error(self, error: Exception) -> str:
        """Blame OpenCode only for a fault in its own output; a runner fault is not evidence about it."""

        if isinstance(error, OpenCodeShapeError | CursorError | json.JSONDecodeError):
            return SHAPE_FAULT_SUMMARY
        return INTERNAL_FAULT_SUMMARY


__all__ = [
    "DEFAULT_COMMAND_TIMEOUT_SECONDS",
    "PINNED_VERSION_PATTERN",
    "LiveProviderOptInRequired",
    "OpenCodeCompatibilityProbe",
    "OpenCodeProbeError",
]
