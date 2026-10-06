"""Composition-root threading (``bzh:dependency-injection``) —.

Each case pins one ``RunnerConfig`` key reaching the collaborator built from it: an
unthreaded key is read from the operator's toml and dropped, which no other tier sees.
Both roots that build a ``ClaudeCodeAdapter`` are covered.
"""

from __future__ import annotations

import json
import shlex
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import blizzard.runner.app as runner_app
import blizzard.runner.composition as composition
import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.platform_tracing.handle import build_platform_tracing
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.runner.app import build_hosted_app, create_app
from blizzard.runner.composition import build_runner_process
from blizzard.runner.config import (
    CONFIG_FILENAME,
    LEGACY_ANTHROPIC_SLUG,
    ConfigError,
    RunnerConfig,
    SubscriptionDeclaration,
)
from blizzard.runner.environments.internal.basic_provider import BasicWorkspaceProvider
from blizzard.runner.environments.provider import WorkspaceRepo
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.adapter import AcquiredEnvironment, WorkerPreamble
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.claude_code.adapter import ClaudeCodeAdapter
from blizzard.runner.harness.claude_code.section import ClaudeCodeSection
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.opencode.adapter import OpenCodeAdapter
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.wiring import publish_harness_bundle
from blizzard.runner.leases import NewLease
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.tick import tick
from blizzard.runner.loop_wiring import LoopWiring, PeriodicDriver, ResumeMarking, _LazyUsageHttpClient
from blizzard.runner.stores import RunnerReadStores
from blizzard.runner.subscriptions.credential_renewer import RenewalOutcome, RenewalOutcomeKind
from blizzard.runner.subscriptions.internal.anthropic_subscription_sampler import AnthropicSubscriptionSampler
from blizzard.runner.subscriptions.internal.openai_credential_renewer import OpenAICredentialRenewer
from blizzard.runner.subscriptions.internal.openai_subscription_sampler import OpenAISubscriptionSampler
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC, PROVIDER_OPENAI
from tests.harness_sections import sections
from tests.runner_fakes import (
    FakeHub,
    FakeProbe,
    loop_context,
    loop_graph,
    make_store,
    make_stores,
    migrated_store_at,
)
from tests.support import InMemoryTraceExporter

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _migrated_default_store(tmp_path: Path) -> None:
    """Every graph a case builds over ``RunnerConfig.default_db_url(tmp_path)`` boots over a migrated store."""
    migrated_store_at(RunnerConfig.default_db_url(tmp_path))


@pytest.mark.unit
def test_basic_provider_wired_with_shared_absolute_root_and_capacity(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_provider="basic",
        workspace_root="scratch",
        workspace_repos=(WorkspaceRepo("toy", "file:///tmp/toy.git"),),
        max_environments=3,
    )
    hosted = build_hosted_app(config).app
    with loop_context(config) as loop:
        assert isinstance(hosted.state.workspace_provider, BasicWorkspaceProvider)
        assert isinstance(loop.provider, BasicWorkspaceProvider)
        assert loop.config.env_capacity == 3
        assert loop.config.workspace_root == ""
        assert loop.usage.workspace_root == loop.config.workspace_root
        assert hosted.state.workspace_provider._root == loop.provider._root == tmp_path / "scratch"
        assert SpawnCwd.of_session(loop.config.workspace_root, str(tmp_path / "scratch" / "chunk")) == str(
            tmp_path / "scratch" / "chunk"
        )
        assert hosted.state.runner_status._env_pool == ()


@pytest.mark.unit
def test_basic_default_root_is_shared_with_transcript_usage(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_provider="basic",
        workspace_repos=(WorkspaceRepo("toy", "file:///tmp/toy.git"),),
    )

    with loop_context(config) as loop:
        assert loop.config.workspace_root == ""
        assert loop.usage.workspace_root == loop.config.workspace_root


