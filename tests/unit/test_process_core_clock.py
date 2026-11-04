"""Offline process core clock selection."""

from unittest.mock import Mock, patch

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.hub.composition import build_process_core


def test_process_core_ignores_controlled_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLIZZARD_CLOCK", "controlled")
    monkeypatch.setenv("BLIZZARD_CLOCK_FENCE", "1")
    engine = Mock()
    with patch("blizzard.hub.composition.build_hub_core") as build:
        assert build_process_core(engine) is build.return_value
    assert build.call_args.args == (engine,)
    assert isinstance(build.call_args.kwargs["clock"], SystemClock)
