"""The Claude Code health-probe binding (blizzard#438).

Standalone, mirroring :class:`~blizzard.runner.harness.internal.opencode_health.
OpenCodeHealthProbe`: a narrow, separately-injected collaborator rather than a slice folded
onto :class:`~blizzard.runner.harness.internal.claude_code_adapter.ClaudeCodeAdapter` itself."""

from __future__ import annotations

import json
import re
from pathlib import Path

from packaging.specifiers import SpecifierSet

from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.health import DeclaredDegradation
from blizzard.runner.harness.internal import harness_shared
from blizzard.runner.subscriptions.internal.anthropic_subscription_sampler import DEFAULT_CREDENTIALS_PATH

# Currently >=2.1,<3.0, corpus-free (D1, blizzard#606): membership via `version_admitted` admits it alone.
ADMITTED_CLAUDE_CODE_RANGE_DISPLAY = ">=2.1,<3.0"
ADMITTED_CLAUDE_CODE_RANGE: SpecifierSet = SpecifierSet(ADMITTED_CLAUDE_CODE_RANGE_DISPLAY)

# Strips Claude Code's `--version` prefix/suffix off one line, keeping any pre-release suffix (blizzard#606).
_CLAUDE_CODE_VERSION_PATTERN = re.compile(
    r"^\s*(?:claude(?:\s+code)?(?:\s+version)?\s+)?(?:v)?"
    r"(?P<version>\d+\.\d+\.\d+(?:(?:-[0-9A-Za-z][0-9A-Za-z.-]*)|(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)|(?:\.[0-9A-Za-z][0-9A-Za-z.-]*))?)"
    r"\s*(?:\(claude\s+code\))?\s*$",
    re.IGNORECASE,
)


def normalize_claude_code_version(raw: str | None) -> str | None:
    """The bare semantic version in one raw Claude Code ``--version`` output, or ``None`` when
    it isn't exactly one matching line. Accepts the real shape (``X.Y.Z (Claude Code)``) and a
    bare ``X.Y.Z`` alike, keeping any semver pre-release suffix so a pre-release reaches
    ``version_admitted`` and reads ``INCOMPATIBLE_VERSION``, never ``UNKNOWN_VERSION``."""
    if raw is None:
        return None
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    match = _CLAUDE_CODE_VERSION_PATTERN.fullmatch(lines[0])
    return match.group("version") if match else None


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

    def supported_version(self) -> SpecifierSet:
        return ADMITTED_CLAUDE_CODE_RANGE

    def supported_version_display(self) -> str:
        return ADMITTED_CLAUDE_CODE_RANGE_DISPLAY

    def normalize_version(self, raw: str | None) -> str | None:
        return normalize_claude_code_version(raw)

    def classifies_offline(self) -> bool:
        # Claude Code declares no committed compatibility corpus (D1) — range membership
        # alone admits it, so no observed version is ever run through `classify_offline`.
        return False

    def declared_degradations(self) -> tuple[DeclaredDegradation, ...]:
        return ()


def _conforms_harness_health_probe(x: ClaudeCodeHealthProbe) -> IHarnessHealthProbe:
    return x


__all__ = [
    "ADMITTED_CLAUDE_CODE_RANGE",
    "ADMITTED_CLAUDE_CODE_RANGE_DISPLAY",
    "ClaudeCodeHealthProbe",
    "normalize_claude_code_version",
]
