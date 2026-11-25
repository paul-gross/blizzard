"""The host seeds global settings only after checking the store revision."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from click.testing import CliRunner

from blizzard.foundation.clock import IClock
from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.hub.cli import runtime
from blizzard.hub.config import HubConfig
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.hub_settings_store import HubSettingsStore
from tests.support.config_inputs import hub_runtime_of, init_hub
from tests.support.scaffolding import TEST_HUB_ACTIONS


def test_host_seeds_and_restarts_from_stored_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_hub(tmp_path)
    seen: list[tuple[bool, float]] = []

    def serve(
        config: HubConfig,
        window: object,
        listening_file: Path | None,
        settings: HubSettingsGraph,
        clock: IClock,
        *resources: object,
    ) -> None:
        seen.append((config.follow_latest, config.action_pass_seconds))
        if len(seen) == 1:
            revision = settings.reader.snapshot().revision
            from blizzard.foundation.settings_record.vocabulary import Change

            settings.authoring.apply(
                Change("edit", sets={"action_pass_seconds": 45}), if_match=revision, door="api", actor="test"
            )

    monkeypatch.setattr(runtime, "_serve", serve)
    cli = CliRunner()
    result = cli.invoke(runtime.host, ["--dir", str(tmp_path)])
    assert result.exit_code == 0, repr(result.exception)
    path = tmp_path / "blizzard-hub.toml"
    path.write_text(
        path.read_text()
        .replace("follow_latest = false", "follow_latest = true")
        .replace("action_pass_seconds = 300", 'action_pass_seconds = "invalid"')
    )
    result = cli.invoke(runtime.host, ["--dir", str(tmp_path)])
    assert result.exit_code == 0, repr(result.exception)
    assert seen == [(False, 300), (True, 45)]


def test_host_refuses_behind_head_before_seeding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_hub(tmp_path)
    hub_runtime_of(tmp_path).migrate(down="20261111_1000_chunk_repository_targets")

    def unexpected_serve(*args: object) -> None:
        pytest.fail("host started")

    monkeypatch.setattr(runtime, "_serve", unexpected_serve)
    result = CliRunner().invoke(runtime.host, ["--dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "migrate" in result.output


def test_invalid_late_seed_can_be_corrected_before_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_hub(tmp_path)
    path = tmp_path / "blizzard-hub.toml"
    path.write_text(
        path.read_text()
        .replace("annotation_interval_seconds = 120", "annotation_interval_seconds = 45")
        .replace("action_pass_seconds = 300", "action_pass_seconds = -1")
    )
    seen: list[tuple[int, float]] = []

    def serve(
        config: HubConfig,
        window: object,
        listening_file: Path | None,
        settings: HubSettingsGraph,
        clock: IClock,
        *resources: object,
    ) -> None:
        seen.append((config.annotation_interval_seconds, config.action_pass_seconds))

    monkeypatch.setattr(runtime, "_serve", serve)
    cli = CliRunner()
    assert cli.invoke(runtime.host, ["--dir", str(tmp_path)]).exit_code != 0
    path.write_text(
        path.read_text()
        .replace("annotation_interval_seconds = 45", "annotation_interval_seconds = 60")
        .replace("action_pass_seconds = -1", "action_pass_seconds = 12")
    )
    result = cli.invoke(runtime.host, ["--dir", str(tmp_path)])
    assert result.exit_code == 0, repr(result.exception)
    assert seen == [(60, 12)]


def test_migrate_ignores_only_values_already_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_hub(tmp_path)

    def no_serve(*_args: object) -> None:
        pass

    monkeypatch.setattr(runtime, "_serve", no_serve)
    cli = CliRunner()
    result = cli.invoke(runtime.host, ["--dir", str(tmp_path)])
    assert result.exit_code == 0, repr(result.exception)
    path = tmp_path / "blizzard-hub.toml"
    path.write_text(path.read_text().replace("action_pass_seconds = 300", 'action_pass_seconds = "invalid"'))
    migrate = cli.invoke(runtime.migrate_cmd, ["--dir", str(tmp_path)])
    assert migrate.exit_code == 0, repr(migrate.exception)
    # An unstored live value is still validated by the offline operation.
    path.write_text('public_url = "not-an-origin"\n' + path.read_text())
    invalid = cli.invoke(runtime.migrate_cmd, ["--dir", str(tmp_path)])
    assert invalid.exit_code != 0
    assert "public_url" in invalid.output


def test_startup_legacy_guard_uses_captured_file_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blizzard.hub.domain.runners.version_window import VersionWindow
    from tests.support.config_inputs import build_hosted_hub_app as build_hosted_app

    for name in ("BZ_FORGE_URL", "BZ_FORGE_OWNER", "BZ_FORGE_BASE_BRANCH", "BZ_FORGE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    init_hub(tmp_path)

    def serve(
        config: HubConfig,
        window: VersionWindow,
        listening_file: Path | None,
        settings: HubSettingsGraph,
        clock: IClock,
        *resources: object,
    ) -> None:
        with config.config_path.open("a") as f:
            f.write('\n[[work_source]]\nname="new"\nprovider="github"\nrepo="a/b"\ntoken_env="NEW_TOKEN"\n')
        monkeypatch.setenv("BZ_FORGE_URL", "changed-after-capture")
        app = build_hosted_app(
            config,
            version_window=window,
            settings=settings,
            clock=clock,
            engine=resources[0],
            connections=resources[1],
            platform_tracing=resources[2],
        )
        app.state.hosted_cleanup.close()

    monkeypatch.setattr(runtime, "_serve", serve)
    result = CliRunner().invoke(runtime.host, ["--dir", str(tmp_path)])
    assert result.exit_code == 0, repr(result.exception)


def test_migrate_keeps_external_database_guard(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///{tmp_path / "outside.db"}"\n')
    result = CliRunner().invoke(runtime.migrate_cmd, ["--dir", str(root)])
    assert result.exit_code != 0
    assert "outside this directory" in result.output
    assert not (tmp_path / "outside.db").exists()


def test_legacy_stored_boot_value_does_not_override_file_or_migrate_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blizzard.foundation.settings_record.vocabulary import Change

    init_hub(tmp_path)
    seen: list[bool] = []

    def serve(
        config: HubConfig,
        window: object,
        listening_file: Path | None,
        settings: HubSettingsGraph,
        clock: IClock,
        *resources: object,
    ) -> None:
        seen.append(config.follow_latest)
        if len(seen) == 1:
            # Simulate a legacy stored boot value, bypassing current authoring validation.
            cast(HubSettingsStore, settings.store).apply(
                Change("edit", sets={"follow_latest": False}),
                if_match=settings.reader.snapshot().revision,
                door="api",
                actor="test",
            )

    monkeypatch.setattr(runtime, "_serve", serve)
    cli = CliRunner()
    first = cli.invoke(runtime.host, ["--dir", str(tmp_path)])
    assert first.exit_code == 0, repr(first.exception)
    path = tmp_path / "blizzard-hub.toml"
    path.write_text(path.read_text().replace("follow_latest = false", "follow_latest = true"))
    second = cli.invoke(runtime.host, ["--dir", str(tmp_path)])
    assert second.exit_code == 0, repr(second.exception)
    assert seen == [False, True]
    path.write_text(path.read_text().replace("follow_latest = true", 'follow_latest = "invalid"'))
    result = cli.invoke(runtime.migrate_cmd, ["--dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "follow_latest" in result.output


def test_settings_audit_uses_hosted_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    import sqlalchemy as sa

    from blizzard.foundation.clock import ControlledClock
    from blizzard.foundation.settings_record.vocabulary import Change
    from blizzard.hub.domain.runners.version_window import VersionWindow
    from blizzard.hub.store.schema import hub_setting_changes
    from tests.support.config_inputs import build_hosted_hub_app as build_hosted_app

    monkeypatch.setenv("BLIZZARD_CLOCK", "controlled")
    monkeypatch.setenv("BLIZZARD_CLOCK_FENCE", "1")
    init_hub(tmp_path)

    def serve(
        config: HubConfig,
        window: VersionWindow,
        listening_file: Path | None,
        settings: HubSettingsGraph,
        clock: IClock,
        *resources: object,
    ) -> None:
        app = build_hosted_app(
            config,
            version_window=window,
            settings=settings,
            clock=clock,
            engine=resources[0],
            connections=resources[1],
            platform_tracing=resources[2],
        )
        try:
            assert isinstance(clock, ControlledClock)
            assert app.state.hub_process.resolver.get(IClock) is clock
            clock.set(clock.now() + timedelta(days=1))
            settings.authoring.apply(
                Change("edit", sets={"action_pass_seconds": 45}),
                if_match=settings.reader.snapshot().revision,
                door="api",
                actor="test",
            )
            with app.state.engine.connect() as conn:
                recorded = conn.execute(
                    sa.select(hub_setting_changes.c.recorded_at).where(hub_setting_changes.c.actor == "test")
                ).scalar_one()
            assert recorded.replace(tzinfo=clock.now().tzinfo) == clock.now()
        finally:
            app.state.hosted_cleanup.close()

    monkeypatch.setattr(runtime, "_serve", serve)
    result = CliRunner().invoke(runtime.host, ["--dir", str(tmp_path)])
    assert result.exit_code == 0, repr(result.exception)


def test_host_instruments_shared_settings_before_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from blizzard.foundation.platform_tracing.handle import build_platform_tracing
    from blizzard.foundation.settings_record.vocabulary import Change
    from blizzard.hub import composition

    init_hub(tmp_path)
    path = tmp_path / "blizzard-hub.toml"
    path.write_text(
        path.read_text()
        .replace("# platform = false", "platform = true")
        .replace("# platform_sample_ratio = 0.01", "platform_sample_ratio = 1.0")
        .replace("action_pass_seconds = 300", "action_pass_seconds = 44")
    )
    exporter = InMemorySpanExporter()
    acquisitions: list[object] = []
    original_open = runtime.open_hub_store

    def tracing(
        config: TracingConfig,
        environ: Mapping[str, str],
        *,
        resource: Mapping[str, str],
        scope: str,
        scope_version: str,
    ) -> IPlatformTracing:
        return build_platform_tracing(
            config,
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"},
            resource=resource,
            scope=scope,
            scope_version=scope_version,
            exporter=exporter,
        )

    def acquire(db_url: str, platform_tracing: IPlatformTracing) -> tuple[object, HubStoreConnections]:
        result = original_open(db_url, platform_tracing)
        acquisitions.append(result)
        return result

    def serve(
        config: HubConfig,
        window: object,
        listening_file: object,
        settings: HubSettingsGraph,
        clock: IClock,
        engine: object,
        connections: HubStoreConnections,
        handle: IPlatformTracing,
    ) -> None:
        assert acquisitions == [(engine, connections)]
        with handle.tracer.root("startup seed complete"):
            pass
        core = composition.build_hub_core(
            connections, clock=clock, config=config, platform_tracing=handle, environ={}, actions=TEST_HUB_ACTIONS
        )
        assert core.store_connections is connections
        snapshot = settings.reader.snapshot()
        settings.authoring.apply(
            Change("edit", sets={"action_pass_seconds": 45}), if_match=snapshot.revision, door="api", actor="test"
        )
        settings.reader.snapshot()

    monkeypatch.setattr(runtime, "build_platform_tracing", tracing)
    monkeypatch.setattr(runtime, "open_hub_store", acquire)
    monkeypatch.setattr(runtime, "_serve", serve)
    result = CliRunner().invoke(runtime.host, ["--dir", str(tmp_path)])
    assert result.exit_code == 0, repr(result.exception)
    spans = exporter.get_finished_spans()
    sql = [str((span.attributes or {}).get("db.query.text", "")) for span in spans]
    marker = next(span for span in spans if span.name == "startup seed complete")
    assert any(
        "hub_setting_entries" in str((span.attributes or {}).get("db.query.text", "")).lower()
        and "insert" in str((span.attributes or {}).get("db.query.text", "")).lower()
        and span.start_time is not None
        and marker.start_time is not None
        and span.start_time < marker.start_time
        for span in spans
    )
    assert any("hub_settings" in statement.lower() and "update" in statement.lower() for statement in sql)
    assert any("settings" in statement.lower() and "select" in statement.lower() for statement in sql)
