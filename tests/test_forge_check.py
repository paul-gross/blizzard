"""The forge pre-flight judgement over address records (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.config.forge_check import (
    ForgeRefusalCase,
    RepositoryAddress,
    TenantAddresses,
    WorkSourceAddress,
    judge,
)

pytestmark = pytest.mark.unit


def _judged(
    repositories: tuple[RepositoryAddress, ...] = (), work_sources: tuple[WorkSourceAddress, ...] = ()
) -> dict[ForgeRefusalCase, list[tuple[str, ...]]]:
    refusals = judge(TenantAddresses("ten_1", "acme", repositories, work_sources)).refusals
    judged: dict[ForgeRefusalCase, list[tuple[str, ...]]] = {}
    for refusal in refusals:
        judged.setdefault(refusal.case, []).append(refusal.slugs)
    return judged


def test_an_address_with_a_user_needs_cleaning() -> None:
    judged = _judged((RepositoryAddress("r1", "https://user@forge.example", "acme", "widget"),))
    assert judged == {ForgeRefusalCase.NEEDS_CLEANING: [("r1",)]}


def test_a_work_source_address_with_no_scheme_is_not_an_origin() -> None:
    judged = _judged(work_sources=(WorkSourceAddress("w1", "ghe.acme", None),))
    assert judged == {ForgeRefusalCase.NOT_AN_ORIGIN: [("w1",)]}


def test_an_explicit_web_base_that_is_not_an_origin_is_named_too() -> None:
    judged = _judged(work_sources=(WorkSourceAddress("w1", None, "ghe.acme/web"),))
    assert judged == {ForgeRefusalCase.NOT_AN_ORIGIN: [("w1",)]}


def test_two_spellings_of_one_coordinate_name_both_records() -> None:
    judged = _judged(
        (
            RepositoryAddress("r1", "https://forge.example", "acme", "widget"),
            RepositoryAddress("r2", "https://FORGE.example/", "acme", "widget"),
            RepositoryAddress("r3", "https://forge.example", "acme", "other"),
        )
    )
    assert judged == {ForgeRefusalCase.COORDINATE_COLLISION: [("r1", "r2")]}


def test_two_explicit_web_bases_for_one_origin_name_both_sources() -> None:
    judged = _judged(
        work_sources=(
            WorkSourceAddress("w1", "https://ghe.example/api/v3", "https://ghe.example"),
            WorkSourceAddress("w2", "https://GHE.example/api/v3/", "https://web.ghe.example"),
            WorkSourceAddress("w3", "https://ghe.example/api/v3", None),
            WorkSourceAddress("w4", "https://ghe.example/api/v3", "https://ghe.example/"),
        )
    )
    assert judged == {ForgeRefusalCase.WEB_BASE_DISAGREEMENT: [("w1", "w2", "w4")]}


def test_a_refused_address_takes_no_part_in_the_comparisons() -> None:
    judged = _judged(
        (
            RepositoryAddress("r1", "https://forge.example", "acme", "widget"),
            RepositoryAddress("r2", "https://user@forge.example", "acme", "widget"),
        )
    )
    assert judged == {ForgeRefusalCase.NEEDS_CLEANING: [("r2",)]}


@pytest.mark.parametrize("api_base", [None, "", "   "])
def test_a_blank_api_base_reads_as_github_and_names_nothing(api_base: str | None) -> None:
    assert _judged(work_sources=(WorkSourceAddress("w1", api_base, None),)) == {}


def test_a_feature_environment_origin_names_nothing() -> None:
    assert _judged((RepositoryAddress("r1", "http://127.0.0.1:8123", "acme", "widget"),)) == {}
