"""A store carried from before tenants and projects round-trips its configuration document: migrated to the head, its
export applied back changes nothing — every outcome is unchanged and the change log does not move — and a second
export is byte for byte the first.

The carried store is seeded with literal SQL at the last revision before tenants, rather than through
``src/blizzard/hub/store/schema.py``, whose tables keep changing shape after it."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import Migrations
from tests.support import build_hub

pytestmark = pytest.mark.component

_BEFORE_TENANTS = "20261008_1000_drop_work_item_proposals"
_NOW = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
_AT = "'2026-07-13 00:00:00.000000'"

#: A configuration as an operator left it before tenants: secrets, a work source and a repository each retired and
#: restored, a retired scope, a routine retired and restored across one linked scope, and a legacy import.
_SEED = (
    f"INSERT INTO secrets (name, ciphertext, nonce, key_id, revision, replaced_at, replaced_by, created_at) VALUES"
    f" ('forge-token', 'c0', 'n0', 'k0', 1, {_AT}, 'op', {_AT}), ('gh', 'c1', 'n1', 'k0', 1, {_AT}, 'operator', {_AT})",
    f"INSERT INTO work_sources (name, provider, locator, api_base, web_base, annotate, secret_name, revision,"
    f" created_at, created_by) VALUES ('acme', 'github', 'acme/blizzard', NULL, NULL, 1, 'gh', 3, {_AT}, 'operator')",
    f"INSERT INTO work_source_lifecycle_facts (id, name, retired, set_at, set_by) VALUES"
    f" (1, 'acme', 1, {_AT}, 'operator'), (2, 'acme', 0, {_AT}, 'operator')",
    f"INSERT INTO repositories (name, forge_api_url, owner, repo, base_branch, secret_name, revision, created_at,"
    f" created_by) VALUES ('acme-widget', 'http://forge.fixture', 'acme', 'widget', 'main', 'forge-token', 1, {_AT},"
    f" 'op'), ('acme-blizzard', 'https://api.github.com', 'acme', 'blizzard', 'master', 'gh', 3, {_AT}, 'operator')",
    f"INSERT INTO repository_lifecycle_facts (id, name, retired, set_at, set_by) VALUES"
    f" (1, 'acme-blizzard', 1, {_AT}, 'operator'), (2, 'acme-blizzard', 0, {_AT}, 'operator')",
    f"INSERT INTO scopes (slug, description, created_at, revision) VALUES ('hub', 'The hub service and its store',"
    f" {_AT}, 1), ('web', 'The web board', {_AT}, 1), ('legacy', 'Retired surface', {_AT}, 2)",
    f"INSERT INTO scope_lifecycle_facts (id, slug, retired, set_at, set_by) VALUES (1, 'legacy', 1, {_AT}, 'operator')",
    f"INSERT INTO routines (routine_id, name, graph_name, default_scope_slug, default_model, default_effort,"
    f" created_at, default_harnesses, revision) VALUES ('rtn_01KXCCC1008WAMRPZ1Z3D2F8ZN', 'code-health',"
    f" 'garden-routine', 'hub', NULL, NULL, {_AT}, NULL, 3)",
    f"INSERT INTO routine_lifecycle_facts (id, routine_id, retired, set_at, set_by) VALUES"
    f" (1, 'rtn_01KXCCC1008WAMRPZ1Z3D2F8ZN', 1, {_AT}, 'operator'),"
    f" (2, 'rtn_01KXCCC1008WAMRPZ1Z3D2F8ZN', 0, {_AT}, 'operator')",
    "INSERT INTO routine_scopes (routine_id, scope_slug) VALUES ('rtn_01KXCCC1008WAMRPZ1Z3D2F8ZN', 'hub')",
)


def test_a_carried_store_applies_its_own_export_without_a_change(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(_NOW)).runner.upgrade(_BEFORE_TENANTS)
    engine = create_engine_from_url(db_url)
    try:
        with engine.begin() as conn:
            for statement in _SEED:
                conn.execute(sa.text(statement))
    finally:
        engine.dispose()

    hub = build_hub(tmp_path, repositories=())
    assert hub.client.post("/api/graphs/sync").status_code == 200
    exported = hub.client.get("/api/config/export")
    assert exported.status_code == 200
    document = exported.json()
    assert [(p["slug"], p["sources"], p["repositories"]) for p in document["projects"]] == [
        ("default", ["acme"], ["acme-blizzard", "acme-widget"])
    ]
    assert [r["name"] for r in document["routines"]] == ["code-health"]

    def changes() -> list[dict[str, object]]:
        return hub.client.get("/api/config/changes", params={"limit": 200}).json()["changes"]

    before = changes()
    applied = hub.client.post("/api/config/apply", json=document)

    assert applied.status_code == 200, applied.text
    outcomes = applied.json()["outcomes"]
    assert outcomes
    assert Counter(outcome["op"] for outcome in outcomes) == {"unchanged": len(outcomes)}
    assert changes() == before
    assert hub.client.get("/api/config/export").content == exported.content
