"""Selects the renewer binding for one declared provider subscription (blizzard#504).

A ``provider -> binding`` map, confined to ``internal/`` (``bzh:dependency-inversion``),
beside :func:`~blizzard.runner.subscriptions.internal.subscription_sampler_factory.select_sampler`.
Anthropic and any unknown provider select ``None`` — declared, but with no renewal
binding, which keeps today's read-only behaviour exactly (D2)."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.subscriptions.credential_renewer import ICredentialRenewer
from blizzard.runner.subscriptions.internal.openai_credential_renewer import OpenAICredentialRenewer
from blizzard.runner.subscriptions.one_shot_process import IOneShotProcess
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_OPENAI


def select_renewer(
    declaration: SubscriptionDeclaration,
    *,
    clock: IClock,
    subprocess: IOneShotProcess,
) -> ICredentialRenewer | None:
    """The renewer ``declaration.provider`` binds to, or ``None`` for Anthropic or any
    unknown provider."""
    if declaration.provider == PROVIDER_OPENAI:
        return OpenAICredentialRenewer(
            credentials_path=declaration.credentials_path, subprocess=subprocess, clock=clock
        )
    return None
