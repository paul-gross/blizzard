"""The GitHub-shaped PM source adapter (component tier).

Exercises :class:`~blizzard.hub.pm.internal.github_pm_source.GitHubPmSource`'s URL
parsing and vendor-native read against the GitHub-REST double — the same choice of a
local double over a ``blizzard-mock`` dev dependency recorded in ``tests.support`` —
plus the D-106 factory that builds one credentialed client per configured source and
the D-108 label/web-base rendering the binding now owns.
"""

from __future__ import annotations

import pytest

from blizzard.hub.config import PmSourceConfig
from blizzard.hub.domain.work import PmPointer
from blizzard.hub.pm.internal.factory import build_pm_registry
from blizzard.hub.pm.internal.github_pm_source import GitHubPmSource
from blizzard.hub.pm.registry import PmSourceRegistry, resolve_source
from blizzard.hub.pm.source import PmSourceError, UnknownSource
from tests.support import github_double

pytestmark = pytest.mark.component


def test_fetch_reads_issue_body_and_comments() -> None:
    issues = {"acme/widget#12": {"body": "the bug", "comments": ["me too", "repro"]}}
    source = GitHubPmSource(github_double(issues=issues), name="widget", repo="acme/widget", web_base="https://x")
    item = source.fetch(PmPointer(provider="github", url="http://forge/repos/acme/widget/issues/12"))
    assert item.body == "the bug"
    assert item.comments == ["me too", "repro"]


def test_fetch_parses_html_style_pointer_urls() -> None:
    issues = {"acme/widget#3": {"body": "x", "comments": []}}
    source = GitHubPmSource(github_double(issues=issues), name="widget", repo="acme/widget", web_base="https://x")
    # A pointer carrying the html_url form still resolves to the API path.
    item = source.fetch(PmPointer(provider="github", url="http://forge/acme/widget/issues/3"))
    assert item.body == "x"


def test_unparseable_pointer_raises() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    with pytest.raises(PmSourceError):
        source.fetch(PmPointer(provider="github", url="http://forge/not-an-issue"))


def test_fetch_rejects_a_pointer_naming_a_different_repo() -> None:
    """A source is pinned to its own configured repo (D-106) — a pointer naming another
    repo is a hard error, not a silent cross-repo read."""
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    with pytest.raises(PmSourceError):
        source.fetch(PmPointer(provider="github", url="http://forge/repos/other/repo/issues/1"))


def test_label_renders_source_name_hash_number() -> None:
    """D-108: the label is ``{name}#{number}`` — the source's own configured name, not a
    provider short-code."""
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    pointer = PmPointer(provider="github", url="http://forge/repos/acme/widget/issues/12")
    assert source.label(pointer) == "widget#12"


def test_label_is_none_for_a_non_issue_shaped_pointer() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    pointer = PmPointer(provider="github", url="http://forge/acme/widget/wiki")
    assert source.label(pointer) is None


def test_web_url_renders_the_browser_issue_address() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = PmPointer(provider="github", url="http://forge/repos/acme/widget/issues/12")
    assert source.web_url(pointer) == "https://github.com/acme/widget/issues/12"


def test_branch_url_qualifies_a_bare_repo_with_this_source_s_owner() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    assert source.branch_url("widget", "feat/x") == "https://github.com/acme/widget/tree/feat/x"
    assert source.branch_url("other/widget", "feat/x") == "https://github.com/other/widget/tree/feat/x"


def test_parse_accepts_this_source_s_own_token_form() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = source.parse("widget:12")
    assert pointer.provider == "github"
    assert pointer.url == "https://github.com/acme/widget/issues/12"


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
    pointer = PmPointer(provider="github", url="https://api.github.com/repos/paul-gross/blizzard/issues/9")
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
    pointer = PmPointer(provider="github", url="https://git.corp.internal/api/v3/repos/acme/internal-tool/issues/2")
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


def _source(name: str, repo: str) -> GitHubPmSource:
    return GitHubPmSource(github_double(), name=name, repo=repo, web_base="https://x")


@pytest.mark.parametrize(
    ("url", "owned"),
    [
        # Both URL shapes the e2e tier ingests (D-107) — schemeless shorthand and full html_url.
        ("acme/widget/issues/3", True),
        ("https://github.com/acme/widget/issues/3", True),
        # The REST `/repos/` prefix is stripped before the owner/repo pair is read.
        ("http://127.0.0.1:8080/repos/acme/widget/issues/1", True),
        # Repo membership is independent of issue shape: a wiki page still belongs to its
        # repo's binding even though `label`/`web_url` render it None.
        ("http://forge.local/acme/widget/wiki", True),
        # A query string does not leak into the repo segment.
        ("https://github.com/acme/widget/issues/9?foo=bar", True),
        # A different repo — the case Phase 1's first-entry shim got wrong.
        ("https://github.com/acme/gadget/issues/3", False),
        # A different owner, same repo name.
        ("https://github.com/other/widget/issues/3", False),
        # Fewer than two path segments names no repo at all.
        ("https://github.com/acme", False),
    ],
)
def test_owns_matches_repo_membership_on_the_real_adapter(url: str, owned: bool) -> None:
    """``owns`` (D-107) against the real GitHub grammar, not a test double's copy.

    ``tests.support.FakePmSource`` reimplements this matching with its own copy of the
    issue regex, so the component tier's two-sources-configured proof never exercises
    ``GitHubPmSource``'s own repo extraction. This pins it directly."""
    assert _source("widget", "acme/widget").owns(PmPointer(provider="github", url=url)) is owned


def test_resolver_picks_the_matching_binding_over_real_adapters() -> None:
    """The D-107 resolver over two **real** bindings — the Phase 1 regression, proven
    against the adapters that ship rather than against ``FakePmSource``.

    Phase 1's shim returned the registry's first entry for every pointer, so a ``beta``
    pointer resolved to ``alpha``. Registration order must not decide the match."""
    alpha, beta = _source("alpha", "acme/alpha"), _source("beta", "acme/beta")
    registry = PmSourceRegistry({"alpha": alpha, "beta": beta})

    beta_pointer = PmPointer(provider="github", url="https://github.com/acme/beta/issues/7")
    alpha_pointer = PmPointer(provider="github", url="https://github.com/acme/alpha/issues/7")

    # `alpha` is registered first, yet the beta pointer must resolve to `beta`.
    assert resolve_source(registry, beta_pointer) is beta
    assert resolve_source(registry, alpha_pointer) is alpha
    # The label the board renders follows the binding, not the registration order.
    assert resolve_source(registry, beta_pointer).label(beta_pointer) == "beta#7"  # type: ignore[union-attr]
    # A repo no binding claims resolves to None — the 422 at ingest, the null label at read.
    assert resolve_source(registry, PmPointer(provider="github", url="https://github.com/acme/gamma/issues/7")) is None


def test_resolver_over_an_empty_registry_is_none() -> None:
    assert resolve_source(PmSourceRegistry({}), PmPointer(provider="github", url="acme/widget/issues/1")) is None
