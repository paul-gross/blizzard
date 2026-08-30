from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src" / "blizzard"
_TESTS_DIR = _REPO_ROOT / "tests"
_FOUNDATION_DIR = _SRC_DIR / "foundation"
_HUB_DIR = _SRC_DIR / "hub"
_RUNNER_DIR = _SRC_DIR / "runner"
_HUB_STORE_INTERNAL_DIR = _HUB_DIR / "store" / "internal"
_RUNNER_STORE_DIR = _RUNNER_DIR / "store"
_RUNNER_DOMAIN_DIR = _RUNNER_DIR / "domain"

_MOVED_HOMES = {
    "ChunkStatus": "blizzard.foundation.chunk_status",
    "TERMINAL_STATUSES": "blizzard.foundation.chunk_status",
    "ArtifactKind": "blizzard.foundation.artifacts",
    "ArtifactScope": "blizzard.foundation.artifacts",
    "Executor": "blizzard.foundation.node_steps",
    "JudgedBy": "blizzard.foundation.node_steps",
    "SessionMode": "blizzard.foundation.node_steps",
    "TokenHash": "blizzard.foundation.tokens",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            modules.add(node.module)
    return modules


def _violations(root: Path, forbidden_prefixes: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for module in sorted(_imported_modules(path)):
            if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden_prefixes):
                violations.append(f"{path.relative_to(_REPO_ROOT)} imports {module}")
    return violations


def _misrouted_moved_names(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                home = _MOVED_HOMES.get(alias.name)
                if home is None:
                    continue
                misrouted = node.level > 0 or (node.module is not None and node.module.startswith("blizzard"))
                if misrouted and node.module != home:
                    origin = f"{'.' * node.level}{node.module or ''}"
                    violations.append(f"{path.relative_to(_REPO_ROOT)} imports {alias.name} from {origin}")
    return violations


def test_foundation_imports_neither_daemon() -> None:
    violations = _violations(_FOUNDATION_DIR, ("blizzard.hub", "blizzard.runner"))
    assert not violations, f"A — foundation must not import either daemon: {violations}"


def test_hub_does_not_import_runner() -> None:
    violations = _violations(_HUB_DIR, ("blizzard.runner",))
    assert not violations, f"B — hub must not import the runner: {violations}"


def test_runner_does_not_import_hub() -> None:
    violations = _violations(_RUNNER_DIR, ("blizzard.hub",))
    assert not violations, f"C — the runner must not import the hub: {violations}"


def test_moved_vocabulary_has_exactly_one_importable_home() -> None:
    violations = _misrouted_moved_names(_SRC_DIR) + _misrouted_moved_names(_TESTS_DIR)
    assert not violations, f"D — imported from somewhere other than its declared foundation home: {violations}"


def _bare_engine_accesses(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr == "_engine" and isinstance(node.value, ast.Name) and node.value.id == "self":
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno} acquires self._engine")
    return violations


def test_hub_store_internal_acquires_no_connection_outside_the_seam() -> None:
    """D5 (blizzard#413): every ``hub/store/internal/`` adapter takes the injected
    ``HubStoreConnections`` collaborator in place of ``Engine`` — no adapter method may
    reach past it to acquire a connection directly."""
    violations = _bare_engine_accesses(_HUB_STORE_INTERNAL_DIR)
    assert not violations, (
        f"E — hub/store/internal/ must route every connection through HubStoreConnections: {violations}"
    )


def _protocol_declarations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
                if name == "Protocol":
                    violations.append(f"{path.relative_to(_REPO_ROOT)} declares Protocol class {node.name}")
    return violations


def test_no_protocol_is_declared_under_runner_store() -> None:
    """AC1 (blizzard#410): every seam Protocol lives beside the concept that uses it —
    ``runner/store/`` holds only adapters, schema, and errors, never a Protocol."""
    violations = _protocol_declarations(_RUNNER_STORE_DIR)
    assert not violations, f"F — runner/store/ must declare no Protocol: {violations}"


def test_no_runner_domain_module_imports_from_runner_store() -> None:
    """AC1 (blizzard#410): a domain module owns its own seam Protocol — it never reaches
    into ``runner/store/`` for one, which would invert the dependency arrow."""
    violations = _violations(_RUNNER_DOMAIN_DIR, ("blizzard.runner.store",))
    assert not violations, f"G — runner/domain/ must not import from runner/store/: {violations}"
