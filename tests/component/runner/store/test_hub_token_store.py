"""The sealed hub-token adapter inserts where absent and replaces at the next revision."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.runner.store.internal.host_hub_token_store import HostHubTokenStore
from blizzard.runner.store.runner_guard import GUARD_ENVIRONMENT_VARIABLE, install_runner_guard
from tests.support.runner_fakes import make_store

_AT = datetime(2026, 11, 18, tzinfo=UTC)


def _sealed(tag: bytes) -> SealedValue:
    return SealedValue(key_id="k1", ciphertext=b"cipher-" + tag, nonce=b"nonce-" + tag)


def test_insert_where_absent_keeps_the_first_row(tmp_path: Path) -> None:
    tokens = HostHubTokenStore(make_store(f"sqlite:///{tmp_path / 'host.db'}").connections)
    assert tokens.sealed("rn_a") is None
    assert tokens.seal_if_absent("rn_a", _sealed(b"a"), at=_AT)
    assert not tokens.seal_if_absent("rn_a", _sealed(b"b"), at=_AT + timedelta(hours=1))
    row = tokens.sealed("rn_a")
    assert row is not None
    assert (row.revision, row.sealed, row.updated_at) == (1, _sealed(b"a"), _AT)


def test_replace_takes_the_next_revision_and_inserts_a_missing_row(tmp_path: Path) -> None:
    tokens = HostHubTokenStore(make_store(f"sqlite:///{tmp_path / 'host.db'}").connections)
    seen: list[int] = []

    def seal(revision: int) -> SealedValue:
        seen.append(revision)
        return _sealed(str(revision).encode())

    assert tokens.replace("rn_a", seal, at=_AT) == 1
    later = _AT + timedelta(minutes=5)
    assert tokens.replace("rn_a", seal, at=later) == 2
    assert seen == [1, 2]
    row = tokens.sealed("rn_a")
    assert row is not None
    assert (row.revision, row.sealed, row.updated_at) == (2, _sealed(b"2"), later)
    assert tokens.sealed("rn_b") is None


def test_reads_name_their_runner_under_the_statement_guard(tmp_path: Path) -> None:
    assert install_runner_guard({GUARD_ENVIRONMENT_VARIABLE: "1"})
    store = make_store(f"sqlite:///{tmp_path / 'host.db'}")
    tokens = HostHubTokenStore(store.connections)
    tokens.seal_if_absent("rn_a", _sealed(b"a"), at=_AT)
    tokens.seal_if_absent(store.connections.bound_runner_id(), _sealed(b"own"), at=_AT)
    row = tokens.sealed("rn_a")
    assert row is not None and row.sealed == _sealed(b"a")


def test_drop_deletes_only_its_runners_row(tmp_path: Path) -> None:
    tokens = HostHubTokenStore(make_store(f"sqlite:///{tmp_path / 'host.db'}").connections)
    tokens.seal_if_absent("rn_a", _sealed(b"a"), at=_AT)
    tokens.seal_if_absent("rn_b", _sealed(b"b"), at=_AT)

    assert tokens.drop("rn_a")
    assert not tokens.drop("rn_a")
    assert tokens.sealed("rn_a") is None
    assert tokens.sealed("rn_b") is not None


def test_a_first_replace_losing_the_insert_race_writes_the_next_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a store whose zero-row update locks nothing (Postgres), a rival's first row can land between this replace's
    lock and its insert; the insert's key conflict is retried as a replacement, never surfaced."""
    url = f"sqlite:///{tmp_path / 'host.db'}"
    tokens = HostHubTokenStore(make_store(url).connections)
    rival = HostHubTokenStore(make_store(url).connections)
    once = tokens._replace_once  # pyright: ignore[reportPrivateUsage]
    raced: list[bool] = []

    def losing(runner_id: str, seal: Callable[[int], SealedValue], *, at: datetime) -> tuple[int, SealedValue]:
        if not raced:
            raced.append(True)
            assert rival.seal_if_absent(runner_id, _sealed(b"rival"), at=at)
            raise IntegrityError("INSERT INTO runner_hub_tokens", None, Exception("duplicate key"))
        return once(runner_id, seal, at=at)

    monkeypatch.setattr(tokens, "_replace_once", losing)

    assert tokens.replace("rn_a", lambda revision: _sealed(str(revision).encode()), at=_AT) == 2
    row = tokens.sealed("rn_a")
    assert row is not None
    assert (row.revision, row.sealed) == (2, _sealed(b"2"))


def test_key_ids_names_each_generation_the_hosts_rows_are_sealed_under(tmp_path: Path) -> None:
    tokens = HostHubTokenStore(make_store(f"sqlite:///{tmp_path / 'host.db'}").connections)
    assert tokens.key_ids() == frozenset()
    tokens.seal_if_absent("rn_a", _sealed(b"a"), at=_AT)
    tokens.seal_if_absent("rn_b", SealedValue(key_id="k2", ciphertext=b"c", nonce=b"n"), at=_AT)
    assert tokens.key_ids() == {"k1", "k2"}
