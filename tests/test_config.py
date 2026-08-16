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
from blizzard.runner.config import DEFAULT_RUNNER_CEILING_WINDOW_HOURS, ConfigError, RunnerConfig
from blizzard.runner.config import ENV_PORT as RUNNER_ENV_PORT
from blizzard.runner.harness.workspace_prompts import PACKAGED


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
    assert config.resolved_workspace_prompt() == "# Fleet worker\nFrom a file."


@pytest.mark.unit
def test_workspace_prompt_package_resolves_a_packaged_sample(tmp_path: Path) -> None:
    """A named sample is resolved out of the wheel — no file in the runtime root at all."""
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_prompt_package="winter",
    )
    assert config.resolved_workspace_prompt() == PACKAGED.text("winter")


@pytest.mark.unit
def test_workspace_prompt_package_rejects_an_unknown_sample(tmp_path: Path) -> None:
    """Fail fast at startup, naming the corpus, rather than spawning workers with no policy."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", workspace_prompt_package="no-such-sample")
    with pytest.raises(ConfigError) as caught:
        config.resolved_workspace_prompt()
    assert "no-such-sample" in str(caught.value)
    assert "winter" in str(caught.value)


@pytest.mark.unit
@pytest.mark.parametrize("knob", ["workspace_prompt", "workspace_prompt_file"])
def test_workspace_prompt_package_is_exclusive_with_the_text_knobs(tmp_path: Path, knob: str) -> None:
    """The pair has no precedence rule, so the ambiguity is refused instead of silently ranked."""
    base = RunnerConfig(root=tmp_path, db_url="sqlite://", workspace_prompt_package="winter")
    config = (
        dataclasses.replace(base, workspace_prompt="something")
        if knob == "workspace_prompt"
        else dataclasses.replace(base, workspace_prompt_file="something")
    )
    with pytest.raises(ConfigError) as caught:
        config.resolved_workspace_prompt()
    assert knob in str(caught.value)


@pytest.mark.unit
def test_workspace_prompt_package_round_trips_through_toml(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    written = RunnerConfig(root=root, db_url="sqlite://", workspace_prompt_package="winter")
    (root / "blizzard-runner.toml").write_text(written.to_toml())
    assert RunnerConfig.load(root).workspace_prompt_package == "winter"


@pytest.mark.unit
def test_workspace_prompt_package_env_seeds_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_WORKSPACE_PROMPT_PACKAGE", "winter")
    assert RunnerConfig.scaffold(tmp_path).workspace_prompt_package == "winter"


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
def test_transcripts_ship_defaults_false(tmp_path: Path) -> None:
    # Off by default (D5, issue #246) — a fresh scaffold ships no transcript content.
    assert RunnerConfig.scaffold(tmp_path).transcripts_ship is False


@pytest.mark.unit
def test_transcripts_ship_absent_when_transcripts_table_omits_the_key(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n[transcripts]\n')
    assert RunnerConfig.load(root).transcripts_ship is False


@pytest.mark.unit
def test_transcripts_ship_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    edited = RunnerConfig(root=root, db_url=RunnerConfig.default_db_url(root), transcripts_ship=True)
    (root / "blizzard-runner.toml").write_text(edited.to_toml())
    reloaded = RunnerConfig.load(root)
    assert reloaded.transcripts_ship is True


@pytest.mark.unit
def test_transcripts_ship_parses_from_a_hand_written_transcripts_table(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(
        f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n[transcripts]\nship = true\n'
    )
    assert RunnerConfig.load(root).transcripts_ship is True


@pytest.mark.unit
def test_transcripts_ship_rejects_a_non_boolean_typo_rather_than_coercing_it(tmp_path: Path) -> None:
    """review F10, blizzard#246: ``bool("false")`` is truthy — a typo'd string on the one
    switch gating the entire lane must not silently turn it ON."""
    from blizzard.runner.config import ConfigError

    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(
        f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n[transcripts]\nship = "false"\n'
    )
    with pytest.raises(ConfigError, match="ship"):
        RunnerConfig.load(root)


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
def test_external_usage_credentials_path_defaults_none(tmp_path: Path) -> None:
    # No `[external_subscription_usage]` table at all — absent means the adapter's own
    # real-credential-store default, not a scratch/disabled path (issue #218).
    assert RunnerConfig.scaffold(tmp_path).external_usage_credentials_path is None


@pytest.mark.unit
def test_external_usage_credentials_path_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    scratch = str(tmp_path / "scratch-credentials.json")
    edited = RunnerConfig(
        root=root,
        db_url=RunnerConfig.default_db_url(root),
        external_usage_credentials_path=scratch,
    )
    (root / "blizzard-runner.toml").write_text(edited.to_toml())
    reloaded = RunnerConfig.load(root)
    assert reloaded.external_usage_credentials_path == scratch


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
    # see hub/cli.py's ingest token parsing.
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
def test_work_source_annotate_defaults_to_false(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T"\n'
    )
    loaded = HubConfig.load(root)
    assert loaded.work_sources[0].annotate is False


@pytest.mark.unit
def test_work_source_annotate_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = HubConfig.load(config.root)

    sources = (
        WorkSourceConfig(
            name="blizzard",
            provider="github",
            repo="paul-gross/blizzard",
            token_env="BZ_WORK_SOURCE_TOKEN",
            annotate=True,
        ),
    )
    edited = dataclasses.replace(loaded, work_sources=sources)
    edited.config_path.write_text(edited.to_toml())

    reloaded = HubConfig.load(edited.root)
    assert reloaded.work_sources == sources
    assert reloaded.work_sources[0].annotate is True


@pytest.mark.unit
def test_work_source_annotate_non_bool_raises_naming_the_source(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\n'
        'token_env = "T"\nannotate = "yes"\n'
    )
    with pytest.raises(HubConfigError, match="blizzard"):
        HubConfig.load(root)


@pytest.mark.unit
def test_work_source_close_defaults_to_false(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T"\n'
    )
    loaded = HubConfig.load(root)
    assert loaded.work_sources[0].close is False


@pytest.mark.unit
def test_work_source_close_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = HubConfig.load(config.root)

    sources = (
        WorkSourceConfig(
            name="blizzard",
            provider="github",
            repo="paul-gross/blizzard",
            token_env="BZ_WORK_SOURCE_TOKEN",
            close=True,
        ),
    )
    edited = dataclasses.replace(loaded, work_sources=sources)
    edited.config_path.write_text(edited.to_toml())

    reloaded = HubConfig.load(edited.root)
    assert reloaded.work_sources == sources
    assert reloaded.work_sources[0].close is True


@pytest.mark.unit
def test_work_source_close_non_bool_raises_naming_the_source(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\n'
        'token_env = "T"\nclose = "yes"\n'
    )
    with pytest.raises(HubConfigError, match="blizzard"):
        HubConfig.load(root)


@pytest.mark.unit
def test_annotation_interval_seconds_defaults_to_120(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    assert config.annotation_interval_seconds == 120


@pytest.mark.unit
def test_annotation_interval_seconds_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    edited = dataclasses.replace(config, annotation_interval_seconds=30)
    edited.config_path.write_text(edited.to_toml())

    reloaded = HubConfig.load(edited.root)

    assert reloaded.annotation_interval_seconds == 30


@pytest.mark.unit
def test_annotation_interval_seconds_absent_from_toml_defaults_to_120(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\n')
    assert HubConfig.load(root).annotation_interval_seconds == 120


@pytest.mark.unit
def test_a_leftover_pm_source_block_fails_the_load_naming_the_new_key(tmp_path: Path) -> None:
    """Issue #55's deliberate no-alias: a config still carrying the pre-rename
    `[[pm_source]]` key fails fast, naming the new key, rather than silently parsing as
    zero sources."""
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[pm_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T"\n'
    )
    with pytest.raises(HubConfigError, match=r"\[\[work_source\]\]"):
        HubConfig.load(root)


@pytest.mark.unit
def test_a_leftover_pm_source_block_fails_even_beside_a_valid_work_source(tmp_path: Path) -> None:
    """Carrying both keys still fails — the check is not merely "did we end up with any
    sources"."""
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
# `route_token_mode` — the route-capability-token rollout brake (issue #84b).


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
# `produces_mode` — the produces-artifact rollout brake (issue #113 phase 5).


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
    # A fresh scaffold trusts no proxy — forwarded headers ignored.
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
    """`to_toml` hand-rolls its emit as an ordered list of strings, so `follow_latest`
    must precede the first `[table]` header or it silently loads as `False`."""
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
# `BZ_HUB_DB_URL` / `BZ_HUB_HOST` / `BZ_HUB_PORT` — load-time env overrides (issue #187).


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
    # A container re-created against an existing volume sets BZ_HUB_HOST at `load`
    # time, not just `scaffold`.
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
    """Every variable unset leaves parsed values byte-identical."""
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

    # Inside root: an override pointing elsewhere is exactly what the --dir isolation
    # guard (issue #234) exists to catch — see test_config.py's own guard tests below.
    override_url = f"sqlite:///{root / 'override.db'}"
    monkeypatch.setenv(HUB_ENV_DB_URL, override_url)

    migrate(root)  # resolves through HubConfig.load — migrates the overridden store
    config = HubConfig.load(root)
    assert config.db_url == override_url
    assert (root / "override.db").exists()
    assert migration_runner(config).current_revision() is not None