def _seeded_running_lease_store(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A store holding one live, session-bearing build lease — the shape both restart-resume
    hooks mark, seeded exactly as ``tests/test_runner_restart_resume.py`` does."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    return store


@pytest.mark.unit
def test_loop_wiring_threads_worker_env_passthrough_into_the_adapter(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        worker_env_passthrough=("MY_HARNESS_QUIRK", "ANOTHER_VAR"),
    )

    with loop_context(config) as ctx:
        harness = ctx.harnesses.lifecycle(CLAUDE_CODE_HARNESS_ID)

        assert isinstance(harness, ClaudeCodeAdapter)
        assert harness._worker_env.passthrough == ("MY_HARNESS_QUIRK", "ANOTHER_VAR")


@pytest.mark.unit
def test_loop_wiring_threads_external_usage_credentials_path_into_the_sampler(tmp_path: Path) -> None:
    """An unthreaded override leaves every daemon this root builds reading the
    sampler's own default credentials path and reaching the real Anthropic endpoint
    . The sampler is a separate seam from the harness adapter —
    selected from the config's resolved (declared-or-synthesized) subscription list, not
    threaded through ``ClaudeCodeAdapter`` anymore, and keyed by slug since a
    runner may declare several. The legacy table's own ``external_usage_sample_interval_seconds``
    reaches that same synthesized declaration's ``sample_interval_seconds`` — the cadence
    the tick's per-slug gate reads (``ExternalUsageSample``), not a runner-wide setting."""
    scratch = str(tmp_path / "scratch-credentials.json")
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        external_usage_credentials_path=scratch,
        external_usage_sample_interval_seconds=123,
    )

    with loop_context(config) as ctx:
        assert isinstance(ctx.harnesses.lifecycle(CLAUDE_CODE_HARNESS_ID), ClaudeCodeAdapter)
        assert [s.slug for s in ctx.subscriptions] == [LEGACY_ANTHROPIC_SLUG]
        resolved = ctx.subscriptions[0]
        assert resolved.sample_interval_seconds == 123
        assert isinstance(resolved.sampler, AnthropicSubscriptionSampler)
        assert resolved.sampler._credentials_path == scratch


@pytest.mark.unit
def test_the_loops_declared_subscriptions_share_one_root_owned_http_client(tmp_path: Path) -> None:
    """The laziness invariant ("a sampler that never samples opens no pool") lives at the
    composition root: every declared subscription's sampler draws
    from the *same* shared client, not one each."""
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        subscriptions=(
            SubscriptionDeclaration(slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC),
            SubscriptionDeclaration(slug="codex", name="Codex", provider=PROVIDER_OPENAI),
        ),
    )

    with loop_context(config) as ctx:
        assert [s.slug for s in ctx.subscriptions] == ["anthropic", "codex"]
        first_sampler, second_sampler = (s.sampler for s in ctx.subscriptions)
        assert isinstance(first_sampler, AnthropicSubscriptionSampler)
        assert isinstance(second_sampler, OpenAISubscriptionSampler)
        assert first_sampler._http_client is ctx.usage_http_client
        assert second_sampler._http_client is ctx.usage_http_client


@pytest.mark.unit
def test_a_tick_with_an_openai_subscription_never_invokes_a_renewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renewal is composed into the host's own pass, never the tick: a whole tick over an
    ``openai`` declaration samples it but never reaches either half of the renewer seam."""
    calls: list[str] = []

    def _due(self: OpenAICredentialRenewer) -> bool:
        calls.append("renewal_due")
        return True

    def _renew(self: OpenAICredentialRenewer) -> RenewalOutcome:
        calls.append("renew")
        return RenewalOutcome(RenewalOutcomeKind.RENEWED)

    monkeypatch.setattr(OpenAICredentialRenewer, "renewal_due", _due)
    monkeypatch.setattr(OpenAICredentialRenewer, "renew", _renew)
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        subscriptions=(
            SubscriptionDeclaration(
                slug="codex", name="Codex", provider=PROVIDER_OPENAI, credentials_path=str(tmp_path / "auth.json")
            ),
        ),
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    make_store(config.db_url)  # schema only; the graph opens its own engine over it

    with loop_graph(config) as graph:
        ctx = LoopWiring(config, "", "", None).context(FakeHub(), graph)
        try:
            tick(ctx)
        finally:
            ctx.usage_http_client.close()
        assert graph.stores.usage.last_external_usage_attempt_at("codex") is not None  # the tick sampled it
        assert graph.credential_renewal is not None  # composed for `runner host` alone

    assert calls == []


@pytest.mark.unit
def test_no_renewal_pass_is_composed_when_no_provider_binds_a_renewer(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        subscriptions=(SubscriptionDeclaration(slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC),),
    )

    with loop_graph(config) as graph:
        assert graph.credential_renewal is None


@pytest.mark.unit
def test_the_loops_usage_http_client_is_not_built_merely_by_composing_the_context(tmp_path: Path) -> None:
    """No declared subscription ever samples in this test — composing the context alone
    must open no real connection pool; only calling the provider
    builds one."""
    config = RunnerConfig(
        root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path), workspace_root=str(tmp_path / "workspace")
    )

    with loop_context(config) as ctx:
        assert isinstance(ctx.usage_http_client, _LazyUsageHttpClient)
        assert ctx.usage_http_client._client is None


@pytest.mark.unit
def test_the_loops_usage_http_client_owner_closes_a_client_it_actually_built(tmp_path: Path) -> None:
    """Closing the context's shared client closes the real ``httpx.Client`` a sampler built."""
    config = RunnerConfig(
        root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path), workspace_root=str(tmp_path / "workspace")
    )

    with loop_context(config) as ctx:
        assert isinstance(ctx.usage_http_client, _LazyUsageHttpClient)
        client = ctx.usage_http_client()
        assert not client.is_closed

        ctx.usage_http_client.close()

        assert client.is_closed


