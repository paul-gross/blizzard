"""The hub-wide jobs that are not sweeps act in every open tenant (component tier): the boot steps, proven on
``build_hosted_app`` itself, key rotation, and the legacy import's one tenant — over a store holding two."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import get_type_hints

import pytest
import sqlalchemy as sa
from click.testing import CliRunner
from sqlalchemy import Engine

from blizzard.cli.main import blizzard
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import app as hub_app
from blizzard.hub.composition import TenantStores, build_config_authoring, build_hub_core
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.carry_over import ImportStatus
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.forges import ForgeKindOffers
from blizzard.hub.domain.config.secrets import (
    IResealSecretRepository,
    Reseal,
    SealedSecret,
    SecretName,
    SecretRotationConflict,
)
from blizzard.hub.domain.config.work_sources import WorkSourceDraft, WorkSourceSecretUnavailable
from blizzard.hub.secrets import (
    ENV_SECRET_KEY,
    ENV_SECRET_KEY_PREVIOUS,
    StoreSecretReader,
    hub_key_provider,
    secret_cipher,
    secret_keys_dir,
)
from blizzard.hub.secrets.rotation import rotate_keys
from blizzard.hub.store import schema
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.config_inputs import build_hosted_hub_app, init_hub
from tests.support import (
    OP,
    add_tenant,
    close_tenant,
    default_project,
    default_tenant,
    hub_scoped,
    scoped_connections,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 4, tzinfo=UTC)
_BLOCK = """
[[work_source]]
name = "blizzard"
provider = "github"
repo = "acme/blizzard"
token_env = "BZ_SHARED_TOKEN"
"""
_ENVIRON = {"BZ_SHARED_TOKEN": "shared-tok"}
_SOURCE = WorkSourceDraft(provider="github", locator="acme/demo", annotate=False, secret="gh")


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)
    monkeypatch.delenv(ENV_SECRET_KEY_PREVIOUS, raising=False)


@dataclass(frozen=True)
class _Hub:
    config: HubConfig
    engine: Engine
    first: TenantId
    second: TenantId

    def stores(self, tenant: TenantId) -> TenantStores:
        return build_hub_core(self.engine, clock=FixedClock(_NOW)).open(StoreScope(tenant))

    def authoring(self, tenant: TenantId) -> ConfigAuthoring:
        keys = hub_key_provider({}, data_dir=self.config.data_dir)
        return build_config_authoring(
            self.stores(tenant), cipher=secret_cipher(keys), events=None, offers=ForgeKindOffers()
        )

    def reveal(self, tenant: TenantId, name: str) -> str:
        """``name``'s value in ``tenant``, opened as a running hub opens it."""
        store = self.stores(tenant).secrets
        keys = hub_key_provider({}, data_dir=self.config.data_dir)
        reader = StoreSecretReader(catalog=store, sealed=store, cipher=secret_cipher(keys))
        return reader.reveal(SecretName.parse(name)).expose()

    def key_ids(self, tenant: TenantId) -> set[str]:
        return self.stores(tenant).secrets.key_ids_in_use()

    def meta(self) -> dict[str, object]:
        return json.loads((secret_keys_dir(self.config.data_dir) / "meta.json").read_text())


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[_Hub]:
    """An initialized hub runtime whose store holds its carried-over tenant and a second one."""
    config = init_hub(tmp_path / "hub")
    engine = create_engine_from_url(config.db_url)
    first = default_tenant(engine)
    second = add_tenant(engine, "second")
    yield _Hub(config=config, engine=engine, first=first, second=second)
    engine.dispose()


def _build(config: HubConfig) -> None:
    build_hosted_hub_app(config).state.engine.dispose()


