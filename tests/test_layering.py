from __future__ import annotations

import ast
import builtins
import functools
import importlib.util
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

from blizzard.runner.harness.wiring import HARNESS_SECTION_KINDS
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit


def _production_files(root: Path) -> list[Path]:
    """Every module under ``root`` bar the store-migration tests colocated beside their revisions, which are test code
    held to the standards of ``tests/``."""
    return sorted(
        path for path in root.rglob("*.py") if path.parent.name != "tests" or path.parent.parent.name != "migrations"
    )


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
    "OperatorWindow": "blizzard.foundation.operator_window",
    "WindowFault": "blizzard.foundation.operator_window",
    "fault_message": "blizzard.foundation.operator_window",
    "EgressState": "blizzard.foundation.egress_state",
    "IdentityState": "blizzard.foundation.runner_identity_state",
    "HostRegistrationState": "blizzard.foundation.host_registration_state",
    "HostRunnerState": "blizzard.foundation.host_runner_state",
    "HOST_TOKEN_VARIABLE": "blizzard.foundation.runner_token_names",
    "runner_prefix": "blizzard.runner.route_paths",
    "is_api_path": "blizzard.runner.route_paths",
    "within_runner": "blizzard.runner.route_paths",
    "RUNNER_ROUTE_PREFIX": "blizzard.runner.route_paths",
    "RUNNERS_SEGMENT": "blizzard.runner.route_paths",
    "OTLP_PATHS": "blizzard.runner.route_paths",
    "PublicOrigins": "blizzard.runner.public_origins",
    "RedactingExporter": "blizzard.foundation.platform_tracing.redaction",
    "redacted": "blizzard.foundation.platform_tracing.redaction",
    "EnabledReceivedTelemetryExport": "blizzard.runner.tracing.internal.received_pipeline",
    "TenantState": "blizzard.foundation.tenants",
    "TenantDeletionState": "blizzard.foundation.tenants",
    "TENANT_NAME_MAX_LENGTH": "blizzard.foundation.tenants",
    "TENANT_NAME_PATTERN": "blizzard.foundation.tenants",
    "is_tenant_name": "blizzard.foundation.tenants",
    "HUB_PERMISSIONS": "blizzard.hub.auth.permissions",
    "permissions_for": "blizzard.hub.auth.permissions",
    "MEMBERSHIP_ROLES": "blizzard.hub.auth.memberships",
    "HubEventKind": "blizzard.hub.domain.observability.hub_events",
    "HUB_EVENT_SEVERITY": "blizzard.hub.domain.observability.hub_events",
    "narrow_hub_event_kind": "blizzard.hub.domain.observability.hub_events",
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
    return _file_violations(_production_files(root), forbidden_prefixes)


def _file_violations(paths: Iterable[Path], forbidden_prefixes: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        for module in sorted(_imported_modules(path)):
            if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden_prefixes):
                violations.append(f"{path.relative_to(_REPO_ROOT)} imports {module}")
    return violations


def _misrouted_moved_names(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _production_files(root):
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


#: Kernel modules that stay shared despite one side reaching them, each with the reason — an entry the check no longer
#: needs fails it (``bzh:shared-kernel``).
_SINGLE_SIDE_KERNEL_ALLOWLIST: dict[str, str] = {}


def _defines_nothing(tree: ast.Module) -> bool:
    """Whether a module body is only a docstring and ``from __future__ import annotations``."""
    for node in tree.body:
        is_docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        if not (is_docstring or is_future):
            return False
    return True


def _module_name(path: Path, src_root: Path) -> str:
    return ".".join(path.relative_to(src_root).with_suffix("").parts).removesuffix(".__init__")


def _reaching_sides(src_root: Path) -> tuple[dict[str, set[str]], dict[str, Path]]:
    """For every production module under ``src_root/blizzard``, the tags of what reaches it — ``hub``, ``runner``,
    ``wire`` — and the module's path. A hub or runner module is its own side; a wire, foundation, auth_core or cli
    module passes on the tags of whatever imports it, a wire module adding ``wire``; a tool is no side. An import
    names the longest dotted prefix that is a module, so ``from pkg import name`` reaches ``pkg`` and
    ``from pkg import sub`` reaches ``pkg.sub``, never both."""
    files = {_module_name(path, src_root): path for path in _production_files(src_root / "blizzard")}
    imports: dict[str, set[str]] = {name: set() for name in files}
    for name, path in files.items():
        for _, bound in _import_statements(path, src_root):
            for segments in bound:
                reached = next(
                    (m for m in (".".join(segments[:n]) for n in range(len(segments), 0, -1)) if m in files), None
                )
                if reached is not None and reached != name:
                    imports[name].add(reached)
    tags: dict[str, set[str]] = {name: set() for name in files}
    fixed = {"blizzard.hub": "hub", "blizzard.runner": "runner"}
    for name in files:
        for prefix, side in fixed.items():
            if name == prefix or name.startswith(f"{prefix}."):
                tags[name].add(side)
        if name.startswith("blizzard.wire"):
            tags[name].add("wire")
    passes_on = {n for n in files if n.split(".")[1:2] in (["wire"], ["foundation"], ["auth_core"], ["cli"])}
    changed = True
    while changed:
        changed = False
        for name, reached in imports.items():
            if name.startswith("blizzard.tools"):
                continue
            for target in reached:
                if target in passes_on and not tags[name] <= tags[target]:
                    tags[target] |= tags[name]
                    changed = True
    return tags, files


def _single_side_kernel_modules(src_root: Path, allowlist: Mapping[str, str]) -> list[str]:
    """The ``foundation/`` and ``auth_core/`` modules reached by neither a ``wire/`` module nor both daemons, bar the
    ``allowlist``, and the allowlist entries that name no such module or would pass without the entry
    (``bzh:shared-kernel``). ``internal/`` modules and docstring-only ``__init__`` modules are not judged."""
    tags, files = _reaching_sides(src_root)
    judged = [
        name
        for name, path in files.items()
        if name.split(".")[:2] in (["blizzard", "foundation"], ["blizzard", "auth_core"])
        and "internal" not in name.split(".")
        and not _defines_nothing(ast.parse(path.read_text(), filename=str(path)))
    ]
    problems: list[str] = []
    for name in judged:
        reach = tags[name]
        shared = "wire" in reach or {"hub", "runner"} <= reach
        if name in allowlist:
            if shared:
                problems.append(f"{name} is on the allowlist but is shared — drop the entry")
        elif not shared:
            sides = " and ".join(sorted(reach - {"wire"})) or "no"
            problems.append(
                f"{name} is reached by the {sides} side only and by no wire module — move it or allowlist it"
            )
    problems.extend(
        f"{name} is on the allowlist but is no judged kernel module" for name in allowlist if name not in judged
    )
    return problems


def test_every_kernel_module_is_shared_by_both_sides_or_the_wire() -> None:
    """``bzh:shared-kernel`` — a ``foundation/`` or ``auth_core/`` module only one daemon reaches belongs to that
    daemon, unless the wire reaches it."""
    problems = _single_side_kernel_modules(_SRC_DIR.parent, _SINGLE_SIDE_KERNEL_ALLOWLIST)
    assert not problems, f"G — a kernel module only one side uses must move into that side: {problems}"


def _plant_kernel(tmp_path: Path, files: Mapping[str, str]) -> Path:
    """A planted ``blizzard`` tree: each of ``files`` (keyed by path under ``blizzard/``) written, every package given an
    ``__init__.py``."""
    root = tmp_path / "src"
    for rel, text in files.items():
        path = root / "blizzard" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for package in {p for path in root.rglob("*.py") for p in path.parents if p != root.parent and root in p.parents}:
        (package / "__init__.py").touch()
    return root


_SHARED = "from blizzard.foundation import shared\n"


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        pytest.param({"hub/a.py": _SHARED, "foundation/shared.py": "X = 1\n"}, 1, id="one-sided"),
        pytest.param(
            {"hub/a.py": _SHARED, "runner/b.py": _SHARED, "foundation/shared.py": "X = 1\n"}, 0, id="two-sided"
        ),
        pytest.param({"wire/w.py": _SHARED, "foundation/shared.py": "X = 1\n"}, 0, id="wire-reached"),
        pytest.param(
            {
                "wire/w.py": "from blizzard.foundation import mid\n",
                "foundation/mid.py": "from blizzard.foundation import shared\n",
                "foundation/shared.py": "X = 1\n",
            },
            0,
            id="reached-through-a-wire-reached-module",
        ),
        pytest.param(
            {
                "hub/h.py": "from blizzard.cli import drive\n",
                "runner/r.py": "from blizzard.cli import drive\n",
                "cli/drive.py": _SHARED,
                "foundation/shared.py": "X = 1\n",
            },
            0,
            id="through-a-cli-module-driving-both",
        ),
        pytest.param(
            {
                "hub/h.py": "from blizzard.cli import drive\n",
                "cli/drive.py": _SHARED,
                "foundation/shared.py": "X = 1\n",
            },
            1,
            id="through-a-cli-module-driving-one",
        ),
        pytest.param(
            {"cli/drive.py": _SHARED, "foundation/shared.py": "X = 1\n"}, 1, id="through-an-unreached-cli-module"
        ),
        pytest.param({"tools/t.py": _SHARED, "foundation/shared.py": "X = 1\n"}, 1, id="reached-only-by-a-tool"),
        pytest.param(
            {"hub/a.py": "from blizzard.foundation import shared\n", "foundation/shared.py": "X = 1\n"},
            1,
            id="a-symbol-import-names-the-module-not-its-package",
        ),
        pytest.param(
            {"hub/a.py": _SHARED, "foundation/shared.py": "X = 1\n", "foundation/internal/impl.py": "Y = 1\n"},
            1,
            id="an-internal-module-is-not-judged",
        ),
        pytest.param(
            {"hub/a.py": "from blizzard.foundation.pkg import sub\n", "foundation/pkg/sub.py": "Z = 1\n"},
            1,
            id="a-docstring-only-package-init-is-not-judged",
        ),
    ],
)
def test_the_single_side_check_judges_each_planted_module(
    tmp_path: Path, files: Mapping[str, str], expected: int
) -> None:
    root = _plant_kernel(tmp_path, files)
    assert len(_single_side_kernel_modules(root, {})) == expected, _single_side_kernel_modules(root, {})


def test_the_single_side_check_fails_a_stale_allowlist_entry(tmp_path: Path) -> None:
    shared = {"hub/a.py": _SHARED, "runner/b.py": _SHARED, "foundation/shared.py": "X = 1\n"}
    root = _plant_kernel(tmp_path, shared)
    assert _single_side_kernel_modules(root, {"blizzard.foundation.shared": "no longer one-sided"}) == [
        "blizzard.foundation.shared is on the allowlist but is shared — drop the entry"
    ]
    assert _single_side_kernel_modules(root, {"blizzard.foundation.gone": "deleted"}) == [
        "blizzard.foundation.gone is on the allowlist but is no judged kernel module"
    ]


def test_the_single_side_check_admits_an_allowlisted_one_sided_module(tmp_path: Path) -> None:
    root = _plant_kernel(tmp_path, {"hub/a.py": _SHARED, "foundation/shared.py": "X = 1\n"})
    assert _single_side_kernel_modules(root, {"blizzard.foundation.shared": "kept low on purpose"}) == []


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
        for path in _production_files(root)
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
    for path in _production_files(root):
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
    """A-C only see imports; a module path threaded as a string is invisible to them,
    so this walker catches a daemon named by string inside foundation."""
    violations = _daemon_named_strings(_FOUNDATION_DIR, ("blizzard.hub.", "blizzard.runner."))
    assert not violations, f"N — foundation must not name either daemon, even as a string: {violations}"


def _bare_engine_accesses(root: Path, *, exempt: frozenset[Path] = frozenset()) -> list[str]:
    violations: list[str] = []
    for path in _production_files(root):
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


