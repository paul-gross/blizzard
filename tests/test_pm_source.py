"""The GitHub-shaped PM source adapter (component tier).

Exercises :class:`~blizzard.hub.pm.internal.github_pm_source.GitHubPmSource`'s URL
parsing and vendor-native read against the GitHub-REST double — the same choice of a
local double over a ``blizzard-mock`` dev dependency recorded in ``tests.support``.
"""

from __future__ import annotations

import pytest

from blizzard.hub.domain.work import PmPointer
from blizzard.hub.pm.internal.github_pm_source import GitHubPmSource
from blizzard.hub.pm.source import PmSourceError
from tests.support import github_double

pytestmark = pytest.mark.component


def test_fetch_reads_issue_body_and_comments() -> None:
    issues = {"acme/widget#12": {"body": "the bug", "comments": ["me too", "repro"]}}
    source = GitHubPmSource(github_double(issues=issues))
    item = source.fetch(PmPointer(provider="github", url="http://forge/repos/acme/widget/issues/12"))
    assert item.body == "the bug"
    assert item.comments == ["me too", "repro"]


def test_fetch_parses_html_style_pointer_urls() -> None:
    issues = {"acme/widget#3": {"body": "x", "comments": []}}
    source = GitHubPmSource(github_double(issues=issues))
    # A pointer carrying the html_url form still resolves to the API path.
    item = source.fetch(PmPointer(provider="github", url="http://forge/acme/widget/issues/3"))
    assert item.body == "x"


def test_unparseable_pointer_raises() -> None:
    source = GitHubPmSource(github_double())
    with pytest.raises(PmSourceError):
        source.fetch(PmPointer(provider="github", url="http://forge/not-an-issue"))
