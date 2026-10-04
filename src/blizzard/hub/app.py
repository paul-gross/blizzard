"""Composition root — wire the hub and build its FastAPI app (``bzh:dependency-injection``).

The single place collaborators are constructed and injected. ``create_app`` builds
the app from resolved config and does **not** open the store, so store-free callers
build it without a migrated database; ``build_hosted_app`` is the ``host``
composition root that opens the store and wires every fleet seam."""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from fastapi import Depends, FastAPI, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from blizzard import __version__
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.attributes import annotate
from blizzard.foundation.platform_tracing.handle import (
    DisabledPlatformTracing,
    IPlatformTracing,
    build_platform_tracing,
)
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer, NoopPlatformTracer
from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.store.readiness import ReadinessService
from blizzard.foundation.trace_attributes import CHUNK_ID
from blizzard.foundation.trace_export.internal.otlp import OtlpTraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.web import Frontend
from blizzard.hub.api.analytics import router as analytics_router
from blizzard.hub.api.auth_login import router as auth_login_router
from blizzard.hub.api.chunk_dependencies import router as chunk_dependencies_router
from blizzard.hub.api.chunks import router as chunks_router
from blizzard.hub.api.config import router as config_router
from blizzard.hub.api.decisions import router as decisions_router
from blizzard.hub.api.egress import router as egress_router
from blizzard.hub.api.events import router as events_router
from blizzard.hub.api.findings import router as findings_router
from blizzard.hub.api.fleet import router as fleet_router
from blizzard.hub.api.garden_proposals import router as garden_proposals_router
from blizzard.hub.api.garden_runs import router as garden_runs_router
from blizzard.hub.api.graphs import router as graphs_router
from blizzard.hub.api.health import router as health_router
from blizzard.hub.api.idp import router as idp_router
from blizzard.hub.api.me import router as me_router
from blizzard.hub.api.questions import router as questions_router
from blizzard.hub.api.queue import router as queue_router
from blizzard.hub.api.readiness import router as readiness_router
from blizzard.hub.api.repositories import router as repositories_router
from blizzard.hub.api.routines import router as routines_router
from blizzard.hub.api.runners import router as runners_router
from blizzard.hub.api.scopes import router as scopes_router
from blizzard.hub.api.secrets import router as secrets_router
from blizzard.hub.api.secrets import sanitized_validation_response
from blizzard.hub.api.spend import router as spend_router
from blizzard.hub.api.trace_continuation import TraceGatedFastAPI
from blizzard.hub.api.traces import router as traces_router
from blizzard.hub.api.transcripts import router as transcripts_router
from blizzard.hub.api.users import router as users_router
from blizzard.hub.api.work_sources import router as work_sources_router
from blizzard.hub.auth.bootstrap import Superuser
from blizzard.hub.composition import HubServices, build_process_core, build_services
from blizzard.hub.config import AUTH_MODE_OAUTH, ConfigError, EgressConfig, HubConfig
from blizzard.hub.domain.egress.event_rows import missing_key_reason
from blizzard.hub.domain.registry import RunnerRetired
from blizzard.hub.domain.tracing.attributes import (
    INSTRUMENTATION_SCOPE,
    INSTRUMENTATION_SCOPE_VERSION,
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    resource_attributes,
)
from blizzard.hub.domain.transcripts import TranscriptCaps
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.runtime import migration_runner
from blizzard.hub.secrets import KeyCoverage, hub_key_provider
from blizzard.hub.secrets.rotation import RotationResult, rotate_keys
from blizzard.hub.work_sources.internal.factory import WorkSourceEntry

ENV_FORGE_URL = "BZ_FORGE_URL"
ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
# Qualifies a bare (worktree-name-only) delivery repo into the forge's ``owner/name`` coordinate.
ENV_FORGE_OWNER = "BZ_FORGE_OWNER"
# Defaults to "blizzard" when unset, qualifying a bare repo name into that owner.
DEFAULT_FORGE_OWNER = "blizzard"
# The branch every PR/merge targets, so a PR's ``base`` resolves instead of 422-ing.
ENV_FORGE_BASE_BRANCH = "BZ_FORGE_BASE_BRANCH"
DEFAULT_FORGE_BASE_BRANCH = "main"

