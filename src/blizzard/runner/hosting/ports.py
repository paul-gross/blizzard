"""The hosted-runner repository and admission seams."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.runner.hosting.model import HostedRunnerFact, HostedRunnerOp, HostingFact, RunnerAdmission
from blizzard.runner.hub.client import HostRefused, IHostRunnerAdder, RetiredHostRunner
from blizzard.runner.hub.identity import RunnerIdentity


class IReadHostedRunnerRepository(Protocol):
    """Which runners the host holds, and the facts each one's state derives from."""

    def hosted_runner_ids(self) -> frozenset[str]:
        """Every runner with a hosted row, added or retired."""
        ...

    def active(self) -> tuple[str, ...]:
        """The runners whose newest fact is ``added``, in the order they were first hosted."""
        ...

    def retired(self) -> frozenset[str]:
        """The runners whose newest fact is ``retired``."""
        ...

    def facts(self, runner_id: str) -> tuple[HostedRunnerFact, ...]:
        """``runner_id``'s facts, oldest first; none for a runner the host never held."""
        ...


class IWriteHostedRunnerRepository(IReadHostedRunnerRepository, Protocol):
    def host(self, runner_ids: Sequence[str], hosting: HostingFact) -> tuple[str, ...]:
        """Give each of ``runner_ids`` the store does not already hold, added or retired, a hosted row and an
        ``added`` fact, all in one transaction; the ones it hosted, in order."""
        ...

    def append_fact(self, runner_id: str, op: HostedRunnerOp, hosting: HostingFact) -> None:
        """Append one fact about a runner the store holds."""
        ...

    def drop(self, runner_id: str) -> bool:
        """Delete ``runner_id``'s hosted row and facts, for a runner that has left this host's store; whether it held
        any."""
        ...


class IAdmissionHandle(Protocol):
    """One open admission — the write transaction its runner and host halves share. The domain-facing view declares
    no connection access; each store's own ``*_locked`` method recovers what it runs on."""

    @property
    def runner_id(self) -> str:
        """The runner admitted."""
        ...


class IWriteAdmittedHosting(Protocol):
    """The host half of an admission: the admitted runner's hosting, on the admission's own transaction."""

    def host_admitted_locked(self, handle: IAdmissionHandle, hosting: HostingFact, *, alongside: Sequence[str]) -> bool:
        """Host the admitted runner unless the store already holds it; whether it was hosted. A store hosting no
        runner yet first hosts every runner ``alongside`` (the ones its identity rows already name), under the seed's
        door and actor."""
        ...


class IWriteAdmittedHubToken(Protocol):
    """The admitted runner's sealed hub token, on the admission's own transaction."""

    def seal_admitted_locked(self, handle: IAdmissionHandle, sealed: SealedValue, *, at: datetime) -> bool:
        """Insert ``sealed`` as the admitted runner's first row unless one is held; whether it was inserted."""
        ...


class IRunnerAdmission(Protocol):
    """One runner's identity row and its hosting written in one transaction, so no crash leaves the row unhosted.
    Bound to that one runner."""

    def admit(self, admission: RunnerAdmission, token: SealedValue) -> None:
        """Write an added runner's identity row, first settings revision, hosted row, ``added`` fact and sealed
        token (inserted only where none is held)."""
        ...

    def record_joined(self, identity: RunnerIdentity, hosting: HostingFact) -> None:
        """Write a joined runner's identity row and, unless the store already holds it, its hosted row and ``added``
        fact."""
        ...


class IRunnerAdmissions(Protocol):
    """An admission bound to each runner the hub mints."""

    def admission(self, runner_id: str) -> IRunnerAdmission: ...


class INewRunnerSettings(Protocol):
    """A new runner's settings, judged as a write of them is before anything is minted."""

    def problems(self, name: str, entries: Mapping[str, Entry]) -> Mapping[str, str]:
        """Every failing key of ``entries`` (its ``name`` among them) and why; empty when they would resolve."""
        ...


class IHostedRunnerNames(Protocol):
    """The names the host's runners go by."""

    def name_of(self, runner_id: str) -> str:
        """A hosted runner's name: its ``[[runner]]``'s, else its stored ``name`` entry, else its id."""
        ...

    def unhosted_names(self) -> frozenset[str]:
        """The names of the ``[[runner]]`` entries no hosted runner stands for yet — a scaffold not yet joined."""
        ...


class IRunnerTokenSealer(Protocol):
    def seal_first(self, runner_id: str, token: str) -> SealedValue:
        """``token`` sealed as ``runner_id``'s first row."""
        ...


class IHostRunnerRetirer(Protocol):
    """The runner retire under the host's token alone."""

    def retire_runner(self, runner_id: str, *, force: bool = False) -> RetiredHostRunner | HostRefused:
        """Retire a runner under the host; a refusal while it holds chunks raises
        :class:`~blizzard.runner.hub.client.HostRunnerRetireRefused`."""
        ...


class IHostRunnerHub(IHostRunnerAdder, IHostRunnerRetirer, Protocol):
    """The host's hub calls that add and retire its runners."""
