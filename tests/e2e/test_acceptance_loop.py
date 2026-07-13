"""The acceptance loop — the standing e2e smoke test (verification.md, P6 exit).

This is the *skeleton* of the P6 exit criterion: ONE chunk traveling
ingest -> acquire -> mock-scripted commit -> deliver -> landed in the bare origin,
with the hub's facts deriving ``done``. Each step is a ``TODO`` the walking-skeleton
builders fill as their track lands; the shape here is the contract for what the
loop asserts.

It is the **e2e tier** (``bzh:`` verification tiers): it needs the full live stack —
postgres, the mock forge, ``blizzard hub``, ``blizzard runner``, and a fixture
workspace — so it is **skipped unless ``BLIZZARD_E2E=1``**, keeping the default
``pytest`` gate (unit + component) hermetic and token-free. Bring the stack up with
``winter service up <env> --wait`` and set the flag to run it for real.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e acceptance loop needs the live stack; set BLIZZARD_E2E=1 after `winter service up <env> --wait`",
    ),
]


def test_acceptance_loop_one_chunk_ingest_to_landed() -> None:
    """One chunk travels the whole lifecycle and derives ``done`` (verification.md)."""
    # TODO(P6): 1. winter service up <env> --wait — postgres, mock forge, hub, runner.
    # TODO(P6): 2. Seed a scenario — fixture workspace + a mock-forge issue + store rows
    #             (mock-data CLI), one named fixture; mint the default graph if absent.
    # TODO(P6): 3. Generate work — file the issue on the mock forge, `blizzard hub ingest`
    #             its {provider, url} pointer -> POST /api/chunks -> a `ch_<ulid>` chunk,
    #             pinned to the default graph, deriving `ready`.
    # TODO(P6): 4. Watch it travel:
    #             - the runner FILLs: GET /api/queue/peek, the winter provider acquires a
    #               fixture env, POST /api/routes claims it (chunk derives `running`);
    #             - the Claude Code adapter spawns the mock-claude-code façade, which makes
    #               a real commit and pushes the branch to the `file://` origin;
    #             - the runner submits POST /api/chunks/{id}/completions (build `pass`),
    #               the apply-response's envelope enters the deliver hub node
    #               (chunk derives `delivering`);
    #             - the hub coordinator's strict-FIFO merge queue lands the branch via the
    #               mock forge (per-repo `delivery.repo_landed`, then `delivery.landed`).
    # TODO(P6): 5. Assert BOTH ends:
    #             - the merged commit is reachable from the bare origin's `main`;
    #             - GET /api/chunks/{id} derives status == `done`.
    pytest.skip("acceptance-loop body is a P6 walking-skeleton TODO — see the step plan above")
