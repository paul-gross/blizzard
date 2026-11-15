"""Which declaration binds a harness id: the one it has, or, among several, the one its configured binary's
``--version`` names. Each composed harness set keeps its selection for its retained invocations, including terminal
work, even when a replacement set becomes current. A version no declaration binds falls to the first, whose health then
refuses it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from blizzard.foundation.roles import collaborator
from blizzard.runner.harness.capability_snapshot import BinaryVersionCache
from blizzard.runner.harness.declaration import HarnessSection, IHarnessDeclaration, IVersionSelectedDeclaration


class IVersionProbe(Protocol):
    """What choosing a declaration asks of a binary: whether it is there to run, and the version it reports."""

    def binary_present(self, binary: str) -> bool: ...

    def observe_version(self, binary: str) -> str | None: ...


class CachedVersionProbe:
    """A binary probe sharing successful evidence with selection, adapters and health."""

    def __init__(self, probe: IVersionProbe, versions: BinaryVersionCache) -> None:
        self._probe = probe
        self._versions = versions

    def binary_present(self, binary: str) -> bool:
        return self._probe.binary_present(binary)

    def observe_version(self, binary: str) -> str | None:
        return self._versions.get(binary, lambda: self._probe.observe_version(binary))


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
    family: Sequence[IHarnessDeclaration[Any]], section: HarnessSection, versions: IVersionProbe
) -> IHarnessDeclaration[Any]:
    """Choose the family's declaration using version evidence when needed; otherwise choose the first.

    The probe may reuse successful evidence; health reports a missing binary."""
    if len(family) == 1 or not section.enabled:
        return family[0]
    candidates = [cast(IVersionSelectedDeclaration[Any], declaration) for declaration in family]
    binary = candidates[0].version_binary(section)
    if not versions.binary_present(binary):
        return family[0]
    raw = versions.observe_version(binary)
    return next((each for each in candidates if each.admits_version(raw)), family[0])


def select_bindings(
    families: HarnessFamilies, sections: Mapping[str, HarnessSection], versions: IVersionProbe
) -> BindingSelection:
    """Each harness id's chosen declaration, for the parsed ``sections`` by harness id."""
    return BindingSelection(
        {
            harness_id: select_declaration(family, sections[harness_id], versions)
            for harness_id, family in families.items()
        }
    )


__all__ = ["BindingSelection", "HarnessFamilies", "IVersionProbe", "select_bindings", "select_declaration"]
