"""Packaged workflow graphs, and the loader that reads them.

Packaged data is one directory per graph, each holding its own ``graph.yaml`` plus its own ``prompts/``
— never shared across graphs, since two can name the same prompt filename with different content. This
module is the *loader*: the edge that reads YAML and inlines prompt *file* references before the pure
parser and validator run, deliberately outside the domain, which touches neither (``bzh:domain-core``)."""

from __future__ import annotations

from pathlib import Path

import yaml

from blizzard.hub.domain.graph import GraphDoc

_GRAPHS_DIR = Path(__file__).resolve().parent
DEFAULT_GRAPH_PATH = _GRAPHS_DIR / "default" / "graph.yaml"

# The prompt-carrying fields whose file references are inlined at load.
_PROMPT_KEYS = ("prompt", "prompt_addendum")


def default_graph_yaml() -> str:
    """The raw default-graph YAML text (the ``POST /graphs`` body, un-inlined)."""
    return DEFAULT_GRAPH_PATH.read_text()


def packaged_graph_paths() -> list[Path]:
    """Every packaged graph's ``graph.yaml``, sorted by directory name (issue #146) so a report over
    them reads the same way twice. The filename is the membership test, not a name blocklist that would
    need maintaining: a directory carrying no ``graph.yaml`` is skipped by construction."""
    return sorted(_GRAPHS_DIR.glob("*/graph.yaml"), key=lambda path: path.parent.name)


def load_graph_doc(path: Path) -> GraphDoc:
    """Load a graph definition file, inline its prompt references, and parse it.

    Inlining resolves every ``prompt`` / ``judgement.prompt`` / ``prompt_addendum`` file reference
    relative to ``path`` and substitutes the file's text, so the parsed :class:`GraphDoc` carries prose,
    never paths. A missing referenced file raises :class:`FileNotFoundError`."""
    return GraphDoc.of(_load_and_inline(path))


def load_default_graph_doc() -> GraphDoc:
    """Load and parse the packaged default graph."""
    return load_graph_doc(DEFAULT_GRAPH_PATH)


def inline_graph_yaml(path: Path) -> str:
    """Load a graph definition file, inline its prompt references, and re-serialize.

    Same inlining as :func:`load_graph_doc`, but returns YAML **text** rather than a parsed
    :class:`GraphDoc` — what a mint taking raw ``definition_yaml``, which resolves no file references
    of its own, needs (issue #123)."""
    return yaml.safe_dump(_load_and_inline(path), sort_keys=False)


def _load_and_inline(path: Path) -> dict[str, object]:
    """Read ``path`` as a graph-definition mapping with prompt refs inlined, in place."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path} is not a graph-definition mapping")
    _inline_prompts(raw, path.parent)
    return raw


def _inline_prompts(node: object, base: Path) -> None:
    """Recursively replace prompt file references with their text, in place."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _PROMPT_KEYS and isinstance(value, str) and _looks_like_ref(value):
                node[key] = (base / value).read_text()
            else:
                _inline_prompts(value, base)
    elif isinstance(node, list):
        for item in node:
            _inline_prompts(item, base)


def _looks_like_ref(value: str) -> bool:
    """A prompt value is a file reference (path), not already-inlined prose."""
    return "\n" not in value and (value.startswith("./") or value.startswith("../") or value.endswith(".md"))