# --------------------------------------------------------------------------- #
# The db_url --dir isolation guard (issue #234).


@pytest.mark.unit
def test_db_url_outside_root_is_refused(tmp_path: Path) -> None:
    live_db = tmp_path / "live" / "hub.db"
    root = tmp_path / "copy"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///{live_db}"\n')

    with pytest.raises(HubConfigError, match=str(live_db)):
        HubConfig.load(root)


@pytest.mark.unit
def test_db_url_outside_root_proceeds_with_allow_external_db(tmp_path: Path) -> None:
    live_db = tmp_path / "live" / "hub.db"
    root = tmp_path / "copy"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///{live_db}"\n')

    config = HubConfig.load(root, allow_external_db=True)
    assert config.db_url == f"sqlite:///{live_db}"


@pytest.mark.unit
def test_db_url_absolute_but_inside_root_needs_no_flag(tmp_path: Path) -> None:
    # Acceptance: existing configs with an in-dir absolute db_url still work — the guard
    # triggers only on paths that resolve *outside* root.
    root = tmp_path / "hub"
    root.mkdir()
    in_dir_db = root / "hub.db"
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///{in_dir_db}"\n')

    config = HubConfig.load(root)
    assert config.db_url == f"sqlite:///{in_dir_db}"


@pytest.mark.unit
def test_db_url_env_override_outside_root_is_also_guarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Acceptance: BZ_HUB_DB_URL overrides remain honored, but pass through the same guard.
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///{root / "hub.db"}"\n')
    outside_db = tmp_path / "elsewhere" / "hub.db"
    monkeypatch.setenv(HUB_ENV_DB_URL, f"sqlite:///{outside_db}")

    with pytest.raises(HubConfigError, match=str(outside_db)):
        HubConfig.load(root)

    config = HubConfig.load(root, allow_external_db=True)
    assert config.db_url == f"sqlite:///{outside_db}"


