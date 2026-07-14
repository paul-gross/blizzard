"""The facts-level invariant checker (``bzh:invariant-checker``).

After any crash → restart → recover cycle, the durable facts in both stores must still
satisfy the correctness conditions the design rests on. This module is the library of
those assertions — a violation names the exact broken invariant rather than a vague
"corruption", so a failing kill-9 sweep points straight at the window and the rule.

Because both stores are facts-only (``bzh:facts-not-status``), every check here is a
plain query over recorded rows plus, for the derivation totality, the real
status-derivation itself. Nothing here mutates; it opens each store read-only.

The kill-9 sweep (:mod:`tests.crash`) calls :func:`check_invariants` after every armed
crash; the hidden ``blizzard dev check-invariants`` CLI exposes the same entry to an
operator inspecting a store by hand.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import Engine, func, select

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.work import derive_chunk_status
from blizzard.hub.store import schema as hub
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.runner.store import schema as runner


@dataclass(frozen=True)
class Violation:
    """One broken invariant — its stable slug and a concrete detail of the breach."""

    invariant: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.invariant}] {self.detail}"


# --------------------------------------------------------------------------- #
# Runner store invariants
# --------------------------------------------------------------------------- #


def check_runner_store(engine: Engine) -> list[Violation]:
    """Assert the runner store's durable invariants (leases, bindings, outbound buffer)."""
    violations: list[Violation] = []
    with engine.connect() as conn:
        # runner:one-live-lease-per-chunk — a live lease is one with no closure fact
        # (``bzh:facts-not-status``); at most one per chunk (MAX_AGENTS math, D-082).
        closed = select(runner.lease_closures.c.lease_id)
        live = select(runner.leases.c.chunk_id).where(runner.leases.c.lease_id.notin_(closed))
        per_chunk = Counter(row[0] for row in conn.execute(live))
        for chunk_id, n in per_chunk.items():
            if n > 1:
                violations.append(Violation("runner:one-live-lease-per-chunk", f"chunk {chunk_id} has {n} live leases"))

        # runner:unique-env-binding — a held env id (binding with no release fact) is
        # bound to at most one chunk. Two chunks sharing a held env would double-book it.
        held = _held_bindings(conn)
        by_env: dict[str, set[str]] = {}
        for chunk_id, env_id in held:
            by_env.setdefault(env_id, set()).add(chunk_id)
        for env_id, chunks in by_env.items():
            if len(chunks) > 1:
                violations.append(
                    Violation("runner:unique-env-binding", f"env {env_id} held by chunks {sorted(chunks)}")
                )

        # runner:gapless-outbound-seq — the outbound buffer's per-runner seq is strictly
        # monotonic and gapless (D-069); one runner per store, so the seqs must be a
        # contiguous range with no holes (a hole would break FIFO idempotent replay).
        seqs = sorted(row[0] for row in conn.execute(select(runner.outbound_buffer.c.seq)))
        if seqs:
            expected = list(range(seqs[0], seqs[0] + len(seqs)))
            if seqs != expected:
                missing = sorted(set(expected) - set(seqs))
                violations.append(
                    Violation("runner:gapless-outbound-seq", f"outbound seqs not gapless; missing {missing}")
                )
    return violations


def _held_bindings(conn) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    """(chunk_id, environment_id) for every binding with no matching release fact."""
    releases = {
        (row[0], row[1])
        for row in conn.execute(select(runner.binding_releases.c.chunk_id, runner.binding_releases.c.environment_id))
    }
    held: list[tuple[str, str]] = []
    for chunk_id, env_id in conn.execute(select(runner.env_bindings.c.chunk_id, runner.env_bindings.c.environment_id)):
        if (chunk_id, env_id) not in releases:
            held.append((chunk_id, env_id))
    return held


# --------------------------------------------------------------------------- #
# Hub store invariants
# --------------------------------------------------------------------------- #


