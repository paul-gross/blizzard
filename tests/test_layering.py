from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_REPO_ROOT = repo_root()
_SRC_DIR = _REPO_ROOT / "src" / "blizzard"
_TESTS_DIR = _REPO_ROOT / "tests"
_FOUNDATION_DIR = _SRC_DIR / "foundation"
_HUB_DIR = _SRC_DIR / "hub"
_RUNNER_DIR = _SRC_DIR / "runner"
_HUB_STORE_INTERNAL_DIR = _HUB_DIR / "store" / "internal"
_HUB_STORE_ERRORS_FILE = _HUB_DIR / "store" / "errors.py"
_RUNNER_STORE_DIR = _RUNNER_DIR / "store"
_RUNNER_DOMAIN_DIR = _RUNNER_DIR / "domain"
_WIRE_DIR = _SRC_DIR / "wire"
_AUTH_CORE_DIR = _SRC_DIR / "auth_core"

#: ``bzh:shared-kernel`` — the packages both daemons import and that import no daemon.
_KERNEL_DIRS = (_FOUNDATION_DIR, _WIRE_DIR, _AUTH_CORE_DIR)
_NON_KERNEL_PACKAGES = ("blizzard.hub", "blizzard.runner", "blizzard.cli", "blizzard.tools")

_MOVED_HOMES = {
    "ChunkStatus": "blizzard.foundation.chunk_status",
    "TERMINAL_STATUSES": "blizzard.foundation.chunk_status",
    "ArtifactKind": "blizzard.foundation.artifacts",
    "ArtifactScope": "blizzard.foundation.artifacts",
    "Executor": "blizzard.foundation.node_steps",
    "JudgedBy": "blizzard.foundation.node_steps",
    "SessionMode": "blizzard.foundation.node_steps",
    "TokenHash": "blizzard.foundation.tokens",
    "EventLogKind": "blizzard.foundation.event_log",
    "EventLogSeverity": "blizzard.foundation.event_log",
    "EVENT_LOG_SEVERITY": "blizzard.foundation.event_log",
    "PROVIDER_ANTHROPIC": "blizzard.runner.subscriptions.subscription_sampler",
    "PROVIDER_OPENAI": "blizzard.runner.subscriptions.subscription_sampler",
    # ``EventRecord`` is left out: hub analytics owns an unrelated record by that name.
    "SpanRecord": "blizzard.foundation.trace_spans",
    "LinkRecord": "blizzard.foundation.trace_spans",
    "SpanKind": "blizzard.foundation.trace_spans",
    "SpanStatus": "blizzard.foundation.trace_spans",
    "AttributeValue": "blizzard.foundation.trace_spans",
    "Attributes": "blizzard.foundation.trace_spans",
    "GENAI_SEMCONV_VERSION": "blizzard.foundation.trace_attributes",
    "SHARED_ATTRIBUTES": "blizzard.foundation.trace_attributes",
    "genai_usage": "blizzard.foundation.trace_attributes",
    "service_name": "blizzard.foundation.trace_attributes",
    "ITraceExporter": "blizzard.foundation.trace_export.exporter",
    "TracingSettings": "blizzard.foundation.trace_export.settings",
    "TracingState": "blizzard.foundation.trace_export.settings",
    "endpoint_origin": "blizzard.foundation.trace_export.settings",
    "OtlpTraceExporter": "blizzard.foundation.trace_export.internal.otlp",
    "TracingConfig": "blizzard.foundation.trace_export.config",
    "JumpReason": "blizzard.foundation.trace_export.cursor",
    "CursorJump": "blizzard.foundation.trace_export.cursor",
    "first_pass_jump": "blizzard.foundation.trace_export.cursor",
    "lag_cap_jump": "blizzard.foundation.trace_export.cursor",
    "BACKOFF_CAP": "blizzard.foundation.lane_retry",
    "OutageLatch": "blizzard.foundation.lane_retry",
    "WorkItemPriority": "blizzard.foundation.work_items",
    "WorkItemClosure": "blizzard.foundation.work_items",
    "MigrationMode": "blizzard.foundation.chunk_migration",
    "GardenProposalOrigin": "blizzard.foundation.garden_proposals",
    "GardenProposalClosureKind": "blizzard.foundation.garden_proposals",
    "GardenProposalItemOutcome": "blizzard.foundation.garden_proposals",
    "LeaseState": "blizzard.foundation.leases",
    "TurnKind": "blizzard.foundation.transcripts",
    "TranscriptUnavailable": "blizzard.foundation.transcripts",
    "TranscriptProvenance": "blizzard.foundation.transcripts",
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


def test_kernel_imports_no_non_kernel_blizzard_package() -> None:
    violations = [v for root in _KERNEL_DIRS for v in _violations(root, _NON_KERNEL_PACKAGES)]
    assert not violations, f"A — wire/, foundation/ and auth_core/ must import no daemon, CLI or tool: {violations}"


def test_hub_does_not_import_runner() -> None:
    violations = _violations(_HUB_DIR, ("blizzard.runner",))
    assert not violations, f"B — hub must not import the runner: {violations}"


def test_runner_does_not_import_hub() -> None:
    violations = _violations(_RUNNER_DIR, ("blizzard.hub",))
    assert not violations, f"C — the runner must not import the hub: {violations}"


def _kernel_modules() -> list[str]:
    return [
        ".".join(path.relative_to(_SRC_DIR.parent).with_suffix("").parts).removesuffix(".__init__")
        for root in _KERNEL_DIRS
        for path in sorted(root.rglob("*.py"))
    ]


def _loaded_after_importing_all(modules: list[str]) -> set[str]:
    return _loaded_after_importing(", ".join(modules))


def _loaded_non_kernel(loaded: set[str], prefixes: tuple[str, ...]) -> list[str]:
    return sorted(m for m in loaded if any(m == p or m.startswith(f"{p}.") for p in prefixes))


def test_kernel_loads_no_non_kernel_blizzard_module_transitively() -> None:
    """A, transitively: a static scan sees only a module's own imports, not what they pull in."""
    loaded = _loaded_after_importing_all(_kernel_modules())
    leaked = _loaded_non_kernel(loaded, _NON_KERNEL_PACKAGES)
    assert not leaked, f"A — importing the kernel loaded: {leaked}"


def test_neither_daemon_root_loads_the_other_daemon() -> None:
    """B and C, transitively: no composition root drags in the other daemon through the kernel."""
    hub_roots = ["blizzard.hub.app", "blizzard.hub.composition"]
    runner_roots = ["blizzard.runner.app", "blizzard.runner.composition", "blizzard.runner.loop.build"]
    leaks = [
        f"{root} loads {m}"
        for root in hub_roots
        for m in _loaded_non_kernel(_loaded_after_importing(root), ("blizzard.runner",))
    ]
    leaks += [
        f"{root} loads {m}"
        for root in runner_roots
        for m in _loaded_non_kernel(_loaded_after_importing(root), ("blizzard.hub",))
    ]
    assert not leaks, f"B/C — a daemon root loaded the other daemon: {leaks}"


def test_moved_vocabulary_has_exactly_one_importable_home() -> None:
    violations = _misrouted_moved_names(_SRC_DIR) + _misrouted_moved_names(_TESTS_DIR)
    assert not violations, f"D — imported from somewhere other than its declared foundation home: {violations}"


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _daemon_named_strings(root: Path, forbidden_prefixes: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        docstring_ids = _docstring_ids(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docstring_ids:
                continue  # a docstring's bare-pointer prose (bzh:comment-encapsulation) is not a live dependency
            if any(prefix in node.value for prefix in forbidden_prefixes):
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno} names {node.value!r}")
    return violations


def test_foundation_names_neither_daemon_in_a_string_literal() -> None:
    """A-C only see imports; a module path threaded as a string is invisible to them —
    ``foundation/crash.py`` used to hold exactly that shape. This file's first non-import
    check, one bespoke walker like its siblings."""
    violations = _daemon_named_strings(_FOUNDATION_DIR, ("blizzard.hub.", "blizzard.runner."))
    assert not violations, f"N — foundation must not name either daemon, even as a string: {violations}"


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


def test_hub_acquires_no_connection_outside_the_store_seam() -> None:
    """Every adapter under ``hub/`` takes
    ``HubStoreConnections`` in place of ``Engine`` — none may acquire a connection
    directly. Retires the narrower ``hub/store/internal/``-only assertion this replaces."""
    violations = _bare_engine_accesses(_HUB_DIR, exempt=frozenset({_HUB_STORE_ERRORS_FILE}))
    assert not violations, f"E — hub/ must route every connection through HubStoreConnections: {violations}"


_EVENT_LOG_SERVICE_FILE = _HUB_DIR / "domain" / "event_log.py"
_CHUNK_EVENTS_STORE_FILE = _HUB_DIR / "store" / "internal" / "chunk_events_store.py"


def _record_event_call_sites(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path in (_EVENT_LOG_SERVICE_FILE, _CHUNK_EVENTS_STORE_FILE):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "record_event":
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno} calls record_event")
    return violations


def test_record_event_is_called_only_through_event_log_service() -> None:
    """Recording an event is what publishes it (``bzh:operational-event-log``):
    ``EventLogService.record`` (``event_log.py``) is the only caller of the write
    repository's ``record_event`` — every event-authoring call site takes the service
    instead, so a new one can never land a row that stays unbroadcast."""
    violations = _record_event_call_sites(_SRC_DIR)
    assert not violations, f"M — record_event must be called only from EventLogService: {violations}"


_RUNNER_STORE_CONNECTIONS_FILE = _RUNNER_STORE_DIR / "errors.py"


def test_runner_acquires_no_connection_outside_the_store_seam() -> None:
    """(plan: structural gates over runner wiring): every ``runner/`` module takes
    ``RunnerStoreConnections`` in place of a bare ``Engine`` — none may acquire a
    connection directly outside the connections seam itself."""
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
    """AC1: every seam Protocol lives beside the concept that uses it —
    ``runner/store/`` holds only adapters, schema, and errors, never a Protocol."""
    violations = _protocol_declarations(_RUNNER_STORE_DIR)
    assert not violations, f"F — runner/store/ must declare no Protocol: {violations}"


def test_no_runner_domain_module_imports_from_runner_store() -> None:
    """AC1: a domain module owns its own seam Protocol — it never reaches
    into ``runner/store/`` for one, which would invert the dependency arrow."""
    violations = _violations(_RUNNER_DOMAIN_DIR, ("blizzard.runner.store",))
    assert not violations, f"G — runner/domain/ must not import from runner/store/: {violations}"


def test_no_runner_domain_module_imports_from_runner_loop_or_api() -> None:
    """A domain module owns its own seam Protocol — it never reaches outward into
    ``runner/loop/`` or ``runner/api/`` for one."""
    violations = _violations(_RUNNER_DOMAIN_DIR, ("blizzard.runner.loop", "blizzard.runner.api"))
    assert not violations, f"runner/domain/ must not import from runner/loop/ or runner/api/: {violations}"


_RUNNER_STORE_INTERNAL_DIR = _RUNNER_STORE_DIR / "internal"
_RUNNER_STORE_SCHEMA_FILE = _RUNNER_STORE_DIR / "schema.py"
_RUNNER_STORE_ERRORS_FILE = _RUNNER_STORE_DIR / "errors.py"
_RUNNER_STORE_MIGRATIONS_DIR = _RUNNER_STORE_DIR / "migrations"
_RUNNER_COMPOSITION_FILE = _RUNNER_DIR / "composition.py"
_RUNNER_APP_FILE = _RUNNER_DIR / "app.py"

# AC3: every file outside the store's own package that still
# names ``sqlalchemy`` — each an accepted, individually-justified exception, not the
# store surface this criterion polices. ``None`` allows every name from that import;
# a tuple narrows to only those names.
_SQLALCHEMY_EXCEPTIONS: dict[Path, tuple[str, ...] | None] = {
    # Engine only, for DI typing — shared with hub/composition.py, permanently out of
    # scope (plan's "Out of scope": "Engine in a composition root"). ``app.py`` holds the
    # graph's engine only as the handle it hands its own callers to dispose.
    _RUNNER_COMPOSITION_FILE: ("Engine",),
    _RUNNER_APP_FILE: ("Engine",),
    # IntegrityError only, for the replay-check catch: the collision itself IS the
    # business-logic check, so this one name stays local instead of the table-bound form.
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
    """AC3: ``sqlalchemy`` is a name the store's own adapters,
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
    """AC4: every concrete ``store/internal/`` adapter is named by
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


#: The runner's process-graph collaborators, built once by ``build_runner_process``.
_RUNNER_GRAPH_CONSTRUCTORS = frozenset({"build_stores", "build_production_harness_registry", "HarnessHealthCache"})


def _called_names(tree: ast.AST) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name is not None:
                calls.append((name, node.lineno))
    return calls


def test_composition_is_the_only_module_constructing_the_runner_process_graph_collaborators() -> None:
    """I: the process-graph constructors are called from ``runner/composition.py`` and nowhere
    else under ``src/`` — every other root takes the one ``RunnerProcess`` graph."""
    violations: list[str] = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if path == _RUNNER_COMPOSITION_FILE:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for name, lineno in _called_names(tree):
            if name in _RUNNER_GRAPH_CONSTRUCTORS:
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno} calls {name}")
    assert not violations, f"I — only runner/composition.py may construct the runner process graph: {violations}"


# The one roster of composition roots: modules that wire adapters, and so may import any
# package's ``internal/`` (``bzh:internal-visibility``). The last two are short-lived
# command roots wiring adapters inline at the top of the command body.
_COMPOSITION_ROOTS = frozenset(
    {
        _HUB_DIR / "app.py",
        _HUB_DIR / "composition.py",
        _HUB_DIR / "cli" / "__init__.py",
        _RUNNER_DIR / "app.py",
        _RUNNER_DIR / "loop" / "build.py",
        _RUNNER_DIR / "cli" / "runtime.py",
        _RUNNER_DIR / "cli" / "external_usage.py",
        _RUNNER_COMPOSITION_FILE,
        _RUNNER_DIR / "cli" / "opencode.py",
        _SRC_DIR / "tools" / "invariants.py",
    }
)

_RUNNER_COMPOSITION_MODULE = "blizzard.runner.composition"


def _runner_composition_imports(root: Path, *, exempt: frozenset[Path]) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path in exempt:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == _RUNNER_COMPOSITION_MODULE:
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno} imports from {node.module}")
            elif isinstance(node, ast.ImportFrom) and node.module == "blizzard.runner":
                if any(alias.name == "composition" for alias in node.names):
                    violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno} imports composition")
            elif isinstance(node, ast.Import) and any(alias.name == _RUNNER_COMPOSITION_MODULE for alias in node.names):
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno} imports {_RUNNER_COMPOSITION_MODULE}")
    return violations


