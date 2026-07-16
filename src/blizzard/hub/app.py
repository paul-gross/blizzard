"""Composition root — wire the hub and build its FastAPI app (``bzh:dependency-injection``).

The single place collaborators are constructed and injected. ``create_app`` builds
the app from resolved config and does **not** open the store — the startup revision
guard (``blizzard hub host``) and the offline ``migrate`` verb own that. Keeping
``create_app`` store-free lets the OpenAPI exporter and unit tests build the app
without a migrated database; the fleet routes then report the store is unwired.

``build_hosted_app`` is the ``host`` composition root: it opens the store, wires the
readiness seam, constructs the forge-delivery seam over its own GitHub-shaped HTTP
client (base URL + token from the environment) and the PM source registry over the
configured ``[[pm_source]]`` entries — each with its own credentialed client (D-105) —
and assembles the fleet services (:func:`blizzard.hub.composition.build_services`).
"""

from __future__ import annotations

import os

import httpx
from fastapi import FastAPI

from blizzard import __version__
from blizzard.foundation.assets import frontend_dir
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.web import mount_web_app
from blizzard.hub.api.chunks import router as chunks_router
from blizzard.hub.api.decisions import router as decisions_router
from blizzard.hub.api.events import router as events_router
from blizzard.hub.api.graphs import router as graphs_router
from blizzard.hub.api.health import router as health_router
from blizzard.hub.api.questions import router as questions_router
from blizzard.hub.api.queue import router as queue_router
from blizzard.hub.api.readiness import router as readiness_router
from blizzard.hub.api.routes import router as routes_router
from blizzard.hub.api.runners import router as runners_router
from blizzard.hub.composition import HubServices, build_services
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.forge import IForgeDelivery, LandingRequest, LandingResult, PrHandle, PrState
from blizzard.hub.delivery.internal.github_forge import GitHubForgeDelivery
from blizzard.hub.domain.readiness import ReadinessService
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.pm.internal.factory import build_pm_registry
from blizzard.hub.runtime import migration_runner

ENV_FORGE_URL = "BZ_FORGE_URL"
ENV_FORGE_HOST = "BZ_FORGE_HOST"
ENV_FORGE_PORT = "BZ_FORGE_PORT"
ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
# The owner segment used to qualify a bare (worktree-name-only) delivery repo into
# the forge's ``owner/name`` coordinate (github_forge._repo_path). GitHub in
# production names the owner explicitly; the verification forge fronts flat bare
# origins that resolve under any owner, so a workspace-configured default is enough.
ENV_FORGE_OWNER = "BZ_FORGE_OWNER"
DEFAULT_FORGE_OWNER = "blizzard"
# The branch every PR/merge targets (D-060). ``main`` matches the verification forge's
# bare origins; a real repo whose default branch differs (e.g. ``master`` on
# ``paul-gross/blizzard``) sets this so a PR's ``base`` resolves instead of 422-ing.
ENV_FORGE_BASE_BRANCH = "BZ_FORGE_BASE_BRANCH"
DEFAULT_FORGE_BASE_BRANCH = "main"


class _UnconfiguredForge:
    """The forge binding when no forge URL is configured — deliver fails loudly.

    Most of the hub serves without a forge; only the deliver hub node and the PM
    pass-through need one. Rather than refuse to start, the daemon binds this stub so
    ingest/claim/completion still work and a delivery attempt names the missing config.
    """

    def land(self, request: LandingRequest) -> LandingResult:
        raise RuntimeError(f"no forge configured (set {ENV_FORGE_URL}); cannot land {request.repo}")

    def open_pr(self, request: LandingRequest) -> PrHandle:
        raise RuntimeError(f"no forge configured (set {ENV_FORGE_URL})")

    def check_pr(self, handle: PrHandle) -> PrState:
        raise RuntimeError(f"no forge configured (set {ENV_FORGE_URL})")


def create_app(
    config: HubConfig,
    *,
    readiness: ReadinessService | None = None,
    services: HubServices | None = None,
) -> FastAPI:
    """Build a fully wired hub app from resolved config.

    ``readiness`` is the store-backed readiness evaluator; ``services`` is the wired
    fleet-service bundle. Both are optional so the store-free paths — the OpenAPI
    export and unit tests — build the app without opening a database; the ``/api/ready``
    probe then reports ``ready=false`` and the fleet routes report the store is unwired.
    """
    log = get_logger("blizzard.hub")

    app = FastAPI(title="blizzard-hub", version=__version__)
    app.state.config = config
    app.state.readiness = readiness
    app.state.services = services
    # The event broker is always present (cheap, in-memory) so the SSE stream opens
    # cleanly even on the store-free app; mutating routes publish through it.
    app.state.events = services.events if services is not None else EventBroker()

    # API routers first, so /api/* always wins over the web mount at /.
    app.include_router(health_router)
    app.include_router(readiness_router)
    app.include_router(events_router)
    app.include_router(graphs_router)
    app.include_router(chunks_router)
    app.include_router(decisions_router)
    app.include_router(routes_router)
    app.include_router(queue_router)
    app.include_router(questions_router)
    app.include_router(runners_router)

    # The embedded frontend, served from the same process and origin (D-096).
    mount_web_app(app, frontend_dir("hub"), app_name="blizzard-hub")

    log.info("hub app created", db_url=config.db_url, services_wired=services is not None)
    return app


def build_hosted_app(config: HubConfig) -> FastAPI:
    """The ``host`` composition root: open the store and wire every fleet seam."""
    engine = create_engine_from_url(config.db_url)
    reader = SqlAlchemyStoreStatusReader(engine)
    expected = migration_runner(config).script_head()
    readiness = ReadinessService(reader=reader, expected_revision=expected)

    client = _forge_client()
    owner = os.environ.get(ENV_FORGE_OWNER, DEFAULT_FORGE_OWNER)
    forge: IForgeDelivery = (
        GitHubForgeDelivery(client, default_owner=owner) if client is not None else _UnconfiguredForge()
    )
    # The PM registry builds its own credentialed client per configured source (D-105) —
    # it no longer shares the delivery forge's client or credential.
    pm = build_pm_registry(config.pm_sources)
    base_branch = os.environ.get(ENV_FORGE_BASE_BRANCH, DEFAULT_FORGE_BASE_BRANCH)

    services = build_services(engine, forge=forge, events=EventBroker(), pm=pm, base_branch=base_branch)
    return create_app(config, readiness=readiness, services=services)


def _forge_client() -> httpx.Client | None:
    """A GitHub-shaped HTTP client for the forge, from the environment, or None.

    The hub holds the forge base URL and token (D-047): ``BZ_FORGE_URL`` (or
    ``BZ_FORGE_HOST``/``BZ_FORGE_PORT``) plus an optional ``BZ_FORGE_TOKEN``. When no
    forge is configured, the delivery and PM seams stay unwired.
    """
    base_url = os.environ.get(ENV_FORGE_URL)
    if not base_url:
        host = os.environ.get(ENV_FORGE_HOST)
        port = os.environ.get(ENV_FORGE_PORT)
        if port:
            base_url = f"http://{host or '127.0.0.1'}:{port}"
    if not base_url:
        return None
    headers = {}
    token = os.environ.get(ENV_FORGE_TOKEN)
    if token:
        headers["Authorization"] = f"token {token}"
    return httpx.Client(base_url=base_url, headers=headers, timeout=30.0)


def create_app_for_export() -> FastAPI:
    """Build the app with throwaway config for OpenAPI export (no store, no dirs)."""
    from pathlib import Path

    return create_app(HubConfig(root=Path("."), db_url="sqlite://"))
