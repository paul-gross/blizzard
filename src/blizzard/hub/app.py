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
from blizzard.foundation.web import mount_web_app
from blizzard.hub.api.health import router as health_router
from blizzard.hub.config import HubConfig


def create_app(config: HubConfig) -> FastAPI:
    """Build a fully wired hub app from resolved config."""
    log = get_logger("blizzard.hub")

    app = FastAPI(title="blizzard-hub", version=__version__)
    app.state.config = config

    # API routers first, so /api/* always wins over the web mount at /.
    app.include_router(health_router)

    # The embedded frontend, served from the same process and origin (D-096).
    mount_web_app(app, frontend_dir("hub"), app_name="blizzard-hub")

    log.info("hub app created", db_url=config.db_url)
    return app


def create_app_for_export() -> FastAPI:
    """Build the app with throwaway config for OpenAPI export (no store, no dirs)."""
    from pathlib import Path

    return create_app(HubConfig(root=Path("."), db_url="sqlite://"))
