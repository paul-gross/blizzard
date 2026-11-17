"""Measured test durations, their Fibonacci weights, and the greedy placement that shards by them.

The one home of the rounding, the suite registry (every suite CI runs, the workflow jobs that run it, and whether it is
a case suite or one indivisible scripted unit), the ``tests/shard-weights.json`` inventory, and the placement. Run as
``uv run python -m tests.support.shard_weights <refresh|plan-playwright|report>``; ``docs/ci.md`` owns how and when.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import statistics
import subprocess
import sys
import tempfile
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from tests.support.ci_workflows import load_workflow
from tests.support.repo_files import repo_root

#: Names the file the pytest timing recorder writes; unset records nothing.
TIMINGS_ENV = "BLIZZARD_TEST_TIMINGS"

INVENTORY_PATH = repo_root() / "tests" / "shard-weights.json"
WORKFLOWS_DIR = repo_root() / ".github" / "workflows"
TESTS_DIR = repo_root() / "tests"

#: Playwright's title-path separator, as its ``--list`` and ``--test-list`` lines write it.
SEP = " \u203a "

#: The unit-suite evidence file in a local evidence directory: ``{suite id: wall seconds}``.
UNITS_FILE = "units.json"

type Kind = Literal["cases", "unit"]
type Evidence = Literal["pytest", "playwright", "unit"]
type Pool = Literal["file", "test", "playwright"]


@dataclass(frozen=True)
class Suite:
    """One suite CI runs, in one configuration.

    ``jobs`` names each ``<workflow file>:<job id>`` that runs it; ``ci_job`` is the ``fnmatch`` pattern its job's
    display name matches in ``gh run view --json jobs`` (a unit suite only; ``None`` when CI's job record carries more
    than this suite, so only a local measurement can weigh it). ``pool``/``shards`` name the shard pool, if any.
    """

    id: str
    kind: Kind
    evidence: Evidence
    jobs: tuple[str, ...]
    ci_job: str | None = None
    pool: Pool | None = None
    shards: int = 1


SUITES: tuple[Suite, ...] = (
    Suite("gate-pytest", "cases", "pytest", ("gate.yml:python-tests",), pool="file", shards=6),
    Suite("refusal-census", "unit", "unit", ("gate.yml:refusal-census",), "*runner refusal aggregate coverage census"),
    Suite("web-test-hub", "unit", "unit", ("gate.yml:frontend",)),
    Suite("web-test-runner", "unit", "unit", ("gate.yml:frontend",)),
    Suite("web-test-fleet", "unit", "unit", ("gate.yml:frontend",)),
    Suite("web-story-render", "unit", "unit", ("gate.yml:frontend",)),
    Suite("exemplar-check", "unit", "unit", ("gate.yml:exemplars",), "*exemplar check (web)"),
    Suite("service-pytest", "cases", "pytest", ("upper-tiers.yml:service-tier",), pool="test", shards=3),
    Suite(
        "service-playwright",
        "cases",
        "playwright",
        ("upper-tiers.yml:service-playwright",),
        pool="playwright",
        shards=6,
    ),
    Suite("crash-sweep-bounded", "cases", "pytest", ("upper-tiers.yml:crash-sweep",)),
    Suite("crash-sweep-full", "cases", "pytest", ("upper-tiers.yml:crash-sweep",)),
    Suite("ui", "cases", "playwright", ("upper-tiers.yml:ui",)),
    Suite("journey", "cases", "playwright", ("upper-tiers.yml:journey",)),
    Suite("image-smoke", "unit", "unit", ("upper-tiers.yml:image-smoke",), "*hub image smoke (blizzard:image-smoke)"),
    Suite(
        "compose-smoke", "unit", "unit", ("upper-tiers.yml:compose-smoke",), "*compose smoke (blizzard:compose-smoke)"
    ),
    Suite("isolation-declared", "cases", "playwright", ("isolation-cross-check.yml:declared",)),
    Suite("isolation-raised", "cases", "playwright", ("isolation-cross-check.yml:raised",)),
    Suite(
        "postgres-uow-concurrency",
        "unit",
        "unit",
        ("postgres-uow-concurrency.yml:postgres-uow-concurrency",),
        "*hub writers racing on Postgres*",
    ),
    Suite("rehearsal-sqlite", "unit", "unit", ("rehearsal.yml:leg",), "*rehearse * on sqlite (blizzard:rehearsal)"),
    Suite("rehearsal-postgres", "unit", "unit", ("rehearsal.yml:leg",), "*rehearse * on postgres (blizzard:rehearsal)"),
    Suite("store-baseline", "cases", "pytest", ("store-baseline.yml:store-baseline",)),
)

#: Every workflow job that runs no test suite — checks, aggregators, builds, and publishing. A job calling another
#: workflow (``uses: ./.github/workflows/…``) is classified by that workflow's own jobs and needs no entry.
NON_TEST_JOBS: frozenset[str] = frozenset(
    {
        "gate.yml:python-quality",
        "gate.yml:repo-checks",
        "gate.yml:python-tests-result",
        "gate.yml:playwright-static",
        "gate.yml:compat",
        "gate.yml:process-ref-lint",
        "isolation-cross-check.yml:mock-ref",
        "isolation-cross-check.yml:compare",
        "pr.yml:versions-changed",
        "pr.yml:store-baseline-result",
        "pr.yml:release-cut",
        "pr.yml:cut-wheel",
        "pr.yml:rehearsal-result",
        "pr.yml:line-provenance",
        "push.yml:proven-against",
        "push.yml:dev-build",
        "push.yml:dev-image",
        "rehearsal.yml:releases",
        "release.yml:proof",
        "release.yml:compat",
        "release.yml:flag-lifecycle",
        "release.yml:wheel",
        "release.yml:build",
        "release.yml:publish",
        "release.yml:verify",
        "upper-tiers.yml:mock-ref",
        "upper-tiers.yml:service-tier-result",
    }
)

_SUITES_BY_ID = {suite.id: suite for suite in SUITES}


def suite(suite_id: str) -> Suite:
    return _SUITES_BY_ID[suite_id]


# --- rounding -------------------------------------------------------------------------------------------------------


def fibonacci_weight(seconds: float) -> int:
    """The Fibonacci number (1, 2, 3, 5, 8, …) nearest ``seconds``; a tie rounds up and anything under 1 weighs 1."""
    lower, upper = 1, 2
    while upper < seconds:
        lower, upper = upper, lower + upper
    if seconds <= lower:
        return lower
    return lower if seconds - lower < upper - seconds else upper


# --- completeness ---------------------------------------------------------------------------------------------------


def unclassified_jobs(
    workflows_dir: Path, suites: Iterable[Suite] = SUITES, non_test: frozenset[str] = NON_TEST_JOBS
) -> list[str]:
    """Each ``<workflow>:<job>`` neither a registered suite's job nor a declared non-test job, plus each registered
    or declared job no workflow defines — so a suite is never silently omitted and the registry never goes stale."""
    registered = {job for entry in suites for job in entry.jobs} | non_test
    present: set[str] = set()
    for path in sorted(workflows_dir.glob("*.yml")):
        for job_id, job in load_workflow(path)["jobs"].items():
            if job.get("uses", "").startswith("./.github/workflows/"):
                continue
            present.add(f"{path.name}:{job_id}")
    return sorted(present - registered) + sorted(f"{job} (no such job)" for job in registered - present)


# --- inventory ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Measured:
    seconds: float
    weight: int


@dataclass
class SuiteWeights:
    """One suite's inventory: its fallback weight and each measured case (or, for a unit suite, the suite's own
    identity); ``missing`` names why it has no measurement."""

    fallback: int = 1
    entries: dict[str, Measured] = field(default_factory=dict[str, Measured])
    missing: str | None = None


type Inventory = dict[str, SuiteWeights]


def measured(seconds: float) -> Measured:
    return Measured(round(seconds, 3), fibonacci_weight(seconds))


def suite_weights(durations: Mapping[str, float]) -> SuiteWeights:
    """A measured suite: each identity weighed, the fallback the round of the median measured duration."""
    if not durations:
        return SuiteWeights(missing="the evidence holds no passing case")
    entries = {identity: measured(durations[identity]) for identity in sorted(durations)}
    return SuiteWeights(fibonacci_weight(statistics.median(durations.values())), entries)


def dump_inventory(inventory: Inventory) -> str:
    suites: dict[str, object] = {}
    for suite_id in sorted(inventory):
        weights = inventory[suite_id]
        if weights.missing is not None:
            suites[suite_id] = {"fallback": weights.fallback, "missing": weights.missing}
        else:
            suites[suite_id] = {
                "fallback": weights.fallback,
                "entries": {k: {"seconds": v.seconds, "weight": v.weight} for k, v in weights.entries.items()},
            }
    header = "Generated by `mise run shard-weights-refresh` (tests/support/shard_weights.py); never edit by hand."
    return json.dumps({"_generated": header, "suites": suites}, indent=2) + "\n"


def load_inventory(path: Path = INVENTORY_PATH) -> Inventory:
    if not path.exists():
        return {}
    raw = cast(dict[str, dict[str, dict[str, object]]], json.loads(path.read_text()))["suites"]
    inventory: Inventory = {}
    for suite_id, body in raw.items():
        entries = cast(dict[str, dict[str, float]], body.get("entries", {}))
        inventory[suite_id] = SuiteWeights(
            cast(int, body["fallback"]),
            {k: Measured(v["seconds"], int(v["weight"])) for k, v in entries.items()},
            cast(str | None, body.get("missing")),
        )
    return inventory


# --- evidence parsing -----------------------------------------------------------------------------------------------


def pytest_durations(path: Path) -> dict[str, float]:
    """The recorder's file: ``{nodeid: seconds}``."""
    return {k: float(v) for k, v in cast(dict[str, float], json.loads(path.read_text())).items()}


