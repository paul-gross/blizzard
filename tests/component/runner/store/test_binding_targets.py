"""The repository targets a binding's environment was prepared at (component tier): recorded with the binding, read back
for the chunk's newest held binding, and gone once that binding is released."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from blizzard.runner.environments.provider import WorktreeTarget
from tests.support.runner_fakes import SqlAlchemyRunnerStore, make_store

_T0 = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _target(repo: str, branch: str, *, inherited: bool = False) -> WorktreeTarget:
    return WorktreeTarget(f"repo_{repo}", repo, "acme", repo, branch, inherited=inherited)


def _bind(store: SqlAlchemyRunnerStore, environment_id: str, targets: list[WorktreeTarget], *, at: int) -> None:
    store.record_binding(
        chunk_id="ch_1",
        environment_id=environment_id,
        workdir=f"/ws/{environment_id}",
        bound_at=_T0 + timedelta(seconds=at),
        workspace_id="ws",
        project_id="p",
        targets=targets,
    )


def test_a_binding_reads_back_the_targets_it_was_prepared_at_by_slug(tmp_path: Path) -> None:
    store = _store(tmp_path)
    web, api = _target("web", "main", inherited=True), _target("api", "rust-rewrite")

    _bind(store, "e1", [web, api], at=0)

    assert store.held_targets("ch_1") == (api, web)


def test_the_newest_held_binding_answers_and_a_released_one_answers_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _bind(store, "e1", [_target("api", "rust-rewrite")], at=0)
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_T0 + timedelta(seconds=1))
    released = store.held_targets("ch_1")
    _bind(store, "e2", [_target("api", "master", inherited=True)], at=2)

    assert released == ()
    assert store.held_targets("ch_1") == (_target("api", "master", inherited=True),)


def test_a_binding_recorded_with_no_targets_reads_none(tmp_path: Path) -> None:
    store = _store(tmp_path)

    _bind(store, "e1", [], at=0)

    assert store.held_targets("ch_1") == ()
