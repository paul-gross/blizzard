from __future__ import annotations

import ast
import builtins
import functools
import json
import os
import re
import subprocess
import sys
import textwrap
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
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
_WIRE_DIR = _SRC_DIR / "wire"
_AUTH_CORE_DIR = _SRC_DIR / "auth_core"

#: ``bzh:shared-kernel`` — the packages both daemons import and that import no daemon.
_KERNEL_DIRS = (_FOUNDATION_DIR, _WIRE_DIR, _AUTH_CORE_DIR)
_NON_KERNEL_PACKAGES = ("blizzard.hub", "blizzard.runner", "blizzard.cli", "blizzard.tools")

_MOVED_HOMES = {
    "RESERVED_HUB_SOURCE_NAME": "blizzard.hub.domain.kernel.hub_source",
    "KNOWN_WORK_SOURCE_PROVIDERS": "blizzard.hub.domain.config.work_sources",
    "ROUTE_TOKEN_WARN": "blizzard.hub.domain.execution.auth.route",
    "ROUTE_TOKEN_ENFORCE": "blizzard.hub.domain.execution.auth.route",
    "PRODUCES_WARN": "blizzard.hub.domain.execution.auth.produces",
    "PRODUCES_ENFORCE": "blizzard.hub.domain.execution.auth.produces",
    "ENV_FORGE_URL": "blizzard.hub.domain.config.legacy_keys",
    "ENV_FORGE_OWNER": "blizzard.hub.domain.config.legacy_keys",
    "ENV_FORGE_BASE_BRANCH": "blizzard.hub.domain.config.legacy_keys",
    "ENV_FORGE_TOKEN": "blizzard.hub.domain.config.legacy_keys",
    "LEGACY_FORGE_VARIABLES": "blizzard.hub.domain.config.legacy_keys",
    "WorkSourceConfig": "blizzard.hub.domain.config.legacy_keys",
    "LegacyKeys": "blizzard.hub.domain.config.legacy_keys",
    "EgressConfig": "blizzard.hub.domain.observability.egress.config",
    "EGRESS_DATASETS": "blizzard.hub.domain.observability.egress.config",
    "ChunkStatus": "blizzard.foundation.chunk_status",
    "TERMINAL_STATUSES": "blizzard.foundation.chunk_status",
    "ArtifactKind": "blizzard.foundation.artifacts",
    "ArtifactScope": "blizzard.foundation.artifacts",
    "Executor": "blizzard.foundation.node_steps",
    "JudgedBy": "blizzard.foundation.node_steps",
    "SessionMode": "blizzard.foundation.node_steps",
    "LEASE_MINTED": "blizzard.foundation.fact_kinds",
    "ESCALATION_RECORDED": "blizzard.foundation.fact_kinds",
    "QUESTION_ASKED": "blizzard.foundation.fact_kinds",
    "ANSWER_DELIVERED": "blizzard.foundation.fact_kinds",
    "RUNNER_LOCALLY_PAUSED": "blizzard.foundation.fact_kinds",
    "RUNNER_LOCALLY_RESUMED": "blizzard.foundation.fact_kinds",
    "USAGE_RECORDED": "blizzard.foundation.fact_kinds",
    "EVENT_RECORDED": "blizzard.foundation.fact_kinds",
    "EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED": "blizzard.foundation.fact_kinds",
    "EXTERNAL_SUBSCRIPTION_USAGE_MISSED": "blizzard.foundation.fact_kinds",
    "TIER_PREFIX": "blizzard.foundation.node_steps",
    "ApplyOutcome": "blizzard.foundation.node_steps",
    "ChunkChangeCause": "blizzard.foundation.hub_event_types",
    "ActivityChunkChangeCause": "blizzard.foundation.hub_event_types",
    "RunnerChangeKind": "blizzard.foundation.hub_event_types",
    "LeaseChangeCause": "blizzard.foundation.runner_event_types",
    "AskChangeCause": "blizzard.foundation.runner_event_types",
    "EscalationChangeCause": "blizzard.foundation.runner_event_types",
    "TakeoverChangeCause": "blizzard.foundation.runner_event_types",
    "EnvironmentChangeCause": "blizzard.foundation.runner_event_types",
    "TokenHash": "blizzard.foundation.tokens",
    "EventLogKind": "blizzard.foundation.event_log",
    "EventLogSeverity": "blizzard.foundation.event_log",
    "EVENT_LOG_SEVERITY": "blizzard.foundation.event_log",
    "RunMode": "blizzard.foundation.run_mode",
    "PROVIDER_ANTHROPIC": "blizzard.runner.subscriptions.subscription_sampler",
    "PROVIDER_OPENAI": "blizzard.runner.subscriptions.subscription_sampler",
    "Coverage": "blizzard.foundation.completion_gates",
    "ChecksGate": "blizzard.foundation.completion_gates",
    "SpanEvent": "blizzard.foundation.trace_spans",
    "FinishedSpan": "blizzard.foundation.trace_spans",
    "SpanLink": "blizzard.foundation.trace_spans",
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
    # ``IProcessProbe`` is left out: the leases domain declares its own narrower protocol by that name.
    "LinuxProcessProbe": "blizzard.runner.process.internal.linux_process_probe",
    "HarnessTelemetryPlan": "blizzard.runner.harness.harness_telemetry_plan",
    "HEARTBEAT_HOOK_COMMAND": "blizzard.runner.harness.worker_hooks",
    "SESSION_END_HOOK_COMMAND": "blizzard.runner.harness.worker_hooks",
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
    return _file_violations(sorted(root.rglob("*.py")), forbidden_prefixes)


def _file_violations(paths: Iterable[Path], forbidden_prefixes: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in paths:
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
    runner_roots = ["blizzard.runner.app", "blizzard.runner.composition", "blizzard.runner.loop_wiring"]
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


_EVENT_LOG_SERVICE_FILE = _HUB_DIR / "domain" / "chunk" / "event_log.py"
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
    _RUNNER_DIR / "store" / "internal" / "jti_cache_store.py": ("IntegrityError",),
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
        _RUNNER_DIR / "loop_wiring.py",
        _RUNNER_DIR / "cli" / "runtime.py",
        _RUNNER_DIR / "cli" / "external_usage.py",
        _RUNNER_COMPOSITION_FILE,
        _RUNNER_DIR / "cli" / "opencode.py",
        _SRC_DIR / "tools" / "invariants.py",
    }
)

_RUNNER_COMPOSITION_MODULE = "blizzard.runner.composition"


def _resolved_imports(path: Path, tree: ast.Module, src_root: Path) -> list[tuple[int, str]]:
    package = list(path.relative_to(src_root.parent).with_suffix("").parts)[:-1]
    targets: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            targets += [(node.lineno, module), *((node.lineno, f"{module}.{alias.name}") for alias in node.names)]
    return targets


def _runner_composition_imports(root: Path, *, exempt: frozenset[Path], src_root: Path = _SRC_DIR) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path in exempt:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        lines = {
            lineno
            for lineno, module in _resolved_imports(path, tree, src_root)
            if module == _RUNNER_COMPOSITION_MODULE or module.startswith(f"{_RUNNER_COMPOSITION_MODULE}.")
        }
        shown = path.relative_to(src_root.parent.parent)
        violations += [f"{shown}:{lineno} imports {_RUNNER_COMPOSITION_MODULE}" for lineno in sorted(lines)]
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
    """Only `foundation/operator_sessions/` (the Protocol's own package) may name `IWriteSessionStore` — every
    CLI module, the hub's login/logout included, takes the `SessionService` application service instead, never
    the raw write seam (``bzh:controller-read-only``)."""
    sessions_dir = _FOUNDATION_DIR / "operator_sessions"
    exempt = _COMPOSITION_ROOTS | set(sessions_dir.rglob("*.py"))
    violations = _write_session_store_accesses(_SRC_DIR, exempt=exempt)
    assert not violations, (
        f"D4 — IWriteSessionStore must be named only inside foundation/operator_sessions/: {violations}"
    )


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


_ADAPTER_PACKAGES = ("claude_code", "opencode")
_PROCESS_PROBE_FILE = _RUNNER_DIR / "process" / "probe.py"
_LINUX_PROCESS_PROBE_FILE = _RUNNER_DIR / "process" / "internal" / "linux_process_probe.py"


def _adapter_breach(module: str, *, harness: str, in_harness: bool, own: str | None, may_name: bool) -> str | None:
    """Why one imported ``module`` breaches the adapter boundary for its importer, or ``None``."""
    runner = harness.rsplit(".", 1)[0]
    loops = (f"{runner}.loop", f"{runner}.loop_wiring")
    if in_harness and any(module == loop or module.startswith(f"{loop}.") for loop in loops):
        return "harness -> loop"
    if in_harness and not may_name and module == f"{harness}.wiring":
        return "harness -> wiring, which names every adapter"
    target = next(
        (
            name
            for name in _ADAPTER_PACKAGES
            if module == f"{harness}.{name}" or module.startswith(f"{harness}.{name}.")
        ),
        None,
    )
    if target is None or target == own:
        return None
    if own is not None:
        return "adapter -> other adapter"
    return None if may_name else "adapter named outside wiring and the composition roots"


def _adapter_isolation_violations(src_root: Path, *, exempt: frozenset[Path]) -> list[str]:
    """Every import statement breaching the harness adapter boundary under ``src_root``: the
    harness core or any other module naming an adapter package, an adapter naming the other, and
    any harness module importing ``runner/loop`` or its composition root. ``harness/wiring.py`` and
    ``exempt`` may name an adapter; since wiring names both, any other harness module importing it
    reaches every adapter through it."""
    harness = f"{src_root.name}.runner.harness"
    wiring = src_root / "runner" / "harness" / "wiring.py"
    violations: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        parts = path.relative_to(src_root).parts
        in_harness = parts[:2] == ("runner", "harness")
        own = parts[2] if in_harness and len(parts) > 3 and parts[2] in _ADAPTER_PACKAGES else None
        may_name = path == wiring or path in exempt
        tree = ast.parse(path.read_text(), filename=str(path))
        breaches: dict[int, str] = {}
        for lineno, module in _resolved_imports(path, tree, src_root):
            breach = _adapter_breach(module, harness=harness, in_harness=in_harness, own=own, may_name=may_name)
            if breach is not None:
                breaches.setdefault(lineno, f"{module} ({breach})")
        shown = path.relative_to(src_root.parent.parent)
        violations += [f"{shown}:{lineno} imports {breach}" for lineno, breach in sorted(breaches.items())]
    return violations


def test_adapters_are_named_only_by_the_harness_wiring_and_the_composition_roots() -> None:
    """The harness core names no adapter, an adapter never names the other, and only
    ``harness/wiring.py`` and the composition roots import an adapter package
    (``bzh:pluggable-seams``); nothing under ``harness/`` imports ``runner/loop``."""
    violations = _adapter_isolation_violations(_SRC_DIR, exempt=_COMPOSITION_ROOTS)
    assert not violations, f"S — adapter isolation: {violations}"


def _plant_harness(tmp_path: Path, importer: str, statement: str) -> list[str]:
    """A harness tree with both adapter packages, a core module, the wiring module, and a loop
    package, where ``importer`` holds one ``statement``."""
    src = tmp_path / "blizzard"
    for rel in (
        "__init__.py",
        "runner/__init__.py",
        "runner/loop/__init__.py",
        "runner/loop_wiring.py",
        "runner/api/__init__.py",
        "runner/harness/__init__.py",
        "runner/harness/core.py",
        "runner/harness/wiring.py",
        "runner/harness/claude_code/__init__.py",
        "runner/harness/claude_code/section.py",
        "runner/harness/opencode/__init__.py",
        "runner/harness/opencode/a.py",
        "runner/harness/opencode/paths.py",
        "runner/harness/opencode/compatibility/__init__.py",
    ):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text("")
    (src / importer).parent.mkdir(parents=True, exist_ok=True)
    (src / importer).write_text(f"{statement}\n")
    return _adapter_isolation_violations(src, exempt=frozenset({src / "runner" / "composition.py"}))


@pytest.mark.parametrize(
    ("importer", "statement", "breach"),
    [
        ("runner/harness/core.py", "from blizzard.runner.harness.opencode.a import X", "adapter named outside"),
        ("runner/harness/core.py", "from blizzard.runner.harness import claude_code", "adapter named outside"),
        ("runner/harness/core.py", "from .opencode import a", "adapter named outside"),
        ("runner/harness/opencode/a.py", "from blizzard.runner.harness.claude_code.section import X", "other adapter"),
        ("runner/harness/opencode/a.py", "from ..claude_code import section", "other adapter"),
        ("runner/harness/opencode/compatibility/x.py", "from ...claude_code import section", "other adapter"),
        ("runner/api/x.py", "import blizzard.runner.harness.claude_code.section", "adapter named outside"),
        ("runner/harness/core.py", "from blizzard.runner.loop.steps import X", "harness -> loop"),
        ("runner/harness/opencode/a.py", "from blizzard.runner import loop", "harness -> loop"),
        ("runner/harness/core.py", "from blizzard.runner.loop_wiring import X", "harness -> loop"),
        ("runner/harness/claude_code/section.py", "from blizzard.runner.harness.wiring import X", "-> wiring"),
        ("runner/harness/claude_code/section.py", "from blizzard.runner.harness import wiring", "-> wiring"),
        ("runner/harness/opencode/a.py", "from ..wiring import X", "-> wiring"),
        ("runner/harness/core.py", "from .wiring import X", "-> wiring"),
    ],
)
def test_adapter_isolation_catches_every_breach(tmp_path: Path, importer: str, statement: str, breach: str) -> None:
    violations = _plant_harness(tmp_path, importer, statement)
    assert len(violations) == 1
    assert breach in violations[0]


@pytest.mark.parametrize(
    ("importer", "statement"),
    [
        ("runner/harness/wiring.py", "from blizzard.runner.harness.opencode.a import X"),
        ("runner/harness/wiring.py", "from .claude_code import section"),
        ("runner/composition.py", "from blizzard.runner.harness.claude_code.section import X"),
        ("runner/harness/opencode/compatibility/x.py", "from blizzard.runner.harness.opencode import paths"),
        ("runner/harness/opencode/a.py", "from .compatibility import x"),
        ("runner/harness/opencode/a.py", "from blizzard.runner.harness.core import X"),
        ("runner/api/x.py", "from blizzard.runner.harness.core import X"),
        ("runner/api/x.py", "from blizzard.runner.harness.wiring import X"),
    ],
)
def test_adapter_isolation_admits_wiring_roots_and_own_adapter(tmp_path: Path, importer: str, statement: str) -> None:
    assert _plant_harness(tmp_path, importer, statement) == []