def playwright_durations(path: Path, tests_dir: Path = TESTS_DIR) -> dict[str, float]:
    """Each passing test of a Playwright JSON report as ``<project> > <file relative to tests/> > <title path>``, joined by :data:`SEP`,
    an ordinal suffix only where one file repeats a title path; seconds sum every attempt."""
    report = cast(dict[str, object], json.loads(path.read_text()))
    root_dir = Path(cast(dict[str, str], report["config"])["rootDir"])
    seconds: dict[str, float] = {}
    seen: dict[str, int] = {}

    def walk(node: dict[str, object], titles: tuple[str, ...], top: bool) -> None:
        here = titles if top else (*titles, cast(str, node["title"]))
        for spec in cast(list[dict[str, object]], node.get("specs", [])):
            file = os.path.relpath(root_dir / cast(str, spec["file"]), tests_dir).replace(os.sep, "/")
            for test in cast(list[dict[str, object]], spec["tests"]):
                results = cast(list[dict[str, object]], test["results"])
                if not results or results[-1].get("status") != "passed":
                    continue
                base = SEP.join((cast(str, test["projectName"]), file, *here, cast(str, spec["title"])))
                seen[base] = seen.get(base, 0) + 1
                identity = base if seen[base] == 1 else f"{base} #{seen[base]}"
                seconds[identity] = sum(cast(float, result["duration"]) for result in results) / 1000
        for child in cast(list[dict[str, object]], node.get("suites", [])):
            walk(child, here, False)

    for file_suite in cast(list[dict[str, object]], report["suites"]):
        walk(file_suite, (), True)
    return seconds


