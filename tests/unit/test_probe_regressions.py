import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from blizzard.runner.harness.opencode.permissions.permission_resolver import OpenCodeEffectivePermissions
from blizzard.runner.node_steps.envelope import Envelope
from blizzard.runner.process.child_env import ChildEnv

compaction = importlib.import_module("scripts.probe_opencode_compaction_window")
bundle = importlib.import_module("scripts.probe_opencode_operator_bundle")
headless = importlib.import_module("scripts.probe_headless_permission_refusal")
telemetry = importlib.import_module("scripts.probe_claude_code_telemetry_precedence")


@pytest.mark.parametrize("name", ["fresh", "resume", "judge", "nudge"])
def test_bundle_run_confirms_each_launch_and_mints_only_fresh_identity(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = MagicMock()
    handle = MagicMock(pid=123)
    handle.await_identity.return_value.session_id = "session"
    adapter.spawn.return_value = adapter.judge.return_value = adapter.resume_with_message.return_value = handle

    def wait(pid: int) -> None:
        assert pid == 123

    def events(path: Path) -> tuple[list[str], list[dict[str, str]]]:
        assert path == tmp_path / f"{name}.jsonl"
        return ["read"], [{"sessionID": "session"}]

    monkeypatch.setattr(bundle, "_wait", wait)
    monkeypatch.setattr(bundle, "_events", events)
    session = None if name == "fresh" else "session"
    assert bundle._run(adapter, tmp_path, tmp_path, name, "prompt", session, "stub/model") == ("session", ["read"])
    handle.confirm_durable.assert_called_once()
    assert handle.await_identity.call_count == (1 if name == "fresh" else 0)
    if name in {"fresh", "resume"}:
        assert isinstance(adapter.spawn.call_args.args[0], Envelope)
        assert adapter.spawn.call_args.kwargs["resume_from"] == session
    else:
        call = adapter.judge if name == "judge" else adapter.resume_with_message
        assert call.call_args.args[1] == session
        assert call.call_args.kwargs["compaction_window"] == "8k"


def test_headless_spawn_uses_current_envelope_and_confirms_before_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = MagicMock(spec=headless.OpenCodeAdapter)
    handle = adapter.spawn.return_value
    handle.pid = 42

    def wait(pid: int) -> str:
        handle.confirm_durable.assert_called_once()
        assert pid == 42
        return "exit 0"

    monkeypatch.setattr(headless, "_wait", wait)
    assert headless._spawn(adapter, tmp_path, tmp_path / "out", tmp_path / "err", "stub/model", "prompt") == "exit 0"
    envelope, preamble, session = adapter.spawn.call_args.args
    assert isinstance(envelope, Envelope)
    assert envelope.prompt == "prompt"
    assert preamble.environments[0].workdir == str(tmp_path)
    assert session is None


def test_bundle_events_ignore_incomplete_tools_and_reject_errors(tmp_path: Path) -> None:
    path = tmp_path / "events"
    path.write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"type": "tool_use", "part": {"tool": "read", "state": {"status": "completed"}}},
                {"type": "tool_use", "part": {"tool": "write", "state": {"status": "pending"}}},
                {"type": "text"},
            ]
        )
    )
    assert bundle._events(path)[0] == ["read"]
    path.write_text('{"type":"error"}\n')
    with pytest.raises(AssertionError):
        bundle._events(path)