def test_only_the_composition_roots_import_the_runner_composition_module() -> None:
    """`blizzard.runner.composition` is a wiring module, not a Protocol or a
    bundle seam — importing it in any form outside the composition roots means
    constructing runner stores outside their one approved wiring site
    (``bzh:dependency-injection``). Fail-closed: no name exemptions, unlike the old
    allowlist this replaces, which missed `build_read_stores`."""
    violations = _runner_composition_imports(_SRC_DIR, exempt=_COMPOSITION_ROOTS)
    assert not violations, (
        f"D5 — blizzard.runner.composition must only be imported at its composition roots: {violations}"
    )


def _write_session_store_accesses(root: Path, *, exempt: frozenset[Path]) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path in exempt:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names_write_store = (isinstance(node, ast.Name) and node.id == "IWriteSessionStore") or (
                isinstance(node, ast.Attribute) and node.attr == "IWriteSessionStore"
            )
            if names_write_store:
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno} names IWriteSessionStore")  # type: ignore[union-attr]
    return violations


def test_iwritesessionstore_is_named_only_inside_sessions_or_the_composition_root() -> None:
    """Only `hub/cli/sessions/` (the Protocol's own package) may name
    `IWriteSessionStore` — every other hub CLI module, including `auth.py`'s
    login/logout, takes the `SessionService` application service instead, never the raw
    write seam (``bzh:controller-read-only``)."""
    hub_cli_dir = _HUB_DIR / "cli"
    sessions_dir = hub_cli_dir / "sessions"
    exempt = _COMPOSITION_ROOTS | set(sessions_dir.rglob("*.py"))
    violations = _write_session_store_accesses(hub_cli_dir, exempt=exempt)
    assert not violations, f"D4 — IWriteSessionStore must be named only inside sessions/: {violations}"


