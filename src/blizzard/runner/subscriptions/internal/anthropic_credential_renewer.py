"""The Anthropic (Claude subscription) credential-renewer binding (``bzh:pluggable-seams``): asks the Claude
Code CLI to refresh its own OAuth token by running a minimal headless haiku one-shot, and reports ``renewed``
only once the credential file's ``claudeAiOauth.expiresAt`` has moved forward. Claude Code refreshes the
token before it sends the request, so the exit code does not matter: a throttled call (429) exits non-zero
after the refresh already landed, and is still ``renewed``. ``--bare`` is never used — it ignores OAuth and
the credential file.

Lead window, measured against Claude Code 2.1.310: its ``isOAuthTokenExpired`` check is
``now + 300000 >= expiresAt`` — a token is refreshed once it is within 5 minutes of expiry (found by reading
the installed bundle, not by forcing a refresh, which would rotate the live credential's refresh token). The
lead sits inside that threshold, so a due check never spends a call the CLI would decline to refresh on."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from blizzard.foundation.clock import IClock
from blizzard.foundation.credential_renewal import RenewalFailureReason
from blizzard.foundation.logging import get_logger
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.subscriptions.credential_renewer import (
    ICredentialRenewer,
    RenewalOutcome,
    RenewalOutcomeKind,
    renewal_due,
)
from blizzard.runner.subscriptions.one_shot_process import IOneShotProcess
from blizzard.runner.subscriptions.subscription_sampler import ANTHROPIC_DEFAULT_CREDENTIALS_PATH

_log = get_logger("blizzard.runner.subscriptions")

DEFAULT_CLAUDE_BINARY = "claude"

# Inside Claude Code's own 5-minute refresh threshold (see the module docstring), and well above the
# sampler's 5s request budget.
_RENEWAL_LEAD_WINDOW = timedelta(minutes=4)

_ONE_SHOT_TIMEOUT_SECONDS = 60.0

# Either would override the credential file the CLI is meant to refresh.
_OVERRIDING_CREDENTIAL_VARS = frozenset({"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"})


class AnthropicCredentialRenewer:
    """Reports a Claude subscription's credential due once it is at or near expiry, and asks the
    ``claude`` CLI to refresh it via a one-turn haiku call with no tools, MCP servers, settings or session."""

    def __init__(
        self,
        *,
        credentials_path: str | None = None,
        claude_binary: str = DEFAULT_CLAUDE_BINARY,
        subprocess: IOneShotProcess,
        env: ChildEnv,
        clock: IClock,
    ) -> None:
        self._credentials_path = credentials_path or ANTHROPIC_DEFAULT_CREDENTIALS_PATH
        self._claude_binary = claude_binary
        self._subprocess = subprocess
        self._env = env
        self._clock: IClock = clock

    def renewal_due(self) -> bool:
        expires_at = self._read_expiry()
        return expires_at is not None and renewal_due(expires_at, self._clock.now(), _RENEWAL_LEAD_WINDOW)

    def renew(self) -> RenewalOutcome:
        expires_at = self._read_expiry()
        config_dir = str(Path(self._credentials_path).parent)
        result = self._subprocess.run(
            [
                self._claude_binary,
                "-p",
                "ok",
                "--model",
                "haiku",
                "--max-turns",
                "1",
                "--tools",
                "",
                "--strict-mcp-config",
                "--setting-sources",
                "",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--output-format",
                "json",
            ],
            stdin="",
            timeout=_ONE_SHOT_TIMEOUT_SECONDS,
            env=self._child_env(config_dir),
        )
        if result.exit_code is None:
            if result.timed_out:
                _log.warning("credential renewal timed out", config_dir=config_dir)
                return RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.TIMED_OUT)
            _log.warning("credential renewal could not launch the vendor CLI", detail=result.stderr)
            return RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.RENEWER_UNAVAILABLE)
        renewed_until = self._read_expiry()
        if renewed_until is not None and (expires_at is None or renewed_until > expires_at):
            return RenewalOutcome(RenewalOutcomeKind.RENEWED)
        _log.warning(
            "credential renewal vendor CLI did not advance the credential's expiry", exit_code=result.exit_code
        )
        return RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.VENDOR_REFUSED)

    def _child_env(self, config_dir: str) -> ChildEnv:
        kept = ChildEnv(tuple((k, v) for k, v in self._env.pairs if k not in _OVERRIDING_CREDENTIAL_VARS))
        # The tool profile carries the PATH a bare binary name resolves on; CLAUDE_CONFIG_DIR and HOME name the
        # credential home the vendor CLI refreshes.
        return kept.adding({"CLAUDE_CONFIG_DIR": config_dir, "HOME": str(Path.home())})

    def _read_expiry(self) -> datetime | None:
        try:
            data = json.loads(Path(self._credentials_path).read_text())
        except (OSError, ValueError):
            return None
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        expires_at = oauth.get("expiresAt") if isinstance(oauth, dict) else None
        if isinstance(expires_at, bool) or not isinstance(expires_at, int | float):
            return None
        try:
            return datetime.fromtimestamp(expires_at / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None


def _conforms_credential_renewer(x: AnthropicCredentialRenewer) -> ICredentialRenewer:
    return x
