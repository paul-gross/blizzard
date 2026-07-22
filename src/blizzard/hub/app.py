"""Composition root — wire the hub and build its FastAPI app (``bzh:dependency-injection``).

The single place collaborators are constructed and injected. ``create_app`` builds
the app from resolved config and does **not** open the store — the startup revision
guard (``blizzard hub host``) and the offline ``migrate`` verb own that. Keeping
``create_app`` store-free lets the OpenAPI exporter and unit tests build the app
without a migrated database; the fleet routes then report the store is unwired.

``build_hosted_app`` is the ``host`` composition root: it opens the store, wires the
readiness seam, and the PM source registry over the configured ``[[pm_source]]``
entries — each with its own credentialed client — and assembles the fleet services
(:func:`blizzard.hub.composition.build_services`). The forge a hub command node's
``run:`` script talks to (#65/#67) is injected as plain env (``BZ_FORGE_URL`` /
``BZ_FORGE_TOKEN`` / ``BZ_FORGE_OWNER``), read straight from the environment here —
no forge-delivery seam sits in front of it; the script itself speaks HTTP via
``urllib`` (``bzh:deterministic-shell``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI

from blizzard import __version__
from blizzard.foundation.assets import frontend_dir
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.web import mount_web_app
from blizzard.hub.api.auth_login import router as auth_login_router
from blizzard.hub.api.chunks import router as chunks_router
from blizzard.hub.api.decisions import router as decisions_router
from blizzard.hub.api.events import router as events_router
from blizzard.hub.api.fleet import router as fleet_router
from blizzard.hub.api.graphs import router as graphs_router
from blizzard.hub.api.health import router as health_router
from blizzard.hub.api.idp import router as idp_router
from blizzard.hub.api.me import router as me_router
from blizzard.hub.api.questions import router as questions_router
from blizzard.hub.api.queue import router as queue_router
from blizzard.hub.api.readiness import router as readiness_router
from blizzard.hub.api.runners import router as runners_router
from blizzard.hub.api.spend import router as spend_router
from blizzard.hub.api.users import router as users_router
from blizzard.hub.auth.bootstrap import ensure_superuser_bootstrap
from blizzard.hub.composition import HubServices, build_services
from blizzard.hub.config import AUTH_MODE_OAUTH, ConfigError, HubConfig
from blizzard.hub.domain.readiness import ReadinessService
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.pm.internal.factory import build_pm_registry
from blizzard.hub.runtime import migration_runner

ENV_FORGE_URL = "BZ_FORGE_URL"
ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
# The owner segment used to qualify a bare (worktree-name-only) delivery repo into
# the forge's ``owner/name`` coordinate (mirrors the packaged ``land_default``/
# ``land_pr_ci`` scripts' own ``qualify_repo``). GitHub in production names the owner
# explicitly; the verification forge fronts flat bare origins that resolve under any
# owner, so a workspace-configured default is enough.
ENV_FORGE_OWNER = "BZ_FORGE_OWNER"
DEFAULT_FORGE_OWNER = "blizzard"
# The branch every PR/merge targets. ``main`` matches the verification forge's
# bare origins; a real repo whose default branch differs (e.g. ``master`` on
# ``paul-gross/blizzard``) sets this so a PR's ``base`` resolves instead of 422-ing.
ENV_FORGE_BASE_BRANCH = "BZ_FORGE_BASE_BRANCH"
DEFAULT_FORGE_BASE_BRANCH = "main"


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Set ``app.state.shutdown`` on the ASGI ``lifespan`` "shutdown" message (issue #47).

    ``app.state.shutdown`` (an ``asyncio.Event``, created eagerly in :func:`create_app` so
    it exists before the first subscriber connects) is what every ``/api/events/stream``
    generator races against its queue read (``blizzard.hub.api.events._stream``), so a
    shutting-down stream returns immediately instead of blocking a graceful drain until its
    next 15s keepalive wake (or forever). This ASGI-level hook makes the app a well-behaved
    lifespan citizen under any runner, but it is **not** what unblocks the stream when
    ``blizzard hub host`` serves under uvicorn: uvicorn's own ``Server.shutdown`` only sends
    this message *after* waiting (up to ``timeout_graceful_shutdown``) for in-flight
    responses to finish on their own — an SSE response never does, so that wait would
    already be blocking or timing out before this fires. The real signal path there is
    ``blizzard.hub.cli._EarlyShutdownServer.handle_exit``, which sets the same event
    synchronously the instant SIGTERM/SIGINT is caught, well before uvicorn's drain begins.
    """
    yield
    app.state.shutdown.set()


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

    app = FastAPI(title="blizzard-hub", version=__version__, lifespan=_lifespan)
    app.state.config = config
    app.state.readiness = readiness
    app.state.services = services
    # The event broker is always present (cheap, in-memory) so the SSE stream opens
    # cleanly even on the store-free app; mutating routes publish through it.
    app.state.events = services.events if services is not None else EventBroker()
    # Set on shutdown by ``_lifespan``; every SSE stream races it (issue #47).
    app.state.shutdown = asyncio.Event()

    # API routers first, so /api/* always wins over the web mount at /.
    app.include_router(health_router)
    app.include_router(readiness_router)
    app.include_router(me_router)
    app.include_router(auth_login_router)
    app.include_router(idp_router)
    app.include_router(events_router)
    app.include_router(graphs_router)
    app.include_router(chunks_router)
    app.include_router(decisions_router)
    app.include_router(queue_router)
    app.include_router(questions_router)
    app.include_router(runners_router)
    app.include_router(spend_router)
    app.include_router(users_router)
    # The runner-authenticated fleet router (issue #87): `require_runner_principal` is
    # declared once at router level (`blizzard.hub.api.fleet`), not repeated per route —
    # a fleet verb is authenticated *because of where it is mounted*.
    app.include_router(fleet_router)

    # The embedded frontend, served from the same process and origin.
    mount_web_app(app, frontend_dir("hub"), app_name="blizzard-hub")

    log.info("hub app created", db_url=config.db_url, services_wired=services is not None)
    return app


