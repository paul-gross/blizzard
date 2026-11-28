"""A registration vocabulary for legacy-built state: ``leaf`` and ``bag`` over the ordinary ``Registry``.

A leaf is one legacy-constructed value an adopted provider may depend on; a bag assembles legacy state out of adopted
providers and leaves. Both are built as factories, but each is recorded as its own kind so the inventory of what the
legacy side still owns can be counted. They live here, not on ``Registry``, so area wiring cannot reach them: only a
process root holds a ``LegacyRegistry`` — the runner host's, whose leaves stand over its borrowed host and compose
its runner children — and the hub's graph declares neither kind."""

from __future__ import annotations

from collections.abc import Callable

from blizzard.foundation.registry.interface import Lifetime, Ref
from blizzard.foundation.registry.internal.recording import Kind, RecordingRegistry


class LegacyRegistry(RecordingRegistry):
    """A ``Registry`` that also declares leaves and bags.

    Nothing but a bag may depend on a bag: the translation refuses any other registration, or alias, whose dependency
    is a bag key, so an adopted provider never ends up behind a legacy bag."""

    def leaf[T](self, build: Callable[..., T], *, lifetime: Lifetime) -> Ref[T]:
        """Register a function that constructs one legacy value, keyed by its return annotation."""
        return self._own_built(Kind.LEAF, build, lifetime)

    def bag[T](self, assemble: Callable[..., T], *, lifetime: Lifetime) -> Ref[T]:
        """Register a function that assembles legacy state from providers and leaves, keyed by its return annotation."""
        return self._own_built(Kind.BAG, assemble, lifetime)
