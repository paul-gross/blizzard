"""Selected worker command's validated collaborator scope."""

from collections.abc import Mapping
from typing import Any

import click

from blizzard.cli import command_root
from blizzard.cli import wiring as shared
from blizzard.cli.collaborators import CliCollaborators
from blizzard.foundation.registry.scopes import ScopeValues, supply
from blizzard.runner.cli import wiring
from blizzard.runner.cli.worker_call import WorkerPolicy, WorkerSession


class ContainerWorkerCommand(click.Command):
    def __init__(
        self,
        *args: Any,
        collaborators: Mapping[str, type[object]],
        policy: WorkerPolicy,
        area: command_root.Area | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        taken = sorted({param.name for param in self.params} & set(collaborators))
        if taken:
            raise ValueError(f"{self.name}: collaborators named like parameters: {', '.join(taken)}")
        self.collaborators = dict(collaborators)
        self.policy = policy
        self._area = area

    def areas(self) -> list[command_root.Area]:
        areas: list[command_root.Area] = [shared.register, wiring.register]
        if self._area is not None:
            areas.append(self._area)
        return areas

    def invoke(self, ctx: click.Context) -> Any:
        values = self.values(ctx)
        with command_root.command_scope(
            self.areas(), owner=ctx.command_path, values=values, collaborators=self.collaborators
        ) as resolved:
            ctx.params.update(resolved)
            return super().invoke(ctx)

    def values(self, ctx: click.Context) -> ScopeValues:
        return ScopeValues(
            (
                supply(shared.CLIENT_FACTORY, CliCollaborators.of(ctx).client_factory),
                supply(shared.TRACE_HEADERS, WorkerSession.from_context(ctx)),
                supply(wiring.SESSION, WorkerSession.from_context(ctx)),
                supply(wiring.POLICY, self.policy),
            )
        )
