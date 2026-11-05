"""The binding a harness id composes: its one declaration, or among several the one its configured binary's
``--version`` names, probed once; a version none binds, a missing binary, or a disabled section takes the first."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blizzard.runner.harness.binding_selection import BindingSelection, select_declaration
from blizzard.runner.harness.declaration import IHarnessDeclaration
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.opencode.declaration import OPENCODE_DECLARATION
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.opencode_v2.declaration import OPENCODE_V2_DECLARATION
from blizzard.runner.harness.wiring import (
    HarnessSections,
    declared_normalizer_versions,
    enabled,
    first_bindings,
    harness_families,
    select_harness_bindings,
)
from blizzard.runner.process.child_env import AllowlistedEnv
from tests.support.child_launch import CHILDREN, tool_env


class _Counting(VersionProbe):
    def __init__(self) -> None:
        super().__init__(CHILDREN, tool_env())
        self.probes = 0

    def observe_version(self, binary: str) -> str | None:
        self.probes += 1
        return super().observe_version(binary)


def _binary(tmp_path: Path, output: str) -> str:
    path = tmp_path / "opencode"
    path.write_text(f"#!/bin/sh\necho '{output}'\n")
    path.chmod(0o755)
    return str(path)


def _sections(binary: str, *, enabled: bool = True) -> HarnessSections:
    return HarnessSections.defaults().replaced(OpenCodeSection(enabled=enabled, binary=binary))


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
def test_the_binarys_version_chooses_the_opencode_declaration(
    tmp_path: Path, output: str, chosen: IHarnessDeclaration[Any]
) -> None:
    probe = _Counting()
    family = harness_families()["opencode"]
    section = _sections(_binary(tmp_path, output)).of("opencode")
    assert select_declaration(family, section, probe) is chosen
    assert probe.probes == 1


def test_a_missing_binary_or_a_disabled_section_takes_opencode_1_without_a_probe(tmp_path: Path) -> None:
    family = harness_families()["opencode"]
    absent = _Counting()
    missing = _sections(str(tmp_path / "absent")).of("opencode")
    assert select_declaration(family, missing, absent) is OPENCODE_DECLARATION and absent.probes == 0
    probe = _Counting()
    disabled = _sections(_binary(tmp_path, "2.1.4"), enabled=False).of("opencode")
    assert select_declaration(family, disabled, probe) is OPENCODE_DECLARATION and probe.probes == 0


def test_one_opencode_section_composes_either_major_and_claude_code_is_never_probed(tmp_path: Path) -> None:
    sections = _sections(_binary(tmp_path, "2.1.4"))
    selection = select_harness_bindings(sections, worker_env=AllowlistedEnv.of(()), children=CHILDREN)
    assert [declaration for declaration, _ in enabled(sections, selection)][-1] is OPENCODE_V2_DECLARATION
    assert selection.of("claude_code") is harness_families()["claude_code"][0]
    assert dict(enabled(sections))[OPENCODE_DECLARATION] is sections.of("opencode")


def test_with_no_selection_each_harness_takes_its_first_declaration() -> None:
    assert first_bindings() == BindingSelection.first(harness_families())
    assert first_bindings().of("opencode") is OPENCODE_DECLARATION


def test_every_declaration_s_normalizer_is_declared_for_the_dialect_guard() -> None:
    assert {"opencode-v2/1", OPENCODE_DECLARATION.normalizer_version} <= set(declared_normalizer_versions())
