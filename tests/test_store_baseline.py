"""A folded baseline builds exactly the store the history it replaced built.

Each tree's baseline-only store is compared with the store the floor release's own history builds, on the same
backend: the schema ``schema_snapshot`` fingerprints, every row (minted ids and stamped instants read by
``blizzard.tools.store_fold.seeds.normalize``), and on SQLite each table's ``AUTOINCREMENT``. The runner history
cannot reach head on Postgres from an empty store (``20260920_0200_usage_cost_is_share`` sets a Boolean
``server_default`` of ``0``, which Postgres rejects), so that leg holds the baseline's Postgres store to the floor's
committed ``contracts/store/runner.json`` and its rows to the runner history's SQLite rows.

Skips until a tree declares a ``BASELINE_FLOOR``. Reproduce with ``mise run store-baseline``; the inputs are this
module's ``BLIZZARD_STORE_BASELINE*`` variables. Downloads the floor's wheel, so it needs ``GH_TOKEN``."""

from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from blizzard.runner.store import MIGRATIONS_DIR as RUNNER_MIGRATIONS_DIR
from blizzard.tools.release_install import Installation, download_release, install
from blizzard.tools.store_fold.history import history_store
from blizzard.tools.store_fold.seeds import normalize
from tests.rehearsal.comparison import reflection_drift
from tests.rehearsal.stores import baseline_contract, postgres_database, reflect
from tests.repo_files import repo_root
from tests.store_baseline_support import (
    autoincrement_of,
    row_differences,
    rows_of,
    schema_differences,
    schema_of,
)
from tests.support import MIGRATION_CLOCK

_GATE = "BLIZZARD_STORE_BASELINE"
_PG_URL = "BLIZZARD_STORE_BASELINE_PG_URL"

pytestmark = [
    pytest.mark.store_baseline,
    pytest.mark.skipif(
        os.environ.get(_GATE) != "1", reason=f"the store-baseline proof installs a published release; set {_GATE}=1"
    ),
]

#: Tables a hub history renamed, new name to old: see ``schema_of`` for the one class of difference this reads away.
_RENAMED_ON_POSTGRES = {"chunk_work_refs": "chunk_pm_pointers"}

_TREES = {"hub": HUB_MIGRATIONS_DIR, "runner": RUNNER_MIGRATIONS_DIR}


@pytest.fixture(scope="module")
def floors() -> dict[str, Installation]:
    """One installation per floor tag, filled as the legs ask for it."""
    return {}


def _floor(floors: dict[str, Installation], tmp_path_factory: pytest.TempPathFactory, tag: str) -> Installation:
    if tag not in floors:
        root = tmp_path_factory.mktemp("floor")
        floors[tag] = install(tag, download_release(tag, root / "release"), root / "venv")
    return floors[tag]


def _server_url(backend: str) -> str | None:
    if backend == "sqlite":
        return None
    url = os.environ.get(_PG_URL)
    if url:
        return url
    if os.environ.get("CI"):
        pytest.fail(f"{_PG_URL} is unset under CI, so the Postgres legs would pass without running")
    pytest.skip(f"{_PG_URL} is unset, so the Postgres legs do not run")


def _baseline_only_store(tree: str, url: str) -> str:
    """The revision the baseline is, applied to an empty store at ``url``; returns that revision."""
    scripts = _TREES[tree]
    (base,) = ScriptDirectory(str(scripts)).get_bases()
    runner = MigrationRunner(scripts, url, clock=MIGRATION_CLOCK)
    runner.upgrade(base)
    assert runner.current_revision() == base
    return base


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("tree", ["hub", "runner"])
def test_the_baseline_builds_the_store_its_history_built(
    tree: str,
    backend: str,
    tmp_path: Path,
    floors: dict[str, Installation],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    floor_tag = MigrationRunner(_TREES[tree], "sqlite://").baseline_floor
    if floor_tag is None:
        pytest.skip(f"the {tree} tree declares no BASELINE_FLOOR, so there is no baseline to prove")
    server_url = _server_url(backend)
    floor = _floor(floors, tmp_path_factory, floor_tag)
    with ExitStack() as stack:
        built_url = (
            stack.enter_context(postgres_database(server_url))
            if server_url
            else f"sqlite:///{tmp_path / 'baseline.db'}"
        )
        _baseline_only_store(tree, built_url)
        built_rows = normalize(rows_of(built_url))

        if tree == "runner" and server_url:
            history_url = history_store(floor, "runner", tmp_path / "history")
            drift = reflection_drift(reflect(built_url, "runner"), baseline_contract(repo_root(), floor_tag, "runner"))
            assert not drift, (
                f"the {floor_tag} runner baseline on Postgres is not its committed document:\n" + "\n".join(drift)
            )
        else:
            renamed = _RENAMED_ON_POSTGRES if server_url and tree == "hub" else None
            history_db = stack.enter_context(postgres_database(server_url)) if server_url else None
            history_url = history_store(floor, tree, tmp_path / "history", db_url=history_db)
            differences = schema_differences(schema_of(history_url, renamed=renamed), schema_of(built_url))
            assert not differences, (
                f"the {tree} baseline differs from its {floor_tag} history on {backend}:\n" + "\n".join(differences)
            )
            if backend == "sqlite":
                assert autoincrement_of(history_url) == autoincrement_of(built_url), (
                    f"the {tree} baseline and its {floor_tag} history disagree on AUTOINCREMENT"
                )
        differences = row_differences(normalize(rows_of(history_url)), built_rows)
        assert not differences, f"the {tree} baseline's rows differ from its {floor_tag} history's:\n" + "\n".join(
            differences
        )