_TENANT_GUARD_MODULE = "blizzard.hub.store.tenant_guard"
_HUB_SCOPED_NAMES = frozenset({"HUB_SCOPED_OPTION", "blizzard_hub_scoped"})


def _tenant_seam_breaches(src_root: Path) -> list[str]:
    """Each place under ``src_root`` (a ``blizzard`` package root) that reaches the tenant seam from outside it: an
    import of the statement guard outside ``hub/store/`` and the hub app that installs it; the hub-scoped exemption
    named — as an import, a name, an attribute, a keyword argument or a string — anywhere but the exemption's own
    holders (:func:`_holds_hub_scoped_exemption`); ``ScopedConnections`` constructed outside ``hub/store/`` rather
    than opened through ``HubStoreConnections.scoped``."""
    store_dir = src_root / "hub" / "store"
    guard_importers = (src_root / "hub" / "app.py",)
    breaches: list[str] = []
    for path in _production_files(src_root):
        in_store = path.is_relative_to(store_dir)
        holds_exemption = _holds_hub_scoped_exemption(path, src_root)
        where = path.relative_to(src_root.parent)
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not holds_exemption and _HUB_SCOPED_NAMES.intersection(_names_spelled(node)):
                breaches.append(f"{where}:{getattr(node, 'lineno', '?')} names the hub-scoped exemption")
            if in_store:
                continue
            if isinstance(node, ast.Import | ast.ImportFrom) and path not in guard_importers:
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    base = _absolute_module(path, src_root, node)
                    modules = [base, *(f"{base}.{alias.name}" for alias in node.names)]
                if _TENANT_GUARD_MODULE in modules:
                    breaches.append(f"{where}:{node.lineno} imports {_TENANT_GUARD_MODULE}")
            if isinstance(node, ast.Call) and _terminal(node.func) == "ScopedConnections":
                breaches.append(f"{where}:{node.lineno} constructs ScopedConnections")
    return breaches


def _holds_hub_scoped_exemption(path: Path, src_root: Path) -> bool:
    """Whether ``path`` is one of the few modules that may name the hub-scoped exemption: the tenant primitives that
    declare it, the statement guard that honours it, the ``HubScopedReads`` modules (``hub/store/internal/
    hub_scoped_*.py``), the invariant checker's hub half (its cross-tenant audit), and the hub's migrations."""
    internal = src_root / "hub" / "store" / "internal"
    holders = (src_root / "hub" / "tenancy.py", src_root / "hub" / "store" / "tenant_guard.py")
    return (
        path in (*holders, internal / "invariants.py")
        or (path.parent == internal and path.name.startswith("hub_scoped_"))
        or path.is_relative_to(src_root / "hub" / "store" / "migrations")
    )


def _names_spelled(node: ast.AST) -> list[str]:
    """The identifiers or string a node spells: an imported name, a bare name, an attribute, a keyword argument's
    name, a string literal."""
    if isinstance(node, ast.ImportFrom):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.keyword):
        return [node.arg] if node.arg is not None else []
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return []


def test_the_tenant_seam_is_reached_only_through_its_own_doors() -> None:
    """``bzh:tenant-scoped-store`` and ``bzh:hub-scoped-reads-confined``: the statement guard is installed by the hub
    app alone, a scoped connections object is opened only through ``HubStoreConnections.scoped``, and the exemption
    that lets a statement past the guard is named only by the tenant primitives, the guard, ``HubScopedReads``, the
    invariant checker's cross-tenant audits, and migrations."""
    breaches = _tenant_seam_breaches(_SRC_DIR)
    assert not breaches, f"tenant seam — reached from outside its own doors: {breaches}"


def _plant_tenant_seam(tmp_path: Path, rel: str, text: str) -> list[str]:
    src = tmp_path / "blizzard"
    (src / rel).parent.mkdir(parents=True, exist_ok=True)
    (src / rel).write_text(text)
    return _tenant_seam_breaches(src)


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("hub/domain/x.py", "from blizzard.hub.store.tenant_guard import install_tenant_guard\n"),
        ("hub/domain/x.py", "import blizzard.hub.store.tenant_guard\n"),
        ("hub/domain/x.py", "from blizzard.hub.store import tenant_guard\n"),
        ("hub/composition.py", "from .store.tenant_guard import install_tenant_guard\n"),
        ("hub/domain/x.py", "from blizzard.hub.tenancy import HUB_SCOPED_OPTION\n"),
        ("hub/api/x.py", "import blizzard.hub.tenancy as t\nOPTIONS = {t.HUB_SCOPED_OPTION: True}\n"),
        ("hub/api/x.py", 'OPTIONS = {"blizzard_hub_scoped": True}\n'),
        ("hub/domain/x.py", "conn = ScopedConnections(engine, errors, scope)\n"),
        ("hub/composition.py", "conn = scoped.ScopedConnections(engine, errors, scope)\n"),
        ("hub/api/x.py", "with conns.read(op) as c:\n    c = c.execution_options(blizzard_hub_scoped=True)\n"),
        ("hub/store/internal/chunks_store.py", "from blizzard.hub.tenancy import HUB_SCOPED_OPTION\n"),
        ("hub/store/internal/chunks_store.py", "OPTIONS = {tenancy.HUB_SCOPED_OPTION: True}\n"),
        ("hub/store/scoped.py", "from blizzard.hub.tenancy import HUB_SCOPED_OPTION\n"),
        ("hub/store/errors.py", 'conn.execution_options(**{"blizzard_hub_scoped": True})\n'),
    ],
)
def test_tenant_seam_check_catches_every_breach(tmp_path: Path, rel: str, text: str) -> None:
    assert _plant_tenant_seam(tmp_path, rel, text)


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("hub/app.py", "from blizzard.hub.store.tenant_guard import install_tenant_guard\n"),
        ("hub/store/scoped.py", "ScopedConnections(e, r, s)\n"),
        ("hub/store/internal/x.py", "from blizzard.hub.store.tenant_guard import install_tenant_guard\n"),
        ("hub/store/internal/hub_scoped_reads.py", "from blizzard.hub.tenancy import HUB_SCOPED_OPTION\n"),
        ("hub/store/tenant_guard.py", "from blizzard.hub.tenancy import HUB_SCOPED_OPTION\n"),
        ("hub/store/migrations/versions/x.py", "from blizzard.hub.tenancy import HUB_SCOPED_OPTION\n"),
        ("hub/tenancy.py", 'HUB_SCOPED_OPTION = "blizzard_hub_scoped"\n'),
        ("hub/store/internal/invariants.py", "from blizzard.hub.tenancy import HUB_SCOPED_OPTION\n"),
        ("hub/domain/x.py", "connections = core.connections.scoped(scope)\n"),
    ],
)
def test_tenant_seam_check_admits_its_own_doors(tmp_path: Path, rel: str, text: str) -> None:
    assert _plant_tenant_seam(tmp_path, rel, text) == []


_HUB_SCOPED_SEAM_NAMES = frozenset({"HubScopedReads", "build_hub_scoped_reads"})


def _hub_scoped_member_imports(src_root: Path) -> list[str]:
    """Each import under ``src_root``, outside the ``HubScopedReads`` modules themselves, that takes from them anything
    but the aggregate and its factory — a member group, the marking helper, or a module whole. A holder that imported
    one would re-export it to every module that imports the holder, past ``bzh:hub-scoped-reads-confined``."""
    internal = src_root / "hub" / "store" / "internal"
    imports: list[str] = []
    for path in _production_files(src_root):
        if path.parent == internal and path.name.startswith("hub_scoped_"):
            continue
        where = path.relative_to(src_root.parent)
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not isinstance(node, ast.ImportFrom):
                continue
            from_seam = (node.module or "").rsplit(".", 1)[-1].startswith("hub_scoped_")
            for alias in node.names:
                if alias.name.startswith("hub_scoped_") or (from_seam and alias.name not in _HUB_SCOPED_SEAM_NAMES):
                    imports.append(f"{where}:{node.lineno} imports {alias.name} from the hub-scoped seam")
    return imports


def test_the_hub_scoped_seam_is_taken_whole() -> None:
    """``bzh:hub-scoped-reads-confined``: the modules allowed to hold ``HubScopedReads`` take the aggregate and its
    factory and nothing else, so no member group lands in a namespace another module can import it from."""
    imports = _hub_scoped_member_imports(_SRC_DIR)
    assert not imports, f"hub-scoped seam — a member taken apart from its aggregate: {imports}"


@pytest.mark.parametrize(
    ("text", "taken_apart"),
    [
        ("from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads, TenantStateReads\n", True),
        ("from .store.internal.hub_scoped_reads import hub_scoped_read\n", True),
        ("from blizzard.hub.store.internal import hub_scoped_reads\n", True),
        ("from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads, build_hub_scoped_reads\n", False),
    ],
)
def test_hub_scoped_seam_check_refuses_a_member_taken_apart(tmp_path: Path, text: str, taken_apart: bool) -> None:
    src = tmp_path / "blizzard"
    (src / "hub").mkdir(parents=True)
    (src / "hub" / "composition.py").write_text(text)

    assert bool(_hub_scoped_member_imports(src)) is taken_apart


_RUNNER_SCOPED_NAMES = frozenset({"RUNNER_UNSCOPED_OPTION", "blizzard_runner_unscoped"})
#: ``HostStores``' guard-exempt member, in its own module: the invariant audit.
_HOST_EXEMPT_MEMBER_MODULES = frozenset({"host_runner_audit.py"})
#: The single cross-kind write: a host-scoped sample and its runner-scoped outbound report in one transaction —
#: (host module, method, the runner table it may name there).
_CROSS_KIND_WRITE = ("host_external_usage_store.py", "record_external_usage_attempt", "outbound_buffer")