def _orphan_a_turn(hub: _Hub, tenant: TenantId, suffix: str) -> None:
    """Leave one open turn in ``tenant``, as a turn the previous process died in does."""
    conns = scoped_connections(hub.engine, tenant)
    with conns.write("orphan_a_turn") as conn:
        conn.execute(
            conns.insert(schema.graphs).values(
                graph_id=f"gr_{suffix}", name="g", entry_node_id="nd_1", definition_yaml="", created_at=_NOW
            )
        )
        conn.execute(
            conns.insert(schema.chunks).values(
                chunk_id=f"ch_{suffix}",
                graph_id=f"gr_{suffix}",
                minted_at=_NOW,
                model="m",
                project_id=default_project(hub.engine, tenant),
            )
        )
        conn.execute(
            conns.insert(schema.flow_submissions).values(
                submission_id=f"fsub_{suffix}",
                chunk_id=f"ch_{suffix}",
                node_id="nd_1",
                epoch=1,
                generation=0,
                arrival=1,
                submitted_at=_NOW,
            )
        )
        conn.execute(
            conns.insert(schema.hub_turns).values(
                turn_id=f"htn_{suffix}", submission_id=f"fsub_{suffix}", started_at=_NOW
            )
        )


def _open_turns(hub: _Hub, tenant: TenantId) -> int:
    turns = schema.hub_turns
    with hub.engine.connect() as conn:
        return conn.execute(
            hub_scoped(
                sa.select(sa.func.count())
                .select_from(turns)
                .where(turns.c.tenant_id == tenant, turns.c.ended_at.is_(None))
            )
        ).scalar_one()


def test_a_hub_holding_two_open_tenants_boots_and_ends_each_tenants_open_turn(hub: _Hub) -> None:
    _orphan_a_turn(hub, hub.first, "first")
    _orphan_a_turn(hub, hub.second, "second")

    _build(hub.config)

    assert (_open_turns(hub, hub.first), _open_turns(hub, hub.second)) == (0, 0)


def test_boot_acts_in_no_closed_tenant(hub: _Hub) -> None:
    closed = add_tenant(hub.engine, "closed")
    _orphan_a_turn(hub, closed, "closed")
    close_tenant(hub.engine, closed)
    _orphan_a_turn(hub, hub.first, "first")

    _build(hub.config)

    assert _open_turns(hub, hub.first) == 0
    assert _open_turns(hub, closed) == 1


def test_the_hub_refuses_to_start_while_any_tenants_secret_has_no_generation(hub: _Hub, tmp_path: Path) -> None:
    hub.authoring(hub.second).create_secret(SecretName.parse("gh"), "tok", OP)
    (key_id,) = hub.key_ids(hub.second)
    (secret_keys_dir(hub.config.data_dir) / f"{key_id}.key").rename(tmp_path / "moved-away.key")

    with pytest.raises(ConfigError, match=key_id):
        _build(hub.config)


def test_rotation_reseals_every_open_tenant_under_one_new_generation(hub: _Hub) -> None:
    hub.authoring(hub.first).create_secret(SecretName.parse("gh"), "tok-first", OP)
    hub.authoring(hub.second).create_secret(SecretName.parse("gh"), "tok-second", OP)
    old = hub.meta()["current"]

    result = hub_app.rotate_secret_keys(hub.config, {})

    assert result.resealed == 2
    assert hub.key_ids(hub.first) == hub.key_ids(hub.second) == {result.key_id}
    assert hub.meta() == {"current": result.key_id, "previous": old}
    assert (hub.reveal(hub.first, "gh"), hub.reveal(hub.second, "gh")) == ("tok-first", "tok-second")


class _Racing:
    """A tenant's secrets whose re-seal loses a race with a concurrent replace."""

    def __init__(self, inner: IResealSecretRepository) -> None:
        self._inner = inner

    def get_sealed(self, name: str) -> SealedSecret | None:
        return self._inner.get_sealed(name)

    def list_sealed(self) -> list[SealedSecret]:
        return self._inner.list_sealed()

    def reseal(self, changes: list[Reseal]) -> None:
        raise SecretRotationConflict("gh")


