"""``blizzard runner add`` and ``retire`` over a registered host directory: a runner added under the host's own
credential into the store alone — through a running host's route, or a stopped host's store — and one retired at the
hub before its ``retired`` fact is recorded; neither writes ``.env`` or the host file, and both are refused, reaching no
hub, on a host holding no host token. A registered host's ``tick`` drives its runner as one the host reports for, and a
stopped host's ``token set`` and ``tick`` reach a runner only the store hosts."""

from __future__ import annotations

import threading
import tomllib
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
import uvicorn

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.cli.main import blizzard
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli.runtime import StoreHostedRunners, named_runners, seed_runners
from blizzard.runner.composition import (
    RunnerProcess,
    build_host_process,
    build_host_stores,
    build_hosted_runner_service,
    build_settings_stores,
)
from blizzard.runner.environments.workspaces import WorkspaceEntry, WorkspaceRegistry
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_layout import HostLayout, HostLock
from blizzard.runner.hub.client import ClaimRequest, HostRunnerRetireRefused, QueueEntry, RouteClaimOutcome
from blizzard.runner.hub.sealed_tokens import StoredToken
from blizzard.runner.hub.token_file import ENV_FILENAME
from blizzard.runner.listeners import Listeners, Uds
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop_wiring import LoopWiring, RunnerStepper
from blizzard.runner.store.schema import lease_spawns
from tests.support.child_launch import wait_for_exit
from tests.support.config_inputs import load_host_config
from tests.support.host_files import UnresolvableSettings
from tests.support.hub_tokens import host_hub_tokens
from tests.support.runner_add_retire import (
    _HUB,  # pyright: ignore[reportPrivateUsage]
    _registered,  # pyright: ignore[reportPrivateUsage]
    _run,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.runner_cli_home import _await_socket  # pyright: ignore[reportPrivateUsage]
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
    runner_store_errors,
)
from tests.support.runner_init_fakes import FakeInitHub, init_runner


def _runner_names(root: Path) -> list[str]:
    return [r["name"] for r in tomllib.loads(HostLayout(root).config_path.read_text()).get("runner", [])]


def _unregistered(root: Path) -> None:
    env = root / ENV_FILENAME
    env.write_text(
        "".join(line for line in env.read_text().splitlines(keepends=True) if HOST_TOKEN_VARIABLE not in line)
    )