def check_hub_store(engine: Engine) -> list[Violation]:
    """Assert the hub store's durable invariants (transitions, epochs, delivery)."""
    violations: list[Violation] = []
    with engine.connect() as conn:
        # hub:one-transition-per-node-epoch — at most one accepted transition per
        # (chunk, from_node, epoch): the idempotency guarantee (D-090). A duplicate is a
        # double-apply — the fence or the idempotent replay probe failed.
        key = Counter(
            (row[0], row[1], row[2])
            for row in conn.execute(
                select(hub.transitions.c.chunk_id, hub.transitions.c.from_node_id, hub.transitions.c.epoch)
            )
        )
        for (chunk_id, from_node, epoch), n in key.items():
            if n > 1:
                violations.append(
                    Violation(
                        "hub:one-transition-per-node-epoch",
                        f"chunk {chunk_id} node {from_node} epoch {epoch} has {n} transitions",
                    )
                )

        # hub:epoch-consistent-transitions — no accepted transition carries an epoch
        # greater than the chunk's latest lease fact (D-007): a transition's fence is
        # always a lease the hub already knows, so a higher one means a zombie landed.
        latest_lease = {
            row[0]: row[1]
            for row in conn.execute(
                select(hub.lease_facts.c.chunk_id, func.max(hub.lease_facts.c.epoch)).group_by(
                    hub.lease_facts.c.chunk_id
                )
            )
        }
        for chunk_id, max_epoch in conn.execute(
            select(hub.transitions.c.chunk_id, func.max(hub.transitions.c.epoch)).group_by(hub.transitions.c.chunk_id)
        ):
            known = latest_lease.get(chunk_id)
            if known is None or max_epoch > known:
                violations.append(
                    Violation(
                        "hub:epoch-consistent-transitions",
                        f"chunk {chunk_id} transition epoch {max_epoch} exceeds latest lease {known}",
                    )
                )

        # hub:per-repo-land-idempotent — at most one landed fact per (chunk, repo):
        # a redelivery skips already-landed repos (D-091), so a duplicate is a double land.
        repo_lands = Counter(
            (row[0], row[1])
            for row in conn.execute(select(hub.delivery_repo_landed.c.chunk_id, hub.delivery_repo_landed.c.repo))
        )
        for (chunk_id, repo), n in repo_lands.items():
            if n > 1:
                violations.append(
                    Violation("hub:per-repo-land-idempotent", f"chunk {chunk_id} repo {repo} landed {n} times")
                )

        # hub:no-double-delivery — at most one whole-chunk delivery.landed terminal fact.
        landed = Counter(row[0] for row in conn.execute(select(hub.delivery_landed.c.chunk_id)))
        for chunk_id, n in landed.items():
            if n > 1:
                violations.append(
                    Violation("hub:no-double-delivery", f"chunk {chunk_id} has {n} delivery.landed facts")
                )

    # hub:merge-queue-single-state — a delivered chunk's newest transition is the
    # terminal, so it never reads as both landed and mid-flight (two states at once).
    # hub:derived-status-total — every chunk derives exactly one status without panic.
    violations.extend(_check_derivation_and_delivery(engine))
    return violations


def _check_derivation_and_delivery(engine: Engine) -> list[Violation]:
    """Run the real status derivation for every chunk; assert delivered ⇒ terminal."""
    violations: list[Violation] = []
    store = ChunkStore(engine, SystemClock())
    for chunk in store.list_all():
        facts = store.load_facts(chunk.chunk_id)
        if facts is None:
            violations.append(Violation("hub:derived-status-total", f"chunk {chunk.chunk_id} has no loadable facts"))
            continue
        try:
            derive_chunk_status(facts)
        except Exception as exc:  # a fact combination the derivation cannot resolve
            violations.append(
                Violation("hub:derived-status-total", f"chunk {chunk.chunk_id} derivation raised {exc!r}")
            )
            continue
        # Both terminal delivery facts require the terminal transition: merge-to-main's
        # ``delivery.landed`` and open-pr's ``pr.closed`` (D-065). An *open* PR
        # (``pr_opened`` without ``pr_closed``) is deliberately parked — no terminal
        # transition, environments held (D-066) — so it is never flagged here.
        if facts.delivery_landed or facts.pr_closed:
            newest = max(facts.transitions, key=lambda t: (t.recorded_at, t.epoch), default=None)
            if newest is None or newest.to_node_id != RESERVED_TERMINAL:
                target = None if newest is None else newest.to_node_id
                fact = "delivery.landed" if facts.delivery_landed else "pr.closed"
                violations.append(
                    Violation(
                        "hub:merge-queue-single-state",
                        f"chunk {chunk.chunk_id} is {fact} but newest transition targets {target}",
                    )
                )
    return violations


# --------------------------------------------------------------------------- #
# Combined entry — the sweep and the dev CLI both call this
# --------------------------------------------------------------------------- #


def check_invariants(*, runner_db_url: str | None = None, hub_db_url: str | None = None) -> list[Violation]:
    """Check both stores (whichever URLs are given) and return every violation found.

    Each store is opened read-only over its own engine; an empty list is the pass
    signal the sweep asserts after every armed crash.
    """
    violations: list[Violation] = []
    if runner_db_url is not None:
        violations.extend(check_runner_store(create_engine_from_url(runner_db_url)))
    if hub_db_url is not None:
        violations.extend(check_hub_store(create_engine_from_url(hub_db_url)))
    return violations
