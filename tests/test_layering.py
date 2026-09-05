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


def _bare_engine_accesses(root: Path, *, exempt: frozenset[Path] = frozenset()) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path in exempt:
            continue
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


_RUNNER_STORE_CONNECTIONS_FILE = _RUNNER_STORE_DIR / "internal" / "base.py"


def test_runner_acquires_no_connection_outside_the_store_seam() -> None:
    """D5 (plan: structural gates over runner wiring): every ``runner/`` module takes the
    injected ``RunnerStoreConnections`` collaborator in place of a bare ``Engine`` — no
    module outside the connections seam itself may reach past it to acquire a connection
    directly and open its own transaction."""
    violations = _bare_engine_accesses(_RUNNER_DIR, exempt=frozenset({_RUNNER_STORE_CONNECTIONS_FILE}))
    assert not violations, f"K — runner/ must route every connection through RunnerStoreConnections: {violations}"


def test_hub_store_internal_holds_no_http_client() -> None:
    """``hub/store/internal/`` is the SQL adapters' home, as every module there states. An
    HTTP client belongs to the package owning its own seam — `hub/forge/internal/`,
    `hub/work_sources/internal/` — never beside them."""
    violations = _violations(_HUB_STORE_INTERNAL_DIR, ("httpx",))
    assert not violations, f"E — hub/store/internal/ is SQL-only: {violations}"


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


_RUNNER_STORE_INTERNAL_DIR = _RUNNER_STORE_DIR / "internal"
_RUNNER_STORE_SCHEMA_FILE = _RUNNER_STORE_DIR / "schema.py"
_RUNNER_STORE_ERRORS_FILE = _RUNNER_STORE_DIR / "errors.py"
_RUNNER_STORE_MIGRATIONS_DIR = _RUNNER_STORE_DIR / "migrations"
_RUNNER_COMPOSITION_FILE = _RUNNER_DIR / "composition.py"

# AC3 (blizzard#410, Phases 4-5): every file outside the store's own package that still
# names ``sqlalchemy`` — each an accepted, individually-justified exception, not the
# store surface this criterion polices. ``None`` allows every name from that import;
# a tuple narrows to only those names.
_SQLALCHEMY_EXCEPTIONS: dict[Path, tuple[str, ...] | None] = {
    # Engine only, for DI typing — shared with hub/composition.py, permanently out of
    # scope (plan's "Out of scope": "Engine in a composition root").
    _RUNNER_COMPOSITION_FILE: ("Engine",),
    # IntegrityError only, for the replay-check catch (D6): the primary-key collision IS
    # the replay check, business logic the adapter must see — statement construction is
    # the table-bound form, so no other sqlalchemy name is needed here.
    _RUNNER_DIR / "auth" / "internal" / "jti_cache_repository.py": ("IntegrityError",),
}


def _sqlalchemy_import_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlalchemy" or alias.name.startswith("sqlalchemy."):
                    names.add("*")
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == "sqlalchemy" or node.module.startswith("sqlalchemy."))
        ):
            names.update(alias.name for alias in node.names)
    return names


def test_sqlalchemy_is_imported_only_from_the_store_seam() -> None:
    """AC3 (blizzard#410, Phases 4-5): ``sqlalchemy`` is a name the store's own adapters,
    schema, errors and migrations may hold — every other module takes the Protocol seam,
    never the driver underneath it, bar the individually-justified exceptions above."""
    violations: list[str] = []
    for path in sorted(_RUNNER_DIR.rglob("*.py")):
        if (
            path.is_relative_to(_RUNNER_STORE_INTERNAL_DIR)
            or path in (_RUNNER_STORE_SCHEMA_FILE, _RUNNER_STORE_ERRORS_FILE)
            or path.is_relative_to(_RUNNER_STORE_MIGRATIONS_DIR)
        ):
            continue
        names = _sqlalchemy_import_names(path)
        if not names:
            continue
        allowed = _SQLALCHEMY_EXCEPTIONS.get(path, ())
        if allowed is None:
            continue
        extra = names if allowed == () else names - set(allowed)
        if extra:
            violations.append(f"{path.relative_to(_REPO_ROOT)} imports sqlalchemy name(s) {sorted(extra)}")
    assert not violations, f"H — sqlalchemy must stay inside the store seam: {violations}"


def _runner_store_adapter_names() -> set[str]:
    names: set[str] = set()
    for path in sorted(_RUNNER_STORE_INTERNAL_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name.endswith("Store"):
                names.add(node.name)
    return names


def test_composition_is_the_only_module_naming_a_concrete_runner_store_adapter() -> None:
    """AC4 (blizzard#410, D4): every concrete ``store/internal/`` adapter is named by
    ``runner/composition.py`` and nowhere else under ``src/`` — every other collaborator
    takes a Protocol seam or the ``RunnerStores`` bundle it builds."""
    adapters = _runner_store_adapter_names()
    violations: list[str] = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if path.is_relative_to(_RUNNER_STORE_INTERNAL_DIR) or path == _RUNNER_COMPOSITION_FILE:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                hit = adapters & {alias.name for alias in node.names}
                if hit:
                    violations.append(f"{path.relative_to(_REPO_ROOT)} imports {sorted(hit)}")
    assert not violations, f"I — only runner/composition.py may name a concrete runner-store adapter: {violations}"


_COMPOSITION_ROOTS = frozenset(
    {
        _HUB_DIR / "app.py",
        _HUB_DIR / "composition.py",
        _RUNNER_DIR / "app.py",
        _RUNNER_DIR / "loop" / "build.py",
        _RUNNER_DIR / "cli" / "runtime.py",
        _RUNNER_DIR / "cli" / "external_usage.py",
    }
)

_GATED_COMPOSITION_NAMES = ("build_stores", "ClaudeCodeAdapter")


def test_build_stores_and_claude_code_adapter_are_named_only_at_a_composition_root() -> None:
    """L (plan: structural gates over runner wiring, D1, D2): bzh:dependency-injection's six
    declared composition roots are the only modules that may import ``build_stores`` or
    ``ClaudeCodeAdapter`` — every other collaborator takes ``RunnerStores``/``RunnerReadStores``
    or ``IHarnessAdapter``."""
    violations: list[str] = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if path in _COMPOSITION_ROOTS:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            hit = set(_GATED_COMPOSITION_NAMES) & {alias.name for alias in node.names}
            if hit:
                violations.append(f"{path.relative_to(_REPO_ROOT)} imports {sorted(hit)}")
    assert not violations, f"L — only a declared composition root may import {_GATED_COMPOSITION_NAMES}: {violations}"


_RUNNER_API_DIR = _RUNNER_DIR / "api"


def test_runner_api_names_no_write_capable_store_or_bundle() -> None:
    """AC (blizzard#412, D1): a runner route resolves only ``RunnerReadStores`` and its
    per-concept mutation services — never ``RunnerStores`` nor an ``IWrite*`` seam, which
    would let a route mutate directly instead of delegating to a domain service."""
    violations: list[str] = []
    for path in sorted(_RUNNER_API_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                is_write_repository = alias.name.startswith("IWrite") and alias.name.endswith("Repository")
                if alias.name == "RunnerStores" or is_write_repository:
                    violations.append(f"{path.relative_to(_REPO_ROOT)} imports {alias.name}")
    assert not violations, f"J — runner/api/ must name no write-capable store or bundle: {violations}"
