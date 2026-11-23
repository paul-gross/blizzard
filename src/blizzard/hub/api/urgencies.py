"""Urgency routes — create, list, show, edit, retire, enable, the icon, and the set's two reorders.

The controller stays read-only (``bzh:controller-read-only``), resolving ``{key}`` and every urgency a body names — an
id or a slug — before delegating to the domain (``bzh:domain-takes-objects``). Every write needs ``config:edit`` and
carries the request's :class:`ChangeContext`; a retire tells the domain whether the caller holds ``chunk:control``,
which a retire moving open work needs beside it. What a write moved is published after its commit
(``bzh:publish-after-commit``): a reorder the queue's change, a retire each moved chunk's ``edited`` change and then
the queue's once."""

from __future__ import annotations

from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Header, Request, Response, status

from blizzard.auth_core import Permission
from blizzard.foundation.project_colors import ProjectColor
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import problems, problems_admin
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.chunk_events import ChunkChanged, load_frame_states
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.auth.permissions import may_edit_chunks
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.kernel.unset import UNSET
from blizzard.hub.domain.urgency.chain import URGENCY_FIELD
from blizzard.hub.domain.urgency.mark import IconShape
from blizzard.hub.domain.urgency.model import (
    MoveTo,
    Urgency,
    UrgencyEdit,
    UrgencyInUse,
    UrgencyIsDefault,
    UrgencyMoveRefused,
    UrgencyRetired,
    UrgencyShape,
    UrgencyUnknown,
)
from blizzard.hub.domain.urgency.services import UrgencyAuthoring, UrgencyLifecycle, UrgencyOrdering, UrgencyReading
from blizzard.hub.domain.urgency.services import UrgencyView as UrgencyRead
from blizzard.hub.events.broker import EventBroker
from blizzard.wire.urgency import (
    UrgenciesListView,
    UrgencyCreateRequest,
    UrgencyIconDocument,
    UrgencyIconView,
    UrgencyOrderRequest,
    UrgencyPatchRequest,
    UrgencyPositionRequest,
    UrgencyRetireRequest,
    UrgencyRetireView,
    UrgencyView,
)

router = APIRouter(
    prefix="/api/urgencies",
    tags=["urgencies"],
    dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)],
)

Reading = FromDishka[UrgencyReading]
Authoring = FromDishka[UrgencyAuthoring]
Lifecycle = FromDishka[UrgencyLifecycle]
Ordering = FromDishka[UrgencyOrdering]
Frames = FromDishka[ChunkFrameLoader]
Events = FromDishka[EventBroker]

_VIEW = [Depends(require(Permission.FLEET_VIEW))]
Editor = Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))]

#: An icon is served so a browser draws it and never runs it.
_ICON_POLICY = "default-src 'none'"


@inject_sync
def path_urgency(key: str, reading: Reading) -> Urgency:
    found = reading.resolve(key)
    if found is None:
        raise ProblemHTTPException(partial(problems_admin.urgency_not_found_problem, f"unknown urgency {key!r}"))
    return found


PathUrgency = Annotated[Urgency, Depends(path_urgency)]


def urgency_refusal(exc: ConfigFieldError) -> ProblemHTTPException:
    """A mint's or chunk edit's stated urgency refused, ``urgency_unknown`` or ``urgency_retired``, in the body."""
    return ProblemHTTPException(
        partial(problems_admin.config_field_invalid_problem, "body", exc.field, exc.message, exc.code)
    )


def stated_urgency(key: str | None, reading: UrgencyReading) -> Urgency | None:
    """The live urgency a mint request states by id or slug, checked before anything is written; ``None`` when it
    states none. 422 ``urgency_unknown`` or ``urgency_retired`` otherwise."""
    if key is None:
        return None
    try:
        return reading.live(URGENCY_FIELD, key)
    except (UrgencyUnknown, UrgencyRetired) as exc:
        raise urgency_refusal(exc) from exc


def _view(read: UrgencyRead) -> UrgencyView:
    urgency = read.urgency
    return UrgencyView(
        urgency_id=urgency.urgency_id,
        slug=urgency.slug,
        name=urgency.name,
        color=urgency.color.value if isinstance(urgency.color, ProjectColor) else urgency.color,
        emoji=urgency.emoji,
        icon=None
        if urgency.icon is None
        else UrgencyIconView(media_type=urgency.icon.media_type.value, digest=urgency.icon.digest),
        retired=urgency.retired,
        place=read.place,
        default=read.default,
        revision=urgency.revision,
        open_chunks=read.open_chunks,
        created_at=iso_utc(urgency.created_at),
        created_by=urgency.created_by,
    )


