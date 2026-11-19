"""Whose store it is (unit tier): the store's own hosted runners decide, and the file is judged only by a store that
hosts none yet."""

from __future__ import annotations

from blizzard.runner.lifecycle.identity_check import (
    ForeignStore,
    ForeignStoreKind,
    foreign_store_reason,
    host_store_verdict,
)


def test_a_store_from_before_hosted_runners_is_judged_against_the_file() -> None:
    assert host_store_verdict(("rn_a",), (), {"rn_a"}) is None
    assert host_store_verdict(("rn_a", "rn_x"), (), {"rn_a"}) == ForeignStore(
        kind=ForeignStoreKind.MIXED, named=("rn_a", "rn_x"), foreign=("rn_x",)
    )


def test_a_store_hosting_a_runner_the_file_never_names_is_ours() -> None:
    assert host_store_verdict(("rn_a", "rn_api"), {"rn_a", "rn_api"}, {"rn_a"}) is None


def test_a_store_sharing_none_of_the_files_runners_is_refused_as_alien() -> None:
    assert host_store_verdict(("rn_x",), {"rn_x", "rn_y"}, {"rn_a"}) == ForeignStore(
        kind=ForeignStoreKind.ALIEN, named=("rn_x", "rn_y"), foreign=("rn_x", "rn_y")
    )


def test_an_identity_row_no_hosted_row_accounts_for_is_refused() -> None:
    assert host_store_verdict(("rn_a", "rn_stray"), {"rn_a"}, {"rn_a"}) == ForeignStore(
        kind=ForeignStoreKind.MIXED, named=("rn_a", "rn_stray"), foreign=("rn_stray",)
    )


def test_a_file_naming_no_runner_leaves_a_hosting_store_standing() -> None:
    assert host_store_verdict(("rn_api",), {"rn_api"}, ()) is None


def test_a_retired_runners_hosted_row_still_accounts_for_its_identity_row() -> None:
    assert host_store_verdict(("rn_a", "rn_gone"), {"rn_a", "rn_gone"}, {"rn_a"}) is None


def test_refusals_keep_the_mixed_and_alien_wording() -> None:
    mixed = ForeignStore(kind=ForeignStoreKind.MIXED, named=("rn_a", "rn_x"), foreign=("rn_x",))
    alien = ForeignStore(kind=ForeignStoreKind.ALIEN, named=("rn_x",), foreign=("rn_x",))

    assert "rn_x is configured by no [[runner]] of this host" in foreign_store_reason(mixed, "rn_a", "reset")
    assert foreign_store_reason(alien, "rn_a", "reset").startswith(
        "the store belongs to runner rn_x, not the configured rn_a; correct the `id` in the host file to rn_x"
    )
