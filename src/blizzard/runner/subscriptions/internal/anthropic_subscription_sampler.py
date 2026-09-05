"""The Anthropic (Claude Code) subscription-sampler binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.subscriptions.subscription_sampler.ISubscriptionSampler`
against Claude Code's own OAuth usage endpoint, independent of the coding-harness adapter
(blizzard#436)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.subscriptions.subscription_sampler import (
    ExternalSubscriptionUsageSnapshot,
    ExternalSubscriptionUsageWindow,
    ISubscriptionSampler,
)
from blizzard.wire.facts import PROVIDER_ANTHROPIC as PROVIDER_ANTHROPIC

_log = get_logger("blizzard.runner.harness")

# The subscription-usage seam (issue #218) — the API host and the shared credential file
# the harness's own login writes. Both overridable via the constructor.
DEFAULT_USAGE_API_BASE = "https://api.anthropic.com"
DEFAULT_CREDENTIALS_PATH = str(Path.home() / ".claude" / ".credentials.json")

_USAGE_PATH = "/api/oauth/usage"
_USAGE_OAUTH_BETA_HEADER = "oauth-2025-04-20"
_USAGE_TIMEOUT_SECONDS = 5.0

# The label and fixed length each source-body key maps to (issue #218), so no caller has
# to hardcode the window -> seconds mapping itself.
_USAGE_WINDOW_SPECS: tuple[tuple[str, str, int], ...] = (
    ("five_hour", "5h", 18_000),
    ("seven_day", "7d", 604_800),
)


class AnthropicSubscriptionSampler:
    """Samples a Claude Code OAuth subscription's rate-limit utilization. Never raises."""

    def __init__(
        self,
        *,
        credentials_path: str | None = None,
        usage_api_base: str = DEFAULT_USAGE_API_BASE,
        http_client: httpx.Client | None = None,
        clock: IClock | None = None,
    ) -> None:
        # `credentials_path` is read-only here, and the client is constructed lazily.
        self._credentials_path = credentials_path or DEFAULT_CREDENTIALS_PATH
        self._usage_api_base = usage_api_base
        self._http_client = http_client
        self._clock: IClock = clock or SystemClock()

    def sample(self) -> ExternalSubscriptionUsageSnapshot | None:
        access_token = self._read_access_token()
        if access_token is None:
            return None
        try:
            resp = self._usage_client().get(
                f"{self._usage_api_base}{_USAGE_PATH}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "anthropic-beta": _USAGE_OAUTH_BETA_HEADER,
                    "Content-Type": "application/json",
                },
                timeout=_USAGE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            # Covers both a timeout and a connection failure: a best-effort diagnostic
            # sample, never a spawn/resume failure.
            _log.warning("external subscription usage sample failed: request error", detail=str(exc))
            return None
        if not resp.is_success:
            _log.warning("external subscription usage sample failed: non-2xx response", status_code=resp.status_code)
            return None
        try:
            body = resp.json()
        except ValueError as exc:
            _log.warning("external subscription usage sample failed: unparseable response body", detail=str(exc))
            return None
        if not isinstance(body, dict):
            _log.warning(
                "external subscription usage sample failed: unexpected response shape",
                body_type=type(body).__name__,
            )
            return None
        windows = self._parse_usage_windows(body)
        if not windows:
            _log.warning("external subscription usage sample failed: no parseable windows in response")
            return None
        return ExternalSubscriptionUsageSnapshot(sampled_at=self._clock.now(), windows=tuple(windows))

    def _usage_client(self) -> httpx.Client:
        """The injected ``httpx.Client``, or a lazily-constructed real one.

        Lazy, so a sampler that never samples opens no connection pool, and cached once
        created, so repeated samples reuse one connection."""
        if self._http_client is None:
            self._http_client = httpx.Client()
        return self._http_client

    def _read_access_token(self) -> str | None:
        """The OAuth bearer token from the credential file, or ``None`` on any failure.

        Read-only, always: the harness owns the refresh flow and the file is shared by
        every worker this runner spawns, so a second writer risks corrupting it mid-refresh.
        An expired token is another ``None`` path, never a refresh trigger."""
        try:
            raw = Path(self._credentials_path).read_text()
        except OSError as exc:
            _log.warning(
                "external subscription usage sample failed: could not read credentials file",
                path=self._credentials_path,
                detail=str(exc),
            )
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            _log.warning(
                "external subscription usage sample failed: malformed credentials JSON",
                path=self._credentials_path,
                detail=str(exc),
            )
            return None
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        if not isinstance(oauth, dict):
            _log.warning(
                "external subscription usage sample failed: no claudeAiOauth block in credentials",
                path=self._credentials_path,
            )
            return None
        access_token = oauth.get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            _log.warning(
                "external subscription usage sample failed: no access token in credentials",
                path=self._credentials_path,
            )
            return None
        expires_at = self._parse_epoch_millis(oauth.get("expiresAt"))
        if expires_at is None:
            _log.warning(
                "external subscription usage sample failed: missing/unparseable token expiry",
                path=self._credentials_path,
            )
            return None
        if expires_at <= self._clock.now():
            _log.warning(
                "external subscription usage sample failed: access token expired",
                path=self._credentials_path,
                expires_at=iso_utc(expires_at),
            )
            return None
        return access_token

    def _parse_usage_windows(self, body: dict[str, object]) -> list[ExternalSubscriptionUsageWindow]:
        """Every window ``body`` reports usable data for. A window whose key is absent, or
        whose ``utilization``/``resets_at`` is null or unparseable, is skipped rather than
        fabricated as a zero entry."""
        windows: list[ExternalSubscriptionUsageWindow] = []
        for key, label, seconds in _USAGE_WINDOW_SPECS:
            entry = body.get(key)
            if not isinstance(entry, dict):
                continue
            utilization = entry.get("utilization")
            resets_at_raw = entry.get("resets_at")
            if utilization is None or resets_at_raw is None or not isinstance(utilization, int | float):
                continue
            resets_at = self._parse_resets_at(resets_at_raw)
            if resets_at is None:
                continue
            windows.append(
                ExternalSubscriptionUsageWindow(
                    window=label, utilization_pct=float(utilization), resets_at=resets_at, window_seconds=seconds
                )
            )
        return windows

    @staticmethod
    def _parse_epoch_millis(value: object) -> datetime | None:
        """Claude Code's own credential file stamps ``expiresAt`` in epoch milliseconds."""
        if not isinstance(value, int | float):
            return None
        return datetime.fromtimestamp(value / 1000, tz=UTC)

    @staticmethod
    def _parse_resets_at(value: object) -> datetime | None:
        """``resets_at`` as either epoch seconds (int/float) or an ISO-8601 string,
        coerced to the same UTC-aware instant either way (``bzh:utc-instants``)."""
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value, tz=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return None


def _conforms_subscription_sampler(x: AnthropicSubscriptionSampler) -> ISubscriptionSampler:
    return x
