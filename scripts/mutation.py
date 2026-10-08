"""Scoped, resumable mutation testing over `src/blizzard` — `mise run mutation <scope>`.

Mutates only the source a garden scope names, runs only the unit tests import
analysis selects for that scope, and writes `mutants/report.json`. mutmut itself lives in
the `mutation` dependency group and its own project environment (`mise.toml`), never in
`.venv`, so this module only imports it inside the functions that actually drive a run —
importing it at module scope would make it a hard dependency of every test that imports
this file, including the ones that run in the default environment.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_ROOT = REPO_ROOT / "tests"
MUTANTS_DIR = REPO_ROOT / "mutants"
SCOPE_MARKER_NAME = ".scope"
TEST_SELECTION_NAME = ".test-selection.json"
REPORT_NAME = "report.json"
DELTA_REPORT_NAME = "delta-report.json"
# The delta mode's wall-clock budget, preparation included; `--budget` bounds execution only.
DEFAULT_DELTA_BUDGET_SECONDS = 1800.0
# How much of a failed scope run's output the aggregate report keeps as its reason.
FAILURE_TAIL_LINES = 12

# Distinct from mutmut's own exit codes (it never exits non-zero for survivors) — this is
# the caller-facing signal that the run stopped on --budget and should be re-invoked.
BUDGET_EXIT_CODE = 3
# An unresolvable --since revision: the caller asked for a narrowed run this cannot give, and
# it never widens to a full one.
BAD_REVISION_EXIT_CODE = 2
# The assertion mutmut raises when the mutant names it was given match no mutant.
NO_MATCHING_MUTANTS_MESSAGE = "Filtered for specific mutants, but nothing matches"


def _load_module_by_path(name: str, path: Path) -> ModuleType:
    """Load a module by file path under a private name, never the real dotted package.

    mutmut later runs pytest against mutants/tests/, in the same process, importing
    modules under the real `tests.*` names. Reaching tests/repo_files.py through a
    real `import tests...` here would cache `sys.modules["tests"]` against this repo's
    tests/ first, and pytest's conftest loader raises ImportPathMismatchError the moment
    it tries to load mutants/tests/conftest.py under that already-bound name.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Scope:
    """One garden-scope row: what it mutates and which tests cover it."""

    slug: str
    only_mutate: tuple[str, ...]
    do_not_mutate: tuple[str, ...] = ()
    test_selection_packages: tuple[str, ...] = ()


# Grounds follow garden/architecture.md's scope table (blizzard-context), minus web-suite
# (the Angular workspace is out of scope) — cli-surface owns the CLI packages, hub-daemon
# and runner-daemon own the rest of hub/ and runner/, shared-spine owns the daemon-neutral
# layer including the top-level package `__init__.py`.
SCOPES: dict[str, Scope] = {
    "hub-daemon": Scope(
        slug="hub-daemon",
        only_mutate=("src/blizzard/hub/*",),
        do_not_mutate=("src/blizzard/hub/cli/*",),
        test_selection_packages=("blizzard.hub",),
    ),
    "runner-daemon": Scope(
        slug="runner-daemon",
        only_mutate=("src/blizzard/runner/*",),
        do_not_mutate=("src/blizzard/runner/cli/*",),
        test_selection_packages=("blizzard.runner",),
    ),
    "shared-spine": Scope(
        slug="shared-spine",
        only_mutate=(
            "src/blizzard/foundation/*",
            "src/blizzard/wire/*",
            "src/blizzard/auth_core/*",
            "src/blizzard/__init__.py",
        ),
        test_selection_packages=("blizzard.foundation", "blizzard.wire", "blizzard.auth_core"),
    ),
    "cli-surface": Scope(
        slug="cli-surface",
        only_mutate=(
            "src/blizzard/cli/*",
            "src/blizzard/hub/cli/*",
            "src/blizzard/runner/cli/*",
        ),
        test_selection_packages=("blizzard.cli", "blizzard.hub.cli", "blizzard.runner.cli"),
    ),
}

_MUTMUT_PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mutmut"]
# Applied on top of every scope, so a scope row never has to restate the committed exclusions.
PYPROJECT_DO_NOT_MUTATE: tuple[str, ...] = tuple(_MUTMUT_PYPROJECT["do_not_mutate"])
# Copied into a sandbox and run by a bare interpreter, where mutmut's trampoline import cannot resolve.
STANDALONE_SCRIPT_EXCLUSION = "src/blizzard/runner/harness/opencode/compatibility/tool_boundary.py"
GLOBAL_EXCLUSIONS = (*PYPROJECT_DO_NOT_MUTATE, STANDALONE_SCRIPT_EXCLUSION)
# The tier [tool.mutmut].pytest_add_cli_args selects; a test file with nothing in this tier
# would only be deselected, so selection leaves it out of the fingerprint mutmut keys on.
MUTATION_TIER_MARKER = "unit"
TOOLS_PREFIX = "src/blizzard/tools/"


