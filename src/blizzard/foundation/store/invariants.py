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
from datetime import datetime

from sqlalchemy import Engine, func, select

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Executor
from blizzard.hub.domain.work import derive_chunk_status, newest_live_route, newest_live_route_token
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
        # (``bzh:facts-not-status``); at most one per chunk (MAX_AGENTS math).
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
        # monotonic and gapless; one runner per store, so the seqs must be a
        # contiguous range with no holes (a hole would break FIFO idempotent replay).
        seqs = sorted(row[0] for row in conn.execute(select(runner.outbound_buffer.c.seq)))
        if seqs:
            expected = list(range(seqs[0], seqs[0] + len(seqs)))
            if seqs != expected:
                missing = sorted(set(expected) - set(seqs))
                violations.append(
                    Violation("runner:gapless-outbound-seq", f"outbound seqs not gapless; missing {missing}")
                )

        # runner:one-open-pause-park-per-lease — a lease has at most one *open* pause-park
        # (a park fact with no pause-resume at or after it) (issue #46). The park is
        # additive and append-only, so the only thing keeping it single is the writer's
        # guard: PULL parks a lease only when it is not already in
        # ``pause_parked_lease_ids()``. Drop that guard and every tick appends another park
        # for the same standing pause — unbounded growth, and an ``open_pause_park`` whose
        # answer depends on which duplicate it reads. A re-pause (paused -> resumed ->
        # paused again on one lease) is legitimate and does *not* breach this: the earlier
        # park is closed by its resume, so only the newest is open.
        open_parks = Counter(lease_id for lease_id, _ in _open_pause_parks(conn))
        for lease_id, n in open_parks.items():
            if n > 1:
                violations.append(
                    Violation("runner:one-open-pause-park-per-lease", f"lease {lease_id} has {n} open pause-parks")
                )

        # runner:usage-attributed-once — a harness invocation's usage is attributed
        # exactly once per (lease, generation, kind) (epic #57, issue #58). Append-only by
        # design (a retry/resume within a lease mints a new generation and so a genuinely
        # new row) and idempotent by construction (`record_usage`'s own check-then-insert,
        # not a DB constraint — `bzh:sql-portable`) — a duplicate here means that guard was
        # bypassed, e.g. by a second writer never routing through `record_usage`.
        usage_rows = select(runner.usage_facts.c.lease_id, runner.usage_facts.c.generation, runner.usage_facts.c.kind)
        usage_key = Counter((row[0], row[1], row[2]) for row in conn.execute(usage_rows))
        for (lease_id, generation, kind), n in usage_key.items():
            if n > 1:
                violations.append(
                    Violation(
                        "runner:usage-attributed-once",
                        f"lease {lease_id} generation {generation} kind {kind} has {n} usage facts",
                    )
                )

        # runner:nudge-at-most-once — a lease's `produces`-unmet nudge fires at most
        # once per (lease, epoch) (issue #113, Phase 4). `record_nudge_fired` is an
        # insert never an upsert, gated by `_advance_exited_worker`'s own
        # check-then-insert (not a DB constraint — `bzh:sql-portable`), so a duplicate
        # here means that guard was bypassed — the crash-correctness property the
        # `nudge.*` crash points exist to prove holds across a kill -9 at either one.
        nudge_key = Counter(
            (row[0], row[1]) for row in conn.execute(select(runner.nudge_facts.c.lease_id, runner.nudge_facts.c.epoch))
        )
        for (lease_id, epoch), n in nudge_key.items():
            if n > 1:
                violations.append(
                    Violation("runner:nudge-at-most-once", f"lease {lease_id} epoch {epoch} has {n} nudge facts")
                )

        # NOT checked, deliberately: "a pause-parked lease has no closure" (issue #46 plan §7).
        # It reads like the natural companion to the rule above, and it is **false on a legal
        # history**: pause a chunk, then detach it. `_reconcile_leases` abandons the lease —
        # closure `released`, envs freed — and records no pause-park resume, so the park is still
        # open over a closed lease. The plan wants detach to win there, so that history is
        # correct and the invariant, not the loop, is what is wrong. An invariant must hold at
        # every instant of every legal history (this checker runs after arbitrary kill -9s), so a
        # rule with a legitimate counterexample cannot live here at any strength: scoping it to
        # unclosed leases makes it the tautology "an unclosed lease has no closure".
        #
        # The property it was reaching for — `_kill_and_park_paused` must not close the lease it
        # parks, which is what FILL's `_reconcile_interrupted_claims` and ADVANCE's
        # `_advance_held_chunk` rest on when they skip a chunk with an active lease — is a
        # statement about **loop behavior**, not about durable facts, and its home is the
        # component tier: `tests/test_chunk_pause.py` asserts it directly and independently per
        # seam. Nor is a stale park over a closed lease a leak to plug: every reader of
        # `pause_parked_lease_ids()` / `parked_lease_ids()` first iterates `list_active_leases()`,
        # so a park is only ever consulted for a live lease — the same property the older
        # ask-park (`park_facts`) has always rested on, with the identical exposure and no
        # invariant of its own.
    return violations