_RUNNER_API_DIR = _RUNNER_DIR / "api"


def test_runner_api_names_no_write_capable_store_or_bundle() -> None:
    """AC: a runner route resolves only ``RunnerReadStores`` and its
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


def _wire_model_names() -> set[str]:
    names: set[str] = set()
    for path in sorted(_WIRE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                names.add(node.name)
    return names


def _wire_cross_model_constructions() -> list[str]:
    wire_names = _wire_model_names()
    violations: list[str] = []
    for path in sorted(_WIRE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            for method in cls.body:
                if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for node in ast.walk(method):
                    if not isinstance(node, ast.Call):
                        continue
                    name = node.func.id if isinstance(node.func, ast.Name) else None
                    if name is None or name not in wire_names or name == cls.name:
                        continue
                    violations.append(f"{path.relative_to(_REPO_ROOT)}::{cls.name}.{method.name} constructs {name}")
    return violations


def test_no_wire_model_projects_into_another() -> None:
    """O (``bzh:shared-kernel``): a wire model is a pydantic shape,
    never a projection — no method under ``wire/`` may instantiate another wire model,
    only its own class (a classmethod's bare ``cls(...)``, or a ``default_factory``
    supplying a sibling default outside any method body, are model config, not this)."""
    violations = _wire_cross_model_constructions()
    assert not violations, f"O — wire/ must declare no cross-model projection: {violations}"


def _internal_crossings(src_root: Path, *, exempt: frozenset[Path]) -> list[str]:
    """Every import of a module under ``<owner>/internal/`` from a module outside ``<owner>``.
    Absolute, relative, and ``from <owner> import internal`` forms all resolve to the module
    path they name before the owner comparison."""
    top = src_root.name
    violations: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        if path in exempt:
            continue
        importer = list(path.relative_to(src_root.parent).with_suffix("").parts)
        if path.name == "__init__.py":
            importer = importer[:-1]
        package = importer if path.name == "__init__.py" else importer[:-1]
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                named = [alias.name.split(".") for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = package[: len(package) - (node.level - 1)] if node.level else []
                base = [*base, *(node.module.split(".") if node.module else [])]
                named = [base, *([*base, alias.name] for alias in node.names)]
            else:
                continue
            for module in named:
                if module[:1] != [top] or "internal" not in module:
                    continue
                owner = module[: module.index("internal")]
                if importer[: len(owner)] != owner:
                    violations.append(
                        f"{path.relative_to(src_root.parent.parent)}:{node.lineno} imports {'.'.join(module)}"
                        f" (internal to {'.'.join(owner)})"
                    )
                    break
    return violations


def test_internal_is_imported_only_by_its_owner_or_a_composition_root() -> None:
    """``<pkg>/internal/`` is private to ``<pkg>`` and everything below it — any other
    importer is a composition root (``bzh:internal-visibility``). One generic check over
    every ``internal/`` under ``src/blizzard/``, no per-name roster beyond the roots."""
    violations = _internal_crossings(_SRC_DIR, exempt=_COMPOSITION_ROOTS)
    assert not violations, f"P — internal/ is importable only by its owner or a composition root: {violations}"


def _plant(tmp_path: Path, importer: str, statement: str) -> list[str]:
    """A two-package tree where ``blizzard/a/internal/x.py`` is a's private module and
    ``importer`` holds one ``statement`` — the crossings the generic check must catch."""
    src = tmp_path / "blizzard"
    for rel, text in {
        "__init__.py": "",
        "a/__init__.py": "",
        "a/internal/__init__.py": "",
        "a/internal/x.py": "VALUE = 1\n",
        "b/__init__.py": "",
        "a/sibling.py": "",
    }.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    (src / importer).write_text(f"{statement}\n")
    return _internal_crossings(src, exempt=frozenset())


@pytest.mark.parametrize(
    ("importer", "statement"),
    [
        ("b/mod.py", "from blizzard.a.internal.x import VALUE"),
        ("b/mod.py", "import blizzard.a.internal.x"),
        ("b/mod.py", "from blizzard.a import internal"),
        ("b/mod.py", "from blizzard.a.internal import x"),
        ("b/mod.py", "from ..a.internal.x import VALUE"),
        ("b/mod.py", "from ..a import internal"),
    ],
)
def test_internal_check_catches_every_import_form(tmp_path: Path, importer: str, statement: str) -> None:
    violations = _plant(tmp_path, importer, statement)
    assert len(violations) == 1
    assert violations[0].endswith("(internal to blizzard.a)")


@pytest.mark.parametrize(
    ("importer", "statement"),
    [
        ("a/sibling.py", "from blizzard.a.internal.x import VALUE"),
        ("a/sibling.py", "from .internal.x import VALUE"),
        ("a/internal/y.py", "from blizzard.a.internal import x"),
        ("a/__init__.py", "from .internal import x"),
        ("b/mod.py", "from blizzard.a import sibling"),
    ],
)
def test_internal_check_admits_the_owner_and_public_imports(tmp_path: Path, importer: str, statement: str) -> None:
    assert _plant(tmp_path, importer, statement) == []


_DOMAIN_CORE_FORBIDDEN = ("fastapi", "starlette", "sqlalchemy", "click", "httpx")


def test_domain_core_imports_no_framework_or_driver() -> None:
    """``hub/domain/`` and ``runner/domain/`` are framework-free (``bzh:domain-core``): no
    web framework, driver, CLI, or HTTP-client import."""
    violations = _violations(_HUB_DIR / "domain", _DOMAIN_CORE_FORBIDDEN) + _violations(
        _RUNNER_DOMAIN_DIR, _DOMAIN_CORE_FORBIDDEN
    )
    assert not violations, f"Q — a domain core must import no framework or driver: {violations}"


_COMPOSITION_ROOT_FILES = (
    _HUB_DIR / "app.py",
    _HUB_DIR / "composition.py",
    _HUB_DIR / "store" / "internal" / "chunk_store_factory.py",
)


# `TranscriptCaps` is a value object; `EventBroker` has a store-free `create_app` fallback.
_REPEATABLE_CONSTRUCTIONS = frozenset({"TranscriptCaps", "EventBroker"})


def _blizzard_constructions(path: Path) -> list[tuple[str, int]]:
    """Each ``Name(...)`` call in ``path`` whose ``Name`` was imported from ``blizzard.*``
    and is a class (CamelCase) — a construction, not a function call."""
    tree = ast.parse(path.read_text(), filename=str(path))
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None and node.module.startswith("blizzard.")
        for alias in node.names
    }
    return [
        (node.func.id, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in imported
        and node.func.id[:1].isupper()
        and not node.func.id.isupper()
    ]


def test_each_blizzard_class_is_constructed_once_across_the_hub_composition_roots() -> None:
    """bzh:dependency-injection: a process-scoped collaborator is built once and shared, so
    no class is constructed twice across the hub's composition-root files."""
    sites: dict[str, list[str]] = {}
    for path in _COMPOSITION_ROOT_FILES:
        for name, lineno in _blizzard_constructions(path):
            sites.setdefault(name, []).append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")
    duplicated = {
        name: where for name, where in sites.items() if len(where) > 1 and name not in _REPEATABLE_CONSTRUCTIONS
    }
    assert not duplicated, f"constructed more than once across the composition roots: {duplicated}"


