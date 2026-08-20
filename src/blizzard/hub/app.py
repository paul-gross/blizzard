"""Composition root — wire the hub and build its FastAPI app (``bzh:dependency-injection``).

The single place collaborators are constructed and injected. ``create_app`` builds
the app from resolved config and does **not** open the store, so store-free callers
build it without a migrated database; ``build_hosted_app`` is the ``host``
composition root that opens the store and wires every fleet seam."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI

from blizzard import __version__
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.store.readiness import ReadinessService
from blizzard.foundation.web import Frontend
from blizzard.hub.api.analytics import router as analytics_router
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
from blizzard.hub.api.transcripts import router as transcripts_router
from blizzard.hub.api.users import router as users_router
from blizzard.hub.auth.bootstrap import Superuser
from blizzard.hub.composition import HubServices, build_services
from blizzard.hub.config import AUTH_MODE_OAUTH, ConfigError, HubConfig
from blizzard.hub.domain.forge_status import AnnotationReconciler
from blizzard.hub.domain.transcripts import TranscriptCaps
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.runtime import migration_runner
from blizzard.hub.work_sources.internal.factory import WorkSourceEntry

ENV_FORGE_URL = "BZ_FORGE_URL"
ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
# Qualifies a bare (worktree-name-only) delivery repo into the forge's ``owner/name`` coordinate.
ENV_FORGE_OWNER = "BZ_FORGE_OWNER"
DEFAULT_FORGE_OWNER = "blizzard"
# The branch every PR/merge targets, so a PR's ``base`` resolves instead of 422-ing.
ENV_FORGE_BASE_BRANCH = "BZ_FORGE_BASE_BRANCH"
DEFAULT_FORGE_BASE_BRANCH = "main"

#: The transcript-event derivation sweep's own interval (blizzard#254 D1) — a module
#: constant, not an operator config key, in this slice.
EVENT_DERIVATION_INTERVAL_SECONDS = 30


class _Sweepable(Protocol):
    """The one capability :class:`Sweep` needs — structural, so any reconciler
    stands in with no inheritance."""

    def sweep(self) -> None: ...


@dataclass(frozen=True)
class Sweep:
    """One reconciler stepped once per interval until shutdown (``bzh:steppable-loop``)."""

    reconciler: _Sweepable
    interval_seconds: int
    shutdown: asyncio.Event
    logger_name: str

    @classmethod
    def all(cls, app: FastAPI) -> Iterator[Sweep]:
        """Every sweep a work source has opted this app into — none on the store-free app."""
        services: HubServices | None = app.state.services
        if services is None:
            return
        interval = app.state.config.annotation_interval_seconds
        if services.work_sources.annotating_names():
            annotator = AnnotationReconciler(chunks=services.chunks, work_sources=services.work_sources)
            yield cls(annotator, interval, app.state.shutdown, "blizzard.hub.forge_status")
        if services.work_sources.closing_names():
            yield cls(services.delivery_closure, interval, app.state.shutdown, "blizzard.hub.work_closure")
        yield cls(
            services.event_derivation,
            EVENT_DERIVATION_INTERVAL_SECONDS,
            app.state.shutdown,
            "blizzard.hub.transcript_events",
        )

    async def run(self) -> None:
        """Call ``sweep()``, then wait out the interval. Races ``shutdown`` so it wakes
        immediately instead of holding a graceful drain. A sweep that raises is logged and
        swallowed — a bad tick must never kill the loop, only skip a cycle."""
        log = get_logger(self.logger_name)
        while not self.shutdown.is_set():
            try:
                await asyncio.to_thread(self.reconciler.sweep)
            except Exception:
                log.exception("sweep failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.shutdown.wait(), timeout=self.interval_seconds)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Set ``app.state.shutdown`` on the ASGI ``lifespan`` "shutdown" message (issue #47)
    and drive the sweeps across the app's lifetime. The tasks are created here — not in
    :func:`build_hosted_app` — because this is the one place that runs for every app the
    ``lifespan`` fires for."""
    tasks = [asyncio.create_task(sweep.run()) for sweep in Sweep.all(app)]
    yield
    app.state.shutdown.set()
    for task in tasks:
        await task


def create_app(
    config: HubConfig,
    *,
    readiness: ReadinessService | None = None,
    services: HubServices | None = None,
) -> FastAPI:
    """Build a fully wired hub app from resolved config.

    ``readiness`` and ``services`` are optional so the store-free paths build the app
    without opening a database.
    """
    log = get_logger("blizzard.hub")

    app = FastAPI(title="blizzard-hub", version=__version__, lifespan=_lifespan)
    app.state.config = config
    app.state.readiness = readiness
    app.state.services = services
    # The event broker is always present (cheap, in-memory) so the SSE stream opens
    # cleanly even on the store-free app.
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
    app.include_router(transcripts_router)
    app.include_router(analytics_router)
    # The runner-authenticated fleet router (issue #87) — a fleet verb is authenticated
    # *because of where it is mounted*; see `blizzard.hub.api.fleet`.
    app.include_router(fleet_router)

    Frontend.embedded("hub", app_name="blizzard-hub").mount(app)

    log.info("hub app created", db_url=config.db_url, services_wired=services is not None)
    return app


