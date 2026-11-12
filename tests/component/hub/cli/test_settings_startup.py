"""The host seeds global settings only after checking the store revision."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.hub.cli import runtime
from blizzard.hub.composition import HubSettingsGraph
from blizzard.hub.config import HubConfig
from tests.support.config_inputs import hub_runtime_of, init_hub


def test_host_seeds_and_restarts_from_stored_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_hub(tmp_path)
    seen: list[bool] = []

    def serve(config: HubConfig, window: object, listening_file: Path | None, settings: HubSettingsGraph) -> None:
        seen.append(config.follow_latest)
        if len(seen) == 1:
            revision = settings.reader.snapshot().revision
            from blizzard.foundation.settings_record.vocabulary import Change

            settings.authoring.apply(
                Change("edit", sets={"follow_latest": True}), if_match=revision, door="api", actor="test"
            )

    monkeypatch.setattr(runtime, "_serve", serve)
    cli = CliRunner()
    result = cli.invoke(runtime.host, ["--dir", str(tmp_path)])
    assert result.exit_code == 0, repr(result.exception)
    path = tmp_path / "blizzard-hub.toml"
    path.write_text(path.read_text().replace("follow_latest = false", 'follow_latest = "invalid"'))
    result = cli.invoke(runtime.host, ["--dir", str(tmp_path)])
    assert result.exit_code == 0, repr(result.exception)
    assert seen == [False, True]


def test_host_refuses_behind_head_before_seeding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_hub(tmp_path)
    hub_runtime_of(tmp_path).migrate(down="20261111_1000_chunk_repository_targets")

    def unexpected_serve(*args: object) -> None:
        pytest.fail("host started")

    monkeypatch.setattr(runtime, "_serve", unexpected_serve)
    result = CliRunner().invoke(runtime.host, ["--dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "migrate" in result.output


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
    from blizzard.hub.app import PLATFORM_TRACING_SHUTDOWN_SECONDS, build_hosted_app
    from blizzard.hub.domain.runners.version_window import VersionWindow

    for name in ("BZ_FORGE_URL", "BZ_FORGE_OWNER", "BZ_FORGE_BASE_BRANCH", "BZ_FORGE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    init_hub(tmp_path)

    def serve(
        config: HubConfig, window: VersionWindow, listening_file: Path | None, settings: HubSettingsGraph
    ) -> None:
        with config.config_path.open("a") as f:
            f.write('\n[[work_source]]\nname="new"\nprovider="github"\nrepo="a/b"\ntoken_env="NEW_TOKEN"\n')
        monkeypatch.setenv("BZ_FORGE_URL", "changed-after-capture")
        app = build_hosted_app(config, version_window=window, settings=settings)
        app.state.hosted_cleanup.close()
        app.state.platform_tracing.shutdown(PLATFORM_TRACING_SHUTDOWN_SECONDS)

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