#: The transcript-event derivation sweep's own interval — a module
#: constant; its own change probe skips the pass when nothing changed.
EVENT_DERIVATION_INTERVAL_SECONDS = 60

#: The delivery-materialization sweep's own interval.
WORK_ITEM_MATERIALIZATION_INTERVAL_SECONDS = 60

#: The close-intent drain sweep's own interval.
CLOSE_DRAIN_INTERVAL_SECONDS = 60

#: How long the hub's shutdown waits for buffered platform spans to leave.
PLATFORM_TRACING_SHUTDOWN_SECONDS = 3.0


class _Sweepable(Protocol):
    """The one capability :class:`Sweep` needs — structural, so any reconciler
    stands in with no inheritance."""

    def sweep(self) -> None: ...


@dataclass(frozen=True)
class Sweep:
    """One reconciler stepped once per interval until shutdown (``bzh:steppable-loop``).
    The first pass runs immediately, unjittered; ``jitter_seconds`` offsets only the
    second pass, so sibling sweeps synchronize once at boot then decorrelate for good.
    ``timer`` is the injectable monotonic clock ``run`` measures each pass's elapsed time with;
    ``tracer`` opens each pass's ``sweep <name>`` root."""

    reconciler: _Sweepable
    interval_seconds: int
    shutdown: asyncio.Event
    logger_name: str
    jitter_seconds: float | None = None
    timer: Callable[[], float] = time.monotonic
    tracer: IPlatformTracer = field(default_factory=NoopPlatformTracer)

    @classmethod
    def all(cls, app: FastAPI) -> Iterator[Sweep]:
        """The forge-status sweep a work source opts into, plus the always-on
        event-derivation, delivery-materialization, and close-drain sweeps, plus the trace-export
        sweep when tracing is enabled, plus the fact-egress sweep when a directory is configured — none on the
        store-free app. Each sweep's jitter is drawn uniformly from ``[0, interval_seconds)``
        here so their recurring cadence decorrelates from its second pass
        on."""
        services: HubServices | None = app.state.services
        if services is None:
            return
        interval = app.state.config.annotation_interval_seconds
        tracer = app.state.platform_tracing.tracer
        if services.annotation is not None:
            yield cls(
                services.annotation,
                interval,
                app.state.shutdown,
                "blizzard.hub.forge_status",
                jitter_seconds=random.uniform(0, interval),
                tracer=tracer,
            )
        yield cls(
            services.event_derivation,
            EVENT_DERIVATION_INTERVAL_SECONDS,
            app.state.shutdown,
            "blizzard.hub.transcript_events",
            jitter_seconds=random.uniform(0, EVENT_DERIVATION_INTERVAL_SECONDS),
            tracer=tracer,
        )
        yield cls(
            services.work_item_materialization,
            WORK_ITEM_MATERIALIZATION_INTERVAL_SECONDS,
            app.state.shutdown,
            "blizzard.hub.work_item_materialization",
            jitter_seconds=random.uniform(0, WORK_ITEM_MATERIALIZATION_INTERVAL_SECONDS),
            tracer=tracer,
        )
        yield cls(
            services.close_drain,
            CLOSE_DRAIN_INTERVAL_SECONDS,
            app.state.shutdown,
            "blizzard.hub.work_closure",
            jitter_seconds=random.uniform(0, CLOSE_DRAIN_INTERVAL_SECONDS),
            tracer=tracer,
        )
        if services.trace_export is not None:
            every = app.state.config.tracing.sweep_seconds
            yield cls(
                services.trace_export,
                every,
                app.state.shutdown,
                "blizzard.hub.trace_export",
                jitter_seconds=random.uniform(0, every),
                tracer=tracer,
            )

        if services.egress_export is not None:
            every = app.state.config.egress.sweep_seconds
            yield cls(
                services.egress_export,
                every,
                app.state.shutdown,
                "blizzard.hub.egress",
                jitter_seconds=random.uniform(0, every),
                tracer=tracer,
            )

    @property
    def name(self) -> str:
        """The sweep's own name — the last segment of its logger name."""
        return self.logger_name.rsplit(".", 1)[-1]

    async def _wait(self, timeout: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.shutdown.wait(), timeout=timeout)

    async def run(self) -> None:
        """Call ``sweep()`` immediately, then after ``jitter_seconds`` (default: plain
        ``interval_seconds``), then every ``interval_seconds`` after that, until shutdown.
        Every wait races ``shutdown``. A sweep that raises is logged and swallowed — a bad
        tick skips a cycle, never kills the loop. Every pass logs its elapsed time; an
        overrun logs a warning naming this sweep."""
        log = get_logger(self.logger_name)
        first = True
        while not self.shutdown.is_set():
            started = self.timer()
            try:
                with self.tracer.root(f"sweep {self.name}"):
                    await asyncio.to_thread(self.reconciler.sweep)
            except Exception:
                log.exception("sweep failed")
            elapsed = self.timer() - started
            log.info("sweep pass completed", elapsed_seconds=elapsed)
            if elapsed > self.interval_seconds:
                log.warning(
                    "sweep pass exceeded its own interval",
                    sweep=self.logger_name,
                    elapsed_seconds=elapsed,
                    interval_seconds=self.interval_seconds,
                )
            if first and self.jitter_seconds is not None:
                await self._wait(self.jitter_seconds)
            else:
                await self._wait(self.interval_seconds)
            first = False


