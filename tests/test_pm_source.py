"""The GitHub-shaped PM source adapter (component tier).

Exercises :class:`~blizzard.hub.pm.internal.github_pm_source.GitHubPmSource`'s
``{source, ref}`` pointer handling (D-105) and vendor-native read against the GitHub-REST
double — the same choice of a local double over a ``blizzard-mock`` dev dependency
recorded in ``tests.support`` — plus the D-106 factory that builds one credentialed
client per configured source and the D-108 label/web-base rendering the binding owns.
"""

from __future__ import annotations

import pytest

from blizzard.hub.config import PmSourceConfig
from blizzard.hub.domain.work import PmPointer
from blizzard.hub.pm.internal.factory import build_pm_registry
from blizzard.hub.pm.internal.github_pm_source import GitHubPmSource
from blizzard.hub.pm.registry import PmSourceRegistry
from blizzard.hub.pm.source import UnknownSource
from tests.support import github_double

pytestmark = pytest.mark.component


def test_fetch_reads_issue_body_and_comments() -> None:
    issues = {"acme/widget#12": {"body": "the bug", "comments": ["me too", "repro"]}}
    source = GitHubPmSource(github_double(issues=issues), name="widget", repo="acme/widget", web_base="https://x")
    item = source.fetch(PmPointer(source="widget", ref="12"))
    assert item.body == "the bug"
    assert item.comments == ["me too", "repro"]


def test_label_renders_source_name_hash_ref() -> None:
    """D-108: the label is ``{name}#{ref}`` — the source's own configured name, not a
    provider short-code."""
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    pointer = PmPointer(source="widget", ref="12")
    assert source.label(pointer) == "widget#12"


def test_web_url_renders_the_browser_issue_address() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = PmPointer(source="widget", ref="12")
    assert source.web_url(pointer) == "https://github.com/acme/widget/issues/12"


def test_branch_url_qualifies_a_bare_repo_with_this_source_s_owner() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    assert source.branch_url("widget", "feat/x") == "https://github.com/acme/widget/tree/feat/x"
    assert source.branch_url("other/widget", "feat/x") == "https://github.com/other/widget/tree/feat/x"


def test_parse_accepts_this_source_s_own_token_form() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = source.parse("widget:12")
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_rejects_a_token_naming_a_different_source() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    with pytest.raises(UnknownSource):
        source.parse("other:12")


# --------------------------------------------------------------------------- #
# The factory (D-106) — one credentialed client per configured source.
# --------------------------------------------------------------------------- #


def test_factory_derives_web_base_by_stripping_the_api_host_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public GitHub: ``api.github.com`` -> ``github.com`` (strip the ``api.`` host)."""
    monkeypatch.setenv("_TEST_TOKEN_A", "token-a")
    registry = build_pm_registry(
        [PmSourceConfig(name="blizzard", provider="github", repo="paul-gross/blizzard", token_env="_TEST_TOKEN_A")]
    )
    source = registry.get("blizzard")
    assert source is not None
    pointer = PmPointer(source="blizzard", ref="9")
    assert source.web_url(pointer) == "https://github.com/paul-gross/blizzard/issues/9"


def test_factory_derives_web_base_by_stripping_the_api_v3_path_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GHE install: ``git.corp.internal/api/v3`` -> ``git.corp.internal`` (strip ``/api/v3``)."""
    monkeypatch.setenv("_TEST_TOKEN_GHE", "ghe-token")
    registry = build_pm_registry(
        [
            PmSourceConfig(
                name="internal",
                provider="github",
                repo="acme/internal-tool",
                token_env="_TEST_TOKEN_GHE",
                api_base="https://git.corp.internal/api/v3",
            )
        ]
    )
    source = registry.get("internal")
    assert source is not None
    pointer = PmPointer(source="internal", ref="2")
    assert source.web_url(pointer) == "https://git.corp.internal/acme/internal-tool/issues/2"


def test_factory_gives_each_source_its_own_credentialed_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two sources, two tokens: each built client carries only its own credential — the
    PM seam never shares one client (or token) across sources (D-106)."""
    monkeypatch.setenv("_TEST_TOKEN_ONE", "token-one")
    monkeypatch.setenv("_TEST_TOKEN_TWO", "token-two")
    sources = [
        PmSourceConfig(name="one", provider="github", repo="acme/one", token_env="_TEST_TOKEN_ONE"),
        PmSourceConfig(name="two", provider="github", repo="acme/two", token_env="_TEST_TOKEN_TWO"),
    ]
    registry = build_pm_registry(sources)
    assert sorted(registry.names()) == ["one", "two"]
    source_one = registry.get("one")
    source_two = registry.get("two")
    assert isinstance(source_one, GitHubPmSource)
    assert isinstance(source_two, GitHubPmSource)
    assert source_one._client.headers["Authorization"] == "token token-one"
    assert source_two._client.headers["Authorization"] == "token token-two"
    assert source_one._client is not source_two._client


def test_factory_fails_at_boot_naming_the_unset_token_variable() -> None:
    from blizzard.hub.config import ConfigError

    sources = [PmSourceConfig(name="one", provider="github", repo="acme/one", token_env="_DEFINITELY_UNSET_TOKEN")]
    with pytest.raises(ConfigError, match="_DEFINITELY_UNSET_TOKEN"):
        build_pm_registry(sources)


def test_factory_over_an_empty_source_list_is_a_legal_empty_registry() -> None:
    registry = build_pm_registry([])
    assert registry.names() == []
    assert registry.get("anything") is None


def test_registry_get_picks_the_named_binding_over_real_adapters() -> None:
    """D-105/D-106: resolution is a plain name lookup — ``registry.get(pointer.source)`` —
    never registration order. Proven against the real adapters that ship, not
    ``FakePmSource``."""
    alpha = GitHubPmSource(github_double(), name="alpha", repo="acme/alpha", web_base="https://x")
    beta = GitHubPmSource(github_double(), name="beta", repo="acme/beta", web_base="https://x")
    registry = PmSourceRegistry({"alpha": alpha, "beta": beta})

    beta_pointer = PmPointer(source="beta", ref="7")

    # `alpha` is registered first, yet a `beta`-sourced pointer must resolve to `beta`.
    assert registry.get(beta_pointer.source) is beta
    assert registry.get("alpha") is alpha
    # The label the board renders follows the named binding, not registration order.
    assert registry.get(beta_pointer.source).label(beta_pointer) == "beta#7"  # type: ignore[union-attr]
    # A name no binding declares resolves to None — the 422 at ingest, the null label at read.
    assert registry.get("gamma") is None


def test_registry_get_over_an_empty_registry_is_none() -> None:
    assert PmSourceRegistry({}).get("widget") is None
