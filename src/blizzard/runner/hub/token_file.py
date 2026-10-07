"""The runner's hub bearer token as its runtime dir's ``.env`` holds it.

``runner init`` writes the token the hub issues there, and every runner verb reads it back when the
process environment carries none. The file keeps systemd ``EnvironmentFile`` syntax — ``NAME=value``
lines, ``#`` and ``;`` comments, optionally quoted values, the last assignment winning — because an
installed runner's unit loads the same file, and the token's line is the only one ``init`` rewrites.

These are the pure rules over the file's text; the file driver is ``internal/token_file.py``."""

from __future__ import annotations

#: The file in a runner runtime dir that holds its hub token.
ENV_FILENAME = ".env"

_COMMENT_PREFIXES = ("#", ";")
_QUOTES = ('"', "'")


def assigned_token(text: str, token_env: str) -> str:
    """The token ``text`` assigns to ``token_env`` (the last assignment wins), or ``""`` when none."""
    token = ""
    for line in text.splitlines():
        assigned = _assignment(line)
        if assigned is not None and assigned[0] == token_env:
            token = assigned[1]
    return token


def with_token(text: str, token_env: str, token: str) -> str:
    """``text`` with the ``token_env`` assignment replaced in place by ``token`` (appended when absent),
    every other line kept as it was; a repeated assignment collapses to the first's position."""
    kept: list[str] = []
    placed = False
    for line in text.splitlines():
        assigned = _assignment(line)
        if assigned is None or assigned[0] != token_env:
            kept.append(line)
        elif not placed:
            kept.append(f"{token_env}={token}")
            placed = True
    if not placed:
        kept.append(f"{token_env}={token}")
    return "\n".join(kept) + "\n"


def _assignment(line: str) -> tuple[str, str] | None:
    """A ``NAME=value`` line's name and unquoted value; ``None`` for a blank line, a comment, or
    anything else that assigns nothing."""
    stripped = line.strip()
    if not stripped or stripped.startswith(_COMMENT_PREFIXES) or "=" not in stripped:
        return None
    name, _, value = stripped.partition("=")
    value = value.strip()
    if len(value) >= 2 and value[0] in _QUOTES and value[-1] == value[0]:
        value = value[1:-1]
    return name.strip(), value