def job_durations(path: Path) -> dict[str, float]:
    """Each successful job of ``gh run view --json jobs`` as ``{display name: wall seconds}``."""
    jobs = cast(dict[str, list[dict[str, str]]], json.loads(path.read_text()))["jobs"]
    return {
        job["name"]: (
            datetime.fromisoformat(job["completedAt"]) - datetime.fromisoformat(job["startedAt"])
        ).total_seconds()
        for job in jobs
        if job.get("conclusion") == "success"
    }


def _evidence_files(directory: Path, suite_id: str, suffix: str) -> list[Path]:
    """``<prefix><suite>.json`` and its shards ``<prefix><suite>-<n>.json`` — never a longer suite id's file."""
    found: list[Path] = []
    for path in sorted(directory.glob(f"*{suite_id}*.json")):
        stem = path.name.removesuffix(".json")
        for prefix in (f"{suffix}-", ""):
            rest = stem.removeprefix(prefix + suite_id) if stem.startswith(prefix + suite_id) else None
            if rest is not None and (rest == "" or (rest.startswith("-") and rest[1:].isdigit())):
                found.append(path)
                break
    return found


@dataclass
class Refreshed:
    inventory: Inventory
    unmeasured: list[str]
    stale: dict[str, list[str]]


def refresh(evidence: Path, previous: Inventory, tests_dir: Path = TESTS_DIR) -> Refreshed:
    """Rebuild the inventory from an evidence directory.

    Case evidence: ``timings-<suite>[-<shard>].json`` (the pytest recorder) or ``report-<suite>[-<shard>].json``
    (a Playwright JSON report). Unit evidence: ``units.json`` (locally measured wall seconds) or a ``jobs*.json``
    from ``gh run view --json jobs``. A suite the evidence does not measure keeps its previous inventory and is
    reported unmeasured; an entry the new measurement no longer holds is reported stale and dropped.
    """
    units = (
        cast(dict[str, float], json.loads((evidence / UNITS_FILE).read_text()))
        if (evidence / UNITS_FILE).exists()
        else {}
    )
    jobs: dict[str, float] = {}
    for path in sorted(evidence.glob("jobs*.json")):
        jobs |= job_durations(path)
    inventory: Inventory = {}
    unmeasured: list[str] = []
    stale: dict[str, list[str]] = {}
    for entry in SUITES:
        durations: dict[str, float] | None = None
        if entry.kind == "unit":
            if entry.id in units:
                durations = {entry.id: float(units[entry.id])}
            elif entry.ci_job is not None and (
                hits := [s for n, s in jobs.items() if fnmatch.fnmatch(n, entry.ci_job)]
            ):
                durations = {entry.id: max(hits)}
        else:
            files = (
                _evidence_files(evidence, entry.id, "timings")
                if entry.evidence == "pytest"
                else _evidence_files(evidence, entry.id, "report")
            )
            if files:
                durations = {}
                for path in files:
                    durations |= (
                        pytest_durations(path) if entry.evidence == "pytest" else playwright_durations(path, tests_dir)
                    )
        if durations is None:
            unmeasured.append(entry.id)
            inventory[entry.id] = previous.get(entry.id) or SuiteWeights(missing=_missing_reason(entry))
            continue
        inventory[entry.id] = suite_weights(durations)
        dropped = sorted(set(previous.get(entry.id, SuiteWeights()).entries) - set(durations))
        if dropped:
            stale[entry.id] = dropped
    return Refreshed(inventory, unmeasured, stale)