def _declared_table_scopes(schema_file: Path) -> dict[str, str]:
    """Each table ``schema_file`` declares, by its module-level name, mapped to its ``info={"scope": ...}`` — read
    statically, so a planted tree needs no importable schema."""
    scopes: dict[str, str] = {}
    for node in ast.parse(schema_file.read_text(), filename=str(schema_file)).body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        if _terminal(node.value.func) != "Table" or not isinstance(node.targets[0], ast.Name):
            continue
        for keyword in node.value.keywords:
            if keyword.arg == "info" and isinstance(keyword.value, ast.Dict):
                for key, value in zip(keyword.value.keys, keyword.value.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value == "scope" and isinstance(value, ast.Constant):
                        scopes[node.targets[0].id] = str(value.value)
    return scopes


def _table_names_in(tree: ast.AST) -> Iterator[tuple[ast.AST, str, str | None]]:
    """Every identifier ``tree`` spells (an imported name, a bare name, an attribute), with the function it sits in."""

    def walk(node: ast.AST, function: str | None) -> Iterator[tuple[ast.AST, str, str | None]]:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            function = node.name
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                yield node, alias.name, function
        elif isinstance(node, ast.Name):
            yield node, node.id, function
        elif isinstance(node, ast.Attribute):
            yield node, node.attr, function
        for child in ast.iter_child_nodes(node):
            yield from walk(child, function)

    yield from walk(tree, None)


def _host_store_breaches(src_root: Path) -> list[str]:
    """Each place under ``src_root`` (a ``blizzard`` package root) that reaches across the runner store's two kinds:
    a runner adapter module (``runner/store/internal/`` but not ``host_*``) naming a host-scoped table; a host adapter
    module naming a runner-scoped table, bar :data:`_CROSS_KIND_WRITE`'s outbound report (the exempt member, which
    audits runner rows by design, is confined by :func:`_runner_seam_breaches` instead); and the runner-scoped
    exemption named anywhere but the seam that declares it, the guard that honours it, the exempt member's module, and
    runner migrations."""
    store = src_root / "runner" / "store"
    internal = store / "internal"
    scopes = _declared_table_scopes(store / "schema.py")
    breaches: list[str] = []
    for path in sorted(internal.glob("*.py")) if internal.is_dir() else []:
        host_module = path.name.startswith("host_")
        if path.name in _HOST_EXEMPT_MEMBER_MODULES:
            continue
        foreign = "runner" if host_module else "host"
        where = path.relative_to(src_root.parent)
        tree = ast.parse(path.read_text(), filename=str(path))
        if not host_module:
            # A host module's exempt statements are reached through its HostStores member, handed in by composition.
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and ".internal.host_" in f".{node.module or ''}":
                    breaches.append(f"{where}:{node.lineno} — a runner adapter imports the host module {node.module}")
        seen: set[tuple[int, str]] = set()
        for node, name, function in _table_names_in(tree):
            if scopes.get(name) != foreign:
                continue
            module, method, table = _CROSS_KIND_WRITE
            if host_module and (path.name, name) == (module, table) and function in (method, None):
                continue
            line = getattr(node, "lineno", 0)
            if (line, name) not in seen:
                seen.add((line, name))
                kind = "host adapter" if host_module else "runner adapter"
                breaches.append(f"{where}:{line} — a {kind} names the {foreign}-scoped table {name}")
    breaches += _runner_seam_breaches(src_root)
    return breaches


def _runner_seam_breaches(src_root: Path) -> list[str]:
    """Each place under ``src_root`` that names the runner-scoped exemption outside its own holders: the seam that
    declares it (``src/blizzard/runner/store/scoped.py``), the guard (``src/blizzard/runner/store/runner_guard.py``), ``HostStores``' exempt
    members' modules, and runner migrations — mirroring :func:`_tenant_seam_breaches`."""
    store = src_root / "runner" / "store"
    holders = {store / "scoped.py", store / "runner_guard.py"} | {
        store / "internal" / name for name in _HOST_EXEMPT_MEMBER_MODULES
    }
    breaches: list[str] = []
    for path in _production_files(src_root):
        if path in holders or path.is_relative_to(store / "migrations"):
            continue
        where = path.relative_to(src_root.parent)
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if _RUNNER_SCOPED_NAMES.intersection(_names_spelled(node)):
                breaches.append(f"{where}:{getattr(node, 'lineno', '?')} names the runner-scoped exemption")
    return breaches


@pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ,
    reason="sweeps the source tree, where mutmut's copy of a host adapter also carries every generated mutant body, "
    "renamed away from the cross-kind write this sweep exempts by method name",
)
def test_host_tables_are_reached_only_through_the_host_stores() -> None:
    """``bzh:host-stores-confined``: a runner adapter names no host-scoped table and a host adapter no runner-scoped
    one, bar the single cross-kind write (``record_external_usage_attempt``'s outbound report); the exemption from the
    runner statement guard is named only by its seam, the guard, ``HostStores``' exempt member, and runner
    migrations — the invariant checker reaches the store through the audit member, never spelling it."""
    breaches = _host_store_breaches(_SRC_DIR)
    assert not breaches, f"host stores — a store kind reached from outside its own doors: {breaches}"


_PLANTED_SCHEMA = (
    'leases = Table("leases", metadata, info={"scope": "runner"})\n'
    'outbound_buffer = Table("outbound_buffer", metadata, info={"scope": "runner"})\n'
    'daemon_liveness = Table("daemon_liveness", metadata, info={"scope": "host"})\n'
    'external_usage_samples = Table("external_usage_samples", metadata, info={"scope": "host"})\n'
)


def _plant_host_store(tmp_path: Path, rel: str, text: str) -> list[str]:
    src = tmp_path / "blizzard"
    (src / "runner" / "store").mkdir(parents=True)
    (src / "runner" / "store" / "schema.py").write_text(_PLANTED_SCHEMA)
    (src / rel).parent.mkdir(parents=True, exist_ok=True)
    (src / rel).write_text(text)
    return _host_store_breaches(src)


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("runner/store/internal/pause_store.py", "from blizzard.runner.store.schema import daemon_liveness\n"),
        ("runner/store/internal/usage_store.py", "rows = schema.external_usage_samples.select()\n"),
        ("runner/store/internal/host_daemon_liveness_store.py", "from blizzard.runner.store.schema import leases\n"),
        (
            "runner/store/internal/host_external_usage_store.py",
            "def prune_external_usage_samples(self):\n    return outbound_buffer.delete()\n",
        ),
        ("tools/invariants.py", "from blizzard.runner.store.scoped import RUNNER_UNSCOPED_OPTION\n"),
        ("runner/store/internal/pause_store.py", 'OPTIONS = {"blizzard_runner_unscoped": True}\n'),
        (
            "runner/store/internal/identity_store.py",
            "from blizzard.runner.store.internal.host_runner_audit import RunnerAudit\n",
        ),
        (
            "runner/store/internal/host_daemon_liveness_store.py",
            "conn.execution_options(**{RUNNER_UNSCOPED_OPTION: 1})\n",
        ),
        ("runner/composition.py", "from blizzard.runner.store.scoped import RUNNER_UNSCOPED_OPTION\n"),
    ],
)
def test_host_store_check_catches_every_breach(tmp_path: Path, rel: str, text: str) -> None:
    assert _plant_host_store(tmp_path, rel, text)


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("runner/store/internal/pause_store.py", "from blizzard.runner.store.schema import leases\n"),
        ("runner/store/internal/host_daemon_liveness_store.py", "rows = daemon_liveness.select()\n"),
        (
            "runner/store/internal/host_external_usage_store.py",
            "from blizzard.runner.store.schema import outbound_buffer\n"
            "def record_external_usage_attempt(self):\n    return outbound_buffer.insert()\n",
        ),
        ("runner/store/internal/host_runner_audit.py", "OPTIONS = {RUNNER_UNSCOPED_OPTION: True}\nleases\n"),
        (
            "runner/store/internal/host_runner_audit.py",
            "from blizzard.runner.store.scoped import RUNNER_UNSCOPED_OPTION\n",
        ),
        ("runner/store/runner_guard.py", "from blizzard.runner.store.scoped import RUNNER_UNSCOPED_OPTION\n"),
        ("runner/store/scoped.py", 'RUNNER_UNSCOPED_OPTION = "blizzard_runner_unscoped"\n'),
        ("runner/store/migrations/versions/x.py", "from blizzard.runner.store.scoped import RUNNER_UNSCOPED_OPTION\n"),
    ],
)
def test_host_store_check_admits_its_own_doors(tmp_path: Path, rel: str, text: str) -> None:
    assert _plant_host_store(tmp_path, rel, text) == []


_OUTBOX_TABLES = frozenset({"outbound_buffer", "transcript_outbound_buffer"})
_INSERT_BUILDERS = frozenset({"insert", "insert_rows", "insert_from_select"})
_FACT_TABLES = frozenset(
    {"env_bindings", "binding_releases", "pause_parks", "pause_park_resumes", "requeues", "leases"}
)
_FACT_INSERT_SQL = re.compile(
    r"insert\s+(or\s+\w+\s+)?into\s+\W*(env_bindings|binding_releases|pause_parks|pause_park_resumes|requeues|leases)\b",
    re.IGNORECASE,
)
_OUTBOX_INSERT_SQL = re.compile(r"insert\s+(or\s+\w+\s+)?into\s+\W*(transcript_)?outbound_buffer\b", re.IGNORECASE)
_OUTBOX_SEAM_FILE = Path("runner") / "store" / "internal" / "base.py"


def _insert_breaches(
    src_root: Path, tables: frozenset[str], raw_insert: re.Pattern[str], what: str, *, exempt: tuple[Path, ...] = ()
) -> list[str]:
    """Each place under ``src_root`` (a ``blizzard`` package root) that inserts into one of ``tables`` other than
    through the seam in ``src/blizzard/runner/store/internal/base.py``: an insert builder (``insert``, ``insert_rows``,
    ``insert_from_select``, called on a store, on ``sqlalchemy``, or on the table itself) given one, under any name it
    was imported as, or raw ``INSERT INTO`` text matching ``raw_insert``. Runner migrations write the tables as they
    were and are not judged, nor is anything under an ``exempt`` root."""
    breaches: list[str] = []
    for path in _production_files(src_root):
        relative = path.relative_to(src_root)
        if relative == _OUTBOX_SEAM_FILE or relative.is_relative_to(Path("runner") / "store" / "migrations"):
            continue
        if any(relative.is_relative_to(root) for root in exempt):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        names = set(tables)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names |= {alias.asname for alias in node.names if alias.name in tables and alias.asname}
        where = path.relative_to(src_root.parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _terminal(node.func) in _INSERT_BUILDERS:
                receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
                if any(_terminal(arg) in names for arg in (receiver, *node.args)):
                    breaches.append(f"{where}:{node.lineno} inserts into {what} outside the seam")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and raw_insert.search(node.value):
                breaches.append(f"{where}:{node.lineno} inserts into {what} outside the seam")
    return breaches


def _outbox_insert_breaches(src_root: Path) -> list[str]:
    return _insert_breaches(src_root, _OUTBOX_TABLES, _OUTBOX_INSERT_SQL, "an outbox table")


def _fact_insert_breaches(src_root: Path) -> list[str]:
    # The hub store has a ``leases`` table of its own, with no sequence.
    return _insert_breaches(src_root, _FACT_TABLES, _FACT_INSERT_SQL, "a sequenced fact table", exempt=(Path("hub"),))


def test_every_outbox_row_is_inserted_through_the_seam() -> None:
    """Every outbox ``seq`` comes from the runner's durable counter (``src/blizzard/runner/store/internal/base.py``'s
    ``insert_outbox_row``), advanced in the transaction that writes the row: an adapter that inserts into
    ``outbound_buffer`` or ``transcript_outbound_buffer`` any other way would mint a ``seq`` the counter never saw."""
    breaches = _outbox_insert_breaches(_SRC_DIR)
    assert not breaches, f"outbox inserts — a row written around its counter: {breaches}"


def test_every_sequenced_fact_row_is_inserted_through_the_seam() -> None:
    """Every ``seq`` on a bind, release, park, resume, requeue or lease comes from the runner's durable fact counter
    (``src/blizzard/runner/store/internal/base.py``'s ``insert_fact_row``), advanced in the transaction that writes the
    row: an adapter that inserts into one of them any other way writes a row no closure can order."""
    breaches = _fact_insert_breaches(_SRC_DIR)
    assert not breaches, f"fact inserts — a row written around its counter: {breaches}"


#: Tables whose dialect-specific table option is an admitted exception in
#: blizzard-context:/standards/persistence.md — every other table stays inside the portable surface.
_DIALECT_OPTION_ALLOWLIST = {"in_flight_elicitations", "host_outbound_buffer"}


def _tables_with_dialect_options() -> set[str]:
    from blizzard.hub.store.schema import metadata as hub_metadata
    from blizzard.runner.store.schema import metadata as runner_metadata

    return {
        table.name for metadata in (runner_metadata, hub_metadata) for table in metadata.tables.values() if table.kwargs
    }


def test_dialect_table_options_are_all_admitted() -> None:
    """A table that sets a dialect-specific option (``sqlite_autoincrement``) must be named by the persistence
    standard's Exception, so the allowlist and the standard move together."""
    unadmitted = _tables_with_dialect_options() - _DIALECT_OPTION_ALLOWLIST
    assert not unadmitted, f"dialect-specific table options outside the persistence Exception: {sorted(unadmitted)}"
    assert _tables_with_dialect_options() == _DIALECT_OPTION_ALLOWLIST, (
        "the allowlist names a table that no longer sets one"
    )


def _plant_outbox_insert(tmp_path: Path, rel: str, text: str) -> list[str]:
    src = tmp_path / "blizzard"
    (src / rel).parent.mkdir(parents=True, exist_ok=True)
    (src / rel).write_text(text)
    return _outbox_insert_breaches(src)


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        (
            "runner/store/internal/pause_store.py",
            "def f(self, conn):\n    conn.execute(self._store.insert(outbound_buffer).values(kind='k'))\n",
        ),
        (
            "runner/store/internal/usage_store.py",
            "def f(self, conn):\n    conn.execute(sa.insert(schema.transcript_outbound_buffer))\n",
        ),
        ("runner/store/internal/usage_store.py", "def f(conn):\n    conn.execute(outbound_buffer.insert())\n"),
        (
            "runner/store/internal/usage_store.py",
            "from blizzard.runner.store.schema import outbound_buffer as ob\ndef f(s):\n    return s.insert_rows(ob, [])\n",
        ),
        ("runner/store/internal/usage_store.py", "SQL = 'INSERT INTO outbound_buffer (kind) VALUES (1)'\n"),
        ("tools/invariants.py", "def f(c):\n    c.execute(insert(transcript_outbound_buffer))\n"),
    ],
)
def test_outbox_insert_check_catches_a_bypass(tmp_path: Path, rel: str, text: str) -> None:
    assert _plant_outbox_insert(tmp_path, rel, text)


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        (
            "runner/store/internal/base.py",
            "def f(store, conn):\n    conn.execute(store.insert(outbound_buffer).values(seq=1))\n",
        ),
        (
            "runner/store/internal/pause_store.py",
            "def f(self, conn):\n    insert_outbox_row(self._store, conn, outbound_buffer, kind='k')\n",
        ),
        (
            "runner/store/internal/pause_store.py",
            "def f(self, conn):\n    conn.execute(self._store.insert(pause_parks))\n",
        ),
        (
            "runner/store/migrations/versions/x.py",
            "def upgrade(bind):\n    bind.execute(sa.insert(outbound_buffer))\n",
        ),
    ],
)
def test_outbox_insert_check_admits_the_seam_and_other_tables(tmp_path: Path, rel: str, text: str) -> None:
    assert _plant_outbox_insert(tmp_path, rel, text) == []


