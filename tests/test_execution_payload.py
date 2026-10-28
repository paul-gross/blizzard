"""Coercions applied to pushed fact bodies."""

import pytest

from blizzard.hub.domain.execution.payload import Payload

pytestmark = pytest.mark.unit


def test_payload_coercions_preserve_python_conversion() -> None:
    detail = {"key": "value"}
    payload = Payload({"number": "12", "amount": "2.5", "items": "ab", "detail": detail})
    assert payload.require_number("number") == 12
    assert payload.amount("amount") == 2.5
    assert payload.amount("missing") is None
    assert payload.strings("items") == ["a", "b"]
    assert payload.strings("missing") == []
    assert payload.mapping("detail") is detail
    assert payload.mapping("missing") is None
    assert Payload({"detail": {1: "value"}}).mapping("detail") is None


def test_payload_invalid_values_keep_conversion_errors() -> None:
    payload = Payload({"number": None, "amount": [], "items": None})
    with pytest.raises(TypeError):
        payload.require_number("number")
    with pytest.raises(TypeError):
        payload.amount("amount")
    with pytest.raises(TypeError):
        payload.strings("items")
