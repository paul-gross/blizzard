"""Hub settings responses preserve vocabulary values added by newer servers."""

import pytest

from blizzard.foundation.config_keys import Lifetime
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.config import ConfigApplyOutcome
from blizzard.wire.hub_settings import HubSettingRow, HubSettingsChange


def test_settings_row_vocabulary_is_open() -> None:
    row = HubSettingRow.model_validate({"key": "a", "value": 1, "source": "default", "lifetime": "pass"})
    assert row.source == "default"
    assert row.lifetime is Lifetime.PASS
    row = HubSettingRow.model_validate({"key": "a", "value": 1, "source": "future", "lifetime": "future"})
    assert row.source == row.lifetime == Unrecognized("future")
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
    outcome = ConfigApplyOutcome.model_validate({"kind": "settings", "key": "a", "op": "future"})
    assert outcome.op == Unrecognized("future")
    assert outcome.model_dump()["op"] == "future"
