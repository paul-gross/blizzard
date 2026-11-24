"""What an ingest or a runner reset reads and moves of one runner, through a port bound to that runner.

A factory binds the port once the runner's id is resolved, so none of its methods takes a runner id. Each store is
named by its URL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.hub.sealed_tokens import SealedHubToken
from blizzard.runner.transfer.rows import HostIngest

__all__ = [
    "HeldBinding",
    "IRunnerBoundReads",
    "IRunnerBoundReadsFactory",
    "IRunnerBoundStores",
    "IRunnerBoundStoresFactory",
    "RunnerStoreView",
]


@domain_model
@dataclass(frozen=True)
class HeldBinding:
    """A held binding, beside the runner holding it."""

    runner_id: str
    binding: EnvBinding


@domain_model
@dataclass(frozen=True)
class RunnerStoreView:
    """What an ingest reads of one store about one runner: the runners its identity rows name, the tenant its recorded
    identity stands in, its open elicitations and takeovers, its held bindings, and the store's last liveness stamp."""

    named_runners: tuple[str, ...]
    #: The tenant of the identity the runner recorded; ``None`` when it recorded none or no tenant.
    tenant_id: str | None
    open_elicitation_lease_ids: frozenset[str]
    open_takeover_chunk_ids: frozenset[str]
    held_bindings: tuple[EnvBinding, ...]
    last_alive_at: datetime | None
    #: The projects the hub last answered the runner serves, by slug — each one a project answered to.
    served_projects: tuple[str, ...] = ()


class IRunnerBoundReads(Protocol):
    """One runner's reads of store files: its view, its latest ingest, and its sealed hub token."""

    def runner_view(self, url: str) -> RunnerStoreView:
        """What the store at ``url`` holds about the runner."""
        ...

    def latest_host_ingest(self, url: str) -> HostIngest | None:
        """The newest ingest of the runner the store at ``url`` recorded."""
        ...

    def sealed_hub_token(self, url: str) -> SealedHubToken | None:
        """The runner's sealed hub token the store at ``url`` holds, or ``None``."""
        ...


class IRunnerBoundStores(IRunnerBoundReads, Protocol):
    """One runner's reach into store files: its reads, and its rows copied or removed."""

    def copy_runner(
        self,
        source_url: str,
        destination_url: str,
        *,
        workspace_ids: Mapping[str, str] | None = None,
        bound_workspaces: Sequence[str] = (),
        ingest: HostIngest | None = None,
        hub_token: SealedValue | None = None,
    ) -> None:
        """Copy the runner's rows from the store at ``source_url`` into the one at ``destination_url``, in one
        destination transaction, with ``ingest``'s record and ``hub_token``'s seal; the source is only read
        (:meth:`IRunnerTransfer.copy_runner`)."""
        ...

    def remove_runner_rows(self, url: str, *, keep_counters: bool, drop_host: bool = False) -> None:
        """Delete the runner's rows from the store at ``url``, its outbox counters too unless ``keep_counters``, and,
        with ``drop_host``, its sealed hub token and its hosting there, since it has left that host — all in one
        transaction."""
        ...


class IRunnerBoundReadsFactory(Protocol):
    """Binds :class:`IRunnerBoundReads` to one runner."""

    def reads_for(self, runner_id: str) -> IRunnerBoundReads:
        """The store reads bound to ``runner_id``."""
        ...


class IRunnerBoundStoresFactory(Protocol):
    """Binds :class:`IRunnerBoundStores` to one runner."""

    def for_runner(self, runner_id: str) -> IRunnerBoundStores:
        """The stores bound to ``runner_id``."""
        ...
