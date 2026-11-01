"""The stdout-target idiom both coding-harness bindings open their capture files with."""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from typing import IO


@contextlib.contextmanager
def stdout_target(path: str, *, mode: str = "ab") -> Generator[IO[bytes] | None, None, None]:
    """The injected stdout file, opened in ``mode``, else ``None`` (no redirect) when
    ``path`` is empty. A context manager so no caller leaks the descriptor across a failed
    ``Popen``. ``mode`` defaults to append (``"ab"``) — a lease's own growing capture; a
    detached judgement's single-shot reply file passes ``mode="wb"`` instead."""
    if not path:
        yield None
        return
    with open(path, mode) as f:
        yield f
