"""Configuration routes — the served document schemas and the change log.

Both are read-only (``FLEET_VIEW``); configured records change only through their own
verbs, each of which appends the change rows this reads."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from blizzard.auth_core import FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.config.changes import ConfigChange, RecordKind
from blizzard.wire.config import ConfigChangesPage, ConfigChangeView, FieldChangeView
from blizzard.wire.repository import RepositoryDocument
from blizzard.wire.work_source import WorkSourceDocument

router = APIRouter(
    prefix="/api/config",
    tags=["config"],
    dependencies=[Depends(reject_runner_principal), Depends(require(FLEET_VIEW))],
)

#: The largest page of the change log a caller may request.
MAX_CHANGES_LIMIT = 200

_SCHEMAS = {"work-sources": WorkSourceDocument, "repositories": RepositoryDocument}


def _view(change: ConfigChange) -> ConfigChangeView:
    assert change.id is not None
    return ConfigChangeView(
        id=change.id,
        recorded_at=iso_utc(change.recorded_at),
        actor=change.actor,
        door=change.door.value,
        record_kind=change.record_kind.value,
        record_key=change.record_key,
        revision=change.revision,
        op=change.op.value,
        diff=[FieldChangeView(field=d.field, old=d.old, new=d.new) for d in change.diff],
        apply_id=change.apply_id,
    )


@router.get("/schema/{kind}")
def get_schema(kind: str) -> dict[str, object]:
    """The JSON Schema of a record kind's document model; 404 for a kind with none yet."""
    model = _SCHEMAS.get(kind)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no document schema for {kind!r}")
    return model.model_json_schema()


@router.get("/changes", response_model=ConfigChangesPage)
def list_changes(
    services: Annotated[HubServices, Depends(get_services)],
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_CHANGES_LIMIT)] = 50,
    record_kind: RecordKind | None = None,
    record_key: str | None = None,
) -> ConfigChangesPage:
    """The change log newest-first, keyset-paged on `id`: pass the page's `next_before` as
    `before` for the next. Optionally narrowed to one `record_kind` and `record_key`."""
    rows = services.config_changes.page(before=before, limit=limit + 1, record_kind=record_kind, record_key=record_key)
    page = rows[:limit]
    return ConfigChangesPage(changes=[_view(c) for c in page], next_before=page[-1].id if len(rows) > limit else None)
