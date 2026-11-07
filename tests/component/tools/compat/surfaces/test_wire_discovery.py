"""The wire selection covers the runner's discovered outbound hub operations."""

import json

from blizzard.tools.compat.surfaces.wire import is_wire_operation
from tests.support.repo_files import repo_root
from tests.support.runner_refusal_discovery import match_operations, runner_boundary


def test_discovered_runner_operations_are_in_wire_selection() -> None:
    root = repo_root()
    reaches, unresolved = runner_boundary(root)
    spec = json.loads((root / "openapi/hub.openapi.json").read_text())
    operations = {
        (method.upper(), path)
        for path, methods in spec["paths"].items()
        for method in methods
        if method in {"get", "post", "put", "patch", "delete"}
    }
    matched, missing = match_operations(reaches, operations)
    assert not unresolved
    assert not missing
    assert matched
    assert not {
        op for candidates in matched.values() for op in candidates if not is_wire_operation(op[1], op[0].lower())
    }
