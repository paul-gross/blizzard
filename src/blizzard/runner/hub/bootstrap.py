"""Joining a runner to its hub — the hub step of ``blizzard runner init``.

A held token the hub accepts is kept. A runner is added only when it holds none the hub accepts, and
only when adding cannot undo an operator's act: a **revoked** or **retired** token stops, and an
**unknown** one — also what a runner pointed at the wrong hub sees, where adding would overwrite the
only token it holds for its own — adds it again only when the operator says the hub's data was reset.

A runner serves the tenant it was added in for life — its host's. A held token whose runner belongs
to another tenant than the one the operator names stops, since keeping it would serve the wrong
tenant and adding would leave a store to a runner it does not belong to. An add is made under the
host's own credential, so it never leaves the tenant unsettled.

A runner's store belongs to the runner that wrote it, so a runner added again never inherits the old
one's: the store is set aside before the add, never after it. A crash between the two leaves the same
unknown token over a fresh store, which the next init adds over again; the other order could leave a
token the hub knows over a store naming another runner, which the runner refuses for good."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.hub.client import (
    IRunnerAdder,
    ITokenIdentityReader,
    TokenIdentity,
    TokenRefusal,
)


class BootstrapStop(StrEnum):
    """Why joining stops with nothing added and the held token left as it was."""

    #: The hub never issued the held token, and adding the runner again was not allowed.
    UNKNOWN_TOKEN = "unknown_token"
    #: The held token was revoked or rotated away; ``runner_id`` names its runner.
    REVOKED_TOKEN = "revoked_token"
    #: The held token's runner is retired; ``runner_id`` names it.
    RETIRED_RUNNER = "retired_runner"
    #: The hub received no token, though one was presented.
    TOKEN_NOT_RECEIVED = "token_not_received"
    #: A replacement token would go unread: the process environment supplies the held one.
    TOKEN_OVERRIDDEN = "token_overridden"
    #: No token is held, but the store belongs to runner ``runner_id``, and adding another was not allowed.
    STORE_NAMES_A_RUNNER = "store_names_a_runner"
    #: The held token's runner, ``held``, belongs to another tenant than the one named.
    ANOTHER_TENANT = "another_tenant"


class BootstrapStopped(Exception):
    """Joining stopped with nothing added; ``stop`` says why. ``runner_id`` names the runner a
    revoked token, a retired runner, or a store belongs to; ``held`` is the held token's runner
    when it belongs to another tenant."""

    def __init__(
        self,
        stop: BootstrapStop,
        *,
        runner_id: str | None = None,
        held: TokenIdentity | None = None,
    ) -> None:
        super().__init__(stop.value)
        self.stop = stop
        self.runner_id = runner_id
        self.held = held


class TokenNotWritten(Exception):
    """The hub added runner ``runner_id``, but its token could not be written."""

    def __init__(self, runner_id: str, cause: OSError) -> None:
        super().__init__(f"runner {runner_id} was added, but its token was not written: {cause}")
        self.runner_id = runner_id
        self.cause = cause


@domain_model
@dataclass(frozen=True)
class HeldToken:
    """The bearer token the runner holds, and whether the process environment supplies it — then it
    overrides the token file, and a token written there would go unread."""

    token: str
    from_process_env: bool


@domain_model
@dataclass(frozen=True)
class JoinedRunner:
    """The runner the joined runtime holds a current token for, whether joining added it, and its
    tenant as far as joining learned it: a kept runner's from the hub, an added one's as named."""

    runner_id: str
    runner_name: str
    added: bool
    tenant_id: str | None = None
    tenant_name: str | None = None


def kept_identity(
    answer: TokenIdentity | TokenRefusal, *, held: HeldToken, allow_readd: bool, tenant_id: str | None = None
) -> TokenIdentity | None:
    """The runner the hub's ``answer`` about the held token keeps, or ``None`` when a runner is to be
    added in its place. Raises :class:`BootstrapStopped` when neither is allowed — among them a
    runner in another tenant than ``tenant_id`` names. A hub that names no tenant settles none."""
    if isinstance(answer, TokenIdentity):
        if tenant_id is not None and answer.tenant_id is not None and answer.tenant_id != tenant_id:
            raise BootstrapStopped(BootstrapStop.ANOTHER_TENANT, runner_id=answer.runner_id, held=answer)
        return answer
    if answer.reason is RunnerTokenRefusalReason.UNKNOWN:
        if not allow_readd:
            raise BootstrapStopped(BootstrapStop.UNKNOWN_TOKEN)
        if held.from_process_env:
            raise BootstrapStopped(BootstrapStop.TOKEN_OVERRIDDEN)
        return None
    if answer.reason is RunnerTokenRefusalReason.REVOKED:
        raise BootstrapStopped(BootstrapStop.REVOKED_TOKEN, runner_id=answer.runner_id)
    if answer.reason is RunnerTokenRefusalReason.RETIRED:
        raise BootstrapStopped(BootstrapStop.RETIRED_RUNNER, runner_id=answer.runner_id)
    raise BootstrapStopped(BootstrapStop.TOKEN_NOT_RECEIVED)


class ITokenWriter(Protocol):
    """Where an added runner's token is kept for the runner to present."""

    def write(self, token: str) -> None:
        """Replace the held token. Raises ``OSError`` when it cannot."""
        ...


class IRunnerStoreAside(Protocol):
    """The runner's local store, as joining sees it: whose it is, and moving it aside."""

    def stored_runner_id(self) -> str | None:
        """The id of the runner the store's identity row names, or ``None`` when it names none."""
        ...

    def set_aside(self) -> None:
        """Move the store aside under a timestamped name and start a fresh one; never deletes."""
        ...


class RunnerBootstrap:
    """Joins one runner to its hub: the identity read under the runner's own token, the store set
    aside when a runner is added in place of the one it belonged to, the add under the host's
    credential, and the new token written straight after the add."""

    def __init__(
        self, identity: ITokenIdentityReader, admin: IRunnerAdder, tokens: ITokenWriter, store: IRunnerStoreAside
    ) -> None:
        self._identity = identity
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
        projects: tuple[str, ...] | None = None,
    ) -> JoinedRunner:
        """Keep the runner ``held`` names, or add one under ``name`` in its host's tenant, serving
        the projects ``projects`` names, else the tenant's only one — and write its token. A kept
        runner whose tenant is not ``tenant_id`` stops; a kept runner keeps the declaration the hub
        holds for it. An unreachable hub, a server error, or a refused add raises
        :class:`~blizzard.runner.hub.client.HubClientError` with nothing added."""
        if held is not None:
            answer = self._identity.identity()
            kept = kept_identity(answer, held=held, allow_readd=allow_readd, tenant_id=tenant_id)
            if kept is not None:
                return JoinedRunner(
                    runner_id=kept.runner_id,
                    runner_name=kept.runner_name,
                    added=False,
                    tenant_id=kept.tenant_id,
                    tenant_name=kept.tenant_name,
                )
            self._store.set_aside()
        elif (stored := self._store.stored_runner_id()) is not None:
            if not allow_readd:
                raise BootstrapStopped(BootstrapStop.STORE_NAMES_A_RUNNER, runner_id=stored)
            self._store.set_aside()
        issued = self._admin.add_runner(name, projects=projects)
        try:
            self._tokens.write(issued.token)
        except OSError as exc:
            raise TokenNotWritten(issued.runner_id, exc) from exc
        return JoinedRunner(runner_id=issued.runner_id, runner_name=issued.runner_name, added=True, tenant_id=tenant_id)
