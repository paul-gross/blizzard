"""The host tick takes the host's workspace and subscription records up with no restart: a moved workspace rebuilds
the one registry every reader holds, over locks that outlive the rebuild; a retired workspace finishes what it holds
and takes no new acquisition; a retired subscription leaves its runner running as though it named none; and a
binding the host does not serve halts no runner but refuses its next settings write."""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import blizzard.runner.composition as composition
import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.runner.app import HostedApp
from blizzard.runner.composition import HostProcess, build_host_record_services, host_report
from blizzard.runner.config import ProjectBinding
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.environments.factory import HeldIds, WorkspaceLaunch, WorkspaceSettings
from blizzard.runner.environments.provider import AcquiredEnvironment, IWorkspaceProvider
from blizzard.runner.host_records.model import RecordKind
from blizzard.runner.host_records.service import HostRecordService
from blizzard.runner.lifecycle.env_release import EnvironmentRelease
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.settings.authoring import RunnerSettingsAuthoring
from blizzard.runner.throttle.gate import ThrottleGate
from tests.support.host_files import TEST_WORKSPACE_ID
from tests.support.runner_fakes import FakeProvider
from tests.support.seeded_host import seeded_host

_HOME = TEST_WORKSPACE_ID
_SUBSCRIPTION = '[[subscription]]\nslug = "team"\nname = "Team"\nprovider = "anthropic"\n'


@dataclass
class _Providers:
    """Every provider the host builds, in build order, each over a one-environment pool under its root."""

    built: list[tuple[str, FakeProvider]] = field(default_factory=list[tuple[str, FakeProvider]])

    def build(
        self, settings: WorkspaceSettings, *, launch: WorkspaceLaunch, held_ids: HeldIds = None
    ) -> IWorkspaceProvider:
        del launch, held_ids
        provider = FakeProvider({"e1": f"{settings.workspace_root}/e1"}, spawn_root=settings.workspace_root)
        self.built.append((settings.workspace_root, provider))
        return provider


@dataclass
class _Host:
    process: HostProcess
    app: HostedApp
    providers: _Providers

    def records(self, kind: RecordKind) -> HostRecordService:
        config = self.process.host_settings
        assert config is not None
        return build_host_record_services(config.reader, self.process.host_stores, clock=SystemClock())[kind]

    def tick(self) -> None:
        stepper = self.app.host_stepper
        assert stepper is not None
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED

    def context(self) -> LoopContext:
        return self.app.steppers[0]._context()  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> _Providers:
    built = _Providers()
    monkeypatch.setattr(composition, "build_workspace_provider", built.build)
    return built


def _host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, providers: _Providers, *, top: str = "", runner: str = ""
) -> Generator[_Host]:
    with seeded_host(
        tmp_path,
        monkeypatch,
        top=top,
        workspace=f'root = "{tmp_path / "home"}"',
        runner=runner,
        projects=f'[{{ project = "p", workspace = "{_HOME}" }}]',
    ) as (process, app):
        yield _Host(process, app, providers)


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, providers: _Providers) -> Generator[_Host]:
    yield from _host(tmp_path, monkeypatch, providers)


def _edit(service: HostRecordService, key: str, **sets: object) -> None:
    record = service.held(key)
    service.edit(record, sets, frozenset(), if_match=record.revision, actor="test", door="api")


def _retire(service: HostRecordService, key: str) -> None:
    service.retire(service.held(key), actor="test", door="api")


def _try_lock(host: _Host, workspace_id: str) -> bool:
    outcome: list[bool] = []

    def attempt() -> None:
        with host.process.workspaces.try_acquisition_lock(workspace_id) as taken:
            outcome.append(taken)

    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join(5)
    return outcome[0]


def test_a_workspace_repointed_between_two_acquisitions_hands_both_the_one_lock(host: _Host, tmp_path: Path) -> None:
    before = host.process.workspaces.provider(_HOME)
    with host.process.workspaces.try_acquisition_lock(_HOME) as taken:
        assert taken
        _edit(host.records(RecordKind.WORKSPACE), _HOME, root=str(tmp_path / "moved"))
        host.tick()
        after = host.process.workspaces.provider(_HOME)
        assert after is not before and after is not None
        assert after.spawn_root() == str(tmp_path / "moved")
        # An acquisition through the replacement waits on the lock the one through the replaced provider holds.
        assert _try_lock(host, _HOME) is False
    assert _try_lock(host, _HOME) is True


