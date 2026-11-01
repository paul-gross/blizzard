"""The GitHub-shaped work source adapter (component tier).

Exercises :class:`GitHubWorkSource`'s ``{source, ref}`` pointer handling and
vendor-native read against the GitHub-REST double, plus the factory, the label/web-base
rendering, and the ``parse``/registry ``resolve`` that give it its production caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.composition import TenantStores, build_hub_core, build_live_config
from blizzard.hub.domain.chunk.delivery_read import DeliveryTrace, TraceLanding
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.forges import GITHUB_API_BASE, ForgeId
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.domain.config.work_sources import WorkSourceFields, WorkSourceId
from blizzard.hub.forge.budget import ForgeBudgets
from blizzard.hub.secrets import hub_key_provider, secret_cipher
from blizzard.hub.tenancy import StoreScope
from blizzard.hub.work_sources.annotator import WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.closer import WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.internal.factory import WorkSourceEntry
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.source import IWorkSourceRegistry
from tests.support import (
    OMIT_TITLE,
    OP,
    ForgeDouble,
    ForgeIssue,
    WorkSourceRegistry,
    config_authoring,
    default_tenant,
    forge_state,
    github_double,
    migrate_to,
    seed_forge,
)

pytestmark = pytest.mark.component


@dataclass(frozen=True)
class _Source:
    """A GitHub work source to configure, on the forge holding ``api_base``."""

    locator: str
    secret: str
    api_base: str
    annotate: bool

    def fields(self, forge_id: ForgeId) -> WorkSourceFields:
        return WorkSourceFields(
            provider="github", locator=self.locator, forge_id=forge_id, annotate=self.annotate, secret=self.secret
        )


def _github(locator: str, *, secret: str = "gh", api_base: str = GITHUB_API_BASE, annotate: bool = False) -> _Source:
    return _Source(locator, secret, api_base, annotate)


def _registry_world(
    tmp_path: Path,
    *sources: tuple[str, _Source],
    tokens: dict[str, str] | None = None,
    close_forge_writes_enabled: bool = True,
) -> tuple[IWorkSourceRegistry, ConfigAuthoring, TenantStores, dict[str, WorkSourceId]]:
    """The production registry over a migrated store holding ``sources`` and their secrets,
    each written through ``ConfigAuthoring``, and each source's id by its slug."""
    engine = migrate_to(tmp_path, "head")[1]
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    keys = hub_key_provider({}, data_dir=tmp_path / "data")
    core = build_hub_core(engine, clock=clock)
    stores = core.open(StoreScope(default_tenant(engine)))
    authoring = config_authoring(engine, keys=keys, clock=clock)
    for secret, value in (tokens or {"gh": "token"}).items():
        authoring.create_secret(SecretName.parse(secret), value, OP)
    ids = {
        slug: authoring.create_work_source(
            slug, None, source.fields(seed_forge(engine, source.api_base)), OP
        ).work_source_id
        for slug, source in sources
    }
    live = build_live_config(stores, cipher=secret_cipher(keys))
    registry = WorkSourceEntry.registry(
        records=stores.work_source_records,
        forges=stores.forge_records,
        objects=live.objects,
        secrets=live.secrets,
        users=core.users,
        work_item_store=stores.work_item_store,
        edits=stores.work_item_edits,
        resolution=stores.garden_proposal_resolution,
        budgets=ForgeBudgets(clock=clock),
        close_forge_writes_enabled=close_forge_writes_enabled,
    )
    return registry, authoring, stores, ids


def _registry(
    tmp_path: Path,
    *sources: tuple[str, _Source],
    tokens: dict[str, str] | None = None,
    close_forge_writes_enabled: bool = True,
) -> tuple[IWorkSourceRegistry, dict[str, WorkSourceId]]:
    """The registry and each source's id by its slug."""
    registry, _authoring, _stores, ids = _registry_world(
        tmp_path, *sources, tokens=tokens, close_forge_writes_enabled=close_forge_writes_enabled
    )
    return registry, ids


