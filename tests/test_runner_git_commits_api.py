"""``POST /api/leases/{id}/git-commits`` (issue #143, Phase 3).

Exercised over a real store via TestClient, mirroring
``tests/test_runner_attachments_api.py``'s convention: the route's shape, its
403/404/503 forms, and the round-trip it delegates to
(:class:`~blizzard.runner.domain.git_commit_declaration.GitCommitDeclarationService`,
pinned at the unit level by ``tests/test_lease_auth.py`` and the store level by
``tests/test_runner_store.py``) are the point here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.enrollment import hash_token
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.git_commit_declaration import GitCommitDeclarationService
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import FakeProvider, make_store

_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"
_BODY = {"repo": "toy-api", "branch": "feat/x", "commit": "abc123"}
_PROVIDER = FakeProvider({"e1": "/ws/e1"})


def _app_with_declarations(tmp_path: Path):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = GitCommitDeclarationService(store, FixedClock(_NOW), _PROVIDER)
    return create_app(config, runner_store=store, git_commit_declarations=service), store


def _seed_lease(store, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "runner_id": "runner-local",
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))  # type: ignore[arg-type]
    store.record_lease_token(str(fields["lease_id"]), hash_token(_TOKEN), _NOW)
    # A declaration resolves its environment from the chunk's bindings, so a lease
    # without one has nothing to declare against.
    store.record_binding(chunk_id=str(fields["chunk_id"]), environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


@pytest.mark.component
def test_503_when_declaration_service_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config)) as client:
        resp = client.post("/api/leases/lease_1/git-commits", json=_BODY)
    assert resp.status_code == 503


@pytest.mark.component
def test_503_when_store_unwired(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = GitCommitDeclarationService(store, FixedClock(_NOW), _PROVIDER)
    # The service is wired, but ``runner_store`` — the controller's own read-only
    # resolution seam — is not: the edge must still answer 503, not raise.
    app = create_app(config, git_commit_declarations=service)
    with TestClient(app) as client:
        resp = client.post("/api/leases/lease_1/git-commits", json=_BODY)
    assert resp.status_code == 503


@pytest.mark.component
def test_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_declarations(tmp_path)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_ghost/git-commits", json=_BODY, headers={"X-Blizzard-Lease-Token": _TOKEN}
        )
    assert resp.status_code == 404


@pytest.mark.component
def test_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_declarations(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post("/api/leases/lease_1/git-commits", json=_BODY)
    assert resp.status_code == 403
    assert store.git_commit_declarations_for_lease("lease_1") == {}


@pytest.mark.component
def test_403_for_a_wrong_token(tmp_path: Path) -> None:
    app, store = _app_with_declarations(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/git-commits", json=_BODY, headers={"X-Blizzard-Lease-Token": "not-the-real-token"}
        )
    assert resp.status_code == 403
    assert store.git_commit_declarations_for_lease("lease_1") == {}


@pytest.mark.component
def test_200_records_the_declaration_with_the_dedicated_header(tmp_path: Path) -> None:
    app, store = _app_with_declarations(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post("/api/leases/lease_1/git-commits", json=_BODY, headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"recorded": True, "lease_id": "lease_1", "repo": "toy-api", "environment_id": "e1"}
    declared = store.git_commit_declarations_for_lease("lease_1")[("e1", "toy-api")]
    assert (declared.environment_id, declared.repo, declared.branch, declared.commit) == (
        "e1",
        "toy-api",
        "feat/x",
        "abc123",
    )


@pytest.mark.component
def test_200_records_the_declaration_with_a_bearer_authorization_header(tmp_path: Path) -> None:
    app, store = _app_with_declarations(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post("/api/leases/lease_1/git-commits", json=_BODY, headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 200, resp.text
    assert ("e1", "toy-api") in store.git_commit_declarations_for_lease("lease_1")


@pytest.mark.component
def test_re_declare_of_the_same_repo_overwrites_the_prior_declaration(tmp_path: Path) -> None:
    app, store = _app_with_declarations(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        first = client.post("/api/leases/lease_1/git-commits", json=_BODY, headers={"X-Blizzard-Lease-Token": _TOKEN})
        second_body = {**_BODY, "commit": "def456"}
        second = client.post(
            "/api/leases/lease_1/git-commits", json=second_body, headers={"X-Blizzard-Lease-Token": _TOKEN}
        )
    assert first.status_code == 200
    assert second.status_code == 200
    declared = store.git_commit_declarations_for_lease("lease_1")[("e1", "toy-api")]
    assert declared.commit == "def456"


@pytest.mark.component
def test_a_closed_lease_is_404_not_403(tmp_path: Path) -> None:
    """A lease's own token still hashes correctly once closed — 404 (unknown/closed)
    takes precedence over ever reaching the token check."""
    app, store = _app_with_declarations(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    with TestClient(app) as client:
        resp = client.post("/api/leases/lease_1/git-commits", json=_BODY, headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


@pytest.mark.component
def test_400_for_a_repo_the_environment_does_not_hold(tmp_path: Path) -> None:
    """The rejection the silent drop replaces: a repo outside the env's manifest is
    refused at declare time, while the worker is still alive to re-run the verb, and the
    detail names what the env does hold so the correction is obvious."""
    app, store = _app_with_declarations(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/git-commits",
            json={**_BODY, "repo": "not-a-repo"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 400
    assert "toy-api" in resp.json()["detail"]
    assert store.git_commit_declarations_for_lease("lease_1") == {}


@pytest.mark.component
def test_400_for_an_environment_the_chunk_does_not_hold(tmp_path: Path) -> None:
    app, store = _app_with_declarations(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/git-commits",
            json={**_BODY, "environment_id": "e9"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 400
    assert "e1" in resp.json()["detail"]
    assert store.git_commit_declarations_for_lease("lease_1") == {}


@pytest.mark.component
def test_400_when_several_environments_are_held_and_none_is_named(tmp_path: Path) -> None:
    """With one env the worker need not repeat what cannot be anything else; with two,
    inferring would silently attribute a branch to the wrong environment, so it is
    refused rather than guessed."""
    provider = FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"})
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = GitCommitDeclarationService(store, FixedClock(_NOW), provider)
    app = create_app(config, runner_store=store, git_commit_declarations=service)
    _seed_lease(store)
    store.record_binding(chunk_id="ch_1", environment_id="e2", workdir="/ws/e2", bound_at=_NOW)

    with TestClient(app) as client:
        ambiguous = client.post("/api/leases/lease_1/git-commits", json=_BODY, headers={"X-Blizzard-Lease-Token": _TOKEN})
        named = client.post(
            "/api/leases/lease_1/git-commits",
            json={**_BODY, "environment_id": "e2"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert ambiguous.status_code == 400
    assert "--env" in ambiguous.json()["detail"]
    assert named.status_code == 200
    assert set(store.git_commit_declarations_for_lease("lease_1")) == {("e2", "toy-api")}


@pytest.mark.component
def test_the_same_repo_in_two_environments_is_two_declarations(tmp_path: Path) -> None:
    """The clobber the environment key removes: under a repo-only key the second env's
    branch read as a *correction* of the first's, collapsing two facts into one."""
    provider = FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"})
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = GitCommitDeclarationService(store, FixedClock(_NOW), provider)
    app = create_app(config, runner_store=store, git_commit_declarations=service)
    _seed_lease(store)
    store.record_binding(chunk_id="ch_1", environment_id="e2", workdir="/ws/e2", bound_at=_NOW)

    with TestClient(app) as client:
        for env, commit in (("e1", "aaa111"), ("e2", "bbb222")):
            resp = client.post(
                "/api/leases/lease_1/git-commits",
                json={**_BODY, "environment_id": env, "commit": commit},
                headers={"X-Blizzard-Lease-Token": _TOKEN},
            )
            assert resp.status_code == 200, resp.text

    declarations = store.git_commit_declarations_for_lease("lease_1")
    assert set(declarations) == {("e1", "toy-api"), ("e2", "toy-api")}
    assert declarations[("e1", "toy-api")].commit == "aaa111"
    assert declarations[("e2", "toy-api")].commit == "bbb222"
