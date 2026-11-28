"""Runner trace-export bindings resolve once per child over the shared host scope."""

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.clock import SystemClock, SystemMonotonicClock
from blizzard.foundation.registry.interface import Lifetime, ScopeClosed, port
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, open_child, supply
from blizzard.foundation.trace_spans import FinishedSpan
from blizzard.runner.composition import RunnerCompose
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import BuiltRunner, HostConfig
from blizzard.runner.host_root import open_host_infrastructure
from blizzard.runner.hub.client import HostReport
from blizzard.runner.hub.host_options import HostHubOptions
from blizzard.runner.loop.host_tick import HostTick, HostTickInForce
from blizzard.runner.tracing.host_options import HostTracingOptions
from blizzard.runner.tracing.runner_export import RunnerTraceExport, RunnerTraceOptions
from blizzard.runner.wiring import HostRuntimeOptions

_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}


class Transport:
    def __init__(self) -> None:
        self.closes = 0

    def export(self, spans: Sequence[FinishedSpan]) -> bool:
        return True

    def close(self) -> None:
        self.closes += 1


@pytest.fixture
def owner(tmp_path: Path) -> Iterator[OwnedChild]:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    opened = open_host_infrastructure(
        config,
        SystemClock(),
        SystemMonotonicClock(),
        HostRuntimeOptions({}),
        HostHubOptions("http://localhost", "", HostReport, lambda: ()),
        HostTracingOptions(config.tracing, {}, ()),
        tick_in_force=HostTickInForce(HostTick(), ()),
    )
    try:
        yield opened
    finally:
        opened.close()


def child(owner: OwnedChild, options: RunnerTraceOptions, name: str) -> OwnedChild:
    return open_child(
        owner,
        lifetime=Lifetime.RUNNER,
        owner=name,
        values=ScopeValues(
            (
                supply(port(BuiltRunner), cast(BuiltRunner, object())),
                supply(port(HostConfig), cast(HostConfig, object())),
                supply(port(RunnerCompose), RunnerCompose()),
                supply(port(RunnerTraceOptions), options),
            )
        ),
    )


def test_default_exporters_are_per_runner_and_finalized_once(
    owner: OwnedChild, monkeypatch: pytest.MonkeyPatch
) -> None:
    transports: list[Transport] = []

    def make(resource: object, *, scope: str, scope_version: str) -> Transport:
        transport = Transport()
        transports.append(transport)
        return transport

    monkeypatch.setattr("blizzard.runner.tracing.internal.runner_export.otlp_trace_exporter", make)
    one, two = (
        child(owner, RunnerTraceOptions(_ENDPOINT, None), "one"),
        child(owner, RunnerTraceOptions(_ENDPOINT, None), "two"),
    )
    first, second = one.resolver.get(RunnerTraceExport), two.resolver.get(RunnerTraceExport)
    assert first is one.resolver.get(RunnerTraceExport)
    assert first.exporter is not None and second.exporter is not None
    assert first.exporter is not second.exporter
    assert len(transports) == 2
    one.close()
    one.close()
    assert [transport.closes for transport in transports] == [1, 0]
    with pytest.raises(ScopeClosed):
        one.resolver.get(RunnerTraceExport)
    two.close()
    assert [transport.closes for transport in transports] == [1, 1]


def test_injected_exporter_shares_guard_but_remains_borrowed(owner: OwnedChild) -> None:
    transport = Transport()
    one = child(owner, RunnerTraceOptions(_ENDPOINT, transport), "one")
    two = child(owner, RunnerTraceOptions(_ENDPOINT, transport), "two")
    first, second = one.resolver.get(RunnerTraceExport), two.resolver.get(RunnerTraceExport)
    assert first is not second
    assert first.exporter is second.exporter
    one.close()
    two.close()
    assert transport.closes == 0


def test_disabled_export_does_not_acquire_or_close_transport(
    owner: OwnedChild, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(resource: object, *, scope: str, scope_version: str) -> Transport:
        raise AssertionError("disabled tracing acquired an exporter")

    monkeypatch.setattr("blizzard.runner.tracing.internal.runner_export.otlp_trace_exporter", refused)
    borrowed = Transport()
    runner = child(owner, RunnerTraceOptions({}, borrowed), "one")
    assert runner.resolver.get(RunnerTraceExport).exporter is None
    runner.close()
    assert borrowed.closes == 0
