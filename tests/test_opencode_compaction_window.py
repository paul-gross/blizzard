import json

import pytest

from blizzard.runner.harness.json_value import JsonObject
from blizzard.runner.harness.opencode.compaction_window import compose_window
from blizzard.runner.harness.opencode.model_limits import FileOpenCodeModelLimitsCatalog, OpenCodeModelLimits

pytestmark = pytest.mark.unit


def test_catalog_reads_limits_without_pricing(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"p": {"models": {"m": {"limit": {"context": 100000, "output": 4000}}}}}))
    assert FileOpenCodeModelLimitsCatalog(path).limits_for("p", "m") == OpenCodeModelLimits(100000, 4000)


def test_composition_uses_effective_reserve_and_preserves_inline_settings():
    base: JsonObject = {"provider": {"p": {"models": {"m": {"limit": {"output": 6000}}, "other": {"name": "kept"}}}}}
    effective: JsonObject = {"compaction": {"reserved": 1234}, **base}
    original = json.dumps(base, sort_keys=True)
    result = compose_window(base, effective, "p", "m", "8k", OpenCodeModelLimits(100000, 4000))
    assert result == {
        "provider": {
            "p": {
                "models": {
                    "m": {"limit": {"context": 100000, "output": 6000, "input": 9234}},
                    "other": {"name": "kept"},
                }
            }
        }
    }
    assert json.dumps(base, sort_keys=True) == original


def test_default_reserve_uses_effective_output():
    effective: JsonObject = {"provider": {"p": {"models": {"m": {"limit": {"output": 12000}}}}}}
    result = compose_window({}, effective, "p", "m", "8000", OpenCodeModelLimits(100000, 4000))
    assert result == {
        "provider": {"p": {"models": {"m": {"limit": {"context": 100000, "output": 12000, "input": 20000}}}}}
    }


def test_effective_context_is_preserved():
    effective: JsonObject = {"provider": {"p": {"models": {"m": {"limit": {"context": 90000}}}}}}
    result = compose_window({}, effective, "p", "m", "8k", OpenCodeModelLimits(100000, 4000))
    assert result == {
        "provider": {"p": {"models": {"m": {"limit": {"context": 90000, "output": 4000, "input": 12000}}}}}
    }
