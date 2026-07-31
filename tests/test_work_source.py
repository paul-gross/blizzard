"""The GitHub-shaped work source adapter (component tier).

Exercises :class:`~blizzard.hub.work_sources.internal.github_work_source.GitHubWorkSource`'s
``{source, ref}`` pointer handling and vendor-native read against the GitHub-REST
double — the same choice of a local double over a ``blizzard-mock`` dev dependency
recorded in ``tests.support`` — plus the factory that builds one credentialed
client per configured source, the label/web-base rendering the binding owns, and
the ``parse``/registry ``resolve`` that give it its production caller.
"""

from __future__ import annotations

import pytest

from blizzard.hub.config import WorkSourceConfig
from blizzard.hub.domain.work import WorkRef
from blizzard.hub.work_sources.annotator import WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.internal.factory import build_work_source_registry
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.registry import WorkSourceRegistry
from tests.support import OMIT_TITLE, forge_state, github_double

pytestmark = pytest.mark.component


def test_fetch_reads_issue_body_and_comments() -> None:
    issues = {"acme/widget#12": {"title": "the bug title", "body": "the bug", "comments": ["me too", "repro"]}}
    source = GitHubWorkSource(github_double(issues=issues), name="widget", repo="acme/widget", web_base="https://x")
    item = source.fetch(WorkRef(source="widget", ref="12"))
    assert item.title == "the bug title"
    assert item.body == "the bug"
    assert item.comments == ["me too", "repro"]


def test_label_renders_source_name_hash_ref() -> None:
    """The label is ``{name}#{ref}`` — the source's own configured name, not a
    provider short-code."""
    source = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    pointer = WorkRef(source="widget", ref="12")
    assert source.label(pointer) == "widget#12"


def test_fetch_maps_a_missing_or_null_title_to_empty_string() -> None:
    """The forge's ``title`` is absent or ``null`` for some pointer shapes — never raise, degrade to ""."""
    issues = {
        "acme/widget#5": {"title": OMIT_TITLE, "body": "no title key", "comments": []},
        "acme/widget#6": {"title": None, "body": "null title", "comments": []},
    }
    source = GitHubWorkSource(github_double(issues=issues), name="widget", repo="acme/widget", web_base="https://x")
    missing = source.fetch(WorkRef(source="widget", ref="5"))
    null = source.fetch(WorkRef(source="widget", ref="6"))
    assert missing.title == ""
    assert null.title == ""


def test_web_url_renders_the_browser_issue_address() -> None:
    source = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = WorkRef(source="widget", ref="12")
    assert source.web_url(pointer) == "https://github.com/acme/widget/issues/12"


def test_branch_url_qualifies_a_bare_repo_with_this_source_s_owner() -> None:
    source = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    assert source.branch_url("widget", "feat/x") == "https://github.com/acme/widget/tree/feat/x"
    assert source.branch_url("other/widget", "feat/x") == "https://github.com/other/widget/tree/feat/x"


