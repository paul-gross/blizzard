"""The composition root's small builders: the credential-renewal pass it wires and the held set a workspace's pool
reads host-wide."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.runner.composition import credential_renewal_pass, pool_held_ids
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.subscriptions.internal.anthropic_credential_renewer import AnthropicCredentialRenewer
from blizzard.runner.subscriptions.internal.openai_credential_renewer import OpenAICredentialRenewer
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC, PROVIDER_OPENAI
from tests.support_child_launch import CHILDREN, tool_env

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def test_the_renewal_pass_pairs_each_renewable_subscription_with_its_slug_cadence_renewer_and_clock() -> None:
    clock = SystemClock()
    stores: Any = SimpleNamespace(external_usage=object())
    declarations = (
        SubscriptionDeclaration(slug="claude", name="Claude", provider=PROVIDER_ANTHROPIC),
        SubscriptionDeclaration(
            slug="gpt",
            name="GPT",
            provider=PROVIDER_OPENAI,
            credentials_path="/creds/auth.json",
            sample_interval_seconds=77,
        ),
    )

    built = credential_renewal_pass(declarations, stores, clock, children=CHILDREN, env=tool_env())

    assert built is not None
    claude, gpt = built.subscriptions
    assert isinstance(claude.renewer, AnthropicCredentialRenewer)
    assert (gpt.slug, gpt.sample_interval_seconds) == ("gpt", 77)
    assert isinstance(gpt.renewer, OpenAICredentialRenewer)
    assert built.renewals is stores.external_usage
    assert built.clock is clock


def test_a_host_whose_providers_bind_no_renewer_builds_no_renewal_pass() -> None:
    stores: Any = SimpleNamespace(external_usage=object())
    declarations = (SubscriptionDeclaration(slug="mystery", name="Mystery", provider="some-unshipped-provider"),)

    built = credential_renewal_pass(
        declarations,
        stores,
        SystemClock(),
        children=CHILDREN,
        env=tool_env(),
    )
    assert built is None


class _Audit:
    """Held bindings of two runners, one environment held by both rows and one free of the other workspace."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def connect(self) -> AbstractContextManager[Any]:
        raise NotImplementedError

    def read_transaction(self) -> AbstractContextManager[Any]:
        raise NotImplementedError

    def named_runners(self) -> tuple[str, ...]:
        raise NotImplementedError

    def held_workspace_bindings(self, workspace_id: str) -> list[EnvBinding]:
        self.asked.append(workspace_id)
        return [_binding("e2"), _binding("e1"), _binding("e2")]


def _binding(environment_id: str) -> EnvBinding:
    return EnvBinding(chunk_id="c", environment_id=environment_id, workdir="/w", bound_at=_NOW)


def test_the_pool_held_set_is_every_runners_held_environment_once_in_the_order_first_held() -> None:
    audit = _Audit()

    held = pool_held_ids(audit, "ws_a")

    assert held() == ["e2", "e1"]
    assert audit.asked == ["ws_a"]


def test_the_pool_held_set_is_read_afresh_on_every_call() -> None:
    audit = _Audit()
    held = pool_held_ids(audit, "ws_a")

    held()
    held()

    assert audit.asked == ["ws_a", "ws_a"]
