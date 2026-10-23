"""The runner's ``[tracing]`` keys: the three only it acts on round-trip through ``blizzard-host.toml`` and are
refused when malformed, and a host's scaffold renders them beside the shared knobs."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from blizzard.foundation.trace_export.config import TracingConfig, toml_literal
from blizzard.runner import config as runner_config
from blizzard.runner.host_config import HostConfig, HostConfigError
from blizzard.runner.tracing.config import RESERVED_SERVICE_NAMES, RunnerTracingConfig
from tests.config_inputs import load_host_config

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("clean_seeds")]


@pytest.fixture
def clean_seeds(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in vars(runner_config):
        if name.startswith("ENV_") and isinstance(value := getattr(runner_config, name), str):
            monkeypatch.delenv(value, raising=False)
    monkeypatch.delenv("BZ_HARNESS_BINARY", raising=False)


def _write(root: Path, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "blizzard-host.toml").write_text(text)
    return root


def _with_tracing(root: Path, tracing: RunnerTracingConfig) -> str:
    return dataclasses.replace(HostConfig.scaffold(root), tracing=tracing).to_toml()


def test_the_scaffold_renders_every_tracing_knob_commented_at_its_default(tmp_path: Path) -> None:
    root = tmp_path / "host"
    text = HostConfig.scaffold(root).to_toml()

    for key, value in dataclasses.asdict(RunnerTracingConfig()).items():
        if key != "worker_program_services":
            assert f"# {key} = {toml_literal(value)}\n" in text
    assert "# [tracing.worker_program_services]\n" in text
    assert load_host_config(_write(root, text)).tracing == RunnerTracingConfig()


def test_harness_telemetry_round_trips_and_refuses_a_non_boolean(tmp_path: Path) -> None:
    root = tmp_path / "host"
    text = _with_tracing(root, RunnerTracingConfig(harness_telemetry=True, worker_programs=True))
    assert "\nharness_telemetry = true\n" in text
    assert "\nworker_programs = true\n" in text

    assert load_host_config(_write(root, text)).tracing == RunnerTracingConfig(
        harness_telemetry=True, worker_programs=True
    )
    bad = HostConfig.scaffold(root).to_toml().replace("[tracing]\n", '[tracing]\nharness_telemetry = "yes"\n', 1)
    with pytest.raises(HostConfigError, match=re.escape("tracing.harness_telemetry must be true or false")):
        load_host_config(_write(root, bad))


def test_worker_program_services_round_trip_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "host"
    tracing = RunnerTracingConfig(worker_program_services={"winter_cli": "winter-blizzard", "a.b": "svc"})

    text = _with_tracing(root, tracing)

    assert load_host_config(_write(root, text)).tracing == tracing


@pytest.mark.parametrize(
    ("entry", "complaint"),
    [
        ('"" = "svc"', "empty scope"),
        ('"blizzard.cli" = "svc"', "tracing.worker_program_services.blizzard.cli"),
        ('winter_cli = ""', "tracing.worker_program_services.winter_cli"),
        ("winter_cli = 3", "tracing.worker_program_services.winter_cli"),
        ('winter_cli = "blizzard-hub"', "'blizzard-hub'"),
        ('winter_cli = "blizzard-runner"', "'blizzard-runner'"),
        ('winter_cli = "blizzard-cli"', "'blizzard-cli'"),
    ],
)
def test_worker_program_services_refuses_a_bad_entry(tmp_path: Path, entry: str, complaint: str) -> None:
    root = tmp_path / "host"
    text = HostConfig.scaffold(root).to_toml() + f"\n[tracing.worker_program_services]\n{entry}\n"

    with pytest.raises(HostConfigError, match=re.escape(complaint)):
        load_host_config(_write(root, text))


def test_worker_program_services_refuses_a_non_table(tmp_path: Path) -> None:
    root = tmp_path / "host"
    text = HostConfig.scaffold(root).to_toml().replace("[tracing]\n", '[tracing]\nworker_program_services = "x"\n', 1)

    with pytest.raises(HostConfigError, match="must be a table"):
        load_host_config(_write(root, text))


def test_reserved_service_names_cover_each_daemons_own_name() -> None:
    from blizzard.hub.domain.observability.tracing.attributes import CHUNK_SERVICE_NAME as CHUNK
    from blizzard.hub.domain.observability.tracing.attributes import DEFAULT_SERVICE_NAME as HUB
    from blizzard.runner.tracing.attributes import DEFAULT_SERVICE_NAME as RUNNER

    assert {HUB, RUNNER, CHUNK} <= RESERVED_SERVICE_NAMES


def test_the_runner_config_is_the_shared_one_plus_its_own_keys() -> None:
    assert issubclass(RunnerTracingConfig, TracingConfig)
    assert {f.name for f in dataclasses.fields(RunnerTracingConfig)} - {
        f.name for f in dataclasses.fields(TracingConfig)
    } == {
        "worker_programs",
        "worker_program_services",
        "harness_telemetry",
    }