def test_fetch_reads_issue_body_and_comments() -> None:
    issues: dict[str, ForgeIssue] = {
        "acme/widget#12": {"title": "the bug title", "body": "the bug", "comments": ["me too", "repro"]}
    }
    source = GitHubWorkSource(
        github_double(issues=issues), source_id="widget", slug="widget", repo="acme/widget", web_base="https://x"
    )
    item = source.fetch(WorkRef(source="widget", ref="12"))
    assert item.title == "the bug title"
    assert item.body == "the bug"
    assert item.comments == ["me too", "repro"]


def test_label_renders_source_name_hash_ref() -> None:
    """The label is ``{name}#{ref}`` — the source's own configured name, not a
    provider short-code."""
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://x"
    )
    pointer = WorkRef(source="widget", ref="12")
    assert source.label(pointer) == "widget#12"


def test_fetch_maps_a_missing_or_null_title_to_empty_string() -> None:
    """The forge's ``title`` is absent or ``null`` for some pointer shapes — never raise, degrade to ""."""
    issues: dict[str, ForgeIssue] = {
        "acme/widget#5": {"title": OMIT_TITLE, "body": "no title key", "comments": []},
        "acme/widget#6": {"title": None, "body": "null title", "comments": []},
    }
    source = GitHubWorkSource(
        github_double(issues=issues), source_id="widget", slug="widget", repo="acme/widget", web_base="https://x"
    )
    missing = source.fetch(WorkRef(source="widget", ref="5"))
    null = source.fetch(WorkRef(source="widget", ref="6"))
    assert missing.title == ""
    assert null.title == ""


def test_web_url_renders_the_browser_issue_address() -> None:
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://github.com"
    )
    pointer = WorkRef(source="widget", ref="12")
    assert source.web_url(pointer, live_holder=None) == "https://github.com/acme/widget/issues/12"


def test_forge_reference_names_the_configured_repo_not_the_source_alias() -> None:
    """GitHub cross-links ``owner/repo#n``; the source ``name`` is only a config alias."""
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://github.com"
    )
    assert source.forge_reference(WorkRef(source="widget", ref="12")) == "acme/widget#12"


def test_parse_accepts_this_source_s_own_colon_token_form() -> None:
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://github.com"
    )
    pointer = source.parse("widget:12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_accepts_this_source_s_own_hash_token_form() -> None:
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://github.com"
    )
    pointer = source.parse("widget#12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_accepts_this_source_s_own_full_issue_url() -> None:
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://github.com"
    )
    pointer = source.parse("https://github.com/acme/widget/issues/12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_accepts_this_source_s_own_schemeless_issue_url() -> None:
    """The schemeless shorthand (``{owner}/{repo}/issues/{n}``) the e2e tier ingests."""
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://github.com"
    )
    pointer = source.parse("acme/widget/issues/12")
    assert pointer is not None
    assert pointer.source == "widget"
    assert pointer.ref == "12"


def test_parse_resolves_a_url_even_when_the_source_name_is_not_the_repo_tail() -> None:
    """A source whose name is not its repo tail must still resolve."""
    source = GitHubWorkSource(
        github_double(), source_id="bz", slug="bz", repo="paul-gross/blizzard", web_base="https://github.com"
    )
    pointer = source.parse("https://github.com/paul-gross/blizzard/issues/26")
    assert pointer is not None
    assert pointer.source == "bz"
    assert pointer.ref == "26"


def test_parse_rejects_a_token_naming_a_different_source() -> None:
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://github.com"
    )
    assert source.parse("other:12") is None


def test_parse_matches_the_source_id_and_never_its_slug() -> None:
    """The registry rewrites a slug prefix to the id before ``parse`` sees it; ``parse`` itself answers only to
    the id, and every pointer it mints holds the id."""
    source = GitHubWorkSource(
        github_double(), source_id="wsrc_widget", slug="widget", repo="acme/widget", web_base="https://github.com"
    )
    assert source.parse("widget:12") is None
    assert source.parse("wsrc_widget#12") == WorkRef(source="wsrc_widget", ref="12")
    assert source.parse("acme/widget/issues/12") == WorkRef(source="wsrc_widget", ref="12")