def _plant_fact_insert(tmp_path: Path, rel: str, text: str) -> list[str]:
    src = tmp_path / "blizzard"
    (src / rel).parent.mkdir(parents=True, exist_ok=True)
    (src / rel).write_text(text)
    return _fact_insert_breaches(src)


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        (
            "runner/store/internal/environment_store.py",
            "def f(self, conn):\n    conn.execute(self._store.insert(env_bindings).values(chunk_id='c'))\n",
        ),
        ("runner/store/internal/requeue_store.py", "def f(conn):\n    conn.execute(requeues.insert())\n"),
        (
            "runner/store/internal/lease_record_store.py",
            "from blizzard.runner.store.schema import leases as ls\ndef f(s):\n    return s.insert_rows(ls, [])\n",
        ),
        ("runner/store/internal/pause_store.py", "def f(c):\n    c.execute(sa.insert(schema.pause_park_resumes))\n"),
        ("runner/store/internal/pause_store.py", "SQL = 'INSERT INTO pause_parks (lease_id) VALUES (1)'\n"),
        ("tools/invariants.py", "def f(c):\n    c.execute(insert(binding_releases))\n"),
    ],
)
def test_fact_insert_check_catches_a_bypass(tmp_path: Path, rel: str, text: str) -> None:
    assert _plant_fact_insert(tmp_path, rel, text)


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        (
            "runner/store/internal/base.py",
            "def f(store, conn):\n    conn.execute(store.insert(requeues).values(seq=1))\n",
        ),
        (
            "runner/store/internal/requeue_store.py",
            "def f(self, conn):\n    insert_fact_row(self._store, conn, requeues, chunk_id='c')\n",
        ),
        (
            "runner/store/internal/pause_store.py",
            "def f(self, conn):\n    conn.execute(self._store.insert(outbound_buffer))\n",
        ),
        ("runner/store/migrations/versions/x.py", "def upgrade(bind):\n    bind.execute(sa.insert(leases))\n"),
    ],
)
def test_fact_insert_check_admits_the_seam_and_other_tables(tmp_path: Path, rel: str, text: str) -> None:
    assert _plant_fact_insert(tmp_path, rel, text) == []


_EVENT_LOG_SERVICE_FILE = _HUB_DIR / "domain" / "chunk" / "event_log.py"
_CHUNK_EVENTS_STORE_FILE = _HUB_DIR / "store" / "internal" / "chunk_events_store.py"


def _record_event_call_sites(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _production_files(root):
        if path in (_EVENT_LOG_SERVICE_FILE, _CHUNK_EVENTS_STORE_FILE):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "record_event":
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno} calls record_event")
    return violations


def test_record_event_is_called_only_through_event_log_service() -> None:
    """Recording an event is what publishes it (``bzh:operational-event-log``):
    ``EventLogService.record`` (``src/blizzard/hub/domain/chunk/event_log.py``) is the only caller of the write
    repository's ``record_event`` — every event-authoring call site takes the service
    instead, so a new one can never land a row that stays unbroadcast."""
    violations = _record_event_call_sites(_SRC_DIR)
    assert not violations, f"M — record_event must be called only from EventLogService: {violations}"


_RUNNER_STORE_CONNECTIONS_FILE = _RUNNER_STORE_DIR / "scoped.py"


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
    violations = _violations(_HUB_STORE_INTERNAL_DIR, ("httpx2",))
    assert not violations, f"E — hub/store/internal/ is SQL-only: {violations}"


def _protocol_declarations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _production_files(root):
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
_RUNNER_STORE_SCOPED_FILE = _RUNNER_STORE_DIR / "scoped.py"
_RUNNER_STORE_GUARD_FILE = _RUNNER_STORE_DIR / "runner_guard.py"
_RUNNER_STORE_MIGRATIONS_DIR = _RUNNER_STORE_DIR / "migrations"
_RUNNER_COMPOSITION_FILE = _RUNNER_DIR / "composition.py"
_RUNNER_APP_FILE = _RUNNER_DIR / "app.py"

# AC3: every file outside the store's own package that still
# names ``sqlalchemy`` — each an accepted, individually-justified exception, not the
# store surface this criterion polices. ``None`` allows every name from that import;
# a tuple narrows to only those names.
_SQLALCHEMY_EXCEPTIONS: dict[Path, tuple[str, ...] | None] = {
    # Engine only, for DI typing — shared with src/blizzard/hub/composition.py, permanently out of
    # scope (plan's "Out of scope": "Engine in a composition root"). ``src/blizzard/hub/app.py`` holds the
    # graph's engine only as the handle it hands its own callers to dispose.
    _RUNNER_COMPOSITION_FILE: ("Engine",),
    _RUNNER_APP_FILE: ("Engine",),
    # IntegrityError only, for the replay-check catch: the collision itself IS the
    # business-logic check, so this one name stays local instead of the table-bound form.
    _RUNNER_DIR / "store" / "internal" / "host_jti_cache_store.py": ("IntegrityError",),
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
    schema, errors, statement guard and migrations may hold — every other module takes the Protocol seam,
    never the driver underneath it, bar the individually-justified exceptions above."""
    violations: list[str] = []
    for path in _production_files(_RUNNER_DIR):
        if (
            path.is_relative_to(_RUNNER_STORE_INTERNAL_DIR)
            or path in (_RUNNER_STORE_SCHEMA_FILE, _RUNNER_STORE_SCOPED_FILE, _RUNNER_STORE_GUARD_FILE)
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
    ``src/blizzard/runner/composition.py`` and nowhere else under ``src/`` — every other collaborator
    takes a Protocol seam or the ``RunnerStores`` bundle it builds."""
    adapters = _runner_store_adapter_names()
    violations: list[str] = []
    for path in _production_files(_SRC_DIR):
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
    """I: the process-graph constructors are called from ``src/blizzard/runner/composition.py`` and nowhere
    else under ``src/`` — every other root takes the one ``RunnerProcess`` graph."""
    violations: list[str] = []
    for path in _production_files(_SRC_DIR):
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
        _RUNNER_DIR / "cli" / "runners.py",
        _RUNNER_DIR / "cli" / "store.py",
        _RUNNER_DIR / "cli" / "store_guard.py",
        _RUNNER_COMPOSITION_FILE,
        _RUNNER_DIR / "cli" / "opencode.py",
        _RUNNER_DIR / "cli" / "children.py",
        _SRC_DIR / "cli" / "dev.py",
        _SRC_DIR / "tools" / "invariants.py",
        _SRC_DIR / "tools" / "compat" / "cli.py",
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
    for path in _production_files(root):
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
    for path in _production_files(root):
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
    """Only `foundation/operator_sessions/` (the Protocol's own package) and the hub's `SessionService` module may
    name `IWriteSessionStore` — every CLI module, the hub's login/logout included, takes the `SessionService`
    application service instead, never the raw write seam (``bzh:controller-read-only``)."""
    sessions_dir = _FOUNDATION_DIR / "operator_sessions"
    exempt = _COMPOSITION_ROOTS | set(sessions_dir.rglob("*.py")) | {_HUB_DIR / "cli" / "session_service.py"}
    violations = _write_session_store_accesses(_SRC_DIR, exempt=exempt)
    assert not violations, (
        f"D4 — IWriteSessionStore must be named only inside foundation/operator_sessions/ and hub/cli/session_service.py: {violations}"
    )


_RUNNER_API_DIR = _RUNNER_DIR / "api"


def test_runner_api_names_no_write_capable_store_or_bundle() -> None:
    """AC: a runner route resolves only ``RunnerReadStores`` and its
    per-concept mutation services — never ``RunnerStores`` nor an ``IWrite*`` seam, which
    would let a route mutate directly instead of delegating to a domain service."""
    violations: list[str] = []
    for path in _production_files(_RUNNER_API_DIR):
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
    for path in _production_files(src_root):
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


#: The harness binding ids, in the catalog's order — the one roster every other binding list derives from.
_HARNESS_IDS = tuple(kind.harness_id for kind in HARNESS_SECTION_KINDS)
_ADAPTER_PACKAGES = _HARNESS_IDS
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
    any harness module importing ``runner/loop`` or its composition root. ``src/blizzard/runner/harness/wiring.py`` and
    ``exempt`` may name an adapter; since wiring names both, any other harness module importing it
    reaches every adapter through it."""
    harness = f"{src_root.name}.runner.harness"
    wiring = src_root / "runner" / "harness" / "wiring.py"
    violations: list[str] = []
    for path in _production_files(src_root):
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
    ``src/blizzard/runner/harness/wiring.py`` and the composition roots import an adapter package
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
        for path in _production_files(root)
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
        node.annotation.slice.id
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.annotation, ast.Subscript)
        and isinstance(node.annotation.value, ast.Name)
        and node.annotation.value.id == "type"
        and isinstance(node.annotation.slice, ast.Name)
        and isinstance(node.value, ast.Name)
        and node.value.id == "LoopContext"
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
    sentinel = "_{}_conforms: type[{}] = LoopContext\n"
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


_DOMAIN_CORE_FRAMEWORKS = ("fastapi", "starlette", "sqlalchemy", "click", "httpx2")
_DOMAIN_CORE_STDLIB_DRIVERS = ("os", "shutil", "subprocess", "tempfile")
_DOMAIN_CORE_FORBIDDEN = (*_DOMAIN_CORE_FRAMEWORKS, *_DOMAIN_CORE_STDLIB_DRIVERS)

#: The harness binding nodes: adapters, held to the framework imports only; a binding without a layer row fails the gate.
_HARNESS_BINDING_NODES = tuple(f"harness/{harness_id}" for harness_id in _HARNESS_IDS)

#: Runner module -> the stdlib driver packages admitted for it, each a reasoned exception; an entry that
#: admits a package its module no longer imports is stale and fails the gate.
_DOMAIN_CORE_DRIVER_EXCEPTIONS: dict[Path, tuple[str, ...]] = {
    # The one reader of the daemon's own environment — the single owner ``bzh:worker-env-allowlist`` requires.
    _RUNNER_DIR / "process" / "child_env.py": ("os",),
    # The one home of ``os.killpg``, which its TERM, grace, KILL sequence signals through.
    _RUNNER_DIR / "process" / "groups.py": ("os",),
    # The canary builds paths in its own throwaway scratch workdir.
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
            "harness/claude_code/http.py": "import httpx2\n",
            "harness/wiring.py": "import tempfile\n",
        },
    )
    files = _runner_domain_core_files(runner, _PLANTED_RUNNER_LAYERS)
    flagged = _driver_import_violations(runner, files, {})
    assert "leases/launch.py imports subprocess" in flagged
    assert "harness/wiring.py imports tempfile" in flagged
    assert "harness/claude_code/http.py imports httpx2" in flagged
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
    "projects": frozenset({"config"}),
    "graph": frozenset({"artifact"}),
    "runners": frozenset(),
    "chunk": frozenset({"graph", "runners", "artifact", "projects"}),
    "actions": frozenset({"chunk", "graph", "artifact", "config", "projects"}),
    "delivery": frozenset({"actions", "chunk", "graph", "config", "projects"}),
    "execution": frozenset({"chunk", "graph", "runners", "artifact"}),
    "operations": frozenset({"execution", "chunk", "graph", "runners"}),
    "work_items": frozenset({"operations", "chunk", "graph", "projects"}),
    "garden": frozenset({"work_items", "chunk", "graph", "config", "projects"}),
    "config_apply": frozenset({"garden", "graph", "config", "projects"}),
    "observability": frozenset({"chunk", "graph", "runners"}),
    "tenancy": frozenset({"graph", "config", "projects", "observability"}),
    "sweeps": frozenset(),
}
_DOMAIN_SHARED_KERNEL = "kernel"


#: The hub's edge modules beside its domain: config reads and parses, delivery executes.
_HUB_DOMAIN_EDGES = ("config", "delivery")


def _hub_domain_tree(domain_dir: Path, layers: Mapping[str, frozenset[str]]) -> _LayeredTree:
    """The hub domain as a layered tree: ``kernel`` is its shared node, its edge modules are refused."""
    umbrella = ("blizzard", "hub", "domain")
    refused = tuple((*umbrella[:-1], edge) for edge in _HUB_DOMAIN_EDGES)
    return _LayeredTree(domain_dir, umbrella, layers, shared=_DOMAIN_SHARED_KERNEL, refused=refused)


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


@dataclass(frozen=True)
class _LayeredTree:
    """One layered tree under ``bzh:domain-package-layers``: its ``layers`` table, the ``shared`` node every node
    may import, the ``refused`` edge modules, the ``subnodes`` that are nodes of their own, and the ``exempt``
    top-level names that are edges."""

    root: Path
    umbrella: tuple[str, ...]
    layers: Mapping[str, frozenset[str]]
    shared: str | None = None
    refused: tuple[tuple[str, ...], ...] = ()
    subnodes: frozenset[str] = frozenset()
    exempt: frozenset[str] = frozenset()

    @property
    def src_root(self) -> Path:
        return self.root.parents[len(self.umbrella) - 1]


def _layer_node(tree: _LayeredTree, segments: Iterable[str]) -> str | None:
    """The node a dotted path under the tree's root (given as its segments) belongs to: a subnode, else its
    first segment; ``None`` for the bare root package."""
    parts = list(segments)
    if not parts:
        return None
    if len(parts) > 1 and f"{parts[0]}/{parts[1]}" in tree.subnodes:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _layer_module_node(tree: _LayeredTree, path: Path) -> str | None:
    rel = path.relative_to(tree.root).with_suffix("").parts
    return _layer_node(tree, rel[:-1] if rel[-1] == "__init__" else rel)


def _layer_statements(tree: _LayeredTree) -> Iterator[tuple[Path, int, str | None, list[list[str]]]]:
    """Each import statement of a declared node's module as ``(path, lineno, own node, bound names' segments)``."""
    for path in _production_files(tree.root):
        own = _layer_module_node(tree, path)
        if own is not None and own not in tree.layers:
            continue
        for lineno, named in _import_statements(path, tree.src_root):
            yield path, lineno, own, named


def _layer_targets(tree: _LayeredTree, named: Iterable[list[str]]) -> list[tuple[list[str], str | None]]:
    """The ``(module, target node)`` of each name that lies under the tree's root."""
    depth = len(tree.umbrella)
    return [(module, _layer_node(tree, module[depth:])) for module in named if tuple(module[:depth]) == tree.umbrella]


