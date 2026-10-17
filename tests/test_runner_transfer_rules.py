"""The ingest rules, pinned by value — plain views, bindings and workspace shapes, no store, no hub, no host files
(``blizzard.runner.transfer.rules``) — and the runner standing an ingest is judged by, from plain ids."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.hub.client import HostHeldRunner, HostRecord
from blizzard.runner.hub.host_identity import HostIdentity
from blizzard.runner.hub.host_registration import standing_of
from blizzard.runner.hub.identity import HostStanding
from blizzard.runner.transfer.refusals import (
    BindingConflict,
    RunnerAlreadyHere,
    RunnerBusy,
    SourceMoved,
    TokenConflict,
    WorkspaceConflict,
)
from blizzard.runner.transfer.rows import HostIngest
from blizzard.runner.transfer.rules import (
    DeclaredRunner,
    WorkspacePlacement,
    WorkspaceShape,
    place_workspaces,
    refuse_archive_rename,
    refuse_binding_conflict,
    refuse_busy,
    refuse_declared_here,
    refuse_source_moved,
    refuse_token_conflict,
    resumes,
)
from blizzard.runner.transfer.runner_stores import HeldBinding, RunnerStoreView

_AT = datetime(2026, 1, 1, tzinfo=UTC)
_LATER = datetime(2026, 1, 2, tzinfo=UTC)


def _record(source: str = "/hosts/there", alive_at: datetime | None = _AT) -> HostIngest:
    return HostIngest(runner_id="rn_1", source=source, source_alive_at=alive_at, ingested_at=_AT)


def _view(*, leases: frozenset[str] = frozenset(), chunks: frozenset[str] = frozenset()) -> RunnerStoreView:
    return RunnerStoreView(
        named_runners=("rn_1",),
        tenant_id=None,
        open_elicitation_lease_ids=leases,
        open_takeover_chunk_ids=chunks,
        held_bindings=(),
        last_alive_at=_AT,
    )


def _binding(chunk: str, env: str, workspace: str | None) -> EnvBinding:
    return EnvBinding(chunk_id=chunk, environment_id=env, workdir="/w", bound_at=_AT, workspace_id=workspace)


def test_a_runner_absent_here_is_a_fresh_ingest() -> None:
    assert not resumes(False, None, source="/hosts/there", runner_name="r", runner_id="rn_1")


def test_a_runner_here_from_this_source_resumes() -> None:
    assert resumes(True, _record(), source="/hosts/there", runner_name="r", runner_id="rn_1")


@pytest.mark.parametrize("record", [None, _record(source="/hosts/elsewhere")])
def test_a_runner_here_from_anything_else_is_already_here(record: HostIngest | None) -> None:
    with pytest.raises(RunnerAlreadyHere, match="already holds runner r"):
        resumes(True, record, source="/hosts/there", runner_name="r", runner_id="rn_1")


def test_an_unmoved_source_passes() -> None:
    refuse_source_moved(_record(), _AT, runner_name="r", source_store="/s.db", here="sqlite:///h.db")


def test_a_source_that_ran_since_the_copy_has_moved() -> None:
    with pytest.raises(SourceMoved, match="has run since"):
        refuse_source_moved(_record(), _LATER, runner_name="r", source_store="/s.db", here="sqlite:///h.db")


def test_an_idle_runner_is_not_busy() -> None:
    refuse_busy(_view(), runner_name="r")


@pytest.mark.parametrize(
    ("view", "named"),
    [(_view(leases=frozenset({"l1"})), "elicitation"), (_view(chunks=frozenset({"c1"})), "takeover")],
)
def test_an_open_elicitation_or_takeover_is_busy(view: RunnerStoreView, named: str) -> None:
    with pytest.raises(RunnerBusy, match=named):
        refuse_busy(view, runner_name="r")


@pytest.mark.parametrize("declared", [DeclaredRunner("r", None), DeclaredRunner("other", "rn_1")])
def test_a_runner_declared_by_name_or_id_is_already_here(declared: DeclaredRunner) -> None:
    with pytest.raises(RunnerAlreadyHere, match="already declares"):
        refuse_declared_here((declared,), runner_name="r", runner_id="rn_1", config_path="/h/host.toml")


def test_an_undeclared_runner_is_not_here() -> None:
    refuse_declared_here((DeclaredRunner("other", None),), runner_name="r", runner_id="rn_1", config_path="/h")


def test_an_archive_takes_no_workspace_rename() -> None:
    refuse_archive_rename(None)
    with pytest.raises(WorkspaceConflict, match="--workspace-id"):
        refuse_archive_rename("ws_new")


_SHAPE = WorkspaceShape("/src", frozenset({"e1"}))
_OTHER = WorkspaceShape("/other", frozenset({"e9"}))


def test_a_new_workspace_is_appended_under_its_own_id() -> None:
    placed = place_workspaces(("ws",), runner_name="r", workspace_id=None, offered={"ws": _SHAPE}, held={})
    assert placed == (WorkspacePlacement("ws", "ws", appended=True),)


def test_the_same_workspace_here_is_reused() -> None:
    placed = place_workspaces(("ws",), runner_name="r", workspace_id=None, offered={"ws": _SHAPE}, held={"ws": _SHAPE})
    assert placed == (WorkspacePlacement("ws", "ws", appended=False),)


def test_a_renamed_workspace_takes_the_new_id() -> None:
    placed = place_workspaces(("ws",), runner_name="r", workspace_id="ws2", offered={"ws": _SHAPE}, held={})
    assert placed == (WorkspacePlacement("ws", "ws2", appended=True),)


@pytest.mark.parametrize(
    ("names", "workspace_id", "offered", "held", "match"),
    [
        (("a", "b"), "new", {"a": _SHAPE, "b": _OTHER}, {}, "names one new id"),
        (("ws",), None, {}, {}, "does not declare"),
        (("ws",), None, {"ws": _SHAPE}, {"ws": _OTHER}, "re-run with --workspace-id <new id>"),
        (("ws",), "taken", {"ws": _SHAPE}, {"taken": _OTHER}, "pick another id"),
        (("ws",), None, {"ws": _SHAPE}, {"twin": _SHAPE}, "re-run with --workspace-id twin"),
        (("ws",), None, {"ws": _SHAPE}, {"near": WorkspaceShape("/src", frozenset({"e1", "e2"}))}, "shares root"),
    ],
)
def test_a_workspace_that_cannot_stand_beside_this_hosts_conflicts(
    names: tuple[str, ...],
    workspace_id: str | None,
    offered: dict[str, WorkspaceShape],
    held: dict[str, WorkspaceShape],
    match: str,
) -> None:
    with pytest.raises(WorkspaceConflict, match=match):
        place_workspaces(names, runner_name="r", workspace_id=workspace_id, offered=offered, held=held)


def test_a_binding_on_an_environment_held_here_for_another_chunk_conflicts() -> None:
    ours = (HeldBinding("rn_here", _binding("c2", "e1", "ws2")),)
    with pytest.raises(BindingConflict, match="holds environment e1"):
        refuse_binding_conflict((_binding("c1", "e1", "ws"),), ours, renamed={"ws": "ws2"}, runner_name="r")


@pytest.mark.parametrize(
    "held",
    [_binding("c1", "e1", "ws"), _binding("c2", "e2", "ws"), _binding("c2", "e1", "elsewhere")],
)
def test_a_binding_on_the_same_chunk_another_environment_or_workspace_does_not_conflict(held: EnvBinding) -> None:
    refuse_binding_conflict((_binding("c1", "e1", "ws"),), (HeldBinding("rn_here", held),), renamed={}, runner_name="r")


@pytest.mark.parametrize(
    ("carries_runner", "offered", "held_here", "match"),
    [
        (True, "", "", "holds no SRC"),
        (False, "", "", "an archive store carries no token"),
        (True, "tok", "other", "already holds a different"),
    ],
)
def test_a_token_that_cannot_move_conflicts(carries_runner: bool, offered: str, held_here: str, match: str) -> None:
    with pytest.raises(TokenConflict, match=match):
        refuse_token_conflict(
            carries_runner=carries_runner,
            offered=offered,
            held_here=held_here,
            source_variable="SRC",
            variable="DST",
            runner_name="r",
            runner_id="rn_1",
        )


@pytest.mark.parametrize(
    ("carries_runner", "offered", "held_here"), [(True, "tok", ""), (True, "tok", "tok"), (False, "", "tok")]
)
def test_a_token_that_can_move_passes(carries_runner: bool, offered: str, held_here: str) -> None:
    refuse_token_conflict(
        carries_runner=carries_runner,
        offered=offered,
        held_here=held_here,
        source_variable="SRC",
        variable="DST",
        runner_name="r",
        runner_id="rn_1",
    )


_HOST = HostIdentity(host_id="hs_1", tenant_id="tn_1", registered_at=_AT)
_HOLDING = HostRecord(host_id="hs_1", tenant_id="tn_1", host_name="h", runners=(HostHeldRunner("rn_1", "r"),))


@pytest.mark.parametrize("tenant_id", [None, "tn_1"])
def test_a_runner_held_in_the_hosts_tenant_stands_held(tenant_id: str | None) -> None:
    assert standing_of("rn_1", tenant_id, _HOLDING, _HOST) == (HostStanding.HELD, None)


def test_a_runner_the_host_does_not_hold_stands_not_held() -> None:
    standing, reason = standing_of("rn_2", None, _HOLDING, _HOST)
    assert standing is HostStanding.NOT_HELD and reason is not None and "hs_1" in reason


def test_a_runner_recorded_in_another_tenant_stands_apart() -> None:
    standing, reason = standing_of("rn_1", "tn_2", _HOLDING, _HOST)
    assert standing is HostStanding.TENANT_DIFFERS and reason is not None and "tn_2" in reason