def _refuse_retired_runner(_request: Request, exc: Exception) -> JSONResponse:
    """Map the domain's :class:`RunnerRetired` to a 403 carrying its message — the one
    refusal every runner-contact route and IdP federation return for a retired runner."""
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})


def rotate_secret_keys(config: HubConfig, environ: Mapping[str, str]) -> RotationResult:
    """The offline ``rotate-key`` wiring: the store and key source opened directly, no app."""
    engine = create_engine_from_url(config.db_url)
    try:
        return rotate_keys(build_process_core(engine).secrets, environ, data_dir=config.data_dir)
    finally:
        engine.dispose()


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    """The framework's 422, except on the secret routes, which never echo request input."""
    assert isinstance(exc, RequestValidationError)
    return sanitized_validation_response(request, exc) or await request_validation_exception_handler(request, exc)


def _annotate_chunk(request: Request) -> None:
    """Stamp ``blizzard.chunk.id`` on the request's span from a ``chunk_id`` path parameter."""
    chunk_id = request.path_params.get("chunk_id")
    if chunk_id is not None:
        annotate({CHUNK_ID: str(chunk_id)})


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Set ``app.state.shutdown`` on the ASGI ``lifespan`` "shutdown" message
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
    platform_tracing: IPlatformTracing | None = None,
) -> FastAPI:
    """Build a fully wired hub app from resolved config.

    ``readiness`` and ``services`` are optional so the store-free paths build the app
    without opening a database. ``platform_tracing`` is off unless the composition root passes one.
    """
    log = get_logger("blizzard.hub")

    platform_tracing = platform_tracing or DisabledPlatformTracing()
    # No framework exporters: tests/test_apps.py::test_an_otlp_endpoint_installs_no_framework_exporters.
    app = TraceGatedFastAPI(
        title="blizzard-hub",
        version=__version__,
        lifespan=_lifespan,
        telemetry=platform_tracing.fastapi_telemetry(),
        dependencies=[Depends(_annotate_chunk)],
    )
    app.state.platform_tracing = platform_tracing
    app.state.config = config
    app.state.readiness = readiness
    app.state.services = services
    # The event broker is always present (cheap, in-memory) so the SSE stream opens
    # cleanly even on the store-free app.
    app.state.events = services.events if services is not None else EventBroker()
    # Set on shutdown by ``_lifespan``; every SSE stream races it.
    app.state.shutdown = asyncio.Event()

    app.add_exception_handler(RunnerRetired, _refuse_retired_runner)
    app.add_exception_handler(RequestValidationError, _validation_error)

    # API routers first, so /api/* always wins over the web mount at /.
    app.include_router(health_router)
    app.include_router(readiness_router)
    app.include_router(me_router)
    app.include_router(auth_login_router)
    app.include_router(idp_router)
    app.include_router(events_router)
    app.include_router(graphs_router)
    app.include_router(scopes_router)
    app.include_router(secrets_router)
    app.include_router(config_router)
    app.include_router(routines_router)
    app.include_router(findings_router)
    app.include_router(garden_proposals_router)
    app.include_router(garden_runs_router)
    app.include_router(chunks_router)
    app.include_router(chunk_dependencies_router)
    app.include_router(decisions_router)
    app.include_router(queue_router)
    app.include_router(questions_router)
    app.include_router(runners_router)
    app.include_router(spend_router)
    app.include_router(users_router)
    app.include_router(transcripts_router)
    app.include_router(analytics_router)
    app.include_router(traces_router)
    app.include_router(egress_router)
    app.include_router(work_sources_router)
    app.include_router(repositories_router)
    # The runner-authenticated fleet router — a fleet verb is authenticated
    # *because of where it is mounted*; see `blizzard.hub.api.fleet`.
    app.include_router(fleet_router)

    Frontend.embedded("hub", app_name="blizzard-hub").mount(app)

    log.info("hub app created", db_url=config.db_url, services_wired=services is not None)
    return app


