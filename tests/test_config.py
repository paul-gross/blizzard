"""Runtime config — the store URL is the single portability knob (``bzh:sql-portable``).

Both daemons scaffold a sqlite default under the data dir and read any store URL
back verbatim: a postgres URL is accepted with no code branch on the backend, and
the winter service band ``BZ_*_PORT`` env overrides the bind port.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.trace_export.config import TracingConfig, toml_literal
from blizzard.hub.config import ENV_DB_URL as HUB_ENV_DB_URL
from blizzard.hub.config import ENV_HOST as HUB_ENV_HOST
from blizzard.hub.config import ENV_PORT as HUB_ENV_PORT
from blizzard.hub.config import ConfigError as HubConfigError
from blizzard.hub.config import HubConfig, parse_egress_config, read_legacy_keys, render_egress_toml
from blizzard.hub.domain.config.legacy_keys import LegacyKeys, WorkSourceConfig
from blizzard.hub.domain.execution.auth.produces import PRODUCES_ENFORCE
from blizzard.hub.domain.observability.egress.config import EgressConfig
from tests.config_inputs import STRICT, init_hub, load_hub_config, migrate_hub


@pytest.mark.unit
def test_hub_default_db_url_is_sqlite_under_data_dir(tmp_path: Path) -> None:
    url = HubConfig.default_db_url(tmp_path)
    assert url.startswith("sqlite:///")
    assert url.endswith("data/hub.db")


@pytest.mark.unit
def test_postgres_url_round_trips_through_config(tmp_path: Path) -> None:
    pg = "postgresql+psycopg://blizzard:secret@localhost:5432/hub"
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "{pg}"\nhost = "0.0.0.0"\nport = 9001\n')
    config = load_hub_config(root)
    assert config.db_url == pg
    assert config.host == "0.0.0.0"
    assert config.port == 9001


# --------------------------------------------------------------------------- #
# `[[work_source]]` — legacy blocks, read by `LegacyKeys` for the import alone.


def _hub_config(tmp_path: Path) -> HubConfig:
    root = tmp_path / "hub"
    root.mkdir()
    return HubConfig(root=root, db_url=HubConfig.default_db_url(root))


_BLOCK = '\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T"\n'


def _legacy(tmp_path: Path, body: str, environ: dict[str, str] | None = None) -> LegacyKeys:
    path = tmp_path / "blizzard-hub.toml"
    path.write_text('db_url = "sqlite:///x"\n' + body)
    return read_legacy_keys(path, environ or {}, strict=STRICT)


@pytest.mark.unit
def test_a_scaffolded_config_carries_no_legacy_keys(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    legacy = read_legacy_keys(config.config_path, {}, strict=STRICT)
    assert not legacy.present()
    assert "[[work_source]]" not in config.to_toml()


@pytest.mark.unit
def test_hub_config_load_ignores_work_source_blocks(tmp_path: Path) -> None:
    """The raw parse is the import's alone — even an invalid block never fails the load."""
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\n\n[[work_source]]\nname = "hub"\n')
    assert not hasattr(load_hub_config(root, allow_external_db=True), "work_sources")