def _loaded_after_importing(module: str) -> set[str]:
    code = f"import sys, json; import {module}; print(json.dumps(sorted(sys.modules)))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return set(json.loads(result.stdout))


def _loaded_after_running(argv: list[str], env: dict[str, str] | None = None) -> set[str]:
    """The modules a ``blizzard`` invocation leaves loaded, in a fresh interpreter."""
    code = (
        "import sys, json; from click.testing import CliRunner; from blizzard.cli.main import blizzard; "
        f"CliRunner().invoke(blizzard, {argv!r}); print(json.dumps(sorted(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env={**os.environ, **(env or {})}
    )
    return set(json.loads(result.stdout))


def test_a_worker_verb_loads_no_opentelemetry() -> None:
    """A worker verb is a short-lived process that must not pay for the OpenTelemetry SDK."""
    loaded = _loaded_after_running(
        ["runner", "chunk", "--help"],
        {
            "BLIZZARD_TRACEPARENT": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
            "BLIZZARD_RUNNER_URL": "http://127.0.0.1:1",
        },
    )
    heavy = sorted(m for m in loaded if m.split(".")[0] == "opentelemetry")
    assert not heavy, (
        f"a worker verb loaded {len(heavy)} opentelemetry modules, first {heavy[:3]}. Command modules "
        "load on demand through the lazy registries in blizzard.cli.main and blizzard.runner.cli "
        "(blizzard.cli.lazy_group.LazyGroup): register a new verb there by 'module:attribute' and keep "
        "its module free of module-level imports of the hub, fastapi, or telemetry stacks"
    )


