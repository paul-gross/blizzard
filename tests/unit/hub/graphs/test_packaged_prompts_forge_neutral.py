"""Packaged graph prompts state forge operations as obligations, not GitHub commands (unit tier).

`bzh:app-agnostic-graphs` leaves forge means to the repository's or workspace's configured
forge tools. Two claims: no packaged node prompt surface names a GitHub CLI invocation, and
the CI-retry recovery prompts — collected by their flake vocabulary and wait-then-retry instruction — are exactly the
three known sites, each still carrying every recovery obligation and its outcome. Sibling to
``tests/unit/hub/graphs/test_packaged_prompts_no_hub_verb.py``."""

from __future__ import annotations

import re

import pytest

from blizzard.hub.domain.graph.model import GraphDoc, NodeDoc
from blizzard.hub.graphs import PACKAGED

_GH_INVOCATION = re.compile(r"\bgh\s+[a-z]")
_RETRY_VOCABULARY = re.compile(r"\bflak(?:e|y)\b")
_RETRY_ACTION = re.compile(r"\bwait\b.*\bretry\b")

_PRE_RELEASE = "prompts/pre-release.from-deliver.md"
_EXPECTED_SITES = {
    f"basic-development-workflow/{_PRE_RELEASE}",
    f"basic-harness-workflow/{_PRE_RELEASE}",
    "advanced-development-workflow/prompts/resolve.md",
}

# Decision-level obligations every recovery site carries, as loose keyword matches.
_OBLIGATIONS = {
    "reads logs": re.compile(r"\blogs?\b"),
    "compares with base and other changes": re.compile(r"base[- ]branch.*other (?:changes|prs)"),
    "waits for the run or pipeline": re.compile(r"\bwait\b.*\b(?:run|pipeline)\b.*\bcomplete"),
    "retries once": re.compile(r"\bretry\b.*\bonce\b"),
    "reports the gap": re.compile(r"\bgap\b"),
    "never claims unperformed work": re.compile(r"\bnever claim\b"),
}
_OUTCOMES = {
    _PRE_RELEASE: [r"\bsignificant\b", r"`pre-release-summary`"],
    "prompts/resolve.md": [r"`resolve-report`", r"`broken`", r"case 2"],
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def _node_prompt_text(node: NodeDoc) -> str:
    """The node's full inlined prompt surface — main prompt, judgement prompt, and every
    choice's `prompt_addendum`."""
    parts = [node.prompt or ""]
    if node.judgement is not None:
        parts.append(node.judgement.prompt or "")
        for choice in node.judgement.choices:
            parts.append(choice.prompt_addendum or "")
    return "\n".join(parts)


def _packaged_graphs() -> list[tuple[str, GraphDoc]]:
    return [(f.path.parent.name, f.doc) for f in PACKAGED.files]


@pytest.mark.parametrize(
    ("graph_name", "doc"),
    [pytest.param(g, doc, id=g) for g, doc in _packaged_graphs()],
)
def test_no_packaged_prompt_names_a_github_cli_invocation(graph_name: str, doc: GraphDoc) -> None:
    for node in doc.nodes:
        match = _GH_INVOCATION.search(_node_prompt_text(node))
        assert match is None, (
            f"{graph_name}: node {node.name!r}'s prompt names a GitHub CLI invocation ({match.group(0)!r}) — "
            f"state the forge operation as an obligation and leave the means to the configured forge tools."
        )


def _recovery_sites() -> dict[str, str]:
    sites: dict[str, str] = {}
    for path in sorted(PACKAGED.root.glob("*/prompts/*.md")):
        text = _norm(path.read_text(encoding="utf-8"))
        if _RETRY_VOCABULARY.search(text) and _RETRY_ACTION.search(text):
            sites[path.relative_to(PACKAGED.root).as_posix()] = text
    return sites


def test_ci_retry_recovery_census_is_the_three_known_sites() -> None:
    """A guard on the guard: the census matches exactly the known prompts, so the per-site
    checks below cannot pass vacuously and a new site must be brought under them."""
    assert set(_recovery_sites()) == _EXPECTED_SITES


@pytest.mark.parametrize("site", sorted(_EXPECTED_SITES))
def test_recovery_site_carries_every_obligation_and_outcome(site: str) -> None:
    text = _recovery_sites()[site]
    missing = [name for name, pattern in _OBLIGATIONS.items() if not pattern.search(text)]
    assert not missing, f"{site}: recovery obligation(s) missing: {missing}"
    outcomes = next(v for k, v in _OUTCOMES.items() if site.endswith(k))
    absent = [o for o in outcomes if not re.search(o, text)]
    assert not absent, f"{site}: outcome token(s) missing: {absent}"
