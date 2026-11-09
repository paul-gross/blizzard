"""The owner attempts every disposal even when one engine refuses cleanup."""

from unittest.mock import Mock

import pytest

from tests.support.engine_lifecycle import EngineOwner


def test_disposal_continues_after_a_failure(request: pytest.FixtureRequest) -> None:
    owner = EngineOwner(request.session)
    good = Mock()
    bad = Mock()
    bad.dispose.side_effect = RuntimeError("disposal failed")
    owner.engines[request.session] = [good, bad]
    with pytest.raises(ExceptionGroup, match="engine disposal failed"):
        owner.dispose(request.session)
    good.dispose.assert_called_once_with()
    bad.dispose.assert_called_once_with()
    assert request.session not in owner.engines