def test_a_hub_client_verb_loads_no_opentelemetry() -> None:
    """A hub client verb is a short-lived process that must not pay for the hub's app or its SDK."""
    loaded = _loaded_after_running(["hub", "chunk", "list", "--hub-url", "http://127.0.0.1:1"])
    heavy = sorted(m for m in loaded if m.split(".")[0] == "opentelemetry")
    assert not heavy, (
        f"a hub client verb loaded {len(heavy)} opentelemetry modules, first {heavy[:3]}. Command modules "
        "load on demand through the lazy registry in blizzard.hub.cli (blizzard.cli.lazy_group.LazyGroup): "
        "register a new verb there by 'module:attribute' and keep a client verb's module free of "
        "module-level imports of the hub app, fastapi, or telemetry stacks"
    )


def test_an_operator_trace_loads_no_opentelemetry_and_an_untraced_command_no_emitter() -> None:
    """The operator span is hand-built OTLP/JSON: sending it never loads the SDK, and a command
    with no endpoint configured never loads the emitter."""
    argv = ["hub", "chunk", "list", "--hub-url", "http://127.0.0.1:1"]
    traced = _loaded_after_running(argv, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1"})
    untraced = _loaded_after_running(argv, {"OTEL_EXPORTER_OTLP_ENDPOINT": ""})
    heavy = sorted(m for m in traced if m.split(".")[0] == "opentelemetry")
    assert not heavy, f"an operator trace loaded {len(heavy)} opentelemetry modules, first {heavy[:3]}"
    assert "blizzard.foundation.cli_spans" in traced
    assert "blizzard.foundation.cli_spans" not in untraced


def test_trace_ids_load_no_driver_or_hub_store() -> None:
    """The runner imports the id derivation, so it loads nothing but the standard library and the kernel."""
    loaded = _loaded_after_importing("blizzard.foundation.trace_ids")
    heavy = {m for m in loaded if m.split(".")[0] in ("sqlalchemy", "httpx") or m.startswith("blizzard.hub")}
    assert not heavy, heavy


def test_trace_step_rules_load_no_http_driver_or_hub_store() -> None:
    loaded = _loaded_after_importing("blizzard.hub.domain.tracing.steps")
    heavy = {m for m in loaded if m.split(".")[0] == "httpx" or m.startswith("blizzard.hub.store")}
    assert not heavy, heavy


def test_trace_assembly_loads_no_http_driver_opentelemetry_or_hub_store() -> None:
    loaded = _loaded_after_importing("blizzard.hub.domain.tracing.assembly")
    heavy = {m for m in loaded if m.split(".")[0] in ("httpx", "opentelemetry") or m.startswith("blizzard.hub.store")}
    assert not heavy, heavy


def test_only_the_trace_bindings_import_opentelemetry() -> None:
    """No daemon installs a global provider or instrumentation: OpenTelemetry is named only by the
    shared OTLP binding, which builds finished spans and calls the exporter directly, and by
    platform tracing, which hands one per-process provider to each instrumentation explicitly."""
    homes = tuple(
        str(path.relative_to(_REPO_ROOT))
        for path in (_FOUNDATION_DIR / "trace_export" / "internal", _FOUNDATION_DIR / "platform_tracing")
    )
    violations = [v for v in _violations(_SRC_DIR, ("opentelemetry",)) if not v.startswith(homes)]
    assert not violations, violations


def test_only_the_parquet_binding_imports_pyarrow() -> None:
    home = str((_HUB_DIR / "egress" / "internal" / "parquet.py").relative_to(_REPO_ROOT))
    violations = [v for v in _violations(_SRC_DIR, ("pyarrow",)) if not v.startswith(home)]
    assert not violations, violations


@pytest.mark.parametrize("module", ["blizzard.hub.egress", "blizzard.hub.egress.factory", "blizzard.hub.app"])
def test_importing_the_egress_package_or_the_hub_loads_no_pyarrow(module: str) -> None:
    loaded = _loaded_after_importing(module)
    assert not {m for m in loaded if m.split(".")[0] == "pyarrow"}


def test_the_trace_sweep_and_cursor_import_no_store_or_opentelemetry() -> None:
    tracing = _HUB_DIR / "domain" / "tracing"
    runner_tracing = _RUNNER_DOMAIN_DIR / "tracing"
    shared_cursor = _FOUNDATION_DIR / "trace_export" / "cursor.py"
    paths = (tracing / "sweep.py", tracing / "cursor.py", shared_cursor, *sorted(runner_tracing.glob("*.py")))
    violations = [
        f"{path.relative_to(_REPO_ROOT)} imports {module}"
        for path in paths
        for module in sorted(_imported_modules(path))
        if module.split(".")[0] in ("opentelemetry", "sqlalchemy")
        or module.startswith(("blizzard.hub.store", "blizzard.runner.store"))
    ]
    assert not violations, violations


def test_the_egress_sweep_imports_no_store_filesystem_or_format_library() -> None:
    domain = _HUB_DIR / "domain" / "egress"
    violations = [
        f"{path.relative_to(_REPO_ROOT)} imports {module}"
        for path in sorted(domain.glob("*.py"))
        for module in sorted(_imported_modules(path))
        if module.split(".")[0] in ("opentelemetry", "sqlalchemy", "pyarrow", "gzip", "os", "shutil", "pathlib")
        or module.startswith(("blizzard.hub.store", "blizzard.hub.egress.internal", "blizzard.hub.egress.factory"))
    ]
    assert not violations, violations


def test_only_the_composition_root_builds_an_egress_writer() -> None:
    """Nothing in the fleet's path constructs or calls the writer: the factory and the file bindings are
    named by the composition root alone, and the sweep sees only the ``IEgressWriter`` seam."""
    homes = (
        str((_HUB_DIR / "composition.py").relative_to(_REPO_ROOT)),
        str((_HUB_DIR / "egress").relative_to(_REPO_ROOT)),
    )
    violations = [
        v
        for v in _violations(_SRC_DIR, ("blizzard.hub.egress.factory", "blizzard.hub.egress.internal"))
        if not v.startswith(homes)
    ]
    assert not violations, violations
    seam = (str((_HUB_DIR / "domain" / "egress").relative_to(_REPO_ROOT)), *homes)
    callers = [v for v in _violations(_SRC_DIR, ("blizzard.hub.egress",)) if not v.startswith(seam)]
    assert not callers, callers


_SECRET_PLAINTEXT_NAMES = frozenset({"SecretValue", "ISecretReader", "ISealedSecretRepository"})
_REQUEST_PLANE_DIRS = (_HUB_DIR / "api", _HUB_DIR / "cli", _WIRE_DIR)


def _secret_plaintext_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found.extend(a.name for a in node.names if a.name in _SECRET_PLAINTEXT_NAMES)
        elif isinstance(node, ast.Import):
            found.extend(a.name for a in node.names if a.name.rsplit(".", 1)[-1] in _SECRET_PLAINTEXT_NAMES)
    return found


def test_no_request_plane_module_imports_the_secret_plaintext_seams() -> None:
    """``bzh:secret-write-only``: a stored value is revealed only through ``ISecretReader``
    into the objects that call external systems — never on a route, a CLI verb, or a wire model."""
    violations = {
        str(path.relative_to(_REPO_ROOT)): names
        for directory in _REQUEST_PLANE_DIRS
        for path in sorted(directory.rglob("*.py"))
        if (names := _secret_plaintext_imports(path))
    }
    assert not violations, f"api/, cli/, and wire/ must not import the secret plaintext seams: {violations}"


def test_secret_plaintext_guard_catches_every_import_form(tmp_path: Path) -> None:
    for statement in (
        "from blizzard.hub.domain.secrets import SecretValue",
        "from blizzard.hub.domain.secrets import ISecretReader as Reader",
        "from blizzard.hub.domain.secrets import ISealedSecretRepository",
        "import blizzard.hub.domain.secrets.SecretValue",
    ):
        module = tmp_path / "m.py"
        module.write_text(statement + "\n")
        assert _secret_plaintext_imports(module), statement


_CONFIGURED_WRITE_NAMES = frozenset(
    {"IWriteSecretRepository", "IWriteWorkSourceRepository", "IWriteRepositoryRecordRepository"}
)
_CONFIGURED_WRITE_HOMES = frozenset(
    {
        _HUB_DIR / "domain" / "config" / "authoring.py",
        _HUB_DIR / "domain" / "config" / "work_sources.py",
        _HUB_DIR / "domain" / "config" / "repositories.py",
        _HUB_DIR / "domain" / "secrets.py",
        _HUB_DIR / "composition.py",
    }
)


def _configured_write_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        a.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for a in node.names
        if a.name in _CONFIGURED_WRITE_NAMES
    ]


