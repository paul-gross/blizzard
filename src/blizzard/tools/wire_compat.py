"""Fail on a breaking change to the hub↔runner wire surface (bzh:fleet-wire-additive).

``classify_spec_diff`` is pure, over two parsed OpenAPI documents; the rest resolves a
git or GitHub baseline and walks commits one first-parent step at a time, attributing
a break to its landing — acknowledged, not failed, by a Conventional Commits ``!``.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import click

from blizzard.foundation.roles import domain_model

#: The declared surface (docs/versioning.md's hub↔runner skew row): fleet calls, plus federation.
_SURFACE_PREFIX = "/api/fleet"
_SURFACE_EXTRA_PATHS = ("/api/auth/jwks.json", "/api/auth/authorize")
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")

_BREAKING_COMMIT_MARKER = re.compile(r"^\w+(\([^)]*\))?!:")

_SPEC_PATH = "openapi/hub.openapi.json"


@domain_model
@dataclass(frozen=True)
class Violation:
    """One breaking-change finding: the surface path or schema it names, and why."""

    where: str
    reason: str

    def __str__(self) -> str:
        return f"{self.where}: {self.reason}"


class WireCompatError(RuntimeError):
    """A baseline could not be resolved, or a git/gh call needed to check it failed."""


# --- Surface + schema reachability -------------------------------------------------


def _is_surface_path(path: str) -> bool:
    return path.startswith(_SURFACE_PREFIX) or path in _SURFACE_EXTRA_PATHS


def _surface_operations(doc: dict) -> list[tuple[str, str, dict]]:
    operations = []
    for path, methods in doc.get("paths", {}).items():
        if not _is_surface_path(path):
            continue
        for method, op in methods.items():
            if method in _HTTP_METHODS:
                operations.append((path, method, op))
    return operations


def _schema_components(doc: dict) -> dict:
    return doc.get("components", {}).get("schemas", {})


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _request_schema(op: dict) -> dict | None:
    body = op.get("requestBody")
    if not body:
        return None
    media = body.get("content", {}).get("application/json")
    return media.get("schema") if media else None


def _response_schema(op: dict) -> dict | None:
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
    doc: dict, node: dict | None, role: str, roles: dict[str, set[str]], seen: set[tuple[str, str]]
) -> None:
    if node is None:
        return
    ref = node.get("$ref")
    if ref:
        name = _ref_name(ref)
        roles.setdefault(name, set()).add(role)
        key = (name, role)
        if key in seen:
            return
        seen.add(key)
        _collect_reachable(doc, _schema_components(doc).get(name), role, roles, seen)
        return
    for sub in node.get("properties", {}).values():
        _collect_reachable(doc, sub, role, roles, seen)
    if "items" in node:
        _collect_reachable(doc, node["items"], role, roles, seen)
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in node.get(key, []):
            _collect_reachable(doc, sub, role, roles, seen)


def _reachable_schemas(doc: dict) -> dict[str, set[str]]:
    """Component schema name -> the surface roles ("request", "response") that reach it.

    ``seen`` is keyed by (schema, role): a schema reached by both roles must have its
    descendants walked once per role, not just whichever role's walk arrives first.
    """
    roles: dict[str, set[str]] = {}
    seen: set[tuple[str, str]] = set()
    for _path, _method, op in _surface_operations(doc):
        _collect_reachable(doc, _request_schema(op), "request", roles, seen)
        _collect_reachable(doc, _response_schema(op), "response", roles, seen)
    return roles


# --- Classification ------------------------------------------------------------------


def _type_signature(schema: dict) -> tuple | None:
    if "$ref" in schema:
        return ("$ref", schema["$ref"])
    t = schema.get("type")
    if t is not None:
        if isinstance(t, list):
            return tuple(sorted(x for x in t if x != "null"))
        return (t,)
    # pydantic's `X | None` compiles to `anyOf: [...X, {type: null}]` with no top-level `type`
    # key — resolve the signature through the non-null member(s) to match a typed nullable's.
    members = schema.get("anyOf") or schema.get("oneOf")
    if not members:
        return None
    non_null = [m for m in members if m.get("type") != "null"]
    signatures = tuple(sorted(_type_signature(m) or ("unknown",) for m in non_null))
    return signatures[0] if len(signatures) == 1 else signatures


def _is_nullable(schema: dict) -> bool:
    t = schema.get("type")
    if isinstance(t, list) and "null" in t:
        return True
    if schema.get("nullable") is True:
        return True
    return any(member.get("type") == "null" for member in (*schema.get("anyOf", []), *schema.get("oneOf", [])))


def _union_ref_names(schema: dict) -> set[str] | None:
    members = schema.get("anyOf")
    if members is None:
        members = schema.get("oneOf")
    if members is None:
        return None
    return {_ref_name(m["$ref"]) for m in members if "$ref" in m}


def _inline_enum(schema: dict) -> list | None:
    """The inline ``enum`` a property carries itself or on its non-null ``anyOf``/``oneOf`` member."""
    if "enum" in schema:
        return schema["enum"]
    for member in (*schema.get("anyOf", []), *schema.get("oneOf", [])):
        if member.get("type") != "null" and "enum" in member:
            return member["enum"]
    return None


def _string_enum_target(base: dict, head: dict, head_schemas: dict) -> dict | None:
    """The string-enum component a ``string`` property is retyped onto, or None when the retype
    is anything else. An inline-enum base (a ``Literal``) qualifies too; its values are diffed."""
    if _type_signature(base) != ("string",):
        return None
    head_sig = _type_signature(head)
    if head_sig is None or head_sig[0] != "$ref":
        return None
    target = head_schemas.get(_ref_name(head_sig[1]), {})
    return target if target.get("type") == "string" and target.get("enum") else None


def _diff_enum_and_union(
    label: str, base: dict, head: dict, is_request: bool, is_response: bool, violations: list[Violation]
) -> None:
    b_enum, h_enum = base.get("enum"), head.get("enum")
    if b_enum is not None and h_enum is not None:
        gained = set(h_enum) - set(b_enum)
        lost = set(b_enum) - set(h_enum)
        if is_response and gained:
            violations.append(Violation(label, f"response enum gained value(s) {sorted(gained)}"))
        if is_request and lost:
            violations.append(Violation(label, f"request enum lost value(s) {sorted(lost)}"))

    b_union, h_union = _union_ref_names(base), _union_ref_names(head)
    if b_union is not None and h_union is not None:
        gained_members = h_union - b_union
        if is_response and gained_members:
            violations.append(Violation(label, f"response union gained member(s) {sorted(gained_members)}"))


def _diff_schema(
    name: str,
    base: dict,
    head: dict,
    is_request: bool,
    is_response: bool,
    violations: list[Violation],
    head_schemas: dict | None = None,
) -> None:
    _diff_enum_and_union(name, base, head, is_request, is_response, violations)

    base_props: dict = base.get("properties", {})
    head_props: dict = head.get("properties", {})
    base_required = set(base.get("required", []))
    head_required = set(head.get("required", []))
    forbid = base.get("additionalProperties") is False or head.get("additionalProperties") is False

    for prop in base_props:
        if prop in head_props:
            continue
        if is_response:
            violations.append(Violation(f"{name}.{prop}", "response property removed"))
        elif is_request and forbid:
            violations.append(Violation(f"{name}.{prop}", "property removed from a forbid request schema"))

    for prop in head_props:
        if prop not in base_props and is_response and forbid:
            violations.append(Violation(f"{name}.{prop}", "property added to a forbid schema a response reaches"))

    for prop in sorted(set(base_props) & set(head_props)):
        bp, hp = base_props[prop], head_props[prop]
        label = f"{name}.{prop}"

        target = None if is_request else _string_enum_target(bp, hp, head_schemas or {})
        if target is not None:
            # A response-only narrowing: an older `str` parse accepts every value sent, but an older
            # closed-`Literal` parse only its own, so an inline-enum base still holds the enum-gain rule.
            if not _is_nullable(bp) and _is_nullable(hp):
                violations.append(Violation(label, "response property newly became nullable"))
            base_enum = _inline_enum(bp)
            if base_enum is not None:
                _diff_enum_and_union(label, {"enum": base_enum}, target, is_request, is_response, violations)
            continue

        b_sig, h_sig = _type_signature(bp), _type_signature(hp)
        if b_sig is not None and h_sig is not None and b_sig != h_sig:
            if is_response:
                violations.append(Violation(label, "response property type changed"))
            if is_request:
                violations.append(Violation(label, "request property type narrowed"))

        # Response: newly nullable breaks an old runner's non-Optional parse; losing
        # nullability is safe — a `None`-tolerant runner still parses a never-null value.
        if is_response and not _is_nullable(bp) and _is_nullable(hp):
            violations.append(Violation(label, "response property newly became nullable"))
        # Request direction: a property that used to accept null and no longer does breaks a
        # runner still sending `None` for it.
        if is_request and _is_nullable(bp) and not _is_nullable(hp):
            violations.append(Violation(label, "request property dropped nullability"))

        _diff_enum_and_union(label, bp, hp, is_request, is_response, violations)

    for prop in sorted(head_required - base_required):
        if is_request:
            violations.append(Violation(f"{name}.{prop}", "request property became required"))
    for prop in sorted(base_required - head_required):
        if is_response:
            violations.append(Violation(f"{name}.{prop}", "response property stopped being required"))


def _diff_paths_and_methods(base: dict, head: dict, violations: list[Violation]) -> None:
    base_paths = {p: m for p, m in base.get("paths", {}).items() if _is_surface_path(p)}
    head_paths = {p: m for p, m in head.get("paths", {}).items() if _is_surface_path(p)}
    for path, methods in base_paths.items():
        if path not in head_paths:
            violations.append(Violation(path, "surface path removed"))
            continue
        for method in methods:
            if method in _HTTP_METHODS and method not in head_paths[path]:
                violations.append(Violation(f"{method.upper()} {path}", "surface method removed"))


def classify_spec_diff(base: dict, head: dict) -> list[Violation]:
    """Every breaking change (bzh:fleet-wire-additive's declared classes) from ``base`` to
    ``head`` on the declared hub↔runner surface. An empty list means the change is
    additive."""
    violations: list[Violation] = []
    _diff_paths_and_methods(base, head, violations)

    base_roles = _reachable_schemas(base)
    head_roles = _reachable_schemas(head)
    base_schemas = _schema_components(base)
    head_schemas = _schema_components(head)

    for name in sorted(base_roles):
        if name not in head_schemas:
            violations.append(Violation(name, "component schema deleted"))
            continue
        if name not in head_roles:
            violations.append(Violation(name, "no longer reachable from the surface"))
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
        )
    return violations


# --- Git / gh plumbing ----------------------------------------------------------------


def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise WireCompatError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _spec_at(ref: str, cwd: Path) -> dict:
    return json.loads(_run_git(["show", f"{ref}:{_SPEC_PATH}"], cwd))


def resolve_merge_base_baseline(against: str, cwd: Path) -> str:
    return _run_git(["merge-base", "HEAD", against], cwd).strip()


#: How many of the newest ``push.yml`` runs on ``master`` are searched for a successful one.
_DEPLOYED_SEARCH_DEPTH = 100


def resolve_deployed_baseline(repo: str, cwd: Path) -> str:
    """The head commit of the newest successful ``push.yml`` run on ``master`` — what the
    hosted hub's ``edge`` currently runs. Success is filtered here, not by ``--status``:
    GitHub answers ``branch`` and ``status`` together from a stale index."""
    result = subprocess.run(
        [
            "gh",
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            "push.yml",
            "--branch",
            "master",
            "--limit",
            str(_DEPLOYED_SEARCH_DEPTH),
            "--json",
            "headSha,conclusion",
        ],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    if result.returncode != 0:
        raise WireCompatError(f"gh run list failed: {result.stderr.strip()}")
    runs = json.loads(result.stdout or "[]")
    for run in runs:
        if run.get("conclusion") == "success":
            return run["headSha"]
    raise WireCompatError(f"no successful push.yml run among the newest {_DEPLOYED_SEARCH_DEPTH} on {repo}@master")


def _first_parent_steps(baseline: str, cwd: Path) -> list[str]:
    out = _run_git(["rev-list", "--first-parent", "--reverse", f"{baseline}..HEAD"], cwd)
    return [line for line in out.splitlines() if line]


def _is_acknowledged(prev: str, curr: str, cwd: Path) -> bool:
    landed = [line for line in _run_git(["rev-list", f"{prev}..{curr}"], cwd).splitlines() if line]
    for sha in landed:
        subject = _run_git(["log", "-1", "--format=%s", sha], cwd).strip()
        if _BREAKING_COMMIT_MARKER.match(subject):
            return True
    return False


def check_history(baseline_commit: str, cwd: Path, *, echo=click.echo) -> bool:
    """Walk ``baseline_commit..HEAD`` first-parent; true iff every step is additive or
    acknowledged by a ``!``-marked landing. Checked net-first, skipping the per-step walk
    when the whole range is additive — see ``docs/ci.md`` for the revert recovery path.
    """
    steps = _first_parent_steps(baseline_commit, cwd)
    if not steps:
        echo(f"wire-compat: HEAD is already at baseline {baseline_commit[:9]}, nothing to check.")
        return True

    if not classify_spec_diff(_spec_at(baseline_commit, cwd), _spec_at(steps[-1], cwd)):
        echo(f"wire-compat: net change since baseline {baseline_commit[:9]} is additive, nothing to check.")
        return True

    ok = True
    prev = baseline_commit
    for curr in steps:
        violations = classify_spec_diff(_spec_at(prev, cwd), _spec_at(curr, cwd))
        if violations:
            acknowledged = _is_acknowledged(prev, curr, cwd)
            if acknowledged:
                echo(f"wire-compat: ACKNOWLEDGED breaking change at {curr[:9]}:")
            else:
                ok = False
                echo(f"wire-compat: UNACKNOWLEDGED breaking change at {curr[:9]}:")
            for v in violations:
                echo(f"  - {v}")
            if not acknowledged:
                echo("  Mark the landing commit's subject with '!' (e.g. 'feat!: ...') to acknowledge.")
        prev = curr
    return ok


@click.command()
@click.option(
    "--baseline",
    type=click.Choice(["merge-base", "deployed"]),
    default="merge-base",
    show_default=True,
    help="merge-base: diff against the PR's merge-base with --against. deployed: diff "
    "against the last commit whose push.yml run succeeded (what edge currently runs).",
)
@click.option("--against", default="origin/master", show_default=True, help="merge-base mode's comparison ref.")
@click.option("--repo", default="paul-gross/blizzard", show_default=True, help="deployed mode's GitHub repo.")
@click.option(
    "--base-spec",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Diff two spec files directly, bypassing git and the ! acknowledgement walk.",
)
@click.option("--head-spec", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
def main(baseline: str, against: str, repo: str, base_spec: Path | None, head_spec: Path | None) -> None:
    if base_spec or head_spec:
        if not (base_spec and head_spec):
            raise click.UsageError("--base-spec and --head-spec must be given together")
        violations = classify_spec_diff(json.loads(base_spec.read_text()), json.loads(head_spec.read_text()))
        for v in violations:
            click.echo(f"  - {v}")
        raise SystemExit(1 if violations else 0)

    cwd = Path.cwd()
    baseline_commit = (
        resolve_merge_base_baseline(against, cwd) if baseline == "merge-base" else resolve_deployed_baseline(repo, cwd)
    )
    ok = check_history(baseline_commit, cwd)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
