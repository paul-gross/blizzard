"""Hosted clock selection stays in the hosted composition root."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import ControlledClock, IClock, SystemClock
from blizzard.hub.app import Sweep
from blizzard.hub.composition import build_flow_driver, build_hub_core
from blizzard.hub.delivery.flow_driver import StepWake
from blizzard.hub.domain.sweeps.names import SweepMode, SweepModes, SweepName
from blizzard.hub.domain.sweeps.roster import SweepRoster
from blizzard.hub.tenancy import StoreScope
from tests.support.config_inputs import build_hosted_hub_app, init_hub
from tests.support.hub_event_rows import hub_event_rows


@pytest.mark.parametrize(
    "clock,fence,expected",
    [
        (None, None, SystemClock),
        ("controlled", "1", ControlledClock),
        ("controlled", "true", SystemClock),
    ],
)
def test_hosted_clock_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: str | None,
    fence: str | None,
    expected: type,
) -> None:
    for name, value in (("BLIZZARD_CLOCK", clock), ("BLIZZARD_CLOCK_FENCE", fence)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    with capture_logs() as logs:
        app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    try:
        resolver = app.state.hub_process.resolver
        assert isinstance(resolver.get(IClock), expected)
        assert (app.state.flow_driver is None) == (expected is not SystemClock)
        assert resolver.get(StepWake)._wake is not None
        assert len([e for e in logs if "controlled clock asked for without its fence" in e.get("event", "")]) == (
            1 if clock == "controlled" and fence != "1" else 0
        )
        if expected is ControlledClock:
            rows = hub_event_rows(app.state.engine, "hub-started")
            assert len(rows) == 1
            assert rows[0].recorded_at.replace(tzinfo=resolver.get(IClock).now().tzinfo) == resolver.get(IClock).now()
            assert len(list(Sweep.all(app))) == sum(
                app.state.config.sweeps.of(name).value != "stepped" for name in resolver.get(SweepRoster).names()
            )
    finally:
        app.state.engine.dispose()


@pytest.mark.asyncio
async def test_controlled_graph_boot_announcements_and_forced_wake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BLIZZARD_CLOCK", "controlled")
    monkeypatch.setenv("BLIZZARD_CLOCK_FENCE", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "grpc")
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.delenv("BZ_EGRESS_PATH_KEY", raising=False)
    config = init_hub(tmp_path / "hub")
    config = replace(
        config,
        egress=replace(config.egress, directory=tmp_path / "egress", file_paths="hashed"),
        sweeps=SweepModes({SweepName.FORGE_STATUS: SweepMode.STEPPED}),
    )
    with (
        patch("blizzard.hub.app.build_hub_core", wraps=build_hub_core) as core_builder,
        patch("blizzard.hub.app.build_flow_driver", wraps=build_flow_driver) as driver_builder,
    ):
        app = build_hosted_hub_app(config)
    try:
        services = app.state.services
        resolver = app.state.hub_process.resolver
        # wraps returns the real object, not the mock's return_value; inspect the injected clock and tenant cache.
        clock = core_builder.call_args.kwargs["clock"]
        assert resolver.get(IClock) is clock
        tenant = next(iter(services.tenants.open_tenants.every()))
        assert services.tenants.open_services(StoreScope(tenant)).clock is clock
        assert app.state.flow_driver is None
        assert {sweep.config_name for sweep in Sweep.all(app)} == set(resolver.get(SweepRoster).names()) - {
            SweepName.FORGE_STATUS
        }
        assert driver_builder.call_count == 1
        for kind in ("hub-started", "trace-config-rejected", "egress-config-rejected"):
            [row] = hub_event_rows(app.state.engine, kind)
            assert row.recorded_at.replace(tzinfo=clock.now().tzinfo) == clock.now()
        # The retained wake forces a real second pass despite frozen time and an unchanged probe.
        driver = resolver.get(StepWake)._wake.__self__
        assert driver._clock is clock
        assert len((await driver.step()).reconciled) == 1
        assert len((await driver.step()).skipped) == 1
        resolver.get(StepWake).wake()
        assert len((await driver.step()).reconciled) == 1
    finally:
        app.state.engine.dispose()
