"""Packaged workflow graphs, and the loader that reads them.

Packaged data is one directory per graph — its own ``graph.yaml`` plus its own ``prompts/``, never shared, since two
can name the same prompt filename with different content. This module is the *loader*: the edge that decodes a
definition file through the config codec and inlines prompt *file* references, outside the domain
(``bzh:domain-core``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from blizzard.hub.documents.codec import YAML_CODEC, accepted_extensions, codec_for_path
from blizzard.hub.domain.graph import GraphDoc

# The prompt-carrying fields whose file references are inlined at load.
_PROMPT_KEYS = ("prompt", "prompt_addendum")

# The top-level key `Inliner`'s tree walk never descends into — `artifacts:` is inlined by
# its own pass below instead, popped before the walk runs.
_TOP_LEVEL_ARTIFACTS_KEY = "artifacts"


class GraphArtifactFileMissing(ValueError):
    """A graph's ``artifacts:`` entry names a file that does not resolve — naming the
    entry and the path it failed to resolve to."""

    def __init__(self, name: str, path: Path) -> None:
        super().__init__(f"artifact `{name}`: no file at `{path}`")
        self.name = name
        self.path = path


@dataclass(frozen=True)
class ArtifactInliner:
    """The graph-scoped ``artifacts:`` pass: every entry is always a file reference (no
    ``is_ref`` heuristic; a typo becomes a load-time error, never a literal-content
    artifact). Runs separately from — and before mint-time validation ever sees — the
    prompt tree walk, over one directory."""

    base: Path

    def inline(self, raw: object) -> object:
        """Resolve every entry's file reference to its text; a shape that is not a mapping
        is left untouched — that malformation is ``GraphDoc.of``'s to report, not the
        loader's (``bzh:domain-core``)."""
        if not isinstance(raw, dict):
            return raw
        inlined: dict[str, object] = {}
        for name, value in raw.items():
            if not isinstance(value, str):
                inlined[name] = value
                continue
            path = self.base / value
            try:
                inlined[name] = path.read_text()
            except OSError as exc:
                raise GraphArtifactFileMissing(str(name), path) from exc
        return inlined


@dataclass(frozen=True)
class Inliner:
    """Prompt file references resolved against one directory and substituted in place."""

    base: Path

    def inline(self, node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _PROMPT_KEYS and isinstance(value, str) and self.is_ref(value):
                    node[key] = (self.base / value).read_text()
                else:
                    self.inline(value)
        elif isinstance(node, list):
            for item in node:
                self.inline(item)

    @staticmethod
    def is_ref(value: str) -> bool:
        """A prompt value is a file reference (path), not already-inlined prose."""
        return "\n" not in value and (value.startswith("./") or value.startswith("../") or value.endswith(".md"))


@dataclass(frozen=True)
class GraphFile:
    """One graph definition file on disk. Every read below re-reads it; nothing is cached."""

    path: Path

    @property
    def text(self) -> str:
        """The raw definition text (the ``POST /graphs`` body, un-inlined)."""
        return self.path.read_text()

    @property
    def body(self) -> dict[str, object]:  # ast-grep-ignore: bzh:property-delegates
        """The definition mapping, with every prompt reference and every top-level
        ``artifacts:`` entry replaced by its referenced file's text, resolved relative to
        :attr:`path`. A missing prompt file raises :class:`FileNotFoundError`; a missing
        ``artifacts:`` file raises :class:`GraphArtifactFileMissing`, naming the entry."""
        codec = codec_for_path(self.path)
        if codec is None:
            raise ValueError(f"{self.path}: unsupported extension; expected one of {', '.join(accepted_extensions())}")
        raw = codec.decode(self.path.read_bytes())
        # Popped before the prompt walk runs, so it never descends into `artifacts:`.
        artifacts = raw.pop(_TOP_LEVEL_ARTIFACTS_KEY, None)
        Inliner(self.path.parent).inline(raw)
        if artifacts is not None:
            raw[_TOP_LEVEL_ARTIFACTS_KEY] = ArtifactInliner(self.path.parent).inline(artifacts)
        return raw

    @property
    def doc(self) -> GraphDoc:
        return GraphDoc.of(self.body)

    @property
    def inlined_yaml(self) -> str:
        """:attr:`body` re-serialized as YAML — what a mint taking raw ``definition_yaml``, which resolves no
        file references of its own, needs."""
        return YAML_CODEC.encode(self.body).decode("utf-8")


@dataclass(frozen=True)
class PackagedGraphs:
    """The graph set shipped in this package — one directory per graph."""

    root: Path

    def named(self, name: str) -> GraphFile:
        return GraphFile(self.root / name / "graph.yaml")

    @property
    def default(self) -> GraphFile:
        return self.named("default")

    @property
    def paths(self) -> list[Path]:
        """Every packaged graph's ``graph.yaml``, sorted by directory name so a report over
        them reads the same way twice. The filename is the membership test, not a name blocklist that would
        need maintaining: a directory carrying no ``graph.yaml`` is skipped by construction."""
        return sorted(self.root.glob("*/graph.yaml"), key=lambda path: path.parent.name)

    @property
    def files(self) -> list[GraphFile]:
        return [GraphFile(path) for path in self.paths]


PACKAGED = PackagedGraphs(Path(__file__).resolve().parent)
