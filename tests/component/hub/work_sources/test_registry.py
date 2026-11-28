"""The hosted hub's work sources read from their records on use (component tier).

A real composition (``build_hosted_app``) over a migrated store, with the GitHub-shaped
double served on a loopback port so the adapter the factory builds talks HTTP to it.
Sources and secrets are written through the hub's own API while the app runs; nothing is
rebuilt between a write and the next registry call."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.chunk.ports.work_sources import WorkAnnotateError
from blizzard.hub.domain.chunk.status_marker import WorkStatusMarker
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import ForgeEdit, IReadForgeRepository
from blizzard.hub.domain.config.revisions import ConfigRevisions
from blizzard.hub.domain.config.work_sources import (
    ConfiguredWorkSource,
    IReadWorkSourceRepository,
    WorkSourceEdit,
    WorkSourceId,
    WorkSourceSlug,
)
from blizzard.hub.domain.kernel.forge_refusals import AuthRefusal, ClassifiedRefusal, ForgeDiagnostic
from blizzard.hub.domain.observability.forge_status import AnnotationReconciler
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.store.internal.config_revisions_store import ConfigRevisionsStore
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.registry import BuiltWorkSource, StoreWorkSourceRegistry
from blizzard.hub.work_sources.source import IWorkSourceRegistry, WorkSourceAuthRefused, WorkSourceError
from tests.support.config_inputs import build_hosted_hub_app, hosted_engine, init_hub
from tests.support.forge_auth import sent_header
from tests.support.registry_statements import registry_over
from tests.support.scaffolding import (
    OP,
    FakeWorkSource,
    ForgeDouble,
    forge_state,
    hub_store_connections,
    retire_credential,
    tenant_adapter_of,
)
from tests.support.work_source_registry_ops import (
    _id,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.work_source_registry_ops
    _ingest,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.work_source_registry_ops
    create,
    forge,
    promote,
)

# Re-exported so pytest resolves the shared `forge` fixture here; the tests request it by name.
_FIXTURES = (forge,)


def _annotation(client: TestClient) -> AnnotationReconciler:
    """The hosted hub's forge-status annotation sweep, resolved from its process container."""
    return cast(FastAPI, client.app).state.hub_process.resolver.get(AnnotationReconciler)


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[tuple[TestClient, FastAPI]]:
    config = init_hub(tmp_path / "hub")
    app = build_hosted_hub_app(config)
    # Never entered as a context manager: no lifespan, so no background sweep races the test's own passes.
    client = TestClient(app)
    yield client, app
    hosted_engine(app).dispose()


def _sources(app: FastAPI) -> IWorkSourceRegistry:
    return tenant_adapter_of(app, IWorkSourceRegistry)


def _rename(app: FastAPI, source_id: str, slug: str) -> None:
    record = tenant_adapter_of(app, IReadWorkSourceRepository).resolve(source_id)
    assert record is not None
    tenant_adapter_of(app, ConfigAuthoring).edit_work_source(record, WorkSourceEdit(slug=slug), OP)


def _adapter(app: FastAPI) -> GitHubWorkSource:
    source = _sources(app).get(_id(app, "widget"))
    assert isinstance(source, GitHubWorkSource)
    return source


