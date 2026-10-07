"""Init fakes — what ``blizzard runner init`` reaches instead of a live hub: :func:`init_runner` hands a
:class:`FakeInitHub` to the root group as ``obj``, and the autouse :func:`fake_init_hub` supplies one per test."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from click.testing import CliRunner

from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.cli.runtime import InitCollaborators
from blizzard.runner.config import DEFAULT_TOKEN_ENV, ENV_HUB_URL
from blizzard.runner.hub.client import (
    HubClientError,
    IHubRunnerAdmin,
    IssuedIdentity,
    ITokenIdentityReader,
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


class InitRunner(CliRunner):
    """A ``CliRunner`` whose every invocation hands the root group the :class:`FakeInitHub` as ``obj``,
    so an in-process ``runner init`` joins it."""

    def __init__(self, hub: FakeInitHub) -> None:
        super().__init__()
        self.hub = hub

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("obj", InitCollaborators(self.hub.clients))
        return super().invoke(*args, **kwargs)


def init_runner(hub: FakeInitHub | None = None) -> InitRunner:
    """A runner of in-process commands whose ``runner init`` joins ``hub`` — a fresh one when none is given."""
    return InitRunner(hub or FakeInitHub())


@pytest.fixture(autouse=True)
def fake_init_hub(monkeypatch: pytest.MonkeyPatch) -> FakeInitHub:
    """The hub a test hands its in-process ``runner init`` — through :func:`init_runner` — and the guard
    for one that forgets to: the hub URL environment points at a refused local address, so such an init
    fails fast rather than reaching a developer's live hub. A hub token the developer's shell carries is stripped."""
    monkeypatch.delenv(DEFAULT_TOKEN_ENV, raising=False)
    monkeypatch.setenv(ENV_HUB_URL, _REFUSED_HUB)
    return FakeInitHub()
