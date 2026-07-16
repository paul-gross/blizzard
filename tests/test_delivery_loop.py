"""The hub-boundary acceptance loop (component tier).

One chunk travels the whole hub lifecycle over the HTTP surface —
ingest -> peek -> claim -> completion -> deliver -> done — with the forge behind an
in-process fake. This is the hub half of the P6 exit criterion (verification.md); the
full cross-daemon loop (real runner, real fixture workspace, real mock forge) is the
``e2e`` acceptance test, skipped without the live stack.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import FakeForge, build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "12"}

# A minimal build -> deliver graph named `default-delivery`, pre-minted so the hub's
# lazy `ensure_default` (POST /chunks) reuses it by name (D-081) instead of minting
# the packaged prose graph. This keeps these hub-delivery mechanics tests focused on
# the deliver hub node, decoupled from the packaged default graph's shape (P7 promoted
# that to build -> review -> deliver; the review cycle is test_review_cycle's subject).
_BUILD_DELIVER_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: deliver
        fail:
          description: Incomplete.
          to: build
  deliver:
    executor: hub
    mode: merge-to-main
"""


def _ingest(hub) -> str:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_DELIVER_YAML}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # ready to claim (D-103)
    return chunk_id


def _claim(hub, chunk_id: str) -> dict:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    # The runner mints its lease and reports it up (D-044) — the fence input for the
    # completion that follows.
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return resp.json()


def _build_completion(chunk_id: str, build_node_id: str, epoch: int) -> dict:
    return {
        "choice": "pass",
        "epoch": epoch,
        "runner_id": "r1",
        "from_node_id": build_node_id,
        "check_results": [{"command": "mise run test", "passed": True}],
        "artifacts": [
            {
                "name": "work",
                "kind": "git_commit",
                "repo": "acme/widget",
                "branch_name": "blizzard/ch-12",
                "commit_hash": "abc123",
            }
        ],
    }


def _git_artifact(name: str, repo: str, commit: str) -> dict:
    return {"name": name, "kind": "git_commit", "repo": repo, "branch_name": "blizzard/ch-12", "commit_hash": commit}


def _two_repo_completion(build_node_id: str, epoch: int, *, api_commit: str, web_commit: str) -> dict:
    """A build completion over a two-repo chunk — one pointer per repo (distinct names).

    The two artifacts carry distinct names (``work-api`` / ``work-web``) so both survive
    the latest-by-``{node_name}.{name}`` resolution (D-089); the coordinator lands them
    serially in list order (``acme/api`` first).
    """
    return {
        "choice": "pass",
        "epoch": epoch,
        "runner_id": "r1",
        "from_node_id": build_node_id,
        "check_results": [{"command": "mise run test", "passed": True}],
        "artifacts": [
            _git_artifact("work-api", "acme/api", api_commit),
            _git_artifact("work-web", "acme/web", web_commit),
        ],
    }


def test_one_chunk_ingest_to_landed(tmp_path: Path) -> None:
    forge = FakeForge()
    hub = build_hub(tmp_path, forge=forge)

    chunk_id = _ingest(hub)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"

    # FILL: the ready queue shows the chunk before it is claimed.
    peek = hub.client.get("/api/queue/peek").json()
    assert [e["chunk_id"] for e in peek["entries"]] == [chunk_id]

    claim = _claim(hub, chunk_id)
    envelope = claim["envelope"]
    assert envelope["node"]["node_name"] == "build"
    # The claim precedes the runner's lease report, so its envelope carries epoch 0
    # (no lease yet); the runner's own lease epoch (1) is what the completion fences on.
    assert envelope["epoch"] == 0
    build_node_id = envelope["node"]["node_id"]

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert detail["route"]["environment_ids"] == ["env-a"]
    assert detail["current_node_id"] == build_node_id

    # ADVANCE: build passes -> deliver hub node takes over, coordinator lands.
    apply = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_build_completion(chunk_id, build_node_id, 1))
    assert apply.status_code == 200, apply.text
    assert apply.json()["outcome"] == "hub_node_taken"

    # The forge saw the land, and the chunk derives done with its route released.
    assert [(r.repo, r.branch_name, r.commit_hash) for r in forge.landed] == [
        ("acme/widget", "blizzard/ch-12", "abc123")
    ]
    final = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert final["status"] == "done"
    assert final["route"] is None