def test_the_process_probe_is_declared_under_runner_process() -> None:
    """``IProcessProbe`` lives in ``runner/process/``, so a harness reaches the probe without importing
    ``runner/loop``; the ``/proc`` driver ``LinuxProcessProbe`` lives under ``runner/process/internal/``,
    and :data:`_MOVED_HOMES` routes every import of it there."""

    def declared(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(), filename=str(path))
        return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}

    assert "IProcessProbe" in declared(_PROCESS_PROBE_FILE)
    assert "LinuxProcessProbe" in declared(_LINUX_PROCESS_PROBE_FILE)


_LOOP_DIR = _RUNNER_DIR / "loop"
_LOOP_DRIVER = frozenset({_LOOP_DIR / "context.py", _LOOP_DIR / "tick.py", _LOOP_DIR / "steps.py"})
_RUNNER_EDGES = (_RUNNER_DIR / "api", _RUNNER_DIR / "cli")
_STORE_BUNDLES = frozenset({"RunnerStores", "RunnerReadStores", "IReadRunnerStore", "IWriteRunnerStore"})
_LOOP_CONTEXT = frozenset({"LoopContext"})


def _scanned(root: Path, exempt: frozenset[Path], exempt_dirs: tuple[Path, ...]) -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(), filename=str(path)))
        for path in sorted(root.rglob("*.py"))
        if path not in exempt and not any(path.is_relative_to(d) for d in exempt_dirs)
    ]


def _naming_sites(
    root: Path,
    names: frozenset[str],
    *,
    exempt: frozenset[Path],
    exempt_dirs: tuple[Path, ...] = (),
    src_root: Path = _SRC_DIR,
) -> list[str]:
    sites: set[tuple[str, int, str]] = set()
    for path, tree in _scanned(root, exempt, exempt_dirs):
        shown = str(path.relative_to(src_root.parent.parent))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in names:
                sites.add((shown, node.lineno, node.id))
            elif isinstance(node, ast.Attribute) and node.attr in names:
                sites.add((shown, node.lineno, node.attr))
            elif isinstance(node, ast.ImportFrom):
                sites |= {(shown, node.lineno, alias.name) for alias in node.names if alias.name in names}
    return [f"{shown}:{lineno} names {name}" for shown, lineno, name in sorted(sites)]


def test_only_the_loop_driver_and_composition_roots_name_loop_context() -> None:
    violations = _naming_sites(_SRC_DIR, _LOOP_CONTEXT, exempt=_LOOP_DRIVER | _COMPOSITION_ROOTS)
    assert not violations, f"bzh:narrow-seams — a step types its ctx by its own Protocol, not LoopContext: {violations}"


def test_only_the_roots_driver_and_edges_name_a_runner_store_bundle() -> None:
    exempt = _LOOP_DRIVER | _COMPOSITION_ROOTS | {_RUNNER_DIR / "stores.py"}
    violations = _naming_sites(_RUNNER_DIR, _STORE_BUNDLES, exempt=exempt, exempt_dirs=_RUNNER_EDGES)
    assert not violations, f"bzh:narrow-seams — a runner core module takes individual store seams: {violations}"


def test_no_runner_loop_module_is_a_composition_root_or_imports_composition() -> None:
    violations = [str(root) for root in _COMPOSITION_ROOTS if root.is_relative_to(_LOOP_DIR)]
    violations += _runner_composition_imports(_LOOP_DIR, exempt=frozenset())
    assert not violations, f"bzh:narrow-seams — the loop is wired from outside, never wires itself: {violations}"


def _declares_protocol(node: ast.ClassDef) -> bool:
    return any(
        (isinstance(base, ast.Name) and base.id == "Protocol")
        or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
        for base in node.bases
    )