class UnknownScopeError(ValueError):
    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.valid_slugs = sorted(SCOPES)
        super().__init__(f"Unknown mutation scope {slug!r}. Valid scopes: {', '.join(self.valid_slugs)}")


def resolve_scope(slug: str) -> Scope:
    try:
        return SCOPES[slug]
    except KeyError:
        raise UnknownScopeError(slug) from None


# --- scope table validation: disjointness + coverage against the real tree ---------------


def mutable_source_paths(root: Path = REPO_ROOT) -> set[Path]:
    """Every `.py` file under `src/blizzard`, as paths relative to `root` (e.g. `src/blizzard/hub/foo.py`)."""
    src = root / "src" / "blizzard"
    return {path.relative_to(root) for path in src.rglob("*.py") if "__pycache__" not in path.parts}


def _matches_any(rel_path: Path, patterns: Iterable[str]) -> bool:
    rel_str = rel_path.as_posix()
    return any(fnmatch.fnmatch(rel_str, pattern) for pattern in patterns)


def _excluded_from_every_scope(rel_path: Path) -> bool:
    rel_str = rel_path.as_posix()
    return rel_str.startswith(TOOLS_PREFIX) or any(fnmatch.fnmatch(rel_str, g) for g in GLOBAL_EXCLUSIONS)


def files_for_scope(scope: Scope, paths: Iterable[Path]) -> set[Path]:
    do_not_mutate = (*GLOBAL_EXCLUSIONS, *scope.do_not_mutate)
    return {path for path in paths if _matches_any(path, scope.only_mutate) and not _matches_any(path, do_not_mutate)}


def validate_scopes(root: Path = REPO_ROOT) -> None:
    """Assert the scope table is disjoint and, together, covers every mutable file.

    Raises AssertionError naming the gap so a new package under `src/blizzard` that falls
    through every scope, or one two scopes both claim, fails loudly instead of silently
    narrowing or duplicating coverage.
    """
    all_paths = mutable_source_paths(root)
    expected = {path for path in all_paths if not _excluded_from_every_scope(path)}

    owner_by_path: dict[Path, str] = {}
    for slug, scope in SCOPES.items():
        for path in files_for_scope(scope, all_paths):
            if path in owner_by_path:
                raise AssertionError(f"{path} is claimed by both {owner_by_path[path]!r} and {slug!r}")
            owner_by_path[path] = slug

    covered = set(owner_by_path)
    missing = expected - covered
    if missing:
        raise AssertionError(f"No scope covers: {sorted(str(p) for p in missing)}")
    extra = covered - expected
    if extra:
        raise AssertionError(f"Scope(s) cover excluded file(s): {sorted(str(p) for p in extra)}")


# --- import-derived test selection --------------------------------------------------------


def _package_dotted_name(directory: Path, tests_root: Path) -> str:
    rel = directory.relative_to(tests_root.parent)
    return ".".join(rel.parts)


def _resolved_import_names(path: Path, tests_root: Path) -> set[str]:
    """Absolute dotted module names `path` imports, resolving relative imports against its own package.

    `from X import Y` records both `X` and `X.Y`, since `Y` may itself be a submodule —
    `from tests import helper` has to reach `tests.helper` to be followed.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _package_dotted_name(path.parent, tests_root)
    package_parts = package.split(".") if package else []

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                module = node.module or ""
            else:
                trim = node.level - 1
                base_parts = package_parts[: len(package_parts) - trim] if trim else package_parts
                module = ".".join([*base_parts, node.module] if node.module else base_parts)
            if module:
                names.add(module)
            names.update(f"{module}.{alias.name}" if module else alias.name for alias in node.names)
    return names


def _marks_tier(path: Path, marker: str) -> bool:
    """True when `path` applies `marker` anywhere — `pytest.mark.<marker>` or an imported `mark.<marker>`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == marker:
            owner = node.value
            if (isinstance(owner, ast.Attribute) and owner.attr == "mark") or (
                isinstance(owner, ast.Name) and owner.id == "mark"
            ):
                return True
    return False


def _selects(imported: set[str], packages: Iterable[str]) -> bool:
    return any(name == pkg or name.startswith(pkg + ".") for name in imported for pkg in packages)


