"""The hub OpenAPI classifier (bzh:fleet-wire-additive), pure over two parsed documents.

The wire selection is the hub↔runner surface the skew window governs; ``is_any_operation`` selects the whole document,
which the HTTP API surface diffs. A finding is ``deprecated`` when the baseline marked the thing ``deprecated: true``. A
gained response enum value is additive where the baseline marks every response position reaching it open."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

from blizzard.tools.compat.violation import Violation

#: The declared surface (docs/versioning.md's hub↔runner skew row): fleet calls, plus federation.
_SURFACE_PREFIX = "/api/fleet"
_SURFACE_EXTRA_PATHS = ("/api/auth/jwks.json", "/api/auth/authorize")
#: Single operations a runner reaches on an operator path — `runner init` adding its runner, or its host.
_SURFACE_EXTRA_OPERATIONS = (("post", "/api/runners"), ("post", "/api/hosts"))
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")
#: The document mark an open response position carries; compat reads documents, never the code that writes them.
_OPEN_MARK = "x-blizzard-open"

#: A parsed OpenAPI document or any object inside one: open JSON, read only at the keys the classifier names.
type Node = dict[str, Any]

#: A property's type shape: its type names, or for a union the shapes of its non-null members.
type Signature = tuple[str | Signature, ...]

#: Whether the operation at (path, method) is on the surface being diffed.
type Selection = Callable[[str, str], bool]

# --- Surface + schema reachability -------------------------------------------------


def is_wire_operation(path: str, method: str) -> bool:
    return (
        path.startswith(_SURFACE_PREFIX) or path in _SURFACE_EXTRA_PATHS or (method, path) in _SURFACE_EXTRA_OPERATIONS
    )


def _surface_operations(doc: Node, select: Selection) -> list[tuple[str, str, Node]]:
    operations: list[tuple[str, str, Node]] = []
    for path, methods in doc.get("paths", {}).items():
        for method, op in methods.items():
            if method in _HTTP_METHODS and select(path, method):
                operations.append((path, method, op))
    return operations


def is_any_operation(path: str, method: str) -> bool:
    """The HTTP API selection: every operation in the document."""
    return True


def _violation(where: str, reason: str, base_node: Node) -> Violation:
    """A wire finding whose ``deprecated`` is the mark the baseline carried on ``base_node``."""
    return Violation("wire", where, reason, base_node.get("deprecated") is True)


def _schema_components(doc: Node) -> Node:
    return doc.get("components", {}).get("schemas", {})


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _request_schema(op: Node) -> Node | None:
    body = op.get("requestBody")
    if not body:
        return None
    media = body.get("content", {}).get("application/json")
    return media.get("schema") if media else None


def _response_schema(op: Node) -> Node | None:
    responses = op.get("responses", {})
    for code in ("200", "201", "202"):
        resp = responses.get(code)
        if not resp:
            continue
        media = resp.get("content", {}).get("application/json")
        if media and "schema" in media:
            return media["schema"]
    return None


def _collect_reachable(
    doc: Node,
    node: Node | None,
    role: str,
    roles: dict[str, set[str]],
    seen: set[tuple[str, str]],
    marks: dict[str, list[bool]],
) -> None:
    if node is None:
        return
    ref = node.get("$ref")
    if ref:
        name = _ref_name(ref)
        roles.setdefault(name, set()).add(role)
        if role == "response":
            marks.setdefault(name, []).append(node.get(_OPEN_MARK) is True)
        key = (name, role)
        if key in seen:
            return
        seen.add(key)
        _collect_reachable(doc, _schema_components(doc).get(name), role, roles, seen, marks)
        return
    for sub in node.get("properties", {}).values():
        _collect_reachable(doc, sub, role, roles, seen, marks)
    if "items" in node:
        _collect_reachable(doc, node["items"], role, roles, seen, marks)
    if isinstance(node.get("additionalProperties"), dict):
        _collect_reachable(doc, node["additionalProperties"], role, roles, seen, marks)
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in node.get(key, []):
            _collect_reachable(doc, sub, role, roles, seen, marks)


def _reachable_schemas(doc: Node, select: Selection) -> dict[str, set[str]]:
    """Component schema name -> the surface roles ("request", "response") that reach it."""
    return _walk(doc, select)[0]


def _open_components(doc: Node, select: Selection) -> set[str]:
    """The components whose every response position — each ``$ref`` a response walk meets — carries the open mark."""
    return {name for name, marked in _walk(doc, select)[1].items() if all(marked)}


def _walk(doc: Node, select: Selection) -> tuple[dict[str, set[str]], dict[str, list[bool]]]:
    """Each reached component's roles, and the open mark of each response position that references it.

    ``seen`` is keyed by (schema, role): a schema reached by both roles must have its
    descendants walked once per role, not just whichever role's walk arrives first.
    """
    roles: dict[str, set[str]] = {}
    seen: set[tuple[str, str]] = set()
    marks: dict[str, list[bool]] = {}
    for _path, _method, op in _surface_operations(doc, select):
        _collect_reachable(doc, _request_schema(op), "request", roles, seen, marks)
        for param in op.get("parameters", []):
            _collect_reachable(doc, param.get("schema"), "request", roles, seen, marks)
        _collect_reachable(doc, _response_schema(op), "response", roles, seen, marks)
    return roles, marks


# --- Classification ------------------------------------------------------------------


def _type_signature(schema: Node) -> Signature | None:
    if "$ref" in schema:
        return ("$ref", schema["$ref"])
    t = schema.get("type")
    if t is not None:
        if isinstance(t, list):
            names: list[str] = cast(list[str], t)
            return tuple(sorted(x for x in names if x != "null"))
        return (t,)
    # pydantic's `X | None` compiles to `anyOf: [...X, {type: null}]` with no top-level `type`
    # key — resolve the signature through the non-null member(s) to match a typed nullable's.
    members = schema.get("anyOf") or schema.get("oneOf")
    if not members:
        return None
    non_null = [m for m in members if m.get("type") != "null"]
    signatures = tuple(sorted(_type_signature(m) or ("unknown",) for m in non_null))
    return signatures[0] if len(signatures) == 1 else signatures


def _is_nullable(schema: Node) -> bool:
    t = schema.get("type")
    if isinstance(t, list) and "null" in t:
        return True
    if schema.get("nullable") is True:
        return True
    return any(member.get("type") == "null" for member in (*schema.get("anyOf", []), *schema.get("oneOf", [])))


def _union_ref_names(schema: Node) -> set[str] | None:
    members = schema.get("anyOf")
    if members is None:
        members = schema.get("oneOf")
    if members is None:
        return None
    return {_ref_name(m["$ref"]) for m in members if "$ref" in m}


def _inline_enum(schema: Node) -> Node | None:
    """The node carrying the inline ``enum`` of a property: itself, or its non-null ``anyOf``/``oneOf`` member."""
    if "enum" in schema:
        return schema
    for member in (*schema.get("anyOf", []), *schema.get("oneOf", [])):
        if member.get("type") != "null" and "enum" in member:
            return member
    return None


def _string_enum_target(base: Node, head: Node, head_schemas: Node) -> Node | None:
    """The string-enum component a ``string`` property is retyped onto, or None when the retype
    is anything else. An inline-enum base (a ``Literal``) qualifies too; its values are diffed."""
    if _type_signature(base) != ("string",):
        return None
    head_sig = _type_signature(head)
    if head_sig is None or head_sig[0] != "$ref" or not isinstance(head_sig[1], str):
        return None
    target = head_schemas.get(_ref_name(head_sig[1]), {})
    return target if target.get("type") == "string" and target.get("enum") else None


def _diff_enum_and_union(
    label: str,
    base: Node,
    head: Node,
    is_request: bool,
    is_response: bool,
    violations: list[Violation],
    *,
    is_open: bool = False,
) -> None:
    """``is_open``: the baseline's clients tolerate a gained value here, so a gain is additive."""
    b_enum, h_enum = base.get("enum"), head.get("enum")
    if b_enum is not None and h_enum is not None:
        gained = set(h_enum) - set(b_enum)
        lost = set(b_enum) - set(h_enum)
        if is_response and gained and not is_open:
            violations.append(_violation(label, f"response enum gained value(s) {sorted(gained)}", base))
        if is_request and lost:
            violations.append(_violation(label, f"request enum lost value(s) {sorted(lost)}", base))

    b_union, h_union = _union_ref_names(base), _union_ref_names(head)
    if b_union is not None and h_union is not None:
        gained_members = h_union - b_union
        if is_response and gained_members:
            violations.append(_violation(label, f"response union gained member(s) {sorted(gained_members)}", base))


