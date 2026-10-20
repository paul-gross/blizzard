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
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.hub.client import (
    IRunnerAdder,
    ITokenIdentityReader,
    TokenIdentity,
    TokenRefusal,
)
from blizzard.runner.hub.identity import judged


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
    #: The held token's runner, ``held``, is not the configured ``runner_id``, and no re-add was allowed.
    ANOTHER_RUNNER = "another_runner"
    #: No token is held for ``runner_id``, the runner configured, and adding another in its place was not allowed.
    TOKENLESS_CONFIGURED = "tokenless_configured"
    #: Adding the runner again would set aside a store the runners ``sharing`` also hold.
    STORE_SHARED = "store_shared"


class BootstrapStopped(Exception):
    """Joining stopped with nothing added; ``stop`` says why. ``runner_id`` names the runner a
    revoked token, a retired runner, or a store belongs to, or the one configured; ``held`` is the held
    token's runner when it belongs to another tenant or another runner than the configured one;
    ``sharing`` names the runners a shared store also holds."""

    def __init__(
        self,
        stop: BootstrapStop,
        *,
        runner_id: str | None = None,
        held: TokenIdentity | None = None,
        sharing: tuple[str, ...] = (),
    ) -> None:
        super().__init__(stop.value)
        self.stop = stop
        self.runner_id = runner_id
        self.held = held
        self.sharing = sharing


class StoreShared(Exception):
    """The store cannot be set aside for one runner: the runners ``sharing`` also hold it, and setting it aside
    would drop their backlog and rewind their sequences below the hub's high-water marks."""

    def __init__(self, sharing: tuple[str, ...]) -> None:
        super().__init__(f"the store is shared with runner {', '.join(sharing)}")
        self.sharing = sharing


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
    answer: TokenIdentity | TokenRefusal,
    *,
    held: HeldToken,
    allow_readd: bool,
    tenant_id: str | None = None,
    configured_id: str | None = None,
) -> TokenIdentity | None:
    """The runner the hub's ``answer`` about the held token keeps, or ``None`` when a runner is to be
    added in its place. Raises :class:`BootstrapStopped` when neither is allowed — among them a
    runner other than ``configured_id`` while no re-add was asked for, since the configured id is the runner's
    and only a re-add replaces it, and then a runner that does not serve ``tenant_id``."""
    if isinstance(answer, TokenIdentity):
        if (
            configured_id is not None
            and not allow_readd
            and judged(configured_id, answer.runner_id) is IdentityState.MISMATCHED
        ):
            raise BootstrapStopped(BootstrapStop.ANOTHER_RUNNER, runner_id=configured_id, held=answer)
        if not answer.serves(tenant_id):
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


def refuse_tokenless(
    held: HeldToken | None, configured_id: str | None, stored: str | None, *, allow_readd: bool
) -> None:
    """Raise :class:`BootstrapStopped` when a runner holding no token would be added in place of one it may not
    replace: the runner ``stored`` its store names, else the ``configured_id`` the host file names. Holding a
    token, or a re-add asked for, stops nothing here."""
    if held is not None or allow_readd:
        return
    if stored is not None:
        raise BootstrapStopped(BootstrapStop.STORE_NAMES_A_RUNNER, runner_id=stored)
    if configured_id is not None:
        raise BootstrapStopped(BootstrapStop.TOKENLESS_CONFIGURED, runner_id=configured_id)


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
        """Move the store aside under a timestamped name and start a fresh one; never deletes. Raises
        :class:`StoreShared` when other runners hold it too."""
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
        configured_id: str | None = None,
    ) -> JoinedRunner:
        """Keep the runner ``held`` names, or add one under ``name`` in its host's tenant, serving
        the projects ``projects`` names, else the tenant's only one — and write its token. A kept
        runner other than ``configured_id``, or whose tenant is not ``tenant_id``, stops; a kept runner keeps the
        declaration the hub holds for it. An unreachable hub, a server error, or a refused add raises
        :class:`~blizzard.runner.hub.client.HubClientError` with nothing added."""
        if held is not None:
            answer = self._identity.identity()
            kept = kept_identity(
                answer, held=held, allow_readd=allow_readd, tenant_id=tenant_id, configured_id=configured_id
            )
            if kept is not None:
                return JoinedRunner(
                    runner_id=kept.runner_id,
                    runner_name=kept.runner_name,
                    added=False,
                    tenant_id=kept.tenant_id,
                    tenant_name=kept.tenant_name,
                )
            self._set_aside()
        else:
            stored = self._store.stored_runner_id()
            refuse_tokenless(held, configured_id, stored, allow_readd=allow_readd)
            if stored is not None:
                self._set_aside()
        issued = self._admin.add_runner(name, projects=projects)
        try:
            self._tokens.write(issued.token)
        except OSError as exc:
            raise TokenNotWritten(issued.runner_id, exc) from exc
        return JoinedRunner(runner_id=issued.runner_id, runner_name=issued.runner_name, added=True, tenant_id=tenant_id)

    def _set_aside(self) -> None:
        try:
            self._store.set_aside()
        except StoreShared as exc:
            raise BootstrapStopped(BootstrapStop.STORE_SHARED, sharing=exc.sharing) from exc
