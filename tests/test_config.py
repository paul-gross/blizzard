"""Runtime config — the store URL is the single portability knob (``bzh:sql-portable``).

Both daemons scaffold a sqlite default under the data dir and read any store URL
back verbatim: a postgres URL is accepted with no code branch on the backend, and
the winter service band ``BZ_*_PORT`` env overrides the bind port.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from blizzard.hub.config import ENV_DB_URL as HUB_ENV_DB_URL
from blizzard.hub.config import ENV_HOST as HUB_ENV_HOST
from blizzard.hub.config import ENV_PORT as HUB_ENV_PORT
from blizzard.hub.config import PRODUCES_ENFORCE, HubConfig, WorkSourceConfig
from blizzard.hub.config import ConfigError as HubConfigError
from blizzard.runner.config import DEFAULT_RUNNER_CEILING_WINDOW_HOURS, RunnerConfig
from blizzard.runner.config import ENV_PORT as RUNNER_ENV_PORT


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
def test_runner_prompt_defaults_empty_and_round_trips_inline(tmp_path: Path) -> None:
    # Absent on a fresh scaffold — the baked DEFAULT_BLIZZARD_PREAMBLE is used instead
    # (issue #103); a multi-line inline prompt round-trips through to_toml intact.
    root = tmp_path / "runner"
    root.mkdir()
    scaffolded = RunnerConfig.scaffold(root)
    assert scaffolded.resolved_runner_prompt() == ""

    edited = RunnerConfig(
        root=root,
        db_url=scaffolded.db_url,
        runner_prompt="You are a blizzard fleet worker.\nUse the CLI.",
    )
    root_written = root / "blizzard-runner.toml"
    root_written.write_text(edited.to_toml())
    reloaded = RunnerConfig.load(root)
    assert reloaded.runner_prompt == "You are a blizzard fleet worker.\nUse the CLI."
    assert reloaded.resolved_runner_prompt() == "You are a blizzard fleet worker.\nUse the CLI."


@pytest.mark.unit
def test_runner_prompt_file_wins_and_resolves_relative_to_root(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "runner-prompt.md").write_text("# Blizzard preamble\nFrom a file.")
    config = RunnerConfig(
        root=root,
        db_url=RunnerConfig.default_db_url(root),
        runner_prompt="inline-loses",
        runner_prompt_file="runner-prompt.md",
    )
    # The file wins over the inline value, and a relative path resolves under root.
    assert config.resolved_runner_prompt() == "# Blizzard preamble\nFrom a file."


@pytest.mark.unit
def test_runner_prompt_env_seeds_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_RUNNER_PROMPT", "seeded by the service")
    assert RunnerConfig.scaffold(tmp_path).runner_prompt == "seeded by the service"


@pytest.mark.unit
def test_missing_runner_prompt_file_raises(tmp_path: Path) -> None:
    from blizzard.runner.config import ConfigError

    root = tmp_path / "runner"
    root.mkdir()
    config = RunnerConfig(
        root=root,
        db_url=RunnerConfig.default_db_url(root),
        runner_prompt_file="does-not-exist.md",
    )
    with pytest.raises(ConfigError):
        config.resolved_runner_prompt()


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
def test_chunk_cap_usd_defaults_absent(tmp_path: Path) -> None:
    # No `[cost]` table at all on a fresh scaffold — absent means no cap (issue #61a).
    assert RunnerConfig.scaffold(tmp_path).chunk_cap_usd is None


@pytest.mark.unit
def test_chunk_cap_usd_absent_when_cost_table_omits_the_key(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n[cost]\n')
    assert RunnerConfig.load(root).chunk_cap_usd is None


@pytest.mark.unit
def test_chunk_cap_usd_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    edited = RunnerConfig(root=root, db_url=RunnerConfig.default_db_url(root), chunk_cap_usd=12.5)
    (root / "blizzard-runner.toml").write_text(edited.to_toml())
    reloaded = RunnerConfig.load(root)
    assert reloaded.chunk_cap_usd == 12.5


@pytest.mark.unit
def test_chunk_cap_usd_parses_from_a_hand_written_cost_table(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(
        f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n[cost]\nchunk_cap_usd = 3\n'
    )
    config = RunnerConfig.load(root)
    assert config.chunk_cap_usd == 3.0


@pytest.mark.unit
def test_runner_ceiling_usd_defaults_absent(tmp_path: Path) -> None:
    # No `[cost]` table at all on a fresh scaffold — absent means no ceiling (issue #61b).
    config = RunnerConfig.scaffold(tmp_path)
    assert config.runner_ceiling_usd is None
    assert config.runner_ceiling_window_hours == DEFAULT_RUNNER_CEILING_WINDOW_HOURS


@pytest.mark.unit
def test_runner_ceiling_usd_absent_when_cost_table_omits_the_key(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n[cost]\n')
    assert RunnerConfig.load(root).runner_ceiling_usd is None


@pytest.mark.unit
def test_runner_ceiling_usd_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    edited = RunnerConfig(
        root=root,
        db_url=RunnerConfig.default_db_url(root),
        runner_ceiling_usd=50.0,
        runner_ceiling_window_hours=6.0,
    )
    (root / "blizzard-runner.toml").write_text(edited.to_toml())
    reloaded = RunnerConfig.load(root)
    assert reloaded.runner_ceiling_usd == 50.0
    assert reloaded.runner_ceiling_window_hours == 6.0


@pytest.mark.unit
def test_runner_ceiling_usd_parses_from_a_hand_written_cost_table(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(
        f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n[cost]\nrunner_ceiling_usd = 20\nwindow_hours = 12\n'
    )
    config = RunnerConfig.load(root)
    assert config.runner_ceiling_usd == 20.0
    assert config.runner_ceiling_window_hours == 12.0


@pytest.mark.unit
def test_runner_ceiling_window_hours_defaults_when_ceiling_set_but_window_omitted(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(
        f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n[cost]\nrunner_ceiling_usd = 20\n'
    )
    config = RunnerConfig.load(root)
    assert config.runner_ceiling_usd == 20.0
    assert config.runner_ceiling_window_hours == DEFAULT_RUNNER_CEILING_WINDOW_HOURS


@pytest.mark.unit
def test_worker_env_passthrough_defaults_absent(tmp_path: Path) -> None:
    # No `[worker]` table at all on a fresh scaffold — absent means no operator
    # extension to the spawn-environment allowlist (issue #88).
    assert RunnerConfig.scaffold(tmp_path).worker_env_passthrough == ()


@pytest.mark.unit
def test_worker_env_passthrough_absent_when_worker_table_omits_the_key(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n[worker]\n')
    assert RunnerConfig.load(root).worker_env_passthrough == ()


@pytest.mark.unit
def test_worker_env_passthrough_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    edited = RunnerConfig(
        root=root,
        db_url=RunnerConfig.default_db_url(root),
        worker_env_passthrough=("MY_HARNESS_QUIRK", "ANOTHER_VAR"),
    )
    (root / "blizzard-runner.toml").write_text(edited.to_toml())
    reloaded = RunnerConfig.load(root)
    assert reloaded.worker_env_passthrough == ("MY_HARNESS_QUIRK", "ANOTHER_VAR")


@pytest.mark.unit
def test_worker_env_passthrough_parses_from_a_hand_written_worker_table(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(
        f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n[worker]\nenv_passthrough = ["MY_HARNESS_QUIRK"]\n'
    )
    config = RunnerConfig.load(root)
    assert config.worker_env_passthrough == ("MY_HARNESS_QUIRK",)


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
# `[[work_source]]` — the hub's configured work sources.
# --------------------------------------------------------------------------- #


def _hub_config(tmp_path: Path) -> HubConfig:
    root = tmp_path / "hub"
    root.mkdir()
    return HubConfig(root=root, db_url=HubConfig.default_db_url(root))


@pytest.mark.unit
def test_work_sources_default_to_empty(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    assert config.work_sources == ()


@pytest.mark.unit
def test_work_sources_round_trip_through_to_toml_and_load(tmp_path: Path) -> None:
    # `HubConfig.load` -> `dataclasses.replace` -> `to_toml` -> `HubConfig.load` (the
    # idiom `tests/crash/support.py::write_runner_config` establishes for the runner).
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = HubConfig.load(config.root)

    sources = (
        WorkSourceConfig(
            name="blizzard", provider="github", repo="paul-gross/blizzard", token_env="BZ_WORK_SOURCE_TOKEN"
        ),
        WorkSourceConfig(
            name="internal",
            provider="github",
            repo="acme/internal-tool",
            token_env="BZ_INTERNAL_TOKEN",
            api_base="https://git.corp.internal/api/v3",
            web_base="https://git.corp.internal",
        ),
    )
    edited = dataclasses.replace(loaded, work_sources=sources)
    edited.config_path.write_text(edited.to_toml())

    reloaded = HubConfig.load(edited.root)
    assert reloaded.work_sources == sources


@pytest.mark.unit
def test_work_source_missing_required_key_raises(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\n'
    )
    with pytest.raises(HubConfigError, match="token_env"):
        HubConfig.load(root)


@pytest.mark.unit
def test_work_source_duplicate_name_raises(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n'
        '\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T1"\n'
        '\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r2"\ntoken_env = "T2"\n'
    )
    with pytest.raises(HubConfigError, match="duplicate"):
        HubConfig.load(root)


@pytest.mark.unit
def test_work_source_duplicate_provider_and_repo_raises(tmp_path: Path) -> None:
    # Two names for one (provider, repo) would let the same item be ingested twice
    # under two identities — this is what holds pointer identity uniqueness up.
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n'
        '\n[[work_source]]\nname = "a"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T1"\n'
        '\n[[work_source]]\nname = "b"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T2"\n'
    )
    with pytest.raises(HubConfigError, match="duplicate"):
        HubConfig.load(root)


@pytest.mark.unit
def test_work_source_name_with_a_colon_raises(tmp_path: Path) -> None:
    # hub/cli.py's ingest token partitions on the first colon.
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[work_source]]\nname = "acme:blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T"\n'
    )
    with pytest.raises(HubConfigError, match=":"):
        HubConfig.load(root)


@pytest.mark.unit
def test_work_source_unknown_provider_raises(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[work_source]]\nname = "blizzard"\nprovider = "jira"\nrepo = "o/r"\ntoken_env = "T"\n'
    )
    with pytest.raises(HubConfigError, match="jira"):
        HubConfig.load(root)


@pytest.mark.unit
def test_a_leftover_pm_source_block_fails_the_load_naming_the_new_key(tmp_path: Path) -> None:
    """Issue #55's deliberate no-alias: `[[pm_source]]` was the pre-rename key, and a hub
    whose config still carries it would otherwise parse as zero configured sources — a
    hub that looks configured, 503s every work-item read, and nulls every board label.
    Config is operator-owned and skew-free, so this fails fast and names the new key."""
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[pm_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T"\n'
    )
    with pytest.raises(HubConfigError, match=r"\[\[work_source\]\]"):
        HubConfig.load(root)


@pytest.mark.unit
def test_a_leftover_pm_source_block_fails_even_beside_a_valid_work_source(tmp_path: Path) -> None:
    """The check is not "did we end up with any sources" — a config carrying both keys
    would silently drop the old block's source, which is exactly the quiet
    half-configuration the fail-fast exists to prevent."""
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n'
        '\n[[work_source]]\nname = "a"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T1"\n'
        '\n[[pm_source]]\nname = "b"\nprovider = "github"\nrepo = "o/r2"\ntoken_env = "T2"\n'
    )
    with pytest.raises(HubConfigError, match=r"\[\[work_source\]\]"):
        HubConfig.load(root)


# --------------------------------------------------------------------------- #
# `runner_auth_mode` — the runner-authentication rollout brake (issue #86a).
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_runner_auth_mode_defaults_to_warn(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    assert config.runner_auth_mode == "warn"


@pytest.mark.unit
def test_runner_auth_mode_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = HubConfig.load(config.root)
    assert loaded.runner_auth_mode == "warn"

    edited = dataclasses.replace(loaded, runner_auth_mode="enforce")
    edited.config_path.write_text(edited.to_toml())
    reloaded = HubConfig.load(edited.root)
    assert reloaded.runner_auth_mode == "enforce"


@pytest.mark.unit
def test_runner_auth_mode_absent_from_toml_defaults_to_warn(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\n')
    assert HubConfig.load(root).runner_auth_mode == "warn"


@pytest.mark.unit
def test_runner_auth_mode_unknown_value_raises(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nrunner_auth_mode = "block"\n')
    with pytest.raises(HubConfigError, match="runner_auth_mode"):
        HubConfig.load(root)


# --------------------------------------------------------------------------- #
# `route_token_mode` — the route-capability-token rollout brake (issue #84b), a
# separate flag from `runner_auth_mode` above.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_route_token_mode_defaults_to_warn(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    assert config.route_token_mode == "warn"


@pytest.mark.unit
def test_route_token_mode_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = HubConfig.load(config.root)
    assert loaded.route_token_mode == "warn"

    edited = dataclasses.replace(loaded, route_token_mode="enforce")
    edited.config_path.write_text(edited.to_toml())
    reloaded = HubConfig.load(edited.root)
    assert reloaded.route_token_mode == "enforce"


@pytest.mark.unit
def test_route_token_mode_absent_from_toml_defaults_to_warn(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\n')
    assert HubConfig.load(root).route_token_mode == "warn"


@pytest.mark.unit
def test_route_token_mode_unknown_value_raises(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nroute_token_mode = "block"\n')
    with pytest.raises(HubConfigError, match="route_token_mode"):
        HubConfig.load(root)


@pytest.mark.unit
def test_route_token_mode_enforces_independently_of_runner_auth_mode(tmp_path: Path) -> None:
    """The two flags are separate — setting one leaves the other at its own default."""
    config = _hub_config(tmp_path)
    edited = dataclasses.replace(config, runner_auth_mode="enforce")
    edited.config_path.write_text(edited.to_toml())
    reloaded = HubConfig.load(edited.root)
    assert reloaded.runner_auth_mode == "enforce"
    assert reloaded.route_token_mode == "warn"


# --------------------------------------------------------------------------- #
# `produces_mode` — the produces-artifact rollout brake (issue #113 phase 5), a
# separate flag from `runner_auth_mode`/`route_token_mode` above.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_produces_mode_defaults_to_warn(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    assert config.produces_mode == "warn"


@pytest.mark.unit
def test_produces_mode_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = HubConfig.load(config.root)
    assert loaded.produces_mode == "warn"

    edited = dataclasses.replace(loaded, produces_mode="enforce")
    edited.config_path.write_text(edited.to_toml())
    reloaded = HubConfig.load(edited.root)
    assert reloaded.produces_mode == "enforce"


@pytest.mark.unit
def test_produces_mode_absent_from_toml_defaults_to_warn(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\n')
    assert HubConfig.load(root).produces_mode == "warn"


@pytest.mark.unit
def test_produces_mode_unknown_value_raises(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nproduces_mode = "block"\n')
    with pytest.raises(HubConfigError, match="produces_mode"):
        HubConfig.load(root)


@pytest.mark.unit
def test_produces_mode_enforces_independently_of_the_other_modes(tmp_path: Path) -> None:
    """All three flags are separate — setting one leaves the others at their own default."""
    config = _hub_config(tmp_path)
    edited = dataclasses.replace(config, produces_mode=PRODUCES_ENFORCE)
    edited.config_path.write_text(edited.to_toml())
    reloaded = HubConfig.load(edited.root)
    assert reloaded.produces_mode == "enforce"
    assert reloaded.runner_auth_mode == "warn"
    assert reloaded.route_token_mode == "warn"


# --------------------------------------------------------------------------- #
# `token_env` / `hub_token` — the runner presents its bearer token (issue #86b).
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_token_env_defaults_to_bz_hub_token(tmp_path: Path) -> None:
    config = RunnerConfig.scaffold(tmp_path)
    assert config.token_env == "BZ_HUB_TOKEN"


@pytest.mark.unit
def test_hub_token_absent_from_environment_resolves_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BZ_HUB_TOKEN", raising=False)
    config = RunnerConfig.scaffold(tmp_path)
    assert config.hub_token == ""
    assert config.auth_headers() == {}


@pytest.mark.unit
def test_hub_token_resolves_from_the_named_env_var_at_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN", "sekret-token")
    config = RunnerConfig.scaffold(tmp_path)
    assert config.hub_token == "sekret-token"
    assert config.auth_headers() == {"Authorization": "Bearer sekret-token"}


@pytest.mark.unit
def test_token_env_round_trips_through_to_toml_but_never_the_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `token_env` (the variable NAME) round-trips through toml; the secret itself never
    # does — it is re-resolved from the (possibly renamed) env var at every `load`.
    root = tmp_path / "runner"
    root.mkdir()
    edited = RunnerConfig(
        root=root, db_url=RunnerConfig.default_db_url(root), token_env="MY_CUSTOM_HUB_TOKEN", hub_token="unwritten"
    )
    (root / "blizzard-runner.toml").write_text(edited.to_toml())
    assert "unwritten" not in (root / "blizzard-runner.toml").read_text()

    monkeypatch.delenv("MY_CUSTOM_HUB_TOKEN", raising=False)
    reloaded = RunnerConfig.load(root)
    assert reloaded.token_env == "MY_CUSTOM_HUB_TOKEN"
    assert reloaded.hub_token == ""

    monkeypatch.setenv("MY_CUSTOM_HUB_TOKEN", "reloaded-secret")
    reloaded_with_env = RunnerConfig.load(root)
    assert reloaded_with_env.hub_token == "reloaded-secret"


@pytest.mark.unit
def test_token_env_absent_from_toml_defaults_to_bz_hub_token(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text('db_url = "sqlite:///x"\n')
    assert RunnerConfig.load(root).token_env == "BZ_HUB_TOKEN"


# --- trusted_proxies (issue #130) -------------------------------------------------


@pytest.mark.unit
def test_hub_trusted_proxies_default_empty(tmp_path: Path) -> None:
    # A fresh scaffold trusts no proxy — forwarded headers ignored, today's behavior.
    assert HubConfig.scaffold(tmp_path).trusted_proxies == ()


@pytest.mark.unit
def test_hub_trusted_proxies_round_trip_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    edited = dataclasses.replace(HubConfig.scaffold(root), trusted_proxies=("10.0.0.4", "192.168.0.0/16"))
    (root / "blizzard-hub.toml").write_text(edited.to_toml())
    assert HubConfig.load(root).trusted_proxies == ("10.0.0.4", "192.168.0.0/16")


@pytest.mark.unit
def test_hub_trusted_proxies_rejects_a_malformed_entry(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\ntrusted_proxies = ["not-an-ip"]\n')
    with pytest.raises(HubConfigError):
        HubConfig.load(root)


@pytest.mark.unit
def test_runner_trusted_proxies_default_empty(tmp_path: Path) -> None:
    assert RunnerConfig.scaffold(tmp_path).trusted_proxies == ()


@pytest.mark.unit
def test_runner_trusted_proxies_round_trip_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    edited = RunnerConfig(root=root, db_url=RunnerConfig.default_db_url(root), trusted_proxies=("10.0.0.4",))
    (root / "blizzard-runner.toml").write_text(edited.to_toml())
    assert RunnerConfig.load(root).trusted_proxies == ("10.0.0.4",)


@pytest.mark.unit
def test_runner_trusted_proxies_rejects_a_malformed_entry(tmp_path: Path) -> None:
    from blizzard.runner.config import ConfigError

    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text('db_url = "sqlite:///x"\ntrusted_proxies = ["10.0.0.0/999"]\n')
    with pytest.raises(ConfigError):
        RunnerConfig.load(root)


# --------------------------------------------------------------------------- #
# `follow_latest` — the fleet-wide auto-migration policy default (issue #164).
# Not a `*_mode` rollout ramp like the three above: a plain on/off, so the trio
# here pins the boolean shape rather than a value set.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_follow_latest_defaults_to_false(tmp_path: Path) -> None:
    """The shipped default keeps every chunk pinned to the mint it started on — adopting
    the policy is a deliberate act, so this is the pin against arming a fleet by upgrade."""
    assert _hub_config(tmp_path).follow_latest is False


@pytest.mark.unit
def test_follow_latest_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = HubConfig.load(config.root)
    assert loaded.follow_latest is False

    edited = dataclasses.replace(loaded, follow_latest=True)
    edited.config_path.write_text(edited.to_toml())
    reloaded = HubConfig.load(edited.root)
    assert reloaded.follow_latest is True


@pytest.mark.unit
def test_follow_latest_stays_a_top_level_key_in_the_emitted_toml(tmp_path: Path) -> None:
    """`to_toml` hand-rolls its emit as an ordered list of strings, so a key can drift
    below a `[table]` header without any type error. `follow_latest` would then load as
    `False` and the fleet-wide policy would be silently off — the failure direction with
    no symptom. Pin its position: it must precede the first table header."""
    emitted = dataclasses.replace(_hub_config(tmp_path), follow_latest=True).to_toml()
    assert "\nfollow_latest = true\n" in emitted
    first_table = emitted.index("\n[")
    assert emitted.index("\nfollow_latest = true\n") < first_table


@pytest.mark.unit
def test_follow_latest_absent_from_toml_defaults_to_false(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\n')
    assert HubConfig.load(root).follow_latest is False


@pytest.mark.unit
@pytest.mark.parametrize("value", ['"true"', '"yes"', "1", "[]"])
def test_follow_latest_non_boolean_raises(tmp_path: Path, value: str) -> None:
    """Validated, never truthy-coerced: `follow_latest = "true"` is a plausible typo, and
    coercing it would silently arm a fleet-wide migration policy nobody chose."""
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///x"\nfollow_latest = {value}\n')
    with pytest.raises(HubConfigError, match="follow_latest"):
        HubConfig.load(root)


# --------------------------------------------------------------------------- #
# `BZ_HUB_DB_URL` / `BZ_HUB_HOST` / `BZ_HUB_PORT` — load-time env overrides
# (issue #187). Precedence: CLI flag > environment > toml > default.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_hub_db_url_env_overrides_toml_at_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///toml-value.db"\n')
    override = "postgresql+psycopg://blizzard:secret@localhost:5432/hub"
    monkeypatch.setenv(HUB_ENV_DB_URL, override)
    assert HubConfig.load(root).db_url == override


@pytest.mark.unit
def test_hub_db_url_env_overrides_default_at_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = "postgresql+psycopg://blizzard:secret@localhost:5432/hub"
    monkeypatch.setenv(HUB_ENV_DB_URL, override)
    assert HubConfig.scaffold(tmp_path).db_url == override


@pytest.mark.unit
def test_hub_host_env_honored_at_load_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Previously only `scaffold` read BZ_HUB_HOST — a value set at `host` time on an
    # already-scaffolded config (e.g. a container re-created against an existing volume)
    # was ignored. `load` now honors it too.
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nhost = "127.0.0.1"\n')
    monkeypatch.setenv(HUB_ENV_HOST, "0.0.0.0")
    assert HubConfig.load(root).host == "0.0.0.0"


@pytest.mark.unit
def test_hub_host_cli_flag_wins_over_env_at_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nhost = "127.0.0.1"\n')
    monkeypatch.setenv(HUB_ENV_HOST, "0.0.0.0")
    assert HubConfig.load(root, host="10.0.0.5").host == "10.0.0.5"


@pytest.mark.unit
def test_hub_port_env_honored_at_load_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nport = 8421\n')
    monkeypatch.setenv(HUB_ENV_PORT, "9999")
    assert HubConfig.load(root).port == 9999


@pytest.mark.unit
def test_hub_port_cli_flag_wins_over_env_at_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nport = 8421\n')
    monkeypatch.setenv(HUB_ENV_PORT, "9999")
    assert HubConfig.load(root, port=1234).port == 1234


@pytest.mark.unit
def test_hub_config_byte_identical_when_env_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every variable unset leaves parsed values byte-identical to today — the
    regression case pinning that this change is additive, not a behavior shift."""
    for var in (HUB_ENV_DB_URL, HUB_ENV_HOST, HUB_ENV_PORT):
        monkeypatch.delenv(var, raising=False)
    root = tmp_path / "hub"
    root.mkdir()
    pg = "postgresql+psycopg://blizzard:secret@localhost:5432/hub"
    (root / "blizzard-hub.toml").write_text(f'db_url = "{pg}"\nhost = "0.0.0.0"\nport = 9001\n')
    config = HubConfig.load(root)
    assert config.db_url == pg
    assert config.host == "0.0.0.0"
    assert config.port == 9001


