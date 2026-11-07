"""Run component observations and compare successful aggregate assertions to discovery.

Run with ``uv run python -m tests.support.aggregate_census``. Only a passing test
with an explicit equality-to-empty aggregate assertion counts. Producer fingerprints
are a change detector, not proof of dynamic reachability or refusal coverage.
"""

import ast
import inspect
from collections.abc import Generator
from typing import Any

import pytest
from pluggy import Result

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.hub.app import create_app_for_export
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches
from tests.support.repo_files import repo_root
from tests.support.runner_producer_fingerprints import FINGERPRINTS
from tests.support.runner_refusal_discovery import (
    match_operations,
    producer_file_fingerprints,
    route_inventory,
    runner_boundary,
)

Operation = tuple[str, str]


def aggregate_assertion_lines(source: str, first_line: int = 1) -> set[int]:
    tree = ast.parse(source)
    return {
        line + first_line - 1
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert)
        and isinstance(node.test, ast.Compare)
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.List)
        and not node.test.comparators[0].elts
        and isinstance(node.test.left, ast.Call)
        and isinstance(node.test.left.func, ast.Name)
        and node.test.left.func.id == "coverage_mismatches"
        for line in range(node.test.left.lineno, (node.test.left.end_lineno or node.test.left.lineno) + 1)
    }


def credited(operations: set[Operation], observations: list[RefusalObservation], failures: list[str]) -> set[Operation]:
    if failures:
        return set()
    observed = {(item.method.upper(), item.path) for item in observations}
    return {(method.upper(), path) for method, path in operations} & observed


class Census:
    def __init__(self) -> None:
        self.covered: set[Operation] = set()
        self.current: set[Operation] = set()
        self.observations: list[RefusalObservation] = []
        self.current_observations: list[RefusalObservation] = []
        self.call_passed = False

    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        self.current = set()
        self.current_observations = []
        self.call_passed = False

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item: pytest.Function) -> Generator[None, Result[None], None]:
        module = item.module
        original = getattr(module, "coverage_mismatches", None)
        function = getattr(item, "obj", None)
        lines: set[int] = set()
        if original is coverage_mismatches and callable(function):
            source, first_line = inspect.getsourcelines(function)
            lines = aggregate_assertion_lines("".join(source), first_line)
        eligible = bool(lines)
        if eligible and module is not None:

            def record(
                spec: dict[str, Any], operations: set[Operation], observations: list[RefusalObservation]
            ) -> list[str]:
                failures = coverage_mismatches(spec, operations, observations)
                frame = inspect.currentframe()
                if frame is not None and frame.f_back is not None and frame.f_back.f_lineno in lines:
                    self.current.update(credited(operations, observations, failures))
                    if not failures:
                        self.current_observations.extend(
                            item for item in observations if (item.method.upper(), item.path) in self.current
                        )
                return failures

            module.coverage_mismatches = record
        yield
        if eligible and module is not None:
            module.coverage_mismatches = original

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
    ) -> Generator[None, Result[pytest.TestReport], None]:
        outcome = yield
        report = outcome.get_result()
        if report.when == "call":
            self.call_passed = report.passed
        elif report.when == "teardown" and report.passed and self.call_passed:
            self.covered.update(self.current)
            self.observations.extend(self.current_observations)


def missing_operations(
    reaches: set[Operation], routes: set[Operation], covered: set[Operation]
) -> tuple[set[Operation], set[Operation]]:
    matched, unmatched = match_operations(reaches, routes)
    discovered = {operation for operations in matched.values() for operation in operations}
    return discovered - covered, unmatched


def main() -> int:
    root = repo_root()
    reaches, unknown = runner_boundary(root)
    routes, route_unknown = route_inventory(create_app_for_export(packaged_flag_registry()))
    census = Census()
    api = root / "tests/component/hub/api"
    files = [
        str(path) for path in sorted(api.glob("test_*observations.py")) if "coverage_mismatches(" in path.read_text()
    ]
    result = pytest.main([*files, "-q", "-p", "no:cacheprovider"], plugins=[census])
    missing, unmatched = missing_operations(reaches, set(routes), census.covered)
    matched, _ = match_operations(reaches, set(routes))
    discovered = {operation for operations in matched.values() for operation in operations}
    variants = coverage_mismatches(
        create_app_for_export(packaged_flag_registry()).openapi(), discovered, census.observations
    )
    expected = {path: (int(count), digest) for path, count, digest in map(str.split, FINGERPRINTS.splitlines())}
    actual = producer_file_fingerprints(root)
    changed = sorted(path for path in actual.keys() | expected.keys() if actual.get(path) != expected.get(path))
    for title, entries in (
        ("unresolved discovery", unknown + route_unknown),
        ("unmatched reaches", sorted(unmatched)),
        ("pending aggregate coverage", sorted(missing)),
        ("variant mismatches", sorted(variants)),
        ("producer candidate files requiring review", changed),
    ):
        print(f"\n{title} ({len(entries)}):")
        for entry in entries:
            print(f"  {entry}")
    return int(bool(result or unknown or route_unknown or unmatched or missing or variants or changed))


if __name__ == "__main__":
    raise SystemExit(main())