@pytest.mark.unit
def test_db_url_relative_sqlite_path_bypasses_the_guard(tmp_path: Path) -> None:
    # A relative sqlite path resolves against the process cwd at open time, not root —
    # there is nothing for the --dir guard to compare it to.
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///relative.db"\n')
    assert HubConfig.load(root).db_url == "sqlite:///relative.db"


@pytest.mark.unit
def test_postgres_db_url_bypasses_the_guard(tmp_path: Path) -> None:
    # A non-sqlite db_url is inherently external — it passes the --dir guard untouched.
    root = tmp_path / "hub"
    root.mkdir()
    pg = "postgresql+psycopg://blizzard:secret@elsewhere:5432/hub"
    (root / "blizzard-hub.toml").write_text(f'db_url = "{pg}"\n')
    assert HubConfig.load(root).db_url == pg


@pytest.mark.unit
def test_fresh_scaffold_omits_db_url_from_to_toml(tmp_path: Path) -> None:
    # issue #234: a fresh scaffold's db_url is the resolved default, which `to_toml`
    # omits rather than serializing an absolute path a copied dir shouldn't carry.
    root = tmp_path / "hub"
    root.mkdir()
    config = HubConfig.scaffold(root)
    emitted = config.to_toml()
    assert "db_url" not in emitted


