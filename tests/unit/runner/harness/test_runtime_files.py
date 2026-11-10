"""Each section kind's ``runtime_files`` names exactly the files its family's ``scaffold_runtime`` writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.harness.declaration import IHarnessSectionKind
from blizzard.runner.harness.wiring import HARNESS_RUNTIME_FILES, HARNESS_SECTION_KINDS, harness_families


def _written(root: Path) -> set[str]:
    return {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("kind", HARNESS_SECTION_KINDS, ids=[kind.harness_id for kind in HARNESS_SECTION_KINDS])
def test_a_section_kind_names_the_files_its_family_scaffolds(tmp_path: Path, kind: IHarnessSectionKind) -> None:
    for declaration in harness_families()[kind.harness_id]:
        declaration.scaffold_runtime(kind.scaffold(tmp_path, {}), tmp_path, worker_shell="sh")
    assert {file.path for file in kind.runtime_files} == _written(tmp_path)


def test_the_host_roster_is_every_kinds_files_in_catalog_order() -> None:
    assert [path for path, _ in HARNESS_RUNTIME_FILES] == [
        file.path for kind in HARNESS_SECTION_KINDS for file in kind.runtime_files
    ]