@pytest.mark.unit
def test_loop_wiring_threads_the_worker_settings_path_and_permission_mode(tmp_path: Path) -> None:
    """The configured worker settings path and permission mode reach the Claude Code adapter."""
    settings = str(tmp_path / "worker-settings.json")
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        harness_sections=sections(ClaudeCodeSection(worker_settings_path=settings, permission_mode="acceptEdits")),
    )

    with loop_context(config) as ctx:
        harness = ctx.harnesses.lifecycle(CLAUDE_CODE_HARNESS_ID)

        assert isinstance(harness, ClaudeCodeAdapter)
        assert harness._settings_path == settings
        assert harness._permission_override == "acceptEdits"


@pytest.mark.unit
def test_process_graph_delivers_the_published_snapshot_to_the_claude_code_adapter(tmp_path: Path) -> None:
    """The snapshot's real (resolved) path, never the ``current`` symlink, reaches the adapter."""
    bundle = tmp_path / "bundle"
    (bundle / "claude-code").mkdir(parents=True)
    (bundle / "claude-code" / "mcp.json").write_text("{}")
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        harness_sections=sections(ClaudeCodeSection(worker_settings_path=str(tmp_path / "worker-settings.json"))),
    )
    snapshot = publish_harness_bundle(bundle, tmp_path)

    graph = build_runner_process(config, bundle=snapshot)
    try:
        adapter = graph.harnesses.lifecycle(CLAUDE_CODE_HARNESS_ID)
        assert isinstance(adapter, ClaudeCodeAdapter)
        argv = adapter._settings_args()
    finally:
        graph.close()

    effective = snapshot.path.resolve() / "claude-code"
    assert argv == ["--settings", str(effective / "settings.json"), f"--mcp-config={effective / 'mcp.json'}"]


@pytest.mark.unit
@pytest.mark.parametrize("autonomy", list(Autonomy))
def test_registry_threads_the_autonomy_to_both_bindings(tmp_path: Path, autonomy: Autonomy) -> None:
    """The constructor path is the only way ``autonomy`` reaches a binding."""
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        autonomy=autonomy,
    )

    with loop_context(config) as ctx:
        claude = ctx.harnesses.lifecycle(CLAUDE_CODE_HARNESS_ID)
        opencode = ctx.harnesses.lifecycle(OPENCODE_HARNESS_ID)

        assert isinstance(claude, ClaudeCodeAdapter)
        assert isinstance(opencode, OpenCodeAdapter)
        assert claude._autonomy is autonomy
        assert opencode._autonomy is autonomy


@pytest.mark.unit
def test_hosted_app_threads_the_worker_settings_path_and_permission_mode(tmp_path: Path) -> None:
    """The hosted app builds its own adapter, and the takeover command it composes
    asserts the permission mode — a second threading of the same two keys,
    which the loop's own root does not cover."""
    settings = str(tmp_path / "worker-settings.json")
    (tmp_path / CONFIG_FILENAME).write_text(
        f'db_url = "{RunnerConfig.default_db_url(tmp_path)}"\n'
        f'worker_settings_path = "{settings}"\n'
        'harness_permission_mode = "acceptEdits"\n'
    )

    app = build_hosted_app(RunnerConfig.load(tmp_path)).app

    harness = app.state.harnesses.lifecycle(CLAUDE_CODE_HARNESS_ID)
    assert isinstance(harness, ClaudeCodeAdapter)
    assert harness._settings_path == settings
    assert " --permission-mode acceptEdits" in harness.resume_command("/w", "s-1", attended=True)