def test_label_renders_the_slug_for_a_pointer_holding_the_id() -> None:
    source = GitHubWorkSource(
        github_double(), source_id="wsrc_widget", slug="widget", repo="acme/widget", web_base="https://github.com"
    )
    assert source.label(WorkRef(source="wsrc_widget", ref="12")) == "widget#12"


def test_parse_rejects_a_url_naming_a_different_repo() -> None:
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://github.com"
    )
    assert source.parse("https://github.com/other-org/other-repo/issues/12") is None


def test_parse_rejects_an_unshaped_token() -> None:
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://github.com"
    )
    assert source.parse("no-separator-here") is None


# --------------------------------------------------------------------------- #
# The factory — one credentialed client per configured source.


def test_factory_derives_web_base_by_stripping_the_api_host_prefix(tmp_path: Path) -> None:
    """Public GitHub: ``api.github.com`` -> ``github.com`` (strip the ``api.`` host)."""
    registry, ids = _registry(tmp_path, ("blizzard", _github("paul-gross/blizzard")))
    source = registry.get(ids["blizzard"])
    assert source is not None
    pointer = WorkRef(source=ids["blizzard"], ref="9")
    assert source.web_url(pointer, live_holder=None) == "https://github.com/paul-gross/blizzard/issues/9"


def test_factory_derives_web_base_by_stripping_the_api_v3_path_suffix(tmp_path: Path) -> None:
    """A GHE install: ``git.corp.internal/api/v3`` -> ``git.corp.internal`` (strip ``/api/v3``)."""
    source = _github("acme/internal-tool", api_base="https://git.corp.internal/api/v3")
    registry, ids = _registry(tmp_path, ("internal", source))
    source = registry.get(ids["internal"])
    assert source is not None
    pointer = WorkRef(source=ids["internal"], ref="2")
    assert source.web_url(pointer, live_holder=None) == "https://git.corp.internal/acme/internal-tool/issues/2"


def test_factory_gives_each_source_its_own_credentialed_client(tmp_path: Path) -> None:
    """Two sources, two secrets: each built client carries only its own credential — the
    work-source seam never shares one client (or token) across sources."""
    registry, ids = _registry(
        tmp_path,
        ("one", _github("acme/one", secret="tok-one")),
        ("two", _github("acme/two", secret="tok-two")),
        tokens={"tok-one": "token-one", "tok-two": "token-two"},
    )
    assert sorted(registry.names()) == ["hub", "one", "two"]
    source_one = registry.get(ids["one"])
    source_two = registry.get(ids["two"])
    assert isinstance(source_one, GitHubWorkSource)
    assert isinstance(source_two, GitHubWorkSource)
    assert source_one._client.headers["Authorization"] == "token token-one"
    assert source_two._client.headers["Authorization"] == "token token-two"
    assert source_one._client is not source_two._client


def test_a_retired_source_whose_secret_cannot_be_revealed_reads_as_absent(tmp_path: Path) -> None:
    """Items already ingested from a retired source must stay readable: once its secret is retired too,
    the source reads as absent rather than failing every read path."""
    registry, authoring, stores, ids = _registry_world(tmp_path, ("one", _github("acme/one")))
    record = stores.work_source_records.get(WorkSourceId(ids["one"]))
    assert record is not None
    authoring.retire_work_source(record, OP)
    secret = stores.secrets.get("gh")
    assert secret is not None
    authoring.retire_secret(secret, OP)
    assert registry.get(ids["one"]) is None


def test_factory_over_an_empty_source_list_still_seats_the_built_in_hub_source(tmp_path: Path) -> None:
    """Zero configured sources is a legal, non-empty registry:
    the built-in ``hub`` source is always seated, with no record and no credential."""
    registry, _ids = _registry(tmp_path)
    assert registry.names() == ["hub"]
    assert registry.get("anything") is None
    assert registry.resolve("hub:42") == WorkRef(source="hub", ref="42")


