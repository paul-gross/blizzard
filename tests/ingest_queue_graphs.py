"""Helpers for the ingest and queue tests, shared with other test modules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tests.support import (
    HubHarness,
)

# A build -> review -> deliver graph mirroring the packaged default's shape, but with a
# trivial deliver node — hermetic, since this drives re-ingest, not delivery mechanics.
BUILD_REVIEW_DELIVER_YAML = """
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
          to: review
        fail:
          description: Incomplete.
          to: build
  review:
    executor: runner
    prompt: |
      Review the change.
    judgement:
      prompt: |
        Assess the review.
      choices:
        pass:
          description: Passes review.
          to: deliver
        fail:
          description: Blocking issues.
          to: build
  deliver:
    executor: hub
    action: pass
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


def pass_node(
    hub: HubHarness, chunk_id: str, node_id: str, epoch: int, *, artifacts: Sequence[Mapping[str, str]]
) -> dict[str, Any]:
    body: dict[str, Any] = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": node_id,
            "artifacts": artifacts,
        },
    ).json()
    return body