def _configured_write_holders(root: Path, *, exempt: frozenset[Path]) -> dict[str, list[str]]:
    return {
        str(path.relative_to(_REPO_ROOT)): names
        for path in sorted(root.rglob("*.py"))
        if path not in exempt and "store/internal" not in path.as_posix() and (names := _configured_write_imports(path))
    }


def test_only_config_authoring_holds_a_configured_write_repository() -> None:
    """``bzh:controller-read-only``: a configured record is written through ``ConfigAuthoring``
    alone, so every write appends its change row. The adapters and the composition root
    that wire them are the only other places the write Protocols are named."""
    violations = _configured_write_holders(_SRC_DIR, exempt=_CONFIGURED_WRITE_HOMES)
    assert not violations, f"only ConfigAuthoring may name a configured write repository: {violations}"


def test_configured_write_guard_catches_a_second_holder(tmp_path: Path) -> None:
    for statement in (
        "from blizzard.hub.domain.config.work_sources import IWriteWorkSourceRepository",
        "from blizzard.hub.domain.config.repositories import IWriteRepositoryRecordRepository",
        "from blizzard.hub.domain.secrets import IWriteSecretRepository as Writer",
    ):
        module = tmp_path / "rogue.py"
        module.write_text(statement + "\n")
        assert _configured_write_imports(module), statement
