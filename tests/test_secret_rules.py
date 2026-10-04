"""Unit tier: the secret's rules, pinned by value — no repository, no clock.

A written value is never blank, a retired secret is never revealed, a referenced secret is
never retired, a listing hides retired secrets unless asked, and key coverage and rotation
share one computation of which generations and rows are stale."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.changes import RecordKind, RecordRef
from blizzard.hub.domain.config.secrets import (
    SealedSecret,
    SealedValue,
    SecretMetadata,
    SecretReferenced,
    SecretRetired,
    SecretValue,
    SecretValueBlank,
    listed,
    require_revealable,
    require_unreferenced,
    stale_seals,
    uncovered_key_ids,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _metadata(name: str) -> SecretMetadata:
    return SecretMetadata(name=name, revision=1, replaced_at=_T0, replaced_by="op", created_at=_T0)


def _sealed(name: str, key_id: str) -> SealedSecret:
    return SealedSecret(name=name, revision=1, sealed=SealedValue(key_id=key_id, ciphertext=b"c", nonce=b"n"))


@pytest.mark.parametrize("raw", ["", " ", "\t\n"])
def test_a_blank_value_is_refused_on_entry(raw: str) -> None:
    with pytest.raises(SecretValueBlank):
        SecretValue.entered(raw)


def test_a_non_blank_value_enters_verbatim() -> None:
    assert SecretValue.entered(" tok ").expose() == " tok "


def test_a_stored_blank_value_still_opens() -> None:
    assert SecretValue("").expose() == ""


def test_a_retired_secret_is_not_revealable() -> None:
    with pytest.raises(SecretRetired) as caught:
        require_revealable("gh", retired=True)
    assert caught.value.name == "gh"
    require_revealable("gh", retired=False)


def test_a_referenced_secret_cannot_be_retired() -> None:
    refs = [RecordRef(RecordKind.WORK_SOURCE, "issues")]
    with pytest.raises(SecretReferenced) as caught:
        require_unreferenced("gh", refs)
    assert caught.value.referrers == refs
    require_unreferenced("gh", [])


def test_a_listing_hides_retired_secrets_unless_asked() -> None:
    records = [_metadata("a"), _metadata("b"), _metadata("c")]
    assert [r.name for r in listed(records, {"b"}, include_retired=False)] == ["a", "c"]
    assert [r.name for r in listed(records, {"b"}, include_retired=True)] == ["a", "b", "c"]


def test_uncovered_key_ids_are_those_in_use_no_generation_answers() -> None:
    assert uncovered_key_ids({"k1", "k2", "k3"}, {"k2"}) == frozenset({"k1", "k3"})
    assert uncovered_key_ids({"k1"}, {"k1", "k2"}) == frozenset()


def test_a_rotation_reseals_only_rows_under_another_generation() -> None:
    rows = [_sealed("a", "k1"), _sealed("b", "k2"), _sealed("c", "k1")]
    assert [r.name for r in stale_seals(rows, "k2")] == ["a", "c"]
    assert stale_seals(rows, "k3") == rows