def _nested_schema(schema: Node, key: str) -> Node | None:
    """The ``items`` or ``additionalProperties`` schema a property carries itself or on its
    non-null ``anyOf``/``oneOf`` member (``list[X] | None`` compiles to the latter)."""
    nested: Any = schema.get(key)
    if isinstance(nested, dict):
        return cast(Node, nested)
    for member in (*schema.get("anyOf", []), *schema.get("oneOf", [])):
        nested = member.get("type") != "null" and member.get(key)
        if isinstance(nested, dict):
            return cast(Node, nested)
    return None


def _diff_property(
    label: str,
    bp: Node,
    hp: Node,
    is_request: bool,
    is_response: bool,
    violations: list[Violation],
    head_schemas: Node | None,
) -> None:
    """The property rules over one property, then over its array items and map values."""
    target = None if is_request else _string_enum_target(bp, hp, head_schemas or {})
    if target is not None:
        # A response-only narrowing: an older `str` parse accepts every value sent, but an older
        # closed-`Literal` parse only its own, so an inline-enum base still holds the enum-gain rule.
        if not _is_nullable(bp) and _is_nullable(hp):
            violations.append(_violation(label, "response property newly became nullable", bp))
        base_enum = _inline_enum(bp)
        if base_enum is not None:
            _diff_enum_and_union(
                label,
                {"enum": base_enum["enum"], "deprecated": bp.get("deprecated")},
                target,
                is_request,
                is_response,
                violations,
                is_open=base_enum.get(_OPEN_MARK) is True,
            )
        return

    b_sig, h_sig = _type_signature(bp), _type_signature(hp)
    if b_sig is not None and h_sig is not None and b_sig != h_sig:
        if is_response:
            violations.append(_violation(label, "response property type changed", bp))
        if is_request:
            violations.append(_violation(label, "request property type narrowed", bp))

    # Response: newly nullable breaks an old runner's non-Optional parse; losing
    # nullability is safe — a `None`-tolerant runner still parses a never-null value.
    if is_response and not _is_nullable(bp) and _is_nullable(hp):
        violations.append(_violation(label, "response property newly became nullable", bp))
    # Request direction: a property that used to accept null and no longer does breaks a
    # runner still sending `None` for it.
    if is_request and _is_nullable(bp) and not _is_nullable(hp):
        violations.append(_violation(label, "request property dropped nullability", bp))

    _diff_enum_and_union(label, bp, hp, is_request, is_response, violations, is_open=bp.get(_OPEN_MARK) is True)

    # A closed inline enum widened to a bare string gains values an older closed-`Literal` parse refuses.
    base_enum = _inline_enum(bp)
    if (
        is_response
        and base_enum is not None
        and base_enum.get(_OPEN_MARK) is not True
        and _inline_enum(hp) is None
        and h_sig == ("string",)
    ):
        violations.append(_violation(label, "response enum widened to a plain string", bp))

    for key, suffix in (("items", "[]"), ("additionalProperties", "{}")):
        b_nested, h_nested = _nested_schema(bp, key), _nested_schema(hp, key)
        if b_nested is not None and h_nested is not None:
            _diff_property(label + suffix, b_nested, h_nested, is_request, is_response, violations, head_schemas)


