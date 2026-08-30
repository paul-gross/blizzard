"""Composition-root threading (``bzh:dependency-injection``) — issue #88.

Each case pins one ``RunnerConfig`` key reaching the collaborator built from it: an
unthreaded key is read from the operator's toml and dropped, which no other tier sees.
Both roots that build a ``ClaudeCodeAdapter`` are covered (issue #276).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.app import build_hosted_app, create_app
from blizzard.runner.config import CONFIG_FILENAME, ConfigError, RunnerConfig
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.loop.build import LoopWiring, PeriodicDriver, ResumeMarking
from tests.runner_fakes import FakeHub, FakeProbe, make_store, make_stores

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


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
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
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

    ctx = LoopWiring(config, "", "").context(FakeHub())

    assert isinstance(ctx.harness, ClaudeCodeAdapter)
    assert ctx.harness._env_passthrough == ("MY_HARNESS_QUIRK", "ANOTHER_VAR")


@pytest.mark.unit
def test_loop_wiring_threads_external_usage_credentials_path_into_the_adapter(tmp_path: Path) -> None:
    """An unthreaded override leaves every daemon this root builds reading the
    adapter's own default credentials path and reaching the real Anthropic endpoint
    (issue #218)."""
    scratch = str(tmp_path / "scratch-credentials.json")
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        external_usage_credentials_path=scratch,
    )

    ctx = LoopWiring(config, "", "").context(FakeHub())

    assert isinstance(ctx.harness, ClaudeCodeAdapter)
    assert ctx.harness._credentials_path == scratch


@pytest.mark.unit
def test_loop_wiring_threads_the_worker_settings_path_and_permission_mode(tmp_path: Path) -> None:
    """Both reach the spawned worker only as adapter argv flags (``--settings``,
    ``--permission-mode``), so dropping either threading leaves every worker running
    without the runner-owned hook file, or at the interactive permission default."""
    settings = str(tmp_path / "worker-settings.json")
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        worker_settings_path=settings,
        harness_permission_mode="acceptEdits",
    )

    ctx = LoopWiring(config, "", "").context(FakeHub())

    assert isinstance(ctx.harness, ClaudeCodeAdapter)
    assert ctx.harness._settings_path == settings
    assert ctx.harness._permission_mode == "acceptEdits"


@pytest.mark.unit
def test_hosted_app_threads_the_worker_settings_path_and_permission_mode(tmp_path: Path) -> None:
    """The hosted app builds its own adapter, and the takeover command it composes
    asserts the permission mode (issue #258) — a second threading of the same two keys,
    which the loop's own root does not cover."""
    settings = str(tmp_path / "worker-settings.json")
    (tmp_path / CONFIG_FILENAME).write_text(
        f'db_url = "{RunnerConfig.default_db_url(tmp_path)}"\n'
        f'worker_settings_path = "{settings}"\n'
        'harness_permission_mode = "acceptEdits"\n'
    )

    app = build_hosted_app(RunnerConfig.load(tmp_path))

    harness = app.state.harness
    assert isinstance(harness, ClaudeCodeAdapter)
    assert harness._settings_path == settings
    assert " --permission-mode acceptEdits" in harness.resume_command("/w", "s-1", attended=True)


@pytest.mark.unit
def test_loop_wiring_threads_runner_dir_from_the_resolved_root(tmp_path: Path) -> None:
    """The wrapped takeover command (issue #251) needs ``LoopConfig.runner_dir`` to
    mirror ``RunnerConfig``'s resolved ``root``. Routed through ``RunnerConfig.load()``
    with an un-resolved ``..``-bearing path, since a bare ``tmp_path`` already resolves."""
    real_root = tmp_path / "runner"
    real_root.mkdir()
    (real_root / CONFIG_FILENAME).write_text(f'db_url = "{RunnerConfig.default_db_url(real_root)}"\n')
    unresolved_root = tmp_path / "nested" / ".." / "runner"

    config = RunnerConfig.load(unresolved_root)
    ctx = LoopWiring(config, "", "").context(FakeHub())

    assert ".." not in ctx.config.runner_dir
    assert ctx.config.runner_dir == str(real_root.resolve())