def _resolve_tests_helper(name: str, tests_root: Path) -> Path | None:
    if name != "tests" and not name.startswith("tests."):
        return None
    rel_parts = name.split(".")[1:]
    if not rel_parts:
        return None
    as_module = tests_root.joinpath(*rel_parts).with_suffix(".py")
    if as_module.is_file():
        return as_module
    as_package = tests_root.joinpath(*rel_parts, "__init__.py")
    if as_package.is_file():
        return as_package
    return None


def test_module_selected(
    path: Path,
    packages: Iterable[str],
    tests_root: Path = TESTS_ROOT,
    *,
    _visited: set[Path] | None = None,
) -> bool:
    """True when `path` imports a selecting package, directly or through a tests/ helper module.

    `conftest.py` is never followed as a helper: pytest loads it implicitly for every test
    module, so it carries no scope-selecting signal of its own.
    """
    visited = _visited if _visited is not None else set()
    if path in visited or path.name == "conftest.py":
        return False
    visited.add(path)

    imported = _resolved_import_names(path, tests_root)
    if _selects(imported, packages):
        return True

    for name in imported:
        helper = _resolve_tests_helper(name, tests_root)
        if (
            helper is not None
            and helper.name != "conftest.py"
            and test_module_selected(helper, packages, tests_root, _visited=visited)
        ):
            return True
    return False


def _is_test_file(path: Path) -> bool:
    return path.name != "conftest.py" and (path.name.startswith("test_") or path.name.endswith("_test.py"))


def selected_test_files(scope: Scope, tests_root: Path = TESTS_ROOT, repo_root: Path = REPO_ROOT) -> list[str]:
    return sorted(
        str(path.relative_to(repo_root))
        for path in tests_root.rglob("*.py")
        if "__pycache__" not in path.parts
        and _is_test_file(path)
        and _marks_tier(path, MUTATION_TIER_MARKER)
        and test_module_selected(path, scope.test_selection_packages, tests_root)
    )


# --- scope -> mutmut config override -------------------------------------------------------


@dataclass(frozen=True)
class ConfigOverride:
    only_mutate: list[str]
    do_not_mutate: list[str]
    pytest_add_cli_args_test_selection: list[str]


def config_override_for_scope(scope: Scope, test_selection: list[str]) -> ConfigOverride:
    return ConfigOverride(
        only_mutate=list(scope.only_mutate),
        do_not_mutate=[*GLOBAL_EXCLUSIONS, *scope.do_not_mutate],
        pytest_add_cli_args_test_selection=list(test_selection),
    )


def apply_config_override(mutmut_config: object, override: ConfigOverride) -> None:
    """Install `override` onto a live `mutmut.configuration.Config`.

    mutmut forks every worker from this process after this runs, so the override reaches
    each child through inherited memory — never through a rewritten pyproject.toml.
    """
    mutmut_config.only_mutate = list(override.only_mutate)  # type: ignore[attr-defined]
    mutmut_config.do_not_mutate = list(override.do_not_mutate)  # type: ignore[attr-defined]
    mutmut_config.pytest_add_cli_args_test_selection = list(  # type: ignore[attr-defined]
        override.pytest_add_cli_args_test_selection
    )


# --- the mutants/ tree: one scope at a time -------------------------------------------------


def ensure_mutant_tree_for_scope(
    scope: Scope,
    mutants_dir: Path = MUTANTS_DIR,
    tests_root: Path = TESTS_ROOT,
    repo_root: Path = REPO_ROOT,
    *,
    fresh: bool = False,
) -> list[str]:
    """Prepare `mutants_dir` for `scope` and return the test selection frozen with it.

    A tree built for another scope, or carrying no scope marker at all, is cleared, and so
    is any tree when `fresh`; a repeat of the same scope keeps it — that is what makes
    resume possible. The selection
    is computed once, when the tree is built, and reused on every resume: mutmut
    fingerprints it, and any change to it discards every cached verdict. A frozen test file
    since deleted from the tree is dropped rather than handed to pytest.
    """
    repo_files = _load_module_by_path("_mutation_repo_files", REPO_ROOT / "tests" / "repo_files.py")
    prepare_mutant_tree = repo_files.prepare_mutant_tree

    marker = mutants_dir / SCOPE_MARKER_NAME
    if mutants_dir.exists() and (fresh or not marker.is_file() or marker.read_text().strip() != scope.slug):
        shutil.rmtree(mutants_dir)

    mutants_dir.mkdir(parents=True, exist_ok=True)
    prepare_mutant_tree(mutants_dir)
    marker.write_text(scope.slug)
    discard_unreadable_metas(mutants_dir)

    selection_path = mutants_dir / TEST_SELECTION_NAME
    if selection_path.is_file():
        frozen = json.loads(selection_path.read_text())
        selection = [test_file for test_file in frozen if (repo_root / test_file).is_file()]
    else:
        selection = selected_test_files(scope, tests_root, repo_root)
    selection_path.write_text(json.dumps(selection, indent=1) + "\n")
    return selection