def test_a_tenant_that_fails_to_reseal_leaves_the_old_key_current_and_a_rerun_moves_every_tenant(
    hub: _Hub,
) -> None:
    hub.authoring(hub.first).create_secret(SecretName.parse("gh"), "tok-first", OP)
    hub.authoring(hub.second).create_secret(SecretName.parse("gh"), "tok-second", OP)
    before = hub.meta()
    old = str(before["current"])
    first, second = hub.stores(hub.first).secrets, hub.stores(hub.second).secrets

    with pytest.raises(SecretRotationConflict):
        rotate_keys([first, _Racing(second)], hub_key_provider({}, data_dir=hub.config.data_dir), secret_cipher)

    assert hub.meta() == before
    assert hub.key_ids(hub.second) == {old}
    assert hub.reveal(hub.first, "gh") == "tok-first"  # re-sealed into the minted, not yet current, generation

    result = rotate_keys([first, second], hub_key_provider({}, data_dir=hub.config.data_dir), secret_cipher)

    assert hub.key_ids(hub.first) == hub.key_ids(hub.second) == {result.key_id}
    assert (hub.reveal(hub.first, "gh"), hub.reveal(hub.second, "gh")) == ("tok-first", "tok-second")


def _write_legacy_block(hub: _Hub) -> None:
    hub.config.config_path.write_text(hub.config.config_path.read_text() + _BLOCK)


def test_the_import_refuses_naming_every_tenant_when_the_hub_holds_several_and_none_is_named(hub: _Hub) -> None:
    _write_legacy_block(hub)

    with pytest.raises(ConfigError, match="--tenant") as refused:
        hub_app.import_legacy_config(hub.config, _ENVIRON)

    assert hub.first in str(refused.value)
    assert hub.second in str(refused.value)
    assert not hub.stores(hub.first).config_import.recorded()


def test_the_verb_imports_into_the_tenant_it_names_and_writes_nothing_in_another(
    hub: _Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_legacy_block(hub)
    monkeypatch.setenv("BZ_SHARED_TOKEN", "shared-tok")

    result = CliRunner().invoke(
        blizzard,
        ["hub", "config", "import-legacy", "--dir", str(hub.config.root), "--tenant", hub.second],
    )

    assert result.exit_code == 0, result.output
    second, first = hub.stores(hub.second), hub.stores(hub.first)
    assert [record.name for record in second.work_source_records.list_all(include_retired=True)] == ["blizzard"]
    assert [secret.name for secret in second.secrets.list_all()] == ["bz-shared-token"]
    assert second.config_import.recorded()
    assert first.work_source_records.list_all(include_retired=True) == []
    assert first.secrets.list_all() == []
    again = hub_app.import_legacy_config(hub.config, _ENVIRON, tenant=hub.first)
    assert again.status is ImportStatus.ALREADY_IMPORTED  # the import is the hub's, carried into one tenant
    assert first.work_source_records.list_all(include_retired=True) == []


def test_the_import_refuses_a_tenant_that_is_not_open(hub: _Hub) -> None:
    _write_legacy_block(hub)

    with pytest.raises(ConfigError, match="ten_unknown"):
        hub_app.import_legacy_config(hub.config, _ENVIRON, tenant=TenantId("ten_unknown"))


def test_a_hub_whose_import_one_tenant_recorded_refuses_to_start_naming_the_keys_that_remain(hub: _Hub) -> None:
    _write_legacy_block(hub)
    hub_app.import_legacy_config(hub.config, _ENVIRON, tenant=hub.second)

    with pytest.raises(ConfigError, match="was imported, but these keys remain"):
        _build(hub.config)


def test_a_work_source_cannot_name_a_secret_only_another_tenant_holds(hub: _Hub) -> None:
    hub.authoring(hub.first).create_secret(SecretName.parse("gh"), "tok-first", OP)

    with pytest.raises(WorkSourceSecretUnavailable):
        hub.authoring(hub.second).create_work_source_naming("demo", None, _SOURCE, ForgeChoice(), OP)

    assert hub.stores(hub.second).work_source_records.list_all(include_retired=True) == []


def _setting_names(model: type) -> Iterator[str]:
    hints = get_type_hints(model)
    for setting in dataclasses.fields(model):
        yield setting.name
        kind = hints[setting.name]
        if isinstance(kind, type) and dataclasses.is_dataclass(kind):
            yield from _setting_names(kind)


def test_no_startup_setting_varies_by_tenant() -> None:
    names = set(_setting_names(HubConfig))

    assert {"auth", "mode", "route_token_mode", "produces_mode"} <= names
    assert not [name for name in names if "tenant" in name]