def _diff_schema(
    name: str,
    base: Node,
    head: Node,
    is_request: bool,
    is_response: bool,
    violations: list[Violation],
    head_schemas: Node | None = None,
    is_open: bool = False,
) -> None:
    _diff_enum_and_union(name, base, head, is_request, is_response, violations, is_open=is_open)

    base_props: Node = base.get("properties", {})
    head_props: Node = head.get("properties", {})
    base_required = set(base.get("required", []))
    head_required = set(head.get("required", []))
    forbid = base.get("additionalProperties") is False or head.get("additionalProperties") is False

    for prop in base_props:
        if prop in head_props:
            continue
        if is_response:
            violations.append(_violation(f"{name}.{prop}", "response property removed", base_props[prop]))
        elif is_request and forbid:
            violations.append(
                _violation(f"{name}.{prop}", "property removed from a forbid request schema", base_props[prop])
            )

    for prop in head_props:
        if prop not in base_props and is_response and forbid:
            violations.append(
                _violation(f"{name}.{prop}", "property added to a forbid schema a response reaches", base)
            )

    for prop in sorted(set(base_props) & set(head_props)):
        _diff_property(
            f"{name}.{prop}", base_props[prop], head_props[prop], is_request, is_response, violations, head_schemas
        )

    for prop in sorted(head_required - base_required):
        if is_request:
            violations.append(
                _violation(f"{name}.{prop}", "request property became required", base_props.get(prop, {}))
            )
    for prop in sorted(base_required - head_required):
        if is_response:
            violations.append(
                _violation(f"{name}.{prop}", "response property stopped being required", base_props.get(prop, {}))
            )