@pytest.mark.unit
def test_loop_wiring_threads_runner_dir_from_the_resolved_root(tmp_path: Path) -> None:
    """The wrapped takeover command needs ``LoopConfig.runner_dir`` to
    mirror ``RunnerConfig``'s resolved ``root``. Routed through ``RunnerConfig.load()``
    with an un-resolved ``..``-bearing path, since a bare ``tmp_path`` already resolves."""
    real_root = tmp_path / "runner"
    real_root.mkdir()
    (real_root / CONFIG_FILENAME).write_text(f'db_url = "{RunnerConfig.default_db_url(real_root)}"\n')
    unresolved_root = tmp_path / "nested" / ".." / "runner"

    config = RunnerConfig.load(unresolved_root)
    migrated_store_at(config.db_url)
    with loop_context(config) as ctx:
        assert ".." not in ctx.config.runner_dir
        assert ctx.config.runner_dir == str(real_root.resolve())


@pytest.mark.unit
def test_loop_wiring_of_defaults_to_no_broker(tmp_path: Path) -> None:
    """A loop-only caller (``blizzard runner tick``) threads no
    broker, so its ``LoopContext`` publishes nothing — the disposition for the
    store-free/export app and every other path with no stream to feed."""
    config = RunnerConfig(root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path))

    with loop_context(config) as ctx:
        assert ctx.events is None


@pytest.mark.unit
def test_loop_wiring_of_threads_the_broker_into_the_loop_context(tmp_path: Path) -> None:
    """The ``host`` verb's one broker reaches ``LoopContext`` — the
    seam's publish call sites read off."""
    config = RunnerConfig(root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path))
    broker = EventBroker()

    with loop_context(config, broker=broker) as ctx:
        assert ctx.events is broker


@pytest.mark.unit
def test_periodic_driver_threads_the_graph_broker_into_its_own_loop_wiring(tmp_path: Path) -> None:
    """The broker the graph carries reaches ``PeriodicDriver``'s own ``LoopWiring``."""
    config = RunnerConfig(
        root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path), workspace_root=str(tmp_path / "workspace")
    )
    broker = EventBroker()

    with loop_graph(config, events=broker) as graph:
        driver = PeriodicDriver(config, interval_seconds=30.0, process_graph=graph)

    assert driver._wiring.events is broker


@pytest.mark.unit
def test_periodic_driver_defaults_to_no_broker(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path), workspace_root=str(tmp_path / "workspace")
    )

    with loop_graph(config) as graph:
        driver = PeriodicDriver(config, interval_seconds=30.0, process_graph=graph)

    assert driver._wiring.events is None


@pytest.mark.unit
def test_context_health_is_the_graphs_own_cache(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path), workspace_root=str(tmp_path / "workspace")
    )

    with loop_graph(config) as graph:
        ctx = LoopWiring(config, "", "").context(FakeHub(), graph)
        try:
            assert ctx.harness_health is graph.health
        finally:
            ctx.usage_http_client.close()


@pytest.mark.unit
def test_hosted_app_threads_the_broker_into_create_apps_seam_list(tmp_path: Path) -> None:
    """The ``host`` verb's broker reaches ``app.state.events`` — the
    seam the stream route (``runner/api/events.py``) reads off the served app."""
    (tmp_path / CONFIG_FILENAME).write_text(f'db_url = "{RunnerConfig.default_db_url(tmp_path)}"\n')
    broker = EventBroker()

    app = build_hosted_app(RunnerConfig.load(tmp_path), events=broker).app

    assert app.state.events is broker


@pytest.mark.unit
def test_hosted_app_exposes_the_same_harness_health_cache_it_wires_into_create_app(tmp_path: Path) -> None:
    """``HostedApp.harness_health`` is the instance ``app.state`` carries."""
    (tmp_path / CONFIG_FILENAME).write_text(f'db_url = "{RunnerConfig.default_db_url(tmp_path)}"\n')

    hosted = build_hosted_app(RunnerConfig.load(tmp_path))

    assert hosted.app.state.harness_health is hosted.harness_health