def _missing_reason(entry: Suite) -> str:
    if entry.kind == "unit" and entry.ci_job is None:
        return "no local measurement: CI's job record runs more than this unit"
    return "no timing evidence yet"


# --- placement ------------------------------------------------------------------------------------------------------


def unit_weight(inventory: Inventory, suite_id: str, cases: Iterable[str]) -> int:
    """A unit's weight: the sum of its inventoried cases, each unmeasured one weighing the suite's fallback."""
    weights = inventory.get(suite_id, SuiteWeights())
    return sum(weights.entries[case].weight if case in weights.entries else weights.fallback for case in cases)


def place(weights: Mapping[str, int], count: int) -> list[list[str]]:
    """Greedy heaviest-first placement: units by weight descending, ties by identity ascending, each onto the
    lightest shard, ties to the lowest index. Deterministic — every process computing it agrees."""
    shards: list[list[str]] = [[] for _ in range(count)]
    loads = [0] * count
    for unit in sorted(weights, key=lambda key: (-weights[key], key)):
        lightest = loads.index(min(loads))
        shards[lightest].append(unit)
        loads[lightest] += weights[unit]
    return shards


def hash_shard(key: str, count: int) -> int:
    """The 1-based stable-hash shard of ``key`` — the owner of a unit the weighted placement does not see."""
    return zlib.crc32(key.encode()) % count + 1


def pytest_file_unit(nodeid: str) -> str:
    return nodeid.partition("::")[0]


def playwright_unit(identity: str) -> str:
    """``<project> > <file> > ...`` -> ``<project> > <file>``: a Playwright shard owns whole (project, spec file) pairs."""
    project, _, rest = identity.partition(SEP)
    return f"{project}{SEP}{rest.partition(SEP)[0]}"


def group(cases: Iterable[str], unit_of: Pool) -> dict[str, list[str]]:
    keyer: Callable[[str], str] = {"file": pytest_file_unit, "test": str, "playwright": playwright_unit}[unit_of]
    units: dict[str, list[str]] = {}
    for case in cases:
        units.setdefault(keyer(case), []).append(case)
    return units


