"""The one reading of a refusal body a client shows its operator."""

import pytest

from blizzard.wire.problem import FLAG_OFF, TENANT_NOT_FOUND
from blizzard.wire.problem_reading import read_problem
from blizzard.wire.tenant import NO_SUCH_TENANT


def test_a_problem_with_a_string_detail_shows_that_detail() -> None:
    reading = read_problem({"type": TENANT_NOT_FOUND, "title": "Tenant not found", "detail": NO_SUCH_TENANT})

    assert (reading.type, reading.message, reading.legacy_code) == (TENANT_NOT_FOUND, NO_SUCH_TENANT, None)


def test_a_problem_without_a_detail_shows_its_title() -> None:
    reading = read_problem({"type": "urn:blizzard:problem:x", "title": "Something refused"})

    assert reading.message == "Something refused"


def test_a_legacy_object_detail_contributes_its_message_and_code() -> None:
    reading = read_problem({"detail": {"code": "flag_off", "flag": "f", "message": "not available yet"}})

    assert (reading.type, reading.message, reading.legacy_code) == (None, "not available yet", "flag_off")


def test_a_typed_problem_keeps_the_legacy_object_detail_beside_its_type() -> None:
    reading = read_problem({"type": FLAG_OFF, "title": "t", "detail": {"code": "flag_off", "message": "m"}})

    assert (reading.type, reading.message) == (FLAG_OFF, "m")


def test_a_validation_list_is_joined_into_one_message() -> None:
    body = {"detail": [{"loc": ["body", "name"], "msg": "too short"}, {"loc": ["body", "slug"], "msg": "bad"}, "x"]}

    assert read_problem(body).message == "name: too short; slug: bad"


def test_a_present_but_unknown_type_is_read_by_its_message() -> None:
    reading = read_problem({"type": "urn:blizzard:problem:from-the-future", "title": "T", "detail": "words"})

    assert (reading.type, reading.message) == ("urn:blizzard:problem:from-the-future", "words")


@pytest.mark.parametrize("body", [None, "plain text", ["a"], 3, {}, {"detail": 7}, {"detail": []}])
def test_a_body_with_no_refusal_words_reads_as_empty(body: object) -> None:
    reading = read_problem(body)

    assert (reading.type, reading.message, reading.legacy_code) == (None, None, None)