def _diff_paths_and_methods(base: Node, head: Node, violations: list[Violation], select: Selection) -> None:
    head_paths = head.get("paths", {})
    base_ops = _surface_operations(base, select)
    removed_paths: set[str] = set()
    for path, method, op in base_ops:
        if path not in head_paths:
            if path not in removed_paths:
                removed_paths.add(path)
                path_ops = [other for other_path, _method, other in base_ops if other_path == path]
                deprecated = all(other.get("deprecated") is True for other in path_ops)
                violations.append(Violation("wire", path, "surface path removed", deprecated))
        elif method not in head_paths[path]:
            violations.append(_violation(f"{method.upper()} {path}", "surface method removed", op))


def _parameter_schema(op: Node) -> Node:
    """An operation's parameters as one synthetic request object schema, one property per
    ``(in, name)`` — so the request rules apply to them unchanged."""
    properties: dict[str, Node] = {}
    required: list[str] = []
    for param in op.get("parameters", []):
        key = f"{param.get('in')} {param.get('name')}"
        properties[key] = {**param.get("schema", {}), **({"deprecated": True} if param.get("deprecated") else {})}
        if param.get("required"):
            required.append(key)
    return {"properties": properties, "required": required}


def _diff_parameters(
    base: Node, head: Node, violations: list[Violation], head_schemas: Node, select: Selection
) -> None:
    head_ops = {(path, method): op for path, method, op in _surface_operations(head, select)}
    for path, method, base_op in _surface_operations(base, select):
        head_op = head_ops.get((path, method))
        if head_op is None:
            continue
        _diff_schema(
            f"{method.upper()} {path} parameter",
            _parameter_schema(base_op),
            _parameter_schema(head_op),
            is_request=True,
            is_response=False,
            violations=violations,
            head_schemas=head_schemas,
        )


def classify_spec_diff(
    base: Node, head: Node, select: Selection = is_wire_operation, surface: str = "wire"
) -> list[Violation]:
    """Every breaking change (bzh:fleet-wire-additive's declared classes) from ``base`` to
    ``head`` on the operations ``select`` keeps, reported under ``surface``. An empty list
    means the change is additive. ``deprecated`` is whether the baseline marked the removed
    or changed thing deprecated; whether a response enum is open is read from ``base`` alone."""
    violations: list[Violation] = []
    _diff_paths_and_methods(base, head, violations, select)

    base_roles = _reachable_schemas(base, select)
    head_roles = _reachable_schemas(head, select)
    base_open = _open_components(base, select)
    base_schemas = _schema_components(base)
    head_schemas = _schema_components(head)
    _diff_parameters(base, head, violations, head_schemas, select)

    for name in sorted(base_roles):
        if name not in head_schemas:
            violations.append(_violation(name, "component schema deleted", base_schemas.get(name, {})))
            continue
        if name not in head_roles:
            violations.append(_violation(name, "no longer reachable from the surface", base_schemas.get(name, {})))
            continue
        role = base_roles[name] | head_roles[name]
        _diff_schema(
            name,
            base_schemas.get(name, {}),
            head_schemas[name],
            is_request="request" in role,
            is_response="response" in role,
            violations=violations,
            head_schemas=head_schemas,
            is_open=name in base_open,
        )
    return [replace(v, surface=surface) for v in violations]
