"""Composition root — wire the hub and build its FastAPI app (``bzh:dependency-injection``).

The single place collaborators are constructed and injected. ``create_app`` builds
the app from resolved config and does **not** open the store — the startup
revision guard (``blizzard hub host``) and the offline ``migrate`` verb own that.
Keeping ``create_app`` store-free lets the OpenAPI exporter and unit tests build
the app without a migrated database.
"""

from __future__ import annotations

from fastapi import FastAPI

from blizzard import __version__
from blizzard.foundation.assets import frontend_dir
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.web import mount_web_app
from blizzard.hub.api.events import router as events_router
from blizzard.hub.api.health import router as health_router
from blizzard.hub.api.readiness import router as readiness_router
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.readiness import ReadinessService
from blizzard.hub.runtime import migration_runner


def create_app(config: HubConfig, *, readiness: ReadinessService | None = None) -> FastAPI:
    """Build a fully wired hub app from resolved config.

    ``readiness`` is the store-backed readiness evaluator wired by the ``host``
    composition root (:func:`build_hosted_app`). It is optional so the store-free
    paths — the OpenAPI export and unit tests — build the app without opening a
    database; the ``/api/ready`` probe then reports ``ready=false`` honestly.
    """
    log = get_logger("blizzard.hub")

    app = FastAPI(title="blizzard-hub", version=__version__)
    app.state.config = config
    app.state.readiness = readiness

    # API routers first, so /api/* always wins over the web mount at /.
    app.include_router(health_router)
    app.include_router(readiness_router)
    app.include_router(events_router)

    # The embedded frontend, served from the same process and origin (D-096).
    mount_web_app(app, frontend_dir("hub"), app_name="blizzard-hub")

    log.info("hub app created", db_url=config.db_url, readiness_wired=readiness is not None)
    return app


def build_hosted_app(config: HubConfig) -> FastAPI:
    """The ``host`` composition root: open the store and wire the readiness seam.

    Constructs the engine and the store-status reader once here (``bzh:dependency-injection``)
    and injects them through the domain :class:`ReadinessService`. Engine creation
    is connection-free, so this stays cheap; the connection is opened lazily on the
    first ``/api/ready`` read.
    """
    engine = create_engine_from_url(config.db_url)
    reader = SqlAlchemyStoreStatusReader(engine)
    expected = migration_runner(config).script_head()
    readiness = ReadinessService(reader=reader, expected_revision=expected)
    return create_app(config, readiness=readiness)


def create_app_for_export() -> FastAPI:
    """Build the app with throwaway config for OpenAPI export (no store, no dirs)."""
    from pathlib import Path

    return create_app(HubConfig(root=Path("."), db_url="sqlite://"))
