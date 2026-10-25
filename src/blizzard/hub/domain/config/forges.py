"""The forge kinds a repository's forge may declare, and what each kind offers."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.hub.domain.config.capabilities import Capability

#: The only forge kinds a forge may declare; an unknown kind fails at config load, not at first use.
KNOWN_FORGE_KINDS = frozenset({"github"})

#: What each forge kind's adapter offers.
FORGE_KIND_CAPABILITIES: Mapping[str, frozenset[Capability]] = {
    "github": frozenset(Capability),
}