def _annotated_names(tree: ast.Module) -> list[tuple[str, int, ast.expr | None]]:
    """Every function parameter and class field in ``tree`` as ``(name, lineno, annotation)``."""
    found: list[tuple[str, int, ast.expr | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            found += [(arg.arg, arg.lineno, arg.annotation) for arg in args]
        elif isinstance(node, ast.ClassDef):
            found += [
                (stmt.target.id, stmt.lineno, stmt.annotation)
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            ]
    return found


def _named_types(annotation: ast.expr | None) -> set[str]:
    """Every type an annotation names, bare or module-qualified, unioned or wrapped."""
    if annotation is None:
        return set()
    return {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(annotation)
        if isinstance(node, ast.Name | ast.Attribute)
    }


def _step_contexts(
    root: Path, *, exempt: frozenset[Path], exempt_dirs: tuple[Path, ...], src_root: Path = _SRC_DIR
) -> tuple[list[str], set[str]]:
    """Each step's context typing: a ``ctx`` must be a Protocol its own module declares, and any other
    parameter or field typed by a step context — ``LoopContext``, or a Protocol some ``ctx`` is typed by —
    must name one its own module declares, so renaming the parameter never smuggles in a foreign context."""
    scanned = _scanned(root, exempt, exempt_dirs)
    violations: list[str] = []
    contexts: set[str] = set()
    local_by_path: dict[Path, set[str]] = {}
    for path, tree in scanned:
        local = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and _declares_protocol(node)}
        local_by_path[path] = local
        for name, lineno, annotation in _annotated_names(tree):
            if name != "ctx":
                continue
            if isinstance(annotation, ast.Name) and annotation.id in local:
                contexts.add(annotation.id)
            else:
                shown = ast.unparse(annotation) if annotation else "nothing"
                violations.append(f"{path.relative_to(src_root.parent.parent)}:{lineno} types ctx by {shown}")
    step_contexts = contexts | _LOOP_CONTEXT
    for path, tree in scanned:
        for name, lineno, annotation in _annotated_names(tree):
            foreign = sorted((_named_types(annotation) & step_contexts) - local_by_path[path])
            if name != "ctx" and foreign:
                violations.append(
                    f"{path.relative_to(src_root.parent.parent)}:{lineno} types {name} by step context {foreign[0]}"
                )
    return violations, contexts


def _conformance_sentinels(context_file: Path) -> set[str]:
    tree = ast.parse(context_file.read_text(), filename=str(context_file))
    return {
        node.returns.id
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("_conforms_")
        and isinstance(node.returns, ast.Name)
        and len(node.args.args) == 1
        and isinstance(node.args.args[0].annotation, ast.Name)
        and node.args.args[0].annotation.id == "LoopContext"
    }


def test_every_loop_step_types_its_context_by_a_protocol_it_declares() -> None:
    exempt = _LOOP_DRIVER | _COMPOSITION_ROOTS
    violations, _ = _step_contexts(_RUNNER_DIR, exempt=exempt, exempt_dirs=_RUNNER_EDGES)
    assert not violations, f"bzh:narrow-seams — each step's ctx is a Protocol its module declares: {violations}"


def test_loop_context_has_a_conformance_sentinel_for_every_step_context() -> None:
    _, contexts = _step_contexts(_RUNNER_DIR, exempt=_LOOP_DRIVER | _COMPOSITION_ROOTS, exempt_dirs=_RUNNER_EDGES)
    missing = sorted(contexts - _conformance_sentinels(_LOOP_DIR / "context.py"))
    assert not missing, f"bzh:narrow-seams — loop/context.py proves LoopContext satisfies each step context: {missing}"


def _plant_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    src = tmp_path / "src" / "blizzard"
    for rel, text in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    return src


_TYPE_CHECKING_IMPORT = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    {}\n"


@pytest.mark.parametrize(
    "statement",
    [
        "from blizzard.runner.loop.context import LoopContext",
        "from .context import LoopContext",
        "from blizzard.runner.loop import context\nx: context.LoopContext",
        "import blizzard.runner.loop.context as c\nx: c.LoopContext",
        _TYPE_CHECKING_IMPORT.format("from blizzard.runner.loop.context import LoopContext"),
    ],
)
def test_loop_context_check_catches_every_naming_form(tmp_path: Path, statement: str) -> None:
    src = _plant_tree(tmp_path, {"runner/loop/step.py": statement})
    violations = _naming_sites(src, _LOOP_CONTEXT, exempt=frozenset(), src_root=src)
    assert len(violations) == 1
    assert violations[0].startswith("src/blizzard/runner/loop/step.py:")


def test_loop_context_check_admits_the_driver_and_other_context_names(tmp_path: Path) -> None:
    src = _plant_tree(
        tmp_path,
        {
            "runner/loop/tick.py": "from blizzard.runner.loop.context import LoopContext",
            "runner/loop/step.py": "from blizzard.runner.loop.context import LoopConfig, ResolvedSubscription",
        },
    )
    exempt = frozenset({src / "runner/loop/tick.py"})
    assert _naming_sites(src, _LOOP_CONTEXT, exempt=exempt, src_root=src) == []


_BUNDLE_FORMS = (
    "from blizzard.runner.stores import {}",
    _TYPE_CHECKING_IMPORT.format("from blizzard.runner.stores import {}"),
    "def build(stores: {}) -> None: ...",
)


@pytest.mark.parametrize("name", sorted(_STORE_BUNDLES))
@pytest.mark.parametrize("form", _BUNDLE_FORMS)
def test_store_bundle_check_catches_a_bundle_outside_the_edges(tmp_path: Path, name: str, form: str) -> None:
    src = _plant_tree(tmp_path, {"runner/leases/service.py": form.format(name)})
    violations = _naming_sites(src / "runner", _STORE_BUNDLES, exempt=frozenset(), src_root=src)
    assert violations == [f"src/blizzard/runner/leases/service.py:{len(form.splitlines())} names {name}"]


def test_store_bundle_check_admits_the_edges_and_driver(tmp_path: Path) -> None:
    statement = "from blizzard.runner.stores import RunnerStores, RunnerReadStores"
    src = _plant_tree(
        tmp_path,
        dict.fromkeys(("runner/api/route.py", "runner/cli/verb.py", "runner/loop/steps.py"), statement),
    )
    edges = (src / "runner/api", src / "runner/cli")
    exempt = frozenset({src / "runner/loop/steps.py"})
    assert _naming_sites(src / "runner", _STORE_BUNDLES, exempt=exempt, exempt_dirs=edges, src_root=src) == []


@pytest.mark.parametrize(
    "statement",
    [
        "import blizzard.runner.composition",
        "from blizzard.runner.composition import RunnerProcess",
        "from blizzard.runner import composition",
        "from .. import composition",
    ],
)
def test_loop_composition_check_catches_every_import_form(tmp_path: Path, statement: str) -> None:
    src = _plant_tree(tmp_path, {"runner/loop/step.py": statement})
    violations = _runner_composition_imports(src / "runner/loop", exempt=frozenset(), src_root=src)
    assert violations == [f"src/blizzard/runner/loop/step.py:1 imports {_RUNNER_COMPOSITION_MODULE}"]


_STEP_PROTOCOL = "from typing import Protocol\nclass StepContext(Protocol): ...\n"


@pytest.mark.parametrize(
    "body",
    [
        "def step(ctx: LoopContext) -> None: ...",
        "from blizzard.runner.lifecycle.spawn import SpawnContext\ndef step(ctx: SpawnContext) -> None: ...",
        "@dataclass\nclass Step:\n    ctx: LoopContext",
        "def step(context: LoopContext) -> None: ...",
        "@dataclass\nclass Step:\n    loop: LoopContext | None",
        "from blizzard.runner.loop import other\ndef step(context: other.OtherContext) -> None: ...",
    ],
)
def test_step_context_check_catches_a_foreign_or_bundle_context(tmp_path: Path, body: str) -> None:
    other = (
        "from typing import Protocol\nclass OtherContext(Protocol): ...\ndef other(ctx: OtherContext) -> None: ...\n"
    )
    src = _plant_tree(tmp_path, {"runner/loop/step.py": _STEP_PROTOCOL + body, "runner/loop/other.py": other})
    violations, _ = _step_contexts(src / "runner", exempt=frozenset(), exempt_dirs=(), src_root=src)
    assert len(violations) == 1
    assert violations[0].startswith("src/blizzard/runner/loop/step.py:")


def test_step_context_check_admits_a_local_protocol(tmp_path: Path) -> None:
    body = (
        "def step(ctx: StepContext) -> None: ...\n@dataclass\nclass Step:\n    ctx: StepContext\n"
        "def helper(context: StepContext, label: str) -> None: ...\n"
    )
    src = _plant_tree(tmp_path, {"runner/loop/step.py": _STEP_PROTOCOL + body})
    assert _step_contexts(src / "runner", exempt=frozenset(), exempt_dirs=(), src_root=src) == ([], {"StepContext"})


def test_conformance_check_catches_a_step_context_without_a_sentinel(tmp_path: Path) -> None:
    sentinel = "def _conforms_to_{}(ctx: LoopContext) -> {}:\n        return ctx\n"
    context = _TYPE_CHECKING_IMPORT.format(sentinel.format("other", "OtherContext"))
    src = _plant_tree(
        tmp_path,
        {"runner/loop/step.py": _STEP_PROTOCOL + "def step(ctx: StepContext) -> None: ...\n"},
    )
    _, contexts = _step_contexts(src / "runner", exempt=frozenset(), exempt_dirs=(), src_root=src)
    (src / "runner/loop/context.py").write_text(context)
    assert contexts - _conformance_sentinels(src / "runner/loop/context.py") == {"StepContext"}
    (src / "runner/loop/context.py").write_text(context + "    " + sentinel.format("step", "StepContext"))
    assert contexts - _conformance_sentinels(src / "runner/loop/context.py") == set()


_DOMAIN_CORE_FRAMEWORKS = ("fastapi", "starlette", "sqlalchemy", "click", "httpx")
_DOMAIN_CORE_STDLIB_DRIVERS = ("os", "shutil", "subprocess", "tempfile")
_DOMAIN_CORE_FORBIDDEN = (*_DOMAIN_CORE_FRAMEWORKS, *_DOMAIN_CORE_STDLIB_DRIVERS)

#: The harness binding nodes: adapters, so they may bind a process or the filesystem (the stdlib-driver half
#: of ``bzh:domain-core``) while staying held to the framework imports. An explicit set — a new binding
#: fails the gate until it is named here.
_HARNESS_BINDING_NODES = ("harness/claude_code", "harness/opencode")

#: Runner module -> the stdlib driver packages admitted for it, each a reasoned exception; an entry that
#: admits a package its module no longer imports is stale and fails the gate.
_DOMAIN_CORE_DRIVER_EXCEPTIONS: dict[Path, tuple[str, ...]] = {
    # The one reader of the daemon's own environment — the single owner ``bzh:worker-env-allowlist`` requires.
    _RUNNER_DIR / "harness" / "env_allowlist.py": ("os",),
    # The canary builds paths in, and reads its resume stdout back from, its own throwaway scratch workdir.
    _RUNNER_DIR / "selftest" / "checks.py": ("os",),
}


def _is_binding_module(path: Path, runner_dir: Path) -> bool:
    rel = path.relative_to(runner_dir).as_posix()
    return any(rel.startswith(f"{node}/") for node in _HARNESS_BINDING_NODES)


def _imports_package(modules: Iterable[str], package: str) -> bool:
    return any(module == package or module.startswith(f"{package}.") for module in modules)


def _driver_import_violations(
    runner_dir: Path, files: Iterable[Path], exceptions: Mapping[Path, tuple[str, ...]]
) -> list[str]:
    """Runner domain-core modules importing a framework, or a stdlib driver nothing admits them to; plus
    every exception entry admitting a package its module does not import. Paths read relative to ``runner_dir``."""
    violations: list[str] = []
    for path in files:
        forbidden = _DOMAIN_CORE_FRAMEWORKS if _is_binding_module(path, runner_dir) else _DOMAIN_CORE_FORBIDDEN
        admitted = exceptions.get(path, ())
        modules = _imported_modules(path)
        violations += [
            f"{path.relative_to(runner_dir)} imports {package}"
            for package in forbidden
            if package not in admitted and _imports_package(modules, package)
        ]
    for path, admitted in sorted(exceptions.items()):
        modules = _imported_modules(path) if path.exists() else set()
        violations += [
            f"{path.relative_to(runner_dir)} is exempt for {package} but does not import it"
            for package in admitted
            if not _imports_package(modules, package)
        ]
    return violations


def test_domain_core_imports_no_framework_or_driver() -> None:
    """``hub/domain/`` and every runner domain-core module are framework- and driver-free (``bzh:domain-core``):
    no web framework, database driver, CLI, or HTTP-client import, and no ``os``, ``shutil``, ``subprocess``, or
    ``tempfile`` — bar the harness binding nodes (adapters, held to the frameworks only) and the reasoned
    per-module exceptions."""
    violations = _violations(_HUB_DIR / "domain", _DOMAIN_CORE_FORBIDDEN) + _driver_import_violations(
        _RUNNER_DIR, _runner_domain_core_files(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS), _DOMAIN_CORE_DRIVER_EXCEPTIONS
    )
    assert not violations, f"Q — a domain core must import no framework or driver: {violations}"


def test_domain_core_driver_gate_flags_a_driver_and_honours_its_exemptions(tmp_path: Path) -> None:
    runner = _plant_runner(
        tmp_path,
        {
            "leases/launch.py": "import subprocess\n",
            "leases/paths.py": "import os\nimport shutil\n",
            "leases/clean.py": "import posixpath\n",
            "harness/claude_code/adapter.py": "import subprocess\nimport os\n",
            "harness/claude_code/http.py": "import httpx\n",
            "harness/wiring.py": "import tempfile\n",
        },
    )
    files = _runner_domain_core_files(runner, _PLANTED_RUNNER_LAYERS)
    flagged = _driver_import_violations(runner, files, {})
    assert "leases/launch.py imports subprocess" in flagged
    assert "harness/wiring.py imports tempfile" in flagged
    assert "harness/claude_code/http.py imports httpx" in flagged
    assert not [v for v in flagged if v.startswith(("harness/claude_code/adapter.py", "leases/clean.py"))]
    admitted = _driver_import_violations(runner, files, {runner / "leases" / "paths.py": ("os",)})
    assert "leases/paths.py imports shutil" in admitted
    assert "leases/paths.py imports os" not in admitted


def test_domain_core_driver_gate_fails_a_stale_exemption(tmp_path: Path) -> None:
    runner = _plant_runner(tmp_path, {"leases/clean.py": "import posixpath\n"})
    clean = runner / "leases" / "clean.py"
    assert _driver_import_violations(runner, [clean], {clean: ("os",)}) == [
        "leases/clean.py is exempt for os but does not import it"
    ]


_HUB_DOMAIN_DIR = _HUB_DIR / "domain"

#: Each hub domain package -> the packages it may import besides itself and ``kernel`` (``bzh:domain-package-layers``).
_DOMAIN_PACKAGE_LAYERS: dict[str, frozenset[str]] = {
    "kernel": frozenset(),
    "artifact": frozenset(),
    "config": frozenset(),
    "graph": frozenset({"artifact"}),
    "runners": frozenset(),
    "chunk": frozenset({"graph", "runners", "artifact"}),
    "execution": frozenset({"chunk", "graph", "runners", "artifact"}),
    "operations": frozenset({"execution", "chunk", "graph", "runners"}),
    "work_items": frozenset({"operations", "chunk", "graph"}),
    "garden": frozenset({"work_items", "chunk", "graph", "config"}),
    "observability": frozenset({"chunk", "graph", "runners"}),
}
_DOMAIN_SHARED_KERNEL = "kernel"


#: The hub's edge modules beside its domain — config reads and parses, delivery executes; the domain takes
#: their values as domain-owned types and their collaborators as domain Protocols.
_HUB_DOMAIN_EDGES = ("config", "delivery")


def _import_statements(path: Path, src_root: Path) -> Iterator[tuple[int, list[list[str]]]]:
    """Each import statement in ``path``, wherever it sits (function bodies and ``TYPE_CHECKING``
    blocks included), as its line and the dotted segments of every name it binds — a relative import
    resolved against the module's own package under ``src_root``."""
    package = list(path.relative_to(src_root).with_suffix("").parts)[:-1]
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            yield node.lineno, [alias.name.split(".") for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            base = [*base, *(node.module.split(".") if node.module else [])]
            yield node.lineno, [[*base, alias.name] for alias in node.names]


def _domain_layer_crossings(
    domain_dir: Path, layers: dict[str, frozenset[str]], edges: tuple[str, ...] = ()
) -> list[str]:
    """Every import of a ``blizzard.hub.domain`` package its importer's layer does not allow, wherever it
    sits in the module (function bodies and ``TYPE_CHECKING`` blocks included), resolved as
    :func:`_internal_crossings` resolves them; plus every module that sits in no declared package.
    ``edges`` names the modules beside the domain it may not import at all, compared by whole segment
    so the domain's own same-named package is never mistaken for one."""
    src_root = domain_dir.parent.parent.parent
    umbrella = list(domain_dir.relative_to(src_root).parts)
    refused = [[*umbrella[:-1], edge] for edge in edges]
    violations: list[str] = []
    for path in sorted(domain_dir.rglob("*.py")):
        rel = path.relative_to(domain_dir).parts
        if len(rel) == 1 and rel[0] != "__init__.py":
            violations.append(f"{path.name} sits in no package")
            continue
        own = rel[0] if len(rel) > 1 else None
        if own is not None and own not in layers:
            violations.append(f"{'/'.join(rel)} sits in undeclared package {own}")
            continue
        allowed = {own, _DOMAIN_SHARED_KERNEL, *layers[own]} if own is not None else set()
        for lineno, named in _import_statements(path, src_root):
            for module in named:
                if any(module[: len(edge)] == edge for edge in refused):
                    violations.append(f"{'/'.join(rel)}:{lineno} imports the edge {'.'.join(module)}")
                    break
                if module[: len(umbrella)] != umbrella:
                    continue
                target = module[len(umbrella)] if len(module) > len(umbrella) else None
                if target not in allowed:
                    violations.append(
                        f"{'/'.join(rel)}:{lineno} ({own or 'the umbrella'}) imports {'.'.join(module)}"
                        f" ({target or 'the umbrella'})"
                    )
                    break
    return violations


def _layer_cycle(layers: Mapping[str, frozenset[str]], kernel: str | None = _DOMAIN_SHARED_KERNEL) -> list[str]:
    """A cycle through the declared edges plus every package's implicit edge to the shared ``kernel``, if
    any, as the packages along it — empty when the layers are acyclic."""
    edges = {
        pkg: set(deps) | ({kernel} if kernel is not None and pkg != kernel else set()) for pkg, deps in layers.items()
    }
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(pkg: str) -> list[str]:
        state[pkg] = 1
        stack.append(pkg)
        for dep in sorted(edges.get(pkg, ())):
            if state.get(dep) == 1:
                return [*stack[stack.index(dep) :], dep]
            if dep not in state and (found := visit(dep)):
                return found
        stack.pop()
        state[pkg] = 2
        return []

    for pkg in sorted(edges):
        if pkg not in state and (found := visit(pkg)):
            return found
    return []


def test_hub_domain_packages_import_only_what_their_layer_allows() -> None:
    violations = _domain_layer_crossings(_HUB_DOMAIN_DIR, _DOMAIN_PACKAGE_LAYERS, _HUB_DOMAIN_EDGES)
    assert not violations, f"a hub domain package may import only its own layer's dependencies: {violations}"


def _domain_init_reexports(domain_dir: Path) -> list[str]:
    """Every hub domain package ``__init__.py`` holding anything beyond a docstring and the
    ``from __future__ import annotations`` line — a package re-exports nothing, so each name has
    one import path."""
    offenders: list[str] = []
    for init in sorted(domain_dir.rglob("__init__.py")):
        body = ast.parse(init.read_text()).body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        rest = [node for node in body if not (isinstance(node, ast.ImportFrom) and node.module == "__future__")]
        if rest:
            offenders.append(str(init.relative_to(domain_dir)))
    return offenders


def test_hub_domain_package_inits_re_export_nothing() -> None:
    assert _domain_init_reexports(_HUB_DOMAIN_DIR) == []


def test_a_domain_init_that_imports_a_name_is_flagged(tmp_path: Path) -> None:
    (tmp_path / "chunk").mkdir()
    (tmp_path / "chunk" / "__init__.py").write_text('"""Chunk."""\n\nfrom __future__ import annotations\n')
    (tmp_path / "garden").mkdir()
    (tmp_path / "garden" / "__init__.py").write_text('"""Garden."""\n\nfrom .model import Finding\n')
    assert _domain_init_reexports(tmp_path) == ["garden/__init__.py"]


def _module_bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    """The names a module binds at its top level — including inside a top-level ``if``, ``try``, or
    ``with`` — split into the ones it defines and the ones it only imports."""
    defined: set[str] = set()
    imported: set[str] = set()

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                defined.update(n.id for target in node.targets for n in ast.walk(target) if isinstance(n, ast.Name))
            elif isinstance(node, ast.AnnAssign | ast.AugAssign) and isinstance(node.target, ast.Name):
                defined.add(node.target.id)
            elif isinstance(node, ast.TypeAlias):
                defined.add(node.name.id)
            elif isinstance(node, ast.Import):
                imported.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(alias.asname or alias.name for alias in node.names)
            elif isinstance(node, ast.If | ast.Try | ast.With):
                walk(node.body)
                walk(getattr(node, "orelse", []))
                walk(getattr(node, "finalbody", []))
                for handler in getattr(node, "handlers", []):
                    walk(handler.body)

    walk(tree.body)
    return defined, imported - defined


def _domain_second_spellings(src_root: Path, domain_dir: Path, importer_roots: Iterable[Path]) -> list[str]:
    """Every ``from <module> import <name>`` under ``importer_roots`` whose ``<module>`` sits under
    ``domain_dir`` and only imports ``<name>`` rather than defining it — a second import path for a
    name defined elsewhere. Relative imports resolve against the importer's package when it sits
    under ``src_root``."""
    umbrella = list(domain_dir.relative_to(src_root).parts)
    bindings: dict[tuple[str, ...], set[str] | None] = {}

    def only_imported(module: tuple[str, ...]) -> set[str] | None:
        if module not in bindings:
            base = src_root.joinpath(*module)
            path = base.with_suffix(".py") if base.with_suffix(".py").is_file() else base / "__init__.py"
            bindings[module] = _module_bindings(ast.parse(path.read_text()))[1] if path.is_file() else None
        return bindings[module]

    violations: list[str] = []
    for root in importer_roots:
        for path in sorted(root.rglob("*.py")):
            package = (
                list(path.relative_to(src_root).with_suffix("").parts)[:-1] if path.is_relative_to(src_root) else None
            )
            for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level:
                    if package is None:
                        continue
                    module = [*package[: len(package) - (node.level - 1)], *(node.module or "").split(".")]
                else:
                    module = (node.module or "").split(".")
                module = [part for part in module if part]
                if module[: len(umbrella)] != umbrella:
                    continue
                reexported = only_imported(tuple(module)) or set()
                violations.extend(
                    f"{path.relative_to(root.parent)}:{node.lineno} imports {alias.name} from {'.'.join(module)}"
                    for alias in node.names
                    if alias.name in reexported
                )
    return violations


def test_each_hub_domain_name_has_one_import_path() -> None:
    violations = _domain_second_spellings(_SRC_DIR.parent, _HUB_DOMAIN_DIR, (_SRC_DIR, _TESTS_DIR))
    assert not violations, f"import a hub domain name from the module that defines it: {violations}"


def _plant_second_spelling(tmp_path: Path, rel: str, text: str) -> list[str]:
    """A two-package hub domain — ``operations/queue.py`` imports ``ChunkNotFound`` from
    ``chunk/errors.py``, which defines it — with ``text`` written at ``rel`` under ``tmp_path``."""
    src = tmp_path / "src"
    domain = src / "blizzard" / "hub" / "domain"
    for path, body in {
        domain / "__init__.py": "",
        domain / "chunk" / "__init__.py": "",
        domain / "chunk" / "errors.py": "class ChunkNotFound(Exception):\n    pass\n",
        domain / "operations" / "__init__.py": "",
        domain / "operations" / "queue.py": (
            "from typing import TYPE_CHECKING\n"
            "from blizzard.hub.domain.chunk.errors import ChunkNotFound\n"
            "from blizzard.hub.domain.chunk import errors as chunk_errors\n"
            "if TYPE_CHECKING:\n    from blizzard.hub.domain.chunk.errors import ChunkNotFound as Missing\n"
            "from blizzard.foundation.ids import Id\nId = Id\n"
            "class GroupService:\n    pass\n"
        ),
        tmp_path / rel: text,
    }.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return _domain_second_spellings(src, domain, (src / "blizzard", tmp_path / "tests"))


@pytest.mark.parametrize(
    ("rel", "text", "caught"),
    [
        ("tests/test_queue.py", "from blizzard.hub.domain.operations.queue import ChunkNotFound\n", True),
        ("tests/test_queue.py", "from blizzard.hub.domain.operations.queue import chunk_errors\n", True),
        ("tests/test_queue.py", "from blizzard.hub.domain.operations.queue import Missing\n", True),
        ("tests/test_queue.py", "def f():\n    from blizzard.hub.domain.operations.queue import ChunkNotFound\n", True),
        ("src/blizzard/hub/domain/operations/run.py", "from .queue import ChunkNotFound\n", True),
        ("src/blizzard/hub/api/chunks.py", "from ..domain.operations.queue import ChunkNotFound\n", True),
        ("tests/test_queue.py", "from blizzard.hub.domain.chunk.errors import ChunkNotFound\n", False),
        ("tests/test_queue.py", "from blizzard.hub.domain.operations.queue import GroupService, Id\n", False),
        ("tests/test_queue.py", "from blizzard.hub.domain.operations import queue\n", False),
        ("src/blizzard/hub/domain/operations/run.py", "from ..chunk.errors import ChunkNotFound\n", False),
        ("tests/test_queue.py", "from blizzard.foundation.ids import Id\n", False),
    ],
)
def test_domain_second_spelling_check_flags_an_import_through_an_importer(
    tmp_path: Path, rel: str, text: str, caught: bool
) -> None:
    violations = _plant_second_spelling(tmp_path, rel, text)
    assert len(violations) == (1 if caught else 0), violations


def test_hub_domain_package_layers_are_acyclic() -> None:
    assert _layer_cycle(_DOMAIN_PACKAGE_LAYERS) == []
    assert all(dep in _DOMAIN_PACKAGE_LAYERS for deps in _DOMAIN_PACKAGE_LAYERS.values() for dep in deps)
    packages = {p.name for p in _HUB_DOMAIN_DIR.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    assert packages == set(_DOMAIN_PACKAGE_LAYERS)


def _plant_domain(tmp_path: Path, files: dict[str, str]) -> list[str]:
    """A three-package hub domain — ``chunk`` may import neither ``garden`` nor anything but ``kernel`` —
    with ``files`` laid over it."""
    domain = tmp_path / "blizzard" / "hub" / "domain"
    for rel, text in {
        "../../__init__.py": "",
        "../__init__.py": "",
        "__init__.py": "",
        "chunk/__init__.py": "",
        "chunk/model.py": "X = 1\n",
        "garden/__init__.py": "",
        "garden/run.py": "Y = 1\n",
        "kernel/__init__.py": "",
        "kernel/unset.py": "UNSET = 1\n",
        "config/__init__.py": "",
        "config/legacy_keys.py": "Z = 1\n",
        **files,
    }.items():
        (domain / rel).parent.mkdir(parents=True, exist_ok=True)
        (domain / rel).write_text(text)
    layers = {
        "kernel": frozenset[str](),
        "chunk": frozenset[str](),
        "config": frozenset[str](),
        "garden": frozenset({"chunk", "config"}),
    }
    return _domain_layer_crossings(domain, layers, _HUB_DOMAIN_EDGES)


@pytest.mark.parametrize(
    ("rel", "text", "caught"),
    [
        ("chunk/ports.py", "from blizzard.hub.domain.garden.run import Y", True),
        ("chunk/ports.py", "import blizzard.hub.domain.garden.run", True),
        ("chunk/ports.py", "from blizzard.hub.domain import garden", True),
        ("chunk/ports.py", "from blizzard.hub.domain.garden import run", True),
        ("chunk/ports.py", "def f():\n    from blizzard.hub.domain.garden.run import Y", True),
        (
            "chunk/ports.py",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from blizzard.hub.domain.garden.run import Y",
            True,
        ),
        ("chunk/ports.py", "from ..garden.run import Y", True),
        ("chunk/ports.py", "from .. import garden", True),
        ("chunk/ports.py", "import blizzard.hub.domain", True),
        ("__init__.py", "from blizzard.hub.domain.chunk.model import X", True),
        ("stray.py", "", True),
        ("mystery/__init__.py", "", True),
        ("chunk/ports.py", "from blizzard.hub.domain.chunk.model import X", False),
        ("chunk/ports.py", "from .model import X", False),
        ("chunk/ports.py", "from blizzard.hub.domain.kernel.unset import UNSET", False),
        ("chunk/ports.py", "from blizzard.hub.domain import kernel", False),
        ("garden/ports.py", "def f():\n    from blizzard.hub.domain.chunk.model import X", False),
        ("chunk/ports.py", "import blizzard.hub.config", True),
        ("chunk/ports.py", "from blizzard.hub.delivery.hub_node import HubNodeExecutor", True),
        ("chunk/ports.py", "from ...config import X", True),
        ("chunk/ports.py", "from blizzard.hub import config", True),
        ("garden/ports.py", "from blizzard.hub.domain.config.legacy_keys import Z", False),
    ],
)
def test_domain_layer_check_counts_every_import_form(tmp_path: Path, rel: str, text: str, caught: bool) -> None:
    violations = _plant_domain(tmp_path, {rel: f"{text}\n"})
    assert len(violations) == (1 if caught else 0), violations


@pytest.mark.parametrize(
    "layers",
    [
        {"kernel": frozenset[str](), "chunk": frozenset({"garden"}), "garden": frozenset({"chunk"})},
        {"kernel": frozenset({"chunk"}), "chunk": frozenset[str]()},
    ],
)
def test_domain_layer_cycle_check_catches_a_cycle(layers: dict[str, frozenset[str]]) -> None:
    cycle = _layer_cycle(layers)
    assert cycle
    assert cycle[0] == cycle[-1]


#: Each runner node -> the nodes it may import besides itself (``bzh:domain-package-layers``); the rest are edges.
_RUNNER_PACKAGE_LAYERS: dict[str, frozenset[str]] = {
    "config_table": frozenset(),
    "node_steps": frozenset(),
    "process": frozenset(),
    "events": frozenset(),
    "environments": frozenset(),
    "harness": frozenset({"config_table", "environments", "node_steps", "process"}),
    "subscriptions": frozenset(),
    "auth": frozenset(),
    "leases": frozenset({"environments", "events", "harness", "node_steps"}),
    "hub": frozenset({"auth", "events", "harness", "leases", "node_steps"}),
    "transcripts": frozenset({"environments", "harness", "hub", "leases"}),
    "throttle": frozenset({"events", "harness", "leases"}),
    "usage": frozenset({"environments", "events", "harness", "leases", "subscriptions", "transcripts"}),
    "lifecycle": frozenset(
        {
            "auth",
            "environments",
            "events",
            "harness",
            "hub",
            "leases",
            "node_steps",
            "process",
            "throttle",
            "transcripts",
            "usage",
        }
    ),
    "operator": frozenset({"leases", "lifecycle"}),
    "tracing": frozenset({"harness", "hub", "leases", "transcripts"}),
    "selftest": frozenset({"environments", "harness", "lifecycle", "node_steps", "process"}),
    "status": frozenset({"environments", "harness", "hub", "leases", "lifecycle", "throttle"}),
    "harness/claude_code": frozenset({"config_table", "harness", "node_steps", "process", "subscriptions"}),
    "harness/opencode": frozenset({"config_table", "harness", "node_steps", "process"}),
    "harness/wiring": frozenset({"config_table", "harness", "process", "harness/claude_code", "harness/opencode"}),
    "stores": frozenset(
        {
            "auth",
            "environments",
            "harness",
            "hub",
            "leases",
            "lifecycle",
            "throttle",
            "tracing",
            "transcripts",
            "usage",
        }
    ),
    "loop": frozenset(
        {
            "process",
            "events",
            "environments",
            "harness",
            "subscriptions",
            "leases",
            "hub",
            "transcripts",
            "throttle",
            "usage",
            "lifecycle",
            "tracing",
            "stores",
        }
    ),
}
_RUNNER_EDGE_PACKAGES = frozenset({"api", "cli", "store"})
#: Top-level runner modules that assemble or serve the graph (composition roots, config loader, runtime).
_RUNNER_EDGE_MODULES = frozenset({"app", "composition", "config", "listeners", "loop_wiring", "runtime"})
_RUNNER_SUBNODES = frozenset({"harness/claude_code", "harness/opencode", "harness/wiring"})


def _runner_node(segments: Iterable[str]) -> str | None:
    """The dependency node a dotted path under ``blizzard.runner`` (given as its segments) belongs to: a
    ``harness`` subnode, else its first segment; ``None`` for the bare ``blizzard.runner`` package."""
    parts = list(segments)
    if not parts:
        return None
    if len(parts) > 1 and f"{parts[0]}/{parts[1]}" in _RUNNER_SUBNODES:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _runner_module_node(path: Path, runner_dir: Path) -> str | None:
    rel = path.relative_to(runner_dir).with_suffix("").parts
    return _runner_node(rel[:-1] if rel[-1] == "__init__" else rel)


def _runner_node_imports(
    runner_dir: Path, layers: Mapping[str, frozenset[str]]
) -> Iterator[tuple[Path, int, str, list[tuple[tuple[str, ...], str | None]]]]:
    """Each import statement of a module of a declared node, as ``(path, lineno, own node, [(module, target
    node)])`` over the ``blizzard.runner`` modules it names, resolved as :func:`_import_statements` resolves it."""
    src_root = runner_dir.parent.parent
    umbrella = list(runner_dir.relative_to(src_root).parts)
    for path in sorted(runner_dir.rglob("*.py")):
        own = _runner_module_node(path, runner_dir)
        if own is None or own not in layers:
            continue
        for lineno, named in _import_statements(path, src_root):
            targets = [
                (tuple(module), _runner_node(module[len(umbrella) :]))
                for module in named
                if module[: len(umbrella)] == umbrella
            ]
            if targets:
                yield path, lineno, own, targets


def _runner_layer_crossings(runner_dir: Path, layers: Mapping[str, frozenset[str]]) -> list[str]:
    """Every ``blizzard.runner`` import by a module of a declared node that its node's row does not allow,
    wherever it sits in the module, resolved as :func:`_import_statements` resolves it — an import of a module
    that is no node at all (``config``, ``composition``, ``api``, the bare package) included."""
    violations: list[str] = []
    for path, lineno, own, targets in _runner_node_imports(runner_dir, layers):
        allowed = {own, *layers[own]}
        crossing = next(((module, target) for module, target in targets if target not in allowed), None)
        if crossing is not None:
            module, target = crossing
            violations.append(
                f"{path.relative_to(runner_dir)}:{lineno} ({own}) imports {'.'.join(module)}"
                f" ({target or 'the bare package'})"
            )
    return violations


def _unused_runner_edges(runner_dir: Path, layers: Mapping[str, frozenset[str]]) -> list[str]:
    """Every declared edge no import walks: slack a sideways or upward import could later pass along unreviewed."""
    used = {
        (own, target)
        for _, _, own, targets in _runner_node_imports(runner_dir, layers)
        for _, target in targets
        if target is not None and target != own
    }
    return sorted(f"{own} -> {dep}" for own, deps in layers.items() for dep in deps if (own, dep) not in used)


def _undeclared_runner_nodes(runner_dir: Path, layers: Mapping[str, frozenset[str]]) -> list[str]:
    """Every runner package outside the edge packages, and every top-level runner module outside the edge
    modules, that maps to no node — a new package or module enters the graph."""
    undeclared: list[str] = []
    for init in sorted(runner_dir.rglob("__init__.py")):
        rel = init.parent.relative_to(runner_dir).parts
        if rel and rel[0] not in _RUNNER_EDGE_PACKAGES and _runner_node(rel) not in layers:
            undeclared.append("/".join(rel))
    for module in sorted(runner_dir.glob("*.py")):
        if module.stem not in {"__init__", *_RUNNER_EDGE_MODULES} and module.stem not in layers:
            undeclared.append(module.name)
    return undeclared


def _runner_domain_core_files(runner_dir: Path, layers: Mapping[str, frozenset[str]]) -> list[Path]:
    """Every module of a runner node outside an ``internal/`` package — the runner's ``bzh:domain-core``: a
    concept package's public surface holds its models, ports, and the services carrying its rules, while the
    adapters binding a framework or driver sit in its ``internal/`` (``bzh:internal-visibility``)."""
    return [
        path
        for path in sorted(runner_dir.rglob("*.py"))
        if _runner_module_node(path, runner_dir) in layers and "internal" not in path.relative_to(runner_dir).parts[:-1]
    ]


_DOMAIN_TAKES_OBJECTS_RULE = _REPO_ROOT / "contracts" / "ast-grep" / "rules" / "domain-takes-objects.yml"


def _rule_list(text: str, key: str) -> set[str]:
    """The ``- item`` entries under one top-level ``key:`` of an ast-grep rule file."""
    lines = text.splitlines()
    start = lines.index(f"{key}:") + 1
    items: set[str] = set()
    for line in lines[start:]:
        if not line.startswith("  - "):
            break
        items.add(line.removeprefix("  - ").strip())
    return items


def _runner_domain_core_globs(runner_dir: Path, layers: Mapping[str, frozenset[str]]) -> set[str]:
    """The ast-grep ``files:`` globs, relative to ``runner_dir``, that reach exactly
    :func:`_runner_domain_core_files` once ``**/internal/**`` is ignored: one per top-level node, a
    package's tree or a module's file."""
    tops = {node.split("/")[0] for node in layers}
    return {f"{top}/**" if (runner_dir / top).is_dir() else f"{top}.py" for top in tops}


def test_domain_takes_objects_reaches_the_runner_domain_core() -> None:
    """``bzh:domain-takes-objects`` scopes the runner by the same key as ``bzh:domain-core``, so a new
    concept package — or a module added to one — is in scope with no edit to the rule."""
    text = _DOMAIN_TAKES_OBJECTS_RULE.read_text()
    shown = _RUNNER_DIR.relative_to(_REPO_ROOT).as_posix()
    runner_globs = {
        glob.removeprefix(f"{shown}/") for glob in _rule_list(text, "files") if glob.startswith(f"{shown}/")
    }
    assert runner_globs == _runner_domain_core_globs(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS)
    assert f"{shown}/**/internal/**" in _rule_list(text, "ignores")


def test_the_runner_domain_core_globs_follow_the_layer_table(tmp_path: Path) -> None:
    runner = _plant_runner(tmp_path, {})
    assert _runner_domain_core_globs(runner, _PLANTED_RUNNER_LAYERS) == {
        "config_table.py",
        "leases/**",
        "hub/**",
        "harness/**",
    }


def _loop_reach_files(runner_dir: Path, layers: Mapping[str, frozenset[str]]) -> list[Path]:
    """Every module of ``loop`` and of each node its layer row lets it import — the tick and every step it runs."""
    tops = {node.split("/")[0] for node in ("loop", *layers["loop"])}
    return sorted(
        path
        for top in tops
        for path in ((runner_dir / top).rglob("*.py") if (runner_dir / top).is_dir() else [runner_dir / f"{top}.py"])
    )


def test_no_module_the_loop_reaches_imports_chunk_detail() -> None:
    """Every per-chunk read the tick and its steps make goes through ``IChunkViews``/``ChunkStatusView``,
    never the full ``ChunkDetail`` aggregate — across ``loop`` and every node its layer row reaches."""
    offenders = [
        str(path.relative_to(_REPO_ROOT))
        for path in _loop_reach_files(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS)
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path)))
        if isinstance(node, ast.ImportFrom) and any(alias.name == "ChunkDetail" for alias in node.names)
    ]
    assert offenders == []


