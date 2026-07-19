"""``POST /api/leases/{id}/attachments`` (issue #113, Phase 2).

Exercised over a real store via TestClient, mirroring
``tests/test_runner_takeover_api.py``'s convention: the route's shape, its
403/404/503 forms, and the round-trip it delegates to
(:class:`~blizzard.runner.domain.attachments.AttachmentService`, pinned at the unit
level by ``tests/test_lease_auth.py`` and the store level by
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
from blizzard.runner.domain.attachments import AttachmentService
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import make_store

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"


def _app_with_attachments(tmp_path: Path):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = AttachmentService(store, FixedClock(_NOW))
    return create_app(config, runner_store=store, attachments=service), store


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


@pytest.mark.component
def test_503_when_attachment_service_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config)) as client:
        resp = client.post("/api/leases/lease_1/attachments", json={"name": "n", "content": "c"})
    assert resp.status_code == 503


@pytest.mark.component
def test_503_when_store_unwired(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = AttachmentService(store, FixedClock(_NOW))
    # The service is wired, but ``runner_store`` — the controller's own read-only
    # resolution seam — is not: the edge must still answer 503, not raise.
    app = create_app(config, attachments=service)
    with TestClient(app) as client:
        resp = client.post("/api/leases/lease_1/attachments", json={"name": "n", "content": "c"})
    assert resp.status_code == 503


@pytest.mark.component
def test_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_attachments(tmp_path)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_ghost/attachments",
            json={"name": "n", "content": "c"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 404


@pytest.mark.component
def test_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post("/api/leases/lease_1/attachments", json={"name": "n", "content": "c"})
    assert resp.status_code == 403
    assert store.attachments_for_lease("lease_1") == {}


@pytest.mark.component
def test_403_for_a_wrong_token(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": "c"},
            headers={"X-Blizzard-Lease-Token": "not-the-real-token"},
        )
    assert resp.status_code == 403
    assert store.attachments_for_lease("lease_1") == {}


@pytest.mark.component
def test_200_records_the_attachment_with_the_dedicated_header(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "review-findings", "content": "looks good"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"recorded": True, "lease_id": "lease_1", "name": "review-findings"}
    assert store.attachments_for_lease("lease_1") == {"review-findings": "looks good"}


@pytest.mark.component
def test_200_records_the_attachment_with_a_bearer_authorization_header(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": "c"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert resp.status_code == 200, resp.text
    assert store.attachments_for_lease("lease_1") == {"n": "c"}


@pytest.mark.component
def test_re_attach_of_the_same_name_overwrites_the_prior_content(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        first = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": "first"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
        second = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": "second"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert first.status_code == 200
    assert second.status_code == 200
    assert store.attachments_for_lease("lease_1") == {"n": "second"}


@pytest.mark.component
def test_a_closed_lease_is_404_not_403(tmp_path: Path) -> None:
    """A lease's own token still hashes correctly once closed — 404 (unknown/closed)
    takes precedence over ever reaching the token check."""
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": "c"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 404
