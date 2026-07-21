"""``mark_deprecated`` — the shared deprecation-header helper (issue #104), unit tier.

Pure over a ``fastapi.Response``, no app or route needed.
"""

from __future__ import annotations

import pytest
from fastapi import Response

from blizzard.hub.api.deprecation import mark_deprecated

pytestmark = pytest.mark.unit


def test_mark_deprecated_sets_deprecation_and_link_headers() -> None:
    response = Response()
    mark_deprecated(response, successor="/api/queue")
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Link"] == '</api/queue>; rel="successor-version"'
    assert "Sunset" not in response.headers


def test_mark_deprecated_sets_sunset_header_when_given() -> None:
    response = Response()
    mark_deprecated(response, successor="/api/queue", sunset="Wed, 21 Oct 2026 07:28:00 GMT")
    assert response.headers["Sunset"] == "Wed, 21 Oct 2026 07:28:00 GMT"