def build_hosted_app(config: HubConfig) -> FastAPI:
    """The ``host`` composition root: open the store and wire every fleet seam."""
    engine = create_engine_from_url(config.db_url)
    reader = SqlAlchemyStoreStatusReader(engine)
    expected = migration_runner(config).script_head()
    readiness = ReadinessService(reader=reader, expected_revision=expected)

    owner = os.environ.get(ENV_FORGE_OWNER, DEFAULT_FORGE_OWNER)
    # The PM registry builds its own credentialed client per configured source.
    pm = build_pm_registry(config.pm_sources)
    base_branch = os.environ.get(ENV_FORGE_BASE_BRANCH, DEFAULT_FORGE_BASE_BRANCH)

    # The provider-login seam (issue #92) is consumed only under `oauth` — under `none`
    # there is no login mechanism, so no provider is built even if `[[auth.oauth.
    # provider]]` entries are configured (mirrors #95's "no IdP surface under none").
    oauth_providers = config.auth.oauth_providers if config.auth.mode == AUTH_MODE_OAUTH else ()
    # The IdP signing-key lifecycle (issue #95) — likewise built only under `oauth`; a
    # `none` deployment never touches disk for a keypair it will never mint or publish.
    signing_keys_dir = config.data_dir / "auth" / "signing-keys" if config.auth.mode == AUTH_MODE_OAUTH else None

    services = build_services(
        engine,
        events=EventBroker(),
        pm=pm,
        base_branch=base_branch,
        hub_workdir_root=config.data_dir / "hub_workdirs",
        hub_marker_callback_base_url=f"http://{config.host}:{config.port}",
        forge_url=os.environ.get(ENV_FORGE_URL),
        forge_token=os.environ.get(ENV_FORGE_TOKEN),
        forge_owner=owner,
        oauth_providers=oauth_providers,
        signing_keys_dir=signing_keys_dir,
        trusted_proxies=TrustedProxies.parse(config.trusted_proxies),
    )
    # Only checked once the store is confirmed at the expected schema head — reusing
    # the same readiness evaluation `/api/ready` reports rather than a raw query, so a
    # store mid-migration (or rolled back, as `blizzard hub migrate --down` leaves it)
    # fails *readiness*, not *boot* (`build_hosted_app` must still return a serving —
    # if not-ready — app; see `test_ready_probe_false_on_unmigrated_store`).
    if readiness.evaluate().ready:
        _check_provider_name_immutability(config, services)
        ensure_superuser_bootstrap(email=config.auth.superuser, users=services.users, auth=services.auth)
    return create_app(config, readiness=readiness, services=services)


def _check_provider_name_immutability(config: HubConfig, services: HubServices) -> None:
    """Fail boot with an actionable error when a stored identity names a provider
    absent from ``[[auth.oauth.provider]]`` (issue #92) — a rename must not silently
    orphan identities and re-mint duplicate users on the next login. Runs regardless of
    ``auth.mode`` (an operator flipping back to ``none`` does not erase this guarantee)."""
    configured = {provider.name for provider in config.auth.oauth_providers}
    orphaned = services.identities.distinct_provider_names() - configured
    if orphaned:
        raise ConfigError(
            "stored identities reference OAuth provider name(s) "
            f"{sorted(orphaned)} absent from [[auth.oauth.provider]] — a provider name is "
            "immutable once identities reference it; restore the entry (or its name) rather "
            "than deleting/renaming it"
        )


def create_app_for_export() -> FastAPI:
    """Build the app with throwaway config for OpenAPI export (no store, no dirs)."""
    from pathlib import Path

    return create_app(HubConfig(root=Path("."), db_url="sqlite://"))
