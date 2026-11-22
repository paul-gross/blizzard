"""A test root's replacements over the hub's registered graph, through the registry's test override seam.

A root swaps a configured adapter by replacing the one registration that owns its key, so every alias and every bag
field the registration fills resolves to the stand-in, and no production parameter carries it."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from blizzard.foundation.registry.interface import Ref
from blizzard.foundation.registry.internal.overrides import OverrideSet
from blizzard.foundation.registry.internal.recording import OWNING, RecordingRegistry
from blizzard.hub.forge.readiness import ICredentialProbe


def owned_ref(registry: RecordingRegistry, key: object) -> Ref[Any]:
    """The ``Ref`` of the one registration in ``registry`` that owns ``key``."""
    (token,) = [entry.token for entry in registry.entries if entry.kind in OWNING and entry.key == key]
    return Ref(token)


def replaced_probes(registry: RecordingRegistry, probes: Mapping[str, ICredentialProbe]) -> OverrideSet:
    """Replacements supplying ``probes`` as every forge kind's credential check, borrowed."""
    overrides = OverrideSet(registry)
    overrides.replace_value(owned_ref(registry, Mapping[str, ICredentialProbe]), probes)
    return overrides