def test_an_unmoved_workspace_keeps_its_provider_across_a_rebuild(host: _Host, tmp_path: Path) -> None:
    host.tick()
    before = host.process.workspaces.provider(_HOME)
    host.records(RecordKind.WORKSPACE).create("ws-b", {"root": str(tmp_path / "b")}, actor="test", door="api")
    host.tick()
    assert host.process.workspaces.provider(_HOME) is before


def test_a_held_environment_is_released_through_the_rebuilt_provider(host: _Host, tmp_path: Path) -> None:
    runner = host.process.runners[0]
    release = EnvironmentRelease(runner.stores.environments, SystemClock(), runner.workspaces)
    _edit(host.records(RecordKind.WORKSPACE), _HOME, root=str(tmp_path / "moved"))
    host.tick()
    release.release_binding("chunk-1", _HOME, [AcquiredEnvironment(environment_id="e1", workdir="/x")])
    (old_root, old), (new_root, new) = host.providers.built[0], host.providers.built[-1]
    assert (old_root, new_root) == (str(tmp_path / "home"), str(tmp_path / "moved"))
    assert (old.released, new.released) == ([], ["e1"])


def test_every_reader_sees_a_created_workspace_at_the_next_tick(host: _Host, tmp_path: Path) -> None:
    root = str(tmp_path / "b")
    host.records(RecordKind.WORKSPACE).create("ws-b", {"root": root}, actor="test", door="api")
    client = TestClient(host.app.app)
    served = {w["workspace_id"] for w in client.get("/api/host/environments").json()["workspaces"]}
    assert "ws-b" not in served and host.process.workspaces.provider("ws-b") is None

    host.tick()

    served = {w["workspace_id"] for w in client.get("/api/host/environments").json()["workspaces"]}
    assert served == {_HOME, "ws-b"}
    ctx = host.context()
    assert ctx.workspaces.spawn_root("ws-b") == root
    assert ctx.free_environments.has_free_environment("ws-b")
    assert ctx.workspaces.environment_workdirs()["ws-b"] == {"e1": f"{root}/e1"}
    # The spawn-root health probes run over the bound workspaces' roots, read through the same holder.
    reader = host.process.runners[0].settings_reader
    assert reader is not None
    rebound = dataclasses.replace(reader.snapshot_pair()[1], bindings=(ProjectBinding("q", "ws-b"),))
    assert composition.bound_spawn_roots(rebound, host.process.workspaces) == (root,)
    report = host_report(host.process.runners[0].config, host.process.workspaces, host.process.subscriptions)
    assert [w.workspace_id for w in report.workspaces] == [_HOME, "ws-b"]


def test_a_host_with_no_workspace_runs_and_claims_nothing(host: _Host, monkeypatch: pytest.MonkeyPatch) -> None:
    _retire(host.records(RecordKind.WORKSPACE), _HOME)
    host.tick()
    seen: list[LoopContext] = []
    monkeypatch.setattr(loop_wiring, "tick", seen.append)

    assert host.app.steppers[0].request().outcome is SweepPassOutcome.COMPLETED

    (ctx,) = seen
    assert host.process.workspaces.ids() == ()
    assert ctx.workspaces.acquisition_provider(_HOME) is None
    assert not ctx.free_environments.has_free_environment(_HOME)
    report = host_report(host.process.runners[0].config, host.process.workspaces, host.process.subscriptions)
    assert report.workspaces == ()
    served = TestClient(host.app.app).get("/api/host/environments").json()["workspaces"]
    assert served == []


