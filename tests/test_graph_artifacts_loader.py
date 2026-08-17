"""The graph loader's top-level ``artifacts:`` inlining pass (unit tier).

``hub/graphs/__init__.py`` resolves every ``artifacts:`` entry's file reference to text before
the pure parser ever runs, separately from the prompt tree walk and by a stricter rule: every
entry is a file reference unconditionally, where a prompt value is inlined only when it reads
as a path. A missing referenced file is a load-time error naming the entry and its path."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from blizzard.hub.domain.graph import GraphDoc
from blizzard.hub.domain.graph_validation import Validator
from blizzard.hub.graphs import GraphArtifactFileMissing, GraphFile

pytestmark = pytest.mark.unit

_GRAPH_YAML = """
name: t
entry: build
artifacts:
  docket: ./reference-notes.md
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""

_DOCKET_TEXT = "the docket's own baked prose"

# The entry name and its referenced filename are deliberately unalike: an error message
# that named only the path would otherwise satisfy an assertion looking for the entry.
_DOCKET_FILENAME = "reference-notes.md"


def _write_graph(tmp_path: Path, *, graph_yaml: str = _GRAPH_YAML, docket_text: str | None = _DOCKET_TEXT) -> Path:
    graph_path = tmp_path / "graph.yaml"
    graph_path.write_text(graph_yaml)
    if docket_text is not None:
        (tmp_path / _DOCKET_FILENAME).write_text(docket_text)
    return graph_path


def test_body_inlines_the_artifact_file_reference(tmp_path: Path) -> None:
    graph_path = _write_graph(tmp_path)

    assert GraphFile(graph_path).body["artifacts"] == {"docket": _DOCKET_TEXT}


def test_doc_carries_the_baked_content(tmp_path: Path) -> None:
    graph_path = _write_graph(tmp_path)

    assert GraphFile(graph_path).doc.artifacts == {"docket": _DOCKET_TEXT}


def test_inlined_yaml_carries_the_baked_content_and_no_file_path(tmp_path: Path) -> None:
    graph_path = _write_graph(tmp_path)

    inlined = GraphFile(graph_path).inlined_yaml

    assert _DOCKET_TEXT in inlined
    assert f"./{_DOCKET_FILENAME}" not in inlined


def test_the_inlined_yaml_reparses_to_the_same_baked_content(tmp_path: Path) -> None:
    """The hub never inlines on the mint path, so ``inlined_yaml`` must survive its own
    re-serialization — the parse half of the round trip a raw ``POST /api/graphs`` body relies
    on, whose whole edge is pinned at the component tier."""
    graph_path = _write_graph(tmp_path)
    inlined = GraphFile(graph_path).inlined_yaml

    reparsed = yaml.safe_load(inlined)

    assert reparsed["artifacts"] == {"docket": _DOCKET_TEXT}


def test_inlining_is_what_clears_the_validator_s_uninlined_value_rejection(tmp_path: Path) -> None:
    """The same YAML validates from a file and is rejected without one: the file path
    inlines ``./reference-notes.md`` to prose, where a directory-less definition would leave
    the path in place for the validator to refuse."""
    graph_path = _write_graph(tmp_path)

    assert Validator.of(GraphFile(graph_path).doc).result.ok
    uninlined = Validator.of(GraphDoc.of(yaml.safe_load(_GRAPH_YAML))).result
    assert not uninlined.ok
    assert any("is a file path, not content" in e for e in uninlined.errors)


def test_a_missing_artifact_file_names_the_entry_and_its_unresolved_path(tmp_path: Path) -> None:
    graph_path = _write_graph(tmp_path, docket_text=None)

    with pytest.raises(GraphArtifactFileMissing) as excinfo:
        _ = GraphFile(graph_path).body

    assert excinfo.value.name == "docket"
    assert excinfo.value.path == tmp_path / _DOCKET_FILENAME
    # Both halves, and neither implies the other: the entry's name is nowhere in its path.
    assert "docket" in str(excinfo.value)
    assert str(tmp_path / _DOCKET_FILENAME) in str(excinfo.value)


def test_inline_artifact_text_is_not_accepted_from_a_file_at_all(tmp_path: Path) -> None:
    """Resolving every entry unconditionally means inline text is read as a filename too, so from
    a file it is a load failure rather than content — the opposite of a node ``prompt:``, whose
    prose the path heuristic leaves alone. Inline text is authorable only where no directory is."""
    graph_path = _write_graph(
        tmp_path, graph_yaml=_GRAPH_YAML.replace("docket: ./reference-notes.md", f"docket: {_DOCKET_TEXT}")
    )

    with pytest.raises(GraphArtifactFileMissing) as excinfo:
        _ = GraphFile(graph_path).doc

    assert excinfo.value.name == "docket"
    assert excinfo.value.path == tmp_path / _DOCKET_TEXT


def test_the_error_is_a_value_error_subclass(tmp_path: Path) -> None:
    # Both consuming edges (`graph_sync`'s reconciliation, `hub graph mint <path>`) already
    # catch `ValueError` into a report row / a `ClickException` — this needs no new plumbing.
    graph_path = _write_graph(tmp_path, docket_text=None)

    with pytest.raises(ValueError, match="docket"):
        _ = GraphFile(graph_path).body


def test_an_artifact_named_prompt_is_inlined_by_the_artifacts_pass_not_the_prompt_walk(tmp_path: Path) -> None:
    """The prompt tree walk never descends into ``artifacts:``, so an entry that
    happens to be named ``prompt`` is resolved as a file reference by the artifacts pass
    exactly like every other entry — never mistaken for the node-level ``prompt:`` field."""
    graph_yaml = """
name: t
entry: build
artifacts:
  prompt: ./aside.md
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""
    graph_path = tmp_path / "graph.yaml"
    graph_path.write_text(graph_yaml)
    (tmp_path / "aside.md").write_text("an aside, not a node prompt")

    doc = GraphFile(graph_path).doc

    assert doc.artifacts == {"prompt": "an aside, not a node prompt"}
    build = doc.node("build")
    assert build is not None
    assert build.prompt == "do the work"  # untouched by the artifacts pass


def test_an_entry_left_valueless_fails_the_load_instead_of_baking_a_placeholder(tmp_path: Path) -> None:
    """The likeliest authoring typo: a declared entry whose file reference was never
    written. There is no reference to resolve, so the pass leaves it for the parser, which
    refuses to coerce it rather than bake the literal text ``None`` as the docket."""
    graph_path = _write_graph(tmp_path, graph_yaml=_GRAPH_YAML.replace("docket: ./reference-notes.md", "docket:"))

    with pytest.raises(ValueError, match=r"artifacts\.docket"):
        _ = GraphFile(graph_path).doc


def test_a_graph_with_no_artifacts_block_leaves_body_untouched(tmp_path: Path) -> None:
    graph_yaml = """
name: t
entry: build
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""
    graph_path = tmp_path / "graph.yaml"
    graph_path.write_text(graph_yaml)

    assert "artifacts" not in GraphFile(graph_path).body