def _open_pause_parks(conn) -> list[tuple[str, datetime]]:  # type: ignore[no-untyped-def]
    """(lease_id, parked_at) for every pause-park with no pause-resume at or after it.

    The plain-query mirror of the store adapter's ``_pause_park_is_open`` — same
    ``>=`` (a same-instant resume is a resume) and same per-lease correlation, so the
    checker and the loop agree on what "parked" means."""
    resumes: dict[str, list[datetime]] = {}
    for lease_id, resumed_at in conn.execute(
        select(runner.pause_park_resumes.c.lease_id, runner.pause_park_resumes.c.resumed_at)
    ):
        resumes.setdefault(lease_id, []).append(resumed_at)
    return [
        (lease_id, parked_at)
        for lease_id, parked_at in conn.execute(select(runner.pause_parks.c.lease_id, runner.pause_parks.c.parked_at))
        if not any(r >= parked_at for r in resumes.get(lease_id, ()))
    ]


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
        # (chunk, from_node, epoch): the idempotency guarantee. A duplicate is a
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
        # greater than the chunk's latest lease fact: a transition's fence is
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

        # hub:route-seq-unique — per-chunk route ``seq`` is unique across
        # ``route_created`` + ``route_released`` + ``route_token_minted`` combined
        # (issue #41, joined by ``route_token_minted`` in #84a): the three tables share
        # one counter (``ChunkStore._next_route_seq``) so a create/release/token-mint
        # triple is totally ordered even at a same-instant timestamp tie
        # (``work.newest_live_route``/``newest_live_route_token``). A duplicate means
        # two route events raced past the allocator uncaught — exactly the tie #41
        # closed, reopened.
        route_seqs = Counter(
            (row[0], row[1]) for row in conn.execute(select(hub.route_created.c.chunk_id, hub.route_created.c.seq))
        )
        route_seqs.update(
            (row[0], row[1]) for row in conn.execute(select(hub.route_released.c.chunk_id, hub.route_released.c.seq))
        )
        route_seqs.update(
            (row[0], row[1])
            for row in conn.execute(select(hub.route_token_minted.c.chunk_id, hub.route_token_minted.c.seq))
        )
        for (chunk_id, seq), n in route_seqs.items():
            if n > 1:
                violations.append(
                    Violation("hub:route-seq-unique", f"chunk {chunk_id} seq {seq} used by {n} route events")
                )

        # hub:per-repo-land-idempotent — at most one landed fact per (chunk, repo):
        # a redelivery skips already-landed repos, so a duplicate is a double land.
        repo_lands = Counter(
            (row[0], row[1])
            for row in conn.execute(select(hub.delivery_repo_landed.c.chunk_id, hub.delivery_repo_landed.c.repo))
        )
        for (chunk_id, repo), n in repo_lands.items():
            if n > 1:
                violations.append(
                    Violation("hub:per-repo-land-idempotent", f"chunk {chunk_id} repo {repo} landed {n} times")
                )

        # hub:per-repo-marker-idempotent — at most one `merged/<repo>` marker artifact
        # per (chunk, node, epoch, name): #67's generic-marker counterpart to
        # `hub:per-repo-land-idempotent` above — a re-run skips a repo whose marker
        # already exists (`HubNodeExecutor`/the mid-run callback), so a duplicate here
        # means that idempotent-append guard failed to hold.
        markers = Counter(
            (row[0], row[1], row[2], row[3])
            for row in conn.execute(
                select(
                    hub.artifacts.c.chunk_id, hub.artifacts.c.node_id, hub.artifacts.c.epoch, hub.artifacts.c.name
                ).where(hub.artifacts.c.name.like("merged/%"))
            )
        )
        for (chunk_id, node_id, epoch, name), n in markers.items():
            if n > 1:
                violations.append(
                    Violation(
                        "hub:per-repo-marker-idempotent",
                        f"chunk {chunk_id} node {node_id} epoch {epoch} has {n} `{name}` marker artifacts",
                    )
                )

        # hub:pr-opened-idempotent — at most one pr.opened fact per (chunk, repo): a
        # racing redelivery is caught by ``uq_delivery_pr_opened_chunk_repo`` at the store
        # layer (20260716_2206_hub_pr_opened_idempotent), so a duplicate here means that guard
        # failed to hold.
        pr_opens = Counter(
            (row[0], row[1])
            for row in conn.execute(select(hub.delivery_pr_opened.c.chunk_id, hub.delivery_pr_opened.c.repo))
        )
        for (chunk_id, repo), n in pr_opens.items():
            if n > 1:
                violations.append(
                    Violation("hub:pr-opened-idempotent", f"chunk {chunk_id} repo {repo} has {n} pr.opened facts")
                )

        # hub:no-double-delivery — at most one whole-chunk delivery.landed terminal fact.
        landed = Counter(row[0] for row in conn.execute(select(hub.delivery_landed.c.chunk_id)))
        for chunk_id, n in landed.items():
            if n > 1:
                violations.append(
                    Violation("hub:no-double-delivery", f"chunk {chunk_id} has {n} delivery.landed facts")
                )

        # hub:one-live-exec-slot — at most one hub_exec_slot row is live
        # (``released_at IS NULL``) at a time (#65): the fleet-wide serialization slot
        # is a FACT, not an in-process lock, precisely so this is assertable after any
        # crash — two live slots would mean two chunks' hub command nodes could run
        # concurrently, the exact hazard the slot exists to close.
        live_slots = conn.execute(
            select(func.count()).select_from(hub.hub_exec_slot).where(hub.hub_exec_slot.c.released_at.is_(None))
        ).scalar()
        if (live_slots or 0) > 1:
            violations.append(Violation("hub:one-live-exec-slot", f"{live_slots} hub-execution slots are live at once"))

    # hub:one-migration-per-node-epoch + hub:migration-pin-consistent — a cross-graph
    # migration (#90) is all-or-nothing: recorded once per (chunk, from_node, epoch), and
    # its re-pin lands with it (never a migration fact without the graph/model pin moving).
    violations.extend(_check_migrations(engine))
    # hub:merge-queue-single-state — a delivered chunk's newest transition is the
    # terminal, so it never reads as both landed and mid-flight (two states at once).
    # hub:derived-status-total — every chunk derives exactly one status without panic.
    violations.extend(_check_derivation_and_delivery(engine))
    # hub:live-route-has-token — every chunk with a live route has a live route token
    # (issue #84b/#84a).
    violations.extend(_check_route_tokens(engine))
    return violations


