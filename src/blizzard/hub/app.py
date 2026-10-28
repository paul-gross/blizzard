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
from dataclasses import dataclass
from functools import partial

import httpx2
from fastapi import Depends, FastAPI, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from blizzard import __version__
from blizzard.foundation.flags import FlagOffError, FlagRegistry, Flags, resolve_flags
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.attributes import annotate
from blizzard.foundation.platform_tracing.handle import (
    DisabledPlatformTracing,
    IPlatformTracing,
    build_platform_tracing,
)
from blizzard.foundation.roles import collaborator, domain_model
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.store.readiness import ReadinessService
from blizzard.foundation.trace_attributes import CHUNK_ID
from blizzard.foundation.trace_export.internal.otlp import OtlpTraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.web import Frontend, install_schema_components
from blizzard.hub.api.admin_events import router as admin_events_router
from blizzard.hub.api.admin_tenants import router as admin_tenants_router
from blizzard.hub.api.analytics import router as analytics_router
from blizzard.hub.api.auth_login import router as auth_login_router
from blizzard.hub.api.auth_session import refuse_closed_tenant
from blizzard.hub.api.chunk_dependencies import router as chunk_dependencies_router
from blizzard.hub.api.chunks import router as chunks_router
from blizzard.hub.api.config import router as config_router
from blizzard.hub.api.decisions import router as decisions_router
from blizzard.hub.api.egress import router as egress_router
from blizzard.hub.api.events import router as events_router
from blizzard.hub.api.findings import project_router as project_findings_router
from blizzard.hub.api.findings import router as findings_router
from blizzard.hub.api.flag_guard import refuse_flag_off
from blizzard.hub.api.flags import router as flags_router
from blizzard.hub.api.fleet import claim_router as fleet_claim_router
from blizzard.hub.api.fleet import router as fleet_router
from blizzard.hub.api.fleet_host import router as fleet_host_router
from blizzard.hub.api.fleet_identity import router as fleet_identity_router
from blizzard.hub.api.forges import router as forges_router
from blizzard.hub.api.garden_proposals import project_router as project_garden_proposals_router
from blizzard.hub.api.garden_proposals import router as garden_proposals_router
from blizzard.hub.api.garden_runs import project_router as project_garden_runs_router
from blizzard.hub.api.garden_runs import router as garden_runs_router
from blizzard.hub.api.graphs import router as graphs_router
from blizzard.hub.api.health import router as health_router
from blizzard.hub.api.hosts import router as hosts_router
from blizzard.hub.api.hub_admins import router as hub_admins_router
from blizzard.hub.api.idp import router as idp_router
from blizzard.hub.api.invitations import router as invitations_router
from blizzard.hub.api.me import router as me_router
from blizzard.hub.api.members import grants_router as member_grants_router
from blizzard.hub.api.members import refuse_membership_role
from blizzard.hub.api.members import router as members_router
from blizzard.hub.api.project_params import refuse_project_required
from blizzard.hub.api.projects import router as projects_router
from blizzard.hub.api.questions import router as questions_router
from blizzard.hub.api.queue import router as queue_router
from blizzard.hub.api.readiness import router as readiness_router
from blizzard.hub.api.repositories import router as repositories_router
from blizzard.hub.api.routines import project_router as project_routines_router
from blizzard.hub.api.routines import router as routines_router
from blizzard.hub.api.runners import router as runners_router
from blizzard.hub.api.scopes import project_router as project_scopes_router
from blizzard.hub.api.scopes import router as scopes_router
from blizzard.hub.api.secrets import router as secrets_router
from blizzard.hub.api.secrets import sanitized_validation_response
from blizzard.hub.api.spend import router as spend_router
from blizzard.hub.api.sweeps import admin_router as admin_sweeps_router
from blizzard.hub.api.sweeps import router as sweeps_router
from blizzard.hub.api.trace_continuation import TraceGatedFastAPI
from blizzard.hub.api.traces import router as traces_router
from blizzard.hub.api.transcripts import router as transcripts_router
from blizzard.hub.api.users import router as users_router
from blizzard.hub.api.work_source_params import refuse_built_in_work_source
from blizzard.hub.api.work_sources import router as work_sources_router
from blizzard.hub.auth.bootstrap import Superuser
from blizzard.hub.auth.memberships import MembershipRoleRefused
from blizzard.hub.composition import (
    HubServices,
    LiveConfig,
    TenantStores,
    build_config_authoring,
    build_flow_driver,
    build_hub_flags,
    build_process_core,
    build_services,
)
from blizzard.hub.config import AUTH_MODE_OAUTH, ConfigError, HubConfig, StoreUrl, read_legacy_keys
from blizzard.hub.delivery.flow_driver import FlowDriver
from blizzard.hub.domain.config.carry_over import ExistingConfig, ImportResult, LegacyStart, LegacyStartRefused
from blizzard.hub.domain.config.forge_check import ForgeCheck
from blizzard.hub.domain.config.work_sources import BuiltInWorkSource
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.event_rows import missing_key_reason
from blizzard.hub.domain.observability.tracing.attributes import (
    INSTRUMENTATION_SCOPE,
    INSTRUMENTATION_SCOPE_VERSION,
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    resource_attributes,
)
from blizzard.hub.domain.observability.transcripts import TranscriptCaps
from blizzard.hub.domain.projects.declarations import ImportLinks
from blizzard.hub.domain.projects.model import ProjectRequired
from blizzard.hub.domain.runners.host import HostRetired
from blizzard.hub.domain.runners.registration import RunnerRetired
from blizzard.hub.domain.runners.version_window import VersionWindow
from blizzard.hub.domain.sweeps.locks import SweepInProgress
from blizzard.hub.domain.sweeps.names import SweepMode, SweepName
from blizzard.hub.forge.internal.factory import build_forge_registry
from blizzard.hub.forge.registry import ForgeRegistry
from blizzard.hub.layout import SIGNING_KEYS_PARTS
from blizzard.hub.runtime import migration_runner
from blizzard.hub.secrets import KeyCoverage, hub_key_provider, require_hub_key, secret_cipher
from blizzard.hub.secrets.rotation import RotationResult, rotate_keys
from blizzard.hub.store.forge_check_reads import UnreadableStore, read_forge_addresses
from blizzard.hub.store.tenant_guard import install_tenant_guard
from blizzard.hub.tenancy import StoreScope, TenantClosed, TenantId, UnknownTenant
from blizzard.hub.work_sources.internal.factory import WorkSourceEntry
from blizzard.hub.work_sources.source import IWorkSourceRegistry
from blizzard.wire.components import HUB_SCHEMA_COMPONENTS

