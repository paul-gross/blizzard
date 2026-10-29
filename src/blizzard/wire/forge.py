"""Forge wire models — what a repository or work-source write answers about the forge it named the old way."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.config import JsonDict

from blizzard.wire.base import WireModel

#: The JSON Schema mark of an old address field, kept through the forge transition beside the ``forge`` reference.
DEPRECATED_FIELD: JsonDict = {"deprecated": True}


class OldFieldWarning(WireModel):
    """One old address field a write used — ``forge_api_url``, ``api_base`` or ``web_base`` — and the forge it
    resolved to, by id and slug."""

    field: str
    forge_id: str
    forge_slug: str
    message: str


class ForgeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    kind: str
    api_base: str
    web_base: str | None = None


class ForgePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    api_base: str | None = None
    web_base: str | None = None


class ForgeReferenceView(WireModel):
    kind: str
    key: str
    label: str | None = None


class ForgeSummary(WireModel):
    forge_id: str
    slug: str
    name: str
    kind: str
    api_base: str
    web_base: str | None
    web_origin: str
    revision: int
    created_at: str
    created_by: str
    created_by_name: str | None = None
    retired: bool


class ForgeBudgetView(WireModel):
    """One rate budget's last reading of one bucket: the secret its calls go out under (``None`` for the anonymous
    budget), and the limit, remaining and reset the forge last answered with."""

    secret_name: str | None
    bucket: str
    limit: int
    remaining: int
    reset: str


class ForgeDetail(ForgeSummary):
    repositories: list[ForgeReferenceView]
    work_sources: list[ForgeReferenceView]
    #: Each budget's last reading, held in memory since the hub started; empty until the forge has been heard from.
    budgets: list[ForgeBudgetView]


class ForgesListView(WireModel):
    forges: list[ForgeSummary]
