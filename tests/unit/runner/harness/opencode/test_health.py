"""OpenCode 1's manifest declarations are filtered and unioned by probe."""

from __future__ import annotations

from blizzard.runner.harness.health import DeclaredDegradation
from blizzard.runner.harness.json_value import JsonValue
from blizzard.runner.harness.opencode.health import OpenCodeHealthProbe
from tests.support.child_launch import version_probe


class _Corpus:
    def __init__(self, manifests: dict[str, dict[str, JsonValue]]) -> None:
        self.manifests = manifests

    def versions(self, harness_id: str) -> tuple[str, ...]:
        return tuple(self.manifests)

    def manifest(self, harness_id: str, version: str) -> dict[str, JsonValue] | None:
        return self.manifests.get(version)


def test_manifest_entries_are_validated_and_union_keeps_first_per_probe() -> None:
    corpus = _Corpus(
        {
            "1.18.26": {
                "declared_degradations": [
                    {"probe": "root_hook", "summary": "later"},
                    {"probe": "usage_cost", "summary": "cost"},
                ]
            },
            "1.18.25": {
                "declared_degradations": [
                    {"probe": "root_hook", "summary": "first"},
                    {"probe": "not_a_probe", "summary": "unknown"},
                    {"probe": "", "summary": "empty"},
                    {"probe": "usage_cost", "summary": " "},
                    {"probe": 1, "summary": "invalid"},
                    {"probe": "usage_cost", "summary": 1},
                    None,
                ]
            },
            "2.1.4": {"declared_degradations": [{"probe": "child_sessions", "summary": "out of range"}]},
        }
    )
    probe = OpenCodeHealthProbe(auth_path=None, corpus=corpus, versions=version_probe())
    assert probe.declared_degradations() == (
        DeclaredDegradation("root_hook", "first"),
        DeclaredDegradation("usage_cost", "cost"),
    )


def test_non_list_manifest_declarations_are_ignored() -> None:
    probe = OpenCodeHealthProbe(
        auth_path=None, corpus=_Corpus({"1.18.25": {"declared_degradations": {}}}), versions=version_probe()
    )
    assert probe.declared_degradations() == ()
