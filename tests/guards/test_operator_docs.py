"""The operator docs' references resolve (unit tier, blizzard:docs-references).

Two halves. Every relative link under ``docs/`` names a file that exists and, when it
carries a ``#fragment``, a heading in that file. Every ``blizzard hub …`` invocation the
deployment pages show — in an inline code span or a fenced block — walks to a command in
``contracts/cli/hub.json``, and every option it passes is one that command declares. The
CLI contract is itself pinned to the live tree by ``blizzard:cli-contract``, so a page
naming a verb or option the hub does not have goes red here."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.support.repo_files import repo_root

_DOCS = repo_root() / "docs"
_DEPLOYMENT = _DOCS / "deployment"
_HUB_CONTRACT = repo_root() / "contracts" / "cli" / "hub.json"

_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE = re.compile(r"(`+)(.+?)\1")
_INLINE_LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_REFERENCE_DEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+.*)?$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_HTML_ANCHOR = re.compile(r"<a\s+(?:id|name)=\"([^\"]+)\"")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_INVOCATION = re.compile(r"\bblizzard hub\b(.*)")
_SHELL_BREAK = re.compile(r"\s(?:\||&&|\|\||;|>|>>|2>|<)\s|\s#|\$\(|\)")
_ALWAYS_ACCEPTED = {"--help", "-h"}


def _prose_lines(text: str) -> Iterator[tuple[int, str]]:
    """Every line outside a fenced block, with inline code spans blanked out."""
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield number, _INLINE_CODE.sub("", line)


def _code_snippets(text: str) -> Iterator[tuple[int, str, bool]]:
    """Every code snippet: each fenced line (continuations joined) and each inline span.
    The flag is true for an inline span, where ``\\|`` is a markdown-escaped
    alternation bar rather than a shell pipe."""
    fenced = False
    pending = ""
    start = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            if not pending:
                start = number
            if line.rstrip().endswith("\\"):
                pending += line.rstrip()[:-1] + " "
                continue
            yield start, pending + line, False
            pending = ""
        else:
            for match in _INLINE_CODE.finditer(line):
                yield number, match.group(2), True


def _slug(heading: str) -> str:
    """GitHub's heading anchor: markdown stripped, lowercased, punctuation dropped,
    spaces hyphenated."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text).replace("`", "").replace("*", "")
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(text: str) -> set[str]:
    found: set[str] = set()
    seen: dict[str, int] = {}
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        found.update(_HTML_ANCHOR.findall(line))
        heading = _HEADING.match(line)
        if heading:
            slug = _slug(heading.group(1))
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            found.add(slug if count == 0 else f"{slug}-{count}")
    return found


def link_offenders(page: Path) -> list[str]:
    text = page.read_text()
    offenders: list[str] = []
    for number, line in _prose_lines(text):
        targets = [m.group(1) for m in _INLINE_LINK.finditer(line)]
        definition = _REFERENCE_DEF.match(line)
        if definition:
            targets.append(definition.group(1))
        for target in targets:
            if _SCHEME.match(target) or target.startswith("//"):
                continue
            path_part, _, fragment = target.partition("#")
            resolved = (page.parent / path_part).resolve() if path_part else page
            where = f"{_shown(page)}:{number}: {target}"
            if not resolved.exists():
                offenders.append(f"{where} — no such file")
                continue
            if fragment and resolved.suffix == ".md" and fragment not in anchors(resolved.read_text()):
                offenders.append(f"{where} — no heading #{fragment}")
    return offenders


def _shown(page: Path) -> Path:
    return page.relative_to(repo_root()) if page.is_relative_to(repo_root()) else page


def _tokens(invocation: str, inline: bool) -> list[str]:
    if inline:
        invocation = invocation.replace("\\|", " ")
    invocation = _SHELL_BREAK.split(f" {invocation} ", maxsplit=1)[0]
    cleaned = re.sub(r"[\[\](){}|]", " ", invocation)
    return cleaned.split()


def _children(node: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for name, child in node.get("commands", {}).items():
        by_name[name] = child
        for alias in child.get("aliases", []):
            by_name[alias] = child
    return by_name


def _declared_options(node: dict[str, Any]) -> set[str]:
    return {
        opt
        for param in node.get("params", [])
        if param["kind"] == "option"
        for opt in [*param["opts"], *param["secondary_opts"]]
    }


def invocation_offenders(snippet: str, inline: bool, contract: dict[str, Any]) -> list[str]:
    offenders: list[str] = []
    for match in _INVOCATION.finditer(snippet):
        node = contract
        path = ["blizzard", "hub"]
        walking = True
        for token in _tokens(match.group(1), inline):
            if walking and token.startswith("<"):
                # A placeholder where a command goes names a family; the walk can go no further.
                break
            if walking and not token.startswith("-"):
                child = _children(node).get(token)
                if child is not None:
                    node = child
                    path.append(token)
                    continue
                if node.get("commands"):
                    offenders.append(f"`{' '.join(path)}` has no command {token!r}")
                    break
                walking = False
            if token.startswith("-") and not set(token) <= {"-"}:
                option = token.split("=", 1)[0]
                if option not in _ALWAYS_ACCEPTED and option not in _declared_options(node):
                    offenders.append(f"`{' '.join(path)}` declares no option {option!r}")
    return offenders


def _hub_contract() -> dict[str, Any]:
    return json.loads(_HUB_CONTRACT.read_text())


def _pages(root: Path) -> list[Path]:
    return sorted(root.rglob("*.md"))


@pytest.mark.parametrize("page", _pages(_DOCS), ids=lambda p: str(p.relative_to(_DOCS)))
def test_every_relative_link_resolves_to_a_file_and_heading(page: Path) -> None:
    offenders = link_offenders(page)
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize("page", _pages(_DEPLOYMENT), ids=lambda p: str(p.relative_to(_DOCS)))
def test_every_hub_invocation_resolves_in_the_cli_contract(page: Path) -> None:
    contract = _hub_contract()
    offenders = [
        f"{_shown(page)}:{number}: {offender}"
        for number, snippet, inline in _code_snippets(page.read_text())
        for offender in invocation_offenders(snippet, inline, contract)
    ]
    assert not offenders, "\n".join(offenders)


def test_the_scan_covers_the_deployment_pages() -> None:
    """A moved docs tree would otherwise reduce this guard to a green no-op."""
    assert len(_pages(_DEPLOYMENT)) > 10
    assert any(_INVOCATION.search(s) for p in _pages(_DEPLOYMENT) for _, s, _ in _code_snippets(p.read_text()))


# Seeded failures: each defect the guard exists to catch goes red.


def test_a_link_to_a_missing_file_is_an_offender(tmp_path: Path) -> None:
    page = _seeded(tmp_path, "See [gone](./gone.md).\n")
    assert any("no such file" in o for o in link_offenders(page))


def test_a_link_to_a_missing_heading_is_an_offender(tmp_path: Path) -> None:
    (tmp_path / "other.md").write_text("# Other\n\n## The forge token\n")
    page = _seeded(tmp_path, "[ok](./other.md#the-forge-token) and [bad](./other.md#the-forge-tokens).\n")
    offenders = link_offenders(page)
    assert len(offenders) == 1 and "no heading #the-forge-tokens" in offenders[0]


def test_a_same_page_anchor_to_a_missing_heading_is_an_offender(tmp_path: Path) -> None:
    page = _seeded(tmp_path, "# Page\n\n## Real `code` heading\n\n[ok](#real-code-heading) [bad](#unreal)\n")
    offenders = link_offenders(page)
    assert len(offenders) == 1 and "#unreal" in offenders[0]


def test_a_link_inside_code_is_not_checked(tmp_path: Path) -> None:
    page = _seeded(tmp_path, "`[x](./gone.md)`\n\n```\n[y](./gone.md)\n```\n")
    assert link_offenders(page) == []


def test_an_unknown_verb_is_an_offender() -> None:
    offenders = invocation_offenders("blizzard hub repo frobnicate blizzard", True, _hub_contract())
    assert offenders == ["`blizzard hub repo` has no command 'frobnicate'"]


def test_an_unknown_option_is_an_offender() -> None:
    offenders = invocation_offenders(
        "blizzard hub repo create x --owner o --repo r --colour red", True, _hub_contract()
    )
    assert offenders == ["`blizzard hub repo create` declares no option '--colour'"]


def test_a_known_invocation_with_placeholders_and_alternations_is_clean() -> None:
    snippet = "blizzard hub project position <project> <repository> (--after <repository> \\| --first \\| --unranked)"
    assert invocation_offenders(snippet, True, _hub_contract()) == []


def test_a_piped_fenced_invocation_is_walked_from_the_hub_root() -> None:
    contract = _hub_contract()
    assert invocation_offenders("printf '%s' \"$T\" | blizzard hub secret create gh-token", False, contract) == []
    assert invocation_offenders("blizzard hub secret mint gh-token | jq .", False, contract) != []


def _seeded(directory: Path, body: str) -> Path:
    page = directory / "page.md"
    page.write_text(body)
    return page