def _transcript_caps(config: HubConfig) -> TranscriptCaps:
    """The configured ingest ceilings, each falling back to the domain's own default —
    resolved here rather than in `HubConfig`, which carries overrides and never restates
    a value the domain owns."""
    defaults = TranscriptCaps()
    configured = config.transcripts
    return TranscriptCaps(
        record_max_bytes=configured.record_max_bytes or defaults.record_max_bytes,
        chunk_budget_max_bytes=configured.chunk_budget_max_bytes or defaults.chunk_budget_max_bytes,
        runner_daily_rate_max_bytes=configured.runner_daily_rate_max_bytes or defaults.runner_daily_rate_max_bytes,
    )


def build_hosted_app(
    config: HubConfig,
    *,
    platform_tracing: IPlatformTracing | None = None,
    oauth_http_client: httpx.Client | None = None,
) -> FastAPI:
    """The ``host`` composition root: open the store and wire every fleet seam.

    ``platform_tracing`` defaults to the handle ``[tracing]`` and the OTLP environment decide;
    tests inject one over an in-memory exporter. ``oauth_http_client`` replaces the OAuth
    providers' client. Either way, the engine and every outbound client are instrumented through it."""
    platform_tracing = platform_tracing or build_platform_tracing(
        config.tracing,
        os.environ,
        resource=resource_attributes(os.environ, __version__),
        scope=PLATFORM_INSTRUMENTATION_SCOPE,
        scope_version=PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    )
    engine = create_engine_from_url(config.db_url)
    platform_tracing.instrument_engine(engine)
    reader = SqlAlchemyStoreStatusReader(engine)
    expected = migration_runner(config).script_head()
    readiness = ReadinessService(reader=reader, expected_revision=expected)

    owner = os.environ.get(ENV_FORGE_OWNER, DEFAULT_FORGE_OWNER)
    # The one process-scoped clock and the stores and leaf services built once over it —
    # the work-source registry and `build_services` below both take the same core.
    core = build_process_core(engine)
    work_source_registry = WorkSourceEntry.registry(
        config.work_sources,
        users=core.users,
        work_item_store=core.work_item_store,
        edits=core.work_item_edits,
        resolution=core.garden_proposal_resolution,
        close_forge_writes_enabled=config.close_forge_writes_enabled,
        instrument_client=platform_tracing.instrument_client,
    )
    base_branch = os.environ.get(ENV_FORGE_BASE_BRANCH, DEFAULT_FORGE_BASE_BRANCH)
    tracing = TracingSettings.of(os.environ)

    # The provider-login seam is built only under `oauth`: under `none`
    # there is no login mechanism to serve.
    oauth_providers = config.auth.oauth_providers if config.auth.mode == AUTH_MODE_OAUTH else ()
    # The IdP signing-key lifecycle — likewise built only under `oauth`; a
    # `none` deployment never touches disk for a keypair it will never mint or publish.
    signing_keys_dir = config.data_dir / "auth" / "signing-keys" if config.auth.mode == AUTH_MODE_OAUTH else None
    # Minted here on first start when no key source exists yet.
    secret_keys = hub_key_provider(os.environ, data_dir=config.data_dir)
    oauth_client = oauth_http_client or httpx.Client(timeout=15.0)
    forge_client = httpx.Client(timeout=10.0)
    for client in (oauth_client, forge_client):
        platform_tracing.instrument_client(client)

    services = build_services(
        core,
        events=EventBroker(),
        work_sources=work_source_registry,
        base_branch=base_branch,
        hub_workdir_root=config.data_dir / "hub_workdirs",
        hub_marker_callback_base_url=f"http://{config.host}:{config.port}",
        forge_url=os.environ.get(ENV_FORGE_URL),
        forge_token=os.environ.get(ENV_FORGE_TOKEN),
        forge_owner=owner,
        public_url=config.public_url,
        forge_http_client=forge_client,
        oauth_providers=oauth_providers,
        oauth_http_client=oauth_client,
        signing_keys_dir=signing_keys_dir,
        secret_keys=secret_keys,
        trusted_proxies=TrustedProxies.parse(config.trusted_proxies),
        transcript_caps=_transcript_caps(config),
        # No exporter is built unless OpenTelemetry's own variables enable tracing.
        trace_exporter=(
            OtlpTraceExporter(
                resource=resource_attributes(os.environ, __version__),
                scope=INSTRUMENTATION_SCOPE,
                scope_version=INSTRUMENTATION_SCOPE_VERSION,
            )
            if tracing.enabled()
            else None
        ),
        tracing=config.tracing,
        tracing_settings=tracing,
        platform_tracer=platform_tracing.tracer,
        egress=config.egress,
        egress_path_key=_egress_path_key(config.egress),
    )
    # Only once the store is at the expected schema head: a store mid-migration must
    # fail *readiness*, not *boot* (pinned: `test_ready_probe_false_on_unmigrated_store`).
    if readiness.evaluate().ready:
        OrphanedProviders.of(config, services).check()
        KeyCoverage.of(core.secrets, secret_keys).check()
        Superuser(email=config.auth.superuser, users=services.users, auth=services.auth).ensure()
        _announce_rejected_tracing(tracing, services)
        _announce_rejected_egress(config.egress, services)
    app = create_app(config, readiness=readiness, services=services, platform_tracing=platform_tracing)
    # `host` disposes this on `app.state` — carried here.
    app.state.engine = engine
    return app