def _layer_crossings(tree: _LayeredTree) -> list[str]:
    """Every import, wherever it sits, that a node's row does not allow: the shared node, a node in its row and its
    own node pass; a refused edge module, a module that is no node, or the bare root package fails."""
    violations: list[str] = []
    for path, lineno, own, named in _layer_statements(tree):
        rel = path.relative_to(tree.root)
        allowed = {
            node for node in (own, tree.shared, *(tree.layers[own] if own is not None else ())) if node is not None
        }
        refused = next((m for m in named for edge in tree.refused if tuple(m[: len(edge)]) == edge), None)
        if refused is not None:
            violations.append(f"{rel}:{lineno} imports the edge {'.'.join(refused)}")
            continue
        crossing = next(((m, t) for m, t in _layer_targets(tree, named) if t not in allowed), None)
        if crossing is not None:
            module, target = crossing
            violations.append(
                f"{rel}:{lineno} ({own or 'the root package'}) imports {'.'.join(module)}"
                f" ({target or 'the bare package'})"
            )
    return violations


def _unused_layer_edges(tree: _LayeredTree) -> list[str]:
    """Every declared edge no import walks: slack a sideways or upward import could later pass along unreviewed."""
    used = {
        (own, target)
        for _, _, own, named in _layer_statements(tree)
        for _, target in _layer_targets(tree, named)
        if target is not None and target != own
    }
    return sorted(f"{own} -> {dep}" for own, deps in tree.layers.items() for dep in deps if (own, dep) not in used)


def _undeclared_layer_nodes(tree: _LayeredTree) -> list[str]:
    """Every package outside the exempt edges, and every top-level module outside them, that maps to no node —
    a new package or module enters the graph."""
    undeclared: list[str] = []
    for directory in sorted({path.parent for path in _production_files(tree.root)}):
        rel = directory.relative_to(tree.root).parts
        if rel and rel[0] not in tree.exempt and _layer_node(tree, rel) not in tree.layers:
            undeclared.append("/".join(rel))
    for module in sorted(tree.root.glob("*.py")):
        if module.stem not in {"__init__", *tree.exempt} and module.stem not in tree.layers:
            undeclared.append(module.name)
    return undeclared


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
    tree = _hub_domain_tree(_HUB_DOMAIN_DIR, _DOMAIN_PACKAGE_LAYERS)
    violations = [*_layer_crossings(tree), *_undeclared_layer_nodes(tree)]
    assert not violations, f"a hub domain package may import only its own layer's dependencies: {violations}"


def test_every_hub_domain_layer_edge_is_one_the_code_uses() -> None:
    unused = _unused_layer_edges(_hub_domain_tree(_HUB_DOMAIN_DIR, _DOMAIN_PACKAGE_LAYERS))
    assert not unused, f"a hub domain layer row may allow only edges today's code walks; drop: {unused}"


def _domain_init_reexports(domain_dir: Path) -> list[str]:
    """Every package ``__init__.py`` under ``domain_dir`` holding anything beyond a docstring and the
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


@pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ,
    reason="sweeps package inits, which mutmut rewrites to import its generated trampoline names",
)
def test_hub_domain_package_inits_re_export_nothing() -> None:
    assert _domain_init_reexports(_HUB_DOMAIN_DIR) == []


@pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ,
    reason="sweeps package inits, which mutmut rewrites to import its generated trampoline names",
)
def test_runner_node_package_inits_re_export_nothing() -> None:
    nodes = [node for node in _RUNNER_PACKAGE_LAYERS if "/" not in node and (_RUNNER_DIR / node).is_dir()]
    assert [f"{node}/{init}" for node in nodes for init in _domain_init_reexports(_RUNNER_DIR / node)] == []


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


def _layer_second_spellings(tree: _LayeredTree, importer_roots: Iterable[Path]) -> list[str]:
    """Every ``from <module> import <name>`` under ``importer_roots`` whose ``<module>`` sits under the tree's
    root and only imports ``<name>`` rather than defining it — a second import path for a name defined
    elsewhere. Relative imports resolve against the importer's package when it sits under the source root."""
    src_root = tree.src_root
    umbrella = list(tree.umbrella)
    bindings: dict[tuple[str, ...], set[str] | None] = {}

    def only_imported(module: tuple[str, ...]) -> set[str] | None:
        if module not in bindings:
            base = src_root.joinpath(*module)
            path = base.with_suffix(".py") if base.with_suffix(".py").is_file() else base / "__init__.py"
            bindings[module] = _module_bindings(ast.parse(path.read_text()))[1] if path.is_file() else None
        return bindings[module]

    violations: list[str] = []
    for root in importer_roots:
        for path in _production_files(root):
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
    tree = _hub_domain_tree(_HUB_DOMAIN_DIR, _DOMAIN_PACKAGE_LAYERS)
    violations = _layer_second_spellings(tree, (_SRC_DIR, _TESTS_DIR))
    assert not violations, f"import a hub domain name from the module that defines it: {violations}"


def _plant_second_spelling(tmp_path: Path, rel: str, text: str) -> list[str]:
    """A two-package hub domain — ``src/blizzard/hub/domain/operations/queue.py`` imports ``ChunkNotFound`` from
    ``src/blizzard/hub/domain/chunk/errors.py``, which defines it — with ``text`` written at ``rel`` under ``tmp_path``."""
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
    tree = _hub_domain_tree(domain, {"chunk": frozenset(), "operations": frozenset({"chunk"})})
    return _layer_second_spellings(tree, (src / "blizzard", tmp_path / "tests"))


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
    tree = _hub_domain_tree(domain, layers)
    return [*_layer_crossings(tree), *_undeclared_layer_nodes(tree)]


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
        ("newpkg/mod.py", "from blizzard.hub.domain.chunk.model import X", True),
        ("chunk/ports.py", "from blizzard.hub.domain.chunk.model import X", False),
        ("chunk/ports.py", "from .model import X", False),
        ("chunk/ports.py", "from blizzard.hub.domain.kernel.unset import UNSET", False),
        ("chunk/ports.py", "from blizzard.hub.domain import kernel", False),
        ("garden/ports.py", "def f():\n    from blizzard.hub.domain.chunk.model import X", False),
        ("chunk/ports.py", "import blizzard.hub.config", True),
        ("chunk/ports.py", "from blizzard.hub.delivery.hub_node import HubNodeRouter", True),
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


# ``bzh:hub-action-purity`` — every module under ``hub/delivery/actions/`` imports only what this allowlist names.
_HUB_ACTIONS_DIR = _HUB_DIR / "delivery" / "actions"
_ACTION_STDLIB = frozenset(
    {
        "__future__",
        "abc",
        "asyncio",
        "base64",
        "collections",
        "collections.abc",
        "contextlib",
        "dataclasses",
        "datetime",
        "enum",
        "functools",
        "hashlib",
        "itertools",
        "json",
        "math",
        "re",
        "textwrap",
        "types",
        "typing",
        "urllib.parse",
    }
)
_ACTION_FOUNDATION = frozenset(
    f"blizzard.foundation.{name}" for name in ("roles", "escalation_causes", "findings", "garden_proposals", "repo_ref")
)
_ACTION_BLIZZARD = frozenset(
    {
        "blizzard.hub.domain.actions",
        "blizzard.hub.domain.graph.actions",
        "blizzard.hub.domain.kernel.garden_delivery_fault",
        "blizzard.hub.domain.config.repositories",
        "blizzard.hub.domain.config.capabilities",
        "blizzard.hub.domain.config.landing_policies",
        "blizzard.hub.forge.landing",
        "blizzard.hub.delivery.actions",
    }
)
#: ``asyncio`` is admitted whole, so the attributes reaching a process or a connection are refused by name.
_ACTION_REFUSED_ASYNCIO = frozenset(
    {
        "subprocess",
        "create_subprocess_exec",
        "create_subprocess_shell",
        "open_connection",
        "open_unix_connection",
        "start_server",
    }
)
#: What no blizzard module an action reaches may import: the modules that reach a process or the network.
_ACTION_TRANSITIVE_REFUSED = (
    "subprocess",
    "socket",
    "ssl",
    "urllib.request",
    "http.client",
    "multiprocessing",
    "ctypes",
)


