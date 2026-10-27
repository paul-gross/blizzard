"""The self-healing PR + CI-watch delivery policy delivers through the generic path (#67).

Proves delivery **policy lives in YAML**: `_graph_yaml()` differs from the default graph
only in `deliver`'s poll cadence and routing, yet drives the wait/self-heal/bounce/
failure routes below through the same generic `executor: hub` primitive.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import httpx2
import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from tests.e2e.fleet_traces import FleetCollector, StepExpect, assert_skeleton
from tests.fleet_launchers import REPO, git_bare, mock_bin_dir, run_forge, run_hub, runner_api, runner_config
from tests.fleet_world import FIXTURE_ENV, PUSH_AND_DECLARE_SCRIPT, REPO_NAME, mint_world, scripted_build_prompt
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e delivery-pr-ci needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_BUILD_SCRIPT = scripted_build_prompt(
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "PR_CI_LANDED.md").write_text("landed via the PR+CI policy\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: a change the PR+CI policy lands once CI goes green"],\n'
    "    check=True,\n"
    ")\n" + PUSH_AND_DECLARE_SCRIPT
)
_BUILD_JUDGEMENT = "verdict('pass', 'committed the change; checks are green')\n"


def _graph_yaml(poll_timeout: int = 600) -> str:
    """The PR+CI delivery policy's shape, inlined with a re-poll-every-tick cadence.

    Names the SAME `land` action and choice names the shipped graph authors,
    exercising real routing. Differs only in poll cadence and in routing
    `conflict`/`failure` back to `build` (this stand-in has no `resolve` spine)."""
    import yaml

    graph = {
        # Named `default-delivery` so the hub's default-graph resolution binds this
        # graph at ingest; the policy rides `deliver`'s `land` action, not the name.
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _BUILD_SCRIPT,
                "judgement": {
                    "prompt": _BUILD_JUDGEMENT,
                    "choices": {"pass": {"description": "Committed and green.", "to": "deliver"}},
                },
                "retries": {"max": 2, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "poll_interval": 1,  # a brisk 1s cadence so the scenario converges in seconds
                "poll_timeout": poll_timeout,  # 600: never time out — these prove routing, not #64's timeout kick-back
                "action": "land",
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo's PR merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo's PR is dirty; back to build.", "to": "build"},
                        "failure": {"description": "poll_timeout exceeded; back to build.", "to": "build"},
                        "inherited-failure": {
                            "description": "The base check remains red after a rerun; back to build.",
                            "to": "build",
                        },
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _reset_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Reset the mock fixture workspace; skip if the sibling mock isn't present."""
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"
    return bin_dir, workspace, origins, origin_bare


def _ingest_and_promote(hub: httpx2.Client, forge: httpx2.Client, *, poll_timeout: int = 600) -> str:
    assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml(poll_timeout)}).status_code == 201
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": "pr-ci", "body": "the PR+CI chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


