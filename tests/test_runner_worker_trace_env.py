"""The step's trace context in a worker's identity environment, and the ``OTEL_*`` passthrough drop (unit tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import AcquiredEnvironment, WorkerPreamble
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.internal.harness_shared import build_identity_env

pytestmark = pytest.mark.unit

_TRACEPARENT = "00-0d338bce0f63eb7ab7992a965d508c04-74b20379223e78cb-01"


def _preamble(traceparent: str) -> WorkerPreamble:
    return WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        traceparent=traceparent,
    )


def test_identity_env_carries_the_traceparent_under_both_names() -> None:
    env = build_identity_env(_preamble(_TRACEPARENT), "ch_1", "sess", AllowlistedEnv.of(()))
    assert env["BLIZZARD_TRACEPARENT"] == _TRACEPARENT
    assert env["TRACEPARENT"] == _TRACEPARENT


def test_identity_env_sets_neither_name_without_a_traceparent() -> None:
    env = build_identity_env(_preamble(""), "ch_1", "sess", AllowlistedEnv.of(()))
    assert "BLIZZARD_TRACEPARENT" not in env
    assert "TRACEPARENT" not in env


def test_no_exporter_variable_reaches_the_worker_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=secret")
    env = build_identity_env(_preamble(_TRACEPARENT), "ch_1", "sess", AllowlistedEnv.of(()))
    assert not [name for name in env if name.startswith("OTEL_")]


def _config(tmp_path: Path) -> RunnerConfig:
    return RunnerConfig(
        root=tmp_path,
        db_url="sqlite://",
        worker_env_passthrough=("MY_QUIRK", "OTEL_SERVICE_NAME", "OTEL_EXPORTER_OTLP_HEADERS"),
    )


def test_otel_names_are_dropped_from_the_passthrough_while_tracing_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    config = _config(tmp_path)
    assert config.dropped_otel_passthrough == ("OTEL_SERVICE_NAME", "OTEL_EXPORTER_OTLP_HEADERS")
    assert config.worker_env.passthrough == ("MY_QUIRK",)


def test_otel_names_pass_through_untouched_while_tracing_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    config = _config(tmp_path)
    assert config.dropped_otel_passthrough == ()
    assert config.worker_env.passthrough == config.worker_env_passthrough
