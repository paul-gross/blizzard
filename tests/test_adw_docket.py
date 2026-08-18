"""The adv-dwf graph's `docket` artifact and its prompts' pointer to it.

A prompt restating a slice of the findings-docket format carries one sentence naming the
retrieval command; the site set is a vocabulary match against raw prompt text, so an
omission reds rather than passes vacuously. Only the pointer's presence is asserted — keeping
a restatement true to the docket is a maintainer's obligation, stated for them in `graph.yaml`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml as yaml_lib

from blizzard.hub.graphs import PACKAGED

pytestmark = pytest.mark.unit

_GRAPH_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "blizzard" / "hub" / "graphs" / "advanced-development-workflow"
)
_PROMPTS_DIR = _GRAPH_DIR / "prompts"
_RETRIEVAL_COMMAND = "blizzard runner artifact get docket --scope graph"

# Phrases a prompt uses only when restating the docket's format mechanics — the entry
# fields or the refutation record's rules — as opposed to merely naming a docket concept
# in passing. Matched against normalized prompt text, so the census below collects itself
# rather than tracking filenames by hand.
_DOCKET_VOCAB = re.compile(
    r"copied verbatim|entire record|restarts? at `F1`",
    re.IGNORECASE,
)


def _docket_vocabulary_prompts() -> list[Path]:
    return sorted(
        (p for p in _PROMPTS_DIR.glob("*.md") if _DOCKET_VOCAB.search(_normalized(p.read_text()))),
        key=lambda p: p.name,
    )


def _normalized(text: str) -> str:
    """Whitespace runs collapsed to single spaces, so a phrase is matched against the prose's
    words and not the line breaks between them. The markdown formatter hard-wraps these prompts
    at 120 columns and does split an inline code span, which a literal match would red on."""
    return " ".join(text.split())


def test_the_docket_vocabulary_census_is_exactly_six_files() -> None:
    """Guards the guard: the set is collected by matching text, so a vocabulary that stopped
    matching would shrink it to nothing and every parametrized case below would pass vacuously.
    This enumeration is the census's own home — joining or leaving it is an edit right here."""
    names = {p.name for p in _docket_vocabulary_prompts()}
    assert names == {
        "build.from-review.md",
        "build.md",
        "plan.from-plan-review.md",
        "plan.md",
        "plan-review.md",
        "review.md",
    }


@pytest.mark.parametrize("path", _docket_vocabulary_prompts(), ids=lambda p: p.name)
def test_each_docket_vocabulary_prompt_names_the_retrieval_command(path: Path) -> None:
    """Every prompt restating a slice of the docket format also points a worker at the
    retrievable docket, additively — the restated prose stays, so a worker that never
    runs the command still reads everything it reads today."""
    text = _normalized(path.read_text())
    assert _normalized(_RETRIEVAL_COMMAND) in text, (
        f"{path.name} matched the docket-vocabulary census (it restates part of the findings-docket "
        f"format) but does not name `{_RETRIEVAL_COMMAND}` — every docket-vocabulary prompt must point "
        f"a worker at the full docket."
    )


def test_the_graph_declares_the_docket_as_a_graph_scoped_artifact() -> None:
    """The mint-time declaration: `artifacts: {docket: ./docket.md}` as a top-level
    sibling of `nodes:`/`sessions:`, not a node facet."""
    raw = yaml_lib.safe_load((_GRAPH_DIR / "graph.yaml").read_text())
    assert raw.get("artifacts") == {"docket": "./docket.md"}


def test_the_loader_bakes_the_docket_file_content_verbatim() -> None:
    """`hub graph sync` mints a new adv-dwf graph carrying the docket's text: the same
    inlining path `PACKAGED` exercises reads the declared reference into the graph doc
    the mint compiles from."""
    doc = PACKAGED.named("advanced-development-workflow").doc
    assert doc.artifacts == {"docket": (_GRAPH_DIR / "docket.md").read_text()}
