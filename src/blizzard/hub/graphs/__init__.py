"""Packaged workflow graphs — the hub-configured default graph (D-081).

The hub ships a default graph every ingested chunk is pinned to (D-033/D-081). It
lives here as packaged data (``default.yaml`` plus its prompt files) so a fresh hub
mints it at init without any authoring. This module is the *loader* — the edge that
reads YAML and inlines prompt *file* references (D-033) before the pure-domain
parser and validator run; it is deliberately outside the domain (it touches the
filesystem and PyYAML), which the domain must not (``bzh:domain-core``).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from blizzard.hub.domain.graph import GraphDoc, parse_graph_doc

_GRAPHS_DIR = Path(__file__).resolve().parent
DEFAULT_GRAPH_PATH = _GRAPHS_DIR / "default.yaml"

# The prompt-carrying fields whose file references are inlined at load (D-033).
_PROMPT_KEYS = ("prompt", "prompt_addendum")


def default_graph_yaml() -> str:
    """The raw default-graph YAML text (the ``POST /graphs`` body, un-inlined)."""
    return DEFAULT_GRAPH_PATH.read_text()


def load_graph_doc(path: Path) -> GraphDoc:
    """Load a graph definition file, inline its prompt references, and parse it.

    Inlining resolves every ``prompt`` / ``judgement.prompt`` / ``prompt_addendum``
    file reference relative to ``path`` and replaces it with the file's text (D-033),
    so the parsed :class:`GraphDoc` carries prose, never paths — exactly what a mint
    persists. A missing referenced file raises :class:`FileNotFoundError`.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path} is not a graph-definition mapping")
    _inline_prompts(raw, path.parent)
    return parse_graph_doc(raw)


def load_default_graph_doc() -> GraphDoc:
    """Load and parse the packaged default graph (D-081)."""
    return load_graph_doc(DEFAULT_GRAPH_PATH)


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
