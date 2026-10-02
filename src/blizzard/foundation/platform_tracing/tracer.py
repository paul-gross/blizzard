"""The platform-span seam loop and domain code open spans through — never OpenTelemetry directly."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from blizzard.foundation.trace_ids import DerivedContext
from blizzard.foundation.trace_spans import Attributes


class IPlatformTracer(Protocol):
    def root(self, name: str, attributes: Attributes | None = None) -> AbstractContextManager[None]:
        """A span on an empty context — a new trace, sampled by the root rule."""
        ...

    def child(self, name: str, attributes: Attributes | None = None) -> AbstractContextManager[None]:
        """A span under whichever span is current."""
        ...

    def under(self, derived: DerivedContext) -> AbstractContextManager[None]:
        """Make ``derived`` the remote, sampled parent of whatever opens inside; opens no span itself."""
        ...

    def link(self, derived: DerivedContext) -> None: ...


class NoopPlatformTracer:
    """What every consumer holds while platform tracing is off."""

    def root(self, name: str, attributes: Attributes | None = None) -> AbstractContextManager[None]:
        return _nothing()

    def child(self, name: str, attributes: Attributes | None = None) -> AbstractContextManager[None]:
        return _nothing()

    def under(self, derived: DerivedContext) -> AbstractContextManager[None]:
        return _nothing()

    def link(self, derived: DerivedContext) -> None:
        return None


@contextmanager
def _nothing() -> Iterator[None]:
    yield