def plan(inventory: Inventory, suite_id: str, cases: Iterable[str]) -> list[list[str]]:
    """The weighted placement of the cases this run discovered (never inventory keys) into the suite's pool's units,
    one list of units per shard."""
    entry = suite(suite_id)
    if entry.pool is None:
        raise ValueError(f"{suite_id} is not sharded")
    units = group(cases, entry.pool)
    return place({unit: unit_weight(inventory, suite_id, members) for unit, members in units.items()}, entry.shards)


def plan_playwright(
    inventory: Inventory, identities: Sequence[str], index: int, suite_id: str = "service-playwright"
) -> list[str]:
    """The (project, spec file) units shard ``index`` (1-based) of a Playwright pool owns."""
    return plan(inventory, suite_id, identities)[index - 1]


# --- recorder -------------------------------------------------------------------------------------------------------


@dataclass
class TimingRecorder:
    """Sums each nodeid's setup, call, and teardown seconds; the controller writes them as JSON."""

    path: Path
    seconds: dict[str, float] = field(default_factory=dict[str, float])

    def record(self, nodeid: str, duration: float) -> None:
        self.seconds[nodeid] = self.seconds.get(nodeid, 0.0) + duration

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({k: round(v, 3) for k, v in sorted(self.seconds.items())}, indent=1) + "\n")


# --- CLI ------------------------------------------------------------------------------------------------------------


def _report(inventory: Inventory) -> list[str]:
    lines: list[str] = []
    for entry in SUITES:
        weights = inventory.get(entry.id)
        if weights is None or weights.missing is not None:
            lines.append(f"{entry.id}: missing ({weights.missing if weights else 'not in the inventory'})")
            continue
        total = sum(m.weight for m in weights.entries.values())
        lines.append(f"{entry.id}: {len(weights.entries)} measured, total weight {total}, fallback {weights.fallback}")
        if entry.pool is not None:
            units = group(weights.entries, entry.pool)
            unit_weights = {unit: unit_weight(inventory, entry.id, cases) for unit, cases in units.items()}
            placed = [sum(unit_weights[u] for u in shard) for shard in place(unit_weights, entry.shards)]
            hashed = [0] * entry.shards
            for unit, weight in unit_weights.items():
                hashed[hash_shard(unit, entry.shards) - 1] += weight
            lines.append(
                f"  weighted shards {placed} (max {max(placed)}); stable-hash shards {hashed} (max {max(hashed)})"
            )
    return lines


def _download_run(run_id: str, into: Path) -> None:
    jobs = subprocess.run(["gh", "run", "view", run_id, "--json", "jobs"], check=True, capture_output=True, text=True)
    (into / "jobs.json").write_text(jobs.stdout)
    subprocess.run(["gh", "run", "download", run_id, "--pattern", "timings-*", "--dir", str(into)], check=False)
    for path in into.glob("timings-*/*.json"):
        path.rename(into / f"{path.parent.name}.json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.support.shard_weights")
    commands = parser.add_subparsers(dest="command", required=True)
    refresh_cmd = commands.add_parser("refresh", help="rebuild tests/shard-weights.json from timing evidence")
    source = refresh_cmd.add_mutually_exclusive_group(required=True)
    source.add_argument("--evidence", type=Path, help="a local evidence directory")
    source.add_argument("--run", help="a CI run id, fetched with gh")
    commands.add_parser("report", help="each suite's measurement and each pool's predicted shard weights")
    playwright = commands.add_parser("plan-playwright", help="the (project, spec file) units one Playwright shard owns")
    playwright.add_argument("--shard", required=True)
    args = parser.parse_args(argv)
    if args.command == "report":
        print("\n".join(_report(load_inventory())))
        return 0
    if args.command == "plan-playwright":
        print("plan-playwright: not wired to a Playwright --list yet", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as fetched:
        evidence = cast(Path | None, args.evidence)
        if evidence is None:
            evidence = Path(fetched)
            _download_run(cast(str, args.run), evidence)
        result = refresh(evidence, load_inventory())
    INVENTORY_PATH.write_text(dump_inventory(result.inventory))
    for suite_id in result.unmeasured:
        print(f"unmeasured: {suite_id} ({result.inventory[suite_id].missing or 'kept its previous measurement'})")
    for suite_id, entries in result.stale.items():
        print(f"stale: {suite_id}: {len(entries)} entries dropped — {', '.join(entries[:5])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
