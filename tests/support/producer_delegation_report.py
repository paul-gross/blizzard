"""Report evidence; pass --check-closure to fail on any unresolved discovery or call.

The normal baseline detects changes, not closure. This opt-in check is currently expected
to fail and does not establish coverage beyond the traversal's capabilities.
"""

import argparse

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.hub.app import create_app_for_export
from tests.support.producer_delegation import operation_evidence
from tests.support.repo_files import repo_root
from tests.support.runner_refusal_discovery import match_operations, route_inventory, runner_boundary


def render_report(
    evidence: dict[tuple[str, str], tuple[set[str], set[str]]],
    discovery_unknowns: list[str],
) -> str:
    lines = ["Delegation evidence (candidates are not proof of complete coverage)"]
    lines.append(f"Discovery unknowns: {len(discovery_unknowns)}")
    lines.extend(f"  {item}" for item in sorted(discovery_unknowns))
    for (method, path), (candidates, unknowns) in sorted(evidence.items()):
        lines.append(f"{method} {path}: {len(unknowns)} unresolved, {len(candidates)} producer candidates")
        lines.extend(f"  unresolved: {item}" for item in sorted(unknowns))
        lines.extend(f"  candidate: {item}" for item in sorted(candidates))
    return "\n".join(lines)


def closure_failures(
    evidence: dict[tuple[str, str], tuple[set[str], set[str]]], discovery_unknowns: list[str]
) -> list[str]:
    failures = [f"discovery: {item}" for item in sorted(discovery_unknowns)]
    failures.extend(
        f"{method} {path}: {item}"
        for (method, path), (_, unknowns) in sorted(evidence.items())
        for item in sorted(unknowns)
    )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-closure", action="store_true", help="fail if any evidence remains unresolved")
    args = parser.parse_args()
    reaches, boundary_unknowns = runner_boundary(repo_root())
    routes, route_unknowns = route_inventory(create_app_for_export(packaged_flag_registry()))
    matched, missing = match_operations(reaches, set(routes))
    operations = {operation for group in matched.values() for operation in group}
    unknowns = [f"runner boundary: {item}" for item in boundary_unknowns]
    unknowns.extend(f"route inventory: {item}" for item in route_unknowns)
    unknowns.extend(f"unmatched operation: {item}" for item in sorted(missing))
    evidence = {op: operation_evidence(routes[op]) for op in sorted(operations)}
    print(render_report(evidence, unknowns))
    if args.check_closure:
        failures = closure_failures(evidence, unknowns)
        print(f"\nOpt-in closure check: {len(failures)} unresolved (independent of the change baseline)")
        return int(bool(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