def _announce_rejected_tracing(tracing: TracingSettings, services: HubServices) -> None:
    """A rejected tracing setting never stops startup: the hub serves with tracing off and
    records one hub-wide ``trace-config-rejected`` per start, naming the setting."""
    if tracing.state != "rejected":
        return
    services.event_log.record(
        kind="trace-config-rejected",
        runner_id=None,
        chunk_id=None,
        lease_id=None,
        node_name=None,
        message=tracing.rejection_message("hub"),
        detail={"setting": tracing.setting, "value": tracing.value},
        at=services.clock.now(),
    )


def _announce_rejected_egress(egress: EgressConfig, services: HubServices) -> None:
    """An export the hub cannot fully honor never stops startup, and records one hub-wide ``egress-config-rejected``
    per start. Without its format's extra the export is off and the event names the format; without the path hash
    key a hashing file path policy needs, only the events dataset is off and the event names the variable — never
    a key value."""
    unavailable = services.egress_unavailable
    variable = services.egress_missing_path_key
    if unavailable is not None:
        message = f"fact egress is off: {unavailable.reason}"
        detail = {"setting": "egress.format", "value": egress.format}
    elif variable is not None:
        message = f"fact egress: {missing_key_reason(variable)}"
        detail = {"setting": "egress.path_key_env", "value": variable}
    else:
        return
    services.event_log.record(
        kind="egress-config-rejected",
        runner_id=None,
        chunk_id=None,
        lease_id=None,
        node_name=None,
        message=message,
        detail=detail,
        at=services.clock.now(),
    )


def _egress_path_key(egress: EgressConfig) -> bytes | None:
    """The file path hash key, read once here from the variable ``egress.path_key_env`` names; empty is unset."""
    value = os.environ.get(egress.path_key_env, "")
    return value.encode() if value else None


@domain_model
@dataclass(frozen=True)
class OrphanedProviders:
    """Provider names stored identities reference that ``[[auth.oauth.provider]]`` no longer declares."""

    names: frozenset[str]

    @classmethod
    def of(cls, config: HubConfig, services: HubServices) -> OrphanedProviders:
        configured = {provider.name for provider in config.auth.oauth_providers}
        return cls(frozenset(services.identities.distinct_provider_names() - configured))

    def check(self) -> None:
        """Fail boot with an actionable error — a rename must not silently orphan
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