@pytest.mark.unit
def test_legacy_keys_read_blocks_and_the_variables_present(tmp_path: Path) -> None:
    body = (
        '\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "paul-gross/blizzard"\n'
        'token_env = "BZ_WORK_SOURCE_TOKEN"\n'
        '\n[[work_source]]\nname = "internal"\nprovider = "github"\nrepo = "acme/internal-tool"\n'
        'token_env = "BZ_INTERNAL_TOKEN"\napi_base = "https://git.corp.internal/api/v3"\n'
        'web_base = "https://git.corp.internal"\n'
    )
    environ = {"BZ_FORGE_URL": "http://forge", "BZ_WORK_SOURCE_TOKEN": "t", "UNRELATED": "x"}
    legacy = _legacy(tmp_path, body, environ)
    assert legacy.sources == (
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
    assert legacy.variables == ("BZ_FORGE_URL", "BZ_WORK_SOURCE_TOKEN")
    assert legacy.present()
    assert legacy.locations() == (
        'blizzard-hub.toml [[work_source]] "blizzard"',
        'blizzard-hub.toml [[work_source]] "internal"',
        "environment BZ_FORGE_URL",
        "environment BZ_WORK_SOURCE_TOKEN",
    )


@pytest.mark.unit
def test_legacy_keys_never_count_a_commented_out_block(tmp_path: Path) -> None:
    commented = "".join(f"# {line}\n" for line in _BLOCK.strip().splitlines())
    legacy = _legacy(tmp_path, commented, {"T": "secret"})
    assert not legacy.present()


@pytest.mark.unit
def test_legacy_keys_count_a_forge_variable_alone(tmp_path: Path) -> None:
    legacy = _legacy(tmp_path, "", {"BZ_FORGE_OWNER": "acme"})
    assert legacy.present()
    assert legacy.locations() == ("environment BZ_FORGE_OWNER",)


@pytest.mark.unit
def test_work_source_missing_required_key_raises(tmp_path: Path) -> None:
    with pytest.raises(HubConfigError, match="token_env"):
        _legacy(tmp_path, '\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\n')


@pytest.mark.unit
def test_work_source_duplicate_name_raises(tmp_path: Path) -> None:
    with pytest.raises(HubConfigError, match="duplicate"):
        _legacy(
            tmp_path,
            '\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T1"\n'
            '\n[[work_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r2"\ntoken_env = "T2"\n',
        )


@pytest.mark.unit
def test_work_source_named_hub_raises(tmp_path: Path) -> None:
    """``hub`` is reserved for the built-in, always-seated source."""
    with pytest.raises(HubConfigError, match="hub"):
        _legacy(tmp_path, '\n[[work_source]]\nname = "hub"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T1"\n')


@pytest.mark.unit
def test_work_source_duplicate_provider_and_repo_raises(tmp_path: Path) -> None:
    # Two names for one (provider, repo) would let the same item be ingested twice
    # under two identities — this is what holds pointer identity uniqueness up.
    with pytest.raises(HubConfigError, match="duplicate"):
        _legacy(
            tmp_path,
            '\n[[work_source]]\nname = "a"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T1"\n'
            '\n[[work_source]]\nname = "b"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T2"\n',
        )


@pytest.mark.unit
def test_work_source_name_with_a_colon_raises(tmp_path: Path) -> None:
    # see src/blizzard/hub/cli/chunk.py's ingest token parsing.
    with pytest.raises(HubConfigError, match=":"):
        _legacy(tmp_path, _BLOCK.replace('"blizzard"', '"acme:blizzard"'))


@pytest.mark.unit
def test_work_source_unknown_provider_raises(tmp_path: Path) -> None:
    with pytest.raises(HubConfigError, match="jira"):
        _legacy(tmp_path, _BLOCK.replace('"github"', '"jira"'))


@pytest.mark.unit
def test_work_source_annotate_defaults_to_false(tmp_path: Path) -> None:
    assert _legacy(tmp_path, _BLOCK).sources[0].annotate is False


@pytest.mark.unit
def test_work_source_annotate_is_read(tmp_path: Path) -> None:
    assert _legacy(tmp_path, _BLOCK + "annotate = true\n").sources[0].annotate is True


@pytest.mark.unit
def test_work_source_annotate_non_bool_raises_naming_the_source(tmp_path: Path) -> None:
    with pytest.raises(HubConfigError, match="blizzard"):
        _legacy(tmp_path, _BLOCK + 'annotate = "yes"\n')


@pytest.mark.unit
def test_a_leftover_close_key_fails_the_read_naming_the_removal(tmp_path: Path) -> None:
    """Closure is unconditional — a block still hand-carrying the retired `close` key
    fails fast, naming the removal, rather than silently parsing it away."""
    with pytest.raises(HubConfigError, match="blizzard"):
        _legacy(tmp_path, _BLOCK + "close = true\n")


@pytest.mark.unit
def test_annotation_interval_seconds_defaults_to_120(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    assert config.annotation_interval_seconds == 120


@pytest.mark.unit
def test_annotation_interval_seconds_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    edited = dataclasses.replace(config, annotation_interval_seconds=30)
    edited.config_path.write_text(edited.to_toml())

    reloaded = load_hub_config(edited.root)

    assert reloaded.annotation_interval_seconds == 30


@pytest.mark.unit
def test_annotation_interval_seconds_absent_from_toml_defaults_to_120(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\n')
    assert load_hub_config(root).annotation_interval_seconds == 120


@pytest.mark.unit
def test_public_url_defaults_to_none_and_is_shown_commented_out(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    assert config.public_url is None
    assert '# public_url = "' in config.to_toml()
    config.config_path.write_text(config.to_toml())
    assert load_hub_config(config.root).public_url is None


@pytest.mark.unit
def test_public_url_round_trips_through_to_toml_and_load_without_a_trailing_slash(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\npublic_url = "https://blizzard.example.com/"\n')
    loaded = load_hub_config(root, allow_external_db=True)
    assert loaded.public_url == "https://blizzard.example.com"

    loaded.config_path.write_text(loaded.to_toml())
    assert load_hub_config(root, allow_external_db=True).public_url == "https://blizzard.example.com"


@pytest.mark.unit
@pytest.mark.parametrize("value", ['"blizzard.example.com"', '"/board"', '"ftp://blizzard.example.com"', "8080"])
def test_a_public_url_that_is_not_an_absolute_http_url_fails_the_load(tmp_path: Path, value: str) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///x"\npublic_url = {value}\n')
    with pytest.raises(HubConfigError, match="public_url"):
        load_hub_config(root, allow_external_db=True)


@pytest.mark.unit
def test_a_leftover_pm_source_block_fails_the_load_naming_the_new_key(tmp_path: Path) -> None:
    """Deliberate no-alias: a config still carrying the pre-rename
    `[[pm_source]]` key fails fast, naming the new key, rather than silently parsing as
    zero sources."""
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        'db_url = "sqlite:///x"\n\n[[pm_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T"\n'
    )
    with pytest.raises(HubConfigError, match=r"\[\[work_source\]\]"):
        load_hub_config(root)


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
        load_hub_config(root)


# --------------------------------------------------------------------------- #
# `runner_auth_mode` — a key the hub does not read; a toml that still sets it loads as if it did not.


@pytest.mark.unit
@pytest.mark.parametrize("value", ["enforce", "warn", "block"])
def test_a_toml_still_setting_runner_auth_mode_loads_with_the_key_ignored(tmp_path: Path, value: str) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///x"\nrunner_auth_mode = "{value}"\n')

    loaded = load_hub_config(root)

    assert not hasattr(loaded, "runner_auth_mode")
    assert "runner_auth_mode" not in loaded.to_toml()


# --------------------------------------------------------------------------- #
# `route_token_mode` — the route-capability-token rollout brake.


@pytest.mark.unit
def test_route_token_mode_defaults_to_warn(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    assert config.route_token_mode == "warn"


@pytest.mark.unit
def test_route_token_mode_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = load_hub_config(config.root)
    assert loaded.route_token_mode == "warn"

    edited = dataclasses.replace(loaded, route_token_mode="enforce")
    edited.config_path.write_text(edited.to_toml())
    reloaded = load_hub_config(edited.root)
    assert reloaded.route_token_mode == "enforce"


@pytest.mark.unit
def test_route_token_mode_absent_from_toml_defaults_to_warn(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\n')
    assert load_hub_config(root).route_token_mode == "warn"


@pytest.mark.unit
def test_route_token_mode_unknown_value_raises(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nroute_token_mode = "block"\n')
    with pytest.raises(HubConfigError, match="route_token_mode"):
        load_hub_config(root)


# --------------------------------------------------------------------------- #
# `produces_mode` — the produces-artifact rollout brake.


@pytest.mark.unit
def test_produces_mode_defaults_to_warn(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    assert config.produces_mode == "warn"


@pytest.mark.unit
def test_produces_mode_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = load_hub_config(config.root)
    assert loaded.produces_mode == "warn"

    edited = dataclasses.replace(loaded, produces_mode="enforce")
    edited.config_path.write_text(edited.to_toml())
    reloaded = load_hub_config(edited.root)
    assert reloaded.produces_mode == "enforce"


@pytest.mark.unit
def test_produces_mode_absent_from_toml_defaults_to_warn(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\n')
    assert load_hub_config(root).produces_mode == "warn"


@pytest.mark.unit
def test_produces_mode_unknown_value_raises(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nproduces_mode = "block"\n')
    with pytest.raises(HubConfigError, match="produces_mode"):
        load_hub_config(root)


@pytest.mark.unit
def test_produces_mode_enforces_independently_of_the_other_modes(tmp_path: Path) -> None:
    """The two flags are separate — setting one leaves the other at its own default."""
    config = _hub_config(tmp_path)
    edited = dataclasses.replace(config, produces_mode=PRODUCES_ENFORCE)
    edited.config_path.write_text(edited.to_toml())
    reloaded = load_hub_config(edited.root)
    assert reloaded.produces_mode == "enforce"
    assert reloaded.route_token_mode == "warn"


# --------------------------------------------------------------------------- #
# `token_env` / `hub_token` — the runner presents its bearer token.


# --- trusted_proxies -------------------------------------------------


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
    assert load_hub_config(root).trusted_proxies == ("10.0.0.4", "192.168.0.0/16")


@pytest.mark.unit
def test_hub_trusted_proxies_rejects_a_malformed_entry(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\ntrusted_proxies = ["not-an-ip"]\n')
    with pytest.raises(HubConfigError):
        load_hub_config(root)


# --------------------------------------------------------------------------- #
# `follow_latest` — the fleet-wide auto-migration policy default.


@pytest.mark.unit
def test_follow_latest_defaults_to_false(tmp_path: Path) -> None:
    """The shipped default keeps every chunk pinned to the mint it started on — adopting
    the policy is a deliberate act, so this is the pin against arming a fleet by upgrade."""
    assert _hub_config(tmp_path).follow_latest is False


@pytest.mark.unit
def test_follow_latest_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = load_hub_config(config.root)
    assert loaded.follow_latest is False

    edited = dataclasses.replace(loaded, follow_latest=True)
    edited.config_path.write_text(edited.to_toml())
    reloaded = load_hub_config(edited.root)
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
    assert load_hub_config(root).follow_latest is False


@pytest.mark.unit
@pytest.mark.parametrize("value", ['"true"', '"yes"', "1", "[]"])
def test_follow_latest_non_boolean_raises(tmp_path: Path, value: str) -> None:
    """Validated, never truthy-coerced: `follow_latest = "true"` is a plausible typo, and
    coercing it would silently arm a fleet-wide migration policy nobody chose."""
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///x"\nfollow_latest = {value}\n')
    with pytest.raises(HubConfigError, match="follow_latest"):
        load_hub_config(root)


# --------------------------------------------------------------------------- #
# `close_forge_writes_enabled` — the instance-level forge-write posture for closing.


@pytest.mark.unit
def test_close_forge_writes_enabled_defaults_to_true(tmp_path: Path) -> None:
    assert _hub_config(tmp_path).close_forge_writes_enabled is True


@pytest.mark.unit
def test_close_forge_writes_enabled_round_trips_through_to_toml_and_load(tmp_path: Path) -> None:
    config = _hub_config(tmp_path)
    config.config_path.write_text(config.to_toml())
    loaded = load_hub_config(config.root)
    assert loaded.close_forge_writes_enabled is True

    edited = dataclasses.replace(loaded, close_forge_writes_enabled=False)
    edited.config_path.write_text(edited.to_toml())
    reloaded = load_hub_config(edited.root)
    assert reloaded.close_forge_writes_enabled is False


@pytest.mark.unit
def test_close_forge_writes_enabled_absent_from_toml_defaults_to_true(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\n')
    assert load_hub_config(root).close_forge_writes_enabled is True


@pytest.mark.unit
@pytest.mark.parametrize("value", ['"true"', '"yes"', "1", "[]"])
def test_close_forge_writes_enabled_non_boolean_raises(tmp_path: Path, value: str) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///x"\nclose_forge_writes_enabled = {value}\n')
    with pytest.raises(HubConfigError, match="close_forge_writes_enabled"):
        load_hub_config(root)


# --------------------------------------------------------------------------- #
# `BZ_HUB_DB_URL` / `BZ_HUB_HOST` / `BZ_HUB_PORT` — load-time env overrides.


@pytest.mark.unit
def test_hub_db_url_env_overrides_toml_at_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///toml-value.db"\n')
    override = "postgresql+psycopg://blizzard:secret@localhost:5432/hub"
    monkeypatch.setenv(HUB_ENV_DB_URL, override)
    assert load_hub_config(root).db_url == override


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
    assert load_hub_config(root).host == "0.0.0.0"


@pytest.mark.unit
def test_hub_host_cli_flag_wins_over_env_at_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nhost = "127.0.0.1"\n')
    monkeypatch.setenv(HUB_ENV_HOST, "0.0.0.0")
    assert load_hub_config(root, host="10.0.0.5").host == "10.0.0.5"


@pytest.mark.unit
def test_hub_port_env_honored_at_load_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nport = 8421\n')
    monkeypatch.setenv(HUB_ENV_PORT, "9999")
    assert load_hub_config(root).port == 9999


@pytest.mark.unit
def test_hub_port_cli_flag_wins_over_env_at_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///x"\nport = 8421\n')
    monkeypatch.setenv(HUB_ENV_PORT, "9999")
    assert load_hub_config(root, port=1234).port == 1234


@pytest.mark.unit
def test_hub_config_byte_identical_when_env_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every variable unset leaves parsed values byte-identical."""
    for var in (HUB_ENV_DB_URL, HUB_ENV_HOST, HUB_ENV_PORT):
        monkeypatch.delenv(var, raising=False)
    root = tmp_path / "hub"
    root.mkdir()
    pg = "postgresql+psycopg://blizzard:secret@localhost:5432/hub"
    (root / "blizzard-hub.toml").write_text(f'db_url = "{pg}"\nhost = "0.0.0.0"\nport = 9001\n')
    config = load_hub_config(root)
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
        load_hub_config(root)


@pytest.mark.unit
def test_hub_db_url_honored_identically_by_host_and_migrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`blizzard hub host` and `blizzard hub migrate` both resolve through
    `HubConfig.load`/`scaffold` — the override needs no per-verb wiring."""
    from blizzard.hub.runtime import migration_runner

    root = tmp_path / "hub"
    init_hub(root)  # scaffolds + migrates the default sqlite store

    # Inside root: an override pointing elsewhere is exactly what the --dir isolation
    # guard exists to catch — see tests/test_config.py's own guard tests below.
    override_url = f"sqlite:///{root / 'override.db'}"
    monkeypatch.setenv(HUB_ENV_DB_URL, override_url)

    migrate_hub(root)  # resolves through HubConfig.load — migrates the overridden store
    config = load_hub_config(root)
    assert config.db_url == override_url
    assert (root / "override.db").exists()
    assert migration_runner(config).current_revision() is not None


# --------------------------------------------------------------------------- #
# The db_url --dir isolation guard.


@pytest.mark.unit
def test_db_url_outside_root_is_refused(tmp_path: Path) -> None:
    live_db = tmp_path / "live" / "hub.db"
    root = tmp_path / "copy"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///{live_db}"\n')

    with pytest.raises(HubConfigError, match=str(live_db)):
        load_hub_config(root)


@pytest.mark.unit
def test_db_url_outside_root_proceeds_with_allow_external_db(tmp_path: Path) -> None:
    live_db = tmp_path / "live" / "hub.db"
    root = tmp_path / "copy"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///{live_db}"\n')

    config = load_hub_config(root, allow_external_db=True)
    assert config.db_url == f"sqlite:///{live_db}"


@pytest.mark.unit
def test_db_url_absolute_but_inside_root_needs_no_flag(tmp_path: Path) -> None:
    # Acceptance: existing configs with an in-dir absolute db_url still work — the guard
    # triggers only on paths that resolve *outside* root.
    root = tmp_path / "hub"
    root.mkdir()
    in_dir_db = root / "hub.db"
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///{in_dir_db}"\n')

    config = load_hub_config(root)
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
        load_hub_config(root)

    config = load_hub_config(root, allow_external_db=True)
    assert config.db_url == f"sqlite:///{outside_db}"


@pytest.mark.unit
def test_db_url_relative_sqlite_path_bypasses_the_guard(tmp_path: Path) -> None:
    # A relative sqlite path resolves against the process cwd at open time, not root —
    # there is nothing for the --dir guard to compare it to.
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text('db_url = "sqlite:///relative.db"\n')
    assert load_hub_config(root).db_url == "sqlite:///relative.db"


@pytest.mark.unit
def test_postgres_db_url_bypasses_the_guard(tmp_path: Path) -> None:
    # A non-sqlite db_url is inherently external — it passes the --dir guard untouched.
    root = tmp_path / "hub"
    root.mkdir()
    pg = "postgresql+psycopg://blizzard:secret@elsewhere:5432/hub"
    (root / "blizzard-hub.toml").write_text(f'db_url = "{pg}"\n')
    assert load_hub_config(root).db_url == pg


@pytest.mark.unit
def test_fresh_scaffold_omits_db_url_from_to_toml(tmp_path: Path) -> None:
    # A fresh scaffold's db_url is the resolved default, which `to_toml`
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
    assert load_hub_config(root).db_url == HubConfig.default_db_url(root)


@pytest.mark.unit
def test_a_freshly_scaffolded_dir_copied_elsewhere_re_derives_its_own_db_url(tmp_path: Path) -> None:
    """`cp -r` a freshly-inited runtime dir and it is self-contained — the
    copy's db_url points into the copy, not back at the original, with no
    `--allow-external-db` needed."""
    import shutil

    original = tmp_path / "original"
    init_hub(original)

    copy_root = tmp_path / "copy"
    shutil.copytree(original, copy_root)

    copy_config = load_hub_config(copy_root)
    assert copy_config.db_url == HubConfig.default_db_url(copy_root)
    assert Path(copy_config.db_url.removeprefix("sqlite:///")).exists()


# --- the lane's byte ceilings -----------------------------------------


# --- the hub's own ingest ceilings ------------------------------------


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

    reloaded = load_hub_config(root)

    assert reloaded.transcripts.runner_daily_rate_max_bytes == 214748364800
    # The two left unset stay unset through the round trip rather than being frozen at
    # whatever the default happened to be when the file was written.
    assert reloaded.transcripts.record_max_bytes is None
    assert reloaded.transcripts.chunk_budget_max_bytes is None


@pytest.mark.unit
def test_the_hub_template_shows_each_ingest_ceiling_at_its_domain_default(tmp_path: Path) -> None:
    from blizzard.hub.config import HubConfig
    from blizzard.hub.domain.observability.transcripts import TranscriptCaps

    rendered = HubConfig(root=tmp_path, db_url=HubConfig.default_db_url(tmp_path)).to_toml()
    defaults = TranscriptCaps()

    assert f"# record_max_bytes = {defaults.record_max_bytes}\n" in rendered
    assert f"# chunk_budget_max_bytes = {defaults.chunk_budget_max_bytes}\n" in rendered
    assert f"# runner_daily_rate_max_bytes = {defaults.runner_daily_rate_max_bytes}\n" in rendered


@pytest.mark.unit
@pytest.mark.parametrize("key", ["record_max_bytes", "chunk_budget_max_bytes", "runner_daily_rate_max_bytes"])
@pytest.mark.parametrize("value", ["0", '"10485760"', "false"])
def test_a_hub_ingest_cap_refuses_a_non_positive_or_non_integer_value(tmp_path: Path, key: str, value: str) -> None:
    from blizzard.hub.config import ConfigError

    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f"[transcripts]\n{key} = {value}\n")

    with pytest.raises(ConfigError, match=key):
        load_hub_config(root)


@pytest.mark.unit
def test_the_configured_hub_caps_reach_the_wired_ingest_service(tmp_path: Path) -> None:
    """The resolution seam itself: a configured ceiling must reach the
    service, and an unconfigured one must fall back to the domain default rather than None."""
    from blizzard.hub.app import _transcript_caps
    from blizzard.hub.config import HubConfig, TranscriptCapsConfig
    from blizzard.hub.domain.observability.transcripts import TranscriptCaps

    resolved = _transcript_caps(
        HubConfig(
            root=tmp_path,
            db_url=HubConfig.default_db_url(tmp_path),
            transcripts=TranscriptCapsConfig(runner_daily_rate_max_bytes=214748364800),
        )
    )

    assert resolved.runner_daily_rate_max_bytes == 214748364800
    assert resolved.record_max_bytes == TranscriptCaps().record_max_bytes


# --------------------------------------------------------------------------- #
# `[[subscription]]` — declared provider subscriptions.


def _write_runner_config(root: Path, body: str) -> None:
    root.mkdir(exist_ok=True)
    (root / "blizzard-runner.toml").write_text('db_url = "sqlite://"\n' + body)


@pytest.mark.unit
def test_tracing_defaults_apply_when_the_table_is_absent(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "{HubConfig.default_db_url(root)}"\n')
    assert load_hub_config(root).tracing == TracingConfig(
        sweep_seconds=60, settle_seconds=300, batch_limit=200, max_lag_seconds=86400, replay_max_window=604800
    )


@pytest.mark.unit
def test_tracing_scaffold_renders_every_knob_commented_at_its_default(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    text = HubConfig.scaffold(root).to_toml()
    assert "[tracing]\n" in text
    assert "worker_program_services" not in text
    for key, value in dataclasses.asdict(TracingConfig()).items():
        if key != "worker_program_services":
            assert f"# {key} = {toml_literal(value)}\n" in text
    (root / "blizzard-hub.toml").write_text(text)
    assert load_hub_config(root).tracing == TracingConfig()


@pytest.mark.unit
def test_tracing_overrides_round_trip_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    tracing = TracingConfig(
        sweep_seconds=5,
        settle_seconds=0,
        batch_limit=10,
        max_lag_seconds=3600,
        replay_max_window=7200,
        platform=True,
        platform_sample_ratio=0.25,
    )
    config = dataclasses.replace(HubConfig.scaffold(root), tracing=tracing)
    (root / "blizzard-hub.toml").write_text(config.to_toml())
    assert load_hub_config(root).tracing == tracing


@pytest.mark.unit
def test_harness_telemetry_round_trips_and_refuses_a_non_boolean(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    assert TracingConfig().harness_telemetry is False
    assert "\n# harness_telemetry = false\n" in HubConfig.scaffold(root).to_toml()
    config = dataclasses.replace(HubConfig.scaffold(root), tracing=TracingConfig(harness_telemetry=True))
    text = config.to_toml()
    assert "\nharness_telemetry = true\n" in text
    (root / "blizzard-hub.toml").write_text(text)
    assert load_hub_config(root).tracing.harness_telemetry is True
    (root / "blizzard-hub.toml").write_text(
        f'db_url = "{HubConfig.default_db_url(root)}"\n\n[tracing]\nharness_telemetry = "yes"\n'
    )
    with pytest.raises(HubConfigError, match=re.escape("tracing.harness_telemetry must be true or false")):
        load_hub_config(root)


@pytest.mark.unit
def test_worker_program_services_round_trip_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    tracing = TracingConfig(worker_program_services={"winter_cli": "winter-blizzard", "a.b": "svc"})
    config = dataclasses.replace(HubConfig.scaffold(root), tracing=tracing)
    (root / "blizzard-hub.toml").write_text(config.to_toml())
    assert load_hub_config(root).tracing == tracing


@pytest.mark.unit
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
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        f'db_url = "{HubConfig.default_db_url(root)}"\n\n[tracing.worker_program_services]\n{entry}\n'
    )
    with pytest.raises(HubConfigError, match=re.escape(complaint)):
        load_hub_config(root)


@pytest.mark.unit
def test_worker_program_services_refuses_a_non_table(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        f'db_url = "{HubConfig.default_db_url(root)}"\n\n[tracing]\nworker_program_services = "x"\n'
    )
    with pytest.raises(HubConfigError, match="must be a table"):
        load_hub_config(root)


@pytest.mark.unit
def test_reserved_service_names_cover_each_daemons_own_name() -> None:
    from blizzard.foundation.trace_export.config import RESERVED_SERVICE_NAMES
    from blizzard.hub.domain.observability.tracing.attributes import CHUNK_SERVICE_NAME as CHUNK
    from blizzard.hub.domain.observability.tracing.attributes import DEFAULT_SERVICE_NAME as HUB
    from blizzard.runner.tracing.attributes import DEFAULT_SERVICE_NAME as RUNNER

    assert {HUB, RUNNER, CHUNK} <= RESERVED_SERVICE_NAMES


@pytest.mark.unit
def test_tracing_parses_from_a_hand_written_table(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        f'db_url = "{HubConfig.default_db_url(root)}"\n\n[tracing]\nsettle_seconds = 0\nbatch_limit = 50\n'
    )
    tracing = load_hub_config(root).tracing
    assert tracing.settle_seconds == 0
    assert tracing.batch_limit == 50
    assert tracing.sweep_seconds == 60
    assert tracing.platform is False


@pytest.mark.unit
def test_platform_tracing_parses_from_a_hand_written_table(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        f'db_url = "{HubConfig.default_db_url(root)}"\n\n[tracing]\nplatform = true\nplatform_sample_ratio = 0\n'
    )
    tracing = load_hub_config(root).tracing
    assert tracing.platform is True
    assert tracing.platform_sample_ratio == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("sweep_seconds", "0"),
        ("settle_seconds", "-1"),
        ("batch_limit", "0"),
        ("max_lag_seconds", '"1d"'),
        ("replay_max_window", "true"),
        ("sweep_seconds", "1.5"),
        ("platform", '"yes"'),
        ("platform", "1"),
        ("platform_sample_ratio", "1.5"),
        ("platform_sample_ratio", "-0.1"),
        ("platform_sample_ratio", "true"),
        ("platform_sample_ratio", '"half"'),
    ],
)
def test_tracing_rejects_an_invalid_knob(tmp_path: Path, key: str, value: str) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(
        f'db_url = "{HubConfig.default_db_url(root)}"\n\n[tracing]\n{key} = {value}\n'
    )
    with pytest.raises(HubConfigError, match=f"tracing.{key}"):
        load_hub_config(root)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "minimum", "bound"),
    [
        ("sweep_seconds", 1, "positive"),
        ("settle_seconds", 0, "non-negative"),
        ("batch_limit", 1, "positive"),
        ("max_lag_seconds", 1, "positive"),
        ("replay_max_window", 1, "positive"),
    ],
)
def test_tracing_integer_knobs_enforce_their_individual_minima(key: str, minimum: int, bound: str) -> None:
    assert getattr(TracingConfig.of(ConfigView.unchecked({key: minimum}), HubConfigError), key) == minimum
    with pytest.raises(HubConfigError) as error:
        TracingConfig.of(ConfigView.unchecked({key: minimum - 1}), HubConfigError)
    assert str(error.value) == f"tracing.{key} must be {bound}, got {minimum - 1!r}"


def _hub_egress_root(tmp_path: Path, table: str = "") -> Path:
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "{HubConfig.default_db_url(root)}"\n{table}')
    return root


@pytest.mark.unit
def test_egress_is_off_with_working_defaults_when_the_table_is_absent(tmp_path: Path) -> None:
    egress = load_hub_config(_hub_egress_root(tmp_path)).egress
    assert egress == EgressConfig()
    assert egress.directory is None
    assert egress.format == "ndjson"
    assert egress.datasets == ("steps", "invocations", "events")
    assert egress.extractor_versions == "current"
    assert (egress.settle_seconds, egress.min_free_bytes) == (300, 1024**3)


@pytest.mark.unit
def test_egress_scaffold_leaves_the_switch_off_and_every_other_knob_commented_at_its_default(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    text = HubConfig.scaffold(root).to_toml()
    assert "[egress]\n" in text
    assert '# directory = "' in text
    for key in (
        "format",
        "datasets",
        "sweep_seconds",
        "settle_seconds",
        "batch_limit",
        "max_rows_per_file",
        "file_paths",
        "path_key_env",
        "extractor_versions",
    ):
        assert f"# {key} = " in text
    assert '# datasets = ["steps", "invocations", "events"]\n' in text
    assert '# extractor_versions = "current"\n' in text
    assert '# file_paths = "relative"\n' in text
    assert '# path_key_env = "BZ_EGRESS_PATH_KEY"\n' in text
    (root / "blizzard-hub.toml").write_text(text)
    assert load_hub_config(root).egress == EgressConfig()


@pytest.mark.unit
def test_egress_overrides_round_trip_through_to_toml_and_load(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    egress = EgressConfig(
        directory=tmp_path / "out",
        format="parquet",
        datasets=("invocations",),
        sweep_seconds=5,
        settle_seconds=0,
        batch_limit=10,
        max_rows_per_file=20,
        min_free_bytes=0,
        backfill_max_window=3600,
        extractor_versions="all",
    )
    config = dataclasses.replace(HubConfig.scaffold(root), egress=egress)
    (root / "blizzard-hub.toml").write_text(config.to_toml())
    assert load_hub_config(root).egress == egress


@pytest.mark.unit
def test_egress_parses_from_a_hand_written_table_and_orders_datasets_as_a_pass_writes_them(tmp_path: Path) -> None:
    root = _hub_egress_root(
        tmp_path,
        '\n[egress]\ndirectory = "/srv/egress"\ndatasets = ["events", "invocations", "steps"]\nsettle_seconds = 0\n'
        'extractor_versions = "all"\n',
    )
    egress = load_hub_config(root).egress
    assert egress.directory == Path("/srv/egress")
    assert egress.datasets == ("steps", "invocations", "events")
    assert egress.extractor_versions == "all"
    assert egress.settle_seconds == 0
    assert egress.sweep_seconds == 60


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["relative", "hashed", "absolute", "omit"])
def test_egress_file_paths_and_key_variable_parse_and_round_trip(tmp_path: Path, mode: str) -> None:
    root = _hub_egress_root(tmp_path, f'\n[egress]\nfile_paths = "{mode}"\npath_key_env = "MY_KEY"\n')
    egress = load_hub_config(root).egress
    assert (egress.file_paths, egress.path_key_env) == (mode, "MY_KEY")
    (root / "blizzard-hub.toml").write_text(dataclasses.replace(HubConfig.scaffold(root), egress=egress).to_toml())
    assert load_hub_config(root).egress == egress


@pytest.mark.unit
def test_egress_file_paths_default_to_relative_with_the_conventional_key_variable() -> None:
    egress = EgressConfig()
    assert (egress.file_paths, egress.path_key_env) == ("relative", "BZ_EGRESS_PATH_KEY")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("directory", '""'),
        ("directory", "3"),
        ("format", '"csv"'),
        ("datasets", '["traces"]'),
        ("datasets", "[]"),
        ("datasets", '"steps"'),
        ("sweep_seconds", "0"),
        ("settle_seconds", "-1"),
        ("batch_limit", "0"),
        ("max_rows_per_file", "0"),
        ("min_free_bytes", "-1"),
        ("backfill_max_window", "0"),
        ("batch_limit", "true"),
        ("sweep_seconds", "1.5"),
        ("file_paths", '"bogus"'),
        ("file_paths", "3"),
        ("path_key_env", '""'),
        ("path_key_env", "3"),
        ("extractor_versions", '"newest"'),
        ("extractor_versions", "1"),
    ],
)
def test_egress_rejects_an_invalid_key(tmp_path: Path, key: str, value: str) -> None:
    root = _hub_egress_root(tmp_path, f"\n[egress]\n{key} = {value}\n")
    with pytest.raises(HubConfigError, match=f"egress.{key}"):
        load_hub_config(root)


@pytest.mark.unit
@pytest.mark.parametrize("key", ["sweep_seconds", "batch_limit", "max_rows_per_file", "backfill_max_window"])
def test_egress_positive_keys_accept_one_and_refuse_zero(tmp_path: Path, key: str) -> None:
    assert getattr(parse_egress_config(ConfigView.unchecked({key: 1})), key) == 1
    with pytest.raises(HubConfigError) as error:
        parse_egress_config(ConfigView.unchecked({key: 0}))
    assert str(error.value) == f"egress.{key} must be positive, got 0"


@pytest.mark.unit
def test_egress_file_paths_refuses_a_non_string_naming_every_mode() -> None:
    with pytest.raises(HubConfigError) as error:
        parse_egress_config(ConfigView.unchecked({"file_paths": 3}))
    assert str(error.value) == "egress.file_paths must be one of relative, hashed, absolute, omit, got 3"


@pytest.mark.unit
def test_egress_header_comment_names_the_file_path_and_key_variable_knobs() -> None:
    header = render_egress_toml(EgressConfig())[0]
    assert "# batch_limit and max_rows_per_file (rows) and min_free_bytes. file_paths decides what a file\n" in header
    assert "# read's path leaves as (relative, hashed, absolute or omit); path_key_env names the\n" in header
    assert "# environment variable holding the key for hashed paths. extractor_versions is current or\n" in header


_LEGACY_SHAPED_RUNNER_TOML = """db_url = "sqlite:///legacy.db"
harness_binary = "/opt/claude"
harness_permission_mode = "acceptEdits"
worker_settings_path = "/w/worker-settings.json"
claude_code_credentials_path = "/c/credentials.json"

[models.aliases]
"blizzard:frontier" = "opus"

[effort.aliases]
"blizzard:deep" = "high"

[opencode]
enabled = false
binary = "/opt/opencode"
worker_config_path = "/w/opencode-worker-config.json"
auth_path = "/a/auth.json"

[opencode.models.aliases]
"blizzard:frontier" = "openai/gpt"

[opencode.effort.aliases]
"high" = "max"
"""
