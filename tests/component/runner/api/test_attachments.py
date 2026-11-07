"""``POST /api/leases/{id}/attachments`` and its read-back
counterpart ``GET /api/leases/{id}/attachments``.

Exercised over a real store via TestClient: the route's shape, its 403/404/503 forms,
and the round-trip it delegates to :class:`~blizzard.runner.operator.attachments.AttachmentService`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Unpack

import httpx2
from fastapi import FastAPI

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.tokens import TokenHash
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.operator.attachments import AttachmentService
from tests.support.config_inputs import create_runner_app
from tests.support.factory_fields import NewLeaseFields
from tests.support.runner_client import runner_client
from tests.support.runner_fakes import (
    SqlAlchemyRunnerStore,
    make_host_stores,
    make_store,
    make_stores,
    no_retry_clock,
)
from tests.support.runner_mint import new_mint

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"


def _app_with_attachments(tmp_path: Path):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = AttachmentService(store, FixedClock(_NOW), store, store)
    return create_runner_app(
        config, runner_stores=make_stores(store), host_stores=make_host_stores(store), attachments=service
    ), store


def _seed_lease(store: SqlAlchemyRunnerStore, **overrides: Unpack[NewLeaseFields]) -> None:
    fields: NewLeaseFields = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_mint(new_mint(NewLease(**fields)))
    store.record_lease_token(fields["lease_id"], TokenHash(_TOKEN).hex, _NOW)


def test_503_when_attachment_service_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with runner_client(create_runner_app(config)) as client:
        resp = client.post("/api/leases/lease_1/attachments", json={"name": "n", "content": "c"})
    assert resp.status_code == 503


def test_503_when_store_unwired(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = AttachmentService(store, FixedClock(_NOW), store, store)
    # The service is wired, but ``runner_stores`` — the controller's own read-only
    # resolution seam — is not: the edge must still answer 503, not raise.
    app = create_runner_app(config, attachments=service)
    with runner_client(app) as client:
        resp = client.post("/api/leases/lease_1/attachments", json={"name": "n", "content": "c"})
    assert resp.status_code == 503


def test_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_attachments(tmp_path)
    with runner_client(app) as client:
        resp = client.post(
            "/api/leases/lease_ghost/attachments",
            json={"name": "n", "content": "c"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 404


def test_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        resp = client.post("/api/leases/lease_1/attachments", json={"name": "n", "content": "c"})
    assert resp.status_code == 403
    assert store.attachments_for_lease("lease_1") == {}


def test_403_for_a_wrong_token(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        resp = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": "c"},
            headers={"X-Blizzard-Lease-Token": "not-the-real-token"},
        )
    assert resp.status_code == 403
    assert store.attachments_for_lease("lease_1") == {}


def test_200_records_the_attachment_with_the_dedicated_header(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        resp = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "review-findings", "content": "looks good"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"recorded": True, "lease_id": "lease_1", "name": "review-findings", "bytes": 10}
    assert store.attachments_for_lease("lease_1") == {"review-findings": "looks good"}


def test_200_records_the_attachment_with_a_bearer_authorization_header(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        resp = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": "c"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert resp.status_code == 200, resp.text
    assert store.attachments_for_lease("lease_1") == {"n": "c"}


def test_re_attach_of_the_same_name_overwrites_the_prior_content(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
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


def test_a_closed_lease_is_404_not_403(tmp_path: Path) -> None:
    """A lease's own token still hashes correctly once closed — 404 (unknown/closed)
    takes precedence over ever reaching the token check."""
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    with runner_client(app) as client:
        resp = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": "c"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 404


def test_an_open_takeover_authorizes_a_closed_reference_lease_but_refuses_the_attach(tmp_path: Path) -> None:
    """Once an open takeover names the (now closed) reference lease, its re-minted token reaches this
    route like an active lease's — a wrong token is still ``403`` — but a closed lease rides no further
    completion, so the attach is refused ``409``."""
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    takeover_token = "the-takeover-token"
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    store.record_lease_token("lease_1", TokenHash(takeover_token).hex, _NOW)

    with runner_client(app) as client:
        forged = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": "c"},
            headers={"X-Blizzard-Lease-Token": "not-it"},
        )
        resp = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": "c"},
            headers={"X-Blizzard-Lease-Token": takeover_token},
        )
    assert forged.status_code == 403, forged.text
    assert resp.status_code == 409, resp.text
    assert "closed" in resp.json()["detail"]
    assert store.attachments_for_lease("lease_1") == {}


def test_422_for_empty_content(tmp_path: Path) -> None:
    """Empty content is refused at the API door too, not only by the CLI — and refused
    after the token check, so an unauthorized caller still reads ``403``."""
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        forged = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": ""},
            headers={"X-Blizzard-Lease-Token": "not-it"},
        )
        resp = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "n", "content": ""},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert forged.status_code == 403, forged.text
    assert resp.status_code == 422, resp.text
    assert store.attachments_for_lease("lease_1") == {}


# --------------------------------------------------------------------------- #
# GET /api/leases/{id}/attachments — a worker's read-back of its own staged submissions


def test_get_503_when_store_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with runner_client(create_runner_app(config)) as client:
        resp = client.get("/api/leases/lease_1/attachments", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 503


def test_get_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_attachments(tmp_path)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_ghost/attachments", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


def test_get_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/attachments")
    assert resp.status_code == 403


def test_get_403_for_a_wrong_token(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/attachments", headers={"X-Blizzard-Lease-Token": "nope"})
    assert resp.status_code == 403


def test_get_returns_an_empty_list_when_nothing_is_staged(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/attachments", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_get_reads_back_a_just_staged_submission_immediately(tmp_path: Path) -> None:
    """The point of the route: a fresh ``create`` is visible here even though it will
    not show up in ``artifact list``/``get`` (the envelope-backed reads) until the
    node-step completes and publishes it."""
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "review-findings", "content": "looks good"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
        resp = client.get("/api/leases/lease_1/attachments", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{"name": "review-findings", "content": "looks good"}]


def test_get_returns_newest_content_per_name_sorted_by_name(tmp_path: Path) -> None:
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "z-name", "content": "z"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
        client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "a-name", "content": "first"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
        client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "a-name", "content": "second"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
        resp = client.get("/api/leases/lease_1/attachments", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert resp.json() == [
        {"name": "a-name", "content": "second"},
        {"name": "z-name", "content": "z"},
    ]


def test_409_once_the_leases_outcome_is_buffered(tmp_path: Path) -> None:
    """An attachment after the node-step's outcome is buffered would be recorded and never
    submitted — refused instead, and nothing is written."""
    app, store = _app_with_attachments(tmp_path)
    _seed_lease(store)
    store.enqueue_outbound(
        kind="completion.submitted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW
    )
    with runner_client(app) as client:
        resp = client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "note", "content": "late"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 409, resp.text
    assert "declare assets before the node-step's verdict" in resp.json()["detail"]
    assert store.attachments_for_lease("lease_1") == {}


# --------------------------------------------------------------------------- #
# The hub's stage-time format check

_HUB_URL = "http://hub.local:8421"
_REFUSAL = "artifact 'delta' does not match the finding-delta shape: measurement: Input should be a valid string"


def _app_with_hub(tmp_path: Path, handler: Callable[[httpx2.Request], httpx2.Response]):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=_HUB_URL)
    service = AttachmentService(store, FixedClock(_NOW), store, store)
    app = create_runner_app(
        config,
        runner_stores=make_stores(store),
        host_stores=make_host_stores(store),
        attachments=service,
        hub_proxy_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        hub_retry_clock=no_retry_clock(),
    )
    return app, store


def _stage(app: FastAPI, content: str) -> httpx2.Response:
    with runner_client(app) as client:
        return client.post(
            "/api/leases/lease_1/attachments",
            json={"name": "delta", "content": content},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )


def test_a_hub_refusal_is_422_with_the_refusal_and_leaves_the_prior_content(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        refuse = b"bad" in request.content
        return httpx2.Response(200, json={"refusal": _REFUSAL if refuse else None})

    app, store = _app_with_hub(tmp_path, handler)
    _seed_lease(store)
    assert _stage(app, "good").status_code == 200

    resp = _stage(app, "bad")

    assert resp.status_code == 422
    assert resp.json()["detail"] == _REFUSAL
    assert store.attachments_for_lease("lease_1") == {"delta": "good"}
    assert {r.url.path for r in seen} == {"/api/fleet/chunks/ch_1/artifact-check"}


def test_a_no_refusal_verdict_stages_the_content(tmp_path: Path) -> None:
    app, store = _app_with_hub(tmp_path, lambda _request: httpx2.Response(200, json={"refusal": None}))
    _seed_lease(store)
    assert _stage(app, "fine").status_code == 200
    assert store.attachments_for_lease("lease_1") == {"delta": "fine"}


def test_an_unreachable_hub_or_a_404_stages_the_content(tmp_path: Path) -> None:
    (tmp_path / "0").mkdir()
    (tmp_path / "1").mkdir()

    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("hub is down", request=request)

    def missing(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"detail": "unknown chunk"})

    handlers: list[Callable[[httpx2.Request], httpx2.Response]] = [down, missing]
    for index, handler in enumerate(handlers):
        app, store = _app_with_hub(tmp_path / str(index), handler)
        _seed_lease(store)
        assert _stage(app, "unchecked").status_code == 200
        assert store.attachments_for_lease("lease_1") == {"delta": "unchecked"}


def test_a_lifecycle_refusal_takes_precedence_over_a_hub_format_refusal(tmp_path: Path) -> None:
    asked: list[httpx2.Request] = []

    def refusing(request: httpx2.Request) -> httpx2.Response:
        asked.append(request)
        return httpx2.Response(200, json={"refusal": _REFUSAL})

    app, store = _app_with_hub(tmp_path, refusing)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )

    assert _stage(app, "bad").status_code == 409
    assert asked == []
