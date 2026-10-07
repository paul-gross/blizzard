"""The per-signal harness-telemetry plan and the spawn env it layers onto a Claude Code worker (unit tier)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, NoReturn

import pytest

from blizzard.foundation.harness_telemetry_outcome import HarnessTelemetryOutcome
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import AcquiredEnvironment, WorkerPreamble
from blizzard.runner.harness.claude_code.adapter import ClaudeCodeAdapter
from blizzard.runner.harness.claude_code.section import WORKER_SETTINGS_FILENAME, ClaudeCodeSection
from blizzard.runner.harness.claude_code.telemetry_plan import plan_harness_telemetry
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryPlan
from blizzard.runner.harness.process_launch import LaunchedProcess
from blizzard.runner.harness.telemetry_signals import TelemetrySignal
from blizzard.runner.harness.wiring import claude_code_section, publish_harness_bundle
from blizzard.runner.runtime import Runtime
from tests.harness_sections import sections
from tests.runner_fakes import FakeProbe, make_envelope

pytestmark = pytest.mark.unit

CAPTURED = HarnessTelemetryOutcome.CAPTURED
YIELDED = HarnessTelemetryOutcome.OPERATOR_CONFIGURED
NO_DESTINATION = HarnessTelemetryOutcome.NO_RUNNER_DESTINATION
OFF = HarnessTelemetryOutcome.OFF

#: A runner whose own variables export every signal.
RUNNER_ENV = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}

_HARNESS_NAMES = ("CLAUDE_CODE_ENABLE_TELEMETRY", "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA")


def _config(tmp_path: Path, *, worker_settings_path: str | None = None, **overrides: object) -> RunnerConfig:
    if worker_settings_path is not None:
        overrides["harness_sections"] = sections(ClaudeCodeSection(worker_settings_path=worker_settings_path))
    return RunnerConfig(root=tmp_path, db_url="sqlite://", **overrides)  # type: ignore[arg-type]


def _settings(tmp_path: Path, env: Mapping[str, str]) -> str:
    path = tmp_path / "worker-settings.json"
    path.write_text(json.dumps({"env": dict(env), "hooks": {}}))
    return str(path)


def _plan(
    config: RunnerConfig, *, runner_environ: Mapping[str, str] = RUNNER_ENV, enabled: bool = True, bundle=None
) -> HarnessTelemetryPlan:  # type: ignore[no-untyped-def]
    return plan_harness_telemetry(
        claude_code_section(config.harness_sections),
        worker_env=config.worker_env,
        bundle=bundle,
        runner_environ=runner_environ,
        enabled=enabled,
    )


def _all(outcome: HarnessTelemetryOutcome) -> HarnessTelemetryPlan:
    return HarnessTelemetryPlan(traces=outcome, metrics=outcome, logs=outcome)


def test_every_signal_is_off_while_the_setting_is_off(tmp_path: Path) -> None:
    assert _plan(_config(tmp_path), enabled=False) == _all(OFF)


def test_every_signal_is_captured_when_nothing_is_configured(tmp_path: Path) -> None:
    assert _plan(_config(tmp_path, worker_settings_path=_settings(tmp_path, {}))) == _all(CAPTURED)


def test_every_signal_is_off_without_an_enabled_claude_code_binding(tmp_path: Path) -> None:
    config = _config(tmp_path, harness_sections=sections(ClaudeCodeSection(enabled=False)))
    assert _plan(config) == _all(OFF)


def test_a_missing_settings_document_configures_nothing(tmp_path: Path) -> None:
    assert _plan(_config(tmp_path, worker_settings_path=str(tmp_path / "absent.json"))) == _all(CAPTURED)


@pytest.mark.parametrize(
    ("env", "signal"),
    [
        ({"OTEL_METRICS_EXPORTER": "none"}, TelemetrySignal.METRICS),
        ({"OTEL_LOGS_EXPORTER": "otlp"}, TelemetrySignal.LOGS),
        ({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://t:4318/v1/traces"}, TelemetrySignal.TRACES),
        ({"OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://m:4318/v1/metrics"}, TelemetrySignal.METRICS),
        ({"OTEL_EXPORTER_OTLP_LOGS_HEADERS": "x-api-key=k"}, TelemetrySignal.LOGS),
        ({"OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "grpc"}, TelemetrySignal.TRACES),
    ],
)
def test_one_signal_is_yielded_through_the_worker_settings_path_env(
    tmp_path: Path, env: dict[str, str], signal: TelemetrySignal
) -> None:
    plan = _plan(_config(tmp_path, worker_settings_path=_settings(tmp_path, env)))
    assert plan.outcome(signal) is YIELDED
    assert [s for s in TelemetrySignal if plan.outcome(s) is CAPTURED] == [s for s in TelemetrySignal if s != signal]


def test_one_signal_is_yielded_through_the_published_bundles_settings_env(tmp_path: Path) -> None:
    source = tmp_path / "bundle"
    (source / "claude-code").mkdir(parents=True)
    (source / "claude-code" / "settings.json").write_text(json.dumps({"env": {"OTEL_METRICS_EXPORTER": "none"}}))
    snapshot = publish_harness_bundle(source, tmp_path)
    # The runner's own worker file names a destination too; the bundle's composed document supersedes it.
    config = _config(tmp_path, worker_settings_path=_settings(tmp_path, {"OTEL_LOGS_EXPORTER": "otlp"}))
    plan = _plan(config, bundle=snapshot)
    assert plan == HarnessTelemetryPlan(traces=CAPTURED, metrics=YIELDED, logs=CAPTURED)


def test_runner_init_erases_an_opt_out_edited_into_the_default_worker_settings(tmp_path: Path) -> None:
    """The default worker settings file is rewritten by every ``init``, so it is no durable home for an opt-out."""
    config = Runtime(tmp_path).init()
    path = tmp_path / WORKER_SETTINGS_FILENAME
    path.write_text(json.dumps({**json.loads(path.read_text()), "env": {"OTEL_METRICS_EXPORTER": "none"}}))
    assert _plan(config).metrics is YIELDED

    assert _plan(Runtime(tmp_path).init()) == _all(CAPTURED)


def test_a_generic_endpoint_yields_every_signal(tmp_path: Path) -> None:
    config = _config(tmp_path, worker_settings_path=_settings(tmp_path, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x"}))
    assert _plan(config) == _all(YIELDED)


@pytest.mark.parametrize("value", ["0", "false", "FALSE", ""])
def test_a_falsy_enable_telemetry_in_the_settings_env_yields_every_signal(tmp_path: Path, value: str) -> None:
    config = _config(tmp_path, worker_settings_path=_settings(tmp_path, {"CLAUDE_CODE_ENABLE_TELEMETRY": value}))
    assert _plan(config) == _all(YIELDED)


def test_a_truthy_enable_telemetry_in_the_settings_env_yields_nothing(tmp_path: Path) -> None:
    config = _config(tmp_path, worker_settings_path=_settings(tmp_path, {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}))
    assert _plan(config) == _all(CAPTURED)


def test_the_beta_tracing_pair_yields_traces_and_logs(tmp_path: Path) -> None:
    env = {"BETA_TRACING_ENDPOINT": "http://beta:4318", "ENABLE_BETA_TRACING_DETAILED": "1"}
    plan = _plan(_config(tmp_path, worker_settings_path=_settings(tmp_path, env)))
    assert plan == HarnessTelemetryPlan(traces=YIELDED, metrics=CAPTURED, logs=YIELDED)


def test_the_beta_endpoint_alone_yields_nothing(tmp_path: Path) -> None:
    config = _config(tmp_path, worker_settings_path=_settings(tmp_path, {"BETA_TRACING_ENDPOINT": "http://beta"}))
    assert _plan(config) == _all(CAPTURED)


def test_the_allowlisted_spawn_env_yields_a_signal_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The passthrough withholds OTEL_* while the daemon's own env enables tracing; the plan reads the spawn env as built.
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "console")
    plan = _plan(_config(tmp_path, worker_env_passthrough=("OTEL_LOGS_EXPORTER",)))
    assert plan.logs is YIELDED
    assert plan.traces is CAPTURED


def test_the_runners_own_receiver_variables_never_cause_a_yield(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    ):
        monkeypatch.setenv(name, "http://127.0.0.1:8431/v1/traces")
    config = _config(tmp_path, worker_env_passthrough=("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",))
    assert _plan(config) == _all(CAPTURED)


def test_a_traces_only_runner_endpoint_leaves_metrics_and_logs_without_a_destination(tmp_path: Path) -> None:
    environ = {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318/v1/traces"}
    plan = _plan(_config(tmp_path), runner_environ=environ)
    assert plan == HarnessTelemetryPlan(traces=CAPTURED, metrics=NO_DESTINATION, logs=NO_DESTINATION)


def test_an_operator_destination_wins_over_no_runner_destination(tmp_path: Path) -> None:
    environ = {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318/v1/traces"}
    config = _config(tmp_path, worker_settings_path=_settings(tmp_path, {"OTEL_METRICS_EXPORTER": "none"}))
    assert _plan(config, runner_environ=environ).metrics is YIELDED


def _preamble(*, lease_token: str = "tok", worker_programs: bool = False) -> WorkerPreamble:
    return WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        lease_token=lease_token,
        traceparent="00-0d338bce0f63eb7ab7992a965d508c04-74b20379223e78cb-01",
        worker_programs=worker_programs,
    )


class _NeverLaunches:
    def launch(self, *args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("an identity-env read launches nothing")


def _adapter(plan: HarnessTelemetryPlan | None) -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter(
        worker_env=AllowlistedEnv.of(()),
        process=FakeProbe(),
        launcher=_NeverLaunches(),  # type: ignore[arg-type]
        harness_telemetry=plan,
    )


def test_an_adapter_without_a_plan_sets_no_harness_telemetry_variable() -> None:
    env = _adapter(None).identity_env(_preamble(), "ch_1", "sess")
    assert not [n for n in env if n.startswith(("OTEL_", *_HARNESS_NAMES))]


class _RecordingLauncher:
    """Records the env of every launch; starts nothing."""

    def __init__(self) -> None:
        self.envs: list[dict[str, str]] = []

    def launch(
        self,
        argv: list[str],
        *,
        cwd: str | None,
        env: dict[str, str],
        stdout: IO[bytes] | int | None,
        stderr: IO[bytes] | int | None,
        defer_disarm: bool = False,
    ) -> LaunchedProcess:
        self.envs.append(dict(env))
        return LaunchedProcess(pid=4242, pgid=4242, process_start_time="1", confirm_durable=lambda: None)


def _launched_envs(
    plan: HarnessTelemetryPlan | None, tmp_path: Path, preamble: WorkerPreamble
) -> dict[str, dict[str, str]]:
    """The env each launch kind hands the launcher, keyed by kind."""
    launcher = _RecordingLauncher()
    adapter = ClaudeCodeAdapter(
        worker_env=AllowlistedEnv.of(()), process=FakeProbe(), launcher=launcher, harness_telemetry=plan
    )
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    kinds: dict[str, Callable[[], object]] = {
        "spawn": lambda: adapter.spawn(envelope, preamble, session_hint="sess"),
        "resume": lambda: adapter.spawn(envelope, preamble, session_hint=None, resume_from="sess"),
        "nudge": lambda: adapter.resume_with_message(
            str(tmp_path), "sess", "go on", preamble=preamble, chunk_id="ch_1"
        ),
        "judge": lambda: adapter.judge(
            str(tmp_path), "sess", "judge it", str(tmp_path / "j.out"), preamble=preamble, chunk_id="ch_1"
        ),
    }
    envs: dict[str, dict[str, str]] = {}
    for kind, run in kinds.items():
        run()
        envs[kind] = launcher.envs[-1]
    return envs


#: Exactly what capturing every signal adds to a launch env; no generic ``OTEL_EXPORTER_OTLP_*`` name among it.
_EVERY_SIGNAL_CAPTURED = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
    **{
        name: value
        for signal in TelemetrySignal
        for name, value in (
            (signal.exporter_variable, "otlp"),
            (signal.endpoint_variable, f"http://127.0.0.1:8431/v1/{signal.value}"),
            (signal.protocol_variable, "http/protobuf"),
            (signal.headers_variable, "X-Blizzard-Lease-Token=tok"),
        )
    },
}


@pytest.mark.parametrize("worker_programs", [False, True])
def test_a_plan_with_every_signal_off_launches_every_kind_byte_identical(tmp_path: Path, worker_programs: bool) -> None:
    preamble = _preamble(worker_programs=worker_programs)
    assert _launched_envs(_all(OFF), tmp_path, preamble) == _launched_envs(None, tmp_path, preamble)


def test_every_kind_launches_with_exactly_the_captured_signals_variables(tmp_path: Path) -> None:
    plain = _launched_envs(None, tmp_path, _preamble())
    captured = _launched_envs(_all(CAPTURED), tmp_path, _preamble())
    for kind, env in captured.items():
        assert {k: v for k, v in env.items() if plain[kind].get(k) != v} == _EVERY_SIGNAL_CAPTURED, kind
        assert set(plain[kind]) <= set(env), kind


def test_a_yielded_or_destinationless_signal_gets_no_variable() -> None:
    plan = HarnessTelemetryPlan(traces=YIELDED, metrics=CAPTURED, logs=NO_DESTINATION)
    env = _adapter(plan).identity_env(_preamble(), "ch_1", "sess")
    assert env["OTEL_METRICS_EXPORTER"] == "otlp"
    assert not [n for n in env if "TRACES" in n or "LOGS" in n]
    assert env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] == "1"


def test_no_captured_signal_sets_neither_switch() -> None:
    plan = HarnessTelemetryPlan(traces=YIELDED, metrics=YIELDED, logs=NO_DESTINATION)
    env = _adapter(plan).identity_env(_preamble(), "ch_1", "sess")
    assert not [n for n in env if n in _HARNESS_NAMES]


def test_no_kind_captures_without_a_lease_token(tmp_path: Path) -> None:
    preamble = _preamble(lease_token="")
    assert _launched_envs(_all(CAPTURED), tmp_path, preamble) == _launched_envs(None, tmp_path, preamble)


def test_worker_programs_and_capture_agree_on_the_traces_variables() -> None:
    env = _adapter(_all(CAPTURED)).identity_env(_preamble(worker_programs=True), "ch_1", "sess")
    assert env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == "http://127.0.0.1:8431/v1/traces"
    assert env["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] == "X-Blizzard-Lease-Token=tok"
