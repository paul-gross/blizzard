"""Conservative handler inventory and optional transitive-dispatch diagnostic.

Run ``python -m tests.support.handler_applicability`` to print per-operation
evidence and unresolved sites. This diagnostic is not a reviewed baseline.
"""

import ast
import builtins
import hashlib
import inspect
import sys
import textwrap
import types
from typing import Any, cast


def transitive_dispatch(route: Any, app: Any) -> tuple[dict[str, set[str]], set[str]]:
    """Find candidate dispatches and expose calls or raises that cannot be resolved.

    A may-reach analysis: caught exceptions are not subtracted. An empty result
    is meaningful only alongside an empty unresolved set.
    """
    pending = [route.dependant]
    functions = [route.endpoint]
    while pending:
        dependency = pending.pop()
        functions.append(dependency.call)
        pending.extend(dependency.dependencies)
    visited: set[int] = set()
    evidence: dict[str, set[str]] = {}
    unresolved: set[str] = set()

    def visit(function: Any) -> None:
        if not inspect.isfunction(function):
            unresolved.add(f"unresolved callable: {function!r}")
            return
        if id(function) in visited:
            return
        visited.add(id(function))
        name = f"{function.__module__}.{function.__qualname__}"
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        except (OSError, TypeError, SyntaxError):
            unresolved.add(f"{name}: source unavailable")
            return
        definition = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
        if definition is None:
            unresolved.add(f"{name}: definition unavailable")
            return
        shadowed = {n.arg for n in ast.walk(definition) if isinstance(n, ast.arg)}
        shadowed.update(
            n.id for n in ast.walk(definition) if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))
        )
        direct_imports = {id(n) for n in definition.body if isinstance(n, ast.Import)}
        for node in ast.walk(definition):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in direct_imports:
                shadowed.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        local_modules: dict[str, tuple[int, types.ModuleType]] = {}
        for statement in definition.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    binding = alias.asname or alias.name.split(".")[0]
                    module = sys.modules.get(alias.name if alias.asname else binding)
                    if module is not None and binding not in shadowed and binding not in local_modules:
                        local_modules[binding] = (statement.lineno, module)
                    else:
                        shadowed.add(binding)

        def resolve(expr: ast.expr) -> Any:
            if isinstance(expr, ast.Name) and expr.id not in shadowed:
                if expr.id in local_modules:
                    line, module = local_modules[expr.id]
                    return module if expr.lineno > line else None
                return function.__globals__.get(expr.id, getattr(builtins, expr.id, None))
            if isinstance(expr, ast.Attribute):
                owner = resolve(expr.value)
                if isinstance(owner, types.ModuleType):
                    return vars(owner).get(expr.attr)
                if inspect.isclass(owner) and type(owner) is type:
                    descriptor: Any = inspect.getattr_static(owner, expr.attr, None)
                    if isinstance(descriptor, (staticmethod, classmethod)):
                        return cast(Any, descriptor).__func__
            return None

        nodes: list[ast.AST] = list(definition.body)
        while nodes:
            node = nodes.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            nodes.extend(ast.iter_child_nodes(node))
            if isinstance(node, ast.Raise):
                expr = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                exception = resolve(expr) if expr is not None else None
                if not inspect.isclass(exception) or not issubclass(exception, BaseException):
                    unresolved.add(f"{name}:{node.lineno}: unresolved raise")
                    continue
                for cls in exception.__mro__:
                    handler = app.exception_handlers.get(cls)
                    if handler is not None:
                        key = f"{handler.__module__}.{handler.__qualname__}"
                        evidence.setdefault(key, set()).add(
                            f"{name} raises {exception.__module__}.{exception.__qualname__}"
                        )
                        break
            elif isinstance(node, ast.Call):
                target = resolve(node.func)
                if inspect.isclass(target) and issubclass(target, BaseException):
                    continue
                if (
                    any(target is safe for safe in (builtins.id, builtins.callable, builtins.type))
                    and len(node.args) == 1
                    and not node.keywords
                    and not isinstance(node.args[0], ast.Starred)
                ):
                    continue
                if inspect.isfunction(target) and (
                    target.__module__.startswith("blizzard.") or target.__module__ == function.__module__
                ):
                    visit(target)
                else:
                    unresolved.add(f"{name}:{node.lineno}: unresolved call {ast.unparse(node)}")

    for function in functions:
        visit(function)
    return evidence, unresolved


