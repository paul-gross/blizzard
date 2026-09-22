"""The Claude Code health-probe binding (blizzard#438).

Standalone, mirroring :class:`~blizzard.runner.harness.internal.opencode_health.
OpenCodeHealthProbe`: a narrow, separately-injected collaborator rather than a slice folded
onto :class:`~blizzard.runner.harness.internal.claude_code_adapter.ClaudeCodeAdapter` itself."""

from __future__ import annotations

import json
from pathlib import Path

from packaging.specifiers import SpecifierSet

from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.health import DeclaredDegradation
from blizzard.runner.harness.internal import harness_shared
from blizzard.runner.subscriptions.internal.anthropic_subscription_sampler import DEFAULT_CREDENTIALS_PATH


class ClaudeCodeHealthProbe:
    """The Claude Code binding's :class:`~blizzard.runner.harness.adapter.
    IHarnessHealthProbe`. Dumb, like the adapter it stands beside: reports evidence,
    never decides availability."""

    def __init__(self, binary: str = "claude", *, credentials_path: str | None = None) -> None:
        self._binary = binary
        # Injectable for testability (`bzh:dependency-injection`); defaults to the same
        # OAuth credential file `AnthropicSubscriptionSampler` already reads for usage sampling.
        self._credentials_path = Path(credentials_path or DEFAULT_CREDENTIALS_PATH)

    def binary_present(self) -> bool:
        return harness_shared.binary_present(self._binary)

    def probe_authentication(self) -> bool:
        """Claude Code exposes no ``--version``-adjacent auth-status flag either, so this
        checks the same OAuth credential file ``AnthropicSubscriptionSampler`` already reads
        for usage sampling — a token recorded there is the account's own authentication
        signal, independent of this probe's binary responding. Expiry is not checked: an
        expired-but-present token still reads as authenticated."""
        if harness_shared.observe_version(self._binary) is None:
            return False
        return self._has_access_token()

    def _has_access_token(self) -> bool:
        try:
            raw = self._credentials_path.read_text()
        except OSError:
            return False
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        if not isinstance(oauth, dict):
            return False
        access_token = oauth.get("accessToken")
        return isinstance(access_token, str) and bool(access_token)

    def supported_version(self) -> SpecifierSet | None:
        # Claude Code declares no supported-version range (blizzard#438's plan); see
        # `IHarnessHealthProbe.supported_version` for why that's `None`, not an empty set.
        return None

    def supported_version_display(self) -> str | None:
        return None

    def declared_degradations(self) -> tuple[DeclaredDegradation, ...]:
        return ()


def _conforms_harness_health_probe(x: ClaudeCodeHealthProbe) -> IHarnessHealthProbe:
    return x


__all__ = ["ClaudeCodeHealthProbe"]
