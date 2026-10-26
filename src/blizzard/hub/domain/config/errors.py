"""The refusals every configured-record write shares: a field that is invalid, and a revision that moved."""

from __future__ import annotations


class ConfigFieldError(ValueError):
    """A write is invalid on one field, which it names. ``code`` tells one refusal apart from another on the wire —
    ``value_error`` for a malformed field, a refusal's own code otherwise."""

    code: str = "value_error"

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class ConfigRevisionConflict(Exception):
    """A write read a revision the store no longer holds — names the current one."""

    def __init__(self, kind: str, key: str, *, current: int) -> None:
        super().__init__(f"{kind} {key} is at revision {current}")
        self.current = current
