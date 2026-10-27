"""The repo-path pointer gate: every repo-root path a Python file's prose names
resolves to a tracked target — `blizzard-context:/standards/one-prose-home.md`.

check    exit 1 on an unresolved, unexempted pointer or a stale exemption, 2 when
         git, a scanned file, or the exemption file cannot be read.

Prose is what `scripts/prose_spans.py` extracts from tracked `.py` files under
`_SCOPE` — docstrings and full-line comments — plus trailing inline comments. A
pointer is a token rooted at one of `_ROOTS`, the local roots
`styles/Blizzard/ShortFormPointer.yml` names; a token preceded by `:`, `/`, `.` or a
word character is notation, a URL, or another repo's path, and is not checked. The
tracked set is this checkout's `git ls-files`, never the working tree, so a file on
disk that git does not track satisfies nothing.

`scripts/repo-pointers-exemptions.json` lists the reviewed exceptions, one entry per
`file` + `symbol` + `target` site with its exact occurrence `count`; an occurrence
beyond it fails, and an entry nothing matches any more fails as stale.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import prose_spans

REPO_ROOT = Path(__file__).resolve().parent.parent
EXEMPTIONS = Path(__file__).with_name("repo-pointers-exemptions.json")

_SCOPE = ("src/", "tests/", "scripts/", "exemplars/python/")
_ROOTS = (
    "src",
    "tests",
    "scripts",
    "docs",
    "web",
    "exemplars",
    "contracts",
    "openapi",
    "packaging",
    "mutants",
    "dist",
    "styles",
)
_KINDS = {"illustration", "generated", "debt"}
_ENTRY_KEYS = {"file", "symbol", "target", "count", "kind", "reason"}

_TOKEN_RE = re.compile(rf"(?<![\w:/.-])(?:{'|'.join(_ROOTS)})/[^\s`'\"()\[\]]*")
_LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")
_SYMBOL_SUFFIX_RE = re.compile(r"^(.*\.(?:py|ts|md|json|toml|ya?ml|sh|sql|txt))\.[A-Za-z_][\w.]*$")
_GLOB_CHARS = ("*", "?", "{", "<")

SiteKey = tuple[str, str, str]


class PointerCheckError(Exception):
    """Git, a scanned file, or the exemption file could not be read — not a finding."""


@dataclass(frozen=True)
class Pointer:
    file: str
    symbol: str
    line: int
    target: str


@dataclass(frozen=True)
class Tracked:
    files: frozenset[str]
    dirs: frozenset[str]

    @classmethod
    def of(cls, paths: list[str]) -> Tracked:
        dirs = {"/".join(p.split("/")[:i]) for p in paths for i in range(1, p.count("/") + 1)}
        return cls(frozenset(paths), frozenset(dirs))


def tracked_paths(root: Path) -> list[str]:
    try:
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False)
    except OSError as exc:
        raise PointerCheckError(f"{root}: git ls-files failed: {exc}") from exc
    if result.returncode != 0:
        raise PointerCheckError(f"{root}: git ls-files failed: {result.stderr.decode(errors='replace').strip()}")
    return [p for p in result.stdout.decode().split("\0") if p]


def normalize(token: str) -> str:
    """Strip a token's trailing punctuation and its `#fragment`, `::symbol`,
    `:line[-line]` and `.symbol` suffixes, leaving the path it points at."""
    token = token.split("#", 1)[0].split("::", 1)[0]
    token = token.rstrip(".,;!?'\"")
    token = _LINE_SUFFIX_RE.sub("", token)
    token = token.split(":", 1)[0].rstrip(".,;!?")
    head, sep, last = token.rpartition("/")
    m = _SYMBOL_SUFFIX_RE.match(last)
    return f"{head}{sep}{m.group(1)}" if m else token


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "{" and "}" in pattern[i:]:
            end = pattern.index("}", i)
            out.append("(?:" + "|".join(re.escape(alt) for alt in pattern[i + 1 : end].split(",")) + ")")
            i = end + 1
        elif c == "<" and ">" in pattern[i:]:
            out.append("[^/]+")
            i = pattern.index(">", i) + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out))


def resolves(target: str, tracked: Tracked) -> bool:
    path = target.rstrip("/")
    if any(c in target for c in _GLOB_CHARS):
        rx = _glob_regex(path)
        pool = tracked.dirs if target.endswith("/") else tracked.files | tracked.dirs
        return any(rx.fullmatch(p) for p in pool)
    if target.endswith("/"):
        return path in tracked.dirs
    return path in tracked.files or path in tracked.dirs


def pointers(root: Path, files: list[str]) -> list[Pointer]:
    found: list[Pointer] = []
    for rel in files:
        path = root / rel
        spans, skipped = prose_spans.extract_spans_result(path, inline_comments=True)
        if skipped:
            raise PointerCheckError(f"{rel}: unreadable or unparseable")
        for span in spans:
            for m in _TOKEN_RE.finditer(span.text):
                line = span.start_line + span.text.count("\n", 0, m.start())
                found.append(Pointer(rel, span.symbol, line, normalize(m.group(0))))
    return found


def load_exemptions(path: Path) -> dict[SiteKey, int]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PointerCheckError(f"{path}: {exc}") from exc
    if not isinstance(entries, list):
        raise PointerCheckError(f"{path}: expected a list of entries")
    exempt: dict[SiteKey, int] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            raise PointerCheckError(f"{path}: entry must carry exactly {sorted(_ENTRY_KEYS)}: {entry}")
        key = (entry["file"], entry["symbol"], entry["target"])
        if entry["kind"] not in _KINDS:
            raise PointerCheckError(f"{path}: {key}: kind must be one of {sorted(_KINDS)}")
        if not (isinstance(entry["count"], int) and entry["count"] >= 1):
            raise PointerCheckError(f"{path}: {key}: count must be a positive integer")
        if not (isinstance(entry["reason"], str) and entry["reason"].strip()):
            raise PointerCheckError(f"{path}: {key}: reason must be non-empty")
        if key in exempt:
            raise PointerCheckError(f"{path}: {key}: duplicate entry")
        exempt[key] = entry["count"]
    return exempt


def check(root: Path, exemptions: Path) -> list[str]:
    """Every problem found, one line each; empty when the tree is clean."""
    exempt = load_exemptions(exemptions)
    paths = tracked_paths(root)
    tracked = Tracked.of(paths)
    scanned = sorted(p for p in paths if p.endswith(".py") and p.startswith(_SCOPE))
    unresolved = [p for p in pointers(root, scanned) if not resolves(p.target, tracked)]

    counts = Counter((p.file, p.symbol, p.target) for p in unresolved)
    problems: list[str] = []
    for p in unresolved:
        key = (p.file, p.symbol, p.target)
        if key not in exempt:
            problems.append(f"{p.file}:{p.line}: {p.symbol}: '{p.target}' is not a tracked path")
    for key, observed in sorted(counts.items()):
        if key in exempt and observed > exempt[key]:
            problems.append(
                f"{key[0]}: {key[1]}: '{key[2]}' occurs {observed} times, over its exemption's {exempt[key]}"
            )
    for key, allowed in sorted(exempt.items()):
        observed = counts.get(key, 0)
        if observed < allowed:
            problems.append(
                f"{key[0]}: {key[1]}: '{key[2]}' exemption is stale — {observed} occurrences, entry says {allowed}"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check"])
    parser.parse_args(argv)
    try:
        problems = check(REPO_ROOT, EXEMPTIONS)
    except PointerCheckError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for problem in problems:
        print(problem)
    if problems:
        print(f"\n{len(problems)} pointer problem(s).", file=sys.stderr)
        return 1
    print("OK: every repo-root pointer in Python prose resolves to a tracked path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
