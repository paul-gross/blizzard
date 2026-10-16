"""The identity row records its tenant: beside the runner's id it keeps the tenant the hub answered
with, for display. A store written before tenants gains the two columns empty and fills them at the
runner's first contact after the upgrade, with no configuration change and nothing else touched; no
other table carries a tenant."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.lifecycle.identity_check import IdentityCheck
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.store import schema as runner_schema
from tests.migration_support_runner_identity_tenant import _NOW, _REGISTERED, _RUNNER, _ctx, _tenant_hub
from tests.runner_fakes import (
    make_store,
    migrated_store_at,
)

pytestmark = [pytest.mark.component]


def test_no_table_but_the_identity_rows_carries_a_tenant(tmp_path: Path) -> None:
    """One store, one tenant: nothing keys or filters on the tenant, in the declared schema or in a store migrated to
    head — only the runner's and the host's identity rows name it."""
    declared = {t.name for t in runner_schema.metadata.sorted_tables if any("tenant" in c.name for c in t.columns)}
    assert declared == {"runner_identity", "host_identity"}

    url = f"sqlite:///{tmp_path / 'store.db'}"
    engine = create_engine_from_url(url)
    try:
        migrated_store_at(url)
        inspector = sa.inspect(engine)
        migrated = {
            t for t in inspector.get_table_names() if any("tenant" in c["name"] for c in inspector.get_columns(t))
        }
        assert migrated == {"runner_identity", "host_identity"}
        assert not any(
            "tenant" in column
            for table in inspector.get_table_names()
            for index in inspector.get_indexes(table)
            for column in index["column_names"]
            if column is not None
        )
    finally:
        engine.dispose()


def test_an_empty_row_takes_the_registered_runner_with_its_tenant(tmp_path: Path) -> None:
    """The check writes nothing into an empty row; the registration after it records the configured runner and its
    tenant."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=_RUNNER)
    ctx = _ctx(store, _tenant_hub())

    IdentityCheck(ctx).run()
    assert store.runner_identity() is None

    Registration(ctx).run()
    recorded = store.runner_identity()
    assert recorded is not None
    assert (recorded.runner_id, recorded.tenant_id, recorded.tenant_name) == (_RUNNER, "ten_default", "default")


def test_a_mismatched_answer_records_nothing_of_the_other_runners_tenant(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=_RUNNER)
    store.record_runner_identity(RunnerIdentity(_RUNNER, "r-claude", _REGISTERED))
    hub = _tenant_hub("ten_other", "other")
    hub.issued[hub.token] = "rn_other"
    ctx = _ctx(store, hub)

    IdentityCheck(ctx).run()

    assert ctx.identity_verdict.verdict().state is IdentityState.MISMATCHED
    assert store.runner_identity() == RunnerIdentity(_RUNNER, "r-claude", _REGISTERED)


@pytest.mark.parametrize(
    ("answered", "kept"),
    [
        (("ten_default", "default"), ("ten_default", "default")),
        (("ten_default", "Platform team"), ("ten_default", "Platform team")),
        ((None, None), ("ten_default", "default")),
    ],
    ids=["same-tenant", "renamed-tenant", "hub-names-none"],
)
def test_each_registration_keeps_the_tenant_its_reply_names_else_the_rows(
    tmp_path: Path, answered: tuple[str | None, str | None], kept: tuple[str | None, str | None]
) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=_RUNNER)
    store.record_runner_identity(RunnerIdentity(_RUNNER, "r-claude", _REGISTERED, "ten_default", "default"))
    ctx = _ctx(store, _tenant_hub(*answered), checked=True)

    Registration(ctx).run()

    recorded = store.runner_identity()
    assert recorded is not None and (recorded.tenant_id, recorded.tenant_name) == kept
    assert ctx.identity.current() == recorded


def test_a_first_registration_after_the_upgrade_fills_the_tenant_when_the_check_could_not(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=_RUNNER)
    store.record_runner_identity(RunnerIdentity(_RUNNER, "r-claude", _REGISTERED))

    Registration(_ctx(store, _tenant_hub(), checked=True)).run()

    assert store.runner_identity() == RunnerIdentity(_RUNNER, "r-claude", _NOW, "ten_default", "default")