def test_registry_get_picks_the_named_binding_over_real_adapters() -> None:
    """Resolution is a plain name lookup — ``registry.get(pointer.source)`` —
    never registration order. Proven against the real adapters that ship, not
    ``FakeWorkSource``."""
    alpha = GitHubWorkSource(github_double(), source_id="alpha", slug="alpha", repo="acme/alpha", web_base="https://x")
    beta = GitHubWorkSource(github_double(), source_id="beta", slug="beta", repo="acme/beta", web_base="https://x")
    registry = WorkSourceRegistry({"alpha": alpha, "beta": beta})

    beta_pointer = WorkRef(source="beta", ref="7")

    # `alpha` is registered first, yet a `beta`-sourced pointer must resolve to `beta`.
    assert registry.get(beta_pointer.source) is beta
    assert registry.get("alpha") is alpha
    # The label follows the named binding, not registration order.
    assert registry.get(beta_pointer.source).label(beta_pointer) == "beta#7"  # type: ignore[union-attr]
    # A name no binding declares resolves to None — the 422 at ingest, the null label at read.
    assert registry.get("gamma") is None


# --------------------------------------------------------------------------- #
# The registry's intake-side resolver — tries every binding's `parse`, first claim wins.


def test_resolve_tries_every_binding_and_returns_the_first_claim() -> None:
    alpha = GitHubWorkSource(github_double(), source_id="alpha", slug="alpha", repo="acme/alpha", web_base="https://x")
    beta = GitHubWorkSource(github_double(), source_id="beta", slug="beta", repo="acme/beta", web_base="https://x")
    registry = WorkSourceRegistry({"alpha": alpha, "beta": beta})

    pointer = registry.resolve("beta:7")

    assert pointer == WorkRef(source="beta", ref="7")


def test_resolve_over_a_url_naming_a_source_whose_name_is_not_its_repo_tail() -> None:
    """A source whose name isn't its repo tail must still resolve at the registry —
    the resolver a hub route actually calls — not just the binding directly."""
    bz = GitHubWorkSource(
        github_double(), source_id="bz", slug="bz", repo="paul-gross/blizzard", web_base="https://github.com"
    )
    registry = WorkSourceRegistry({"bz": bz})

    pointer = registry.resolve("https://github.com/paul-gross/blizzard/issues/26")

    assert pointer == WorkRef(source="bz", ref="26")


def test_resolve_returns_none_when_no_binding_claims_the_token() -> None:
    widget = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://x"
    )
    registry = WorkSourceRegistry({"widget": widget})

    assert registry.resolve("other:12") is None


def test_resolve_over_an_empty_registry_is_none() -> None:
    assert WorkSourceRegistry({}).resolve("anything:1") is None


def test_registry_get_over_an_empty_registry_is_none() -> None:
    assert WorkSourceRegistry({}).get("widget") is None


# --------------------------------------------------------------------------- #
# IWorkAnnotator — the write half (forge-status projection)


def test_set_status_adds_the_desired_label_and_removes_the_other() -> None:
    double = github_double()
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")
    pointer = WorkRef(source="widget", ref="1")

    source.set_status(pointer, WorkStatusMarker.INGESTED)
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:ingested"}

    source.set_status(pointer, WorkStatusMarker.IN_PROGRESS)
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:in-progress"}


def test_set_status_is_idempotent() -> None:
    double = github_double()
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")
    pointer = WorkRef(source="widget", ref="1")

    source.set_status(pointer, WorkStatusMarker.INGESTED)
    source.set_status(pointer, WorkStatusMarker.INGESTED)

    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:ingested"}


def test_clear_status_removes_every_marker() -> None:
    double = github_double()
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")
    pointer = WorkRef(source="widget", ref="1")
    source.set_status(pointer, WorkStatusMarker.INGESTED)

    source.clear_status(pointer)

    assert forge_state(double)["issue_labels"]["acme/widget#1"] == set()


def test_clear_status_over_an_unlabelled_ref_is_a_no_op() -> None:
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://x"
    )
    source.clear_status(WorkRef(source="widget", ref="1"))  # must not raise