def _check_migrations(engine: Engine) -> list[Violation]:
    """Assert a cross-graph migration (#90) is atomic and idempotent in the durable facts.

    ``hub:one-migration-per-node-epoch`` — at most one ``chunk_migrations`` row per
    ``(chunk, from_node, epoch)``: the natural key ``record_migration`` guards, so a
    crash-replay never lands a second migration. ``hub:migration-pin-consistent`` — a
    migrated chunk's ``graph_id`` (and its ``model`` when the newest migration re-pinned
    one) equals that newest migration's target: the re-pin is written in the **same
    transaction** as the fact, so a fact without its pin — or a pin without its fact — is
    the half-write a ``kill -9`` in the ``migrate.`` window must never leave.
    ``hub:migration-route-released`` — a **runner-landing** migration also releases the
    route in that same transaction (the migration ends the attempt and re-queues the chunk
    ``ready``), so it always carries a ``route_released`` at or after its ``recorded_at``. A
    runner-landing migration fact whose route release never landed is the other face of the
    torn ``migrate.`` write: the chunk would re-pin yet keep its stale claim, unclaimable
    under the new graph. A migration landing on a **hub-executed** node (issue #111) is the
    deliberate exception: it **retains** the route so the hub keeps the chunk and drives the
    landed hub node via the holding runner's ADVANCE poll (deriving ``delivering``, exactly
    as a transition into a hub node does), so no ``route_released`` is expected and the
    assertion applies to runner landings alone. (Artifact co-persistence — the third
    limb of the atomic write — is not independently countable from the store without the
    graph definition, so it is asserted at the component tier over a real
    ``record_migration`` in ``test_migration_store``; here we assert the two facets a torn
    write leaves observable in the durable facts alone.)
    """
    violations: list[Violation] = []
    with engine.connect() as conn:
        key = Counter(
            (row[0], row[1], row[2])
            for row in conn.execute(
                select(
                    hub.chunk_migrations.c.chunk_id,
                    hub.chunk_migrations.c.from_node_id,
                    hub.chunk_migrations.c.epoch,
                )
            )
        )
        for (chunk_id, from_node, epoch), n in key.items():
            if n > 1:
                violations.append(
                    Violation(
                        "hub:one-migration-per-node-epoch",
                        f"chunk {chunk_id} node {from_node} epoch {epoch} has {n} migrations",
                    )
                )

        newest: dict[str, object] = {}
        for m in conn.execute(select(hub.chunk_migrations)):
            cur = newest.get(m.chunk_id)
            if cur is None or (m.recorded_at, m.epoch) >= (cur.recorded_at, cur.epoch):  # type: ignore[attr-defined]
                newest[m.chunk_id] = m
        chunks = {c.chunk_id: c for c in conn.execute(select(hub.chunks))}
        # A migration's landed node executor (issue #111): a hub landing retains the route
        # by design, so it is exempt from the route-released assertion below. Node ids are
        # globally-unique, so one node_id -> executor map resolves any landing node.
        landed_executor = {
            row.node_id: row.executor
            for row in conn.execute(select(hub.graph_nodes.c.node_id, hub.graph_nodes.c.executor))
        }
        # The latest route release per chunk — a runner-landing migration releases the route
        # in its own transaction, so its ``recorded_at`` is never above the chunk's newest
        # release (a hub landing retains it — checked below).
        latest_release: dict[str, datetime] = {}
        for r in conn.execute(select(hub.route_released.c.chunk_id, hub.route_released.c.released_at)):
            cur = latest_release.get(r.chunk_id)
            if cur is None or r.released_at > cur:
                latest_release[r.chunk_id] = r.released_at
        for chunk_id, m in newest.items():
            chunk = chunks.get(chunk_id)
            if chunk is None:
                continue
            if chunk.graph_id != m.to_graph_id:  # type: ignore[attr-defined]
                violations.append(
                    Violation(
                        "hub:migration-pin-consistent",
                        f"chunk {chunk_id} pinned {chunk.graph_id} but its newest migration targets {m.to_graph_id}",  # type: ignore[attr-defined]
                    )
                )
            elif m.model_after is not None and chunk.model != m.model_after:  # type: ignore[attr-defined]
                violations.append(
                    Violation(
                        "hub:migration-pin-consistent",
                        f"chunk {chunk_id} model {chunk.model} but its newest migration re-pinned {m.model_after}",  # type: ignore[attr-defined]
                    )
                )
            # A hub-landing migration (issue #111) retains the route by design — it is not a
            # torn write, so it is exempt from the route-released assertion.
            lands_on_hub = landed_executor.get(m.landed_node_id) == Executor.HUB  # type: ignore[attr-defined]
            released = latest_release.get(chunk_id)
            if not lands_on_hub and (released is None or released < m.recorded_at):  # type: ignore[attr-defined]
                violations.append(
                    Violation(
                        "hub:migration-route-released",
                        f"chunk {chunk_id} migrated at {m.recorded_at} but no route release landed "  # type: ignore[attr-defined]
                        "with it — a torn migrate. write kept the stale claim",
                    )
                )
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
        # ``delivery.landed`` and open-pr's ``pr.closed``. An *open* PR
        # (``pr_opened`` without ``pr_closed``) is deliberately parked — no terminal
        # transition, environments held — so it is never flagged here.
        #
        # This is the facts-level embodiment of #63's "DONE derives from *reaching* the
        # terminal transition, never from a landed fact alone": a whole-chunk ``delivery.landed``
        # fact that is not paired with the terminal transition would be a chunk merged yet
        # not-terminal — read as both landed and mid-flight (two states), the "un-merged"
        # corruption. The complementary case #63 makes legal — a chunk merged into a
        # post-merge node (per-repo ``delivery.repo_landed`` facts, a NON-terminal newest
        # transition, and no whole-chunk ``delivery.landed``) — is correctly not flagged here:
        # it carries no whole-chunk terminal fact, so it derives its live status, exactly as
        # #63 requires. "No double delivery" is held by ``hub:no-double-delivery`` +
        # ``hub:per-repo-land-idempotent`` above (append-only lands, never removed → never un-merged).
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