@pytest.mark.unit
def test_tick_once_over_a_supplied_graph_traces_through_it_and_leaves_it_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A driver of repeated ticks hands one graph in: its platform tracer reaches the tick's context, its
    hub client is instrumented, and the tick does not close it."""
    config = RunnerConfig(root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path), hub_url="http://hub.test:8421")
    graph = build_runner_process(config)
    ticked: list[object] = []
    instrumented: list[httpx.Client | httpx.AsyncClient] = []
    monkeypatch.setattr(loop_wiring, "tick", lambda ctx: ticked.append(ctx.tracer))
    monkeypatch.setattr(graph.platform_tracing, "instrument_client", instrumented.append)
    try:
        LoopWiring.of(config).tick_once(process=graph)
        LoopWiring.of(config).tick_once(process=graph)
        assert ticked == [graph.platform_tracing.tracer] * 2
        assert len(instrumented) == 2
        for client in instrumented:
            assert type(client) is httpx.Client
            assert client.base_url == httpx.URL(config.hub_url)
        assert graph.executor.submit(lambda: "open").result() == "open"
    finally:
        graph.close()


@pytest.mark.unit
def test_hosted_graph_shares_process_scoped_dependencies_with_loop_and_recovery(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path))
    graph = build_runner_process(config, events=EventBroker())
    hosted = build_hosted_app(config, process_graph=graph)
    try:
        ctx = LoopWiring(config, "", "", graph.events).context(FakeHub(), graph)
        try:
            assert hosted.app.state.workspace_provider is ctx.provider is graph.provider
            assert hosted.app.state.harnesses is ctx.harnesses is graph.harnesses
            assert hosted.app.state.harness_health is ctx.harness_health is graph.health
            assert hosted.app.state.events is ctx.events is graph.events
            assert hosted.app.state.clock is ctx.clock is hosted.resume.clock is graph.clock
            assert hosted.resume.process is ctx.process is graph.process
            assert ctx.stores is graph.stores
        finally:
            ctx.usage_http_client.close()
    finally:
        hosted.close()
        graph.close()


@pytest.mark.unit
@pytest.mark.parametrize("owned_graph", [True, False])
def test_failed_hosted_app_wiring_closes_partial_clients_and_only_its_own_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owned_graph: bool
) -> None:
    config = RunnerConfig(root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path))
    graph = build_runner_process(config)
    disposed: list[str] = []
    real_dispose = graph.engine.dispose

    def dispose() -> None:
        disposed.append("engine")
        real_dispose()

    monkeypatch.setattr(graph.engine, "dispose", dispose)
    monkeypatch.setattr(runner_app, "build_runner_process", lambda *_args, **_kwargs: graph)
    original_client = httpx.Client
    clients: list[httpx.Client] = []

    def client(*args, **kwargs):  # type: ignore[no-untyped-def]
        result = original_client(*args, **kwargs)
        clients.append(result)
        return result

    def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("app wiring failed")

    monkeypatch.setattr(runner_app.httpx, "Client", client)
    monkeypatch.setattr(runner_app, "create_app", fail)
    try:
        with pytest.raises(RuntimeError, match="app wiring failed"):
            if owned_graph:
                build_hosted_app(config)
            else:
                build_hosted_app(config, process_graph=graph)
        assert len(clients) == 3
        assert all(instance.is_closed for instance in clients)
        assert disposed == (["engine"] if owned_graph else [])
    finally:
        if not owned_graph:
            graph.close()


@pytest.mark.unit
def test_hosted_app_defaults_to_no_broker(tmp_path: Path) -> None:
    """The disposition for every ``build_hosted_app`` caller but ``host`` itself — none
    exists yet, but the default must stay absent so a future one degrades safely."""
    (tmp_path / CONFIG_FILENAME).write_text(f'db_url = "{RunnerConfig.default_db_url(tmp_path)}"\n')

    app = build_hosted_app(RunnerConfig.load(tmp_path)).app

    assert app.state.events is None


@pytest.mark.unit
def test_create_app_for_export_stays_broker_less(tmp_path: Path) -> None:
    """The OpenAPI-export/store-free app is one of the paths names as having no
    stream to feed — unlike the hub, ``create_app`` never conjures a broker on its own."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")

    app = create_app(config)

    assert app.state.events is None


@pytest.mark.unit
def test_periodic_driver_resolves_prompts_eagerly_at_construction(tmp_path: Path) -> None:
    """A configured-but-missing ``runner_prompt_file`` raises ``ConfigError`` from the
    constructor."""
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        runner_prompt_file="does-not-exist.md",
    )

    with loop_graph(config) as graph, pytest.raises(ConfigError):
        PeriodicDriver(config, interval_seconds=30.0, process_graph=graph)