def test_a_retired_workspace_refuses_its_runners_next_write_while_the_other_binding_claims_and_the_held_chunk_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, providers: _Providers
) -> None:
    for host in _host(tmp_path, monkeypatch, providers, top=f'[[workspace]]\nid = "ws-b"\nroot = "{tmp_path / "b"}"'):
        runner = host.process.runners[0]
        reader = runner.settings_reader
        assert reader is not None
        authoring = RunnerSettingsAuthoring(reader, runner.stores.settings, HOST_REGISTRY)
        both = [{"project": "p", "workspace": _HOME}, {"project": "q", "workspace": "ws-b"}]
        authoring.edit(
            {"projects": both}, frozenset(), if_match=runner.stores.settings.snapshot().revision, door="api", actor="t"
        )

        # A chunk holds ws-b's one environment before the retirement.
        host.tick()
        b_root = str(tmp_path / "b")
        runner.stores.environments.record_binding(
            chunk_id="chunk-b",
            environment_id="e1",
            workdir=f"{b_root}/e1",
            bound_at=datetime.now(UTC),
            workspace_id="ws-b",
        )

        _retire(host.records(RecordKind.WORKSPACE), "ws-b")
        host.tick()
        held_b = host.process.workspaces.provider("ws-b")

        with pytest.raises(SettingsInvalid) as refused:
            authoring.edit(
                {"agents": 2}, frozenset(), if_match=runner.stores.settings.snapshot().revision, door="api", actor="t"
            )
        assert "ws-b" in str(refused.value.errors)
        ctx = host.context()
        assert ctx.free_environments.has_free_environment(_HOME)
        assert not ctx.free_environments.has_free_environment("ws-b")
        assert ctx.workspaces.acquisition_provider("ws-b") is None
        # The chunk held there stays under ws-b, and finishes there: its environment goes back to ws-b's pool.
        assert ctx.workspaces.held("ws-b", (_HOME, "ws-b")) == "ws-b"
        assert held_b is not None and isinstance(held_b, FakeProvider) and held_b.spawn_root() == b_root
        ctx.env_release.release_binding("chunk-b", "ws-b", [AcquiredEnvironment(environment_id="e1", workdir="/x")])
        assert held_b.released == ["e1"]
        assert [b.chunk_id for b in runner.stores.environments.held_bindings()] == []

        fixed = [{"project": "p", "workspace": _HOME}]
        authoring.edit(
            {"projects": fixed}, frozenset(), if_match=runner.stores.settings.snapshot().revision, door="api", actor="t"
        )


def test_an_edit_to_a_retired_workspace_rebuilds_the_provider_it_releases_through(host: _Host, tmp_path: Path) -> None:
    runner = host.process.runners[0]
    release = EnvironmentRelease(runner.stores.environments, SystemClock(), runner.workspaces)
    service = host.records(RecordKind.WORKSPACE)
    service.create("ws-b", {"root": str(tmp_path / "b")}, actor="test", door="api")
    host.tick()
    _retire(service, "ws-b")
    host.tick()
    retired = host.process.workspaces.provider("ws-b")

    _edit(service, "ws-b", root=str(tmp_path / "b-moved"))
    host.tick()

    moved = host.process.workspaces.provider("ws-b")
    assert moved is not retired and moved is not None
    assert moved.spawn_root() == str(tmp_path / "b-moved")
    assert host.process.workspaces.acquisition_provider("ws-b") is None
    release.release_binding("chunk-b", "ws-b", [AcquiredEnvironment(environment_id="e1", workdir="/x")])
    assert isinstance(moved, FakeProvider) and moved.released == ["e1"]


def test_a_retired_subscription_leaves_its_runner_unthrottled_by_it_and_declaring_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, providers: _Providers
) -> None:
    runner = 'subscription = "team"\n[runner.throttle.pace]\n'
    for host in _host(tmp_path, monkeypatch, providers, top=_SUBSCRIPTION, runner=runner):
        ctx = host.context()
        assert ctx.config.subscription == "team" and [s.slug for s in ctx.subscriptions] == ["team"]
        # Unsampled, its pace says no.
        assert not ThrottleGate(ctx).verdict().admits_claim
        assert host.process.host_tick.external_usage_sample is not None
        sampled_at = datetime.now(UTC)
        host.process.runners[0].external_usage_attempts.record_external_usage_attempt(
            slug="team", sampled_at=sampled_at, payload=None, report_kind="", report_payload=""
        )

        _retire(host.records(RecordKind.SUBSCRIPTION), "team")
        host.tick()

        ctx = host.context()
        assert ctx.config.subscription is None and ctx.subscriptions == ()
        # No subscription's usage throttles it: its pace is not asked.
        assert ThrottleGate(ctx).verdict().levers == ()
        assert host.process.subscriptions == ()
        assert host.process.host_tick.external_usage_sample is None
        assert TestClient(host.app.app).get("/api/host/subscriptions").json()["items"] == []
        report = host_report(host.process.runners[0].config, host.process.workspaces, host.process.subscriptions)
        assert report.subscriptions == ()
        # Its samples stay.
        assert host.process.host_stores.external_usage.last_external_usage_attempt_at("team") == sampled_at

        host.records(RecordKind.SUBSCRIPTION).enable(
            host.records(RecordKind.SUBSCRIPTION).held("team"), actor="test", door="api"
        )
        host.tick()
        assert host.context().config.subscription == "team"
        assert host.process.host_tick.external_usage_sample is not None
