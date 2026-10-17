"""The rules an ingest of one runner is held to, each over values already loaded: it returns a decision or raises the
:mod:`~blizzard.runner.transfer.refusals` refusal naming why the ingest cannot go ahead."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.transfer.refusals import (
    BindingConflict,
    RunnerAlreadyHere,
    RunnerBusy,
    SourceMoved,
    TokenConflict,
    WorkspaceConflict,
)
from blizzard.runner.transfer.rows import HostIngest
from blizzard.runner.transfer.runner_stores import HeldBinding, RunnerStoreView

__all__ = [
    "DeclaredRunner",
    "WorkspacePlacement",
    "WorkspaceShape",
    "place_workspaces",
    "refuse_archive_rename",
    "refuse_binding_conflict",
    "refuse_busy",
    "refuse_declared_here",
    "refuse_source_moved",
    "refuse_token_conflict",
    "resumes",
]


@domain_model
@dataclass(frozen=True)
class DeclaredRunner:
    """A runner this host's file declares: its name, and its id when it has one."""

    name: str
    id: str | None


@domain_model
@dataclass(frozen=True)
class WorkspaceShape:
    """What makes a workspace the same one on two hosts: its root resolved against its own host, and its
    environments."""

    root: str
    envs: frozenset[str]

    def described(self) -> str:
        return f"root {self.root} with environments {', '.join(sorted(self.envs)) or 'none'}"


@domain_model
@dataclass(frozen=True)
class WorkspacePlacement:
    """Where one workspace the runner binds lands here: the id it takes, and whether this host must append it."""

    name: str
    target: str
    appended: bool


def resumes(present: bool, record: HostIngest | None, *, source: str, runner_name: str, runner_id: str) -> bool:
    """Whether the ingest resumes one of ``source`` this host recorded, given whether this host's store holds the
    runner (``present``) and its latest ingest ``record``. Raises :class:`RunnerAlreadyHere` when the store holds it
    from anything else."""
    resuming = present and record is not None and record.source == source
    if present and not resuming:
        raise RunnerAlreadyHere(
            f"this host's store already holds runner {runner_name} ({runner_id}), not from an ingest of "
            f"{source}; nothing was changed"
        )
    return resuming


def refuse_source_moved(
    record: HostIngest, last_alive_at: datetime | None, *, runner_name: str, source_store: str, here: str
) -> None:
    """Raises :class:`SourceMoved` when the source's liveness stamp moved since the copy ``record`` names."""
    if record.source_alive_at != last_alive_at:
        raise SourceMoved(
            f"the source {source_store} has run since this host {here} copied runner {runner_name} from it (its "
            f"liveness stamp moved from {record.source_alive_at} to {last_alive_at}), so the copy may be stale; "
            f"nothing was changed. To start over, run `blizzard runner store reset --runner {runner_name}` on this "
            "host, then ingest again"
        )


def refuse_busy(view: RunnerStoreView, *, runner_name: str) -> None:
    """Raises :class:`RunnerBusy` when the runner holds an open elicitation or takeover in the source."""
    if view.open_elicitation_lease_ids:
        raise RunnerBusy(
            f"runner {runner_name} holds an open elicitation on lease(s) "
            f"{', '.join(sorted(view.open_elicitation_lease_ids))}; let it finish in the source, then re-run"
        )
    if view.open_takeover_chunk_ids:
        raise RunnerBusy(
            f"runner {runner_name} holds an open takeover of chunk(s) "
            f"{', '.join(sorted(view.open_takeover_chunk_ids))}; end it in the source, then re-run"
        )


def refuse_declared_here(
    declared: Sequence[DeclaredRunner], *, runner_name: str, runner_id: str, config_path: str
) -> None:
    """Raises :class:`RunnerAlreadyHere` when this host's file already declares a runner of that name or id."""
    for held in declared:
        if runner_name == held.name or (held.id is not None and held.id == runner_id):
            raise RunnerAlreadyHere(
                f"{config_path} already declares runner {held.name} ({held.id or 'no id'}), so it cannot take in "
                f"runner {runner_name} ({runner_id}); nothing was changed"
            )


def refuse_archive_rename(workspace_id: str | None) -> None:
    """Raises :class:`WorkspaceConflict` when ``--workspace-id`` names a workspace for an archive's runner, which
    already runs in this host's workspace."""
    if workspace_id is not None:
        raise WorkspaceConflict(
            "--workspace-id renames the workspace a host directory's runner brings; an archive's runner already runs "
            "in this host's workspace"
        )


