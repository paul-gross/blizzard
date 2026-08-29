"""Packaged system artifacts — blizzard's own read-only documents, published under
``ArtifactScope.SYSTEM`` in a global, slash-bearing namespace. Loaded the way packaged
graphs are (:mod:`blizzard.hub.graphs`): one file per name, its name derived from its path
under the packaged root, resolved fresh on every read — nothing here is cached or synced
to a store (``bzh:system-scope-reads-live``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from blizzard.hub.domain.artifacts import is_valid_system_artifact_name

#: Every packaged document's own extension — stripped to form the artifact's name.
_SUFFIX = ".md"

#: Filenames that never publish despite carrying `_SUFFIX` — directory documentation, not a
#: document blizzard means to serve (the one realistic accident: a `README.md` here).
_NEVER_PUBLISHED = frozenset({"README.md"})


class SystemArtifactNameInvalid(ValueError):
    """A packaged file's derived name fails :func:`is_valid_system_artifact_name` — a
    packaging bug, not a caller error, so it is raised eagerly rather than silently skipped."""

    def __init__(self, name: str, path: Path) -> None:
        super().__init__(f"packaged system artifact at {path} derives an invalid name {name!r}")
        self.name = name
        self.path = path


@dataclass(frozen=True)
class SystemArtifactFile:
    """One packaged system artifact on disk. Re-read on every access; nothing is cached."""

    path: Path
    name: str

    @property
    def text(self) -> str:
        return self.path.read_text()


@dataclass(frozen=True)
class PackagedSystemArtifacts:
    """The system-artifact set shipped in this package — one ``.md`` file per name, named
    by its path relative to ``root`` with the extension stripped, so ``garden/finding-format``
    is authored at ``garden/finding-format.md``."""

    root: Path

    @property
    def paths(self) -> list[Path]:
        """Every packaged document's path, sorted so a report over them reads the same way
        twice (mirrors ``PackagedGraphs.paths``) — excluding ``_NEVER_PUBLISHED`` names."""
        return sorted(p for p in self.root.rglob(f"*{_SUFFIX}") if p.name not in _NEVER_PUBLISHED)

    @property
    def files(self) -> list[SystemArtifactFile]:
        files = []
        for path in self.paths:
            name = path.relative_to(self.root).with_suffix("").as_posix()
            if not is_valid_system_artifact_name(name):
                raise SystemArtifactNameInvalid(name, path)
            files.append(SystemArtifactFile(path=path, name=name))
        return files

    def named(self, name: str) -> SystemArtifactFile | None:
        return next((f for f in self.files if f.name == name), None)


PACKAGED = PackagedSystemArtifacts(Path(__file__).resolve().parent)