def dispatch_review_failures(
    actual: dict[tuple[str, str], tuple[dict[str, set[str]], set[str]]],
    reviewed: dict[tuple[str, str], tuple[dict[str, set[str]], set[str]]],
) -> list[str]:
    """Compare evidence and unresolved sites separately, without accepting new gaps."""
    failures = [f"{operation}: stale dispatch review" for operation in sorted(reviewed.keys() - actual.keys())]
    for operation, (evidence, unresolved) in sorted(actual.items()):
        if operation not in reviewed:
            failures.append(f"{operation}: missing transitive dispatch review; unresolved: {sorted(unresolved)}")
            continue
        expected_evidence, expected_unresolved = reviewed[operation]
        actual_pairs = {(handler, item) for handler, items in evidence.items() for item in items}
        expected_pairs = {(handler, item) for handler, items in expected_evidence.items() for item in items}
        for label, changes in (
            ("new dispatch", actual_pairs - expected_pairs),
            ("stale dispatch", expected_pairs - actual_pairs),
            ("unreviewed unresolved", unresolved - expected_unresolved),
            ("stale unresolved", expected_unresolved - unresolved),
        ):
            failures.extend(f"{operation}: {label}: {item}" for item in sorted(changes))
    return failures


def dispatch_diagnostic(
    actual: dict[tuple[str, str], tuple[dict[str, set[str]], set[str]]],
) -> list[str]:
    """Render positive evidence and every unresolved site, without claiming review."""
    lines: list[str] = []
    for operation, (evidence, unresolved) in sorted(actual.items()):
        lines.append(f"{operation[0]} {operation[1]}")
        for handler, items in sorted(evidence.items()):
            lines.extend(f"  dispatch {handler}: {item}" for item in sorted(items))
        lines.extend(f"  unresolved: {item}" for item in sorted(unresolved))
        if not evidence and not unresolved:
            lines.append("  no explicit dispatch or unresolved sites found")
    return lines


def main() -> int:
    """Print the runner-operation diagnostic; unresolved analysis exits nonzero."""
    from blizzard.cli.config_inputs import packaged_flag_registry
    from blizzard.hub.app import create_app_for_export
    from tests.support.repo_files import repo_root
    from tests.support.runner_refusal_discovery import match_operations, route_inventory, runner_boundary

    app = create_app_for_export(packaged_flag_registry())
    routes, route_unknown = route_inventory(app)
    reaches, unknown = runner_boundary(repo_root())
    matched, missing = match_operations(reaches, set(routes))
    operations = {operation for group in matched.values() for operation in group}
    actual = {operation: transitive_dispatch(routes[operation], app) for operation in operations}
    for line in dispatch_diagnostic(actual):
        print(line)
    for issue in sorted(route_unknown + list(unknown)):
        print(f"unresolved discovery: {issue}")
    for method, path in sorted(missing):
        print(f"unmatched operation: {method} {path}")
    return int(bool(route_unknown or unknown or missing or any(gaps for _, gaps in actual.values())))


def handler_inventory(app: Any) -> set[str]:
    """Treat every registration as potentially applicable to every operation."""
    result: set[str] = set()
    for exception, handler in app.exception_handlers.items():
        key = f"{exception.__module__}.{exception.__qualname__}" if inspect.isclass(exception) else repr(exception)
        name = f"{handler.__module__}.{handler.__qualname__}"
        try:
            source = inspect.getsource(handler)
        except (OSError, TypeError):
            source = "<source unavailable>"
        digest = hashlib.sha256(source.encode()).hexdigest()
        result.add(f"{key} -> {name} {digest}")
        dependencies = _dependencies(handler)
        if dependencies:
            content = repr(sorted(dependencies.items()))
            digest = hashlib.sha256(content.encode()).hexdigest()
            result.add(f"{key} -> {name} transitive {digest}")
    return result


def _dependencies(handler: Any) -> dict[str, str]:
    found: dict[str, str] = {}
    visited: set[int] = set()

    def visit(function: Any) -> None:
        if not inspect.isfunction(function) or id(function) in visited:
            return
        visited.add(id(function))
        try:
            source = inspect.getsource(function)
        except (OSError, TypeError):
            return
        tree = ast.parse(textwrap.dedent(source))
        for node in ast.walk(tree):
            target = None
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                target = function.__globals__.get(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                owner = function.__globals__.get(node.value.id)
                if isinstance(owner, types.ModuleType):
                    target = getattr(owner, node.attr, None)
            if not inspect.isfunction(target) or not target.__module__.startswith("blizzard."):
                continue
            try:
                dependency_source = inspect.getsource(target)
            except (OSError, TypeError):
                continue
            name = f"{target.__module__}.{target.__qualname__}"
            found[name] = dependency_source
            visit(target)

    visit(handler)
    return found


def applicability_failures(
    operations: set[tuple[str, str]], handlers: set[str], reviewed: dict[tuple[str, str], set[str]]
) -> list[str]:
    failures = [f"{operation}: stale operation" for operation in sorted(set(reviewed) - operations)]
    for operation in sorted(operations):
        if operation not in reviewed:
            failures.append(f"{operation}: missing handler applicability review")
            continue
        for handler in sorted(handlers - reviewed[operation]):
            failures.append(f"{operation}: unreviewed potentially applicable handler: {handler}")
        for handler in sorted(reviewed[operation] - handlers):
            failures.append(f"{operation}: stale handler review: {handler}")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
