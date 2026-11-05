"""Which declaration binds a harness id: the one it has, or, among several, the one its configured binary's
``--version`` names. Chosen once, when a runner is composed, and never swapped while it runs; a version no
declaration binds falls to the first, whose health then refuses it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from blizzard.foundation.roles import collaborator
from blizzard.runner.harness.declaration import HarnessSection, IHarnessDeclaration, IVersionSelectedDeclaration
from blizzard.runner.harness.internal.version_probe import VersionProbe

type HarnessFamilies = Mapping[str, Sequence[IHarnessDeclaration[Any]]]


@collaborator
@dataclass(frozen=True)
class BindingSelection:
    """The declaration chosen for each harness id."""

    chosen: Mapping[str, IHarnessDeclaration[Any]]

    @classmethod
    def first(cls, families: HarnessFamilies) -> BindingSelection:
        """Each family's first declaration, chosen with no binary probed."""
        return cls({harness_id: family[0] for harness_id, family in families.items()})

    def of(self, harness_id: str) -> IHarnessDeclaration[Any]:
        return self.chosen[harness_id]


def select_declaration(
    family: Sequence[IHarnessDeclaration[Any]], section: HarnessSection, versions: VersionProbe
) -> IHarnessDeclaration[Any]:
    """The declaration of ``family`` binding ``section``'s binary, probed once when the family holds several."""
    if len(family) == 1 or not section.enabled:
        return family[0]
    candidates = [cast(IVersionSelectedDeclaration[Any], declaration) for declaration in family]
    raw = versions.observe_version(candidates[0].version_binary(section))
    return next((each for each in candidates if each.admits_version(raw)), family[0])


def select_bindings(
    families: HarnessFamilies, sections: Mapping[str, HarnessSection], versions: VersionProbe
) -> BindingSelection:
    """Each harness id's chosen declaration, for the parsed ``sections`` by harness id."""
    return BindingSelection(
        {
            harness_id: select_declaration(family, sections[harness_id], versions)
            for harness_id, family in families.items()
        }
    )


__all__ = ["BindingSelection", "HarnessFamilies", "select_bindings", "select_declaration"]
