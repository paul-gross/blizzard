"""Static discovery behind the runner-refusal guards: runner reaches, hub routes, and refusal producers.

Unknown expressions are returned, never silently skipped.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import inspect
import itertools
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.responses import Response

from tests.support.stable_ast import stable_dump, unparse


@dataclass(frozen=True, order=True)
class Reach:
    method: str
    path: str
    line: int


def _string(node: ast.AST, names: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                parts.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                resolved = _string(part.value, names)
                if (
                    resolved is None
                    and isinstance(part.value, ast.Call)
                    and isinstance(part.value.func, ast.Name)
                    and part.value.func.id == "quote"
                    and len(part.value.args) == 1
                    and isinstance(part.value.args[0], ast.Name | ast.Attribute)
                ):
                    resolved = "{" + unparse(part.value.args[0]) + "}"
                if resolved is None and not isinstance(part.value, ast.Name | ast.Attribute):
                    return None
                parts.append(resolved if resolved is not None else "{" + unparse(part.value) + "}")
            else:
                return None
        return "".join(parts)
    return None


def discover(source: str, root: Path | None = None) -> tuple[set[Reach], list[str]]:
    """Find direct client and wrapper calls; report dynamic arguments with locations.

    Assignments are resolved per function, not across branches or across calls.
    Plumbing that forwards its parameters is also reported as unresolved.
    """
    tree = ast.parse(source)
    classified = classify_get_receivers(source)
    classified.update(classify_jwks_data(source))
    if root is not None:
        classified.update(classify_imported_mappings(source, root))
        classified.update(classify_runner_wiring(source, root))
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = _string(node.value, constants)
            if value is not None:
                constants[node.targets[0].id] = value
    reaches: set[Reach] = set()
    unresolved: list[str] = []
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    owners = {
        id(method): cls
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef)
        for method in cls.body
        if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for function in functions:
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == function.name
        ]
        choices: dict[str, set[str]] = {}
        for index, parameter in enumerate(function.args.args):
            values = [_string(call.args[index], constants) if index < len(call.args) else None for call in calls]
            if values and all(value is not None for value in values):
                choices[parameter.arg] = {value for value in values if value is not None}
        # Resolve methods forwarding paths to their transport from every local
        # self.method caller. Unknown call arguments remain unresolved below.
        method_calls = [
            (caller, call)
            for caller in functions
            if id(caller) in owners and owners[id(caller)] is owners.get(id(function))
            for statement in caller.body
            for call in ast.walk(statement)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self"
            and call.func.attr == function.name
        ]
        bindings: list[dict[str, str]] = []
        for caller, call in method_calls:
            local = dict(constants)
            for statement in caller.body:
                if statement.lineno >= call.lineno:
                    break
                if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                    target = statement.targets[0]
                    if isinstance(target, ast.Name):
                        value = _string(statement.value, local)
                        if value is not None:
                            local[target.id] = value
            binding: dict[str, str] = {}
            for index, parameter in enumerate(function.args.args[1:]):
                argument = (
                    call.args[index]
                    if index < len(call.args)
                    else next((keyword.value for keyword in call.keywords if keyword.arg == parameter.arg), None)
                )
                value = _string(argument, local) if argument is not None else None
                if value is not None:
                    binding[parameter.arg] = value
            bindings.append(binding)
        combinations = itertools.product(*choices.values())
        for combination in combinations:
            for binding in bindings or [{}]:
                names = constants | dict(zip(choices, combination, strict=True)) | binding
                found, unknown = _function_reaches(function, names, classified)
                reaches.update(found)
                unresolved.extend(unknown)
    return reaches, sorted(set(unresolved))


def classify_jwks_data(source: str) -> dict[tuple[int, int], str]:
    """Recognize JSON data and the explicitly typed key cache, not arbitrary receivers."""
    tree = ast.parse(source)
    if not any(
        isinstance(n, ast.Import) and any(a.name == "httpx2" and a.asname is None for a in n.names) for n in tree.body
    ):
        return {}
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "JwksCache"), None)
    if cls is None:
        return {}
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    init, refresh, key_for = (methods.get(n) for n in ("__init__", "_refresh", "key_for"))
    if init is None or refresh is None or key_for is None:
        return {}
    result: dict[tuple[int, int], str] = {}

    def calls(function: ast.FunctionDef, expression: str) -> list[ast.Call]:
        return [n for n in ast.walk(function) if isinstance(n, ast.Call) and unparse(n) == expression]

    def stores(function: ast.FunctionDef, name: str) -> int:
        return sum(
            isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store | ast.Del)
            for n in ast.walk(function)
        )

    http_arg = next((a for a in init.args.args if a.arg == "http_client"), None)
    http_writes = [
        n
        for n in ast.walk(cls)
        if isinstance(n, ast.Assign | ast.AnnAssign | ast.AugAssign)
        and any(unparse(t) == "self._http" for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))
    ]
    http_typed = (
        http_arg is not None
        and http_arg.annotation is not None
        and unparse(http_arg.annotation) == "httpx2.Client"
        and len(http_writes) == 1
        and isinstance(http_writes[0], ast.Assign)
        and unparse(http_writes[0].value) == "http_client"
    )
    response = [
        n
        for n in ast.walk(refresh)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and unparse(n.targets[0]) == "resp"
        and unparse(n.value) == "self._http.get(self._url)"
    ]
    loops = [
        n
        for n in ast.walk(refresh)
        if isinstance(n, ast.For) and unparse(n.target) == "jwk" and unparse(n.iter) == "resp.json().get('keys', [])"
    ]
    if (
        http_typed
        and len(response) == 1
        and stores(refresh, "resp") == 1
        and len(loops) == 1
        and stores(refresh, "jwk") == 1
    ):
        for expression in ("resp.json().get('keys', [])", "jwk.get('kid')"):
            for call in calls(refresh, expression):
                if expression.startswith("resp") or call in list(ast.walk(loops[0])):
                    result[(call.lineno, call.col_offset)] = "httpx JSON data"
    cache_writes = [
        n
        for n in ast.walk(cls)
        if isinstance(n, ast.Assign | ast.AnnAssign | ast.AugAssign)
        and any(unparse(t) == "self._keys" for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))
    ]
    typed = [
        n
        for n in cache_writes
        if isinstance(n, ast.AnnAssign)
        and unparse(n.annotation) == "dict[str, RSAPublicKey]"
        and n.value is not None
        and unparse(n.value) == "{}"
    ]
    refreshed = [n for n in cache_writes if isinstance(n, ast.Assign) and unparse(n.value) == "keys"]
    key_decls = [
        n
        for n in ast.walk(refresh)
        if isinstance(n, ast.AnnAssign)
        and unparse(n.target) == "keys"
        and unparse(n.annotation) == "dict[str, RSAPublicKey]"
        and n.value is not None
        and unparse(n.value) == "{}"
    ]
    if len(cache_writes) == 2 and len(typed) == len(refreshed) == len(key_decls) == 1 and stores(refresh, "keys") == 1:
        for call in calls(key_for, "self._keys.get(kid)"):
            result[(call.lineno, call.col_offset)] = "typed key cache"
    return result


def classify_runner_wiring(source: str, root: Path) -> dict[tuple[int, int], str]:
    """Recognize local wired services only while their declared seam types agree."""
    tree = ast.parse(source)
    if not any(
        isinstance(n, ast.ImportFrom)
        and n.module == "blizzard.runner.api.wiring"
        and any(a.name == "RunnerWiring" and a.asname is None for a in n.names)
        for n in tree.body
    ):
        return {}
    wiring_path = root / "src/blizzard/runner/api/wiring.py"
    if not wiring_path.is_file():
        return {}
    wiring = ast.parse(wiring_path.read_text())
    cls = next((n for n in wiring.body if isinstance(n, ast.ClassDef) and n.name == "RunnerWiring"), None)
    if cls is None:
        return {}
    expected = {
        "selftests": ("SelfTestService", "get"),
        "stepper": ("RunnerStepper", "request"),
        "host_stepper": ("HostPassStepper", "request"),
    }
    proven: dict[str, str] = {}
    for accessor, (type_name, method) in expected.items():
        definition = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == accessor), None)
        if definition is None or not isinstance(definition.returns, ast.Name) or definition.returns.id != type_name:
            continue
        imported = next(
            (
                n
                for n in ast.walk(wiring)
                if isinstance(n, ast.ImportFrom)
                and n.module
                and any(a.name == type_name and a.asname is None for a in n.names)
            ),
            None,
        )
        if imported is None or imported.module is None:
            continue
        path = root / "src" / (imported.module.replace(".", "/") + ".py")
        if not path.is_file():
            continue
        module = ast.parse(path.read_text())
        target = next((n for n in module.body if isinstance(n, ast.ClassDef) and n.name == type_name), None)
        if target and any(isinstance(n, ast.FunctionDef) and n.name == method for n in target.body):
            proven[accessor] = method
    result: dict[tuple[int, int], str] = {}
    for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
        writes: dict[str, int] = {}
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
                writes[node.id] = writes.get(node.id, 0) + 1
        local: dict[str, str] = {}
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and writes.get(node.targets[0].id) == 1
            ):
                local[node.targets[0].id] = unparse(node.value)
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = unparse(node.func.value)
            receiver = local.get(receiver, receiver)
            for accessor, method in proven.items():
                if (
                    node.func.attr == method
                    and receiver == f"RunnerWiring.of(request).{accessor}()"
                    and not writes.get("request")
                    and not writes.get("RunnerWiring")
                ):
                    result[(node.lineno, node.col_offset)] = f"RunnerWiring.{accessor} typed seam"
    return result


def classify_imported_mappings(source: str, root: Path) -> dict[tuple[int, int], str]:
    """Follow imported function and typed-port return annotations to mapping uses.

    Only direct imports and a single local assignment are followed. Unsupported
    expressions remain unknown rather than being inferred from the method name.
    """
    tree = ast.parse(source)
    imports: dict[str, ast.AST] = {}
    class_modules: dict[int, ast.Module] = {}
    url_mappings = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "urllib.parse"
        for alias in node.names
        if alias.name == "parse_qs"
    }
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module or not node.module.startswith("blizzard."):
            continue
        path = root / "src" / (node.module.replace(".", "/") + ".py")
        if not path.is_file():
            continue
        imported = ast.parse(path.read_text())
        for alias in node.names:
            definition = next(
                (
                    item
                    for item in imported.body
                    if isinstance(item, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
                    and item.name == alias.name
                ),
                None,
            )
            if definition is not None:
                imports[alias.asname or alias.name] = definition
                if isinstance(definition, ast.ClassDef):
                    class_modules[id(definition)] = imported

    def inherited_method(cls: ast.ClassDef, name: str, seen: set[str]) -> ast.AST | None:
        if cls.name in seen:
            return None
        seen.add(cls.name)
        own = next(
            (
                item
                for item in cls.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == name
            ),
            None,
        )
        if own is not None:
            return own
        module = class_modules.get(id(cls))
        if module is None:
            return None
        for base in cls.bases:
            if isinstance(base, ast.Name):
                parent = next(
                    (item for item in module.body if isinstance(item, ast.ClassDef) and item.name == base.id), None
                )
                if parent is not None:
                    class_modules[id(parent)] = module
                    target = inherited_method(parent, name, seen)
                    if target is not None:
                        return target
        return None

    def dict_return(node: ast.AST | None) -> bool:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return (dict_return(node.left) and isinstance(node.right, ast.Constant) and node.right.value is None) or (
                dict_return(node.right) and isinstance(node.left, ast.Constant) and node.left.value is None
            )
        return isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "dict"

    result: dict[tuple[int, int], str] = {}
    for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
        args = {arg.arg: arg.annotation for arg in function.args.args + function.args.kwonlyargs}
        writes: dict[str, list[ast.Name]] = {}
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
                writes.setdefault(node.id, []).append(node)
        mappings = {name for name, annotation in args.items() if dict_return(annotation) and name not in writes}
        # A declared local protocol method is not itself an HTTP transport.
        # Resolve the imported class and method, rather than guessing from `.get`.
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = node.func.value
            if not isinstance(receiver, ast.Name) or receiver.id in writes:
                continue
            annotation = args.get(receiver.id)
            if not isinstance(annotation, ast.Name):
                continue
            annotation_name = annotation.id
            cls = imports.get(annotation_name)
            if (
                not isinstance(cls, ast.ClassDef)
                or not any(isinstance(base, ast.Name) and base.id == "Protocol" for base in cls.bases)
                or node.func.attr != "get"
                or cls.name != "IReadHarnessHealth"
                or not any(
                    isinstance(item, ast.ImportFrom)
                    and item.module == "blizzard.runner.harness.health_cache"
                    and any(
                        alias.name == cls.name and (alias.asname or alias.name) == annotation_name
                        for alias in item.names
                    )
                    for item in tree.body
                )
            ):
                continue
            method = inherited_method(cls, node.func.attr, set())
            module = class_modules.get(id(cls))
            if method is not None and module is not None:
                result[(node.lineno, node.col_offset)] = f"typed local port {cls.name}.{node.func.attr}"
        for node in ast.walk(function):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            value = node.value
            if isinstance(value, ast.IfExp) and isinstance(value.orelse, ast.Constant) and value.orelse.value is None:
                value = value.body
            if len(writes.get(name, [])) != 1 or not isinstance(value, ast.Call):
                continue
            func = value.func
            target: ast.AST | None = None
            if isinstance(func, ast.Name) and func.id not in writes and func.id not in args:
                target = imports.get(func.id)
                if func.id in url_mappings:
                    mappings.add(name)
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                receiver = func.value.id
                annotation = args.get(receiver)
                cls = (
                    imports.get(annotation.id) if isinstance(annotation, ast.Name) and receiver not in writes else None
                )
                if isinstance(cls, ast.ClassDef):
                    target = inherited_method(cls, func.attr, set())
            if isinstance(target, ast.FunctionDef | ast.AsyncFunctionDef) and dict_return(target.returns):
                mappings.add(name)
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in mappings
            ):
                result[(node.lineno, node.col_offset)] = "imported mapping return annotation"
    return result


def classify_get_receivers(source: str) -> dict[tuple[int, int], str]:
    """Classify only receivers whose local syntax supplies positive evidence.

    A service-returned object is not a mapping merely because its method is
    named ``get``.
    """
    result: dict[tuple[int, int], str] = {}
    tree = ast.parse(source)

    def is_dict(node: ast.AST | None) -> bool:
        return isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "dict"

    module_dicts = {
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and is_dict(node.annotation)
        and isinstance(node.value, ast.Dict | ast.DictComp)
    }
    module_dicts = {
        name
        for name in module_dicts
        if sum(
            isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store)
            for node in ast.walk(tree)
        )
        == 1
    }
    local_dict_functions = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and is_dict(node.returns)
        and all(isinstance(ret.value, ast.Dict | ast.DictComp) for ret in ast.walk(node) if isinstance(ret, ast.Return))
        and any(isinstance(ret, ast.Return) for ret in ast.walk(node))
    }
    frozen_requests: dict[int, set[str]] = {}
    for cls in (item for item in tree.body if isinstance(item, ast.ClassDef)):
        frozen = any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "dataclass"
            and any(
                keyword.arg == "frozen" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                for keyword in decorator.keywords
            )
            for decorator in cls.decorator_list
        )
        if not frozen:
            continue
        fields = {
            item.target.id
            for item in cls.body
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and isinstance(item.annotation, ast.Name)
            and item.annotation.id == "Request"
        }
        for method in cls.body:
            if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                frozen_requests[id(method)] = fields
    for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
        requests = {
            arg.arg
            for arg in function.args.args + function.args.kwonlyargs
            if arg.annotation is not None and unparse(arg.annotation) == "Request"
        }
        writes: dict[str, int] = {}
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                writes[node.id] = writes.get(node.id, 0) + 1
        requests = {name for name in requests if not writes.get(name)}
        arguments = {arg.arg for arg in function.args.args + function.args.kwonlyargs}
        mappings: set[str] = module_dicts - set(writes) - arguments
        for statement in ast.walk(function):
            if isinstance(statement, ast.Assign) and (
                isinstance(statement.value, ast.Dict | ast.DictComp)
                or (
                    isinstance(statement.value, ast.Call)
                    and isinstance(statement.value.func, ast.Name)
                    and statement.value.func.id in local_dict_functions
                    and statement.value.func.id not in writes
                    and statement.value.func.id not in arguments
                )
            ):
                mappings.update(
                    target.id for target in statement.targets if isinstance(target, ast.Name) and writes[target.id] == 1
                )
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and is_dict(statement.annotation)
                and isinstance(statement.value, ast.Dict | ast.DictComp)
                and writes[statement.target.id] == 1
            ):
                mappings.add(statement.target.id)
        for statement in function.body:
            for node in ast.walk(statement):
                if (
                    not isinstance(node, ast.Call)
                    or not isinstance(node.func, ast.Attribute)
                    or node.func.attr != "get"
                ):
                    continue
                receiver = node.func.value
                if isinstance(receiver, ast.Dict | ast.DictComp) or (
                    isinstance(receiver, ast.Name) and receiver.id in mappings
                ):
                    result[(node.lineno, node.col_offset)] = "local mapping literal"
                elif (
                    isinstance(receiver, ast.Attribute)
                    and receiver.attr in ("headers", "cookies")
                    and isinstance(receiver.value, ast.Name)
                    and receiver.value.id in requests
                ):
                    result[(node.lineno, node.col_offset)] = f"Request.{receiver.attr}"
                elif (
                    isinstance(receiver, ast.Attribute)
                    and receiver.attr in ("headers", "cookies")
                    and isinstance(receiver.value, ast.Attribute)
                    and isinstance(receiver.value.value, ast.Name)
                    and receiver.value.value.id == "self"
                    and receiver.value.attr in frozen_requests.get(id(function), set())
                    and not writes.get("self")
                ):
                    result[(node.lineno, node.col_offset)] = f"frozen Request.{receiver.attr}"
    return result


def _function_reaches(
    function: ast.FunctionDef | ast.AsyncFunctionDef, names: dict[str, str], classified: dict[tuple[int, int], str]
) -> tuple[set[Reach], list[str]]:
    reaches: set[Reach] = set()
    unresolved: list[str] = []
    proxy_names: set[str] = set()
    for node in (child for statement in function.body for child in ast.walk(statement)):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and unparse(node.value.func) == "HubProxy.of"
        ):
            proxy_names.update(target.id for target in node.targets if isinstance(target, ast.Name))
    for argument in function.args.args + function.args.kwonlyargs:
        if argument.annotation is not None and unparse(argument.annotation) == "HubProxy":
            proxy_names.add(argument.arg)
    for node in (child for statement in function.body for child in ast.walk(statement)):
        if isinstance(node, ast.JoinedStr) and "hub_url" in unparse(node):
            segments = [
                part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)
            ]
            path = next((re.match(r"(/api/[^?&]*)", value) for value in segments if value.startswith("/api/")), None)
            if path is not None:
                reaches.add(Reach("GET", path.group(1), node.lineno))
            else:
                unresolved.append(f"{function.name}:{node.lineno}: dynamic hub URL {unparse(node)}")
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = _string(node.value, names)
            if value is not None:
                names[node.targets[0].id] = value
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        receiver = unparse(node.func.value)
        if attr in ("_get", "_post", "_send") and receiver == "self":
            method = {"_get": "GET", "_post": "POST"}.get(attr)
        elif attr in ("get", "post", "put", "patch", "delete", "request") and (
            receiver in ("self._client", "self.client", "self._http")
            or "HubProxy.of(" in receiver
            or receiver in proxy_names
        ):
            method = None if attr == "request" else attr.upper()
        else:
            if (
                attr in ("get", "post", "put", "patch", "delete", "request")
                and (node.lineno, node.col_offset) not in classified
            ):
                unresolved.append(f"{function.name}:{node.lineno}: unknown receiver {unparse(node)}")
            continue
        args = node.args
        if method is None:
            method = _string(args[0], names) if args else None
            args = args[1:]
        path = _string(args[0], names) if args else None
        if method is None or path is None or not path.startswith("/"):
            unresolved.append(f"{function.name}:{node.lineno}: {unparse(node)}")
        else:
            reaches.add(Reach(method, path, node.lineno))
    return reaches, unresolved


def producers(source: str, resolved: dict[str, str] | None = None) -> set[tuple[str, int, str]]:
    """Explicit HTTP exception raises and response constructions, independent of declarations."""
    found: set[tuple[str, int, str]] = set()
    tree = ast.parse(source)
    aliases = {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    aliases.update(resolved or {})
    for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else (node.func.attr if isinstance(node.func, ast.Attribute) else "")
            )
            name = aliases.get(name, name)
            if name in ("problem_response", "declared_problem_response") or name.endswith(
                ("HTTPException", "Response")
            ):
                found.add((function.name, node.lineno, unparse(node)))
    return found


def boundary_producer_census(root: Path) -> set[tuple[str, str, int, str]]:
    """Census explicit HTTP constructors throughout the hub, before routing analysis.

    Successful responses and unrelated operations are included as candidates;
    dynamic constructors are not, so absence here is not proof of absence.
    """
    hub = root / "src/blizzard/hub"
    paths = sorted(hub.rglob("*.py"))
    return {
        (str(path.relative_to(root)), function, line, expression)
        for path in paths
        for function, line, expression in producers(path.read_text())
    }


def producer_file_fingerprints(root: Path) -> dict[str, tuple[int, str]]:
    """Fingerprint constructor candidates per file, independently of declarations.

    Expressions and owners, not line numbers, form the fingerprint, so line
    movement is not mistaken for a change.
    """
    grouped: dict[str, list[str]] = {}
    for path, function, _, expression in boundary_producer_census(root):
        call = ast.parse(expression, mode="eval").body
        if isinstance(call, ast.Call):
            status = next((kw.value for kw in call.keywords if kw.arg == "status_code"), None)
            if isinstance(status, ast.Constant) and isinstance(status.value, int) and 200 <= status.value < 400:
                continue
            if isinstance(status, ast.Attribute) and re.match(r"HTTP_[23]\d\d_", status.attr):
                continue
        grouped.setdefault(path, []).append(f"{function}: {expression}")
    return {
        path: (len(entries), hashlib.sha256("\n".join(sorted(entries)).encode()).hexdigest())
        for path, entries in grouped.items()
    }


def missing_coverage[T](discovered: set[T], covered: set[T]) -> set[T]:
    """The discoveries the declared coverage does not cover."""
    return discovered - covered


def runner_boundary(root: Path) -> tuple[set[tuple[str, str]], list[str]]:
    """Collect runner outbound call sites, including proxy callers, with file-qualified unknowns.

    Proxy plumbing is resolved only when its forwarding and callers are proven.
    """
    runner = root / "src/blizzard/runner"
    paths = [
        runner / "hub/internal/http_hub.py",
        runner / "transcripts/internal/http_archived_transcript_repository.py",
        *sorted((runner / "auth/internal").glob("http_*.py")),
        *sorted((runner / "api").glob("*.py")),
    ]
    operations: set[tuple[str, str]] = set()
    unresolved: list[str] = outbound_seam_unknowns(runner)
    for path in paths:
        reached, unknown = discover(path.read_text(), root)
        operations.update((r.method, r.path) for r in reached)
        unresolved.extend(f"{path.relative_to(root)}:{item}" for item in unknown)
    jwks, wired = jwks_wiring(runner)
    operations.update(("GET", path) for path in jwks)
    if wired:
        marker = "src/blizzard/runner/auth/internal/http_jwks_cache.py:_refresh:"
        unresolved = [
            item for item in unresolved if not (item.startswith(marker) and "self._http.get(self._url)" in item)
        ]
    proxied, proxy_unknown = proxy_forwarding(runner)
    operations.update(proxied)
    if not proxy_unknown:
        unresolved = [
            item
            for item in unresolved
            if not (
                item.startswith("src/blizzard/runner/api/hub_proxy.py:_forward:")
                and ("dynamic hub URL" in item or "self.client.request(method, url" in item)
            )
        ]
    else:
        unresolved.extend(f"proxy proof: {item}" for item in proxy_unknown)
    return operations, sorted(unresolved)


def outbound_seam_unknowns(runner: Path) -> list[str]:
    """Check known hub transports and surface new HTTP-library imports for review.

    The import census covers the runner tree, not only the adapter discovery
    list. It does not prove that arbitrary injected objects cannot make HTTP.
    """
    http_import_owners = {
        "app.py",
        "composition.py",
        "loop_wiring.py",
        "api/attachments.py",  # Catches httpx2.HTTPError from the reviewed HubProxy call.
        "api/hub_proxy.py",
        "api/wiring.py",
        "auth/internal/http_hub_auth_mode.py",
        "auth/internal/http_jwks_cache.py",
        "hub/internal/http_hub.py",
        "transcripts/internal/http_archived_transcript_repository.py",
        "subscriptions/internal/usage_http_client.py",
        "subscriptions/internal/anthropic_subscription_sampler.py",
        "subscriptions/internal/openai_subscription_sampler.py",
        "subscriptions/internal/subscription_sampler_factory.py",
    }
    transports = {
        "api/hub_proxy.py": {"self.client"},
        "auth/internal/http_hub_auth_mode.py": {"self._http"},
        "auth/internal/http_jwks_cache.py": {"self._http"},
        "hub/internal/http_hub.py": {"self._client"},
        "transcripts/internal/http_archived_transcript_repository.py": {"self._client"},
    }
    # Client construction is reviewed separately from permission to import httpx.
    # Counts intentionally flag a new constructor even in an existing owner file.
    client_constructors = {
        "app.py": 6,
        "composition.py": 2,
        "loop_wiring.py": 2,
        "subscriptions/internal/usage_http_client.py": 1,
    }
    from tests.support.runner_injected_call_inventory import FINGERPRINTS

    unknown: list[str] = []
    actual = injected_call_fingerprints(runner)
    for relative in sorted(actual.keys() | FINGERPRINTS.keys()):
        if actual.get(relative) != FINGERPRINTS.get(relative):
            unknown.append(f"{relative}: injected receiver call inventory changed")
    for path in runner.rglob("*.py"):
        relative = path.relative_to(runner).as_posix()
        tree = ast.parse(path.read_text())
        constructors = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and unparse(n.func) in {"httpx2.Client", "httpx2.AsyncClient", "httpx.Client", "httpx.AsyncClient"}
        ]
        if len(constructors) != client_constructors.get(relative, 0):
            unknown.append(f"{relative}: HTTP client construction count changed: {len(constructors)}")
        parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for method in (n for n in cls.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
                args = {arg.arg for arg in method.args.args + method.args.kwonlyargs}
                aliases = set(args)
                assignments = [n for n in ast.walk(method) if isinstance(n, ast.Assign)]
                while True:
                    expanded = aliases | {
                        target.id
                        for assignment in assignments
                        if isinstance(assignment.value, ast.Name) and assignment.value.id in aliases
                        for target in assignment.targets
                        if isinstance(target, ast.Name)
                    }
                    if expanded == aliases:
                        break
                    aliases = expanded
                for assignment in (n for n in ast.walk(method) if isinstance(n, ast.Assign)):
                    if not isinstance(assignment.value, ast.Name) or assignment.value.id not in aliases:
                        continue
                    for target in assignment.targets:
                        if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
                            continue
                        if target.value.id != "self":
                            continue
                        receiver = unparse(target)
                        # A client-shaped field used with an HTTP method is an
                        # unreviewed transport, irrespective of imports.
                        if receiver not in transports.get(relative, set()) and any(
                            isinstance(n, ast.Call)
                            and isinstance(n.func, ast.Attribute)
                            and unparse(n.func.value) == receiver
                            and n.func.attr in {"get", "post", "put", "patch", "delete", "request", "send", "stream"}
                            and (
                                "client" in assignment.value.id
                                or "http" in assignment.value.id
                                or any(
                                    isinstance(arg, ast.Constant)
                                    and isinstance(arg.value, str)
                                    and arg.value.startswith("/api/")
                                    for arg in n.args
                                )
                            )
                            for n in ast.walk(cls)
                        ):
                            unknown.append(
                                f"{relative}:{assignment.lineno}: unreviewed injected client field: {receiver}"
                            )
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            arguments = {arg.arg for arg in function.args.args + function.args.kwonlyargs}
            arguments.discard("self")
            assignments = [n for n in ast.walk(function) if isinstance(n, ast.Assign)]
            while True:
                expanded = arguments | {
                    target.id
                    for assignment in assignments
                    if isinstance(assignment.value, ast.Name) and assignment.value.id in arguments
                    for target in assignment.targets
                    if isinstance(target, ast.Name)
                }
                if expanded == arguments:
                    break
                arguments = expanded
            # Unknown client-like parameters called as transports require review,
            # including in files already permitted to import HTTP libraries.
            for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
                if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name):
                    continue
                receiver = call.func.value.id
                proven_proxy = (
                    any(
                        arg.arg == receiver and isinstance(arg.annotation, ast.Name) and arg.annotation.id == "HubProxy"
                        for arg in function.args.args + function.args.kwonlyargs
                    )
                    and relative.startswith("api/")
                    and any(
                        isinstance(item, ast.ImportFrom)
                        and item.module == "blizzard.runner.api.hub_proxy"
                        and any(alias.name == "HubProxy" and alias.asname is None for alias in item.names)
                        for item in tree.body
                    )
                    and not any(
                        isinstance(item, ast.Name)
                        and item.id in {receiver, "HubProxy"}
                        and isinstance(item.ctx, ast.Store | ast.Del)
                        for item in ast.walk(function)
                    )
                    and not proxy_forwarding(runner)[1]
                )
                if (
                    receiver in arguments
                    and not proven_proxy
                    and (
                        "client" in receiver
                        or "http" in receiver
                        or any(
                            isinstance(arg, ast.Constant)
                            and isinstance(arg.value, str)
                            and arg.value.startswith("/api/")
                            for arg in call.args
                        )
                    )
                    and call.func.attr
                    in {
                        "get",
                        "post",
                        "put",
                        "patch",
                        "delete",
                        "request",
                        "send",
                        "stream",
                    }
                ):
                    unknown.append(f"{relative}:{call.lineno}: unreviewed injected transport: {unparse(call)}")
        for node in ast.walk(tree):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if (
                isinstance(node, ast.Import | ast.ImportFrom)
                and relative not in http_import_owners
                and any(name.split(".")[0] in {"httpx", "httpx2", "requests", "aiohttp", "urllib3"} for name in modules)
            ):
                unknown.append(f"{relative}:{node.lineno}: unreviewed HTTP library import")
            if not isinstance(node, ast.Attribute) or unparse(node) not in transports.get(relative, set()):
                continue
            parent = parents.get(id(node))
            if isinstance(node.ctx, ast.Store):
                continue
            if isinstance(parent, ast.Attribute) and parent.value is node:
                call = parents.get(id(parent))
                if (
                    parent.attr in {"get", "post", "put", "patch", "delete", "request"}
                    and isinstance(call, ast.Call)
                    and call.func is parent
                ):
                    continue
            unknown.append(f"{relative}:{node.lineno}: unsupported or escaped HTTP client: {unparse(node)}")
    return sorted(unknown)


def injected_call_fingerprints(runner: Path) -> dict[str, tuple[int, str]]:
    """Change detector for HTTP-shaped calls on injected arguments and fields.

    The enclosing function or class is fingerprinted with the call, so forwarding,
    annotation and field changes require a fresh review.
    """
    methods = {"get", "post", "put", "patch", "delete", "request", "send", "stream"}
    result: dict[str, tuple[int, str]] = {}
    for path in runner.rglob("*.py"):
        tree = ast.parse(path.read_text())
        entries: list[str] = []
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            arguments = {
                a.arg
                for a in function.args.posonlyargs + function.args.args + function.args.kwonlyargs
                if a.annotation is None
            }
            arguments.discard("self")
            assignments = [n for n in ast.walk(function) if isinstance(n, ast.Assign)]
            while True:
                expanded = arguments | {
                    target.id
                    for assignment in assignments
                    if isinstance(assignment.value, ast.Name) and assignment.value.id in arguments
                    for target in assignment.targets
                    if isinstance(target, ast.Name)
                }
                if expanded == arguments:
                    break
                arguments = expanded
            for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
                if not isinstance(call.func, ast.Attribute) or call.func.attr not in methods:
                    continue
                receiver = call.func.value
                if isinstance(receiver, ast.Name) and receiver.id in arguments:
                    entries.append(stable_dump(function) + stable_dump(call))
            # A bound transport method can be saved and invoked later without an
            # attribute call at the invocation site. Track the binding itself.
            call_targets = {id(call.func) for call in ast.walk(function) if isinstance(call, ast.Call)}
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in methods
                    and isinstance(node.value, ast.Name)
                    and node.value.id in arguments
                    and id(node) not in call_targets
                ):
                    entries.append(stable_dump(function) + stable_dump(node))
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            fields: set[str] = set()
            for method in (n for n in cls.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
                args = {
                    a.arg
                    for a in method.args.posonlyargs + method.args.args + method.args.kwonlyargs
                    if a.annotation is None
                } - {"self"}
                assignments = [n for n in ast.walk(method) if isinstance(n, ast.Assign)]
                while True:
                    expanded = args | {
                        t.id
                        for a in assignments
                        if isinstance(a.value, ast.Name) and a.value.id in args
                        for t in a.targets
                        if isinstance(t, ast.Name)
                    }
                    if expanded == args:
                        break
                    args = expanded
                fields.update(
                    unparse(t)
                    for a in assignments
                    if isinstance(a.value, ast.Name) and a.value.id in args
                    for t in a.targets
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
                )
            for call in (n for n in ast.walk(cls) if isinstance(n, ast.Call)):
                if (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr in methods
                    and unparse(call.func.value) in fields
                ):
                    entries.append(stable_dump(cls) + stable_dump(call))
        if entries:
            result[path.relative_to(runner).as_posix()] = (
                len(entries),
                hashlib.sha256("\n".join(sorted(entries)).encode()).hexdigest(),
            )
    return result


_FORWARD_URL = stable_dump(ast.parse("f\"{self.config.hub_url.rstrip('/')}{path}\"", mode="eval").body)


def proxy_forwarding(runner: Path) -> tuple[set[tuple[str, str]], list[str]]:
    """Trace proxy call sites and check the transport's argument forwarding.

    Reject unsupported uses and mutations; unknowns remain visible to the caller.
    """
    tree = ast.parse((runner / "api/hub_proxy.py").read_text())
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "HubProxy"]
    if len(classes) != 1:
        return set(), ["HubProxy definition missing or ambiguous"]
    methods = {node.name: node for node in classes[0].body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}
    required = {
        "get": ("self.forward", ("'GET'", "path")),
        "post": ("self.forward", ("'POST'", "path")),
        "forward": ("self._forward", ("method", "path")),
        "_forward": ("self.client.request", ("method", "url")),
    }
    unknown: list[str] = []
    for name, (target, arguments) in required.items():
        method = methods.get(name)
        calls = (
            []
            if method is None
            else [node for node in ast.walk(method) if isinstance(node, ast.Call) and unparse(node.func) == target]
        )
        if len(calls) != 1 or tuple(unparse(arg) for arg in calls[0].args[:2]) != arguments:
            unknown.append(f"HubProxy.{name}: forwarding to {target} is not established")
        if method is not None:
            protected = {"path", "method", "url"} & {arg.arg for arg in method.args.args}
            if name == "_forward":
                protected.add("url")
            writes = [n for n in ast.walk(method) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
            for variable in protected:
                allowed = 1 if name == "_forward" and variable == "url" else 0
                if sum(n.id == variable for n in writes) != allowed:
                    unknown.append(f"HubProxy.{name}: {variable} reassigned")
    for name, method in methods.items():
        parents = {id(child): parent for parent in ast.walk(method) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(method):
            if isinstance(node, ast.Attribute) and unparse(node) in (
                "self.forward",
                "self._forward",
                "self.client.request",
            ):
                expected_owner = {
                    "self.forward": {"get", "post"},
                    "self._forward": {"forward"},
                    "self.client.request": {"_forward"},
                }[unparse(node)]
                parent = parents.get(id(node))
                if name not in expected_owner or not isinstance(parent, ast.Call) or parent.func is not node:
                    unknown.append(f"HubProxy.{name}: unexpected forwarding use {unparse(node)}")
    forward = methods.get("_forward")
    if forward is None or not any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "url" for target in node.targets)
        and stable_dump(node.value) == _FORWARD_URL
        for node in ast.walk(forward)
    ):
        unknown.append("HubProxy._forward: URL construction is not established")
    reaches: set[tuple[str, str]] = set()
    for path in sorted((runner / "api").glob("*.py")):
        if path.name == "hub_proxy.py":
            continue
        source = path.read_text()
        if "HubProxy" in source:
            found, unresolved = discover(source)
            reaches.update((item.method, item.path) for item in found)
            unknown.extend(f"{path.name}:{item}" for item in unresolved)
    # Every use of the imported class must be a direct HubProxy.of call or a
    # type annotation. In particular, an escaped class could acquire new callers.
    for path in runner.rglob("*.py"):
        if path.name == "hub_proxy.py":
            continue
        tree = ast.parse(path.read_text())
        aliases = {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "blizzard.runner.api.hub_proxy"
            for alias in node.names
            if alias.name == "HubProxy"
        }
        if aliases and path.parent != runner / "api":
            unknown.append(f"{path.relative_to(runner)}: proxy caller outside scanned API directory")
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module == "blizzard.runner.api.hub_proxy"
            and any(alias.name == "*" for alias in node.names)
            for node in tree.body
        ):
            unknown.append(f"{path.relative_to(runner)}: wildcard proxy import")
        parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for imported in (n for n in ast.walk(tree) if isinstance(n, ast.Import)):
            if any(a.name == "blizzard.runner.api.hub_proxy" for a in imported.names):
                unknown.append(f"{path.name}:{imported.lineno}: unsupported proxy module import")

        def permitted_use(
            node: ast.AST, parents: dict[int, ast.AST] = parents, path: Path = path, tree: ast.Module = tree
        ) -> bool:
            parent = parents.get(id(node))
            if isinstance(parent, ast.Attribute) and parent.value is node and parent.attr in ("get", "post"):
                call = parents.get(id(parent))
                return isinstance(call, ast.Call) and call.func is parent
            if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and parent.func.id == "fleet_summary":
                return path.name == "fleet_summary.py" or any(
                    isinstance(item, ast.ImportFrom)
                    and item.module == "blizzard.runner.api.fleet_summary"
                    and any(a.name == "fleet_summary" and a.asname is None for a in item.names)
                    for item in tree.body
                )
            return False

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in aliases:
                parent = parents.get(id(node))
                if isinstance(parent, ast.Attribute) and parent.value is node and parent.attr == "of":
                    constructor = parents.get(id(parent))
                    if not isinstance(constructor, ast.Call) or constructor.func is not parent:
                        unknown.append(f"{path.name}:{node.lineno}: escaped proxy factory")
                        continue
                    owner = parents.get(id(constructor))
                    if not permitted_use(constructor) and not (
                        isinstance(owner, ast.Assign)
                        and len(owner.targets) == 1
                        and isinstance(owner.targets[0], ast.Name)
                    ):
                        unknown.append(f"{path.name}:{node.lineno}: escaped proxy instance")
                    continue
                if isinstance(parent, ast.arg) and parent.annotation is node:
                    continue
                unknown.append(f"{path.name}:{node.lineno}: unsupported HubProxy use {unparse(node)}")
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            names = {
                arg.arg
                for arg in function.args.args
                if isinstance(arg.annotation, ast.Name) and arg.annotation.id in aliases
            }
            names.update(
                n.targets[0].id
                for n in ast.walk(function)
                if isinstance(n, ast.Assign)
                and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name)
                and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Attribute)
                and isinstance(n.value.func.value, ast.Name)
                and n.value.func.value.id in aliases
                and n.value.func.attr == "of"
            )
            for node in ast.walk(function):
                if not isinstance(node, ast.Name) or node.id not in names:
                    continue
                if isinstance(node.ctx, ast.Store):
                    parent = parents.get(id(node))
                    if (
                        isinstance(parent, ast.Assign)
                        and isinstance(parent.value, ast.Call)
                        and unparse(parent.value.func) == "HubProxy.of"
                    ):
                        continue
                if isinstance(node.ctx, ast.Load) and permitted_use(node):
                    continue
                unknown.append(f"{path.name}:{node.lineno}: proxy instance escapes or is reassigned: {node.id}")
    return reaches, sorted(unknown)


def jwks_wiring(runner: Path) -> tuple[set[str], bool]:
    """Resolve the cache's field through its constructor and every local construction.

    Any unknown constructor argument or changed field forwarding keeps the cache
    call unresolved. This does not assume that a second call site uses the first URL.
    """
    cache = ast.parse((runner / "auth/internal/http_jwks_cache.py").read_text())
    classes = [n for n in cache.body if isinstance(n, ast.ClassDef) and n.name == "JwksCache"]
    if len(classes) != 1:
        return set(), False
    cls = classes[0]
    constructors = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"]
    if len(constructors) != 1:
        return set(), False
    init = constructors[0]
    bindings = [
        n
        for n in ast.walk(cls)
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and unparse(n.targets[0]) == "self._url"
    ]
    if len(bindings) != 1 or not isinstance(bindings[0].value, ast.Name):
        return set(), False
    if any(
        isinstance(node, ast.AnnAssign | ast.AugAssign) and unparse(node.target) == "self._url"
        for node in ast.walk(cls)
    ):
        return set(), False
    parameter = bindings[0].value.id
    arguments = [arg.arg for arg in init.args.args]
    if parameter not in arguments:
        return set(), False
    position = arguments.index(parameter) - 1
    urls: set[str] = set()
    complete = True
    count = 0
    for path in runner.rglob("*.py"):
        tree = ast.parse(path.read_text())
        if any(
            isinstance(node, ast.Import) and any(alias.name.endswith(".http_jwks_cache") for alias in node.names)
            for node in ast.walk(tree)
        ):
            complete = False
        names = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "blizzard.runner.auth.internal.http_jwks_cache"
            for alias in node.names
            if alias.name == "JwksCache"
        }
        direct_uses = {
            id(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names
        }
        if any(
            isinstance(node, ast.Name) and node.id in names and id(node) not in direct_uses for node in ast.walk(tree)
        ):
            complete = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id not in names:
                continue
            count += 1
            argument = (
                node.args[position]
                if 0 <= position < len(node.args)
                else next((keyword.value for keyword in node.keywords if keyword.arg == parameter), None)
            )
            value = _string(argument, {}) if argument is not None else None
            if value is None or not value.startswith("/"):
                complete = False
            else:
                urls.add(value)
    return urls, complete and count > 0


def coverage_failures[T](expected: set[T], declared: set[T]) -> list[str]:
    """Report each independently discovered item absent from a proposed inventory."""
    return [f"missing coverage: {item}" for item in sorted(expected - declared, key=str)]


def producer_inventory_failures(
    observed: dict[tuple[str, str], set[str]],
    inventory: dict[tuple[str, str], set[str]],
    unresolved: dict[tuple[str, str], set[str]],
) -> list[str]:
    """Compare independently discovered producers per operation, failing closed on unknowns.

    An operation cannot borrow another operation's evidence, and an empty
    producer set must be inventoried explicitly.
    """
    failures: list[str] = []
    for operation in sorted(observed.keys() | unresolved.keys()):
        label = f"{operation[0]} {operation[1]}"
        for item in sorted(unresolved.get(operation, set())):
            failures.append(f"{label}: unresolved: {item}")
        if operation not in inventory:
            failures.append(f"{label}: missing producer inventory")
            continue
        for item in sorted(observed.get(operation, set()) - inventory[operation]):
            failures.append(f"{label}: missing producer: {item}")
    return failures


def _shape(path: str) -> str:
    return re.sub(r"\{[^{}]+\}", "{}", path)


def match_operations(
    reaches: set[tuple[str, str]], operations: set[tuple[str, str]]
) -> tuple[dict[tuple[str, str], set[tuple[str, str]]], set[tuple[str, str]]]:
    """Match parameter names structurally, without assuming unmatched reaches are absent."""
    matched: dict[tuple[str, str], set[tuple[str, str]]] = {}
    missing: set[tuple[str, str]] = set()
    for method, path in reaches:
        candidates = {op for op in operations if op[0] == method and _shape(op[1]) == _shape(path)}
        if candidates:
            matched[(method, path)] = candidates
        else:
            missing.add((method, path))
    return matched, missing


def route_inventory(app: Any) -> tuple[dict[tuple[str, str], Any], list[str]]:
    """Read effective routes, including dependencies contributed by router inclusion."""
    routes: dict[tuple[str, str], Any] = {}
    unresolved: list[str] = []
    for item in app.routes:
        contexts = getattr(item, "effective_route_contexts", None)
        candidates = list(contexts()) if contexts is not None else [item]
        for route in candidates:
            if isinstance(route, APIRoute) or isinstance(getattr(route, "original_route", None), APIRoute):
                for method in route.methods or ():
                    key = (method, route.path_format)
                    if key in routes:
                        unresolved.append(f"duplicate route {method} {route.path_format}")
                    routes[key] = route
            elif getattr(route, "original_router", None) is not None:
                unresolved.append(f"nested router not expanded: {route!r}")
    return routes, unresolved


def callable_producers(func: Any) -> tuple[set[str], set[str]]:
    """Walk statically named, locally imported Python helpers from one handler.

    Dynamic calls are not treated as evidence of absence; this is a conservative
    candidate census, not an interprocedural proof of exception reachability.
    """
    pending = [func]
    seen: set[Any] = set()
    found: set[str] = set()
    unresolved: set[str] = set()
    while pending:
        current = pending.pop()
        bound = current.__self__ if inspect.ismethod(current) else None
        if inspect.ismethod(current):
            current = current.__func__
        key = (current, id(bound))
        if not inspect.isfunction(current) or key in seen:
            continue
        seen.add(key)
        if not current.__module__.startswith("blizzard.hub."):
            continue
        try:
            lines, start = inspect.getsourcelines(current)
            source = textwrap.dedent("".join(lines))
            tree = ast.parse(source)
        except (OSError, TypeError, SyntaxError):
            unresolved.add(f"source unavailable: {current.__module__}.{current.__qualname__}")
            continue
        name = f"{current.__module__}.{current.__qualname__}"
        aliases = {
            key: value.__name__
            for key, value in current.__globals__.items()
            if inspect.isclass(value) and issubclass(value, (Response, HTTPException))
        }
        found.update(f"{name}:{start + line - 1}: {call}" for _, line, call in producers(source, aliases))
        definition = next(n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef))
        parameters = {
            arg.arg for arg in definition.args.posonlyargs + definition.args.args + definition.args.kwonlyargs
        }
        parameters.update(arg.arg for arg in (definition.args.vararg, definition.args.kwarg) if arg is not None)
        writes = {
            node.id
            for node in ast.walk(definition)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del)
        }
        for node in ast.walk(definition):
            if isinstance(node, ast.Import | ast.ImportFrom):
                writes.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
            elif (
                isinstance(node, ast.ExceptHandler | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and node is not definition
                and node.name is not None
            ):
                writes.add(node.name)
        # Follow a single local alias of a known callable, without executing it.
        assignments: dict[str, list[tuple[ast.AST, ast.stmt]]] = {}
        for node in ast.walk(definition):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                assignments.setdefault(node.targets[0].id, []).append((node.value, node))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                assignments.setdefault(node.target.id, []).append((node.value, node))
        aliases_local: dict[str, tuple[int, Any]] = {}
        for local, values in assignments.items():
            stores = sum(
                isinstance(node, ast.Name) and node.id == local and isinstance(node.ctx, ast.Store | ast.Del)
                for node in ast.walk(definition)
            )
            if len(values) != 1 or stores != 1 or local in parameters:
                continue
            value, assignment = values[0]
            if assignment in definition.body and isinstance(value, ast.Name) and value.id not in parameters | writes:
                target = current.__globals__.get(value.id)
                if inspect.isfunction(target) or inspect.ismethod(target):
                    aliases_local[local] = (assignment.lineno, target)
        nested_calls = {
            id(call)
            for statement in definition.body
            for nested in ast.walk(statement)
            if isinstance(nested, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda)
            for call in ast.walk(nested)
            if isinstance(call, ast.Call)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                alias = aliases_local.get(node.func.id)
                target = current.__globals__.get(node.func.id) if node.func.id not in parameters | writes else None
                if alias and node.lineno > alias[0] and id(node) not in nested_calls:
                    target = alias[1]
                if inspect.isfunction(target) or inspect.ismethod(target):
                    pending.append(target)
                elif inspect.isclass(target) and target.__module__.startswith("blizzard.hub."):
                    if not issubclass(target, (Response, HTTPException)):
                        for method in ("__new__", "__init__"):
                            candidate = inspect.getattr_static(target, method, None)
                            if isinstance(candidate, staticmethod):
                                candidate = candidate.__func__
                            if inspect.isfunction(candidate):
                                pending.append(candidate)
                        unresolved.add(f"{name}:{start + node.lineno - 1}: constructor dispatch {unparse(node.func)}")
                elif (
                    node.func.id in parameters | writes
                    or (target is None and not hasattr(builtins, node.func.id))
                    or (target is not None and not inspect.isclass(target))
                ):
                    unresolved.add(f"{name}:{start + node.lineno - 1}: dynamic call {unparse(node.func)}")
            elif isinstance(node.func, ast.Attribute):
                receiver = node.func.value
                target = None
                if isinstance(receiver, ast.Name) and receiver.id not in writes:
                    instance = (
                        bound
                        if bound is not None and definition.args.args and receiver.id == definition.args.args[0].arg
                        else current.__globals__.get(receiver.id)
                        if receiver.id not in parameters
                        else None
                    )
                    if inspect.ismodule(instance):
                        target = vars(instance).get(node.func.attr)
                    elif instance is not None and not inspect.isclass(instance):
                        target = inspect.getattr_static(instance, node.func.attr, None)
                        if inspect.isfunction(target):
                            target = target.__get__(instance)
                if inspect.ismethod(target) or inspect.isfunction(target):
                    pending.append(target)
                else:
                    unresolved.add(f"{name}:{start + node.lineno - 1}: dynamic call {unparse(node.func)}")
    return found, unresolved


def operation_producers(route: Any, app: Any) -> tuple[set[str], set[str]]:
    """Candidates from endpoint, recursive FastAPI dependencies, and app handlers.

    App handlers are candidates for every operation, not a claim that each error
    can actually be raised there. Framework validation is intentionally separate.
    """
    pending = [route.dependant]
    functions: list[Any] = [route.endpoint]
    while pending:
        dependency = pending.pop()
        functions.append(dependency.call)
        pending.extend(dependency.dependencies)
    found: set[str] = set()
    unresolved: set[str] = set()
    for exception, handler in app.exception_handlers.items():
        if inspect.isfunction(handler) and handler.__module__.startswith("blizzard.hub"):
            functions.append(handler)
            unresolved.add(
                f"exception handler applicability: {getattr(exception, '__name__', exception)} -> "
                f"{handler.__module__}.{handler.__qualname__}"
            )
    for function in functions:
        if function is None:
            continue
        if not inspect.isfunction(function) and not inspect.ismethod(function):
            instance: object = function
            call: object = inspect.getattr_static(type(instance), "__call__", None)
            if inspect.isfunction(call):
                function = call.__get__(function)
            else:
                unresolved.add(f"non-function dependency or handler: {type(function).__name__}")
                continue
        candidates, unknown = callable_producers(function)
        found.update(candidates)
        unresolved.update(unknown)
    return found, unresolved


def direct_exception_dispatch(route: Any, app: Any) -> dict[str, set[str]]:
    """Positive evidence of registered dispatch from explicit raises in route dependencies.

    Absence is not inferred: helper calls, catches and dynamic construction still
    need analysis, and a raise does not prove its branch executes.
    """
    pending = [route.dependant]
    functions = [route.endpoint]
    while pending:
        dependency = pending.pop()
        functions.append(dependency.call)
        pending.extend(dependency.dependencies)
    evidence: dict[str, set[str]] = {}
    for function in functions:
        if not inspect.isfunction(function):
            continue
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        except (OSError, TypeError, SyntaxError):
            continue
        shadowed = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
        }
        shadowed.update(node.arg for node in ast.walk(tree) if isinstance(node, ast.arg))
        definition = next(
            (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None
        )
        if definition is None:
            continue
        nodes: list[ast.AST] = list(definition.body)
        direct_nodes: list[ast.AST] = []
        while nodes:
            node = nodes.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            direct_nodes.append(node)
            nodes.extend(ast.iter_child_nodes(node))
        for node in direct_nodes:
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            expr = node.exc.func
            if not isinstance(expr, ast.Name) or expr.id in shadowed:
                continue
            exception = function.__globals__.get(expr.id)
            if not inspect.isclass(exception) or not issubclass(exception, Exception):
                continue
            for cls in exception.__mro__:
                handler = app.exception_handlers.get(cls)
                if handler is not None:
                    key = f"{handler.__module__}.{handler.__qualname__}"
                    exception_name = f"{exception.__module__}.{exception.__qualname__}"
                    evidence.setdefault(key, set()).add(
                        f"{function.__module__}.{function.__qualname__} raises {exception_name}"
                    )
                    break
    return evidence


def discovered_producer_inventory(
    reaches: set[tuple[str, str]], app: Any
) -> tuple[dict[tuple[str, str], set[str]], dict[tuple[str, str], set[str]], list[str]]:
    """Analyze each matched effective operation, retaining every analysis gap.

    This produces candidates to review, not an approved inventory: dynamic
    calls and app-wide exception handlers need explicit applicability analysis.
    """
    routes, route_unknown = route_inventory(app)
    matched, missing = match_operations(reaches, set(routes))
    found: dict[tuple[str, str], set[str]] = {}
    unknown: dict[tuple[str, str], set[str]] = {}
    for operations in matched.values():
        for operation in operations:
            if operation not in found:
                found[operation], unknown[operation] = operation_producers(routes[operation], app)
    return found, unknown, route_unknown + [f"unmatched reach: {method} {path}" for method, path in sorted(missing)]
