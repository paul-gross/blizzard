"""Init fakes — what ``blizzard runner init`` reaches instead of a live hub.

:class:`FakeInitHub` stands in at the init CLI's ``hub_clients`` seam. The autouse
:func:`fake_init_hub` installs one for every test, so an in-process ``runner init`` joins it,
never a real hub — and any real hub connection the init module attempts fails the test."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest

from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.cli import runtime as runner_cli_runtime
from blizzard.runner.config import DEFAULT_TOKEN_ENV
from blizzard.runner.hub.client import (
    HubClientError,
    IHubRunnerAdmin,
    IssuedIdentity,
    ITokenIdentityReader,
    TokenIdentity,
    TokenRefusal,
)


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
    ``added`` holds every identity it handed out, oldest first."""

    issued: IssuedIdentity | None = None
    refusal: HubClientError | None = None
    added: list[IssuedIdentity] = field(default_factory=list)

    def add_runner(self, name: str) -> IssuedIdentity:
        if self.refusal is not None:
            raise self.refusal
        n = len(self.added) + 1
        issued = self.issued or IssuedIdentity(runner_id=f"rn_init{n}", runner_name=name, token=f"init-token-{n}")
        self.added.append(issued)
        return issued


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
    sign-in: ``admin`` adds every runner, and the identity read names the runner a token it issued
    belongs to and refuses any other token as unknown — so a re-run keeps its runner, and clearing
    ``admin.added`` resets the hub's data. ``identity`` scripts the read instead."""

    admin: FakeHubRunnerAdmin = field(default_factory=FakeHubRunnerAdmin)
    identity: FakeTokenIdentityReader | None = None
    calls: list[InitHubCall] = field(default_factory=list)

    @contextmanager
    def clients(
        self, hub_url: str, runner_token: str, operator_token: str | None
    ) -> Iterator[tuple[ITokenIdentityReader, IHubRunnerAdmin]]:
        self.calls.append(InitHubCall(hub_url, runner_token, operator_token))
        yield self.identity or FakeTokenIdentityReader(self._issued_to(runner_token)), self.admin

    def _issued_to(self, token: str) -> TokenIdentity | TokenRefusal:
        for issued in self.admin.added:
            if issued.token == token:
                return TokenIdentity(runner_id=issued.runner_id, runner_name=issued.runner_name)
        return TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN)


class _NoHubConnections:
    """Stands in for ``httpx`` inside the init CLI module, so a real hub connection fails the test."""

    def Client(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("runner init reached for a real hub; script the `fake_init_hub` fixture instead")


@pytest.fixture(autouse=True)
def fake_init_hub(monkeypatch: pytest.MonkeyPatch) -> FakeInitHub:
    """Join every in-process ``runner init`` to a fresh :class:`FakeInitHub`, and strip a hub token
    the developer's shell may carry, so no init inherits one."""
    monkeypatch.delenv(DEFAULT_TOKEN_ENV, raising=False)
    hub = FakeInitHub()
    monkeypatch.setattr(runner_cli_runtime, "hub_clients", hub.clients)
    monkeypatch.setattr(runner_cli_runtime, "httpx", _NoHubConnections())
    return hub
