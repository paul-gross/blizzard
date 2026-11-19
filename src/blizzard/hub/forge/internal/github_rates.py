"""GitHub's rate dialect (package-private): how a GitHub answer's ``X-RateLimit-*`` headers become a budget reading,
and whether an answer is a rate refusal and when it lifts. Shared by every GitHub adapter that speaks to a forge, so
each reads GitHub's answers the same way; free of any HTTP library."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.roles import adapter_model
from blizzard.hub.forge.budget import CORE_BUCKET, BudgetReading

#: A rate limit that names no usable instant, and the floor of a secondary limit.
DEFAULT_RESUME = timedelta(seconds=60)
#: What a rate refusal's message names, whichever limit it hit.
_RATE_MARKER = "rate limit"


@adapter_model
@dataclass(frozen=True)
class GitHubRateDialect:
    """GitHub's answers read as seam-level readings and refusal verdicts."""

    def reading(self, headers: Mapping[str, str], now: datetime) -> BudgetReading | None:
        """The reading an answer's rate headers carry, in the bucket ``X-RateLimit-Resource`` names or
        :data:`CORE_BUCKET` when it names none; ``None`` for an answer without a whole reading."""
        lowered = _lowered(headers)
        try:
            limit = int(lowered["x-ratelimit-limit"])
            remaining = int(lowered["x-ratelimit-remaining"])
            reset = datetime.fromtimestamp(int(lowered["x-ratelimit-reset"]), tz=now.tzinfo)
        except (KeyError, ValueError, OverflowError, OSError):
            return None
        return BudgetReading(lowered.get("x-ratelimit-resource") or CORE_BUCKET, limit, remaining, reset)

    def refusal(self, status: int, headers: Mapping[str, str], message: str, now: datetime) -> datetime | None:
        return rate_refusal_resume_at(status, headers, message, now)


def is_rate_refusal(status: int, headers: Mapping[str, str], message: str) -> bool:
    """Whether a GitHub answer refuses for rate rather than for the credential. Only a ``403`` or a ``429`` can: with
    its budget spent, a ``Retry-After``, or a message naming a rate limit."""
    if status not in (403, 429):
        return False
    lowered = _lowered(headers)
    return lowered.get("x-ratelimit-remaining") == "0" or "retry-after" in lowered or _RATE_MARKER in message.lower()


def rate_refusal_resume_at(status: int, headers: Mapping[str, str], message: str, now: datetime) -> datetime | None:
    """When a GitHub answer says to come back, or ``None`` when :func:`is_rate_refusal` says it is no rate refusal: the
    ``Retry-After``, else the budget's reset, else :data:`DEFAULT_RESUME` from now — a secondary limit named in
    ``message`` never lifting sooner than :data:`DEFAULT_RESUME`."""
    if not is_rate_refusal(status, headers, message):
        return None
    lowered = _lowered(headers)
    secondary = "secondary rate limit" in message.lower()
    resume = _seconds_from(now, lowered.get("retry-after")) or _reset_at(now, lowered.get("x-ratelimit-reset"))
    resume = resume or now + DEFAULT_RESUME
    return max(resume, now + DEFAULT_RESUME) if secondary else resume


def _lowered(headers: Mapping[str, str]) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()}


def _seconds_from(now: datetime, raw: str | None) -> datetime | None:
    try:
        seconds = int(raw) if raw is not None else 0
    except ValueError:
        return None
    return now + timedelta(seconds=seconds) if seconds > 0 else None


def _reset_at(now: datetime, raw: str | None) -> datetime | None:
    try:
        reset = datetime.fromtimestamp(int(raw), tz=now.tzinfo) if raw is not None else None
    except (ValueError, OverflowError, OSError):
        return None
    return reset if reset is not None and reset > now else None
