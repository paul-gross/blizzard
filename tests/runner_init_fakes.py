"""Init fakes — what ``blizzard runner init`` reaches instead of a live hub: :func:`init_runner` hands a
:class:`FakeInitHub` to the root group as ``obj``, and the autouse :func:`fake_init_hub` supplies one per test."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from click.testing import CliRunner

from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.cli.runtime import HostClients, InitCollaborators
from blizzard.runner.config import ENV_HUB_URL
from blizzard.runner.hub.client import (
    HostFactPushAck,
    HostHeldRunner,
    HostRecord,
    HostRefusalReason,
    HostRefused,
    HostReport,
    HubClientError,
    IHubRunnerAdmin,
    IssuedHost,
    IssuedIdentity,
    ITokenIdentityReader,
    OperatorTenant,
    PushedFact,
    RetiredHostRunner,
    TokenIdentity,
    TokenRefusal,
)

# Nothing listens on the discard port, so a connection to it is refused at once.
_REFUSED_HUB = "http://127.0.0.1:9"


@dataclass
class FakeTokenIdentityReader:
    """Answers every identity read with ``answer``, raising it when it is a :class:`HubClientError`."""

    answer: TokenIdentity | TokenRefusal | HubClientError
    calls: int = 0

    def identity(self) -> TokenIdentity | TokenRefusal:
        self.calls += 1
        if isinstance(self.answer, HubClientError):
            raise self.answer
        return self.answer


@dataclass
class FakeHubRunnerAdmin:
    """Answers every add with ``issued`` — or, unscripted, a fresh ``rn_init<n>`` id and token per
    add — and raises ``refusal`` (a ``RunnerAddRefused`` or ``HubClientError``) instead when set.
    ``added`` holds every identity it handed out, oldest first, ``named`` the tenant each add
    named and ``declared`` the projects it named; ``hosts`` holds every host it added, each a fresh ``hs_init<n>``.
    The tenant listing answers ``tenants``, raising it when it is a ``HubClientError``."""

    issued: IssuedIdentity | None = None
    refusal: HubClientError | None = None
    added: list[IssuedIdentity] = field(default_factory=list)
    named: list[str | None] = field(default_factory=list)
    declared: list[tuple[str, ...] | None] = field(default_factory=list)
    tenants_answer: list[OperatorTenant] | HubClientError = field(default_factory=list)
    hosts: list[IssuedHost] = field(default_factory=list)
    #: Raised instead of adding a host when set.
    host_refusal: HubClientError | None = None
    #: Raised instead of retiring a runner under a host when set.
    retire_refusal: HubClientError | None = None
    host_tenants: dict[str, str] = field(default_factory=dict)
    held: dict[str, list[str]] = field(default_factory=dict)
    adopted: list[tuple[str, ...]] = field(default_factory=list)

    def add_runner(
        self, name: str, *, tenant_id: str | None = None, projects: tuple[str, ...] | None = None
    ) -> IssuedIdentity:
        if self.refusal is not None:
            raise self.refusal
        n = len(self.added) + 1
        issued = self.issued or IssuedIdentity(runner_id=f"rn_init{n}", runner_name=name, token=f"init-token-{n}")
        self.added.append(issued)
        self.named.append(tenant_id)
        self.declared.append(projects)
        return issued

    def tenants(self) -> list[OperatorTenant]:
        if isinstance(self.tenants_answer, HubClientError):
            raise self.tenants_answer
        return self.tenants_answer

    def add_host(self, name: str, *, tenant_id: str | None = None, adopt: tuple[str, ...] = ()) -> IssuedHost:
        if self.host_refusal is not None:
            raise self.host_refusal
        n = len(self.host_tenants) + 1
        host = IssuedHost(host_id=f"hs_init{n}", host_name=name, token=f"init-host-token-{n}")
        self.hosts.append(host)
        self.host_tenants[host.host_id] = tenant_id or _DEFAULT_TENANT
        self.held[host.host_id] = list(adopt)
        self.adopted.append(adopt)
        return host

    def host_of(self, token: str) -> IssuedHost | None:
        """The host ``token`` belongs to, or ``None`` when the hub does not know it."""
        return next((host for host in self.hosts if host.token == token), None)


#: The tenant a host added naming none serves.
_DEFAULT_TENANT = "ten_default"


@dataclass
class FakeHostHubClient:
    """The host plane of the fake hub, under one host token: the record, the runner add and retire, each refused
    as an unknown credential when ``admin`` holds no host with ``token``."""

    admin: FakeHubRunnerAdmin
    token: str
    #: Scripts the record read's answer for any token.
    record_answer: HostRefused | HubClientError | None = None

    def report(self, report: HostReport) -> HostRecord | HostRefused:
        del report
        return self.record()

    def record(self) -> HostRecord | HostRefused:
        host = self.admin.host_of(self.token)
        if isinstance(self.record_answer, HubClientError):
            raise self.record_answer
        if self.record_answer is not None:
            return self.record_answer
        if host is None:
            return _UNKNOWN_HOST
        names = {issued.runner_id: issued.runner_name for issued in self.admin.added}
        return HostRecord(
            host_id=host.host_id,
            tenant_id=self.admin.host_tenants[host.host_id],
            host_name=host.host_name,
            runners=tuple(HostHeldRunner(r, names.get(r, r)) for r in self.admin.held[host.host_id]),
        )

    def push_facts(self, facts: Sequence[PushedFact]) -> HostFactPushAck | HostRefused:
        raise NotImplementedError("init pushes no facts")

    def add_runner(self, name: str, *, projects: tuple[str, ...] | None = None) -> IssuedIdentity | HostRefused:
        host = self.admin.host_of(self.token)
        if host is None:
            return _UNKNOWN_HOST
        issued = self.admin.add_runner(name, tenant_id=self.admin.host_tenants[host.host_id], projects=projects)
        self.admin.held[host.host_id].append(issued.runner_id)
        return issued

    def retire_runner(self, runner_id: str, *, force: bool = False) -> RetiredHostRunner | HostRefused:
        host = self.admin.host_of(self.token)
        if host is None:
            return _UNKNOWN_HOST
        if self.admin.retire_refusal is not None:
            raise self.admin.retire_refusal
        if runner_id not in self.admin.held[host.host_id]:
            return HostRefused(HostRefusalReason.NOT_FOUND, 404, f"runner {runner_id} is not under this host")
        self.admin.held[host.host_id].remove(runner_id)
        return RetiredHostRunner(runner_id=runner_id)


_UNKNOWN_HOST = HostRefused(HostRefusalReason.CREDENTIAL_REFUSED, 401, "unknown host token")


@dataclass(frozen=True)
class InitHubCall:
    """One ``runner init``'s reach for the hub: the URL, the runner token it presents (``""`` for
    none), and the operator session it holds for that hub."""

    hub_url: str
    runner_token: str
    operator_token: str | None


@dataclass
class FakeInitHub:
    """The hub every in-process ``runner init`` joins. Unscripted, it acts like a fresh hub with no
    sign-in: ``admin`` adds every host and runner, and the identity read names the runner a token it issued
    belongs to and refuses any other token as unknown — so a re-run keeps its runner, and clearing
    ``admin.added`` resets the hub's data. ``identity`` scripts the read instead."""

    admin: FakeHubRunnerAdmin = field(default_factory=FakeHubRunnerAdmin)
    identity: FakeTokenIdentityReader | None = None
    calls: list[InitHubCall] = field(default_factory=list)
    #: Scripts every host record read's answer.
    host_record: HostRefused | HubClientError | None = None

    @contextmanager
    def clients(
        self, hub_url: str, runner_token: str, operator_token: str | None
    ) -> Iterator[tuple[ITokenIdentityReader, IHubRunnerAdmin]]:
        self.calls.append(InitHubCall(hub_url, runner_token, operator_token))
        yield self.identity or FakeTokenIdentityReader(self._issued_to(runner_token)), self.admin

    @contextmanager
    def host_clients(self, hub_url: str) -> Iterator[HostClients]:
        del hub_url
        yield lambda token: FakeHostHubClient(self.admin, token, self.host_record)

    def _issued_to(self, token: str) -> TokenIdentity | TokenRefusal:
        for issued in self.admin.added:
            if issued.token == token:
                return TokenIdentity(runner_id=issued.runner_id, runner_name=issued.runner_name)
        return TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN)


class InitRunner(CliRunner):
    """A ``CliRunner`` whose every invocation hands the root group the :class:`FakeInitHub` as ``obj``,
    so an in-process ``runner init`` joins it."""

    def __init__(self, hub: FakeInitHub) -> None:
        super().__init__()
        self.hub = hub

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("obj", InitCollaborators(self.hub.clients, self.hub.host_clients))
        return super().invoke(*args, **kwargs)


def init_runner(hub: FakeInitHub | None = None) -> InitRunner:
    """A runner of in-process commands whose ``runner init`` joins ``hub`` — a fresh one when none is given."""
    return InitRunner(hub or FakeInitHub())


@pytest.fixture(autouse=True)
def fake_init_hub(monkeypatch: pytest.MonkeyPatch) -> FakeInitHub:
    """The hub a test hands its in-process ``runner init`` — through :func:`init_runner` — and the guard
    for one that forgets to: the hub URL environment points at a refused local address, so such an init
    fails fast rather than reaching a developer's live hub."""
    monkeypatch.setenv(ENV_HUB_URL, _REFUSED_HUB)
    return FakeInitHub()