def test_the_loop_reach_follows_the_layer_table(tmp_path: Path) -> None:
    runner = _plant_runner(tmp_path, {"loop/__init__.py": "", "loop/tick.py": "", "api/detail.py": ""})
    reach = {
        path.relative_to(runner).as_posix()
        for path in _loop_reach_files(runner, {**_PLANTED_RUNNER_LAYERS, "loop": frozenset({"config_table", "hub"})})
    }
    assert reach == {"loop/__init__.py", "loop/tick.py", "config_table.py", "hub/__init__.py", "hub/client.py"}


def test_runner_packages_import_only_what_their_layer_allows() -> None:
    violations = _runner_layer_crossings(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS)
    assert not violations, f"a runner package may import only its own layer's dependencies: {violations}"


def test_every_runner_layer_edge_is_one_the_code_uses() -> None:
    unused = _unused_runner_edges(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS)
    assert not unused, f"a runner layer row may allow only edges today's code walks; drop: {unused}"


def test_runner_package_layers_are_acyclic() -> None:
    assert _layer_cycle(_RUNNER_PACKAGE_LAYERS, kernel=None) == []
    assert all(dep in _RUNNER_PACKAGE_LAYERS for deps in _RUNNER_PACKAGE_LAYERS.values() for dep in deps)
    undeclared = _undeclared_runner_nodes(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS)
    assert not undeclared, (
        f"every runner package outside api/, cli/ and store/, and every top-level module outside the edge "
        f"modules, is a declared node: {undeclared}"
    )


