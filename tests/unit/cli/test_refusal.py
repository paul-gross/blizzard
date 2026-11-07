"""The adapter from a response to the shared refusal reading."""

import httpx2

from blizzard.cli.refusal import refusal_of


def test_a_json_refusal_is_read() -> None:
    resp = httpx2.Response(
        404, json={"type": "urn:blizzard:problem:chunk-not-found", "title": "t", "detail": "no ch_1"}
    )

    reading = refusal_of(resp)

    assert (reading.type, reading.message) == ("urn:blizzard:problem:chunk-not-found", "no ch_1")


def test_a_body_that_is_not_json_reads_as_empty() -> None:
    reading = refusal_of(httpx2.Response(502, content=b"<html>bad gateway</html>"))

    assert (reading.type, reading.message) == (None, None)