@pytest.mark.unit
def test_load_falls_back_to_default_db_url_when_key_is_absent(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('host = "0.0.0.0"\n')
    assert HubConfig.load(root).db_url == HubConfig.default_db_url(root)


@pytest.mark.unit
def test_a_freshly_scaffolded_dir_copied_elsewhere_re_derives_its_own_db_url(tmp_path: Path) -> None:
    """Issue #234: `cp -r` a freshly-inited runtime dir and it is self-contained — the
    copy's db_url points into the copy, not back at the original, with no
    `--allow-external-db` needed."""
    import shutil

    from blizzard.hub.runtime import init_environment

    original = tmp_path / "original"
    init_environment(original)

    copy_root = tmp_path / "copy"
    shutil.copytree(original, copy_root)

    copy_config = HubConfig.load(copy_root)
    assert copy_config.db_url == HubConfig.default_db_url(copy_root)
    assert Path(copy_config.db_url.removeprefix("sqlite:///")).exists()


# --- the lane's byte ceilings (blizzard#338) -----------------------------------------


@pytest.mark.unit
def test_transcript_caps_default_to_none_so_the_pump_keeps_its_own_defaults(tmp_path: Path) -> None:
    """`None`, not a copy of the number: the config layer must never restate a value
    `blizzard.runner.transcripts.caps` owns, or the two drift apart silently."""
    scaffolded = RunnerConfig.scaffold(tmp_path)

    assert scaffolded.transcript_record_max_bytes is None
    assert scaffolded.transcript_chunk_max_bytes is None


@pytest.mark.unit
def test_transcript_caps_parse_from_a_hand_written_transcripts_table(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(
        f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n'
        "[transcripts]\nrecord_max_bytes = 4194304\nchunk_max_bytes = 214748364800\n"
    )
    loaded = RunnerConfig.load(root)

    assert loaded.transcript_record_max_bytes == 4194304
    assert loaded.transcript_chunk_max_bytes == 214748364800


@pytest.mark.unit
def test_transcript_caps_round_trip_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    edited = RunnerConfig(
        root=root,
        db_url=RunnerConfig.default_db_url(root),
        transcript_record_max_bytes=4194304,
        transcript_chunk_max_bytes=214748364800,
    )
    (root / "blizzard-runner.toml").write_text(edited.to_toml())
    reloaded = RunnerConfig.load(root)

    assert reloaded.transcript_record_max_bytes == 4194304
    assert reloaded.transcript_chunk_max_bytes == 214748364800


@pytest.mark.unit
def test_an_unset_transcript_cap_is_scaffolded_commented_at_its_default(tmp_path: Path) -> None:
    """The template shows the ceiling an operator is about to override — a bare `[transcripts]`
    with nothing under it leaves them guessing what the widened value is relative to."""
    from blizzard.runner.transcripts.caps import CHUNK_TRANSCRIPT_MAX_BYTES, TRANSCRIPT_RECORD_MAX_BYTES

    rendered = RunnerConfig.scaffold(tmp_path).to_toml()

    assert f"# record_max_bytes = {TRANSCRIPT_RECORD_MAX_BYTES}\n" in rendered
    assert f"# chunk_max_bytes = {CHUNK_TRANSCRIPT_MAX_BYTES}\n" in rendered


@pytest.mark.unit
@pytest.mark.parametrize("key", ["record_max_bytes", "chunk_max_bytes"])
@pytest.mark.parametrize("value", ["0", '"8388608"', "true"])
def test_a_transcript_cap_refuses_a_non_positive_or_non_integer_value(tmp_path: Path, key: str, value: str) -> None:
    """A cap has no "off" value: zero would reject every record while reading as unset, and a
    quoted number would coerce to something arbitrary rather than the bytes it looks like."""
    from blizzard.runner.config import ConfigError

    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text(
        f'db_url = "{RunnerConfig.default_db_url(root)}"\n\n[transcripts]\n{key} = {value}\n'
    )

    with pytest.raises(ConfigError, match=key):
        RunnerConfig.load(root)


# --- the hub's own ingest ceilings (blizzard#338) ------------------------------------


@pytest.mark.unit
def test_hub_transcript_caps_default_to_none_so_the_domain_keeps_its_own(tmp_path: Path) -> None:
    from blizzard.hub.config import HubConfig

    caps = HubConfig(root=tmp_path, db_url=HubConfig.default_db_url(tmp_path)).transcripts

    assert caps.record_max_bytes is None
    assert caps.chunk_budget_max_bytes is None
    assert caps.runner_daily_rate_max_bytes is None


@pytest.mark.unit
def test_hub_transcript_caps_parse_and_round_trip_through_to_toml(tmp_path: Path) -> None:
    from blizzard.hub.config import HubConfig, TranscriptCapsConfig

    root = tmp_path / "hub"
    root.mkdir()
    edited = HubConfig(
        root=root,
        db_url=HubConfig.default_db_url(root),
        transcripts=TranscriptCapsConfig(runner_daily_rate_max_bytes=214748364800),
    )
    (root / "blizzard-hub.toml").write_text(edited.to_toml())

    reloaded = HubConfig.load(root)

    assert reloaded.transcripts.runner_daily_rate_max_bytes == 214748364800
    # The two left unset stay unset through the round trip rather than being frozen at
    # whatever the default happened to be when the file was written.
    assert reloaded.transcripts.record_max_bytes is None
    assert reloaded.transcripts.chunk_budget_max_bytes is None


@pytest.mark.unit
def test_the_hub_template_shows_each_ingest_ceiling_at_its_domain_default(tmp_path: Path) -> None:
    from blizzard.hub.config import HubConfig
    from blizzard.hub.domain.transcripts import TranscriptCaps

    rendered = HubConfig(root=tmp_path, db_url=HubConfig.default_db_url(tmp_path)).to_toml()
    defaults = TranscriptCaps()

    assert f"# record_max_bytes = {defaults.record_max_bytes}\n" in rendered
    assert f"# chunk_budget_max_bytes = {defaults.chunk_budget_max_bytes}\n" in rendered
    assert f"# runner_daily_rate_max_bytes = {defaults.runner_daily_rate_max_bytes}\n" in rendered


@pytest.mark.unit
@pytest.mark.parametrize("key", ["record_max_bytes", "chunk_budget_max_bytes", "runner_daily_rate_max_bytes"])
@pytest.mark.parametrize("value", ["0", '"10485760"', "false"])
def test_a_hub_ingest_cap_refuses_a_non_positive_or_non_integer_value(tmp_path: Path, key: str, value: str) -> None:
    from blizzard.hub.config import ConfigError, HubConfig

    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f"[transcripts]\n{key} = {value}\n")

    with pytest.raises(ConfigError, match=key):
        HubConfig.load(root)


@pytest.mark.unit
def test_the_configured_hub_caps_reach_the_wired_ingest_service(tmp_path: Path) -> None:
    """The resolution seam itself (blizzard#338): a configured ceiling must reach the
    service, and an unconfigured one must fall back to the domain default rather than None."""
    from blizzard.hub.app import _transcript_caps
    from blizzard.hub.config import HubConfig, TranscriptCapsConfig
    from blizzard.hub.domain.transcripts import TranscriptCaps

    resolved = _transcript_caps(
        HubConfig(
            root=tmp_path,
            db_url=HubConfig.default_db_url(tmp_path),
            transcripts=TranscriptCapsConfig(runner_daily_rate_max_bytes=214748364800),
        )
    )

    assert resolved.runner_daily_rate_max_bytes == 214748364800
    assert resolved.record_max_bytes == TranscriptCaps().record_max_bytes
