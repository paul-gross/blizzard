"""``blizzard runner add`` and ``retire`` over a registered host directory: a runner added under the host's own
credential with its token, directory and ``[[runner]]`` written in that order; one retired at the hub before its entry
is removed; and both refused, reaching no hub, on a host holding no host token. A registered host's ``tick`` drives its
runner as one the host reports for."""

from __future__ import annotations

import tomllib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.cli.runtime import named_runners, seed_runners
from blizzard.runner.composition import RunnerProcess, build_host_process
from blizzard.runner.environments.workspaces import WorkspaceEntry, WorkspaceRegistry
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.host_config import BuiltRunner
from blizzard.runner.host_file import remove_runner
from blizzard.runner.host_layout import HostLayout, HostLock
from blizzard.runner.hub.client import ClaimRequest, HostRunnerRetireRefused, QueueEntry, RouteClaimOutcome
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.token_file import ENV_FILENAME
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop_wiring import LoopWiring, RunnerStepper
from blizzard.runner.store.schema import lease_spawns, runner_setting_changes
from tests.support.child_launch import wait_for_exit
from tests.support.config_inputs import load_host_config
from tests.support.runner_add_retire import (
    _HUB,  # pyright: ignore[reportPrivateUsage]
    _registered,  # pyright: ignore[reportPrivateUsage]
    _run,  # pyright: ignore[reportPrivateUsage]
)
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
)
from tests.support.runner_init_fakes import FakeInitHub


def _runner_names(root: Path) -> list[str]:
    return [r["name"] for r in tomllib.loads(HostLayout(root).config_path.read_text()).get("runner", [])]


def _unregistered(root: Path) -> None:
    env = root / ENV_FILENAME
    env.write_text(
        "".join(line for line in env.read_text().splitlines(keepends=True) if HOST_TOKEN_VARIABLE not in line)
    )


def test_add_mints_the_runner_under_the_host_and_writes_its_token_directory_and_entry(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)

    result = _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1", "rn_init2"]}
    assert HubTokenFile.of(root, "BZ_HUB_TOKEN_R2").held() == "init-token-2"
    assert (root / "runners" / "r2").is_dir()
    assert _runner_names(root) == ["runner-local", "r2"]
    host = load_host_config(root)
    added = host.runners[1]
    assert (added.id, added.subscription) == ("rn_init2", host.runners[0].subscription)
    assert [(b.project, b.workspace) for b in added.bindings] == [("default", "workspace-local")]
    assert added.own is not None and added.own.agents == 1
    assert all(isinstance(p, BuiltRunner) for p in host.projections())
    assert "starts" not in result.output  # no host is running here


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
    added = load_host_config(root).runners[1]
    assert [(b.project, b.workspace) for b in added.bindings] == [
        ("p1", "workspace-local"),
        ("p2", "workspace-local"),
    ]
    assert added.own is not None and added.own.agents == 5


