"""Scoped, resumable mutation testing over `src/blizzard` — `mise run mutation <scope>`.

Mutates only the source a garden scope names, runs only the fast-tier tests import
analysis selects for that scope, and writes `mutants/report.json`. mutmut itself lives in
the `mutation` dependency group and its own project environment (`mise.toml`), never in
`.venv`, so this module only imports it inside the functions that actually drive a run —
importing it at module scope would make it a hard dependency of every test that imports
this file, including the ones that run in the default environment.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import shutil
import signal
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_ROOT = REPO_ROOT / "tests"
MUTANTS_DIR = REPO_ROOT / "mutants"
SCOPE_MARKER_NAME = ".scope"
REPORT_NAME = "report.json"

# Distinct from mutmut's own exit codes (it never exits non-zero for survivors) — this is
# the caller-facing signal that the run stopped on --budget and should be re-invoked.
BUDGET_EXIT_CODE = 3


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

# Mirrors the committed [tool.mutmut].do_not_mutate migrations exclusion in pyproject.toml —
# applied on top of every scope, so a scope row never has to restate it.
MIGRATIONS_EXCLUSION = "src/blizzard/*/store/migrations/versions/*"
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
    return rel_str.startswith(TOOLS_PREFIX) or fnmatch.fnmatch(rel_str, MIGRATIONS_EXCLUSION)


def files_for_scope(scope: Scope, paths: Iterable[Path]) -> set[Path]:
    do_not_mutate = (MIGRATIONS_EXCLUSION, *scope.do_not_mutate)
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
    """Absolute dotted module names `path` imports, resolving relative imports against its own package."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _package_dotted_name(path.parent, tests_root)
    package_parts = package.split(".") if package else []

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    names.add(node.module)
                continue
            trim = node.level - 1
            base_parts = package_parts[: len(package_parts) - trim] if trim else package_parts
            if node.module:
                names.add(".".join([*base_parts, node.module]))
            else:
                names.update(".".join([*base_parts, alias.name]) for alias in node.names)
    return names


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
        and test_module_selected(path, scope.test_selection_packages, tests_root)
    )


# --- scope -> mutmut config override -------------------------------------------------------


@dataclass(frozen=True)
class ConfigOverride:
    only_mutate: list[str]
    do_not_mutate: list[str]
    pytest_add_cli_args_test_selection: list[str]


