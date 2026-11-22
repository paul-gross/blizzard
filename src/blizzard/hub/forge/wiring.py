"""The forge area's registrations: each forge kind's read-only credential check, held once for the process."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Registry
from blizzard.hub.forge.internal.factory import build_credential_probes


def register_process(registry: Registry) -> None:
    """Declare the credential probes, by forge kind."""
    registry.factory(build_credential_probes, lifetime=Lifetime.PROCESS)
