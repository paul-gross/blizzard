"""Both daemons' registries against the lifetime model: the variables they declare, the new key, and the constants
that never become keys."""

from __future__ import annotations

import importlib

import pytest

from blizzard.foundation.config_keys import ARRAY_OF_TABLES, NUMBER, TABLE, ConfigKey, ConfigRegistry, Lifetime
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE
from blizzard.hub.config_defaults import ENV_DB_URL, ENV_HOST, ENV_PORT
from blizzard.hub.config_keys import HUB_REGISTRY, WORK_SOURCE_RECORDS
from blizzard.runner.config import DEFAULT_TICK_SECONDS
from blizzard.runner.config_keys import HOST_REGISTRY

_REGISTRIES = [HUB_REGISTRY, HOST_REGISTRY]

#: Constants that carry no lifetime: they stay in code and never become a key. Each is ``(module, name)``.
_PINNED: tuple[tuple[str, str], ...] = (
    # Cryptographic sizes.
    ("blizzard.foundation.secrets.internal.key_material", "KEY_BYTES"),
    ("blizzard.foundation.secrets.internal.aes_gcm", "_NONCE_BYTES"),
    # Wire-format limits.
    ("blizzard.wire.release", "RELEASE_REASON_MAX_LENGTH"),
    # Slug and name lengths.
    ("blizzard.hub.domain.kernel.slugs", "SLUG_MAX"),
    # GitHub's own limits.
    ("blizzard.hub.forge.internal.github_landing", "_PAGE_SIZE"),
    ("blizzard.hub.forge.internal.github_landing", "_PAGE_MAX"),
    ("blizzard.hub.forge.internal.github_landing", "_COMPARE_FILES_MAX"),
    # The admitted harness version ranges.
    ("blizzard.runner.harness.claude_code.health", "ADMITTED_CLAUDE_CODE_RANGE"),
    ("blizzard.runner.harness.opencode.compatibility.probe", "ADMITTED_OPENCODE_RANGE"),
    # The Anthropic credential renewal lead inside Claude Code's own refresh threshold.
    ("blizzard.runner.subscriptions.internal.anthropic_credential_renewer", "_RENEWAL_LEAD_WINDOW"),
    # The usage-limit retention window above one week.
    ("blizzard.runner.store.internal.usage_limit_rows", "USAGE_LIMIT_RETENTION_WINDOW"),
)


def _key(registry: ConfigRegistry, path: str) -> ConfigKey:
    key = registry.key(path)
    assert key is not None, path
    return key


@pytest.mark.parametrize("registry", _REGISTRIES, ids=[r.name for r in _REGISTRIES])
def test_every_value_key_declares_a_lifetime_and_no_grouping_key_does(registry: ConfigRegistry) -> None:
    for key in registry.keys:
        assert (key.lifetime is None) == (key.type in (TABLE, ARRAY_OF_TABLES)), key.path


@pytest.mark.parametrize("registry", _REGISTRIES, ids=[r.name for r in _REGISTRIES])
def test_the_variables_declared_are_the_ones_the_loaders_read(registry: ConfigRegistry) -> None:
    expected = {
        HUB_REGISTRY.name: {"host": ENV_HOST, "port": ENV_PORT, "db_url": ENV_DB_URL},
        HOST_REGISTRY.name: {"host_token": HOST_TOKEN_VARIABLE},
    }[registry.name]
    assert {key.path: key.env for key in registry.keys if key.env is not None} == expected


def test_tick_seconds_is_a_pass_number_defaulting_to_the_tick_default() -> None:
    assert _key(HOST_REGISTRY, "tick_seconds") == ConfigKey(
        "tick_seconds", NUMBER, default=DEFAULT_TICK_SECONDS, lifetime=Lifetime.PASS
    )
    assert DEFAULT_TICK_SECONDS == 30.0


def test_work_source_is_a_deprecated_grouping_key_replaced_by_its_records_with_boot_children() -> None:
    work_source = _key(HUB_REGISTRY, "work_source")
    assert (work_source.deprecated, work_source.replaced_by, work_source.lifetime) == (True, WORK_SOURCE_RECORDS, None)
    children = [key for key in HUB_REGISTRY.keys if key.path.startswith("work_source[].")]
    assert children
    assert {key.lifetime for key in children} == {Lifetime.BOOT}


def test_a_prefixed_harness_key_keeps_its_lifetime() -> None:
    assert _key(HOST_REGISTRY, "runner[].claude_code.binary").lifetime is Lifetime.SESSION
    assert _key(HOST_REGISTRY, "runner[].claude_code.enabled").lifetime is Lifetime.PASS


@pytest.mark.parametrize(("module", "name"), _PINNED, ids=[name for _, name in _PINNED])
def test_a_pinned_constant_resolves_and_no_registry_declares_a_key_for_it(module: str, name: str) -> None:
    assert hasattr(importlib.import_module(module), name), f"{module}.{name} no longer resolves"
    segment = name.lstrip("_").lower()
    for registry in _REGISTRIES:
        named = [key.path for key in registry.keys if segment in key.path.replace("[]", "").split(".")]
        assert named == [], f"{registry.name} declares a key for the pinned {name}"
