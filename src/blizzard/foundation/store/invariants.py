"""The facts-level invariant checker (``bzh:invariant-checker``).

After any crash → restart → recover cycle, the durable facts in both stores must still
satisfy the correctness conditions the design rests on. A violation names the exact
broken invariant, so a failure points at the window and the rule. Because both stores
are facts-only (``bzh:facts-not-status``), every check is a plain query; nothing mutates."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, func, select

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Executor
from blizzard.hub.domain.work import newest_live_route, newest_live_route_token
from blizzard.hub.store import schema as hub
from blizzard.hub.store.internal.chunk_store import ChunkStore, _deserialize_default_model
from blizzard.runner.store import schema as runner


@dataclass(frozen=True)
class Violation:
    """One broken invariant — its stable slug and a concrete detail of the breach."""

    invariant: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.invariant}] {self.detail}"


# --------------------------- Runner store invariants ----------------------- #


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

        # runner:gapless-outbound-seq — the outbound buffer's seqs are a contiguous range;
        # a hole would break FIFO idempotent replay.
        seqs = sorted(row[0] for row in conn.execute(select(runner.outbound_buffer.c.seq)))
        if seqs:
            expected = list(range(seqs[0], seqs[0] + len(seqs)))
            if seqs != expected:
                missing = sorted(set(expected) - set(seqs))
                violations.append(
                    Violation("runner:gapless-outbound-seq", f"outbound seqs not gapless; missing {missing}")
                )

        # runner:one-open-pause-park-per-lease — a lease has at most one *open* pause-park
        # (a park fact with no pause-resume at or after it) (issue #46). A re-pause is legal.
        open_parks = Counter(lease_id for lease_id, _ in _open_pause_parks(conn))
        for lease_id, n in open_parks.items():
            if n > 1:
                violations.append(
                    Violation("runner:one-open-pause-park-per-lease", f"lease {lease_id} has {n} open pause-parks")
                )

        # runner:usage-attributed-once — a harness invocation's usage is attributed
        # exactly once per (lease, generation, kind) (epic #57, issue #58).
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
        # once per (lease, epoch) (issue #113).
        nudge_key = Counter(
            (row[0], row[1]) for row in conn.execute(select(runner.nudge_facts.c.lease_id, runner.nudge_facts.c.epoch))
        )
        for (lease_id, epoch), n in nudge_key.items():
            if n > 1:
                violations.append(
                    Violation("runner:nudge-at-most-once", f"lease {lease_id} epoch {epoch} has {n} nudge facts")
                )

        # runner:checks-recorded-when-marked — a `checks_ran` marker implies its check
        # result rows exist (issue #114).
        marked = {
            (row[0], row[1]) for row in conn.execute(select(runner.checks_ran.c.lease_id, runner.checks_ran.c.epoch))
        }
        have_rows = {
            (row[0], row[1])
            for row in conn.execute(select(runner.check_results.c.lease_id, runner.check_results.c.epoch))
        }
        for lease_id, epoch in sorted(marked - have_rows):
            violations.append(
                Violation(
                    "runner:checks-recorded-when-marked",
                    f"lease {lease_id} epoch {epoch} is marked checks-ran but has no check_results rows",
                )
            )

        # NOT checked, deliberately: "a pause-parked lease has no closure" (issue #46) — it is
        # false on a legal history; pinned by tests/test_pin_foundation.py.
    return violations


def _open_pause_parks(conn) -> list[tuple[str, datetime]]:  # type: ignore[no-untyped-def]
    """(lease_id, parked_at) for every pause-park with no pause-resume at or after it.

    The plain-query mirror of the store adapter's ``_pause_park_is_open`` — same ``>=``
    (a same-instant resume is a resume) and same per-lease correlation."""
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


# ----------------------------- Hub store invariants ------------------------ #


def check_hub_store(engine: Engine) -> list[Violation]:
    """Assert the hub store's durable invariants (transitions, epochs, delivery)."""
    violations: list[Violation] = []
    with engine.connect() as conn:
        # hub:one-transition-per-node-epoch — at most one accepted transition per
        # (chunk, from_node, epoch): the idempotency guarantee. A duplicate is a double-apply.
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

        # hub:epoch-consistent-transitions — no accepted transition carries an epoch greater
        # than the chunk's latest lease fact; a higher one means a zombie landed.
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

        # hub:route-seq-unique — per-chunk route ``seq`` is unique across ``route_created`` +
        # ``route_released`` + ``route_token_minted`` combined (issues #41, #84a).
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
        # per (chunk, node, epoch, name) (issue #67).
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

        # hub:pr-opened-idempotent — at most one pr.opened fact per (chunk, repo), also
        # guarded by ``uq_delivery_pr_opened_chunk_repo``.
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

        # hub:one-live-exec-slot — at most one hub_exec_slot row is live (``released_at IS
        # NULL``) at a time (#65; pinned by tests/test_pin_foundation.py).
        live_slots = conn.execute(
            select(func.count()).select_from(hub.hub_exec_slot).where(hub.hub_exec_slot.c.released_at.is_(None))
        ).scalar()
        if (live_slots or 0) > 1:
            violations.append(Violation("hub:one-live-exec-slot", f"{live_slots} hub-execution slots are live at once"))

    # hub:one-migration-per-node-epoch + hub:migration-pin-consistent — a cross-graph
    # migration (#90) is all-or-nothing: the fact and its re-pin land together.
    violations.extend(_check_migrations(engine))
    # hub:merge-queue-single-state — a delivered chunk's newest transition is the terminal.
    # hub:derived-status-total — every chunk derives exactly one status without panic.
    violations.extend(_check_derivation_and_delivery(engine))
    # hub:live-route-has-token — every chunk with a live route has a live route token
    # (issue #84b/#84a).
    violations.extend(_check_route_tokens(engine))
    return violations


