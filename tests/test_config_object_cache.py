"""``ConfigObjectCache`` over the composition's revisions read and secret reader (component tier).

Records are written through ``ConfigAuthoring`` against a migrated-to-head sqlite store; the
built object is a stand-in client holding the record's fields and its revealed token."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import LiveConfig, build_hub_core, build_live_config
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.domain.config.work_sources import WorkSourceEdit, WorkSourceFields
from blizzard.hub.runtime import migration_runner
from blizzard.hub.secrets import hub_key_provider
from tests.support import OP, config_authoring

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
_FIELDS = WorkSourceFields(
    provider="github", locator="acme/demo", api_base=None, web_base=None, annotate=False, secret="gh"
)


@dataclass
class _Client:
    locator: str
    annotate: bool
    token: str
    closed: bool = field(default=False)

    def close(self) -> None:
        self.closed = True


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
        self.engine = create_engine_from_url(db_url)
        self.keys = hub_key_provider({}, data_dir=tmp_path / "data")
        clock = FixedClock(_NOW)
        self.core = build_hub_core(self.engine, clock=clock)
        self.authoring = config_authoring(self.engine, keys=self.keys, clock=clock)
        self.authoring.create_secret(SecretName.parse("gh"), "tok-a", OP)
        self.authoring.create_work_source("demo", _FIELDS, OP)
        self.live = self.second_process()
        self.builds = 0

    def second_process(self) -> LiveConfig:
        return build_live_config(self.core, secret_keys=self.keys)

    def client(self, live: LiveConfig | None = None) -> _Client | None:
        live = live or self.live

        def build() -> _Client:
            self.builds += 1
            record = self.core.work_source_records.get("demo")
            assert record is not None and record.fields.secret is not None
            token = live.secrets.reveal(SecretName.parse(record.fields.secret)).expose()
            return _Client(record.fields.locator, record.fields.annotate, token)

        return live.objects.get(RecordKind.WORK_SOURCE, "demo", build)

    def source(self):  # type: ignore[no-untyped-def]
        record = self.core.work_source_records.get("demo")
        assert record is not None
        return record


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_a_hit_reuses_the_built_object(world: _World) -> None:
    first = world.client()
    assert world.client() is first
    assert world.builds == 1
    assert first is not None and not first.closed


def test_a_record_edit_rebuilds_and_sets_the_replaced_object_aside(world: _World) -> None:
    first = world.client()
    world.authoring.edit_work_source(world.source(), WorkSourceEdit(annotate=True), OP)

    second = world.client()

    assert first is not None and not first.closed
    assert second is not None and second.annotate and not second.closed


def test_a_replaced_object_is_closed_by_the_replacement_after_next(world: _World) -> None:
    first = world.client()
    world.authoring.edit_work_source(world.source(), WorkSourceEdit(annotate=True), OP)
    world.client()
    assert first is not None and not first.closed
    world.authoring.edit_work_source(world.source(), WorkSourceEdit(annotate=False), OP)

    world.client()

    assert first.closed


def test_a_retire_rebuilds_and_sets_the_replaced_object_aside(world: _World) -> None:
    first = world.client()
    world.authoring.retire_work_source(world.source(), OP)

    second = world.client()

    assert first is not None and not first.closed
    assert second is not None and second is not first


def test_a_secret_replace_rebuilds_with_the_new_value(world: _World) -> None:
    first = world.client()
    secret = world.core.secrets.get("gh")
    assert secret is not None
    world.authoring.replace_secret(secret, "tok-b", OP)

    second = world.client()

    assert first is not None and not first.closed and first.token == "tok-a"
    assert second is not None and second.token == "tok-b"


def test_a_cold_cache_and_a_second_cache_build_equivalent_objects(world: _World) -> None:
    world.client()
    world.authoring.edit_work_source(world.source(), WorkSourceEdit(locator="acme/moved"), OP)
    warm = world.client()

    other = world.client(world.second_process())

    assert warm is not None and other is not None
    assert (other.locator, other.annotate, other.token) == (warm.locator, warm.annotate, warm.token)


def test_an_unknown_record_builds_nothing(world: _World) -> None:
    assert world.live.objects.get(RecordKind.REPOSITORY, "absent", lambda: _Client("x", False, "t")) is None
