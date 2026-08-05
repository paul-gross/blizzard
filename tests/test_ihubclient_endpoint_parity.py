"""Unit-tier mirror of the service guard's table-vs-protocol check (blizzard-mock#4).

``tests/service/test_parity_guard.py``'s table-vs-protocol check is service-gated as
a whole, so a drifted ``_IHUBCLIENT_ENDPOINTS`` entry would only trip under
``BLIZZARD_SERVICE=1``. Re-runs just that half here, at the unit tier.
"""

from __future__ import annotations

import pytest

from tests.service.test_parity_guard import _assert_ihubclient_endpoint_table_matches_protocol

pytestmark = pytest.mark.unit


def test_ihubclient_endpoint_table_matches_the_protocol_method_set() -> None:
    """See ``_assert_ihubclient_endpoint_table_matches_protocol`` for the check itself."""
    _assert_ihubclient_endpoint_table_matches_protocol()
