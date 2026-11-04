"""What a flag hides while it is off, at the flag layer: ``Flags.require_on`` and the wire-field marker
(``bzh:flag-hides-behavior``)."""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel

from blizzard.foundation.flags import FlagGuarded, FlagOffError, without_off_fields
from tests.support_flag_guard import flags_for as _flags


def test_require_on_raises_for_an_off_flag_and_checks_the_name_as_on_does() -> None:
    flags = _flags("hub")
    with pytest.raises(FlagOffError) as raised:
        flags.require_on("hub-thing")
    assert raised.value.flag == "hub-thing"
    flags_on = _flags("hub", **{"hub-thing": True})
    flags_on.require_on("hub-thing")
    with pytest.raises(LookupError):
        flags_on.require_on("mistyped")


class _Inner(BaseModel):
    note: Annotated[str, FlagGuarded("hub-thing")] = "plain"


class _Wire(BaseModel):
    kept: int = 1
    hidden: Annotated[int, FlagGuarded("hub-thing")] = 0
    listed: Annotated[list[str], FlagGuarded("hub-thing")] = []
    inner: _Inner = _Inner()
    inners: list[_Inner] = []


def test_a_field_behind_an_off_flag_is_accepted_and_ignored_on_a_request_and_default_on_a_response() -> None:
    sent = _Wire.model_validate(
        {"kept": 5, "hidden": 9, "listed": ["x"], "inner": {"note": "n"}, "inners": [{"note": "m"}]}
    )
    shed = without_off_fields(sent, _flags("hub"))
    assert shed == _Wire(kept=5, inners=[_Inner(note="plain")]) and shed.inner.note == "plain"
    assert sent.hidden == 9


def test_a_field_behind_an_on_flag_passes_through_untouched() -> None:
    sent = _Wire(kept=5, hidden=9, listed=["x"])
    assert without_off_fields(sent, _flags("hub", **{"hub-thing": True})) is sent


def test_a_guarded_field_without_a_default_is_a_declaration_error() -> None:
    class Bad(BaseModel):
        required: Annotated[int, FlagGuarded("hub-thing")]

    with pytest.raises(TypeError, match="needs a default"):
        without_off_fields(Bad(required=1), _flags("hub"))
    with pytest.raises(TypeError, match="needs a default"):
        without_off_fields(Bad(required=1), _flags("hub", **{"hub-thing": True}))
