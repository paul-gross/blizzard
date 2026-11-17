"""The Fibonacci weights, the suite registry's completeness over every workflow job, the inventory refresh over each
evidence form, and the greedy placement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.shard_weights import (
    INVENTORY_PATH,
    SEP,
    SUITES,
    WORKFLOWS_DIR,
    Measured,
    SuiteWeights,
    TimingRecorder,
    dump_inventory,
    fibonacci_weight,
    load_inventory,
    place,
    plan,
    plan_playwright,
    refresh,
    unclassified_jobs,
)


@pytest.mark.parametrize(
    ("seconds", "weight"),
    [
        (0.0, 1),
        (0.4, 1),
        (0.99, 1),
        (1.0, 1),
        (2.0, 2),
        (3.0, 3),
        (13.0, 13),
        (1.4, 1),
        (6.0, 5),
        (7.0, 8),
        (1.5, 2),
        (2.5, 3),
        (4.0, 5),
        (6.5, 8),
        (10.5, 13),
        (1000.0, 987),
        (1300.0, 1597),
    ],
)
def test_seconds_round_to_the_nearest_fibonacci_weight_ties_up(seconds: float, weight: int) -> None:
    assert fibonacci_weight(seconds) == weight


def test_every_workflow_job_is_a_registered_suite_or_a_declared_non_test_job() -> None:
    assert unclassified_jobs(WORKFLOWS_DIR) == []


def test_an_unclassified_job_fails_completeness(tmp_path: Path) -> None:
    (tmp_path / "gate.yml").write_text(
        "on: push\njobs:\n  python-tests:\n    steps: []\n  new-suite:\n    steps: []\n"
        "  called:\n    uses: ./.github/workflows/other.yml\n"
    )
    found = unclassified_jobs(tmp_path, SUITES[:1], frozenset({"gate.yml:gone"}))
    assert found == ["gate.yml:new-suite", "gate.yml:gone (no such job)"]


def test_the_committed_inventory_holds_every_registered_suite() -> None:
    inventory = load_inventory(INVENTORY_PATH)
    assert set(inventory) == {entry.id for entry in SUITES}
    for weights in inventory.values():
        assert weights.missing or weights.entries
        assert weights.fallback >= 1


def test_placement_puts_the_heaviest_first_onto_the_lightest_shard() -> None:
    shards = place({"a": 8, "b": 5, "c": 5, "d": 3, "e": 1, "f": 1}, 3)
    assert shards == [["a"], ["b", "d"], ["c", "e", "f"]]


def test_placement_breaks_weight_ties_by_identity_and_shard_ties_by_index() -> None:
    assert place({"z": 1, "y": 1, "x": 1}, 2) == [["x", "z"], ["y"]]
    assert place({}, 2) == [[], []]


def test_plan_covers_the_discovered_cases_once_by_module_and_ignores_stale_entries() -> None:
    inventory = {
        "gate-pytest": SuiteWeights(
            2, {"tests/a.py::t1": Measured(30.0, 34), "tests/gone.py::t": Measured(90.0, 89)}, None
        )
    }
    cases = ["tests/a.py::t1", "tests/a.py::t2", "tests/b.py::t", "tests/c.py::t"]
    shards = plan(inventory, "gate-pytest", cases)
    assert len(shards) == 6
    placed = [unit for shard in shards for unit in shard]
    assert sorted(placed) == ["tests/a.py", "tests/b.py", "tests/c.py"]
    assert shards[0] == ["tests/a.py"]


def test_plan_playwright_keeps_a_project_and_file_on_one_shard() -> None:
    identities = [
        f"service-hub{SEP}service/hub/a.spec.ts{SEP}one",
        f"service-hub{SEP}service/hub/a.spec.ts{SEP}two",
        f"harness{SEP}harness/checks/b.spec.ts{SEP}one",
    ]
    owned = [plan_playwright({}, identities, index) for index in range(1, 7)]
    assert sorted(unit for shard in owned for unit in shard) == [
        f"harness{SEP}harness/checks/b.spec.ts",
        f"service-hub{SEP}service/hub/a.spec.ts",
    ]


def test_the_recorder_sums_each_phase_of_a_test(tmp_path: Path) -> None:
    recorder = TimingRecorder(tmp_path / "out" / "timings.json")
    for nodeid, seconds in [("t::a", 0.1), ("t::a", 2.0), ("t::a", 0.2), ("t::b", 1.0)]:
        recorder.record(nodeid, seconds)
    recorder.write()
    assert json.loads((tmp_path / "out" / "timings.json").read_text()) == {"t::a": 2.3, "t::b": 1.0}


def _playwright_report(tests_dir: Path) -> dict[str, object]:
    def test(project: str, ms: float, status: str = "passed") -> dict[str, object]:
        return {"projectName": project, "results": [{"duration": ms, "status": status}]}

    return {
        "config": {"rootDir": str(tests_dir / "service" / "hub")},
        "suites": [
            {
                "title": "a.spec.ts",
                "specs": [{"title": "top", "file": "a.spec.ts", "tests": [test("service-hub", 400)]}],
                "suites": [
                    {
                        "title": "group",
                        "specs": [
                            {"title": "same", "file": "a.spec.ts", "tests": [test("service-hub", 2500)]},
                            {"title": "same", "file": "a.spec.ts", "tests": [test("service-hub", 5000)]},
                            {"title": "flaky", "file": "a.spec.ts", "tests": [test("service-hub", 1, "failed")]},
                        ],
                    }
                ],
            }
        ],
    }


def _evidence(tmp_path: Path) -> Path:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "timings-gate-pytest-1.json").write_text(json.dumps({"tests/a.py::t": 0.4, "tests/a.py::u": 4.0}))
    (evidence / "timings-gate-pytest-2.json").write_text(json.dumps({"tests/b.py::t": 7.0}))
    (evidence / "report-service-playwright-3.json").write_text(json.dumps(_playwright_report(tmp_path / "tests")))
    (evidence / "units.json").write_text(json.dumps({"web-test-hub": 41.0}))
    jobs = [
        {
            "name": "upper-tiers / hub image smoke (blizzard:image-smoke)",
            "conclusion": "success",
            "startedAt": "2026-11-01T10:00:00Z",
            "completedAt": "2026-11-01T10:02:00Z",
        },
        {
            "name": "upper-tiers / reference compose smoke (blizzard:compose-smoke)",
            "conclusion": "failure",
            "startedAt": "2026-11-01T10:00:00Z",
            "completedAt": "2026-11-01T10:09:00Z",
        },
    ]
    (evidence / "jobs.json").write_text(json.dumps({"jobs": jobs}))
    return evidence


def test_refresh_weighs_each_evidence_form_under_stable_identities(tmp_path: Path) -> None:
    result = refresh(_evidence(tmp_path), {}, tmp_path / "tests")
    gate = result.inventory["gate-pytest"]
    assert {k: v.weight for k, v in gate.entries.items()} == {
        "tests/a.py::t": 1,
        "tests/a.py::u": 5,
        "tests/b.py::t": 8,
    }
    assert gate.fallback == 5
    playwright = result.inventory["service-playwright"].entries
    assert {k: v.seconds for k, v in playwright.items()} == {
        f"service-hub{SEP}service/hub/a.spec.ts{SEP}top": 0.4,
        f"service-hub{SEP}service/hub/a.spec.ts{SEP}group{SEP}same": 2.5,
        f"service-hub{SEP}service/hub/a.spec.ts{SEP}group{SEP}same #2": 5.0,
    }
    assert result.inventory["web-test-hub"].entries["web-test-hub"] == Measured(41.0, 34)
    assert result.inventory["image-smoke"].entries["image-smoke"] == Measured(120.0, 144)
    assert result.inventory["compose-smoke"].missing == "no timing evidence yet"
    assert result.inventory["web-test-fleet"].missing is not None
    assert "compose-smoke" in result.unmeasured
    assert "gate-pytest" not in result.unmeasured


def test_refresh_is_reproducible(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path)
    first = dump_inventory(refresh(evidence, {}, tmp_path / "tests").inventory)
    second = dump_inventory(refresh(evidence, {}, tmp_path / "tests").inventory)
    assert first == second
    path = tmp_path / "inventory.json"
    path.write_text(first)
    assert dump_inventory(load_inventory(path)) == first


def test_refresh_reports_stale_entries_and_keeps_an_unmeasured_suite(tmp_path: Path) -> None:
    previous = {
        "gate-pytest": SuiteWeights(1, {"tests/removed.py::t": Measured(3.0, 3)}),
        "store-baseline": SuiteWeights(2, {"tests/store_baseline/x.py::t": Measured(2.0, 2)}),
    }
    result = refresh(_evidence(tmp_path), previous, tmp_path / "tests")
    assert result.stale == {"gate-pytest": ["tests/removed.py::t"]}
    assert "tests/removed.py::t" not in result.inventory["gate-pytest"].entries
    assert "store-baseline" in result.unmeasured
    assert result.inventory["store-baseline"] == previous["store-baseline"]


def test_an_unmeasured_case_weighs_its_suite_fallback() -> None:
    inventory = {"service-pytest": SuiteWeights(3, {"tests/service/a.py::t": Measured(8.0, 8)})}
    shards = plan(
        inventory, "service-pytest", ["tests/service/a.py::t", "tests/service/b.py::t", "tests/service/c.py::t"]
    )
    assert shards == [["tests/service/a.py::t"], ["tests/service/b.py::t"], ["tests/service/c.py::t"]]
    assert plan({}, "service-pytest", ["x::a", "x::b", "x::c", "x::d"]) == [["x::a", "x::d"], ["x::b"], ["x::c"]]
