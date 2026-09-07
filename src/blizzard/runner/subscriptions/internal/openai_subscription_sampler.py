"""The OpenAI (ChatGPT plan) subscription-sampler binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.subscriptions.subscription_sampler.ISubscriptionSampler`
against the usage endpoint the Codex CLI's own account reads, independent of the
coding-harness adapter — blizzard runs no OpenAI harness today."""

from __future__ import annotations

import base64
import binascii
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import httpx

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.subscriptions.subscription_sampler import (
    ExternalSubscriptionUsageSnapshot,
    ExternalSubscriptionUsageWindow,
    ISubscriptionSampler,
)

_log = get_logger("blizzard.runner.harness")

# The API host and the credential file the Codex CLI's own login writes. Both overridable.
DEFAULT_USAGE_API_BASE = "https://chatgpt.com/backend-api"
DEFAULT_CREDENTIALS_PATH = str(Path.home() / ".codex" / "auth.json")

_USAGE_PATH = "/codex/usage"
_USAGE_TIMEOUT_SECONDS = 5.0

# Load-bearing, not cosmetic: the edge in front of this endpoint 403s httpx's default
# `python-httpx/*` agent, so leaving it unset fails every sample. Pinned by a test.
_CLIENT_USER_AGENT = "blizzard-runner"

# The response nests each window under `rate_limit`; the label is derived from the
# window's own reported length rather than from these keys, which carry no duration.
_USAGE_WINDOW_KEYS: tuple[str, ...] = ("primary_window", "secondary_window")