def discard_unreadable_metas(mutants_dir: Path) -> list[Path]:
    """Delete every `.meta` that does not parse, with its mutated source, returning the metas removed.

    mutmut crashes loading a truncated `.meta`, and regenerates a file's mutants only when
    the mutated copy is missing — so removing both re-generates and re-runs that file's
    mutants instead of wedging every later resume.
    """
    removed: list[Path] = []
    for meta_path in sorted(mutants_dir.rglob("*.meta")):
        try:
            json.loads(meta_path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            meta_path.unlink()
            meta_path.with_suffix("").unlink(missing_ok=True)
            removed.append(meta_path)
            print(f"mutation: discarded unreadable {meta_path}; its mutants re-run", file=sys.stderr)
    return removed


def install_atomic_meta_save(mutation_data_class: type) -> None:
    """Make mutmut's per-file `.meta` save atomic.

    mutmut truncates the file and then dumps into it, so an interrupt landing between the
    two — the budget's SIGINT, or a Ctrl-C — leaves an empty `.meta`. This writes through
    mutmut's own serializer to a sibling temp file and renames it over the original, so an
    interrupt leaves either the old content or the new, never neither.
    """
    original_save = mutation_data_class.save
    if getattr(original_save, "_atomic", False):
        return

    def atomic_save(self: object) -> None:
        meta_path = self.meta_path  # type: ignore[attr-defined]
        temp_path = Path(meta_path).with_name(Path(meta_path).name + ".tmp")
        self.meta_path = temp_path  # type: ignore[attr-defined]
        try:
            original_save(self)
        finally:
            self.meta_path = meta_path  # type: ignore[attr-defined]
        os.replace(temp_path, meta_path)

    atomic_save._atomic = True  # type: ignore[attr-defined]
    mutation_data_class.save = atomic_save  # type: ignore[attr-defined]


# --- delta: the functions a revision range changed, as mutant-name globs ----------------------

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_SOURCE_DIFF_ROOT = "src/blizzard"


def changed_line_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Per file, the `(first, last)` new-side lines a zero-context unified diff touches.

    A pure deletion touches no new-side line, so it is recorded as the empty range
    `(after, after - 1)` at the line it followed; `functions_touching` reads that as a
    change to a function that spans `after`.
    """
    ranges: dict[str, list[tuple[int, int]]] = {}
    current: list[tuple[int, int]] | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            current = ranges.setdefault(target[2:], []) if target.startswith("b/") else None
            continue
        header = _HUNK_HEADER.match(line)
        if header is None or current is None:
            continue
        start = int(header.group(1))
        count = int(header.group(2)) if header.group(2) is not None else 1
        current.append((start, start + count - 1) if count else (start, start - 1))
    return ranges


def functions_touching(source: str, ranges: Iterable[tuple[int, int]]) -> list[tuple[str | None, str]]:
    """The `(class or None, function)` pairs mutmut mutates whose lines `ranges` touch.

    mutmut mutates a module-level function and a method of a module-level class, whole; a
    function nested inside either is part of its enclosing one. A decorator line belongs to
    its function.
    """
    tree = ast.parse(source)
    spans: list[tuple[str | None, str, int, int]] = []

    def add(class_name: str | None, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        spans.append((class_name, node.name, first, node.end_lineno or node.lineno))

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            add(None, node)
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    add(node.name, member)

    def touched(first: int, last: int, lo: int, hi: int) -> bool:
        if hi < lo:  # a pure deletion after line `lo`; one at the last line shrank the tail
            return first <= lo <= last
        return lo <= last and first <= hi

    ranges = list(ranges)
    return [(c, n) for c, n, first, last in spans if any(touched(first, last, lo, hi) for lo, hi in ranges)]


def mutant_module_name(rel_path: str) -> str:
    """The dotted module prefix mutmut gives the mutants of `rel_path` (mirrors its `get_mutant_name`)."""
    module = rel_path.removesuffix(".py").replace("/", ".").removeprefix("src.")
    return module.removesuffix(".__init__")


def mutant_name_globs(rel_path: str, functions: Iterable[tuple[str | None, str]]) -> list[str]:
    module = mutant_module_name(rel_path)
    return [
        f"{module}.x{_CLASS_NAME_SEPARATOR}{class_name}{_CLASS_NAME_SEPARATOR}{name}__mutmut_*"
        if class_name
        else f"{module}.x_{name}__mutmut_*"
        for class_name, name in functions
    ]


class UnknownRevisionError(ValueError):
    def __init__(self, revision: str) -> None:
        self.revision = revision
        super().__init__(f"mutation: --since {revision!r} does not name a commit in this repository")


def _git(*args: str, repo_root: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo_root, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def changed_mutant_globs(
    scope: Scope, since: str, repo_root: Path = REPO_ROOT, *, only_files: Sequence[str] | None = None
) -> list[str]:
    """Mutant-name globs for every function in `scope`'s ground whose source differs between `since` and HEAD.

    Raises `UnknownRevisionError` for a `since` that names no commit; it never falls back to the whole scope.
    """
    try:
        _git("rev-parse", "--verify", "--quiet", f"{since}^{{commit}}", repo_root=repo_root)
    except subprocess.CalledProcessError:
        raise UnknownRevisionError(since) from None

    diff_args = ("diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames", since, "HEAD", "--", _SOURCE_DIFF_ROOT)
    diff = _git(*diff_args, repo_root=repo_root)
    ranges = changed_line_ranges(diff)
    candidates = (Path(path) for path in ranges if path.endswith(".py") and (only_files is None or path in only_files))
    ground = files_for_scope(scope, candidates)

    globs: list[str] = []
    for path in sorted(ground):
        source = _git("show", f"HEAD:{path.as_posix()}", repo_root=repo_root)
        globs.extend(mutant_name_globs(path.as_posix(), functions_touching(source, ranges[path.as_posix()])))
    return globs


# --- delta mode: every scope a revision range touches, under one wall-clock budget ---------------


def changed_source_files(since: str, repo_root: Path = REPO_ROOT) -> list[str]:
    """Every file under `src/blizzard` that differs between `since` and HEAD.

    Raises `UnknownRevisionError` for a `since` that names no commit.
    """
    try:
        _git("rev-parse", "--verify", "--quiet", f"{since}^{{commit}}", repo_root=repo_root)
    except subprocess.CalledProcessError:
        raise UnknownRevisionError(since) from None
    names = _git("diff", "--name-only", "--no-renames", since, "HEAD", "--", _SOURCE_DIFF_ROOT, repo_root=repo_root)
    return sorted(line for line in names.splitlines() if line)


def route_changed_files(files: Iterable[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Split changed files into `(scope slug -> its files, files no scope owns)`, scopes in table order."""
    paths = [Path(f) for f in files]
    routed = {slug: sorted(path.as_posix() for path in files_for_scope(scope, paths)) for slug, scope in SCOPES.items()}
    owned = {f for group in routed.values() for f in group}
    return {slug: group for slug, group in routed.items() if group}, sorted(
        path.as_posix() for path in paths if path.as_posix() not in owned
    )


@dataclass(frozen=True)
class ScopeRun:
    """What one child `scripts/mutation.py <scope> --since REV --fresh` run came to."""

    exit_code: int | None  # None: killed at the wall budget
    output_tail: str


ScopeRunner = Callable[[str, str, float], ScopeRun]


def _run_scope_process(slug: str, since: str, timeout_seconds: float) -> ScopeRun:
    """Run one scope in its own process group, killing the group when `timeout_seconds` runs out.

    A fresh process per scope keeps mutmut's process-global state from one scope out of the
    next, and lets the wall budget stop a run in its preparation phase, which an in-process
    timer cannot.
    """
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), slug, "--since", since, "--fresh"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
        # A backgrounded caller hands down SIGINT ignored, which the suite's own process-group
        # tests, and mutmut's budget interrupt, both need live.
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )
    try:
        output, _ = proc.communicate(timeout=max(timeout_seconds, 0.0))
    except subprocess.TimeoutExpired:
        # The group may have exited between the timeout and the kill; its output is still collected.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate()
        return ScopeRun(None, _tail(output))
    return ScopeRun(proc.returncode, _tail(output))


