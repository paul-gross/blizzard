"""Hub settings responses preserve vocabulary values added by newer servers."""

import pytest
from pydantic import ValidationError

from blizzard.foundation.config_keys import Lifetime
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.hub.config import ConfigError
from blizzard.hub.settings.footing import validate_document_values
from blizzard.wire.config import ConfigApplyOutcome
from blizzard.wire.hub_settings import SETTINGS_EXPORT_ADAPTER, HubSettingRow, HubSettingsChange


def test_shared_document_graph_is_linear_and_still_checks_cycles_and_nonfinite_values() -> None:
    shared: list[object] = [1]
    for _ in range(35):
        shared = [shared, shared]
    document = {"first": shared, "second": shared}
    assert SETTINGS_EXPORT_ADAPTER.validate_python(document) == document
    validate_document_values(document)

    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(ValidationError):
        SETTINGS_EXPORT_ADAPTER.validate_python({"first": shared, "second": cycle})
    with pytest.raises(ConfigError, match="cycle"):
        validate_document_values({"first": shared, "second": cycle})
    with pytest.raises(ConfigError, match="non-finite"):
        validate_document_values({"first": shared, "second": [float("inf")]})


def test_settings_row_vocabulary_is_open() -> None:
    row = HubSettingRow.model_validate({"key": "a", "value": 1, "source": "default", "lifetime": "pass"})
    assert row.source == "default"
    assert row.lifetime is Lifetime.PASS
    row = HubSettingRow.model_validate({"key": "a", "value": 1, "source": "future", "lifetime": "future"})
    assert row.source == row.lifetime == Unrecognized("future")
    assert row.model_dump()["source"] == "future"
    assert row.model_dump()["lifetime"] == "future"


@pytest.mark.parametrize("field", ["door", "op"])
def test_settings_change_vocabulary_is_open(field: str) -> None:
    data: dict[str, object] = {
        "id": 1,
        "revision": 1,
        "recorded_at": "now",
        "actor": "a",
        "door": "api",
        "op": "edit",
        "apply_id": None,
        "diff": [],
    }
    data[field] = "future"
    change = HubSettingsChange.model_validate(data)
    assert getattr(change, field) == Unrecognized("future")
    assert change.model_dump()[field] == "future"


def test_apply_outcome_op_is_open() -> None:
    known = ConfigApplyOutcome.model_validate({"kind": "settings", "key": "a", "op": "replace"})
    assert known.op == "replace"
    outcome = ConfigApplyOutcome.model_validate({"kind": "settings", "key": "a", "op": "future"})
    assert outcome.op == Unrecognized("future")
    assert outcome.model_dump()["op"] == "future"


def test_inline_vocabulary_schemas_are_unchanged() -> None:
    source = HubSettingRow.model_json_schema()["properties"]["source"]
    op = ConfigApplyOutcome.model_json_schema()["properties"]["op"]
    assert source == {"enum": ["entry", "default"], "title": "Source", "type": "string", "x-blizzard-open": True}
    assert op == {
        "enum": ["create", "edit", "retire", "enable", "replace", "unchanged"],
        "title": "Op",
        "type": "string",
        "x-blizzard-open": True,
    }