class OpenAISubscriptionSampler:
    """Samples a ChatGPT plan's Codex rate-limit utilization. Never raises."""

    def __init__(
        self,
        *,
        credentials_path: str | None = None,
        usage_api_base: str = DEFAULT_USAGE_API_BASE,
        http_client: httpx.Client | None = None,
        clock: IClock,
    ) -> None:
        # `credentials_path` is read-only here, and the client is constructed lazily.
        self._credentials_path = credentials_path or DEFAULT_CREDENTIALS_PATH
        self._usage_api_base = usage_api_base
        self._http_client = http_client
        self._clock: IClock = clock

    def sample(self) -> ExternalSubscriptionUsageSnapshot | None:
        credential = self._read_credential()
        if credential is None:
            return None
        access_token, account_id = credential
        try:
            resp = self._usage_client().get(
                f"{self._usage_api_base}{_USAGE_PATH}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    # Required to be present; its value is not what selects the account —
                    # the token is — but a real one is sent rather than a placeholder.
                    "chatgpt-account-id": account_id,
                    "User-Agent": _CLIENT_USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=_USAGE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            # Covers both a timeout and a connection failure: a best-effort diagnostic
            # sample, never a spawn/resume failure.
            _log.warning(
                "external subscription usage sample failed: request error",
                path=self._credentials_path,
                detail=str(exc),
            )
            return None
        if not resp.is_success:
            # 401 here is the expired-token path: nothing refreshes this credential but
            # the Codex CLI itself, and this sampler never writes the file.
            _log.warning(
                "external subscription usage sample failed: non-2xx response",
                path=self._credentials_path,
                status_code=resp.status_code,
            )
            return None
        try:
            body = resp.json()
        except ValueError as exc:
            _log.warning(
                "external subscription usage sample failed: unparseable response body",
                path=self._credentials_path,
                detail=str(exc),
            )
            return None
        if not isinstance(body, dict):
            _log.warning(
                "external subscription usage sample failed: unexpected response shape",
                path=self._credentials_path,
                body_type=type(body).__name__,
            )
            return None
        windows = self._parse_usage_windows(body)
        if not windows:
            _log.warning(
                "external subscription usage sample failed: no parseable windows in response",
                path=self._credentials_path,
            )
            return None
        return ExternalSubscriptionUsageSnapshot(sampled_at=self._clock.now(), windows=tuple(windows))

    def _usage_client(self) -> httpx.Client:
        """The injected ``httpx.Client``, or a lazily-constructed real one.

        Lazy, so a sampler that never samples opens no connection pool, and cached once
        created, so repeated samples reuse one connection."""
        if self._http_client is None:
            self._http_client = httpx.Client()
        return self._http_client

    def _read_credential(self) -> tuple[str, str] | None:
        """The access token and account id from the credential file, or ``None``.

        Read-only, always: the Codex CLI owns the refresh flow, holds its own lock over
        this file, and rotates the refresh token, so a second writer risks both
        corrupting it mid-refresh and invalidating the login it just renewed."""
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
        tokens = data.get("tokens") if isinstance(data, dict) else None
        if not isinstance(tokens, dict):
            _log.warning(
                "external subscription usage sample failed: no tokens block in credentials",
                path=self._credentials_path,
            )
            return None
        access_token = tokens.get("access_token")
        account_id = tokens.get("account_id")
        if not isinstance(access_token, str) or not access_token:
            _log.warning(
                "external subscription usage sample failed: no access token in credentials",
                path=self._credentials_path,
            )
            return None
        if not isinstance(account_id, str) or not account_id:
            _log.warning(
                "external subscription usage sample failed: no account id in credentials",
                path=self._credentials_path,
            )
            return None
        expires_at = self._parse_token_expiry(access_token)
        if expires_at is not None and expires_at <= self._clock.now():
            _log.warning(
                "external subscription usage sample failed: access token expired",
                path=self._credentials_path,
                expires_at=iso_utc(expires_at),
            )
            return None
        return access_token, account_id

    def _parse_usage_windows(self, body: dict[str, object]) -> list[ExternalSubscriptionUsageWindow]:
        """Every window ``body`` reports usable data for. A window that is absent, or whose
        percentage, length, or reset instant is null or unparseable, is skipped rather than
        fabricated as a zero entry."""
        rate_limit = body.get("rate_limit")
        if not isinstance(rate_limit, dict):
            return []
        windows: list[ExternalSubscriptionUsageWindow] = []
        for key in _USAGE_WINDOW_KEYS:
            entry = rate_limit.get(key)
            if not isinstance(entry, dict):
                continue
            used_percent = entry.get("used_percent")
            window_seconds = self._parse_window_seconds(entry.get("limit_window_seconds"))
            if not isinstance(used_percent, int | float) or isinstance(used_percent, bool):
                continue
            if window_seconds is None:
                continue
            resets_at = self._parse_resets_at(entry.get("reset_at"))
            if resets_at is None:
                continue
            label = _window_label(window_seconds)
            # Two windows reporting one length would derive one label, and the board keys
            # its per-window render on it; the second is dropped rather than collided.
            if any(existing.window == label for existing in windows):
                continue
            windows.append(
                ExternalSubscriptionUsageWindow(
                    window=label,
                    utilization_pct=float(used_percent),
                    resets_at=resets_at,
                    window_seconds=window_seconds,
                )
            )
        return windows

    @staticmethod
    def _parse_window_seconds(value: object) -> int | None:
        """A window's positive whole-second length, or ``None`` for anything unusable.

        Tolerates a float, since this API is undocumented and a widened numeric type must
        not silently drop the window it describes — but a non-finite one is rejected here,
        because ``int()`` raises on it and this seam never raises."""
        if not isinstance(value, int | float) or isinstance(value, bool):
            return None
        if not math.isfinite(value) or value <= 0:
            return None
        return int(value)

    @staticmethod
    def _parse_resets_at(value: object) -> datetime | None:
        """``reset_at`` as either epoch seconds or an ISO-8601 string, coerced to the same
        UTC-aware instant either way (``bzh:utc-instants``). This endpoint returns epoch
        seconds; the string form is carried because an unparseable reset instant drops its
        window, and a single undocumented shape change would otherwise silence the sampler."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            try:
                return datetime.fromtimestamp(value, tz=UTC)
            except (OverflowError, OSError, ValueError):
                return None
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return None

    @staticmethod
    def _parse_token_expiry(access_token: str) -> datetime | None:
        """The ``exp`` claim of the JWT access token, read unverified — the server remains
        the authority, and this only avoids spending a request on a token already dead.
        ``None`` for any token this cannot read, which proceeds to the request."""
        parts = access_token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        try:
            decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            claims = json.loads(decoded)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
        exp = claims.get("exp") if isinstance(claims, dict) else None
        if not isinstance(exp, int | float) or isinstance(exp, bool):
            return None
        try:
            return datetime.fromtimestamp(exp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None


def _window_label(window_seconds: int) -> str:
    """This provider names its windows ``primary``/``secondary``, so the operator-facing
    label is derived from the length instead — giving the same ``5h``/``7d`` vocabulary
    the Anthropic binding reports, for the two windows a ChatGPT plan meters on."""
    if window_seconds % 86_400 == 0:
        return f"{window_seconds // 86_400}d"
    if window_seconds % 3_600 == 0:
        return f"{window_seconds // 3_600}h"
    if window_seconds % 60 == 0:
        return f"{window_seconds // 60}m"
    return f"{window_seconds}s"


def _conforms_subscription_sampler(x: OpenAISubscriptionSampler) -> ISubscriptionSampler:
    return x