def test_a_source_created_mid_run_claims_ingest_with_no_rebuild(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    _, forge_url = forge
    assert _ingest(client, "widget:1").status_code == 422

    source_id = create(client, app, forge_url)

    assert _ingest(client, "widget:1").status_code == 201
    assert _sources(app).names() == ["widget", "hub"]
    assert _sources(app).get(source_id) is not None


def test_a_secret_replace_reaches_the_next_get_and_a_later_one_closes_the_replaced_client(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    create(client, app, forge[1])
    before = _adapter(app)
    assert sent_header(before.client.auth) == "token tok-a"
    assert _adapter(app) is before  # a hit reuses the built adapter

    assert client.put("/api/secrets/gh/value", json={"value": "tok-b"}).status_code == 200

    after = _adapter(app)
    assert sent_header(after.client.auth) == "token tok-b"
    assert not before.client.is_closed  # a caller may still be mid-request on it
    assert client.put("/api/secrets/gh/value", json={"value": "tok-c"}).status_code == 200
    _adapter(app)
    assert before.client.is_closed


def test_a_forge_edit_rebuilds_the_adapter_of_every_source_naming_it(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])
    before = _adapter(app)
    record = tenant_adapter_of(app, IReadWorkSourceRepository).get(WorkSourceId(source_id))
    assert record is not None
    named = tenant_adapter_of(app, IReadForgeRepository).get(record.fields.forge_id)
    assert named is not None

    tenant_adapter_of(app, ConfigAuthoring).edit_forge(named, ForgeEdit(web_base="https://code.acme.example"), OP)

    after = _adapter(app)
    assert after is not before
    assert after.web_base == "https://code.acme.example"


def test_a_locator_edit_reaches_the_next_resolve(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])
    old_url = "https://github.com/acme/widget/issues/7"
    new_url = "https://github.com/acme/gadget/issues/7"
    assert _sources(app).resolve(old_url) == WorkRef(source=source_id, ref="7")

    assert client.patch(f"/api/work-sources/{source_id}", json={"locator": "acme/gadget"}).status_code == 200

    assert _sources(app).resolve(old_url) is None
    assert _sources(app).resolve(new_url) == WorkRef(source=source_id, ref="7")


def test_the_annotation_sweep_reads_the_annotating_set_each_pass(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    double, forge_url = forge
    source_id = create(client, app, forge_url)
    promote(client, "widget:1")

    _annotation(client).sweep()
    assert forge_state(double)["issue_labels"] == {}

    assert client.patch(f"/api/work-sources/{source_id}", json={"annotate": True}).status_code == 200
    _annotation(client).sweep()
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:ingested"}

    assert client.patch(f"/api/work-sources/{source_id}", json={"annotate": False}).status_code == 200
    _annotation(client).sweep()
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == set()


def test_a_retired_source_refuses_ingest_while_its_ingested_item_still_closes_and_annotates(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    double, forge_url = forge
    source_id = create(client, app, forge_url, annotate=True)
    promote(client, "widget:1")

    assert client.post(f"/api/work-sources/{source_id}/retire").status_code == 200

    refused = _ingest(client, "widget:2")
    assert refused.status_code == 422
    assert _sources(app).names() == ["hub"]
    assert _sources(app).annotating_ids() == []
    closer = _sources(app).closer(source_id)
    annotator = _sources(app).annotator(source_id)
    assert closer is not None and annotator is not None
    pointer = WorkRef(source=source_id, ref="1")
    annotator.set_status(pointer, WorkStatusMarker.IN_PROGRESS)
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:in-progress"}
    closer.close(pointer, trace=None)
    assert forge_state(double)["issue_state"]["acme/widget#1"]["state"] == "closed"


def test_a_retired_source_whose_secret_is_retired_reads_as_absent_not_as_an_error(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])
    assert client.post(f"/api/work-sources/{source_id}/retire").status_code == 200
    retire_credential(client, "gh")
    assert client.post("/api/secrets/gh/retire").status_code == 200

    assert _sources(app).get(source_id) is None
    assert _sources(app).closer(source_id) is None
    assert _sources(app).annotator(source_id) is None
    assert _sources(app).label_clearer(source_id) is None
    # The record still names the source's current slug, so the item it held still displays.
    assert _sources(app).label(WorkRef(source=source_id, ref="1")) == "widget#1"


def test_board_reads_a_retired_source_without_its_secret_and_renames_on_the_next_request(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])
    minted = _ingest(client, "widget:1")
    assert minted.status_code == 201, minted.text
    chunk_id = minted.json()["chunk_id"]
    before = client.get(f"/api/chunks/{chunk_id}").json()["work_refs"][0]
    assert before["label"] == "widget#1"
    _rename(app, source_id, "widget-core")
    after = client.get(f"/api/chunks/{chunk_id}").json()["work_refs"][0]
    assert after["label"] == "widget-core#1"
    assert client.post(f"/api/work-sources/{source_id}/retire").status_code == 200
    retire_credential(client, "gh")
    assert client.post("/api/secrets/gh/retire").status_code == 200
    assert _sources(app).get(source_id) is None

    def labels() -> None:
        page = client.get("/api/chunks")
        detail = client.get(f"/api/chunks/{chunk_id}")
        assert page.status_code == detail.status_code == 200
        (row,) = page.json()["chunks"]
        assert row["work_refs"] == detail.json()["work_refs"]
        assert row["work_refs"][0]["label"] == f"{_sources(app).source_label(source_id)}#1"
        assert row["work_refs"][0]["web_url"] is None

    labels()


