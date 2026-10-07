"""The filesystem driver behind ``ICompatibilityCorpus``: the committed ``contracts/`` tree."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import collaborator
from blizzard.runner.harness.offline_compatibility import ICompatibilityCorpus

# Package-relative, not repo-root-relative: the wheel ships only `src/blizzard`
# (`pyproject.toml`'s `packages`), so the corpus lives under `harness/contracts` and is
# found the same way in a checkout and an installed wheel alike.
DEFAULT_CORPUS_ROOT = Path(__file__).resolve().parent.parent / "contracts"


@collaborator
@dataclass(frozen=True)
class CommittedCorpus:
    """``<root>/<harness_id>/<version>/manifest.json`` on disk; ``root`` defaults to this
    package's own ``contracts/`` tree."""

    root: Path = DEFAULT_CORPUS_ROOT

    def versions(self, harness_id: str) -> tuple[str, ...]:
        harness_dir = self.root / harness_id
        if not harness_dir.is_dir():
            return ()
        return tuple(
            child.name for child in harness_dir.iterdir() if child.is_dir() and (child / "manifest.json").is_file()
        )

    def manifest(self, harness_id: str, version: str) -> Mapping[str, object] | None:
        try:
            manifest = json.loads((self.root / harness_id / version / "manifest.json").read_text())
        except (OSError, ValueError):
            return None
        return manifest if isinstance(manifest, dict) else None


def _conforms_compatibility_corpus(x: CommittedCorpus) -> ICompatibilityCorpus:
    return x
