"""SQLAlchemy adapter for the fleet-registry seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only,
status derived (``bzh:facts-not-status``): each brake derives from the newest row of its
own fact table.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Sequence
from datetime import datetime

from sqlalchemy import Connection

from blizzard.foundation.runner_version import VersionRefusalReason
from blizzard.foundation.store.batching import id_batches
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.domain.runners.registration import (
    DeclaredSubscription,
    IWriteRunnerRegistry,
    RecordedPause,
    RunnerAddition,
    RunnerBinding,
    RunnerCapability,
    RunnerDeclining,
    RunnerRegistration,
    ServedEntry,
    SubscriptionUsageMiss,
    SubscriptionUsageSample,
    TokenRotation,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.host_usage import read_host_rosters, read_host_usage, stored_usage_windows
from blizzard.hub.store.internal.newest_fact import newest_fact_select
from blizzard.hub.store.scoped import ScopedConnections, statements_of


def locked_token_hash(conn: Connection, runner_id: str) -> str | None:
    """The runner's current token hash, read under its registration row's write lock — a no-op
    ``UPDATE`` as the transaction's first statement, then the read (``bzh:store-exclusive-write``;
    ``FOR UPDATE`` renders nothing on SQLite). ``conn`` is one a scoped ``write`` yielded, whose
    tenant both statements are confined to."""
    stmts = statements_of(conn)
    registrations = s.runner_registrations
    conn.execute(
        stmts.update(registrations)
        .where(registrations.c.runner_id == runner_id)
        .values(runner_id=registrations.c.runner_id)
    )
    return conn.execute(
        stmts.select(registrations, registrations.c.token_hash).where(registrations.c.runner_id == runner_id)
    ).scalar_one_or_none()


class RunnerRegistryStore:
    """Read-write fleet-registry adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    # --- reads --------------------------------------------------------------

    def get_runner(self, runner_id: str) -> RunnerRegistration | None:
        with self._store.read("get_runner") as conn:
            return self.get_runner_conn(conn, runner_id)

    def is_retired(self, runner_id: str) -> bool:
        with self._store.read("is_retired") as conn:
            return self._lifecycle(conn, runner_id)[0]

    def get_runner_conn(self, conn, runner_id: str) -> RunnerRegistration | None:
        """`get_runner`'s already-open-connection sibling — the locked-transaction seam's
        own read (``bzh:store-exclusive-write``), resolved on the caller's connection
        rather than a fresh one."""
        row = conn.execute(
            self._store.select(s.runner_registrations).where(s.runner_registrations.c.runner_id == runner_id)
        ).one_or_none()
        if row is None:
            return None
        hosted = row.host_id is not None
        registration = self._registration(
            row,
            self._paused(conn, runner_id),
            self._local_pause_detail(conn, runner_id),
            [] if hosted else self._external_usage(conn, runner_id),
            [] if hosted else self._external_usage_misses(conn, runner_id),
            self._lifecycle(conn, runner_id),
        )
        return self._reading_hosts(conn, [registration])[0]

    def list_runners(self, *, include_retired: bool = False, host_id: str | None = None) -> list[RunnerRegistration]:
        with self._store.read("list_runners") as conn:
            query = self._store.select(s.runner_registrations)
            if host_id is not None:
                query = query.where(s.runner_registrations.c.host_id == host_id)
            rows = conn.execute(
                query.order_by(s.runner_registrations.c.added_at, s.runner_registrations.c.runner_id)
            ).all()
            return self._hydrate(conn, rows, include_retired=include_retired)

    def get_runners(self, runner_ids: Iterable[str]) -> dict[str, RunnerRegistration]:
        ids = list(dict.fromkeys(runner_ids))
        if not ids:
            return {}
        registrations = s.runner_registrations
        with self._store.read("get_runners") as conn:
            rows = [
                row
                for batch in id_batches(ids)
                for row in conn.execute(self._store.select(registrations).where(registrations.c.runner_id.in_(batch)))
            ]
            return {r.runner_id: r for r in self._hydrate(conn, rows, include_retired=True)}

    def _hydrate(self, conn: Connection, rows: Sequence, *, include_retired: bool) -> list[RunnerRegistration]:
        """The registrations ``rows`` stand for, in row order — retired ones only when ``include_retired``."""
        runner_ids = [row.runner_id for row in rows]
        lifecycle = self._lifecycle_many(conn, runner_ids)
        if not include_retired:
            rows = [row for row in rows if not lifecycle[row.runner_id][0]]
            runner_ids = [row.runner_id for row in rows]
        paused = self._paused_many(conn, runner_ids)
        local_pause = self._local_pause_detail_many(conn, runner_ids)
        # A hosted runner's usage is its host's, so its own rows are not read.
        alone = [row.runner_id for row in rows if row.host_id is None]
        usage = self._external_usage_many(conn, alone)
        usage_misses = self._external_usage_misses_many(conn, alone)
        return self._reading_hosts(
            conn,
            [
                self._registration(
                    row,
                    paused[row.runner_id],
                    local_pause[row.runner_id],
                    usage.get(row.runner_id, []),
                    usage_misses.get(row.runner_id, []),
                    lifecycle[row.runner_id],
                )
                for row in rows
            ],
        )

    def _reading_hosts(self, conn: Connection, registrations: list[RunnerRegistration]) -> list[RunnerRegistration]:
        """Each hosted registration presenting its host's usage and roster, in two bulk reads however many hosts
        stand behind the page; a registration standing alone is returned as hydrated."""
        host_ids = sorted({r.host_id for r in registrations if r.host_id is not None})
        if not host_ids:
            return registrations
        usage = read_host_usage(self._store, conn, host_ids)
        rosters = read_host_rosters(self._store, conn, host_ids)
        return [
            r
            if r.host_id is None
            else r.reading_host_usage(usage[r.host_id].samples, usage[r.host_id].misses, rosters[r.host_id])
            for r in registrations
        ]

    def list_pause_facts_since(
        self, since: datetime, *, limit: int, runners: Collection[str] | None = None
    ) -> list[RecordedPause]:
        if runners is not None and not runners:
            return []
        with self._store.read("list_pause_facts_since") as conn:
            fleet_query = self._store.select(s.runner_pause_facts).where(s.runner_pause_facts.c.set_at >= since)
            local_query = self._store.select(s.runner_local_pause_facts).where(
                s.runner_local_pause_facts.c.set_at >= since
            )
            if runners is not None:
                fleet_query = fleet_query.where(s.runner_pause_facts.c.runner_id.in_(sorted(runners)))
                local_query = local_query.where(s.runner_local_pause_facts.c.runner_id.in_(sorted(runners)))
            fleet_rows = conn.execute(
                fleet_query.order_by(s.runner_pause_facts.c.set_at.desc(), s.runner_pause_facts.c.id.desc()).limit(
                    limit
                )
            ).all()
            local_rows = conn.execute(
                local_query.order_by(
                    s.runner_local_pause_facts.c.set_at.desc(), s.runner_local_pause_facts.c.id.desc()
                ).limit(limit)
            ).all()
        fleet = [
            RecordedPause(
                key=f"runner_pause_facts:{r.id}",
                at=r.set_at,
                runner_id=r.runner_id,
                local=False,
                paused=r.paused,
                by=r.set_by,
            )
            for r in fleet_rows
        ]
        local = [
            RecordedPause(
                key=f"runner_local_pause_facts:{r.id}",
                # `set_at` is the runner-machine's own clock, so a skewed one can float a
                # row out of this window — a known gap, not fixable without a schema change.
                at=r.set_at,
                runner_id=r.runner_id,
                local=True,
                paused=r.paused,
                by=r.set_by,
                reason=r.reason,
            )
            for r in local_rows
        ]
        return [*fleet, *local]

    def names_for(self, runner_ids: Iterable[str]) -> dict[str, str]:
        ids = list(dict.fromkeys(runner_ids))
        names: dict[str, str] = {}
        if not ids:
            return names
        with self._store.read("names_for") as conn:
            for batch in id_batches(ids):
                rows = conn.execute(
                    self._store.select(
                        s.runner_registrations, s.runner_registrations.c.runner_id, s.runner_registrations.c.name
                    ).where(s.runner_registrations.c.runner_id.in_(batch))
                ).all()
                names.update({row.runner_id: row.name for row in rows})
        return names

    # --- writes -------------------------------------------------------------

    def add(self, addition: RunnerAddition) -> None:
        with self._store.write("add") as conn:
            conn.execute(
                self._store.insert(s.runner_registrations).values(
                    runner_id=addition.runner_id,
                    name=addition.name,
                    token_hash=addition.token_hash,
                    added_at=addition.at,
                    added_by=addition.by,
                    projects=_served_json(addition.served),
                    host_id=addition.host_id,
                )
            )

    def record_registration(
        self,
        runner_id: str,
        *,
        name: str | None = None,
        workspace_id: str,
        env_capacity: int | None,
        public_url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        capabilities: tuple[RunnerCapability, ...] = (),
        subscriptions: tuple[DeclaredSubscription, ...] | None = None,
        gates: tuple[str, ...] = (),
        projects: tuple[ServedEntry, ...] | None = None,
        bindings: tuple[RunnerBinding, ...] | None = None,
        subscription: str | None = None,
        agents: int | None = None,
        declining: tuple[RunnerDeclining, ...] | None = None,
        version: str | None = None,
        at: datetime,
    ) -> bool:
        # Written unconditionally on every registration, `None`/empty verbatim included: the
        # overwrite on refresh is what converges a changed value on re-registration.
        redirect_uris_json = json.dumps(list(redirect_uris)) if redirect_uris else None
        capabilities_json = (
            json.dumps(
                [
                    {
                        "harness_id": c.harness_id,
                        "version": c.version,
                        "tiers": list(c.tiers),
                        "default": c.default,
                        "available": c.available,
                    }
                    for c in capabilities
                ]
            )
            if capabilities
            else None
        )
        # Unlike `capabilities`, an empty roster is kept distinct from an absent one:
        # `None` means the runner reported no roster, `[]` means it declared none.
        subscriptions_json = (
            json.dumps([{"slug": d.slug, "name": d.name, "provider": d.provider} for d in subscriptions])
            if subscriptions is not None
            else None
        )
        gates_json = json.dumps(list(gates)) if gates else None
        refreshed = {
            "workspace_id": workspace_id,
            "last_seen_at": at,
            "env_capacity": env_capacity,
            "public_url": public_url,
            "redirect_uris": redirect_uris_json,
            "capabilities": capabilities_json,
            "subscriptions": subscriptions_json,
            "gates": gates_json,
            "bindings": _bindings_json(bindings),
            "subscription": subscription,
            "agents": agents,
            "declining": _declining_json(declining),
            "reported_version": version,
            "version_refusal": None,
            "version_judged_at": at,
        }
        if name is not None:
            refreshed["name"] = name
        # `None` leaves the stored declaration as it is; `()` stores an empty one, which serves nothing.
        if projects is not None:
            refreshed["projects"] = _served_json(projects)
        registration = s.runner_registrations.c.runner_id == runner_id
        with self._store.write("record_registration") as conn:
            if not conn.execute(
                self._store.update(s.runner_registrations).where(registration).values(refreshed)
            ).rowcount:
                raise LookupError(f"runner {runner_id!r} was never added")
            # Stamped only while unset, so of two racing first registrations exactly one reads as first.
            first = conn.execute(
                self._store.update(s.runner_registrations)
                .where(registration, s.runner_registrations.c.registered_at.is_(None))
                .values(registered_at=at)
            )
            return bool(first.rowcount)

    def record_version_refusal(
        self, runner_id: str, *, version: str | None, reason: VersionRefusalReason, at: datetime
    ) -> None:
        with self._store.write("record_version_refusal") as conn:
            if not conn.execute(
                self._store.update(s.runner_registrations)
                .where(s.runner_registrations.c.runner_id == runner_id)
                .values(reported_version=version, version_refusal=reason.value, version_judged_at=at)
            ).rowcount:
                raise LookupError(f"runner {runner_id!r} was never added")

    def touch_last_seen(self, runner_id: str, *, at: datetime) -> bool:
        with self._store.write("touch_last_seen") as conn:
            result = conn.execute(
                self._store.update(s.runner_registrations)
                .where(
                    s.runner_registrations.c.runner_id == runner_id,
                    s.runner_registrations.c.registered_at.is_not(None),
                )
                .values(last_seen_at=at)
            )
            return bool(result.rowcount)

    def record_pause(self, runner_id: str, *, paused: bool, at: datetime, by: str) -> int:
        with self._store.write("record_pause") as conn:
            result = conn.execute(
                self._store.insert(s.runner_pause_facts).values(
                    runner_id=runner_id, paused=paused, set_at=at, set_by=by
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, reason: str | None = None
    ) -> int:
        with self._store.write("record_local_pause") as conn:
            result = conn.execute(
                self._store.insert(s.runner_local_pause_facts).values(
                    runner_id=runner_id, paused=paused, set_at=at, set_by=by, reason=reason
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_external_usage(
        self, runner_id: str, *, slug: str, name: str, sampled_at: datetime, windows_json: str, at: datetime
    ) -> None:
        # No FK, no known-runner requirement: the fact can legitimately arrive ahead of
        # the registration, and must not stall this runner's high-water mark waiting.
        # Upserts on (runner_id, slug) — one row per declared subscription,
        # so a sibling slug's row is untouched by this one's write.
        with self._store.write("record_external_usage") as conn:
            existing = conn.execute(
                self._store.select(s.runner_external_usage, s.runner_external_usage.c.runner_id).where(
                    s.runner_external_usage.c.runner_id == runner_id, s.runner_external_usage.c.slug == slug
                )
            ).one_or_none()
            if existing is None:
                conn.execute(
                    self._store.insert(s.runner_external_usage).values(
                        runner_id=runner_id,
                        slug=slug,
                        name=name,
                        sampled_at=sampled_at,
                        windows=windows_json,
                        updated_at=at,
                    )
                )
                return
            conn.execute(
                self._store.update(s.runner_external_usage)
                .where(s.runner_external_usage.c.runner_id == runner_id, s.runner_external_usage.c.slug == slug)
                .values(name=name, sampled_at=sampled_at, windows=windows_json, updated_at=at)
            )

    def record_external_usage_miss(
        self, runner_id: str, *, slug: str, name: str, missed_at: datetime, reason: str, at: datetime
    ) -> None:
        # Sibling to `record_external_usage`: same no-FK, no-known-runner-required upsert on
        # (runner_id, slug) — its own table, never overwriting the sample row it joins at read.
        with self._store.write("record_external_usage_miss") as conn:
            existing = conn.execute(
                self._store.select(s.runner_external_usage_misses, s.runner_external_usage_misses.c.runner_id).where(
                    s.runner_external_usage_misses.c.runner_id == runner_id,
                    s.runner_external_usage_misses.c.slug == slug,
                )
            ).one_or_none()
            if existing is None:
                conn.execute(
                    self._store.insert(s.runner_external_usage_misses).values(
                        runner_id=runner_id,
                        slug=slug,
                        name=name,
                        missed_at=missed_at,
                        reason=reason,
                        updated_at=at,
                    )
                )
                return
            conn.execute(
                self._store.update(s.runner_external_usage_misses)
                .where(
                    s.runner_external_usage_misses.c.runner_id == runner_id,
                    s.runner_external_usage_misses.c.slug == slug,
                )
                .values(name=name, missed_at=missed_at, reason=reason, updated_at=at)
            )

    def record_lifecycle(self, runner_id: str, *, retired: bool, at: datetime, by: str) -> int:
        with self._store.write("record_lifecycle") as conn:
            result = conn.execute(
                self._store.insert(s.runner_lifecycle_facts).values(
                    runner_id=runner_id, retired=retired, set_at=at, set_by=by
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def revoke_token(self, runner_id: str, *, at: datetime, by: str) -> int | None:
        # The revocation fact and the nulled hash land in one transaction, so a token always resolves or
        # reads as revoked; the row lock orders it against a concurrent rotation.
        with self._store.write("revoke_token") as conn:
            token_hash = locked_token_hash(conn, runner_id)
            if token_hash is None:
                return None
            result = conn.execute(
                self._store.insert(s.runner_token_revocations).values(
                    runner_id=runner_id, token_hash=token_hash, revoked_at=at, revoked_by=by
                )
            )
            conn.execute(
                self._store.update(s.runner_registrations)
                .where(s.runner_registrations.c.runner_id == runner_id)
                .values(token_hash=None)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def rotate_token(self, rotation: TokenRotation) -> int | None:
        # The replaced hash's revocation and the new hash land in one transaction, and the row lock makes a
        # concurrent rotation read the hash this one stored, so every replaced hash is revoked.
        with self._store.write("rotate_token") as conn:
            replaced = locked_token_hash(conn, rotation.runner_id)
            revocation_id: int | None = None
            if replaced is not None:
                result = conn.execute(
                    self._store.insert(s.runner_token_revocations).values(
                        runner_id=rotation.runner_id,
                        token_hash=replaced,
                        revoked_at=rotation.at,
                        revoked_by=rotation.by,
                    )
                )
                key = result.inserted_primary_key
                revocation_id = int(key[0]) if key is not None else 0
            conn.execute(
                self._store.update(s.runner_registrations)
                .where(s.runner_registrations.c.runner_id == rotation.runner_id)
                .values(token_hash=rotation.token_hash)
            )
            return revocation_id

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _paused(conn, runner_id: str) -> bool:
        """Derive the fleet's brake from the newest pause/resume fact, default False."""
        return RunnerRegistryStore._paused_many(conn, [runner_id])[runner_id]

    @staticmethod
    def _paused_many(conn, runner_ids: Sequence[str]) -> dict[str, bool]:
        """``_paused``'s grouped sibling and its one home — every listed runner's fleet
        brake off its own newest pause/resume fact, one query for every id in
        ``runner_ids`` rather than one query per runner (`list_runners`)."""
        result = dict.fromkeys(runner_ids, False)
        if not runner_ids:
            return result
        newest: dict[str, bool] = {}
        for batch in id_batches(runner_ids):
            rows = conn.execute(
                newest_fact_select(
                    statements_of(conn),
                    s.runner_pause_facts,
                    s.runner_pause_facts.c.runner_id,
                    batch,
                    s.runner_pause_facts.c.runner_id,
                    s.runner_pause_facts.c.paused,
                )
            ).all()
            for row in rows:
                newest[row.runner_id] = row.paused
        result.update(newest)
        return result

    @staticmethod
    def _lifecycle(conn, runner_id: str) -> tuple[bool, datetime | None, str | None]:
        """The runner's retirement, off its newest lifecycle fact."""
        return RunnerRegistryStore._lifecycle_many(conn, [runner_id])[runner_id]

    @staticmethod
    def _lifecycle_many(conn, runner_ids: Sequence[str]) -> dict[str, tuple[bool, datetime | None, str | None]]:
        """``_lifecycle``'s grouped sibling — every listed runner's ``(retired, at, by)`` off
        its newest lifecycle fact, one query per id batch. Defaults ``(False, None, None)``,
        and a reinstated runner's newest fact nulls ``at``/``by``."""
        result: dict[str, tuple[bool, datetime | None, str | None]] = dict.fromkeys(runner_ids, (False, None, None))
        if not runner_ids:
            return result
        for batch in id_batches(runner_ids):
            rows = conn.execute(
                newest_fact_select(
                    statements_of(conn),
                    s.runner_lifecycle_facts,
                    s.runner_lifecycle_facts.c.runner_id,
                    batch,
                    s.runner_lifecycle_facts.c.runner_id,
                    s.runner_lifecycle_facts.c.retired,
                    s.runner_lifecycle_facts.c.set_at,
                    s.runner_lifecycle_facts.c.set_by,
                )
            ).all()
            for row in rows:
                result[row.runner_id] = (True, row.set_at, row.set_by) if row.retired else (False, None, None)
        return result

    @staticmethod
    def _local_pause_detail(conn, runner_id: str) -> tuple[bool, str | None, str | None]:
        """The runner's own brake plus its cause, off the newest fact."""
        return RunnerRegistryStore._local_pause_detail_many(conn, [runner_id])[runner_id]

    @staticmethod
    def _local_pause_detail_many(conn, runner_ids: Sequence[str]) -> dict[str, tuple[bool, str | None, str | None]]:
        """``_local_pause_detail``'s grouped sibling and its one home — every listed
        runner's own brake plus its cause, off its own newest fact, one query for every id
        in ``runner_ids`` rather than one query per runner (`list_runners`).

        Defaults ``(False, None, None)``, and ``by``/``reason`` are nulled once a runner's
        newest fact is a *resume* — a stale cause must not outlive the brake it named."""
        result: dict[str, tuple[bool, str | None, str | None]] = dict.fromkeys(runner_ids, (False, None, None))
        if not runner_ids:
            return result
        newest: dict[str, tuple[bool, str | None, str | None]] = {}
        for batch in id_batches(runner_ids):
            rows = conn.execute(
                newest_fact_select(
                    statements_of(conn),
                    s.runner_local_pause_facts,
                    s.runner_local_pause_facts.c.runner_id,
                    batch,
                    s.runner_local_pause_facts.c.runner_id,
                    s.runner_local_pause_facts.c.paused,
                    s.runner_local_pause_facts.c.set_by,
                    s.runner_local_pause_facts.c.reason,
                )
            ).all()
            for row in rows:
                newest[row.runner_id] = (row.paused, row.set_by, row.reason)
        for runner_id, (paused, set_by, reason) in newest.items():
            result[runner_id] = (True, set_by, reason) if paused else (False, None, None)
        return result

    @staticmethod
    def _external_usage(conn, runner_id: str) -> list[tuple[str, str, datetime, str]]:
        """Every reported subscription's newest sample for this runner, raw,
        one row per slug. Empty for a runner that has never reported one."""
        return RunnerRegistryStore._external_usage_many(conn, [runner_id])[runner_id]

    @staticmethod
    def _external_usage_many(conn, runner_ids: Sequence[str]) -> dict[str, list[tuple[str, str, datetime, str]]]:
        """``_external_usage``'s grouped sibling and its one home — every listed runner's
        reported subscriptions, raw, one row per slug — ``(slug, name, sampled_at,
        windows_json)`` tuples — one query for every id in ``runner_ids`` rather than one
        query per runner (`list_runners`)."""
        grouped: dict[str, list[tuple[str, str, datetime, str]]] = {runner_id: [] for runner_id in runner_ids}
        if not runner_ids:
            return grouped
        for batch in id_batches(runner_ids):
            rows = conn.execute(
                statements_of(conn)
                .select(
                    s.runner_external_usage,
                    s.runner_external_usage.c.runner_id,
                    s.runner_external_usage.c.slug,
                    s.runner_external_usage.c.name,
                    s.runner_external_usage.c.sampled_at,
                    s.runner_external_usage.c.windows,
                )
                .where(s.runner_external_usage.c.runner_id.in_(batch))
                .order_by(s.runner_external_usage.c.runner_id, s.runner_external_usage.c.slug)
            ).all()
            for row in rows:
                grouped[row.runner_id].append((row.slug, row.name, row.sampled_at, row.windows))
        return grouped

    @staticmethod
    def _external_usage_misses(conn, runner_id: str) -> list[tuple[str, str, datetime, str]]:
        """Every reported subscription's newest miss for this runner, raw,
        one row per slug. Empty for a runner that has never reported one."""
        return RunnerRegistryStore._external_usage_misses_many(conn, [runner_id])[runner_id]

    @staticmethod
    def _external_usage_misses_many(conn, runner_ids: Sequence[str]) -> dict[str, list[tuple[str, str, datetime, str]]]:
        """``_external_usage_misses``'s grouped sibling and its one home — every listed
        runner's reported subscription misses, raw, one row per slug — ``(slug, name,
        missed_at, reason)`` tuples — one query for every id in ``runner_ids`` rather than
        one query per runner (`list_runners`)."""
        grouped: dict[str, list[tuple[str, str, datetime, str]]] = {runner_id: [] for runner_id in runner_ids}
        if not runner_ids:
            return grouped
        for batch in id_batches(runner_ids):
            rows = conn.execute(
                statements_of(conn)
                .select(
                    s.runner_external_usage_misses,
                    s.runner_external_usage_misses.c.runner_id,
                    s.runner_external_usage_misses.c.slug,
                    s.runner_external_usage_misses.c.name,
                    s.runner_external_usage_misses.c.missed_at,
                    s.runner_external_usage_misses.c.reason,
                )
                .where(s.runner_external_usage_misses.c.runner_id.in_(batch))
                .order_by(s.runner_external_usage_misses.c.runner_id, s.runner_external_usage_misses.c.slug)
            ).all()
            for row in rows:
                grouped[row.runner_id].append((row.slug, row.name, row.missed_at, row.reason))
        return grouped

    @staticmethod
    def _registration(
        row,
        hub_paused: bool,
        local_pause_detail: tuple[bool, str | None, str | None],
        external_usage: list[tuple[str, str, datetime, str]],
        external_usage_misses: list[tuple[str, str, datetime, str]],
        lifecycle: tuple[bool, datetime | None, str | None],
    ) -> RunnerRegistration:
        retired, retired_at, retired_by = lifecycle
        locally_paused, locally_paused_by, locally_paused_reason = local_pause_detail
        subscription_usage = tuple(
            SubscriptionUsageSample(
                slug=slug,
                name=name,
                sampled_at=sampled_at,
                windows=stored_usage_windows(windows_json),
            )
            for slug, name, sampled_at, windows_json in external_usage
        )
        subscription_usage_misses = tuple(
            SubscriptionUsageMiss(slug=slug, name=name, missed_at=missed_at, reason=reason)
            for slug, name, missed_at, reason in external_usage_misses
        )
        capabilities = tuple(
            RunnerCapability(
                harness_id=c["harness_id"],
                version=c.get("version"),
                tiers=tuple(c.get("tiers") or ()),
                default=bool(c.get("default", False)),
                available=bool(c.get("available", True)),
            )
            for c in (json.loads(row.capabilities) if row.capabilities else [])
        )
        # `NULL` (no roster reported) stays `None`; `"[]"` (an empty declared roster)
        # decodes to `()`, not `None` — the absent/empty distinction is kept.
        declared_subscriptions = (
            tuple(
                DeclaredSubscription(slug=d["slug"], name=d["name"], provider=d["provider"])
                for d in json.loads(row.subscriptions)
            )
            if row.subscriptions is not None
            else None
        )
        return RunnerRegistration(
            runner_id=row.runner_id,
            name=row.name,
            added_at=row.added_at,
            added_by=row.added_by,
            workspace_id=row.workspace_id,
            registered_at=row.registered_at,
            last_seen_at=row.last_seen_at,
            hub_paused=hub_paused,
            locally_paused=locally_paused,
            locally_paused_by=locally_paused_by,
            locally_paused_reason=locally_paused_reason,
            token_hash=row.token_hash,
            env_capacity=row.env_capacity,
            public_url=row.public_url,
            redirect_uris=tuple(json.loads(row.redirect_uris)) if row.redirect_uris else (),
            subscription_usage=subscription_usage,
            subscription_usage_misses=subscription_usage_misses,
            capabilities=capabilities,
            declared_subscriptions=declared_subscriptions,
            retired=retired,
            retired_at=as_utc(retired_at) if retired_at is not None else None,
            retired_by=retired_by,
            gates=tuple(json.loads(row.gates)) if row.gates else (),
            host_id=row.host_id,
            bindings=(
                tuple(RunnerBinding(b["project"], b["workspace_id"], b["agents"]) for b in json.loads(row.bindings))
                if row.bindings is not None
                else None
            ),
            subscription=row.subscription,
            agents=row.agents,
            declining=tuple(
                RunnerDeclining(
                    lever=d["lever"],
                    detail=d["detail"],
                    subscription=d.get("subscription"),
                    until=None if d.get("until") is None else as_utc(datetime.fromisoformat(d["until"])),
                )
                for d in (json.loads(row.declining) if row.declining else [])
            ),
            reported_version=row.reported_version,
            version_refusal=None if row.version_refusal is None else VersionRefusalReason(row.version_refusal),
            version_judged_at=row.version_judged_at,
            served=tuple(
                ServedEntry(entry=e["entry"], project_id=e.get("project_id"))
                for e in (json.loads(row.projects) if row.projects else [])
            ),
        )


def _served_json(served: tuple[ServedEntry, ...]) -> str:
    """The served declaration as stored: a JSON list of ``{entry, project_id}``, ``[]`` when it serves nothing."""
    return json.dumps([{"entry": e.entry, "project_id": e.project_id} for e in served])


def _bindings_json(bindings: tuple[RunnerBinding, ...] | None) -> str | None:
    """The declared bindings as stored: a JSON list of ``{project, workspace_id, agents}``; ``NULL`` for none sent,
    kept distinct from ``[]``."""
    if bindings is None:
        return None
    return json.dumps([{"project": b.project, "workspace_id": b.workspace_id, "agents": b.agents} for b in bindings])


def _declining_json(declining: tuple[RunnerDeclining, ...] | None) -> str | None:
    """The reported declining reasons as stored: a JSON list of ``{lever, detail, subscription, until}`` with ``until``
    an ISO UTC instant; ``NULL`` for none sent, which reads back as none declining, as ``[]`` does."""
    if declining is None:
        return None
    return json.dumps(
        [
            {
                "lever": d.lever,
                "detail": d.detail,
                "subscription": d.subscription,
                "until": None if d.until is None else iso_utc(d.until),
            }
            for d in declining
        ]
    )


def _conforms_registry(x: RunnerRegistryStore) -> IWriteRunnerRegistry:
    return x
