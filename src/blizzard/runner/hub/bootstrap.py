"""Joining a runner to its hub — the hub step of ``blizzard runner init``.

A held token the hub accepts is kept. A runner is added only when it holds none the hub accepts, and
only when adding cannot undo an operator's act: a **revoked** or **retired** token stops, and an
**unknown** one — also what a runner pointed at the wrong hub sees, where adding would overwrite the
only token it holds for its own — adds it again only when the operator says the hub's data was reset."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.hub.client import IHubRunnerAdmin, ITokenIdentityReader, TokenIdentity, TokenRefusal


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


class BootstrapStopped(Exception):
    """Joining stopped with nothing added; ``stop`` says why, and ``runner_id`` names the runner a
    revoked token or a retired runner belongs to."""

    def __init__(self, stop: BootstrapStop, *, runner_id: str | None = None) -> None:
        super().__init__(stop.value)
        self.stop = stop
        self.runner_id = runner_id


class TokenNotWritten(Exception):
    """The hub added runner ``runner_id``, but its token could not be written — the hub holds the
    runner as never connected until an operator retires it."""

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
    """The runner the joined runtime holds a current token for, and whether joining added it."""

    runner_id: str
    runner_name: str
    added: bool


def kept_identity(answer: TokenIdentity | TokenRefusal, *, held: HeldToken, allow_readd: bool) -> TokenIdentity | None:
    """The runner the hub's ``answer`` about the held token keeps, or ``None`` when a runner is to be
    added in its place. Raises :class:`BootstrapStopped` when neither is allowed."""
    if isinstance(answer, TokenIdentity):
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


class RunnerBootstrap:
    """Joins one runner to its hub: the identity read under the runner's own token, the add under
    the operator's credential, and the new token written straight after the add."""

    def __init__(self, identity: ITokenIdentityReader, admin: IHubRunnerAdmin, tokens: ITokenWriter) -> None:
        self._identity = identity
        self._admin = admin
        self._tokens = tokens

    def join(self, name: str, *, held: HeldToken | None, allow_readd: bool) -> JoinedRunner:
        """Keep the runner ``held`` names, or add one under ``name`` and write its token. An
        unreachable hub, a server error, or a refused add raises
        :class:`~blizzard.runner.hub.client.HubClientError` with nothing added."""
        if held is not None:
            kept = kept_identity(self._identity.identity(), held=held, allow_readd=allow_readd)
            if kept is not None:
                return JoinedRunner(runner_id=kept.runner_id, runner_name=kept.runner_name, added=False)
        issued = self._admin.add_runner(name)
        try:
            self._tokens.write(issued.token)
        except OSError as exc:
            raise TokenNotWritten(issued.runner_id, exc) from exc
        return JoinedRunner(runner_id=issued.runner_id, runner_name=issued.runner_name, added=True)
