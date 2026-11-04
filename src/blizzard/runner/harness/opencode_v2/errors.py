"""What a turn's failure was, read from the session's error record by its ``type`` — the classes the 2.1.4 corpus
names — or named by the driver itself when the service, not the turn, failed."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.opencode_v2.shapes import StructuredError


class ErrorKind(StrEnum):
    USAGE_LIMIT = "usage-limit"
    OVERLOAD = "overload"
    CONFIGURATION = "configuration"
    REFUSAL = "refusal"
    INTERRUPTED = "interrupted"
    SESSION_MISSING = "session-missing"
    SERVICE = "service"
    OTHER = "other"


_KINDS: dict[str, ErrorKind] = {
    "provider.rate-limit": ErrorKind.USAGE_LIMIT,
    "provider.quota": ErrorKind.USAGE_LIMIT,
    "provider.free-limit": ErrorKind.USAGE_LIMIT,
    "provider.internal": ErrorKind.OVERLOAD,
    "provider.no-route": ErrorKind.CONFIGURATION,
    "provider.content-filter": ErrorKind.REFUSAL,
    "aborted": ErrorKind.INTERRUPTED,
}

OVERLOAD_STATUS = 529


@domain_model
@dataclass(frozen=True)
class TurnError:
    """A classified failure. ``retryable`` holds for an overload alone; ``retry_after_ms`` is a usage limit's
    reset, when the provider sent one."""

    kind: ErrorKind
    message: str
    type: str = ""
    status: int | None = None
    retry_after_ms: int | None = None

    def retryable(self) -> bool:
        return self.kind is ErrorKind.OVERLOAD


def classify_error(error: StructuredError) -> TurnError:
    kind = _KINDS.get(error.type, ErrorKind.OTHER)
    if error.status == OVERLOAD_STATUS:
        kind = ErrorKind.OVERLOAD
    return TurnError(
        kind=kind, message=error.message, type=error.type, status=error.status, retry_after_ms=error.retry_after_ms
    )


__all__ = ["OVERLOAD_STATUS", "ErrorKind", "TurnError", "classify_error"]
