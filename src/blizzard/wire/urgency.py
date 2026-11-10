"""Urgency wire models — the ``/api/urgencies`` record verbs, the set's two reorders, and the tenant's settings at
``/api/settings``.

A request body forbids undeclared fields and tells an explicit ``null`` from an absent field by the fields it was sent,
so a ``null`` that clears a mark is never mistaken for a field left alone."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from blizzard.wire.base import WireModel
from blizzard.wire.problem import CommonProblemMembers, ProblemDetails


class UrgencyIconDocument(BaseModel):
    """An icon as a request carries it: its media type and its bytes in base64."""

    model_config = ConfigDict(extra="forbid")

    media_type: str
    data: str


class UrgencyCreateRequest(BaseModel):
    """A new urgency. An absent ``name`` takes the slug and an absent ``color`` the palette color the live urgencies
    use least; an absent ``after`` lands it at the least urgent end, and ``after: null`` first."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    color: str | None = None
    emoji: str | None = None
    icon: UrgencyIconDocument | None = None
    after: str | None = None


class UrgencyPatchRequest(BaseModel):
    """A sparse edit: a field left out stays; ``null`` on ``emoji`` or ``icon`` clears it, and is refused on ``name``
    or ``color``. The slug is fixed, so a body stating one is refused."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    color: str | None = None
    emoji: str | None = None
    icon: UrgencyIconDocument | None = None


class UrgencyRetireRequest(BaseModel):
    """Where the retiring urgency's open chunks go, by id or slug; ``null`` or absent when none carry it."""

    model_config = ConfigDict(extra="forbid")

    move_to: str | None = None


class UrgencyOrderRequest(BaseModel):
    """The whole live order, most urgent first, each urgency an id or a slug, naming every live urgency once."""

    model_config = ConfigDict(extra="forbid")

    urgencies: list[str]


class UrgencyPositionRequest(BaseModel):
    """One urgency moved to just after ``after``, or first under ``after: null``; each an id or a slug."""

    model_config = ConfigDict(extra="forbid")

    urgency: str
    after: str | None


class UrgencyIconView(WireModel):
    """What a view carries of an icon: its media type and the digest of its bytes, never the bytes."""

    media_type: str
    digest: str


class UrgencyView(WireModel):
    urgency_id: str
    slug: str
    name: str
    color: str
    emoji: str | None
    icon: UrgencyIconView | None
    retired: bool
    #: The urgency's place in the live order, ``1`` the most urgent; ``None`` once retired.
    place: int | None
    #: Whether the urgency is the tenant's default — what every new chunk takes when nothing names another.
    default: bool
    revision: int
    #: How many open chunks carry the urgency.
    open_chunks: int
    created_at: str
    created_by: str


class UrgenciesListView(WireModel):
    urgencies: list[UrgencyView]


class UrgencyRetireView(UrgencyView):
    """A retired urgency, and how many open chunks the retire moved."""

    moved_chunks: int


class SettingsPatchRequest(BaseModel):
    """A sparse edit of the tenant's settings: ``default_urgency``, by id or slug, when stated; ``null`` is
    refused."""

    model_config = ConfigDict(extra="forbid")

    default_urgency: str | None = None


class UrgencyRef(WireModel):
    """An urgency a chunk carries or the settings name as the default, by id and slug; a reader joins it to the
    tenant's urgency set for the rest."""

    urgency_id: str
    slug: str


class SettingsView(WireModel):
    """The tenant's settings."""

    default_urgency: UrgencyRef
    revision: int
    updated_at: str
    updated_by: str


URGENCY_NOT_FOUND: Final = "urn:blizzard:problem:urgency-not-found"
URGENCY_ICON_NOT_FOUND: Final = "urn:blizzard:problem:urgency-icon-not-found"
URGENCY_IS_DEFAULT: Final = "urn:blizzard:problem:urgency-is-default"
URGENCY_IN_USE: Final = "urn:blizzard:problem:urgency-in-use"

URGENCY_PROBLEM_TYPES: frozenset[str] = frozenset(
    {URGENCY_NOT_FOUND, URGENCY_ICON_NOT_FOUND, URGENCY_IS_DEFAULT, URGENCY_IN_USE}
)


class UrgencyNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:urgency-not-found"]]):
    """A path names an urgency the tenant does not hold."""


class UrgencyIconNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:urgency-icon-not-found"]]):
    """The urgency carries no icon."""


class UrgencyIsDefaultDetail(WireModel):
    code: Literal["urgency_is_default"] = "urgency_is_default"
    message: str
    slug: str


class UrgencyInUseDetail(WireModel):
    code: Literal["urgency_in_use"] = "urgency_in_use"
    message: str
    slug: str
    #: How many open chunks carry the urgency, so a client can ask where they go.
    open_chunks: int


class UrgencyIsDefaultProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:urgency-is-default"], UrgencyIsDefaultDetail]
):
    """A retire names the tenant's default urgency; the default must point elsewhere first."""

    status: int | None = None


class UrgencyInUseProblem(CommonProblemMembers[Literal["urn:blizzard:problem:urgency-in-use"], UrgencyInUseDetail]):
    """A retire names an urgency open chunks carry, and no urgency to move them to."""

    status: int | None = None