@pytest.mark.parametrize("signal", ["traces", "logs", "metrics"])
def test_telemetry_sanitizes_resource_and_signal_attributes(signal: str) -> None:
    trace = ExportTraceServiceRequest()
    logs = ExportLogsServiceRequest()
    metrics = ExportMetricsServiceRequest()
    if signal == "traces":
        resource_trace = trace.resource_spans.add()
        resource_trace.resource.attributes.add(key="host.name").value.string_value = "private"
        attrs = resource_trace.scope_spans.add().spans.add().attributes
    elif signal == "logs":
        resource_logs = logs.resource_logs.add()
        resource_logs.resource.attributes.add(key="host.name").value.string_value = "private"
        attrs = resource_logs.scope_logs.add().log_records.add().attributes
    else:
        resource_metrics = metrics.resource_metrics.add()
        resource_metrics.resource.attributes.add(key="host.name").value.string_value = "private"
        attrs = resource_metrics.scope_metrics.add().metrics.add().gauge.data_points.add().attributes
    attrs.add(key="session.id").value.string_value = "secret"
    attrs.add(key="safe").value.string_value = "kept"
    request = {"traces": trace, "logs": logs, "metrics": metrics}[signal]
    sink = telemetry._Sink(SimpleNamespace(server_port=1))
    sink.bodies[signal].append(request.SerializeToString())
    sanitized = type(request)()
    sanitized.ParseFromString(telemetry._sanitized_export(sink, signal))
    assert b"private" not in sanitized.SerializeToString()
    assert b"secret" not in sanitized.SerializeToString()
    assert b"kept" in sanitized.SerializeToString()


@pytest.mark.parametrize("kind", ["gauge", "sum", "histogram", "exponential_histogram", "summary"])
def test_telemetry_supports_every_metric_point_kind(kind: str) -> None:
    request = ExportMetricsServiceRequest()
    metric = request.resource_metrics.add().scope_metrics.add().metrics.add()
    point = getattr(metric, kind).data_points.add()
    point.attributes.add(key="user.id").value.string_value = "secret"
    sink = telemetry._Sink(SimpleNamespace(server_port=1))
    sink.bodies["metrics"].append(request.SerializeToString())
    assert b"secret" not in telemetry._sanitized_export(sink, "metrics")
    assert b"sanitized" in telemetry._sanitized_export(sink, "metrics")


def test_compaction_requires_scratch_and_absolute_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLIZZARD_TMPDIR", str(tmp_path))
    monkeypatch.setattr(
        "sys.argv", ["probe", "--binary", "relative", "--window", "8k", "--evidence-dir", str(tmp_path / "evidence")]
    )
    with pytest.raises(ValueError, match="absolute"):
        compaction.main()
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("window", ["8k", "auto", None])
def test_compaction_cell_composes_evidence_and_real_launch(
    window: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver_envs: list[dict[str, str]] = []
    launches: list[dict[str, str]] = []

    class StopLaunch(Exception):
        pass

    def resolve(self: object, *, cwd: str, env: ChildEnv) -> OpenCodeEffectivePermissions:
        variables = env.variables
        assert cwd == str(tmp_path / (window or "none") / "project")
        resolver_envs.append(variables)
        return OpenCodeEffectivePermissions(
            merged_config=json.loads(variables["OPENCODE_CONFIG_CONTENT"]), agent_rulesets={}
        )

    def launch(self: object, cmd: object, *, cwd: str, env: ChildEnv, **kwargs: object) -> None:
        launches.append(env.variables)
        raise StopLaunch

    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, "original")
    monkeypatch.setenv("SECRET_FOR_TEST", "hidden")
    monkeypatch.setattr(compaction.SubprocessOpenCodePermissionResolver, "resolve", resolve)
    monkeypatch.setattr(compaction.ProcessLauncher, "launch", launch)
    with ThreadPoolExecutor(max_workers=1) as executor, pytest.raises(StopLaunch):
        compaction._cell(tmp_path, "/unused/opencode", window, executor)

    cell = tmp_path / (window or "none")
    inline = json.loads((cell / "inline-config.json").read_text())
    assert json.loads((cell / "effective-config.json").read_text()) == inline
    assert json.loads(launches[0]["OPENCODE_CONFIG_CONTENT"]) == inline
    limits = inline["provider"]["stub"]["models"]["stub"]["limit"]
    assert limits == (
        {"context": 32000, "output": 1000, "input": 9000} if window == "8k" else {"context": 32000, "output": 1000}
    )
    assert all(env["HOME"] == str(cell / "home") and "SECRET_FOR_TEST" not in env for env in resolver_envs)
    assert all(not any(key.startswith("BLIZZARD_") for key in env) for env in resolver_envs)
    assert launches[0]["OPENCODE_CONFIG"] == str(cell / "bundle.json")
