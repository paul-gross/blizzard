"""Selects the renewer binding for one declared provider subscription.

A ``provider -> binding`` map, confined to ``internal/`` (``bzh:dependency-inversion``),
beside :func:`~blizzard.runner.subscriptions.internal.subscription_sampler_factory.select_sampler`.
An unknown provider selects ``None`` — declared, but with no renewal
binding, which keeps today's read-only behaviour exactly."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.subscriptions.credential_renewer import ICredentialRenewer
from blizzard.runner.subscriptions.internal.anthropic_credential_renewer import AnthropicCredentialRenewer
from blizzard.runner.subscriptions.internal.openai_credential_renewer import OpenAICredentialRenewer
from blizzard.runner.subscriptions.one_shot_process import IOneShotProcess
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC, PROVIDER_OPENAI, SubscriptionSource


def select_renewer(
    declaration: SubscriptionSource,
    *,
    clock: IClock,
    subprocess: IOneShotProcess,
    env: ChildEnv,
) -> ICredentialRenewer | None:
    """The renewer ``declaration.provider`` binds to, launching under the tool profile ``env``, or ``None`` for
    an unknown provider."""
    if declaration.provider == PROVIDER_OPENAI:
        return OpenAICredentialRenewer(
            credentials_path=declaration.credentials_path, subprocess=subprocess, env=env, clock=clock
        )
    if declaration.provider == PROVIDER_ANTHROPIC:
        return AnthropicCredentialRenewer(
            credentials_path=declaration.credentials_path, subprocess=subprocess, env=env, clock=clock
        )
    return None
