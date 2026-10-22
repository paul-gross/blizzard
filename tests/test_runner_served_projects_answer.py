"""A runner names the projects it serves — the runner side.

Registration declares the configured projects (``None`` sent as JSON null, never ``[]``), and the
hub's answer is kept whole in the store: a reply answering none keeps it, an unreachable hub keeps
everything, and a reply naming another runner stores nothing. Status reads the stored answer with no
hub call. ``runner init --project`` scaffolds and adds with the projects it names. A claim's
incompatible ``409`` — with or without a ``reason`` — is read as an incompatible denial, never a lost
race."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from click.testing import Result

from blizzard.cli.main import blizzard
from blizzard.foundation.claim_denials import ClaimDenialReason
from blizzard.runner.host_config import HostConfig
from blizzard.runner.hub.client import ClaimRequest, RunnerAddRefusalReason, RunnerAddRefused
from blizzard.runner.hub.identity import RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from blizzard.runner.hub.served_projects import (
    ServedProject,
    ServedProjectsAnswer,
    ServedProjectStatus,
    served_projects_status,
)
from blizzard.runner.lifecycle.model import ClaimVerdict, claim_denial_fields, claim_verdict
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.loop.context import LoopConfig, LoopContext
from tests.host_files import load_runner_config
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
    serving,
)
from tests.runner_init_fakes import FakeInitHub, init_runner

_AT = datetime(2026, 10, 8, 11, 0, tzinfo=UTC)
_LATER = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
_BLIZZARD = ServedProject("blizzard", "prj_1", "blizzard", "Blizzard")
_CONTEXT = ServedProject("prj_2", "prj_2", "blizzard-context", "Blizzard context")
_UNRESOLVED = ServedProject("old-slug")
_HUB = "http://hub.test:8421"


# --- the stored answer ------------------------------------------------------------------------------


def _store(tmp_path: Path):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_runner_identity(RunnerIdentity(runner_id=FIXTURE_RUNNER_ID, runner_name="r", registered_at=_AT))
    return store


@pytest.mark.component
def test_no_answer_is_told_apart_from_an_answer_serving_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.served_projects_answer() is None
    store.record_served_projects((), at=_AT)
    assert store.served_projects_answer() == ServedProjectsAnswer(projects=(), answered_at=_AT)


@pytest.mark.component
def test_an_answer_is_kept_in_declared_order_and_replaced_whole(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_served_projects((_CONTEXT, _UNRESOLVED, _BLIZZARD), at=_AT)
    assert store.served_projects_answer() == ServedProjectsAnswer((_CONTEXT, _UNRESOLVED, _BLIZZARD), _AT)
    store.record_served_projects((_BLIZZARD,), at=_LATER)
    assert store.served_projects_answer() == ServedProjectsAnswer((_BLIZZARD,), _LATER)


@pytest.mark.component
def test_re_recording_the_same_runners_identity_keeps_the_answer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_served_projects((), at=_AT)
    store.record_runner_identity(
        RunnerIdentity(runner_id=FIXTURE_RUNNER_ID, runner_name="renamed", registered_at=_LATER)
    )
    assert store.served_projects_answer() == ServedProjectsAnswer(projects=(), answered_at=_AT)


# --- the registration step --------------------------------------------------------------------------


def _ctx(tmp_path: Path, hub: FakeHub, projects: tuple[str, ...] | None) -> LoopContext:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=FakeProbe(alive=set()),
        config=LoopConfig(
            runner_name="r", runner_id=FIXTURE_RUNNER_ID, bindings=serving(1), agents=1, projects=projects
        ),
    )
    return type(ctx)(**{**ctx.__dict__, "identity": RunnerIdentityHolder(store.runner_identity())})


@pytest.mark.component
@pytest.mark.parametrize("projects", [None, (), ("blizzard", "old-slug")])
def test_registration_declares_the_configured_projects_as_configured(
    tmp_path: Path, projects: tuple[str, ...] | None
) -> None:
    hub = FakeHub()
    Registration(_ctx(tmp_path, hub, projects)).run()
    assert hub.registered_projects == [projects]


@pytest.mark.component
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


@pytest.mark.component
def test_an_unreachable_hub_keeps_the_stored_answer(tmp_path: Path) -> None:
    hub = FakeHub()
    hub.served_projects = (_BLIZZARD,)
    ctx = _ctx(tmp_path, hub, ("blizzard",))
    Registration(ctx).run()
    stored = ctx.stores.served_projects.served_projects_answer()
    hub.down = True
    Registration(ctx).run()
    assert ctx.stores.served_projects.served_projects_answer() == stored


@pytest.mark.component
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


# --- what status shows ------------------------------------------------------------------------------


@pytest.mark.unit
def test_status_lists_the_answer_then_each_configured_entry_not_yet_answered() -> None:
    answer = ServedProjectsAnswer((_BLIZZARD, _UNRESOLVED), _AT)
    assert served_projects_status(("blizzard", "old-slug", "blizzard-context"), answer) == (
        ServedProjectStatus("blizzard", "prj_1", "blizzard", "Blizzard"),
        ServedProjectStatus("old-slug"),
        ServedProjectStatus("blizzard-context", answered=False),
    )


@pytest.mark.unit
def test_status_without_an_answer_marks_every_configured_entry_not_yet_answered() -> None:
    assert served_projects_status(("default",), None) == (ServedProjectStatus("default", answered=False),)


@pytest.mark.unit
def test_status_shows_the_hubs_answer_to_a_config_naming_none_and_nothing_before_one() -> None:
    assert served_projects_status(None, None) is None
    assert served_projects_status(None, ServedProjectsAnswer((_BLIZZARD,), _AT)) == (
        ServedProjectStatus("blizzard", "prj_1", "blizzard", "Blizzard"),
    )
    assert served_projects_status((), ServedProjectsAnswer((), _AT)) == ()


# --- the wire ---------------------------------------------------------------------------------------


def _client(handler) -> HttpHubClient:  # type: ignore[no-untyped-def]
    return HttpHubClient(httpx.Client(base_url="http://hub.test", transport=httpx.MockTransport(handler)))


def _registration(seen: dict[str, object], answer: object = None):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200, json={"runner_id": "rn_1", "runner_name": "r", "first_registration": False, "projects": answer}
        )

    return handler


@pytest.mark.unit
def test_a_registration_naming_no_projects_sends_null_never_an_empty_list() -> None:
    seen: dict[str, object] = {}
    _client(_registration(seen)).register_runner("r", "ws1")
    assert "projects" in seen and seen["projects"] is None


@pytest.mark.unit
@pytest.mark.parametrize("projects", [(), ("blizzard", "prj_2")])
def test_a_registration_sends_the_declared_projects_as_given(projects: tuple[str, ...]) -> None:
    seen: dict[str, object] = {}
    _client(_registration(seen)).register_runner("r", "ws1", projects=projects)
    assert seen["projects"] == list(projects)


@pytest.mark.unit
def test_a_registration_reads_the_answer_and_a_hub_answering_none() -> None:
    answer = [
        {"entry": "blizzard", "project_id": "prj_1", "slug": "blizzard", "name": "Blizzard"},
        {"entry": "old-slug", "project_id": None, "slug": None, "name": None},
    ]
    assert _client(_registration({}, answer)).register_runner("r", "ws1").projects == (_BLIZZARD, _UNRESOLVED)
    assert _client(_registration({})).register_runner("r", "ws1").projects is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ({}, ClaimDenialReason.CAPABILITY),
        ({"reason": "capability"}, ClaimDenialReason.CAPABILITY),
        ({"reason": "project"}, ClaimDenialReason.PROJECT),
    ],
)
def test_an_incompatible_claim_denial_with_or_without_a_reason_is_never_a_lost_race(
    extra: dict[str, str], reason: ClaimDenialReason
) -> None:
    body = {"chunk_id": "ch_1", "incompatible_runner_id": "rn_1", "detail": "runner does not serve blizzard", **extra}
    outcome = _client(lambda _request: httpx.Response(409, json=body)).claim_route(
        ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=["e1"])
    )
    assert outcome.conflict is None
    assert outcome.denied_incompatible is not None and outcome.denied_incompatible.reason is reason
    assert claim_verdict(outcome) is ClaimVerdict.INCOMPATIBLE
    assert claim_denial_fields(outcome) == {"reason": reason, "detail": "runner does not serve blizzard"}


# --- runner init --project --------------------------------------------------------------------------


def _init(hub: FakeInitHub, root: Path, *args: str) -> Result:
    return init_runner(hub).invoke(blizzard, ["runner", "init", str(root), "--hub", _HUB, *args])


@pytest.mark.component
def test_init_scaffolds_and_adds_with_every_project_named(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = tmp_path / "runner"
    result = _init(fake_init_hub, root, "--project", "blizzard", "--project", "blizzard-context")
    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.declared == [("blizzard", "blizzard-context")]
    assert load_runner_config(root).projects == ("blizzard", "blizzard-context")


@pytest.mark.component
def test_init_naming_no_project_scaffolds_default_and_adds_naming_none(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "runner"
    result = _init(fake_init_hub, root)
    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.declared == [None]
    assert load_runner_config(root).projects == ("default",)


@pytest.mark.component
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


@pytest.mark.component
def test_init_refused_an_unknown_project_says_so_and_adds_nothing(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    fake_init_hub.admin.refusal = RunnerAddRefused(
        "POST /api/runners -> 422", reason=RunnerAddRefusalReason.NO_SUCH_PROJECT, detail="no project answers to: nope"
    )
    result = _init(fake_init_hub, tmp_path / "runner", "--project", "nope")
    assert result.exit_code != 0
    assert "no project answers to: nope" in result.output
    assert "nothing was added" in result.output
