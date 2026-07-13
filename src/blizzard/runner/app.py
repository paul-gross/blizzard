"""Composition root — wire the runner and build its FastAPI app (``bzh:dependency-injection``).

The single place collaborators are constructed and injected. ``create_app`` builds
the app from resolved config and does **not** open the store — the startup
revision guard (``blizzard runner host``) and the offline ``migrate`` verb own
that. Keeping ``create_app`` store-free lets the OpenAPI exporter and unit tests
build the app without a migrated database.
"""

from __future__ import annotations

from fastapi import FastAPI

from blizzard import __version__
from blizzard.foundation.assets import frontend_dir
from blizzard.foundation.logging import get_logger
from blizzard.foundation.web import mount_web_app
from blizzard.runner.api.health import router as health_router
from blizzard.runner.config import RunnerConfig


def create_app(config: RunnerConfig) -> FastAPI:
    """Build a fully wired runner app from resolved config."""
    log = get_logger("blizzard.runner")

    app = FastAPI(title="blizzard-runner", version=__version__)
    app.state.config = config

    # API routers first, so /api/* always wins over the web mount at /.
    app.include_router(health_router)

    # The runner-served web app (post-MVP); the mount point is live from the
    # scaffold so the seam is exercised (D-096).
    mount_web_app(app, frontend_dir("runner"), app_name="blizzard-runner")

    log.info("runner app created", db_url=config.db_url)
    return app


def create_app_for_export() -> FastAPI:
    """Build the app with throwaway config for OpenAPI export (no store, no dirs)."""
    from pathlib import Path

    return create_app(RunnerConfig(root=Path("."), db_url="sqlite://"))
