"""The open-redirect guard on a caller-supplied post-login destination."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReturnTo:
    """Where a login lane should land the browser once it is done."""

    raw: str | None

    @property
    def safe(self) -> str:
        """The raw target when it is a same-origin relative path, else ``/`` — an absolute
        URL or a protocol-relative ``//host`` is an open-redirect vector."""
        if self.raw and self.raw.startswith("/") and not self.raw.startswith("//"):
            return self.raw
        return "/"
