"""The GitHub rate dialect — how a GitHub answer's ``X-RateLimit-*`` headers become a budget reading, and
``rate_refusal_resume_at``, the rule for whether a GitHub answer is a rate refusal and when it lifts (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.forge.budget import BudgetReading
from blizzard.hub.forge.internal.github_rates import GitHubRateDialect, rate_refusal_resume_at

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
_RESET = _NOW + timedelta(seconds=600)
_DIALECT = GitHubRateDialect()


def _rate(remaining: int, *, limit: int = 100, reset: datetime = _RESET) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(int(reset.timestamp())),
    }


def test_an_answer_with_rate_headers_reads_its_limit_remaining_and_reset() -> None:
    assert _DIALECT.reading(_rate(30), _NOW) == BudgetReading("core", 100, 30, _RESET)


def test_an_answer_without_rate_headers_teaches_nothing() -> None:
    assert _DIALECT.reading({"Content-Type": "application/json", "X-RateLimit-Remaining": "0"}, _NOW) is None


def test_a_reading_lands_in_the_bucket_the_answer_names() -> None:
    assert _DIALECT.reading({**_rate(0), "X-RateLimit-Resource": "search"}, _NOW) == BudgetReading(
        "search", 100, 0, _RESET
    )


def test_a_reading_naming_no_bucket_lands_in_the_core_bucket() -> None:
    reading = _DIALECT.reading(_rate(5), _NOW)

    assert reading is not None
    assert reading.bucket == "core"


@pytest.mark.parametrize(
    ("status", "headers", "message", "resume_after"),
    [
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(_NOW.timestamp()) + 600)}, "", 600),
        (429, {"Retry-After": "30"}, "", 30),
        (403, {"retry-after": "30", "x-ratelimit-reset": str(int(_NOW.timestamp()) + 600)}, "", 30),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(_NOW.timestamp()) - 5)}, "", 60),
        (429, {"retry-after": "0"}, "", 60),
        (403, {}, "You have exceeded a secondary rate limit.", 60),
        (403, {"retry-after": "10"}, "You have exceeded a secondary rate limit.", 60),
        (403, {"retry-after": "120"}, "You have exceeded a secondary rate limit.", 120),
    ],
    ids=[
        "reset",
        "retry-after",
        "retry-after-wins",
        "past-reset",
        "zero-retry-after",
        "secondary",
        "secondary-floor",
        "secondary-later",
    ],
)
def test_a_rate_refusal_says_when_it_lifts(
    status: int, headers: dict[str, str], message: str, resume_after: int
) -> None:
    assert rate_refusal_resume_at(status, headers, message, _NOW) == _NOW + timedelta(seconds=resume_after)


@pytest.mark.parametrize(
    ("status", "headers", "message"),
    [
        (403, {"x-ratelimit-remaining": "12"}, "Resource not accessible"),
        (404, {"x-ratelimit-remaining": "0"}, ""),
        (200, {"retry-after": "30"}, ""),
    ],
    ids=["forbidden", "not-found", "ok"],
)
def test_any_other_answer_is_no_rate_refusal(status: int, headers: dict[str, str], message: str) -> None:
    assert rate_refusal_resume_at(status, headers, message, _NOW) is None


def test_the_dialect_refusal_is_the_rate_refusal_rule() -> None:
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(_RESET.timestamp()))}

    assert _DIALECT.refusal(403, headers, "", _NOW) == _RESET
    assert _DIALECT.refusal(404, headers, "", _NOW) is None
