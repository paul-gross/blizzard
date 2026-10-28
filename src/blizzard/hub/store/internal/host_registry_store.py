"""SQLAlchemy adapter for the host-registry seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only, status derived
(``bzh:facts-not-status``): retirement derives from the newest row of ``host_lifecycle_facts``; ``name``,
``last_seen_at`` and ``token_hash`` are the refreshed-in-place columns."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, CursorResult, Row

from blizzard.foundation.store.batching import id_batches
from blizzard.foundation.store.utc import as_utc
from blizzard.hub.domain.runners.host import (
    Host,
    HostAddition,
    HostLifecycleFact,
    HostNameTaken,
    HostReport,
    HostTokenRotation,
    HostUsage,
    HostWorkspace,
    IWriteHostRegistry,
    RunnerAlreadyHosted,
)
from blizzard.hub.domain.runners.registration import DeclaredSubscription
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.host_usage import read_host_usage
from blizzard.hub.store.internal.newest_fact import newest_fact_select, newest_retired_select
from blizzard.hub.store.scoped import ScopedConnections, statements_of

_Lifecycle = tuple[bool, datetime | None, str | None]


def _locked_token_hash(conn: Connection, host_id: str) -> str | None:
    """The host's current token hash, read under its row's write lock — a no-op ``UPDATE`` as the transaction's first
    statement, then the read (``bzh:store-exclusive-write``; ``FOR UPDATE`` renders nothing on SQLite)."""
    stmts = statements_of(conn)
    hosts = s.hosts
    conn.execute(stmts.update(hosts).where(hosts.c.host_id == host_id).values(host_id=hosts.c.host_id))
    return conn.execute(stmts.select(hosts, hosts.c.token_hash).where(hosts.c.host_id == host_id)).scalar_one_or_none()


class HostRegistryStore:
    """Read-write host-registry adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    # --- reads --------------------------------------------------------------

    def get_host(self, host_id: str) -> Host | None:
        with self._store.read("get_host") as conn:
            row = conn.execute(self._store.select(s.hosts).where(s.hosts.c.host_id == host_id)).one_or_none()
            if row is None:
                return None
            return self._host(row, self._lifecycle_many(conn, [host_id])[host_id])

    def get_hosts(self, host_ids: Iterable[str]) -> dict[str, Host]:
        ids = list(dict.fromkeys(host_ids))
        if not ids:
            return {}
        hosts = s.hosts
        with self._store.read("get_hosts") as conn:
            rows = [
                row
                for batch in id_batches(ids)
                for row in conn.execute(self._store.select(hosts).where(hosts.c.host_id.in_(batch)))
            ]
            lifecycle = self._lifecycle_many(conn, [row.host_id for row in rows])
            return {row.host_id: self._host(row, lifecycle[row.host_id]) for row in rows}

    def list_hosts(self, *, include_retired: bool = False) -> list[Host]:
        with self._store.read("list_hosts") as conn:
            rows = conn.execute(self._store.select(s.hosts).order_by(s.hosts.c.added_at, s.hosts.c.host_id)).all()
            lifecycle = self._lifecycle_many(conn, [row.host_id for row in rows])
            return [
                self._host(row, lifecycle[row.host_id])
                for row in rows
                if include_retired or not lifecycle[row.host_id][0]
            ]

    def live_names(self, *, excluding: str | None = None) -> frozenset[str]:
        with self._store.read("live_names") as conn:
            return frozenset(self._live_names(conn, excluding))

    def high_water(self, host_id: str) -> int:
        with self._store.read("host_high_water") as conn:
            seq = conn.execute(
                self._store.select(s.host_high_water, s.host_high_water.c.seq).where(
                    s.host_high_water.c.host_id == host_id
                )
            ).scalar_one_or_none()
            return int(seq) if seq is not None else 0

    def usage_of(self, host_ids: Sequence[str]) -> dict[str, HostUsage]:
        with self._store.read("host_usage_of") as conn:
            return read_host_usage(self._store, conn, host_ids)

    # --- writes -------------------------------------------------------------

    def set_high_water(self, host_id: str, *, seq: int, at: datetime) -> None:
        mark = s.host_high_water
        with self._store.write("set_host_high_water") as conn:
            existing = conn.execute(
                self._store.select(mark, mark.c.host_id).where(mark.c.host_id == host_id)
            ).one_or_none()
            if existing is None:
                conn.execute(self._store.insert(mark).values(host_id=host_id, seq=seq, updated_at=at))
                return
            conn.execute(self._store.update(mark).where(mark.c.host_id == host_id).values(seq=seq, updated_at=at))

    def record_external_usage(
        self, host_id: str, *, slug: str, name: str, sampled_at: datetime, windows_json: str, at: datetime
    ) -> None:
        # Refresh-in-place per (host_id, slug): a replay re-writes the row, it never double-counts.
        usage = s.host_external_usage
        match = (usage.c.host_id == host_id, usage.c.slug == slug)
        with self._store.write("record_host_external_usage") as conn:
            if conn.execute(self._store.select(usage, usage.c.host_id).where(*match)).one_or_none() is None:
                conn.execute(
                    self._store.insert(usage).values(
                        host_id=host_id,
                        slug=slug,
                        name=name,
                        sampled_at=sampled_at,
                        windows=windows_json,
                        updated_at=at,
                    )
                )
                return
            conn.execute(
                self._store.update(usage)
                .where(*match)
                .values(name=name, sampled_at=sampled_at, windows=windows_json, updated_at=at)
            )

    def record_external_usage_miss(
        self, host_id: str, *, slug: str, name: str, missed_at: datetime, reason: str, at: datetime
    ) -> None:
        # Sibling to `record_external_usage`: its own table, never overwriting the sample row it joins at read.
        missed = s.host_external_usage_misses
        match = (missed.c.host_id == host_id, missed.c.slug == slug)
        with self._store.write("record_host_external_usage_miss") as conn:
            if conn.execute(self._store.select(missed, missed.c.host_id).where(*match)).one_or_none() is None:
                conn.execute(
                    self._store.insert(missed).values(
                        host_id=host_id, slug=slug, name=name, missed_at=missed_at, reason=reason, updated_at=at
                    )
                )
                return
            conn.execute(
                self._store.update(missed)
                .where(*match)
                .values(name=name, missed_at=missed_at, reason=reason, updated_at=at)
            )

    def add(self, addition: HostAddition) -> None:
        with self._store.write("add_host") as conn:
            # The tenant-row lock the scoped write takes serializes two adds of one name.
            if addition.name in self._live_names(conn, None):
                raise HostNameTaken(addition.name)
            conn.execute(
                self._store.insert(s.hosts).values(
                    host_id=addition.host_id,
                    name=addition.name,
                    token_hash=addition.token_hash,
                    added_at=addition.at,
                    added_by=addition.by,
                )
            )
            self._place(conn, addition.host_id, addition.adopt)

    def record_report(self, host_id: str, report: HostReport, *, name: str | None, at: datetime) -> bool:
        refreshed: dict[str, object] = {
            "last_seen_at": at,
            "url": report.url,
            "redirect_uris": json.dumps(list(report.redirect_uris)),
            "workspaces": json.dumps(
                [{"workspace_id": w.workspace_id, "env_capacity": w.env_capacity} for w in report.workspaces]
            ),
            "subscriptions": json.dumps(
                [{"slug": d.slug, "name": d.name, "provider": d.provider} for d in report.subscriptions]
            ),
        }
        if name is not None:
            refreshed["name"] = name
        match = s.hosts.c.host_id == host_id
        with self._store.write("record_host_report") as conn:
            if not conn.execute(self._store.update(s.hosts).where(match).values(refreshed)).rowcount:
                raise LookupError(f"host {host_id!r} was never added")
            # The tenant-row lock the scoped write takes serializes a rename against an add or another rename, so the
            # domain's earlier check cannot go stale before this write lands.
            if name is not None and name in self._live_names(conn, host_id):
                raise HostNameTaken(name)
            # Stamped only while unset, so of two racing first reports exactly one reads as first.
            first = conn.execute(
                self._store.update(s.hosts).where(match, s.hosts.c.registered_at.is_(None)).values(registered_at=at)
            )
            return bool(first.rowcount)

    def adopt(self, host_id: str, runner_ids: Sequence[str]) -> None:
        with self._store.write("adopt_runners") as conn:
            self._place(conn, host_id, runner_ids)

    def release(self, host_id: str, runner_ids: Sequence[str]) -> None:
        runners = s.runner_registrations
        with self._store.write("release_runners") as conn:
            for batch in id_batches(list(runner_ids)):
                conn.execute(
                    self._store.update(runners)
                    .where(runners.c.host_id == host_id, runners.c.runner_id.in_(batch))
                    .values(host_id=None)
                )

    def record_retirement(
        self, host_id: str, *, fact: HostLifecycleFact | None, at: datetime, by: str
    ) -> tuple[int | None, int | None]:
        with self._store.write("record_host_retirement") as conn:
            fact_id = None
            if fact is not None:
                result = conn.execute(
                    self._store.insert(s.host_lifecycle_facts).values(
                        host_id=host_id, retired=fact.retired, set_at=fact.at, set_by=fact.by
                    )
                )
                fact_id = _inserted_id(result)
            return fact_id, self._revoke(conn, host_id, at=at, by=by)

    def revoke_token(self, host_id: str, *, at: datetime, by: str) -> int | None:
        # The revocation fact and the nulled hash land in one transaction, so a token always resolves or reads as
        # revoked; the row lock orders it against a concurrent rotation.
        with self._store.write("revoke_host_token") as conn:
            return self._revoke(conn, host_id, at=at, by=by)

    def rotate_token(self, rotation: HostTokenRotation) -> int | None:
        # The replaced hash's revocation and the new hash land in one transaction, and the row lock makes a
        # concurrent rotation read the hash this one stored, so every replaced hash is revoked.
        with self._store.write("rotate_host_token") as conn:
            replaced = _locked_token_hash(conn, rotation.host_id)
            revocation_id: int | None = None
            if replaced is not None:
                revocation_id = _inserted_id(
                    conn.execute(
                        self._store.insert(s.host_token_revocations).values(
                            host_id=rotation.host_id,
                            token_hash=replaced,
                            revoked_at=rotation.at,
                            revoked_by=rotation.by,
                        )
                    )
                )
            conn.execute(
                self._store.update(s.hosts)
                .where(s.hosts.c.host_id == rotation.host_id)
                .values(token_hash=rotation.token_hash)
            )
            return revocation_id

    # --- helpers ------------------------------------------------------------

    def _revoke(self, conn: Connection, host_id: str, *, at: datetime, by: str) -> int | None:
        token_hash = _locked_token_hash(conn, host_id)
        if token_hash is None:
            return None
        revocation_id = _inserted_id(
            conn.execute(
                self._store.insert(s.host_token_revocations).values(
                    host_id=host_id, token_hash=token_hash, revoked_at=at, revoked_by=by
                )
            )
        )
        conn.execute(self._store.update(s.hosts).where(s.hosts.c.host_id == host_id).values(token_hash=None))
        return revocation_id

    def _place(self, conn: Connection, host_id: str, runner_ids: Sequence[str]) -> None:
        """Place each runner under ``host_id`` iff it still stands alone; one that does not rolls the write back."""
        runners = s.runner_registrations
        for runner_id in runner_ids:
            placed = conn.execute(
                self._store.update(runners)
                .where(runners.c.runner_id == runner_id, runners.c.host_id.is_(None))
                .values(host_id=host_id)
            )
            if not placed.rowcount:
                raise RunnerAlreadyHosted(runner_id)

    def _live_names(self, conn: Connection, excluding: str | None) -> set[str]:
        hosts = s.hosts
        retired = newest_retired_select(statements_of(conn), s.host_lifecycle_facts, s.host_lifecycle_facts.c.host_id)
        query = self._store.select(hosts, hosts.c.name).where(hosts.c.host_id.not_in(retired))
        if excluding is not None:
            query = query.where(hosts.c.host_id != excluding)
        return set(conn.execute(query).scalars().all())

    @staticmethod
    def _lifecycle_many(conn: Connection, host_ids: Sequence[str]) -> dict[str, _Lifecycle]:
        """Every listed host's ``(retired, at, by)`` off its newest lifecycle fact, one query per id batch."""
        result: dict[str, _Lifecycle] = dict.fromkeys(host_ids, (False, None, None))
        for batch in id_batches(list(host_ids)):
            rows = conn.execute(
                newest_fact_select(
                    statements_of(conn),
                    s.host_lifecycle_facts,
                    s.host_lifecycle_facts.c.host_id,
                    batch,
                    s.host_lifecycle_facts.c.host_id,
                    s.host_lifecycle_facts.c.retired,
                    s.host_lifecycle_facts.c.set_at,
                    s.host_lifecycle_facts.c.set_by,
                )
            ).all()
            for row in rows:
                result[row.host_id] = (True, row.set_at, row.set_by) if row.retired else (False, None, None)
        return result

    @staticmethod
    def _host(row: Row[Any], lifecycle: _Lifecycle) -> Host:
        retired, retired_at, retired_by = lifecycle
        redirect_uris: list[str] = json.loads(row.redirect_uris) if row.redirect_uris else []
        workspaces: list[dict[str, Any]] = json.loads(row.workspaces) if row.workspaces else []
        subscriptions: list[dict[str, Any]] = json.loads(row.subscriptions) if row.subscriptions else []
        return Host(
            host_id=row.host_id,
            name=row.name,
            added_at=row.added_at,
            added_by=row.added_by,
            registered_at=row.registered_at,
            last_seen_at=row.last_seen_at,
            url=row.url,
            redirect_uris=tuple(redirect_uris),
            workspaces=tuple(
                HostWorkspace(workspace_id=w["workspace_id"], env_capacity=w.get("env_capacity")) for w in workspaces
            ),
            subscriptions=tuple(
                DeclaredSubscription(slug=d["slug"], name=d["name"], provider=d["provider"]) for d in subscriptions
            ),
            token_hash=row.token_hash,
            retired=retired,
            retired_at=as_utc(retired_at) if retired_at is not None else None,
            retired_by=retired_by,
        )


def _inserted_id(result: CursorResult[Any]) -> int:
    key = result.inserted_primary_key
    return int(key[0]) if key is not None else 0


_registry_conforms: type[IWriteHostRegistry] = HostRegistryStore
