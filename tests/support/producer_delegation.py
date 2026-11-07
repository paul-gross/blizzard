"""Conservative source traversal for producer delegation candidates."""

import ast
import hashlib
import inspect
import re
import textwrap
from pathlib import Path
from types import FunctionType, MethodType, ModuleType
from typing import Any

from fastapi import HTTPException
from starlette.responses import Response

from blizzard.hub import composition
from blizzard.hub.api.auth import HostAuth, RunnerAuth
from tests.support.runner_refusal_discovery import producers
from tests.support.stable_ast import stable_dump, unparse


def _inert_expression(node: ast.AST) -> bool:
    return isinstance(node, (ast.Name, ast.Constant))


def _inert_statement(node: ast.stmt) -> bool:
    return isinstance(node, ast.Pass) or (
        isinstance(node, ast.Assign)
        and all(isinstance(target, ast.Name) for target in node.targets)
        and _inert_expression(node.value)
    )


def _wired_candidates(module: ModuleType) -> dict[tuple[str, str], set[FunctionType]]:
    """Collect possible HubServices implementations from constructor source, not runtime instances."""
    tree = ast.parse(inspect.getsource(module))
    result: dict[tuple[str, str], set[FunctionType]] = {}
    for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        assignments: dict[str, list[ast.AST]] = {}
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments.setdefault(target.id, []).append(node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                assignments.setdefault(node.target.id, []).append(node.value)
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Name) or call.func.id != "HubServices":
                continue
            for keyword in call.keywords:
                if keyword.arg is None:
                    continue
                values = (
                    assignments.get(keyword.value.id, []) if isinstance(keyword.value, ast.Name) else [keyword.value]
                )
                for value in values:
                    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
                        continue
                    cls = vars(module).get(value.func.id)
                    if not isinstance(cls, type):
                        continue
                    for base in cls.__mro__:
                        for method, candidate in vars(base).items():
                            if isinstance(candidate, FunctionType):
                                result.setdefault((keyword.arg, method), set()).add(candidate)
    return result