def config_override_for_scope(
    scope: Scope, tests_root: Path = TESTS_ROOT, repo_root: Path = REPO_ROOT
) -> ConfigOverride:
    return ConfigOverride(
        only_mutate=list(scope.only_mutate),
        do_not_mutate=[MIGRATIONS_EXCLUSION, *scope.do_not_mutate],
        pytest_add_cli_args_test_selection=selected_test_files(scope, tests_root, repo_root),
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


def ensure_mutant_tree_for_scope(scope: Scope, mutants_dir: Path = MUTANTS_DIR) -> None:
    """Clear `mutants_dir` when it was last built for a different scope; otherwise leave it.

    Invoking the same scope again never clears — that is what makes resume possible.
    """
    repo_files = _load_module_by_path("_mutation_repo_files", REPO_ROOT / "tests" / "repo_files.py")
    prepare_mutant_tree = repo_files.prepare_mutant_tree

    marker = mutants_dir / SCOPE_MARKER_NAME
    if mutants_dir.exists() and marker.is_file() and marker.read_text().strip() != scope.slug:
        shutil.rmtree(mutants_dir)

    mutants_dir.mkdir(parents=True, exist_ok=True)
    prepare_mutant_tree(mutants_dir)
    marker.write_text(scope.slug)


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


def build_report(*, scope: str, mutants_dir: Path, diff_provider: DiffProvider) -> dict:
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
            total += 1
            status = STATUS_BY_EXIT_CODE.get(exit_code, "suspicious")
            counts[status] += 1
            if status == "survived":
                function_name, _class_name = _function_and_class_from_mutant_name(mutant_name)
                survivors.append(
                    {
                        "mutant": mutant_name,
                        "file": source_path.as_posix(),
                        "function": function_name,
                        "diff": diff_provider(mutant_name, source_path),
                    }
                )

    return {
        "scope": scope,
        "total": total,
        "counts": counts,
        "survivors": survivors,
    }


def write_report(scope: Scope, mutants_dir: Path = MUTANTS_DIR) -> dict:
    report = build_report(scope=scope.slug, mutants_dir=mutants_dir, diff_provider=_real_diff_provider)
    (mutants_dir / REPORT_NAME).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


# --- driving mutmut in-process, with a budget bounding only mutant execution ---------------


def _run_with_budget(budget_seconds: float | None) -> bool:
    """Run mutmut's `run` command in-process. Returns False iff `budget_seconds` expired.

    The budget starts counting only once mutmut's coverage map (`mutants/mutmut-stats.json`)
    is saved — never during the mapping phase itself, which always runs to completion. A
    background timer thread then sends this process SIGINT, which mutmut's own mutation
    loop catches as KeyboardInterrupt and uses to stop workers cleanly. A single delivery
    can go unnoticed for a long time when dozens of freshly forked mutant workers are
    contending for the CPU alongside the timer thread, so once the budget expires this
    keeps resending SIGINT once a second until the run actually returns.
    """
    import mutmut.__main__ as mutmut_main

    budget_expired = threading.Event()
    run_finished = threading.Event()
    timer: threading.Timer | None = None
    original_save_stats = mutmut_main.save_stats

    def resend_until_finished() -> None:
        while not run_finished.wait(timeout=1.0):
            os.kill(os.getpid(), signal.SIGINT)

    def send_interrupt() -> None:
        budget_expired.set()
        os.kill(os.getpid(), signal.SIGINT)
        nagger = threading.Thread(target=resend_until_finished, daemon=True)
        nagger.start()

    def start_budget_after_first_save() -> None:
        nonlocal timer
        original_save_stats()
        mutmut_main.save_stats = original_save_stats
        if budget_seconds is not None:
            timer = threading.Timer(budget_seconds, send_interrupt)
            timer.daemon = True
            timer.start()

    if budget_seconds is not None:
        mutmut_main.save_stats = start_budget_after_first_save

    # A SIGINT that lands before the mutation loop's own try/except (still inside stats
    # collection, clean-test, or forced-fail-test setup) surfaces here uncaught instead.
    try:
        mutmut_main._run((), None)
    except KeyboardInterrupt:
        if not budget_expired.is_set():
            raise
    finally:
        run_finished.set()
        if timer is not None:
            timer.cancel()
        mutmut_main.save_stats = original_save_stats

    return not budget_expired.is_set()


def run_scope(scope: Scope, *, budget_seconds: float | None) -> bool:
    import mutmut.__main__ as mutmut_main
    from mutmut.configuration import config

    cfg = config()
    apply_config_override(cfg, config_override_for_scope(scope))

    # A mutant interrupted mid-test (never a killed/survived/timeout verdict) must be
    # retried on resume, the same as one mutmut never got to at all.
    mutmut_main._reset_mutant_results(lambda _key, exit_code: exit_code == 2)

    return _run_with_budget(budget_seconds)


# --- CLI -------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mutation.py",
        description="Scoped, resumable mutation testing over src/blizzard.",
    )
    parser.add_argument("scope", help=f"One of: {', '.join(sorted(SCOPES))}")
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="Seconds to bound mutant execution (not the coverage-mapping phase). "
        "On expiry the run stops and exits non-zero; re-invoke the same scope to resume.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        scope = resolve_scope(args.scope)
    except UnknownScopeError as exc:
        print(exc, file=sys.stderr)
        return 1

    os.chdir(REPO_ROOT)
    ensure_mutant_tree_for_scope(scope)

    completed = run_scope(scope, budget_seconds=args.budget)
    if not completed:
        print(f"mutation: --budget expired before {scope.slug!r} finished; re-run to resume", file=sys.stderr)
        return BUDGET_EXIT_CODE

    write_report(scope)
    return 0


if __name__ == "__main__":
    sys.exit(main())