# --------------------------------------------------------------------------- #
# Identity: refs hold the id, tokens may name any handle, labels follow a rename.


def test_an_ingested_ref_holds_the_id_whichever_handle_the_token_names(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])
    sources = _sources(app)

    assert sources.resolve("widget:1") == WorkRef(source=source_id, ref="1")
    assert sources.resolve("widget#1") == WorkRef(source=source_id, ref="1")
    assert sources.resolve(f"{source_id}:1") == WorkRef(source=source_id, ref="1")
    assert sources.resolve("https://github.com/acme/widget/issues/1") == WorkRef(source=source_id, ref="1")
    assert sources.resolve("hub:1") == WorkRef(source="hub", ref="1")


def test_a_rename_keeps_ingested_refs_resolving_and_their_labels_follow(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])
    minted = _ingest(client, "widget:1")
    assert minted.status_code == 201, minted.text
    chunk_id = minted.json()["chunk_id"]

    _rename(app, source_id, "widget-core")

    [ref] = client.get(f"/api/chunks/{chunk_id}").json()["work_refs"]
    assert ref["source"] == source_id
    assert ref["label"] == "widget-core#1"
    assert _sources(app).label(WorkRef(source=source_id, ref="1")) == "widget-core#1"
    # Both the new slug and the one the source left still name it.
    assert _sources(app).resolve("widget-core:2") == WorkRef(source=source_id, ref="2")
    assert _sources(app).resolve("widget:2") == WorkRef(source=source_id, ref="2")
    assert _sources(app).names() == ["widget-core", "hub"]
    # Re-ingesting the held item under its former slug is refused naming its current label.
    held = _ingest(client, "widget:1")
    assert held.status_code == 409
    assert held.json()["source"] == source_id


def test_a_reclaimed_former_slug_resolves_to_its_new_holder(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    first = create(client, app, forge[1])
    _rename(app, first, "widget-core")

    second = create(client, app, forge[1], slug="widget", locator="acme/gadget")

    assert second != first
    assert _sources(app).resolve("widget:3") == WorkRef(source=second, ref="3")
    assert _sources(app).resolve("widget-core:3") == WorkRef(source=first, ref="3")


def test_an_id_shaped_prefix_never_resolves_as_a_slug(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    """A ``wsrc_`` value is only ever an id: one naming no source resolves to nothing, whatever the slugs."""
    client, app = hub
    source_id = create(client, app, forge[1])
    unknown = "wsrc_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert unknown != source_id

    assert _sources(app).resolve(f"{unknown}:1") is None
    assert WorkSourceSlug.of(source_id) is None


def test_hub_is_never_a_work_source_slug(hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])

    with pytest.raises(ConfigFieldError):
        _rename(app, source_id, "hub")
    # A ``hub`` prefix always reaches the built-in source, never a configured one.
    assert _sources(app).resolve("hub:4") == WorkRef(source="hub", ref="4")


def test_a_retired_source_s_former_slug_is_refused_like_its_current_one(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])
    _rename(app, source_id, "widget-core")
    assert client.post(f"/api/work-sources/{source_id}/retire").status_code == 200

    assert _sources(app).resolve("widget:1") is None
    assert _sources(app).resolve("widget-core:1") is None
    assert _sources(app).resolve(f"{source_id}:1") is None


