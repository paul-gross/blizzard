"""The shape every hub action takes: a catalogue spec and one pass over what it is handed.

An action is pure of the hub's data (``bzh:hub-action-purity``): it reads only its three inputs, writes
only through its sink, and is constructed with no arguments, so no collaborator arrives through it."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.actions.effects import IEffectSink, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.forge.landing import VisitForges


class IHubAction(Protocol):
    @property
    def spec(self) -> ActionSpec: ...

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass: ...