def test_runner_domain_core_selection_holds_the_lease_model() -> None:
    assert _RUNNER_DIR / "leases" / "__init__.py" in _runner_domain_core_files(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS)


#: A planted runner exercising the node, subnode, and edge kinds of :data:`_RUNNER_PACKAGE_LAYERS`.
_PLANTED_RUNNER_LAYERS: dict[str, frozenset[str]] = {
    "config_table": frozenset(),
    "leases": frozenset(),
    "hub": frozenset({"leases"}),
    "harness": frozenset({"config_table"}),
    "harness/claude_code": frozenset({"harness"}),
    "harness/wiring": frozenset({"harness", "harness/claude_code"}),
}


def _plant_runner(tmp_path: Path, files: dict[str, str]) -> Path:
    runner = tmp_path / "blizzard" / "runner"
    for rel, text in {
        "../__init__.py": "",
        "__init__.py": "",
        "config.py": "C = 1\n",
        "config_table.py": "T = 1\n",
        "leases/__init__.py": "",
        "leases/record.py": "R = 1\n",
        "hub/__init__.py": "",
        "hub/client.py": "H = 1\n",
        "harness/__init__.py": "",
        "harness/wiring.py": "W = 1\n",
        "harness/claude_code/__init__.py": "",
        "harness/claude_code/section.py": "S = 1\n",
        **files,
    }.items():
        (runner / rel).parent.mkdir(parents=True, exist_ok=True)
        (runner / rel).write_text(text)
    return runner


@pytest.mark.parametrize(
    ("rel", "text", "caught"),
    [
        ("leases/ports.py", "from blizzard.runner.hub.client import H", True),
        ("leases/ports.py", "import blizzard.runner.hub.client", True),
        ("leases/ports.py", "from blizzard.runner import hub", True),
        ("leases/ports.py", "def f():\n    from blizzard.runner.hub.client import H", True),
        ("leases/ports.py", _TYPE_CHECKING_IMPORT.format("from blizzard.runner.hub.client import H"), True),
        ("leases/ports.py", "from ..hub.client import H", True),
        ("leases/ports.py", "from .. import hub", True),
        ("leases/ports.py", "from blizzard.runner.config import C", True),
        ("leases/ports.py", "import blizzard.runner", True),
        ("leases/__init__.py", "from blizzard.runner.hub.client import H", True),
        ("harness/claude_code/plan.py", "from blizzard.runner.harness.wiring import W", True),
        ("harness/claude_code/plan.py", "from blizzard.runner.harness import wiring", True),
        ("harness/claude_code/plan.py", "from blizzard.runner.config_table import T", True),
        ("harness/core.py", "from blizzard.runner.harness.claude_code.section import S", True),
        ("hub/ports.py", "from blizzard.runner.leases.record import R", False),
        ("hub/ports.py", "from ..leases import record", False),
        ("leases/ports.py", "from .record import R", False),
        ("harness/claude_code/plan.py", "from blizzard.runner.harness import H", False),
        ("harness/claude_code/plan.py", "from .section import S", False),
        ("harness/wiring.py", "from blizzard.runner.harness.claude_code.section import S", False),
        ("harness/core.py", "from blizzard.runner.config_table import T", False),
        ("config.py", "from blizzard.runner.hub.client import H", False),
        ("leases/ports.py", "import blizzard.hub.config", False),
    ],
)
def test_runner_layer_check_counts_every_import_form(tmp_path: Path, rel: str, text: str, caught: bool) -> None:
    violations = _runner_layer_crossings(_plant_runner(tmp_path, {rel: f"{text}\n"}), _PLANTED_RUNNER_LAYERS)
    assert len(violations) == (1 if caught else 0), violations


@pytest.mark.parametrize(
    ("rel", "caught"),
    [
        ("mystery/__init__.py", True),
        ("mystery.py", True),
        ("harness/contracts/__init__.py", False),
        ("api/routes/__init__.py", False),
        ("composition.py", False),
        ("harness/stray.py", False),
    ],
)
def test_runner_node_check_catches_an_undeclared_package_or_module(tmp_path: Path, rel: str, caught: bool) -> None:
    undeclared = _undeclared_runner_nodes(_plant_runner(tmp_path, {rel: ""}), _PLANTED_RUNNER_LAYERS)
    assert len(undeclared) == (1 if caught else 0), undeclared


def test_runner_unused_edge_check_catches_an_edge_no_import_walks(tmp_path: Path) -> None:
    runner = _plant_runner(
        tmp_path,
        {
            "hub/ports.py": "from blizzard.runner.leases.record import R\n",
            "harness/core.py": "from blizzard.runner.config_table import T\n",
            "harness/claude_code/plan.py": "from blizzard.runner.harness import H\n",
            "harness/wiring.py": "from .claude_code import section\nfrom blizzard.runner import harness\n",
        },
    )
    assert _unused_runner_edges(runner, _PLANTED_RUNNER_LAYERS) == []
    widened = {**_PLANTED_RUNNER_LAYERS, "hub": frozenset({"leases", "config_table"})}
    assert _unused_runner_edges(runner, widened) == ["hub -> config_table"]


def test_runner_layer_cycle_check_catches_a_cycle() -> None:
    cycle = _layer_cycle({"hub": frozenset({"leases"}), "leases": frozenset({"hub"})}, kernel=None)
    assert cycle
    assert cycle[0] == cycle[-1]


def test_runner_domain_core_selection_keys_on_every_node_module_outside_internal(tmp_path: Path) -> None:
    runner = _plant_runner(
        tmp_path,
        {
            "leases/model.py": "from blizzard.foundation.roles import domain_model\n@domain_model\nclass Lease: ...\n",
            "leases/requeue.py": "import fastapi\nclass RequeueService: ...\n",
            "leases/internal/__init__.py": "",
            "leases/internal/http_leases.py": "import httpx\n",
            "hub/internal/http_hub.py": "import httpx\n",
            "config.py": "from typing import Protocol\nclass IConfig(Protocol): ...\n",
            "api/routes.py": "import fastapi\n",
        },
    )
    selected = {
        path.relative_to(runner).as_posix() for path in _runner_domain_core_files(runner, _PLANTED_RUNNER_LAYERS)
    }
    assert "leases/requeue.py" in selected
    assert "leases/model.py" in selected
    assert not {"leases/internal/http_leases.py", "hub/internal/http_hub.py", "config.py", "api/routes.py"} & selected


_COMPOSITION_ROOT_FILES = (
    _HUB_DIR / "app.py",
    _HUB_DIR / "composition.py",
    _HUB_DIR / "store" / "internal" / "chunk_store_factory.py",
)


# `TranscriptCaps` is a value object; `EventBroker` has a store-free `create_app` fallback; `ConfigError` is a
# refusal, raised wherever a boot check or the config edge refuses.
_REPEATABLE_CONSTRUCTIONS = frozenset({"TranscriptCaps", "EventBroker", "ConfigError"})


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


def _adapter_modules_beyond_sections(loaded: set[str]) -> list[str]:
    """Every loaded harness adapter module other than an adapter package and its ``section`` module."""
    adapters = tuple(f"blizzard.runner.harness.{name}" for name in _ADAPTER_PACKAGES)
    return sorted(
        module
        for module in loaded
        if any(module.startswith(f"{adapter}.") for adapter in adapters)
        and module not in {f"{adapter}.section" for adapter in adapters}
    )


@pytest.mark.parametrize(
    "load",
    [
        lambda: _loaded_after_importing("blizzard.runner.cli.worker"),
        lambda: _loaded_after_running(["runner", "heartbeat", "--help"]),
    ],
    ids=["hook-entry-point", "heartbeat-verb"],
)
def test_a_worker_hook_loads_no_harness_adapter_beyond_its_section(load: Callable[[], set[str]]) -> None:
    """The per-tool-call ``heartbeat`` and ``session-end`` hooks load no adapter's declaration graph, whatever
    they reach of ``harness/wiring.py`` (``bzh:pluggable-seams``)."""
    loaded = load()
    adapters = _adapter_modules_beyond_sections(loaded)
    assert not adapters, (
        f"a worker hook loaded {len(adapters)} harness adapter modules, first {adapters[:3]}: keep "
        "harness/wiring.py's module-level imports to each adapter's section module and resolve a "
        "declaration through harness_catalog()"
    )


def test_the_adapter_module_filter_keeps_only_what_lies_beyond_a_section() -> None:
    loaded = {
        "blizzard.runner.harness.claude_code",
        "blizzard.runner.harness.claude_code.section",
        "blizzard.runner.harness.opencode.section",
        "blizzard.runner.harness.opencode.compatibility.probe",
        "blizzard.runner.harness.claude_code.declaration",
        "blizzard.runner.harness.wiring",
    }
    assert _adapter_modules_beyond_sections(loaded) == [
        "blizzard.runner.harness.claude_code.declaration",
        "blizzard.runner.harness.opencode.compatibility.probe",
    ]


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
    loaded = _loaded_after_importing("blizzard.hub.domain.observability.tracing.steps")
    heavy = {m for m in loaded if m.split(".")[0] == "httpx" or m.startswith("blizzard.hub.store")}
    assert not heavy, heavy


def test_trace_assembly_loads_no_http_driver_opentelemetry_or_hub_store() -> None:
    loaded = _loaded_after_importing("blizzard.hub.domain.observability.tracing.assembly")
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
    tracing = _HUB_DIR / "domain" / "observability" / "tracing"
    runner_tracing = _RUNNER_DIR / "tracing"
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
    domain = _HUB_DIR / "domain" / "observability" / "egress"
    violations = [
        f"{path.relative_to(_REPO_ROOT)} imports {module}"
        for path in sorted(domain.glob("*.py"))
        for module in sorted(_imported_modules(path))
        # `config.py` only declares the export's directory as a `Path` value; it never touches the filesystem.
        if module.split(".")[0] in ("opentelemetry", "sqlalchemy", "pyarrow", "gzip", "os", "shutil")
        or (module == "pathlib" and path.name != "config.py")
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
    seam = (str((_HUB_DIR / "domain" / "observability" / "egress").relative_to(_REPO_ROOT)), *homes)
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
        "from blizzard.hub.domain.config.secrets import SecretValue",
        "from blizzard.hub.domain.config.secrets import ISecretReader as Reader",
        "from blizzard.hub.domain.config.secrets import ISealedSecretRepository",
        "import blizzard.hub.domain.config.secrets.SecretValue",
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
        _HUB_DIR / "domain" / "config" / "secrets.py",
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
        "from blizzard.hub.domain.config.secrets import IWriteSecretRepository as Writer",
    ):
        module = tmp_path / "rogue.py"
        module.write_text(statement + "\n")
        assert _configured_write_imports(module), statement


# Data roles (``bzh:data-roles``). Every check reads source only — no scanned module is imported.

_ROLE_MARKERS = frozenset({"domain_model", "dto", "adapter_model", "collaborator"})
_ROLES_MODULE = "blizzard.foundation.roles"
_PORT_NAME = re.compile(r"^I[A-Z]")
_EXCEPTION_NAMES = frozenset(
    name for name, value in vars(builtins).items() if isinstance(value, type) and issubclass(value, BaseException)
)
_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"})
_PYDANTIC_BASES = frozenset({"BaseModel", "RootModel", "BaseSettings"})
_FUNCTIONAL_DATA_CLASSES = frozenset({"NamedTuple", "namedtuple", "make_dataclass"})
#: Concrete infrastructure-handle packages: the framework and driver imports ``bzh:domain-core`` bars.
_DRIVER_PACKAGES = frozenset(_DOMAIN_CORE_FORBIDDEN)
_DIRECT_WRAPPERS = frozenset({"Optional", "Union", "Annotated", "InitVar", "Final"})
_PROPERTY_DECORATORS = frozenset({"property", "cached_property"})
_CONSTRUCTOR_DUNDERS = frozenset({"__init__", "__new__", "__post_init__"})
#: The app boundary, relative to the source root: the packages where the app meets the outside.
_APP_BOUNDARY = tuple(Path(p) for p in ("wire", "hub/api", "hub/cli", "runner/api", "runner/cli", "cli"))
#: The adapters that send or receive the ``wire/`` contract itself: the runner's hub client, the
#: two daemons' SSE brokers, and the runner's read-back of its own shipped transcript segments.
_WIRE_ADAPTERS = tuple(
    Path(p)
    for p in (
        "runner/hub",
        "hub/events/broker.py",
        "runner/events/broker.py",
        "runner/transcripts/internal/http_archived_transcript_repository.py",
        "runner/transcripts/internal/segment_projection.py",
    )
)


def _terminal(expr: ast.AST | None) -> str | None:
    """The last dotted name an expression spells, through any subscript or call."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    if isinstance(expr, ast.Subscript):
        return _terminal(expr.value)
    if isinstance(expr, ast.Call):
        return _terminal(expr.func)
    return None


def _annotation(expr: ast.AST | None) -> ast.AST | None:
    """An annotation with its string (forward-reference) form parsed back into an expression."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        try:
            return ast.parse(expr.value, mode="eval").body
        except SyntaxError:
            return None
    return expr


def _subscript_args(node: ast.Subscript) -> list[ast.expr]:
    return list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]


def _direct_types(expr: ast.AST | None) -> Iterator[str]:
    """The type names an annotation *is*: itself, or an arm of a union, ``Optional``,
    ``Annotated``, ``InitVar``, or ``Final`` — never a name nested in a container's arguments.
    A dotted name counts both bare and dotted."""
    node = _annotation(expr)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        yield from _direct_types(node.left)
        yield from _direct_types(node.right)
    elif isinstance(node, ast.Subscript) and _terminal(node.value) in _DIRECT_WRAPPERS:
        args = _subscript_args(node)
        for arg in args[:1] if _terminal(node.value) == "Annotated" else args:
            yield from _direct_types(arg)
    elif isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Attribute):
        yield node.attr
        yield ast.unparse(node)