def test_a_key_naming_no_source_has_no_closer(hub: tuple[TestClient, FastAPI]) -> None:
    _client, app = hub

    assert _sources(app).closer("ws_unknown") is None


def test_an_active_source_that_cannot_be_built_raises_on_closer(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    """Only a retired source's failed build reads as absent; an active one's surfaces."""
    client, app = hub
    source_id = create(client, app, forge[1])

    def unbuildable(record: ConfiguredWorkSource) -> BuiltWorkSource:
        raise WorkSourceError(f"work source {record.slug} cannot be built")

    registry = StoreWorkSourceRegistry(
        records=tenant_adapter_of(app, IReadWorkSourceRepository),
        objects=ConfigObjectCache(
            ConfigRevisionsStore(hub_store_connections(hosted_engine(cast(FastAPI, client.app))))
        ),
        build=unbuildable,
        built_in=_sources(app).get("hub"),  # pyright: ignore[reportArgumentType]  # the registry port types its "hub" entry as IWorkSource; it is the built-in source
    )

    with pytest.raises(WorkSourceError):
        registry.closer(source_id)


def test_a_source_whose_credential_the_forge_refuses_at_build_raises_the_annotation_refusal(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    """The write halves carry a refused build as the refusal the forge-status pass records, naming the forge."""
    client, app = hub
    source_id = create(client, app, forge[1])
    classified = ClassifiedRefusal(
        refusal=AuthRefusal.INSTALLATION_SUSPENDED,
        diagnostic=ForgeDiagnostic(
            status=403,
            operation="mint",
            endpoint="POST /app/installations/{installation_id}/access_tokens",
            repository=None,
            message="This installation has been suspended",
            documentation_url=None,
            request_id=None,
            accepted_permissions=None,
        ),
    )

    def refused(record: ConfiguredWorkSource) -> BuiltWorkSource:
        raise WorkSourceAuthRefused(f"work source {record.slug}", classified)

    registry = StoreWorkSourceRegistry(
        records=tenant_adapter_of(app, IReadWorkSourceRepository),
        objects=ConfigObjectCache(
            ConfigRevisionsStore(hub_store_connections(hosted_engine(cast(FastAPI, client.app))))
        ),
        build=refused,
        built_in=_sources(app).get("hub"),  # pyright: ignore[reportArgumentType]  # the registry port types its "hub" entry as IWorkSource; it is the built-in source
    )
    record = tenant_adapter_of(app, IReadWorkSourceRepository).get(WorkSourceId(source_id))
    assert record is not None

    for reach in (registry.annotator, registry.label_clearer):
        with pytest.raises(WorkAnnotateError) as raised:
            reach(source_id)
        assert raised.value.refusal is not None
        assert raised.value.refusal.classified == classified
        assert raised.value.refusal.forge_id == record.fields.forge_id


# --------------------------------------------------------------------------- #
# Bindings: a set of sources resolved together answers as the singular lookups do.


def _stored_registry(
    app: FastAPI, build: Callable[[ConfiguredWorkSource], BuiltWorkSource], *, close_forge_writes_enabled: bool = True
) -> StoreWorkSourceRegistry:
    """A registry of its own over ``app``'s records and revisions, building through ``build``."""
    return registry_over(
        tenant_adapter_of(app, IReadWorkSourceRepository),
        ConfigRevisionsStore(hub_store_connections(hosted_engine(app))),
        _sources(app).get("hub"),
        build,
        close_forge_writes_enabled=close_forge_writes_enabled,
    )


def _assert_bindings_match(registry: IWorkSourceRegistry, keys: list[str]) -> None:
    bound = registry.bindings(keys)
    for key in keys:
        pointer = WorkRef(source=key, ref="1")
        assert bound.get(key) is registry.get(key), key
        assert bound.closer(key) is registry.closer(key), key
        assert bound.annotator(key) is registry.annotator(key), key
        assert bound.label_clearer(key) is registry.label_clearer(key), key
        assert bound.source_label(key) == registry.source_label(key), key
        assert bound.label(pointer) == registry.label(pointer), key


def test_bindings_answer_each_lookup_as_the_singular_one_does(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    annotating = create(client, app, forge[1], annotate=True)
    silent = create(client, app, forge[1], slug="gadget", locator="acme/gadget")
    retired = create(client, app, forge[1], slug="old", locator="acme/old")
    assert client.post(f"/api/work-sources/{retired}/retire").status_code == 200
    keys = ["hub", "wsrc_unknown", annotating, silent, retired]
    sources = _sources(app)

    _assert_bindings_match(sources, keys)

    bound = sources.bindings(keys)
    assert bound.annotator(annotating) is not None and bound.annotator(silent) is None
    assert bound.label_clearer(silent) is not None and bound.closer(retired) is not None
    assert bound.get("wsrc_unknown") is None


def test_bindings_read_a_retired_source_that_no_longer_builds_as_absent(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1], annotate=True)
    assert client.post(f"/api/work-sources/{source_id}/retire").status_code == 200
    retire_credential(client, "gh")
    assert client.post("/api/secrets/gh/retire").status_code == 200

    _assert_bindings_match(_sources(app), [source_id])

    bound = _sources(app).bindings([source_id])
    assert bound.get(source_id) is None
    assert bound.label(WorkRef(source=source_id, ref="1")) == "widget#1"


def test_bindings_seat_no_configured_closer_while_close_writes_are_disabled(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])
    registry = _stored_registry(
        app,
        lambda record: BuiltWorkSource(record, FakeWorkSource(), lambda: None),
        close_forge_writes_enabled=False,
    )

    _assert_bindings_match(registry, ["hub", source_id])

    bound = registry.bindings(["hub", source_id])
    assert bound.closer(source_id) is None and bound.closer("hub") is not None


def test_bindings_raise_a_failing_build_at_that_source_s_call_alone(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    broken = create(client, app, forge[1])
    working = create(client, app, forge[1], slug="gadget", locator="acme/gadget")

    def build(record: ConfiguredWorkSource) -> BuiltWorkSource:
        if record.work_source_id == broken:
            raise WorkSourceError(f"work source {record.slug} cannot be built")
        return BuiltWorkSource(record, FakeWorkSource(), lambda: None)

    bound = _stored_registry(app, build).bindings([broken, working])

    assert bound.closer(working) is not None
    for reach in (bound.get, bound.closer, bound.annotator, bound.label_clearer):
        with pytest.raises(WorkSourceError):
            reach(broken)


def test_bindings_carry_a_refused_build_as_the_annotation_refusal(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])
    classified = ClassifiedRefusal(
        refusal=AuthRefusal.INSTALLATION_SUSPENDED,
        diagnostic=ForgeDiagnostic(
            status=403,
            operation="mint",
            endpoint="POST /app/installations/{installation_id}/access_tokens",
            repository=None,
            message="This installation has been suspended",
            documentation_url=None,
            request_id=None,
            accepted_permissions=None,
        ),
    )

    def refused(record: ConfiguredWorkSource) -> BuiltWorkSource:
        raise WorkSourceAuthRefused(f"work source {record.slug}", classified)

    bound = _stored_registry(app, refused).bindings([source_id])
    record = tenant_adapter_of(app, IReadWorkSourceRepository).get(WorkSourceId(source_id))
    assert record is not None

    for reach in (bound.annotator, bound.label_clearer):
        with pytest.raises(WorkAnnotateError) as raised:
            reach(source_id)
        assert raised.value.refusal is not None
        assert raised.value.refusal.classified == classified
        assert raised.value.refusal.forge_id == record.fields.forge_id


def test_bindings_render_the_fallback_slug_of_the_record_they_read(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])

    class Unlabelled(FakeWorkSource):
        def label(self, pointer: WorkRef) -> None:
            return None

    registry = _stored_registry(app, lambda record: BuiltWorkSource(record, Unlabelled(), lambda: None))
    bound = registry.bindings([source_id])
    _rename(app, source_id, "widget-core")
    pointer = WorkRef(source=source_id, ref="1")

    assert bound.label(pointer) == "widget#1"
    assert registry.bindings([source_id]).label(pointer) == "widget-core#1"


