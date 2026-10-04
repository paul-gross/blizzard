"""SQLAlchemy adapter for the fleet-registry seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only,
status derived (``bzh:facts-not-status``): each brake derives from the newest row of its
own fact table; ``last_seen_at`` and ``token_hash`` are the refreshed-in-place columns.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import insert, select

from blizzard.foundation.store.batching import id_batches
from blizzard.foundation.store.utc import as_utc
from blizzard.hub.domain.runners.activity import ActivityEntry
from blizzard.hub.domain.runners.registration import (
    DeclaredSubscription,
    ExternalSubscriptionUsageWindow,
    IWriteRunnerRegistry,
    RunnerCapability,
    RunnerRegistration,
    SubscriptionUsageMiss,
    SubscriptionUsageSample,
    TokenRotation,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.newest_fact import newest_fact_select
from blizzard.wire.facts import ExternalSubscriptionUsageWindowFact


class RunnerRegistryStore:
    """Read-write fleet-registry adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    # --- reads --------------------------------------------------------------

    def get_runner(self, runner_id: str) -> RunnerRegistration | None:
        with self._store.read("get_runner") as conn:
            return self.get_runner_conn(conn, runner_id)

    def get_runner_conn(self, conn, runner_id: str) -> RunnerRegistration | None:  # type: ignore[no-untyped-def]
        """`get_runner`'s already-open-connection sibling — the locked-transaction seam's
        own read (``bzh:store-exclusive-write``), resolved on the caller's connection
        rather than a fresh one."""
        row = conn.execute(
            select(s.runner_registrations).where(s.runner_registrations.c.runner_id == runner_id)
        ).one_or_none()
        if row is None:
            return None
        return self._registration(
            row,
            self._paused(conn, runner_id),
            self._local_pause_detail(conn, runner_id),
            self._external_usage(conn, runner_id),
            self._external_usage_misses(conn, runner_id),
            self._lifecycle(conn, runner_id),
        )

    def list_runners(self, *, include_retired: bool = False) -> list[RunnerRegistration]:
        with self._store.read("list_runners") as conn:
            rows = conn.execute(select(s.runner_registrations).order_by(s.runner_registrations.c.registered_at)).all()
            runner_ids = [row.runner_id for row in rows]
            lifecycle = self._lifecycle_many(conn, runner_ids)
            if not include_retired:
                rows = [row for row in rows if not lifecycle[row.runner_id][0]]
                runner_ids = [row.runner_id for row in rows]
            paused = self._paused_many(conn, runner_ids)
            local_pause = self._local_pause_detail_many(conn, runner_ids)
            usage = self._external_usage_many(conn, runner_ids)
            usage_misses = self._external_usage_misses_many(conn, runner_ids)
            return [
                self._registration(
                    row,
                    paused[row.runner_id],
                    local_pause[row.runner_id],
                    usage[row.runner_id],
                    usage_misses[row.runner_id],
                    lifecycle[row.runner_id],
                )
                for row in rows
            ]

    def is_token_revoked(self, token_hash: str) -> bool:
        with self._store.read("is_token_revoked") as conn:
            row = conn.execute(
                select(s.runner_token_revocations.c.id)
                .where(s.runner_token_revocations.c.token_hash == token_hash)
                .limit(1)
            ).first()
            return row is not None

    def registration_for_token_hash(self, token_hash: str) -> RunnerRegistration | None:
        with self._store.read("registration_for_token_hash") as conn:
            row = conn.execute(
                select(s.runner_registrations).where(s.runner_registrations.c.token_hash == token_hash)
            ).one_or_none()
            if row is None:
                return None
            return self._registration(
                row,
                self._paused(conn, row.runner_id),
                self._local_pause_detail(conn, row.runner_id),
                self._external_usage(conn, row.runner_id),
                self._external_usage_misses(conn, row.runner_id),
                self._lifecycle(conn, row.runner_id),
            )

    def list_pause_facts_since(self, since: datetime, *, limit: int) -> list[ActivityEntry]:
        with self._store.read("list_pause_facts_since") as conn:
            fleet_rows = conn.execute(
                select(s.runner_pause_facts)
                .where(s.runner_pause_facts.c.set_at >= since)
                .order_by(s.runner_pause_facts.c.set_at.desc(), s.runner_pause_facts.c.id.desc())
                .limit(limit)
            ).all()
            local_rows = conn.execute(
                select(s.runner_local_pause_facts)
                .where(s.runner_local_pause_facts.c.set_at >= since)
                .order_by(s.runner_local_pause_facts.c.set_at.desc(), s.runner_local_pause_facts.c.id.desc())
                .limit(limit)
            ).all()
        fleet = [
            ActivityEntry(
                type="runner-changed",
                key=f"runner_pause_facts:{r.id}",
                at=r.set_at,
                runner_id=r.runner_id,
                kind="paused" if r.paused else "resumed",
                by=r.set_by,
            )
            for r in fleet_rows
        ]
        local = [
            ActivityEntry(
                type="runner-changed",
                key=f"runner_local_pause_facts:{r.id}",
                # `set_at` is the runner-machine's own clock, so a skewed one can float a
                # row out of this window — a known gap, not fixable without a schema change.
                at=r.set_at,
                runner_id=r.runner_id,
                kind="locally-paused" if r.paused else "locally-resumed",
                by=r.set_by,
                reason=r.reason,
            )
            for r in local_rows
        ]
        return [*fleet, *local]

    # --- writes -------------------------------------------------------------

    def upsert_registration(
        self,
        runner_id: str,
        *,
        workspace_id: str,
        env_capacity: int | None,
        public_url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        capabilities: tuple[RunnerCapability, ...] = (),
        subscriptions: tuple[DeclaredSubscription, ...] | None = None,
        gates: tuple[str, ...] = (),
        at: datetime,
    ) -> bool:
        # Written unconditionally on both branches, `None`/empty verbatim included: the
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
        with self._store.write("upsert_registration") as conn:
            existing = conn.execute(
                select(s.runner_registrations.c.runner_id).where(s.runner_registrations.c.runner_id == runner_id)
            ).one_or_none()
            if existing is None:
                conn.execute(
                    insert(s.runner_registrations).values(
                        runner_id=runner_id,
                        workspace_id=workspace_id,
                        registered_at=at,
                        last_seen_at=at,
                        env_capacity=env_capacity,
                        public_url=public_url,
                        redirect_uris=redirect_uris_json,
                        capabilities=capabilities_json,
                        subscriptions=subscriptions_json,
                        gates=gates_json,
                    )
                )
                return True
            conn.execute(
                s.runner_registrations.update()
                .where(s.runner_registrations.c.runner_id == runner_id)
                .values(
                    workspace_id=workspace_id,
                    last_seen_at=at,
                    env_capacity=env_capacity,
                    public_url=public_url,
                    redirect_uris=redirect_uris_json,
                    capabilities=capabilities_json,
                    subscriptions=subscriptions_json,
                    gates=gates_json,
                )
            )
            return False

    def touch_last_seen(self, runner_id: str, *, at: datetime) -> bool:
        with self._store.write("touch_last_seen") as conn:
            result = conn.execute(
                s.runner_registrations.update()
                .where(s.runner_registrations.c.runner_id == runner_id)
                .values(last_seen_at=at)
            )
            return bool(result.rowcount)

    def record_pause(self, runner_id: str, *, paused: bool, at: datetime, by: str) -> int:
        with self._store.write("record_pause") as conn:
            result = conn.execute(
                insert(s.runner_pause_facts).values(runner_id=runner_id, paused=paused, set_at=at, set_by=by)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, reason: str | None = None
    ) -> int:
        with self._store.write("record_local_pause") as conn:
            result = conn.execute(
                insert(s.runner_local_pause_facts).values(
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
                select(s.runner_external_usage.c.runner_id).where(
                    s.runner_external_usage.c.runner_id == runner_id, s.runner_external_usage.c.slug == slug
                )
            ).one_or_none()
            if existing is None:
                conn.execute(
                    insert(s.runner_external_usage).values(
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
                s.runner_external_usage.update()
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
                select(s.runner_external_usage_misses.c.runner_id).where(
                    s.runner_external_usage_misses.c.runner_id == runner_id,
                    s.runner_external_usage_misses.c.slug == slug,
                )
            ).one_or_none()
            if existing is None:
                conn.execute(
                    insert(s.runner_external_usage_misses).values(
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
                s.runner_external_usage_misses.update()
                .where(
                    s.runner_external_usage_misses.c.runner_id == runner_id,
                    s.runner_external_usage_misses.c.slug == slug,
                )
                .values(name=name, missed_at=missed_at, reason=reason, updated_at=at)
            )

    def record_lifecycle(self, runner_id: str, *, retired: bool, at: datetime, by: str) -> int:
        with self._store.write("record_lifecycle") as conn:
            result = conn.execute(
                insert(s.runner_lifecycle_facts).values(runner_id=runner_id, retired=retired, set_at=at, set_by=by)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def revoke_token(self, runner_id: str, *, at: datetime, by: str) -> int | None:
        # The revocation fact and the nulled hash land in one transaction, so there is
        # no instant where the token neither resolves nor reads as revoked.
        with self._store.write("revoke_token") as conn:
            token_hash = conn.execute(
                select(s.runner_registrations.c.token_hash).where(s.runner_registrations.c.runner_id == runner_id)
            ).scalar_one_or_none()
            if token_hash is None:
                return None
            result = conn.execute(
                insert(s.runner_token_revocations).values(
                    runner_id=runner_id, token_hash=token_hash, revoked_at=at, revoked_by=by
                )
            )
            conn.execute(
                s.runner_registrations.update()
                .where(s.runner_registrations.c.runner_id == runner_id)
                .values(token_hash=None)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def rotate_token(self, rotation: TokenRotation) -> int | None:
        # The replaced hash's revocation and the new hash land in one transaction, so the old
        # token reads as revoked from the instant the new one resolves.
        with self._store.write("rotate_token") as conn:
            replaced = conn.execute(
                select(s.runner_registrations.c.token_hash).where(
                    s.runner_registrations.c.runner_id == rotation.runner_id
                )
            ).scalar_one_or_none()
            revocation_id: int | None = None
            if replaced is not None:
                result = conn.execute(
                    insert(s.runner_token_revocations).values(
                        runner_id=rotation.runner_id,
                        token_hash=replaced,
                        revoked_at=rotation.at,
                        revoked_by=rotation.by,
                    )
                )
                key = result.inserted_primary_key
                revocation_id = int(key[0]) if key is not None else 0
            conn.execute(
                s.runner_registrations.update()
                .where(s.runner_registrations.c.runner_id == rotation.runner_id)
                .values(token_hash=rotation.token_hash)
            )
            return revocation_id

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _paused(conn, runner_id: str) -> bool:  # type: ignore[no-untyped-def]
        """Derive the fleet's brake from the newest pause/resume fact, default False."""
        return RunnerRegistryStore._paused_many(conn, [runner_id])[runner_id]

    @staticmethod
    def _paused_many(conn, runner_ids: Sequence[str]) -> dict[str, bool]:  # type: ignore[no-untyped-def]
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
    def _lifecycle(conn, runner_id: str) -> tuple[bool, datetime | None, str | None]:  # type: ignore[no-untyped-def]
        """The runner's retirement, off its newest lifecycle fact."""
        return RunnerRegistryStore._lifecycle_many(conn, [runner_id])[runner_id]

    @staticmethod
    def _lifecycle_many(  # type: ignore[no-untyped-def]
        conn, runner_ids: Sequence[str]
    ) -> dict[str, tuple[bool, datetime | None, str | None]]:
        """``_lifecycle``'s grouped sibling — every listed runner's ``(retired, at, by)`` off
        its newest lifecycle fact, one query per id batch. Defaults ``(False, None, None)``,
        and a reinstated runner's newest fact nulls ``at``/``by``."""
        result: dict[str, tuple[bool, datetime | None, str | None]] = dict.fromkeys(runner_ids, (False, None, None))
        if not runner_ids:
            return result
        for batch in id_batches(runner_ids):
            rows = conn.execute(
                newest_fact_select(
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
    def _local_pause_detail(conn, runner_id: str) -> tuple[bool, str | None, str | None]:  # type: ignore[no-untyped-def]
        """The runner's own brake plus its cause, off the newest fact."""
        return RunnerRegistryStore._local_pause_detail_many(conn, [runner_id])[runner_id]

    @staticmethod
    def _local_pause_detail_many(  # type: ignore[no-untyped-def]
        conn, runner_ids: Sequence[str]
    ) -> dict[str, tuple[bool, str | None, str | None]]:
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
    def _external_usage(conn, runner_id: str) -> list[tuple[str, str, datetime, str]]:  # type: ignore[no-untyped-def]
        """Every reported subscription's newest sample for this runner, raw,
        one row per slug. Empty for a runner that has never reported one."""
        return RunnerRegistryStore._external_usage_many(conn, [runner_id])[runner_id]

    @staticmethod
    def _external_usage_many(  # type: ignore[no-untyped-def]
        conn, runner_ids: Sequence[str]
    ) -> dict[str, list[tuple[str, str, datetime, str]]]:
        """``_external_usage``'s grouped sibling and its one home — every listed runner's
        reported subscriptions, raw, one row per slug — ``(slug, name, sampled_at,
        windows_json)`` tuples — one query for every id in ``runner_ids`` rather than one
        query per runner (`list_runners`)."""
        grouped: dict[str, list[tuple[str, str, datetime, str]]] = {runner_id: [] for runner_id in runner_ids}
        if not runner_ids:
            return grouped
        for batch in id_batches(runner_ids):
            rows = conn.execute(
                select(
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
    def _external_usage_misses(conn, runner_id: str) -> list[tuple[str, str, datetime, str]]:  # type: ignore[no-untyped-def]
        """Every reported subscription's newest miss for this runner, raw,
        one row per slug. Empty for a runner that has never reported one."""
        return RunnerRegistryStore._external_usage_misses_many(conn, [runner_id])[runner_id]

    @staticmethod
    def _external_usage_misses_many(  # type: ignore[no-untyped-def]
        conn, runner_ids: Sequence[str]
    ) -> dict[str, list[tuple[str, str, datetime, str]]]:
        """``_external_usage_misses``'s grouped sibling and its one home — every listed
        runner's reported subscription misses, raw, one row per slug — ``(slug, name,
        missed_at, reason)`` tuples — one query for every id in ``runner_ids`` rather than
        one query per runner (`list_runners`)."""
        grouped: dict[str, list[tuple[str, str, datetime, str]]] = {runner_id: [] for runner_id in runner_ids}
        if not runner_ids:
            return grouped
        for batch in id_batches(runner_ids):
            rows = conn.execute(
                select(
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
        row,  # type: ignore[no-untyped-def]
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
                windows=RunnerRegistryStore._usage_windows(windows_json),
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
        )

    @staticmethod
    def _usage_windows(windows_json: str) -> tuple[ExternalSubscriptionUsageWindow, ...]:
        """Valid windows from current or pre-validation stored samples."""
        try:
            entries = json.loads(windows_json)
        except (TypeError, json.JSONDecodeError):
            return ()
        if not isinstance(entries, list):
            return ()
        windows = []
        for entry in entries:
            try:
                window = ExternalSubscriptionUsageWindowFact.model_validate(entry)
            except ValidationError:
                continue
            windows.append(
                ExternalSubscriptionUsageWindow(
                    window=window.window,
                    utilization_pct=window.utilization_pct,
                    resets_at=as_utc(window.resets_at),
                    window_seconds=window.window_seconds,
                )
            )
        return tuple(windows)


def _conforms_registry(x: RunnerRegistryStore) -> IWriteRunnerRegistry:
    return x
