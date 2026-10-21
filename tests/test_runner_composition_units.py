"""The composition root's small builders: the credential-renewal pass it wires and the held set a workspace's pool
reads host-wide."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.composition import _credential_renewal_pass, _pool_held_ids
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.subscriptions.internal.openai_credential_renewer import OpenAICredentialRenewer
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC, PROVIDER_OPENAI
from tests.support_child_launch import CHILDREN, tool_env

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def test_the_renewal_pass_pairs_each_renewable_subscription_with_its_slug_cadence_renewer_and_clock() -> None:
    clock = FixedClock(_NOW)
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

    built = _credential_renewal_pass(declarations, stores, clock, children=CHILDREN, env=tool_env())  # type: ignore[arg-type]

    assert built is not None
    [renewable] = built._subscriptions
    assert (renewable.slug, renewable.sample_interval_seconds) == ("gpt", 77)
    assert isinstance(renewable.renewer, OpenAICredentialRenewer)
    assert built._renewals is stores.external_usage
    assert built._clock is clock


def test_a_host_whose_providers_bind_no_renewer_builds_no_renewal_pass() -> None:
    stores: Any = SimpleNamespace(external_usage=object())
    declarations = (SubscriptionDeclaration(slug="claude", name="Claude", provider=PROVIDER_ANTHROPIC),)

    built = _credential_renewal_pass(
        declarations,
        stores,
        FixedClock(_NOW),  # type: ignore[arg-type]
        children=CHILDREN,
        env=tool_env(),
    )
    assert built is None


class _Audit:
    """Held bindings of two runners, one environment held by both rows and one free of the other workspace."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def held_workspace_bindings(self, workspace_id: str) -> list[Any]:
        self.asked.append(workspace_id)
        return [
            SimpleNamespace(environment_id="e2"),
            SimpleNamespace(environment_id="e1"),
            SimpleNamespace(environment_id="e2"),
        ]


def test_the_pool_held_set_is_every_runners_held_environment_once_in_the_order_first_held() -> None:
    audit = _Audit()

    held = _pool_held_ids(audit, "ws_a")  # type: ignore[arg-type]

    assert held() == ["e2", "e1"]
    assert audit.asked == ["ws_a"]


def test_the_pool_held_set_is_read_afresh_on_every_call() -> None:
    audit = _Audit()
    held = _pool_held_ids(audit, "ws_a")  # type: ignore[arg-type]

    held()
    held()

    assert audit.asked == ["ws_a", "ws_a"]