def test_bindings_refuse_a_source_outside_the_bound_set(hub: tuple[TestClient, FastAPI]) -> None:
    _client, app = hub
    bound = _sources(app).bindings([])

    for reach in (bound.get, bound.closer, bound.annotator, bound.label_clearer, bound.source_label):
        with pytest.raises(KeyError):
            reach("hub")


class _Slugged(FakeWorkSource):
    """Labels every pointer with the slug of the record it was built from."""

    def __init__(self, slug: str) -> None:
        super().__init__()
        self.slug = slug

    def label(self, pointer: WorkRef) -> str:
        return f"{self.slug}!{pointer.ref}"


def _slugged(record: ConfiguredWorkSource) -> BuiltWorkSource:
    return BuiltWorkSource(record, _Slugged(record.slug), lambda: None)


def test_a_binding_older_than_the_cached_adapter_hands_out_that_one_and_leaves_it_installed(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    client, app = hub
    source_id = create(client, app, forge[1])
    released: list[str] = []

    def build(record: ConfiguredWorkSource) -> BuiltWorkSource:
        return BuiltWorkSource(record, _Slugged(record.slug), lambda: released.append(record.slug))

    registry = _stored_registry(app, build)
    bound = registry.bindings([source_id])
    _rename(app, source_id, "widget-core")
    newer = registry.get(source_id)

    assert bound.get(source_id) is newer
    assert registry.get(source_id) is newer
    assert released == []


def test_an_edit_landing_between_a_binding_s_two_reads_is_not_cached_as_current(
    hub: tuple[TestClient, FastAPI], forge: tuple[ForgeDouble, str]
) -> None:
    """The revisions are read before the records: read the other way, the old record would be cached under the new
    revision and handed out until the next edit."""
    client, app = hub
    source_id = create(client, app, forge[1])
    stored = tenant_adapter_of(app, IReadWorkSourceRepository)
    edits = [lambda: _rename(app, source_id, "widget-core")]

    def edit_once() -> None:
        while edits:
            edits.pop()()

    class EditingRevisions(ConfigRevisionsStore):
        def revisions_for(self, kind: RecordKind, keys: Sequence[str]) -> dict[str, ConfigRevisions]:
            found = super().revisions_for(kind, keys)
            edit_once()
            return found

    class EditingRecords:
        def __getattr__(self, name: str) -> object:
            return getattr(stored, name)

        def get_many(self, work_source_ids: list[WorkSourceId]) -> dict[WorkSourceId, ConfiguredWorkSource]:
            found = stored.get_many(work_source_ids)
            edit_once()
            return found

    registry = registry_over(
        cast(IReadWorkSourceRepository, EditingRecords()),
        EditingRevisions(hub_store_connections(hosted_engine(app))),
        _sources(app).get("hub"),
        _slugged,
    )
    pointer = WorkRef(source=source_id, ref="1")
    registry.bindings([source_id]).get(source_id)
    assert edits == []

    current = registry.get(source_id)
    assert current is not None and current.label(pointer) == "widget-core!1"