def test_add_with_one_agents_value_sets_the_runner_count_for_every_project(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)

    result = _run(fake_init_hub, "add", "r2", "--project", "p1", "--project", "p2", "--agents", "4", "--dir", str(root))

    assert result.exit_code == 0, result.output
    added = load_host_config(root).runners[1]
    assert added.own is not None and added.own.agents == 4


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (("r2", "--project", "p", "--workspace", "elsewhere"), "--workspace 'elsewhere' is not a workspace"),
        (("runner-local", "--project", "p"), "already holds a runner named 'runner-local'"),
        (("RUNNER_LOCAL", "--project", "p"), "BZ_HUB_TOKEN_RUNNER_LOCAL"),
        (("r2", "--project", "p", "--project", "q", "--agents", "1", "--agents", "2", "--agents", "3"), "--agents"),
    ],
)
def test_add_refuses_what_the_host_file_does_not_allow_before_reaching_the_hub(
    tmp_path: Path, fake_init_hub: FakeInitHub, args: tuple[str, ...], said: str
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    before = (HostLayout(root).config_path.read_bytes(), (root / ENV_FILENAME).read_bytes())

    result = _run(fake_init_hub, "add", *args, "--dir", str(root))

    assert result.exit_code != 0
    assert said in result.output
    assert len(fake_init_hub.admin.added) == 1
    assert (HostLayout(root).config_path.read_bytes(), (root / ENV_FILENAME).read_bytes()) == before


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


def test_add_on_a_running_host_says_it_starts_the_runner_at_the_next_start(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    with HostLock.acquire(HostLayout(root)):
        result = _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert "it starts runner r2 at its next start" in result.output


def test_retire_retires_at_the_hub_then_removes_the_entry(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0

    with HostLock.acquire(HostLayout(root)):
        result = _run(fake_init_hub, "retire", "--runner", "r2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1"]}
    assert _runner_names(root) == ["runner-local"]
    assert "it halts runner r2 at its next tick" in result.output


def test_retire_names_the_runner_by_id_too(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0

    result = _run(fake_init_hub, "retire", "--runner", "rn_init2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert _runner_names(root) == ["runner-local"]


def test_retire_of_a_runner_holding_chunks_is_refused_leaving_its_entry_and_names_force(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    fake_init_hub.admin.retire_refusal = HostRunnerRetireRefused(
        "409", detail="runner rn_1 holds 1 chunk(s): ch_1 (environments: e1) — retire with --force to release"
    )

    result = _run(fake_init_hub, "retire", "--runner", "runner-local", "--dir", str(root))

    assert result.exit_code != 0
    assert "ch_1" in result.output and "--force" in result.output
    assert _runner_names(root) == ["runner-local"]


def test_retire_of_a_runner_whose_lifecycle_moved_at_the_hub_says_so_leaving_its_entry(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    fake_init_hub.admin.retire_refusal = HostRunnerRetireRefused(
        "409", detail="runner rn_1 changed after it was read — run it again"
    )

    result = _run(fake_init_hub, "retire", "--runner", "runner-local", "--dir", str(root))

    assert result.exit_code != 0
    assert "run it again" in result.output and "holds chunks" not in result.output
    assert _runner_names(root) == ["runner-local"]


def test_retire_of_a_runner_not_under_the_host_is_refused_leaving_its_entry(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    fake_init_hub.admin.held["hs_init1"].clear()

    result = _run(fake_init_hub, "retire", "--runner", "runner-local", "--dir", str(root))

    assert result.exit_code != 0
    assert "does not stand under this host" in result.output
    assert _runner_names(root) == ["runner-local"]


def test_retire_forgets_the_runners_identity_so_init_runs_again_over_the_remaining_runner(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    rerun = _run(fake_init_hub, "init", str(root), "--hub", _HUB)
    assert rerun.exit_code == 0, rerun.output
    assert set(named_runners(load_host_config(root), SystemClock())) == {"rn_init1", "rn_init2"}

    result = _run(fake_init_hub, "retire", "--runner", "r2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert named_runners(load_host_config(root), SystemClock()) == ("rn_init1",)
    again = _run(fake_init_hub, "init", str(root), "--hub", _HUB)
    assert again.exit_code == 0, again.output


def test_stopped_retire_retains_identity_with_history_until_explicit_reset(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    assert _run(fake_init_hub, "init", str(root), "--hub", _HUB).exit_code == 0
    engine = create_engine_from_url(load_host_config(root).db_url)
    with engine.begin() as conn:
        conn.execute(
            runner_setting_changes.insert().values(
                runner_id="rn_init2",
                recorded_at=datetime(2026, 11, 1, tzinfo=UTC),
                revision=1,
                op="set",
                door="cli",
                actor="test",
                diff="[]",
            )
        )
    engine.dispose()

    result = _run(fake_init_hub, "retire", "--runner", "r2", "--dir", str(root))
    assert result.exit_code == 0, result.output
    assert set(named_runners(load_host_config(root), SystemClock())) == {"rn_init1", "rn_init2"}
    refused = _run(fake_init_hub, "init", str(root), "--hub", _HUB)
    assert refused.exit_code != 0
    reset = _run(fake_init_hub, "store", "reset", str(root), "--runner", "rn_init2")
    assert reset.exit_code == 0, reset.output
    again = _run(fake_init_hub, "init", str(root), "--hub", _HUB)
    assert again.exit_code == 0, again.output


def test_a_readd_of_one_runner_beside_another_refuses_rather_than_set_the_shared_store_aside(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    assert _run(fake_init_hub, "init", str(root), "--hub", _HUB).exit_code == 0
    env = root / ENV_FILENAME
    env.write_text("".join(line for line in env.read_text().splitlines(keepends=True) if "BZ_HUB_TOKEN_R2" not in line))
    store = HostLayout(root).store_path
    before = (named_runners(load_host_config(root), SystemClock()), HostLayout(root).config_path.read_bytes())

    refused = _run(fake_init_hub, "init", str(root), "--hub", _HUB, "--allow-readd")

    assert refused.exit_code != 0
    assert "runner-local" in refused.output and "blizzard runner retire --runner r2" in refused.output
    assert (named_runners(load_host_config(root), SystemClock()), HostLayout(root).config_path.read_bytes()) == before
    assert not [path for path in store.parent.iterdir() if path.name.startswith(f"{store.name}.")]
    assert len(fake_init_hub.admin.added) == 2


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
    ready, halted = seed_runners(host, list(host.runners))
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
    ready, halted = seed_runners(host, list(host.runners))
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
        assert rows[0][:2] == (revision, None)
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
    ready, halted = seed_runners(host, list(host.runners))
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
        first = stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert first.config.runner_settings_revision == applied
        settings.write(
            Change("edit", sets={"agents": 2}),
            if_match=applied,
            validate=reader.validate,
            door="api",
            actor="test",
        )
        failed = settings.snapshot().revision
        assert failed > applied
        (root / ".env").unlink()
        (root / ".env").mkdir()

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
        assert rows[0][:2] == (applied, None)
        assert pid is not None
    finally:
        (tmp_path / "release").touch()
        if pid is not None:
            wait_for_exit(pid)
        stepper.close()
        graph.close()
    assert (tmp_path / "launched").exists()


def test_add_gives_the_new_runner_the_hosts_tool_profile_so_the_host_still_loads(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    path = HostLayout(root).config_path
    text = path.read_text()
    assert "env_passthrough = []" in text
    path.write_text(text.replace("env_passthrough = []", 'env_passthrough = ["SSH_AUTH_SOCK"]', 1))

    result = _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root))

    assert result.exit_code == 0, result.output
    runners = tomllib.loads(path.read_text())["runner"]
    assert runners[1]["worker"]["env_passthrough"] == ["SSH_AUTH_SOCK"]
    assert runners[1]["worker"]["path_prepend"] == runners[0]["worker"]["path_prepend"]
    assert len(load_host_config(root).runners) == 2


def test_add_refuses_a_workspace_the_host_does_not_declare_with_nothing_added(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)

    result = _run(fake_init_hub, "add", "r4", "--project", "default", "--workspace", "nope", "--dir", str(root))

    assert result.exit_code != 0
    assert "--workspace 'nope' is not a workspace this host declares (workspace-local)" in result.output
    assert len(fake_init_hub.admin.added) == 1


def _row_ids(root: Path) -> tuple[str, ...]:
    return named_runners(load_host_config(root), SystemClock())


def test_retire_under_a_running_host_writes_nothing_to_the_store_and_leaves_the_forget_to_the_host(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    assert _run(fake_init_hub, "init", str(root), "--hub", _HUB).exit_code == 0
    store = HostLayout(root).store_path
    before = (_row_ids(root), store.read_bytes())

    with HostLock.acquire(HostLayout(root)):
        result = _run(fake_init_hub, "retire", "--runner", "r2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert "it halts runner r2 at its next tick" in result.output
    assert (_row_ids(root), store.read_bytes()) == before
    assert _runner_names(root) == ["runner-local"]


def test_retire_of_a_runner_only_the_store_names_retires_it_at_the_hub_and_forgets_the_row(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    assert _run(fake_init_hub, "init", str(root), "--hub", _HUB).exit_code == 0
    remove_runner(HostLayout(root), "r2", strict=False)
    assert set(_row_ids(root)) == {"rn_init1", "rn_init2"}

    result = _run(fake_init_hub, "retire", "--runner", "rn_init2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1"]}
    assert _row_ids(root) == ("rn_init1",)
    assert _runner_names(root) == ["runner-local"]


def test_retire_of_a_runner_only_the_store_names_is_refused_while_a_host_runs(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    assert _run(fake_init_hub, "init", str(root), "--hub", _HUB).exit_code == 0
    remove_runner(HostLayout(root), "r2", strict=False)

    with HostLock.acquire(HostLayout(root)):
        result = _run(fake_init_hub, "retire", "--runner", "rn_init2", "--dir", str(root))

    assert result.exit_code != 0
    assert "stop it first" in result.output
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1", "rn_init2"]}
    assert set(_row_ids(root)) == {"rn_init1", "rn_init2"}
