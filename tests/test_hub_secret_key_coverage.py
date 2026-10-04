"""The hub refuses to start when a stored secret's key generation is missing (component tier).

Proven on ``build_hosted_app`` itself — ``tests/support.py::build_hub`` bypasses it —
the ``tests/test_hub_platform_tracing.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.domain.secrets import SecretAuthoring, SecretName, SecretValue
from blizzard.hub.secrets import (
    ENV_SECRET_KEY,
    ENV_SECRET_KEY_PREVIOUS,
    hub_key_provider,
    secret_cipher,
    secret_keys_dir,
)
from blizzard.hub.store.internal.secret_store import SecretStore
from tests.support import hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 4, tzinfo=UTC)
_ENV_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)
    monkeypatch.delenv(ENV_SECRET_KEY_PREVIOUS, raising=False)


def _seed_secret(config: HubConfig) -> str:
    """Seal one secret under the runtime's current generation; returns that key id."""
    keys = hub_key_provider({}, data_dir=config.data_dir)
    engine = create_engine_from_url(config.db_url)
    try:
        store = SecretStore(hub_store_connections(engine))
        SecretAuthoring(secrets=store, cipher=secret_cipher(keys), clock=FixedClock(_NOW)).create(
            SecretName.parse("gh"), SecretValue("tok"), by="op"
        )
    finally:
        engine.dispose()
    return keys.current().key_id


def _build(config: HubConfig) -> None:
    hub_app.build_hosted_app(config).state.engine.dispose()


def test_init_mints_the_first_key_generation(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")

    meta = secret_keys_dir(config.data_dir) / "meta.json"
    assert meta.is_file()


def test_init_mints_nothing_under_the_env_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_SECRET_KEY, _ENV_KEY)

    config = hub_runtime.init_environment(tmp_path / "hub")

    assert not secret_keys_dir(config.data_dir).exists()


def test_the_hub_starts_when_every_row_is_covered(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    _seed_secret(config)

    _build(config)


def test_the_hub_refuses_to_start_naming_a_missing_generation(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    key_id = _seed_secret(config)
    (secret_keys_dir(config.data_dir) / f"{key_id}.key").rename(tmp_path / "moved-away.key")

    with pytest.raises(ConfigError, match=key_id):
        _build(config)


def test_a_retired_row_still_needs_its_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    key_id = _seed_secret(config)
    engine = create_engine_from_url(config.db_url)
    try:
        SecretStore(hub_store_connections(engine)).record_lifecycle("gh", retired=True, at=_NOW, by="op")
    finally:
        engine.dispose()
    monkeypatch.setenv(ENV_SECRET_KEY, _ENV_KEY)

    with pytest.raises(ConfigError, match=key_id):
        _build(config)