def _check_route_tokens(engine: Engine) -> list[Violation]:
    """Every chunk with a live route has a live route token (issue #84b).

    The derivation itself (:func:`~blizzard.hub.domain.work.newest_live_route_token`)
    can never be *ambiguous* — it is a ``max()`` over candidates, so it always
    resolves to at most one fact — what it *can* legitimately fail to find is a live
    route with no qualifying token fact at all: the mint fact
    (``ClaimService._claim_locked``) failing to land in the same store write as its
    route (``record_route``), or a kill-9 landing between the two inserts of a
    non-atomic adapter. Either would leave the chunk's every chunk-scoped write
    permanently rejected under ``route_token_mode=enforce`` with no re-key possible
    (re-key itself requires a live route, but derives no token to rotate)."""
    violations: list[Violation] = []
    store = ChunkStore(engine, SystemClock())
    for chunk in store.list_all():
        facts = store.load_facts(chunk.chunk_id)
        if facts is None:
            continue
        live_route = newest_live_route(facts.routes_created, facts.routes_released)
        if live_route is None:
            continue
        live_token = newest_live_route_token(facts.routes_created, facts.routes_released, facts.route_tokens_minted)
        if live_token is None:
            violations.append(
                Violation("hub:live-route-has-token", f"chunk {chunk.chunk_id} has a live route but no live token")
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