def _action_imports(path: Path, src_root: Path) -> Iterator[tuple[int, str, str | None]]:
    """Each import in ``path`` as its line, the module it names — a relative one resolved against ``path``'s own
    package under ``src_root`` — and, for a ``from`` import, the name it takes from that module."""
    package = list(path.relative_to(src_root).with_suffix("").parts)[:-1]
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name, None
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            for alias in node.names:
                yield node.lineno, module, alias.name


def _within(module: str, prefixes: Iterable[str]) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


def _action_import_admitted(module: str, name: str | None) -> bool:
    """A plain import of an allowlisted module, or a name taken from one — or a submodule taken from a package
    whose submodule is allowlisted (``from blizzard.foundation import roles``)."""
    # Test-fenced crash points are the sole admitted process-affecting hook, not the crash module's other API.
    if module == "blizzard.foundation.crash" and name == "crashpoint":
        return True
    candidates = [module] if name is None else [module, f"{module}.{name}"]
    for candidate in candidates:
        if candidate in _ACTION_STDLIB or candidate in _ACTION_FOUNDATION or _within(candidate, _ACTION_BLIZZARD):
            return candidate != "asyncio" or name not in _ACTION_REFUSED_ASYNCIO
    return False


def _module_path(module: str, src_root: Path) -> Path | None:
    """The source file a dotted blizzard module loads from under ``src_root``, if it is blizzard's own."""
    base = src_root.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _os_misuses(path: Path, src_root: Path) -> list[str]:
    """Every use of ``os`` in ``path`` but ``os.urandom``."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found = [
        f"{path.relative_to(src_root)}:{line} takes os.{name}"
        for line, module, name in _action_imports(path, src_root)
        if module == "os" and name is not None and name != "urandom"
    ]
    found += [
        f"{path.relative_to(src_root)}:{node.lineno} reaches os.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr != "urandom"
    ]
    return found


def _reached_violations(
    reached: list[str],
    module_root: Path,
    refused: tuple[str, ...],
    *,
    os_misuse: bool,
    process_calls: bool = False,
    crash_hook: bool = False,
) -> list[str]:
    """Walk every blizzard module ``reached`` names — and each it imports in turn, through its parent packages — under
    ``module_root``, naming each import of a ``refused`` module, and, with ``os_misuse``, any use of ``os`` but
    ``os.urandom``, and with ``process_calls``, any ``asyncio`` process call."""
    violations: list[str] = []
    seen: set[Path] = set()
    while reached:
        module = reached.pop()
        # A package's own `__init__` loads before any of its modules does.
        parts = module.split(".")
        for depth in range(1, len(parts) + 1):
            path = _module_path(".".join(parts[:depth]), module_root)
            if path is None or path in seen:
                continue
            seen.add(path)
            for line, imported, name in _action_imports(path, module_root):
                if (
                    crash_hook
                    and path.relative_to(module_root).as_posix()
                    in {
                        "blizzard/hub/delivery/actions/landing/park.py",
                        "blizzard/hub/delivery/actions/landing/fast_forward.py",
                    }
                    and imported == "blizzard.foundation.crash"
                    and name == "crashpoint"
                ):
                    continue
                if _within(imported, refused) or (name is not None and _within(f"{imported}.{name}", refused)):
                    violations.append(
                        f"{path.relative_to(module_root)}:{line} imports {imported}, reached by an action"
                    )
                elif imported.startswith("blizzard."):
                    reached += [imported, f"{imported}.{name}"] if name is not None else [imported]
            if os_misuse:
                violations += _os_misuses(path, module_root)
            if process_calls:
                violations += _asyncio_process_calls(path, module_root)
    return violations


def _hub_action_purity_violations(actions_dir: Path, src_root: Path, module_root: Path | None = None) -> list[str]:
    """``bzh:hub-action-purity``'s import gate over ``actions_dir``: every import on the allowlist, no refused
    ``asyncio`` attribute, and no blizzard module the actions reach — directly or through another — importing a
    module that reaches a process or the network, or using any of ``os`` but ``os.urandom``. The blizzard modules
    resolve under ``module_root``, ``src_root`` unless the actions live outside it."""
    violations: list[str] = []
    reached: list[str] = []
    for path in _production_files(actions_dir):
        rel = path.relative_to(src_root)
        for line, module, name in _action_imports(path, src_root):
            crash_allowed = rel.as_posix() in {
                "blizzard/hub/delivery/actions/landing/park.py",
                "blizzard/hub/delivery/actions/landing/fast_forward.py",
            }
            if not _action_import_admitted(module, name) or (
                module == "blizzard.foundation.crash" and not crash_allowed
            ):
                violations.append(f"{rel}:{line} imports {module if name is None else f'{module}.{name}'}")
            elif module.startswith("blizzard.") and not (
                module == "blizzard.foundation.crash" and name == "crashpoint"
            ):
                reached += [module, f"{module}.{name}"] if name is not None else [module]
        violations += [
            f"{rel}:{node.lineno} reaches asyncio.{node.attr}"
            for node in ast.walk(ast.parse(path.read_text(), filename=str(path)))
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "asyncio"
            and node.attr in _ACTION_REFUSED_ASYNCIO
        ]
    violations += _reached_violations(
        reached, module_root or src_root, _ACTION_TRANSITIVE_REFUSED, os_misuse=True, crash_hook=True
    )
    return sorted(set(violations))


#: What no module under ``hub/delivery/`` reaches, directly or through another blizzard module: a child process.
_DELIVERY_REFUSED = ("subprocess",)
_DELIVERY_REFUSED_ASYNCIO = frozenset({"subprocess", "create_subprocess_exec", "create_subprocess_shell"})


def _asyncio_process_calls(path: Path, src_root: Path) -> list[str]:
    """Each ``asyncio`` process call ``path`` takes by name or reaches as an attribute."""
    rel = path.relative_to(src_root)
    found = [
        f"{rel}:{line} takes asyncio.{name}"
        for line, module, name in _action_imports(path, src_root)
        if module == "asyncio" and name in _DELIVERY_REFUSED_ASYNCIO
    ]
    found += [
        f"{rel}:{node.lineno} reaches asyncio.{node.attr}"
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path)))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "asyncio"
        and node.attr in _DELIVERY_REFUSED_ASYNCIO
    ]
    return found


def _hub_delivery_process_violations(delivery_dir: Path, src_root: Path) -> list[str]:
    """``bzh:runner-child-launch``'s transitive half over ``delivery_dir``: no module there, nor any blizzard module
    it reaches, imports ``subprocess`` or takes an ``asyncio`` process call."""
    violations: list[str] = []
    reached: list[str] = []
    for path in _production_files(delivery_dir):
        rel = path.relative_to(src_root)
        for line, module, name in _action_imports(path, src_root):
            if _within(module, _DELIVERY_REFUSED) or (
                name is not None and _within(f"{module}.{name}", _DELIVERY_REFUSED)
            ):
                violations.append(f"{rel}:{line} imports {module}")
            elif module.startswith("blizzard."):
                reached += [module, f"{module}.{name}"] if name is not None else [module]
        violations += _asyncio_process_calls(path, src_root)
    violations += _reached_violations(reached, src_root, _DELIVERY_REFUSED, os_misuse=False, process_calls=True)
    return sorted(set(violations))


@pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ,
    reason="sweeps the actions package, whose modules mutmut rewrites to import its generated trampoline names",
)
def test_hub_actions_import_only_their_allowlist() -> None:
    violations = _hub_action_purity_violations(_HUB_ACTIONS_DIR, _SRC_DIR.parent)
    assert not violations, f"a hub action may import only its allowlist (bzh:hub-action-purity): {violations}"


def _plant_actions(tmp_path: Path, files: Mapping[str, str]) -> list[str]:
    """A blizzard tree holding one clean action and an allowlisted domain module, with ``files`` laid over it."""
    root = tmp_path / "src"
    for rel, text in {
        "blizzard/__init__.py": "",
        "blizzard/hub/__init__.py": "",
        "blizzard/hub/domain/__init__.py": "",
        "blizzard/hub/domain/actions/__init__.py": "",
        "blizzard/hub/domain/actions/visit.py": "import os\n\nID = os.urandom(4)\n",
        "blizzard/hub/delivery/__init__.py": "",
        "blizzard/hub/delivery/actions/__init__.py": "",
        "blizzard/hub/delivery/actions/clean.py": (
            "from __future__ import annotations\n\nimport asyncio\n\n"
            "from blizzard.hub.domain.actions.visit import ID\n\n"
            "async def take() -> bytes:\n    await asyncio.sleep(0)\n    return ID\n"
        ),
        **files,
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return _hub_action_purity_violations(root / "blizzard" / "hub" / "delivery" / "actions", root)


@pytest.mark.parametrize(
    ("rel", "text", "caught"),
    [
        ("blizzard/hub/delivery/actions/a.py", "from blizzard.hub.store import schema", True),
        ("blizzard/hub/delivery/actions/a.py", "import os", True),
        ("blizzard/hub/delivery/actions/a.py", "import httpx2", True),
        ("blizzard/hub/delivery/actions/a.py", "from ...store import schema", True),
        ("blizzard/hub/delivery/actions/a.py", "from blizzard.foundation import process", True),
        ("blizzard/hub/delivery/actions/a.py", "from blizzard.foundation.crash import crashpoint", True),
        ("blizzard/hub/delivery/actions/landing/park.py", "from blizzard.foundation.crash import crashpoint", False),
        ("blizzard/hub/delivery/actions/a.py", "from blizzard.foundation.crash import CrashPoint", True),
        ("blizzard/hub/delivery/actions/a.py", "import blizzard.foundation.crash", True),
        ("blizzard/hub/delivery/actions/a.py", "import asyncio\n\nasyncio.create_subprocess_exec('true')", True),
        ("blizzard/hub/delivery/actions/a.py", "from asyncio import open_connection", True),
        ("blizzard/hub/domain/actions/visit.py", "import subprocess", True),
        ("blizzard/hub/domain/actions/visit.py", "import os\n\nKEY = os.environ", True),
        ("blizzard/hub/domain/actions/visit.py", "from os import environ", True),
        ("blizzard/hub/delivery/actions/a.py", "from blizzard.foundation import roles", False),
        ("blizzard/hub/delivery/actions/a.py", "from .clean import take", False),
        ("blizzard/hub/delivery/actions/a.py", "import asyncio\n\nasyncio.sleep(0)", False),
    ],
)
def test_the_hub_action_purity_gate_judges_each_planted_module(
    tmp_path: Path, rel: str, text: str, caught: bool
) -> None:
    violations = _plant_actions(tmp_path, {rel: f"{text}\n"})
    assert bool(violations) is caught, violations


def test_the_hub_action_purity_gate_follows_an_allowlisted_module_through_another(tmp_path: Path) -> None:
    violations = _plant_actions(
        tmp_path,
        {
            "blizzard/hub/domain/actions/visit.py": "from blizzard.hub.domain.helper import X\n",
            "blizzard/hub/domain/helper.py": "import socket\n\nX = socket\n",
        },
    )
    assert violations == ["blizzard/hub/domain/helper.py:1 imports socket, reached by an action"]


#: The exemplar action package, held to the same purity gate as ``src/``'s (``bzh:exemplar-conformance``).
_EXEMPLAR_ACTIONS_DIR = _REPO_ROOT / "exemplars" / "python" / "exemplar" / "hub" / "delivery" / "actions"


@pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ,
    reason="walks into the actions package, whose modules mutmut rewrites to import its generated trampoline names",
)
def test_the_exemplar_actions_import_only_their_allowlist() -> None:
    assert _production_files(_EXEMPLAR_ACTIONS_DIR), "the exemplar action package holds no module"
    violations = _hub_action_purity_violations(_EXEMPLAR_ACTIONS_DIR, _EXEMPLAR_ACTIONS_DIR.parents[3], _SRC_DIR.parent)
    assert not violations, f"an exemplar hub action may import only its allowlist (bzh:hub-action-purity): {violations}"


@pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ,
    reason="walks the delivery package, whose modules mutmut rewrites to import its generated trampoline names",
)
def test_nothing_under_hub_delivery_reaches_a_child_process() -> None:
    violations = _hub_delivery_process_violations(_HUB_DIR / "delivery", _SRC_DIR.parent)
    assert not violations, f"the hub launches no child process (bzh:runner-child-launch): {violations}"


def _plant_delivery(tmp_path: Path, files: Mapping[str, str]) -> list[str]:
    """A blizzard tree holding one clean delivery module that reaches a domain module, with ``files`` laid over it."""
    root = tmp_path / "src"
    for rel, text in {
        "blizzard/__init__.py": "",
        "blizzard/hub/__init__.py": "",
        "blizzard/hub/domain/__init__.py": "",
        "blizzard/hub/domain/model.py": "import json\n\nX = json\n",
        "blizzard/hub/delivery/__init__.py": "",
        "blizzard/hub/delivery/executor.py": "from blizzard.hub.domain.model import X\n",
        **files,
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return _hub_delivery_process_violations(root / "blizzard" / "hub" / "delivery", root)


@pytest.mark.parametrize(
    ("rel", "text", "caught"),
    [
        ("blizzard/hub/delivery/runner.py", "import subprocess", True),
        ("blizzard/hub/delivery/internal/runner.py", "from subprocess import run", True),
        ("blizzard/hub/delivery/runner.py", "import asyncio\n\nasyncio.create_subprocess_exec('true')", True),
        ("blizzard/hub/delivery/runner.py", "from asyncio import create_subprocess_shell", True),
        ("blizzard/hub/domain/model.py", "import subprocess", True),
        ("blizzard/hub/domain/model.py", "import asyncio\n\nasyncio.create_subprocess_shell('true')", True),
        ("blizzard/hub/domain/__init__.py", "from subprocess import PIPE", True),
        ("blizzard/hub/delivery/runner.py", "import asyncio\n\nasyncio.sleep(0)", False),
        ("blizzard/hub/elsewhere.py", "import subprocess", False),
    ],
)
def test_the_delivery_process_gate_judges_each_planted_module(
    tmp_path: Path, rel: str, text: str, caught: bool
) -> None:
    violations = _plant_delivery(tmp_path, {rel: f"{text}\n"})
    assert bool(violations) is caught, violations


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("actions/visit.py", "from blizzard.hub.domain.execution.apply import X"),
        ("graph/model.py", "from blizzard.hub.domain.actions.visit import Visit"),
    ],
)
def test_the_layer_walker_holds_the_actions_package_in_its_row(tmp_path: Path, rel: str, text: str) -> None:
    domain = tmp_path / "blizzard" / "hub" / "domain"
    for path, body in {
        "../../__init__.py": "",
        "../__init__.py": "",
        "__init__.py": "",
        "kernel/__init__.py": "",
        "graph/__init__.py": "",
        "graph/actions.py": "RESERVED_WAITS = frozenset()\n",
        "execution/__init__.py": "",
        "execution/apply.py": "X = 1\n",
        "actions/__init__.py": "",
        "actions/effects.py": "from blizzard.hub.domain.graph.actions import RESERVED_WAITS\n",
        rel: f"{text}\n",
    }.items():
        (domain / path).parent.mkdir(parents=True, exist_ok=True)
        (domain / path).write_text(body)
    layers = {
        "kernel": frozenset[str](),
        "graph": frozenset[str](),
        "execution": frozenset({"graph"}),
        "actions": _DOMAIN_PACKAGE_LAYERS["actions"] & {"graph"},
    }
    assert len(_layer_crossings(_hub_domain_tree(domain, layers))) == 1


#: Each runner node -> the nodes it may import besides itself (``bzh:domain-package-layers``); the rest are edges.
_RUNNER_PACKAGE_LAYERS: dict[str, frozenset[str]] = {
    "config_table": frozenset(),
    "node_steps": frozenset(),
    "process": frozenset(),
    "route_paths": frozenset(),
    "events": frozenset(),
    "environments": frozenset({"process"}),
    "harness": frozenset({"config_table", "environments", "node_steps", "process"}),
    "subscriptions": frozenset({"process"}),
    "auth": frozenset(),
    "transfer": frozenset({"environments"}),
    "leases": frozenset({"environments", "events", "harness", "node_steps"}),
    "hub": frozenset({"auth", "events", "harness", "leases", "node_steps"}),
    "transcripts": frozenset({"environments", "harness", "hub", "leases"}),
    "throttle": frozenset({"events", "harness", "hub", "leases", "subscriptions", "usage"}),
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
    "operator": frozenset({"hub", "leases", "lifecycle"}),
    "tracing": frozenset({"harness", "hub", "leases", "route_paths", "transcripts"}),
    "selftest": frozenset({"environments", "harness", "lifecycle", "node_steps", "process"}),
    "status": frozenset({"environments", "harness", "hub", "leases", "lifecycle", "throttle"}),
    "harness/claude_code": frozenset({"config_table", "harness", "node_steps", "process", "subscriptions"}),
    "harness/opencode": frozenset({"config_table", "harness", "node_steps", "process"}),
    "harness/wiring": frozenset({"config_table", "harness", "process", *_HARNESS_BINDING_NODES}),
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
            "transfer",
            "usage",
        }
    ),
    "loop": frozenset(
        {
            "process",
            "events",
            "environments",
            "harness",
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
_RUNNER_SUBNODES = frozenset({*_HARNESS_BINDING_NODES, "harness/wiring"})
_RUNNER_EDGE_PACKAGES = frozenset({"api", "cli", "store"})
#: Top-level runner modules that assemble or serve the graph (composition roots, config loader, runtime).
_RUNNER_EDGE_MODULES = frozenset(
    {
        "app",
        "composition",
        "config",
        "config_keys",
        "host_config",
        "host_file",
        "host_layout",
        "ingest",
        "listeners",
        "loop_wiring",
        "public_origins",
        "runtime",
    }
)


def _runner_tree(runner_dir: Path, layers: Mapping[str, frozenset[str]]) -> _LayeredTree:
    """The runner as a layered tree: it has no shared node, its edges are the ones named above, and the nodes
    of ``harness`` it holds beside itself are its binding packages and ``wiring``."""
    return _LayeredTree(
        runner_dir,
        ("blizzard", "runner"),
        layers,
        subnodes=_RUNNER_SUBNODES,
        exempt=_RUNNER_EDGE_PACKAGES | _RUNNER_EDGE_MODULES,
    )


def _runner_domain_core_files(runner_dir: Path, layers: Mapping[str, frozenset[str]]) -> list[Path]:
    """Every module of a runner node outside an ``internal/`` package — the runner's ``bzh:domain-core``: a
    concept package's public surface holds its models, ports, and the services carrying its rules, while the
    adapters binding a framework or driver sit in its ``internal/`` (``bzh:internal-visibility``)."""
    tree = _runner_tree(runner_dir, layers)
    return [
        path
        for path in _production_files(runner_dir)
        if _layer_module_node(tree, path) in layers and "internal" not in path.relative_to(runner_dir).parts[:-1]
    ]


_BINDING_FACTS_RULE = _REPO_ROOT / "contracts" / "ast-grep" / "rules" / "seam-answers-binding-facts.yml"


def _declaration_packages(harness_dir: Path) -> set[str]:
    """Every package directly under ``harness_dir`` holding its ``src/blizzard/runner/harness/<id>/declaration.py``."""
    return {path.parent.name for path in harness_dir.glob("*/declaration.py")}


def test_the_harness_ids_are_the_declaration_packages() -> None:
    """A harness id is its package's name: every catalog id names a ``harness/<id>/`` package holding its
    ``src/blizzard/runner/harness/<id>/declaration.py``, and no other ``harness/`` package holds one."""
    assert _declaration_packages(_RUNNER_DIR / "harness") == set(_HARNESS_IDS)


def test_the_declaration_pin_fails_closed_both_ways(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    for package in ("claude_code", "codex"):
        (harness / package).mkdir(parents=True)
        (harness / package / "declaration.py").write_text("")
    assert _declaration_packages(harness) == {"claude_code", "codex"}
    assert _declaration_packages(harness) != {"claude_code"}
    assert _declaration_packages(harness) != {"claude_code", "opencode", "codex"}


def test_the_binding_facts_rule_names_exactly_the_catalog_harnesses() -> None:
    """``bzh:seam-answers-binding-facts`` names each harness id in its two regexes and its binding-module
    ``ignores``; a YAML rule cannot read the catalog, so a binding added there fails here until the rule names it."""
    text = _BINDING_FACTS_RULE.read_text()
    alternations = re.findall(r"regex: \^\(([\w|]+)\)\$", text)
    assert len(alternations) == 2
    assert [set(alternation.split("|")) for alternation in alternations] == [set(_HARNESS_IDS)] * 2
    globs = set(re.findall(r"^  - src/blizzard/runner/harness/(\w+)/\*\*$", text, re.MULTILINE))
    assert globs == set(_HARNESS_IDS)


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
    violations = _layer_crossings(_runner_tree(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS))
    assert not violations, f"a runner package may import only its own layer's dependencies: {violations}"


def test_each_runner_name_has_one_import_path() -> None:
    tree = _runner_tree(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS)
    violations = _layer_second_spellings(tree, (_RUNNER_DIR,))
    assert not violations, f"import a runner name from the module that defines it: {violations}"


def test_every_runner_layer_edge_is_one_the_code_uses() -> None:
    unused = _unused_layer_edges(_runner_tree(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS))
    assert not unused, f"a runner layer row may allow only edges today's code walks; drop: {unused}"


def test_runner_package_layers_are_acyclic() -> None:
    assert _layer_cycle(_RUNNER_PACKAGE_LAYERS, kernel=None) == []
    assert all(dep in _RUNNER_PACKAGE_LAYERS for deps in _RUNNER_PACKAGE_LAYERS.values() for dep in deps)
    undeclared = _undeclared_layer_nodes(_runner_tree(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS))
    assert not undeclared, (
        f"every runner package outside api/, cli/ and store/, and every top-level module outside the edge "
        f"modules, is a declared node: {undeclared}"
    )


def test_runner_domain_core_selection_holds_the_lease_model() -> None:
    assert _RUNNER_DIR / "leases" / "model.py" in _runner_domain_core_files(_RUNNER_DIR, _RUNNER_PACKAGE_LAYERS)


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
    violations = _layer_crossings(_runner_tree(_plant_runner(tmp_path, {rel: f"{text}\n"}), _PLANTED_RUNNER_LAYERS))
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
    undeclared = _undeclared_layer_nodes(_runner_tree(_plant_runner(tmp_path, {rel: ""}), _PLANTED_RUNNER_LAYERS))
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
    assert _unused_layer_edges(_runner_tree(runner, _PLANTED_RUNNER_LAYERS)) == []
    widened = {**_PLANTED_RUNNER_LAYERS, "hub": frozenset({"leases", "config_table"})}
    assert _unused_layer_edges(_runner_tree(runner, widened)) == ["hub -> config_table"]


def test_runner_second_spelling_check_flags_an_import_through_an_importer(tmp_path: Path) -> None:
    runner = _plant_runner(
        tmp_path,
        {
            "hub/ports.py": "from blizzard.runner.leases.record import R\n",
            "leases/uses.py": "from blizzard.runner.hub.ports import R\nfrom blizzard.runner.hub.client import H\n",
        },
    )
    tree = _runner_tree(runner, _PLANTED_RUNNER_LAYERS)
    assert _layer_second_spellings(tree, (runner,)) == [
        "runner/leases/uses.py:1 imports R from blizzard.runner.hub.ports"
    ]


def test_hub_domain_unused_edge_check_catches_an_edge_no_import_walks(tmp_path: Path) -> None:
    domain = tmp_path / "blizzard" / "hub" / "domain"
    for rel, text in {
        "../../__init__.py": "",
        "../__init__.py": "",
        "__init__.py": "",
        "chunk/__init__.py": "",
        "chunk/model.py": "X = 1\n",
        "garden/__init__.py": "",
        "garden/run.py": "from blizzard.hub.domain.chunk.model import X\n",
        "kernel/__init__.py": "",
    }.items():
        (domain / rel).parent.mkdir(parents=True, exist_ok=True)
        (domain / rel).write_text(text)
    layers = {"kernel": frozenset[str](), "chunk": frozenset[str](), "garden": frozenset({"chunk"})}
    assert _unused_layer_edges(_hub_domain_tree(domain, layers)) == []
    widened = {**layers, "garden": frozenset({"chunk", "kernel"})}
    assert _unused_layer_edges(_hub_domain_tree(domain, widened)) == ["garden -> kernel"]


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
            "leases/internal/http_leases.py": "import httpx2\n",
            "hub/internal/http_hub.py": "import httpx2\n",
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
# refusal, raised wherever a boot check or the config edge refuses; `StoreScope` is a value object naming one tenant,
# built wherever a hub-wide job opens that tenant's stores or services.
_REPEATABLE_CONSTRUCTIONS = frozenset({"TranscriptCaps", "EventBroker", "ConfigError", "StoreScope"})


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


@pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ,
    reason="sweeps the composition roots, where mutmut's copy carries every generated mutant body, each repeating "
    "the constructions it mutates",
)
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


#: What the runner's composition root builds once, so the served app never builds a second, private copy.
_ROOT_BUILT_COLLABORATORS = frozenset(
    {
        "SpanRateLimiter",
        "ReceiverCounter",
        "ReceiverBounds",
        "RunnerIdentityHolder",
        "DisabledPlatformTracing",
        "DisabledReceivedTelemetryExport",
        "TelemetryReceiver",
    }
)


def test_the_runner_app_builds_no_collaborator_the_composition_root_builds() -> None:
    """bzh:dependency-injection: ``create_app`` takes the receiver, handle and identity the process
    graph built, and falls back to none of them."""
    built = {name for name, _ in _blizzard_constructions(_RUNNER_DIR / "app.py")}
    assert not built & _ROOT_BUILT_COLLABORATORS, f"runner/app.py builds {sorted(built & _ROOT_BUILT_COLLABORATORS)}"


def _is_attribute(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == name


def test_only_the_runner_wiring_reads_app_state_under_runner_api() -> None:
    """bzh:dependency-injection: a runner route resolves what it needs through ``RunnerWiring``, which
    refuses an unwired seam with a named 503, rather than reading ``app.state`` itself."""
    reads = [
        f"{path.relative_to(_REPO_ROOT)}:{node.lineno}"
        for path in sorted((_RUNNER_DIR / "api").glob("*.py"))
        if path.name != "wiring.py"
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path)))
        if isinstance(node, ast.Attribute)
        and node.attr == "state"
        and ((isinstance(node.value, ast.Name) and node.value.id == "app") or _is_attribute(node.value, "app"))
    ]
    assert not reads, f"read app.state outside RunnerWiring: {reads}"


def _loaded_after_importing(module: str) -> set[str]:
    code = f"import sys, json; import {module}; print(json.dumps(sorted(sys.modules)))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return set(json.loads(result.stdout))


def loaded_after_running(argv: list[str], env: dict[str, str] | None = None) -> set[str]:
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
    loaded = loaded_after_running(
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
        lambda: loaded_after_running(["runner", "heartbeat", "--help"]),
    ],
    ids=["hook-entry-point", "heartbeat-verb"],
)
def test_a_worker_hook_loads_no_harness_adapter_beyond_its_section(load: Callable[[], set[str]]) -> None:
    """The per-tool-call ``heartbeat`` and ``session-end`` hooks load no adapter's declaration graph, whatever
    they reach of ``src/blizzard/runner/harness/wiring.py`` (``bzh:pluggable-seams``)."""
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
    loaded = loaded_after_running(["hub", "chunk", "list", "--hub-url", "http://127.0.0.1:1"])
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
    traced = loaded_after_running(argv, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1"})
    untraced = loaded_after_running(argv, {"OTEL_EXPORTER_OTLP_ENDPOINT": ""})
    heavy = sorted(m for m in traced if m.split(".")[0] == "opentelemetry")
    assert not heavy, f"an operator trace loaded {len(heavy)} opentelemetry modules, first {heavy[:3]}"
    assert "blizzard.foundation.cli_spans" in traced
    assert "blizzard.foundation.cli_spans" not in untraced


def test_trace_ids_load_no_driver_or_hub_store() -> None:
    """The runner imports the id derivation, so it loads nothing but the standard library and the kernel."""
    loaded = _loaded_after_importing("blizzard.foundation.trace_ids")
    heavy = {m for m in loaded if m.split(".")[0] in ("sqlalchemy", "httpx2") or m.startswith("blizzard.hub")}
    assert not heavy, heavy


def test_trace_step_rules_load_no_http_driver_or_hub_store() -> None:
    loaded = _loaded_after_importing("blizzard.hub.domain.observability.tracing.steps")
    heavy = {m for m in loaded if m.split(".")[0] == "httpx2" or m.startswith("blizzard.hub.store")}
    assert not heavy, heavy


def test_trace_assembly_loads_no_http_driver_opentelemetry_or_hub_store() -> None:
    loaded = _loaded_after_importing("blizzard.hub.domain.observability.tracing.assembly")
    heavy = {m for m in loaded if m.split(".")[0] in ("httpx2", "opentelemetry") or m.startswith("blizzard.hub.store")}
    assert not heavy, heavy


def test_only_the_trace_bindings_import_opentelemetry() -> None:
    """No daemon installs a global provider or instrumentation: OpenTelemetry is named only by the
    shared OTLP binding, which builds finished spans and calls the exporter directly, and by
    platform tracing, which hands one per-process provider to each instrumentation explicitly."""
    homes = tuple(
        str(path.relative_to(_REPO_ROOT))
        for path in (
            _FOUNDATION_DIR / "trace_export" / "internal",
            _FOUNDATION_DIR / "platform_tracing",
            _RUNNER_DIR / "tracing" / "internal",
        )
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
        # `src/blizzard/foundation/trace_export/config.py` only declares the export's directory as a `Path` value; it never touches the filesystem.
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
        for path in _production_files(directory)
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
    {
        "IWriteSecretRepository",
        "IWriteWorkSourceRepository",
        "IWriteRepositoryRecordRepository",
        "IWriteForgeRepository",
    }
)
_CONFIGURED_WRITE_HOMES = frozenset(
    {
        _HUB_DIR / "domain" / "config" / "authoring.py",
        _HUB_DIR / "domain" / "config" / "forges.py",
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
        for path in _production_files(root)
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
        for path in _production_files(src_root):
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
        if not declared and (targets := self.aliases.get(name)):
            return all(
                isinstance(t, ast.Name) and id(t) not in seen and self._is_collaborator(t.id, path, seen | {id(t)})
                for t in targets
            )
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
    for path in _production_files(src_root):
        rel = path.relative_to(src_root)
        if _in_app_boundary(rel) or any(rel.is_relative_to(a) for a in _WIRE_ADAPTERS) or path in exempt:
            continue
        if any(m == "blizzard.wire" or m.startswith("blizzard.wire.") for m in _imported_modules(path)):
            violations.append(f"{rel.as_posix()} imports a wire/ model outside the app boundary")
    return violations


def _domain_model_collaborator_violations(src_root: Path, paths: Iterable[Path] | None = None) -> list[str]:
    """Every ``@domain_model`` in ``paths`` with a field — inherited, ``InitVar``, or
    ``init=False`` included — whose annotation mentions a collaborator at any depth, a driver
    handle spelled dotted (``httpx2.Client``) included."""
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


@pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ,
    reason="sweeps the source tree, where mutmut rewrites each dto method's body into a trampoline call that reads "
    "as behavior",
)
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

import httpx2
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
        "@dto\n@dataclass\nclass Wired:\n    x: int\n    client: httpx2.Client\n",
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
        "@dataclass\nclass Daemon:\n    verb: str\n    client: httpx2.Client\n",
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
        "client: httpx2.Client | None = None",
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


_CONFIGURED_RECORD_VERBS = {"create", "list", "get", "edit", "retire", "enable"}
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


#: The exemplar package (``bzh:exemplar-conformance``): checked by the same scans as ``src/``.
_EXEMPLAR_DIR = _REPO_ROOT / "exemplars" / "python" / "exemplar"


def test_the_exemplars_declare_their_data_roles() -> None:
    """Every exemplar data class meets the same role checks ``src/`` does (``bzh:data-roles``)."""
    checks = (
        _role_marker_violations,
        _boundary_violations,
        _adapter_model_violations,
        _domain_model_collaborator_violations,
        _collaborator_violations,
        _dto_method_violations,
    )
    violations = [violation for check in checks for violation in check(_EXEMPLAR_DIR)]
    violations += _wire_import_violations(_EXEMPLAR_DIR, exempt=frozenset())
    assert not violations, f"exemplar data roles: {violations}"


def test_the_exemplar_domain_core_imports_no_framework_or_driver() -> None:
    """An exemplar ``hub/domain/`` module imports nothing ``src/``'s domain core may not (``bzh:domain-core``)."""
    violations = [
        f"{path.relative_to(_EXEMPLAR_DIR)} imports {module}"
        for path in sorted((_EXEMPLAR_DIR / "hub" / "domain").rglob("*.py"))
        for module in sorted(_imported_modules(path))
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in _DOMAIN_CORE_FORBIDDEN)
    ]
    assert not violations, f"exemplar domain core: {violations}"


def test_the_exemplars_cross_no_internal_package() -> None:
    """An exemplar imports another package's ``internal/`` only from a composition root (``bzh:internal-visibility``)."""
    roots = frozenset(_EXEMPLAR_DIR.rglob("composition.py"))
    violations = _internal_crossings(_EXEMPLAR_DIR, exempt=roots)
    assert not violations, f"exemplar internal crossings: {violations}"


def test_the_exemplars_keep_every_block_within_its_cap() -> None:
    """Every exemplar comment and docstring fits its ``bzh:prose-budget`` cap outright, with no baseline."""
    spec = importlib.util.spec_from_file_location("prose_caps", _REPO_ROOT / "scripts" / "prose_caps.py")
    assert spec is not None and spec.loader is not None
    prose_caps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prose_caps)
    trees = (_EXEMPLAR_DIR, _EXEMPLAR_DIR.parent / "tests")
    violations = [v for tree in trees for path in sorted(tree.rglob("*.py")) for v in prose_caps.over_cap_blocks(path)]
    assert not violations, f"exemplar prose over cap: {violations}"
