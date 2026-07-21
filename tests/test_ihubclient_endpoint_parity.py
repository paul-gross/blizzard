"""Unit-tier mirror of the service guard's table-vs-protocol check (blizzard-mock#4).

``tests/service/test_parity_guard.py`` enumerates :class:`IHubClient`'s protocol
methods and compares them against its own ``_IHUBCLIENT_ENDPOINTS`` mapping table — a
pure import + dict compare that needs no fleet and no network. That module is
``service``-gated as a whole (its other assertion fetches a live mock hub's
``/openapi.json``), so without this mirror an ``IHubClient`` method added without a
matching table entry would only trip under ``BLIZZARD_SERVICE=1``, not in the fast
gate. This re-runs just the table-vs-protocol half at the unit tier, importing the
guard's own check function rather than duplicating ``_IHUBCLIENT_ENDPOINTS``.
"""

from __future__ import annotations

import pytest

from tests.service.test_parity_guard import _assert_ihubclient_endpoint_table_matches_protocol

pytestmark = pytest.mark.unit


def test_ihubclient_endpoint_table_matches_the_protocol_method_set() -> None:
    """See ``_assert_ihubclient_endpoint_table_matches_protocol`` for the check itself."""
    _assert_ihubclient_endpoint_table_matches_protocol()