@pytest.mark.unit
def test_resume_marking_on_shutdown_marks_via_its_injected_clock(tmp_path: Path) -> None:
    """No real process and no wall clock: the marking hook is driven entirely off a
    virtual clock and a scripted probe, both supplied as constructor dependencies."""
    store = _seeded_running_lease_store(tmp_path)
    marking = ResumeMarking(make_stores(store), FixedClock(_NOW), FakeProbe())

    marked = marking.on_shutdown()

    assert marked == 1
    assert store.resume_intent_lease_ids() == {"lease_1"}


@pytest.mark.unit
def test_resume_marking_on_shutdown_drains_the_marked_leases_recorded_group(tmp_path: Path) -> None:
    """`on_shutdown` doesn't just mark — it SIGINTs the marked lease's own recorded group
    right after, through the same injected clock and an injected sleep."""
    store = _seeded_running_lease_store(tmp_path)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
        pgid=100,
    )
    probe = FakeProbe(alive={(100, "start-100")}, groups_alive={100})
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        probe.groups_alive.discard(100)  # the worker exits on the SIGINT this drain just sent

    marking = ResumeMarking(make_stores(store), FixedClock(_NOW), probe, sleep)

    marked = marking.on_shutdown()

    assert marked == 1
    assert probe.interrupted_groups == [100]
    assert probe.killed_groups == []  # exited on its own — never reached the SIGKILL fallback
    assert sleeps == [0.5]


@pytest.mark.unit
def test_resume_marking_on_startup_marks_via_its_injected_clock_and_probe(tmp_path: Path) -> None:
    store = _seeded_running_lease_store(tmp_path)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)  # was actively working when killed
    marking = ResumeMarking(make_stores(store), FixedClock(_NOW), FakeProbe(alive=set()))  # the pid is dead

    marked = marking.on_startup()

    assert marked == 1
    assert store.resume_intent_lease_ids() == {"lease_1"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("claude_code_enabled", "opencode_enabled", "expected"),
    [(False, True, ("opencode",)), (True, False, ("claude_code",))],
)
def test_both_roots_boot_with_a_single_enabled_harness(
    tmp_path: Path, claude_code_enabled: bool, opencode_enabled: bool, expected: tuple[str, ...]
) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_provider="basic",
        workspace_root="scratch",
        workspace_repos=(WorkspaceRepo("toy", "file:///tmp/toy.git"),),
        harness_sections=sections(
            ClaudeCodeSection(enabled=claude_code_enabled), OpenCodeSection(enabled=opencode_enabled)
        ),
    )

    build_hosted_app(config)
    with loop_context(config) as loop:
        assert loop.harnesses.known_harnesses == expected


@pytest.mark.unit
def test_loop_wiring_of_delivers_its_bundle_to_the_graph_it_builds(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "claude-code").mkdir(parents=True)
    (bundle / "claude-code" / "mcp.json").write_text("{}")
    config = RunnerConfig(
        root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path), workspace_root=str(tmp_path / "workspace")
    )
    snapshot = publish_harness_bundle(bundle, tmp_path)

    def settings_argv(ctx: LoopContext) -> list[str]:
        adapter = ctx.harnesses.lifecycle(CLAUDE_CODE_HARNESS_ID)
        assert isinstance(adapter, ClaudeCodeAdapter)
        return adapter._settings_args()

    argv = LoopWiring.of(config, bundle=snapshot)._with_context(settings_argv)

    settings = snapshot.path.resolve() / "claude-code" / "settings.json"
    assert argv[:2] == ["--settings", str(settings)]


def _harness_telemetry_env(
    tmp_path: Path, *, harness_telemetry: bool, platform: bool = True, settings_env: dict[str, str] | None = None
) -> dict[str, str]:
    """The identity env of the Claude Code adapter the production route builds from ``[tracing]``."""
    settings = tmp_path / "worker-settings.json"
    settings.write_text(json.dumps({"env": settings_env or {}}))
    tracing = TracingConfig(platform=platform, harness_telemetry=harness_telemetry)
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        harness_sections=sections(ClaudeCodeSection(worker_settings_path=str(settings))),
        tracing=tracing,
    )
    environ = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}
    handle = build_platform_tracing(
        tracing,
        environ,
        resource={},
        scope="test",
        scope_version="0",
        exporter=InMemorySpanExporter(),
    )
    graph = build_runner_process(config, environ=environ, platform_tracing=handle)
    try:
        adapter = graph.harnesses.lifecycle(CLAUDE_CODE_HARNESS_ID)
        assert isinstance(adapter, ClaudeCodeAdapter)
        preamble = WorkerPreamble(
            environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
            lease_id="lease_1",
            local_api_url="http://127.0.0.1:8431",
            lease_token="tok",
        )
        return adapter.identity_env(preamble, "ch_1", "sess")
    finally:
        graph.close()


