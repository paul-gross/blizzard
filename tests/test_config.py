"""Runtime config — the store URL is the single portability knob (D-095, ``bzh:sql-portable``).

Both daemons scaffold a sqlite default under the data dir and read any store URL
back verbatim: a postgres URL is accepted with no code branch on the backend, and
the winter service band ``BZ_*_PORT`` env overrides the bind port.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from blizzard.hub.config import ENV_PORT as HUB_ENV_PORT
from blizzard.hub.config import ConfigError as HubConfigError
from blizzard.hub.config import HubConfig, PmSourceConfig
from blizzard.runner.config import ENV_PORT as RUNNER_ENV_PORT
from blizzard.runner.config import RunnerConfig


@pytest.mark.unit
def test_hub_default_db_url_is_sqlite_under_data_dir(tmp_path: Path) -> None:
    url = HubConfig.default_db_url(tmp_path)
    assert url.startswith("sqlite:///")
    assert url.endswith("data/hub.db")


@pytest.mark.unit
def test_runner_default_db_url_is_sqlite_under_data_dir(tmp_path: Path) -> None:
    url = RunnerConfig.default_db_url(tmp_path)
    assert url.startswith("sqlite:///")
    assert url.endswith("data/runner.db")


@pytest.mark.unit
def test_postgres_url_round_trips_through_config(tmp_path: Path) -> None:
    # A postgres URL is accepted verbatim — portability is a config value, not a code branch.
    pg = "postgresql+psycopg://blizzard:secret@localhost:5432/hub"
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "{pg}"\nhost = "0.0.0.0"\nport = 9001\n')
    config = HubConfig.load(root)
    assert config.db_url == pg
    assert config.host == "0.0.0.0"
    assert config.port == 9001


@pytest.mark.unit
def test_service_band_port_env_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HUB_ENV_PORT, "4422")
    monkeypatch.setenv(RUNNER_ENV_PORT, "4423")
    assert HubConfig.scaffold(tmp_path).port == 4422
    assert RunnerConfig.scaffold(tmp_path).port == 4423


@pytest.mark.unit
def test_runner_loop_seams_scaffold_from_the_winter_injected_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The winter-service runner slot injects the loop seams so `blizzard runner init`
    # scaffolds a runnable config without hand-editing the toml.
    monkeypatch.setenv("BZ_WORKSPACE_ROOT", "/tmp/fixture/workspace")
    monkeypatch.setenv("BZ_WORKSPACE_ENVS", "e1, e2 ,e3")
    monkeypatch.setenv("BZ_HARNESS_BINARY", "/opt/mock-claude-code")
    monkeypatch.setenv("BZ_BASE_BRANCH", "main")
    config = RunnerConfig.scaffold(tmp_path)
    assert config.workspace_root == "/tmp/fixture/workspace"
    assert config.workspace_envs == ("e1", "e2", "e3")
    assert config.harness_binary == "/opt/mock-claude-code"
    assert config.base_branch == "main"


@pytest.mark.unit
def test_runner_loop_seams_fall_back_to_defaults_without_env(tmp_path: Path) -> None:
    config = RunnerConfig.scaffold(tmp_path)
    assert config.workspace_root == ""
    assert config.workspace_envs == ("e1",)
    assert config.harness_binary == "claude"


@pytest.mark.unit
def test_workspace_prompt_defaults_empty_and_round_trips_inline(tmp_path: Path) -> None:
    # Absent on a fresh scaffold — a table-only spawn (issue #17); a multi-line inline
    # prompt round-trips through to_toml (json-escaped basic string) intact.
    root = tmp_path / "runner"
    root.mkdir()
    scaffolded = RunnerConfig.scaffold(root)
    assert scaffolded.resolved_workspace_prompt() == ""

    edited = RunnerConfig(
        root=root,
        db_url=scaffolded.db_url,
        workspace_prompt="You are a fleet worker.\nWork in your held env.",
    )
    root_written = root / "blizzard-runner.toml"
    root_written.write_text(edited.to_toml())
    reloaded = RunnerConfig.load(root)
    assert reloaded.workspace_prompt == "You are a fleet worker.\nWork in your held env."
    assert reloaded.resolved_workspace_prompt() == "You are a fleet worker.\nWork in your held env."


@pytest.mark.unit
def test_workspace_prompt_file_wins_and_resolves_relative_to_root(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "prompt.md").write_text("# Fleet worker\nFrom a file.")
    config = RunnerConfig(
        root=root,
        db_url=RunnerConfig.default_db_url(root),
        workspace_prompt="inline-loses",
        workspace_prompt_file="prompt.md",
    )
    # The file wins over the inline value, and a relative path resolves under root.
    assert config.resolved_workspace_prompt() == "# Fleet worker\nFrom a file."


@pytest.mark.unit
def test_workspace_prompt_env_seeds_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_WORKSPACE_PROMPT", "seeded by the service")
    assert RunnerConfig.scaffold(tmp_path).workspace_prompt == "seeded by the service"


@pytest.mark.unit
def test_transcripts_root_defaults_empty_and_round_trips(tmp_path: Path) -> None:
    # Empty on a fresh scaffold — resolved to ~/.claude/projects at the composition
    # root (issue #29), never here; a configured value round-trips through to_toml.
    root = tmp_path / "runner"
    root.mkdir()
    assert RunnerConfig.scaffold(root).transcripts_root == ""

    edited = RunnerConfig(
        root=root, db_url=RunnerConfig.default_db_url(root), transcripts_root="/custom/claude/projects"
    )
    (root / "blizzard-runner.toml").write_text(edited.to_toml())
    reloaded = RunnerConfig.load(root)
    assert reloaded.transcripts_root == "/custom/claude/projects"


@pytest.mark.unit
def test_transcripts_root_env_seeds_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_TRANSCRIPTS_ROOT", "/seeded/claude/projects")
    assert RunnerConfig.scaffold(tmp_path).transcripts_root == "/seeded/claude/projects"


@pytest.mark.unit
def test_missing_workspace_prompt_file_raises(tmp_path: Path) -> None:
    from blizzard.runner.config import ConfigError

    root = tmp_path / "runner"
    root.mkdir()
    config = RunnerConfig(
        root=root,
        db_url=RunnerConfig.default_db_url(root),
        workspace_prompt_file="does-not-exist.md",
    )
    with pytest.raises(ConfigError):
        config.resolved_workspace_prompt()


# --------------------------------------------------------------------------- #
# `[[pm_source]]` (D-107/D-108) — the hub's configured PM work sources.
# --------------------------------------------------------------------------- #


def _hub_config(tmp_path: Path) -> HubConfig:
    root = tmp_path / "hub"
    root.mkdir()
    return HubConfig(root=root, db_url=HubConfig.default_db_url(root))


@pytest.mark.unit
def test_pm_sources_default_to_empty(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    assert config.pm_sources == ()


@pytest.mark.unit
def test_pm_sources_round_trip_through_to_toml_and_load(tmp_path: Path) -> None:
    # `HubConfig.load` -> `dataclasses.replace` -> `to_toml` -> `HubConfig.load` (the
    # idiom `tests/crash/support.py::write_runner_config` establishes for the runner).
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = HubConfig.load(config.root)

    sources = (
        PmSourceConfig(name="blizzard", provider="github", repo="paul-gross/blizzard", token_env="BZ_PM_TOKEN"),
        PmSourceConfig(
            name="internal",
            provider="github",
            repo="acme/internal-tool",
            token_env="BZ_INTERNAL_TOKEN",
            api_base="https://git.corp.internal/api/v3",
            web_base="https://git.corp.internal",
        ),
    )
    edited = dataclasses.replace(loaded, pm_sources=sources)
    edited.config_path.write_text(edited.to_toml())

    reloaded = HubConfig.load(edited.root)
    assert reloaded.pm_sources == sources


@pytest.mark.unit
def test_pm_source_missing_required_key_raises(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[pm_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\n'
    )
    with pytest.raises(HubConfigError, match="token_env"):
        HubConfig.load(root)


@pytest.mark.unit
def test_pm_source_duplicate_name_raises(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n'
        '\n[[pm_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T1"\n'
        '\n[[pm_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r2"\ntoken_env = "T2"\n'
    )
    with pytest.raises(HubConfigError, match="duplicate"):
        HubConfig.load(root)


@pytest.mark.unit
def test_pm_source_duplicate_provider_and_repo_raises(tmp_path: Path) -> None:
    # Two names for one (provider, repo) would let the same item be ingested twice
    # under two identities — this is what holds D-093 up (D-107).
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n'
        '\n[[pm_source]]\nname = "a"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T1"\n'
        '\n[[pm_source]]\nname = "b"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T2"\n'
    )
    with pytest.raises(HubConfigError, match="duplicate"):
        HubConfig.load(root)


@pytest.mark.unit
def test_pm_source_name_with_a_colon_raises(tmp_path: Path) -> None:
    # hub/cli.py's ingest token partitions on the first colon (D-107's open question).
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[pm_source]]\nname = "acme:blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T"\n'
    )
    with pytest.raises(HubConfigError, match=":"):
        HubConfig.load(root)


@pytest.mark.unit
def test_pm_source_unknown_provider_raises(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[pm_source]]\nname = "blizzard"\nprovider = "jira"\nrepo = "o/r"\ntoken_env = "T"\n'
    )
    with pytest.raises(HubConfigError, match="jira"):
        HubConfig.load(root)
