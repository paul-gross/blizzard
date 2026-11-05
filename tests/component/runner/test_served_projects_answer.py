"""A runner names the projects it serves — the runner side.

Registration declares the configured projects (``None`` sent as JSON null, never ``[]``), and the
hub's answer is kept whole in the store: a reply answering none keeps it, an unreachable hub keeps
everything, and a reply naming another runner stores nothing. Status reads the stored answer with no
hub call. ``runner init --project`` scaffolds and adds with the projects it names. A claim's
incompatible ``409`` — with or without a ``reason`` — is read as an incompatible denial, never a lost
race."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import Result

from blizzard.cli.main import blizzard
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.host_config import HostConfig
from blizzard.runner.hub.client import RunnerAddRefusalReason, RunnerAddRefused
from blizzard.runner.hub.identity import RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.hub.served_projects import (
    ServedProject,
    ServedProjectsAnswer,
)
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.loop.context import LoopConfig, LoopContext
from tests.support.host_files import load_runner_config
from tests.support.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
    serving,
)
from tests.support.runner_init_fakes import FakeInitHub, init_runner
from tests.support.served_projects_answer import (
    _AT,  # pyright: ignore[reportPrivateUsage]
    _BLIZZARD,  # pyright: ignore[reportPrivateUsage]
    _UNRESOLVED,  # pyright: ignore[reportPrivateUsage]
)

_LATER = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


_CONTEXT = ServedProject("prj_2", "prj_2", "blizzard-context", "Blizzard context")


_HUB = "http://hub.test:8421"


def _store(tmp_path: Path):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_runner_identity(RunnerIdentity(runner_id=FIXTURE_RUNNER_ID, runner_name="r", registered_at=_AT))
    return store


def test_no_answer_is_told_apart_from_an_answer_serving_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.served_projects_answer() is None
    store.record_served_projects((), at=_AT)
    assert store.served_projects_answer() == ServedProjectsAnswer(projects=(), answered_at=_AT)


def test_an_answer_is_kept_in_declared_order_and_replaced_whole(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_served_projects((_CONTEXT, _UNRESOLVED, _BLIZZARD), at=_AT)
    assert store.served_projects_answer() == ServedProjectsAnswer((_CONTEXT, _UNRESOLVED, _BLIZZARD), _AT)
    store.record_served_projects((_BLIZZARD,), at=_LATER)
    assert store.served_projects_answer() == ServedProjectsAnswer((_BLIZZARD,), _LATER)


def test_re_recording_the_same_runners_identity_keeps_the_answer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_served_projects((), at=_AT)
    store.record_runner_identity(
        RunnerIdentity(runner_id=FIXTURE_RUNNER_ID, runner_name="renamed", registered_at=_LATER)
    )
    assert store.served_projects_answer() == ServedProjectsAnswer(projects=(), answered_at=_AT)


def _ctx(tmp_path: Path, hub: FakeHub, projects: tuple[str, ...] | None) -> LoopContext:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(
            handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict=None
        ),
        probe=FakeProbe(alive=set()),
        config=LoopConfig(
            runner_name="r", runner_id=FIXTURE_RUNNER_ID, bindings=serving(1), agents=1, projects=projects
        ),
    )
    return dataclasses.replace(ctx, identity=RunnerIdentityHolder(store.runner_identity()))


@pytest.mark.parametrize("projects", [None, (), ("blizzard", "old-slug")])
def test_registration_declares_the_configured_projects_as_configured(
    tmp_path: Path, projects: tuple[str, ...] | None
) -> None:
    hub = FakeHub()
    Registration(_ctx(tmp_path, hub, projects)).run()
    assert hub.registered_projects == [projects]


def test_registration_stores_the_answer_after_the_identity_and_a_reply_answering_none_keeps_it(
    tmp_path: Path,
) -> None:
    hub = FakeHub()
    hub.served_projects = (_BLIZZARD, _UNRESOLVED)
    ctx = _ctx(tmp_path, hub, ("blizzard", "old-slug"))
    Registration(ctx).run()
    stored = ctx.stores.served_projects.served_projects_answer()
    assert stored is not None and stored.projects == (_BLIZZARD, _UNRESOLVED)

    hub.served_projects = None  # a hub that predates projects
    Registration(ctx).run()
    assert ctx.stores.served_projects.served_projects_answer() == stored


def test_an_unreachable_hub_keeps_the_stored_answer(tmp_path: Path) -> None:
    hub = FakeHub()
    hub.served_projects = (_BLIZZARD,)
    ctx = _ctx(tmp_path, hub, ("blizzard",))
    Registration(ctx).run()
    stored = ctx.stores.served_projects.served_projects_answer()
    hub.down = True
    Registration(ctx).run()
    assert ctx.stores.served_projects.served_projects_answer() == stored


def test_a_reply_naming_another_runner_stores_no_answer(tmp_path: Path) -> None:
    hub = FakeHub()
    hub.served_projects = (_BLIZZARD,)
    ctx = _ctx(tmp_path, hub, ("blizzard",))
    Registration(ctx).run()
    stored = ctx.stores.served_projects.served_projects_answer()
    hub.issued[hub.token] = "rn_other"
    hub.served_projects = ()
    Registration(ctx).run()
    assert ctx.stores.served_projects.served_projects_answer() == stored


def _init(hub: FakeInitHub, root: Path, *args: str) -> Result:
    return init_runner(hub).invoke(blizzard, ["runner", "init", str(root), "--hub", _HUB, *args])


def test_init_scaffolds_and_adds_with_every_project_named(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = tmp_path / "runner"
    result = _init(fake_init_hub, root, "--project", "blizzard", "--project", "blizzard-context")
    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.declared == [("blizzard", "blizzard-context")]
    assert load_runner_config(root).projects == ("blizzard", "blizzard-context")


def test_init_naming_no_project_scaffolds_default_and_adds_naming_none(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "runner"
    result = _init(fake_init_hub, root)
    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.declared == [None]
    assert load_runner_config(root).projects == ("default",)


def test_init_naming_projects_its_config_does_not_hold_stops_and_adds_nothing(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "runner"
    root.mkdir()
    (root / "blizzard-host.toml").write_text(HostConfig.scaffold(root, hub_url=_HUB, projects=("blizzard",)).to_toml())
    result = _init(fake_init_hub, root, "--project", "other")
    assert result.exit_code != 0
    assert "edit its `projects`" in result.output
    assert fake_init_hub.admin.added == []


def test_init_refused_an_unknown_project_says_so_and_adds_nothing(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    fake_init_hub.admin.refusal = RunnerAddRefused(
        "POST /api/runners -> 422", reason=RunnerAddRefusalReason.NO_SUCH_PROJECT, detail="no project answers to: nope"
    )
    result = _init(fake_init_hub, tmp_path / "runner", "--project", "nope")
    assert result.exit_code != 0
    assert "no project answers to: nope" in result.output
    assert "nothing was added" in result.output