@pytest.mark.unit
def test_loop_wiring_of_defaults_to_no_broker(tmp_path: Path) -> None:
    """D2, blizzard#317: a loop-only caller (``blizzard runner tick``) threads no
    broker, so its ``LoopContext`` publishes nothing — the disposition for the
    store-free/export app and every other path with no stream to feed."""
    config = RunnerConfig(root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path))

    ctx = LoopWiring.of(config).context(FakeHub())

    assert ctx.events is None


@pytest.mark.unit
def test_loop_wiring_of_threads_the_broker_into_the_loop_context(tmp_path: Path) -> None:
    """D2, blizzard#317: the ``host`` verb's one broker reaches ``LoopContext`` — the
    seam Phase 3's publish call sites read off."""
    config = RunnerConfig(root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path))
    broker = EventBroker()

    ctx = LoopWiring.of(config, broker=broker).context(FakeHub())

    assert ctx.events is broker


@pytest.mark.unit
def test_periodic_driver_threads_the_broker_into_its_own_loop_wiring(tmp_path: Path) -> None:
    """D2, blizzard#317: the same broker the ``host`` verb passes to ``build_hosted_app``
    also reaches ``PeriodicDriver``'s own ``LoopWiring`` — the second of the two
    composition paths a single instance must reach."""
    config = RunnerConfig(
        root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path), workspace_root=str(tmp_path / "workspace")
    )
    broker = EventBroker()

    driver = PeriodicDriver(config, interval_seconds=30.0, broker=broker)

    assert driver._wiring.events is broker


@pytest.mark.unit
def test_periodic_driver_defaults_to_no_broker(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path), workspace_root=str(tmp_path / "workspace")
    )

    driver = PeriodicDriver(config, interval_seconds=30.0)

    assert driver._wiring.events is None


@pytest.mark.unit
def test_hosted_app_threads_the_broker_into_create_apps_seam_list(tmp_path: Path) -> None:
    """D2, blizzard#317: the ``host`` verb's broker reaches ``app.state.events`` — the
    seam the stream route (``runner/api/events.py``) reads off the served app."""
    (tmp_path / CONFIG_FILENAME).write_text(f'db_url = "{RunnerConfig.default_db_url(tmp_path)}"\n')
    broker = EventBroker()

    app = build_hosted_app(RunnerConfig.load(tmp_path), events=broker)

    assert app.state.events is broker


@pytest.mark.unit
def test_hosted_app_defaults_to_no_broker(tmp_path: Path) -> None:
    """The disposition for every ``build_hosted_app`` caller but ``host`` itself — none
    exists yet, but the default must stay absent so a future one degrades safely."""
    (tmp_path / CONFIG_FILENAME).write_text(f'db_url = "{RunnerConfig.default_db_url(tmp_path)}"\n')

    app = build_hosted_app(RunnerConfig.load(tmp_path))

    assert app.state.events is None


@pytest.mark.unit
def test_create_app_for_export_stays_broker_less(tmp_path: Path) -> None:
    """The OpenAPI-export/store-free app is one of the paths D2 names as having no
    stream to feed — unlike the hub, ``create_app`` never conjures a broker on its own."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")

    app = create_app(config)

    assert app.state.events is None


@pytest.mark.unit
def test_periodic_driver_resolves_prompts_eagerly_at_construction(tmp_path: Path) -> None:
    """A configured-but-missing ``runner_prompt_file`` must raise ``ConfigError`` from
    the constructor, on the caller's own thread — not from inside the background loop
    thread, where it would silently kill the loop while uvicorn keeps serving."""
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        runner_prompt_file="does-not-exist.md",
    )

    with pytest.raises(ConfigError):
        PeriodicDriver(config, interval_seconds=30.0)


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
def test_resume_marking_on_startup_marks_via_its_injected_clock_and_probe(tmp_path: Path) -> None:
    store = _seeded_running_lease_store(tmp_path)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)  # was actively working when killed
    marking = ResumeMarking(make_stores(store), FixedClock(_NOW), FakeProbe(alive=set()))  # the pid is dead

    marked = marking.on_startup()

    assert marked == 1
    assert store.resume_intent_lease_ids() == {"lease_1"}