def _shown(urgency: Urgency, reading: UrgencyReading) -> UrgencyView:
    return _view(reading.view(urgency))


def _listing(reading: UrgencyReading, *, include_retired: bool = False) -> UrgenciesListView:
    return UrgenciesListView(urgencies=[_view(read) for read in reading.list(include_retired=include_retired)])


def _icon(document: UrgencyIconDocument | None) -> IconShape | None:
    return None if document is None else IconShape(document.media_type, document.data)


def _unprocessable(exc: ConfigFieldError) -> ProblemHTTPException:
    return ProblemHTTPException(
        partial(problems_admin.config_field_invalid_problem, "body", exc.field, exc.message, exc.code)
    )


def _revision_conflict(exc: ConfigRevisionConflict) -> ProblemHTTPException:
    return ProblemHTTPException(partial(problems.revision_conflict_problem, str(exc)))


@router.get("", response_model=UrgenciesListView, dependencies=_VIEW)
@inject_sync
def list_urgencies(reading: Reading, retired: bool = False) -> UrgenciesListView:
    """The live urgencies in order, most urgent first, the default marked; with `retired=true`, the retired ones
    too, each at its standing place."""
    return _listing(reading, include_retired=retired)


@router.post("", response_model=UrgencyView, status_code=status.HTTP_201_CREATED)
@inject_sync
def create_urgency(
    request: UrgencyCreateRequest, identity: Editor, door: RequestDoor, reading: Reading, authoring: Authoring
) -> UrgencyView:
    """Create an urgency at the least urgent end, or just after `after` — first under `after: null`. 422 naming the
    field for an invalid one, `urgency_slug_taken` for a slug a live or retired urgency holds, and
    `urgency_mark_conflict` for both an emoji and an icon."""
    sent = request.model_fields_set
    shape = UrgencyShape(
        slug=request.slug,
        name=request.name if "name" in sent else UNSET,
        color=request.color if "color" in sent else UNSET,
        emoji=request.emoji if "emoji" in sent else UNSET,
        icon=_icon(request.icon) if "icon" in sent else UNSET,
    )
    try:
        after = (
            UNSET if "after" not in sent else None if request.after is None else reading.named("after", request.after)
        )
        created = authoring.create(shape, change_context(identity, door), after=after)
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    return _shown(created, reading)


@router.put("/order", response_model=UrgenciesListView)
@inject_sync
def put_urgency_order(
    request: UrgencyOrderRequest,
    identity: Editor,
    door: RequestDoor,
    reading: Reading,
    ordering: Ordering,
    events: Events,
) -> UrgenciesListView:
    """Set the whole live order, most urgent first; one that already stands writes nothing. 422
    `urgency_order_incomplete` when it leaves a live urgency out, names a retired one, or names one twice, and
    `urgency_unknown` for one the tenant does not hold."""
    try:
        named_by = reading.namer()
        named = [named_by("urgencies", key) for key in request.urgencies]
        moved = ordering.set_order(named, change_context(identity, door))
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    if moved:
        events.publish_queue_changed()
    return _listing(reading)


@router.post("/order/position", response_model=UrgenciesListView)
@inject_sync
def position_urgency(
    request: UrgencyPositionRequest,
    identity: Editor,
    door: RequestDoor,
    reading: Reading,
    ordering: Ordering,
    events: Events,
) -> UrgenciesListView:
    """Move one urgency to just after `after`, or first under `after: null`; one already there writes nothing. 422
    for an urgency the tenant does not hold, a retired one, or a move after itself."""
    try:
        moving = reading.named("urgency", request.urgency)
        after = None if request.after is None else reading.named("after", request.after)
        moved = ordering.move(moving, change_context(identity, door), after=after)
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    if moved:
        events.publish_queue_changed()
    return _listing(reading)


@router.get("/{key}", response_model=UrgencyView, dependencies=_VIEW)
@inject_sync
def get_urgency(urgency: PathUrgency, reading: Reading) -> UrgencyView:
    """Show an urgency by id or slug."""
    return _shown(urgency, reading)


