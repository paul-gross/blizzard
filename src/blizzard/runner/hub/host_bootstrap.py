"""Registering a host at its hub — the host step of ``blizzard runner init``, and the adapter every runner
init adds goes through.

A held host token the hub knows is kept. Otherwise a host is added under the operator's credential and
its token written straight after the add. A held token the hub does not know is replaced only when the
operator says the hub's data was reset, and never when the process environment supplies it, since a
replacement written to ``.env`` would go unread. A host serves the tenant it was added in for life, so a
held host in another tenant than the operator names stops.

A host added again never inherits the store the old one wrote: the store is set aside before the add,
and every runner is added afresh under the new host."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.runner.hub.bootstrap import HeldToken, IRunnerStoreAside, ITokenWriter
from blizzard.runner.hub.client import (
    HostAddRefusalReason,
    HostAddRefused,
    HostRecord,
    HostRefusalReason,
    HostRefused,
    HubClientError,
    IHostHubClient,
    IHubRunnerAdmin,
    IRunnerAdder,
    IssuedHost,
    IssuedIdentity,
    OperatorTenant,
    RunnerAddRefusalReason,
    RunnerAddRefused,
)

#: The status the hub refuses a retired host's token with; any other credential refusal is a token it does not know.
_RETIRED_HOST_STATUS = 403


class HostBootstrapStop(StrEnum):
    """Why registering stops with no host added and the held host token left as it was."""

    #: The hub does not know the held host token, and adding the host again was not allowed.
    UNKNOWN_HOST_TOKEN = "unknown_host_token"
    #: The hub does not know the held host token, and a replacement would go unread: something overrides ``.env``.
    HOST_TOKEN_OVERRIDDEN = "host_token_overridden"
    #: The hub refuses the held host token as a retired host's.
    HOST_RETIRED = "host_retired"
    #: The hub answers the host plane not-found: it predates hosts.
    HUB_WITHOUT_HOSTS = "hub_without_hosts"
    #: The held host token's host, ``held``, belongs to another tenant than the one named.
    ANOTHER_TENANT = "another_tenant"
    #: No tenant was named, and the operator may enter several; ``tenants`` lists them.
    TENANT_REQUIRED = "tenant_required"
    #: The named tenant is none the operator may enter; ``tenants`` lists the ones they may.
    NO_SUCH_TENANT = "no_such_tenant"


_TENANT_STOPS = {
    HostAddRefusalReason.TENANT_REQUIRED: HostBootstrapStop.TENANT_REQUIRED,
    HostAddRefusalReason.NO_SUCH_TENANT: HostBootstrapStop.NO_SUCH_TENANT,
}


class HostBootstrapStopped(Exception):
    """Registering stopped with no host added; ``stop`` says why. ``held`` is the held token's host when
    it belongs to another tenant; ``tenants`` lists the tenants the operator may enter when the tenant is
    unsettled — empty when the hub would not list them."""

    def __init__(
        self, stop: HostBootstrapStop, *, held: HostRecord | None = None, tenants: tuple[OperatorTenant, ...] = ()
    ) -> None:
        super().__init__(stop.value)
        self.stop = stop
        self.held = held
        self.tenants = tenants


class HostTokenNotWritten(Exception):
    """The hub added host ``host_id``, but its token could not be written."""

    def __init__(self, host_id: str, cause: OSError) -> None:
        super().__init__(f"host {host_id} was added, but its token was not written: {cause}")
        self.host_id = host_id
        self.cause = cause


@domain_model
@dataclass(frozen=True)
class JoinedHost:
    """The host the init holds a current token for: that token, the hub's record of it, whether it was
    added, and whether it was added in place of a host the hub no longer knows."""

    token: str
    record: HostRecord
    added: bool
    readded: bool = False

    def holds(self, runner_id: str) -> bool:
        """Whether the hub holds ``runner_id`` under this host."""
        return any(runner.runner_id == runner_id for runner in self.record.runners)


class HostBootstrap:
    """Registers one host at its hub: the record read under the held host token, the store set aside when
    a host is added in place of one the hub forgot, the add under the operator's credential, and the new
    token written straight after the add."""

    def __init__(
        self,
        host_client: Callable[[str], IHostHubClient],
        admin: IHubRunnerAdmin,
        tokens: ITokenWriter,
        store: IRunnerStoreAside,
    ) -> None:
        self._host_client = host_client
        self._admin = admin
        self._tokens = tokens
        self._store = store

    def join(
        self,
        name: str,
        *,
        held: HeldToken | None,
        allow_readd: bool,
        tenant_id: str | None = None,
        adopt: tuple[str, ...] = (),
    ) -> JoinedHost:
        """Keep the host ``held`` names, or add one under ``name`` — in the tenant ``tenant_id`` names,
        else the one the hub resolves for the operator — adopting the runners ``adopt`` names, and write
        its token. A host added in place of a forgotten one adopts none. An unreachable hub, a server
        error, or a refused add raises :class:`~blizzard.runner.hub.client.HubClientError` with nothing
        added."""
        readded = False
        if held is not None:
            answer = self._host_client(held.token).record()
            if isinstance(answer, HostRecord):
                if tenant_id is not None and answer.tenant_id != tenant_id:
                    raise HostBootstrapStopped(HostBootstrapStop.ANOTHER_TENANT, held=answer)
                return JoinedHost(token=held.token, record=answer, added=False)
            self._refuse_unless_readded(answer, held=held, allow_readd=allow_readd)
            self._store.set_aside()
            readded, adopt = True, ()
        issued = self._add(name, tenant_id, adopt)
        try:
            self._tokens.write(issued.token)
        except OSError as exc:
            raise HostTokenNotWritten(issued.host_id, exc) from exc
        record = self._host_client(issued.token).record()
        if isinstance(record, HostRefused):
            raise HubClientError(f"the hub refused host {issued.host_id}'s new token: {record.detail}")
        return JoinedHost(token=issued.token, record=record, added=True, readded=readded)

    @staticmethod
    def _refuse_unless_readded(answer: HostRefused, *, held: HeldToken, allow_readd: bool) -> None:
        """Stop unless the refusal is of a token the hub does not know, a re-add was allowed, and the
        token can be replaced where the host reads it."""
        if answer.reason is HostRefusalReason.NOT_FOUND:
            raise HostBootstrapStopped(HostBootstrapStop.HUB_WITHOUT_HOSTS)
        if answer.status == _RETIRED_HOST_STATUS:
            raise HostBootstrapStopped(HostBootstrapStop.HOST_RETIRED)
        if held.from_process_env:
            raise HostBootstrapStopped(HostBootstrapStop.HOST_TOKEN_OVERRIDDEN)
        if not allow_readd:
            raise HostBootstrapStopped(HostBootstrapStop.UNKNOWN_HOST_TOKEN)

    def _add(self, name: str, tenant_id: str | None, adopt: tuple[str, ...]) -> IssuedHost:
        try:
            return self._admin.add_host(name, tenant_id=tenant_id, adopt=adopt)
        except HostAddRefused as exc:
            stop = _TENANT_STOPS.get(exc.reason)
            if stop is None:
                raise
            raise HostBootstrapStopped(stop, tenants=self._enterable()) from exc

    def _enterable(self) -> tuple[OperatorTenant, ...]:
        try:
            return tuple(self._admin.tenants())
        except HubClientError:
            return ()


class HostRunnerAdmin:
    """The runner add taken under the host's own credential: a runner added through it stands under the
    host, in the host's tenant."""

    def __init__(self, host: IHostHubClient) -> None:
        self._host = host

    def add_runner(self, name: str, *, projects: tuple[str, ...] | None = None) -> IssuedIdentity:
        """Add the runner under the host."""
        answer = self._host.add_runner(name, projects=projects)
        if isinstance(answer, IssuedIdentity):
            return answer
        if answer.reason is HostRefusalReason.NOT_FOUND:
            raise RunnerAddRefused(
                f"POST /api/fleet/host/runners -> {answer.status}",
                reason=RunnerAddRefusalReason.UNSUPPORTED,
                detail=answer.detail,
            )
        raise HubClientError(f"the hub refused the host's credential adding runner {name}: {answer.detail}")


def _conforms_runner_adder(x: HostRunnerAdmin) -> IRunnerAdder:
    return x
