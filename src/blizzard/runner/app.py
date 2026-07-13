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
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.web import mount_web_app
from blizzard.runner.api.health import router as health_router
from blizzard.runner.api.readiness import router as readiness_router
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.readiness import ReadinessService
from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.runtime import migration_runner


def create_app(
    config: RunnerConfig,
    *,
    readiness: ReadinessService | None = None,
    workspace_provider: IWorkspaceProvider | None = None,
    harness: IHarnessAdapter | None = None,
) -> FastAPI:
    """Build a fully wired runner app from resolved config.

    ``readiness`` is the store-backed readiness evaluator wired by the ``host``
    composition root (:func:`build_hosted_app`). It is optional so the store-free
    paths — the OpenAPI export and unit tests — build the app without opening a
    database; the ``/api/ready`` probe then reports ``ready=false`` honestly.
    """
    log = get_logger("blizzard.runner")

    app = FastAPI(title="blizzard-runner", version=__version__)
    app.state.config = config
    app.state.readiness = readiness
    # The runner's two execution seams (D-062/D-092), wired at the host root; the
    # store-free app leaves them None. The reconciliation loop the P6 builder adds
    # reads them off app.state. Both bindings are NotImplemented stubs in P6.
    app.state.workspace_provider = workspace_provider
    app.state.harness = harness

    # API routers first, so /api/* always wins over the web mount at /.
    app.include_router(health_router)
    app.include_router(readiness_router)

    # The runner-served web app (post-MVP); the mount point is live from the
    # scaffold so the seam is exercised (D-096).
    mount_web_app(app, frontend_dir("runner"), app_name="blizzard-runner")

    log.info("runner app created", db_url=config.db_url, readiness_wired=readiness is not None)
    return app


def build_hosted_app(config: RunnerConfig) -> FastAPI:
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
    # Bind the reference execution seams (winter workspace, Claude Code) from config
    # (D-062/D-092). The reconciliation loop drives them through its own composition
    # root (:mod:`blizzard.runner.loop.build`); these are exposed on ``app.state`` for
    # the runner's local API surface.
    workspace_provider: IWorkspaceProvider = WinterWorkspaceProvider(
        workspace_root=config.workspace_root or str(config.root),
        env_pool=config.workspace_envs,
        base_branch=config.base_branch,
    )
    harness: IHarnessAdapter = ClaudeCodeAdapter(
        binary=config.harness_binary, settings_path=config.worker_settings_path
    )
    return create_app(config, readiness=readiness, workspace_provider=workspace_provider, harness=harness)


def create_app_for_export() -> FastAPI:
    """Build the app with throwaway config for OpenAPI export (no store, no dirs)."""
    from pathlib import Path

    return create_app(RunnerConfig(root=Path("."), db_url="sqlite://"))