def test_parse_accepts_this_source_s_own_colon_token_form() -> None:
    source = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = source.parse("widget:12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_accepts_this_source_s_own_hash_token_form() -> None:
    source = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = source.parse("widget#12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_accepts_this_source_s_own_full_issue_url() -> None:
    source = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = source.parse("https://github.com/acme/widget/issues/12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_accepts_this_source_s_own_schemeless_issue_url() -> None:
    """The schemeless shorthand (``{owner}/{repo}/issues/{n}``) the e2e tier ingests."""
    source = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    pointer = source.parse("acme/widget/issues/12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_resolves_a_url_even_when_the_source_name_is_not_the_repo_tail() -> None:
    """The regression this phase exists to fix: the old CLI heuristic assumed a
    source's name is its repo tail and could never resolve this case."""
    source = GitHubWorkSource(github_double(), name="bz", repo="paul-gross/blizzard", web_base="https://github.com")
    pointer = source.parse("https://github.com/paul-gross/blizzard/issues/26")
    assert pointer is not None
    assert pointer.source == "bz"
    assert pointer.ref == "26"


def test_parse_rejects_a_token_naming_a_different_source() -> None:
    source = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    assert source.parse("other:12") is None


def test_parse_rejects_a_url_naming_a_different_repo() -> None:
    source = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    assert source.parse("https://github.com/other-org/other-repo/issues/12") is None


def test_parse_rejects_an_unshaped_token() -> None:
    source = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://github.com")
    assert source.parse("no-separator-here") is None


# --------------------------------------------------------------------------- #
# The factory — one credentialed client per configured source.
# --------------------------------------------------------------------------- #


def test_factory_derives_web_base_by_stripping_the_api_host_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public GitHub: ``api.github.com`` -> ``github.com`` (strip the ``api.`` host)."""
    monkeypatch.setenv("_TEST_TOKEN_A", "token-a")
    registry = build_work_source_registry(
        [WorkSourceConfig(name="blizzard", provider="github", repo="paul-gross/blizzard", token_env="_TEST_TOKEN_A")]
    )
    source = registry.get("blizzard")
    assert source is not None
    pointer = WorkRef(source="blizzard", ref="9")
    assert source.web_url(pointer) == "https://github.com/paul-gross/blizzard/issues/9"


def test_factory_derives_web_base_by_stripping_the_api_v3_path_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GHE install: ``git.corp.internal/api/v3`` -> ``git.corp.internal`` (strip ``/api/v3``)."""
    monkeypatch.setenv("_TEST_TOKEN_GHE", "ghe-token")
    registry = build_work_source_registry(
        [
            WorkSourceConfig(
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
    pointer = WorkRef(source="internal", ref="2")
    assert source.web_url(pointer) == "https://git.corp.internal/acme/internal-tool/issues/2"


def test_factory_gives_each_source_its_own_credentialed_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two sources, two tokens: each built client carries only its own credential — the
    work-source seam never shares one client (or token) across sources."""
    monkeypatch.setenv("_TEST_TOKEN_ONE", "token-one")
    monkeypatch.setenv("_TEST_TOKEN_TWO", "token-two")
    sources = [
        WorkSourceConfig(name="one", provider="github", repo="acme/one", token_env="_TEST_TOKEN_ONE"),
        WorkSourceConfig(name="two", provider="github", repo="acme/two", token_env="_TEST_TOKEN_TWO"),
    ]
    registry = build_work_source_registry(sources)
    assert sorted(registry.names()) == ["one", "two"]
    source_one = registry.get("one")
    source_two = registry.get("two")
    assert isinstance(source_one, GitHubWorkSource)
    assert isinstance(source_two, GitHubWorkSource)
    assert source_one._client.headers["Authorization"] == "token token-one"
    assert source_two._client.headers["Authorization"] == "token token-two"
    assert source_one._client is not source_two._client


def test_factory_fails_at_boot_naming_the_unset_token_variable() -> None:
    from blizzard.hub.config import ConfigError

    sources = [WorkSourceConfig(name="one", provider="github", repo="acme/one", token_env="_DEFINITELY_UNSET_TOKEN")]
    with pytest.raises(ConfigError, match="_DEFINITELY_UNSET_TOKEN"):
        build_work_source_registry(sources)


def test_factory_over_an_empty_source_list_is_a_legal_empty_registry() -> None:
    registry = build_work_source_registry([])
    assert registry.names() == []
    assert registry.get("anything") is None


def test_registry_get_picks_the_named_binding_over_real_adapters() -> None:
    """Resolution is a plain name lookup — ``registry.get(pointer.source)`` —
    never registration order. Proven against the real adapters that ship, not
    ``FakeWorkSource``."""
    alpha = GitHubWorkSource(github_double(), name="alpha", repo="acme/alpha", web_base="https://x")
    beta = GitHubWorkSource(github_double(), name="beta", repo="acme/beta", web_base="https://x")
    registry = WorkSourceRegistry({"alpha": alpha, "beta": beta})

    beta_pointer = WorkRef(source="beta", ref="7")

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
    alpha = GitHubWorkSource(github_double(), name="alpha", repo="acme/alpha", web_base="https://x")
    beta = GitHubWorkSource(github_double(), name="beta", repo="acme/beta", web_base="https://x")
    registry = WorkSourceRegistry({"alpha": alpha, "beta": beta})

    pointer = registry.resolve("beta:7")

    assert pointer == WorkRef(source="beta", ref="7")


def test_resolve_over_a_url_naming_a_source_whose_name_is_not_its_repo_tail() -> None:
    """The regression this guards against: a source whose name isn't its repo tail
    must still resolve at the registry (the resolver a hub route actually calls), not
    just the binding directly."""
    bz = GitHubWorkSource(github_double(), name="bz", repo="paul-gross/blizzard", web_base="https://github.com")
    registry = WorkSourceRegistry({"bz": bz})

    pointer = registry.resolve("https://github.com/paul-gross/blizzard/issues/26")

    assert pointer == WorkRef(source="bz", ref="26")


def test_resolve_returns_none_when_no_binding_claims_the_token() -> None:
    widget = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    registry = WorkSourceRegistry({"widget": widget})

    assert registry.resolve("other:12") is None


def test_resolve_over_an_empty_registry_is_none() -> None:
    assert WorkSourceRegistry({}).resolve("anything:1") is None


def test_registry_get_over_an_empty_registry_is_none() -> None:
    assert WorkSourceRegistry({}).get("widget") is None


# --------------------------------------------------------------------------- #
# IWorkAnnotator — the write half (forge-status projection, issue #177)
# --------------------------------------------------------------------------- #


def test_set_status_adds_the_desired_label_and_removes_the_other() -> None:
    double = github_double()
    source = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")
    pointer = WorkRef(source="widget", ref="1")

    source.set_status(pointer, WorkStatusMarker.INGESTED)
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:ingested"}  # type: ignore[index]

    source.set_status(pointer, WorkStatusMarker.IN_PROGRESS)
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:in-progress"}  # type: ignore[index]


def test_set_status_is_idempotent() -> None:
    double = github_double()
    source = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")
    pointer = WorkRef(source="widget", ref="1")

    source.set_status(pointer, WorkStatusMarker.INGESTED)
    source.set_status(pointer, WorkStatusMarker.INGESTED)

    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:ingested"}  # type: ignore[index]


def test_clear_status_removes_every_marker() -> None:
    double = github_double()
    source = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")
    pointer = WorkRef(source="widget", ref="1")
    source.set_status(pointer, WorkStatusMarker.INGESTED)

    source.clear_status(pointer)

    assert forge_state(double)["issue_labels"]["acme/widget#1"] == set()  # type: ignore[index]


def test_clear_status_over_an_unlabelled_ref_is_a_no_op() -> None:
    source = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    source.clear_status(WorkRef(source="widget", ref="1"))  # must not raise


def test_label_bootstrap_creates_both_markers_before_the_first_write() -> None:
    double = github_double()
    source = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")

    source.set_status(WorkRef(source="widget", ref="1"), WorkStatusMarker.INGESTED)

    assert forge_state(double)["repo_labels"]["acme/widget"] == {  # type: ignore[index]
        "blizzard:ingested",
        "blizzard:in-progress",
    }
    # Blizzard cyan variants, hashless per the API: at-rest ingested in the board's
    # light `--cyan` token, active in-progress in the darker `--cyan-dim`.
    assert forge_state(double)["repo_label_colors"] == {  # type: ignore[index]
        "blizzard:ingested": "5cd1e5",
        "blizzard:in-progress": "2b6675",
    }


def test_label_bootstrap_tolerates_an_already_existing_label() -> None:
    """Two annotator instances against the same repo each bootstrap independently;
    the second's 422 (label already exists) must not surface as a failure."""
    double = github_double()
    first = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")
    second = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")

    first.set_status(WorkRef(source="widget", ref="1"), WorkStatusMarker.INGESTED)
    second.set_status(WorkRef(source="widget", ref="2"), WorkStatusMarker.INGESTED)  # must not raise


def test_marked_refs_lists_every_marker() -> None:
    double = github_double()
    source = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")
    source.set_status(WorkRef(source="widget", ref="1"), WorkStatusMarker.INGESTED)
    source.set_status(WorkRef(source="widget", ref="2"), WorkStatusMarker.IN_PROGRESS)

    result = source.marked_refs()

    assert result == {
        WorkRef(source="widget", ref="1"): frozenset({WorkStatusMarker.INGESTED}),
        WorkRef(source="widget", ref="2"): frozenset({WorkStatusMarker.IN_PROGRESS}),
    }


def test_marked_refs_reports_a_doubly_labelled_ref_as_carrying_both_markers() -> None:
    """A ref that has somehow acquired both markers reads as *wrong* to the
    reconciler's diff, not as already-correct — ``marked_refs`` must surface it."""
    double = github_double()
    forge_state(double)["issue_labels"]["acme/widget#3"] = {"blizzard:ingested", "blizzard:in-progress"}  # type: ignore[index]
    source = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")

    result = source.marked_refs()

    assert result[WorkRef(source="widget", ref="3")] == frozenset(
        {WorkStatusMarker.INGESTED, WorkStatusMarker.IN_PROGRESS}
    )


def test_marked_refs_paginates_past_the_first_page() -> None:
    """101 marked issues, a 100-per-page adapter: real pagination, not a stub —
    GitHub's default page size would otherwise silently strand the 101st."""
    double = github_double()
    forge_state(double)["issue_labels"] = {f"acme/widget#{n}": {"blizzard:ingested"} for n in range(1, 102)}
    source = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")

    result = source.marked_refs()

    assert len(result) == 101
    assert WorkRef(source="widget", ref="101") in result


def test_marked_refs_excludes_pull_request_entries() -> None:
    """GitHub's issue-list endpoint returns PRs too; a PR entry must not be
    mistaken for a marked work-item ref."""
    double = github_double(pull_numbers={2})
    forge_state(double)["issue_labels"] = {
        "acme/widget#1": {"blizzard:ingested"},
        "acme/widget#2": {"blizzard:ingested"},
    }
    source = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")

    result = source.marked_refs()

    assert set(result) == {WorkRef(source="widget", ref="1")}


def test_marked_refs_scope_failure_degrades_to_work_annotate_error() -> None:
    double = github_double()
    forge_state(double)["forbidden"] = True
    source = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")

    with pytest.raises(WorkAnnotateError):
        source.marked_refs()


def test_set_status_scope_failure_degrades_to_work_annotate_error() -> None:
    double = github_double()
    forge_state(double)["forbidden"] = True
    source = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")

    with pytest.raises(WorkAnnotateError):
        source.set_status(WorkRef(source="widget", ref="1"), WorkStatusMarker.INGESTED)


def test_set_status_add_label_failure_degrades_to_work_annotate_error() -> None:
    """A failing *add* must surface, not be swallowed.

    The sibling test above arms the broad ``forbidden`` lever, which trips the
    repo-label bootstrap first — so it never reaches the add call and cannot fence
    it. This one fails only the add: the paired remove that follows 404s (the other
    marker is not present), which the adapter deliberately tolerates, so nothing
    else in the call raises on the add's behalf. Without the add's own status check
    ``set_status`` returns cleanly, and the reconciler counts a write that never
    landed — ``written=1 failed=0`` in the sweep log while the forge is unchanged.
    """
    double = github_double()
    source = GitHubWorkSource(double, name="widget", repo="acme/widget", web_base="https://x")
    forge_state(double)["label_add_forbidden"] = True

    with pytest.raises(WorkAnnotateError):
        source.set_status(WorkRef(source="widget", ref="1"), WorkStatusMarker.INGESTED)

    assert forge_state(double)["issue_labels"] == {}, "nothing should have been recorded on the forge"


def test_registry_annotator_is_none_for_a_source_not_opted_in() -> None:
    """A source configured but not bound into the annotator map — the structural
    ``registry.annotator(name) is None`` a non-opted-in ``[[work_source]]`` gets."""
    widget = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    registry = WorkSourceRegistry({"widget": widget})

    assert registry.annotator("widget") is None
    assert registry.annotating_names() == []


def test_registry_annotator_returns_the_bound_annotator() -> None:
    widget = GitHubWorkSource(github_double(), name="widget", repo="acme/widget", web_base="https://x")
    registry = WorkSourceRegistry({"widget": widget}, {"widget": widget})

    assert registry.annotator("widget") is widget
    assert registry.annotating_names() == ["widget"]


# --------------------------------------------------------------------------- #
# The factory's opt-in wiring (issue #179 Phase 3) — an annotator is built
# only for a source configured with annotate=True.
# --------------------------------------------------------------------------- #


def test_factory_builds_no_annotator_for_a_non_opted_in_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """The structural "never written to" property: a configured-but-not-opted
    source has no entry in the annotator map at all."""
    monkeypatch.setenv("_TEST_TOKEN_NOT_OPTED", "token")
    registry = build_work_source_registry(
        [WorkSourceConfig(name="widget", provider="github", repo="acme/widget", token_env="_TEST_TOKEN_NOT_OPTED")]
    )
    assert registry.get("widget") is not None
    assert registry.annotator("widget") is None
    assert registry.annotating_names() == []


def test_factory_builds_an_annotator_for_an_opted_in_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("_TEST_TOKEN_OPTED", "token")
    registry = build_work_source_registry(
        [
            WorkSourceConfig(
                name="widget", provider="github", repo="acme/widget", token_env="_TEST_TOKEN_OPTED", annotate=True
            )
        ]
    )
    annotator = registry.annotator("widget")
    assert annotator is not None
    assert annotator is registry.get("widget")  # one instance, both Protocols
    assert registry.annotating_names() == ["widget"]