def test_label_bootstrap_creates_both_markers_before_the_first_write() -> None:
    double = github_double()
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")

    source.set_status(WorkRef(source="widget", ref="1"), WorkStatusMarker.INGESTED)

    assert forge_state(double)["repo_labels"]["acme/widget"] == {
        "blizzard:ingested",
        "blizzard:in-progress",
    }
    # Blizzard cyan variants, hashless per the API: a lighter cyan for ingested, a
    # darker one for in-progress.
    assert forge_state(double)["repo_label_colors"] == {
        "blizzard:ingested": "5cd1e5",
        "blizzard:in-progress": "2b6675",
    }


def test_label_bootstrap_tolerates_an_already_existing_label() -> None:
    """Two annotator instances against the same repo each bootstrap independently;
    the second's 422 (label already exists) must not surface as a failure."""
    double = github_double()
    first = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")
    second = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")

    first.set_status(WorkRef(source="widget", ref="1"), WorkStatusMarker.INGESTED)
    second.set_status(WorkRef(source="widget", ref="2"), WorkStatusMarker.INGESTED)  # must not raise


def test_marked_refs_lists_every_marker() -> None:
    double = github_double()
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")
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
    forge_state(double)["issue_labels"]["acme/widget#3"] = {"blizzard:ingested", "blizzard:in-progress"}
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")

    result = source.marked_refs()

    assert result[WorkRef(source="widget", ref="3")] == frozenset(
        {WorkStatusMarker.INGESTED, WorkStatusMarker.IN_PROGRESS}
    )


def test_marked_refs_paginates_past_the_first_page() -> None:
    """101 marked issues, a 100-per-page adapter: real pagination, not a stub —
    GitHub's default page size would otherwise silently strand the 101st."""
    double = github_double()
    forge_state(double)["issue_labels"] = {f"acme/widget#{n}": {"blizzard:ingested"} for n in range(1, 102)}
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")

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
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")

    result = source.marked_refs()

    assert set(result) == {WorkRef(source="widget", ref="1")}


def test_marked_refs_scope_failure_degrades_to_work_annotate_error() -> None:
    double = github_double()
    forge_state(double)["forbidden"] = True
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")

    with pytest.raises(WorkAnnotateError):
        source.marked_refs()


def test_set_status_scope_failure_degrades_to_work_annotate_error() -> None:
    double = github_double()
    forge_state(double)["forbidden"] = True
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")

    with pytest.raises(WorkAnnotateError):
        source.set_status(WorkRef(source="widget", ref="1"), WorkStatusMarker.INGESTED)


def test_set_status_add_label_failure_degrades_to_work_annotate_error() -> None:
    """A failing *add* must surface, not be swallowed — the sibling test above arms
    the broad ``forbidden`` lever, which trips the label bootstrap first and never
    reaches the add call, so this one fails only the add."""
    double = github_double()
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")
    forge_state(double)["label_add_forbidden"] = True

    with pytest.raises(WorkAnnotateError):
        source.set_status(WorkRef(source="widget", ref="1"), WorkStatusMarker.INGESTED)

    assert forge_state(double)["issue_labels"] == {}, "nothing should have been recorded on the forge"


def test_registry_annotator_is_none_for_a_source_not_opted_in() -> None:
    """A source configured but not bound into the annotator map — the structural
    ``registry.annotator(name) is None`` a non-opted-in source gets."""
    widget = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://x"
    )
    registry = WorkSourceRegistry({"widget": widget})

    assert registry.annotator("widget") is None
    assert registry.annotating_ids() == []


def test_registry_annotator_returns_the_bound_annotator() -> None:
    widget = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://x"
    )
    registry = WorkSourceRegistry({"widget": widget}, {"widget": widget})

    assert registry.annotator("widget") is widget
    assert registry.annotating_ids() == ["widget"]


# The factory's opt-in wiring: an annotator is built only for a
# source configured with annotate=True.