def place_workspaces(
    names: Sequence[str],
    *,
    runner_name: str,
    workspace_id: str | None,
    offered: Mapping[str, WorkspaceShape],
    held: Mapping[str, WorkspaceShape],
) -> tuple[WorkspacePlacement, ...]:
    """Where each workspace ``names`` lists — the ones the runner binds — lands here. ``offered`` is the source's
    workspaces by id, ``held`` this host's, in its file's order. Raises :class:`WorkspaceConflict` when one cannot
    stand beside this host's under the id it would take."""
    if workspace_id is not None and len(names) != 1:
        raise WorkspaceConflict(
            f"--workspace-id names one new id, but runner {runner_name} is bound to {len(names)} workspaces "
            f"({', '.join(names) or 'none'})"
        )
    placements: list[WorkspacePlacement] = []
    for name in names:
        shape = offered.get(name)
        if shape is None:
            raise WorkspaceConflict(f"runner {runner_name} binds workspace {name!r}, which the source does not declare")
        target = workspace_id or name
        ours = held.get(target)
        if ours is not None and ours != shape:
            if workspace_id is None:
                raise WorkspaceConflict(
                    f"this host gives workspace id {target!r} to {ours.described()}, and the source's is "
                    f"{shape.described()}: re-run with --workspace-id <new id>"
                )
            raise WorkspaceConflict(
                f"--workspace-id {target!r} is this host's workspace on {ours.described()}, not the source's "
                f"{shape.described()}; pick another id"
            )
        if ours is None:
            _refuse_twin(name, shape, held)
        placements.append(WorkspacePlacement(name, target, appended=ours is None))
    return tuple(placements)


def _refuse_twin(name: str, shape: WorkspaceShape, held: Mapping[str, WorkspaceShape]) -> None:
    """A workspace appended under a new id must be neither this host's under another id nor share a root and an
    environment with one."""
    twin = next((held_id for held_id, other in held.items() if other == shape), None)
    if twin is not None:
        raise WorkspaceConflict(
            f"the source's workspace {name!r} is this host's workspace {twin!r} ({held[twin].described()}): re-run "
            f"with --workspace-id {twin}"
        )
    for held_id, other in held.items():
        if other.root == shape.root and other.envs & shape.envs:
            raise WorkspaceConflict(
                f"the source's workspace {name!r} shares root {shape.root} with this host's workspace {held_id!r}, "
                f"and both name environment(s) {', '.join(sorted(other.envs & shape.envs))}"
            )


def refuse_binding_conflict(
    moving: Sequence[EnvBinding], ours: Sequence[HeldBinding], *, renamed: Mapping[str, str], runner_name: str
) -> None:
    """Raises :class:`BindingConflict` when a binding the runner holds names an environment a runner of this host
    holds for another chunk. ``renamed`` maps a source workspace id to the one it takes here."""
    for binding in moving:
        workspace = renamed.get(binding.workspace_id, binding.workspace_id) if binding.workspace_id else None
        for held in ours:
            other = held.binding
            if other.environment_id != binding.environment_id or other.chunk_id == binding.chunk_id:
                continue
            if workspace is None or other.workspace_id is None or other.workspace_id == workspace:
                raise BindingConflict(
                    f"runner {runner_name} holds environment {binding.environment_id} of workspace "
                    f"{workspace or '(unknown)'} for chunk {binding.chunk_id}, and this host's runner "
                    f"{held.runner_id} holds it for chunk {other.chunk_id}; release one, then re-run"
                )


def refuse_token_conflict(
    *,
    carries_runner: bool,
    offered: str,
    held_here: str,
    source_variable: str,
    variable: str,
    runner_name: str,
    runner_id: str,
) -> None:
    """Raises :class:`TokenConflict` when the runner's token cannot move. ``carries_runner`` is whether the source
    brings the runner's ``[[runner]]``; ``offered`` is the token its ``.env`` holds under ``source_variable``, and
    ``held_here`` the one this host's holds under ``variable``."""
    if carries_runner and not offered:
        raise TokenConflict(
            f"the source's .env holds no {source_variable} for runner {runner_name}, so there is none to move"
        )
    if not carries_runner and not held_here:
        raise TokenConflict(
            f"an archive store carries no token, and this host's .env holds no {variable} for runner {runner_name}"
        )
    if offered and held_here and held_here != offered:
        raise TokenConflict(
            f"this host's .env already holds a different {variable}; remove it, then re-run (runner {runner_id})"
        )
