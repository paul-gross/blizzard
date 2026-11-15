"""The executable identity a version observation is evidence about."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class BinaryIdentity:
    """A resolved executable and the file metadata that bounds reuse of its version."""

    path: Path
    size: int
    mtime_ns: int


class IBinaryIdentity(Protocol):
    """Resolve and fingerprint a configured executable, or report no reusable identity."""

    def identify(self, binary: str) -> BinaryIdentity | None: ...