def _drive_until(config: RunnerConfig, hub: httpx2.Client, chunk_id: str, env: dict[str, str], predicate, timeout=60.0):
    """Tick until `predicate(detail)` is truthy; return that detail. Raises on timeout.

    Wrapped in :func:`runner_api` so the build node's scripted push+declare has a live
    local API to POST to.
    """
    prior = dict(os.environ)
    os.environ.update(env)
    try:
        with runner_api(config):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                LoopWiring.of(config).tick_once()
                detail = hub.get(f"/api/chunks/{chunk_id}")
                assert detail.status_code == 200, detail.text
                body = detail.json()
                if predicate(body):
                    return body
                time.sleep(0.4)
            raise AssertionError("predicate never became true within the timeout")
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _push_commit(origin_bare: Path, branch: str, clone: Path, filename: str) -> str:
    """Push one real commit onto ``branch`` of the bare origin and return its sha."""
    subprocess.run(["git", "clone", "--quiet", "-b", branch, str(origin_bare), str(clone)], check=True)
    (clone / filename).write_text(f"{filename}\n")
    identity = ["-c", "user.email=other@blizzard.local", "-c", "user.name=Someone Else"]
    subprocess.run(["git", "-C", str(clone), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(clone), *identity, "commit", "--quiet", "-m", f"add {filename}"], check=True)
    subprocess.run(["git", "-C", str(clone), "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], check=True)
    return subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _fenced_env() -> dict[str, str]:
    return {**os.environ, "BLIZZARD_MOCK_HARNESS_FENCE": "1"}


def test_pr_ci_pends_on_blocked_then_lands_when_green(tmp_path: Path) -> None:
    """A blocked PR pends over several polls, then lands once the lever clears; the
    blocked-wait path also pins exactly one unchanging `delivery-findings`
    artifact across the repeated polls."""
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # `blocked` (required CI not green yet) — the correct "not green" wait state.
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        # Pending: the PR is open but blocked, so nothing merges.
        pending = _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        assert pending["status"] == "delivering", pending["status"]
        assert pending["landed"] is False
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls and not any(p.get("merged") for p in pulls), f"a blocked PR merged while pending: {pulls}"
        assert git_bare(origin_bare, "rev-parse", "main").strip() == main_before, "bare main moved while pending"
        reference = {"repo": REPO, "number": pulls[0]["number"], "url": pulls[0]["html_url"]}
        assert pending["open_prs"] == [reference]
        assert pending["awaiting_external_merge"] is False
        assert pending["landed_repos"] == []
        assert hub.get("/api/chunks").json()["chunks"][0]["open_prs"] == [reference]
        assert forge.get(reference["url"]).status_code == 200

        # The substantive wait writes `delivery-findings` on its first poll...
        findings_rows = [a for a in pending["artifacts"] if a.get("name") == "delivery-findings"]
        assert len(findings_rows) == 1, f"expected exactly one delivery-findings artifact: {findings_rows}"
        first_content = findings_rows[0].get("content") or ""
        assert REPO_NAME in first_content
        assert "ci" in first_content.lower()

        # Repeated polls within the same visit (still blocked, same epoch) must
        # leave it unchanged — per-(chunk, node, name, epoch) idempotence.
        first_next_poll = pending["pending"]["next_poll_at"]
        second = _drive_until(
            config,
            hub,
            chunk_id,
            fenced,
            lambda b: bool(b.get("pending")) and b["pending"]["next_poll_at"] != first_next_poll,
        )
        third = _drive_until(
            config,
            hub,
            chunk_id,
            fenced,
            lambda b: bool(b.get("pending")) and b["pending"]["next_poll_at"] != second["pending"]["next_poll_at"],
        )
        for detail in (second, third):
            rows = [a for a in detail["artifacts"] if a.get("name") == "delivery-findings"]
            assert len(rows) == 1, f"a repeat poll duplicated the delivery-findings artifact: {rows}"
            assert (rows[0].get("content") or "") == first_content, "a repeat poll changed the findings content"

        # CI goes green: clear the lever; the next poll reads clean and merges.
        assert forge.delete("/_levers/checks_pending", params={"repo": REPO}).status_code == 200
        done = _drive_until(config, hub, chunk_id, fenced, lambda b: b["status"] in {"done", "needs_human"}, timeout=90)
        assert done["status"] == "done", f"did not land after CI went green (status {done['status']!r})"
        assert done["landed"] is True
        landed = done["landed_repos"]
        assert len(landed) == 1 and landed[0]["repo"] == REPO
        assert done["open_prs"] == []
        assert done["closed_prs"] == [reference]
        assert forge.get(landed[0]["url"]).status_code == 200
        summary = next(row for row in hub.get("/api/chunks").json()["chunks"] if row["chunk_id"] == chunk_id)
        assert summary["landed_repos"] == landed

    main_after = git_bare(origin_bare, "rev-parse", "main").strip()
    assert main_after != main_before, "bare main did not move despite a clean merge"
    merge_sha, base_parent, head_parent = git_bare(origin_bare, "rev-list", "--parents", "-n", "1", "main").split()
    assert merge_sha == main_after and base_parent == main_before
    assert landed[0]["commit_hash"] == merge_sha
    assert head_parent == pulls[0]["head"]["sha"], "merge commit did not preserve the checked PR head"
    landings = [
        ln for ln in git_bare(origin_bare, "log", "--oneline", "--", "PR_CI_LANDED.md").splitlines() if ln.strip()
    ]
    assert len(landings) == 1, f"PR_CI_LANDED.md landed {len(landings)} times on bare main"


def test_pr_ci_routes_failure_on_a_terminally_failed_check(tmp_path: Path) -> None:
    """A terminally-failed check routes `failure` well inside the 60s budget,
    ruling out a `poll_timeout`-driven trigger. Two passes assert the findings content:
    plain CI failure, then a red base check ("not this change")."""
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # `checks_failed` — blocked with a completed, failing check run on the PR head.
        assert forge.post("/_levers/checks_failed", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        bounced = _drive_until(config, hub, chunk_id, fenced, lambda b: bool(b.get("bounces")))
        assert bounced["bounces"][0]["cause"] == "failure", bounced["bounces"]
        assert bounced["landed"] is False

        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls and not any(p.get("merged") for p in pulls), f"a failing PR merged: {pulls}"

        artifacts = hub.get(f"/api/chunks/{chunk_id}").json()["artifacts"]
        findings = next((a for a in artifacts if a.get("name") == "delivery-findings"), None)
        assert findings is not None, f"no delivery-findings artifact recorded: {artifacts}"
        content = findings.get("content") or ""
        assert REPO_NAME in content
        assert "failure" in content
        assert "not this change" not in content, (
            "the base wasn't armed red in this pass — the findings must not claim it is"
        )

    assert git_bare(origin_bare, "rev-parse", "main").strip() == main_before, "bare main moved on a CI failure"

    # Second pass: the base's own check is also failing (AC3/AC5). Fresh tmp_path — reusing
    # the first pass's hub dir would carry over its bounced `toy-api:1` chunk.
    second_pass = tmp_path / "second-pass"
    bin_dir, workspace, origins, origin_bare = _reset_fixture(second_pass)
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(second_pass / "hub", forge_port, hub_port) as hub:
        assert forge.post("/_levers/checks_failed", json={"repo": REPO}).status_code == 200
        assert forge.post("/_levers/base_checks_failed", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(second_pass / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        bounced = _drive_until(config, hub, chunk_id, fenced, lambda b: bool(b.get("bounces")))
        assert bounced["bounces"][0]["cause"] == "inherited-failure", bounced["bounces"]

        artifacts = hub.get(f"/api/chunks/{chunk_id}").json()["artifacts"]
        findings = next((a for a in artifacts if a.get("name") == "delivery-findings"), None)
        assert findings is not None
        content = findings.get("content") or ""
        assert "not this change" in content, content


def test_pr_ci_self_heals_a_behind_branch_and_lands(tmp_path: Path) -> None:
    """The base really moves before the PR opens, so the self-heal's update-branch merge
    advances the head past the submitted commit and delivery still lands it."""
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    _push_commit(origin_bare, "main", tmp_path / "base-clone", "BASE_MOVED.md")
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # `behind` — base moved, no conflict. The `stale_branch` lever clears ONLY via
        # `PUT .../update-branch`, so reaching `done` proves the action self-healed.
        assert forge.post("/_levers/stale_branch", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        # The behind PR fires update-branch and pends at least once before healing.
        pending = _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        assert pending["landed"] is False

        done = _drive_until(config, hub, chunk_id, fenced, lambda b: b["status"] in {"done", "needs_human"}, timeout=90)
        assert done["status"] == "done", f"a behind PR did not self-heal to done (status {done['status']!r})"
        assert done["landed"] is True

        # Forge truth: the once-behind PR is now merged, exactly once.
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert len([p for p in pulls if p.get("merged")]) == 1, f"expected one merged PR, got {pulls}"

    main_after = git_bare(origin_bare, "rev-parse", "main").strip()
    assert main_after != main_before, "bare main did not move despite the self-healed land"
    _, base_parent, head_parent = git_bare(origin_bare, "rev-list", "--parents", "-n", "1", "main").split()
    assert base_parent == main_before
    assert main_before in git_bare(origin_bare, "rev-list", "--parents", "-n", "1", head_parent).split()[1:], (
        "the advanced head is not a merge of the moved base"
    )


def test_pr_ci_bounces_a_dirty_conflict_back_to_build(tmp_path: Path) -> None:
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # `dirty` — a real merge conflict. The action bounces to `build` immediately.
        assert forge.post("/_levers/merge_conflict", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        # A conflict routes back to build — the FIRST recorded bounce carries cause `conflict`.
        bounced = _drive_until(config, hub, chunk_id, fenced, lambda b: bool(b.get("bounces")))
        assert bounced["bounces"][0]["cause"] == "conflict", bounced["bounces"]
        assert bounced["landed"] is False

        # Nothing merged; the dirty PR is still open.
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls and not any(p.get("merged") for p in pulls), f"a dirty PR merged: {pulls}"

    assert git_bare(origin_bare, "rev-parse", "main").strip() == main_before, "bare main moved despite the conflict"


def test_pr_ci_refuses_a_foreign_commit_on_the_head(tmp_path: Path) -> None:
    """A commit pushed onto the PR's branch after the chunk submitted is not the submitted
    work: delivery bounces `failure` naming it, and nothing merges."""
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        pending = _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        branch = pulls[0]["head"]["ref"]
        foreign = _push_commit(origin_bare, branch, tmp_path / "foreign-clone", "FOREIGN.md")
        assert pending["landed"] is False

        assert forge.delete("/_levers/checks_pending", params={"repo": REPO}).status_code == 200
        bounced = _drive_until(config, hub, chunk_id, fenced, lambda b: bool(b.get("bounces")))
        assert bounced["bounces"][0]["cause"] == "failure", bounced["bounces"]
        assert bounced["landed"] is False

        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls and not any(p.get("merged") for p in pulls), f"a foreign-head PR merged: {pulls}"

        artifacts = hub.get(f"/api/chunks/{chunk_id}").json()["artifacts"]
        findings = next((a for a in artifacts if a.get("name") == "delivery-findings/foreign-head"), None)
        assert findings is not None, f"no foreign-head findings artifact recorded: {artifacts}"
        assert foreign in (findings.get("content") or "")

    assert git_bare(origin_bare, "rev-parse", "main").strip() == main_before, "bare main moved on a foreign head"


def test_pr_ci_bounces_a_poll_timeout_back_to_build(
    tmp_path: Path, fleet_traces: FleetCollector, subtests: pytest.Subtests
) -> None:
    """A PR that stays blocked past `poll_timeout` is a kick-back, not a failure that ends the chunk: the
    delivery bounces with cause `poll-timeout` and the chunk re-enters build."""
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with (
        run_forge(bin_dir, origins, forge_port) as forge,
        run_hub(tmp_path / "hub", forge_port, hub_port, collector=fleet_traces) as hub,
    ):
        # `blocked` for good: polls pend every second until the node's few-second timeout fires.
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge, poll_timeout=4)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        bounced = _drive_until(config, hub, chunk_id, fenced, lambda b: bool(b.get("bounces")))
        assert bounced["bounces"][0]["cause"] == "poll-timeout", bounced["bounces"]
        assert bounced["landed"] is False
        assert bounced["current_node_name"] == "build"

        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls and not any(p.get("merged") for p in pulls), f"a blocked PR merged: {pulls}"

        # Fleet truth, as the trace backend sees it: the deliver step pended over several polls, then bounced with
        # cause `poll-timeout`; the re-entered build links to it by `bounce`. The chunk keeps looping against the
        # blocked PR, so only the first three steps are read.
        with subtests.test(msg="fleet traces"):
            fleet_traces.require()
            _drive_until(
                config, hub, chunk_id, fenced, lambda b: sum(1 for h in b["history"] if h["choice_name"] == "pass") >= 2
            )
            traces = fleet_traces.traces(roots=3, exact=False)
            (bounce,) = [e for e in traces[1].root.events if e.name == "bounce"]
            assert bounce.attributes["blizzard.bounce.cause"] == "poll-timeout"
            assert_skeleton(
                traces,
                [
                    StepExpect("step build", "transitioned", "deliver", children=("queue wait", "claim")),
                    StepExpect(
                        "step deliver",
                        "transitioned",
                        "build",
                        events=("bounce",),
                        min_events=(("hub poll pending", 2),),
                        min_children=(("hub exec", 1),),
                        link="next",
                        attributes=(("blizzard.bounce.cause", "poll-timeout"),),
                    ),
                    StepExpect("step build", "transitioned", "deliver", link="bounce"),
                ],
            )

    assert git_bare(origin_bare, "rev-parse", "main").strip() == main_before, "bare main moved on a poll timeout"


def _open_pull(forge: httpx2.Client) -> dict:
    """The one PR the chunk's delivery opened."""
    pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
    assert len(pulls) == 1, f"expected exactly one PR: {pulls}"
    return pulls[0]


def _assert_nothing_merged(forge: httpx2.Client) -> None:
    pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
    assert pulls and not any(p.get("merged") for p in pulls), f"a PR merged: {pulls}"


def test_pr_ci_lands_an_unstable_pr_once_checks_are_green(tmp_path: Path) -> None:
    """GitHub's `unstable` — mergeable, with a non-required check not passing — is mergeable: once the
    required checks read green the PR merges."""
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # The forced state is per PR, so hold the PR blocked until it exists, then force it `unstable`.
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        number = _open_pull(forge)["number"]
        forced = forge.post("/_levers/mergeable_state", json={"repo": REPO, "number": number, "state": "unstable"})
        assert forced.status_code == 200, forced.text
        assert forge.delete("/_levers/checks_pending", params={"repo": REPO}).status_code == 200

        done = _drive_until(config, hub, chunk_id, fenced, lambda b: b["status"] in {"done", "needs_human"}, timeout=90)
        assert done["status"] == "done", f"an unstable, green PR did not land (status {done['status']!r})"
        assert done["landed"] is True
        assert _open_pull(forge)["merged"] is True

    assert git_bare(origin_bare, "rev-parse", "main").strip() != main_before, "bare main did not move"


@pytest.mark.parametrize("state", ["draft", "has_hooks"])
def test_pr_ci_waits_on_a_draft_or_hooked_pr(tmp_path: Path, state: str) -> None:
    """A green PR the forge reports `draft` or `has_hooks` is not yet mergeable: delivery pends over
    repeated polls and merges nothing."""
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        pending = _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        number = _open_pull(forge)["number"]
        forced = forge.post("/_levers/mergeable_state", json={"repo": REPO, "number": number, "state": state})
        assert forced.status_code == 200, forced.text
        # Checks go green; only the forced state holds the PR back.
        assert forge.delete("/_levers/checks_pending", params={"repo": REPO}).status_code == 200

        previous = pending["pending"]["next_poll_at"]
        for _ in range(3):
            later = _drive_until(
                config,
                hub,
                chunk_id,
                fenced,
                lambda b, seen=previous: (
                    b["status"] != "delivering" or (bool(b.get("pending")) and b["pending"]["next_poll_at"] != seen)
                ),
            )
            assert later["status"] == "delivering", f"a {state} PR left delivery: {later['status']!r}"
            assert later["landed"] is False
            assert not later.get("bounces"), later["bounces"]
            previous = later["pending"]["next_poll_at"]
        _assert_nothing_merged(forge)

    assert git_bare(origin_bare, "rev-parse", "main").strip() == main_before, f"bare main moved on a {state} PR"


def test_pr_ci_waits_on_a_startup_failure_check_then_lands(tmp_path: Path) -> None:
    """A check concluded `startup_failure` never ran, so it is no verdict on the change: delivery pends
    rather than routing `failure`, and lands once the check reads green."""
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert forge.post("/_levers/checks_startup_failure", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        pending = _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        again = _drive_until(
            config,
            hub,
            chunk_id,
            fenced,
            lambda b: (
                bool(b.get("bounces"))
                or (bool(b.get("pending")) and b["pending"]["next_poll_at"] != pending["pending"]["next_poll_at"])
            ),
        )
        assert not again.get("bounces"), f"a startup_failure check routed a bounce: {again['bounces']}"
        assert again["landed"] is False
        _assert_nothing_merged(forge)

        assert forge.delete("/_levers/checks_startup_failure", params={"repo": REPO}).status_code == 200
        done = _drive_until(config, hub, chunk_id, fenced, lambda b: b["status"] in {"done", "needs_human"}, timeout=90)
        assert done["status"] == "done", f"did not land once the check went green (status {done['status']!r})"
        assert not done.get("bounces"), done["bounces"]

    assert git_bare(origin_bare, "rev-parse", "main").strip() != main_before, "bare main did not move"


def test_pr_ci_routes_conflict_when_updating_a_behind_branch_conflicts(tmp_path: Path) -> None:
    """A behind PR whose base really conflicts with the change: the self-heal's update-branch is refused
    as a merge conflict, and delivery routes `conflict` rather than retrying or failing."""
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        # The base lands its own PR_CI_LANDED.md, so merging it into the head conflicts for real.
        _push_commit(origin_bare, "main", tmp_path / "base-clone", "PR_CI_LANDED.md")
        main_before = git_bare(origin_bare, "rev-parse", "main").strip()
        assert forge.post("/_levers/stale_branch", json={"repo": REPO}).status_code == 200

        bounced = _drive_until(config, hub, chunk_id, fenced, lambda b: bool(b.get("bounces")))
        assert bounced["bounces"][0]["cause"] == "conflict", bounced["bounces"]
        assert bounced["landed"] is False
        _assert_nothing_merged(forge)

    assert git_bare(origin_bare, "rev-parse", "main").strip() == main_before, "bare main moved despite the conflict"


def test_pr_ci_escalates_a_rejected_credential_as_forge_auth(tmp_path: Path) -> None:
    """The forge rejecting the repository's credential is nothing a worker can repair: delivery escalates
    the chunk to a person with cause `forge-auth` instead of bouncing it back to build."""
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        assert forge.post("/_levers/token_rejected", json={"repo": REPO}).status_code == 200

        escalated = _drive_until(config, hub, chunk_id, fenced, lambda b: b["status"] == "needs_human")
        escalation = escalated["escalation"]
        assert escalation is not None and escalation["cause"] == "forge-auth", escalation
        assert REPO in (escalation["detail"] or ""), escalation
        assert escalated["landed"] is False
        assert escalated["current_node_name"] == "deliver"

        assert forge.delete("/_levers/token_rejected", params={"repo": REPO}).status_code == 200
        _assert_nothing_merged(forge)

    assert git_bare(origin_bare, "rev-parse", "main").strip() == main_before, "bare main moved on a rejected token"


def test_pr_ci_escalates_a_repository_the_forge_does_not_know_as_forge_config(tmp_path: Path) -> None:
    """A repository record whose forge does not know it is a configuration fault: delivery escalates the
    chunk with cause `forge-config`. The mock forge backs a repository by its name under any owner, so the
    record is pointed at an API root on that forge that serves no repository at all."""
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        nowhere = f"http://127.0.0.1:{forge_port}/no-such-api"
        moved = hub.patch(f"/api/repositories/{REPO_NAME}", json={"forge_api_url": nowhere})
        assert moved.status_code == 200, moved.text

        escalated = _drive_until(config, hub, chunk_id, fenced, lambda b: b["status"] == "needs_human")
        escalation = escalated["escalation"]
        assert escalation is not None and escalation["cause"] == "forge-config", escalation
        assert nowhere in (escalation["detail"] or ""), escalation
        assert escalated["landed"] is False
        _assert_nothing_merged(forge)

    assert git_bare(origin_bare, "rev-parse", "main").strip() == main_before, "bare main moved on an unknown repo"


def _arm_checks(forge: httpx2.Client, ref: str, checks: list[dict[str, str]]) -> None:
    armed = forge.post("/_levers/ref_checks", json={"repo": REPO, "ref": ref, "checks": checks})
    assert armed.status_code == 200, armed.text


def _forge_reads(origins: Path, since: int) -> list[tuple[str, int]]:
    """Each ``GET`` under the repository the forge served after byte ``since`` of its log, as its target and status."""
    text = (origins.parent / "forge.log").read_bytes()[since:].decode(errors="replace")
    reads = re.findall(r'"GET (/repos/\S+) HTTP/1\.1" (\d+)', text)
    return [(target, int(status)) for target, status in reads]


def test_pr_ci_waiting_on_pending_checks_reads_conditionally_one_lookup_a_pass(tmp_path: Path) -> None:
    """A landing whose checks stay pending for ten polls spends no counted call on a landing read after its first
    pass — every one is answered ``304`` — and its already-merged lookup is one filtered read a pass."""
    bin_dir, workspace, origins, _ = _reset_fixture(tmp_path)

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        first = _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        _arm_checks(forge, _open_pull(forge)["head"]["ref"], [{"name": "ci", "status": "pending"}])
        # One more pass reads everything once at its armed answer, so the window below starts from kept answers.
        seen = [first["pending"]["next_poll_at"]]
        for _ in range(2):
            last = seen[-1]
            body = _drive_until(
                config,
                hub,
                chunk_id,
                fenced,
                lambda b, last=last: bool(b.get("pending")) and b["pending"]["next_poll_at"] != last,
            )
            seen.append(body["pending"]["next_poll_at"])
        start = (origins.parent / "forge.log").stat().st_size
        for _ in range(10):
            last = seen[-1]
            body = _drive_until(
                config,
                hub,
                chunk_id,
                fenced,
                lambda b, last=last: bool(b.get("pending")) and b["pending"]["next_poll_at"] != last,
            )
            seen.append(body["pending"]["next_poll_at"])
        reads = _forge_reads(origins, start)

        landing = [(t, s) for t, s in reads if "/issues/" not in t]
        counted = [t for t, s in landing if s != 304]
        assert not counted, f"landing reads counted against the hub's token: {counted}"
        lookups = [t for t, _ in landing if "state=closed" in t and "head=blizzard:" in t]
        assert len(lookups) >= 10, f"the already-merged lookup is not one filtered read a pass: {landing}"
        assert not [t for t, _ in landing if "state=closed" in t and "head=" not in t], landing
        _assert_nothing_merged(forge)


def test_pr_ci_does_not_merge_ahead_of_a_check_that_starts_after_the_others_finish(tmp_path: Path) -> None:
    """A revision whose one check completed green gains a second, pending one before the next pass, and is not
    merged until that one is green."""
    bin_dir, workspace, origins, _ = _reset_fixture(tmp_path)

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        head = _open_pull(forge)["head"]["ref"]
        _arm_checks(forge, head, [{"name": "first", "status": "success"}, {"name": "second", "status": "pending"}])
        # Mergeability is released while the second check is still running.
        assert forge.delete("/_levers/checks_pending", params={"repo": REPO}).status_code == 200

        first = _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        _drive_until(
            config,
            hub,
            chunk_id,
            fenced,
            lambda b: bool(b.get("pending")) and b["pending"]["next_poll_at"] != first["pending"]["next_poll_at"],
        )
        _assert_nothing_merged(forge)

        _arm_checks(forge, head, [{"name": "second", "status": "success"}])
        done = _drive_until(config, hub, chunk_id, fenced, lambda b: b["status"] in {"done", "needs_human"}, timeout=90)
        assert done["status"] == "done", done["status"]


def test_pr_ci_bounces_a_revision_with_no_check_at_its_poll_timeout(tmp_path: Path) -> None:
    """A revision armed with no check is never green: it stays pending and bounces at the node's poll timeout."""
    bin_dir, workspace, origins, _ = _reset_fixture(tmp_path)

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge, poll_timeout=4)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        _arm_checks(forge, _open_pull(forge)["head"]["ref"], [])
        assert forge.delete("/_levers/checks_pending", params={"repo": REPO}).status_code == 200

        bounced = _drive_until(config, hub, chunk_id, fenced, lambda b: bool(b.get("bounces")))
        assert bounced["bounces"][0]["cause"] == "poll-timeout", bounced["bounces"]
        _assert_nothing_merged(forge)