def _tail(output: str | None) -> str:
    return "\n".join((output or "").strip().splitlines()[-FAILURE_TAIL_LINES:])


def run_delta(
    since: str,
    *,
    budget_seconds: float = DEFAULT_DELTA_BUDGET_SECONDS,
    repo_root: Path = REPO_ROOT,
    mutants_dir: Path = MUTANTS_DIR,
    scope_runner: ScopeRunner = _run_scope_process,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Run every scope the changes since `since` touch, one after another, and write the aggregate report.

    Each scope runs fresh. `budget_seconds` covers preparation as well as execution: a scope
    still running at the deadline is killed and reported `over-budget`, and a scope not yet
    started is reported `over-budget` without running. Scopes already finished keep their
    results. The report is always written, whatever the scopes came to.
    Raises `UnknownRevisionError` before anything runs.
    """
    started = clock()
    routed, unscoped = route_changed_files(changed_source_files(since, repo_root))
    mutants_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    for slug, files in routed.items():
        entry: dict = {"scope": slug, "status": "no-changes", "reason": None, "elapsed_seconds": 0.0, "survivors": []}
        entries.append(entry)
        if not changed_mutant_globs(SCOPES[slug], since, repo_root, only_files=files):
            entry["reason"] = "no function in the scope's mutable source changed"
            continue
        remaining = budget_seconds - (clock() - started)
        if remaining <= 0:
            entry.update(status="over-budget", reason="the wall budget was spent before this scope started")
            continue
        (mutants_dir / REPORT_NAME).unlink(missing_ok=True)
        scope_started = clock()
        run = scope_runner(slug, since, remaining)
        entry["elapsed_seconds"] = round(clock() - scope_started, 1)
        _record_scope_run(entry, run, mutants_dir / REPORT_NAME, budget_seconds)

    report = {
        "since": since,
        "budget_seconds": budget_seconds,
        "elapsed_seconds": round(clock() - started, 1),
        "scopes": entries,
        "unscoped_files": unscoped,
    }
    (mutants_dir / DELTA_REPORT_NAME).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def _record_scope_run(entry: dict, run: ScopeRun, report_path: Path, budget_seconds: float) -> None:
    if run.exit_code is None:
        entry.update(status="over-budget", reason=f"still running at the {budget_seconds:g}s wall budget")
        return
    if run.exit_code != 0:
        reason = f"mutation exited {run.exit_code}"
        entry.update(status="failed", reason=f"{reason}: {run.output_tail}" if run.output_tail else reason)
        return
    try:
        scope_report = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError):
        entry.update(status="failed", reason="mutation exited 0 but wrote no readable report")
        return
    entry["status"] = "complete"
    entry["survivors"] = scope_report["survivors"]
    entry["mutants"] = scope_report["total"]


# --- report building: kept import-clean of mutmut, so it runs in the default environment ---

# Mirrors mutmut 3.8.0's mutmut.stats.status_by_exit_code — a small, stable table, and
# the mutation group above pins mutmut exactly, so a version bump is the only thing that
# can move it.
STATUS_BY_EXIT_CODE: dict[int | None, str] = {
    1: "killed",
    3: "killed",
    0: "survived",
    5: "no tests",
    2: "check was interrupted by user",
    None: "not checked",
    33: "no tests",
    34: "skipped",
    35: "suspicious",
    36: "timeout",
    37: "caught by type check",
    -24: "timeout",
    24: "timeout",
    152: "timeout",
    255: "timeout",
    -11: "segfault",
    -9: "segfault",
}
KNOWN_STATUSES = sorted(set(STATUS_BY_EXIT_CODE.values()))

_CLASS_NAME_SEPARATOR = "ǁ"


def _function_and_class_from_mutant_name(mutant_name: str) -> tuple[str, str | None]:
    mangled = mutant_name.partition("__mutmut_")[0]
    _, _, key = mangled.rpartition(".")
    if _CLASS_NAME_SEPARATOR in key:
        class_name = key[key.index(_CLASS_NAME_SEPARATOR) + 1 : key.rindex(_CLASS_NAME_SEPARATOR)]
        func_name = key[key.rindex(_CLASS_NAME_SEPARATOR) + 1 :]
        return func_name, class_name
    return key[2:], None


DiffProvider = Callable[[str, Path], str]


def _real_diff_provider(mutant_name: str, source_path: Path) -> str:
    from mutmut.mutation.diff_apply import get_diff_for_mutant

    return get_diff_for_mutant(mutant_name, path=source_path)


def _in_delta(mutant_name: str, name_globs: list[str] | None) -> bool:
    return name_globs is None or any(fnmatch.fnmatch(mutant_name, glob) for glob in name_globs)


def build_report(
    *,
    scope: str,
    mutants_dir: Path,
    diff_provider: DiffProvider,
    name_globs: list[str] | None = None,
    since: str | None = None,
    complete: bool = True,
) -> dict:
    """Count and list what `mutants_dir` holds, restricted to mutants matching `name_globs` when given.

    `complete` is False when the run stopped on its budget: the counts then carry the
    unchecked mutants under `not checked`, and the survivors are only those found so far.
    """
    counts = dict.fromkeys(KNOWN_STATUSES, 0)
    survivors: list[dict] = []
    total = 0

    for meta_path in sorted(mutants_dir.rglob("*.meta")):
        source_path = Path(meta_path.relative_to(mutants_dir).as_posix()[: -len(".meta")])
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            continue

        for mutant_name, exit_code in meta.get("exit_code_by_key", {}).items():
            if not _in_delta(mutant_name, name_globs):
                continue
            total += 1
            status = STATUS_BY_EXIT_CODE.get(exit_code, "suspicious")
            counts[status] += 1
            if status == "survived":
                function_name, class_name = _function_and_class_from_mutant_name(mutant_name)
                survivors.append(
                    {
                        "mutant": mutant_name,
                        "file": source_path.as_posix(),
                        "function": function_name,
                        "class": class_name,
                        "diff": diff_provider(mutant_name, source_path),
                    }
                )

    return {
        "scope": scope,
        "since": since,
        "complete": complete,
        "total": total,
        "counts": counts,
        "survivors": survivors,
    }


def write_report(
    scope: Scope,
    mutants_dir: Path = MUTANTS_DIR,
    *,
    name_globs: list[str] | None = None,
    since: str | None = None,
    complete: bool = True,
) -> dict:
    report = build_report(
        scope=scope.slug,
        mutants_dir=mutants_dir,
        diff_provider=_real_diff_provider,
        name_globs=name_globs,
        since=since,
        complete=complete,
    )
    (mutants_dir / REPORT_NAME).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


# --- driving mutmut in-process, with a budget bounding only mutant execution ---------------


def _run_with_budget(
    budget_seconds: float | None,
    mutmut_main: ModuleType | None = None,
    mutant_names: tuple[str, ...] = (),
) -> bool:
    """Run mutmut's `run` command in-process. Returns False iff `budget_seconds` expired.

    The budget starts counting only once mutmut starts its mutant workers — never during
    mutant generation, coverage mapping, or the clean and forced-fail test runs, which
    always run to completion. A background timer thread then sends this process SIGINT,
    which mutmut's own mutation loop catches as KeyboardInterrupt and uses to stop workers
    cleanly. A single delivery can go unnoticed for a long time when dozens of freshly
    forked mutant workers are contending for the CPU alongside the timer thread, so once
    the budget expires this keeps resending SIGINT once a second until the run actually
    returns.
    """
    if mutmut_main is None:
        import mutmut.__main__ as mutmut_main

    budget_expired = threading.Event()
    run_finished = threading.Event()
    timer: threading.Timer | None = None
    original_get_mutant_runner = mutmut_main.get_mutant_runner

    def resend_until_finished() -> None:
        while not run_finished.wait(timeout=1.0):
            os.kill(os.getpid(), signal.SIGINT)

    def send_interrupt() -> None:
        budget_expired.set()
        os.kill(os.getpid(), signal.SIGINT)
        nagger = threading.Thread(target=resend_until_finished, daemon=True)
        nagger.start()

    def runner_starting_budget_on_startup(*args: object, **kwargs: object) -> object:
        runner = original_get_mutant_runner(*args, **kwargs)
        original_startup = runner.startup

        def startup_then_start_budget() -> None:
            nonlocal timer
            assert budget_seconds is not None  # installed only when a budget was given
            original_startup()
            timer = threading.Timer(budget_seconds, send_interrupt)
            timer.daemon = True
            timer.start()

        runner.startup = startup_then_start_budget
        return runner

    if budget_seconds is not None:
        mutmut_main.get_mutant_runner = runner_starting_budget_on_startup

    # Once the budget has expired, a resent SIGINT can land outside the mutation loop's own
    # try/except and surface here as KeyboardInterrupt, or inside an in-process pytest run
    # that swallows it and makes mutmut exit(1) — either way, the budget is what stopped it.
    try:
        mutmut_main._run(mutant_names, None)
    except (KeyboardInterrupt, SystemExit):
        if not budget_expired.is_set():
            raise
    except AssertionError as exc:
        # A delta whose changed functions carry no mutant (a decorated body mutmut skips) matches nothing.
        if mutant_names and str(exc).startswith(NO_MATCHING_MUTANTS_MESSAGE):
            return True
        raise
    finally:
        run_finished.set()
        if timer is not None:
            timer.cancel()
        mutmut_main.get_mutant_runner = original_get_mutant_runner

    return not budget_expired.is_set()


def run_scope(
    scope: Scope,
    *,
    test_selection: list[str],
    budget_seconds: float | None,
    mutant_names: tuple[str, ...] = (),
) -> bool:
    import mutmut.__main__ as mutmut_main
    from mutmut.configuration import config
    from mutmut.mutation.data import SourceFileMutationData

    install_atomic_meta_save(SourceFileMutationData)
    cfg = config()
    apply_config_override(cfg, config_override_for_scope(scope, test_selection))

    # A mutant interrupted mid-test (never a killed/survived/timeout verdict) must be
    # retried on resume, the same as one mutmut never got to at all.
    mutmut_main._reset_mutant_results(lambda _key, exit_code: exit_code == 2)

    return _run_with_budget(budget_seconds, mutant_names=mutant_names)


# --- CLI -------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mutation.py",
        description="Scoped, resumable mutation testing over src/blizzard.",
    )
    parser.add_argument(
        "scope",
        nargs="?",
        default=None,
        help=f"One of: {', '.join(sorted(SCOPES))}. Omit it, with --since, to run every scope the delta touches.",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="Seconds to bound mutant execution (not mutant generation, coverage mapping, or the clean-test runs). "
        "On expiry the run stops, writes an incomplete report counting the unchecked mutants, and exits 3; "
        "re-invoke the same scope to resume.",
    )
    parser.add_argument(
        "--since",
        metavar="REV",
        default=None,
        help="Mutate and report only the functions in the scope whose source differs between REV and HEAD. "
        "No changed function gives an empty, complete report; a REV that names no commit exits 2 without running.",
    )
    parser.add_argument(
        "--delta-budget",
        type=float,
        default=None,
        metavar="SECONDS",
        help="With --since and no scope: the wall-clock budget for the whole delta, preparation included "
        f"(default {DEFAULT_DELTA_BUDGET_SECONDS:g}). Distinct from --budget, which bounds one scope's execution.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Discard any existing mutant tree and its verdicts first, so nothing from an earlier run is resumed.",
    )
    return parser.parse_args(argv)


def main_delta(args: argparse.Namespace) -> int:
    if args.since is None:
        print(f"mutation: name a scope ({', '.join(sorted(SCOPES))}) or pass --since REV", file=sys.stderr)
        return 1
    if args.budget is not None:
        print("mutation: --budget bounds one scope's execution; a delta run takes --delta-budget", file=sys.stderr)
        return 1
    os.chdir(REPO_ROOT)
    try:
        budget = DEFAULT_DELTA_BUDGET_SECONDS if args.delta_budget is None else args.delta_budget
        run_delta(args.since, budget_seconds=budget)
    except UnknownRevisionError as exc:
        print(exc, file=sys.stderr)
        return BAD_REVISION_EXIT_CODE
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.scope is None:
        return main_delta(args)
    if args.delta_budget is not None:
        print("mutation: --delta-budget bounds a delta run; a named scope takes --budget", file=sys.stderr)
        return 1
    try:
        scope = resolve_scope(args.scope)
    except UnknownScopeError as exc:
        print(exc, file=sys.stderr)
        return 1

    os.chdir(REPO_ROOT)
    name_globs: list[str] | None = None
    if args.since is not None:
        try:
            name_globs = changed_mutant_globs(scope, args.since)
        except UnknownRevisionError as exc:
            print(exc, file=sys.stderr)
            return BAD_REVISION_EXIT_CODE

    if name_globs is not None and not name_globs:
        MUTANTS_DIR.mkdir(parents=True, exist_ok=True)
        write_report(scope, MUTANTS_DIR, name_globs=name_globs, since=args.since)
        return 0

    test_selection = ensure_mutant_tree_for_scope(scope, MUTANTS_DIR, fresh=args.fresh)

    completed = run_scope(
        scope,
        test_selection=test_selection,
        budget_seconds=args.budget,
        mutant_names=tuple(name_globs or ()),
    )
    write_report(scope, MUTANTS_DIR, name_globs=name_globs, since=args.since, complete=completed)
    if not completed:
        print(f"mutation: --budget expired before {scope.slug!r} finished; re-run to resume", file=sys.stderr)
        return BUDGET_EXIT_CODE
    return 0


if __name__ == "__main__":
    sys.exit(main())
