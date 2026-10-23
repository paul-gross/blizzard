"""The hub auth-mode seam: whether the configured hub runs an IdP surface at all."""

from __future__ import annotations

from typing import Protocol


class IHubAuthMode(Protocol):
    def enabled(self) -> bool:
        """Whether the hub offers an IdP surface to bounce to."""
        ...
