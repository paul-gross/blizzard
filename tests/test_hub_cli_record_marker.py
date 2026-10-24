"""``blizzard hub record-marker`` refuses: a hub step no longer runs commands, so nothing it could serve remains.
It reads no environment and makes no request, and names the release it is removed in."""

from __future__ import annotations

import pytest

from blizzard.hub.cli import hub as hub_group
from tests.worker_http import bind_stubs

pytestmark = pytest.mark.component


def test_record_marker_refuses_naming_its_removal_and_posts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_MARKER_CALLBACK_URL", "http://callback/hub-markers")
    monkeypatch.setenv("BZ_HUB_MARKER_TOKEN", "a-token")
    bound = bind_stubs(post=lambda *a, **k: pytest.fail("record-marker must not post"))

    result = bound.runner.invoke(hub_group, ["record-marker", "merged/acme/widget", "sha1"])

    assert result.exit_code != 0
    assert "no longer runs commands" in result.output
    assert "removed in 0.4" in result.output


def test_record_marker_refuses_with_no_content_argument() -> None:
    result = bind_stubs().runner.invoke(hub_group, ["record-marker", "merged/x"])

    assert result.exit_code != 0
    assert "0.4" in result.output