def _harness_telemetry_names(env: dict[str, str]) -> list[str]:
    return sorted(n for n in env if n.startswith(("OTEL_", "CLAUDE_CODE_ENABLE_TELEMETRY", "CLAUDE_CODE_ENHANCED")))


@pytest.mark.unit
def test_process_graph_points_every_captured_signal_at_the_runner_from_the_toml_key(tmp_path: Path) -> None:
    env = _harness_telemetry_env(tmp_path, harness_telemetry=True)
    assert env["OTEL_TRACES_EXPORTER"] == env["OTEL_METRICS_EXPORTER"] == env["OTEL_LOGS_EXPORTER"] == "otlp"
    assert env["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] == "http://127.0.0.1:8431/v1/logs"


@pytest.mark.unit
def test_process_graph_yields_the_signal_the_worker_settings_env_names(tmp_path: Path) -> None:
    env = _harness_telemetry_env(tmp_path, harness_telemetry=True, settings_env={"OTEL_METRICS_EXPORTER": "none"})
    assert "OTEL_METRICS_EXPORTER" not in env
    assert env["OTEL_LOGS_EXPORTER"] == "otlp"


@pytest.mark.unit
@pytest.mark.parametrize(("harness_telemetry", "platform"), [(False, True), (True, False)])
def test_process_graph_sets_no_harness_telemetry_variable_without_both_switches(
    tmp_path: Path, harness_telemetry: bool, platform: bool
) -> None:
    env = _harness_telemetry_env(tmp_path, harness_telemetry=harness_telemetry, platform=platform)
    assert _harness_telemetry_names(env) == []


@pytest.mark.unit
def test_read_stores_narrow_lease_traces_to_the_same_instance(tmp_path: Path) -> None:
    stores = make_stores(make_store(f"sqlite:///{tmp_path / 'runner.db'}"))
    assert RunnerReadStores.of(stores).lease_traces is stores.lease_traces


@pytest.mark.unit
def test_process_graph_builds_the_trace_sweep_over_the_configured_tracing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[dict[str, object]] = []

    def record(**kwargs: object) -> object:
        built.append(kwargs)
        return object()

    monkeypatch.setattr(composition, "LeaseTraceSweep", record)
    config = RunnerConfig(
        root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path), tracing=TracingConfig(sweep_seconds=7)
    )
    exporter = InMemoryTraceExporter()
    graph = build_runner_process(
        config, environ={"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, trace_exporter=exporter
    )
    try:
        (kwargs,) = built
        assert kwargs["config"] is config.tracing
        assert kwargs["exporter"] is exporter
    finally:
        graph.close()


@pytest.mark.unit
def test_hosted_app_composes_the_escalation_takeover_command_over_the_runner_root(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url=f"sqlite:///{tmp_path / 'runner.db'}",
        workspace_provider="basic",
        workspace_root="scratch",
        workspace_repos=(WorkspaceRepo("toy", "file:///tmp/toy.git"),),
    )
    migrated_store_at(config.db_url)
    hosted = build_hosted_app(config)
    try:
        store = make_store(config.db_url)
        store.record_lease(
            NewLease(
                lease_id="lease_1",
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=_NOW,
            )
        )
        store.record_spawn(
            "lease_1",
            pid=100,
            process_start_time="start-100",
            session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1"),
            spawned_at=_NOW,
        )
        store.record_binding(
            chunk_id="ch_1", environment_id="e1", workdir=str(tmp_path / "scratch" / "e1"), bound_at=_NOW
        )
        store.record_closure(
            lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW
        )
        with TestClient(hosted.app) as client:
            (escalation,) = client.get("/api/escalations").json()["items"]
        assert escalation["wrapped_takeover_command"] == (
            f"blizzard runner takeover ch_1 --dir {shlex.quote(str(config.root))}"
        )
    finally:
        hosted.close()