def delegation_candidates(function: FunctionType, instance: object = None) -> tuple[set[str], set[str]]:
    found: set[str] = set()
    unresolved: set[str] = set()
    pending = [(function, instance)]
    wired = _wired_candidates(composition)
    seen: set[tuple[FunctionType, int]] = set()
    while pending:
        current, receiver = pending.pop()
        key = (current, id(receiver))
        if key in seen:
            continue
        seen.add(key)
        name = f"{current.__module__}.{current.__qualname__}"
        if not current.__module__.startswith("blizzard.hub."):
            unresolved.add(f"external callable: {name}")
            continue
        try:
            lines, start = inspect.getsourcelines(current)
            source = textwrap.dedent("".join(lines))
            tree = ast.parse(source)
        except (OSError, TypeError, SyntaxError):
            unresolved.add(f"source unavailable: {name}")
            continue
        aliases = {
            key: value.__name__
            for key, value in current.__globals__.items()
            if inspect.isclass(value) and issubclass(value, (Response, HTTPException))
        }
        found.update(f"{name}:{start + line - 1}: {call}" for _, line, call in producers(source, aliases))
        # A local binding can shadow a global at any point in the function.
        bound = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
        parameters = {
            arg.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.arguments)
            for arg in (*node.posonlyargs, *node.args, *node.kwonlyargs)
        }
        bound.update(parameters)
        for local in ast.walk(tree):
            if isinstance(local, (ast.Import, ast.ImportFrom)):
                bound.update(alias.asname or alias.name.split(".")[0] for alias in local.names)
            elif (
                isinstance(local, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and local is not tree.body[0]
            ) or (isinstance(local, ast.ExceptHandler) and local.name is not None):
                bound.update([local.name] if local.name is not None else [])
        assignments = [
            node for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
        ]
        body = tree.body[0].body if isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)) else []
        definition = tree.body[0]
        positional = (
            (*definition.args.posonlyargs, *definition.args.args)
            if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
            else ()
        )
        receiver_name = positional[0].arg if receiver is not None and positional else None
        nested_calls = {
            id(call)
            for statement in body
            for nested in ast.walk(statement)
            if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef))
            for call in ast.walk(nested)
            if isinstance(call, ast.Call)
        }
        aliases_local: dict[str, tuple[int, FunctionType]] = {}
        for statement in body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            target, value = statement.targets[0], statement.value
            if (
                not isinstance(target, ast.Name)
                or target.id in parameters
                or not isinstance(value, ast.Name)
                or value.id in bound
            ):
                continue
            candidate = current.__globals__.get(value.id)
            if isinstance(candidate, FunctionType) and sum(node.id == target.id for node in assignments) == 1:
                aliases_local[target.id] = (statement.lineno, candidate)
        # Decorator calls run at definition time; their arguments may wire
        # runtime dependencies, so traversal retains those argument calls.
        registration_calls: set[int] = (
            {id(decorator) for decorator in definition.decorator_list if isinstance(decorator, ast.Call)}
            if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
            else set()
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if id(node) in registration_calls:
                continue
            expr = node.func
            target: object = None
            # A factory may return another type or an overridden method. Inspect the
            # declared auth method as a candidate, retaining the unresolved call.
            if (
                isinstance(expr, ast.Attribute)
                and isinstance(expr.value, ast.Call)
                and isinstance(expr.value.func, ast.Attribute)
                and expr.value.func.attr == "of"
                and isinstance(expr.value.func.value, ast.Name)
                and expr.value.func.value.id not in bound
            ):
                cls = current.__globals__.get(expr.value.func.value.id)
                allowed = (cls is RunnerAuth and expr.attr in {"demand", "refuse_runner"}) or (
                    cls is HostAuth and expr.attr == "demand"
                )
                if allowed:
                    candidate = inspect.getattr_static(cls, expr.attr, None)
                    if isinstance(candidate, FunctionType):
                        pending.append((candidate, None))
            if (
                isinstance(expr, ast.Attribute)
                and isinstance(expr.value, ast.Attribute)
                and isinstance(expr.value.value, ast.Name)
                and expr.value.value.id == "services"
                and expr.value.attr == "memberships"
                and expr.attr == "memberships_for"
            ):
                pending.extend((candidate, None) for candidate in wired.get((expr.value.attr, expr.attr), ()))
            if isinstance(expr, ast.Name) and expr.id not in bound:
                target = current.__globals__.get(expr.id)
            elif (
                isinstance(expr, ast.Name)
                and expr.id in aliases_local
                and node.lineno > aliases_local[expr.id][0]
                and id(node) not in nested_calls
            ):
                target = aliases_local[expr.id][1]
            elif isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id not in bound:
                base = current.__globals__.get(expr.value.id)
                if isinstance(base, ModuleType):
                    target = vars(base).get(expr.attr)
                elif (
                    isinstance(base, type)
                    and type(base) is type
                    and len(body) == 1
                    and isinstance(body[0], (ast.Return, ast.Expr))
                    and (
                        body[0].value is node
                        or (
                            isinstance(body[0].value, ast.Call)
                            and isinstance(body[0].value.func, ast.Attribute)
                            and body[0].value.func.value is node
                        )
                    )
                    and id(node) not in nested_calls
                    and all(_inert_expression(arg) for arg in node.args)
                    and all(keyword.arg is not None and _inert_expression(keyword.value) for keyword in node.keywords)
                ):
                    descriptor = inspect.getattr_static(base, expr.attr, None)
                    if type(descriptor) in (classmethod, staticmethod) and isinstance(
                        descriptor.__func__, FunctionType
                    ):
                        target = descriptor.__func__
            elif (
                isinstance(expr, ast.Attribute)
                and isinstance(expr.value, ast.Name)
                and expr.value.id == receiver_name
                and id(node) not in nested_calls
                and isinstance(body[-1], (ast.Expr, ast.Return))
                and body[-1].value is node
                and all(_inert_statement(statement) for statement in body[:-1])
                and all(_inert_expression(arg) for arg in node.args)
                and all(keyword.arg is not None and _inert_expression(keyword.value) for keyword in node.keywords)
                and not any(n.id == receiver_name for n in assignments)
                and not any(
                    isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del)) for n in ast.walk(tree)
                )
                and type(receiver).__getattribute__ is object.__getattribute__
                and not isinstance(receiver, type)
            ):
                candidate = inspect.getattr_static(receiver, expr.attr, None)
                instance_dict: dict[str, object] = (
                    object.__getattribute__(receiver, "__dict__")
                    if hasattr(type(receiver), "__dict__")
                    and any("__dict__" in vars(cls) for cls in type(receiver).__mro__)
                    else {}
                )
                if (
                    isinstance(candidate, FunctionType)
                    and expr.attr not in instance_dict
                    and inspect.getattr_static(type(receiver), expr.attr, None) is candidate
                ):
                    target = candidate
                    pending.append((candidate, receiver))
                    continue
            if isinstance(target, FunctionType):
                pending.append((target, None))
            elif not (inspect.isclass(target) and issubclass(target, (Response, HTTPException))):
                unresolved.add(f"{name}:{start + node.lineno - 1}: unresolved call {unparse(expr)}")
    return found, unresolved