def _check_migrations(engine: Engine) -> list[Violation]:
    """Assert a cross-graph migration (#90) is atomic and idempotent in the durable facts.

    ``hub:one-migration-per-node-epoch`` — one row per ``(chunk, from_node, epoch)``;
    ``hub:migration-pin-consistent`` — the chunk carries the newest migration's target pin;
    ``hub:migration-route-released`` — a runner landing released the route (a hub landing, issue #111, is exempt)."""
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
        # A migration's landed node executor (issue #111). Node ids are globally-unique, so
        # one node_id -> executor map resolves any landing node.
        landed_executor = {
            row.node_id: row.executor
            for row in conn.execute(select(hub.graph_nodes.c.node_id, hub.graph_nodes.c.executor))
        }
        # The latest route release per chunk — a runner-landing migration's ``recorded_at``
        # is never above the chunk's newest release.
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
            # **Membership**, not equality against `[model_after]` (issue #144) — the list may
            # legitimately grow afterwards (pinned by tests/test_invariant_checker.py).
            elif m.model_after is not None and m.model_after not in _deserialize_default_model(chunk.default_model):  # type: ignore[attr-defined]
                violations.append(
                    Violation(
                        "hub:migration-pin-consistent",
                        f"chunk {chunk_id} default_model {chunk.default_model!r} does not carry "  # type: ignore[attr-defined]
                        f"{m.model_after}, which its newest migration re-pinned",  # type: ignore[attr-defined]
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
            facts.status()
        except Exception as exc:  # a fact combination the derivation cannot resolve
            violations.append(
                Violation("hub:derived-status-total", f"chunk {chunk.chunk_id} derivation raised {exc!r}")
            )
            continue
        # Both terminal delivery facts require the terminal transition (issue #63):
        # ``delivery.landed`` and ``pr.closed``. An *open* PR is parked, so it is not flagged.
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

    A live route with no qualifying token fact means the mint never landed in the same
    store write as its route. That leaves every chunk-scoped write for the chunk
    permanently rejected under ``route_token_mode=enforce``, with no re-key possible."""
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


# -------------------------------- Combined entry --------------------------- #


def check_invariants(*, runner_db_url: str | None = None, hub_db_url: str | None = None) -> list[Violation]:
    """Check both stores (whichever URLs are given) and return every violation found.

    Each store is opened read-only over its own engine; an empty list means every
    checked invariant holds.
    """
    violations: list[Violation] = []
    if runner_db_url is not None:
        violations.extend(check_runner_store(create_engine_from_url(runner_db_url)))
    if hub_db_url is not None:
        violations.extend(check_hub_store(create_engine_from_url(hub_db_url)))
    return violations
