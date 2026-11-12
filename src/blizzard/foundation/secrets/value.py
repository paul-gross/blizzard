"""The plaintext secret value — redacted everywhere it could be printed (``bzh:secret-write-only``)."""

from __future__ import annotations

_REDACTED = "SecretValue(<redacted>)"


class SecretValueBlank(ValueError):
    """A written secret value is empty or only whitespace — never a usable credential."""

    def __init__(self) -> None:
        super().__init__("a secret value must not be blank")


class SecretValue:
    """A plaintext credential. Redacted in ``repr``/``str``; :meth:`expose` is the one
    accessor, called only where the value is handed to an external system."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    @classmethod
    def entered(cls, raw: str) -> SecretValue:
        """A value entering through a create or replace — :class:`SecretValueBlank` when blank."""
        if not raw.strip():
            raise SecretValueBlank()
        return cls(raw)

    def expose(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return _REDACTED

    __str__ = __repr__