def operation_evidence(route: Any) -> tuple[set[str], set[str]]:
    pending = [route.dependant]
    functions: list[tuple[FunctionType, object]] = []
    unknown: set[str] = set()
    while pending:
        dependency = pending.pop()
        call = dependency.call
        if isinstance(call, FunctionType):
            functions.append((call, None))
        elif isinstance(call, MethodType) and isinstance(call.__func__, FunctionType):
            functions.append((call.__func__, call.__self__))
        elif call is not None:
            unknown.add(f"unsupported dependency: {call!r}")
        pending.extend(dependency.dependencies)
    if isinstance(route.endpoint, FunctionType):
        functions.append((route.endpoint, None))
    else:
        unknown.add(f"unsupported endpoint: {route.endpoint!r}")
    found: set[str] = set()
    for function, instance in functions:
        candidates, unresolved = delegation_candidates(function, instance)
        found.update(candidates)
        unknown.update(unresolved)
    return found, unknown


def evidence_digest(values: set[str]) -> str:
    normalized = sorted(re.sub(r":\d+: ", ": ", value) for value in values)
    return hashlib.sha256("\n".join(normalized).encode()).hexdigest()


def source_fingerprint(function: FunctionType) -> str:
    """Fingerprint implementation syntax, independent of formatting and line numbers."""
    source = textwrap.dedent(inspect.getsource(function))
    return hashlib.sha256(stable_dump(ast.parse(source)).encode()).hexdigest()


def application_source_digest(root: Path) -> str:
    """Cover unresolved hub collaborator implementations and their wiring conservatively."""
    sources = root / "src" / "blizzard" / "hub"
    entries = {
        str(path.relative_to(sources)): stable_dump(ast.parse(path.read_text())) for path in sources.rglob("*.py")
    }
    return hashlib.sha256(repr(sorted(entries.items())).encode()).hexdigest()


def producer_inventory_failures(
    operation: tuple[str, str], candidates: set[str], inventory: dict[tuple[str, str], set[tuple[str, str]]]
) -> list[str]:
    """Compare producer owner and expression without relying on source line numbers."""
    if operation not in inventory:
        return [f"{operation}: missing producer inventory"]
    observed = {tuple(re.split(r":\d+: ", candidate, maxsplit=1)) for candidate in candidates}
    return [
        f"{operation}: missing producer: {owner}: {expression}"
        for owner, expression in sorted(observed - inventory[operation])
    ]