def test_factory_builds_no_annotator_for_a_non_opted_in_source(tmp_path: Path) -> None:
    """The structural "never written to" property: a configured-but-not-opted
    source answers no annotator at all."""
    registry, ids = _registry(tmp_path, ("widget", _github("acme/widget")))
    assert registry.get(ids["widget"]) is not None
    assert registry.annotator(ids["widget"]) is None
    assert registry.annotating_ids() == []


def test_factory_builds_an_annotator_for_an_opted_in_source(tmp_path: Path) -> None:
    registry, ids = _registry(tmp_path, ("widget", _github("acme/widget", annotate=True)))
    annotator = registry.annotator(ids["widget"])
    assert annotator is not None
    assert annotator is registry.get(ids["widget"])  # one instance, both Protocols
    assert registry.annotating_ids() == [ids["widget"]]


# --------------------------------------------------------------------------- #
# IWorkCloser — the close half (delivery-time closure)


def test_close_issues_the_documented_patch() -> None:
    double = github_double(issues={"acme/widget#1": {"body": "b"}})
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")

    source.close(WorkRef(source="widget", ref="1"), trace=None)

    assert forge_state(double)["issue_state"]["acme/widget#1"] == {
        "state": "closed",
        "state_reason": "completed",
    }


def test_close_is_idempotent() -> None:
    double = github_double(issues={"acme/widget#1": {"body": "b"}})
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")
    pointer = WorkRef(source="widget", ref="1")

    source.close(pointer, trace=None)
    source.close(pointer, trace=None)  # must not raise — a clean no-op re-close

    assert forge_state(double)["issue_state"]["acme/widget#1"] == {
        "state": "closed",
        "state_reason": "completed",
    }


def test_close_a_missing_item_raises_work_item_gone() -> None:
    source = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://x"
    )

    with pytest.raises(WorkItemGoneError):
        source.close(WorkRef(source="widget", ref="999"), trace=None)


def test_close_scope_failure_degrades_to_work_close_error() -> None:
    double = github_double(issues={"acme/widget#1": {"body": "b"}})
    forge_state(double)["forbidden"] = True
    source = GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")

    with pytest.raises(WorkCloseError):
        source.close(WorkRef(source="widget", ref="1"), trace=None)


_TRACE = DeliveryTrace(
    chunk_id="ch_9",
    board_url="https://hub.example/board/chunk/ch_9",
    landings=(
        TraceLanding(
            repo="acme/widget",
            pr_url="https://github.com/acme/widget/pull/7",
            commit_hash="0123456789abcdef",
            commit_url="https://github.com/acme/widget/commit/0123456789abcdef",
        ),
    ),
)
_CLOSING_KEYWORD = re.compile(r"\b(close[sd]?|fix(es|ed)?|resolve[sd]?)\b", re.IGNORECASE)


def _trace_comments(double: ForgeDouble, key: str = "acme/widget#1") -> list[str]:
    return [c for c in forge_state(double)["issues"][key]["comments"] if "blizzard-delivery:ch_9" in c]  # type: ignore[index]


def _closer_over(issues: dict[str, ForgeIssue]) -> tuple[ForgeDouble, GitHubWorkSource]:
    double = github_double(issues=issues)
    return double, GitHubWorkSource(double, source_id="widget", slug="widget", repo="acme/widget", web_base="https://x")


def test_close_with_a_trace_comments_the_landing_then_closes() -> None:
    double, source = _closer_over({"acme/widget#1": {"body": "b"}})

    source.close(WorkRef(source="widget", ref="1"), trace=_TRACE)

    [comment] = _trace_comments(double)
    assert "https://github.com/acme/widget/pull/7" in comment
    assert "https://github.com/acme/widget/commit/0123456789abcdef" in comment
    assert "ch_9" in comment
    assert not _CLOSING_KEYWORD.search(comment)
    assert forge_state(double)["issue_state"]["acme/widget#1"]["state"] == "closed"


def test_a_repeat_close_posts_no_second_comment() -> None:
    double, source = _closer_over({"acme/widget#1": {"body": "b"}})
    pointer = WorkRef(source="widget", ref="1")

    source.close(pointer, trace=_TRACE)
    source.close(pointer, trace=_TRACE)

    assert len(_trace_comments(double)) == 1


