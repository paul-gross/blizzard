"""The GitHub-shaped PM source adapter (component tier).

Exercises :class:`~blizzard.hub.pm.internal.github_pm_source.GitHubPmSource`'s
``{source, ref}`` pointer handling and vendor-native read against the GitHub-REST
double — the same choice of a local double over a ``blizzard-mock`` dev dependency
recorded in ``tests.support`` — plus the D-108 factory that builds one credentialed
client per configured source, the D-110 label/web-base rendering the binding owns, and
the D-111 ``parse``/registry ``resolve`` that give it its production caller.
"""

from __future__ import annotations

import pytest

from blizzard.hub.config import PmSourceConfig
from blizzard.hub.domain.work import PmPointer
from blizzard.hub.pm.internal.factory import build_pm_registry
from blizzard.hub.pm.internal.github_pm_source import GitHubPmSource
from blizzard.hub.pm.registry import PmSourceRegistry
from tests.support import OMIT_TITLE, github_double

pytestmark = pytest.mark.component


def test_fetch_reads_issue_body_and_comments() -> None:
    issues = {"acme/widget#12": {"title": "the bug title", "body": "the bug", "comments": ["me too", "repro"]}}
    source = GitHubPmSource(github_double(issues=issues), name="widget", repo="acme/widget", web_base="https://x")
    item = source.fetch(PmPointer(source="widget", ref="12"))
    assert item.title == "the bug title"
    assert item.body == "the bug"
    assert item.comments == ["me too", "repro"]


def test_label_renders_source_name_hash_ref() -> None:
    """D-110: the label is ``{name}#{ref}`` — the source's own configured name, not a
    provider short-code."""
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    pointer = PmPointer(source="widget", ref="12")
    assert source.label(pointer) == "widget#12"


def test_fetch_maps_a_missing_or_null_title_to_empty_string() -> None:
    """The forge's ``title`` is absent or ``null`` for some pointer shapes — never raise, degrade to ""."""
    issues = {
        "acme/widget#5": {"title": OMIT_TITLE, "body": "no title key", "comments": []},
        "acme/widget#6": {"title": None, "body": "null title", "comments": []},
    }
    source = GitHubPmSource(github_double(issues=issues), name="widget", repo="acme/widget", web_base="https://x")
    missing = source.fetch(PmPointer(source="widget", ref="5"))
    null = source.fetch(PmPointer(source="widget", ref="6"))
    assert missing.title == ""
    assert null.title == ""


def test_web_url_renders_the_browser_issue_address() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = PmPointer(source="widget", ref="12")
    assert source.web_url(pointer) == "https://github.com/acme/widget/issues/12"


def test_branch_url_qualifies_a_bare_repo_with_this_source_s_owner() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    assert source.branch_url("widget", "feat/x") == "https://github.com/acme/widget/tree/feat/x"
    assert source.branch_url("other/widget", "feat/x") == "https://github.com/other/widget/tree/feat/x"


def test_parse_accepts_this_source_s_own_colon_token_form() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = source.parse("widget:12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_accepts_this_source_s_own_hash_token_form() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = source.parse("widget#12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_accepts_this_source_s_own_full_issue_url() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = source.parse("https://github.com/acme/widget/issues/12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_accepts_this_source_s_own_schemeless_issue_url() -> None:
    """The schemeless shorthand (``{owner}/{repo}/issues/{n}``) the e2e tier ingests."""
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = source.parse("acme/widget/issues/12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_resolves_a_url_even_when_the_source_name_is_not_the_repo_tail() -> None:
    """The regression this phase exists to fix: the old CLI heuristic assumed a
    source's name is its repo tail and could never resolve this case."""
    source = GitHubPmSource(github_double(), name="bz", repo="paul-gross/blizzard", web_base="https://github.com")
    pointer = source.parse("https://github.com/paul-gross/blizzard/issues/26")
    assert pointer is not None
    assert pointer.source == "bz"
    assert pointer.ref == "26"


def test_parse_rejects_a_token_naming_a_different_source() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    assert source.parse("other:12") is None


def test_parse_rejects_a_url_naming_a_different_repo() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    assert source.parse("https://github.com/other-org/other-repo/issues/12") is None


def test_parse_rejects_an_unshaped_token() -> None:
    source = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    assert source.parse("no-separator-here") is None


# --------------------------------------------------------------------------- #
# The factory — one credentialed client per configured source.
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
    PM seam never shares one client (or token) across sources."""
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
    """D-107/D-108: resolution is a plain name lookup — ``registry.get(pointer.source)`` —
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


# --------------------------------------------------------------------------- #
# The registry's intake-side resolver — tries every configured binding's
# own `parse` in turn, first claim wins.
# --------------------------------------------------------------------------- #


def test_resolve_tries_every_binding_and_returns_the_first_claim() -> None:
    alpha = GitHubPmSource(github_double(), name="alpha", repo="acme/alpha", web_base="https://x")
    beta = GitHubPmSource(github_double(), name="beta", repo="acme/beta", web_base="https://x")
    registry = PmSourceRegistry({"alpha": alpha, "beta": beta})

    pointer = registry.resolve("beta:7")

    assert pointer == PmPointer(source="beta", ref="7")


def test_resolve_over_a_url_naming_a_source_whose_name_is_not_its_repo_tail() -> None:
    """The regression D-111 exists to fix, proven at the registry (the resolver a
    hub route actually calls), not just the binding directly."""
    bz = GitHubPmSource(github_double(), name="bz", repo="paul-gross/blizzard", web_base="https://github.com")
    registry = PmSourceRegistry({"bz": bz})

    pointer = registry.resolve("https://github.com/paul-gross/blizzard/issues/26")

    assert pointer == PmPointer(source="bz", ref="26")


def test_resolve_returns_none_when_no_binding_claims_the_token() -> None:
    widget = GitHubPmSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    registry = PmSourceRegistry({"widget": widget})

    assert registry.resolve("other:12") is None


def test_resolve_over_an_empty_registry_is_none() -> None:
    assert PmSourceRegistry({}).resolve("anything:1") is None


def test_registry_get_over_an_empty_registry_is_none() -> None:
    assert PmSourceRegistry({}).get("widget") is None