#: The transcript-event derivation sweep's own interval — a module
#: constant; its own change probe skips the pass when nothing changed.
EVENT_DERIVATION_INTERVAL_SECONDS = 60

#: The close-intent drain sweep's own interval.
CLOSE_DRAIN_INTERVAL_SECONDS = 60

#: The tenant-teardown sweep's own interval — each pass drains every deletion underway by one bounded pass.
TENANT_TEARDOWN_INTERVAL_SECONDS = 30

#: How long the hub's shutdown waits for buffered platform spans to leave.
PLATFORM_TRACING_SHUTDOWN_SECONDS = 3.0


@collaborator
@dataclass(frozen=True)
class Sweep:
    """One sweep's timed passes, stepped once per interval until shutdown (``bzh:steppable-loop``).
    ``reconciler`` is the roster's locked pass over every tenant for ``config_name``. The first pass runs
    immediately, unjittered; ``jitter_seconds`` offsets only the second pass, so sibling sweeps synchronize
    once at boot then decorrelate for good. ``timer`` is the injectable monotonic clock ``run`` measures
    each pass's elapsed time with."""

    config_name: SweepName
    reconciler: Callable[[], object]
    interval_seconds: int
    shutdown: asyncio.Event
    jitter_seconds: float | None = None
    timer: Callable[[], float] = time.monotonic

    @classmethod
    def all(cls, app: FastAPI) -> Iterator[Sweep]:
        """One timed sweep per sweep the hub built — forge status, event derivation, close drain and tenant
        teardown always, trace export when tracing is enabled, fact egress when a directory is configured —
        none for a ``stepped`` one, and none on the store-free app. Each pass opens its ``sweep <name>`` root
        on the app's platform tracer. Each sweep's jitter is drawn uniformly from ``[0, interval_seconds)``
        here so their recurring cadence decorrelates from its second pass on."""
        services: HubServices | None = app.state.services
        if services is None:
            return
        config: HubConfig = app.state.config
        tracer = app.state.platform_tracing.tracer
        for name in services.sweeps.names():
            if config.sweeps.of(name) is SweepMode.STEPPED:
                continue
            interval = _sweep_interval(name, config)
            yield cls(
                name,
                partial(services.sweeps.pass_every_tenant, name, tracer),
                interval,
                app.state.shutdown,
                jitter_seconds=random.uniform(0, interval),
            )

    @property
    def logger_name(self) -> str:
        return self.config_name.logger_name

    @property
    def name(self) -> str:
        """The sweep's own name — the last segment of its logger name."""
        return self.config_name.logger_segment

    async def _wait(self, timeout: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.shutdown.wait(), timeout=timeout)

    async def run(self) -> None:
        """Run a pass immediately, then after ``jitter_seconds`` (default: plain ``interval_seconds``),
        then every ``interval_seconds`` after that, until shutdown. Every wait races ``shutdown``. A pass
        that finds its sweep's lock held is skipped for the interval. Every pass logs its elapsed time; an
        overrun logs a warning naming this sweep."""
        log = get_logger(self.logger_name)
        first = True
        while not self.shutdown.is_set():
            started = self.timer()
            try:
                await asyncio.to_thread(self.reconciler)
            except SweepInProgress:
                log.info("sweep pass skipped", reason="in progress")
            else:
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