def _transcript_caps(config: HubConfig) -> TranscriptCaps:
    """The configured ingest ceilings, each falling back to the domain's own default —
    resolved here rather than in `HubConfig`, which carries overrides and never restates
    a value the domain owns (blizzard#338)."""
    defaults = TranscriptCaps()
    configured = config.transcripts
    return TranscriptCaps(
        record_max_bytes=configured.record_max_bytes or defaults.record_max_bytes,
        chunk_budget_max_bytes=configured.chunk_budget_max_bytes or defaults.chunk_budget_max_bytes,
        runner_daily_rate_max_bytes=configured.runner_daily_rate_max_bytes or defaults.runner_daily_rate_max_bytes,
    )


def build_hosted_app(config: HubConfig) -> FastAPI:
    """The ``host`` composition root: open the store and wire every fleet seam."""
    engine = create_engine_from_url(config.db_url)
    reader = SqlAlchemyStoreStatusReader(engine)
    expected = migration_runner(config).script_head()
    readiness = ReadinessService(reader=reader, expected_revision=expected)

    owner = os.environ.get(ENV_FORGE_OWNER, DEFAULT_FORGE_OWNER)
    work_source_registry = WorkSourceEntry.registry(config.work_sources, engine)
    base_branch = os.environ.get(ENV_FORGE_BASE_BRANCH, DEFAULT_FORGE_BASE_BRANCH)

    # The provider-login seam (issue #92) is built only under `oauth`: under `none`
    # there is no login mechanism to serve.
    oauth_providers = config.auth.oauth_providers if config.auth.mode == AUTH_MODE_OAUTH else ()
    # The IdP signing-key lifecycle (issue #95) — likewise built only under `oauth`; a
    # `none` deployment never touches disk for a keypair it will never mint or publish.
    signing_keys_dir = config.data_dir / "auth" / "signing-keys" if config.auth.mode == AUTH_MODE_OAUTH else None

    services = build_services(
        engine,
        events=EventBroker(),
        work_sources=work_source_registry,
        base_branch=base_branch,
        hub_workdir_root=config.data_dir / "hub_workdirs",
        hub_marker_callback_base_url=f"http://{config.host}:{config.port}",
        forge_url=os.environ.get(ENV_FORGE_URL),
        forge_token=os.environ.get(ENV_FORGE_TOKEN),
        forge_owner=owner,
        oauth_providers=oauth_providers,
        signing_keys_dir=signing_keys_dir,
        trusted_proxies=TrustedProxies.parse(config.trusted_proxies),
        transcript_caps=_transcript_caps(config),
    )
    # Only once the store is at the expected schema head: a store mid-migration must
    # fail *readiness*, not *boot* (pinned: `test_ready_probe_false_on_unmigrated_store`).
    if readiness.evaluate().ready:
        OrphanedProviders.of(config, services).check()
        Superuser(email=config.auth.superuser, users=services.users, auth=services.auth).ensure()
    return create_app(config, readiness=readiness, services=services)


@dataclass(frozen=True)
class OrphanedProviders:
    """Provider names stored identities reference that ``[[auth.oauth.provider]]`` no longer declares."""

    names: frozenset[str]

    @classmethod
    def of(cls, config: HubConfig, services: HubServices) -> OrphanedProviders:
        configured = {provider.name for provider in config.auth.oauth_providers}
        return cls(frozenset(services.identities.distinct_provider_names() - configured))

    def check(self) -> None:
        """Fail boot with an actionable error (issue #92) — a rename must not silently orphan
        identities and re-mint duplicate users on the next login. Checked regardless of
        ``auth.mode``: an operator flipping back to ``none`` does not erase the guarantee."""
        if self.names:
            raise ConfigError(
                "stored identities reference OAuth provider name(s) "
                f"{sorted(self.names)} absent from [[auth.oauth.provider]] — a provider name is "
                "immutable once identities reference it; restore the entry (or its name) rather "
                "than deleting/renaming it"
            )


def create_app_for_export() -> FastAPI:
    """Build the app with throwaway config for OpenAPI export (no store, no dirs)."""
    from pathlib import Path

    return create_app(HubConfig(root=Path("."), db_url="sqlite://"))