def test_completion_replay_is_idempotent(tmp_path: Path) -> None:
    forge = FakeForge()
    hub = build_hub(tmp_path, forge=forge)
    chunk_id = _ingest(hub)
    build_node_id = _claim(hub, chunk_id)["envelope"]["node"]["node_id"]

    first = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_build_completion(chunk_id, build_node_id, 1))
    replay = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_build_completion(chunk_id, build_node_id, 1))

    assert first.json()["outcome"] == "hub_node_taken"
    assert replay.json()["outcome"] == "hub_node_taken"
    # The replay lands nothing new — exactly one delivery happened.
    assert len(forge.landed) == 1
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_delivery_conflict_routes_back_to_entry(tmp_path: Path) -> None:
    forge = FakeForge()
    forge.conflict_repos.add("acme/widget")
    hub = build_hub(tmp_path, forge=forge)
    chunk_id = _ingest(hub)
    build_node_id = _claim(hub, chunk_id)["envelope"]["node"]["node_id"]

    apply = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_build_completion(chunk_id, build_node_id, 1))
    assert apply.json()["outcome"] == "hub_node_taken"

    # A conflict routes intra-graph back to the entry node (build); nothing landed,
    # the chunk is running again in its warm environments (route retained).
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert detail["current_node_id"] == build_node_id
    assert forge.landed == []


def test_multi_repo_partial_land_is_retained_and_redelivery_skips_the_landed_repo(tmp_path: Path) -> None:
    """A two-repo chunk where one repo lands and the other conflicts (D-091).

    The best-effort serial land keeps the partial land: ``acme/api`` merges, then
    ``acme/web`` conflicts and routes the chunk intra-graph back to build with
    ``acme/api`` **retained** as a per-repo land fact. On redelivery — the runner
    re-enters build under a fresh lease and re-submits both pointers — the coordinator
    **skips** the already-landed ``acme/api`` (coordinator reconciliation) and lands only
    the remaining ``acme/web``, so ``acme/api`` is never merged twice.
    """
    forge = FakeForge()
    forge.conflict_repos.add("acme/web")  # api lands; web conflicts on the first pass
    hub = build_hub(tmp_path, forge=forge)
    chunk_id = _ingest(hub)
    build_node_id = _claim(hub, chunk_id)["envelope"]["node"]["node_id"]

    # First delivery (epoch 1): api lands, web conflicts -> partial land retained.
    apply = hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_two_repo_completion(build_node_id, 1, api_commit="api1", web_commit="web1"),
    )
    assert apply.json()["outcome"] == "hub_node_taken"

    # Exactly the landing repo merged; the conflict routed the chunk back to build,
    # running again in its warm environments (route retained), api's land preserved.
    assert [(r.repo, r.commit_hash) for r in forge.landed] == [("acme/api", "api1")]
    mid = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert mid["status"] == "running"
    assert mid["current_node_id"] == build_node_id

    # Redelivery: clear the conflict, the runner re-enters build under a fresh lease
    # (the conflict consumed epoch 2, so the re-entry mints epoch 3) and re-submits BOTH
    # pointers with fresh commits. The coordinator skips the already-landed api and lands
    # only web -> the chunk finishes done with api merged exactly once.
    forge.conflict_repos.discard("acme/web")
    report_lease(hub, chunk_id, epoch=3, seq=2)
    redeliver = hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_two_repo_completion(build_node_id, 3, api_commit="api2", web_commit="web2"),
    )
    assert redeliver.json()["outcome"] == "hub_node_taken"

    # api landed once (the redelivery skipped it, so its original commit stands); web
    # landed on the retry. Never a second api merge.
    assert [(r.repo, r.commit_hash) for r in forge.landed] == [("acme/api", "api1"), ("acme/web", "web2")]
    final = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert final["status"] == "done"
    assert final["route"] is None