def _sweep_interval(name: SweepName, config: HubConfig) -> int:
    """``name``'s timer interval in seconds."""
    match name:
        case SweepName.FORGE_STATUS:
            return config.annotation_interval_seconds
        case SweepName.EVENT_DERIVATION:
            return EVENT_DERIVATION_INTERVAL_SECONDS
        case SweepName.CLOSE_DRAIN:
            return CLOSE_DRAIN_INTERVAL_SECONDS
        case SweepName.TENANT_TEARDOWN:
            return TENANT_TEARDOWN_INTERVAL_SECONDS
        case SweepName.TRACE_EXPORT:
            return config.tracing.sweep_seconds
        case SweepName.EGRESS:
            return config.egress.sweep_seconds


def _refuse_retired_runner(_request: Request, exc: Exception) -> JSONResponse:
    """Map the domain's :class:`RunnerRetired` to a 403 carrying its message."""
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})


def _refuse_retired_host(_request: Request, exc: Exception) -> JSONResponse:
    """Map the domain's :class:`HostRetired` to a 403 carrying its message."""
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})


def _log_unhandled_request_error(request: Request, exc: Exception) -> PlainTextResponse:
    """Write the one JSON line for an exception no handler maps, tagged with the tenant the request
    resolved (read from ``request.state``, so it holds whichever context this runs in), then answer
    the ``500`` Starlette's server-error middleware answers when no handler exists.

    The traceback lands in ``exception`` (``format_exc_info``'s field), so the class is named in ``exception_class``;
    nothing downstream reads the class by another name."""
    tenant = getattr(request.state, "tenant_id", None)
    get_logger("blizzard.hub").error(
        "unhandled request error",
        **({} if tenant is None else {"tenant_id": tenant}),
        method=request.method,
        path=request.url.path,
        exception_class=type(exc).__qualname__,
        exc_info=exc,
    )
    return PlainTextResponse("Internal Server Error", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


def open_hub_engine(db_url: str) -> Engine:
    """The hub process's engine, opened after the tenant statement guard is installed — so a hub started with
    ``BLIZZARD_TENANT_GUARD=1`` checks every statement it runs."""
    install_tenant_guard()
    return create_engine_from_url(db_url)


def rotate_secret_keys(config: HubConfig, environ: Mapping[str, str]) -> RotationResult:
    """The offline ``rotate-key`` wiring: the store and key source opened directly, no app; every open
    tenant's secrets re-sealed."""
    engine = open_hub_engine(config.db_url)
    try:
        tenants = build_process_core(engine).every_open()
        keys = hub_key_provider(environ, data_dir=config.data_dir)
        return rotate_keys([stores.secrets for stores in tenants], keys, secret_cipher)
    finally:
        engine.dispose()


def import_legacy_config(
    config: HubConfig, environ: Mapping[str, str], *, tenant: TenantId | None = None
) -> ImportResult:
    """The offline ``import-legacy`` wiring: the hub key's existence checked before a provider is
    built, then the store and key source opened directly, no app. The import writes into ``tenant``,
    or into the hub's only open tenant when none is named, linking what it creates to that tenant's only project."""
    require_hub_key(environ, data_dir=config.data_dir)
    legacy = read_legacy_keys(config.config_path, environ, strict=config.strict)
    engine = open_hub_engine(config.db_url)
    try:
        core = build_process_core(engine)
        target = _legacy_import_tenant(core.open_tenants(), named=tenant)
        stores = core.open(StoreScope(target))
        authoring = build_config_authoring(
            stores, cipher=secret_cipher(hub_key_provider(environ, data_dir=config.data_dir)), events=None
        )
        existing = ExistingConfig(
            secrets=frozenset(secret.name for secret in stores.secrets.list_all()),
            work_sources=tuple(stores.work_source_records.list_all(include_retired=True)),
            repositories=tuple(stores.repository_records.list_all(include_retired=True)),
            forges=tuple(stores.forge_records.list_all(include_retired=True)),
        )
        return authoring.import_legacy(
            legacy,
            {name: environ[name] for name in legacy.variables},
            stores.config_import.commit_coordinates(),
            existing,
            recorded=stores.config_import.recorded(),
            links=ImportLinks(stores.projects),
        )
    finally:
        engine.dispose()


def check_forges(config: HubConfig) -> ForgeCheck:
    """The offline ``check-forges`` wiring: the store opened directly and read, never written, then judged. A store
    that is not there is refused rather than created."""
    path = StoreUrl(config.db_url).path
    if path is not None and not path.exists():
        raise ConfigError(f"{config.root} holds no store at {path}; nothing to check")
    engine = open_hub_engine(config.db_url)
    try:
        with engine.connect() as conn:
            return _forge_check(conn)
    except (UnreadableStore, SQLAlchemyError) as exc:
        raise ConfigError(f"cannot read the store's forge addresses: {exc}") from exc
    finally:
        engine.dispose()


def _forge_check(conn: Connection) -> ForgeCheck:
    return ForgeCheck.of(read_forge_addresses(conn))


def warn_forge_refusals(engine: Engine) -> None:
    """Log one warning per record set the forge migration would refuse, for the operator reading the startup log. A
    pre-flight aid only: a failure to read is itself a warning, and nothing here stops the hub starting."""
    log = get_logger("blizzard.hub.forge_check")
    try:
        with engine.connect() as conn:
            check = _forge_check(conn)
    except Exception as exc:  # the check never stops startup
        log.warning("forge pre-flight check could not run", error=str(exc))
        return
    for tenant in check.tenants:
        for refusal in tenant.refusals:
            log.warning(
                "forge migration would refuse records",
                tenant_id=tenant.tenant_id,
                tenant=tenant.tenant_name,
                case=refusal.case.value,
                subject=refusal.subject,
                slugs=list(refusal.slugs),
                reason=refusal.reason,
            )


def _legacy_import_tenant(open_tenants: Mapping[TenantId, str], *, named: TenantId | None) -> TenantId:
    """The tenant an import writes into: the named one, which must be open, else the only open one."""
    if named is not None:
        if named not in open_tenants:
            raise ConfigError(f"no open tenant has the id {named}")
        return named
    if len(open_tenants) == 1:
        return next(iter(open_tenants))
    if not open_tenants:
        raise ConfigError("the hub holds no open tenant to import into")
    listing = "; ".join(f"{tenant} ({name})" for tenant, name in sorted(open_tenants.items()))
    raise ConfigError(f"the hub holds several tenants — name the one to import into with --tenant: {listing}")


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    """The framework's 422, unless :func:`sanitized_validation_response` answers instead."""
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
    services: HubServices | None = app.state.services
    if services is not None:
        services.action_loop.start()
    tasks = [asyncio.create_task(sweep.run()) for sweep in Sweep.all(app)]
    flow_driver: FlowDriver | None = app.state.flow_driver
    if flow_driver is not None:
        tasks.append(asyncio.create_task(flow_driver.run(app.state.shutdown)))
    yield
    app.state.shutdown.set()
    for task in tasks:
        await task
    if services is not None:
        services.close_tenant_objects()
        services.action_loop.stop()


def create_app(
    config: HubConfig,
    *,
    readiness: ReadinessService | None = None,
    services: HubServices | None = None,
    platform_tracing: IPlatformTracing | None = None,
    flags: Flags,
) -> FastAPI:
    """Build a fully wired hub app from resolved config and the hub's resolved ``flags``.

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
    app.state.flags = flags
    app.state.readiness = readiness
    app.state.services = services
    # Set on shutdown by ``_lifespan``; every SSE stream races it.
    app.state.shutdown = asyncio.Event()
    # The hosted hub's flow driver, set by ``build_hosted_app`` alone; ``_lifespan`` runs it beside the sweeps.
    app.state.flow_driver = None

    app.add_exception_handler(RunnerRetired, _refuse_retired_runner)
    app.add_exception_handler(HostRetired, _refuse_retired_host)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(TenantClosed, refuse_closed_tenant)
    app.add_exception_handler(UnknownTenant, refuse_closed_tenant)
    app.add_exception_handler(ProjectRequired, refuse_project_required)
    app.add_exception_handler(MembershipRoleRefused, refuse_membership_role)
    app.add_exception_handler(BuiltInWorkSource, refuse_built_in_work_source)
    app.add_exception_handler(FlagOffError, refuse_flag_off)
    app.add_exception_handler(Exception, _log_unhandled_request_error)

    # API routers first, so /api/* always wins over the web mount at /.
    app.include_router(health_router)
    app.include_router(flags_router)
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
    app.include_router(hosts_router)
    app.include_router(spend_router)
    app.include_router(users_router)
    app.include_router(members_router)
    app.include_router(member_grants_router)
    app.include_router(hub_admins_router)
    app.include_router(invitations_router)
    app.include_router(transcripts_router)
    app.include_router(analytics_router)
    app.include_router(traces_router)
    app.include_router(egress_router)
    app.include_router(admin_tenants_router)
    app.include_router(admin_events_router)
    app.include_router(sweeps_router)
    app.include_router(admin_sweeps_router)
    app.include_router(work_sources_router)
    app.include_router(repositories_router)
    app.include_router(forges_router)
    app.include_router(projects_router)
    # A project's gardening, nested under `/api/projects/{project}` after the project routes themselves.
    app.include_router(project_scopes_router)
    app.include_router(project_routines_router)
    app.include_router(project_findings_router)
    app.include_router(project_garden_proposals_router)
    app.include_router(project_garden_runs_router)
    # Outside the fleet gate — pinned by `tests/test_fleet_identity.py`'s tokenless-401 case.
    app.include_router(fleet_identity_router)
    # The runner-authenticated fleet router — a fleet verb is authenticated
    # *because of where it is mounted*; see `blizzard.hub.api.fleet`.
    app.include_router(fleet_host_router)
    app.include_router(fleet_router)
    # The claim route is the other `/api/fleet` path outside that gate's retirement refusal.
    app.include_router(fleet_claim_router)

    Frontend.embedded("hub", app_name="blizzard-hub").mount(app)
    install_schema_components(app, HUB_SCHEMA_COMPONENTS.models, HUB_SCHEMA_COMPONENTS.enums)

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
    oauth_http_client: httpx2.Client | None = None,
    version_window: VersionWindow,
) -> FastAPI:
    """The ``host`` composition root: open the store and wire every fleet seam.

    ``platform_tracing`` defaults to the handle ``[tracing]`` and the OTLP environment decide.
    ``oauth_http_client`` replaces the OAuth providers' client. Either way, the engine and every outbound client are
    instrumented through it. ``version_window`` judges each registering runner's version."""
    platform_tracing = platform_tracing or build_platform_tracing(
        config.tracing,
        os.environ,
        resource=resource_attributes(os.environ, __version__),
        scope=PLATFORM_INSTRUMENTATION_SCOPE,
        scope_version=PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    )
    engine = open_hub_engine(config.db_url)
    platform_tracing.instrument_engine(engine)
    reader = SqlAlchemyStoreStatusReader(engine)
    expected = migration_runner(config).script_head()
    readiness = ReadinessService(reader=reader, expected_revision=expected)

    core = build_process_core(engine)
    # Minted here on first start when no key source exists yet.
    secret_keys = hub_key_provider(os.environ, data_dir=config.data_dir)

    def work_source_registry(stores: TenantStores, live: LiveConfig) -> IWorkSourceRegistry:
        return WorkSourceEntry.registry(
            records=stores.work_source_records,
            forges=stores.forge_records,
            objects=live.objects,
            secrets=live.secrets,
            users=core.users,
            work_item_store=stores.work_item_store,
            edits=stores.work_item_edits,
            resolution=stores.garden_proposal_resolution,
            close_forge_writes_enabled=config.close_forge_writes_enabled,
            instrument_client=platform_tracing.instrument_client,
        )

    def forge_registry(stores: TenantStores, live: LiveConfig) -> ForgeRegistry:
        return build_forge_registry(
            records=stores.repository_records,
            forges=stores.forge_records,
            objects=live.objects,
            secrets=live.secrets,
            clock=stores.clock,
            instrument=platform_tracing.instrument_client,
        )

    tracing = TracingSettings.of(os.environ)

    # The provider-login seam is built only under `oauth`: under `none`
    # there is no login mechanism to serve.
    oauth_providers = config.auth.oauth_providers if config.auth.mode == AUTH_MODE_OAUTH else ()
    # The IdP signing-key lifecycle — likewise built only under `oauth`; a
    # `none` deployment never touches disk for a keypair it will never mint or publish.
    signing_keys_dir = config.data_dir.joinpath(*SIGNING_KEYS_PARTS) if config.auth.mode == AUTH_MODE_OAUTH else None
    oauth_client = oauth_http_client or httpx2.Client(timeout=15.0)
    platform_tracing.instrument_client(oauth_client)

    services = build_services(
        core,
        work_sources=work_source_registry,
        forges=forge_registry,
        public_url=config.public_url,
        oauth_providers=oauth_providers,
        oauth_http_client=oauth_client,
        signing_keys_dir=signing_keys_dir,
        secret_keys=secret_keys,
        trusted_proxies=TrustedProxies.parse(config.trusted_proxies),
        login_throttle=config.auth.login_throttle,
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
        annotation_interval_seconds=config.annotation_interval_seconds,
        action_pass_seconds=config.action_pass_seconds,
        version_window=version_window,
    )
    # Only once the store is at the expected schema head: a store mid-migration must
    # fail *readiness*, not *boot* (pinned: `test_ready_probe_false_on_unmigrated_store`).
    flow_driver: FlowDriver | None = None
    if readiness.evaluate().ready:
        tenants = core.every_open()
        try:
            LegacyStart.of(
                [stores.config_import for stores in tenants],
                read_legacy_keys(config.config_path, os.environ, strict=config.strict),
            ).check()
        except LegacyStartRefused as refusal:
            raise ConfigError(str(refusal)) from refusal
        OrphanedProviders.of(config, services).check()
        warn_forge_refusals(engine)
        KeyCoverage.of([stores.secrets for stores in tenants], secret_keys).check()
        # Before the hub serves, in each open tenant: the slot first, then the actionless escalations, so the
        # flow driver's boot reconcile finds those chunks escalated and submits none of them.
        flow_driver = build_flow_driver(services)
        for stores in tenants:
            hub_node = services.tenants.open_services(stores.scope).hub_node
            hub_node.release_orphaned_slots()
            hub_node.escalate_actionless()
            flow_driver.recover_turns(stores.scope.tenant)
        Superuser(email=config.auth.superuser, auth=services.auth).ensure()
        _announce_started(expected, services)
        _announce_rejected_tracing(tracing, services)
        _announce_rejected_egress(config.egress, services)
    app = create_app(
        config,
        readiness=readiness,
        services=services,
        platform_tracing=platform_tracing,
        flags=build_hub_flags(config),
    )
    # `host` disposes this on `app.state` — carried here.
    app.state.engine = engine
    # Only a store at the expected head is reconciled; `_lifespan` runs the driver it finds here.
    app.state.flow_driver = flow_driver
    return app


def _announce_started(revision: str | None, services: HubServices) -> None:
    """Every start that reaches a migrated store records one ``hub-started`` in the hub's own event log, naming the
    version and the store revision it serves."""
    services.hub_event_log.record(
        kind="hub-started",
        message=f"hub {__version__} started on store revision {revision}",
        detail={"version": __version__, "revision": revision},
        at=services.clock.now(),
    )


def _announce_rejected_tracing(tracing: TracingSettings, services: HubServices) -> None:
    """A rejected tracing setting never stops startup: the hub serves with tracing off and
    records one ``trace-config-rejected`` in the hub's own event log per start, naming the setting."""
    if tracing.state != "rejected":
        return
    services.hub_event_log.record(
        kind="trace-config-rejected",
        message=tracing.rejection_message("hub"),
        detail={"setting": tracing.setting, "value": tracing.value},
        at=services.clock.now(),
    )


def _announce_rejected_egress(egress: EgressConfig, services: HubServices) -> None:
    """An export the hub cannot fully honor never stops startup, and records one ``egress-config-rejected`` in the
    hub's own event log per start. Without its format's extra the export is off and the event names the format;
    without the path hash key a hashing file path policy needs, only the events dataset is off and the event names the
    variable — never a key value."""
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
    services.hub_event_log.record(
        kind="egress-config-rejected",
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


def create_app_for_export(flag_registry: FlagRegistry) -> FastAPI:
    """Build the app with throwaway config for OpenAPI export (no store, no dirs), its flags at ``flag_registry``'s
    defaults."""
    from pathlib import Path

    return create_app(
        HubConfig(root=Path("."), db_url="sqlite://"), flags=resolve_flags(flag_registry, "hub", {}).flags
    )
