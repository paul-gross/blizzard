"""The binding a harness id composes: its one declaration, or among several the one its configured binary's
``--version`` names, probed once; a version none binds, a missing binary, or a disabled section takes the first."""

from __future__ import annotations

from typing import Any

import pytest

from blizzard.runner.harness.binding_selection import BindingSelection, select_bindings, select_declaration
from blizzard.runner.harness.declaration import IHarnessDeclaration
from blizzard.runner.harness.opencode.declaration import OPENCODE_DECLARATION
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.opencode_v2.declaration import OPENCODE_V2_DECLARATION
from blizzard.runner.harness.wiring import (
    HarnessSections,
    declared_normalizer_versions,
    enabled,
    first_bindings,
    harness_families,
)


class _Counting:
    """A version probe reporting ``output`` for ``present`` binaries, counting each version it observes."""

    def __init__(self, output: str | None = None, *, present: bool = True) -> None:
        self.output = output
        self.present = present
        self.probes = 0

    def binary_present(self, binary: str) -> bool:
        return self.present

    def observe_version(self, binary: str) -> str | None:
        self.probes += 1
        return self.output


def _sections(*, enabled: bool = True) -> HarnessSections:
    return HarnessSections.defaults().replaced(OpenCodeSection(enabled=enabled))


@pytest.mark.parametrize(
    ("output", "chosen"),
    [
        ("1.18.33", OPENCODE_DECLARATION),
        ("opencode v2.1.4", OPENCODE_V2_DECLARATION),
        ("2.9.0", OPENCODE_V2_DECLARATION),
        ("3.0.0", OPENCODE_DECLARATION),
        ("1.17.0", OPENCODE_DECLARATION),
        ("not a version", OPENCODE_DECLARATION),
    ],
)
def test_the_binarys_version_chooses_the_opencode_declaration(output: str, chosen: IHarnessDeclaration[Any]) -> None:
    probe = _Counting(output)
    family = harness_families()["opencode"]
    section = _sections().of("opencode")
    assert select_declaration(family, section, probe) is chosen
    assert probe.probes == 1


def test_a_missing_binary_or_a_disabled_section_takes_opencode_1_without_a_probe() -> None:
    family = harness_families()["opencode"]
    absent = _Counting("2.1.4", present=False)
    assert select_declaration(family, _sections().of("opencode"), absent) is OPENCODE_DECLARATION
    assert absent.probes == 0
    probe = _Counting("2.1.4")
    disabled = _sections(enabled=False).of("opencode")
    assert select_declaration(family, disabled, probe) is OPENCODE_DECLARATION and probe.probes == 0


def test_one_opencode_section_composes_either_major_and_claude_code_is_never_probed() -> None:
    sections = _sections()
    probe = _Counting("2.1.4")
    selection = select_bindings(harness_families(), {section.harness_id: section for section in sections}, probe)
    assert [declaration for declaration, _ in enabled(sections, selection)][-1] is OPENCODE_V2_DECLARATION
    assert selection.of("claude_code") is harness_families()["claude_code"][0]
    assert dict(enabled(sections, selection))[OPENCODE_V2_DECLARATION] is sections.of("opencode")
    assert probe.probes == 1


def test_first_bindings_names_each_family_s_first_declaration() -> None:
    assert first_bindings() == BindingSelection.first(harness_families())
    for harness_id, family in harness_families().items():
        assert first_bindings().of(harness_id) is family[0]


def test_every_declaration_s_normalizer_is_declared_for_the_dialect_guard() -> None:
    assert {"opencode-v2/1", OPENCODE_DECLARATION.normalizer_version} <= set(declared_normalizer_versions())
