"""The hosts an ingest reads, as values: this host as :class:`IngestHost`, a source as :class:`SourceHost`, and the
port :class:`ISourceHosts` that opens a source and carries its ``[[runner]]``.

The declarations here mirror what the host file authors — a runner, a workspace, a subscription — field for field, so
an edge adapter maps them back to its own types to write them. The adapters live in the edge modules; this module
names no configuration type."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.runner.environments.provider import WorkspaceRepo
from blizzard.runner.transfer.source import SourceKind

__all__ = [
    "DeclaredBinding",
    "DeclaredSubscription",
    "DeclaredWorkspace",
    "HostDeclarations",
    "ISourceHosts",
    "IngestHost",
    "RunnerDeclaration",
    "SourceHost",
    "SourceKind",
]


@domain_model
@dataclass(frozen=True)
class DeclaredBinding:
    """One ``[[runner]] projects`` binding: a project and the workspace it runs in."""

    project: str
    workspace: str


@domain_model
@dataclass(frozen=True)
class RunnerDeclaration:
    """One ``[[runner]]`` as a host file authors it, with the errors found reading it."""

    name: str
    id: str | None = None
    subscription: str | None = None
    bindings: tuple[DeclaredBinding, ...] = ()
    errors: tuple[str, ...] = ()


@domain_model
@dataclass(frozen=True)
class DeclaredWorkspace:
    """One ``[[workspace]]``, every field the host file carries, and :attr:`effective_root` — its root resolved
    against the host that declared it."""

    id: str
    root: str
    provider: str
    envs: tuple[str, ...]
    max_environments: int
    repos: tuple[WorkspaceRepo, ...]
    base_branch: str
    prompt: str
    prompt_file: str
    prompt_package: str
    effective_root: str


@domain_model
@dataclass(frozen=True)
class DeclaredSubscription:
    """One ``[[subscription]]``, every field the host file carries."""

    slug: str
    name: str
    provider: str
    credentials_path: str | None
    sample_interval_seconds: int
    usage_api_base: str | None


@domain_model
@dataclass(frozen=True)
class HostDeclarations:
    """What one host's file declares: where it is, its runners, its workspaces, and its subscriptions."""

    root: Path
    config_path: Path
    runners: tuple[RunnerDeclaration, ...]
    workspaces: tuple[DeclaredWorkspace, ...]
    subscriptions: tuple[DeclaredSubscription, ...]


@domain_model
@dataclass(frozen=True)
class IngestHost:
    """The host an ingest runs into: where its files are, its store, its hub, and what its file declares."""

    root: Path
    data_dir: Path
    runners_dir: Path
    db_url: str
    hub_url: str
    #: Whether the host holds a token for its hub, so it is registered there.
    holds_host_token: bool
    declared: HostDeclarations


@domain_model
@dataclass(frozen=True)
class SourceHost:
    """The resolved source: its kind, the path the ingest record names, its store file, and — for a host directory, or
    a runner directory graduated in memory — what its file declares, and the variable its ``.env`` holds a runner's
    token under when that is not the runner's own."""

    kind: SourceKind
    path: Path
    store: Path
    declared: HostDeclarations | None
    token_env: str | None = None

    def token_variable(self, runner_name: str) -> str:
        """The variable the source's ``.env`` holds ``runner_name``'s token under."""
        return self.token_env or hub_token_variable(runner_name)


class ISourceHosts(Protocol):
    """How a source is opened, and how its ``[[runner]]`` is carried to this host. Raises
    :class:`~blizzard.runner.transfer.refusals.SourceUnsupported` for a source it cannot read.

    :meth:`runner` (given a source), :meth:`path_keys` and :meth:`carried` act on a :class:`SourceHost` this port
    returned from :meth:`open_host` or :meth:`graduate`, and raise ``LookupError`` for any other."""

    def kind(self, source: Path) -> SourceKind:
        """What ``source`` is: a host directory, a SQLite archive store, or a runner directory that has not
        graduated."""
        ...

    def legacy_socket(self, root: Path) -> Path:
        """The socket a runner directory that has not graduated answers on."""
        ...

    def open_host(self, path: Path) -> SourceHost:
        """The graduated host directory at ``path``."""
        ...

    def legacy_store(self, path: Path) -> tuple[Path, str]:
        """The store of the runner directory at ``path`` that has not graduated, and the variable its ``.env`` holds
        its token under."""
        ...

    def graduate(self, path: Path, *, runner_id: str, served: tuple[str, ...]) -> SourceHost:
        """The runner directory at ``path`` read as the host of one it graduates to, ``runner_id`` the runner its
        store records and ``served`` the projects that runner serves."""
        ...

    def runner(self, source: SourceHost | None, name: str) -> RunnerDeclaration:
        """The ``[[runner]]`` an offline verb acts on: the one ``name`` names by name or id in ``source``'s file, or —
        for an archive, which carries rows only — in this host's. Raises
        :class:`~blizzard.runner.config_table.ConfigError` when the file holds none."""
        ...

    def path_keys(self, source: SourceHost, runner: str) -> Mapping[tuple[str, ...], Path]:
        """The path keys ``runner``'s ``[[runner]]`` in ``source`` sets, each resolved against the source's root."""
        ...

    def carried(
        self,
        source: SourceHost,
        runner: str,
        *,
        projects: tuple[DeclaredBinding, ...] | None,
        copies: Mapping[tuple[str, ...], Path],
    ) -> str:
        """``runner``'s ``[[runner]]`` text in ``source``, authored bytes kept, with ``projects`` written in when given
        and each path key of ``copies`` pointing at its copy."""
        ...