@pytest.mark.parametrize("verb", [("add", "r2", "--project", "p"), ("retire", "--runner", "runner-local")])
def test_add_and_retire_refuse_a_host_holding_no_host_token_naming_how_to_add_one(
    tmp_path: Path, fake_init_hub: FakeInitHub, verb: tuple[str, ...]
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    _unregistered(root)
    before = HostLayout(root).config_path.read_bytes()

    result = _run(fake_init_hub, *verb, "--dir", str(root))

    assert result.exit_code != 0
    assert "blizzard hub runner-host add" in result.output
    assert HostLayout(root).config_path.read_bytes() == before
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1"]}


def test_tick_on_a_registered_host_drives_its_runner_as_one_the_host_reports(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    seen: list[bool] = []

    def tick_once(_wiring: LoopWiring, *, process: RunnerProcess | None = None) -> None:
        assert process is not None
        seen.append(process.host.registered())

    monkeypatch.setattr(LoopWiring, "tick_once", tick_once)

    result = _run(fake_init_hub, "tick", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert seen == [True]


def test_standalone_tick_uses_the_stored_runner_settings_revision(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    host = load_host_config(root)
    ready, halted = seed_runners(host, clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    try:
        runner = graph.runners[0]
        reader = runner.settings_reader
        assert reader is not None
        settings = runner.stores.settings
        settings.write(
            Change("edit", sets={"agents": 2}),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )
        revision = settings.snapshot().revision
    finally:
        graph.close()
    seen: list[tuple[int | None, int]] = []

    def capture(ctx: LoopContext) -> None:
        seen.append((ctx.config.runner_settings_revision, ctx.config.agents))

    monkeypatch.setattr(loop_wiring, "tick", capture)

    result = _run(fake_init_hub, "tick", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert seen == [(revision, 2)]


def _worker_binary(tmp_path: Path) -> Path:
    binary = tmp_path / "claude"
    binary.write_text(
        "#!/usr/bin/python3\n"
        "import pathlib, sys, time\n"
        "if '--version' in sys.argv:\n"
        "    print('2.1.0')\n"
        "    sys.exit(0)\n"
        f"pathlib.Path({str(tmp_path / 'launched')!r}).touch()\n"
        f"while not pathlib.Path({str(tmp_path / 'release')!r}).exists():\n"
        "    time.sleep(0.01)\n"
    )
    binary.chmod(0o755)
    return binary


def _bind_spawn_seams(
    monkeypatch: pytest.MonkeyPatch, store: SqlAlchemyRunnerStore, runner_id: str, tmp_path: Path
) -> None:
    class Hub(FakeHub):
        def claim_route(self, claim: ClaimRequest) -> RouteClaimOutcome:
            self.claim_outcome = claimed_outcome(
                claim.chunk_id,
                make_envelope(claim.chunk_id, "build", node_id="nd_build", choices=[("pass", "ok")]),
                runner_id=runner_id,
            )
            return super().claim_route(claim)

    hub = Hub(default_runner_id=runner_id)
    hub.envelopes["ch_new"] = make_envelope("ch_new", "build", node_id="nd_build", choices=[("pass", "ok")])
    hub.queue = [QueueEntry(chunk_id="ch_new", graph_id="gr_1", position=0, project_id="default")]
    provider = FakeProvider({"e1": str(tmp_path)})
    harness = FakeHarness(
        handle=WorkerHandle(session_id="session", pid=123, process_start_time="start", pgid=123), verdict=None
    )
    original = LoopWiring.context

    def context(wiring: LoopWiring, *args: Any, **kwargs: Any) -> LoopContext:
        production = original(wiring, *args, **kwargs)
        fake = make_context(
            store,
            hub=hub,
            provider=provider,
            harness=harness,
            probe=FakeProbe(),
            config=production.config,
            workspaces=WorkspaceRegistry([WorkspaceEntry("workspace-local", provider, spawn_root=str(tmp_path))]),
        )
        return replace(
            production,
            hub=fake.hub,
            token_identity=fake.token_identity,
            identity_verdict=fake.identity_verdict,
            hub_token=fake.hub_token,
            workspaces=fake.workspaces,
            process=fake.process,
            worktree_git=fake.worktree_git,
            env_release=fake.env_release,
        )

    monkeypatch.setattr(LoopWiring, "context", context)


def test_standalone_tick_spawn_records_stored_settings_revision(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    binary = _worker_binary(tmp_path)
    host = load_host_config(root)
    ready, halted = seed_runners(host, clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    try:
        runner = graph.runners[0]
        reader = runner.settings_reader
        assert reader is not None
        settings = runner.stores.settings
        settings.write(
            Change("edit", sets={"agents": 1, "claude_code.binary": str(binary)}),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )
        revision = settings.snapshot().revision
        runner_id = runner.runner_id
    finally:
        graph.close()

    store = make_store(host.db_url, runner_id)
    _bind_spawn_seams(monkeypatch, store, runner_id, tmp_path)
    pid: int | None = None
    try:
        result = _run(fake_init_hub, "tick", "--dir", str(root))
        assert result.exit_code == 0, result.output
        with store.engine.connect() as conn:
            rows = conn.execute(
                sa.select(
                    lease_spawns.c.runner_settings_revision, lease_spawns.c.host_settings_revision, lease_spawns.c.pid
                ).where(lease_spawns.c.runner_id == runner_id)
            ).all()
        assert len(rows) == 1
        pid = rows[0].pid
        # The tick verb seeds no host record, so the spawn records the host record's revision 0 beside the runner's.
        assert rows[0][:2] == (revision, 0)
        assert pid is not None
    finally:
        (tmp_path / "release").touch()
        if pid is not None:
            wait_for_exit(pid)
    assert (tmp_path / "launched").exists()


def test_spawn_after_failed_settings_rebuild_records_last_applied_revision(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    binary = _worker_binary(tmp_path)
    host = load_host_config(root)
    ready, halted = seed_runners(host, clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    store = make_store(host.db_url, runner.runner_id)
    _bind_spawn_seams(monkeypatch, store, runner.runner_id, tmp_path)
    stepper = RunnerStepper(runner.config, process_graph=runner)
    pid: int | None = None
    try:
        reader = runner.settings_reader
        assert reader is not None
        settings = runner.stores.settings
        settings.write(
            Change("edit", sets={"agents": 1, "claude_code.binary": str(binary)}),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )
        applied = settings.snapshot().revision
        host_settings = graph.host_stores.settings
        assert graph.host_settings is not None
        host_settings.write(
            Change("edit", sets={"worker_stdout.retention_days": 9}),
            if_match=host_settings.revision(),
            validate=graph.host_settings.reader.validate,
            door="api",
            actor="test",
        )
        host_revision = host_settings.revision()
        first = stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert first.config.runner_settings_revision == applied
        assert first.config.host_settings_revision == host_revision
        settings.write(
            Change("edit", sets={"agents": 2}),
            if_match=applied,
            validate=reader.validate,
            door="api",
            actor="test",
        )
        failed = settings.snapshot().revision
        assert failed > applied
        UnresolvableSettings(monkeypatch).hold()

        stepper.tick_once()

        assert stepper.failed_revision == failed
        assert stepper.settings_failure is not None and stepper.settings_failure[0] == failed
        with store.engine.connect() as conn:
            rows = conn.execute(
                sa.select(
                    lease_spawns.c.runner_settings_revision, lease_spawns.c.host_settings_revision, lease_spawns.c.pid
                ).where(lease_spawns.c.runner_id == runner.runner_id)
            ).all()
        assert len(rows) == 1
        pid = rows[0].pid
        # The spawn records both revisions of the last pair that built: the host's and the runner's.
        assert rows[0][:2] == (applied, host_revision)
        assert pid is not None
    finally:
        (tmp_path / "release").touch()
        if pid is not None:
            wait_for_exit(pid)
        stepper.close()
        graph.close()
    assert (tmp_path / "launched").exists()


def _retired(root: Path) -> frozenset[str]:
    engine = create_engine_from_url(load_host_config(root).db_url)
    try:
        return build_host_stores(engine, errors=runner_store_errors(), clock=SystemClock()).hosted_runners.retired()
    finally:
        engine.dispose()


def _row_ids(root: Path) -> tuple[str, ...]:
    return named_runners(load_host_config(root), SystemClock())


def _files(root: Path) -> tuple[bytes, bytes]:
    return HostLayout(root).config_path.read_bytes(), (root / ENV_FILENAME).read_bytes()


def _facts(root: Path, runner_id: str) -> list[tuple[str, str, str]]:
    engine = create_engine_from_url(load_host_config(root).db_url)
    try:
        hosted = build_host_stores(engine, errors=runner_store_errors(), clock=SystemClock()).hosted_runners
        return [(str(fact.op), fact.door, fact.actor) for fact in hosted.facts(runner_id)]
    finally:
        engine.dispose()


def _entries(root: Path, runner_id: str) -> dict[str, object]:
    engine = create_engine_from_url(load_host_config(root).db_url)
    try:
        _, settings = build_settings_stores(engine, errors=runner_store_errors(), runner_id=runner_id)
        return {key: entry.value for key, entry in settings.snapshot().entries.items()}
    finally:
        engine.dispose()


def _stored_token(root: Path, runner_id: str) -> StoredToken:
    config = load_host_config(root)
    with host_hub_tokens(config.root, config.db_url) as tokens:
        return tokens.stored(runner_id)


def test_add_on_a_stopped_host_mints_the_runner_and_writes_it_to_the_store_alone(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    before = _files(root)

    result = _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert "into the stopped host's store" in result.output and "next start" in result.output
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1", "rn_init2"]}
    assert _files(root) == before
    assert not (root / "runners" / "r2").exists()
    assert _runner_names(root) == ["runner-local"]
    assert set(_row_ids(root)) == {"rn_init1", "rn_init2"}
    assert _facts(root, "rn_init2") == [("added", "cli", "operator")]
    assert _entries(root, "rn_init2") == {
        "name": "r2",
        "projects": [{"project": "default", "workspace": "workspace-local"}],
        "agents": 1,
        **({"subscription": "anthropic"} if load_host_config(root).subscriptions else {}),
    }
    assert _stored_token(root, "rn_init2") == StoredToken.opened("init-token-2", 1)


def test_add_binds_each_project_to_its_workspace_and_sums_per_project_agents_into_the_runner_count(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)

    result = _run(
        fake_init_hub,
        "add",
        "r2",
        "--project",
        "p1",
        "--project",
        "p2",
        "--agents",
        "2",
        "--agents",
        "3",
        "--dir",
        str(root),
    )

    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.declared[-1] == ("p1", "p2")
    entries = _entries(root, "rn_init2")
    assert entries["projects"] == [
        {"project": "p1", "workspace": "workspace-local"},
        {"project": "p2", "workspace": "workspace-local"},
    ]
    assert entries["agents"] == 5


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (("r2", "--project", "p", "--workspace", "elsewhere"), "--workspace 'elsewhere' is not a workspace"),
        (("runner-local", "--project", "p"), "already holds a runner named 'runner-local'"),
        (("r2", "--project", "p", "--project", "q", "--agents", "1", "--agents", "2", "--agents", "3"), "--agents"),
    ],
)
def test_add_refuses_what_the_host_does_not_allow_before_reaching_the_hub(
    tmp_path: Path, fake_init_hub: FakeInitHub, args: tuple[str, ...], said: str
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    before = _files(root)

    result = _run(fake_init_hub, "add", *args, "--dir", str(root))

    assert result.exit_code != 0
    assert said in result.output
    assert len(fake_init_hub.admin.added) == 1
    assert _files(root) == before
    assert set(_row_ids(root)) == {"rn_init1"}


def test_add_refuses_a_name_an_added_runner_already_holds(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0

    result = _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root))

    assert result.exit_code != 0
    assert "already holds a runner named 'r2'" in result.output
    assert len(fake_init_hub.admin.added) == 2


def test_add_under_a_host_holding_the_directory_without_answering_is_refused(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    with HostLock.acquire(HostLayout(root)):
        result = _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root))

    assert result.exit_code != 0
    assert "stop it first" in result.output
    assert len(fake_init_hub.admin.added) == 1


def test_init_runs_again_beside_a_runner_a_stopped_host_added(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    """The add's ``cli`` fact is not ``init``'s join: a re-run keeps the scaffold's runner and leaves the added one."""
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    store = HostLayout(root).store_path

    rerun = _run(fake_init_hub, "init", str(root), "--hub", _HUB)

    assert rerun.exit_code == 0, rerun.output
    assert set(_row_ids(root)) == {"rn_init1", "rn_init2"}
    assert load_host_config(root).runners[0].id == "rn_init1"
    assert not [path for path in store.parent.iterdir() if path.name.startswith(f"{store.name}.")]
    assert _facts(root, "rn_init1")[0] == ("added", "cli", "init")


def test_retire_on_a_stopped_host_retires_an_added_runner_and_records_it_retired(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    before = _files(root)

    result = _run(fake_init_hub, "retire", "--runner", "r2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert "recorded it retired in the stopped host's store" in result.output
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1"]}
    assert _retired(root) == {"rn_init2"}
    assert _facts(root, "rn_init2")[-1] == ("retired", "cli", "operator")
    assert set(_row_ids(root)) == {"rn_init1", "rn_init2"}
    assert _files(root) == before


def test_retire_names_the_runner_by_id_too(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0

    result = _run(fake_init_hub, "retire", "--runner", "rn_init2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert _retired(root) == {"rn_init2"}


def test_retire_of_a_file_runner_keeps_its_entry_and_says_it_is_no_longer_read(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    before = _files(root)

    result = _run(fake_init_hub, "retire", "--runner", "runner-local", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert "is no longer read — remove it" in result.output
    assert _retired(root) == {"rn_init1"}
    assert _files(root) == before


def test_a_retired_runners_retire_writes_nothing_and_asks_nothing_of_the_hub(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    assert _run(fake_init_hub, "retire", "--runner", "rn_init2", "--dir", str(root)).exit_code == 0
    facts = _facts(root, "rn_init2")
    fake_init_hub.admin.retire_refusal = HostRunnerRetireRefused("409", detail="never asked")

    result = _run(fake_init_hub, "retire", "--runner", "rn_init2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert "already retired" in result.output
    assert _facts(root, "rn_init2") == facts


@pytest.mark.parametrize(
    ("detail", "said"),
    [
        ("runner rn_1 holds 1 chunk(s): ch_1 (environments: e1) — retire with --force to release", "--force"),
        ("runner rn_1 changed after it was read — run it again", "run it again"),
    ],
)
def test_retire_the_hub_refuses_records_nothing(
    tmp_path: Path, fake_init_hub: FakeInitHub, detail: str, said: str
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    fake_init_hub.admin.retire_refusal = HostRunnerRetireRefused("409", detail=detail)

    result = _run(fake_init_hub, "retire", "--runner", "runner-local", "--dir", str(root))

    assert result.exit_code != 0
    assert said in result.output
    assert _retired(root) == frozenset()


def test_retire_of_a_runner_not_under_the_host_is_refused_recording_nothing(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    fake_init_hub.admin.held["hs_init1"].clear()

    result = _run(fake_init_hub, "retire", "--runner", "runner-local", "--dir", str(root))

    assert result.exit_code != 0
    assert "does not stand under this host" in result.output
    assert _retired(root) == frozenset()


def test_stopped_retire_retains_identity_with_history_until_explicit_reset(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0

    result = _run(fake_init_hub, "retire", "--runner", "r2", "--dir", str(root))
    assert result.exit_code == 0, result.output
    assert set(_row_ids(root)) == {"rn_init1", "rn_init2"}
    assert [op for op, _, _ in _facts(root, "rn_init2")] == ["added", "retired"]
    # Its retired hosted row still accounts for the retained identity, so the store stays the host's.
    kept = _run(fake_init_hub, "init", str(root), "--hub", _HUB)
    assert kept.exit_code == 0, kept.output
    reset = _run(fake_init_hub, "store", "reset", str(root), "--runner", "rn_init2")
    assert reset.exit_code == 0, reset.output
    again = _run(fake_init_hub, "init", str(root), "--hub", _HUB)
    assert again.exit_code == 0, again.output


def test_token_set_on_a_stopped_host_seals_a_runner_only_the_store_hosts(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0

    result = init_runner(fake_init_hub).invoke(
        blizzard, ["runner", "token", "set", "--runner", "r2", "--dir", str(root)], input="tok-replaced\n"
    )

    assert result.exit_code == 0, result.output
    assert "runner r2 (rn_init2): hub token set in the host store at revision 2" in result.output
    assert _stored_token(root, "rn_init2") == StoredToken.opened("tok-replaced", 2)


def test_tick_on_a_stopped_host_drives_a_runner_only_the_store_hosts(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    seen: list[tuple[str, str]] = []

    def tick_once(_wiring: LoopWiring, *, process: RunnerProcess | None = None) -> None:
        assert process is not None
        seen.append((process.runner_id, process.config.name))

    monkeypatch.setattr(LoopWiring, "tick_once", tick_once)

    result = _run(fake_init_hub, "tick", "--runner", "r2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert seen == [("rn_init2", "r2")]


@contextmanager
def _serving(root: Path, hub: FakeInitHub) -> Generator[HostConfig]:
    """The host at ``root`` running, its routes adding and retiring through ``hub``."""
    config = load_host_config(root)
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    graph = build_host_process(config, ready)
    served = replace(ready[0].config, port=0)
    hosted = build_hosted_app(served, host=graph, host_config=config, source=StoreHostedRunners(config, SystemClock()))
    with hub.host_clients(config.hub_url) as host_clients:
        hosted.app.state.hosted_runners = build_hosted_runner_service(
            config, graph.engine, host_clients(config.host_token.value), clock=SystemClock()
        )
        sockets = Listeners.of(served).bound()
        server = uvicorn.Server(uvicorn.Config(hosted.app, log_level="warning"))
        thread = threading.Thread(target=lambda: server.run(sockets=sockets), daemon=True)
        thread.start()
        try:
            _await_socket(served.socket_path)
            yield config
        finally:
            server.should_exit = True
            thread.join(timeout=10.0)
            Uds(served.socket_path).unlink()
            hosted.close()
            graph.close()


def test_add_on_a_running_host_goes_through_its_route_and_lists_the_runner_starting(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    before = _files(root)
    with _serving(root, fake_init_hub) as config:
        result = _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root))
        transport = httpx2.HTTPTransport(uds=str(config.layout.socket_path))
        with httpx2.Client(transport=transport, base_url="http://runner") as client:
            listed = {item["name"]: item["state"] for item in client.get("/api/host/runners").json()["items"]}

    assert result.exit_code == 0, result.output
    assert "through the running host" in result.output and "at its next tick" in result.output
    assert listed == {"runner-local": "serving", "r2": "starting"}
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1", "rn_init2"]}
    assert _facts(root, "rn_init2") == [("added", "api", "operator")]
    assert _files(root) == before
    assert not (root / "runners" / "r2").exists()


def test_retire_on_a_running_host_goes_through_its_route(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    before = _files(root)
    with _serving(root, fake_init_hub):
        result = _run(fake_init_hub, "retire", "--runner", "r2", "--force", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert "through the running host" in result.output and "drains the runner at its next tick" in result.output
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1"]}
    assert _facts(root, "rn_init2")[-1] == ("retired", "api", "operator")
    assert _files(root) == before