def _mentioned_types(expr: ast.AST | None, aliases: Mapping[str, list[ast.expr]]) -> set[str]:
    """Every type name an annotation mentions at any depth — generic arguments, unions, and
    string forms included, ``Literal`` values and ``Annotated`` metadata excluded — with
    module-level type aliases expanded by name. A dotted name counts both bare and dotted."""
    names: set[str] = set()
    stack: list[ast.AST | None] = [expr]
    while stack:
        node = _annotation(stack.pop())
        if node is None:
            continue
        if isinstance(node, ast.Subscript) and _terminal(node.value) in ("Literal", "Annotated"):
            if _terminal(node.value) == "Annotated":
                stack.append(_subscript_args(node)[0])
            continue
        if isinstance(node, ast.Attribute):
            names.add(ast.unparse(node))
        if isinstance(node, ast.Name | ast.Attribute):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name not in names:
                names.add(name)
                stack.extend(aliases.get(name, ()))
            continue
        stack.extend(ast.iter_child_nodes(node))
    return names


def _absolute_module(path: Path, src_root: Path, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = list(path.relative_to(src_root.parent).with_suffix("").parts)[:-1]
    base = package[: len(package) - (node.level - 1)]
    return ".".join([*base, *(node.module.split(".") if node.module else [])])


def _marker_bindings(path: Path, src_root: Path, tree: ast.Module) -> tuple[dict[str, str], set[str]]:
    """The module-level names bound to a role marker imported from ``blizzard.foundation.roles``,
    and the dotted spellings bound to that module — in statement order, so a later rebinding of a
    marker's name (a local ``def dto``, an import from elsewhere) unbinds it."""
    markers: dict[str, str] = {}
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            source = _absolute_module(path, src_root, node)
            for alias in node.names:
                local = alias.asname or alias.name
                markers.pop(local, None)
                if source == _ROLES_MODULE and alias.name in _ROLE_MARKERS:
                    markers[local] = alias.name
                elif f"{source}.{alias.name}" == _ROLES_MODULE:
                    modules.add(local)
        elif isinstance(node, ast.Import):
            modules.update(alias.asname or alias.name for alias in node.names if alias.name == _ROLES_MODULE)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            markers.pop(node.name, None)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    markers.pop(target.id, None)
    return markers, modules


@dataclass(frozen=True)
class _ClassSite:
    path: Path
    node: ast.ClassDef
    markers: tuple[str, ...]

    @property
    def bases(self) -> list[str]:
        return [name for base in self.node.bases if (name := _terminal(base)) is not None]

    @property
    def is_data(self) -> bool:
        decorated = any(_terminal(d) == "dataclass" for d in self.node.decorator_list)
        return decorated or "NamedTuple" in self.bases


@dataclass(frozen=True)
class _Param:
    name: str
    annotation: ast.expr | None
    required: bool


def _field_param(item: ast.AnnAssign) -> _Param | None:
    """A data-class field as a constructor parameter: required unless it has a default, a
    default factory, or ``init=False``. ``ClassVar`` and ``KW_ONLY`` declare no field."""
    if not isinstance(item.target, ast.Name) or _terminal(_annotation(item.annotation)) in ("ClassVar", "KW_ONLY"):
        return None
    value = item.value
    if value is None:
        required = True
    elif isinstance(value, ast.Call) and _terminal(value.func) == "field":
        keywords = {k.arg: k.value for k in value.keywords}
        init = keywords.get("init")
        init_false = isinstance(init, ast.Constant) and init.value is False
        required = not init_false and "default" not in keywords and "default_factory" not in keywords
    else:
        required = False
    return _Param(item.target.id, item.annotation, required)


def _self_reads(node: ast.ClassDef) -> set[str]:
    """The attribute names the class's own methods read off ``self``."""
    return {
        sub.attr
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        for sub in ast.walk(item)
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "self"
    }


class _RoleScan:
    """Every class under one source root, its declared markers, and its inferred role."""

    def __init__(self, src_root: Path) -> None:
        self.src_root = src_root
        self.sites: list[_ClassSite] = []
        self.by_name: dict[str, list[_ClassSite]] = defaultdict(list)
        self.aliases: dict[str, list[ast.expr]] = defaultdict(list)
        self.functional: list[tuple[Path, int, str]] = []
        self.imports: dict[Path, dict[str, tuple[str, str]]] = {}
        self.modules: dict[Path, dict[str, str]] = {}
        self.functions: dict[Path, set[str]] = {}
        for path in sorted(src_root.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            names, modules = _marker_bindings(path, src_root, tree)
            self.imports[path] = {
                alias.asname or alias.name: (_absolute_module(path, src_root, node), alias.name)
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                for alias in node.names
            }
            self.modules[path] = {
                alias.asname or alias.name.split(".")[0]: alias.name if alias.asname else alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            self.functions[path] = {
                node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            for node in tree.body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    self.aliases[node.targets[0].id].append(node.value)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                    if _terminal(node.annotation) == "TypeAlias":
                        self.aliases[node.target.id].append(node.value)
                elif isinstance(node, ast.TypeAlias):
                    self.aliases[node.name.id].append(node.value)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    markers = tuple(
                        role for d in node.decorator_list if (role := self._marker_role(d, names, modules)) is not None
                    )
                    site = _ClassSite(path, node, markers)
                    self.sites.append(site)
                    self.by_name[node.name].append(site)
                elif isinstance(node, ast.Call) and _terminal(node.func) in _FUNCTIONAL_DATA_CLASSES:
                    self.functional.append((path, node.lineno, _terminal(node.func) or ""))
                elif isinstance(node, ast.Call) and _terminal(node.func) == "dataclass" and node.args:
                    self.functional.append((path, node.lineno, "dataclass"))
        self.ports = frozenset(
            name
            for name, sites in self.by_name.items()
            if _PORT_NAME.match(name) and any("Protocol" in site.bases for site in sites)
        )
        self.orchestration: set[int] = set()
        changed = True
        while changed:
            changed = False
            for site in self.sites:
                if id(site.node) not in self.orchestration and self._shape_role(site) is None and self._holds(site):
                    self.orchestration.add(id(site.node))
                    changed = True

    @staticmethod
    def _marker_role(decorator: ast.expr, names: Mapping[str, str], modules: set[str]) -> str | None:
        if isinstance(decorator, ast.Name):
            return names.get(decorator.id)
        if isinstance(decorator, ast.Attribute) and decorator.attr in _ROLE_MARKERS:
            return decorator.attr if ast.unparse(decorator.value) in modules else None
        return None

    def _module(self, path: Path) -> str:
        parts = list(path.relative_to(self.src_root.parent).with_suffix("").parts)
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    def declared(self, name: str, path: Path) -> list[_ClassSite]:
        """The classes ``name`` can mean inside ``path``: its own declaration there, else the one
        in the module it is imported from, else every class of that name under the root."""
        local = [site for site in self.by_name.get(name, []) if site.path == path]
        if local:
            return local
        module, original = self.imports.get(path, {}).get(name, ("", name))
        candidates = self.by_name.get(original, [])
        return [site for site in candidates if self._module(site.path) == module] or candidates

    def _shape_role(self, site: _ClassSite, seen: frozenset[int] = frozenset()) -> str | None:
        """port, error, enum, or pydantic — the roles a class's bases give it. A base declared
        under the root counts only when every class it can mean has the role."""
        if "Protocol" in site.bases:
            return "port"
        for base in site.bases:
            declared = self.declared(base, site.path)
            if declared and id(site.node) not in seen:
                roles = {self._shape_role(other, seen | {id(site.node)}) for other in declared}
                if len(roles) == 1 and (role := roles.pop()) in ("error", "enum", "pydantic"):
                    return role
            elif not declared and (base in _EXCEPTION_NAMES or base.endswith(("Error", "Exception", "Warning"))):
                return "error"
            elif not declared and base in _ENUM_BASES:
                return "enum"
            elif not declared and base in _PYDANTIC_BASES:
                return "pydantic"
        return None

    def inferred_role(self, site: _ClassSite) -> str | None:
        return "orchestration" if id(site.node) in self.orchestration else self._shape_role(site)

    def is_collaborator(self, name: str, path: Path) -> bool:
        """Whether ``name``, spelled in ``path``, is a port (an ``I[A-Z]…`` name a Protocol under
        the root declares), a clock (a name ending in ``Clock``), a driver handle (a type
        imported from a framework or driver package), a class marked ``@collaborator`` or
        inferring orchestration everywhere it can mean, or a Protocol that exposes a
        collaborator."""
        return self._is_collaborator(name, path, frozenset())

    def is_driver(self, name: str, path: Path) -> bool:
        """Whether ``name``, bare or dotted, spells a type imported into ``path`` from a framework
        or driver package."""
        root, _, rest = name.partition(".")
        source = self.modules.get(path, {}).get(root) if rest else None
        if source is None:
            module, original = self.imports.get(path, {}).get(root, ("", ""))
            source = f"{module}.{original}" if rest and module else module
        return source.split(".")[0] in _DRIVER_PACKAGES

    def _is_collaborator(self, name: str, path: Path, seen: frozenset[int]) -> bool:
        if self.is_driver(name, path):
            return True
        if "." in name:
            return False
        if name in self.ports or name.endswith("Clock"):
            return True
        declared = self.declared(name, path)
        orchestration = bool(declared) and all(id(site.node) in self.orchestration for site in declared)
        if orchestration or (bool(declared) and all("collaborator" in site.markers for site in declared)):
            return True
        protocols = bool(declared) and all("Protocol" in site.bases for site in declared)
        return protocols and all(self._exposes_collaborator(site, seen) for site in declared)

    def _exposes_collaborator(self, site: _ClassSite, seen: frozenset[int]) -> bool:
        """Whether a Protocol, through an attribute or property of its own or of a Protocol base,
        hands out a collaborator — a narrowed view of a context that holds collaborators."""
        if id(site.node) in seen:
            return False
        seen = seen | {id(site.node)}
        for item in site.node.body:
            if isinstance(item, ast.AnnAssign):
                annotation = item.annotation
            elif isinstance(item, ast.FunctionDef) and any(_terminal(d) == "property" for d in item.decorator_list):
                annotation = item.returns
            else:
                continue
            if any(self._is_collaborator(name, site.path, seen) for name in _direct_types(annotation)):
                return True
        return any(base != "Protocol" and self._is_collaborator(base, site.path, seen) for base in site.bases)

    def params(self, site: _ClassSite, seen: frozenset[int] = frozenset()) -> list[_Param]:
        """A data class's fields, inherited ones first; any other class's ``__init__`` parameters,
        inherited from a base declared under the root when it defines none."""
        seen = seen | {id(site.node)}
        inherited = [
            param
            for base in site.bases
            for other in self.declared(base, site.path)
            if id(other.node) not in seen and other.is_data == site.is_data
            for param in self.params(other, seen)
        ]
        if site.is_data:
            own = [p for item in site.node.body if isinstance(item, ast.AnnAssign) and (p := _field_param(item))]
            return inherited + own
        for item in site.node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                args = item.args
                positional = [*args.posonlyargs, *args.args][1:]
                required = len(positional) - len(args.defaults)
                kwonly = zip(args.kwonlyargs, args.kw_defaults, strict=True)
                return [_Param(a.arg, a.annotation, i < required) for i, a in enumerate(positional)] + [
                    _Param(a.arg, a.annotation, default is None) for a, default in kwonly
                ]
        return inherited

    def _holds(self, site: _ClassSite) -> bool:
        """Whether a class is orchestration: a parameter annotated directly as a collaborator — a
        collaborator nested in a container's type arguments does not count. A data-class field
        counts only when it is required by the constructor, read off ``self`` by the class's own
        methods, or one of a bundle whose every field is a collaborator."""
        params = self.params(site)
        held = [p for p in params if any(self.is_collaborator(n, site.path) for n in _direct_types(p.annotation))]
        if not held or not site.is_data:
            return bool(held)
        reads = _self_reads(site.node)
        return len(held) == len(params) or any(p.required or p.name in reads for p in held)

    def sites_in(self, paths: Iterable[Path] | None) -> list[_ClassSite]:
        wanted = None if paths is None else {p.resolve() for p in paths}
        return [s for s in self.sites if wanted is None or s.path.resolve() in wanted]

    def where(self, site: _ClassSite) -> str:
        return f"{site.path.relative_to(self.src_root.parent.parent)}:{site.node.lineno} {site.node.name}"


@functools.cache
def _role_scan(src_root: Path) -> _RoleScan:
    return _RoleScan(src_root)


def _role_marker_violations(src_root: Path, paths: Iterable[Path] | None = None) -> list[str]:
    """Every data class in ``paths`` (default: all under ``src_root``) without exactly one role
    marker, every marker on a class whose role is inferred or that is no data class, and every
    data class built by a call, which no marker can reach."""
    scan = _role_scan(src_root)
    violations: list[str] = []
    for site in scan.sites_in(paths):
        inferred = scan.inferred_role(site)
        if site.markers and inferred is not None:
            violations.append(f"{scan.where(site)} has the inferred role {inferred} yet carries {list(site.markers)}")
        elif site.markers and not site.is_data:
            violations.append(f"{scan.where(site)} is no @dataclass or NamedTuple yet carries {list(site.markers)}")
        elif site.is_data and inferred is None and len(site.markers) != 1:
            declared = f"carries {list(site.markers)}" if site.markers else "declares no role"
            violations.append(f"{scan.where(site)} {declared}; a data class carries exactly one role marker")
    wanted = None if paths is None else {p.resolve() for p in paths}
    for path, lineno, call in scan.functional:
        if wanted is None or path.resolve() in wanted:
            violations.append(f"{path.relative_to(src_root.parent.parent)}:{lineno} builds a data class with {call}()")
    return violations


def _in_app_boundary(rel: Path) -> bool:
    """Whether a module path relative to the source root sits in an app-boundary package."""
    return any(rel.is_relative_to(package) for package in _APP_BOUNDARY)


def _boundary_violations(src_root: Path, paths: Iterable[Path] | None = None) -> list[str]:
    """Every ``@dto`` or pydantic model in ``paths`` outside the app boundary, and every
    ``@domain_model`` or ``@adapter_model`` inside it."""
    scan = _role_scan(src_root)
    violations: list[str] = []
    for site in scan.sites_in(paths):
        inside = _in_app_boundary(site.path.relative_to(src_root))
        if not inside and "dto" in site.markers:
            violations.append(f"{scan.where(site)} is a @dto outside the app boundary")
        elif not inside and scan.inferred_role(site) == "pydantic":
            violations.append(f"{scan.where(site)} is a pydantic model outside the app boundary")
        elif inside and (core := [m for m in site.markers if m in ("domain_model", "adapter_model")]):
            violations.append(f"{scan.where(site)} is a @{core[0]} inside the app boundary")
    return violations


def _adapter_model_violations(src_root: Path, paths: Iterable[Path] | None = None) -> list[str]:
    """Every Protocol member in ``paths`` whose annotations name an ``@adapter_model`` declared
    anywhere under ``src_root`` — by its own name or an import alias of it."""
    scan = _role_scan(src_root)
    adapter_models = {site.node.name for site in scan.sites if "adapter_model" in site.markers}
    violations: list[str] = []
    for site in scan.sites_in(paths):
        if "Protocol" not in site.bases:
            continue
        for item in site.node.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                args = item.args
                every = [*args.posonlyargs, *args.args, *args.kwonlyargs, *filter(None, (args.vararg, args.kwarg))]
                annotations = [a.annotation for a in every] + [item.returns]
                member = item.name
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                annotations, member = [item.annotation], item.target.id
            else:
                continue
            mentioned = set().union(*(_mentioned_types(a, scan.aliases) for a in annotations))
            local = scan.imports.get(site.path, {})
            named = {local.get(name, ("", name))[1] for name in mentioned} & adapter_models
            if named:
                violations.append(f"{scan.where(site)}.{member} names @adapter_model {sorted(named)}")
    return violations


def _wire_import_violations(src_root: Path, *, exempt: frozenset[Path]) -> list[str]:
    """Every module under ``src_root`` — outside the app boundary, the wire adapters, and
    ``exempt`` — importing ``blizzard.wire``."""
    violations: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(src_root)
        if _in_app_boundary(rel) or any(rel.is_relative_to(a) for a in _WIRE_ADAPTERS) or path in exempt:
            continue
        if any(m == "blizzard.wire" or m.startswith("blizzard.wire.") for m in _imported_modules(path)):
            violations.append(f"{rel.as_posix()} imports a wire/ model outside the app boundary")
    return violations


def _domain_model_collaborator_violations(src_root: Path, paths: Iterable[Path] | None = None) -> list[str]:
    """Every ``@domain_model`` in ``paths`` with a field — inherited, ``InitVar``, or
    ``init=False`` included — whose annotation mentions a collaborator at any depth, a driver
    handle spelled dotted (``httpx.Client``) included."""
    scan = _role_scan(src_root)
    violations: list[str] = []
    for site in scan.sites_in(paths):
        if "domain_model" not in site.markers:
            continue
        for param in scan.params(site):
            held = sorted(
                n for n in _mentioned_types(param.annotation, scan.aliases) if scan.is_collaborator(n, site.path)
            )
            if held:
                violations.append(f"{scan.where(site)}.{param.name} holds collaborator {held}")
    return violations


def _work_methods(scan: _RoleScan, site: _ClassSite, seen: frozenset[int] = frozenset()) -> list[str]:
    """The methods and properties a class declares or inherits from a base under the root, bar its
    constructors — what it does rather than how it is built."""
    seen = seen | {id(site.node)}
    own = [
        item.name
        for item in site.node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        and _dto_method_kind(site.node.name, item) != "constructor"
    ]
    inherited = [
        name
        for base in site.bases
        for other in scan.declared(base, site.path)
        if id(other.node) not in seen
        for name in _work_methods(scan, other, seen)
    ]
    return own + inherited


def _collaborator_violations(src_root: Path, paths: Iterable[Path] | None = None) -> list[str]:
    """Every ``@collaborator`` in ``paths`` with no method or property beyond its constructors, and
    every data class in ``paths`` named as a clock (``…Clock``) that infers no role yet carries a
    role other than ``@collaborator``."""
    scan = _role_scan(src_root)
    violations: list[str] = []
    for site in scan.sites_in(paths):
        if "collaborator" in site.markers and not _work_methods(scan, site):
            violations.append(f"{scan.where(site)} is a @collaborator with no method beyond its constructors")
        named_clock = site.node.name.endswith("Clock") and site.is_data and scan.inferred_role(site) is None
        if named_clock and site.markers and "collaborator" not in site.markers:
            violations.append(f"{scan.where(site)} is a clock yet carries {list(site.markers)}, not @collaborator")
    return violations


def _mutates_self(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.Delete):
            targets = node.targets if isinstance(node, ast.Assign | ast.Delete) else [node.target]
            for target in targets:
                while isinstance(target, ast.Attribute | ast.Subscript):
                    target = target.value
                    if isinstance(target, ast.Name) and target.id == "self":
                        return True
        elif isinstance(node, ast.Call) and ast.unparse(node.func) == "object.__setattr__":
            return True
    return False


def _dto_method_kind(class_name: str, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """property: a ``property`` or ``cached_property`` getter. constructor: ``__init__``,
    ``__new__``, ``__post_init__``, or a classmethod or staticmethod whose return annotation is the
    class itself or ``Self`` (or a union arm of one). projection: a plain ``def`` taking only
    ``self``, annotated to return a value, assigning nothing rooted at ``self``."""
    decorators = {_terminal(d) for d in fn.decorator_list}
    if decorators & _PROPERTY_DECORATORS:
        return "property"
    if fn.name in _CONSTRUCTOR_DUNDERS:
        return "constructor"
    if decorators & {"classmethod", "staticmethod"}:
        return "constructor" if set(_direct_types(fn.returns)) & {class_name, "Self"} else None
    args = fn.args
    nullary = len([*args.posonlyargs, *args.args]) == 1 and not (args.kwonlyargs or args.vararg or args.kwarg)
    returns = _annotation(fn.returns)
    valued = returns is not None and not (isinstance(returns, ast.Constant) and returns.value is None)
    pure = isinstance(fn, ast.FunctionDef) and not decorators and not _mutates_self(fn)
    return "projection" if nullary and valued and pure else None


def _binds_method(value: ast.expr | None, functions: set[str]) -> bool:
    """Whether a class-body assignment's value binds a method: a lambda, a ``staticmethod`` or
    ``classmethod`` call, or a function the module declares at top level."""
    if isinstance(value, ast.Lambda):
        return True
    if isinstance(value, ast.Call) and _terminal(value.func) in ("staticmethod", "classmethod"):
        return True
    return isinstance(value, ast.Name) and value.id in functions


def _dto_method_violations(src_root: Path, paths: Iterable[Path] | None = None) -> list[str]:
    """Every method of a ``@dto`` in ``paths`` that is not a constructor, a projection, or a
    property — a ``def`` the DTO test rejects, or any method bound by class-body assignment."""
    scan = _role_scan(src_root)
    violations: list[str] = []
    for site in scan.sites_in(paths):
        if "dto" not in site.markers:
            continue
        for item in site.node.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                if _dto_method_kind(site.node.name, item) is None:
                    violations.append(f"{scan.where(site)}.{item.name} is no constructor, projection, or property")
            elif isinstance(item, ast.Assign | ast.AnnAssign) and _binds_method(item.value, scan.functions[site.path]):
                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                bound = ", ".join(ast.unparse(target) for target in targets)
                violations.append(f"{scan.where(site)}.{bound} binds a method by assignment")
    return violations


def test_every_data_class_declares_exactly_one_role() -> None:
    """Every ``@dataclass`` and ``NamedTuple`` carries exactly one of ``@domain_model``, ``@dto``,
    ``@adapter_model``, ``@collaborator`` — unless its shape infers its role (port, error, enum,
    pydantic, orchestration), which it then never annotates (``bzh:data-roles``)."""
    violations = _role_marker_violations(_SRC_DIR)
    assert not violations, f"data roles — {len(violations)} data class(es) misdeclare a role: {violations}"


def test_a_dto_lives_at_the_app_boundary() -> None:
    """A ``@dto`` or pydantic model is the app's contract with the outside, so it lives in an
    app-boundary package, and a ``@domain_model`` or ``@adapter_model`` never does
    (``bzh:data-roles``)."""
    violations = _boundary_violations(_SRC_DIR)
    assert not violations, f"data roles — {len(violations)} class(es) on the wrong side of the boundary: {violations}"


def test_only_the_boundary_names_a_wire_model() -> None:
    """A ``wire/`` model is named only at the app boundary, by an adapter that sends or receives
    the wire itself, and by a composition root (``bzh:data-roles``)."""
    violations = _wire_import_violations(_SRC_DIR, exempt=_COMPOSITION_ROOTS)
    assert not violations, f"data roles — {len(violations)} wire/ import(s) outside the boundary: {violations}"


def test_an_adapter_model_crosses_no_protocol() -> None:
    """An ``@adapter_model`` is an outside system's format private to its adapter: no Protocol
    member's signature names it (``bzh:data-roles``)."""
    violations = _adapter_model_violations(_SRC_DIR)
    assert not violations, f"data roles — {len(violations)} adapter-model leak(s): {violations}"


def test_a_domain_model_holds_no_collaborator() -> None:
    """A ``@domain_model`` carries rules, not collaborators: no field mentions a port, a clock, a
    driver handle, or a ``@collaborator`` or orchestration class (``bzh:data-roles``)."""
    violations = _domain_model_collaborator_violations(_SRC_DIR)
    assert not violations, f"data roles — {len(violations)} domain-model field(s) hold a collaborator: {violations}"


def test_a_collaborator_does_work_and_every_clock_is_one() -> None:
    """A ``@collaborator`` is a class other code calls to do work, so it has a method or property
    beyond its constructors; a data class named as a clock is one (``bzh:data-roles``)."""
    violations = _collaborator_violations(_SRC_DIR)
    assert not violations, f"data roles — {len(violations)} collaborator(s) misdeclared: {violations}"


def test_a_dto_has_only_constructors_projections_and_properties() -> None:
    """A ``@dto`` is the app's contract with the outside, not a home for rules: its methods are
    constructors, projections, and properties only (``bzh:data-roles``)."""
    violations = _dto_method_violations(_SRC_DIR)
    assert not violations, f"data roles — {len(violations)} dto method(s) carry behavior: {violations}"


_ROLE_FIXTURE_HEADER = """\
from collections.abc import Callable
from dataclasses import InitVar, dataclass, field
from functools import cached_property
from ipaddress import IPv4Network
from typing import Literal, NamedTuple, Optional, Protocol, Self

import httpx
from sqlalchemy import Connection

from blizzard.foundation.roles import adapter_model, collaborator, domain_model, dto
from blizzard.ports import AppError, Driver, IClock, IThing, Service, SystemClock
"""

_ROLE_FIXTURE_BASE = {
    "__init__.py": "",
    "foundation/__init__.py": "",
    "foundation/roles.py": "",
    "hub/__init__.py": "",
    "hub/domain/__init__.py": "",
    "hub/store/__init__.py": "",
    "ports.py": (
        "from typing import Protocol\n\n"
        "class IThing(Protocol):\n    def go(self) -> None: ...\n\n"
        "class IClock(Protocol):\n    def now(self) -> float: ...\n\n"
        "class AppError(Exception):\n    pass\n\n"
        "class SystemClock:\n    pass\n\n"
        "class Service:\n    def __init__(self, thing: IThing) -> None:\n        self.thing = thing\n\n"
        "from dataclasses import dataclass\nfrom blizzard.foundation.roles import collaborator\n\n"
        "@collaborator\n@dataclass\nclass Driver:\n    url: str\n\n    def go(self) -> None: ...\n"
    ),
}


def _plant_roles(tmp_path: Path, files: Mapping[str, str]) -> Path:
    """A ``blizzard`` source tree holding the fixture ports plus ``files``, each under the fixture
    header; the returned root is what every data-role check takes."""
    src = tmp_path / "blizzard"
    planted = {
        **_ROLE_FIXTURE_BASE,
        **{rel: _ROLE_FIXTURE_HEADER + textwrap.dedent(text) for rel, text in files.items()},
    }
    for rel, text in planted.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    return src


@pytest.mark.parametrize(
    "source",
    [
        "@dataclass\nclass Bare:\n    x: int\n",
        "class Pair(NamedTuple):\n    x: int\n",
        "@dto\n@adapter_model\n@dataclass\nclass Doubled:\n    x: int\n",
        "@dto\n@dto\n@dataclass\nclass Twice:\n    x: int\n",
        "@dto\n@dataclass\nclass Failure(AppError):\n    x: int\n",
        "@dto\nclass IPort(Protocol):\n    def go(self) -> None: ...\n",
        "@dto\nclass Plain:\n    pass\n",
        "@dto\n@dataclass\nclass Wired:\n    x: int\n    thing: IThing\n",
        "def dto(cls):\n    return cls\n\n@dto\n@dataclass\nclass Shadowed:\n    x: int\n",
        "Pair = NamedTuple('Pair', [('x', int)])\n",
        "@dataclass\nclass Disguised:\n    x: int\n    thing: IThing | None = None\n",
        "@dataclass\nclass Netted:\n    net: IPv4Network\n",
        "@dataclass\nclass Holder:\n    x: int\n    things: tuple[IThing, ...]\n",
        "class Named(Protocol):\n    @property\n    def slug(self) -> str: ...\n\n@dataclass\nclass Holder:\n    x: int\n    named: Named\n",
        "@dto\n@dataclass\nclass Base:\n    x: int\n\n@dataclass\nclass Child(Base):\n    y: int\n",
        "@dto\n@dataclass\nclass ParseError:\n    x: int\n\n@dataclass\nclass Sub(ParseError):\n    y: int\n",
        "class Late:\n    x: int\n\nLate = dataclass(Late)\n",
        "@dto\n@dataclass\nclass Wired:\n    x: int\n    client: httpx.Client\n",
        "@domain_model\n@dataclass\nclass Checked:\n    conn: Connection\n",
        "@dto\n@dataclass\nclass Driven:\n    x: int\n    driver: Driver\n",
        "@collaborator\n@dataclass\nclass Held:\n    thing: IThing\n\n    def go(self) -> None: ...\n",
    ],
)
def test_role_marker_check_catches_a_misdeclared_data_class(tmp_path: Path, source: str) -> None:
    violations = _role_marker_violations(_plant_roles(tmp_path, {"hub/domain/mod.py": source}))
    assert len(violations) == 1, violations


@pytest.mark.parametrize(
    "source",
    [
        "@dto\n@dataclass(frozen=True)\nclass Page:\n    x: int\n",
        "@adapter_model\nclass Row(NamedTuple):\n    x: int\n",
        "from blizzard.foundation import roles\n\n@roles.domain_model\n@dataclass\nclass Model:\n    x: int\n",
        "import blizzard.foundation.roles\n\n@blizzard.foundation.roles.dto\n@dataclass\nclass Dotted:\n    x: int\n",
        "from ...foundation.roles import dto as data\n\n@data\n@dataclass\nclass Relative:\n    x: int\n",
        "@dataclass\nclass Failure(AppError):\n    x: int\n",
        "@dataclass\nclass Wired:\n    x: int\n    thing: IThing\n",
        "@dataclass\nclass Timed:\n    x: int\n    clock: SystemClock = field(default_factory=SystemClock)\n\n"
        "    def stamp(self) -> object:\n        return self.clock\n",
        "@dataclass\nclass Bundle:\n    a: IThing | None = None\n    b: Optional[IClock] = None\n",
        "@dataclass\nclass UsesService:\n    x: int\n    service: Service\n",
        "@dataclass\nclass Ctx:\n    thing: IThing\n\n@dataclass\nclass Step:\n    n: int\n    ctx: Ctx\n",
        "@dataclass\nclass Starts:\n    x: int\n    clock: InitVar[IClock]\n",
        "@dataclass\nclass Ctx:\n    thing: IThing\n\n@dataclass\nclass Narrower(Ctx):\n    n: int = 0\n",
        "class StepContext(Protocol):\n    @property\n    def thing(self) -> IThing: ...\n\n"
        "@dataclass\nclass Step:\n    n: int\n    ctx: StepContext\n",
        "class Stores(Protocol):\n    thing: IThing\n\nclass BaseContext(Protocol):\n    stores: Stores\n\n"
        "class StepContext(BaseContext, Protocol): ...\n\n@dataclass\nclass Step:\n    n: int\n    ctx: StepContext\n",
        "@collaborator\n@dataclass\nclass Runner:\n    url: str\n\n    def run(self) -> None: ...\n",
        "@dataclass\nclass Uses:\n    x: int\n    driver: Driver\n",
        "@dataclass\nclass Daemon:\n    verb: str\n    client: httpx.Client\n",
        "import sqlalchemy as sa\n\n@dataclass\nclass Check:\n    conn: sa.Connection\n",
    ],
)
def test_role_marker_check_admits_a_declared_or_inferred_role(tmp_path: Path, source: str) -> None:
    assert _role_marker_violations(_plant_roles(tmp_path, {"hub/domain/mod.py": source})) == []


_ROW_MODEL = "@adapter_model\n@dataclass\nclass NodeRow:\n    x: int\n"
_ROW_IMPORT = "from blizzard.hub.store.internal.rows import NodeRow\n\n"


@pytest.mark.parametrize(
    "member",
    [
        "def get(self) -> NodeRow: ...",
        "def put(self, row: NodeRow) -> None: ...",
        "def put(self, *, row: NodeRow) -> None: ...",
        "async def put(self, *rows: NodeRow) -> None: ...",
        "def pairs(self) -> list[tuple[str, NodeRow]]: ...",
        "def find(self) -> 'NodeRow | None': ...",
        "def each(self, fn: Callable[[NodeRow], None]) -> None: ...",
        "def all(self) -> Rows: ...",
        "row: NodeRow",
    ],
)
def test_adapter_model_check_catches_one_in_a_protocol_signature(tmp_path: Path, member: str) -> None:
    protocol = f"{_ROW_IMPORT}Rows = list[NodeRow]\n\nclass IRepo(Protocol):\n    {member}\n"
    src = _plant_roles(tmp_path, {"hub/store/internal/rows.py": _ROW_MODEL, "hub/domain/repo.py": protocol})
    violations = _adapter_model_violations(src)
    assert len(violations) == 1, violations
    assert "IRepo" in violations[0]


def test_adapter_model_check_catches_one_under_an_import_alias(tmp_path: Path) -> None:
    protocol = (
        "from blizzard.hub.store.internal.rows import NodeRow as R\n\n"
        "class IRepo(Protocol):\n    def get(self) -> R: ...\n"
    )
    src = _plant_roles(tmp_path, {"hub/store/internal/rows.py": _ROW_MODEL, "hub/domain/repo.py": protocol})
    violations = _adapter_model_violations(src)
    assert len(violations) == 1, violations
    assert "IRepo.get names @adapter_model ['NodeRow']" in violations[0]


def test_adapter_model_check_admits_one_its_adapter_maps_to_a_domain_model(tmp_path: Path) -> None:
    adapter = f"{_ROW_IMPORT}class Adapter:\n    def get(self) -> NodeRow: ...\n"
    protocol = (
        "@domain_model\n@dataclass\nclass Node:\n    x: int\n\n"
        "class IRepo(Protocol):\n    def get(self) -> Node: ...\n    def kind(self) -> Literal['NodeRow']: ...\n"
    )
    src = _plant_roles(
        tmp_path,
        {
            "hub/store/internal/rows.py": _ROW_MODEL,
            "hub/store/internal/adapter.py": adapter,
            "hub/domain/repo.py": protocol,
        },
    )
    assert _adapter_model_violations(src) == []


_PAGE_DTO = "@dto\n@dataclass\nclass Page:\n    x: int\n"
_VIEW_MODEL = "from pydantic import BaseModel\n\nclass View(BaseModel):\n    x: int\n\nclass Sub(View):\n    y: int\n"


@pytest.mark.parametrize(
    ("rel", "source", "expected"),
    [
        ("hub/domain/mod.py", _PAGE_DTO, 1),
        ("runner/hub/client.py", _PAGE_DTO, 1),
        ("client/mod.py", _PAGE_DTO, 1),
        ("hub/domain/mod.py", _VIEW_MODEL, 2),
        ("hub/api/mod.py", "@domain_model\n@dataclass\nclass Model:\n    x: int\n", 1),
        ("wire/mod.py", _ROW_MODEL, 1),
    ],
)
def test_boundary_check_catches_a_class_on_the_wrong_side(tmp_path: Path, rel: str, source: str, expected: int) -> None:
    violations = _boundary_violations(_plant_roles(tmp_path, {rel: source}))
    assert len(violations) == expected, violations


@pytest.mark.parametrize(
    ("rel", "source"),
    [
        ("hub/api/mod.py", _PAGE_DTO),
        ("runner/cli/mod.py", _PAGE_DTO),
        ("cli/mod.py", _PAGE_DTO),
        ("wire/mod.py", _VIEW_MODEL),
        ("hub/domain/mod.py", "@domain_model\n@dataclass\nclass Model:\n    x: int\n"),
        ("hub/store/internal/rows.py", _ROW_MODEL),
        ("runner/harness/claude_code/adapter.py", _ROW_MODEL),
    ],
)
def test_boundary_check_admits_a_class_on_its_own_side(tmp_path: Path, rel: str, source: str) -> None:
    assert _boundary_violations(_plant_roles(tmp_path, {rel: source})) == []


_WIRE_IMPORT = "from blizzard.wire.chunk import ChunkView\n"


@pytest.mark.parametrize(
    ("rel", "source"),
    [
        ("hub/domain/mod.py", _WIRE_IMPORT),
        ("runner/leases/mod.py", "import blizzard.wire.chunk\n"),
        ("hub/store/internal/mod_store.py", _WIRE_IMPORT),
        ("runner/transcripts/internal/other.py", _WIRE_IMPORT),
    ],
)
def test_wire_import_check_catches_an_importer_outside_the_boundary(tmp_path: Path, rel: str, source: str) -> None:
    violations = _wire_import_violations(_plant_roles(tmp_path, {rel: source}), exempt=frozenset())
    assert violations == [f"{rel} imports a wire/ model outside the app boundary"]


def test_wire_import_check_admits_the_boundary_the_wire_adapters_and_a_root(tmp_path: Path) -> None:
    importers = (
        "hub/api/mod.py",
        "wire/view.py",
        "runner/hub/client.py",
        "hub/events/broker.py",
        "runner/events/broker.py",
        "runner/transcripts/internal/segment_projection.py",
        "hub/app.py",
    )
    src = _plant_roles(tmp_path, dict.fromkeys(importers, _WIRE_IMPORT))
    assert _wire_import_violations(src, exempt=frozenset({src / "hub/app.py"})) == []


@pytest.mark.parametrize(
    "fields",
    [
        "thing: IThing",
        "thing: IThing | None = None",
        "things: list[IThing]",
        "clock: 'IClock'",
        "clock: SystemClock",
        "clock: InitVar[IClock]",
        "thing: IThing = field(init=False)",
        "service: Service",
        "things: Things",
        "client: httpx.Client | None = None",
        "conns: list[Connection]",
        "drivers: tuple[Driver, ...] = ()",
    ],
)
def test_domain_model_check_catches_a_collaborator_field(tmp_path: Path, fields: str) -> None:
    source = f"Things = dict[str, IThing]\n\n@domain_model\n@dataclass\nclass Model:\n    x: int\n    {fields}\n"
    violations = _domain_model_collaborator_violations(_plant_roles(tmp_path, {"hub/domain/mod.py": source}))
    assert len(violations) == 1, violations


def test_domain_model_check_catches_an_inherited_collaborator_field(tmp_path: Path) -> None:
    source = "@dataclass\nclass Ctx:\n    thing: IThing\n\n@domain_model\n@dataclass\nclass Model(Ctx):\n    x: int\n"
    violations = _domain_model_collaborator_violations(_plant_roles(tmp_path, {"hub/domain/mod.py": source}))
    assert len(violations) == 1, violations
    assert violations[0].endswith("Model.thing holds collaborator ['IThing']")


def test_domain_model_check_admits_data_fields(tmp_path: Path) -> None:
    source = (
        "@domain_model\n@dataclass\nclass Model:\n    x: int\n    net: IPv4Network\n"
        "    kind: Literal['IThing']\n    tags: tuple[str, ...] = ()\n"
    )
    assert _domain_model_collaborator_violations(_plant_roles(tmp_path, {"hub/domain/mod.py": source})) == []


_DTO_PAGE = "@dto\n@dataclass(frozen=True)\nclass Page:\n    x: int\n"


@pytest.mark.parametrize(
    "method",
    [
        "def scaled(self, k: int) -> int:\n    return self.x * k",
        "def touch(self) -> int:\n    object.__setattr__(self, 'x', 1)\n    return 1",
        "def bump(self) -> int:\n    self.x[0] = 1\n    return 1",
        "def log(self) -> None:\n    print(self.x)",
        "def unannotated(self):\n    return self.x",
        "async def fetch(self) -> int:\n    return self.x",
        "@classmethod\ndef count(cls) -> int:\n    return 1",
        "@staticmethod\ndef label() -> str:\n    return 'x'",
        "@x.setter\ndef x(self, value: int) -> None:\n    pass",
        "def __eq__(self, other: object) -> bool:\n    return True",
    ],
)
def test_dto_check_catches_a_behavior_method(tmp_path: Path, method: str) -> None:
    source = _DTO_PAGE + textwrap.indent(method, "    ") + "\n"
    violations = _dto_method_violations(_plant_roles(tmp_path, {"hub/domain/mod.py": source}))
    assert len(violations) == 1, violations


@pytest.mark.parametrize(
    "binding",
    ["scaled = _scaled", "scaled = staticmethod(_scaled)", "scaled = classmethod(_scaled)", "scaled = lambda self: 1"],
)
def test_dto_check_catches_a_method_bound_by_assignment(tmp_path: Path, binding: str) -> None:
    source = "def _scaled(self) -> int:\n    return 1\n\n" + _DTO_PAGE + f"    {binding}\n    label = 'page'\n"
    violations = _dto_method_violations(_plant_roles(tmp_path, {"hub/domain/mod.py": source}))
    assert len(violations) == 1, violations
    assert "Page.scaled binds a method by assignment" in violations[0]


@pytest.mark.parametrize(
    "source",
    [
        "@collaborator\n@dataclass\nclass Idle:\n    url: str\n",
        "@collaborator\n@dataclass\nclass Built:\n    url: str\n\n    @classmethod\n    def of(cls) -> Self: ...\n",
        "@domain_model\n@dataclass\nclass FakeClock:\n    at: float\n\n    def now(self) -> float: ...\n",
        "@dto\n@dataclass\nclass ManualClock:\n    at: float\n",
    ],
)
def test_collaborator_check_catches_a_misdeclared_collaborator(tmp_path: Path, source: str) -> None:
    violations = _collaborator_violations(_plant_roles(tmp_path, {"hub/domain/mod.py": source}))
    assert len(violations) == 1, violations


@pytest.mark.parametrize(
    "source",
    [
        "@collaborator\n@dataclass\nclass FakeClock:\n    at: float\n\n    def now(self) -> float: ...\n",
        "@collaborator\n@dataclass\nclass File:\n    path: str\n\n    @property\n    def text(self) -> str: ...\n",
        "@collaborator\n@dataclass\nclass Base:\n    def run(self) -> None: ...\n\n"
        "@collaborator\n@dataclass\nclass Sub(Base):\n    url: str\n",
        "@dataclass\nclass HeldClock:\n    thing: IThing\n",
    ],
)
def test_collaborator_check_admits_a_class_that_does_work(tmp_path: Path, source: str) -> None:
    assert _collaborator_violations(_plant_roles(tmp_path, {"hub/domain/mod.py": source})) == []


def test_dto_check_admits_constructors_projections_and_properties(tmp_path: Path) -> None:
    methods = [
        "def __post_init__(self) -> None:\n    object.__setattr__(self, 'x', abs(self.x))",
        "@classmethod\ndef of(cls, x: int) -> Page:\n    return cls(x)",
        "@classmethod\ndef parse(cls, raw: str) -> 'Page | None':\n    return None",
        "@classmethod\ndef empty(cls) -> Self:\n    return cls(0)",
        "@staticmethod\ndef zero() -> Page:\n    return Page(0)",
        "@property\ndef doubled(self) -> int:\n    return self.x * 2",
        "@cached_property\ndef tripled(self) -> int:\n    return self.x * 3",
        "def to_wire(self) -> dict[str, int]:\n    return {'x': self.x}",
        "def __str__(self) -> str:\n    return str(self.x)",
    ]
    source = _DTO_PAGE + "".join(textwrap.indent(m, "    ") + "\n\n" for m in methods)
    assert _dto_method_violations(_plant_roles(tmp_path, {"hub/domain/mod.py": source})) == []


_CONFIGURED_RECORD_VERBS = {"create", "list", "show", "edit", "retire", "enable"}
_CONFIGURED_RECORD_NOUNS = {
    "source": _CONFIGURED_RECORD_VERBS,
    "repo": _CONFIGURED_RECORD_VERBS,
    "secret": (_CONFIGURED_RECORD_VERBS - {"edit"}) | {"set"},
    "scope": _CONFIGURED_RECORD_VERBS,
    "routine": _CONFIGURED_RECORD_VERBS,
}


def test_every_configured_record_noun_carries_its_whole_verb_set() -> None:
    import click

    from blizzard.hub.cli import hub

    ctx = click.Context(hub)
    missing: dict[str, set[str]] = {}
    for noun, required in _CONFIGURED_RECORD_NOUNS.items():
        group = hub.get_command(ctx, noun)
        assert isinstance(group, click.Group), f"hub {noun} is not a command group"
        absent = required - set(group.list_commands(click.Context(group, parent=ctx)))
        if absent:
            missing[noun] = absent
    assert missing == {}, f"configured-record nouns missing verbs: {missing}"
