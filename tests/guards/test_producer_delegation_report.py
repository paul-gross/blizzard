"""Delegation reports retain and order all evidence."""

from tests.support.producer_delegation_report import render_report


def test_report_orders_operations_and_evidence_without_hiding_unknowns() -> None:
    evidence: dict[tuple[str, str], tuple[set[str], set[str]]] = {
        ("POST", "/z"): ({"producer b", "producer a"}, {"unsupported dependency: x", "unresolved call y"}),
        ("GET", "/a"): (set(), {"external callable: z"}),
    }
    result = render_report(evidence, ["route inventory: unknown", "runner boundary: unknown"])
    assert result == "\n".join(
        [
            "Delegation evidence (candidates are not proof of complete coverage)",
            "Discovery unknowns: 2",
            "  route inventory: unknown",
            "  runner boundary: unknown",
            "GET /a: 1 unresolved, 0 producer candidates",
            "  unresolved: external callable: z",
            "POST /z: 2 unresolved, 2 producer candidates",
            "  unresolved: unresolved call y",
            "  unresolved: unsupported dependency: x",
            "  candidate: producer a",
            "  candidate: producer b",
        ]
    )
    assert result == render_report(
        dict(reversed(list(evidence.items()))), list(reversed(["route inventory: unknown", "runner boundary: unknown"]))
    )