@pytest.mark.unit
def test_hub_malformed_port_env_raises_from_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A container's very first boot (`hub init` on an empty volume) scaffolds — it must
    # fail the same named way as any later `load`, not with a raw ValueError.
    monkeypatch.setenv(HUB_ENV_PORT, "not-a-port")
    with pytest.raises(HubConfigError, match=HUB_ENV_PORT):
        HubConfig.scaffold(tmp_path)


@pytest.mark.unit
def test_hub_malformed_port_env_raises_from_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\n')
    monkeypatch.setenv(HUB_ENV_PORT, "not-a-port")
    with pytest.raises(HubConfigError, match=HUB_ENV_PORT):
        HubConfig.load(root)


@pytest.mark.unit
def test_hub_db_url_honored_identically_by_host_and_migrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`blizzard hub host` and `blizzard hub migrate` both resolve through
    `HubConfig.load`/`scaffold` — the override needs no per-verb wiring."""
    from blizzard.hub.runtime import init_environment, migrate, migration_runner

    root = tmp_path / "hub"
    init_environment(root)  # scaffolds + migrates the default sqlite store

    override_url = f"sqlite:///{tmp_path / 'override.db'}"
    monkeypatch.setenv(HUB_ENV_DB_URL, override_url)

    migrate(root)  # resolves through HubConfig.load — migrates the overridden store
    config = HubConfig.load(root)
    assert config.db_url == override_url
    assert (tmp_path / "override.db").exists()
    # what the daemon's own revision-mismatch guard reads at startup is the same store
    assert migration_runner(config).current_revision() is not None