def test_a_comment_followed_by_a_failed_close_is_not_repeated_on_retry() -> None:
    double, source = _closer_over({"acme/widget#1": {"body": "b"}})
    pointer = WorkRef(source="widget", ref="1")
    forge_state(double)["forbidden"] = True
    with pytest.raises(WorkCloseError):
        source.close(pointer, trace=_TRACE)
    forge_state(double)["forbidden"] = False

    source.close(pointer, trace=_TRACE)

    assert len(_trace_comments(double)) == 1
    assert forge_state(double)["issue_state"]["acme/widget#1"]["state"] == "closed"


def test_the_marker_is_found_past_the_first_page_of_comments() -> None:
    crowd = [f"chatter {i}" for i in range(250)]
    double, source = _closer_over({"acme/widget#1": {"body": "b", "comments": crowd}})
    pointer = WorkRef(source="widget", ref="1")

    source.close(pointer, trace=_TRACE)
    source.close(pointer, trace=_TRACE)

    assert len(_trace_comments(double)) == 1


def test_close_without_a_trace_posts_no_comment() -> None:
    double, source = _closer_over({"acme/widget#1": {"body": "b"}})

    source.close(WorkRef(source="widget", ref="1"), trace=None)

    assert forge_state(double)["issues"]["acme/widget#1"].get("comments", []) == []


def test_close_a_missing_item_with_a_trace_raises_work_item_gone() -> None:
    _double, source = _closer_over({})

    with pytest.raises(WorkItemGoneError):
        source.close(WorkRef(source="widget", ref="999"), trace=_TRACE)


def test_registry_closer_is_none_when_no_closer_is_bound() -> None:
    """A source built with no closer entry — the structural ``registry.closer(name) is
    None`` an outbox intent whose source dropped out of config would find."""
    widget = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://x"
    )
    registry = WorkSourceRegistry({"widget": widget})

    assert registry.closer("widget") is None
    assert registry.get("widget") is widget  # `get` still resolves the read-only source


def test_registry_closer_returns_the_bound_closer() -> None:
    widget = GitHubWorkSource(
        github_double(), source_id="widget", slug="widget", repo="acme/widget", web_base="https://x"
    )
    registry = WorkSourceRegistry({"widget": widget}, closers={"widget": widget})

    assert registry.closer("widget") is widget


# The factory's unconditional wiring: a closer is built for every configured source
# while `close_forge_writes_enabled` is true, alongside the always-seated `hub` source.


def test_factory_builds_a_closer_for_every_configured_source(tmp_path: Path) -> None:
    registry, ids = _registry(tmp_path, ("widget", _github("acme/widget")))
    closer = registry.closer(ids["widget"])
    assert closer is not None
    assert closer is registry.get(ids["widget"])  # one instance, every closing Protocol


def test_factory_seats_the_hub_closer_with_zero_configured_sources(tmp_path: Path) -> None:
    """The built-in ``hub`` source needs no configured record at all to be seated as a closer."""
    registry, _ids = _registry(tmp_path)

    closer = registry.closer("hub")

    assert closer is not None
    assert closer is registry.get("hub")


# `close_forge_writes_enabled=False`: a non-canonical hub declines every configured
# source's forge writes — never the built-in `hub` source's, which writes no forge.


def test_factory_seats_no_configured_closer_when_forge_writes_are_disabled(tmp_path: Path) -> None:
    registry, ids = _registry(tmp_path, ("widget", _github("acme/widget")), close_forge_writes_enabled=False)

    assert registry.closer(ids["widget"]) is None
    assert registry.get(ids["widget"]) is not None  # the read-only source is still built


def test_factory_seats_the_hub_closer_even_when_forge_writes_are_disabled(tmp_path: Path) -> None:
    registry, _ids = _registry(tmp_path, close_forge_writes_enabled=False)

    closer = registry.closer("hub")

    assert closer is not None
    assert closer is registry.get("hub")