@router.patch("/{key}", response_model=UrgencyView)
@inject_sync
def patch_urgency(
    identity: Editor,
    urgency: PathUrgency,
    request: UrgencyPatchRequest,
    door: RequestDoor,
    reading: Reading,
    authoring: Authoring,
    if_match: Annotated[int | None, Header()] = None,
) -> UrgencyView:
    """Edit the stated fields, keeping the revision on a no-op; `null` on `emoji` or `icon` clears it. 422 for a
    `slug`, for a `null` `name` or `color`, or for both marks left set; 409 on a stale `If-Match`."""
    sent = request.model_fields_set
    edit = UrgencyEdit(
        # Any stated slug is refused by the domain, a null one included.
        slug=(request.slug or "") if "slug" in sent else UNSET,
        name=request.name if "name" in sent else UNSET,
        color=request.color if "color" in sent else UNSET,
        emoji=request.emoji if "emoji" in sent else UNSET,
        icon=_icon(request.icon) if "icon" in sent else UNSET,
    )
    try:
        edited = authoring.edit(urgency, edit, change_context(identity, door), if_match=if_match)
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return _shown(edited, reading)


@router.post("/{key}/retire", response_model=UrgencyRetireView)
@inject_sync
def retire_urgency(
    identity: Editor,
    urgency: PathUrgency,
    door: RequestDoor,
    reading: Reading,
    lifecycle: Lifecycle,
    frames: Frames,
    events: Events,
    request: UrgencyRetireRequest | None = None,
    if_match: Annotated[int | None, Header()] = None,
) -> UrgencyRetireView:
    """Retire an urgency, moving the open chunks it carries to `move_to`; finished chunks keep it. Retiring a
    retired urgency writes nothing. Refused in order: 409 problem type `urgency-is-default` for the tenant's
    default; 409 `urgency-in-use`, counting them in `open_chunks`, when open chunks carry it and no `move_to` is
    named; 422 `urgency_unknown`, `urgency_retired` or `urgency_move_to_self` for a `move_to` naming no urgency, a
    retired one, or this one. A retire moving open work needs `chunk:control` (403). 409 on a stale `If-Match`."""
    key = None if request is None else request.move_to
    move_to = None if key is None else MoveTo(key, reading.resolve(key))
    try:
        retired = lifecycle.retire(
            urgency,
            change_context(identity, door),
            move_to=move_to,
            may_control=may_edit_chunks(identity.permissions),
            if_match=if_match,
        )
    except UrgencyIsDefault as exc:
        raise ProblemHTTPException(partial(problems_admin.urgency_is_default_problem, str(exc), exc.slug)) from exc
    except UrgencyInUse as exc:
        raise ProblemHTTPException(
            partial(problems_admin.urgency_in_use_problem, str(exc), exc.slug, exc.open_chunks)
        ) from exc
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except UrgencyMoveRefused as exc:
        raise ProblemHTTPException(partial(problems.permission_required_problem, Permission.CHUNK_CONTROL)) from exc
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    if retired.moved:
        for chunk_id, state in load_frame_states(frames, retired.moved).items():
            ChunkChanged.of(frames, chunk_id, prev_status=state.facts.status().value).publish_from(
                state, cause="edited"
            )
        events.publish_queue_changed()
    view = _shown(retired.urgency, reading)
    return UrgencyRetireView(**view.model_dump(), moved_chunks=len(retired.moved))


@router.post("/{key}/enable", response_model=UrgencyView)
@inject_sync
def enable_urgency(
    identity: Editor,
    urgency: PathUrgency,
    door: RequestDoor,
    reading: Reading,
    lifecycle: Lifecycle,
    if_match: Annotated[int | None, Header()] = None,
) -> UrgencyView:
    """Bring a retired urgency back at its standing place in the order; it moves no chunk back. Enabling a live one
    writes nothing. 409 on a stale `If-Match`."""
    try:
        enabled = lifecycle.enable(urgency, change_context(identity, door), if_match=if_match)
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return _shown(enabled, reading)


@router.get(
    "/{key}/icon",
    response_class=Response,
    dependencies=_VIEW,
    responses={
        200: {"content": {"image/png": {}, "image/webp": {}}, "description": "The urgency's icon"},
        304: {"description": "The icon the client holds is current"},
    },
)
@inject_sync
def get_urgency_icon(urgency: PathUrgency, request: Request, reading: Reading) -> object:
    """The urgency's icon, served with its media type, its digest as the entity tag, and a policy that lets no browser
    run it; 304 when `If-None-Match` names the digest. 404 when the urgency carries none."""
    icon = reading.icon(urgency)
    if icon is None:
        raise ProblemHTTPException(
            partial(problems_admin.urgency_icon_not_found_problem, f"urgency {urgency.slug} carries no icon")
        )
    etag = f'"{icon.digest}"'
    headers = {"ETag": etag, "Content-Security-Policy": _ICON_POLICY, "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=icon.data, media_type=icon.media_type.value, headers=headers)
